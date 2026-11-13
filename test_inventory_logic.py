"""
Tests for the quantity-update logic.

  python -m unittest test_inventory_logic -v

The first group needs nothing but Python. The database group runs against the PostgreSQL
configured in config.py. It only creates and then deletes its own rows whose names start with
"ZZ test "; it is skipped automatically when PostgreSQL is unreachable.
"""

import unittest

from inventory_logic import (
    MODE_LEGACY, MODE_PROMPT_ALL, MODE_PROMPT_LOW, convert_quantity, decide_match,
    find_similar_groups, fmt_qty, infer_action, name_key, normalize_unit,
    resolve_action, similarity, synonym_tokens,
)
from config import MATCH_CONFIG
from default_names import STARTER_NAME_GROUPS


class UnitTests(unittest.TestCase):
    def test_normalize_unit(self):
        self.assertEqual(normalize_unit("Grams"), "g")
        self.assertEqual(normalize_unit("kilograms"), "kg")
        self.assertEqual(normalize_unit("litres"), "l")
        self.assertIsNone(normalize_unit(None))
        self.assertIsNone(normalize_unit("null"))

    def test_convert(self):
        self.assertEqual(convert_quantity(250, "grams", "kg"), (0.25, True))
        self.assertEqual(convert_quantity(2, "kg", "g"), (2000.0, True))
        self.assertEqual(convert_quantity(500, "ml", "l"), (0.5, True))
        self.assertEqual(convert_quantity(3, "pieces", "pieces"), (3.0, True))
        self.assertFalse(convert_quantity(1, "g", "l")[1])
        self.assertFalse(convert_quantity(1, "boxes", "kg")[1])
        self.assertEqual(convert_quantity(4, None, "kg"), (4.0, True))

    def test_fmt_qty(self):
        self.assertEqual(fmt_qty(0.25), "0.25")
        self.assertEqual(fmt_qty(12.0), "12")
        self.assertEqual(fmt_qty(0.005), "0.005")


class ActionTests(unittest.TestCase):
    def test_reported_bug_sentence(self):
        text = "I have removed 250 grams of active dry yeast"
        self.assertEqual(infer_action(text, "Active Dry Yeast"), "remove")
        self.assertEqual(resolve_action(text, "Active Dry Yeast", model_action=None), "remove")

    def test_explicit_verbs_beat_a_wrong_model_answer(self):
        self.assertEqual(resolve_action("used 2 kg flour", "flour", model_action="add"), "remove")
        self.assertEqual(resolve_action("bought 2 kg flour", "flour", model_action="remove"), "add")

    def test_multi_item_sentence(self):
        text = "bought 3 kg rice and used 1 kg salt today"
        self.assertEqual(infer_action(text, "rice"), "add")
        self.assertEqual(infer_action(text, "salt"), "remove")

    def test_set_and_fallbacks(self):
        self.assertEqual(infer_action("I have 3 kg of rice left", "rice"), "set")
        self.assertEqual(resolve_action("3 kg rice", "rice", model_action=None), "add")   # default
        self.assertEqual(resolve_action("3 kg rice", "rice", model_action="used"), "remove")
        self.assertEqual(resolve_action("3 kg rice", "rice", quantity=-3), "remove")


