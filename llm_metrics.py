"""
MeteredOllamaParser - the existing OllamaParser plus:

  * metrics   after every parse_input() call, `last_stats` holds the model name,
              prompt tokens, completion tokens and the time Ollama spent
              (Ollama reports these in its final response: prompt_eval_count,
              eval_count, total_duration ...)
  * downloads ensure_model() pulls a missing model from the Ollama library on
              first use, with progress, then everything runs from the local copy

It subclasses ollama_parser.OllamaParser, so prompts, JSON extraction and the
rest of your parser stay exactly as they are.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

import requests

from ollama_parser import OllamaParser


def _ms(ns) -> Optional[int]:
    return None if ns is None else int(round(ns / 1_000_000))


def _same_model(a: str, b: str) -> bool:
    """'tinyllama' and 'tinyllama:latest' are the same model."""
    def norm(name):
        name = name.strip().lower()
        return name if ":" in name else name + ":latest"
    return norm(a) == norm(b)


class MeteredOllamaParser(OllamaParser):
    def __init__(self):
        super().__init__()
        self.last_stats: Optional[dict] = None

    # ── metrics ───────────────────────────────────────────────────────────
    @staticmethod
    def _stats(final: dict) -> dict:
        return {
            "model": final.get("model"),
            "prompt_tokens": final.get("prompt_eval_count"),
            "completion_tokens": final.get("eval_count"),
            "total_ms": _ms(final.get("total_duration")),
            "load_ms": _ms(final.get("load_duration")),
            "eval_ms": _ms(final.get("eval_duration")),
        }

    def parse_input(self, user_input: str, on_chunk=None) -> list:
        self.last_stats = None
        return super().parse_input(user_input, on_chunk=on_chunk)

    def _sync_parse(self, payload: dict) -> list:
        resp = requests.post(f"{self.base_url}/api/generate", json=payload,
                             timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self.last_stats = self._stats(data)
        return self._extract_items(data.get("response", ""))

    def _stream_parse(self, payload: dict, on_chunk) -> list:
        full_response = ""
        with requests.post(f"{self.base_url}/api/generate", json=payload, stream=True,
                           timeout=self.timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                token = chunk.get("response", "")
                full_response += token
                if on_chunk:
                    on_chunk(token)
                if chunk.get("done"):
                    self.last_stats = self._stats(chunk)
                    break
        return self._extract_items(full_response)

    # ── model management ──────────────────────────────────────────────────
    def is_installed(self, name: Optional[str] = None) -> bool:
        name = name or self.model
        return any(_same_model(name, m) for m in self.get_available_models())

    def pull_model(self, name: str, progress: Optional[Callable] = None, cancel=None):
        """Download a model from the Ollama library.

        progress(status, completed_bytes, total_bytes) is called as it downloads.
        Raises ConnectionError (Ollama not running) or RuntimeError (pull failed).
        """
        try:
            with requests.post(f"{self.base_url}/api/pull",
                               json={"model": name, "name": name, "stream": True},
                               stream=True, timeout=(5, 180)) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("download cancelled")
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    if progress:
                        progress(msg.get("status", ""), msg.get("completed"),
                                 msg.get("total"))
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                "Cannot connect to Ollama. Make sure Ollama is running:\n"
                "  ollama serve")
        except requests.exceptions.Timeout:
            raise RuntimeError("the download stalled (no data for 3 minutes)")

    def ensure_model(self, name: Optional[str] = None, progress: Optional[Callable] = None,
                     cancel=None) -> bool:
        """Make sure the model exists locally, downloading it on first use.

        Returns True if a download happened, False if it was already installed."""
        name = name or self.model
        if self.is_installed(name):
            return False
        self.pull_model(name, progress, cancel)
        if not self.is_installed(name):
            raise RuntimeError(f"'{name}' still not installed after the download")
        return True
