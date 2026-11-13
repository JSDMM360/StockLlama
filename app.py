"""
Main GUI Application for AI Inventory Tracker
Modern dark-themed UI using tkinter + ttk
"""

import tkinter as tk
from tkinter import ttk, messagebox, font
import threading
import time
from datetime import datetime, date

import psycopg2
import requests

from database import DatabaseManager
from ollama_parser import OllamaParser
from config import APP_CONFIG, DB_CONFIG, OLLAMA_CONFIG, MATCH_CONFIG, save_settings
from inventory_logic import (
    MATCH_MODES, MATCH_MODE_LABELS, convert_quantity, fmt_qty, name_key,
)


# ── Color Palette ─────────────────────────────────────────────────────────────
COLORS = {
    "bg":          "#0D1117",
    "surface":     "#161B22",
    "surface2":    "#21262D",
    "surface3":    "#30363D",
    "border":      "#30363D",
    "accent":      "#58A6FF",
    "accent2":     "#3FB950",
    "accent3":     "#F78166",
    "accent4":     "#D2A8FF",
    "text":        "#E6EDF3",
    "text_dim":    "#8B949E",
    "text_dimmer": "#484F58",
    "warning":     "#D29922",
    "success":     "#3FB950",
    "error":       "#F85149",
}


class StyledButton(tk.Button):
    def __init__(self, parent, text, command=None, style="primary", **kwargs):
        styles = {
            "primary": {"bg": COLORS["accent"], "fg": "#0D1117", "activebackground": "#79C0FF"},
            "success": {"bg": COLORS["accent2"], "fg": "#0D1117", "activebackground": "#56D364"},
            "danger":  {"bg": COLORS["accent3"], "fg": "#0D1117", "activebackground": "#FF8B7A"},
            "ghost":   {"bg": COLORS["surface2"], "fg": COLORS["text"], "activebackground": COLORS["surface3"]},
            "nuclear": {"bg": "#8B1A1A", "fg": "#FFFFFF", "activebackground": "#B22222"},
        }
        s = styles.get(style, styles["primary"])
        kwargs.setdefault("padx", 16)
        kwargs.setdefault("pady", 8)
        super().__init__(
            parent, text=text, command=command,
            bg=s["bg"], fg=s["fg"],
            activebackground=s["activebackground"],
            activeforeground=s.get("fg", COLORS["text"]),
            relief="flat", bd=0,
            cursor="hand2",
            font=("Consolas", 10, "bold"),
            **kwargs
        )


