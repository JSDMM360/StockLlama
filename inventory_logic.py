"""
Pure inventory logic (no database, no GUI, no Ollama).

This module decides *what a parsed entry means*:
  * is it an addition, a removal or an absolute count?   -> infer_action / resolve_action
  * which existing row does it refer to?                  -> similarity / decide_match
  * how do "250 grams" and a row stored in "kg" relate?   -> convert_quantity

Keeping it free of side effects makes it easy to unit test (see test_inventory_logic.py).
"""

import re
from difflib import SequenceMatcher

# ── Actions ───────────────────────────────────────────────────────────────────
ACTION_ADD = "add"
ACTION_REMOVE = "remove"
ACTION_SET = "set"
VALID_ACTIONS = (ACTION_ADD, ACTION_REMOVE, ACTION_SET)

# Words a small LLM might return in the "action" field, mapped to our three actions.
_ACTION_SYNONYMS = {
    "add": ACTION_ADD, "added": ACTION_ADD, "buy": ACTION_ADD, "bought": ACTION_ADD,
    "purchase": ACTION_ADD, "purchased": ACTION_ADD, "restock": ACTION_ADD,
    "receive": ACTION_ADD, "received": ACTION_ADD, "increase": ACTION_ADD, "in": ACTION_ADD,
    "remove": ACTION_REMOVE, "removed": ACTION_REMOVE, "use": ACTION_REMOVE,
    "used": ACTION_REMOVE, "consume": ACTION_REMOVE, "consumed": ACTION_REMOVE,
    "deduct": ACTION_REMOVE, "subtract": ACTION_REMOVE, "decrease": ACTION_REMOVE,
    "reduce": ACTION_REMOVE, "sell": ACTION_REMOVE, "sold": ACTION_REMOVE, "out": ACTION_REMOVE,
    "set": ACTION_SET, "update": ACTION_SET, "count": ACTION_SET, "recount": ACTION_SET,
    "stocktake": ACTION_SET,
}

# Verb patterns used to read the action straight from the user's sentence. The model is
# small and unreliable here, so explicit verbs in the text take priority over its answer.
_REMOVE_RE = re.compile(
    r"\b(remov\w*|us(?:e|ed|ing)|used\s+up|consum\w*|took|taken|take|sold|sell|finish(?:ed)?|"
    r"ate|eaten|eat|threw|thrown|throw|discard\w*|dispos\w*|spent|spend|deduct\w*|subtract\w*|"
    r"reduc\w*|cook(?:ed)?|bak(?:e|ed)|wast(?:e|ed)|expired|gave\s+away|given\s+away|"
    r"took\s+out|taking\s+out|withdr\w+|dropp?ed|lost|broke)\b", re.I)
_ADD_RE = re.compile(
    r"\b(bought|buy|buying|purchas\w*|add(?:ed|ing)?|receiv\w*|got|get|restock\w*|refill\w*|"
    r"deliver\w*|picked\s+up|stock(?:ed)?\s+up|donat\w*|brought|bring|ordered|arrived)\b", re.I)
_SET_RE = re.compile(
    r"\b(left|remaining|remains?|set\s+to|stock\s*take|stocktake|recount\w*|now\s+have|"
    r"currently\s+have|total\s+of|only\s+have)\b", re.I)

# ── Units ─────────────────────────────────────────────────────────────────────
# canonical symbol -> (family, size of one unit in the family's base unit)
_UNITS = {
    "mg": ("mass", 0.001), "g": ("mass", 1.0), "kg": ("mass", 1000.0),
    "oz": ("mass", 28.349523125), "lb": ("mass", 453.59237),
    "ml": ("volume", 1.0), "l": ("volume", 1000.0), "tsp": ("volume", 4.92892159375),
    "tbsp": ("volume", 14.78676478125), "cup": ("volume", 236.5882365),
    "fl oz": ("volume", 29.5735295625),
}

