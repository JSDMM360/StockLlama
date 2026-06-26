#!/usr/bin/env python3
"""
Pantry Tracker — natural-language inventory management via Ollama + PostgreSQL.

Usage:
    python pantry_tracker.py
    python pantry_tracker.py --setup          # create DB table and exit
    python pantry_tracker.py --list           # print current inventory and exit
    python pantry_tracker.py --reset          # drop + recreate table and exit

Environment variables (or edit config.py):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    OLLAMA_HOST, OLLAMA_MODEL
"""

import json
import os
import re
import sys
import textwrap
import datetime
from typing import Any

import psycopg2
import psycopg2.extras
import requests

# ──────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ──────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "dbname":   os.getenv("DB_NAME",     "pantry"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")   # change to any local model

# ──────────────────────────────────────────────────────────────
# Database helpers
# ──────────────────────────────────────────────────────────────

def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**DB_CONFIG)


def setup_db() -> None:
    """Create the pantry_items table if it does not exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS pantry_items (
        id            SERIAL PRIMARY KEY,
        name          TEXT    NOT NULL UNIQUE,
        quantity_kg   NUMERIC(12, 3) NOT NULL DEFAULT 0,
        last_updated  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    print("✅  Database table 'pantry_items' is ready.")


def reset_db() -> None:
    """Drop and recreate the pantry_items table."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS pantry_items;")
        conn.commit()
    setup_db()
    print("🔄  Table reset.")


def list_inventory() -> list[dict]:
    """Return all rows as a list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT name, quantity_kg, last_updated "
                "FROM pantry_items ORDER BY name;"
            )
            return [dict(r) for r in cur.fetchall()]


def print_inventory() -> None:
    rows = list_inventory()
    if not rows:
        print("  (inventory is empty)")
        return
    w = max(len(r["name"]) for r in rows) + 2
    print(f"\n  {'Item':<{w}}  {'Qty (kg)':>10}  Last updated")
    print("  " + "─" * (w + 30))
    for r in rows:
        ts = r["last_updated"].strftime("%Y-%m-%d %H:%M")
        print(f"  {r['name']:<{w}}  {float(r['quantity_kg']):>10.3f}  {ts}")
    print()


def apply_operations(operations: list[dict]) -> list[str]:
    """
    Apply a list of {name, delta_kg, purchased_at} dicts to the DB.

    Rules
    ─────
    • Positive delta  → upsert (add to existing or create with that qty)
    • Negative delta  → reduce existing quantity; floor at 0; warn if item unknown
    • purchased_at    → stored in last_updated (NULL → NOW())
    """
    messages: list[str] = []

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for op in operations:
                name        = op["name"].strip().lower()
                delta       = float(op["delta_kg"])
                purchased_at = op.get("purchased_at")  # may be None or ISO string

                # Fetch current row
                cur.execute(
                    "SELECT id, quantity_kg FROM pantry_items WHERE name = %s;",
                    (name,)
                )
                row = cur.fetchone()

                if delta > 0:
                    # ── ADD ──────────────────────────────────────────────
                    if row:
                        new_qty = float(row["quantity_kg"]) + delta
                        cur.execute(
                            "UPDATE pantry_items "
                            "SET quantity_kg = %s, last_updated = %s "
                            "WHERE id = %s;",
                            (new_qty, _ts(purchased_at), row["id"])
                        )
                        messages.append(
                            f"  ➕  {name}: {float(row['quantity_kg']):.3f} kg → {new_qty:.3f} kg"
                        )
                    else:
                        cur.execute(
                            "INSERT INTO pantry_items (name, quantity_kg, last_updated) "
                            "VALUES (%s, %s, %s);",
                            (name, delta, _ts(purchased_at))
                        )
                        messages.append(f"  🆕  {name}: added {delta:.3f} kg (new item)")

                elif delta < 0:
                    # ── REDUCE ───────────────────────────────────────────
                    if row:
                        current = float(row["quantity_kg"])
                        reduction = abs(delta)
                        if reduction > current:
                            messages.append(
                                f"  ⚠️   {name}: tried to reduce by {reduction:.3f} kg "
                                f"but only {current:.3f} kg available — set to 0 kg"
                            )
                            new_qty = 0.0
                        else:
                            new_qty = current - reduction
                        cur.execute(
                            "UPDATE pantry_items "
                            "SET quantity_kg = %s, last_updated = %s "
                            "WHERE id = %s;",
                            (new_qty, _ts(purchased_at), row["id"])
                        )
                        if reduction <= current:
                            messages.append(
                                f"  ➖  {name}: {current:.3f} kg → {new_qty:.3f} kg"
                            )
                    else:
                        messages.append(
                            f"  ⚠️   {name}: cannot reduce — item not found in inventory"
                        )
                else:
                    messages.append(f"  ℹ️   {name}: delta is 0, nothing to do")

        conn.commit()

    return messages


def _ts(value: Any) -> datetime.datetime:
    """Convert an ISO string / None / datetime to a timezone-aware datetime."""
    if value is None:
        return datetime.datetime.now(tz=datetime.timezone.utc)
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value
    # string
    try:
        dt = datetime.datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return datetime.datetime.now(tz=datetime.timezone.utc)


# ──────────────────────────────────────────────────────────────
# Ollama LLM helpers
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""
You are a precise pantry inventory assistant.

Your ONLY job is to extract ingredient operations from natural language and return them as JSON.

Rules:
1. "bought", "purchased", "got", "added", "have", "received" → POSITIVE delta (adding stock)
2. "used", "consumed", "cooked with", "ate", "removed", "reduced", "finished", "spent" → NEGATIVE delta (using stock)
3. Normalise all weights to KILOGRAMS (kg). Examples:
   - "500g" → 0.5
   - "2 lbs" → 0.907
   - "1 pound" → 0.454
   - "1 dozen eggs" — treat count as items, use quantity 12 with unit "pcs" → convert to kg: 0.72 (60g each)
   - If no unit is given, assume kg.
4. Normalise ingredient names to lowercase singular (e.g. "tomatoes" → "tomato", "onions" → "onion").
5. Extract the purchase/transaction date if mentioned; otherwise set it to null.
6. If a sentence is ambiguous (e.g. "I have 3kg rice"), treat it as ADDING stock.

Return ONLY a valid JSON object — no explanation, no markdown, no code fences — in this exact schema:

{
  "operations": [
    {
      "name": "<ingredient name, lowercase singular>",
      "delta_kg": <float, positive = add, negative = use>,
      "purchased_at": "<ISO 8601 date string or null>"
    }
  ]
}

If you cannot find any ingredient operations, return:
{"operations": []}
""").strip()