class SimilarityTests(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(name_key("  Active  Dry Yeasts "), "active dry yeast")
        self.assertEqual(name_key("The tomatoes"), "tomato")

    def test_identical_names_and_spelling_variants_are_confident(self):
        self.assertGreaterEqual(similarity("yest", "yeast"), 0.85)               # typo
        self.assertGreaterEqual(similarity("Tomatoes", "tomato"), 0.85)         # plural
        self.assertEqual(similarity("Basmati Rice", "basmati rice"), 1.0)

    def test_names_containing_one_another_are_related_but_never_confident(self):
        """Stricter rule: kidney beans are beans, but they are not the same item."""
        for a, b in (("yeast", "active dry yeast"), ("rice", "basmati rice"), ("beans", "kidney beans"),
                     ("dry yeast", "active dry yeast"), ("extra virgin olive oil", "virgin olive oil")):
            s = similarity(a, b)
            self.assertTrue(0.55 <= s < 0.85, f"{a} ~ {b} = {s}")
        self.assertLess(similarity("beans", "kidney beans", "Food", "Food"), 0.85)   # category bonus too
    def test_same_head_noun_different_modifiers_is_uncertain(self):
        for a, b in (("active dry yeast", "instant dry yeast"), ("basmati rice", "red rice"),
                     ("olive oil", "sunflower oil")):
            s = similarity(a, b)
            self.assertTrue(0.55 <= s < 0.85, f"{a} ~ {b} = {s}")

    def test_different_products_stay_apart(self):
        self.assertLess(similarity("rice flour", "rice"), 0.55)
        self.assertLess(similarity("tomato paste", "tomato"), 0.55)
        self.assertLess(similarity("sugar", "salt"), 0.55)

    def test_category_nudges_the_score(self):
        self.assertLess(similarity("olive oil", "engine oil", "Food", "Automotive"), 0.55)
        self.assertGreater(similarity("olive oil", "sunflower oil", "Food", "Food"),
                           similarity("olive oil", "sunflower oil"))


def row(i, name, unit="kg", category="Food"):
    return {"id": i, "item_name": name, "unit": unit, "category": category, "updated_at": None}


class DecisionTests(unittest.TestCase):
    ROWS = [row(1, "Yeast"), row(2, "Basmati Rice"), row(3, "Salt")]

    def decide(self, name, unit="g", mode=MODE_PROMPT_LOW, rows=None, **kw):
        return decide_match(rows or self.ROWS, name, unit, "Food", mode=mode, **kw)

    def test_exact_match_never_asks(self):
        for mode in (MODE_LEGACY, MODE_PROMPT_LOW, MODE_PROMPT_ALL):
            d = self.decide("yeasts", mode=mode)
            self.assertEqual((d.kind, d.row["id"]), ("exact", 1))

    def test_spelling_variant_is_merged_silently(self):
        d = self.decide("yest")
        self.assertEqual((d.kind, d.row["id"]), ("auto", 1))

    def test_name_containing_an_existing_name_asks(self):
        """'instant dry yeast' contains 'yeast' but may be a different product: ask, never merge silently."""
        d = self.decide("instant dry yeast")
        self.assertEqual((d.kind, d.candidates[0].row["id"]), ("ask", 1))
    def test_uncertain_variant_asks(self):
        d = self.decide("red rice")
        self.assertEqual(d.kind, "ask")
        self.assertEqual(d.candidates[0].row["id"], 2)

    def test_prompt_all_asks_even_when_confident(self):
        self.assertEqual(self.decide("yest", mode=MODE_PROMPT_ALL).kind, "ask")
    def test_legacy_keeps_everything_separate(self):
        self.assertEqual(self.decide("instant dry yeast", mode=MODE_LEGACY).kind, "new")
        self.assertEqual(self.decide("red rice", mode=MODE_LEGACY).kind, "new")

    def test_nothing_similar_is_new(self):
        self.assertEqual(self.decide("sugar").kind, "new")

    def test_two_equally_good_rows_ask(self):
        rows = [row(1, "Active Dry Yeast"), row(2, "Instant Dry Yeast")]
        self.assertEqual(self.decide("yeast", rows=rows).kind, "ask")

    def test_remembered_choices(self):
        d = self.decide("red rice", aliases={"red rice": 2})
        self.assertEqual((d.kind, d.row["id"], d.via), ("exact", 2, "alias"))
        d = self.decide("red rice", distinct={(2, "red rice")})
        self.assertEqual(d.kind, "new")

    def test_incompatible_units_are_not_merged(self):
        self.assertEqual(self.decide("instant dry yeast", unit="l").kind, "new")


class GroupTests(unittest.TestCase):
    def test_groups(self):
        rows = [row(1, "Yeast"), row(2, "Active Dry Yeast"), row(3, "Instant Dry Yeast"),
                row(4, "Rice"), row(5, "Basmati Rice"), row(6, "Red Rice"), row(7, "Salt"),
                row(8, "Rice Flour")]
        groups = find_similar_groups(rows, 0.55)
        by_canon = {g["canonical"]["item_name"]: sorted(m["row"]["item_name"] for m in g["members"])
                    for g in groups}
        self.assertEqual(by_canon["Yeast"], ["Active Dry Yeast", "Instant Dry Yeast"])
        self.assertEqual(by_canon["Rice"], ["Basmati Rice", "Red Rice"])
        self.assertNotIn("Salt", by_canon)
        flat = [m["row"]["item_name"] for g in groups for m in g["members"]]
        self.assertNotIn("Rice Flour", flat)             # different product

    def test_items_sharing_a_head_noun_are_grouped_but_not_confident(self):
        rows = [row(1, "Kidney Beans"), row(2, "Mung Beans"), row(3, "Red Beans"),
                row(4, "Soya Sauce"), row(5, "Hot Sauce")]
        groups = find_similar_groups(rows, 0.55)
        self.assertEqual(len(groups), 2)
        for g in groups:                       # similar, yet below the "confident" threshold
            for m in g["members"]:
                self.assertLess(m["score"], 0.85)

    def test_pairs_kept_separate_are_not_suggested_again(self):
        rows = [row(1, "Soya Sauce"), row(2, "Hot Sauce")]
        self.assertEqual(len(find_similar_groups(rows, 0.55)), 1)
        self.assertEqual(find_similar_groups(rows, 0.55, {(1, "hot sauce"), (2, "soya sauce")}), [])
        self.assertEqual(find_similar_groups(rows, 0.55, {(2, "soya sauce")}), [])   # one direction is enough

    def test_unit_incompatible_member_is_flagged(self):
        groups = find_similar_groups([row(1, "Yeast", "kg"), row(2, "Dry Yeast", "l")], 0.55)
        self.assertFalse(groups[0]["members"][0]["mergeable"])


class DictionaryLogicTests(unittest.TestCase):
    """Several names for one item (pudina = mint) without any database."""

    SYN = {("pudina",): 1, ("mint",): 1, ("curry", "leaf"): 2, ("kadi", "patta"): 2}

    def test_dictionary_names_become_the_same_token(self):
        self.assertEqual(synonym_tokens("Pudina leaves", self.SYN), ["§1", "leaf"])
        self.assertEqual(synonym_tokens("Kadi Patta", self.SYN), ["§2"])          # phrase
        self.assertEqual(synonym_tokens("salt", self.SYN), ["salt"])
        self.assertEqual(synonym_tokens("pudina", None), ["pudina"])

    def test_similarity_with_and_without_the_dictionary(self):
        self.assertLess(similarity("pudina", "mint"), 0.55)                         # nothing in common
        self.assertEqual(similarity("pudina", "mint", synonyms=self.SYN), 1.0)
        self.assertEqual(similarity("Pudina leaves", "mint leaves", synonyms=self.SYN), 1.0)
        self.assertEqual(similarity("curry leaf", "kadi patta", synonyms=self.SYN), 1.0)
        self.assertLess(similarity("pudina", "coriander", synonyms=self.SYN), 0.55)

    def test_dictionary_match_is_silent_in_every_mode(self):
        rows = [row(1, "Mint")]
        for mode in (MODE_LEGACY, MODE_PROMPT_LOW, MODE_PROMPT_ALL):
            d = decide_match(rows, "pudina", "g", "Food", mode=mode, synonyms=self.SYN)
            self.assertEqual((d.kind, d.row["id"], d.via), ("exact", 1, "dictionary"))

    def test_both_directions(self):
        rows = [row(1, "Pudina")]
        d = decide_match(rows, "Mint", "g", "Food", synonyms=self.SYN)
        self.assertEqual((d.kind, d.row["id"]), ("exact", 1))

    def test_name_with_modifiers_is_only_related(self):
        rows = [row(1, "Mint")]
        d = decide_match(rows, "pudina leaves", "g", "Food", synonyms=self.SYN)
        self.assertEqual(d.kind, "new")                    # different head noun (leaf vs mint)

    def test_merge_tool_groups_rows_that_use_different_names(self):
        rows = [row(1, "Mint"), row(2, "Pudina"), row(3, "Salt")]
        groups = find_similar_groups(rows, 0.55, synonyms=self.SYN)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["members"][0]["score"], 1.0)
        self.assertEqual(find_similar_groups(rows, 0.55), [])                        # unrelated without it

    def test_starter_dictionary_is_well_formed(self):
        seen = {}
        for names in STARTER_NAME_GROUPS:
            self.assertGreaterEqual(len(names), 2)
            for n in names:
                k = name_key(n)
                self.assertIn(seen.setdefault(k, id(names)), (id(names),),
                              f"'{n}' appears in two different starter groups")
        self.assertIn(["mint", "pudina"], STARTER_NAME_GROUPS)