_UNIT_ALIASES = {
    "mg": "mg", "milligram": "mg", "milligrams": "mg", "milligramme": "mg", "milligrammes": "mg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "kilogramme": "kg", "kilogrammes": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "ml": "ml", "mls": "ml", "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "millilitres": "ml",
    "l": "l", "lt": "l", "ltr": "l", "ltrs": "l", "liter": "l", "liters": "l", "litre": "l",
    "litres": "l",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "cup": "cup", "cups": "cup",
    "fl oz": "fl oz", "floz": "fl oz", "fluid ounce": "fl oz", "fluid ounces": "fl oz",
    "piece": "pieces", "pieces": "pieces", "pc": "pieces", "pcs": "pieces", "unit": "pieces",
    "units": "pieces", "item": "pieces", "items": "pieces",
    "box": "boxes", "boxes": "boxes", "pack": "packs", "packs": "packs", "packet": "packs",
    "packets": "packs", "bottle": "bottles", "bottles": "bottles", "can": "cans", "cans": "cans",
    "bag": "bags", "bags": "bags", "dozen": "dozen", "dozens": "dozen",
}


def normalize_unit(unit):
    """Return a canonical unit symbol ('grams' -> 'g'), the cleaned text if unknown, or None."""
    if unit is None:
        return None
    text = re.sub(r"\s+", " ", str(unit).strip().lower()).rstrip(".")
    if not text or text in ("none", "null", "n/a"):
        return None
    return _UNIT_ALIASES.get(text, text)


def convert_quantity(qty, from_unit, to_unit):
    """
    Convert qty from from_unit to to_unit.
    Returns (value, ok). ok is False when the units cannot be related
    (for example grams and litres, or boxes and kilograms).
    A missing unit on either side is treated as 'same unit'.
    """
    a, b = normalize_unit(from_unit), normalize_unit(to_unit)
    if a is None or b is None or a == b:
        return float(qty), True
    if a in _UNITS and b in _UNITS and _UNITS[a][0] == _UNITS[b][0]:
        return float(qty) * _UNITS[a][1] / _UNITS[b][1], True
    return float(qty), False


def fmt_qty(value):
    """Format a quantity without pointless trailing zeros (0.250 -> '0.25', 12.0 -> '12')."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


# ── Names ─────────────────────────────────────────────────────────────────────
_LEADING_NOISE = re.compile(r"^(?:some|the|a|an|of|my|our)\s+", re.I)


def _singular(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes", "xes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def name_tokens(name):
    """'Active Dry Yeasts ' -> ['active', 'dry', 'yeast'] (lower-case, singular, no filler words)."""
    text = re.sub(r"[^a-z0-9\s]", " ", str(name or "").lower())
    text = text.strip()
    while True:
        stripped = _LEADING_NOISE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return [_singular(t) for t in text.split() if t]


def name_key(name):
    return " ".join(name_tokens(name))


# ── Similarity & matching ─────────────────────────────────────────────────────
# How the app treats "instant dry yeast", "active dry yeast" and "yeast", or "basmati rice",
# "red rice" and "rice":
#
#   similarity()    scores two names from 0 to 1. The LAST word is the head noun (yeast, rice)
#                   and counts three times as much as a modifier. A name whose words all appear
#                   in a longer name with the same head noun ("rice" in "basmati rice") scores
#                   about 0.9; two names with the same head but different modifiers ("basmati
#                   rice" / "red rice") score about 0.6; a different head noun ("rice flour" /
#                   "rice") is capped at 0.4, so it is treated as a different product.
#   decide_match()  turns the scores into a decision according to the matching mode.

MODE_LEGACY = "legacy"            # only identical names merge; every variation is its own item
MODE_PROMPT_LOW = "prompt_low"    # merge confident matches silently, ask when unsure (default)
MODE_PROMPT_ALL = "prompt_all"    # ask about every similar item
MATCH_MODES = (MODE_LEGACY, MODE_PROMPT_LOW, MODE_PROMPT_ALL)
MATCH_MODE_LABELS = {
    MODE_LEGACY: "Legacy: keep every variation separate",
    MODE_PROMPT_LOW: "Merge confident matches, ask when unsure",
    MODE_PROMPT_ALL: "Ask for every similar item",
}
DEFAULT_AUTO_THRESHOLD = 0.85   # at or above: confident, merge without asking
DEFAULT_ASK_THRESHOLD = 0.55    # at or above: similar enough to mention; below: a new item
_HEAD_WEIGHT = 3
_SAME_HEAD_FLOOR = 0.58


def _fuzzy_equal(a, b):
    """Words are equal, or close enough to be a typo / spelling variant (yest ~ yeast)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.84


