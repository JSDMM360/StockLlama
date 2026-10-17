"""
Tkinter widgets added to StockLlama:

  ChartsTab      - pick ingredients + chart type, one combined chart or one
                   chart per ingredient, rendered inside the window.
  LogsTab        - browse the activity log (date, input prompt, events).
  SpeechControls - model picker (FUTO ACFT / Whisper / local) + record button.

All three take the app's COLORS dict so they match the existing theme.
"""
from __future__ import annotations

import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from activity_log import ActivityLogger
from charts import CHART_TYPES, build_figures, build_ingredients
import stt_engine

FONT = "Consolas"


def _button(parent, text, command, c, kind="ghost", **kw):
    palette = {
        "primary": (c["accent"], "#0D1117", "#79C0FF"),
        "success": (c["accent2"], "#0D1117", "#56D364"),
        "danger": (c["accent3"], "#0D1117", "#FF8B7A"),
        "ghost": (c["surface2"], c["text"], c["surface3"]),
    }[kind]
    return tk.Button(parent, text=text, command=command, bg=palette[0],
                     fg=palette[1], activebackground=palette[2],
                     activeforeground=palette[1], relief="flat", bd=0,
                     cursor="hand2", font=(FONT, 9, "bold"), padx=10, pady=6, **kw)


# ══════════════════════════════════════════════════════════════════════════════
# Charts
# ══════════════════════════════════════════════════════════════════════════════
class ChartsTab(tk.Frame):
    MAX_SEPARATE = 12      # cap on per-ingredient charts drawn at once
    MIN_CHART_WIDTH = 620

    def __init__(self, parent, get_rows, colors):
        super().__init__(parent, bg=colors["bg"])
        self.c = colors
        self.get_rows = get_rows
        self.ingredients = {}
        self._keys: list[str] = []
        self._figures = []
        self._draw_job = None
        self._dirty = True
        self._first_load = True
        self._cols = 1

        self._build_controls()
        self._build_canvas()

    # ── layout ────────────────────────────────────────────────────────────
    def _build_controls(self):
        c = self.c
        side = tk.Frame(self, bg=c["surface"], width=300)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Label(side, text="INGREDIENTS", bg=c["surface"], fg=c["text_dim"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=16, pady=(18, 4))

        lb_frame = tk.Frame(side, bg=c["surface2"], highlightthickness=1,
                            highlightbackground=c["border"])
        lb_frame.pack(fill="both", expand=True, padx=16)
        self.listbox = tk.Listbox(
            lb_frame, selectmode="extended", exportselection=False,
            bg=c["surface2"], fg=c["text"], selectbackground=c["accent"],
            selectforeground="#0D1117", font=(FONT, 10), relief="flat", bd=0,
            highlightthickness=0, activestyle="none")
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._schedule_draw())

        row = tk.Frame(side, bg=c["surface"])
        row.pack(fill="x", padx=16, pady=(6, 0))
        _button(row, "Select all", self._select_all, c).pack(
            side="left", fill="x", expand=True, padx=(0, 3))
        _button(row, "Clear", self._clear_selection, c).pack(
            side="left", fill="x", expand=True, padx=(3, 0))

        tk.Label(side, text="CHART TYPE", bg=c["surface"], fg=c["text_dim"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        self.type_var = tk.StringVar(value=list(CHART_TYPES)[0])
        combo = ttk.Combobox(side, textvariable=self.type_var, state="readonly",
                             values=list(CHART_TYPES), font=(FONT, 9))
        combo.pack(fill="x", padx=16)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._schedule_draw(0))

        self.combined_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            side, text="All ingredients in one chart", variable=self.combined_var,
            command=lambda: self._schedule_draw(0), bg=c["surface"], fg=c["text"],
            selectcolor=c["surface2"], activebackground=c["surface"],
            activeforeground=c["text"], font=(FONT, 9), anchor="w",
            highlightthickness=0, bd=0).pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(side, text="Off = a separate chart for each ingredient",
                 bg=c["surface"], fg=c["text_dimmer"], font=(FONT, 8),
                 anchor="w").pack(fill="x", padx=36)

        _button(side, "📊 DRAW CHARTS", lambda: self.draw(), c, "primary").pack(
            fill="x", padx=16, pady=(14, 4))
        row2 = tk.Frame(side, bg=c["surface"])
        row2.pack(fill="x", padx=16, pady=(0, 6))
        _button(row2, "⟳ Reload data", self.refresh, c).pack(
            side="left", fill="x", expand=True, padx=(0, 3))
        _button(row2, "💾 Export PNG", self.export, c).pack(
            side="left", fill="x", expand=True, padx=(3, 0))

        self.status = tk.Label(side, text="", bg=c["surface"], fg=c["text_dim"],
                               font=(FONT, 8), wraplength=268, justify="left",
                               anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 12))

    def _build_canvas(self):
        c = self.c
        area = tk.Frame(self, bg=c["bg"])
        area.pack(side="right", fill="both", expand=True)
        self.scroll = tk.Canvas(area, bg=c["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(area, orient="vertical", command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.scroll.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.scroll, bg=c["bg"])
        self._win = self.scroll.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.scroll.configure(
            scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>", self._on_canvas_resize)
        # mouse-wheel only while the pointer is over the chart area
        area.bind("<Enter>", self._bind_wheel)
        area.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e):
        self.bind_all("<MouseWheel>", self._wheel)
        self.bind_all("<Button-4>", lambda e: self.scroll.yview_scroll(-2, "units"))
        self.bind_all("<Button-5>", lambda e: self.scroll.yview_scroll(2, "units"))

    def _unbind_wheel(self, _e):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.unbind_all(seq)

    def _wheel(self, event):
        step = -1 if event.delta > 0 else 1
        self.scroll.yview_scroll(step * 2, "units")

    def _on_canvas_resize(self, event):
        self.scroll.itemconfigure(self._win, width=event.width)
        cols = max(1, event.width // self.MIN_CHART_WIDTH)
        if cols != self._cols:
            self._cols = cols
            if self._figures and not self.combined_var.get():
                self._schedule_draw(100)

    # ── data ──────────────────────────────────────────────────────────────
    def mark_dirty(self):
        """Call after the inventory changes."""
        self._dirty = True
        if self.winfo_ismapped():
            self.refresh()

    def on_show(self):
        """Call when the tab becomes visible."""
        if self._dirty:
            self.refresh()

    def refresh(self):
        try:
            rows = self.get_rows()
        except Exception as exc:
            self.status.configure(text=f"Could not load data: {exc}",
                                  fg=self.c["error"])
            return
        self._dirty = False
        previous = {self._keys[i] for i in self.listbox.curselection()}
        self.ingredients = build_ingredients(rows)
        self._keys = list(self.ingredients)
        self.listbox.delete(0, "end")
        for key in self._keys:
            ing = self.ingredients[key]
            self.listbox.insert("end", f"{ing.name}  [{ing.unit or '-'}]")
        if self._first_load and self._keys:
            busiest = sorted(self._keys, key=lambda k: -len(self.ingredients[k].movements))
            previous = set(busiest[:5])
            self._first_load = False
        for i, key in enumerate(self._keys):
            if key in previous:
                self.listbox.selection_set(i)
        self.draw()

    def _select_all(self):
        self.listbox.selection_set(0, "end")
        self._schedule_draw()

    def _clear_selection(self):
        self.listbox.selection_clear(0, "end")
        self._schedule_draw()

    # ── drawing ───────────────────────────────────────────────────────────
    def _schedule_draw(self, delay=250):
        if self._draw_job:
            self.after_cancel(self._draw_job)
        self._draw_job = self.after(delay, self.draw)

    def draw(self):
        self._draw_job = None
        for w in self.inner.winfo_children():
            w.destroy()
        self._figures = []

        selected = [self.ingredients[self._keys[i]]
                    for i in self.listbox.curselection()]
        if not selected:
            msg = ("No inventory data yet - add items first."
                   if not self._keys else "Select one or more ingredients.")
            tk.Label(self.inner, text=msg, bg=self.c["bg"], fg=self.c["text_dim"],
                     font=(FONT, 11)).pack(pady=60, padx=40)
            self.status.configure(text="", fg=self.c["text_dim"])
            return

        combined = self.combined_var.get()
        chart_type = CHART_TYPES[self.type_var.get()]
        note = ""
        if not combined and len(selected) > self.MAX_SEPARATE:
            note = (f"Showing the first {self.MAX_SEPARATE} of {len(selected)} "
                    f"selected ingredients - narrow the selection to see others.")
            selected = selected[:self.MAX_SEPARATE]

        figures = build_figures(selected, chart_type, combined)
        cols = 1 if combined else self._cols
        for col in range(cols):
            self.inner.columnconfigure(col, weight=1, uniform="charts")
        for n, fig in enumerate(figures):
            if combined:
                fig.set_size_inches(9, 5)
            canvas = FigureCanvasTkAgg(fig, master=self.inner)
            canvas.draw()
            canvas.get_tk_widget().grid(row=n // cols, column=n % cols,
                                        sticky="ew", padx=8, pady=8)
        self._figures = figures
        what = "1 combined chart" if combined else f"{len(figures)} chart(s)"
        self.status.configure(text=(f"{what}. " + note).strip(), fg=self.c["text_dim"])

    # ── export ────────────────────────────────────────────────────────────
    def export(self):
        if not self._figures:
            messagebox.showinfo("Export", "Nothing to export yet.")
            return
        if len(self._figures) == 1:
            path = filedialog.asksaveasfilename(
                defaultextension=".png", filetypes=[("PNG image", "*.png")],
                initialfile="stock_chart.png")
            if path:
                self._save(self._figures[0], path)
            return
        folder = filedialog.askdirectory(title="Choose a folder for the charts")
        if not folder:
            return
        for fig in self._figures:
            name = re.sub(r"[^\w\-]+", "_", fig.axes[0].get_title() or "chart")
            self._save(fig, f"{folder}/{name}.png")
        messagebox.showinfo("Export", f"Saved {len(self._figures)} charts to\n{folder}")

    @staticmethod
    def _save(fig, path):
        fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())


# ══════════════════════════════════════════════════════════════════════════════
# Logs
# ══════════════════════════════════════════════════════════════════════════════
class LogsTab(tk.Frame):
    STATUS_COLOR = {"error": "error", "saved": "success", "updated": "success",
                    "deleted": "accent3", "awaiting save": "warning",
                    "discarded": "text_dimmer", "no items": "warning",
                    "started": "text_dim"}
    KIND_COLOR = {"error": "error", "saved": "success", "updated": "success",
                  "parsed": "accent", "speech": "accent4", "model": "text_dim",
                  "llm_response": "text_dim", "discarded": "warning",
                  "deleted": "accent3", "warning": "warning", "empty": "warning"}

    def __init__(self, parent, logger: ActivityLogger, colors):
        super().__init__(parent, bg=colors["bg"])
        self.c = colors
        self.logger = logger
        self._entries: dict[str, dict] = {}
        self._dirty = threading.Event()     # set from any thread, read on Tk thread
        logger.add_listener(lambda _eid: self._dirty.set())
        self._build()
        self.refresh()
        self._poll()

    def _build(self):
        c = self.c
        bar = tk.Frame(self, bg=c["bg"])
        bar.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(bar, text="🔍", bg=c["bg"], fg=c["text_dim"],
                 font=(FONT, 12)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        tk.Entry(bar, textvariable=self.search_var, bg=c["surface2"], fg=c["text"],
                 insertbackground=c["accent"], font=(FONT, 10), relief="flat", bd=6,
                 highlightbackground=c["border"], highlightthickness=1,
                 width=28).pack(side="left", padx=6)
        self.source_var = tk.StringVar(value="All")
        src = ttk.Combobox(bar, textvariable=self.source_var, state="readonly",
                           values=["All", "typed", "voice", "manual"], width=9,
                           font=(FONT, 9))
        src.pack(side="left", padx=4)
        src.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        _button(bar, "⟳ Refresh", self.refresh, c).pack(side="left", padx=4)
        _button(bar, "⇩ Export CSV", self._export, c).pack(side="left", padx=4)
        self.count_label = tk.Label(bar, text="", bg=c["bg"], fg=c["text_dim"],
                                    font=(FONT, 9))
        self.count_label.pack(side="right", padx=8)

        pane = tk.PanedWindow(self, orient="vertical", bg=c["bg"], sashwidth=6,
                              sashrelief="flat", bd=0)
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        top = tk.Frame(pane, bg=c["surface"])
        cols = ("date", "source", "status", "prompt", "events")
        self.tree = ttk.Treeview(top, columns=cols, show="headings",
                                 selectmode="browse")
        for col, text, width, anchor in (
                ("date", "Date", 140, "w"), ("source", "Source", 70, "w"),
                ("status", "Result", 110, "w"), ("prompt", "Input prompt", 420, "w"),
                ("events", "Events", 60, "center")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, minwidth=50,
                             stretch=(col == "prompt"))
        vsb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        for status, colour in self.STATUS_COLOR.items():
            self.tree.tag_configure(status, foreground=c[colour])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        pane.add(top, minsize=120)

        bottom = tk.Frame(pane, bg=c["surface"])
        self.detail = tk.Text(bottom, bg=c["surface"], fg=c["text"], wrap="word",
                              font=(FONT, 9), relief="flat", bd=10, height=10,
                              state="disabled", highlightthickness=0)
        dsb = ttk.Scrollbar(bottom, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=dsb.set)
        dsb.pack(side="right", fill="y")
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("dim", foreground=c["text_dim"])
        self.detail.tag_configure("head", foreground=c["accent"],
                                  font=(FONT, 9, "bold"))
        for kind, colour in self.KIND_COLOR.items():
            self.detail.tag_configure(f"k_{kind}", foreground=c[colour],
                                      font=(FONT, 9, "bold"))
        pane.add(bottom, minsize=120)

    # ── data ──────────────────────────────────────────────────────────────
    def _poll(self):
        if self._dirty.is_set():
            self._dirty.clear()
            self.refresh()
        self.after(400, self._poll)

    def _filtered(self):
        needle = self.search_var.get().strip().casefold()
        source = self.source_var.get()
        out = []
        for e in self.logger.read_entries():
            if source != "All" and e["source"] != source:
                continue
            if needle:
                hay = (e["prompt"] + " " + " ".join(
                    ev["kind"] + " " + ev["message"] for ev in e["events"])).casefold()
                if needle not in hay:
                    continue
            out.append(e)
        return out

    def refresh(self):
        selected = self.tree.selection()
        entries = self._filtered()
        self._entries = {e["id"]: e for e in entries}
        self.tree.delete(*self.tree.get_children())
        for e in entries:
            self.tree.insert("", "end", iid=e["id"], tags=(e["status"],), values=(
                e["timestamp"].replace("T", " "), e["source"], e["status"],
                e["prompt"].replace("\n", " "), len(e["events"])))
        self.count_label.configure(text=f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
        if selected and selected[0] in self._entries:
            self.tree.selection_set(selected[0])
            self._show(self._entries[selected[0]])
        elif entries:
            self.tree.selection_set(entries[0]["id"])      # newest
        else:
            self._show(None)

    def _on_select(self, _event):
        sel = self.tree.selection()
        self._show(self._entries.get(sel[0]) if sel else None)

    def _show(self, entry):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if entry:
            d.insert("end", "Date    ", "dim")
            d.insert("end", entry["timestamp"].replace("T", " ") + "\n")
            d.insert("end", "Source  ", "dim")
            d.insert("end", entry["source"] + "\n")
            d.insert("end", "Result  ", "dim")
            d.insert("end", entry["status"] + "\n\n")
            d.insert("end", "INPUT PROMPT\n", "head")
            d.insert("end", entry["prompt"] + "\n\n")
            d.insert("end", "EVENTS\n", "head")
            if not entry["events"]:
                d.insert("end", "(none yet)\n", "dim")
            for ev in entry["events"]:
                stamp = ev["ts"][11:] if len(ev["ts"]) >= 19 else ev["ts"]
                d.insert("end", f"{stamp}  ", "dim")
                d.insert("end", f"{ev['kind']:<13}", f"k_{ev['kind']}")
                d.insert("end", ev["message"] + "\n")
        d.configure(state="disabled")

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV file", "*.csv")],
            initialfile="stockllama_activity.csv")
        if path:
            self.logger.export_csv(path, list(self._entries.values()))
            messagebox.showinfo("Export", f"Exported {len(self._entries)} entries.")


# ══════════════════════════════════════════════════════════════════════════════
# Speech
# ══════════════════════════════════════════════════════════════════════════════
class SpeechControls(tk.Frame):
    LANGUAGES = ["auto", "en", "hi", "kn", "ta", "te", "ml", "mr", "bn"]

    def __init__(self, parent, recognizer: stt_engine.SpeechRecognizer, colors,
                 on_text, on_error=None):
        """on_text(text, transcription) is called on the Tk thread."""
        super().__init__(parent, bg=colors["surface"])
        self.c = colors
        self.recognizer = recognizer
        self.on_text = on_text
        self.on_error = on_error
        self.recorder = stt_engine.MicRecorder()
        self._load_thread = None
        self._load_error = None
        self._models = stt_engine.available_models()
        self._build()

    def _build(self):
        c = self.c
        tk.Label(self, text="VOICE INPUT", bg=c["surface"], fg=c["text_dim"],
                 font=(FONT, 8, "bold")).pack(anchor="w", pady=(0, 4))
        row1 = tk.Frame(self, bg=c["surface"])
        row1.pack(fill="x")
        self.model_var = tk.StringVar(value=self._default_label())
        self.model_combo = ttk.Combobox(row1, textvariable=self.model_var,
                                        state="readonly", font=(FONT, 9),
                                        values=[m.label for m in self._models])
        self.model_combo.pack(side="left", fill="x", expand=True)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)
        more = _button(row1, "…", self._add_menu, c)
        more.configure(padx=8, pady=2)
        more.pack(side="left", padx=(4, 0))
        self._menu_btn = more

        row2 = tk.Frame(self, bg=c["surface"])
        row2.pack(fill="x", pady=(6, 0))
        self.lang_var = tk.StringVar(value="auto")
        ttk.Combobox(row2, textvariable=self.lang_var, state="readonly", width=5,
                     values=self.LANGUAGES, font=(FONT, 9)).pack(side="left")
        self.rec_btn = _button(row2, "🎙 RECORD", self._toggle, c, "ghost")
        self.rec_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.status = tk.Label(self, text="", bg=c["surface"], fg=c["text_dim"],
                               font=(FONT, 8), anchor="w", justify="left",
                               wraplength=290)
        self.status.pack(fill="x", pady=(4, 0))

    # ── model list ────────────────────────────────────────────────────────
    def _default_label(self):
        for key in ("futo-base.en", "whisper-base.en"):
            m = next(m for m in self._models if m.key == key)
            if stt_engine.backend_installed(m.backend):
                return m.label
        return next(m for m in self._models if m.key == "futo-base.en").label

    def _current_model(self):
        label = self.model_var.get()
        return next(m for m in self._models if m.label == label)

    def _on_model_selected(self, _event=None):
        m = self._current_model()
        if not stt_engine.backend_installed(m.backend):
            self._status(f"Needs:  {stt_engine.install_hint(m.backend)}", "warning")
        else:
            size = f" (~{m.approx_mb} MB)" if m.approx_mb else ""
            self._status(f"Selected{size}. Loads on first recording; "
                         f"hub models download once.", "text_dim")

    def _add_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Add whisper.cpp .bin file…", command=self._add_file)
        menu.add_command(label="Add model folder (HF / CTranslate2)…",
                         command=self._add_folder)
        menu.tk_popup(self._menu_btn.winfo_rootx(),
                      self._menu_btn.winfo_rooty() + self._menu_btn.winfo_height())

    def _add_file(self):
        path = filedialog.askopenfilename(
            title="Choose a whisper.cpp model",
            filetypes=[("whisper.cpp model", "*.bin"), ("All files", "*.*")])
        self._register(path)

    def _add_folder(self):
        self._register(filedialog.askdirectory(title="Choose a Whisper model folder"))

    def _register(self, path):
        if not path:
            return
        model = stt_engine.model_from_path(path)
        if model is None:
            messagebox.showerror("Speech model", "That doesn't look like a Whisper model "
                                 "(.bin file, or a folder with config.json / model.bin).")
            return
        self._models = [m for m in self._models if m.key != model.key] + [model]
        self.model_combo.configure(values=[m.label for m in self._models])
        self.model_var.set(model.label)
        self._on_model_selected()

    # ── recording ─────────────────────────────────────────────────────────
    def _toggle(self):
        if self.recorder.recording:
            self._stop()
        else:
            self._start()

    def _start(self):
        model = self._current_model()
        if not stt_engine.backend_installed(model.backend):
            self._fail(f"{model.label} needs an extra package. Run:  "
                       f"{stt_engine.install_hint(model.backend)}")
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self._fail(str(exc))
            return
        self.rec_btn.configure(text="■ STOP", bg=self.c["accent3"],
                               fg="#0D1117", activebackground="#FF8B7A")
        self._status("Listening… press STOP when done.", "accent3")
        # load the model while the user is still speaking
        self._load_error = None
        if self.recognizer.loaded_key != model.key:
            def work():
                try:
                    self.recognizer.load(model)
                except Exception as exc:
                    self._load_error = exc
            self._load_thread = threading.Thread(target=work, daemon=True)
            self._load_thread.start()
        else:
            self._load_thread = None

    def _stop(self):
        audio = self.recorder.stop()
        self.rec_btn.configure(text="🎙 RECORD", bg=self.c["surface2"],
                               fg=self.c["text"], activebackground=self.c["surface3"])
        if len(audio) < stt_engine.SAMPLE_RATE * 0.3:
            self._status("Too short - nothing recorded.", "warning")
            return
        self.rec_btn.configure(state="disabled")
        self._status("Transcribing…", "warning")
        language = self.lang_var.get()
        model = self._current_model()

        def work():
            try:
                if self._load_thread:
                    self._load_thread.join()
                if self._load_error:
                    raise self._load_error
                if self.recognizer.loaded_key != model.key:
                    self.recognizer.load(model)
                result = self.recognizer.transcribe(audio, language)
                self.after(0, self._done, result)
            except Exception as exc:
                self.after(0, self._fail, f"Speech error: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _done(self, result):
        self.rec_btn.configure(state="normal")
        if not result.text:
            self._status("No speech detected.", "warning")
            return
        self._status(f"Done in {result.elapsed_seconds:.1f}s "
                     f"({result.audio_seconds:.1f}s audio).", "success")
        self.on_text(result.text, result)

    def _fail(self, message):
        self.rec_btn.configure(state="normal")
        self._status(message, "error")
        if self.on_error:
            self.on_error(message)

    def _status(self, text, colour_key):
        self.status.configure(text=text, fg=self.c.get(colour_key, self.c["text_dim"]))
