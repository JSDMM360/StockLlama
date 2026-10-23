"""
🔮 Predictions tab for StockLlama.

Shows, for every ingredient: when it will probably be used next, how likely
that is within a chosen window, when the stock runs out and whether to
reorder.  Select a row for the explanation and a forecast chart.
"""
from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import predictions as pred
from charts import build_ingredients

FONT = "Consolas"


class PredictionsTab(tk.Frame):
    HORIZONS = {"Next 3 days": 3, "Next 7 days": 7, "Next 14 days": 14,
                "Next 30 days": 30}
    SORTS = ["Next use", "Runs out first", "Most likely"]
    ADVICE_COLOR = {"Reorder now": "error", "Out of stock": "error",
                    "Reorder soon": "warning", "OK": "success",
                    "Possibly inactive": "text_dim", "Not enough data": "text_dimmer"}

    def __init__(self, parent, get_rows, colors):
        super().__init__(parent, bg=colors["bg"])
        self.c = colors
        self.get_rows = get_rows
        self._forecasts: dict[str, pred.Forecast] = {}
        self._dirty = True
        self._canvas = None
        self._today = date.today()
        self._build()

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        c = self.c
        bar = tk.Frame(self, bg=c["bg"])
        bar.pack(fill="x", padx=12, pady=(10, 2))

        def label(text):
            tk.Label(bar, text=text, bg=c["bg"], fg=c["text_dim"],
                     font=(FONT, 9)).pack(side="left", padx=(10, 4))

        label("Chance window")
        self.horizon_var = tk.StringVar(value="Next 7 days")
        h = ttk.Combobox(bar, textvariable=self.horizon_var, state="readonly",
                         values=list(self.HORIZONS), width=13, font=(FONT, 9))
        h.pack(side="left")
        h.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        label("Reorder lead (days)")
        self.lead_var = tk.StringVar(value="5")
        spin = tk.Spinbox(bar, from_=1, to=60, width=4, textvariable=self.lead_var,
                          command=self.refresh, bg=c["surface2"], fg=c["text"],
                          buttonbackground=c["surface3"], insertbackground=c["accent"],
                          relief="flat", font=(FONT, 10), highlightthickness=1,
                          highlightbackground=c["border"])
        spin.pack(side="left")
        spin.bind("<Return>", lambda _e: self.refresh())
        spin.bind("<FocusOut>", lambda _e: self.refresh())

        label("Sort by")
        self.sort_var = tk.StringVar(value=self.SORTS[0])
        s = ttk.Combobox(bar, textvariable=self.sort_var, state="readonly",
                         values=self.SORTS, width=14, font=(FONT, 9))
        s.pack(side="left")
        s.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        tk.Button(bar, text="⟳ Refresh", command=self.refresh, bg=c["surface2"],
                  fg=c["text"], activebackground=c["surface3"],
                  activeforeground=c["text"], relief="flat", bd=0, cursor="hand2",
                  font=(FONT, 9, "bold"), padx=10, pady=5).pack(side="left", padx=12)

        self.summary = tk.Label(self, text="", bg=c["bg"], fg=c["accent"],
                                font=(FONT, 10, "bold"), anchor="w", justify="left",
                                wraplength=1100)
        self.summary.pack(fill="x", padx=14, pady=(6, 0))
        self.backtest_label = tk.Label(self, text="", bg=c["bg"], fg=c["text_dim"],
                                       font=(FONT, 8), anchor="w")
        self.backtest_label.pack(fill="x", padx=14, pady=(0, 4))

        pane = tk.PanedWindow(self, orient="vertical", bg=c["bg"], sashwidth=6,
                              sashrelief="flat", bd=0)
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # table
        top = tk.Frame(pane, bg=c["surface"])
        cols = ("ingredient", "stock", "rate", "next", "chance", "runout", "left",
                "conf", "advice")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        spec = (("ingredient", "Ingredient", 150, "w"), ("stock", "In stock", 90, "e"),
                ("rate", "Use / day", 85, "e"), ("next", "Next use", 170, "w"),
                ("chance", "Chance", 70, "center"), ("runout", "Runs out", 110, "w"),
                ("left", "Days left (range)", 120, "center"),
                ("conf", "Confidence", 85, "center"), ("advice", "Advice", 130, "w"))
        for col, text, width, anchor in spec:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=60, anchor=anchor,
                             stretch=(col == "next"))
        vsb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        for advice, colour in self.ADVICE_COLOR.items():
            self.tree.tag_configure(advice, foreground=c[colour])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        pane.add(top, minsize=140)

        # chart + explanation
        bottom = tk.Frame(pane, bg=c["bg"])
        self.detail = tk.Text(bottom, width=46, bg=c["surface"], fg=c["text"],
                              wrap="word", font=(FONT, 9), relief="flat", bd=10,
                              state="disabled", highlightthickness=0)
        self.detail.pack(side="right", fill="y")
        self.detail.tag_configure("title", foreground=c["accent"],
                                  font=(FONT, 11, "bold"))
        self.detail.tag_configure("head", foreground=c["accent4"],
                                  font=(FONT, 9, "bold"))
        self.chart_frame = tk.Frame(bottom, bg=c["bg"])
        self.chart_frame.pack(side="left", fill="both", expand=True)
        pane.add(bottom, minsize=260)

    # ── data ──────────────────────────────────────────────────────────────
    def mark_dirty(self):
        """Call after the inventory changes."""
        self._dirty = True
        if self.winfo_ismapped():
            self.refresh()

    def on_show(self):
        if self._dirty:
            self.refresh()

    def _lead(self) -> int:
        try:
            return max(1, min(60, int(self.lead_var.get())))
        except ValueError:
            return 5

    def refresh(self):
        try:
            rows = self.get_rows()
        except Exception as exc:
            self.summary.configure(text=f"Could not load data: {exc}", fg=self.c["error"])
            return
        self._dirty = False
        self._today = date.today()
        horizon = self.HORIZONS[self.horizon_var.get()]
        lead = self._lead()
        ings = build_ingredients(rows)
        results = pred.forecast_all(list(ings.values()), self._today, horizon, lead,
                                    self.sort_var.get())
        self._forecasts = {f.ingredient.key: f for f in results}

        previous = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for f in results:
            self.tree.insert("", "end", iid=f.ingredient.key, tags=(f.advice,),
                             values=self._row(f))
        self._summarise(results, horizon)
        bt = pred.backtest(ings.values())
        if bt["n"]:
            self.backtest_label.configure(
                text=f"Self-check on your history: next-use dates were off by "
                     f"{bt['mae']:.1f} day(s) on average, within 1 day "
                     f"{bt['within1']:.0%} of the time ({bt['n']} predictions).")
        else:
            self.backtest_label.configure(
                text="Self-check needs ingredients with 4+ usage days on record.")

        if previous and previous[0] in self._forecasts:
            self.tree.selection_set(previous[0])
        elif results:
            self.tree.selection_set(results[0].ingredient.key)
        else:
            self._show(None)

    def _row(self, f: pred.Forecast):
        t = self._today
        ing = f.ingredient
        unit = f" {ing.unit}" if ing.unit else ""
        if f.rate is None:
            return (ing.name, f"{f.stock:g}{unit}", "-", "-", "-", "-", "-", "-", f.advice)
        nxt = "-" if f.next_use is None else (
            f"{pred.fmt_date(f.next_use, t)} ({pred.relative(f.next_use, t)})")
        if f.days_left is None:
            left = "no end in sight"
        else:
            lo = f.days_left_early if f.days_left_early is not None else f.days_left
            hi = f"{f.days_left_late}" if f.days_left_late is not None else "+"
            left = f"{f.days_left} ({lo}-{hi})"
        return (ing.name, f"{f.stock:g}{unit}", f"{f.rate:.3g}", nxt,
                f"{f.chance:.0%}", pred.fmt_date(f.runout, t), left, f.confidence,
                f.advice)

    def _summarise(self, results, horizon):
        ranked = [f for f in results if f.chance is not None and not f.inactive]
        likely = sorted(ranked, key=lambda f: -f.chance)[:3]
        reorder = [f for f in results if f.advice in ("Reorder now", "Out of stock")]
        parts = []
        if likely:
            parts.append(f"Most likely to be used in the next {horizon} days: " +
                         ", ".join(f"{f.ingredient.name} ({f.chance:.0%})" for f in likely))
        if reorder:
            parts.append("Reorder now: " + ", ".join(f.ingredient.name for f in reorder))
        if not parts:
            parts.append("Not enough usage history yet - record what you use "
                         "(e.g. \"used 2 kg rice\") and predictions will appear here.")
        self.summary.configure(text="   |   ".join(parts), fg=self.c["accent"])

    # ── selection ─────────────────────────────────────────────────────────
    def _on_select(self, _event):
        sel = self.tree.selection()
        self._show(self._forecasts.get(sel[0]) if sel else None)

    def _show(self, f):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if self._canvas is not None:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if f is None:
            d.insert("end", "Select an ingredient to see its forecast.")
            d.configure(state="disabled")
            return
        text = pred.describe(f, self._today, self._lead())
        title, _, rest = text.partition("\n")
        d.insert("end", title + "\n", "title")
        for line in rest.split("\n"):
            tag = "head" if line in ("Pattern  (last %d usage days)" % min(f.n_uses, pred.RECENT),
                                     "Forecast") else ()
            d.insert("end", line + "\n", tag)
        d.configure(state="disabled")

        fig = pred.build_projection_figure(f, self._today, self._lead())
        self._canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=(0, 8))