def similarity(name_a, name_b, category_a=None, category_b=None):
    """How likely two item names are the same kind of item (0.0 - 1.0)."""
    ta, tb = name_tokens(name_a), name_tokens(name_b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0

    wa = [_HEAD_WEIGHT if i == len(ta) - 1 else 1 for i in range(len(ta))]
    wb = [_HEAD_WEIGHT if i == len(tb) - 1 else 1 for i in range(len(tb))]
    used, pairs = set(), []
    for i, x in enumerate(ta):
        for j, y in enumerate(tb):
            if j not in used and _fuzzy_equal(x, y):
                used.add(j)
                pairs.append((i, j))
                break
    inter = sum(max(wa[i], wb[j]) for i, j in pairs)
    union = sum(wa) + sum(wb) - inter
    score = inter / union if union else 0.0

    if _fuzzy_equal(ta[-1], tb[-1]):                        # same head noun
        score = max(score, _SAME_HEAD_FLOOR)
        small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(pairs) == len(small):                        # shorter name fully contained
            score = max(score, 0.9 - 0.02 * max(len(big) - len(small) - 1, 0))
    else:                                                   # different head noun: other product
        score = min(score, 0.4)

    ca, cb = (category_a or "").strip().lower(), (category_b or "").strip().lower()
    if ca and cb:
        score += 0.04 if ca == cb else -0.12
    return max(0.0, min(1.0, score))


class Candidate:
    """An existing row that a new entry might refer to, with its similarity score."""

    def __init__(self, row, score):
        self.row, self.score = row, score

    def __repr__(self):
        return f"Candidate({self.row.get('item_name')!r}, {self.score:.2f})"


class MatchDecision:
    """
    kind:  'exact'  same name (or a remembered alias): use `row`, no question needed
           'auto'   confident fuzzy match: use `row`, no question needed
           'ask'    the user should choose among `candidates`
           'new'    nothing similar: this is a new item
    """

    def __init__(self, kind, row=None, score=0.0, candidates=None, via=None):
        self.kind, self.row, self.score = kind, row, score
        self.candidates = candidates or []
        self.via = via

    def __repr__(self):
        return f"MatchDecision({self.kind}, row={self.row and self.row.get('item_name')!r}, {self.score:.2f})"


def _rank(row, unit):
    _, ok = convert_quantity(1, unit, row.get("unit"))
    ts = row.get("updated_at")
    return (0 if ok else 1, -(ts.timestamp() if ts else 0), row.get("id", 0))


def decide_match(rows, name, unit=None, category=None, aliases=None, distinct=None,
                 mode=MODE_PROMPT_LOW, auto_threshold=DEFAULT_AUTO_THRESHOLD,
                 ask_threshold=DEFAULT_ASK_THRESHOLD):
    """
    Decide which existing row (if any) an entry for `name` refers to.

    aliases   {name_key: row_id} choices remembered from earlier questions ("merge")
    distinct  {(row_id, name_key)} pairs the user said to keep separate
    Only rows whose unit can be converted to `unit` are considered for fuzzy matches.
    """
    aliases, distinct = aliases or {}, distinct or set()
    key = name_key(name)
    if not key:
        return MatchDecision("new")
    by_id = {r["id"]: r for r in rows}

    if key in aliases and aliases[key] in by_id:
        return MatchDecision("exact", by_id[aliases[key]], 1.0, via="alias")

    exact = [r for r in rows if name_key(r["item_name"]) == key]
    if exact:
        return MatchDecision("exact", sorted(exact, key=lambda r: _rank(r, unit))[0], 1.0)

    if mode == MODE_LEGACY:
        return MatchDecision("new")

    cands = []
    for r in rows:
        if (r["id"], key) in distinct:
            continue
        if not convert_quantity(1, unit, r.get("unit"))[1]:
            continue
        score = similarity(name, r["item_name"], category, r.get("category"))
        if score >= ask_threshold:
            cands.append(Candidate(r, score))
    if not cands:
        return MatchDecision("new")
    cands.sort(key=lambda c: (-c.score, _rank(c.row, unit)))
    top = cands[0]
    second = cands[1].score if len(cands) > 1 else 0.0

    if mode == MODE_PROMPT_ALL:
        return MatchDecision("ask", candidates=cands)
    if top.score >= auto_threshold and (second < auto_threshold or top.score - second >= 0.06):
        return MatchDecision("auto", top.row, top.score, cands, via="similarity")
    return MatchDecision("ask", candidates=cands)


def find_similar_groups(rows, threshold=DEFAULT_ASK_THRESHOLD):
    """
    Group existing rows that look like the same item, for the "Merge similar items" tool.

    The shortest (most generic) name of a group is its suggested canonical row, and every other
    member is compared with it, so unrelated items are not chained together.
    Returns [{"canonical": row, "members": [{"row", "score", "mergeable"}]}], where
    mergeable is False when the member's unit cannot be converted to the canonical unit.
    """
    ordered = sorted(rows, key=lambda r: (len(name_tokens(r["item_name"])), r["id"]))
    groups = []
    for r in ordered:
        best = None
        for g in groups:
            c = g["canonical"]
            score = (1.0 if name_key(c["item_name"]) == name_key(r["item_name"])
                     else similarity(r["item_name"], c["item_name"], r.get("category"), c.get("category")))
            if score >= threshold and (best is None or score > best[0]):
                best = (score, g)
        if best:
            ok = convert_quantity(1, r.get("unit"), best[1]["canonical"].get("unit"))[1]
            best[1]["members"].append({"row": r, "score": best[0], "mergeable": ok})
        else:
            groups.append({"canonical": r, "members": []})
    return [g for g in groups if g["members"]]


# ── Actions ───────────────────────────────────────────────────────────────────
def normalize_action(value):
    """Map whatever the model returned in 'action' to add/remove/set, or None."""
    if value is None:
        return None
    return _ACTION_SYNONYMS.get(str(value).strip().lower())


_SPLIT_RE = re.compile(r"[;,.]|\band\b|\bbut\b|\bthen\b|\balso\b|\bwhile\b|\bplus\b", re.I)


def _classify(text):
    """Action expressed by a piece of text, or None when it has no recognisable verb."""
    has_remove = bool(_REMOVE_RE.search(text))
    has_add = bool(_ADD_RE.search(text))
    if has_remove and not has_add:
        return ACTION_REMOVE
    if has_add and not has_remove:
        return ACTION_ADD
    if has_add and has_remove:
        return None  # contradictory wording; let the caller fall back
    if _SET_RE.search(text):
        return ACTION_SET
    return None


def infer_action(raw_text, item_name):
    """
    Read add/remove/set for one item from the user's sentence.

    Looks at the clause that mentions the item ("bought 3 kg rice" in
    "bought 3 kg rice and used 1 kg salt"); if the item cannot be located it falls back
    to the whole sentence, but only when the sentence uses a single kind of verb.
    Returns 'add', 'remove', 'set' or None.
    """
    if not raw_text:
        return None
    tokens = set(name_tokens(item_name))
    best, best_overlap = None, 0
    for clause in _SPLIT_RE.split(raw_text):
        clause_tokens = set(name_tokens(clause))
        overlap = len(tokens & clause_tokens)
        if overlap > best_overlap:
            best, best_overlap = clause, overlap
    if best is not None:
        action = _classify(best)
        if action:
            return action
        # e.g. "removed 250 grams of yeast" split by "of"? not split; but "yeast, removed 250 g"
        # leaves the verb in a neighbouring clause - try the whole sentence if unambiguous.
    return _classify(raw_text)


def resolve_action(raw_text, item_name, model_action=None, quantity=None):
    """
    Final action for an item: explicit verbs in the text win, then the model's answer,
    then a negative quantity means remove, and finally the safe default is add.
    """
    action = infer_action(raw_text, item_name)
    if action:
        return action
    action = normalize_action(model_action)
    if action:
        return action
    try:
        if quantity is not None and float(quantity) < 0:
            return ACTION_REMOVE
    except (TypeError, ValueError):
        pass
    return ACTION_ADD
