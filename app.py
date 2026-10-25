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
    MATCH_MODES, MATCH_MODE_LABELS, convert_quantity, fmt_qty,
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
    Lists groups of existing items that look like the same item (for example "yeast",
    "active dry yeast", "instant dry yeast"), lets the user choose the item to keep for each
    group, and merges the ticked groups: quantities are added up in the kept item's unit.
    """

    def __init__(self, parent, groups, auto_threshold, on_merge):
        super().__init__(parent, bg=COLORS["surface"])
        self.title("Merge similar items")
        self.transient(parent)
        self.on_merge = on_merge
        self.geometry("680x560")
        self.minsize(560, 420)
        self.groups = []

        tk.Label(self, text="⧉  MERGE SIMILAR ITEMS", bg=COLORS["surface"], fg=COLORS["accent"],
                 font=("Consolas", 14, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(self, text="Tick the groups to merge and choose which item to keep. The other "
                            "names are remembered,\nso future entries under those names update the "
                            "kept item.",
                 bg=COLORS["surface"], fg=COLORS["text_dim"], font=("Consolas", 9),
                 justify="left").pack(anchor="w", padx=20, pady=(0, 8))

        bottom = tk.Frame(self, bg=COLORS["surface"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=14)
        StyledButton(bottom, "Close", command=self.destroy, style="ghost").pack(side="right")
        StyledButton(bottom, "Merge selected", command=self._merge,
                     style="success").pack(side="right", padx=(0, 8))

        scroll = ScrollableFrame(self, bg=COLORS["surface"])
        scroll.pack(fill="both", expand=True, padx=(20, 4))
        for g in groups:
            self._add_group(scroll.inner, g, auto_threshold)

        self.update_idletasks()
        x = parent.winfo_rootx() + max((parent.winfo_width() - 680) // 2, 0)
        y = parent.winfo_rooty() + 40
        self.geometry(f"680x560+{x}+{y}")
        self.grab_set()

    def _add_group(self, parent, g, auto_threshold):
        canon = g["canonical"]
        members = g["members"]
        conf = min(m["score"] for m in members)
        use = tk.BooleanVar(value=conf >= auto_threshold)
        keep = tk.IntVar(value=canon["id"])
        rows = [canon] + [m["row"] for m in members]
        ok_flags = {canon["id"]: True, **{m["row"]["id"]: m["mergeable"] for m in members}}

        frame = tk.Frame(parent, bg=COLORS["surface2"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        frame.pack(fill="x", pady=5, padx=(0, 12))
        head = tk.Frame(frame, bg=COLORS["surface2"])
        head.pack(fill="x", padx=10, pady=(8, 2))
        tk.Checkbutton(head, text="Merge this group", variable=use, bg=COLORS["surface2"],
                       fg=COLORS["text"], selectcolor=COLORS["surface"],
                       activebackground=COLORS["surface2"], activeforeground=COLORS["text"],
                       font=("Consolas", 10, "bold"), highlightthickness=0).pack(side="left")
        tk.Label(head, text=f"{int(round(conf * 100))}% similar", bg=COLORS["surface2"],
                 fg=COLORS["accent2"] if conf >= auto_threshold else COLORS["warning"],
                 font=("Consolas", 9)).pack(side="right")
        tk.Label(frame, text="Keep:", bg=COLORS["surface2"], fg=COLORS["text_dim"],
                 font=("Consolas", 8)).pack(anchor="w", padx=12)

        result = tk.Label(frame, text="", bg=COLORS["surface2"], fg=COLORS["accent3"],
                          font=("Consolas", 9))

        def preview(*_):
            kept = next(r for r in rows if r["id"] == keep.get())
            total, merged = float(kept["quantity"]), 0
            for r in rows:
                if r["id"] == kept["id"]:
                    continue
                value, ok = convert_quantity(float(r["quantity"]), r.get("unit"), kept.get("unit"))
                if ok:
                    total += value
                    merged += 1
            u = f" {kept['unit']}" if kept.get("unit") else ""
            result.configure(text=f"→ “{kept['item_name']}”: {fmt_qty(total)}{u}  "
                                  f"({merged} item(s) merged in)")

        for r in rows:
            line = tk.Frame(frame, bg=COLORS["surface2"])
            line.pack(fill="x", padx=12)
            tk.Radiobutton(line, text=r["item_name"], variable=keep, value=r["id"],
                           command=preview, bg=COLORS["surface2"], fg=COLORS["text"],
                           selectcolor=COLORS["surface"], activebackground=COLORS["surface2"],
                           activeforeground=COLORS["text"], font=("Consolas", 10),
                           highlightthickness=0, anchor="w").pack(side="left")
            have = f"{fmt_qty(r['quantity'])} {r.get('unit') or ''}".strip()
            note = have if ok_flags[r["id"]] else f"{have}  (different unit - left as is)"
            tk.Label(line, text=note, bg=COLORS["surface2"],
                     fg=COLORS["text_dim"] if ok_flags[r["id"]] else COLORS["warning"],
                     font=("Consolas", 9)).pack(side="right")
        result.pack(anchor="w", padx=12, pady=(4, 8))
        preview()
        self.groups.append({"use": use, "keep": keep, "rows": rows})

    def _merge(self):
        jobs = []
        for g in self.groups:
            if g["use"].get():
                keep = g["keep"].get()
                jobs.append((keep, [r["id"] for r in g["rows"] if r["id"] != keep]))
        self.destroy()
        if jobs:
            self.on_merge(jobs)


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

    def _do_merge(self, jobs):
        merged_total, skipped = 0, []
        try:
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
