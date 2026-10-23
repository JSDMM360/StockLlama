# 🥦 StockLlama

Natural-language inventory management powered by **Ollama** (local LLM) and **PostgreSQL**,
with a dark-themed desktop GUI, **voice input** (FUTO / Whisper models), **activity logging**
and **per-ingredient charts**. Everything runs locally.

---

## Features

- Parse free-form sentences like *"Bought 3 kg rice and 2 liters oil today"* into structured items
- Review the parsed items, then **Save** or **Discard** them
- Manual add / update / delete, search and category filter, low-stock highlighting
- **Voice input** – dictate instead of typing; choose from FUTO ACFT, stock Whisper or your own models
- **Logs tab** – every input with its date, prompt and resulting events
- **Charts tab** – pick ingredients and a chart type, one combined chart or one per ingredient
- **Predictions tab** – which ingredient will be used next, when, and when stock runs out

The window has four tabs: **📦 Inventory**, **📊 Charts**, **🔮 Predictions**, **📋 Logs**.

---

## Quick start

### 1 — Start PostgreSQL

```bash
docker compose up -d          # or use an existing Postgres and edit config.py
```

### 2 — Install Ollama & pull a model

```bash
# https://ollama.com/download
ollama pull tinyllama         # default in the GUI; llama3.2 / mistral / qwen2.5 also work
ollama serve                  # http://localhost:11434
```

### 3 — Install Python dependencies

```bash
pip install -r requirements.txt
pip install matplotlib numpy sounddevice        # charts + microphone
```

