"""
Configuration for Inventory Tracker App

The values below are the defaults. Anything saved from the app's Settings dialog is stored in
settings.json (next to this file) and overrides these defaults on the next start, so you can
change the database host, name, username or password without editing code.
"""

import json
import os

# PostgreSQL configuration
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "inventory_db",
    "user": "postgres",
    "password": "postgres",  # Change this in the app: Settings > Password
}

# Ollama configuration
OLLAMA_CONFIG = {
    "base_url": "http://localhost:11434",
    "model": "tinyllama",  # Default model; can be changed in UI
    "timeout": 60,
}

# Item matching: how variations of the same item (instant dry yeast / active dry yeast / yeast,
# basmati rice / red rice / rice) are handled. Changeable in Settings.
#   mode            "legacy"      every variation stays a separate item (old behaviour)
#                   "prompt_low"  merge confident matches silently, ask when unsure (default)
#                   "prompt_all"  ask about every similar item
#   auto_threshold  similarity at or above which a match counts as confident (0 - 1)
#   ask_threshold   similarity at or above which items count as "similar" (0 - 1)
MATCH_CONFIG = {
    "mode": "prompt_low",
    "auto_threshold": 0.85,
    "ask_threshold": 0.55,
}

# App configuration
APP_CONFIG = {
    "title": "AI Inventory Tracker",
    "geometry": "1280x800",
    "min_size": (1100, 700),
    "low_stock_threshold": 5.0,
}

# ── Persisted user settings ───────────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def load_settings():
    """Merge settings.json (if present) into DB_CONFIG / OLLAMA_CONFIG, in place."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for key, target in (("db", DB_CONFIG), ("ollama", OLLAMA_CONFIG), ("matching", MATCH_CONFIG)):
        values = data.get(key)
        if isinstance(values, dict):
            for k, v in values.items():
                if k in target:
                    target[k] = v
    # "port" must be an int for psycopg2
    try:
        DB_CONFIG["port"] = int(DB_CONFIG["port"])
    except (TypeError, ValueError):
        DB_CONFIG["port"] = 5432
    _sanitize_matching()


def _sanitize_matching():
    """Keep the matching settings usable even if settings.json was edited by hand."""
    if MATCH_CONFIG.get("mode") not in ("legacy", "prompt_low", "prompt_all"):
        MATCH_CONFIG["mode"] = "prompt_low"
    try:
        auto = min(1.0, max(0.0, float(MATCH_CONFIG["auto_threshold"])))
        ask = min(1.0, max(0.0, float(MATCH_CONFIG["ask_threshold"])))
    except (TypeError, ValueError):
        auto, ask = 0.85, 0.55
    if ask > auto:
        ask = auto
    MATCH_CONFIG["auto_threshold"], MATCH_CONFIG["ask_threshold"] = auto, ask


def save_settings(db=None, ollama=None, matching=None):
    """
    Update the live config dictionaries and write them to settings.json.
    The dictionaries are changed in place, so modules that imported them see the new values.
    The file contains the database password in plain text; it is only readable by the
    current user on Linux/macOS, and should not be committed to version control.
    """
    if db:
        DB_CONFIG.update(db)
        DB_CONFIG["port"] = int(DB_CONFIG["port"])
    if ollama:
        OLLAMA_CONFIG.update(ollama)
    if matching:
        MATCH_CONFIG.update(matching)
        _sanitize_matching()
    payload = {"db": dict(DB_CONFIG),
               "matching": dict(MATCH_CONFIG),
               "ollama": {"base_url": OLLAMA_CONFIG["base_url"],
                          "model": OLLAMA_CONFIG["model"],
                          "timeout": OLLAMA_CONFIG["timeout"]}}
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # not supported on some platforms
    os.replace(tmp, SETTINGS_FILE)


load_settings()