class ScrollableFrame(tk.Frame):
    """A vertically scrollable container. Put child widgets into `.inner`."""

    def __init__(self, parent, bg, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel (Windows/macOS: <MouseWheel>, Linux: Button-4/5). Handled globally and
        # filtered by pointer position so the inventory table keeps its own scrolling.
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")
        self.bind_all("<Button-5>", self._on_wheel, add="+")

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _pointer_inside(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def _on_wheel(self, event):
        if not self._pointer_inside(event):
            return
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return  # everything fits, nothing to scroll
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def capture_wheel(self, widget):
        """Stop `widget` (e.g. a Combobox, which changes value on wheel) from eating the scroll."""
        def handler(event):
            self._on_wheel(event)
            return "break"
        widget.bind("<MouseWheel>", handler)
        widget.bind("<Button-4>", handler)
        widget.bind("<Button-5>", handler)


class HoldToConfirmDialog(tk.Toplevel):
    """
    Modal confirmation that cannot be passed by accident: the user must press AND HOLD the bar
    for `hold_seconds`. Releasing, moving the pointer off the bar or closing the window cancels.
    """

    def __init__(self, parent, title, heading, message, bar_text, hold_seconds, on_confirm):
        super().__init__(parent, bg=COLORS["surface"])
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.hold_seconds = hold_seconds
        self.on_confirm = on_confirm
        self._start = None
        self._job = None
        self._done = False
        self.bar_text = bar_text

        tk.Label(self, text="☢", bg=COLORS["surface"], fg=COLORS["error"],
                 font=("Consolas", 36, "bold")).pack(pady=(18, 0))
        tk.Label(self, text=heading, bg=COLORS["surface"], fg=COLORS["error"],
                 font=("Consolas", 14, "bold")).pack(pady=(0, 6))
        tk.Label(self, text=message, bg=COLORS["surface"], fg=COLORS["text"],
                 font=("Consolas", 10), justify="center", wraplength=380).pack(padx=24)
        tk.Label(self, text=f"Press and hold the bar for {hold_seconds:g} seconds to confirm.\n"
                            "Letting go cancels.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 9), justify="center").pack(pady=(10, 8))

        self.bar = tk.Canvas(self, width=380, height=52, bg=COLORS["surface"],
                             highlightthickness=1, highlightbackground=COLORS["error"],
                             cursor="hand2")
        self.bar.pack(padx=24, pady=(0, 10))
        self._draw(0.0)
        self.bar.bind("<ButtonPress-1>", self._press)
        self.bar.bind("<ButtonRelease-1>", self._release)
        self.bar.bind("<Leave>", self._release)

        StyledButton(self, "Cancel", command=self.destroy, style="ghost").pack(pady=(0, 18))
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.focus_set()

    def _draw(self, fraction):
        self.bar.delete("all")
        w, h = 380, 52
        self.bar.create_rectangle(0, 0, w, h, fill="#3A1212", outline="")
        if fraction > 0:
            self.bar.create_rectangle(0, 0, int(w * fraction), h, fill="#B22222", outline="")
        label = self.bar_text if fraction <= 0 else f"{self.bar_text}  {int(fraction * 100)}%"
        self.bar.create_text(w // 2, h // 2, text=label, fill="#FFFFFF",
                             font=("Consolas", 11, "bold"))

    def _press(self, _event):
        if self._done or self._start is not None:
            return
        self._start = time.monotonic()
        self._tick()

    def _tick(self):
        if self._start is None:
            return
        fraction = (time.monotonic() - self._start) / self.hold_seconds
        if fraction >= 1.0:
            self._done = True
            self._start = None
            self._draw(1.0)
            self.grab_release()
            self.destroy()
            self.on_confirm()
            return
        self._draw(fraction)
        self._job = self.after(30, self._tick)

    def _release(self, _event=None):
        if self._done:
            return
        self._start = None
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self._draw(0.0)


class SettingsDialog(tk.Toplevel):
    """Edit the PostgreSQL connection, the Ollama URL and how similar items are matched."""

    def __init__(self, parent, on_save, on_forget):
        super().__init__(parent, bg=COLORS["surface"])
        self.title("Settings")
        self.transient(parent)
        self.resizable(False, False)
        self.on_save = on_save
        self.on_forget = on_forget

        tk.Label(self, text="⚙  SETTINGS", bg=COLORS["surface"], fg=COLORS["accent"],
                 font=("Consolas", 14, "bold")).pack(anchor="w", padx=24, pady=(18, 2))
        tk.Label(self, text="Saved to settings.json next to the app and used from now on.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 9)).pack(anchor="w", padx=24, pady=(0, 10))

        self.vars = {}
        columns = tk.Frame(self, bg=COLORS["surface"])
        columns.pack(fill="x", padx=24)
        left = tk.Frame(columns, bg=COLORS["surface"])
        left.pack(side="left", fill="y", anchor="n")
        right = tk.Frame(columns, bg=COLORS["surface"])
        right.pack(side="left", fill="y", anchor="n", padx=(28, 0))

        def section(frame, text):
            tk.Label(frame, text=text, bg=COLORS["surface"], fg=COLORS["text_dim"],
                     font=("Consolas", 8, "bold")).pack(anchor="w", pady=(8, 4))

        def field(frame, key, label, value, secret=False, width=40):
            tk.Label(frame, text=label, bg=COLORS["surface"], fg=COLORS["text_dim"],
                     font=("Consolas", 8)).pack(anchor="w")
            var = tk.StringVar(value=str(value))
            self.vars[key] = var
            entry = tk.Entry(frame, textvariable=var, width=width,
                             bg=COLORS["surface2"], fg=COLORS["text"],
                             insertbackground=COLORS["accent"], font=("Consolas", 10),
                             relief="flat", bd=6, highlightbackground=COLORS["border"],
                             highlightthickness=1, show="•" if secret else "")
            entry.pack(fill="x", pady=(0, 6))
            return entry

        section(left, "POSTGRESQL")
        field(left, "host", "Host", DB_CONFIG["host"])
        field(left, "port", "Port", DB_CONFIG["port"])
        field(left, "database", "Database name", DB_CONFIG["database"])
        field(left, "user", "Username", DB_CONFIG["user"])
        self.pw_entry = field(left, "password", "Password", DB_CONFIG["password"], secret=True)

        self.show_pw = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Show password", variable=self.show_pw,
                       command=self._toggle_pw, bg=COLORS["surface"], fg=COLORS["text_dim"],
                       selectcolor=COLORS["surface2"], activebackground=COLORS["surface"],
                       activeforeground=COLORS["text"], font=("Consolas", 9),
                       highlightthickness=0, bd=0).pack(anchor="w")

        section(left, "OLLAMA")
        field(left, "ollama_url", "Server URL", OLLAMA_CONFIG["base_url"])

        # ── item matching ──
        section(right, "ITEM MATCHING")
        tk.Label(right, text="Variations such as \"instant dry yeast\", \"active dry yeast\"\n"
                             "and \"yeast\" can be treated as one item.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Consolas", 8),
                 justify="left").pack(anchor="w", pady=(0, 6))
        tk.Label(right, text="When a similar item is found", bg=COLORS["surface"],
                 fg=COLORS["text_dim"], font=("Consolas", 8)).pack(anchor="w")
        self._labels = [MATCH_MODE_LABELS[m] for m in MATCH_MODES]
        self.mode_var = tk.StringVar(value=MATCH_MODE_LABELS[MATCH_CONFIG["mode"]])
        ttk.Combobox(right, textvariable=self.mode_var, values=self._labels, state="readonly",
                     width=42, font=("Consolas", 9)).pack(fill="x", pady=(0, 8))
        field(right, "auto", "Confident match from (%)  - merged without asking",
              round(MATCH_CONFIG["auto_threshold"] * 100), width=10)
        field(right, "ask", "Similar from (%)  - asked about, below this = new item",
              round(MATCH_CONFIG["ask_threshold"] * 100), width=10)
        StyledButton(right, "Forget remembered choices", command=self._forget, style="ghost",
                     padx=10, pady=5).pack(anchor="w", pady=(6, 0))

        self.result = tk.Label(self, text="", bg=COLORS["surface"], fg=COLORS["text_dim"],
                               font=("Consolas", 9), justify="left", wraplength=700, anchor="w")
        self.result.pack(fill="x", padx=24, pady=(10, 0))

        buttons = tk.Frame(self, bg=COLORS["surface"])
        buttons.pack(fill="x", padx=24, pady=(10, 18))
        StyledButton(buttons, "Test connection", command=self._test, style="ghost").pack(side="left")
        StyledButton(buttons, "Cancel", command=self.destroy, style="ghost").pack(side="right")
        StyledButton(buttons, "Save & reconnect", command=self._save,
                     style="primary").pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 4
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()

    def _toggle_pw(self):
        self.pw_entry.configure(show="" if self.show_pw.get() else "•")

    def _forget(self):
        if messagebox.askyesno(
                "Forget remembered choices",
                "Delete every answer you gave to \"Remember my choice\" "
                "(merged names and names kept separate)?", parent=self):
            self.on_forget()
            self._show("Remembered choices deleted.", COLORS["accent2"])

    def _read(self):
        """Return (db_cfg, ollama_cfg, match_cfg) or None after showing a validation error."""
        try:
            port = int(self.vars["port"].get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Settings", "Port must be a number between 1 and 65535.", parent=self)
            return None
        db = {"host": self.vars["host"].get().strip(), "port": port,
              "database": self.vars["database"].get().strip(),
              "user": self.vars["user"].get().strip(),
              "password": self.vars["password"].get()}
        if not db["host"] or not db["database"] or not db["user"]:
            messagebox.showwarning("Settings", "Host, database name and username are required.",
                                   parent=self)
            return None
        try:
            auto = float(self.vars["auto"].get().strip().rstrip("%")) / 100
            ask = float(self.vars["ask"].get().strip().rstrip("%")) / 100
            if not (0 <= ask <= auto <= 1):
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Settings", "Matching thresholds must be percentages from 0 to 100, and "
                            "'Similar from' cannot be higher than 'Confident match from'.", parent=self)
            return None
        mode = MATCH_MODES[self._labels.index(self.mode_var.get())]
        url = self.vars["ollama_url"].get().strip().rstrip("/")
        return (db, {"base_url": url or OLLAMA_CONFIG["base_url"]},
                {"mode": mode, "auto_threshold": auto, "ask_threshold": ask})

    def _show(self, text, color):
        if self.winfo_exists():
            self.result.configure(text=text, fg=color)

    def _test(self):
        cfg = self._read()
        if not cfg:
            return
        db, ollama, _ = cfg
        self._show("Testing...", COLORS["warning"])

        def run():
            lines = []
            try:
                conn = psycopg2.connect(connect_timeout=5, **db)
                conn.close()
                lines.append("✓ PostgreSQL: connected")
            except Exception as e:
                lines.append("✗ PostgreSQL: " + str(e).strip().splitlines()[0])
            try:
                r = requests.get(ollama["base_url"] + "/api/tags", timeout=3)
                lines.append("✓ Ollama: reachable" if r.status_code == 200
                             else f"✗ Ollama: HTTP {r.status_code}")
            except Exception:
                lines.append("✗ Ollama: not reachable")
            color = COLORS["accent2"] if all(l.startswith("✓") for l in lines) else COLORS["warning"]
            try:
                self.after(0, self._show, "\n".join(lines), color)
            except tk.TclError:
                pass  # dialog closed meanwhile

        threading.Thread(target=run, daemon=True).start()

    def _save(self):
        cfg = self._read()
        if not cfg:
            return
        self.on_save(*cfg)
        self.destroy()


class MatchPromptDialog(tk.Toplevel):
    """
    "This looks like an item you already have" - lets the user pick the existing item to update,
    keep the entry as a separate item, or skip it. `result` holds the answer (see
    DatabaseManager.apply_stock_change for the format); closing the window means skip.
    """

    VERBS = {"add": "Add", "remove": "Remove", "set": "Set the count of"}

    def __init__(self, parent, item, decision, position=None):
        super().__init__(parent, bg=COLORS["surface"])
        self.title("Similar item found")
        self.transient(parent)
        self.resizable(False, False)
        self.result = {"action": "skip"}
        self.decision = decision
        action = item.get("action", "add")
        name = item.get("item_name", "?")
        qty = f"{fmt_qty(item.get('quantity', 0))} {item.get('unit') or ''}".strip()

        tk.Label(self, text="≈  SIMILAR ITEM FOUND" + (f"   ({position})" if position else ""),
                 bg=COLORS["surface"], fg=COLORS["accent3"],
                 font=("Consolas", 13, "bold")).pack(anchor="w", padx=24, pady=(18, 4))
        tk.Label(self, text=f"{self.VERBS.get(action, 'Add')} {qty}  “{name}”\n"
                            "looks like something that is already in your inventory.\n"
                            "Which one do you mean?",
                 bg=COLORS["surface"], fg=COLORS["text"], font=("Consolas", 10),
                 justify="left").pack(anchor="w", padx=24, pady=(0, 10))

        self.choice = tk.StringVar()
        box = tk.Frame(self, bg=COLORS["surface2"], highlightbackground=COLORS["border"],
                       highlightthickness=1)
        box.pack(fill="x", padx=24)

        def radio(text, value, hint=""):
            fr = tk.Frame(box, bg=COLORS["surface2"])
            fr.pack(fill="x", padx=10, pady=4)
            tk.Radiobutton(fr, text=text, variable=self.choice, value=value,
                           bg=COLORS["surface2"], fg=COLORS["text"],
                           selectcolor=COLORS["surface"], activebackground=COLORS["surface2"],
                           activeforeground=COLORS["text"], font=("Consolas", 10),
                           highlightthickness=0, anchor="w").pack(side="left")
            if hint:
                tk.Label(fr, text=hint, bg=COLORS["surface2"], fg=COLORS["text_dim"],
                         font=("Consolas", 9)).pack(side="right")

        for cand in decision.candidates[:6]:
            r = cand.row
            have = f"{fmt_qty(r['quantity'])} {r.get('unit') or ''}".strip()
            radio(f"Same as “{r['item_name']}”  (have {have})", f"merge:{r['id']}",
                  f"{int(round(cand.score * 100))}% similar")
        if action == "remove":
            radio("None of these - don't change anything", "skip")
            default = f"merge:{decision.candidates[0].row['id']}"
        else:
            radio(f"No, “{name}” is a different item - keep it separate", "new")
            default = f"merge:{decision.candidates[0].row['id']}"
        self.choice.set(default)

        self.remember = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text=f"Remember my choice for “{name}”", variable=self.remember,
                       bg=COLORS["surface"], fg=COLORS["text_dim"], selectcolor=COLORS["surface2"],
                       activebackground=COLORS["surface"], activeforeground=COLORS["text"],
                       font=("Consolas", 9), highlightthickness=0, bd=0
                       ).pack(anchor="w", padx=24, pady=(10, 0))

        buttons = tk.Frame(self, bg=COLORS["surface"])
        buttons.pack(fill="x", padx=24, pady=(12, 18))
        StyledButton(buttons, "Skip this item", command=self._skip, style="ghost").pack(side="right")
        StyledButton(buttons, "Confirm", command=self._confirm,
                     style="primary").pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self._skip())
        self.bind("<Return>", lambda e: self._confirm())
        self.protocol("WM_DELETE_WINDOW", self._skip)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.focus_set()

    def _confirm(self):
        value = self.choice.get()
        remember = bool(self.remember.get())
        if value.startswith("merge:"):
            self.result = {"action": "merge", "row_id": int(value.split(":")[1]), "remember": remember}
        elif value == "new":
            self.result = {"action": "new", "remember": remember}
        else:
            self.result = {"action": "skip"}
        self.destroy()

    def _skip(self):
        self.result = {"action": "skip"}
        self.destroy()


