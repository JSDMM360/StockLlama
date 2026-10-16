#!/usr/bin/env python3
"""
Pantry Tracker — Tkinter GUI
Run with: python pantry_gui.py
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import datetime
import queue
import sys
import sounddevice as sd
from vosk import KaldiRecognizer, Model

# ── Backend import ────────────────────────────────────────────────────────────
import pantry_tracker as pt


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette
# ─────────────────────────────────────────────────────────────────────────────
BG         = "#1e1e2e"   # deep navy
SURFACE    = "#2a2a3e"   # slightly lighter panel
ACCENT     = "#7c5cbf"   # purple
ACCENT_HOV = "#9b7de0"
GREEN      = "#50fa7b"
RED        = "#ff5555"
YELLOW     = "#f1fa8c"
CYAN       = "#8be9fd"
FG         = "#cdd6f4"   # main text
FG_DIM     = "#6c7086"   # muted text
BORDER     = "#45475a"
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.2  # Process audio every 200ms for rapid updates
BLOCK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)
audio_queue = queue.Queue()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _tag_color(msg: str) -> str:
    """Pick a log-line tag based on emoji prefix."""
    if msg.startswith(("  ➕", "  🆕")):
        return "green"
    if msg.startswith("  ➖"):
        return "cyan"
    if msg.startswith("  ⚠"):
        return "yellow"
    if msg.startswith("  ❌") or "error" in msg.lower():
        return "red"
    return "normal"

def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())


# ─────────────────────────────────────────────────────────────────────────────
# Settings dialog
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "PantryApp"):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="⚙  Settings", bg=BG, fg=FG,
                 font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(14, 6), padx=14)

        fields = [
            ("Ollama Host",   "ollama_host",  pt.OLLAMA_HOST),
            ("Ollama Model",  "ollama_model", pt.OLLAMA_MODEL),
            ("DB Host",       "db_host",      pt.DB_CONFIG["host"]),
            ("DB Port",       "db_port",      str(pt.DB_CONFIG["port"])),
            ("DB Name",       "db_name",      pt.DB_CONFIG["dbname"]),
            ("DB User",       "db_user",      pt.DB_CONFIG["user"]),
            ("DB Password",   "db_pass",      pt.DB_CONFIG["password"]),
        ]

        self.vars: dict[str, tk.StringVar] = {}
        for i, (label, key, default) in enumerate(fields, start=1):
            tk.Label(self, text=label, bg=BG, fg=FG_DIM,
                     font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="e", **pad)
            var = tk.StringVar(value=default)
            self.vars[key] = var
            show = "*" if key == "db_pass" else ""
            e = tk.Entry(self, textvariable=var, bg=SURFACE, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=("Segoe UI", 10), width=28, show=show)
            e.grid(row=i, column=1, sticky="w", **pad)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.grid(row=len(fields) + 1, column=0, columnspan=2, pady=12)

        tk.Button(btn_frame, text="Save", command=self._save,
                  bg=ACCENT, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"),
                  padx=18, pady=5, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancel", command=self.destroy,
                  bg=SURFACE, fg=FG, relief="flat",
                  font=("Segoe UI", 10),
                  padx=18, pady=5, cursor="hand2").pack(side="left", padx=6)

    def _save(self):
        v = self.vars
        pt.OLLAMA_HOST  = v["ollama_host"].get().strip()
        pt.OLLAMA_MODEL = v["ollama_model"].get().strip()
        pt.DB_CONFIG.update({
            "host":     v["db_host"].get().strip(),
            "port":     int(v["db_port"].get().strip() or "5432"),
            "dbname":   v["db_name"].get().strip(),
            "user":     v["db_user"].get().strip(),
            "password": v["db_pass"].get().strip(),
        })
        self.parent.log("  ✅  Settings saved.", "green")
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────
class PantryApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("🥦 Pantry Tracker")
        self.root.configure(bg=BG)
        self.root.geometry("960x640")
        self.root.minsize(760, 520)
        self.toggle_var = tk.BooleanVar(value=False)
        self.model = Model(lang="en-us")
        self._build_styles()
        self._build_ui()
        self._init_db()

    # ── styles ────────────────────────────────────────────────────────────────
    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Treeview",
                         background=SURFACE,
                         fieldbackground=SURFACE,
                         foreground=FG,
                         rowheight=26,
                         bordercolor=BORDER,
                         relief="flat",
                         font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
                         background=ACCENT,
                         foreground="white",
                         font=("Segoe UI", 10, "bold"),
                         relief="flat")
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.configure("Vertical.TScrollbar",
                         background=SURFACE,
                         troughcolor=BG,
                         arrowcolor=FG_DIM)

    # ── UI layout ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── top bar ───────────────────────────────────────────────────────────
        top = tk.Frame(self.root, bg=BG, pady=8)
        top.pack(fill="x", padx=14)

        tk.Label(top, text="🥦  Pantry Tracker",
                 bg=BG, fg=FG, font=("Segoe UI", 16, "bold")).pack(side="left")

        tk.Button(top, text="⚙  Settings",
                  command=self._open_settings,
                  bg=SURFACE, fg=FG, relief="flat",
                  font=("Segoe UI", 9), padx=10, pady=4,
                  cursor="hand2").pack(side="right", padx=(4, 0))

        tk.Button(top, text="🔄  Reset DB",
                  command=self._reset_db,
                  bg=SURFACE, fg=RED, relief="flat",
                  font=("Segoe UI", 9), padx=10, pady=4,
                  cursor="hand2").pack(side="right", padx=(4, 0))

        tk.Button(top, text="↺  Refresh",
                  command=self._refresh_table,
                  bg=SURFACE, fg=CYAN, relief="flat",
                  font=("Segoe UI", 9), padx=10, pady=4,
                  cursor="hand2").pack(side="right", padx=(4, 0))

        # ── separator ─────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=14)

        # ── main pane (inventory left, log right) ─────────────────────────────
        pane = tk.PanedWindow(self.root, orient="horizontal",
                              bg=BG, sashrelief="flat",
                              sashwidth=6, bd=0)
        pane.pack(fill="both", expand=True, padx=14, pady=10)

        # left panel — inventory table
        left = tk.Frame(pane, bg=BG)
        pane.add(left, minsize=320)

        tk.Label(left, text="📦  Inventory",
                 bg=BG, fg=FG, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", pady=(0, 6))

        tree_frame = tk.Frame(left, bg=BORDER, bd=1, relief="flat")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name", "qty", "updated"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("name",    text="Item")
        self.tree.heading("qty",     text="Qty (kg)")
        self.tree.heading("updated", text="Last Updated")
        self.tree.column("name",    width=140, anchor="w")
        self.tree.column("qty",     width=90,  anchor="e")
        self.tree.column("updated", width=140, anchor="center")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # alternating row colours
        self.tree.tag_configure("odd",  background=SURFACE)
        self.tree.tag_configure("even", background="#222235")
        self.tree.tag_configure("low",  background="#3a2a2a")  # low stock

        # right panel — log + input
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=340)

        tk.Label(right, text="📋  Activity Log",
                 bg=BG, fg=FG, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", pady=(0, 6))

        log_frame = tk.Frame(right, bg=BORDER, bd=1)
        log_frame.pack(fill="both", expand=True)

        self.log_box = scrolledtext.ScrolledText(
            log_frame,
            bg=SURFACE, fg=FG,
            font=("Consolas", 9),
            relief="flat", bd=0,
            state="disabled",
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=1, pady=1)

        # coloured tags for log lines
        self.log_box.tag_configure("green",  foreground=GREEN)
        self.log_box.tag_configure("red",    foreground=RED)
        self.log_box.tag_configure("yellow", foreground=YELLOW)
        self.log_box.tag_configure("cyan",   foreground=CYAN)
        self.log_box.tag_configure("normal", foreground=FG)
        self.log_box.tag_configure("dim",    foreground=FG_DIM)
        self.log_box.tag_configure("header", foreground=ACCENT,
                                   font=("Consolas", 9, "bold"))

        # ── input area ────────────────────────────────────────────────────────
        inp_outer = tk.Frame(self.root, bg=BG)
        inp_outer.pack(fill="x", padx=14, pady=(0, 12))

        tk.Label(inp_outer, text="Tell me what happened:",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w")

        inp_row = tk.Frame(inp_outer, bg=BG)
        inp_row.pack(fill="x", pady=(4, 0))

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            inp_row,
            textvariable=self.entry_var,
            bg=SURFACE, fg=FG,
            insertbackground=FG,
            font=("Segoe UI", 11),
            relief="flat", bd=6,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _e: self._submit())

        self.submit_btn = tk.Button(
            inp_row,
            text="Send  ↵",
            command=self._submit,
            bg=ACCENT, fg="white",
            activebackground=ACCENT_HOV,
            activeforeground="white",
            relief="flat",
            font=("Segoe UI", 11, "bold"),
            padx=18, pady=6,
            cursor="hand2",
        )
        self.microphone_btn = tk.Checkbutton(
            inp_row,
            text="🎤",
            variable=self.toggle_var,
            command=self._toggle,
            indicatoron=False,
            bg=SURFACE,
            fg=FG,
            activebackground=ACCENT_HOV,
            activeforeground="white",
            selectcolor=SURFACE,   # Prevent white flash
            relief="flat",
            padx=10,
            pady=6,
        )
        
        self.microphone_btn.pack(side="left",padx=(8,0))
        self.submit_btn.pack(side="left", padx=(8, 0))

        # ── status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Initialising…")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 8), anchor="w").pack(
            fill="x", padx=16, pady=(0, 4))

    # ── DB init ───────────────────────────────────────────────────────────────
    def _init_db(self):
        def _worker():
            try:
                pt.setup_db()
                self.log("  ✅  Connected to database.", "green")
                self.log(f"  🤖  Model : {pt.OLLAMA_MODEL}  @  {pt.OLLAMA_HOST}", "dim")
                self._refresh_table()
                self._set_status("Ready")
            except Exception as exc:
                self.log(f"  ❌  DB error: {exc}", "red")
                self._set_status(f"DB error: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    # ── submit handler ────────────────────────────────────────────────────────

    def _toggle(self):
        if self.toggle_var.get():
            self.microphone_btn.config(
                bg=GREEN,
                fg="black",
                activebackground=GREEN,
                activeforeground="black",
            )

            threading.Thread(
                target=self._microphoneInput,
                daemon=True
            ).start()

        else:
            self.microphone_btn.config(
                bg=SURFACE,
                fg=FG,
                activebackground=ACCENT_HOV,
                activeforeground="white",
            )

    def _microphoneInput(self):
        model = Model(lang="en-us")
        recognizer = KaldiRecognizer(self.model, SAMPLE_RATE)

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=audio_callback,
            blocksize=BLOCK_SIZE,
            dtype="int16",
        )

        with stream:
            while self.toggle_var.get():

                chunk = audio_queue.get()
                audio_bytes = bytes(chunk)

                if recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")

                    if text:
                        self.root.after(
                            0,
                            lambda t=text: self.entry_var.set(
                                self.entry_var.get() + " " + t
                            )
                        )
    def _submit(self):
        text = self.entry_var.get().strip()
        if not text:
            return

        self.entry_var.set("")
        self._set_busy(True)
        self.log(f"\n> {text}", "header")

        def _worker():
            try:
                # 1. call LLM
                self._set_status("Asking LLM…")
                raw = pt.query_ollama(text)

                # 2. parse
                ops = pt.parse_llm_response(raw)
                if not ops:
                    self.log("  ℹ️  No operations detected.", "yellow")
                    self._set_status("Ready")
                    self._set_busy(False)
                    return

                self.log(f"  📝  {len(ops)} operation(s) detected:", "dim")
                for op in ops:
                    sign = "+" if op["delta_kg"] >= 0 else ""
                    dated = f"  [{op['purchased_at']}]" if op["purchased_at"] else ""
                    self.log(f"      • {op['name']}: {sign}{op['delta_kg']:.3f} kg{dated}", "dim")

                # 3. apply
                self._set_status("Updating database…")
                results = pt.apply_operations(ops)
                for msg in results:
                    self.log(msg, _tag_color(msg))

                # 4. refresh table
                self._refresh_table()
                self._set_status("Ready")

            except RuntimeError as exc:
                self.log(f"  ❌  {exc}", "red")
                self._set_status("Error — see log")
            except (ValueError, json.JSONDecodeError) as exc:
                self.log(f"  ❌  Parse error: {exc}", "red")
                self._set_status("Error — see log")
            except Exception as exc:
                self.log(f"  ❌  Unexpected error: {exc}", "red")
                self._set_status("Error — see log")
            finally:
                self._set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    # ── inventory table refresh ───────────────────────────────────────────────
    def _refresh_table(self):
        try:
            rows = pt.list_inventory()
        except Exception as exc:
            self.log(f"  ❌  Cannot load inventory: {exc}", "red")
            return

        def _update():
            for item in self.tree.get_children():
                self.tree.delete(item)
            for i, r in enumerate(rows):
                qty = float(r["quantity_kg"])
                ts  = r["last_updated"].strftime("%Y-%m-%d %H:%M")
                tag = "low" if qty == 0 else ("odd" if i % 2 == 0 else "even")
                self.tree.insert("", "end",
                                 values=(r["name"], f"{qty:.3f}", ts),
                                 tags=(tag,))
            count = len(rows)
            self._set_status(f"Ready — {count} item{'s' if count != 1 else ''} in inventory")

        self.root.after(0, _update)

    # ── helpers ───────────────────────────────────────────────────────────────
    def log(self, text: str, tag: str = "normal"):
        def _append():
            self.log_box.configure(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            if text.startswith("\n"):
                self.log_box.insert("end", "\n")
                clean = text.lstrip("\n")
            else:
                clean = text
            self.log_box.insert("end", f"[{ts}] {clean}\n", tag)
            self.log_box.configure(state="disabled")
            self.log_box.see("end")
        self.root.after(0, _append)

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        cursor = "watch" if busy else "hand2"
        self.root.after(0, lambda: (
            self.submit_btn.configure(state=state, cursor=cursor),
            self.entry.configure(state=state),
        ))

    def _open_settings(self):
        SettingsDialog(self)

    def _reset_db(self):
        if messagebox.askyesno(
            "Reset Database",
            "This will DELETE all inventory data.\n\nAre you sure?",
            icon="warning",
        ):
            def _worker():
                try:
                    pt.reset_db()
                    self.log("  🔄  Database reset.", "yellow")
                    self._refresh_table()
                except Exception as exc:
                    self.log(f"  ❌  Reset failed: {exc}", "red")
            threading.Thread(target=_worker, daemon=True).start()

    # ── run ───────────────────────────────────────────────────────────────────
    def run(self):
        self.entry.focus_set()
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    PantryApp().run()
