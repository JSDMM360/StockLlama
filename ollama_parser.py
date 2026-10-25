"""
Ollama integration for parsing natural language inventory input
"""

import json
import requests
from datetime import datetime
from config import OLLAMA_CONFIG
from inventory_logic import resolve_action, normalize_unit


SYSTEM_PROMPT = """You are an inventory tracking assistant. Extract structured data from natural language input.

Given user input about items, quantities, and dates, extract:
- item_name: the product/item name only, without quantity, unit or verb (string)
- action: what happened to the stock - exactly one of "add", "remove" or "set" (string)
- quantity: the amount mentioned, always a POSITIVE number (number)
- unit: measurement unit exactly as the user said it, e.g. g, grams, kg, liters, pieces, boxes (string or null)
- category: category like Food, Electronics, Stationery, Cleaning, Medicine, etc (string)
- date_used: date the item was used (ISO format YYYY-MM-DD or null)
- date_bought: date the item was bought/purchased (ISO format YYYY-MM-DD or null)
- notes: any additional context (string or null)

Rules for "action":
- "add": bought, purchased, received, added, restocked, got, delivered
- "remove": removed, used, consumed, took, sold, finished, ate, threw away, spent, deducted
- "set": the user states the total now on hand ("I now have 3 kg of rice", "2 kg left")
- Never return a negative quantity; use action "remove" instead

Other rules:
- If no unit mentioned, infer from context or use "pieces"
- If "today" mentioned, use today's date: {today}
- If "yesterday" mentioned, use yesterday's date
- Infer category from item type
- Return ONLY valid JSON array (even for single items)
- If multiple items mentioned, return multiple objects in the array, each with its own action

Example outputs:
[{{\"item_name\": \"Rice\", \"action\": \"add\", \"quantity\": 5, \"unit\": \"kg\", \"category\": \"Food\", \"date_bought\": \"2024-01-15\", \"date_used\": null, \"notes\": null}}]
[{{\"item_name\": \"Active Dry Yeast\", \"action\": \"remove\", \"quantity\": 250, \"unit\": \"grams\", \"category\": \"Food\", \"date_bought\": null, \"date_used\": null, \"notes\": null}}]
[{{\"item_name\": \"Printer Paper\", \"action\": \"remove\", \"quantity\": 2, \"unit\": \"boxes\", \"category\": \"Stationery\", \"date_bought\": null, \"date_used\": \"2024-01-16\", \"notes\": \"A4 size\"}}]
"""


class OllamaParser:
    def __init__(self):
        self.base_url = OLLAMA_CONFIG["base_url"]
        self.model = OLLAMA_CONFIG["model"]
        self.timeout = OLLAMA_CONFIG["timeout"]

    def is_available(self) -> bool:
        """Check if Ollama is running."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def get_available_models(self) -> list:
        """Get list of available Ollama models."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []

    def set_model(self, model: str):
        self.model = model

    def parse_input(self, user_input: str, on_chunk=None) -> list[dict]:
        """
        Parse natural language input into structured inventory items.
        Supports streaming with optional callback.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        system = SYSTEM_PROMPT.format(today=today)

        payload = {
            "model": self.model,
            "system": system,
            "prompt": f"Extract inventory data from: {user_input}",
            "stream": on_chunk is not None,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
            }
        }

        try:
            if on_chunk:
                items = self._stream_parse(payload, on_chunk)
            else:
                items = self._sync_parse(payload)
            return self._resolve_actions(items, user_input)
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                "Cannot connect to Ollama. Make sure Ollama is running:\n"
                "  ollama serve\n"
                f"Then pull the model:\n  ollama pull {self.model}"
            )

    def _sync_parse(self, payload: dict) -> list[dict]:
        """Synchronous parse."""
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return self._extract_items(data.get("response", ""))

    def _stream_parse(self, payload: dict, on_chunk) -> list[dict]:
        """Streaming parse with chunk callback."""
        full_response = ""
        with requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            stream=True,
            timeout=self.timeout
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    full_response += token
                    if on_chunk:
                        on_chunk(token)
                    if chunk.get("done"):
                        break

        return self._extract_items(full_response)

    def _extract_items(self, response_text: str) -> list[dict]:
        """Extract and validate items from model response."""
        text = response_text.strip()

        # Find JSON array in response
        start = text.find("[")
        end = text.rfind("]") + 1
        if start != -1 and end > start:
            text = text[start:end]

        items = json.loads(text)
        if not isinstance(items, list):
            items = [items]

        # Validate and clean each item
        validated = []
        for item in items:
            if not item.get("item_name"):
                continue
            try:
                quantity = float(item.get("quantity", 1))
            except (TypeError, ValueError):
                quantity = 1.0
            validated.append({
                "item_name": str(item.get("item_name", "Unknown")).strip(),
                "action": item.get("action"),       # resolved against the user's text by the caller
                "quantity": quantity,
                "unit": normalize_unit(item.get("unit")),
                "category": item.get("category", "General"),
                "date_used": item.get("date_used"),
                "date_bought": item.get("date_bought"),
                "notes": item.get("notes"),
                "raw_input": None  # Set by caller
            })

        return validated

    @staticmethod
    def _resolve_actions(items: list[dict], user_input: str) -> list[dict]:
        """
        Decide add / remove / set for every item. Explicit verbs in the user's own sentence
        win over the model's answer (small models often get this wrong or omit it), and the
        quantity is always stored as a positive amount.
        """
        for item in items:
            item["action"] = resolve_action(
                user_input, item["item_name"],
                model_action=item.get("action"), quantity=item.get("quantity"))
            item["quantity"] = abs(item["quantity"])
        return [i for i in items if i["quantity"] > 0 or i["action"] == "set"]

    def reload_config(self):
        """Re-read OLLAMA_CONFIG (used after the settings dialog changes it)."""
        self.base_url = OLLAMA_CONFIG["base_url"]
        self.timeout = OLLAMA_CONFIG["timeout"]
