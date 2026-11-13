# 🦙 StockLlama

Natural-language inventory management powered by **Ollama** (a local LLM) and **PostgreSQL**.
Nothing leaves your machine: the language model runs locally and the data lives in your own database.

The repository contains two applications that share the same idea (type what happened, let the
model understand it, let ordinary code update the database):

| App | What it is | Start it with |
|-----|------------|---------------|
| **AI Inventory Tracker** | Desktop GUI (Tkinter) for general inventory: any item, any unit, categories, dates, search, sorting, charts of stats | `python main.py` |
| **Pantry Tracker** | Kilogram-based pantry tracker, as a command-line program and a small GUI | `python run.py` / `python pantry_gui.py` |

> A file-by-file explanation of the code is in **[docs/CODE_GUIDE.md](docs/CODE_GUIDE.md)**.

---

## Table of contents

1. [What's new](#whats-new)
2. [AI Inventory Tracker](#ai-inventory-tracker)
   - [Features](#features)
   - [Quick start](#quick-start-ai-inventory-tracker)
   - [Using the app](#using-the-app)
   - [How quantities are updated](#how-quantities-are-updated)
   - [Similar items and fuzzy matching](#similar-items-and-fuzzy-matching)
   - [Settings](#settings)
   - [Database schema](#database-schema-ai-inventory-tracker)
3. [Pantry Tracker](#pantry-tracker)
4. [Project structure](#project-structure)
5. [Testing](#testing)
6. [Troubleshooting](#troubleshooting)
7. [Known limitations](#known-limitations)

---

## What's new

Everything below is in the AI Inventory Tracker.

### Fixes

- **Quantities are now updated instead of duplicated.** *"I have removed 250 grams of active dry
  yeast"* used to create a new row with quantity 250 and unit grams. It now finds the existing yeast
  row and subtracts 250 g from it (converted to the row's own unit, so 2 kg becomes 1.75 kg).
- **Add / remove / set is understood.** The parser returns an `action` for every item. The verbs in
  your own sentence (*removed, used, sold, bought, received, "3 kg left"*) take priority over the
  model's answer, because small models often get this wrong. Mixed sentences such as *"bought 3 kg
  rice and used 1 kg salt"* are handled item by item.
- **Units are converted.** Grams, kilograms, ounces, pounds, millilitres, litres, teaspoons,
  tablespoons, cups and fluid ounces convert between each other. Units that cannot be related (grams
  vs litres) change nothing and show a warning.
- **Stock never goes negative.** Removing more than you have sets the quantity to 0 and tells you.
  Removing an item that does not exist creates nothing.
- **Manual *ADD ITEM* merges** into an existing item instead of creating a duplicate.
- **More precise quantities.** The `quantity` column is widened to `NUMERIC(12, 3)` (migrated
  automatically) so that, for example, 5 g = 0.005 kg is not rounded away.
- **Change history is accurate.** Manual quantity edits are logged as the real change (new − old)
  instead of the absolute value.
- **The database connection times out after 5 seconds** instead of freezing the window.

### Similar-item matching

- **Fuzzy matching**: *instant dry yeast*, *active dry yeast* and *yeast* (or *basmati rice*,
  *red rice* and *rice*) are recognised as related names. **Stricter rule:** only the *same* name
  (spelling variant, plural, or a name from your dictionary) is merged silently; a name that merely
  contains another (*kidney beans* / *beans*) is always asked about, because it may be a different
  item. See [Similar items and fuzzy matching](#similar-items-and-fuzzy-matching).
- **📖 Names dictionary**: an item can have several names, including everyday local ones (*mint* =
  *pudina*, *curd* = *yogurt*, *turmeric* = *haldi*). Names of one group are treated as one item in
  both directions. See [Names dictionary](#names-dictionary-several-names-for-one-item).
- **Three behaviours** you can choose in Settings: keep everything separate (legacy), merge confident
  matches and ask when unsure (default), or ask for every similar item.
- **"Remember my choice"**: answers to the "is this the same item?" question are stored and not asked
  again.
- **⧉ Merge similar** tool for rows that already exist as separate items. Every item in a group gets
  its own decision (keep separate, or merge into another item), so *soya sauce* and *hot sauce*, or
  *kidney beans*, *mung beans* and *red beans*, can be kept as separate items.

### GUI

- **Sortable columns**: click a table heading to sort, click again to reverse (▲ / ▼).
- **☢ Erase all**: wipes the database, protected by a dialog and a press-and-hold confirmation.
- **Scrollable *Manual Entry*** panel: the left panel scrolls with the mouse wheel or scrollbar.
- **⚙ Settings** dialog: change the database host, port, name, username and password, the Ollama URL
  and the matching options without editing code. Includes *Test connection*.

### Project

- `inventory_logic.py` (new): all matching, unit and action logic as plain, testable Python.
- `default_names.py` (new): optional starter list of common local names for the dictionary.
- `test_inventory_logic.py` (new): 62 unit and database tests.
- `config.py` now loads and saves `settings.json`; `settings.json` is in `.gitignore`.
- This README and `docs/CODE_GUIDE.md`.

---

## AI Inventory Tracker

### Features

- Type in plain English: *"Bought 3 kg rice and 2 litres oil today"* → structured items with name,
  action, quantity, unit, category and dates, parsed by a local Ollama model (streamed live).
- Review the parsed result and **SAVE ALL** or **Discard**.
- Manual entry form (add, update the selected row, delete).
- Searchable, filterable, **sortable** inventory table with low-stock highlighting.
- Statistic cards: total items, unique items, categories, items added today.
- Update-instead-of-duplicate quantity logic with unit conversion.
- Fuzzy matching of item variations with prompts, remembered answers and a merge tool.
- Full change history in the `inventory_log` table.
- Settings dialog and a protected "erase everything" button.

### Quick start (AI Inventory Tracker)

**1. Requirements**

- Python 3.9 or newer with Tkinter (included with the python.org installers; on Debian/Ubuntu:
  `sudo apt install python3-tk`)
- PostgreSQL (local install or Docker)
- [Ollama](https://ollama.com/download) with at least one model, for example `ollama pull tinyllama`
  (the app lists whatever models you have installed and prefers `tinyllama`)

**2. Install the Python packages**

```bash
pip install -r requirements.txt        # psycopg2-binary, requests
```

**3. Start PostgreSQL** – either your own server, or the included Docker file:

```bash
docker compose up -d
```

**4. Create the database** (creates `inventory_db`, the tables and four sample rows):

```bash
python setup_db.py
```

`setup_db.py` connects as the `postgres` user with password `postgres` on `localhost:5432`; edit
`ADMIN_CONFIG` at the top of the file if yours differ.

**5. Run**

```bash
ollama serve          # if Ollama is not already running
python main.py
```

The status text in the header tells you what is connected: `● Ready`, `● DB ok | Ollama offline`,
and so on. If the database login is wrong, click **⚙ Settings** (top right) and fix it there.

### Using the app

**Natural-language input (left panel)**

1. Type something like `I have removed 250 grams of active dry yeast` and press **PARSE WITH AI**.
2. The *AI RESPONSE* box shows what was understood, for example `－ remove 250 g Active Dry Yeast [Food]`
   (`＋ add`, `－ remove` or `＝ set to`).
3. Press **✓ SAVE ALL**. Each line of the result is reported: what changed and from which quantity
   to which (`active dry yeast: removed (2 kg -> 1.75 kg)`). Problems are marked with ⚠.

**Manual entry (left panel, scrolls)**

Fill in the fields and press **＋ ADD ITEM** (adds to an existing item of that name or creates it),
or select a table row, edit the fields and press **✎ UPDATE** to overwrite its values.

**Table (right panel)**

| Control | What it does |
|---------|--------------|
| Search box | Filters by item name as you type |
| Category drop-down | Filters by category |
| **⟳ Refresh** | Reloads the table |
| **⧉ Merge similar** | Finds items with similar names and lets you decide, item by item, what to keep separate and what to merge |
| **📖 Names** | Opens the names dictionary (several names for one item); starts at the selected item |
| **☢ Erase all** | Deletes everything (see below) |
| **🗑 Delete** | Deletes the selected row after a confirmation |
| Column headings | Click to sort by that column; click again for the opposite order |

Rows at or below the low-stock threshold (5, set as `low_stock_threshold` in `config.py`) are shown in
orange. The default order is *Last Updated*, newest first.

**Sorting.** The current column shows ▲ (ascending) or ▼ (descending). Numbers sort numerically
(`ID`, `Qty`), dates chronologically, text case-insensitively. Empty values always go to the end.
The chosen order is kept when the table refreshes.

**☢ Erase all.** Press the red button and a dialog states how many items would be deleted. To
continue you must **press and hold the red bar for 3 seconds**; the bar fills as you hold. Letting go,
moving the pointer off the bar or pressing Esc cancels. It deletes all items, the whole change
history and the remembered matching choices, and restarts the id counters. This cannot be undone.

### How quantities are updated

For every parsed or manually added entry the app decides **which row** it refers to and **what to do**:

| Action | Triggered by (examples) | Effect |
|--------|------------------------|--------|
| `add` | bought, purchased, received, added, restocked, got, delivered | Increases the matching row, or creates it |
| `remove` | removed, used, consumed, took, sold, finished, ate, threw away, spent | Decreases the matching row (never below 0). Creates nothing if the item is unknown |
| `set` | "I now have 3 kg", "2 kg left", "stocktake" | Overwrites the quantity |

Rules, in order of priority for the action: explicit verbs in your sentence → the model's `action`
field → a negative quantity means remove → default `add`.

The quantity is converted into the **unit of the stored row**:

```
stock:  Active Dry Yeast  2 kg
input:  "I have removed 250 grams of active dry yeast"
result: Active Dry Yeast  1.75 kg        (250 g = 0.25 kg)
```

| Situation | Outcome |
|-----------|---------|
| Remove more than is in stock | Set to 0, warning shown (`clamped`) |
| Remove an item that is not in the inventory | Nothing changes (`not_found`) |
| Units cannot be converted (g vs l, boxes vs kg) | Nothing changes for removals (`unit_mismatch`); an addition becomes a separate row |
| Add with no matching item | New row is created |

Each change is written to `inventory_log` (`added`, `removed`, `set`, `updated`, `merged`).

### Similar items and fuzzy matching

Real inventories contain variations of the same thing: *yeast*, *active dry yeast*, *instant dry
yeast*; *rice*, *basmati rice*, *red rice*. The app scores how similar two names are (0–100 %) and
acts on the score.

**How the score works**

- Names are lower-cased, punctuation and plurals are ignored (`Tomatoes` = `tomato`), and small
  typos count as the same word (`yest` ≈ `yeast`).
- The **last word is the head noun** (yeast, rice, oil) and counts three times as much as the
  modifiers in front of it.
- Names made of the same words (allowing spelling variants and plurals), or names your
  [dictionary](#names-dictionary-several-names-for-one-item) lists as one item, score **100 %**.
  This is the only case that is ever *confident*.
- A name whose words all appear in a longer name with the same head noun scores **at most 80 %**
  (`rice` in `basmati rice`, `beans` in `kidney beans`): related, but possibly a different item.
- Same head noun but different modifiers scores about **60 %** (`basmati rice` / `red rice`).
- A **different head noun** is capped at 40 % (`rice flour` is not `rice`; `tomato paste` is not `tomato`).
- If both items have a category, the same category adds a little and different categories subtract
  (so `olive oil` and an `engine oil` in *Automotive* do not look alike).
- Only items whose unit can be converted are considered.

| New entry | Existing item | Score | Default behaviour |
|-----------|---------------|-------|-------------------|
| yest | yeast | 100 % | Merged silently (spelling) |
| Tomatoes | tomato | 100 % | Merged silently (plural) |
| pudina | mint | 100 % | Merged silently *if the dictionary says so* |
| instant dry yeast | yeast | 78 % | **Asks you** (related name) |
| basmati rice | rice | 80 % | **Asks you** |
| kidney beans | beans | 80 % | **Asks you** |
| red rice | basmati rice | 60 % | **Asks you** |
| instant dry yeast | active dry yeast | 67 % | **Asks you** |
| rice flour | rice | 40 % | Separate item |

**The three behaviours** (⚙ Settings → *Item matching* → *When a similar item is found*)

| Mode | Behaviour |
|------|-----------|
| **Legacy: keep every variation separate** | Only identical names merge (ignoring case and plurals). Everything else is its own item, as in the original app |
| **Merge confident matches, ask when unsure** *(default)* | Scores at or above the *confident* threshold (85 %: same name, spelling variant, dictionary name) merge without asking. Scores between the *similar* threshold (55 %) and 85 % open a prompt. Below 55 % the entry is a new item. (To get the old, looser behaviour back, lower *Confident match from* to 80 %) |
| **Ask for every similar item** | The prompt appears for every similar item, even confident ones |

Names that are exactly the same, names the dictionary lists as one item, and names you told the app to
remember never trigger a prompt in any mode.
If two existing items are equally good matches (for example both `active dry yeast` and `dry yeast`
exist) the app asks even in the default mode.

**The prompt.** It lists the candidate items with their stock and score, plus *keep it as a separate
item* (or *none of these* when removing). Tick **Remember my choice** to store the answer:

- choosing an existing item + remember → the new name becomes an *alias* of that item and is
  merged automatically from then on;
- choosing *separate* + remember → that name is never offered those items again.

Choosing *Skip this item* (or closing the window) leaves that entry unapplied. When several items
are saved at once, the prompt says which one it is (`item 2 of 3`).

**⧉ Merge similar (existing rows).** If similar items already exist as separate rows, press
**⧉ Merge similar**. Items are grouped by similar *names* (all the sauces, all the beans), but a group
is only a suggestion: *soya sauce* and *hot sauce* share a word and are still different items.
So the dialog does not force "one item per group":

- **Every item has its own drop-down**: *Keep as a separate item*, or *Merge into "…"* any other item
  of the group that is being kept. Keep as many items as you like and merge only the ones that really
  are the same. A group can even contain several separate merges (for example *Dried Kidney Beans*
  into *Kidney Beans* while *Mung Beans* stays on its own).
- **Safe by default**: only confident matches (at or above the *Confident match from* setting, which in
  practice means the same name or two names from your [dictionary](#names-dictionary-several-names-for-one-item),
  such as *Mint* and *Pudina*) are suggested for merging. Items that merely share or contain a word
  (*kidney beans*, *mung beans*, *beans*; *soya sauce*, *hot sauce*; *rice*, *basmati rice*) are preselected
  as *Keep as a separate item*; you merge them by choosing so.
- **Rules that keep the result valid**: an item that others are merged into cannot itself be merged
  away, and an item can only be merged into an item that is being kept and whose unit it can be
  converted to.
- **Live preview** under each group lists what you will end up with, for example
  `Beans: 1.5 kg  absorbs: Red Beans`, `Kidney Beans: 1 kg`, `Mung Beans: 2 kg`. Buttons:
  *Suggestion* (restore the default choices) and *Keep all separate*.
- **Apply this group** must be ticked for a group to be applied (groups with suggested merges are
  pre-ticked). When applied, merged quantities are converted into the kept item's unit and added, the
  change history is moved to the kept item, the most recent dates win, and the merged names become
  aliases. Items you kept separate are **remembered as different**: they are not suggested together
  again and a new entry for one is never matched to the other (undo with *Settings → Forget remembered
  choices*). Nothing is deleted unless you choose to merge it.

### Names dictionary (several names for one item)

An item can be known by more than one name, and a local name such as *pudina* shares no letters with
its English name *mint*, so no similarity score can find the connection. The **names dictionary** lets
you state it once:

- **📖 Names** (table toolbar) opens the dictionary. Type the names separated by commas
  (`mint, pudina`) and press **Create group**, or select an item first so the dialog opens at its
  group, then **Add name** to add one more name to a group (`mint, pudina, pudhina`).
- From then on the names are **one item in both directions**: an entry for *pudina* updates the row
  stored as *Mint* (`Mint: added (3 kg -> 3.5 kg)  [counted 'pudina' as 'Mint']`), and an entry for
  *mint* updates a row stored as *Pudina*. No question is asked, in any matching mode.
- It also works **inside longer names** (`fresh pudina` ≈ `fresh mint`, `pudina leaves` ≈
  `mint leaves`) and with **phrases** (`curry leaf` = `kadi patta`).
- It does not depend on rows: define the names **before or after** the items exist. If items already
  exist under different names, closing the dialog offers to **review and merge** them (they appear in
  *Merge similar* with a 100 % score).
- **Load starter names** adds about 40 common groups (mint = pudina, coriander = cilantro = dhania,
  turmeric = haldi, yogurt = curd = dahi, …) from `default_names.py`. Nothing is loaded automatically
  and every group can be edited or deleted.
- Groups merge when they overlap (adding `pudina, spearmint` to a group that has *pudina* gives one
  group). A name can belong to only one group. Removing a group's second-to-last name deletes the group.
- The dictionary is stored in PostgreSQL (`name_groups`, `name_group_names`). It is knowledge, not
  stock, so **Erase all** and *Forget remembered choices* do not delete it.

> The dictionary is what makes *Merge similar* confident about items with different names. It is
> deliberately explicit: the app never guesses that two unrelated words mean the same thing.

### Settings

Open **⚙ Settings** (top right). Values are saved in `settings.json` next to the app and used from
then on; the app reconnects as soon as you press **Save & reconnect**.

| Setting | Meaning |
|---------|---------|
| Host, Port, Database name, Username, Password | PostgreSQL connection (*Show password* reveals it) |
| Ollama Server URL | Where Ollama is running (default `http://localhost:11434`) |
| When a similar item is found | Matching mode (see above) |
| Confident match from (%) | Similarity at which a match is merged without asking (default 85) |
| Similar from (%) | Similarity at which items are considered similar at all (default 55) |
| Forget remembered choices | Deletes all stored aliases and "keep separate" answers |
| Test connection | Tries PostgreSQL and Ollama with the values entered, without saving |

`settings.json` contains the database password in plain text. It is excluded by `.gitignore`; on
Linux and macOS it is created readable only by you. To go back to the defaults, delete the file.

The model can be changed any time with the *Model* drop-down in the header.

### Database schema (AI Inventory Tracker)

`setup_db.py` creates the first two tables; the app itself creates/updates all six on start-up.

```sql
CREATE TABLE inventory (
    id           SERIAL PRIMARY KEY,
    item_name    VARCHAR(255) NOT NULL,
    quantity     NUMERIC(12, 3) NOT NULL,        -- widened from (10, 2) automatically
    unit         VARCHAR(50),
    category     VARCHAR(100),
    date_used    DATE,
    date_bought  DATE,
    notes        TEXT,
    raw_input    TEXT,                           -- the sentence that created/changed the row
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE inventory_log (                     -- history of every change
    id              SERIAL PRIMARY KEY,
    inventory_id    INTEGER REFERENCES inventory(id) ON DELETE CASCADE,
    action          VARCHAR(50),                 -- added / removed / set / updated / merged
    quantity_change NUMERIC(12, 3),
    notes           TEXT,
    logged_at       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE item_aliases (                      -- "this name means that item"
    alias_key    TEXT PRIMARY KEY,               -- normalised name, e.g. 'red rice'
    alias_name   TEXT NOT NULL,
    inventory_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE item_distinct (                     -- "this name is NOT that item"
    inventory_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
    name_key     TEXT NOT NULL,
    PRIMARY KEY (inventory_id, name_key)
);

CREATE TABLE name_groups (                       -- names dictionary: one row per item
    id    SERIAL PRIMARY KEY,
    label TEXT NOT NULL
);

CREATE TABLE name_group_names (                  -- every name of that item
    name_key     TEXT PRIMARY KEY,               -- normalised name, e.g. 'pudina'
    display_name TEXT NOT NULL,
    group_id     INTEGER NOT NULL REFERENCES name_groups(id) ON DELETE CASCADE
);
```

---

## Pantry Tracker

A smaller, separate tool: natural-language pantry management in **kilograms**, as a command-line
program (`run.py`) and a Tkinter window (`pantry_gui.py`). It uses its own database (`pantry`) and
table (`pantry_items`) and does not use the fuzzy matching described above.

### Features

- Parse free-form sentences like *"I bought 12 kg onions and used 5 kg garlic"*
- Positive actions (bought / received / added) **increase** quantity
- Negative actions (used / consumed / finished) **decrease** quantity
- Quantities floored at **0 kg**: no negative stock ever
- New items are **auto-created**; existing items are **upserted**
- Optional purchase date extraction from natural language
- Weight normalisation: grams → kg, pounds → kg

### Quick start

**1 – Start PostgreSQL**

```bash
docker compose up -d            # creates a database called "pantry"
```

(or create it yourself: `CREATE DATABASE pantry;`)

**2 – Install Ollama and pull a model**

```bash
ollama pull llama3.2            # ~2 GB, fast and accurate enough
# or: ollama pull mistral   /   ollama pull qwen2.5
ollama serve                    # http://localhost:11434
```

**3 – Install Python dependencies**

```bash
pip install -r requirements.txt
```

**4 – Run**

```bash
python run.py                   # command line
python pantry_gui.py            # graphical version
```

### Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | Postgres host |
| `DB_PORT` | `5432` | Postgres port |
| `DB_NAME` | `pantry` | Database name |
| `DB_USER` | `postgres` | Postgres user |
| `DB_PASSWORD` | `postgres` | Postgres password |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama base URL |
| `OLLAMA_MODEL` | `llama3.2` | Model name to use |

```bash
OLLAMA_MODEL=mistral DB_PASSWORD=secret python run.py
```

The GUI also has a **⚙ Settings** window for the same values (applied for the current session).

### CLI flags

```bash
python run.py --setup    # create table, then exit
python run.py --list     # print current inventory, then exit
python run.py --reset    # drop + recreate table, then exit (deletes all data)
```

### Example session

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
```

### How it works

```
User input → Ollama (local LLM) → JSON operations → PostgreSQL
                                                     ├─ item exists?  → UPDATE quantity_kg, last_updated
                                                     └─ new item?     → INSERT row
```

The model returns a strict JSON schema; the app never lets a quantity go below 0:

```json
{ "operations": [
    { "name": "onion",  "delta_kg":  12.0, "purchased_at": null },
    { "name": "garlic", "delta_kg":  -5.0, "purchased_at": null } ] }
```

### Database schema (Pantry Tracker)

```sql
CREATE TABLE pantry_items (
    id            SERIAL PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    quantity_kg   NUMERIC(12, 3) NOT NULL DEFAULT 0,
    last_updated  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
```

---

## Project structure

Only the project's own files are listed. (A committed virtual environment — `bin/`, `lib/`,
`lib64/`, `share/`, `pyvenv.cfg` — and `__pycache__/` folders are installed/generated content and
are not part of the source.)

```
.
├── main.py                  Starts the AI Inventory Tracker GUI
├── app.py                   The GUI: window, dialogs, table, all user actions
├── database.py              PostgreSQL access for the Inventory Tracker (DatabaseManager)
├── inventory_logic.py       Pure logic: units, names, similarity, dictionary, matching, action detection
├── ollama_parser.py         Talks to Ollama and turns sentences into structured items
├── config.py                Default settings + loading/saving of settings.json
├── setup_db.py              One-time creation of inventory_db, tables and sample data
├── default_names.py         Optional starter list of common local names (names dictionary)
├── test_inventory_logic.py  Unit and database tests
├── settings.json            (created by the Settings dialog; git-ignored)
│
├── run.py                   Starts the Pantry Tracker command line
├── pantry_tracker.py        Pantry Tracker core + CLI (kg-based, table pantry_items)
├── pantry_gui.py            Pantry Tracker Tkinter window
│
├── docker-compose.yml       PostgreSQL 16 container (database "pantry")
├── requirements.txt         psycopg2-binary, requests
├── docs/CODE_GUIDE.md       File-by-file guide to the code
│
├── ollama_funcs/            Experimental Ollama snippet (not used by the apps)
├── mongodb_funcs/           Empty placeholder files (not used)
└── sorting/                 Unrelated file-sorting helper scripts (not used)
```

---

## Testing

```bash
python -m unittest test_inventory_logic -v
```

- The **logic tests** (units, action detection, similarity, matching decisions, grouping, "keep separate" pairs) need
  nothing but Python.
- The **database tests** (quantity updates, merging, remembered choices, the "250 grams of yeast"
  scenario, the matching modes, merge tool) use the PostgreSQL configured in `config.py` /
  `settings.json`. They only create and delete rows whose names start with `ZZ test ` or `ZZ fz `,
  and are skipped automatically when PostgreSQL cannot be reached.

---

## Troubleshooting

| Problem | What to do |
|---------|------------|
| *"Could not connect to PostgreSQL"* at start-up | Press **⚙ Settings** → correct host/port/database/user/password → **Test connection** → **Save & reconnect**. Run `python setup_db.py` if `inventory_db` does not exist yet |
| Header shows *Ollama offline* | Start it with `ollama serve`, or fix the URL in Settings |
| Parsing returns wrong or no items | Try a larger model in the *Model* drop-down; small models are less reliable. Verbs like *removed* or *bought* in the sentence help |
| An item was merged into the wrong one | Use **⚙ Settings → Forget remembered choices**, or switch the mode to *Ask for every similar item* / raise *Confident match from (%)*. In **⧉ Merge similar** choose *Keep as a separate item* for items that are different |
| A removal did nothing | Read the ⚠ message: the item is not in the inventory, the units cannot be converted, or the name matched several items equally and you skipped it |
| Items from older versions are duplicated (e.g. *yeast* and *yeast – 250 g*) | Press **⧉ Merge similar** to combine them |
| `ModuleNotFoundError: tkinter` | Install Tkinter (`sudo apt install python3-tk` on Debian/Ubuntu) |

---

## Known limitations

- Fuzzy matching is name-based: related names (*coconut milk* / *milk*, *kidney beans* / *beans*) are always
  asked about, never merged silently, but the question appears every time unless you tick *Remember my
  choice*.
- The names dictionary is only as complete as you make it; unrelated local names (such as *pudina* and
  *mint*) are not guessed.
- Low-stock highlighting compares the raw number with one threshold regardless of unit.
- The parsed *category* comes from the language model and may vary.
- Only English sentences are tuned for.
- `settings.json` stores the database password unencrypted.
