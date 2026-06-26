# 🥦 Pantry Tracker

Natural-language pantry inventory management powered by **Ollama** (local LLM) and **PostgreSQL**.

---

## Features

- Parse free-form sentences like *"I bought 12 kg onions and used 5 kg garlic"*
- Positive actions (bought / received / added) **increase** quantity
- Negative actions (used / consumed / finished) **decrease** quantity
- Quantities floored at **0 kg** — no negative stock ever
- New items are **auto-created**; existing items are **upserted**
- Optional purchase date extraction from natural language
- Weight normalisation: grams → kg, pounds → kg

---

## Quick Start

### 1 — Start PostgreSQL

**Option A — Docker (recommended):**
```bash
docker compose up -d
```

**Option B — existing Postgres:** create a database called `pantry`
```sql
CREATE DATABASE pantry;
```

### 2 — Install Ollama & pull a model

```bash
# Install: https://ollama.com/download
ollama pull llama3.2        # ~2 GB, fast and accurate enough
# or:
ollama pull mistral
ollama pull qwen2.5
```

Make sure Ollama is running:
```bash
ollama serve        # starts on http://localhost:11434
```

### 3 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4 — Run

```bash
python run.py
```

---

## Configuration (environment variables)

| Variable        | Default       | Description              |
|-----------------|---------------|--------------------------|
| `DB_HOST`       | `localhost`   | Postgres host            |
| `DB_PORT`       | `5432`        | Postgres port            |
| `DB_NAME`       | `pantry`      | Database name            |
| `DB_USER`       | `postgres`    | Postgres user            |
| `DB_PASSWORD`   | `postgres`    | Postgres password        |
| `OLLAMA_HOST`   | `http://localhost:11434` | Ollama base URL |
| `OLLAMA_MODEL`  | `llama3.2`    | Model name to use        |

Example:
```bash
OLLAMA_MODEL=mistral DB_PASSWORD=secret python run.py
```

---

## CLI flags

```bash
python run.py --setup    # create table, then exit
python run.py --list     # print current inventory, then exit
python run.py --reset    # drop + recreate table, then exit
```

---

## Example session

```
You: I bought 12kg onions and 1kg tomatoes, used 5kg garlic

🤖  Asking LLM… done.

📝  Detected 3 operation(s):
     • onion: +12.000 kg
     • tomato: +1.000 kg
     • garlic: -5.000 kg

✅  Database updated:
  🆕  onion: added 12.000 kg (new item)
  🆕  tomato: added 1.000 kg (new item)
  ⚠️   garlic: cannot reduce — item not found in inventory

You: received 3kg garlic, used 500g tomato

✅  Database updated:
  🆕  garlic: added 3.000 kg (new item)
  ➖  tomato: 1.000 kg → 0.500 kg

You: list

📦  Current Inventory:
  Item        Qty (kg)  Last updated
  ──────────────────────────────────
  garlic         3.000  2024-06-01 10:05
  onion         12.000  2024-06-01 10:04
  tomato         0.500  2024-06-01 10:05
```

---

## How it works

```
User input
   │
   ▼
Ollama (local LLM)
   │  Extracts: ingredient name, delta_kg (+/-), date
   ▼
JSON operations list
   │
   ▼
PostgreSQL
   ├─ item exists?  → UPDATE quantity_kg, last_updated
   └─ new item?     → INSERT row
```

The LLM returns a strict JSON schema:

```json
{
  "operations": [
    { "name": "onion",  "delta_kg":  12.0,  "purchased_at": null },
    { "name": "tomato", "delta_kg":   1.0,  "purchased_at": null },
    { "name": "garlic", "delta_kg":  -5.0,  "purchased_at": null }
  ]
}
```

The app never lets a quantity go below **0**.

---

## Database schema

```sql
CREATE TABLE pantry_items (
    id            SERIAL PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    quantity_kg   NUMERIC(12, 3) NOT NULL DEFAULT 0,
    last_updated  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
```