class MergeSimilarDialog(tk.Toplevel):
    """
    Review groups of existing items that have similar names.

    A group is only a suggestion: "soya sauce" and "hot sauce", or "kidney beans", "mung beans"
    and "red beans", look alike but are different items. So every item gets its own decision:
    keep it as a separate item, or merge it into one of the other items of the group. Any
    number of items per group can be kept, and several merges can happen inside one group.
    Quantities of merged items are added up in the kept item's unit.

    on_merge(jobs, separate_pairs) is called for the ticked groups:
        jobs            [(kept_id, [merged_ids])]    what to merge
        separate_pairs  [(id_a, id_b)]                kept items to remember as "different"
    """

    KEEP = "Keep as a separate item"

    def __init__(self, parent, groups, auto_threshold, on_merge):
        super().__init__(parent, bg=COLORS["surface"])
        self.title("Merge similar items")
        self.transient(parent)
        self.on_merge = on_merge
        self.auto_threshold = auto_threshold
        self.groups = []

        tk.Label(self, text="⧉  MERGE SIMILAR ITEMS", bg=COLORS["surface"], fg=COLORS["accent"],
                 font=("Consolas", 14, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(self, text="Items in a group have similar names, but they are not necessarily the\n"
                            "same item (soya sauce and hot sauce are both sauces). For each item choose\n"
                            "to keep it separate or merge it into another item. Tick a group to apply it;\n"
                            "items you keep separate are not suggested together again.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Consolas", 9),
                 justify="left").pack(anchor="w", padx=20, pady=(0, 8))

        bottom = tk.Frame(self, bg=COLORS["surface"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=14)
        StyledButton(bottom, "Close", command=self.destroy, style="ghost").pack(side="right")
        StyledButton(bottom, "Apply ticked groups", command=self._merge,
                     style="success").pack(side="right", padx=(0, 8))

        scroll = ScrollableFrame(self, bg=COLORS["surface"])
        scroll.pack(fill="both", expand=True, padx=(20, 4))
        self._scroll = scroll
        for g in groups:
            self._add_group(scroll.inner, g)

        self.update_idletasks()
        w, h = 760, 620
        x = parent.winfo_rootx() + max((parent.winfo_width() - w) // 2, 0)
        y = parent.winfo_rooty() + 30
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(640, 460)
        self.grab_set()

    # ── one group ──────────────────────────────────────────────────────────────

    def _add_group(self, parent, g):
        canon = g["canonical"]
        rows = [canon] + [m["row"] for m in g["members"]]
        score = {canon["id"]: 1.0, **{m["row"]["id"]: m["score"] for m in g["members"]}}
        names = [r["item_name"].strip().lower() for r in rows]

        def label_for(r):
            suffix = f" (#{r['id']})" if names.count(r["item_name"].strip().lower()) > 1 else ""
            return f"Merge into “{r['item_name']}”{suffix}"

        label_to_id = {label_for(r): r["id"] for r in rows}
        vars_ = {r["id"]: tk.StringVar(value=self.KEEP) for r in rows}
        combos = {}
        use = tk.BooleanVar(value=False)

        def target(rid):
            return label_to_id.get(vars_[rid].get())          # None = keep separate

        def refresh(*_):
            # An item that others are merged into cannot be merged away itself, and an item can
            # only be merged into an item that is being kept: repeat until the choices are valid.
            changed = True
            while changed:
                changed = False
                choice = {r["id"]: target(r["id"]) for r in rows}
                has_deps = {r["id"]: any(t == r["id"] for t in choice.values()) for r in rows}
                for r in rows:
                    rid = r["id"]
                    options = [self.KEEP]
                    if not has_deps[rid]:
                        for t in rows:
                            if (t["id"] != rid and choice[t["id"]] is None and
                                    convert_quantity(1, r.get("unit"), t.get("unit"))[1]):
                                options.append(label_for(t))
                    combos[rid]["values"] = options
                    if vars_[rid].get() not in options:
                        vars_[rid].set(self.KEEP)
                        changed = True
            # result preview
            choice = {r["id"]: target(r["id"]) for r in rows}
            lines = []
            for r in rows:
                if choice[r["id"]] is not None:
                    continue
                total, absorbed = float(r["quantity"]), []
                for o in rows:
                    if choice[o["id"]] == r["id"]:
                        value, _ok = convert_quantity(float(o["quantity"]), o.get("unit"), r.get("unit"))
                        total += value
                        absorbed.append(o["item_name"])
                u = f" {r['unit']}" if r.get("unit") else ""
                extra = f"   absorbs: {', '.join(absorbed)}" if absorbed else ""
                lines.append(f"  → {r['item_name']}: {fmt_qty(total)}{u}{extra}")
            summary.configure(text="Result:\n" + "\n".join(lines))

        def suggest():
            for r in rows:
                rid = r["id"]
                m = next((m for m in g["members"] if m["row"]["id"] == rid), None)
                vars_[rid].set(label_for(canon) if (m and m["mergeable"] and
                                                    m["score"] >= self.auto_threshold) else self.KEEP)
            refresh()
            use.set(any(target(r["id"]) is not None for r in rows))

        def keep_all():
            for r in rows:
                vars_[r["id"]].set(self.KEEP)
            refresh()

        frame = tk.Frame(parent, bg=COLORS["surface2"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        frame.pack(fill="x", pady=5, padx=(0, 12))
        summary = tk.Label(frame, text="", bg=COLORS["surface2"], fg=COLORS["accent3"],
                           font=("Consolas", 9), justify="left", anchor="w")
        head = tk.Frame(frame, bg=COLORS["surface2"])
        head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Checkbutton(head, text="Apply this group", variable=use, bg=COLORS["surface2"],
                       fg=COLORS["text"], selectcolor=COLORS["surface"],
                       activebackground=COLORS["surface2"], activeforeground=COLORS["text"],
                       font=("Consolas", 10, "bold"), highlightthickness=0).pack(side="left")
        StyledButton(head, "Keep all separate", command=keep_all, style="ghost",
                     padx=8, pady=2).pack(side="right", padx=(6, 0))
        StyledButton(head, "Suggestion", command=suggest, style="ghost",
                     padx=8, pady=2).pack(side="right")

        for r in rows:
            line = tk.Frame(frame, bg=COLORS["surface2"])
            line.pack(fill="x", padx=12, pady=2)
            have = f"{fmt_qty(r['quantity'])} {r.get('unit') or ''}".strip()
            tk.Label(line, text=f"{r['item_name']}", bg=COLORS["surface2"], fg=COLORS["text"],
                     font=("Consolas", 10, "bold"), width=22, anchor="w").pack(side="left")
            tk.Label(line, text=have, bg=COLORS["surface2"], fg=COLORS["text_dim"],
                     font=("Consolas", 9), width=11, anchor="w").pack(side="left")
            pct = "" if r is canon else f"{int(round(score[r['id']] * 100))}%"
            tk.Label(line, text=pct, bg=COLORS["surface2"], fg=COLORS["text_dim"],
                     font=("Consolas", 9), width=5, anchor="e").pack(side="left")
            combo = ttk.Combobox(line, textvariable=vars_[r["id"]], state="readonly", width=34,
                                 font=("Consolas", 9))
            combo.pack(side="right")
            combo.bind("<<ComboboxSelected>>", refresh)
            combos[r["id"]] = combo
            self._scroll.capture_wheel(combo)      # wheel scrolls the list, not the drop-down
        summary.pack(fill="x", padx=12, pady=(6, 8))
        suggest()
        self.groups.append({"use": use, "rows": rows, "target": target, "vars": vars_,
                            "combos": combos, "refresh": refresh, "label_for": label_for})

    # ── apply ──────────────────────────────────────────────────────────────────

    def _merge(self):
        jobs, pairs = [], []
        for g in self.groups:
            if not g["use"].get():
                continue
            rows, target = g["rows"], g["target"]
            keepers = [r["id"] for r in rows if target(r["id"]) is None]
            for k in keepers:
                deps = [r["id"] for r in rows if target(r["id"]) == k]
                if deps:
                    jobs.append((k, deps))
            for i, a in enumerate(keepers):
                for b in keepers[i + 1:]:
                    pairs.append((a, b))
        self.destroy()
        if jobs or pairs:
            self.on_merge(jobs, pairs)


class NamesDialog(tk.Toplevel):
    """
    The name dictionary: several names that mean the same item ("mint" = "pudina", "curd" =
    "yogurt"), including everyday local names. Names of one group are treated as one item
    everywhere: a new entry under any of them updates the item that is stored under another.
    Names can be added before or after the items exist.
    """

    def __init__(self, parent, db, prefill=None, on_close=None):
        super().__init__(parent, bg=COLORS["surface"])
        self.title("Names dictionary")
        self.transient(parent)
        self.db = db
        self.on_close = on_close
        self.changed = False
        self.groups = []
        self.current = None

        tk.Label(self, text="📖  NAMES DICTIONARY", bg=COLORS["surface"], fg=COLORS["accent"],
                 font=("Consolas", 14, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(self, text="Some items have more than one name (mint / pudina, curd / yogurt). Names in\n"
                            "the same group are treated as ONE item: an entry under any of them\n"
                            "updates the item stored under another.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Consolas", 9),
                 justify="left").pack(anchor="w", padx=20, pady=(0, 8))

        bottom = tk.Frame(self, bg=COLORS["surface"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=(8, 14))
        StyledButton(bottom, "Close", command=self._close, style="ghost").pack(side="right")
        StyledButton(bottom, "Load starter names", command=self._load_starter,
                     style="ghost").pack(side="left")

        new = tk.Frame(self, bg=COLORS["surface"])
        new.pack(side="bottom", fill="x", padx=20, pady=(6, 0))
        tk.Label(new, text="NEW GROUP - names separated by commas (e.g. mint, pudina)",
                 bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Consolas", 8, "bold")
                 ).pack(anchor="w")
        row = tk.Frame(new, bg=COLORS["surface"])
        row.pack(fill="x", pady=(2, 0))
        self.new_var = tk.StringVar(value=(prefill + ", " if prefill else ""))
        self.new_entry = self._entry(row, self.new_var)
        self.new_entry.pack(side="left", fill="x", expand=True)
        StyledButton(row, "Create group", command=self._create, style="success",
                     padx=10, pady=5).pack(side="left", padx=(8, 0))

        body = tk.Frame(self, bg=COLORS["surface"])
        body.pack(fill="both", expand=True, padx=20)
        left = tk.Frame(body, bg=COLORS["surface"])
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="GROUPS", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w")
        self.group_list = self._listbox(left, 44)
        right = tk.Frame(body, bg=COLORS["surface"])
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))
        tk.Label(right, text="NAMES OF THE SELECTED GROUP", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w")
        self.name_list = self._listbox(right, 30, height=7)
        add = tk.Frame(right, bg=COLORS["surface"])
        add.pack(fill="x", pady=(6, 0))
        self.add_var = tk.StringVar()
        self._entry(add, self.add_var).pack(side="left", fill="x", expand=True)
        StyledButton(add, "Add name", command=self._add_name, style="primary",
                     padx=10, pady=5).pack(side="left", padx=(8, 0))
        btns = tk.Frame(right, bg=COLORS["surface"])
        btns.pack(fill="x", pady=(6, 0))
        StyledButton(btns, "Remove name", command=self._remove_name, style="ghost",
                     padx=10, pady=4).pack(side="left")
        StyledButton(btns, "Delete group", command=self._delete_group, style="danger",
                     padx=10, pady=4).pack(side="left", padx=(8, 0))
        tk.Label(right, text="IN YOUR INVENTORY", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", pady=(10, 0))
        self.items_label = tk.Label(right, text="", bg=COLORS["surface"], fg=COLORS["accent3"],
                                    font=("Consolas", 9), justify="left", anchor="w", wraplength=300)
        self.items_label.pack(fill="x")

        self.group_list.bind("<<ListboxSelect>>", self._on_select)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self._reload(select_name=prefill)

        self.update_idletasks()
        w, h = 860, 600
        x = parent.winfo_rootx() + max((parent.winfo_width() - w) // 2, 0)
        y = parent.winfo_rooty() + 30
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(760, 520)
        self.grab_set()
        self.new_entry.focus_set()

    # ── small widget helpers ──
    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, bg=COLORS["surface2"], fg=COLORS["text"],
                        insertbackground=COLORS["accent"], font=("Consolas", 10), relief="flat",
                        bd=6, highlightbackground=COLORS["border"], highlightthickness=1)

    def _listbox(self, parent, width, height=12):
        frame = tk.Frame(parent, bg=COLORS["surface"])
        frame.pack(fill="both", expand=True)
        lb = tk.Listbox(frame, width=width, height=height, exportselection=False,
                        bg=COLORS["surface2"], fg=COLORS["text"], font=("Consolas", 10),
                        selectbackground=COLORS["accent"], selectforeground="#0D1117",
                        highlightbackground=COLORS["border"], highlightthickness=1, relief="flat",
                        activestyle="none")
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        return lb

    # ── data ──
    def _reload(self, select_id=None, select_name=None):
        self.groups = self.db.get_name_groups()
        self.group_list.delete(0, "end")
        pick = None
        for i, g in enumerate(self.groups):
            mark = "● " if g["items"] else "  "
            self.group_list.insert("end", mark + " = ".join(n["display_name"] for n in g["names"]))
            if select_id is not None and g["id"] == select_id:
                pick = i
            if select_name and any(n["name_key"] == name_key(select_name) for n in g["names"]):
                pick = i
        if pick is not None:
            self.group_list.selection_set(pick)
            self.group_list.see(pick)
        self._on_select()

    def _on_select(self, *_):
        sel = self.group_list.curselection()
        self.current = self.groups[sel[0]] if sel else None
        self.name_list.delete(0, "end")
        if self.current:
            for n in self.current["names"]:
                self.name_list.insert("end", n["display_name"])
            items = self.current["items"]
            self.items_label.configure(
                text="\n".join(f"{r['item_name']}: {fmt_qty(r['quantity'])} {r.get('unit') or ''}".strip()
                               for r in items) or "(no item is stored under these names yet)")
        else:
            self.items_label.configure(text="")

    def _warn(self, text):
        messagebox.showwarning("Names dictionary", text, parent=self)

    def _create(self):
        names = [n.strip() for n in self.new_var.get().split(",") if n.strip()]
        try:
            gid = self.db.add_name_group(names)
        except ValueError as e:
            self._warn(str(e))
            return
        self.changed = True
        self.new_var.set("")
        self._reload(select_id=gid)

    def _add_name(self):
        if not self.current:
            self._warn("Select a group first, or create a new one below.")
            return
        try:
            self.db.add_name_to_group(self.current["id"], self.add_var.get())
        except ValueError as e:
            self._warn(str(e))
            return
        self.changed = True
        self.add_var.set("")
        self._reload(select_id=self.current["id"])

    def _remove_name(self):
        sel = self.name_list.curselection()
        if not (self.current and sel):
            return
        self.db.remove_name(self.current["names"][sel[0]]["name_key"])
        self.changed = True
        self._reload(select_id=self.current["id"])

    def _delete_group(self):
        if not self.current:
            return
        if messagebox.askyesno("Delete group", "Delete this group of names? Your inventory items are not "
                               "touched.", parent=self):
            self.db.delete_name_group(self.current["id"])
            self.changed = True
            self._reload()

    def _load_starter(self):
        if messagebox.askyesno(
                "Load starter names",
                "Add a list of common names (for example mint = pudina, yogurt = curd, turmeric = "
                "haldi)? Existing groups are kept and you can edit or delete any of them.", parent=self):
            n = self.db.load_starter_names()
            self.changed = True
            self._reload()
            messagebox.showinfo("Names dictionary", f"{n} group(s) added.", parent=self)

    def _close(self):
        self.destroy()
        if self.on_close:
            self.on_close(self.changed)


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
        self.sort_col = "updated_at"      # default order: most recently updated first
        self.sort_desc = True
        self._suppress_select = False

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

        # Scrollbar
        style.configure("Vertical.TScrollbar",
            background=COLORS["surface3"],
            troughcolor=COLORS["surface"],
            bordercolor=COLORS["surface"],
            arrowcolor=COLORS["text_dim"])

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

        StyledButton(header, "⚙ Settings", command=self._open_settings, style="ghost",
            padx=12, pady=5).pack(side="right", padx=(0, 16))

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

        # ── Main layout: left sidebar + right content ─────────────────────────
        main = tk.Frame(self.root, bg=COLORS["bg"])
        main.pack(fill="both", expand=True)

        # Left panel
        left = tk.Frame(main, bg=COLORS["surface"], width=360)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # Scrollable so the Manual Entry form is always reachable on small screens
        self.left_scroll = ScrollableFrame(left, bg=COLORS["surface"])
        self.left_scroll.pack(fill="both", expand=True)
        self._build_input_panel(self.left_scroll.inner)

        # Right panel
        right = tk.Frame(main, bg=COLORS["bg"])
        right.pack(side="right", fill="both", expand=True)

        self._build_stats_bar(right)
        self._build_table_panel(right)

    def _build_input_panel(self, parent):
        """Build left AI input panel."""
        tk.Label(parent, text="NATURAL LANGUAGE INPUT",
            bg=COLORS["surface"], fg=COLORS["text_dim"],
            font=("Consolas", 8, "bold")).pack(anchor="w", padx=16, pady=(20, 4))

        # Input area
        input_frame = tk.Frame(parent, bg=COLORS["surface2"],
            highlightbackground=COLORS["border"], highlightthickness=1)
        input_frame.pack(fill="x", padx=16, pady=(0, 8))

        self.input_text = tk.Text(input_frame,
            height=5, bg=COLORS["surface2"],
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
        parse_btn = StyledButton(parent, "⚡  PARSE WITH AI",
            command=self._parse_input, style="primary")
        parse_btn.pack(fill="x", padx=16, pady=(0, 4))

        # AI output
        tk.Label(parent, text="AI RESPONSE",
            bg=COLORS["surface"], fg=COLORS["text_dim"],
            font=("Consolas", 8, "bold")).pack(anchor="w", padx=16, pady=(12, 4))

        ai_frame = tk.Frame(parent, bg=COLORS["surface2"],
            highlightbackground=COLORS["border"], highlightthickness=1)
        ai_frame.pack(fill="x", padx=16)

        self.ai_output = tk.Text(ai_frame,
            height=6, bg=COLORS["surface2"],
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
                self.left_scroll.capture_wheel(widget)
            else:
                widget = tk.Entry(form_scroll, textvariable=var,
                    bg=COLORS["surface2"], fg=COLORS["text"],
                    insertbackground=COLORS["accent"],
                    font=("Consolas", 10), relief="flat", bd=6,
                    highlightbackground=COLORS["border"],
                    highlightthickness=1)

            widget.pack(fill="x", pady=(0, 6))

        btn_frame = tk.Frame(parent, bg=COLORS["surface"])
        btn_frame.pack(fill="x", padx=16, pady=(8, 24))

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

        StyledButton(toolbar, "⧉ Merge similar",
            command=self._open_merge_similar, style="ghost").pack(side="left", padx=4)

        StyledButton(toolbar, "📖 Names",
            command=self._open_names, style="ghost").pack(side="left", padx=4)

        StyledButton(toolbar, "🗑 Delete",
            command=self._delete_item, style="danger").pack(side="right", padx=4)

        StyledButton(toolbar, "☢ Erase all",
            command=self._erase_all, style="nuclear").pack(side="right", padx=(4, 20))

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

        self._headers = dict(zip(columns, headers))
        for col, hdr, w in zip(columns, headers, widths):
            # Clicking a heading sorts by that column; clicking again flips ascending/descending
            self.tree.heading(col, text=hdr, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=w, minwidth=40)
        self._update_heading_arrows()

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

    def _on_row_select(self, event):
        if self._suppress_select:
            return
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

        self._set_ai_output("⚡ Thinking...", COLORS["warning"])
        self._set_status("● Parsing...", COLORS["warning"])

        def _run():
            try:
                accumulated = []

                def on_chunk(token):
                    accumulated.append(token)
                    self.root.after(0, self._set_ai_output,
                        "".join(accumulated), COLORS["accent"])

                items = self.parser.parse_input(text, on_chunk=on_chunk)

                for item in items:
                    item["raw_input"] = text

                self.root.after(0, self._show_parsed_items, items, text)

            except ConnectionError as e:
                self.root.after(0, self._set_ai_output, str(e), COLORS["error"])
                self.root.after(0, self._set_status, "● Ollama offline", COLORS["error"])
            except Exception as e:
                self.root.after(0, self._set_ai_output,
                    f"Parse error: {e}", COLORS["error"])
                self.root.after(0, self._set_status, "● Error", COLORS["error"])

        threading.Thread(target=_run, daemon=True).start()

    def _show_parsed_items(self, items: list, raw_input: str):
        """Show parsed items with confirm button."""
        # Clear confirm frame
        for w in self.confirm_frame.winfo_children():
            w.destroy()

        if not items:
            self._set_ai_output("No items found. Try rephrasing.", COLORS["warning"])
            return

        summary = []
        icons = {"add": "＋ add", "remove": "－ remove", "set": "＝ set to"}
        for item in items:
            qty = fmt_qty(item.get("quantity", 0))
            unit = item.get("unit") or ""
            name = item.get("item_name", "?")
            cat = item.get("category", "")
            verb = icons.get(item.get("action"), "＋ add")
            summary.append(f"  {verb} {qty} {unit} {name} [{cat}]")

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
            command=lambda: [w.destroy() for w in self.confirm_frame.winfo_children()],
            style="ghost").pack(side="left")

    def _save_parsed_items(self, items: list):
        """Apply parsed items to the database (update existing rows, create new ones)."""
        if not self.db:
            messagebox.showerror("DB Error", "Database not connected.")
            return
        try:
            results = []
            for n, item in enumerate(items, 1):
                self._prompt_position = f"item {n} of {len(items)}" if len(items) > 1 else None
                results.append(self.db.apply_stock_change(item, chooser=self._ask_match))
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
            return

        bad = {"not_found", "unit_mismatch", "invalid", "clamped", "ambiguous", "skipped"}
        lines = []
        for r in results:
            mark = "⚠" if r["status"] in bad else "✓"
            lines.append(f"  {mark} {r['message']}")
        problems = sum(1 for r in results if r["status"] in bad)

        self._set_ai_output("\n".join(lines),
                            COLORS["warning"] if problems else COLORS["accent2"])
        done = len(results) - problems
        self._set_status(
            f"✓ Applied {done} change(s)" + (f", {problems} need attention" if problems else ""),
            COLORS["warning"] if problems else COLORS["accent2"])
        for w in self.confirm_frame.winfo_children():
            w.destroy()
        self._refresh_table()
        self._refresh_stats()

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
            "action": "add",
            "quantity": qty,
            "unit": self.form_vars["unit_var"].get().strip() or None,
            "category": self.form_vars["category_var"].get().strip() or "General",
            "date_bought": self.form_vars["date_bought_var"].get().strip() or None,
            "date_used": self.form_vars["date_used_var"].get().strip() or None,
            "notes": self.form_vars["notes_var"].get().strip() or None,
            "raw_input": None,
        }

        try:
            self._prompt_position = None
            res = self.db.apply_stock_change(item, chooser=self._ask_match)
            self._refresh_table()
            self._refresh_stats()
            warn = res["status"] in ("unit_mismatch", "invalid", "ambiguous", "skipped")
            self._set_status(("⚠ " if warn else "✓ ") + res["message"],
                             COLORS["warning"] if warn else COLORS["accent2"])
        except Exception as e:
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

        try:
            self.db.update_item(self.selected_item_id, updates)
            self._refresh_table()
            self._refresh_stats()
            self._set_status("✓ Item updated", COLORS["accent2"])
        except Exception as e:
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

        try:
            self.db.delete_item(self.selected_item_id)
            self.selected_item_id = None
            self._refresh_table()
            self._refresh_stats()
            self._set_status("✓ Item deleted", COLORS["accent3"])
        except Exception as e:
            messagebox.showerror("DB Error", str(e))

    # ── Sorting ────────────────────────────────────────────────────────────────

    _NUMERIC_COLS = ("id", "quantity")

    def _sort_by(self, col):
        """Heading clicked: sort by this column, or flip the direction if it is already active."""
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            # newest first for time columns, otherwise A-Z / smallest first
            self.sort_desc = col in ("updated_at", "date_bought", "date_used")
        self._update_heading_arrows()
        self._refresh_table()

    def _update_heading_arrows(self):
        for col, text in self._headers.items():
            if col == self.sort_col:
                text += "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(col, text=text)

    def _sorted(self, items):
        col = self.sort_col
        if not col:
            return items

        def blank(v):
            return v is None or (isinstance(v, str) and not v.strip())

        def key(item):
            v = item.get(col)
            if col in self._NUMERIC_COLS:
                return float(v)
            if isinstance(v, (datetime, date)):
                return v
            return str(v).strip().lower()

        present = [i for i in items if not blank(i.get(col))]
        missing = [i for i in items if blank(i.get(col))]   # empty values always go last
        present.sort(key=key, reverse=self.sort_desc)
        return present + missing

    # ── Erase everything ───────────────────────────────────────────────────────

    def _erase_all(self):
        """Open the press-and-hold confirmation for wiping the whole database."""
        if not self.db:
            messagebox.showerror("DB Error", "Database not connected.")
            return
        try:
            count = self.db.count_items()
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
            return
        if count == 0:
            messagebox.showinfo("Nothing to erase", "The inventory is already empty.")
            return
        HoldToConfirmDialog(
            self.root, title="Erase database",
            heading="ERASE EVERYTHING?",
            message=(f"This permanently deletes all {count} inventory item(s) and the complete "
                     "change history. It cannot be undone."),
            bar_text="HOLD TO ERASE", hold_seconds=3.0,
            on_confirm=lambda: self._do_erase_all())

    def _do_erase_all(self):
        try:
            removed = self.db.clear_all()
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
            return
        self.selected_item_id = None
        for var in self.form_vars.values():
            var.set("")
        self._refresh_table()
        self._refresh_stats()
        self._set_ai_output("", COLORS["accent2"])
        self._set_status(f"☢ Database erased ({removed} item(s) removed)", COLORS["accent3"])

    # ── Settings ───────────────────────────────────────────────────────────────

    def _open_settings(self):
        SettingsDialog(self.root, on_save=self._apply_settings, on_forget=self._forget_matches)

    def _forget_matches(self):
        if not self.db:
            return
        try:
            n = self.db.forget_remembered_matches()
            self._set_status(f"✓ Forgot {n} remembered choice(s)", COLORS["accent2"])
        except Exception as e:
            messagebox.showerror("DB Error", str(e))

    # ── Similar items ──────────────────────────────────────────────────────────

    def _ask_match(self, decision, item):
        """Ask the user which existing item an uncertain entry refers to."""
        dlg = MatchPromptDialog(self.root, item, decision,
                                position=getattr(self, "_prompt_position", None))
        self.root.wait_window(dlg)
        return dlg.result

    def _open_names(self):
        """Open the names dictionary, starting at the selected item's group (or a new group for it)."""
        if not self.db:
            messagebox.showerror("DB Error", "Database not connected.")
            return
        prefill = None
        sel = self.tree.selection()
        if sel:
            prefill = str(self.tree.item(sel[0])["values"][1])
        NamesDialog(self.root, self.db, prefill=prefill, on_close=self._after_names)

    def _after_names(self, changed):
        """After editing the dictionary: offer to merge items that now share a name."""
        if not changed:
            return
        self._refresh_table()
        try:
            groups = self.db.find_similar_groups()
        except Exception:
            return
        auto = float(MATCH_CONFIG["auto_threshold"])
        confident = [g for g in groups if any(m["score"] >= auto for m in g["members"])]
        if confident and messagebox.askyesno(
                "Items now share a name",
                "Some items in your inventory are stored under different names of the same item "
                "(for example \"Mint\" and \"Pudina\"). Review and merge them now?"):
            self._open_merge_similar()

    def _open_merge_similar(self):
        if not self.db:
            messagebox.showerror("DB Error", "Database not connected.")
            return
        try:
            groups = self.db.find_similar_groups()
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
            return
        if not groups:
            messagebox.showinfo("Merge similar items",
                                "No similar items found. Every item looks distinct.")
            return
        MergeSimilarDialog(self.root, groups, float(MATCH_CONFIG["auto_threshold"]),
                           on_merge=self._do_merge)

    def _do_merge(self, jobs, separate_pairs=()):
        merged_total, skipped, kept = 0, [], 0
        try:
            if separate_pairs:
                kept = self.db.mark_items_distinct(separate_pairs)
            for keep, members in jobs:
                out = self.db.merge_items(keep, members)
                merged_total += len(out["merged"])
                skipped += out["skipped"]
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
        self.selected_item_id = None
        self._refresh_table()
        self._refresh_stats()
        text = f"✓ Merged {merged_total} item(s)"
        if kept:
            text += f", {kept} pair(s) kept separate"
        if skipped:
            text += f"; left {len(skipped)} with a different unit"
        self._set_status(text, COLORS["warning"] if skipped else COLORS["accent2"])

    def _apply_settings(self, db_cfg, ollama_cfg, match_cfg):
        """Persist the new settings and reconnect with them."""
        try:
            save_settings(db=db_cfg, ollama=ollama_cfg, matching=match_cfg)
        except OSError as e:
            messagebox.showwarning(
                "Settings", f"Applied for this session, but could not be saved to disk:\n{e}")
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
            self.db = None
        self.parser.reload_config()
        self.tree.delete(*self.tree.get_children())
        self.selected_item_id = None
        self._set_status("● Reconnecting...", COLORS["warning"])
        self._connect_services()

    # ── Data refresh ───────────────────────────────────────────────────────────

    def _refresh_table(self):
        """Reload inventory table data."""
        if not self.db:
            return

        search = self.search_var.get().strip()
        category = self.filter_var.get()

        try:
            items = self._sorted(self.db.get_all_items(search=search, category=category))
            low_stock = {
                i["id"] for i in self.db.get_low_stock(
                    APP_CONFIG["low_stock_threshold"])
            }

            keep = str(self.selected_item_id) if self.selected_item_id is not None else None
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

                self.tree.insert("", "end", iid=str(item["id"]), values=(
                    item["id"],
                    item["item_name"],
                    fmt_qty(item["quantity"]),
                    item.get("unit") or "",
                    item.get("category") or "",
                    item.get("date_bought") or "",
                    item.get("date_used") or "",
                    item.get("notes") or "",
                    updated,
                ), tags=tags)

            # Keep the current selection (without re-filling the manual form)
            if keep and self.tree.exists(keep):
                self._suppress_select = True
                try:
                    self.tree.selection_set(keep)
                finally:
                    self._suppress_select = False

            # Update category filter
            cats = ["All"] + self.db.get_categories()
            self.filter_combo["values"] = cats

        except Exception as e:
            self._set_status(f"Table error: {e}", COLORS["error"])

    def _refresh_stats(self):
        """Refresh statistics cards."""
        if not self.db:
            return
        try:
            stats = self.db.get_stats()
            for key, widget in self.stat_widgets.items():
                val = stats.get(key, 0) or 0
                widget.configure(text=str(int(val)))
        except Exception:
            pass

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
                    "Open ⚙ Settings (top right) to change the host, database, "
                    "username or password.")

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
                    self._set_status("● DB ok  |  Ollama offline", COLORS["warning"])
                elif ollama_ok:
                    self._set_status("● Ollama ok  |  DB offline", COLORS["warning"])
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
