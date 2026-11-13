"""
Database manager for inventory tracking using PostgreSQL
"""

import psycopg2
from contextlib import contextmanager
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from config import DB_CONFIG, MATCH_CONFIG
from inventory_logic import (
    ACTION_ADD, ACTION_REMOVE, ACTION_SET, normalize_action, normalize_unit,
    convert_quantity, decide_match, find_similar_groups, fmt_qty, name_key, name_tokens,
)
from default_names import STARTER_NAME_GROUPS


class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.connect()
        self.setup_tables()

    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(connect_timeout=5, **DB_CONFIG)
            self.conn.autocommit = True
            print("✅ Database connected successfully")
        except psycopg2.OperationalError as e:
            raise ConnectionError(f"Failed to connect to PostgreSQL: {e}")

    def setup_tables(self):
        """Create tables if they don't exist."""
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id SERIAL PRIMARY KEY,
                    item_name VARCHAR(255) NOT NULL,
                    quantity NUMERIC(10, 2) NOT NULL,
                    unit VARCHAR(50),
                    category VARCHAR(100),
                    date_used DATE,
                    date_bought DATE,
                    notes TEXT,
                    raw_input TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS inventory_log (
                    id SERIAL PRIMARY KEY,
                    inventory_id INTEGER REFERENCES inventory(id) ON DELETE CASCADE,
                    action VARCHAR(50),
                    quantity_change NUMERIC(10, 2),
                    notes TEXT,
                    logged_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Choices the user made when asked about similar items ("remember this choice")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_aliases (
                    alias_key    TEXT PRIMARY KEY,
                    alias_name   TEXT NOT NULL,
                    inventory_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
                    created_at   TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_distinct (
                    inventory_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
                    name_key     TEXT NOT NULL,
                    PRIMARY KEY (inventory_id, name_key)
                )
            """)
            # Name dictionary: several names for one item (mint = pudina). Not tied to a row, so it
            # keeps working when rows are added or deleted.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS name_groups (
                    id    SERIAL PRIMARY KEY,
                    label TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS name_group_names (
                    name_key     TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    group_id     INTEGER NOT NULL REFERENCES name_groups(id) ON DELETE CASCADE
                )
            """)
            # Older databases stored 2 decimals; widen so that conversions such as 5 g -> 0.005 kg
            # are not rounded away. Widening a NUMERIC column is safe and idempotent.
            cur.execute("ALTER TABLE inventory ALTER COLUMN quantity TYPE NUMERIC(12, 3)")
            cur.execute("ALTER TABLE inventory_log ALTER COLUMN quantity_change TYPE NUMERIC(12, 3)")
        print("✅ Tables ready")

    @contextmanager
    def _transaction(self):
        """Run several statements atomically (the connection is otherwise in autocommit mode)."""
        self.conn.autocommit = False
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = True

    def insert_item(self, item: dict) -> int:
        """Insert a new inventory item."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO inventory 
                    (item_name, quantity, unit, category, date_used, date_bought, notes, raw_input)
                VALUES 
                    (%(item_name)s, %(quantity)s, %(unit)s, %(category)s, 
                     %(date_used)s, %(date_bought)s, %(notes)s, %(raw_input)s)
                RETURNING id
            """, item)
            item_id = cur.fetchone()[0]

            # Log the insertion
            cur.execute("""
                INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                VALUES (%s, 'added', %s, 'Initial entry')
            """, (item_id, item.get("quantity", 0)))

            return item_id

    def update_item(self, item_id: int, updates: dict):
        """Update an existing inventory item."""
        fields = ", ".join(f"{k} = %({k})s" for k in updates if k != "id")
        updates["updated_at"] = datetime.now()
        updates["id"] = item_id

        with self._transaction() as cur:
            old = None
            if "quantity" in updates:
                cur.execute("SELECT quantity FROM inventory WHERE id = %s FOR UPDATE", (item_id,))
                row = cur.fetchone()
                old = float(row["quantity"]) if row else None

            cur.execute(f"""
                UPDATE inventory SET {fields}, updated_at = %(updated_at)s
                WHERE id = %(id)s
            """, updates)

            if "quantity" in updates:
                change = float(updates["quantity"]) - (old if old is not None else 0.0)
                cur.execute("""
                    INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                    VALUES (%s, 'updated', %s, 'Quantity edited manually')
                """, (item_id, change))

    def delete_item(self, item_id: int):
        """Delete an inventory item."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM inventory WHERE id = %s", (item_id,))

    def get_all_items(self, search: str = "", category: str = "") -> list:
        """Fetch all inventory items with optional filters."""
        query = "SELECT * FROM inventory WHERE 1=1"
        params = []

        if search:
            query += " AND item_name ILIKE %s"
            params.append(f"%{search}%")

        if category and category != "All":
            query += " AND category = %s"
            params.append(category)

        query += " ORDER BY updated_at DESC"

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_categories(self) -> list:
        """Get unique categories."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM inventory WHERE category IS NOT NULL ORDER BY category")
            return [row[0] for row in cur.fetchall()]

    def get_stats(self) -> dict:
        """Get summary statistics."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_items,
                    COUNT(DISTINCT category) as total_categories,
                    COUNT(DISTINCT item_name) as unique_items,
                    SUM(quantity) as total_quantity
                FROM inventory
            """)
            stats = dict(cur.fetchone())

            cur.execute("""
                SELECT COUNT(*) as today_entries
                FROM inventory
                WHERE DATE(created_at) = CURRENT_DATE
            """)
            stats.update(dict(cur.fetchone()))

            return stats

    def get_low_stock(self, threshold: float = 5.0) -> list:
        """Get items with low quantity."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM inventory 
                WHERE quantity <= %s 
                ORDER BY quantity ASC
            """, (threshold,))
            return [dict(row) for row in cur.fetchall()]

    # ── Stock changes ──────────────────────────────────────────────────────────

    def _match_settings(self):
        return (MATCH_CONFIG["mode"], float(MATCH_CONFIG["auto_threshold"]),
                float(MATCH_CONFIG["ask_threshold"]))

    def resolve_match(self, item: dict):
        """
        Decide which existing row an entry refers to (see inventory_logic.decide_match).
        Reads only; nothing is locked, so it is safe to ask the user afterwards.
        """
        mode, auto, ask = self._match_settings()
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM inventory ORDER BY id")
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT alias_key, inventory_id FROM item_aliases")
            aliases = {r["alias_key"]: r["inventory_id"] for r in cur.fetchall()}
            cur.execute("SELECT inventory_id, name_key FROM item_distinct")
            distinct = {(r["inventory_id"], r["name_key"]) for r in cur.fetchall()}
            synonyms = self._synonym_map(cur)
        return decide_match(rows, item.get("item_name"), normalize_unit(item.get("unit")),
                            item.get("category"), aliases, distinct, mode, auto, ask, synonyms)

    def apply_stock_change(self, item: dict, chooser=None) -> dict:
        """
        Apply one parsed or manual entry to the inventory.

        item["action"] decides what happens:
          add     -> increase the matching row, or create it when it does not exist
          remove  -> decrease the matching row (never below 0); nothing is created
          set     -> overwrite the matching row's quantity, or create it

        The row is found by similarity (inventory_logic.decide_match), so "instant dry yeast"
        updates a "yeast" row. When the match is uncertain `chooser(decision, item)` is called
        (no database lock is held meanwhile) and must return a dict:
            {"action": "merge", "row_id": id, "remember": bool}   use that existing row
            {"action": "new", "remember": bool}                  keep it as a separate item
            {"action": "skip"}                                   do nothing
        Without a chooser, uncertain adds become separate items (the legacy behaviour) and
        uncertain removals are left undone (status "ambiguous").

        The quantity is converted into the row's own unit, so removing "250 grams" from a row
        stored in kg subtracts 0.25.

        Returns {"status", "message", "item_id", "old", "new", "unit"}; status is one of:
        created, increased, decreased, set, clamped, not_found, unit_mismatch, invalid,
        ambiguous, skipped.
        """
        name = (item.get("item_name") or "").strip()
        action = normalize_action(item.get("action")) or ACTION_ADD
        try:
            qty = abs(float(item.get("quantity")))
        except (TypeError, ValueError):
            return self._result("invalid", f"{name or '?'}: quantity is not a number")
        if not name:
            return self._result("invalid", "Item name is missing")

        decision = self.resolve_match(item)
        target_id, force_new, remember = None, False, None
        if decision.kind in ("exact", "auto"):
            target_id = decision.row["id"]
        elif decision.kind == "ask":
            choice = chooser(decision, item) if chooser else None
            if choice is None:
                if action == ACTION_REMOVE:
                    names = ", ".join(c.row["item_name"] for c in decision.candidates[:3])
                    return self._result("ambiguous",
                                        f"{name}: could be {names}; nothing was removed")
                force_new = True
            elif choice.get("action") == "skip":
                return self._result("skipped", f"{name}: skipped")
            elif choice.get("action") == "merge":
                target_id = choice["row_id"]
                if choice.get("remember"):
                    remember = ("alias", target_id)
            else:
                force_new = True
                if choice.get("remember"):
                    remember = ("distinct", [c.row["id"] for c in decision.candidates])

        return self._apply(item, name, action, qty, target_id, force_new, remember,
                           decision.score if decision.kind in ("auto", "ask") else 1.0)

    @staticmethod
    def _result(status, message, item_id=None, old=None, new=None, unit=None):
        return {"status": status, "message": message, "item_id": item_id,
                "old": old, "new": new, "unit": unit}

    def _apply(self, item, name, action, qty, target_id, force_new, remember, score):
        unit = normalize_unit(item.get("unit"))
        today = date.today()
        raw = item.get("raw_input")
        result = self._result

        with self._transaction() as cur:
            row = None
            if target_id is not None and not force_new:
                cur.execute("SELECT * FROM inventory WHERE id = %s FOR UPDATE", (target_id,))
                found = cur.fetchone()
                row = dict(found) if found else None
            compatible = True
            if row is not None:
                _, compatible = convert_quantity(qty, unit, row.get("unit"))

            # ── No usable existing row: create it (adds / sets) ───────────────
            if row is None or (not compatible and action == ACTION_ADD):
                if action == ACTION_REMOVE:
                    return result("not_found", f"{name}: not in inventory, nothing was removed")
                cur.execute("""
                    INSERT INTO inventory
                        (item_name, quantity, unit, category, date_used, date_bought, notes, raw_input)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (name, qty, unit, item.get("category") or "General",
                      item.get("date_used"), item.get("date_bought") or today,
                      item.get("notes"), raw))
                new_id = cur.fetchone()["id"]
                cur.execute("""
                    INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                    VALUES (%s, %s, %s, %s)
                """, (new_id, "set" if action == ACTION_SET else "added", qty, "New item"))
                self._remember(cur, remember, name, new_id)
                note = " (existing row uses a different unit)" if row is not None else ""
                return result("created",
                              f"{name}: new item, {fmt_qty(qty)} {unit or ''}".rstrip() + note,
                              new_id, 0.0, qty, unit)

            if not compatible:
                return result("unit_mismatch",
                              f"{name}: cannot combine {unit} with the stored unit "
                              f"'{row.get('unit')}', nothing was changed", row["id"],
                              float(row["quantity"]), float(row["quantity"]), row.get("unit"))

            # ── Update the existing row ───────────────────────────────────────
            row_unit = row.get("unit") or unit
            delta, _ = convert_quantity(qty, unit, row.get("unit"))
            old = float(row["quantity"])
            if action == ACTION_ADD:
                new, status, log_action, change = old + delta, "increased", "added", delta
            elif action == ACTION_SET:
                new, status, log_action, change = delta, "set", "set", delta - old
            else:
                if delta > old:
                    new, status = 0.0, "clamped"
                else:
                    new, status = old - delta, "decreased"
                log_action, change = "removed", new - old

            new = round(new, 3)
            sets = ["quantity = %s", "updated_at = NOW()", "raw_input = %s"]
            params = [new, raw]
            if action == ACTION_REMOVE:
                sets.append("date_used = %s"); params.append(item.get("date_used") or today)
            elif action == ACTION_ADD:
                sets.append("date_bought = %s"); params.append(item.get("date_bought") or today)
            if not row.get("unit") and unit:
                sets.append("unit = %s"); params.append(unit)
            if not row.get("category") and item.get("category"):
                sets.append("category = %s"); params.append(item["category"])
            if not row.get("notes") and item.get("notes"):
                sets.append("notes = %s"); params.append(item["notes"])
            params.append(row["id"])
            cur.execute(f"UPDATE inventory SET {', '.join(sets)} WHERE id = %s", params)
            cur.execute("""
                INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                VALUES (%s, %s, %s, %s)
            """, (row["id"], log_action, round(change, 3), (raw or "")[:200] or None))
            self._remember(cur, remember, name, row["id"])

        u = f" {row_unit}" if row_unit else ""
        arrow = f"{fmt_qty(old)}{u} -> {fmt_qty(new)}{u}"
        if status == "clamped":
            msg = (f"{row['item_name']}: only {fmt_qty(old)}{u} in stock, "
                   f"removed all of it ({arrow})")
        else:
            verb = {"increased": "added", "decreased": "removed", "set": "set"}[status]
            msg = f"{row['item_name']}: {verb} ({arrow})"
        if name_key(name) != name_key(row["item_name"]):
            msg += f"  [counted '{name}' as '{row['item_name']}']"
        return result(status, msg, row["id"], old, new, row_unit)

    @staticmethod
    def _remember(cur, remember, name, row_id):
        """Store the user's answer to a 'similar item?' question so it is not asked again."""
        if not remember:
            return
        kind, value = remember
        key = name_key(name)
        if kind == "alias":
            cur.execute("""
                INSERT INTO item_aliases (alias_key, alias_name, inventory_id) VALUES (%s, %s, %s)
                ON CONFLICT (alias_key) DO UPDATE SET inventory_id = EXCLUDED.inventory_id,
                                                      alias_name = EXCLUDED.alias_name
            """, (key, name, value))
        else:
            for other in set(value) | {row_id}:
                if other != row_id or kind == "distinct":
                    cur.execute("""
                        INSERT INTO item_distinct (inventory_id, name_key) VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (other, key))

    def forget_remembered_matches(self) -> int:
        """Delete every remembered 'merge' and 'keep separate' answer. Returns how many."""
        with self._transaction() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM item_aliases) + (SELECT COUNT(*) FROM item_distinct) AS n")
            n = cur.fetchone()["n"]
            cur.execute("DELETE FROM item_aliases")
            cur.execute("DELETE FROM item_distinct")
        return n

    # ── Name dictionary (several names for one item) ───────────────────────────

    @staticmethod
    def _synonym_map(cur) -> dict:
        """{tuple(name tokens): group_id} for every name in the dictionary."""
        cur.execute("SELECT name_key, group_id FROM name_group_names")
        return {tuple(r["name_key"].split()): r["group_id"] for r in cur.fetchall()}

    def synonym_map(self) -> dict:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return self._synonym_map(cur)

    def get_name_groups(self) -> list:
        """
        All dictionary groups: [{"id", "label", "names": [{"name_key", "display_name"}],
        "items": [inventory rows stored under one of these names]}].
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, label FROM name_groups ORDER BY LOWER(label)")
            groups = {r["id"]: {"id": r["id"], "label": r["label"], "names": [], "items": []}
                      for r in cur.fetchall()}
            cur.execute("SELECT name_key, display_name, group_id FROM name_group_names ORDER BY display_name")
            owner = {}
            for r in cur.fetchall():
                groups[r["group_id"]]["names"].append(
                    {"name_key": r["name_key"], "display_name": r["display_name"]})
                owner[tuple(r["name_key"].split())] = r["group_id"]
            cur.execute("SELECT id, item_name, quantity, unit FROM inventory ORDER BY item_name")
            for row in cur.fetchall():
                gid = owner.get(tuple(name_tokens(row["item_name"])))
                if gid in groups:
                    groups[gid]["items"].append(dict(row))
        return list(groups.values())

    def find_name_group(self, name: str):
        """The dictionary group that contains `name` (a dict as in get_name_groups) or None."""
        key = name_key(name)
        for g in self.get_name_groups():
            if any(n["name_key"] == key for n in g["names"]):
                return g
        return None

    def add_name_group(self, names, label=None) -> int:
        """
        Declare that all of `names` mean the same item. Names already in the dictionary pull
        their whole group in, so overlapping groups are folded into one (adding "mint, spearmint"
        to a group that already holds "pudina" gives one group "pudina, mint, spearmint").
        Returns the id of the resulting group.
        """
        cleaned, seen = [], set()
        for n in names:
            n = " ".join(str(n or "").split())
            k = name_key(n)
            if k and k not in seen:
                seen.add(k)
                cleaned.append((k, n))
        if len(cleaned) < 2:
            raise ValueError("Enter at least two different names for the same item.")
        with self._transaction() as cur:
            cur.execute("SELECT DISTINCT group_id FROM name_group_names WHERE name_key = ANY(%s)",
                        ([k for k, _ in cleaned],))
            existing = sorted(r["group_id"] for r in cur.fetchall())
            if existing:
                target = existing[0]
                for other in existing[1:]:
                    cur.execute("UPDATE name_group_names SET group_id = %s WHERE group_id = %s",
                                (target, other))
                    cur.execute("DELETE FROM name_groups WHERE id = %s", (other,))
                if label:
                    cur.execute("UPDATE name_groups SET label = %s WHERE id = %s", (label.strip(), target))
            else:
                cur.execute("INSERT INTO name_groups (label) VALUES (%s) RETURNING id",
                            ((label or cleaned[0][1]).strip(),))
                target = cur.fetchone()["id"]
            for k, n in cleaned:
                cur.execute("""
                    INSERT INTO name_group_names (name_key, display_name, group_id) VALUES (%s, %s, %s)
                    ON CONFLICT (name_key) DO NOTHING
                """, (k, n, target))
        return target

    def add_name_to_group(self, group_id: int, name: str):
        """Add one more name to an existing group. A name can belong to one group only."""
        n = " ".join(str(name or "").split())
        k = name_key(n)
        if not k:
            raise ValueError("The name is empty.")
        with self._transaction() as cur:
            cur.execute("SELECT g.label FROM name_group_names x JOIN name_groups g ON g.id = x.group_id "
                        "WHERE x.name_key = %s", (k,))
            found = cur.fetchone()
            if found:
                raise ValueError(f"'{n}' is already a name of the group '{found['label']}'.")
            cur.execute("INSERT INTO name_group_names (name_key, display_name, group_id) VALUES (%s, %s, %s)",
                        (k, n, group_id))

    def remove_name(self, name_key_value: str):
        """Remove one name from the dictionary; a group left with a single name is deleted."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM name_group_names WHERE name_key = %s RETURNING group_id",
                        (name_key_value,))
            gone = cur.fetchone()
            if gone:
                cur.execute("SELECT COUNT(*) AS n FROM name_group_names WHERE group_id = %s",
                            (gone["group_id"],))
                if cur.fetchone()["n"] < 2:
                    cur.execute("DELETE FROM name_groups WHERE id = %s", (gone["group_id"],))

    def delete_name_group(self, group_id: int):
        with self._transaction() as cur:
            cur.execute("DELETE FROM name_groups WHERE id = %s", (group_id,))

    def load_starter_names(self) -> int:
        """Add the built-in list of common regional names (default_names.py). Returns groups added."""
        added = 0
        for names in STARTER_NAME_GROUPS:
            try:
                self.add_name_group(names, label=names[0])
                added += 1
            except ValueError:
                pass
        return added

    # ── Merging items that already exist ───────────────────────────────────────

    def find_similar_groups(self):
        """
        Groups of existing rows that look alike (see inventory_logic.find_similar_groups).
        Pairs the user already marked as "keep separate" are not suggested again.
        """
        _, _, ask = self._match_settings()
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM inventory ORDER BY id")
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT inventory_id, name_key FROM item_distinct")
            distinct = {(r["inventory_id"], r["name_key"]) for r in cur.fetchall()}
            synonyms = self._synonym_map(cur)
        return find_similar_groups(rows, ask, distinct, synonyms)

    def mark_items_distinct(self, pairs) -> int:
        """
        Remember that these pairs of existing items are different products ("soya sauce" and
        "hot sauce"): they are not suggested for merging again and a new entry for one of them is
        never matched to the other. `pairs` is an iterable of (row_id_a, row_id_b).
        Returns the number of pairs recorded.
        """
        pairs = {tuple(sorted(p)) for p in pairs if p[0] != p[1]}
        if not pairs:
            return 0
        ids = sorted({i for p in pairs for i in p})
        with self._transaction() as cur:
            cur.execute("SELECT id, item_name FROM inventory WHERE id = ANY(%s)", (ids,))
            names = {r["id"]: r["item_name"] for r in cur.fetchall()}
            count = 0
            for a, b in pairs:
                if a in names and b in names:
                    for owner, other in ((a, b), (b, a)):
                        cur.execute("""
                            INSERT INTO item_distinct (inventory_id, name_key) VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                        """, (owner, name_key(names[other])))
                    count += 1
        return count

    def merge_items(self, canonical_id: int, member_ids: list) -> dict:
        """
        Merge `member_ids` into `canonical_id`: quantities are converted into the canonical row's
        unit and added up, the change history is kept (re-pointed to the canonical row), the most
        recent dates win, and each merged name is remembered as an alias of the canonical item.
        Members whose unit cannot be converted are left untouched.
        Returns {"merged": [names], "skipped": [names], "quantity": new quantity, "unit": unit}.
        """
        merged, skipped = [], []
        with self._transaction() as cur:
            ids = sorted({canonical_id, *member_ids})
            cur.execute("SELECT * FROM inventory WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (ids,))
            rows = {r["id"]: dict(r) for r in cur.fetchall()}
            canon = rows.get(canonical_id)
            if canon is None:
                raise ValueError("The item to keep no longer exists.")
            total = float(canon["quantity"])
            bought, used = canon.get("date_bought"), canon.get("date_used")
            for mid in member_ids:
                m = rows.get(mid)
                if m is None or mid == canonical_id:
                    continue
                value, ok = convert_quantity(float(m["quantity"]), m.get("unit"), canon.get("unit"))
                if not ok:
                    skipped.append(m["item_name"])
                    continue
                total += value
                bought = max([d for d in (bought, m.get("date_bought")) if d], default=None)
                used = max([d for d in (used, m.get("date_used")) if d], default=None)
                cur.execute("UPDATE inventory_log SET inventory_id = %s WHERE inventory_id = %s",
                            (canonical_id, mid))
                cur.execute("""
                    INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                    VALUES (%s, 'merged', %s, %s)
                """, (canonical_id, round(value, 3), f"Merged '{m['item_name']}'"))
                cur.execute("DELETE FROM inventory WHERE id = %s", (mid,))
                cur.execute("""
                    INSERT INTO item_aliases (alias_key, alias_name, inventory_id) VALUES (%s, %s, %s)
                    ON CONFLICT (alias_key) DO UPDATE SET inventory_id = EXCLUDED.inventory_id
                """, (name_key(m["item_name"]), m["item_name"], canonical_id))
                merged.append(m["item_name"])
            total = round(total, 3)
            cur.execute("""
                UPDATE inventory SET quantity = %s, date_bought = %s, date_used = %s, updated_at = NOW()
                WHERE id = %s
            """, (total, bought, used, canonical_id))
        return {"merged": merged, "skipped": skipped, "quantity": total, "unit": canon.get("unit")}

    def clear_all(self) -> int:
        """
        Permanently delete every inventory row and every log entry and restart the id counters.
        Returns the number of inventory rows that were removed.
        """
        with self._transaction() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM inventory")
            count = cur.fetchone()["n"]
            cur.execute("TRUNCATE TABLE inventory_log, inventory RESTART IDENTITY CASCADE")
        return count

    def count_items(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM inventory")
            return cur.fetchone()[0]

    def close(self):
        if self.conn:
            self.conn.close()