# ── Database round trip ───────────────────────────────────────────────────────
try:
    from database import DatabaseManager
    _db = DatabaseManager()
except Exception:  # PostgreSQL not reachable
    _db = None


@unittest.skipIf(_db is None, "PostgreSQL not reachable")
class StockChangeTests(unittest.TestCase):
    P = "ZZ test "

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with _db.conn.cursor() as cur:
            cur.execute("DELETE FROM inventory WHERE item_name LIKE %s", (self.P + "%",))

    def _row(self, name):
        rows = [r for r in _db.get_all_items() if r["item_name"].lower() == (self.P + name).lower()]
        return rows

    def change(self, name, action, qty, unit, **extra):
        item = {"item_name": self.P + name, "action": action, "quantity": qty, "unit": unit,
                "category": "Food", "date_used": None, "date_bought": None, "notes": None,
                "raw_input": "test"}
        item.update(extra)
        return _db.apply_stock_change(item)

    def test_removal_updates_existing_row_and_converts_units(self):
        self.change("active dry yeast", "add", 2, "kg")
        res = self.change("Active Dry Yeast", "remove", 250, "grams")     # the reported scenario
        rows = self._row("active dry yeast")
        self.assertEqual(len(rows), 1, "must not create a second row")
        self.assertEqual(float(rows[0]["quantity"]), 1.75)
        self.assertEqual(rows[0]["unit"], "kg")
        self.assertEqual(res["status"], "decreased")

    def test_add_merges_into_existing_row(self):
        self.change("flour", "add", 1, "kg")
        self.change("flour", "add", 500, "g")
        rows = self._row("flour")
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["quantity"]), 1.5)

    def test_remove_more_than_stock_clamps_to_zero(self):
        self.change("salt", "add", 1, "kg")
        res = self.change("salt", "remove", 5, "kg")
        self.assertEqual(res["status"], "clamped")
        self.assertEqual(float(self._row("salt")[0]["quantity"]), 0.0)

    def test_remove_unknown_item_creates_nothing(self):
        res = self.change("ghost item", "remove", 1, "kg")
        self.assertEqual(res["status"], "not_found")
        self.assertEqual(self._row("ghost item"), [])

    def test_unit_mismatch_changes_nothing(self):
        self.change("oil", "add", 2, "l")
        res = self.change("oil", "remove", 100, "g")
        self.assertEqual(res["status"], "unit_mismatch")
        self.assertEqual(float(self._row("oil")[0]["quantity"]), 2.0)

    def test_set_overwrites(self):
        self.change("rice", "add", 5, "kg")
        self.change("rice", "set", 3, "kg")
        self.assertEqual(float(self._row("rice")[0]["quantity"]), 3.0)

    def test_small_amounts_keep_precision(self):
        self.change("saffron", "add", 1, "kg")
        self.change("saffron", "remove", 5, "g")                           # 0.005 kg
        self.assertEqual(float(self._row("saffron")[0]["quantity"]), 0.995)

    def test_new_item_is_created_once(self):
        self.change("pepper", "add", 2, "pieces")
        self.assertEqual(len(self._row("pepper")), 1)



