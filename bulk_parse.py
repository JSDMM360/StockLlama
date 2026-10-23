"""
Bulk input handling for StockLlama (no tkinter in here).

Why this exists: a small local model cannot reliably turn 100 items into 100
JSON objects in a single prompt - the input overflows its context window and
the model stops after a handful of items.  So large inputs are handled in
two ways:

  1. STRUCTURED input (a JSON list, JSON lines, a name->quantity mapping or a
     CSV/TSV with a header row) is imported directly - no AI, exact, instant.
     JSON may even be pasted inside other text ("add these: [ ... ]").
  2. PLAIN TEXT is split into small batches (a few lines each) that are sent
     to the model one at a time and merged.  A failed batch never loses the
     others, and failed batches can be retried.

Small inputs (one short sentence) still go to the model unchanged, as one batch.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

MAX_UNITS = 8       # lines per AI batch
MAX_CHARS = 900     # characters per AI batch

NAME_KEYS = ("item_name", "name", "item", "product", "ingredient", "title",
             "description")
QTY_KEYS = ("quantity", "qty", "amount", "count", "number")
UNIT_KEYS = ("unit", "uom", "measure")
CATEGORY_KEYS = ("category", "cat", "group")
USED_KEYS = ("date_used", "used", "used_on", "date_consumed", "consumed_on")
BOUGHT_KEYS = ("date_bought", "bought", "bought_on", "purchased", "purchased_on",
               "date_purchased", "purchase_date")
NOTES_KEYS = ("notes", "note", "remarks", "comment", "comments")
ACTION_KEYS = ("action", "type", "movement", "transaction")
USE_WORDS = ("use", "consum", "issue", "sold", "sale", "out", "remove")
CONTAINER_KEYS = ("items", "inventory", "data", "records", "entries", "products",
                  "ingredients", "stock", "rows")

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
                "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")  # day-first (India)


# ── value helpers ─────────────────────────────────────────────────────────────
def _norm_key(key) -> str:
    return re.sub(r"[\s\-]+", "_", str(key).strip().lower())


def _first(d: dict, names):
    for n in names:
        v = d.get(n)
        if v is not None and str(v).strip() != "":
            return v
    return None


def parse_date(value, today: Optional[date] = None) -> Optional[str]:
    """Return an ISO date string, None for empty values; ValueError if unreadable."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "n/a", "na", "-"):
        return None
    today = today or date.today()
    if s.lower() == "today":
        return today.isoformat()
    if s.lower() == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    for fmt in DATE_FORMATS:
        for candidate in (s, s[:10]):
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    raise ValueError(s)


def split_quantity(value):
    """'3 kg' -> (3.0, 'kg');  2.5 -> (2.5, '');  'abc' -> (None, '')."""
    if isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, float)):
        return float(value), ""
    m = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?|-?\.\d+)\s*(.*)$", str(value).strip())
    if not m:
        return None, ""
    num = m.group(1)
    if "," in num:
        num = num.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", num) \
            else num.replace(",", ".")
    try:
        return float(num), m.group(2).strip()
    except ValueError:
        return None, ""


def normalize_item(raw: dict, idx: int, warnings: list, today: Optional[date] = None,
                   name_override: Optional[str] = None) -> Optional[dict]:
    """Turn one raw record into the item dict the database layer expects."""
    d = {_norm_key(k): v for k, v in raw.items()}
    name = name_override or _first(d, NAME_KEYS)
    if name is None or not str(name).strip():
        warnings.append(f"Row {idx}: no item name - skipped")
        return None
    name = str(name).strip()

    qty, inline_unit = None, ""
    qty_raw = _first(d, QTY_KEYS)
    if qty_raw is not None:
        qty, inline_unit = split_quantity(qty_raw)
    if qty is None:
        warnings.append(f"Row {idx} ({name}): missing or invalid quantity - set to 1")
        qty = 1.0
    if qty < 0:
        warnings.append(f"Row {idx} ({name}): negative quantity - used {abs(qty):g}")
        qty = abs(qty)

    unit = _first(d, UNIT_KEYS) or inline_unit or None
    unit = str(unit).strip() if unit else None
    category = _first(d, CATEGORY_KEYS)
    category = str(category).strip() if category else "General"
    notes = _first(d, NOTES_KEYS)

    def read_date(keys, label):
        v = _first(d, keys)
        if v is None:
            return None
        try:
            return parse_date(v, today)
        except ValueError:
            warnings.append(f"Row {idx} ({name}): could not read {label} '{v}' - left empty")
            return None

    used = read_date(USED_KEYS, "date_used")
    bought = read_date(BOUGHT_KEYS, "date_bought")
    if used is None and bought is None:
        generic = read_date(("date",), "date")
        if generic:
            action = str(_first(d, ACTION_KEYS) or "").lower()
            if any(w in action for w in USE_WORDS):
                used = generic
            else:
                bought = generic

    return {"item_name": name, "quantity": qty, "unit": unit, "category": category,
            "date_used": used, "date_bought": bought,
            "notes": str(notes).strip() if notes else None,
            "raw_input": json.dumps(raw, ensure_ascii=False, default=str)[:500]}


