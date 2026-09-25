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

# Optional/runtime dependencies. The UI still starts without the AI/audio packages
# so setup errors can be displayed cleanly instead of crashing immediately.
try:
    import psutil
except Exception:
    psutil = None

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        standard_transformations,
        implicit_multiplication_application,
        convert_xor,
        parse_expr,
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
    from vosk import Model, KaldiRecognizer
except Exception:
    Model = None
    KaldiRecognizer = None

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


APP_NAME = "TADASHI"
APP_FULL = "Tech Assistant & Data Analyst for Smart Human Interaction"
APP_VERSION = "1.1 offline"
ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
LLM_PATH = MODELS / "SmolLM2-360M-Instruct-Q4_K_M.gguf"
VOSK_PATH = MODELS / "vosk-model-small-en-us-0.15"
SETTINGS_PATH = ROOT / "tadashi_settings.json"

DEFAULT_SETTINGS = {
    "accent": "#5CE1E6",
    "accent2": "#7B61FF",
    "animation_speed": 1.0,
    "always_on_top": False,
    "auto_speak": True,
    "hands_free": False,
    "start_fullscreen": True,
    "voice_rate": 185,
    "llm_threads": max(2, (os.cpu_count() or 4) - 1),
}

SELF_KNOWLEDGE = f"""You are {APP_NAME}, expanded as {APP_FULL}.
Tadashi (但し) is a traditional Japanese masculine given name commonly described as meaning righteous, correct, or loyal.
You are a local desktop assistant running entirely offline on the user's Windows machine.
Your main capabilities are: conversation, reasoning, local system monitoring, offline speech recognition, offline speech synthesis, mathematical/symbolic computation through Python, and lightweight local-language-model assistance.
Python is preferred for arithmetic, symbolic math, system telemetry, and deterministic tasks; the local language model is used for natural-language reasoning and explanation.
You must never claim to have internet access, cloud access, remote APIs, or current online knowledge.
When asked what you are, describe yourself as TADASHI and state that you are your assistant.
"""


class Settings:
    def __init__(self):
        self.data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        try:
            if SETTINGS_PATH.exists():
                saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                self.data.update(saved)
        except Exception:
            pass

    def save(self):
        try:
            SETTINGS_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()


