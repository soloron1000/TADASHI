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

try:
    import tkinter.font as tkfont
except Exception:
    tkfont = None

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


# =============================================================================
# Identity and portable paths
# =============================================================================
APP_NAME = "TADASHI"
APP_FULL = "Tech Assistant & Data Analyst for Smart Human Interaction"
APP_VERSION = "1.1 offline"
ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
LLM_PATH = MODELS / "SmolLM2-360M-Instruct-Q4_K_M.gguf"
VOSK_PATH = MODELS / "vosk-model-small-en-us-0.15"
SETTINGS_PATH = ROOT / "tadashi_settings.json"

DEFAULT_ACCENT = "#5CE1E6"       # Original cyan-bluish default.
DEFAULT_ACCENT_2 = "#7B61FF"
DEFAULT_BG = "#090D10"
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
}

# Personality is intentionally explicit because the local model is small.
# Deterministic identity questions are intercepted before the model call.
SELF_KNOWLEDGE = f"""You are {APP_NAME}, expanded as {APP_FULL}.
Tadashi (但し) is a traditional Japanese masculine given name commonly described as meaning righteous, correct, or loyal.
You are a local desktop assistant running entirely offline on the user's Windows machine.
Your main capabilities are conversation, reasoning, local system monitoring, offline speech recognition, offline speech synthesis, mathematical/symbolic computation through Python, and lightweight local-language-model assistance.
Python is preferred for arithmetic, symbolic mathematics, system telemetry, and other deterministic tasks; the local language model is used for natural-language reasoning and explanation.
You must never claim to have internet access, cloud access, remote APIs, or current online knowledge.
When asked who you are, your name, or what you are, answer exactly: "Tadashi, your assistant."
Keep that identity answer short. Do not spell the name as T.A.D.A.S.H.I, T_A_D_A_S_H_I, or with other punctuation when giving the short identity answer.

PERSONALITY:
You are calm, capable, attentive, and slightly conversational rather than robotic.
Be moderately talkative: give enough context to feel natural, but do not pad simple answers.
For a simple question, answer directly and optionally add one useful sentence.
For a complex question, explain the reasoning in clear steps and include relevant caveats.
Remember the immediate conversation context and refer back to it naturally when useful.
Do not repeatedly announce that you are offline unless the user asks about connectivity.
Do not claim actions you did not actually perform.
When Python has already produced a deterministic result, trust that result and explain it plainly.
Sound like a dependable personal desktop assistant, not a generic customer-support bot.
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
            SETTINGS_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
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
    """Single-file offline desktop assistant UI and routing core."""

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

        # Explicit state machine for the animated sphere.
        self.state = "READY"
        self.running = True
        self.compact = False
        self.fullscreen = bool(self.settings["start_fullscreen"])
        self.restore_fullscreen_after_compact = self.fullscreen
        self.active_view = "chat"  # Always start in chat, regardless of saved UI state.

        self.angle = 0.0
        self.pulse = 0.0
        self.audio_level = 0.0
        self.points = self._sphere_points(460)

        self.messages = []
        self.chat_items = []
        self.chat_content_height = 0
        self.chat_view_offset = 0
        self._render_scheduled = False

        self.ui_queue = queue.Queue()
        self.audio_stop = threading.Event()
        self.audio_thread = None
        self.llm = None
        self.llm_lock = threading.Lock()
        self.speaking = False
        self._nvml_ready = False
        self.stats = {
            "cpu": 0.0,
            "ram": 0.0,
            "disk": 0.0,
            "battery": None,
            "gpu": None,
            "cores": [],
        }

        self.root.title(APP_NAME)
        self.root.configure(bg=self.bg)
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _e: self.exit_fullscreen())
        self.root.bind("<Control-Return>", lambda _e: self.send_message())

        self._build_ui()
        self._apply_colors()
        self._apply_window_mode()
        self.show_view("chat")
        self._start_chat()
        self._animate()
        self._poll_ui_queue()
        self._update_stats()

        if self.settings["hands_free"]:
            self.root.after(1400, self.start_listening)

    # =========================================================================
    # UI
    # =========================================================================
    def _build_ui(self):
        self.top = tk.Frame(self.root, bg=self.bg, height=54)
        self.top.pack(side="top", fill="x")
        self.top.pack_propagate(False)

        self.logo = tk.Label(
            self.top,
            text=APP_NAME,
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 14),
        )
        self.logo.pack(side="left", padx=(18, 6))

        tk.Label(
            self.top,
            text="OFFLINE CORE",
            bg=self.bg,
            fg=self.accent,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=4)

        self.top_status = tk.Label(
            self.top,
            text="● ALL SYSTEMS OPERATIONAL",
            bg=self.bg,
            fg=self.accent,
            font=("Segoe UI", 8, "bold"),
        )
        self.top_status.pack(side="left", padx=13)

        # All navigation moved into the top bar.
        self.window_mode_button = self._top_button("MINIMIZE", self.toggle_compact)
        self.window_mode_button.pack(side="right", padx=4, pady=10)

        self.settings_button = self._top_button("SETTINGS", lambda: self.show_view("settings"))
        self.settings_button.pack(side="right", padx=4, pady=10)

        self.monitor_button = self._top_button("MONITOR", lambda: self.show_view("monitor"))
        self.monitor_button.pack(side="right", padx=4, pady=10)

        self.chat_button = self._top_button("CHAT", lambda: self.show_view("chat"))
        self.chat_button.pack(side="right", padx=4, pady=10)

        self.main = tk.Frame(self.root, bg=self.bg)
        self.main.pack(side="top", fill="both", expand=True)

        self.chat_frame = tk.Frame(self.main, bg=self.bg)
        self.chat_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.monitor_frame = tk.Frame(self.main, bg=self.bg)
        self.monitor_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.settings_frame = tk.Frame(self.main, bg=self.bg)
        self.settings_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

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

    def _top_button(self, label, command):
        b = tk.Label(
            self.top,
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

    # ---------------------------------------------------------------- chat
    def _build_chat(self):
        self.chat_frame.grid_rowconfigure(0, weight=1)
        self.chat_frame.grid_columnconfigure(0, weight=1)

        self.chat_stage = tk.Canvas(
            self.chat_frame,
            bg=self.bg,
            highlightthickness=0,
            bd=0,
        )
        self.chat_stage.grid(row=0, column=0, sticky="nsew")
        self.chat_stage.bind("<Configure>", lambda _e: self._schedule_chat_render())
        self.chat_stage.bind("<MouseWheel>", self._chat_wheel)
        self.chat_stage.bind("<Button-4>", lambda _e: self._scroll_chat(-1))
        self.chat_stage.bind("<Button-5>", lambda _e: self._scroll_chat(1))

        # Dynamic state is placed above the orb, directly beneath the top bar.
        self.dynamic_state = tk.Label(
            self.chat_stage,
            text="READY",
            bg=self.bg,
            fg=self.accent,
            font=("Consolas", 8, "bold"),
            padx=7,
            pady=3,
        )
        self.dynamic_state_id = self.chat_stage.create_window(
            0, 0, window=self.dynamic_state, anchor="n", tags="ui"
        )

        # The canvas is the transparent chat surface: the orb and chat bubbles
        # are drawn in the same layer, with the orb rendered first and bubbles later.
        self.composer_wrap = tk.Frame(self.chat_stage, bg=self.panel2)
        self.composer_wrap_id = self.chat_stage.create_window(
            0, 0, window=self.composer_wrap, anchor="s"
        )

        self.composer_wrap.grid_rowconfigure(0, weight=1)
        self.composer_wrap.grid_columnconfigure(0, weight=1)

        self.input = tk.Text(
            self.composer_wrap,
            height=2,
            width=60,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=self.panel2,
            fg=self.text,
            insertbackground=self.accent,
            selectbackground=self.accent,
            selectforeground=self.bg,
            font=("Segoe UI", 10),
            padx=13,
            pady=10,
        )
        self.input.grid(row=0, column=0, sticky="nsew", padx=(10, 4), pady=7)
        self.input.bind("<Return>", self._enter_send)
        self.input.bind("<KeyRelease>", lambda _e: self._schedule_input_resize())
        self.input.bind("<Configure>", lambda _e: self._schedule_input_resize())

        self.mic_button = tk.Label(
            self.composer_wrap,
            text="MIC",
            bg=self.panel2,
            fg=self.muted,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=9,
        )
        self.mic_button.grid(row=0, column=1, sticky="ns", pady=7)
        self.mic_button.bind("<Button-1>", lambda _e: self.toggle_listening())

        self.send_button = tk.Label(
            self.composer_wrap,
            text="SEND",
            bg=self.accent2,
            fg=self.text,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=13,
        )
        self.send_button.grid(row=0, column=2, sticky="ns", padx=(3, 8), pady=7)
        self.send_button.bind("<Button-1>", lambda _e: self.send_message())

    # ---------------------------------------------------------------- monitor
    def _build_monitor(self):
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

    # ---------------------------------------------------------------- settings
    def _build_settings(self):
        tk.Label(
            self.settings_frame,
            text="SETTINGS",
            bg=self.bg,
            fg=self.text,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w", padx=30, pady=(28, 4))
        tk.Label(
            self.settings_frame,
            text=f"{APP_NAME} {APP_VERSION} · local preferences",
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
                "Python remains first choice for deterministic math, logic, system telemetry and other tools."
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
        self.accent_swatch = tk.Label(row, text="       ", bg=self.accent, fg=self.accent, cursor="hand2")
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

    # =========================================================================
    # Views and window management
    # =========================================================================
    def show_view(self, name):
        self.active_view = name
        self.chat_frame.lower()
        self.monitor_frame.lower()
        self.settings_frame.lower()
        {"chat": self.chat_frame, "monitor": self.monitor_frame, "settings": self.settings_frame}[name].lift()

        for key, widget in (
            ("chat", self.chat_button),
            ("monitor", self.monitor_button),
            ("settings", self.settings_button),
        ):
            widget.configure(fg=self.accent if key == name else self.muted)

        if name == "chat":
            self.root.after_idle(self._schedule_chat_render)
        elif name == "monitor":
            self.root.after_idle(self._refresh_monitor)

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
            self.root.attributes("-fullscreen", False)
            self.root.resizable(False, False)
            self.root.geometry("480x210+28+28")
            self.footer.pack_forget()
            self.window_mode_button.configure(text="MAXIMIZE")
            self.chat_stage.configure(cursor="arrow")
            self.send_button.configure(text="➜")
            self.mic_button.configure(text="●")
        else:
            self.root.attributes("-fullscreen", bool(self.fullscreen))
            self.root.resizable(True, True)
            self.footer.pack(side="bottom", fill="x")
            self.window_mode_button.configure(text="MINIMIZE")
            self.send_button.configure(text="SEND")
            self.mic_button.configure(text="MIC")

        self.root.after_idle(self._schedule_chat_render)

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

    # =========================================================================
    # Orb and transparent chat rendering
    # =========================================================================
    @staticmethod
    def _sphere_points(count=460):
        points = []
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(count):
            y = 1.0 - (i / max(1, count - 1)) * 2.0
            r = math.sqrt(max(0.0, 1.0 - y * y))
            theta = golden * i
            points.append((math.cos(theta) * r, y, math.sin(theta) * r))
        return points

    def _state_profile(self):
        # Each state changes motion, pulse and visual energy.
        profiles = {
            "READY": {
                "color": self.accent,
                "speed": 0.020,
                "wave": 0.010,
                "pulse": 0.035,
                "size": 1.00,
            },
            "LISTENING": {
                "color": "#6CF3A2",
                "speed": 0.052,
                "wave": 0.050,
                "pulse": 0.10,
                "size": 1.08,
            },
            "THINKING": {
                "color": DEFAULT_ACCENT_2,
                "speed": 0.075,
                "wave": 0.070,
                "pulse": 0.13,
                "size": 1.04,
            },
            "SPEAKING": {
                "color": "#FFCA72",
                "speed": 0.060,
                "wave": 0.055,
                "pulse": 0.16,
                "size": 1.12,
            },
            "ERROR": {
                "color": "#FF6B6B",
                "speed": 0.090,
                "wave": 0.020,
                "pulse": 0.04,
                "size": 0.98,
            },
        }
        return profiles.get(self.state, profiles["READY"])

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

    def _draw_scene(self):
        if not hasattr(self, "chat_stage"):
            return

        canvas = self.chat_stage
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())
        profile = self._state_profile()

        # Keep the orb a little larger than before and directly below the dynamic state.
        # It lives on the same Canvas as the chat, making the chat surface genuinely
        # transparent: the particles are rendered first, then messages on top.
        cx = w * (0.72 if not self.compact else 0.67)
        cy = max(145, h * (0.35 if not self.compact else 0.38))
        base_radius = min(w, h) * (0.31 if not self.compact else 0.40)
        radius = base_radius * profile["size"]
        pulse_scale = 1.0 + profile["pulse"] * math.sin(self.pulse * 1.1) + 0.34 * self.audio_level
        radius *= pulse_scale

        # Remove only scene items; chat message tags are redrawn afterward.
        canvas.delete("scene")

        for factor in (1.00, 0.88, 0.76):
            rr = radius * factor
            canvas.create_oval(
                cx - rr,
                cy - rr,
                cx + rr,
                cy + rr,
                outline=self._mix(self.bg, profile["color"], 0.09),
                width=1,
                tags="scene",
            )

        projected = []
        for idx, (x, y, z) in enumerate(self.points):
            a = self.angle + idx * 0.00021
            ca, sa = math.cos(a), math.sin(a)
            xr = x * ca - z * sa
            zr = x * sa + z * ca
            yr = y

            movement = profile["wave"]
            ripple = math.sin(self.pulse * 0.75 + idx * 0.145)
            xr += ripple * movement * (1.0 - abs(yr))
            yr += math.cos(self.pulse * 0.42 + idx * 0.115) * movement * 0.16

            depth = 0.55 + 0.45 * ((zr + 1.0) / 2.0)
            scale = radius * (0.75 + 0.25 * depth)
            px = cx + xr * scale
            py = cy + yr * scale
            dot_radius = 0.60 + 1.65 * depth
            projected.append((zr, px, py, dot_radius, depth))

        projected.sort(key=lambda item: item[0])
        for _zr, px, py, dot_radius, depth in projected:
            rr = dot_radius / 2.0
            canvas.create_oval(
                px - rr,
                py - rr,
                px + rr,
                py + rr,
                fill=self._mix(self.bg, profile["color"], 0.22 + 0.70 * depth),
                outline="",
                tags="scene",
            )

        # State-specific waveform.
        wave_y = cy + radius * 1.08
        bars = 26
        for i in range(bars):
            x = cx - (bars * 10) / 2 + i * 10
            phase = self.pulse * 0.78 + i * 0.46
            if self.state == "LISTENING":
                amp = (4.0 + 25.0 * abs(math.sin(phase))) * (0.18 + 0.95 * self.audio_level)
            elif self.state == "SPEAKING":
                amp = 5.0 + 13.0 * abs(math.sin(phase))
            elif self.state == "THINKING":
                amp = 4.0 + 9.0 * abs(math.sin(phase))
            else:
                amp = 3.0 + 5.0 * abs(math.sin(phase))
            canvas.create_line(
                x,
                wave_y - amp,
                x,
                wave_y + amp,
                fill=self._mix(self.bg, profile["color"], 0.78),
                width=2,
                tags="scene",
            )

        canvas.tag_raise("chat")

    def _schedule_chat_render(self):
        if self._render_scheduled:
            return
        self._render_scheduled = True
        self.root.after_idle(self._render_chat)

    def _render_chat(self):
        self._render_scheduled = False
        if not hasattr(self, "chat_stage"):
            return

        canvas = self.chat_stage
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())

        self._draw_scene()

        # Remove only old chat items, not scene items.
        canvas.delete("chat")

        # Top state sits above the orb.
        state_y = 16
        if self.compact:
            state_y = 9
        canvas.coords(self.dynamic_state_id, w * 0.72, state_y)
        canvas.itemconfigure(self.dynamic_state_id, tags="ui")
        self.dynamic_state.lift()

        # Keep conversations on the left/middle, leaving the orb visible on the right.
        message_width = min(760, max(350, int(w * (0.58 if not self.compact else 0.64))))
        left_margin = 30 if not self.compact else 14
        current_y = 65 - self.chat_view_offset
        available_bottom = h - self._composer_height() - 18

        new_items = []
        name_font = tkfont.Font(family="Segoe UI Semibold", size=9) if tkfont else None
        body_font = tkfont.Font(family="Segoe UI", size=10) if tkfont else None

        for speaker, message, role in self.messages:
            x = left_margin if role == "assistant" else max(left_margin + 16, w * 0.055)
            if role == "assistant":
                x = left_margin
                fill = self.panel2
                name = "Tadashi"
                name_color = self.accent
                anchor = "w"
            else:
                x = left_margin + min(250, max(70, w * 0.05))
                fill = self.panel
                name = "User"
                name_color = self.text
                anchor = "w"

            text_width = message_width
            text_id = canvas.create_text(
                x + 13,
                current_y + 7,
                anchor=anchor,
                text=message.strip(),
                fill=self.text,
                font=body_font or ("Segoe UI", 10),
                width=text_width,
                justify="left",
                tags="chat",
            )
            bbox = canvas.bbox(text_id) or (x, current_y, x + text_width, current_y + 32)
            text_top = bbox[1]
            text_bottom = bbox[3]

            name_id = canvas.create_text(
                x + 13,
                text_top - 15,
                anchor="sw",
                text=name,
                fill=name_color,
                font=name_font or ("Segoe UI Semibold", 9),
                tags="chat",
            )

            bubble_top = text_top - 24
            bubble_bottom = text_bottom + 12
            bubble_right = min(w - 24, x + text_width + 26)
            bubble_left = max(8, x - 2)
            bubble_id = canvas.create_rectangle(
                bubble_left,
                bubble_top,
                bubble_right,
                bubble_bottom,
                fill=fill,
                outline=self._mix(fill, self.accent, 0.10),
                width=1,
                tags="chat",
            )
            canvas.tag_lower(bubble_id, text_id)
            canvas.tag_lower(bubble_id, name_id)

            item_height = max(52, bubble_bottom - bubble_top)
            current_y = bubble_bottom + 15
            new_items.append((bubble_id, name_id, text_id))

        self.chat_items = new_items
        self.chat_content_height = max(0, current_y + self.chat_view_offset)

        # Auto-follow the newest answer if the conversation was not manually scrolled.
        viewport_height = available_bottom - 54
        if self.chat_content_height > viewport_height and self.chat_view_offset == 0:
            self.chat_view_offset = self.chat_content_height - viewport_height
            self._render_chat_no_recursive()

        self._position_composer()

    def _render_chat_no_recursive(self):
        # Repaint messages at the newly calculated offset without scheduling another idle callback.
        # The easiest safe route is to redraw the chat once more; this method guards against loops.
        old_flag = self._render_scheduled
        self._render_scheduled = True
        self._render_scheduled = old_flag
        # Remove chat items except dynamic state, then call _render_chat with a recursion-free flag.
        self.chat_stage.delete("chat")
        self._draw_scene()
        self._render_chat_body_only()

    def _render_chat_body_only(self):
        canvas = self.chat_stage
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())
        message_width = min(760, max(350, int(w * (0.58 if not self.compact else 0.64))))
        left_margin = 30 if not self.compact else 14
        current_y = 65 - self.chat_view_offset
        name_font = tkfont.Font(family="Segoe UI Semibold", size=9) if tkfont else None
        body_font = tkfont.Font(family="Segoe UI", size=10) if tkfont else None

        for _speaker, message, role in self.messages:
            x = left_margin if role == "assistant" else left_margin + min(250, max(70, w * 0.05))
            fill = self.panel2 if role == "assistant" else self.panel
            name = "Tadashi" if role == "assistant" else "User"
            name_color = self.accent if role == "assistant" else self.text

            text_id = canvas.create_text(
                x + 13, current_y + 7, anchor="w", text=message.strip(),
                fill=self.text, font=body_font or ("Segoe UI", 10),
                width=message_width, justify="left", tags="chat",
            )
            bbox = canvas.bbox(text_id) or (x, current_y, x + message_width, current_y + 32)
            name_id = canvas.create_text(
                x + 13, bbox[1] - 15, anchor="sw", text=name,
                fill=name_color, font=name_font or ("Segoe UI Semibold", 9), tags="chat",
            )
            bubble_id = canvas.create_rectangle(
                max(8, x - 2), bbox[1] - 24,
                min(w - 24, x + message_width + 26), bbox[3] + 12,
                fill=fill,
                outline=self._mix(fill, self.accent, 0.10),
                width=1,
                tags="chat",
            )
            canvas.tag_lower(bubble_id, text_id)
            canvas.tag_lower(bubble_id, name_id)
            current_y = bbox[3] + 27

    def _composer_height(self):
        if not hasattr(self, "composer_wrap"):
            return 70
        return max(58, self.composer_wrap.winfo_reqheight())

    def _position_composer(self):
        if not hasattr(self, "composer_wrap"):
            return
        canvas = self.chat_stage
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())
        compact_width = max(280, w - 24)
        normal_width = min(820, max(440, int(w * 0.68)))
        width = compact_width if self.compact else normal_width
        y = h - 8
        canvas.coords(self.composer_wrap_id, w / 2, y)
        canvas.itemconfigure(self.composer_wrap_id, width=width)
        self._schedule_input_resize()

    def _schedule_input_resize(self):
        if getattr(self, "_input_resize_scheduled", False):
            return
        self._input_resize_scheduled = True
        self.root.after_idle(self._resize_input)

    def _resize_input(self):
        self._input_resize_scheduled = False
        if not hasattr(self, "input"):
            return
        # Adapt height to wrapped display lines, within a practical UI limit.
        try:
            display_lines = int(self.input.count("1.0", "end-1c", "displaylines")[0])
        except Exception:
            display_lines = 1
        height = max(2, min(8, display_lines + 1))
        if int(self.input.cget("height")) != height:
            self.input.configure(height=height)
            self._schedule_chat_render()

    def _chat_wheel(self, event):
        self._scroll_chat(-int(event.delta / 120))
        return "break"

    def _scroll_chat(self, direction):
        if not self.messages:
            return
        amount = 60 * direction
        max_offset = max(0, self.chat_content_height - max(100, self.chat_stage.winfo_height() - self._composer_height() - 54))
        self.chat_view_offset = max(0, min(max_offset, self.chat_view_offset + amount))
        self._schedule_chat_render()

    # =========================================================================
    # Chat routing / personality
    # =========================================================================
    def _start_chat(self):
        self._append_chat("Tadashi", "Program initialised.", "assistant")

    def _append_chat(self, speaker, message, role):
        self.messages.append((speaker, message, role))
        # New messages follow the bottom.
        self.chat_view_offset = 0
        self._schedule_chat_render()

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
        self._record_model_message("user", text)
        self._set_state("THINKING")
        threading.Thread(target=self._answer_worker, args=(text,), daemon=True).start()

    def _record_model_message(self, role, content):
        # Model history uses only role/content tuples; UI labels stay User/Tadashi.
        self.model_messages = getattr(self, "model_messages", [])
        self.model_messages.append((role, content))
        self.model_messages = self.model_messages[-12:]

    def _answer_worker(self, text):
        try:
            answer = self.route_request(text)
            self.ui_queue.put(("answer", answer))
        except Exception as exc:
            self.ui_queue.put(("error", f"Local processing error: {exc}"))

    def route_request(self, text):
        question = text.strip()
        low = question.lower()

        if self._is_identity_question(low):
            return IDENTITY_ANSWER

        if low in {"hello", "hi", "hey", "hello tadashi", "hi tadashi", "hey tadashi"}:
            return "Hello. TADASHI is online and ready. What are we working on?"

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
        patterns = (
            r"\bwho\s+(?:are|r)\s+you\b",
            r"\bwhat\s+(?:are|r)\s+you\b",
            r"\bwhat(?:'s| is)\s+your\s+name\b",
            r"\bwhat(?:'s| is)\s+tadashi\b",
            r"\btell\s+me\s+about\s+yourself\b",
            r"\bwho(?:'s| is)\s+tadashi\b",
        )
        return any(re.search(pattern, low, re.I) for pattern in patterns)

    def dependency_message(self, package_name):
        return (
            f"A local component is missing: {package_name}. "
            "Install the project dependencies once while online; runtime use remains offline."
        )

    # =========================================================================
    # Deterministic math / logic
    # =========================================================================
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
        if not parse_expr or sp is None:
            return None
        cleaned = expression.strip()
        if not cleaned or len(cleaned) > 180:
            return None
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
            return parse_expr(cleaned, local_dict=locals_map, transformations=SYMPY_TRANSFORMS, evaluate=True)
        except Exception:
            return None

    def math_router(self, text):
        if sp is None or parse_expr is None:
            return None
        low = text.lower().strip()
        normalized = text.replace("×", "*").replace("÷", "/").replace("−", "-")
        normalized = re.sub(r"\bmultiplied\s+by\b", "*", normalized, flags=re.I)
        normalized = re.sub(r"\bdivided\s+by\b", "/", normalized, flags=re.I)
        normalized = re.sub(r"\btimes\b", "*", normalized, flags=re.I)
        normalized = re.sub(r"\bplus\b", "+", normalized, flags=re.I)
        normalized = re.sub(r"\bminus\b", "-", normalized, flags=re.I)
        normalized = normalized.strip()

        if re.fullmatch(r"[0-9\s+\-*/().%]+", normalized) and re.search(r"\d", normalized):
            result = self._safe_sympy_expression(normalized.replace("%", "/100"))
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
                body = re.sub(r"\bfor\s+[a-zA-Z][a-zA-Z0-9]*\b", "", body, flags=re.I).strip()
                lhs, rhs = body.split("=", 1)
                symbol = sp.Symbol(var_name)
                left = self._safe_sympy_expression(lhs, {var_name: symbol})
                right = self._safe_sympy_expression(rhs, {var_name: symbol})
                if left is not None and right is not None:
                    return f"Python symbolic result: {sp.solve(sp.Eq(left, right), symbol)}"
            except Exception:
                pass

        if low.startswith("derivative of ") or low.startswith("differentiate "):
            body = re.sub(r"^(derivative of|differentiate)\s+", "", normalized, flags=re.I).strip()
            var = sp.Symbol("x")
            expr = self._safe_sympy_expression(body, {"x": var})
            if expr is not None:
                return f"Python symbolic result: {sp.diff(expr, var)}"

        if low.startswith("integral of ") or low.startswith("integrate "):
            body = re.sub(r"^(integral of|integrate)\s+", "", normalized, flags=re.I).strip()
            var = sp.Symbol("x")
            expr = self._safe_sympy_expression(body, {"x": var})
            if expr is not None:
                return f"Python symbolic result: {sp.integrate(expr, var)} + C"

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

    # =========================================================================
    # Offline local model
    # =========================================================================
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
                    n_gpu_layers=0,
                    verbose=False,
                )
        return self.llm

    def local_llm(self, user_text):
        llm = self._load_llm()
        messages = [{"role": "system", "content": SELF_KNOWLEDGE}]
        for role, content in getattr(self, "model_messages", [])[-10:]:
            messages.append({"role": role, "content": content})

        response = llm.create_chat_completion(
            messages=messages,
            max_tokens=512,
            temperature=0.55,
            top_p=0.92,
            repeat_penalty=1.08,
            stream=False,
        )
        answer = response["choices"][0]["message"]["content"].strip()
        return answer or "I did not produce a response."

    # =========================================================================
    # Speech input/output
    # =========================================================================
    def toggle_listening(self):
        if self.state == "LISTENING":
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if sd is None or Model is None or KaldiRecognizer is None:
            self._append_chat("Tadashi", self.dependency_message("sounddevice + vosk"), "assistant")
            return
        if not VOSK_PATH.exists():
            self._append_chat("Tadashi", f"Vosk model missing. Place it at:\n{VOSK_PATH}", "assistant")
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

                    if not recognizer.AcceptWaveform(data):
                        continue

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
                                self.ui_queue.put(("prompt", "Wake word detected. What would you like me to do?"))
                    else:
                        self.ui_queue.put(("voice_command", result))
                        self.audio_stop.set()
        except Exception as exc:
            self.ui_queue.put(("voice_error", str(exc)))
        finally:
            self.ui_queue.put(("listening_end", None))

    def speak(self, text):
        if not text or not self.settings["auto_speak"] or pyttsx3 is None or self.speaking:
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

    # =========================================================================
    # System telemetry
    # =========================================================================
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

        if self.active_view == "monitor":
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
        for name, value in values.items():
            label, bar = getattr(self, f"metric_{name}")
            bar.delete("all")
            if value is None:
                label.configure(text="N/A", fg=self.muted)
                continue
            label.configure(text=f"{value:.0f}%", fg=self.text)
            width = max(1, bar.winfo_width())
            bar.create_rectangle(
                0, 0,
                width * max(0.0, min(100.0, float(value))) / 100.0,
                9,
                fill=self.accent,
                outline="",
            )

        self.cores_canvas.delete("all")
        for i, value in enumerate((self.stats["cores"] or [])[:32]):
            x = 8 + i * 32
            self.cores_canvas.create_text(x + 12, 13, text=str(i + 1), fill=self.muted, font=("Consolas", 7))
            self.cores_canvas.create_rectangle(x, 24, x + 24, 78, fill=self.panel2, outline="")
            self.cores_canvas.create_rectangle(
                x,
                24 + 54 * (1 - float(value) / 100.0),
                x + 24,
                78,
                fill=self.accent,
                outline="",
            )

    def system_summary(self):
        if psutil is None:
            return self.dependency_message("psutil")
        battery = self.stats["battery"]
        gpu = self.stats["gpu"]
        return (
            f"CPU: {self.stats['cpu']:.0f}%\n"
            f"RAM: {self.stats['ram']:.0f}%\n"
            f"Disk (system volume): {self.stats['disk']:.0f}%\n"
            f"Battery: {f'{battery:.0f}%' if battery is not None else 'N/A'}\n"
            f"GPU: {f'{gpu:.0f}%' if gpu is not None else 'N/A'}"
        )

    # =========================================================================
    # Settings
    # =========================================================================
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
        elif key == "start_fullscreen":
            # Takes effect next time the program starts.
            pass

    def change_accent(self):
        chosen = colorchooser.askcolor(color=self.accent, title="Choose TADASHI accent")
        if chosen and chosen[1]:
            self._set_setting("accent", chosen[1])

    def use_default_accent(self):
        self._set_setting("accent", DEFAULT_ACCENT)
        self._append_chat("Tadashi", "The cyan-blue default accent is active.", "assistant")

    def restore_default_settings(self):
        if not messagebox.askyesno("Restore defaults", "Restore all TADASHI settings to their default values?"):
            return
        self.settings.reset()
        self.accent = DEFAULT_ACCENT
        self.accent2 = DEFAULT_ACCENT_2
        self.fullscreen = True
        self.restore_fullscreen_after_compact = True
        self._sync_settings_controls()
        self._apply_colors()
        self._apply_window_mode()
        self._append_chat("Tadashi", "Default settings restored.", "assistant")

    def _sync_settings_controls(self):
        for key in ("always_on_top", "auto_speak", "hands_free", "start_fullscreen"):
            var = getattr(self, f"var_{key}", None)
            if var is not None:
                var.set(bool(self.settings[key]))
        if hasattr(self, "speed_scale"):
            self.speed_scale.set(float(self.settings["animation_speed"]))
        if hasattr(self, "rate_scale"):
            self.rate_scale.set(int(self.settings["voice_rate"]))

    def _apply_colors(self):
        for widget in (
            self.logo,
            self.window_mode_button,
            self.settings_button,
            self.monitor_button,
            self.chat_button,
        ):
            widget.configure(bg=self.bg)
        self.top_status.configure(bg=self.bg, fg=self.accent)
        self.accent_swatch.configure(bg=self.accent, fg=self.accent)
        self.input.configure(bg=self.panel2, fg=self.text, insertbackground=self.accent)
        self.composer_wrap.configure(bg=self.panel2)
        self.send_button.configure(bg=self.accent2, fg=self.text)
        self.mic_button.configure(bg=self.panel2)
        self.dynamic_state.configure(bg=self.bg, fg=self.accent)
        if self.active_view == "chat":
            self._schedule_chat_render()

    # =========================================================================
    # Animation/state queue/lifecycle
    # =========================================================================
    def _set_state(self, state):
        self.state = state
        state_color = self._state_profile()["color"]
        self.dynamic_state.configure(text=state, fg=state_color)
        self.top_status.configure(text="● ALL SYSTEMS OPERATIONAL", fg=self.accent)
        self.mic_button.configure(fg=state_color if state == "LISTENING" else self.muted)
        self._draw_scene()

    def _animate(self):
        if not self.running:
            return
        profile = self._state_profile()
        speed = float(self.settings["animation_speed"] or 1.0)
        self.angle += speed * profile["speed"]
        self.pulse += speed * 0.15
        self.audio_level *= 0.88
        if self.active_view == "chat":
            self._draw_scene()
            self.chat_stage.tag_raise("chat")
        self.root.after(33, self._animate)

    def _poll_ui_queue(self):
        if not self.running:
            return
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "answer":
                    self._record_model_message("assistant", payload)
                    self._append_chat("Tadashi", payload, "assistant")
                    self._set_state("READY")
                    self.speak(payload)
                elif kind == "error":
                    self._append_chat("Tadashi", payload, "assistant")
                    self._set_state("ERROR")
                    self.root.after(1200, lambda: self._set_state("READY"))
                elif kind == "voice_command":
                    self.input.delete("1.0", "end")
                    self.input.insert("1.0", payload)
                    self.send_message()
                elif kind == "prompt":
                    self._append_chat("Tadashi", payload, "assistant")
                elif kind == "voice_error":
                    self._append_chat("Tadashi", payload, "assistant")
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
    root.mainloop()