# ── detection ─────────────────────────────────────────────────────────────────
@dataclass
class Detection:
    kind: str                                   # json | csv | text | bad_json | empty
    items: list = field(default_factory=list)   # directly imported items
    lines: list = field(default_factory=list)   # text lines for the AI
    warnings: list = field(default_factory=list)
    records: int = 0


def _collect(data, depth=0):
    """Pull (name_override, record) pairs and plain strings out of parsed JSON."""
    recs, strs = [], []
    if isinstance(data, list):
        for el in data:
            if isinstance(el, dict):
                recs.append((None, el))
            elif isinstance(el, str) and el.strip():
                strs.append(el.strip())
            elif isinstance(el, list) and depth < 2:
                r, s = _collect(el, depth + 1)
                recs += r
                strs += s
    elif isinstance(data, dict):
        nd = {_norm_key(k): v for k, v in data.items()}
        for key in CONTAINER_KEYS:
            if isinstance(nd.get(key), (list, dict)) and depth < 3:
                return _collect(nd[key], depth + 1)
        if any(k in nd for k in NAME_KEYS):
            recs.append((None, data))
        else:                                     # {"Rice": 3, "Oil": {"quantity": 2}}
            for k, v in data.items():
                if isinstance(v, dict):
                    recs.append((str(k), v))
                elif isinstance(v, (int, float, str)) and not isinstance(v, bool):
                    recs.append((str(k), {"quantity": v}))
    return recs, strs


def _load_json(s: str):
    """Return (data, error_message). data is None when s is not JSON input."""
    if not s:
        return None, None
    first_error = None
    if s[0] in "[{":
        try:
            return json.loads(s), None
        except json.JSONDecodeError as exc:
            first_error = f"{exc.msg} (line {exc.lineno}, column {exc.colno})"
        lines = [ln for ln in s.splitlines() if ln.strip()]
        if len(lines) > 1:                        # JSON lines
            try:
                return [json.loads(ln) for ln in lines], None
            except json.JSONDecodeError:
                pass
    for opener, closer in (("[", "]"), ("{", "}")):   # JSON inside other text
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            try:
                data = json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                continue
            if _collect(data)[0]:
                return data, None
    if first_error and len(s) >= 20 and ('"' in s or ":" in s):
        return None, first_error
    return None, None


def _try_csv(s: str, warnings: list, today):
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    for delim in (",", "\t", ";", "|"):
        header = next(csv.reader([lines[0]], delimiter=delim))
        if len(header) >= 2 and any(_norm_key(h) in NAME_KEYS for h in header):
            reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
            items = []
            for i, row in enumerate(reader, 1):
                row = {k: v for k, v in row.items() if k is not None}
                item = normalize_item(row, i, warnings, today)
                if item:
                    items.append(item)
            return items
    return None


_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+")


def text_lines(text: str) -> list:
    return [_BULLET.sub("", ln).strip() for ln in text.splitlines() if ln.strip()]