class TADASHI:
    def __init__(self, root):
        self.root = root
        self.settings = Settings()
        self.bg = "#0B0F12"
        self.panel = "#10161B"
        self.panel2 = "#151C22"
        self.text = "#EAF2F3"
        self.muted = "#83929B"
        self.accent = self.settings["accent"]
        self.accent2 = self.settings["accent2"]

        self.root.title(APP_NAME)
        self.root.configure(bg=self.bg)
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _e: self.exit_fullscreen())
        self.root.bind("<Control-Return>", lambda _e: self.send_message())

        self.state = "READY"
        self.running = True
        self.fullscreen = bool(self.settings["start_fullscreen"])
        self.compact = False
        self.angle = 0.0
        self.pulse = 0.0
        self.audio_level = 0.0
        self.transcript = ""
        self.messages = []
        self.conversation = []
        self.ui_queue = queue.Queue()
        self.voice_queue = queue.Queue()
        self.audio_stop = threading.Event()
        self.audio_thread = None
        self.llm = None
        self.llm_lock = threading.Lock()
        self.speaking = False
        self.last_tts = ""
        self.stats = {"cpu": 0, "ram": 0, "disk": 0, "battery": None, "gpu": None, "cores": []}
        self.drag_x = 0
        self.drag_y = 0

        self._build_ui()
        self._apply_window_mode()
        self._seed_chat()
        self._animate()
        self._poll_ui_queue()
        self._update_stats()

    # -------------------- UI --------------------
    def _build_ui(self):
        self.top = tk.Frame(self.root, bg=self.bg, height=54)
        self.top.pack(fill="x", side="top")
        self.top.pack_propagate(False)

        self.logo = tk.Label(
            self.top, text="T.A.D.A.S.H.I", fg=self.text, bg=self.bg,
            font=("Segoe UI Semibold", 14)
        )
        self.logo.pack(side="left", padx=(20, 5))
        tk.Label(
            self.top, text="OFFLINE CORE", fg=self.accent, bg=self.bg,
            font=("Segoe UI", 8, "bold")
        ).pack(side="left", padx=8)

        self.status_label = tk.Label(
            self.top, text="● READY", fg=self.accent, bg=self.bg,
            font=("Segoe UI", 9, "bold")
        )
        self.status_label.pack(side="left", padx=12)

        self.btn_top("COMPACT", self.compact_mode).pack(side="right", padx=5, pady=11)
        self.btn_top("SETTINGS", lambda: self.show_view("settings")).pack(side="right", padx=5, pady=11)
        self.btn_top("MONITOR", lambda: self.show_view("monitor")).pack(side="right", padx=5, pady=11)
        self.btn_top("CHAT", lambda: self.show_view("chat")).pack(side="right", padx=5, pady=11)

        self.main = tk.Frame(self.root, bg=self.bg)
        self.main.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.main, bg=self.panel, width=230)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.view = tk.Frame(self.main, bg=self.bg)
        self.view.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_chat()
        self._build_monitor()
        self._build_settings()
        self.show_view("chat")

        self.footer = tk.Frame(self.root, bg=self.bg, height=32)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)
        self.compact_hint = tk.Label(
            self.footer, text="F11 fullscreen · Esc exit fullscreen · Ctrl+Enter send · voice is offline",
            fg=self.muted, bg=self.bg, font=("Segoe UI", 8)
        )
        self.compact_hint.pack(side="left", padx=18)

    def btn_top(self, label, command):
        b = tk.Label(
            self.top, text=label, fg=self.muted, bg=self.bg,
            font=("Segoe UI", 8, "bold"), cursor="hand2", padx=8
        )
        b.bind("<Button-1>", lambda _e: command())
        b.bind("<Enter>", lambda _e: b.configure(fg=self.accent))
        b.bind("<Leave>", lambda _e: b.configure(fg=self.muted))
        return b

    def _build_sidebar(self):
        tk.Label(
            self.sidebar, text="T.A.D.A.S.H.I", fg=self.accent, bg=self.panel,
            font=("Segoe UI", 22, "bold")
        ).pack(anchor="w", padx=20, pady=(25, 2))
        tk.Label(
            self.sidebar, text="LOCAL INTELLIGENCE CONSOLE", fg=self.muted, bg=self.panel,
            font=("Segoe UI", 8, "bold")
        ).pack(anchor="w", padx=21, pady=(0, 22))

        self.sidebar_status = tk.Label(
            self.sidebar, text="SYSTEM NOMINAL", fg=self.accent, bg=self.panel,
            font=("Consolas", 9, "bold")
        )
        self.sidebar_status.pack(anchor="w", padx=20, pady=(0, 18))

        self.sidebar_nav = {}
        for key, label in (("chat", "CHAT"), ("monitor", "SYSTEM MONITOR"), ("settings", "SETTINGS")):
            self.sidebar_button(key, label)

        tk.Frame(self.sidebar, bg=self.panel2, height=1).pack(fill="x", padx=20, pady=22)
        tk.Label(self.sidebar, text="IDENTITY", fg=self.muted, bg=self.panel, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20)
        tk.Label(
            self.sidebar,
            text="T.A.D.A.S.H.I\nTech Assistant & Data Analyst\nfor Smart Human Interaction",
            fg=self.text, bg=self.panel, justify="left", anchor="w",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=20, pady=(6, 20))

        self.mic_indicator = tk.Label(
            self.sidebar, text="◉ MIC OFF", fg=self.muted, bg=self.panel,
            font=("Consolas", 9, "bold")
        )
        self.mic_indicator.pack(anchor="w", padx=20)

    def sidebar_button(self, key, label):
        b = tk.Label(
            self.sidebar, text=label, fg=self.muted, bg=self.panel,
            font=("Segoe UI", 9, "bold"), anchor="w", padx=14, pady=9, cursor="hand2"
        )
        b.pack(fill="x", padx=12, pady=2)
        b.bind("<Button-1>", lambda _e, k=key: self.show_view(k))
        b.bind("<Enter>", lambda _e, w=b: w.configure(fg=self.text, bg=self.panel2))
        b.bind("<Leave>", lambda _e, k=key, w=b: self._nav_leave(k, w))
        self.sidebar_nav[key] = b

    def _nav_leave(self, key, widget):
        widget.configure(bg=self.panel2 if getattr(self, "active_view", "chat") == key else self.panel, fg=self.text if getattr(self, "active_view", "chat") == key else self.muted)

    def _build_chat(self):
        self.chat_frame = tk.Frame(self.view, bg=self.bg)
        self.chat_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.orb_canvas = tk.Canvas(self.chat_frame, bg=self.bg, highlightthickness=0)
        self.orb_canvas.pack(fill="both", expand=True)
        self.orb_canvas.bind("<Configure>", lambda _e: self._draw_orb())

        self.chat_overlay = tk.Frame(self.chat_frame, bg=self.bg)
        self.chat_overlay.place(relx=0.5, rely=0.67, relwidth=0.72, anchor="center")

        self.state_label = tk.Label(
            self.chat_overlay, text="READY", fg=self.accent, bg=self.bg,
            font=("Consolas", 9, "bold")
        )
        self.state_label.pack()

        self.chat_history = tk.Text(
            self.chat_overlay, height=7, wrap="word", relief="flat", borderwidth=0,
            bg=self.panel, fg=self.text, insertbackground=self.accent,
            font=("Segoe UI", 10), padx=14, pady=12
        )
        self.chat_history.pack(fill="x", pady=(8, 9))
        self.chat_history.configure(state="disabled")
        self.chat_history.tag_configure("user", foreground=self.accent, font=("Segoe UI Semibold", 10))
        self.chat_history.tag_configure("assistant", foreground=self.text, font=("Segoe UI", 10))
        self.chat_history.tag_configure("muted", foreground=self.muted, font=("Segoe UI", 8))

        input_row = tk.Frame(self.chat_overlay, bg=self.bg)
        input_row.pack(fill="x")
        self.input = tk.Text(
            input_row, height=3, wrap="word", relief="flat", borderwidth=0,
            bg=self.panel2, fg=self.text, insertbackground=self.accent,
            font=("Segoe UI", 10), padx=12, pady=10
        )
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Return>", self._enter_send)

        self.mic_button = tk.Label(
            input_row, text=" MIC ", fg=self.text, bg=self.panel2,
            font=("Segoe UI", 9, "bold"), cursor="hand2", padx=8
        )
        self.mic_button.pack(side="left", fill="y", padx=(5, 0))
        self.mic_button.bind("<Button-1>", lambda _e: self.toggle_listening())

        self.send_button = tk.Label(
            input_row, text=" SEND ", fg=self.text, bg=self.accent2,
            font=("Segoe UI", 9, "bold"), cursor="hand2", padx=8
        )
        self.send_button.pack(side="left", fill="y", padx=(5, 0))
        self.send_button.bind("<Button-1>", lambda _e: self.send_message())

    def _build_monitor(self):
        self.monitor_frame = tk.Frame(self.view, bg=self.bg)
        self.monitor_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        tk.Label(self.monitor_frame, text="SYSTEM MONITOR", fg=self.text, bg=self.bg, font=("Segoe UI Semibold", 22)).pack(anchor="w", padx=30, pady=(28, 5))
        tk.Label(self.monitor_frame, text="Live local telemetry · no network calls", fg=self.muted, bg=self.bg, font=("Segoe UI", 9)).pack(anchor="w", padx=31, pady=(0, 24))

        self.metric_widgets = {}
        cards = tk.Frame(self.monitor_frame, bg=self.bg)
        cards.pack(fill="x", padx=25, pady=5)
        for i, name in enumerate(("CPU", "RAM", "DISK", "BATTERY", "GPU")):
            card = tk.Frame(cards, bg=self.panel, height=140)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=7, pady=7)
            card.grid_propagate(False)
            cards.rowconfigure(i // 2, weight=0)
            cards.columnconfigure(i % 2, weight=1)
            title = tk.Label(card, text=name, fg=self.muted, bg=self.panel, font=("Segoe UI", 9, "bold"))
            title.pack(anchor="w", padx=18, pady=(17, 4))
            value = tk.Label(card, text="--", fg=self.text, bg=self.panel, font=("Consolas", 24, "bold"))
            value.pack(anchor="w", padx=18)
            bar = tk.Canvas(card, bg=self.panel, height=10, highlightthickness=0)
            bar.pack(fill="x", padx=18, pady=(8, 16))
            self.metric_widgets[name] = (card, value, bar)

        self.core_frame = tk.Frame(self.monitor_frame, bg=self.panel)
        self.core_frame.grid(row=0, column=0) if False else None
        tk.Label(self.monitor_frame, text="CPU CORES", fg=self.muted, bg=self.bg, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=31, pady=(0, 5))
        self.cores_canvas = tk.Canvas(self.monitor_frame, height=90, bg=self.panel, highlightthickness=0)
        self.cores_canvas.pack(fill="x", padx=32, pady=(0, 25))

    def _build_settings(self):
        self.settings_frame = tk.Frame(self.view, bg=self.bg)
        self.settings_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        tk.Label(self.settings_frame, text="SETTINGS", fg=self.text, bg=self.bg, font=("Segoe UI Semibold", 22)).pack(anchor="w", padx=30, pady=(28, 5))
        tk.Label(self.settings_frame, text="Local preferences saved next to the application", fg=self.muted, bg=self.bg, font=("Segoe UI", 9)).pack(anchor="w", padx=31, pady=(0, 20))

        body = tk.Frame(self.settings_frame, bg=self.panel)
        body.pack(fill="x", padx=30, pady=8)

        self._setting_row(body, "UI ACCENT", self.change_accent)
        self._toggle_row(body, "Always on top", "always_on_top")
        self._toggle_row(body, "Auto-speak answers", "auto_speak")
        self._toggle_row(body, "Hands-free wake word: TADASHI", "hands_free")
        self._toggle_row(body, "Start in fullscreen", "start_fullscreen")

        speed_row = tk.Frame(body, bg=self.panel)
        speed_row.pack(fill="x", padx=20, pady=10)
        tk.Label(speed_row, text="Animation speed", fg=self.text, bg=self.panel, font=("Segoe UI", 10)).pack(side="left")
        self.speed = tk.Scale(
            speed_row, from_=0.4, to=2.0, resolution=0.1, orient="horizontal", showvalue=True,
            bg=self.panel, fg=self.text, troughcolor=self.panel2, highlightthickness=0,
            command=lambda v: self._set_setting("animation_speed", float(v))
        )
        self.speed.set(float(self.settings["animation_speed"]))
        self.speed.pack(side="right", fill="x", expand=True, padx=20)

        voice_row = tk.Frame(body, bg=self.panel)
        voice_row.pack(fill="x", padx=20, pady=(4, 18))
        tk.Label(voice_row, text="Speech rate", fg=self.text, bg=self.panel, font=("Segoe UI", 10)).pack(side="left")
        self.rate = tk.Scale(
            voice_row, from_=120, to=240, resolution=5, orient="horizontal", showvalue=True,
            bg=self.panel, fg=self.text, troughcolor=self.panel2, highlightthickness=0,
            command=lambda v: self._set_setting("voice_rate", int(float(v)))
        )
        self.rate.set(int(self.settings["voice_rate"]))
        self.rate.pack(side="right", fill="x", expand=True, padx=20)

        tk.Label(
            self.settings_frame,
            text="Model: SmolLM2-360M-Instruct-Q4_K_M.gguf · 271 MB class\nSpeech: Vosk small English · TTS: Windows SAPI via pyttsx3\nMath/system/audio are routed to Python where possible.",
            fg=self.muted, bg=self.bg, justify="left", font=("Segoe UI", 9)
        ).pack(anchor="w", padx=31, pady=18)

    def _setting_row(self, parent, label, command):
        row = tk.Frame(parent, bg=self.panel)
        row.pack(fill="x", padx=20, pady=(18, 8))
        tk.Label(row, text=label, fg=self.text, bg=self.panel, font=("Segoe UI", 10)).pack(side="left")
        value = tk.Label(row, text="       ", fg=self.accent, bg=self.accent, cursor="hand2")
        value.pack(side="right")
        value.bind("<Button-1>", lambda _e: command())
        self.accent_swatch = value

    def _toggle_row(self, parent, label, key):
        row = tk.Frame(parent, bg=self.panel)
        row.pack(fill="x", padx=20, pady=6)
        tk.Label(row, text=label, fg=self.text, bg=self.panel, font=("Segoe UI", 10)).pack(side="left")
        var = tk.BooleanVar(value=bool(self.settings[key]))
        btn = tk.Checkbutton(
            row, variable=var, onvalue=True, offvalue=False, bg=self.panel,
            fg=self.text, activebackground=self.panel, activeforeground=self.text,
            selectcolor=self.panel2, highlightthickness=0,
            command=lambda v=var, k=key: self._set_setting(k, bool(v.get()))
        )
        btn.pack(side="right")
        setattr(self, f"var_{key}", var)

    def show_view(self, name):
        self.active_view = name
        for k, w in self.sidebar_nav.items():
            w.configure(bg=self.panel2 if k == name else self.panel, fg=self.text if k == name else self.muted)
        for frame in (self.chat_frame, self.monitor_frame, self.settings_frame):
            frame.lower()
        {"chat": self.chat_frame, "monitor": self.monitor_frame, "settings": self.settings_frame}[name].lift()

    def _seed_chat(self):
        self._append_chat("TADASHI", "Local core online. I can handle Python-routed math/system tasks, offline speech, and local language-model conversation.", "assistant")

    def _append_chat(self, who, msg, kind):
        self.chat_history.configure(state="normal")
        self.chat_history.insert("end", f"{who}\n", kind)
        self.chat_history.insert("end", msg.strip() + "\n\n", "assistant" if kind == "assistant" else "user")
        self.chat_history.see("end")
        self.chat_history.configure(state="disabled")

    def _set_state(self, state, extra=""):
        self.state = state
        self.status_label.configure(text=f"● {state}", fg=self.accent)
        self.state_label.configure(text=state, fg=self.accent if state != "ERROR" else "#FF6B6B")
        self.sidebar_status.configure(text=extra or ({"READY": "SYSTEM NOMINAL", "LISTENING": "VOICE INPUT ACTIVE", "THINKING": "LOCAL INFERENCE", "SPEAKING": "AUDIO OUTPUT ACTIVE"}.get(state, state)), fg=self.accent)
        self.mic_indicator.configure(
            text="◉ MIC ON" if state == "LISTENING" else "◉ MIC OFF",
            fg=self.accent if state == "LISTENING" else self.muted
        )

    def _apply_window_mode(self):
        try:
            self.root.attributes("-topmost", bool(self.settings["always_on_top"]))
        except Exception:
            pass
        if self.fullscreen and not self.compact:
            self.root.overrideredirect(False)
            self.root.attributes("-fullscreen", True)
        else:
            self.root.attributes("-fullscreen", False)
            self.root.overrideredirect(self.compact)
            if self.compact:
                self.root.geometry("440x165+30+30")
                self.root.resizable(False, False)
                self.sidebar.pack_forget()
                self.footer.pack_forget()
                self.view.configure(bg=self.bg)
                self.chat_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
                self.chat_overlay.place(relx=0.5, rely=0.64, relwidth=0.92, anchor="center")
                self.chat_history.configure(height=3)
            else:
                self.root.overrideredirect(False)
                self.root.resizable(True, True)
                self.sidebar.pack(side="left", fill="y")
                self.footer.pack(fill="x", side="bottom")
                self.chat_overlay.place(relx=0.5, rely=0.67, relwidth=0.72, anchor="center")
                self.chat_history.configure(height=7)

    def toggle_fullscreen(self):
        if self.compact:
            self.compact_mode()
        self.fullscreen = not self.fullscreen
        self._apply_window_mode()
        return "break"

    def exit_fullscreen(self):
        if self.fullscreen:
            self.fullscreen = False
            self._apply_window_mode()

    def compact_mode(self):
        self.compact = not self.compact
        if self.compact:
            self.fullscreen = False
        else:
            self.fullscreen = True
        self._apply_window_mode()

    # -------------------- animation --------------------
    def _sphere_points(self, count=420):
        points = []
        golden = math.pi * (3 - math.sqrt(5))
        for i in range(count):
            y = 1 - (i / max(1, count - 1)) * 2
            r = math.sqrt(max(0, 1 - y * y))
            theta = golden * i
            points.append((math.cos(theta) * r, y, math.sin(theta) * r))
        return points

    def _ensure_points(self):
        if not hasattr(self, "points"):
            self.points = self._sphere_points()

    def _draw_orb(self):
        self._ensure_points()
        c = self.orb_canvas
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        cx, cy = w / 2, h * 0.35
        radius = min(w, h) * (0.28 if not self.compact else 0.38)
        c.delete("all")

        # Soft rings
        for ring in (0.98, 0.85, 0.72):
            rr = radius * ring
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=self._mix(self.accent, self.bg, 0.90), width=1)

        speed = float(self.settings["animation_speed"] or 1.0)
        state_speed = {"READY": 0.00045, "LISTENING": 0.0011, "THINKING": 0.0016, "SPEAKING": 0.0012}.get(self.state, 0.0007)
        angle = self.angle
        pulse = 1 + 0.06 * math.sin(self.pulse) + 0.30 * self.audio_level
        jitter = {"READY": 0.0, "LISTENING": 0.045, "THINKING": 0.07, "SPEAKING": 0.05}.get(self.state, 0.02)

        projected = []
        for idx, (x, y, z) in enumerate(self.points):
            # Y-axis rotation + tiny response motion.
            ca, sa = math.cos(angle + idx * 0.0002), math.sin(angle + idx * 0.0002)
            xr = x * ca - z * sa
            zr = x * sa + z * ca
            yr = y
            wave = math.sin(self.pulse * 0.7 + idx * 0.17) * jitter
            xr += wave * (1 - abs(yr))
            yr += math.cos(self.pulse * 0.4 + idx * 0.12) * jitter * 0.18
            depth = 0.55 + 0.45 * ((zr + 1) / 2)
            s = radius * pulse * (0.74 + 0.26 * depth)
            px = cx + xr * s
            py = cy + yr * s
            rdot = 0.65 + 1.55 * depth
            projected.append((zr, px, py, rdot, depth))

        projected.sort(key=lambda p: p[0])
        for zr, px, py, rdot, depth in projected:
            col = self._particle_color(depth)
            rr = rdot / 2
            c.create_oval(px - rr, py - rr, px + rr, py + rr, fill=col, outline="")

        # Active waveform under the sphere.
        if self.state in ("LISTENING", "SPEAKING"):
            bar_y = cy + radius * 1.08
            for i in range(28):
                phase = self.pulse * 0.7 + i * 0.44
                amp = (5 + 25 * (0.2 + 0.8 * abs(math.sin(phase)))) * (0.25 + 0.9 * self.audio_level)
                if self.state == "SPEAKING":
                    amp = 5 + 13 * abs(math.sin(phase))
                x = cx - 140 + i * 10
                c.create_line(x, bar_y - amp, x, bar_y + amp, fill=self.accent, width=2)

    def _particle_color(self, depth):
        # Blend accent to a dim version without requiring transparent drawing.
        return self._mix(self.bg, self.accent, 0.25 + 0.65 * depth)

    @staticmethod
    def _mix(a, b, t):
        def rgb(x):
            x = x.lstrip("#")
            return int(x[0:2], 16), int(x[2:4], 16), int(x[4:6], 16)
        ar, ag, ab = rgb(a)
        br, bg, bb = rgb(b)
        t = max(0, min(1, t))
        return "#%02x%02x%02x" % (
            int(ar + (br - ar) * t), int(ag + (bg - ag) * t), int(ab + (bb - ab) * t)
        )

    def _animate(self):
        if not self.running:
            return
        speed = float(self.settings["animation_speed"] or 1.0)
        self.angle += speed * {"READY": 0.025, "LISTENING": 0.055, "THINKING": 0.085, "SPEAKING": 0.065}.get(self.state, 0.03)
        self.pulse += speed * 0.16
        # Decay microphone level between frames.
        self.audio_level *= 0.88
        if hasattr(self, "orb_canvas"):
            self._draw_orb()
        self.root.after(33, self._animate)

    # -------------------- chat/routing --------------------
    def _enter_send(self, event):
        if event.state & 0x0001:  # Shift+Enter
            return
        self.send_message()
        return "break"

    def send_message(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._append_chat("YOU", text, "user")
        self.messages.append(("user", text))
        self._set_state("THINKING")
        threading.Thread(target=self._answer_worker, args=(text,), daemon=True).start()

    def _answer_worker(self, text):
        try:
            answer = self.route_request(text)
            self.ui_queue.put(("answer", answer))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    def route_request(self, text):
        t = text.strip()
        low = t.lower()

        if self._is_self_question(low):
            return self.self_response(t)

        if low in {"time", "what time is it", "what is the time", "current time"}:
            return datetime.now().strftime("Local system time: %A, %d %B %Y, %H:%M:%S")

        if any(k in low for k in ("system status", "system usage", "cpu usage", "ram usage", "hardware status")):
            return self.system_summary()

        math_result = self.math_router(t)
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

        return self.local_llm(t)

    @staticmethod
    def _is_self_question(low):
        keys = ("who are you", "what are you", "your name", "what is tadashi", "about yourself", "what can you do")
        return any(k in low for k in keys)

    def self_response(self, _text):
        return (
            f"I am {APP_NAME} — {APP_FULL}.\n"
            "Tadashi (但し) is a traditional Japanese masculine given name commonly associated with “righteous”, “correct”, or “loyal”.\n\n"
            "I am designed as a local Windows assistant: Python handles deterministic work such as system telemetry and math, while a small GGUF language model handles natural-language reasoning. Speech recognition and speech synthesis are also local."
        )

    def dependency_message(self, pkg):
        return f"A required local component is missing: {pkg}. Install the project dependencies once while online, then TADASHI can run offline."

    # -------------------- math / logic --------------------
    def math_router(self, text):
        if sp is None or parse_expr is None:
            return None
        q = text.strip()
        low = q.lower()
        normalized = self._normalize_math(q)

        # Plain arithmetic: deliberately restrictive; no eval().
        arithmetic = re.fullmatch(r"[0-9\s+\-*/().%]+", normalized)
        if arithmetic and any(ch.isdigit() for ch in normalized):
            try:
                result = sp.N(parse_expr(normalized, transformations=SYMPY_TRANSFORMS, evaluate=True), 15)
                return f"Python math result: {result}"
            except Exception:
                pass

        # Natural-language arithmetic.
        if any(k in low for k in ("calculate", "compute", "what is", "how much is")) and re.search(r"\d", normalized):
            candidate = re.sub(r"^(please\s+)?(calculate|compute|what is|how much is)\s*", "", normalized, flags=re.I)
            if re.fullmatch(r"[0-9\s+\-*/().%]+", candidate):
                try:
                    result = sp.N(parse_expr(candidate, transformations=SYMPY_TRANSFORMS, evaluate=True), 15)
                    return f"Python math result: {result}"
                except Exception:
                    pass

        # Equation solving.
        if "solve" in low and "=" in q:
            try:
                expr_text = q[low.find("solve") + 5:].strip()
                var_match = re.search(r"\bfor\s+([a-zA-Z_]\w*)\b", expr_text, re.I)
                var_name = var_match.group(1) if var_match else "x"
                expr_text = re.sub(r"\bfor\s+[a-zA-Z_]\w*\b", "", expr_text, flags=re.I).strip()
                lhs, rhs = expr_text.split("=", 1)
                symbol = sp.Symbol(var_name)
                local = {var_name: symbol}
                eq = sp.Eq(parse_expr(lhs, local_dict=local, transformations=SYMPY_TRANSFORMS), parse_expr(rhs, local_dict=local, transformations=SYMPY_TRANSFORMS))
                sol = sp.solve(eq, symbol)
                return f"Python symbolic result: {sol}"
            except Exception:
                pass

        # Derivative and integral.
        if low.startswith("derivative of ") or "differentiate " in low:
            try:
                body = re.sub(r"^(derivative of|differentiate)\s+", "", q, flags=re.I).strip()
                var_name = "x"
                var = sp.Symbol(var_name)
                expr = parse_expr(body, local_dict={var_name: var}, transformations=SYMPY_TRANSFORMS)
                return f"Python symbolic result: {sp.diff(expr, var)}"
            except Exception:
                pass

        if low.startswith("integral of ") or "integrate " in low:
            try:
                body = re.sub(r"^(integral of|integrate)\s+", "", q, flags=re.I).strip()
                var_name = "x"
                var = sp.Symbol(var_name)
                expr = parse_expr(body, local_dict={var_name: var}, transformations=SYMPY_TRANSFORMS)
                return f"Python symbolic result: {sp.integrate(expr, var)} + C"
            except Exception:
                pass

        # Simple propositional logic with A/B/C-style variables.
        if re.search(r"\b(and|or|not)\b", low) and len(q) < 220:
            try:
                logic = re.sub(r"\bAND\b", "&", q, flags=re.I)
                logic = re.sub(r"\bOR\b", "|", logic, flags=re.I)
                logic = re.sub(r"\bNOT\b", "~", logic, flags=re.I)
                names = sorted(set(re.findall(r"\b[A-Za-z][A-Za-z0-9_]*\b", logic)) - {"True", "False"})
                locals_map = {n: sp.Symbol(n) for n in names}
                expr = parse_expr(logic, local_dict=locals_map | {"True": sp.true, "False": sp.false}, transformations=SYMPY_TRANSFORMS)
                return f"Python logic simplification: {simplify_logic(expr, force=True)}"
            except Exception:
                pass
        return None

    @staticmethod
    def _normalize_math(s):
        s = s.replace("×", "*").replace("÷", "/").replace("−", "-")
        s = re.sub(r"\bplus\b", "+", s, flags=re.I)
        s = re.sub(r"\bminus\b", "-", s, flags=re.I)
        s = re.sub(r"\btimes\b", "*", s, flags=re.I)
        s = re.sub(r"\bmultiplied by\b", "*", s, flags=re.I)
        s = re.sub(r"\bdivided by\b", "/", s, flags=re.I)
        return s.strip()

    # -------------------- local LLM --------------------
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
        history = [{"role": "system", "content": SELF_KNOWLEDGE}]
        for role, content in self.messages[-8:]:
            history.append({"role": role, "content": content})
        response = llm.create_chat_completion(
            messages=history,
            max_tokens=384,
            temperature=0.3,
            top_p=0.9,
            stream=False,
        )
        content = response["choices"][0]["message"]["content"].strip()
        return content or "I did not produce a response."

    # -------------------- speech --------------------
    def toggle_listening(self):
        if self.state == "LISTENING":
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if sd is None or Model is None or KaldiRecognizer is None:
            self._append_chat("TADASHI", self.dependency_message("sounddevice + vosk"), "assistant")
            return
        if not VOSK_PATH.exists():
            self._append_chat("TADASHI", f"Vosk model missing. Place it at:\n{VOSK_PATH}", "assistant")
            return
        if self.audio_thread and self.audio_thread.is_alive():
            return
        self.audio_stop.clear()
        self.transcript = ""
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

            def callback(indata, frames, _time_info, status):
                if status:
                    pass
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
                            obj = json.loads(recognizer.Result())
                            result = obj.get("text", "").strip()
                        except Exception:
                            result = ""
                        if result:
                            self.ui_queue.put(("transcript", result))
                            if self.settings["hands_free"]:
                                lower = result.lower()
                                if "tadashi" in lower:
                                    cmd = re.sub(r"\btadashi\b[:,; ]*", "", result, count=1, flags=re.I).strip()
                                    if cmd:
                                        self.ui_queue.put(("voice_command", cmd))
                                        self.audio_stop.set()
                                    else:
                                        self.ui_queue.put(("prompt", "Wake word detected. State your command."))
                            else:
                                self.ui_queue.put(("voice_command", result))
                                self.audio_stop.set()
        except Exception as exc:
            self.ui_queue.put(("voice_error", str(exc)))
        finally:
            if self.state == "LISTENING":
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

    # -------------------- system telemetry --------------------
    def _update_stats(self):
        if not self.running:
            return
        if psutil is not None:
            try:
                self.stats["cpu"] = float(psutil.cpu_percent(interval=None))
                self.stats["ram"] = float(psutil.virtual_memory().percent)
                self.stats["disk"] = float(psutil.disk_usage(os.path.abspath(os.sep)).percent)
                bat = psutil.sensors_battery()
                self.stats["battery"] = float(bat.percent) if bat else None
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
            if not getattr(self, "_nvml_ready", False):
                pynvml.nvmlInit()
                self._nvml_ready = True
            count = pynvml.nvmlDeviceGetCount()
            if count < 1:
                return None
            loads = []
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                loads.append(float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu))
            return sum(loads) / len(loads) if loads else None
        except Exception:
            return None

    def _refresh_monitor(self):
        vals = {
            "CPU": self.stats["cpu"],
            "RAM": self.stats["ram"],
            "DISK": self.stats["disk"],
            "BATTERY": self.stats["battery"],
            "GPU": self.stats["gpu"],
        }
        for name, (card, value, bar) in self.metric_widgets.items():
            v = vals[name]
            if v is None:
                value.configure(text="N/A", fg=self.muted)
                bar.delete("all")
                continue
            value.configure(text=f"{v:.0f}%", fg=self.text)
            bar.delete("all")
            w = max(1, bar.winfo_width())
            bar.create_rectangle(0, 0, w * max(0, min(100, v)) / 100, 10, fill=self.accent, outline="")
        self.cores_canvas.delete("all")
        cores = self.stats["cores"] or []
        for i, v in enumerate(cores[:32]):
            x = 8 + i * 32
            self.cores_canvas.create_text(x + 12, 14, text=str(i + 1), fill=self.muted, font=("Consolas", 7))
            self.cores_canvas.create_rectangle(x, 25, x + 24, 78, fill=self.panel2, outline="")
            self.cores_canvas.create_rectangle(x, 25 + 53 * (1 - v / 100), x + 24, 78, fill=self.accent, outline="")

    def system_summary(self):
        if psutil is None:
            return self.dependency_message("psutil")
        disk = self.stats["disk"]
        bat = self.stats["battery"]
        return (
            f"CPU: {self.stats['cpu']:.0f}%\n"
            f"RAM: {self.stats['ram']:.0f}%\n"
            f"Disk (system volume): {disk:.0f}%\n"
            f"Battery: {bat:.0f}%" if bat is not None else
            f"CPU: {self.stats['cpu']:.0f}%\nRAM: {self.stats['ram']:.0f}%\nDisk (system volume): {disk:.0f}%\nBattery: N/A"
        )

    # -------------------- settings --------------------
    def _set_setting(self, key, value):
        self.settings[key] = value
        if key == "accent":
            self.accent = value
        if key == "always_on_top":
            self.root.attributes("-topmost", bool(value))
        if key == "accent":
            self._refresh_colors()

    def change_accent(self):
        chosen = colorchooser.askcolor(color=self.accent, title="Choose TADASHI accent")
        if chosen and chosen[1]:
            self._set_setting("accent", chosen[1])

    def _refresh_colors(self):
        self.status_label.configure(fg=self.accent)
        self.state_label.configure(fg=self.accent)
        self.logo.configure(fg=self.text)
        self.sidebar_status.configure(fg=self.accent)
        self.mic_indicator.configure(fg=self.accent if self.state == "LISTENING" else self.muted)
        self.accent_swatch.configure(bg=self.accent, fg=self.accent)
        self._draw_orb()

    # -------------------- queue / lifecycle --------------------
    def _poll_ui_queue(self):
        if not self.running:
            return
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "answer":
                    self.messages.append(("assistant", payload))
                    self._append_chat("TADASHI", payload, "assistant")
                    self._set_state("READY")
                    self.speak(payload)
                elif kind == "error":
                    self._append_chat("TADASHI", payload, "assistant")
                    self._set_state("ERROR")
                    self.root.after(1200, lambda: self._set_state("READY"))
                elif kind == "transcript":
                    self._append_chat("VOICE", payload, "user")
                elif kind == "voice_command":
                    self.input.delete("1.0", "end")
                    self.input.insert("1.0", payload)
                    self.send_message()
                elif kind == "prompt":
                    self._append_chat("TADASHI", payload, "assistant")
                elif kind == "voice_error":
                    self._append_chat("TADASHI", payload, "assistant")
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