def query_ollama(user_text: str) -> str:
    """Send a prompt to Ollama and return the raw text response."""
    payload = {
        "model":  OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_text},
        ],
    }
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_HOST}. "
            "Is `ollama serve` running?"
        )
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}")

    data = resp.json()
    return data["message"]["content"]


def parse_llm_response(raw: str) -> list[dict]:
    """
    Extract the JSON object from the LLM response.
    Handles accidental markdown fences and stray text gracefully.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()

    # Try to find a JSON object anywhere in the response
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response:\n{raw}")

    parsed = json.loads(match.group())
    ops = parsed.get("operations", [])

    # Validate each operation
    validated = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        name = op.get("name", "").strip()
        delta = op.get("delta_kg")
        if not name or delta is None:
            continue
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            continue
        validated.append({
            "name":         name,
            "delta_kg":     delta,
            "purchased_at": op.get("purchased_at"),
        })
    return validated


# ──────────────────────────────────────────────────────────────
# Main interactive loop
# ──────────────────────────────────────────────────────────────

def main() -> None:
    # CLI flags
    if "--setup" in sys.argv:
        setup_db()
        return
    if "--reset" in sys.argv:
        reset_db()
        return
    if "--list" in sys.argv:
        setup_db()
        print_inventory()
        return

    # Normal interactive mode
    setup_db()

    print("\n" + "═" * 58)
    print("  🥦  Pantry Tracker  (Ollama + PostgreSQL)")
    print("═" * 58)
    print(f"  Model : {OLLAMA_MODEL}  @  {OLLAMA_HOST}")
    print(f"  DB    : {DB_CONFIG['dbname']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print("─" * 58)
    print("  Type your inventory update in plain English.")
    print("  Commands: 'list'  'quit'")
    print("─" * 58 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye! 👋")
            break

        if not user_input:
            continue

        if user_input.lower() in {"quit", "exit", "q"}:
            print("Bye! 👋")
            break

        if user_input.lower() in {"list", "ls", "inventory", "show"}:
            print("\n📦  Current Inventory:")
            print_inventory()
            continue

        print("🤖  Asking LLM…", end="", flush=True)

        try:
            raw_response = query_ollama(user_input)
        except RuntimeError as exc:
            print(f"\n❌  Ollama error: {exc}\n")
            continue

        print(" done.")

        try:
            operations = parse_llm_response(raw_response)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"❌  Could not parse LLM response: {exc}")
            print(f"   Raw response: {raw_response}\n")
            continue

        if not operations:
            print("ℹ️   No ingredient operations detected. Try again.\n")
            continue

        print(f"\n📝  Detected {len(operations)} operation(s):")
        for op in operations:
            sign  = "+" if op["delta_kg"] >= 0 else ""
            dated = f"  (date: {op['purchased_at']})" if op["purchased_at"] else ""
            print(f"     • {op['name']}: {sign}{op['delta_kg']:.3f} kg{dated}")

        results = apply_operations(operations)
        print("\n✅  Database updated:")
        for msg in results:
            print(msg)
        print()
