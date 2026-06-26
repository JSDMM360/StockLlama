"""
Ollama integration for parsing natural language inventory input
"""

import json
import requests
from datetime import datetime
from config import OLLAMA_CONFIG


SYSTEM_PROMPT = """You are an inventory tracking assistant. Extract structured data from natural language input.

Given user input about items, quantities, and dates, extract:
- item_name: the product/item name (string)
- quantity: numeric amount (number)
- unit: measurement unit like kg, liters, pieces, boxes, etc (string or null)
- category: category like Food, Electronics, Stationery, Cleaning, Medicine, etc (string)
- date_used: date the item was used (ISO format YYYY-MM-DD or null)
- date_bought: date the item was bought/purchased (ISO format YYYY-MM-DD or null)
- notes: any additional context (string or null)

Rules:
- If no unit mentioned, infer from context or use "pieces"
- If "today" mentioned, use today's date: {today}
- If "yesterday" mentioned, use yesterday's date
- Infer category from item type
- Return ONLY valid JSON array (even for single items)
- If multiple items mentioned, return multiple objects in the array

Example outputs:
[{{"item_name": "Rice", "quantity": 5, "unit": "kg", "category": "Food", "date_bought": "2024-01-15", "date_used": null, "notes": null}}]
[{{"item_name": "Printer Paper", "quantity": 2, "unit": "boxes", "category": "Stationery", "date_bought": null, "date_used": "2024-01-16", "notes": "A4 size"}}]
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
                return self._stream_parse(payload, on_chunk)
            else:
                return self._sync_parse(payload)
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
            validated.append({
                "item_name": str(item.get("item_name", "Unknown")).strip(),
                "quantity": float(item.get("quantity", 1)),
                "unit": item.get("unit"),
                "category": item.get("category", "General"),
                "date_used": item.get("date_used"),
                "date_bought": item.get("date_bought"),
                "notes": item.get("notes"),
                "raw_input": None  # Set by caller
            })

        return validated