@unittest.skipIf(_db is None, "PostgreSQL not reachable")
class FuzzyDatabaseTests(unittest.TestCase):
    """Variants of one item (yeast / active dry yeast / instant dry yeast) share a single row."""

    P = "ZZ fz "

    def setUp(self):
        self._saved = dict(MATCH_CONFIG)
        MATCH_CONFIG.update(mode="prompt_low", auto_threshold=0.85, ask_threshold=0.55)
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        MATCH_CONFIG.clear(); MATCH_CONFIG.update(self._saved)

    def _cleanup(self):
        with _db.conn.cursor() as cur:
            cur.execute("DELETE FROM inventory WHERE item_name LIKE %s", (self.P + "%",))
            cur.execute("DELETE FROM item_aliases WHERE alias_name LIKE %s", (self.P + "%",))

    def rows(self, like):
        return [r for r in _db.get_all_items() if like.lower() in r["item_name"].lower()
                and r["item_name"].startswith(self.P)]

    def put(self, name, action, qty, unit, chooser=None, category="Food"):
        return _db.apply_stock_change(
            {"item_name": self.P + name, "action": action, "quantity": qty, "unit": unit,
             "category": category, "raw_input": "test"}, chooser=chooser)

    def test_spelling_variant_updates_the_existing_row_silently(self):
        self.put("yeast", "add", 1, "kg")
        self.put("yeasts", "add", 250, "g", chooser=lambda d, i: self.fail("must not ask"))
        rows = self.rows("yeast")
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["quantity"]), 1.25)

    def test_related_name_asks_and_a_yes_updates_the_generic_row(self):
        self.put("yeast", "add", 1, "kg")
        asked = []
        def chooser(decision, item):
            asked.append(decision.kind)
            return {"action": "merge", "row_id": decision.candidates[0].row["id"], "remember": False}
        res = self.put("instant dry yeast", "add", 250, "g", chooser=chooser)
        self.assertEqual(asked, ["ask"])
        rows = self.rows("yeast")
        self.assertEqual((len(rows), float(rows[0]["quantity"])), (1, 1.25))
        self.assertIn("counted", res["message"])
    def test_removal_through_a_related_name_after_confirming(self):
        self.put("rice", "add", 5, "kg")
        self.put("basmati rice", "remove", 500, "g",
                 chooser=lambda d, i: {"action": "merge", "row_id": d.candidates[0].row["id"], "remember": False})
        rows = self.rows("rice")
        self.assertEqual((len(rows), float(rows[0]["quantity"])), (1, 4.5))
    def test_uncertain_match_asks_and_can_merge_and_remember(self):
        self.put("basmati rice", "add", 2, "kg")
        asked = []

        def chooser(decision, item):
            asked.append([c.row["item_name"] for c in decision.candidates])
            return {"action": "merge", "row_id": decision.candidates[0].row["id"], "remember": True}

        self.put("red rice", "add", 1, "kg", chooser=chooser)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(self.rows("rice")), 1)
        self.assertEqual(float(self.rows("rice")[0]["quantity"]), 3.0)
        # remembered: no question the second time
        self.put("red rice", "add", 1, "kg", chooser=lambda d, i: self.fail("should not ask again"))
        self.assertEqual(float(self.rows("rice")[0]["quantity"]), 4.0)

    def test_keep_separate_and_remember(self):
        self.put("basmati rice", "add", 2, "kg")
        self.put("red rice", "add", 1, "kg", chooser=lambda d, i: {"action": "new", "remember": True})
        self.assertEqual(len(self.rows("rice")), 2)
        self.put("red rice", "add", 1, "kg", chooser=lambda d, i: self.fail("should not ask again"))
        self.assertEqual(len(self.rows("rice")), 2)

    def test_skip_changes_nothing(self):
        self.put("basmati rice", "add", 2, "kg")
        res = self.put("red rice", "add", 1, "kg", chooser=lambda d, i: {"action": "skip"})
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(len(self.rows("rice")), 1)

    def test_without_a_chooser_uncertain_adds_stay_separate_and_removals_do_nothing(self):
        self.put("basmati rice", "add", 2, "kg")
        self.put("red rice", "add", 1, "kg")
        self.assertEqual(len(self.rows("rice")), 2)
        self.put("rice cooker", "add", 1, "pieces", category="Appliance")
        res = self.put("brown rice", "remove", 1, "kg")
        self.assertEqual(res["status"], "ambiguous")

    def test_modes(self):
        self.put("yeast", "add", 1, "kg")
        MATCH_CONFIG["mode"] = "legacy"
        self.put("active dry yeast", "add", 100, "g")
        self.assertEqual(len(self.rows("yeast")), 2)               # kept separate
        MATCH_CONFIG["mode"] = "prompt_all"
        asked = []
        self.put("instant dry yeast", "add", 100, "g",
                 chooser=lambda d, i: asked.append(1) or {"action": "skip"})
        self.assertEqual(len(asked), 1)                            # asked even though confident

    def test_merge_existing_items(self):
        MATCH_CONFIG["mode"] = "legacy"
        self.put("yeast", "add", 1, "kg")
        self.put("active dry yeast", "add", 250, "g")
        self.put("instant dry yeast", "add", 500, "g")
        MATCH_CONFIG["mode"] = "prompt_low"
        group = [g for g in _db.find_similar_groups()
                 if g["canonical"]["item_name"] == self.P + "yeast"][0]
        ids = [m["row"]["id"] for m in group["members"]]
        out = _db.merge_items(group["canonical"]["id"], ids)
        self.assertEqual(out["quantity"], 1.75)
        self.assertEqual(len(self.rows("yeast")), 1)
        with _db.conn.cursor() as cur:                             # history survives, aliases saved
            cur.execute("SELECT COUNT(*) FROM inventory_log WHERE inventory_id = %s",
                        (group["canonical"]["id"],))
            self.assertGreaterEqual(cur.fetchone()[0], 3)
        self.assertEqual(self.put("active dry yeast", "add", 250, "g")["status"], "increased")

    def test_keep_separate_pairs_are_remembered_by_the_merge_tool(self):
        MATCH_CONFIG["mode"] = "legacy"
        self.put("soya sauce", "add", 1, "l")
        self.put("hot sauce", "add", 1, "l")
        MATCH_CONFIG["mode"] = "prompt_low"
        ours = lambda: [g for g in _db.find_similar_groups()
                        if g["canonical"]["item_name"].startswith(self.P)]
        self.assertEqual(len(ours()), 1)
        ids = [r["id"] for r in _db.get_all_items() if r["item_name"].startswith(self.P)]
        self.assertEqual(_db.mark_items_distinct([(ids[0], ids[1])]), 1)
        self.assertEqual(ours(), [])
        # and a new entry is no longer offered the other sauce
        self.assertEqual(_db.resolve_match({"item_name": self.P + "hot sauce", "unit": "l"}).kind, "exact")

    def test_forget_remembered_matches(self):
        self.put("basmati rice", "add", 2, "kg")
        self.put("red rice", "add", 1, "kg", chooser=lambda d, i: {"action": "new", "remember": True})
        self.assertGreaterEqual(_db.forget_remembered_matches(), 1)



