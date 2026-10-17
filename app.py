"""
Main GUI Application for AI Inventory Tracker
Modern dark-themed UI using tkinter + ttk

Tabs: Inventory | Charts | Logs
"""

import tkinter as tk
from tkinter import ttk, messagebox, font
import threading
import time
from datetime import datetime

from database import DatabaseManager
from ollama_parser import OllamaParser
from config import APP_CONFIG

from activity_log import ActivityLogger
from gui_tabs import ChartsTab, LogsTab, SpeechControls
from stt_engine import SpeechRecognizer

# ── Color Palette ─────────────────────────────────────────────────────────────
COLORS = {
    "bg": "#0D1117",
    "surface": "#161B22",
    "surface2": "#21262D",
    "surface3": "#30363D",
    "border": "#30363D",
    "accent": "#58A6FF",
    "accent2": "#3FB950",
    "accent3": "#F78166",
    "accent4": "#D2A8FF",
    "text": "#E6EDF3",
    "text_dim": "#8B949E",
    "text_dimmer": "#484F58",
    "warning": "#D29922",
    "success": "#3FB950",
    "error": "#F85149",
}


class StyledButton(tk.Button):
    def __init__(self, parent, text, command=None, style="primary", **kwargs):
        styles = {
            "primary": {"bg": COLORS["accent"], "fg": "#0D1117", "activebackground": "#79C0FF"},
            "success": {"bg": COLORS["accent2"], "fg": "#0D1117", "activebackground": "#56D364"},
            "danger": {"bg": COLORS["accent3"], "fg": "#0D1117", "activebackground": "#FF8B7A"},
            "ghost": {"bg": COLORS["surface2"], "fg": COLORS["text"], "activebackground": COLORS["surface3"]},
        }
        s = styles.get(style, styles["primary"])
        super().__init__(
            parent, text=text, command=command,
            bg=s["bg"], fg=s["fg"],
            activebackground=s["activebackground"],
            activeforeground=s.get("fg", COLORS["text"]),
            relief="flat", bd=0,
            cursor="hand2",
            font=("Consolas", 10, "bold"),
            padx=16, pady=8,
            **kwargs
        )


class InventoryApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_CONFIG["title"])
        self.root.geometry(APP_CONFIG["geometry"])
        self.root.minsize(*APP_CONFIG["min_size"])
        self.root.configure(bg=COLORS["bg"])

        self.db = None
        self.parser = OllamaParser()
        self.selected_item_id = None

        # logging + speech state
        self.logger = ActivityLogger()
        self.recognizer = SpeechRecognizer()
        self._pending_entry = None      # log entry waiting for SAVE / Discard
        self._voice_text = None         # text most recently produced by speech
        self._voice_result = None

        self._setup_styles()
        self._build_ui()
        self._connect_services()

    def _setup_styles(self):
        """Configure ttk styles."""
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Dark.TFrame", background=COLORS["bg"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("Surface2.TFrame", background=COLORS["surface2"])

        style.configure("TLabel",
                        background=COLORS["bg"], foreground=COLORS["text"],
                        font=("Consolas", 10))
        style.configure("Title.TLabel",
                        background=COLORS["bg"], foreground=COLORS["text"],
                        font=("Consolas", 20, "bold"))
        style.configure("Subtitle.TLabel",
                        background=COLORS["bg"], foreground=COLORS["text_dim"],
                        font=("Consolas", 10))
        style.configure("Surface.TLabel",
                        background=COLORS["surface"], foreground=COLORS["text"],
                        font=("Consolas", 10))
        style.configure("Surface2.TLabel",
                        background=COLORS["surface2"], foreground=COLORS["text"],
                        font=("Consolas", 10))
        style.configure("Accent.TLabel",
                        background=COLORS["surface"], foreground=COLORS["accent"],
                        font=("Consolas", 14, "bold"))
        style.configure("Success.TLabel",
                        background=COLORS["surface"], foreground=COLORS["accent2"],
                        font=("Consolas", 9))
        style.configure("Warning.TLabel",
                        background=COLORS["surface"], foreground=COLORS["warning"],
                        font=("Consolas", 9))
        style.configure("Dim.TLabel",
                        background=COLORS["surface"], foreground=COLORS["text_dim"],
                        font=("Consolas", 9))

        # Treeview
        style.configure("Treeview",
                        background=COLORS["surface"],
                        foreground=COLORS["text"],
                        fieldbackground=COLORS["surface"],
                        borderwidth=0,
                        font=("Consolas", 10),
                        rowheight=32)
        style.configure("Treeview.Heading",
                        background=COLORS["surface2"],
                        foreground=COLORS["text_dim"],
                        borderwidth=0,
                        font=("Consolas", 9, "bold"),
                        relief="flat")
        style.map("Treeview",
                  background=[("selected", COLORS["surface3"])],
                  foreground=[("selected", COLORS["accent"])])
        style.map("Treeview.Heading",
                  background=[("active", COLORS["surface3"])])

        # Entry / Combobox
        style.configure("TEntry",
                        fieldbackground=COLORS["surface2"],
                        foreground=COLORS["text"],
                        bordercolor=COLORS["border"],
                        insertcolor=COLORS["text"])
        style.configure("TCombobox",
                        fieldbackground=COLORS["surface2"],
                        foreground=COLORS["text"],
                        background=COLORS["surface2"],
                        selectbackground=COLORS["surface3"],
                        selectforeground=COLORS["text"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", COLORS["surface2"])],
                  foreground=[("readonly", COLORS["text"])])
        # drop-down list colours (Combobox popups are plain Tk listboxes)
        self.root.option_add("*TCombobox*Listbox.background", COLORS["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#0D1117")

        # Scrollbar
        style.configure("Vertical.TScrollbar",
                        background=COLORS["surface3"],
                        troughcolor=COLORS["surface"],
                        bordercolor=COLORS["surface"],
                        arrowcolor=COLORS["text_dim"])

        # Notebook (tabs)
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0,
                        tabmargins=(12, 6, 0, 0))
        style.configure("TNotebook.Tab",
                        background=COLORS["surface2"],
                        foreground=COLORS["text_dim"],
                        padding=(14, 8), borderwidth=0,
                        font=("Consolas", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["surface"])],
                  foreground=[("selected", COLORS["accent"])])

    def _build_ui(self):
        """Build the main UI layout."""
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=COLORS["surface"], height=64)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Label(header,
                 text="◈ AI INVENTORY TRACKER",
                 bg=COLORS["surface"], fg=COLORS["accent"],
                 font=("Consolas", 16, "bold")).pack(side="left", padx=24, pady=16)

        self.status_label = tk.Label(header,
                                     text="● Connecting...",
                                     bg=COLORS["surface"], fg=COLORS["warning"],
                                     font=("Consolas", 9))
        self.status_label.pack(side="right", padx=24)

        self.model_var = tk.StringVar(value="tinyllama")
        model_frame = tk.Frame(header, bg=COLORS["surface"])
        model_frame.pack(side="right", padx=8)
        tk.Label(model_frame, text="Model:", bg=COLORS["surface"],
                 fg=COLORS["text_dim"], font=("Consolas", 9)).pack(side="left")
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.model_var,
                                        width=18, font=("Consolas", 9))
        self.model_combo.pack(side="left", padx=4)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        # ── Tabs: Inventory | Charts | Logs ───────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        main = tk.Frame(self.notebook, bg=COLORS["bg"])           # Inventory tab
        self.charts_tab = ChartsTab(self.notebook, self._all_rows, COLORS)
        self.logs_tab = LogsTab(self.notebook, self.logger, COLORS)
        self.notebook.add(main, text="  📦 Inventory  ")
        self.notebook.add(self.charts_tab, text="  📊 Charts  ")
        self.notebook.add(self.logs_tab, text="  📋 Logs  ")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Left panel
        left = tk.Frame(main, bg=COLORS["surface"], width=340)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_input_panel(left)

        # Right panel
        right = tk.Frame(main, bg=COLORS["bg"])
        right.pack(side="right", fill="both", expand=True)
        self._build_stats_bar(right)
        self._build_table_panel(right)

    def _build_input_panel(self, parent):
        """Build left AI input panel."""
        tk.Label(parent, text="NATURAL LANGUAGE INPUT",
                 bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=16, pady=(16, 4))

        # Input area
        input_frame = tk.Frame(parent, bg=COLORS["surface2"],
                               highlightbackground=COLORS["border"], highlightthickness=1)
        input_frame.pack(fill="x", padx=16, pady=(0, 8))

        self.input_text = tk.Text(input_frame,
                                  height=4, bg=COLORS["surface2"],
                                  fg=COLORS["text"], insertbackground=COLORS["accent"],
                                  font=("Consolas", 11), relief="flat", bd=8,
                                  wrap="word",
                                  highlightthickness=0)
        self.input_text.pack(fill="x")
        self.input_text.insert("1.0", "e.g. Bought 3 kg rice and 2 liters oil today")
        self.input_text.bind("<FocusIn>", self._clear_placeholder)
        self.input_text.bind("<Return>", lambda e: self._parse_input() or "break")
        self.input_text.configure(fg=COLORS["text_dimmer"])

        # Parse button
        parse_btn = StyledButton(parent, "⚡ PARSE WITH AI",
                                 command=self._parse_input, style="primary")
        parse_btn.pack(fill="x", padx=16, pady=(0, 4))

        # Voice input (model picker + record button)
        self.speech = SpeechControls(parent, self.recognizer, COLORS,
                                     on_text=self._on_speech_text,
                                     on_error=self._on_speech_error)
        self.speech.pack(fill="x", padx=16, pady=(8, 0))

        # AI output
        tk.Label(parent, text="AI RESPONSE",
                 bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=16, pady=(10, 4))

        ai_frame = tk.Frame(parent, bg=COLORS["surface2"],
                            highlightbackground=COLORS["border"], highlightthickness=1)
        ai_frame.pack(fill="x", padx=16)

        self.ai_output = tk.Text(ai_frame,
                                 height=5, bg=COLORS["surface2"],
                                 fg=COLORS["accent2"], insertbackground=COLORS["accent"],
                                 font=("Consolas", 9), relief="flat", bd=8,
                                 wrap="word", state="disabled",
                                 highlightthickness=0)
        self.ai_output.pack(fill="x")

        # Confirm section
        self.confirm_frame = tk.Frame(parent, bg=COLORS["surface"])
        self.confirm_frame.pack(fill="x", padx=16, pady=8)

        # Divider
        tk.Frame(parent, bg=COLORS["border"], height=1).pack(
            fill="x", padx=16, pady=8)

        # Manual entry section
        tk.Label(parent, text="MANUAL ENTRY",
                 bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=16, pady=(0, 8))

        self._build_manual_form(parent)

    def _build_manual_form(self, parent):
        """Build manual entry form fields."""
        fields = [
            ("Item Name *", "item_name_var", "entry"),
            ("Quantity *", "quantity_var", "entry"),
            ("Unit", "unit_var", "entry"),
            ("Category", "category_var", "combo"),
            ("Date Bought (YYYY-MM-DD)", "date_bought_var", "entry"),
            ("Date Used (YYYY-MM-DD)", "date_used_var", "entry"),
            ("Notes", "notes_var", "entry"),
        ]

        self.form_vars = {}
        form_scroll = tk.Frame(parent, bg=COLORS["surface"])
        form_scroll.pack(fill="x", padx=16)

        for label, var_name, field_type in fields:
            tk.Label(form_scroll, text=label,
                     bg=COLORS["surface"], fg=COLORS["text_dim"],
                     font=("Consolas", 8)).pack(anchor="w")

            var = tk.StringVar()
            self.form_vars[var_name] = var

            if field_type == "combo":
                categories = ["Food", "Beverage", "Electronics", "Stationery",
                              "Cleaning", "Medicine", "Tools", "Clothing", "General"]
                widget = ttk.Combobox(form_scroll, textvariable=var,
                                      values=categories, font=("Consolas", 10))
            else:
                widget = tk.Entry(form_scroll, textvariable=var,
                                  bg=COLORS["surface2"], fg=COLORS["text"],
                                  insertbackground=COLORS["accent"],
                                  font=("Consolas", 10), relief="flat", bd=6,
                                  highlightbackground=COLORS["border"],
                                  highlightthickness=1)
            widget.pack(fill="x", pady=(0, 6))

        btn_frame = tk.Frame(parent, bg=COLORS["surface"])
        btn_frame.pack(fill="x", padx=16, pady=8)

        StyledButton(btn_frame, "＋ ADD ITEM",
                     command=self._manual_add, style="success").pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        StyledButton(btn_frame, "✎ UPDATE",
                     command=self._manual_update, style="ghost").pack(
            side="left", fill="x", expand=True, padx=(4, 0))

    def _build_stats_bar(self, parent):
        """Build top statistics bar."""
        stats_bar = tk.Frame(parent, bg=COLORS["surface"], height=90)
        stats_bar.pack(fill="x", side="top")
        stats_bar.pack_propagate(False)

        self.stat_widgets = {}
        stats_config = [
            ("total_items", "TOTAL ITEMS", "0", COLORS["accent"]),
            ("unique_items", "UNIQUE ITEMS", "0", COLORS["accent4"]),
            ("total_categories", "CATEGORIES", "0", COLORS["accent2"]),
            ("today_entries", "ADDED TODAY", "0", COLORS["warning"]),
        ]

        for key, label, default, color in stats_config:
            card = tk.Frame(stats_bar, bg=COLORS["surface2"],
                            highlightbackground=COLORS["border"], highlightthickness=1)
            card.pack(side="left", padx=(12, 0), pady=12, fill="y")
            card.pack_propagate(False)
            card.configure(width=140)

            val_label = tk.Label(card, text=default,
                                 bg=COLORS["surface2"], fg=color,
                                 font=("Consolas", 22, "bold"))
            val_label.pack(pady=(8, 0))
            tk.Label(card, text=label,
                     bg=COLORS["surface2"], fg=COLORS["text_dim"],
                     font=("Consolas", 8)).pack()
            self.stat_widgets[key] = val_label

    def _build_table_panel(self, parent):
        """Build inventory table with search and filter."""
        toolbar = tk.Frame(parent, bg=COLORS["bg"])
        toolbar.pack(fill="x", padx=12, pady=(8, 4))

        # Search
        tk.Label(toolbar, text="🔍", bg=COLORS["bg"],
                 fg=COLORS["text_dim"], font=("Consolas", 12)).pack(side="left")

        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *a: self._refresh_table())
        search_entry = tk.Entry(toolbar, textvariable=self.search_var,
                                bg=COLORS["surface2"], fg=COLORS["text"],
                                insertbackground=COLORS["accent"],
                                font=("Consolas", 10), relief="flat", bd=6,
                                highlightbackground=COLORS["border"], highlightthickness=1,
                                width=24)
        search_entry.pack(side="left", padx=6)

        # Category filter
        self.filter_var = tk.StringVar(value="All")
        self.filter_combo = ttk.Combobox(toolbar, textvariable=self.filter_var,
                                         values=["All"], font=("Consolas", 9), width=14)
        self.filter_combo.pack(side="left", padx=4)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_table())

        StyledButton(toolbar, "⟳ Refresh",
                     command=self._refresh_table, style="ghost").pack(side="left", padx=4)
        StyledButton(toolbar, "🗑 Delete",
                     command=self._delete_item, style="danger").pack(side="right", padx=4)

        # Table
        table_frame = tk.Frame(parent, bg=COLORS["surface"])
        table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        columns = ("id", "item_name", "quantity", "unit", "category",
                   "date_bought", "date_used", "notes", "updated_at")
        headers = ("ID", "Item Name", "Qty", "Unit", "Category",
                   "Date Bought", "Date Used", "Notes", "Last Updated")
        widths = (40, 160, 60, 70, 100, 100, 100, 140, 130)

        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="browse")
        for col, hdr, w in zip(columns, headers, widths):
            self.tree.heading(col, text=hdr)
            self.tree.column(col, width=w, minwidth=40)

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal",
                            command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.tag_configure("low", foreground=COLORS["warning"])
        self.tree.tag_configure("even", background=COLORS["surface"])
        self.tree.tag_configure("odd", background=COLORS["surface2"])

    # ── Event handlers ─────────────────────────────────────────────────────────
    def _clear_placeholder(self, event):
        current = self.input_text.get("1.0", "end-1c")
        if current.startswith("e.g."):
            self.input_text.delete("1.0", "end")
            self.input_text.configure(fg=COLORS["text"])

    def _on_model_change(self, event):
        model = self.model_var.get()
        self.parser.set_model(model)
        self._set_status(f"Model: {model}", COLORS["accent2"])

    def _on_tab_changed(self, _event):
        if self.notebook.select() == str(self.charts_tab):
            self.charts_tab.on_show()

    def _all_rows(self):
        """Every inventory row (used by the Charts tab)."""
        return self.db.get_all_items() if self.db else []

    # ── Speech ─────────────────────────────────────────────────────────────────
    def _on_speech_text(self, text: str, result):
        """Put the transcription in the input box (appends to existing text)."""
        current = self.input_text.get("1.0", "end-1c")
        if current.startswith("e.g.") or not current.strip():
            combined = text
        else:
            combined = current.rstrip() + " " + text
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", combined)
        self.input_text.configure(fg=COLORS["text"])
        self._voice_text = combined
        self._voice_result = result
        self._set_status(f"● Voice: {result.model_label.strip()}", COLORS["accent2"])

    def _on_speech_error(self, message: str):
        entry = self.logger.begin("[voice recording]", source="voice")
        self.logger.event(entry, "error", message)

    def _on_row_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.tree.item(sel[0])["values"]
        if not row:
            return
        self.selected_item_id = row[0]

        # Populate form fields
        mapping = {
            "item_name_var": row[1],
            "quantity_var": str(row[2]),
            "unit_var": row[3] or "",
            "category_var": row[4] or "",
            "date_bought_var": str(row[5]) if row[5] else "",
            "date_used_var": str(row[6]) if row[6] else "",
            "notes_var": row[7] or "",
        }
        for var_name, value in mapping.items():
            self.form_vars[var_name].set(value)

    def _parse_input(self):
        """Parse natural language input using Ollama."""
        text = self.input_text.get("1.0", "end-1c").strip()
        if not text or text.startswith("e.g."):
            messagebox.showwarning("Input Required", "Please enter some inventory text.")
            return

        # ── activity log: start an entry for this prompt ──────────────────────
        if self._pending_entry:
            self.logger.event(self._pending_entry, "discarded",
                              "Replaced by a new input before saving")
            self._pending_entry = None
        from_voice = bool(self._voice_text) and text == self._voice_text
        voice_result = self._voice_result if from_voice else None
        entry = self.logger.begin(text, source="voice" if from_voice else "typed")
        if voice_result:
            self.logger.event(
                entry, "speech",
                f"Transcribed with {voice_result.model_label.strip()} "
                f"({voice_result.audio_seconds:.1f}s audio, "
                f"{voice_result.elapsed_seconds:.1f}s, "
                f"language={voice_result.language or 'auto'})")
        self.logger.event(entry, "model", f"Ollama model: {self.parser.model}")
        self._voice_text = None
        self._voice_result = None

        self._set_ai_output("⚡ Thinking...", COLORS["warning"])
        self._set_status("● Parsing...", COLORS["warning"])

        def _run():
            try:
                accumulated = []

                def on_chunk(token):
                    accumulated.append(token)
                    self.root.after(0, self._set_ai_output,
                                    "".join(accumulated), COLORS["accent"])

                started = time.time()
                items = self.parser.parse_input(text, on_chunk=on_chunk)
                self.logger.event(entry, "llm_response", "".join(accumulated),
                                  seconds=round(time.time() - started, 2))
                for item in items:
                    item["raw_input"] = text
                self.root.after(0, self._show_parsed_items, items, text, entry)
            except ConnectionError as e:
                self.logger.event(entry, "error", f"Ollama connection error: {e}")
                self.root.after(0, self._set_ai_output, str(e), COLORS["error"])
                self.root.after(0, self._set_status, "● Ollama offline", COLORS["error"])
            except Exception as e:
                self.logger.event(entry, "error", f"Parse error: {e}")
                self.root.after(0, self._set_ai_output,
                                f"Parse error: {e}", COLORS["error"])
                self.root.after(0, self._set_status, "● Error", COLORS["error"])

        threading.Thread(target=_run, daemon=True).start()

    def _show_parsed_items(self, items: list, raw_input: str, entry=None):
        """Show parsed items with confirm button."""
        # Clear confirm frame
        for w in self.confirm_frame.winfo_children():
            w.destroy()

        if not items:
            self.logger.event(entry, "empty", "The model returned no items")
            self._set_ai_output("No items found. Try rephrasing.", COLORS["warning"])
            return

        summary = []
        for item in items:
            qty = item.get("quantity", "?")
            unit = item.get("unit") or ""
            name = item.get("item_name", "?")
            cat = item.get("category", "")
            summary.append(f"  ✓ {qty} {unit} {name} [{cat}]")

        self.logger.event(
            entry, "parsed",
            f"{len(items)} item(s) detected:\n" + "\n".join(s.strip() for s in summary),
            items=items)
        self._pending_entry = entry

        self._set_ai_output("\n".join(summary), COLORS["accent2"])
        self._set_status(f"✓ Found {len(items)} item(s)", COLORS["accent2"])

        tk.Label(self.confirm_frame,
                 text=f"Save {len(items)} item(s) to database?",
                 bg=COLORS["surface"], fg=COLORS["text"],
                 font=("Consolas", 9)).pack(anchor="w")

        btn_row = tk.Frame(self.confirm_frame, bg=COLORS["surface"])
        btn_row.pack(fill="x", pady=4)

        StyledButton(btn_row, "✓ SAVE ALL",
                     command=lambda: self._save_parsed_items(items),
                     style="success").pack(side="left", padx=(0, 4))
        StyledButton(btn_row, "✗ Discard",
                     command=self._discard_parsed,
                     style="ghost").pack(side="left")

    def _discard_parsed(self):
        for w in self.confirm_frame.winfo_children():
            w.destroy()
        self.logger.event(self._pending_entry, "discarded",
                          "User discarded the parsed items")
        self._pending_entry = None

    def _save_parsed_items(self, items: list):
        """Save parsed items to database."""
        entry = self._pending_entry
        if not self.db:
            self.logger.event(entry, "error", "Database not connected")
            messagebox.showerror("DB Error", "Database not connected.")
            return
        try:
            ids = [self.db.insert_item(item) for item in items]
            count = len(items)
            self.logger.event(
                entry, "saved",
                f"Saved {count} item(s) to the database (ids: "
                f"{', '.join(str(i) for i in ids)})", ids=ids)
            self._pending_entry = None
            self._set_status(f"✓ Saved {count} item(s)", COLORS["accent2"])
            for w in self.confirm_frame.winfo_children():
                w.destroy()
            self._refresh_table()
            self._refresh_stats()
        except Exception as e:
            self.logger.event(entry, "error", f"Database error: {e}")
            messagebox.showerror("DB Error", str(e))

    def _manual_add(self):
        """Add item from manual form."""
        if not self.db:
            messagebox.showerror("DB Error", "Database not connected.")
            return

        name = self.form_vars["item_name_var"].get().strip()
        qty_str = self.form_vars["quantity_var"].get().strip()

        if not name:
            messagebox.showwarning("Validation", "Item Name is required.")
            return
        try:
            qty = float(qty_str)
        except ValueError:
            messagebox.showwarning("Validation", "Quantity must be a number.")
            return

        item = {
            "item_name": name,
            "quantity": qty,
            "unit": self.form_vars["unit_var"].get().strip() or None,
            "category": self.form_vars["category_var"].get().strip() or "General",
            "date_bought": self.form_vars["date_bought_var"].get().strip() or None,
            "date_used": self.form_vars["date_used_var"].get().strip() or None,
            "notes": self.form_vars["notes_var"].get().strip() or None,
            "raw_input": None,
        }
        entry = self.logger.begin(
            f"[manual add] {qty:g} {item['unit'] or ''} {name}".replace("  ", " "),
            source="manual")
        try:
            item_id = self.db.insert_item(item)
            self.logger.event(entry, "saved", f"Added item #{item_id}: {name}", item=item)
            self._refresh_table()
            self._refresh_stats()
            self._set_status(f"✓ Added: {name}", COLORS["accent2"])
        except Exception as e:
            self.logger.event(entry, "error", f"Database error: {e}")
            messagebox.showerror("DB Error", str(e))

    def _manual_update(self):
        """Update selected item from form values."""
        if not self.selected_item_id:
            messagebox.showinfo("Select Item", "Please select a row to update.")
            return
        if not self.db:
            return

        qty_str = self.form_vars["quantity_var"].get().strip()
        try:
            qty = float(qty_str)
        except ValueError:
            messagebox.showwarning("Validation", "Quantity must be a number.")
            return

        updates = {
            "item_name": self.form_vars["item_name_var"].get().strip(),
            "quantity": qty,
            "unit": self.form_vars["unit_var"].get().strip() or None,
            "category": self.form_vars["category_var"].get().strip() or "General",
            "date_bought": self.form_vars["date_bought_var"].get().strip() or None,
            "date_used": self.form_vars["date_used_var"].get().strip() or None,
            "notes": self.form_vars["notes_var"].get().strip() or None,
        }
        entry = self.logger.begin(
            f"[manual update] #{self.selected_item_id} {updates['item_name']} "
            f"-> {qty:g} {updates['unit'] or ''}".strip(), source="manual")
        try:
            self.db.update_item(self.selected_item_id, updates)
            self.logger.event(entry, "updated",
                              f"Updated item #{self.selected_item_id}",
                              updates={k: v for k, v in updates.items()})
            self._refresh_table()
            self._refresh_stats()
            self._set_status("✓ Item updated", COLORS["accent2"])
        except Exception as e:
            self.logger.event(entry, "error", f"Database error: {e}")
            messagebox.showerror("DB Error", str(e))

    def _delete_item(self):
        """Delete the selected item."""
        if not self.selected_item_id:
            messagebox.showinfo("Select Item", "Please select a row to delete.")
            return
        if not self.db:
            return

        if not messagebox.askyesno("Confirm Delete",
                                   "Are you sure you want to delete this item?"):
            return

        name = self.form_vars["item_name_var"].get().strip()
        entry = self.logger.begin(
            f"[manual delete] #{self.selected_item_id} {name}".strip(), source="manual")
        try:
            self.db.delete_item(self.selected_item_id)
            self.logger.event(entry, "deleted", f"Deleted item #{self.selected_item_id}")
            self.selected_item_id = None
            self._refresh_table()
            self._refresh_stats()
            self._set_status("✓ Item deleted", COLORS["accent3"])
        except Exception as e:
            self.logger.event(entry, "error", f"Database error: {e}")
            messagebox.showerror("DB Error", str(e))

    # ── Data refresh ───────────────────────────────────────────────────────────
    def _refresh_table(self):
        """Reload inventory table data."""
        if not self.db:
            return

        search = self.search_var.get().strip()
        category = self.filter_var.get()

        try:
            items = self.db.get_all_items(search=search, category=category)
            low_stock = {
                i["id"] for i in self.db.get_low_stock(
                    APP_CONFIG["low_stock_threshold"])
            }

            self.tree.delete(*self.tree.get_children())
            for idx, item in enumerate(items):
                tags = []
                if item["id"] in low_stock:
                    tags.append("low")
                else:
                    tags.append("even" if idx % 2 == 0 else "odd")

                updated = item.get("updated_at", "")
                if updated:
                    updated = str(updated)[:16]

                self.tree.insert("", "end", values=(
                    item["id"],
                    item["item_name"],
                    item["quantity"],
                    item.get("unit") or "",
                    item.get("category") or "",
                    item.get("date_bought") or "",
                    item.get("date_used") or "",
                    item.get("notes") or "",
                    updated,
                ), tags=tags)

            # Update category filter
            cats = ["All"] + self.db.get_categories()
            self.filter_combo["values"] = cats

        except Exception as e:
            self._set_status(f"Table error: {e}", COLORS["error"])

    def _refresh_stats(self):
        """Refresh statistics cards (and flag the charts as out of date)."""
        if not self.db:
            return
        try:
            stats = self.db.get_stats()
            for key, widget in self.stat_widgets.items():
                val = stats.get(key, 0) or 0
                widget.configure(text=str(int(val)))
        except Exception:
            pass
        self.charts_tab.mark_dirty()

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _set_status(self, text: str, color: str):
        self.status_label.configure(text=text, fg=color)

    def _set_ai_output(self, text: str, color: str = COLORS["accent2"]):
        self.ai_output.configure(state="normal")
        self.ai_output.delete("1.0", "end")
        self.ai_output.insert("1.0", text)
        self.ai_output.configure(state="disabled", fg=color)

    def _connect_services(self):
        """Connect to DB and Ollama in background."""
        def _run():
            # Database
            try:
                self.db = DatabaseManager()
                self.root.after(0, self._refresh_table)
                self.root.after(0, self._refresh_stats)
                db_ok = True
            except Exception as e:
                db_ok = False
                self.root.after(0, messagebox.showerror,
                                "Database Error",
                                f"Could not connect to PostgreSQL:\n{e}\n\n"
                                "Check config.py for DB settings.")

            # Ollama
            ollama_ok = self.parser.is_available()
            models = self.parser.get_available_models()

            def _update_ui():
                if models:
                    self.model_combo["values"] = models
                    if "tinyllama" in models:
                        self.model_var.set("tinyllama")
                    else:
                        self.model_var.set(models[0])
                    self.parser.set_model(self.model_var.get())

                if db_ok and ollama_ok:
                    self._set_status("● Ready", COLORS["accent2"])
                elif db_ok:
                    self._set_status("● DB ok | Ollama offline", COLORS["warning"])
                elif ollama_ok:
                    self._set_status("● Ollama ok | DB offline", COLORS["warning"])
                else:
                    self._set_status("● Services offline", COLORS["error"])

            self.root.after(0, _update_ui)

        threading.Thread(target=_run, daemon=True).start()

    def run(self):
        """Start the application."""
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        if self.db:
            self.db.close()
        self.root.destroy()
