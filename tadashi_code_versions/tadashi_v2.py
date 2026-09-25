import json
import math
import os
import queue
import re
import threading
import time
from array import array
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, messagebox

# Optional local packages. The UI can still open when one of these is missing.
try:
    import psutil
except Exception:
    psutil = None

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )
    from sympy.logic.boolalg import simplify_logic
    SYMPY_TRANSFORMS = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
    )
except Exception:
    sp = None
    parse_expr = None
    simplify_logic = None
    SYMPY_TRANSFORMS = ()

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from vosk import KaldiRecognizer, Model
except Exception:
    KaldiRecognizer = None
    Model = None

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import pynvml
except Exception:
    pynvml = None

try:
    from llama_cpp import Llama
except Exception:
    Llama = None


# -----------------------------------------------------------------------------
# Application identity / paths
# -----------------------------------------------------------------------------
APP_NAME = "TADASHI"
APP_FULL = "Tech Assistant & Data Analyst for Smart Human Interaction"
APP_VERSION = "1.1 offline"
ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
LLM_PATH = MODELS / "SmolLM2-360M-Instruct-Q4_K_M.gguf"
VOSK_PATH = MODELS / "vosk-model-small-en-us-0.15"
SETTINGS_PATH = ROOT / "tadashi_settings.json"

DEFAULT_ACCENT = "#5CE1E6"       # Keep the current cyan-bluish accent as default.
DEFAULT_ACCENT_2 = "#7B61FF"
DEFAULT_BG = "#0B0F12"
DEFAULT_PANEL = "#10161B"
DEFAULT_PANEL_2 = "#151C22"
DEFAULT_TEXT = "#EAF2F3"
DEFAULT_MUTED = "#83929B"

DEFAULT_SETTINGS = {
    "accent": DEFAULT_ACCENT,
    "accent2": DEFAULT_ACCENT_2,
    "animation_speed": 1.0,
    "always_on_top": False,
    "auto_speak": True,
    "hands_free": False,
    "start_fullscreen": True,
    "voice_rate": 185,
    "llm_threads": max(2, (os.cpu_count() or 4) - 1),
    "sidebar_collapsed": False,
}

# This is the model's durable self-description. The UI also intercepts identity
# questions so the exact short answer is deterministic rather than model-dependent.
SELF_KNOWLEDGE = f"""You are {APP_NAME}, expanded as {APP_FULL}.
Tadashi (但し) is a traditional Japanese masculine given name commonly described as meaning righteous, correct, or loyal.
You are a local desktop assistant running entirely offline on the user's Windows machine.
Your main capabilities are conversation, reasoning, local system monitoring, offline speech recognition, offline speech synthesis, mathematical/symbolic computation through Python, and lightweight local-language-model assistance.
Python is preferred for arithmetic, symbolic mathematics, system telemetry, and other deterministic tasks; the local language model is used for natural-language reasoning and explanation.
You must never claim to have internet access, cloud access, remote APIs, or current online knowledge.
When asked who you are, your name, or what you are, answer exactly: "Tadashi, your assistant."
Keep that identity answer short. Do not spell the name as T.A.D.A.S.H.I, T_A_D_A_S_H_I, or with other punctuation when giving the short identity answer.
"""

IDENTITY_ANSWER = "Tadashi, your assistant."


class Settings:
    def __init__(self):
        self.data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        try:
            if SETTINGS_PATH.exists():
                saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.data.update(saved)
        except Exception:
            pass

    def save(self):
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self.data, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def reset(self):
        self.data = dict(DEFAULT_SETTINGS)
        self.save()

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()