@unittest.skipIf(_db is None, "PostgreSQL not reachable")
class NameDictionaryDatabaseTests(unittest.TestCase):
    """The name dictionary stored in PostgreSQL (uses made-up single words starting with 'zz')."""

    def setUp(self):
        self._saved = dict(MATCH_CONFIG)
        MATCH_CONFIG.update(mode="prompt_low", auto_threshold=0.85, ask_threshold=0.55)
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        MATCH_CONFIG.clear(); MATCH_CONFIG.update(self._saved)

    def _cleanup(self):
        with _db.conn.cursor() as cur:
            cur.execute("DELETE FROM inventory WHERE LOWER(item_name) LIKE '%zz%'")
            cur.execute("DELETE FROM name_groups WHERE id IN "
                        "(SELECT group_id FROM name_group_names WHERE name_key LIKE '%zz%')")

    def rows(self):
        return [r for r in _db.get_all_items() if "zz" in r["item_name"].lower()]

    def put(self, name, qty=1, unit="kg", action="add", chooser=None):
        return _db.apply_stock_change({"item_name": name, "action": action, "quantity": qty, "unit": unit,
                                       "category": "Food", "raw_input": "test"}, chooser=chooser)

    def test_a_group_needs_two_names(self):
        with self.assertRaises(ValueError):
            _db.add_name_group(["zzmint"])
        with self.assertRaises(ValueError):
            _db.add_name_group(["zzmint", "ZZMint "])          # the same name twice

    def test_either_name_updates_the_same_item(self):
        self.put("zzmint", 1, "kg")
        _db.add_name_group(["zzmint", "zzpudina"])
        res = self.put("zzpudina", 500, "g", chooser=lambda d, i: self.fail("must not ask"))
        rows = self.rows()
        self.assertEqual((len(rows), float(rows[0]["quantity"])), (1, 1.5))
        self.assertIn("counted 'zzpudina' as 'zzmint'", res["message"])
        self.put("ZZMINT", 250, "g", action="remove")                       # and the other way round
        self.assertEqual(float(self.rows()[0]["quantity"]), 1.25)

    def test_dictionary_defined_before_any_item_exists(self):
        _db.add_name_group(["zzmint", "zzpudina"])
        self.put("zzpudina", 2, "kg")
        self.put("zzmint", 1, "kg")                                         # same item, other name
        rows = self.rows()
        self.assertEqual((len(rows), rows[0]["item_name"], float(rows[0]["quantity"])), (1, "zzpudina", 3.0))

    def test_names_inside_longer_names(self):
        self.put("fresh zzmint", 1, "kg")
        _db.add_name_group(["zzmint", "zzpudina"])
        self.put("fresh zzpudina", 1, "kg", chooser=lambda d, i: self.fail("must not ask"))
        rows = self.rows()
        self.assertEqual((len(rows), float(rows[0]["quantity"])), (1, 2.0))

    def test_overlapping_groups_are_folded_together(self):
        a = _db.add_name_group(["zzmint", "zzpudina"])
        b = _db.add_name_group(["zzpudina", "zzspearmint"])
        self.assertEqual(a, b)
        g = _db.find_name_group("zzspearmint")
        self.assertEqual(sorted(n["name_key"] for n in g["names"]), ["zzmint", "zzpudina", "zzspearmint"])

    def test_add_remove_and_conflicts(self):
        gid = _db.add_name_group(["zzmint", "zzpudina"])
        _db.add_name_to_group(gid, "zzpodina")
        with self.assertRaises(ValueError):
            _db.add_name_to_group(gid, "ZZPudina")                           # already there
        _db.remove_name("zzpodina")
        self.assertEqual(len(_db.find_name_group("zzmint")["names"]), 2)
        _db.remove_name("zzpudina")                                          # one name left: group disappears
        self.assertIsNone(_db.find_name_group("zzmint"))
        self.assertEqual(_db.resolve_match({"item_name": "zzpudina", "unit": "kg"}).kind, "new")

    def test_merge_tool_suggests_rows_stored_under_different_names(self):
        self.put("zzmint", 1, "kg"); self.put("zzpudina", 2, "kg")
        ours = lambda: [g for g in _db.find_similar_groups()
                        if "zz" in g["canonical"]["item_name"].lower()]
        self.assertEqual(ours(), [])                                          # no relation known yet
        _db.add_name_group(["zzmint", "zzpudina"])
        groups = ours()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["members"][0]["score"], 1.0)
        out = _db.merge_items(groups[0]["canonical"]["id"], [groups[0]["members"][0]["row"]["id"]])
        self.assertEqual(out["quantity"], 3.0)

    def test_get_name_groups_lists_the_items_stored_under_the_names(self):
        self.put("zzmint", 1, "kg")
        _db.add_name_group(["zzmint", "zzpudina"], label="zz herb")
        g = _db.find_name_group("zzpudina")
        self.assertEqual((g["label"], [i["item_name"] for i in g["items"]]), ("zz herb", ["zzmint"]))

    def test_erase_all_keeps_the_dictionary(self):
        _db.add_name_group(["zzmint", "zzpudina"])
        # clear_all() would wipe the user's real data in this database, so only check the table
        # relationships: the dictionary does not reference inventory rows.
        with _db.conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) FROM information_schema.table_constraints c
                           JOIN information_schema.constraint_column_usage u ON c.constraint_name = u.constraint_name
                           WHERE c.table_name = 'name_group_names' AND u.table_name = 'inventory'""")
            self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
