# StockLlama – Code Guide

A file-by-file explanation of the project: what each file is for, what its important functions do,
and how the pieces work together.

> **Not covered on purpose:** the installed/generated content that is committed in the repository –
> the virtual environment (`bin/`, `lib/`, `lib64/`, `share/`, `pyvenv.cfg`) and `__pycache__/`
> folders. They are installed dependencies and compiler output, not source code.

## Contents

1. [Big picture](#1-big-picture)
2. **AI Inventory Tracker**
   - [`main.py`](#mainpy)
   - [`app.py`](#apppy) – the GUI
   - [`database.py`](#databasepy) – PostgreSQL access
   - [`inventory_logic.py`](#inventory_logicpy) – matching, units, actions (pure logic)
   - [`ollama_parser.py`](#ollama_parserpy) – sentence → items
   - [`config.py`](#configpy) – settings
   - [`setup_db.py`](#setup_dbpy)
   - [`test_inventory_logic.py`](#test_inventory_logicpy)
3. **Pantry Tracker**
   - [`run.py`](#runpy), [`pantry_tracker.py`](#pantry_trackerpy), [`pantry_gui.py`](#pantry_guipy)
4. [Supporting files](#4-supporting-files) – `docker-compose.yml`, `requirements.txt`, `settings.json`,
   `.gitignore`, `ollama_funcs/`, `mongodb_funcs/`, `sorting/`
5. [Worked example: "removed 250 grams of active dry yeast"](#5-worked-example)

---

## 1. Big picture

The AI Inventory Tracker is built in layers. Each layer only knows about the one below it, which is
what makes the logic testable without a window or a model:

```
 main.py ─▶ app.py (Tkinter GUI: windows, dialogs, buttons)
               │              │
               │              └─▶ ollama_parser.py ─▶ Ollama (HTTP, local LLM)
               ▼                        │
          database.py  (SQL, transactions)       uses
               │                        ▼
               └────────────▶ inventory_logic.py (pure functions: no DB, no GUI, no network)
                                        ▲
                       config.py ───────┘ (DB_CONFIG, OLLAMA_CONFIG, MATCH_CONFIG, settings.json)
```

**The key design rule:** the language model only *interprets text*. Everything that changes data
(which row, which unit, never below zero, ask or not) is ordinary, tested Python code. A model
mistake can therefore produce a wrong *suggestion*, never a corrupted database.

**Data flow for one sentence**

1. `app.py` sends the text to `OllamaParser.parse_input` (in a background thread, streaming).
2. The parser returns a list of items `{item_name, action, quantity, unit, category, date_used,
   date_bought, notes}`; the action is double-checked against the user's words.
3. The user confirms; `app.py` calls `DatabaseManager.apply_stock_change(item, chooser=…)` per item.
4. `database.py` asks `inventory_logic.decide_match` which row is meant, optionally calls the GUI to
   ask the user, converts units and writes the change plus a history entry in one transaction.

---

## `main.py`

Entry point of the AI Inventory Tracker. Creates `InventoryApp` (from `app.py`) and calls `run()`.
Start with `python main.py`.

---

## `app.py`

The whole Tkinter user interface (about 1,500 lines). It contains the colour palette, five helper
widgets/dialogs and the main `InventoryApp` class.

### Palette

`COLORS` is a dict of hex colours (`bg`, `surface`, `surface2`, `surface3`, `border`, `accent`,
`accent2` green, `accent3` orange, `accent4` purple, `text`, `text_dim`, `warning`, `error`, …) used
everywhere so the dark theme stays consistent.

### Helper widgets and dialogs

| Class | Purpose and how it works |
|-------|--------------------------|
| `StyledButton(parent, text, command, style)` | A flat, themed `tk.Button`. Styles: `primary` (blue), `success` (green), `danger` (orange), `ghost` (grey), `nuclear` (dark red, for erase). `padx`/`pady` default to 16/8 but can be overridden through keyword arguments (used for compact header buttons). |
| `ScrollableFrame(parent, bg)` | A canvas + scrollbar container; child widgets go into `.inner`. `_on_inner_configure` keeps the scroll region equal to the content size; `_on_canvas_configure` makes the inner frame as wide as the canvas. The mouse wheel is bound globally (`bind_all`) for Windows/macOS (`<MouseWheel>`) and Linux (`Button-4/5`), but `_pointer_inside` checks that the pointer is actually over this frame, so the inventory table keeps its own scrolling. `_on_wheel` does the actual scrolling (two lines per notch) and does nothing if everything already fits. `capture_wheel(widget)` stops a widget such as a Combobox (which changes its value on wheel) from swallowing the event. Used for the *Manual Entry* panel and the merge dialog. |
| `HoldToConfirmDialog(...)` | Modal confirmation that cannot be passed by accident. The user must press **and hold** a canvas bar for `hold_seconds`. `_press` records `time.monotonic()` and starts `_tick`, which runs every 30 ms, redraws the progress (`_draw`) and, when the fraction reaches 1, closes the dialog and calls `on_confirm`. `_release` (button release **or** pointer leaving the bar) cancels and resets. Esc / Cancel closes it. Used by *Erase all*. |
| `SettingsDialog(parent, on_save, on_forget)` | Two-column form: PostgreSQL (host, port, database, username, password with *Show password*) and Ollama URL on the left, *Item matching* (mode drop-down, two percentage thresholds, *Forget remembered choices*) on the right. `_read` validates (port 1–65535, required fields, `0 ≤ similar ≤ confident ≤ 100`) and returns `(db_cfg, ollama_cfg, match_cfg)`. `_test` checks PostgreSQL (`psycopg2.connect(connect_timeout=5)`) and Ollama (`GET /api/tags`) in a background thread and shows the result without saving. `_save` hands the values to `on_save`. `_toggle_pw` shows/hides the password. |
| `MatchPromptDialog(parent, item, decision, position)` | The "similar item found" question. Lists each candidate of the `MatchDecision` as a radio button with stock and similarity %, plus *keep separate* (for add/set) or *none of these* (for remove). *Remember my choice* is a checkbox. `_confirm` builds the answer dict (`{"action": "merge", "row_id", "remember"}`, `{"action": "new", "remember"}` or `{"action": "skip"}`) into `self.result`; closing the window or Esc (`_skip`) means skip. |
| `MergeSimilarDialog(parent, groups, auto_threshold, on_merge)` | Shows the groups from `find_similar_groups` in a scrollable list. For each group: a "Merge this group" checkbox (pre-ticked when the group's lowest similarity ≥ the confident threshold), a radio button per item to choose the one to keep, a live preview (`preview`) of the resulting total in the kept item's unit, and a warning for items whose unit cannot be converted (`_add_group` builds the widgets of one group). `_merge` collects `(keep_id, [other_ids])` jobs and calls `on_merge`. |

### `InventoryApp`

**Construction and layout**

| Method | What it does |
|--------|--------------|
| `__init__` | Creates the root window (size/minimum from `APP_CONFIG`), the `OllamaParser`, state variables (`db`, `selected_item_id`, `sort_col`/`sort_desc`, `_suppress_select`), styles, UI, and starts `_connect_services`. |
| `_setup_styles` | Configures ttk styles for the table, headings (with hover colour), scrollbars and comboboxes. |
| `_build_ui` | Header (title, model drop-down, status text, **⚙ Settings**) and the main area: a left panel containing a `ScrollableFrame`, and a right area with stats and table. |
| `_build_input_panel` | Left panel: natural-language text box (with placeholder), **PARSE WITH AI**, the AI response box, the confirm area, then the manual form. |
| `_build_manual_form` | Fields: item name, quantity, unit, category (combobox), date bought, date used, notes; buttons **＋ ADD ITEM** and **✎ UPDATE**. Field variables live in `self.form_vars`. |
| `_build_stats_bar` | Four cards keyed `total_items`, `unique_items`, `total_categories`, `today_entries`. |
| `_build_table_panel` | Search box, category filter, **⟳ Refresh**, **⧉ Merge similar**, **☢ Erase all**, **🗑 Delete**, and the `ttk.Treeview` with nine columns (ID, Item Name, Qty, Unit, Category, Date Bought, Date Used, Notes, Last Updated). Each heading is wired to `_sort_by`. Row colours: `low` (orange) for low stock, `even`/`odd` otherwise. |

**Parsing and saving**

| Method | What it does |
|--------|--------------|
| `_parse_input` | Reads the text box and, in a daemon thread, calls `parser.parse_input(text, on_chunk=…)`. Streamed tokens are shown live in the AI response box via `root.after(0, …)` (Tkinter must only be touched from the main thread). Errors turn the status red (`● Ollama offline`, `● Error`). |
| `_show_parsed_items` | Displays what was understood (`＋ add` / `－ remove` / `＝ set to`, quantity, unit, name, category) and shows **✓ SAVE ALL** / **✗ Discard**. |
| `_save_parsed_items` | For each item calls `db.apply_stock_change(item, chooser=self._ask_match)`. Sets `_prompt_position` (`item 2 of 3`) for the prompt, then prints one result line per item (✓ or ⚠), updates status colour, refreshes table and stats. |
| `_ask_match` | The `chooser` callback: opens `MatchPromptDialog`, waits for it (`wait_window`) and returns its result. It runs on the main thread *between* database steps; no transaction is held open while the user decides. |
| `_manual_add` | Validates name/quantity, builds an `add` item and calls `apply_stock_change` (so it merges into an existing item instead of duplicating). |
| `_manual_update` | Overwrites the selected row with the form values via `db.update_item` (a deliberate "edit", not a stock movement). |
| `_delete_item` | Confirms and deletes the selected row. |
| `_on_row_select` / `_clear_placeholder` / `_on_model_change` | Fills the form from the selected row (skipped while `_suppress_select`), clears the input placeholder, switches the Ollama model. |

**Sorting**

| Method | What it does |
|--------|--------------|
| `_sort_by(col)` | Heading clicked: same column → flip direction, new column → ascending (descending for the date columns); updates arrows and refreshes. |
| `_update_heading_arrows` | Appends ▲/▼ to the active heading. |
| `_sorted(items)` | Sorts the list in Python: `id`/`quantity` numerically, dates as dates, text lower-cased. Empty values are placed last in both directions. |

**Erase all**

| Method | What it does |
|--------|--------------|
| `_erase_all` | Checks the connection, counts items (`db.count_items`), tells you if it is already empty, otherwise opens `HoldToConfirmDialog` (3 seconds). |
| `_do_erase_all` | Calls `db.clear_all()`, resets the selection and form, refreshes, reports `☢ Database erased (N item(s) removed)`. |

**Settings and similar items**

| Method | What it does |
|--------|--------------|
| `_open_settings` | Opens `SettingsDialog`. |
| `_apply_settings(db, ollama, match)` | `config.save_settings(...)` writes `settings.json` and updates the live config dicts; then closes the old connection, calls `parser.reload_config()`, clears the table and runs `_connect_services` again. If the file cannot be written the new values still apply for the session. |
| `_forget_matches` | Calls `db.forget_remembered_matches()`. |
| `_open_merge_similar` / `_do_merge` | Gets groups from the database, opens `MergeSimilarDialog`, and for each chosen group calls `db.merge_items`; reports how many were merged and how many were skipped because of units. |

**Refreshing and services**

| Method | What it does |
|--------|--------------|
| `_refresh_table` | Reads items (search/category filters) → `_sorted` → low-stock set → rewrites the tree with `iid = row id`; re-selects the previously selected row with `_suppress_select` set so the form is not overwritten; refreshes the category drop-down. Quantities are shown with `fmt_qty`. |
| `_refresh_stats` | Fills the four stat cards from `db.get_stats()`. |
| `_set_status` / `_set_ai_output` | Update the status text and the read-only AI response box. |
| `_connect_services` | Background thread: creates `DatabaseManager` (shows the "open ⚙ Settings" hint on failure), checks Ollama, lists models (prefers `tinyllama`) and sets the header status (`● Ready`, `● DB ok | Ollama offline`, …). |
| `run` / `_on_close` | Start the main loop / close the DB connection and window. |

---

## `database.py`

`DatabaseManager` wraps all PostgreSQL access for the Inventory Tracker (psycopg2). The connection
is in *autocommit* mode, and `_transaction()` temporarily switches to a real transaction when several
statements must succeed or fail together.

### Connection and schema

| Method | What it does |
|--------|--------------|
| `__init__` / `connect` | Connects with `DB_CONFIG` and a 5-second `connect_timeout` (so a wrong host cannot freeze the app), then `setup_tables`. |
| `setup_tables` | `CREATE TABLE IF NOT EXISTS` for `inventory`, `inventory_log`, `item_aliases`, `item_distinct`, then migrates the `quantity` and `quantity_change` columns to `NUMERIC(12,3)`. Safe to run every start. |
| `_transaction` | Context manager: `autocommit = False` → yields a `RealDictCursor` → commit, or rollback on any exception → autocommit restored. |
| `close` | Closes the connection. |

### Plain CRUD and queries

| Method | What it does |
|--------|--------------|
| `insert_item(item)` | Inserts a row and a log entry (older direct insert; the app now uses `apply_stock_change`). |
| `update_item(id, updates)` | Updates chosen columns of one row. If the quantity changed it logs the **difference** (new − old) as an `updated` entry, inside a transaction with the row locked. |
| `delete_item(id)` | Deletes one row (history and aliases go with it via `ON DELETE CASCADE`). |
| `get_all_items(search, category)` | Items filtered by name (`ILIKE`) and category, newest update first. |
| `get_categories()` | Distinct categories for the filter drop-down. |
| `get_stats()` | `total_items`, `total_categories`, `unique_items`, `total_quantity`, `today_entries`. |
| `get_low_stock(threshold)` | Rows with quantity ≤ threshold. |
| `count_items()` | Number of rows (used by *Erase all*). |
| `clear_all()` | `TRUNCATE inventory_log, inventory RESTART IDENTITY CASCADE` in a transaction: deletes everything including aliases and restarts the ids; returns how many items existed. |

### Stock changes (the heart of the update logic)

| Method | What it does |
|--------|--------------|
| `_match_settings()` | Reads the *live* `MATCH_CONFIG` (mode and two thresholds), so Settings changes apply immediately. |
| `resolve_match(item)` | Read-only: loads all rows, the saved aliases and the "keep separate" pairs, and returns `decide_match(...)`. Holds no lock, so the GUI may ask the user afterwards. |
| `apply_stock_change(item, chooser=None)` | The public entry point for adding, removing or setting. Validates name and quantity, calls `resolve_match`, then: **exact/auto** → use that row; **ask** → call `chooser(decision, item)` and act on `merge` / `new` / `skip`; **new** → create. Without a chooser (tests, scripts) an uncertain *add/set* becomes a separate item (legacy behaviour) and an uncertain *remove* returns `ambiguous` and does nothing. Returns a result dict `{status, message, item_id, old, new, unit}`. |
| `_apply(...)` | The write step, in one transaction. Re-reads the target row `FOR UPDATE` (it might have changed meanwhile). No row → create (adds/sets) or `not_found` (removals). Units that cannot be related → separate row for adds, `unit_mismatch` otherwise. Otherwise converts the quantity into the row's unit and computes: `add` → old + Δ; `set` → Δ; `remove` → old − Δ, floored at 0 (`clamped` if it had to). Updates quantity, `updated_at`, `raw_input` and `date_bought`/`date_used`, fills empty unit/category/notes, writes the history entry, and stores a remembered choice if requested. Builds the human message, adding `[counted 'x' as 'y']` when the name differed. |
| `_result(...)` | Small helper creating the result dict. |
| `_remember(cur, remember, name, row_id)` | Stores the answer to a question: an **alias** (`item_aliases`: this name → that row) or **distinct** pairs (`item_distinct`: this name ≠ these rows). |
| `forget_remembered_matches()` | Deletes both tables' contents; returns how many answers were removed. |

Result `status` values: `created`, `increased`, `decreased`, `set`, `clamped`, `not_found`,
`unit_mismatch`, `invalid`, `ambiguous`, `skipped`.

### Merging existing rows

| Method | What it does |
|--------|--------------|
| `find_similar_groups()` | Loads all rows and returns `inventory_logic.find_similar_groups(rows, ask_threshold)`. |
| `merge_items(canonical_id, member_ids)` | In one transaction, locks the rows; for every member whose unit converts into the kept row's unit: adds the converted quantity, takes the latest `date_bought`/`date_used`, re-points the member's `inventory_log` rows to the kept row, writes a `merged` log entry, deletes the member and stores its name as an alias. Members with an incompatible unit are skipped and reported. Returns `{merged, skipped, quantity, unit}`. |

---

## `inventory_logic.py`

Pure functions and small classes: **no database, no GUI, no network**. This is where the decisions
are made, which is why it can be unit tested in milliseconds.

### Units and formatting

| Function | What it does |
|----------|--------------|
| `normalize_unit(unit)` | Canonical unit symbol: `"Grams"` → `g`, `"litres"` → `l`, `"pcs"` → `pieces`; unknown units are returned lower-cased; empty/`none`/`null` → `None`. |
| `convert_quantity(qty, from, to)` | Returns `(value, ok)`. Converts within a family using a factor table (mass: mg, g, kg, oz, lb; volume: ml, l, tsp, tbsp, cup, fl oz). Same unit, or a missing unit on either side, is treated as "same unit". Different families or different count words (boxes vs pieces) → `ok = False`. |
| `fmt_qty(value)` | `0.250 → "0.25"`, `12.0 → "12"`. |

### Names

| Function | What it does |
|----------|--------------|
| `_singular(word)` | Cheap English singularising (`berries → berry`, `tomatoes → tomato`, `rice` and `hummus` unchanged). |
| `name_tokens(name)` | Lower-cases, strips punctuation and leading filler words (`some`, `the`, `a`, `of`, `my`), singularises each word. `"Active Dry Yeasts"` → `['active','dry','yeast']`. |
| `name_key(name)` | The tokens joined by spaces: the comparison key for "same name". |

### Similarity and matching

| Function / class | What it does |
|------------------|--------------|
| `_fuzzy_equal(a, b)` | Two words are equal, or (when both have ≥ 4 letters) have a `difflib` ratio ≥ 0.84 – tolerates typos such as `yest`/`yeast`. |
| `similarity(a, b, cat_a, cat_b)` | Score from 0 to 1. See the algorithm below. |
| `_rank(row, unit)` | Sort key used to choose between equally named rows: convertible unit first, then most recently updated, then lowest id. |
| `Candidate` | An existing row plus its score. |
| `MatchDecision` | The outcome: `kind` (`exact`, `auto`, `ask`, `new`), `row`, `score`, `candidates`, `via` (`alias` / `similarity`). |
| `decide_match(rows, name, unit, category, aliases, distinct, mode, auto, ask)` | Turns scores into a decision. See below. |
| `find_similar_groups(rows, threshold)` | Groups existing rows for the merge tool. Rows are processed from the most generic name (fewest words) to the least; each joins the first group whose *first* item it is similar to, otherwise it starts a new group. Comparing only with the group's representative stops unrelated items from being chained together. Each member carries `mergeable` (unit convertible to the representative's). |

**`similarity` algorithm**

1. Tokenise both names. Identical token lists → 1.0.
2. Give each word a weight: **3 for the last word (the head noun)**, 1 for the others.
3. Greedily pair up words that are `_fuzzy_equal`. `score = matched weight ÷ (total weight of both − matched)` (a weighted Jaccard).
4. If the head nouns match: the score is at least **0.58**; and if every word of the shorter name appears in the longer one, the score is at least **0.90** (minus 0.02 for each extra word beyond the first, so `yeast` vs `active dry yeast` is 0.88).
5. If the head nouns differ: the score is capped at **0.40** – `rice flour` is not `rice`.
6. If both categories are known: +0.04 when equal, −0.12 when different.

| Pair | Score |
|------|-------|
| yeast ~ active dry yeast | 0.88 |
| rice ~ basmati rice | 0.90 |
| active dry yeast ~ instant dry yeast | 0.67 |
| basmati rice ~ red rice | 0.60 |
| rice flour ~ rice | 0.40 |
| sugar ~ salt | 0.00 |

**`decide_match` rules, in order**

1. A remembered **alias** for this name → `exact`.
2. A row with the same `name_key` → `exact` (if several, prefer one with a convertible unit, then the most recently updated).
3. Mode `legacy` → `new`.
4. Otherwise score every row (skipping "keep separate" pairs and rows whose unit cannot be converted). Keep those ≥ the *similar* threshold, best first. None → `new`.
5. Mode `prompt_all` → `ask`.
6. Mode `prompt_low`: if the best score ≥ the *confident* threshold and the runner-up is below it (or at least 0.06 lower) → `auto` (use that row silently); otherwise → `ask`.

### Actions

| Function | What it does |
|----------|--------------|
| `normalize_action(value)` | Maps the model's word (`"used"`, `"purchase"`, `"consume"`, …) to `add` / `remove` / `set`, or `None`. |
| `_classify(text)` | Reads the action from a piece of text using verb patterns: only remove-verbs → `remove`; only add-verbs → `add`; both → unknown; otherwise words such as *left, remaining, now have* → `set`. |
| `infer_action(raw_text, item_name)` | Splits the sentence into clauses (at `, ; . and but then also while plus`), picks the clause that mentions the item and classifies it; falls back to the whole sentence only if that is unambiguous. This is how *"bought 3 kg rice and used 1 kg salt"* gets the right action for each item. |
| `resolve_action(raw_text, name, model_action, quantity)` | Final decision: explicit verbs in the text → the model's action → a negative quantity means remove → default `add`. |

---

## `ollama_parser.py`

`OllamaParser` talks to the local Ollama server (HTTP, `requests`) and converts a sentence into items.

| Item | What it does |
|------|--------------|
| `SYSTEM_PROMPT` | Instructions given to the model: extract `item_name`, **`action`** (add/remove/set, with word lists), always-positive `quantity`, `unit` exactly as written, `category`, `date_used`, `date_bought`, `notes`; return only a JSON array; today's date is inserted with `{today}`; examples show a purchase and the *active dry yeast* removal. |
| `__init__` / `reload_config` | Read `base_url`, `timeout` and `model` from `OLLAMA_CONFIG`. `reload_config` re-reads them after the Settings dialog changed the URL. |
| `is_available()` | `GET /api/tags` – is Ollama running? |
| `get_available_models()` | Names of installed models for the drop-down. |
| `set_model(model)` | Chooses the model for later requests. |
| `parse_input(text, on_chunk=None)` | Builds the request (system prompt with today's date, `format: "json"`, temperature 0.1) for Ollama's `/api/generate` and runs it either streaming (`_stream_parse`, token callbacks for the live display) or at once (`_sync_parse`). The reply goes through `_extract_items`, then `_resolve_actions`. Raises `ConnectionError` if Ollama is unreachable. |
| `_extract_items(text)` | Finds the JSON array in the reply (also accepts a single object), validates each entry: name, numeric quantity (falls back to 1), canonical unit (`normalize_unit`), category (default `General`), dates, notes, and the model's raw `action`. |
| `_resolve_actions(items, user_input)` | For each item calls `resolve_action` with the user's own sentence, so verbs in the text beat the model's answer; stores quantities as positive numbers; drops zero-quantity entries (except `set`). |

---

## `config.py`

Holds the defaults and the persistence of user changes.

| Name | What it is |
|------|------------|
| `DB_CONFIG` | `host`, `port`, `database` (`inventory_db`), `user`, `password` for psycopg2. |
| `OLLAMA_CONFIG` | `base_url`, default `model` (`tinyllama`), `timeout`. |
| `MATCH_CONFIG` | `mode` (`legacy` / `prompt_low` / `prompt_all`), `auto_threshold` (0.85), `ask_threshold` (0.55). |
| `APP_CONFIG` | Window title, geometry, minimum size and `low_stock_threshold` (5.0). |
| `SETTINGS_FILE` | `settings.json` next to `config.py`. |
| `load_settings()` | Run at import. Merges `settings.json` (sections `db`, `ollama`, `matching`) into the dicts **in place** – other modules hold references to the same dict objects, so they see the changes without re-importing. Ignores unknown keys and a missing/broken file. |
| `_sanitize_matching()` | Repairs hand-edited values: unknown mode → `prompt_low`; thresholds clamped to 0–1; *similar* never above *confident*. |
| `save_settings(db, ollama, matching)` | Updates the live dicts, then writes `settings.json` atomically (temp file + `os.replace`, mode 0600 where supported). |

---

## `setup_db.py`

One-time script (`python setup_db.py`). `setup()` connects to PostgreSQL's default `postgres`
database with `ADMIN_CONFIG` (edit it if your login differs), creates `inventory_db` if missing,
creates `inventory` and `inventory_log`, and inserts four sample rows (Rice, Cooking Oil, Notebooks,
Hand Sanitizer). The app creates the two alias tables itself on first start.

---

## `test_inventory_logic.py`

`python -m unittest test_inventory_logic -v` – 40 tests.

| Test class | Needs a DB? | Covers |
|------------|-------------|--------|
| `UnitTests` | no | unit names, conversions, number formatting |
| `ActionTests` | no | the reported sentence, verbs beating a wrong model answer, multi-item sentences, set/fallbacks |
| `SimilarityTests` | no | variants score high, same-head/different-modifier scores in the "ask" band, different products stay apart, category nudging |
| `DecisionTests` | no | exact vs auto vs ask vs new in each mode, ambiguity, remembered choices, incompatible units |
| `GroupTests` | no | grouping for the merge tool, unit-incompatible members |
| `StockChangeTests` | yes | removal converts units and updates one row, add merges, clamping, unknown item, unit mismatch, set, precision of small amounts |
| `FuzzyDatabaseTests` | yes | variant updates the generic row, removal through a variant, ask → merge + remember, keep separate + remember, skip, behaviour without a chooser, the three modes, merging existing rows (history and aliases kept), forgetting choices |

Database tests use the configured database but only touch rows named `ZZ test …` / `ZZ fz …`, clean up
after themselves and are skipped if PostgreSQL cannot be reached.

---

## `run.py`

Three lines: imports `main` from `pantry_tracker` and runs it. It exists so `pantry_tracker.py`
stays importable as a library (the Pantry GUI imports it).

---

## `pantry_tracker.py`

The Pantry Tracker core and command line. Configuration comes from **environment variables**
(`DB_HOST`, `DB_PORT`, `DB_NAME` default `pantry`, `DB_USER`, `DB_PASSWORD`, `OLLAMA_HOST`,
`OLLAMA_MODEL` default `llama3.2`) – it does not use `config.py`.

| Function | What it does |
|----------|--------------|
| `get_conn()` | New psycopg2 connection from `DB_CONFIG`. |
| `setup_db()` | Creates `pantry_items` (`name` unique, `quantity_kg NUMERIC(12,3)`, `last_updated`). |
| `reset_db()` | Drops and recreates the table. |
| `list_inventory()` / `print_inventory()` | Rows as dicts / a formatted table. |
| `query_ollama(text)` | POSTs the sentence with `SYSTEM_PROMPT` to Ollama's `/api/chat` (non-streaming, 120 s timeout) and returns the reply text; converts connection and HTTP errors to `RuntimeError`. |
| `parse_llm_response(raw)` | Strips code fences, extracts the first JSON object, validates each operation (name, numeric `delta_kg`, optional `purchased_at`) and returns the list. |
| `apply_operations(ops)` | For each operation: positive → add to an existing row or insert; negative → reduce (floored at 0, warning if more than stock) or warn if the item is unknown; zero → nothing. `purchased_at` becomes `last_updated`. Returns the message lines. All in one transaction. |
| `_ts(value)` | ISO string / `None` / datetime → timezone-aware datetime (`None` → now). |
| `main()` | Handles `--setup`, `--reset`, `--list`, otherwise runs the interactive loop (`list`, `quit`, or a sentence → LLM → operations → database). |

---

## `pantry_gui.py`

Tkinter front end for the Pantry Tracker; imports `pantry_tracker as pt` and reuses its functions.

| Item | What it does |
|------|--------------|
| `_tag_color(msg)` | Chooses the log colour from the emoji at the start of a message. |
| `SettingsDialog` | Edits Ollama host/model and the DB host/port/name/user/password; `_save` writes them into `pt.OLLAMA_HOST`, `pt.OLLAMA_MODEL` and `pt.DB_CONFIG` for the running session. |
| `PantryApp._build_styles` / `_build_ui` | Palette, text entry, activity log and inventory table. |
| `_init_db` | Creates the table on start-up (in a thread) and reports the result in the log. |
| `_submit` | Background thread: `query_ollama` → `parse_llm_response` → `apply_operations`; logs each step and refreshes the table. |
| `_refresh_table` | Shows `list_inventory()` in the table. |
| `log`, `_set_status`, `_set_busy` | Coloured log lines, status bar, disable input while working. |
| `_open_settings`, `_reset_db`, `run` | Open settings, confirm and reset the table, start the main loop. |

---

## 4. Supporting files

| File / folder | Purpose |
|---------------|---------|
| `docker-compose.yml` | Starts `postgres:16-alpine` as service `db`: database `pantry`, user/password `postgres`, port 5432, data volume `pantry_data`, health check `pg_isready`. Handy for both apps (the Inventory Tracker's `setup_db.py` creates `inventory_db` on the same server). |
| `requirements.txt` | `psycopg2-binary>=2.9`, `requests>=2.31`. (Tkinter comes with Python / the OS.) |
| `settings.json` | Created by the Settings dialog. Sections `db`, `ollama`, `matching`. Contains the DB password in plain text, so it is listed in `.gitignore`. Delete it to go back to the defaults. |
| `.gitignore` | Excludes `settings.json` and `settings.json.tmp`. |
| `docs/CODE_GUIDE.md` | This file. |
| `ollama_funcs/prompt_ollama.py` | Small experiment using the `ollama` Python package (`prompt_ollama(prompt)` calls `ollama.generate` with TinyLlama). Not used by either app and its packages are not in `requirements.txt`. |
| `mongodb_funcs/` (`insert.py`, `select.py`, `delete.py`) | Empty placeholder files. |
| `sorting/` | Unrelated utility scripts for sorting files by extension and collecting review files into CSV/JSON (`find_all_extensions`, `count_file_extensions`, `create_folder_based_on_extension`, `move_file_into_corresponding_sorted_folder`, `write_reviews_to_csv`, `list_of_dicts`, `list_of_dictss2`, `dict_od_dicts`). Not used by the apps. |

---

## 5. Worked example

Stock: `Active Dry Yeast – 2 kg`. The user types **"I have removed 250 grams of active dry yeast"**.

1. **Parsing** (`ollama_parser`): the model returns one item (`Active Dry Yeast`, quantity 250,
   unit `grams`, maybe no action). `normalize_unit("grams")` → `g`. `resolve_action` reads
   *removed* in the sentence → **remove**.
2. **Confirmation** (`app.py`): the box shows `－ remove 250 g Active Dry Yeast [Food]`; the user
   presses **SAVE ALL**.
3. **Matching** (`database.resolve_match` → `inventory_logic.decide_match`): no alias; the name key
   `active dry yeast` equals the stored row's key → `exact`, no question.
4. **Applying** (`database._apply`): row locked; `convert_quantity(250, "g", "kg")` → 0.25;
   `remove` → 2 − 0.25 = **1.75**; row updated (`updated_at`, `date_used` today, `raw_input`);
   `inventory_log` gets `removed, −0.25`.
5. **Result**: `Active Dry Yeast: removed (2 kg -> 1.75 kg)` is shown and the table refreshes.

Now the user types **"bought 500 g instant dry yeast"** and the stock is `Yeast – 1 kg`:

- `similarity("instant dry yeast", "yeast")` = 0.88 ≥ 0.85, and no other candidate is close → `auto`.
- The 500 g are converted to 0.5 kg and added: `Yeast: added (1 kg -> 1.5 kg) [counted 'instant dry yeast' as 'Yeast']`.

And **"bought 1 kg red rice"** with only `Basmati Rice – 2 kg` in stock:

- similarity 0.60 is between 0.55 and 0.85 → `ask`. The prompt offers *Same as "Basmati Rice"* or
  *keep it separate*. With *Remember my choice* ticked the answer is stored in `item_aliases`
  (or `item_distinct`) and the question is not asked again for "red rice".