def analyse(text: str, today: Optional[date] = None) -> Detection:
    s = (text or "").strip()
    if not s:
        return Detection("empty")
    warnings: list = []
    data, json_error = _load_json(s)
    if json_error:
        return Detection("bad_json", warnings=[json_error])
    if data is not None:
        recs, strs = _collect(data)
        if recs or strs:
            items = []
            for i, (name, raw) in enumerate(recs, 1):
                item = normalize_item(raw, i, warnings, today, name)
                if item:
                    items.append(item)
            return Detection("json", items, [_BULLET.sub("", x) for x in strs],
                             warnings, len(recs))
    csv_items = _try_csv(s, warnings, today)
    if csv_items is not None:
        return Detection("csv", csv_items, [], warnings, len(csv_items))
    return Detection("text", [], text_lines(s))


# ── batching ──────────────────────────────────────────────────────────────────
def _split_long(line: str, max_chars: int) -> list:
    if len(line) <= max_chars:
        return [line]
    parts = [p for p in re.split(r"(?<=[;.])\s+|,\s+", line) if p.strip()]
    return parts or [line]


def chunk_lines(lines: list, max_units: int = MAX_UNITS,
                max_chars: int = MAX_CHARS) -> list:
    """Group text lines into batches small enough for a small model."""
    units = []
    for ln in lines:
        units.extend(_split_long(ln, max_chars))
    chunks, cur, size = [], [], 0
    for u in units:
        if cur and (len(cur) >= max_units or size + len(u) + 1 > max_chars):
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(u)
        size += len(u) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


@dataclass
class BatchResult:
    items: list = field(default_factory=list)
    failures: list = field(default_factory=list)   # [(batch number, text, error)]
    skipped: list = field(default_factory=list)    # batches never attempted
    cancelled: bool = False

    @property
    def retry_chunks(self) -> list:
        return [c for _, c, _ in self.failures] + list(self.skipped)


def parse_chunks(parser, chunks: list, progress: Optional[Callable] = None,
                 cancel=None, stream: Optional[Callable] = None,
                 on_batch: Optional[Callable] = None) -> BatchResult:
    """Run every batch through parser.parse_input and merge the results.

    progress(i, n)                              before each batch
    stream(i, n, text_so_far)                   while a batch streams
    on_batch(i, n, chunk, raw, items, secs, exc) after each batch
    A ConnectionError on the very first batch is raised; later it stops the run
    and the unprocessed batches are returned in `skipped` so they can be retried.
    """
    import time
    result = BatchResult()
    n = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            result.skipped = chunks[i - 1:]
            break
        if progress:
            progress(i, n)
        acc: list = []

        def on_chunk(token, acc=acc, i=i):
            acc.append(token)
            if stream:
                stream(i, n, "".join(acc))

        started = time.time()
        try:
            got = parser.parse_input(chunk, on_chunk=on_chunk)
        except ConnectionError as exc:
            if i == 1 and not result.items and not result.failures:
                raise
            if on_batch:
                on_batch(i, n, chunk, "".join(acc), None, time.time() - started, exc)
            result.skipped = chunks[i - 1:]
            break
        except Exception as exc:                  # bad JSON from the model, timeout ...
            result.failures.append((i, chunk, str(exc)))
            if on_batch:
                on_batch(i, n, chunk, "".join(acc), None, time.time() - started, exc)
            continue
        elapsed = time.time() - started
        if not got and n > 1:
            err = ValueError("the model returned no items")
            result.failures.append((i, chunk, str(err)))
            if on_batch:
                on_batch(i, n, chunk, "".join(acc), got, elapsed, err)
            continue
        for item in got:
            item["raw_input"] = chunk[:500]
        result.items.extend(got)
        if on_batch:
            on_batch(i, n, chunk, "".join(acc), got, elapsed, None)
    return result


def describe(det: Detection) -> str:
    """One-line summary for the Large Input window."""
    if det.kind == "empty":
        return "Paste text, JSON or CSV - or open a file."
    if det.kind == "bad_json":
        return f"⚠ Looks like JSON but cannot be read: {det.warnings[0]}"
    parts = []
    if det.items:
        parts.append(f"✓ {det.kind.upper()} detected: {len(det.items)} item(s) will be "
                     f"imported directly - no AI needed")
    if det.lines:
        batches = len(chunk_lines(det.lines))
        parts.append(f"{len(det.lines)} text line(s) -> {batches} AI batch(es)")
    if det.warnings:
        parts.append(f"{len(det.warnings)} row(s) with problems (see the Logs tab after parsing)")
    return "   |   ".join(parts) if parts else "Nothing recognised."
