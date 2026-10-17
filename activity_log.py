"""
Activity logging for StockLlama.

Every user input (typed, spoken or manual) is logged with:
  * the date/time,
  * the input prompt,
  * every event that happened as a result (model used, LLM response,
    parsed items, rows saved/discarded, errors ...).

Two files are written into ./logs (override with STOCKLLAMA_LOG_DIR):
  activity.jsonl  - machine readable, append-only, read back by the Logs tab
  activity.log    - human readable, rotating (1 MB x 3 backups)

The JSONL file is event-sourced: one "input" record starts an entry and any
number of "event" records are attached to it later (e.g. the user presses
SAVE ALL a minute after the LLM replied).  Nothing is ever rewritten, so a
crash cannot corrupt earlier entries.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

LOG_DIR = Path(os.environ.get("STOCKLLAMA_LOG_DIR",
                              Path(__file__).resolve().parent / "logs"))

MAX_JSON_MESSAGE = 20_000   # characters kept per event in the JSONL file
MAX_TEXT_MESSAGE = 600      # characters kept per event in the readable log


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ActivityLogger:
    def __init__(self, log_dir: Path | str = LOG_DIR):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.dir / "activity.jsonl"
        self.text_path = self.dir / "activity.log"
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str], None]] = []

        # Human readable log via the standard logging module.
        self._text = logging.getLogger(f"stocklllama.activity.{self.text_path}")
        self._text.setLevel(logging.INFO)
        self._text.propagate = False
        if not self._text.handlers:
            handler = RotatingFileHandler(
                self.text_path, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            self._text.addHandler(handler)

    # ── writing ────────────────────────────────────────────────────────────
    def begin(self, prompt: str, source: str = "typed", **meta) -> str:
        """Start a new log entry for an input prompt. Returns the entry id."""
        entry_id = uuid.uuid4().hex[:8]
        self._write({"type": "input", "id": entry_id, "ts": _now(),
                     "source": source, "prompt": prompt, "meta": meta})
        self._text.info("[%s] INPUT (%s): %s", entry_id, source,
                        prompt.replace("\n", " / "))
        self._notify(entry_id)
        return entry_id

    def event(self, entry_id: Optional[str], kind: str, message: str, **data):
        """Attach an event to an entry.

        Typical kinds: model, speech, llm_response, parsed, saved,
        discarded, empty, updated, deleted, info, warning, error.
        """
        if not entry_id:
            return
        self._write({"type": "event", "id": entry_id, "ts": _now(),
                     "kind": kind,
                     "message": str(message)[:MAX_JSON_MESSAGE],
                     "data": data})
        short = str(message).replace("\n", " / ")
        if len(short) > MAX_TEXT_MESSAGE:
            short = short[:MAX_TEXT_MESSAGE] + " ..."
        level = logging.ERROR if kind == "error" else (
            logging.WARNING if kind == "warning" else logging.INFO)
        self._text.log(level, "[%s]   %s: %s", entry_id, kind, short)
        self._notify(entry_id)

    def _write(self, record: dict):
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ── listeners (the Logs tab subscribes to live updates) ────────────────
    def add_listener(self, callback: Callable[[str], None]):
        self._listeners.append(callback)

    def _notify(self, entry_id: str):
        for cb in list(self._listeners):
            try:
                cb(entry_id)
            except Exception:
                pass  # a broken listener must never break logging

    # ── reading ────────────────────────────────────────────────────────────
    def read_entries(self, limit: Optional[int] = None) -> list[dict]:
        """Return entries newest-first, each with its events folded in."""
        entries: dict[str, dict] = {}
        order: list[str] = []
        if not self.jsonl_path.exists():
            return []
        with self._lock:
            with open(self.jsonl_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip a torn / corrupt line
            eid = rec.get("id")
            if rec.get("type") == "input":
                entries[eid] = {
                    "id": eid, "timestamp": rec.get("ts", ""),
                    "source": rec.get("source", ""),
                    "prompt": rec.get("prompt", ""),
                    "meta": rec.get("meta", {}), "events": [],
                }
                order.append(eid)
            elif rec.get("type") == "event" and eid in entries:
                entries[eid]["events"].append({
                    "ts": rec.get("ts", ""), "kind": rec.get("kind", ""),
                    "message": rec.get("message", ""),
                    "data": rec.get("data", {}),
                })
        result = [entries[i] for i in reversed(order)]
        for e in result:
            e["status"] = entry_status(e)
        return result[:limit] if limit else result

    # ── export ─────────────────────────────────────────────────────────────
    def export_csv(self, path: str | Path, entries: Optional[list[dict]] = None):
        entries = entries if entries is not None else self.read_entries()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "source", "prompt", "status", "events"])
            for e in reversed(entries):  # chronological in the file
                events = " | ".join(
                    f"{ev['kind']}: {ev['message']}".replace("\n", " / ")
                    for ev in e["events"])
                w.writerow([e["timestamp"].replace("T", " "), e["source"],
                            e["prompt"], e["status"], events])


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


def format_entry(entry: dict) -> str:
    """Plain-text rendering used by the Logs tab detail pane."""
    lines = [f"Date    : {entry['timestamp'].replace('T', ' ')}",
             f"Source  : {entry['source']}",
             f"Status  : {entry['status']}",
             "", "Prompt:", f"  {entry['prompt']}", "", "Events:"]
    if not entry["events"]:
        lines.append("  (none)")
    for ev in entry["events"]:
        stamp = ev["ts"][11:] if len(ev["ts"]) >= 19 else ev["ts"]
        body = ev["message"].replace("\n", "\n" + " " * 17)
        lines.append(f"  {stamp}  {ev['kind']:<12} {body}")
    return "\n".join(lines)