class TADASHI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings()

        self.bg = DEFAULT_BG
        self.panel = DEFAULT_PANEL
        self.panel2 = DEFAULT_PANEL_2
        self.text = DEFAULT_TEXT
        self.muted = DEFAULT_MUTED
        self.accent = self.settings["accent"] or DEFAULT_ACCENT
        self.accent2 = self.settings["accent2"] or DEFAULT_ACCENT_2

        self.state = "READY"
        self.running = True
        self.compact = False
        self.sidebar_collapsed = bool(self.settings["sidebar_collapsed"])
        self.fullscreen = bool(self.settings["start_fullscreen"])
        self.restore_fullscreen_after_compact = self.fullscreen

        self.angle = 0.0
        self.pulse = 0.0
        self.audio_level = 0.0
        self.messages = []
        self.ui_queue = queue.Queue()
        self.audio_stop = threading.Event()
        self.audio_thread = None
        self.llm = None
        self.llm_lock = threading.Lock()
        self.speaking = False
        self.stats = {
            "cpu": 0.0,
            "ram": 0.0,
            "disk": 0.0,
            "battery": None,
            "gpu": None,
            "cores": [],
        }
        self._nvml_ready = False
        self.points = self._sphere_points()
        self.sidebar_pack_opts = {"side": "left", "fill": "y"}

        self.root.title(APP_NAME)
        self.root.configure(bg=self.bg)
        self.root.minsize(880, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _e: self.exit_fullscreen())
        self.root.bind("<Control-Return>", lambda _e: self.send_message())

        self._build_ui()
        self._apply_colors()
        self._apply_window_mode()
        self._start_chat()
        self._animate()
        self._poll_ui_queue()
        self._update_stats()

    # ------------------------------------------------------------------ UI build
    def _build_ui(self):
        self.top = tk.Frame(self.root, bg=self.bg, height=54)
        self.top.pack(side="top", fill="x")
        self.top.pack_propagate(False)

        self.menu_button = self._top_button("☰", self.toggle_sidebar, "Open/close sidebar")
        self.menu_button.pack(side="left", padx=(12, 4), pady=10)

        self.logo = tk.Label(
            self.top,
            text=APP_NAME,
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 14),
        )
        self.logo.pack(side="left", padx=(3, 6))

        tk.Label(
            self.top,
            text="OFFLINE CORE",
            bg=self.bg,
            fg=self.accent,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=5)

        # Static top status as requested; transient state is shown inside the chat.
        self.top_status = tk.Label(
            self.top,
            text="● ALL SYSTEMS OPERATIONAL",
            bg=self.bg,
            fg=self.accent,
            font=("Segoe UI", 8, "bold"),
        )
        self.top_status.pack(side="left", padx=14)

        self.window_mode_button = self._top_button("MINIMIZE", self.toggle_compact, "Compact window")
        self.window_mode_button.pack(side="right", padx=5, pady=10)

        self.settings_button = self._top_button("SETTINGS", lambda: self.show_view("settings"), "Settings")
        self.settings_button.pack(side="right", padx=5, pady=10)

        self.monitor_button = self._top_button("MONITOR", lambda: self.show_view("monitor"), "System monitor")
        self.monitor_button.pack(side="right", padx=5, pady=10)

        self.chat_button = self._top_button("CHAT", lambda: self.show_view("chat"), "Chat")
        self.chat_button.pack(side="right", padx=5, pady=10)

        self.main = tk.Frame(self.root, bg=self.bg)
        self.main.pack(side="top", fill="both", expand=True)

        self.sidebar = tk.Frame(self.main, bg=self.panel, width=226)
        self.sidebar.pack(**self.sidebar_pack_opts)
        self.sidebar.pack_propagate(False)

        self.view = tk.Frame(self.main, bg=self.bg)
        self.view.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_chat()
        self._build_monitor()
        self._build_settings()

        self.footer = tk.Frame(self.root, bg=self.bg, height=30)
        self.footer.pack(side="bottom", fill="x")
        self.footer.pack_propagate(False)
        tk.Label(
            self.footer,
            text="F11 fullscreen  ·  Esc exit fullscreen  ·  Ctrl+Enter send  ·  voice is offline",
            bg=self.bg,
            fg=self.muted,
            font=("Segoe UI", 8),
        ).pack(side="left", padx=18)

    def _top_button(self, label, command, _tip=None):
        b = tk.Label(
            self.top if hasattr(self, "top") else self.root,
            text=label,
            bg=self.bg,
            fg=self.muted,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=5,
        )
        b.bind("<Button-1>", lambda _e: command())
        b.bind("<Enter>", lambda _e: b.configure(fg=self.accent))
        b.bind("<Leave>", lambda _e: b.configure(fg=self.muted))
        return b

    def _build_sidebar(self):
        self.sidebar_title = tk.Label(
            self.sidebar,
            text=APP_NAME,
            bg=self.panel,
            fg=self.accent,
            font=("Segoe UI", 21, "bold"),
        )
        self.sidebar_title.pack(anchor="w", padx=18, pady=(23, 1))

        self.sidebar_subtitle = tk.Label(
            self.sidebar,
            text="LOCAL INTELLIGENCE CONSOLE",
            bg=self.panel,
            fg=self.muted,
            font=("Segoe UI", 7, "bold"),
        )
        self.sidebar_subtitle.pack(anchor="w", padx=19, pady=(0, 18))

        self.sidebar_status = tk.Label(
            self.sidebar,
            text="SYSTEM NOMINAL",
            bg=self.panel,
            fg=self.accent,
            font=("Consolas", 9, "bold"),
        )
        self.sidebar_status.pack(anchor="w", padx=19, pady=(0, 14))

        self.sidebar_nav = {}
        self.sidebar_nav_data = (
            ("chat", "◉", "CHAT"),
            ("monitor", "▦", "SYSTEM MONITOR"),
            ("settings", "⚙", "SETTINGS"),
        )
        for key, icon, label in self.sidebar_nav_data:
            self._sidebar_button(key, icon, label)

        self.sidebar_separator = tk.Frame(self.sidebar, bg=self.panel2, height=1)
        self.sidebar_separator.pack(fill="x", padx=18, pady=20)

        self.identity_heading = tk.Label(
            self.sidebar,
            text="IDENTITY",
            bg=self.panel,
            fg=self.muted,
            font=("Segoe UI", 8, "bold"),
        )
        self.identity_heading.pack(anchor="w", padx=19)

        self.identity_text = tk.Label(
            self.sidebar,
            text=f"{APP_NAME}\n{APP_FULL}",
            bg=self.panel,
            fg=self.text,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        )
        self.identity_text.pack(anchor="w", padx=19, pady=(6, 18))

        self.mic_indicator = tk.Label(
            self.sidebar,
            text="◉ MIC OFF",
            bg=self.panel,
            fg=self.muted,
            font=("Consolas", 9, "bold"),
        )
        self.mic_indicator.pack(anchor="w", padx=19)

    def _sidebar_button(self, key, icon, label):
        b = tk.Label(
            self.sidebar,
            text=f"{icon}   {label}",
            bg=self.panel,
            fg=self.muted,
            anchor="w",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=9,
        )
        b.pack(fill="x", padx=9, pady=2)
        b.bind("<Button-1>", lambda _e, k=key: self.show_view(k))
        b.bind("<Enter>", lambda _e, w=b: w.configure(fg=self.text, bg=self.panel2))
        b.bind("<Leave>", lambda _e, k=key, w=b: self._sidebar_leave(k, w))
        self.sidebar_nav[key] = b

    def _sidebar_leave(self, key, widget):
        widget.configure(
            bg=self.panel2 if getattr(self, "active_view", "chat") == key else self.panel,
            fg=self.text if getattr(self, "active_view", "chat") == key else self.muted,
        )

    # --------------------------------------------------------------- chat layout
    def _build_chat(self):
        self.chat_frame = tk.Frame(self.view, bg=self.bg)
        self.chat_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.chat_frame.grid_rowconfigure(0, weight=1)
        self.chat_frame.grid_columnconfigure(0, weight=7)
        self.chat_frame.grid_columnconfigure(1, weight=3)

        # Normal AI-chat panel on the left.
        self.chat_panel = tk.Frame(self.chat_frame, bg=self.bg)
        self.chat_panel.grid(row=0, column=0, sticky="nsew", padx=(22, 8), pady=(14, 16))
        self.chat_panel.grid_rowconfigure(1, weight=1)
        self.chat_panel.grid_columnconfigure(0, weight=1)

        self.chat_header = tk.Frame(self.chat_panel, bg=self.bg)
        self.chat_header.grid(row=0, column=0, sticky="ew", pady=(3, 10))
        tk.Label(
            self.chat_header,
            text="TADASHI",
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 21),
        ).pack(side="left")
        self.state_label = tk.Label(
            self.chat_header,
            text="READY",
            bg=self.bg,
            fg=self.accent,
            font=("Consolas", 8, "bold"),
            padx=10,
        )
        self.state_label.pack(side="right", pady=5)

        # The scrollable text area is kept visually simple and ChatGPT-like.
        history_wrap = tk.Frame(self.chat_panel, bg=self.panel)
        history_wrap.grid(row=1, column=0, sticky="nsew")
        history_wrap.grid_rowconfigure(0, weight=1)
        history_wrap.grid_columnconfigure(0, weight=1)

        self.chat_history = tk.Text(
            history_wrap,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=self.panel,
            fg=self.text,
            insertbackground=self.accent,
            selectbackground=self.accent,
            selectforeground=self.bg,
            font=("Segoe UI", 10),
            padx=18,
            pady=15,
            spacing1=1,
            spacing3=8,
        )
        self.chat_history.grid(row=0, column=0, sticky="nsew")
        self.chat_history.configure(state="disabled")
        self.chat_history.tag_configure("user_name", foreground=self.accent, font=("Segoe UI Semibold", 9))
        self.chat_history.tag_configure("tadashi_name", foreground=self.text, font=("Segoe UI Semibold", 9))
        self.chat_history.tag_configure("body", foreground=self.text, font=("Segoe UI", 10))
        self.chat_history.tag_configure("meta", foreground=self.muted, font=("Segoe UI", 8))

        # Normal bottom composer.
        composer = tk.Frame(self.chat_panel, bg=self.panel2)
        composer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        composer.grid_columnconfigure(0, weight=1)

        self.input = tk.Text(
            composer,
            height=3,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=self.panel2,
            fg=self.text,
            insertbackground=self.accent,
            font=("Segoe UI", 10),
            padx=13,
            pady=11,
        )
        self.input.grid(row=0, column=0, sticky="nsew", padx=(10, 2), pady=7)
        self.input.bind("<Return>", self._enter_send)

        self.mic_button = tk.Label(
            composer,
            text="MIC",
            bg=self.panel2,
            fg=self.muted,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=10,
        )
        self.mic_button.grid(row=0, column=1, sticky="ns", pady=7)
        self.mic_button.bind("<Button-1>", lambda _e: self.toggle_listening())

        self.send_button = tk.Label(
            composer,
            text="SEND",
            bg=self.accent2,
            fg=self.text,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=13,
        )
        self.send_button.grid(row=0, column=2, sticky="ns", padx=(4, 8), pady=7)
        self.send_button.bind("<Button-1>", lambda _e: self.send_message())

        # Orb on the right, slightly above centre and intentionally smaller.
        self.orb_panel = tk.Frame(self.chat_frame, bg=self.bg)
        self.orb_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 24), pady=(8, 16))
        self.orb_canvas = tk.Canvas(self.orb_panel, bg=self.bg, highlightthickness=0)
        self.orb_canvas.place(relx=0.5, rely=0.42, anchor="center", relwidth=0.92, relheight=0.74)
        self.orb_canvas.bind("<Configure>", lambda _e: self._draw_orb())

    # --------------------------------------------------------------- monitor
    def _build_monitor(self):
        self.monitor_frame = tk.Frame(self.view, bg=self.bg)
        self.monitor_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        tk.Label(
            self.monitor_frame,
            text="SYSTEM MONITOR",
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w", padx=30, pady=(28, 4))
        tk.Label(
            self.monitor_frame,
            text="Live local telemetry · no network calls",
            bg=self.bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=31, pady=(0, 18))

        cards = tk.Frame(self.monitor_frame, bg=self.bg)
        cards.pack(fill="x", padx=24, pady=4)
        for i, name in enumerate(("CPU", "RAM", "DISK", "BATTERY", "GPU")):
            card = tk.Frame(cards, bg=self.panel, height=126)
            card.grid(row=i // 2, column=i % 2, sticky="ew", padx=7, pady=7)
            card.grid_propagate(False)
            cards.columnconfigure(i % 2, weight=1)

            tk.Label(card, text=name, bg=self.panel, fg=self.muted, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(16, 3))
            value = tk.Label(card, text="--", bg=self.panel, fg=self.text, font=("Consolas", 23, "bold"))
            value.pack(anchor="w", padx=18)
            bar = tk.Canvas(card, bg=self.panel, height=9, highlightthickness=0)
            bar.pack(fill="x", padx=18, pady=(7, 14))
            setattr(self, f"metric_{name.lower()}", (value, bar))

        tk.Label(
            self.monitor_frame,
            text="CPU CORES",
            bg=self.bg,
            fg=self.muted,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=31, pady=(8, 5))
        self.cores_canvas = tk.Canvas(self.monitor_frame, height=94, bg=self.panel, highlightthickness=0)
        self.cores_canvas.pack(fill="x", padx=32, pady=(0, 25))

    # --------------------------------------------------------------- settings
    def _build_settings(self):
        self.settings_frame = tk.Frame(self.view, bg=self.bg)
        self.settings_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        tk.Label(
            self.settings_frame,
            text="SETTINGS",
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w", padx=30, pady=(28, 4))
        tk.Label(
            self.settings_frame,
            text=f"{APP_NAME} {APP_VERSION} · preferences are stored locally",
            bg=self.bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=31, pady=(0, 18))

        body = tk.Frame(self.settings_frame, bg=self.panel)
        body.pack(fill="x", padx=30, pady=6)

        self._setting_row(body, "UI ACCENT", self.change_accent)

        default_row = tk.Frame(body, bg=self.panel)
        default_row.pack(fill="x", padx=20, pady=7)
        tk.Label(default_row, text="Default UI accent", bg=self.panel, fg=self.text, font=("Segoe UI", 10)).pack(side="left")
        default_btn = tk.Label(
            default_row,
            text="USE CYAN-BLUE DEFAULT",
            bg=self.panel2,
            fg=self.accent,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=9,
            pady=6,
        )
        default_btn.pack(side="right")
        default_btn.bind("<Button-1>", lambda _e: self.use_default_accent())

        self._toggle_row(body, "Always on top", "always_on_top")
        self._toggle_row(body, "Auto-speak answers", "auto_speak")
        self._toggle_row(body, "Hands-free wake word: TADASHI", "hands_free")
        self._toggle_row(body, "Start in fullscreen", "start_fullscreen")
        self._toggle_row(body, "Collapsed sidebar on startup", "sidebar_collapsed")

        speed_row = tk.Frame(body, bg=self.panel)
        speed_row.pack(fill="x", padx=20, pady=(7, 2))
        tk.Label(speed_row, text="Animation speed", bg=self.panel, fg=self.text, font=("Segoe UI", 10)).pack(side="left")
        self.speed_scale = tk.Scale(
            speed_row,
            from_=0.4,
            to=2.0,
            resolution=0.1,
            orient="horizontal",
            showvalue=True,
            bg=self.panel,
            fg=self.text,
            troughcolor=self.panel2,
            highlightthickness=0,
            command=lambda v: self._set_setting("animation_speed", float(v)),
        )
        self.speed_scale.set(float(self.settings["animation_speed"]))
        self.speed_scale.pack(side="right", fill="x", expand=True, padx=20)

        rate_row = tk.Frame(body, bg=self.panel)
        rate_row.pack(fill="x", padx=20, pady=(2, 14))
        tk.Label(rate_row, text="Speech rate", bg=self.panel, fg=self.text, font=("Segoe UI", 10)).pack(side="left")
        self.rate_scale = tk.Scale(
            rate_row,
            from_=120,
            to=240,
            resolution=5,
            orient="horizontal",
            showvalue=True,
            bg=self.panel,
            fg=self.text,
            troughcolor=self.panel2,
            highlightthickness=0,
            command=lambda v: self._set_setting("voice_rate", int(float(v))),
        )
        self.rate_scale.set(int(self.settings["voice_rate"]))
        self.rate_scale.pack(side="right", fill="x", expand=True, padx=20)

        action_row = tk.Frame(self.settings_frame, bg=self.bg)
        action_row.pack(fill="x", padx=30, pady=(14, 6))
        self.reset_button = tk.Label(
            action_row,
            text="RESTORE DEFAULT SETTINGS",
            bg=self.panel2,
            fg=self.text,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=8,
        )
        self.reset_button.pack(side="left")
        self.reset_button.bind("<Button-1>", lambda _e: self.restore_default_settings())

        tk.Label(
            self.settings_frame,
            text=(
                "Default accent: cyan-bluish (#5CE1E6)\n"
                "Model: SmolLM2-360M-Instruct-Q4_K_M.gguf\n"
                "Speech recognition: Vosk small English · speech output: Windows SAPI via pyttsx3\n"
                "Math, logic, system telemetry and other deterministic operations are handled by Python where possible."
            ),
            bg=self.bg,
            fg=self.muted,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=31, pady=12)

    def _setting_row(self, parent, label, command):
        row = tk.Frame(parent, bg=self.panel)
        row.pack(fill="x", padx=20, pady=(17, 7))
        tk.Label(row, text=label, bg=self.panel, fg=self.text, font=("Segoe UI", 10)).pack(side="left")
        self.accent_swatch = tk.Label(
            row,
            text="       ",
            bg=self.accent,
            fg=self.accent,
            cursor="hand2",
        )
        self.accent_swatch.pack(side="right")
        self.accent_swatch.bind("<Button-1>", lambda _e: command())

    def _toggle_row(self, parent, label, key):
        row = tk.Frame(parent, bg=self.panel)
        row.pack(fill="x", padx=20, pady=5)
        tk.Label(row, text=label, bg=self.panel, fg=self.text, font=("Segoe UI", 10)).pack(side="left")
        var = tk.BooleanVar(value=bool(self.settings[key]))
        check = tk.Checkbutton(
            row,
            variable=var,
            onvalue=True,
            offvalue=False,
            bg=self.panel,
            fg=self.text,
            activebackground=self.panel,
            activeforeground=self.text,
            selectcolor=self.panel2,
            highlightthickness=0,
            command=lambda v=var, k=key: self._set_setting(k, bool(v.get())),
        )
        check.pack(side="right")
        setattr(self, f"var_{key}", var)

    # --------------------------------------------------------------- navigation
    def show_view(self, name):
        self.active_view = name
        for frame in (self.chat_frame, self.monitor_frame, self.settings_frame):
            frame.lower()
        {"chat": self.chat_frame, "monitor": self.monitor_frame, "settings": self.settings_frame}[name].lift()

        for key, widget in self.sidebar_nav.items():
            widget.configure(
                bg=self.panel2 if key == name else self.panel,
                fg=self.text if key == name else self.muted,
            )

    def toggle_sidebar(self):
        if self.compact:
            return
        self.sidebar_collapsed = not self.sidebar_collapsed
        self._apply_sidebar_mode()
        self.settings["sidebar_collapsed"] = self.sidebar_collapsed

    def _apply_sidebar_mode(self):
        if not hasattr(self, "sidebar"):
            return
        self.sidebar.pack_propagate(False)
        if self.sidebar_collapsed:
            self.sidebar.configure(width=66)
            self.sidebar_title.configure(text="T")
            self.sidebar_subtitle.pack_forget()
            self.sidebar_status.pack_forget()
            self.sidebar_separator.pack_forget()
            self.identity_heading.pack_forget()
            self.identity_text.pack_forget()
            self.mic_indicator.pack_forget()
            for key, icon, _label in self.sidebar_nav_data:
                w = self.sidebar_nav[key]
                w.configure(text=icon, anchor="center", padx=0)
        else:
            self.sidebar.configure(width=226)
            self.sidebar_title.configure(text=APP_NAME)
            self.sidebar_subtitle.pack(anchor="w", padx=19, pady=(0, 18), after=self.sidebar_title)
            self.sidebar_status.pack(anchor="w", padx=19, pady=(0, 14), after=self.sidebar_subtitle)
            for key, icon, label in self.sidebar_nav_data:
                w = self.sidebar_nav[key]
                w.configure(text=f"{icon}   {label}", anchor="w", padx=12)
            self.sidebar_separator.pack(fill="x", padx=18, pady=20, after=self.sidebar_nav["settings"])
            self.identity_heading.pack(anchor="w", padx=19, after=self.sidebar_separator)
            self.identity_text.pack(anchor="w", padx=19, pady=(6, 18), after=self.identity_heading)
            self.mic_indicator.pack(anchor="w", padx=19, after=self.identity_text)

    # ------------------------------------------------------------- window modes
    def toggle_compact(self):
        if self.compact:
            self.compact = False
            self.fullscreen = self.restore_fullscreen_after_compact
        else:
            self.restore_fullscreen_after_compact = self.fullscreen
            self.compact = True
            self.fullscreen = False
        self._apply_window_mode()

    def _apply_window_mode(self):
        try:
            self.root.attributes("-topmost", bool(self.settings["always_on_top"]))
        except Exception:
            pass

        if self.compact:
            self.root.overrideredirect(False)
            self.root.attributes("-fullscreen", False)
            self.root.geometry("470x190+28+28")
            self.root.resizable(False, False)
            self.footer.pack_forget()
            self.sidebar.pack_forget()
            self.window_mode_button.configure(text="MAXIMIZE")
            self.window_mode_button.configure(fg=self.accent)
            self.chat_panel.grid_configure(padx=(12, 10), pady=(8, 10))
            self.chat_frame.grid_columnconfigure(0, weight=8)
            self.chat_frame.grid_columnconfigure(1, weight=2)
            self.orb_canvas.place_configure(relx=0.50, rely=0.35, relwidth=1.10, relheight=0.80)
            self.input.configure(height=2)
            self.send_button.configure(text="➜")
            self.mic_button.configure(text="●")
        else:
            self.root.overrideredirect(False)
            self.root.attributes("-fullscreen", bool(self.fullscreen))
            self.root.resizable(True, True)
            self.footer.pack(side="bottom", fill="x")
            if not self.sidebar.winfo_ismapped():
                self.sidebar.pack(**self.sidebar_pack_opts)
            self.window_mode_button.configure(text="MINIMIZE")
            self.window_mode_button.configure(fg=self.muted)
            self.chat_panel.grid_configure(padx=(22, 8), pady=(14, 16))
            self.chat_frame.grid_columnconfigure(0, weight=7)
            self.chat_frame.grid_columnconfigure(1, weight=3)
            self.orb_canvas.place_configure(relx=0.50, rely=0.42, relwidth=0.92, relheight=0.74)
            self.input.configure(height=3)
            self.send_button.configure(text="SEND")
            self.mic_button.configure(text="MIC")
            self._apply_sidebar_mode()

    def toggle_fullscreen(self):
        if self.compact:
            self.toggle_compact()
        self.fullscreen = not self.fullscreen
        self._apply_window_mode()
        return "break"

    def exit_fullscreen(self):
        if self.fullscreen and not self.compact:
            self.fullscreen = False
            self._apply_window_mode()

    # ------------------------------------------------------------- animation
    @staticmethod
    def _sphere_points(count=360):
        points = []
        golden = math.pi * (3 - math.sqrt(5))
        for i in range(count):
            y = 1 - (i / max(1, count - 1)) * 2
            r = math.sqrt(max(0.0, 1 - y * y))
            theta = golden * i
            points.append((math.cos(theta) * r, y, math.sin(theta) * r))
        return points

    def _draw_orb(self):
        if not hasattr(self, "orb_canvas"):
            return
        canvas = self.orb_canvas
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())
        cx = w * 0.50
        cy = h * 0.43 if not self.compact else h * 0.39
        radius = min(w, h) * (0.245 if not self.compact else 0.31)
        canvas.delete("all")

        # Very subtle boundary rings; particles carry the visual identity.
        for factor in (0.98, 0.86, 0.74):
            rr = radius * factor
            canvas.create_oval(
                cx - rr, cy - rr, cx + rr, cy + rr,
                outline=self._mix(self.accent, self.bg, 0.91), width=1
            )

        speed = float(self.settings["animation_speed"] or 1.0)
        pulse = 1 + 0.05 * math.sin(self.pulse) + 0.28 * self.audio_level
        movement = {
            "READY": 0.010,
            "LISTENING": 0.037,
            "THINKING": 0.054,
            "SPEAKING": 0.042,
        }.get(self.state, 0.018)

        projected = []
        for idx, (x, y, z) in enumerate(self.points):
            a = self.angle + idx * 0.00025
            ca, sa = math.cos(a), math.sin(a)
            xr = x * ca - z * sa
            zr = x * sa + z * ca
            yr = y

            wave = math.sin(self.pulse * 0.72 + idx * 0.16) * movement
            xr += wave * (1 - abs(yr))
            yr += math.cos(self.pulse * 0.38 + idx * 0.11) * movement * 0.14

            depth = 0.55 + 0.45 * ((zr + 1) / 2)
            scale = radius * pulse * (0.76 + 0.24 * depth)
            px = cx + xr * scale
            py = cy + yr * scale
            dot_radius = 0.55 + 1.5 * depth
            projected.append((zr, px, py, dot_radius, depth))

        projected.sort(key=lambda item: item[0])
        for _zr, px, py, dot_radius, depth in projected:
            rr = dot_radius / 2
            canvas.create_oval(
                px - rr, py - rr, px + rr, py + rr,
                fill=self._particle_color(depth), outline=""
            )

        # Reactive voice/thinking pulse under the orb.
        if self.state in ("LISTENING", "SPEAKING", "THINKING"):
            base_y = cy + radius * 1.10
            for i in range(24):
                x = cx - 110 + i * 10
                phase = self.pulse * 0.70 + i * 0.42
                if self.state == "LISTENING":
                    amp = (4 + 24 * abs(math.sin(phase))) * (0.20 + 0.90 * self.audio_level)
                elif self.state == "SPEAKING":
                    amp = 5 + 12 * abs(math.sin(phase))
                else:
                    amp = 4 + 8 * abs(math.sin(phase))
                canvas.create_line(x, base_y - amp, x, base_y + amp, fill=self.accent, width=2)

    def _particle_color(self, depth):
        return self._mix(self.bg, self.accent, 0.22 + 0.67 * depth)

    @staticmethod
    def _mix(a, b, t):
        def rgb(value):
            value = value.lstrip("#")
            return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

        ar, ag, ab = rgb(a)
        br, bg, bb = rgb(b)
        t = max(0.0, min(1.0, t))
        return "#%02x%02x%02x" % (
            int(ar + (br - ar) * t),
            int(ag + (bg - ag) * t),
            int(ab + (bb - ab) * t),
        )

    def _animate(self):
        if not self.running:
            return
        speed = float(self.settings["animation_speed"] or 1.0)
        self.angle += speed * {
            "READY": 0.019,
            "LISTENING": 0.045,
            "THINKING": 0.065,
            "SPEAKING": 0.050,
        }.get(self.state, 0.025)
        self.pulse += speed * 0.14
        self.audio_level *= 0.88
        self._draw_orb()
        self.root.after(33, self._animate)

    # --------------------------------------------------------------- chat logic
    def _start_chat(self):
        self._append_chat("Tadashi", "Program initialised.", "tadashi")

    def _append_chat(self, speaker, message, role):
        self.chat_history.configure(state="normal")
        if role == "user":
            self.chat_history.insert("end", f"User\n", "user_name")
        else:
            self.chat_history.insert("end", f"Tadashi\n", "tadashi_name")
        self.chat_history.insert("end", f"{message.strip()}\n", "body")
        self.chat_history.see("end")
        self.chat_history.configure(state="disabled")

    def _enter_send(self, event):
        if event.state & 0x0001:  # Shift+Enter = newline.
            return None
        self.send_message()
        return "break"

    def send_message(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._append_chat("User", text, "user")
        self.messages.append(("user", text))
        self._set_state("THINKING")
        threading.Thread(target=self._answer_worker, args=(text,), daemon=True).start()

    def _answer_worker(self, text):
        try:
            answer = self.route_request(text)
            self.ui_queue.put(("answer", answer))
        except Exception as exc:
            self.ui_queue.put(("error", f"Local processing error: {exc}"))

    def route_request(self, text):
        question = text.strip()
        low = question.lower()

        # Identity gets an exact deterministic answer. This prevents formatting drift.
        if self._is_identity_question(low):
            return IDENTITY_ANSWER

        if low in {"time", "what time is it", "what is the time", "current time"}:
            return datetime.now().strftime("Local system time: %A, %d %B %Y, %H:%M:%S")

        if any(k in low for k in (
            "system status", "system usage", "cpu usage", "ram usage", "hardware status",
            "computer status", "system monitor",
        )):
            return self.system_summary()

        math_result = self.math_router(question)
        if math_result is not None:
            return math_result

        if Llama is None:
            return self.dependency_message("llama-cpp-python")
        if not LLM_PATH.exists():
            return (
                "The local language model is not installed. Place the GGUF file at:\n"
                f"{LLM_PATH}\n\n"
                "The rest of TADASHI can still run offline without it."
            )

        return self.local_llm(question)

    @staticmethod
    def _is_identity_question(low):
        # Deliberately broad enough to catch natural phrasing, but not normal chat.
        identity_patterns = (
            r"\bwho\s+(?:are|r)\s+you\b",
            r"\bwhat\s+(?:are|r)\s+you\b",
            r"\bwhat(?:'s| is)\s+your\s+name\b",
            r"\bwhat(?:'s| is)\s+tadashi\b",
            r"\btell\s+me\s+about\s+yourself\b",
            r"\bwho(?:'s| is)\s+tadashi\b",
        )
        return any(re.search(pattern, low, re.I) for pattern in identity_patterns)

    def dependency_message(self, package_name):
        return (
            f"A local component is missing: {package_name}. "
            "Install the project dependencies once while online; runtime use remains offline."
        )

    # -------------------------------------------------------------- math / logic
    @staticmethod
    def _allowed_symbol_names(expression):
        names = set(re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", expression))
        allowed = {
            "x", "y", "z", "t", "n", "a", "b", "c", "m", "k",
            "sin", "cos", "tan", "asin", "acos", "atan", "sqrt",
            "log", "ln", "exp", "abs", "pi", "E",
        }
        return names <= allowed

    @classmethod
    def _safe_sympy_expression(cls, expression, local_dict=None):
        if not parse_expr:
            return None
        cleaned = expression.strip()
        if not cleaned or len(cleaned) > 180:
            return None
        # Block syntax that is not needed for math/symbolic expressions.
        if any(bad in cleaned for bad in ("__", "[", "]", "{", "}", ";", ":", "'", '"', "\\")):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_+\-*/^().,\s]+", cleaned):
            return None
        if not cls._allowed_symbol_names(cleaned):
            return None
        locals_map = {
            "sin": sp.sin,
            "cos": sp.cos,
            "tan": sp.tan,
            "asin": sp.asin,
            "acos": sp.acos,
            "atan": sp.atan,
            "sqrt": sp.sqrt,
            "log": sp.log,
            "ln": sp.log,
            "exp": sp.exp,
            "abs": sp.Abs,
            "pi": sp.pi,
            "E": sp.E,
        }
        if local_dict:
            locals_map.update(local_dict)
        try:
            return parse_expr(
                cleaned,
                local_dict=locals_map,
                transformations=SYMPY_TRANSFORMS,
                evaluate=True,
            )
        except Exception:
            return None

    def math_router(self, text):
        if sp is None or parse_expr is None:
            return None
        low = text.lower().strip()
        normalized = (
            text.replace("×", "*")
            .replace("÷", "/")
            .replace("−", "-")
        )
        normalized = re.sub(r"\bmultiplied\s+by\b", "*", normalized, flags=re.I)
        normalized = re.sub(r"\bdivided\s+by\b", "/", normalized, flags=re.I)
        normalized = re.sub(r"\btimes\b", "*", normalized, flags=re.I)
        normalized = re.sub(r"\bplus\b", "+", normalized, flags=re.I)
        normalized = re.sub(r"\bminus\b", "-", normalized, flags=re.I)
        normalized = normalized.strip()

        if re.fullmatch(r"[0-9\s+\-*/().%]+", normalized) and re.search(r"\d", normalized):
            expression = normalized.replace("%", "/100")
            result = self._safe_sympy_expression(expression)
            if result is not None:
                return f"Python math result: {sp.N(result, 15)}"

        if any(k in low for k in ("calculate", "compute", "what is", "how much is")):
            candidate = re.sub(
                r"^(please\s+)?(calculate|compute|what is|how much is)\s*",
                "",
                normalized,
                flags=re.I,
            )
            if re.fullmatch(r"[0-9\s+\-*/().%]+", candidate):
                result = self._safe_sympy_expression(candidate.replace("%", "/100"))
                if result is not None:
                    return f"Python math result: {sp.N(result, 15)}"

        if low.startswith("solve ") and "=" in normalized:
            try:
                body = re.sub(r"^solve\s+", "", normalized, flags=re.I).strip()
                var_match = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z0-9]*)\b", body, flags=re.I)
                var_name = var_match.group(1) if var_match else "x"
                if var_name not in {"x", "y", "z", "t", "n", "a", "b", "c", "m", "k"}:
                    return None
                body = re.sub(r"\bfor\s+[a-zA-Z][a-zA-Z0-9]*\b", "", body, flags=re.I).strip()
                lhs, rhs = body.split("=", 1)
                symbol = sp.Symbol(var_name)
                left = self._safe_sympy_expression(lhs, {var_name: symbol})
                right = self._safe_sympy_expression(rhs, {var_name: symbol})
                if left is None or right is None:
                    return None
                return f"Python symbolic result: {sp.solve(sp.Eq(left, right), symbol)}"
            except Exception:
                pass

        if low.startswith("derivative of ") or low.startswith("differentiate "):
            try:
                body = re.sub(r"^(derivative of|differentiate)\s+", "", normalized, flags=re.I).strip()
                var = sp.Symbol("x")
                expr = self._safe_sympy_expression(body, {"x": var})
                if expr is not None:
                    return f"Python symbolic result: {sp.diff(expr, var)}"
            except Exception:
                pass

        if low.startswith("integral of ") or low.startswith("integrate "):
            try:
                body = re.sub(r"^(integral of|integrate)\s+", "", normalized, flags=re.I).strip()
                var = sp.Symbol("x")
                expr = self._safe_sympy_expression(body, {"x": var})
                if expr is not None:
                    return f"Python symbolic result: {sp.integrate(expr, var)} + C"
            except Exception:
                pass

        if re.search(r"\b(and|or|not)\b", low) and len(text) < 220:
            try:
                logic = re.sub(r"\bAND\b", "&", text, flags=re.I)
                logic = re.sub(r"\bOR\b", "|", logic, flags=re.I)
                logic = re.sub(r"\bNOT\b", "~", logic, flags=re.I)
                if not re.fullmatch(r"[A-Za-z\s&|~()]+", logic):
                    return None
                names = sorted(set(re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", logic)) - {"True", "False"})
                if any(len(name) > 8 for name in names):
                    return None
                local = {name: sp.Symbol(name) for name in names}
                local.update({"True": sp.true, "False": sp.false})
                expr = parse_expr(logic, local_dict=local, transformations=SYMPY_TRANSFORMS)
                return f"Python logic simplification: {simplify_logic(expr, force=True)}"
            except Exception:
                pass

        return None

    # ------------------------------------------------------------- local model
    def _load_llm(self):
        if self.llm is not None:
            return self.llm
        with self.llm_lock:
            if self.llm is None:
                self.llm = Llama(
                    model_path=str(LLM_PATH),
                    n_ctx=2048,
                    n_threads=int(self.settings["llm_threads"]),
                    n_batch=256,
                    n_gpu_layers=0,  # Keep it CPU-first and portable.
                    verbose=False,
                )
        return self.llm

    def local_llm(self, user_text):
        llm = self._load_llm()
        history = [{"role": "system", "content": SELF_KNOWLEDGE}]
        history.extend(
            {"role": role, "content": content}
            for role, content in self.messages[-8:]
        )
        response = llm.create_chat_completion(
            messages=history,
            max_tokens=384,
            temperature=0.3,
            top_p=0.9,
            stream=False,
        )
        answer = response["choices"][0]["message"]["content"].strip()
        return answer or "I did not produce a response."

    # ------------------------------------------------------------------ speech
    def toggle_listening(self):
        if self.state == "LISTENING":
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if sd is None or Model is None or KaldiRecognizer is None:
            self._append_chat("Tadashi", self.dependency_message("sounddevice + vosk"), "tadashi")
            return
        if not VOSK_PATH.exists():
            self._append_chat("Tadashi", f"Vosk model missing. Place it at:\n{VOSK_PATH}", "tadashi")
            return
        if self.audio_thread and self.audio_thread.is_alive():
            return
        self.audio_stop.clear()
        self._set_state("LISTENING")
        self.audio_thread = threading.Thread(target=self._audio_worker, daemon=True)
        self.audio_thread.start()

    def stop_listening(self):
        self.audio_stop.set()
        self._set_state("READY")

    def _audio_worker(self):
        audio_q = queue.Queue()
        try:
            model = Model(str(VOSK_PATH))
            recognizer = KaldiRecognizer(model, 16000)

            def callback(indata, _frames, _time_info, _status):
                raw = bytes(indata)
                audio_q.put(raw)
                try:
                    samples = array("h", raw)
                    if samples:
                        rms = math.sqrt(sum(v * v for v in samples) / len(samples)) / 32768.0
                        self.audio_level = min(1.0, rms * 7.0)
                except Exception:
                    pass

            with sd.RawInputStream(
                samplerate=16000,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while not self.audio_stop.is_set():
                    try:
                        data = audio_q.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if recognizer.AcceptWaveform(data):
                        try:
                            result = json.loads(recognizer.Result()).get("text", "").strip()
                        except Exception:
                            result = ""
                        if not result:
                            continue

                        if self.settings["hands_free"]:
                            lower = result.lower()
                            if re.search(r"\btadashi\b", lower):
                                command = re.sub(
                                    r"\btadashi\b[:,; ]*",
                                    "",
                                    result,
                                    count=1,
                                    flags=re.I,
                                ).strip()
                                if command:
                                    self.ui_queue.put(("voice_command", command))
                                    self.audio_stop.set()
                                else:
                                    self.ui_queue.put(("prompt", "Wake word detected. State your command."))
                        else:
                            self.ui_queue.put(("voice_command", result))
                            self.audio_stop.set()
        except Exception as exc:
            self.ui_queue.put(("voice_error", str(exc)))
        finally:
            self.ui_queue.put(("listening_end", None))

    def speak(self, text):
        if not text or not self.settings["auto_speak"] or pyttsx3 is None:
            return
        if self.speaking:
            return
        threading.Thread(target=self._tts_worker, args=(text,), daemon=True).start()

    def _tts_worker(self, text):
        self.speaking = True
        self.ui_queue.put(("state", "SPEAKING"))
        try:
            engine = pyttsx3.init("sapi5")
            engine.setProperty("rate", int(self.settings["voice_rate"]))
            engine.setProperty("volume", 0.9)
            engine.say(text[:2500])
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            self.ui_queue.put(("voice_error", f"TTS: {exc}"))
        finally:
            self.speaking = False
            self.ui_queue.put(("state", "READY"))
            if self.settings["hands_free"] and self.running:
                time.sleep(0.2)
                self.ui_queue.put(("restart_listening", None))

    # --------------------------------------------------------------- telemetry
    def _update_stats(self):
        if not self.running:
            return
        if psutil is not None:
            try:
                self.stats["cpu"] = float(psutil.cpu_percent(interval=None))
                self.stats["ram"] = float(psutil.virtual_memory().percent)
                self.stats["disk"] = float(psutil.disk_usage(os.path.abspath(os.sep)).percent)
                battery = psutil.sensors_battery()
                self.stats["battery"] = float(battery.percent) if battery else None
                self.stats["cores"] = list(psutil.cpu_percent(interval=None, percpu=True))
            except Exception:
                pass
        self.stats["gpu"] = self._gpu_load()
        if getattr(self, "active_view", "chat") == "monitor":
            self._refresh_monitor()
        self.root.after(1200, self._update_stats)

    def _gpu_load(self):
        if pynvml is None:
            return None
        try:
            if not self._nvml_ready:
                pynvml.nvmlInit()
                self._nvml_ready = True
            count = pynvml.nvmlDeviceGetCount()
            if count <= 0:
                return None
            values = []
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                values.append(float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu))
            return sum(values) / len(values) if values else None
        except Exception:
            return None

    def _refresh_monitor(self):
        values = {
            "cpu": self.stats["cpu"],
            "ram": self.stats["ram"],
            "disk": self.stats["disk"],
            "battery": self.stats["battery"],
            "gpu": self.stats["gpu"],
        }
        for name, val in values.items():
            value_label, bar = getattr(self, f"metric_{name}")
            bar.delete("all")
            if val is None:
                value_label.configure(text="N/A", fg=self.muted)
                continue
            value_label.configure(text=f"{val:.0f}%", fg=self.text)
            width = max(1, bar.winfo_width())
            bar.create_rectangle(
                0,
                0,
                width * max(0.0, min(100.0, float(val))) / 100.0,
                9,
                fill=self.accent,
                outline="",
            )

        self.cores_canvas.delete("all")
        for i, val in enumerate((self.stats["cores"] or [])[:32]):
            x = 8 + i * 32
            self.cores_canvas.create_text(
                x + 12, 13, text=str(i + 1), fill=self.muted, font=("Consolas", 7)
            )
            self.cores_canvas.create_rectangle(
                x, 24, x + 24, 78, fill=self.panel2, outline=""
            )
            self.cores_canvas.create_rectangle(
                x,
                24 + 54 * (1 - float(val) / 100),
                x + 24,
                78,
                fill=self.accent,
                outline="",
            )

    def system_summary(self):
        if psutil is None:
            return self.dependency_message("psutil")
        battery = self.stats["battery"]
        battery_text = f"{battery:.0f}%" if battery is not None else "N/A"
        gpu = self.stats["gpu"]
        gpu_text = f"{gpu:.0f}%" if gpu is not None else "N/A"
        return (
            f"CPU: {self.stats['cpu']:.0f}%\n"
            f"RAM: {self.stats['ram']:.0f}%\n"
            f"Disk (system volume): {self.stats['disk']:.0f}%\n"
            f"Battery: {battery_text}\n"
            f"GPU: {gpu_text}"
        )

    # ---------------------------------------------------------------- settings
    def _set_setting(self, key, value):
        self.settings[key] = value
        if key == "accent":
            self.accent = value or DEFAULT_ACCENT
            self._apply_colors()
        elif key == "always_on_top":
            try:
                self.root.attributes("-topmost", bool(value))
            except Exception:
                pass
        elif key == "sidebar_collapsed":
            self.sidebar_collapsed = bool(value)
            self._apply_sidebar_mode()

    def change_accent(self):
        chosen = colorchooser.askcolor(color=self.accent, title="Choose TADASHI accent")
        if chosen and chosen[1]:
            self._set_setting("accent", chosen[1])

    def use_default_accent(self):
        self._set_setting("accent", DEFAULT_ACCENT)
        self._append_chat("Tadashi", "Default UI accent restored.", "tadashi")

    def restore_default_settings(self):
        should_reset = messagebox.askyesno(
            "Restore defaults",
            "Restore all TADASHI settings to their default values?",
        )
        if not should_reset:
            return

        self.settings.reset()
        self.accent = DEFAULT_ACCENT
        self.accent2 = DEFAULT_ACCENT_2
        self.sidebar_collapsed = False
        self.fullscreen = True
        self.restore_fullscreen_after_compact = True
        self._sync_settings_controls()
        self._apply_colors()
        self._apply_window_mode()
        self._append_chat("Tadashi", "Default settings restored.", "tadashi")

    def _sync_settings_controls(self):
        for key in ("always_on_top", "auto_speak", "hands_free", "start_fullscreen", "sidebar_collapsed"):
            var = getattr(self, f"var_{key}", None)
            if var is not None:
                var.set(bool(self.settings[key]))
        if hasattr(self, "speed_scale"):
            self.speed_scale.set(float(self.settings["animation_speed"]))
        if hasattr(self, "rate_scale"):
            self.rate_scale.set(int(self.settings["voice_rate"]))

    def _apply_colors(self):
        widgets = (
            self.logo,
            self.menu_button,
            self.chat_button,
            self.monitor_button,
            self.settings_button,
            self.window_mode_button,
        )
        for widget in widgets:
            widget.configure(bg=self.bg)
        self.top_status.configure(bg=self.bg, fg=self.accent)
        self.logo.configure(fg=self.text)
        self.chat_history.configure(insertbackground=self.accent, selectbackground=self.accent)
        self.input.configure(insertbackground=self.accent)
        self.send_button.configure(bg=self.accent2)
        self.accent_swatch.configure(bg=self.accent, fg=self.accent)
        self._draw_orb()

    # ------------------------------------------------------------ queue/lifecycle
    def _set_state(self, state):
        self.state = state
        self.state_label.configure(text=state, fg=self.accent if state != "ERROR" else "#FF6B6B")
        status_text = {
            "READY": "SYSTEM NOMINAL",
            "LISTENING": "VOICE INPUT ACTIVE",
            "THINKING": "LOCAL INFERENCE",
            "SPEAKING": "AUDIO OUTPUT ACTIVE",
            "ERROR": "LOCAL PROCESS ERROR",
        }.get(state, state)
        self.sidebar_status.configure(text=status_text, fg=self.accent if state != "ERROR" else "#FF6B6B")
        self.mic_indicator.configure(
            text="◉ MIC ON" if state == "LISTENING" else "◉ MIC OFF",
            fg=self.accent if state == "LISTENING" else self.muted,
        )
        self._draw_orb()

    def _poll_ui_queue(self):
        if not self.running:
            return
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "answer":
                    self.messages.append(("assistant", payload))
                    self._append_chat("Tadashi", payload, "tadashi")
                    self._set_state("READY")
                    self.speak(payload)
                elif kind == "error":
                    self._append_chat("Tadashi", payload, "tadashi")
                    self._set_state("ERROR")
                    self.root.after(1200, lambda: self._set_state("READY"))
                elif kind == "voice_command":
                    self.input.delete("1.0", "end")
                    self.input.insert("1.0", payload)
                    self.send_message()
                elif kind == "prompt":
                    self._append_chat("Tadashi", payload, "tadashi")
                elif kind == "voice_error":
                    self._append_chat("Tadashi", payload, "tadashi")
                    self._set_state("READY")
                elif kind == "listening_end":
                    if self.state == "LISTENING":
                        self._set_state("READY")
                elif kind == "state":
                    self._set_state(payload)
                elif kind == "restart_listening":
                    if self.settings["hands_free"] and self.running:
                        self.start_listening()
        except queue.Empty:
            pass
        self.root.after(80, self._poll_ui_queue)

    def close(self):
        self.running = False
        self.audio_stop.set()
        if self._nvml_ready and pynvml is not None:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = TADASHI(root)
    if app.settings["hands_free"]:
        root.after(1200, app.start_listening)
    root.mainloop()
