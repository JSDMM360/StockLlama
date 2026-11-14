"""
Event logging for StockLlama.

Every user input (typed, spoken, imported or manual) becomes one entry with:
  * an event id and the date/time,
  * the input text,
  * the model output, model name, inference time and prompt/completion tokens,
  * a timeline of the events that followed (parsed, saved, discarded, errors ...).

Storage: the PostgreSQL table  event_logs  (see event_store.py).

Robustness
  * Writes happen on a background thread, so a slow database never freezes the UI.
  * If PostgreSQL is unreachable, entries are appended to  logs/activity.jsonl
    (the "spool") and replayed into the database - in one transaction - as soon as
    it is reachable again.  Nothing is lost and the app keeps working.
  * The same spool file also migrates logs written by earlier versions.
  * A human readable rotating text log  logs/activity.log  is always written.
  * read_entries() returns an in-memory snapshot, so the Event Logs tab never
    waits for the database.

Without a store (ActivityLogger(store=None)) everything stays in the spool file.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

LOG_DIR = Path(os.environ.get("STOCKLLAMA_LOG_DIR",
                              Path(__file__).resolve().parent / "logs"))

MAX_JSON_MESSAGE = 20_000   # characters kept per event
MAX_TEXT_MESSAGE = 600      # characters per event in the readable log
CACHE_LIMIT = 500           # entries kept in the in-memory snapshot


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ActivityLogger:
    def __init__(self, store=None, log_dir: Path | str = LOG_DIR,
                 retry_seconds: float = 15.0):
        self.store = store
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.spool_path = self.dir / "activity.jsonl"
        self.text_path = self.dir / "activity.log"
        self.retry_seconds = retry_seconds

        self._q: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._spool_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: list = []
        self._listeners: list[Callable[[str], None]] = []
        self._db_ok = False
        self._db_error = "not connected yet" if store else "no database configured"
        self._next_retry = 0.0
        self._dirty = True
        self._last_refresh = 0.0
        self._stopping = False

        self._text = logging.getLogger(f"stocklllama.activity.{self.text_path}")
        self._text.setLevel(logging.INFO)
        self._text.propagate = False
        if not self._text.handlers:
            handler = RotatingFileHandler(self.text_path, maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            self._text.addHandler(handler)

        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="activity-log-writer")
        self._worker.start()

    # ── public API: writing ───────────────────────────────────────────────
    def begin(self, prompt: str, source: str = "typed", **meta) -> str:
        """Start a log entry for an input. Returns the entry id."""
        uid = uuid.uuid4().hex[:12]
        self._enqueue({"op": "begin", "uid": uid, "ts": _now(), "source": source,
                       "prompt": prompt, "meta": meta})
        short = prompt.replace("\n", " / ")
        if len(short) > MAX_TEXT_MESSAGE:
            short = f"{short[:MAX_TEXT_MESSAGE]} ... [{len(prompt)} characters]"
        self._text.info("[%s] INPUT (%s): %s", uid, source, short)
        return uid

    def event(self, uid: Optional[str], kind: str, message: str, **data):
        """Attach an event: model, speech, llm_response, parsed, saved, discarded,
        empty, updated, deleted, info, warning, error ..."""
        if not uid:
            return
        self._enqueue({"op": "event", "uid": uid, "ts": _now(), "kind": kind,
                       "message": str(message)[:MAX_JSON_MESSAGE], "data": data})
        short = str(message).replace("\n", " / ")
        if len(short) > MAX_TEXT_MESSAGE:
            short = short[:MAX_TEXT_MESSAGE] + " ..."
        level = logging.ERROR if kind == "error" else (
            logging.WARNING if kind == "warning" else logging.INFO)
        self._text.log(level, "[%s]   %s: %s", uid, kind, short)

    def metrics(self, uid: Optional[str], model: Optional[str] = None,
                output: Optional[str] = None, inference_ms: Optional[int] = None,
                prompt_tokens: Optional[int] = None,
                completion_tokens: Optional[int] = None):
        """Record one model call. Calls add up (output is appended, times and
        token counts are summed), so a multi-batch input gets its totals."""
        if not uid:
            return
        self._enqueue({"op": "metrics", "uid": uid, "ts": _now(), "model": model,
                       "output": output, "ms": inference_ms, "pt": prompt_tokens,
                       "ct": completion_tokens})
        self._text.info("[%s]   model call: %s, %s ms, %s prompt + %s completion tokens",
                        uid, model, inference_ms, prompt_tokens, completion_tokens)

    # ── public API: reading ───────────────────────────────────────────────
    def read_entries(self, limit: Optional[int] = None) -> list:
        """Newest-first snapshot (never blocks on the database)."""
        with self._cache_lock:
            entries = list(self._cache)
        return entries[:limit] if limit else entries

    def add_listener(self, callback: Callable[[str], None]):
        """callback() is invoked (on the writer thread) whenever the snapshot changes."""
        self._listeners.append(callback)

    def status(self) -> dict:
        """{'state': 'connected'|'offline'|'file-only', 'pending': int, 'error': str}"""
        if not self.store:
            return {"state": "file-only", "pending": self._spool_count(), "error": ""}
        return {"state": "connected" if self._db_ok else "offline",
                "pending": self._spool_count(), "error": self._db_error}

    def refresh(self):
        """Ask the writer to reload the snapshot soon (e.g. when a tab opens)."""
        self._dirty = True
        self._last_refresh = 0.0
        self._q.put({"op": "noop"})

    def close(self, timeout: float = 3.0):
        """Flush queued writes (best effort) and stop the writer."""
        self._stopping = True
        self._q.put({"op": "stop"})
        self._worker.join(timeout)
        if self.store:
            self.store.close()

    # ── export ────────────────────────────────────────────────────────────
    def export_csv(self, path, entries: Optional[list] = None):
        entries = entries if entries is not None else self.read_entries()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["event_id", "date", "source", "input", "model_output", "model",
                        "inference_ms", "prompt_tokens", "completion_tokens",
                        "total_tokens", "status", "events"])
            for e in reversed(entries):
                events = " | ".join(f"{ev['kind']}: {ev['message']}".replace("\n", " / ")
                                    for ev in e["events"])
                w.writerow([e.get("event_id") or "", e["timestamp"].replace("T", " "),
                            e["source"], e["prompt"], e.get("output") or "",
                            e.get("model") or "", _n(e.get("inference_ms")),
                            _n(e.get("prompt_tokens")), _n(e.get("completion_tokens")),
                            _n(e.get("total_tokens")), e["status"], events])

    # ── writer thread ─────────────────────────────────────────────────────
    def _enqueue(self, op: dict):
        self._dirty = True
        self._q.put(op)

    def _run(self):
        self._try_connect()                       # initial connect + replay + snapshot
        self._refresh_cache(force=True)
        while True:
            try:
                op = self._q.get(timeout=1.0)
            except queue.Empty:
                op = {"op": "idle"}
            kind = op.get("op")
            if kind == "stop":
                self._drain()
                break
            if kind not in ("noop", "idle"):
                self._write(op)
            elif kind == "idle" and not self._db_ok and self.store \
                    and time.time() >= self._next_retry:
                self._try_connect()
            self._refresh_cache()

    def _drain(self):
        """Write whatever is still queued when shutting down."""
        while True:
            try:
                op = self._q.get_nowait()
            except queue.Empty:
                break
            if op.get("op") not in ("noop", "idle", "stop"):
                self._write(op)

    def _write(self, op: dict):
        if self.store and not self._db_ok and time.time() >= self._next_retry:
            self._try_connect()
        if self.store and self._db_ok:
            try:
                self.store.apply(op)
                return
            except Exception as exc:
                self._mark_down(exc)
        self._spool(op)

    def _mark_down(self, exc: Exception):
        self._db_ok = False
        self._db_error = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
        self._next_retry = time.time() + self.retry_seconds

    def _try_connect(self):
        """Connect to PostgreSQL and replay anything waiting in the spool."""
        if not self.store:
            return
        try:
            self.store.ensure()
            ops = self._read_spool_ops()
            if ops:
                self.store.apply_many(ops)
                self._clear_spool()
            self._db_ok = True
            self._db_error = ""
            self._dirty = True
        except Exception as exc:
            self._mark_down(exc)

    # ── spool file ────────────────────────────────────────────────────────
    def _spool(self, op: dict):
        line = json.dumps(op, ensure_ascii=False, default=str)
        with self._spool_lock:
            with open(self.spool_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _read_spool_ops(self) -> list:
        ops = []
        if not self.spool_path.exists():
            return ops
        with self._spool_lock:
            with open(self.spool_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                op = _to_op(json.loads(line))
            except (json.JSONDecodeError, KeyError):
                continue                           # skip a torn / corrupt line
            if op:
                ops.append(op)
        return ops

    def _clear_spool(self):
        with self._spool_lock:
            try:
                self.spool_path.unlink()
            except FileNotFoundError:
                pass

    def _spool_count(self) -> int:
        try:
            return sum(1 for op in self._read_spool_ops() if op["op"] == "begin")
        except Exception:
            return 0

    # ── snapshot ──────────────────────────────────────────────────────────
    def _refresh_cache(self, force: bool = False):
        now = time.time()
        if not (force or (self._dirty and now - self._last_refresh >= 0.8)):
            return
        self._dirty = False
        self._last_refresh = now
        entries: dict = {}
        if self.store and self._db_ok:
            try:
                for e in self.store.read(CACHE_LIMIT):
                    entries[e["id"]] = e
            except Exception as exc:
                self._mark_down(exc)
        for uid, e in _fold(self._read_spool_ops()).items():
            entries.setdefault(uid, e)             # entries not (yet) in the database
        result = sorted(entries.values(), key=lambda e: e["timestamp"], reverse=True)
        for e in result:
            e["status"] = entry_status(e)
        with self._cache_lock:
            self._cache = result[:CACHE_LIMIT]
        for cb in list(self._listeners):
            try:
                cb("")
            except Exception:
                pass


def _n(value):
    return "" if value is None else value


def _to_op(rec: dict) -> Optional[dict]:
    """Normalise a spool record; also understands the format of earlier versions."""
    if "op" in rec:
        return rec
    if rec.get("type") == "input":
        return {"op": "begin", "uid": rec["id"], "ts": rec.get("ts", _now()),
                "source": rec.get("source", "typed"), "prompt": rec.get("prompt", ""),
                "meta": rec.get("meta") or {}}
    if rec.get("type") == "event":
        return {"op": "event", "uid": rec["id"], "ts": rec.get("ts", _now()),
                "kind": rec.get("kind", ""), "message": rec.get("message", ""),
                "data": rec.get("data") or {}}
    return None


def _fold(ops: list) -> dict:
    """Build entry dicts from spooled operations."""
    entries: dict = {}
    for op in ops:
        uid = op.get("uid")
        if op["op"] == "begin":
            entries[uid] = {
                "id": uid, "event_id": None, "timestamp": str(op["ts"]),
                "source": op.get("source", "typed"), "prompt": op.get("prompt", ""),
                "output": None, "model": None, "inference_ms": None,
                "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
                "meta": op.get("meta") or {}, "events": []}
        elif uid in entries:
            e = entries[uid]
            if op["op"] == "event":
                e["events"].append({"ts": op["ts"], "kind": op["kind"],
                                    "message": op["message"], "data": op.get("data") or {}})
            elif op["op"] == "metrics":
                e["model"] = op.get("model") or e["model"]
                if op.get("output") is not None:
                    e["output"] = (e["output"] + "\n" if e["output"] else "") + str(op["output"])
                for key, field in (("ms", "inference_ms"), ("pt", "prompt_tokens"),
                                   ("ct", "completion_tokens")):
                    if op.get(key) is not None:
                        e[field] = (e[field] or 0) + op[key]
                if e["prompt_tokens"] is not None or e["completion_tokens"] is not None:
                    e["total_tokens"] = (e["prompt_tokens"] or 0) + (e["completion_tokens"] or 0)
    return entries


def entry_status(entry: dict) -> str:
    kinds = [ev["kind"] for ev in entry["events"]]
    if "error" in kinds:
        return "error"
    for k in ("saved", "updated", "deleted"):
        if k in kinds:
            return k
    if "discarded" in kinds:
        return "discarded"
    if "empty" in kinds:
        return "no items"
    if "parsed" in kinds:
        return "awaiting save"
    return "started"


def format_ms(ms) -> str:
    if ms is None:
        return "-"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.2f} s"


def format_entry(entry: dict) -> str:
    """Plain-text rendering of an entry (used in tests and exports)."""
    lines = [f"Event   : {entry.get('event_id') or 'pending'}",
             f"Date    : {entry['timestamp'].replace('T', ' ')}",
             f"Source  : {entry['source']}",
             f"Status  : {entry['status']}",
             f"Model   : {entry.get('model') or '-'}",
             f"Time    : {format_ms(entry.get('inference_ms'))}",
             f"Tokens  : {_n(entry.get('prompt_tokens'))} prompt + "
             f"{_n(entry.get('completion_tokens'))} completion",
             "", "Input:", f"  {entry['prompt']}", "", "Model output:",
             f"  {entry.get('output') or '-'}", "", "Events:"]
    for ev in entry["events"] or [{"ts": "", "kind": "(none)", "message": ""}]:
        stamp = ev["ts"][11:] if len(ev["ts"]) >= 19 else ev["ts"]
        lines.append(f"  {stamp}  {ev['kind']:<12} {ev['message']}")
    return "\n".join(lines)