Voice recognition needs **one** of the backends below (see [Voice input](#voice-input)).

### 4 — Run

```bash
python main.py
```

---

## Voice input

Click **🎙 RECORD**, speak, click **■ STOP**. The transcription is placed in the input box
(appended if text is already there) so you can correct it before pressing **PARSE WITH AI**.
The speech model loads in the background while you are still talking.

### Models

| Group | Models | Backend | Install |
|---|---|---|---|
| **FUTO ACFT** (Whisper fine-tuned by FUTO) | tiny / base / small, English-only (`.en`) and multilingual | `transformers` | `pip install transformers torch` |
| **Stock Whisper** | tiny.en, base.en, small.en, medium.en, small, medium, large-v3 | `faster-whisper` | `pip install faster-whisper` |
| **Local files** | any whisper.cpp `.bin` (e.g. FUTO's GGML downloads) | `pywhispercpp` | `pip install pywhispercpp` |
| **Local folders** | Hugging Face Whisper folder, or CTranslate2 folder | auto-detected | — |

FUTO's models come from <https://github.com/futo-org/whisper-acft>; the Hub checkpoints are
named `futo-org/acft-whisper-{tiny,base,small}[.en]` and download automatically on first use
(~75 MB tiny, ~142 MB base, ~465 MB small). **base.en** is the default: a good speed/accuracy balance.

**Adding your own model:** click **…** next to the model list, or drop a `.bin` file / model folder
into `models/stt/` and restart. Any Hugging Face repo id can also be loaded from code:
`SpeechRecognizer().load("owner/repo")`.

**Language:** `auto` by default; pick `en`, `hi`, `kn`, `ta`, … for multilingual models.
English-only (`.en`) models ignore this setting. Accuracy on non-English speech with tiny/base is limited.

> **Note on FUTO's speed advantage.** FUTO's ACFT models are designed to run with whisper.cpp's
> *dynamic audio context*, which is what makes short dictations fast on phones. The `.bin`
> (whisper.cpp) route uses it automatically for files with `acft` in the name. Through the
> `transformers` route the models run the normal 30 s window: they work fine, just without that speed-up.

---

## Predictions

The **🔮 Predictions** tab learns each ingredient's usage pattern from your history and answers:

- **Which ingredient will be used next, and when?** (*Next use* column, plus a *Chance* that it is used within the window you pick: 3 / 7 / 14 / 30 days)
- **When will the stock run out?** (*Runs out* date and a *Days left* range)
- **Should I reorder?** (*Advice*: Reorder now / Reorder soon / OK / Out of stock / Possibly inactive)

Sort by *Next use*, *Runs out first* or *Most likely*. Set the **reorder lead time** (days your supplier needs)
and the advice adapts. Select a row for a plain-language explanation and a chart of past stock with the
projected stock, the likely range, the next expected use and the "reorder by" date.

**How it works** (simple and explainable, no training step):

| Quantity | Method |
|---|---|
| Typical gap | median days between usage days (last 10 usage days only, so it follows changing habits) |
| Consumption per day | quantity used ÷ days elapsed over that window |
| Next use | last use + typical gap (shown as "today" if overdue) |
| Chance in N days | of the past gaps still "open", the share that ended within N days (smoothed, never 0% or 100%) |
| Run-out range | current stock ÷ fast (75th percentile) and slow (25th percentile) consumption rates |
| Confidence | few records = low, 6+ = medium, 10+ = high; one level lower if the gaps are very irregular |

**Self-check.** The tab back-tests itself on your own history: it predicts each past usage date from the
ones before it and reports the average error in days. Ingredients need 4+ usage days for this.

**Limits to know about**

- An ingredient needs at least **3 usage days** before it is predicted; otherwise it shows *Not enough data*.
- Only *current stock* is projected; future purchases are not assumed.
- Forecasts are only as good as the data: a usage must be recorded as **used** (a `date_used`), see below.
- It is a statistical heuristic, not a trained machine-learning model.

### Recording usage so predictions work

An entry counts as usage only if it has a `date_used` and no `date_bought`. If the LLM leaves both dates empty
(e.g. *"used 2 kg rice"* with no date), it is treated as a purchase. Add these rules to `SYSTEM_PROMPT`
in `ollama_parser.py` so dates are always filled:

```
- If the text says an item was used, consumed, finished or cooked, put the date in date_used and leave date_bought null. If no date is given, use today's date: {today}
- If the text says an item was bought, purchased, received or restocked, put the date in date_bought. If no date is given, use today's date: {today}
- Never fill both date_used and date_bought for the same item
```

Small models such as tinyllama sometimes ignore rules like these, so check the parsed items before pressing SAVE ALL.

---

## Activity log

Every input produces an entry containing:

- **date** and time,
- the **input prompt** (typed, spoken or a manual add/update/delete),
- the **events** that followed: speech transcription, Ollama model used, raw LLM response,
  parsed items, saved / discarded / updated / deleted, errors.

Files are written to `./logs/` (override with the `STOCKLLAMA_LOG_DIR` environment variable):

| File | Purpose |
|---|---|
| `activity.jsonl` | Machine-readable, append-only; what the Logs tab reads |
| `activity.log` | Human-readable, rotating (1 MB × 3) |

Example (`activity.log`):

```
2026-10-17 06:14:25 | [3f9a1c2e] INPUT (voice): bought 2 kg flour
2026-10-17 06:14:25 | [3f9a1c2e]   speech: Transcribed with FUTO ACFT base.en (3.2s audio, 1.1s, language=auto)
2026-10-17 06:14:25 | [3f9a1c2e]   model: Ollama model: tinyllama
2026-10-17 06:14:25 | [3f9a1c2e]   parsed: 1 item(s) detected: ✓ 2.0 kg Flour [Food]
2026-10-17 06:14:25 | [3f9a1c2e]   saved: Saved 1 item(s) to the database (ids: 5)
```

### Logs tab

Entries are listed newest first with a colour-coded result (`saved`, `awaiting save`, `discarded`,
`no items`, `error`, …). Select one to see its full prompt and event timeline. You can search across
prompts and events, filter by source (`typed` / `voice` / `manual`), and **Export CSV**.
The tab updates live as new inputs arrive.

---

## Charts

Open the **📊 Charts** tab, then:

1. **Select ingredients** in the list (multi-select; *Select all* / *Clear*).
2. **Choose a chart type**: Line, Area, Bar or Pie.
3. Toggle **All ingredients in one chart**: on = a single chart, off = one chart per ingredient.

Charts are drawn inside the window and redraw when you change anything. **Export PNG** saves them
(a single file, or one file per ingredient into a folder).

| Type | Combined chart | Separate charts |
|---|---|---|
| Line / Area | Stock level over time, one line per ingredient | Stock level over time |
| Bar | Current stock per ingredient | Daily change (green = bought, red = used) |
| Pie | Share of current stock | Bought vs used |

**How the numbers are derived.** Each row in the `inventory` table is one movement. A row with
only `date_used` is consumption (−); anything else is a purchase / stock-in (+). The movement date
is `date_bought`, else `date_used`, else the day the row was created. Ingredient names are grouped
case-insensitively. Quantities in a combined chart can have different units (kg, liters, pieces);
the axis then says *mixed units*.

Separate mode shows at most 12 charts at once; narrow the selection to see others.

---

## Configuration

| Variable / file | Description |
|---|---|
| `config.py` | Database, Ollama and window settings |
| `STOCKLLAMA_LOG_DIR` | Where log files are written (default `./logs`) |
| `models/stt/` | Drop local speech models here |

---

## Project layout

```
main.py            entry point
app.py             main window: Inventory / Charts / Predictions / Logs tabs
gui_tabs.py        ChartsTab, LogsTab, SpeechControls widgets
predict_tab.py     PredictionsTab widget
predictions.py     forecasting logic, back-test, forecast chart (no Tk)
charts.py          chart data + matplotlib figure builders (no Tk)
activity_log.py    activity logging (JSONL + readable log)
stt_engine.py      speech models (FUTO / Whisper / whisper.cpp) + microphone capture
database.py        PostgreSQL access (inventory, inventory_log)
ollama_parser.py   LLM prompt + JSON parsing
config.py          settings
requirements-extra.txt   optional packages for charts and voice
```

### Database schema

```sql
CREATE TABLE inventory (
    id SERIAL PRIMARY KEY, item_name VARCHAR(255) NOT NULL,
    quantity NUMERIC(10,2) NOT NULL, unit VARCHAR(50), category VARCHAR(100),
    date_used DATE, date_bought DATE, notes TEXT, raw_input TEXT,
    created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE inventory_log (
    id SERIAL PRIMARY KEY, inventory_id INTEGER REFERENCES inventory(id) ON DELETE CASCADE,
    action VARCHAR(50), quantity_change NUMERIC(10,2), notes TEXT,
    logged_at TIMESTAMP DEFAULT NOW()
);
```

The activity log described above is separate from `inventory_log`: it lives in files and records
*what the user typed or said and what happened next*, not just row changes.

---

## Troubleshooting

- **"needs an extra package"** – install the pip package named in the message for that model.
- **No microphone / `sounddevice` error** – `pip install sounddevice`; on Linux also `sudo apt install libportaudio2`.
- **First voice use is slow** – the model is downloading; later runs use the cached copy.
- **Charts tab says "No inventory data yet"** – add items first (or check the database connection).
- **Ollama offline** – run `ollama serve` and `ollama pull <model>`.
