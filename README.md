# T.A.D.A.S.H.I: Tech Assistant & Data Analyst for Smart Human Interaction

Tadashi (但し) traditional Japanese masculine name, meaning "righteous," "correct," or "loyal".

A lightweight, portable, offline-first Windows desktop AI assistant built around Python, a small local GGUF language model, offline speech recognition, local text-to-speech, deterministic Python tools, and a reactive particle-sphere interface.

## Idea

<img width="1920" height="808" alt="idea" src="https://github.com/user-attachments/assets/35d432da-59d5-4583-a38a-e3acc206fdcb" />

## Overview

TADASHI is designed to run locally from a portable folder or pendrive. The application does not require a cloud API at runtime.

The architecture deliberately routes deterministic work to Python instead of the language model whenever possible:

```text
User text / voice
        |
        v
  TADASHI Router
   /     |      \
  /      |       \
Math   System    Conversation
Logic  Telemetry     |
  |       |          v
  +-------+----> Local GGUF LLM
                  |
                  v
            Tadashi response
                  |
             Windows SAPI
```

## Current features

- Fullscreen Windows desktop interface.
- Chat-first startup with `Program initialised.`.
- Top status indicator: `ALL SYSTEMS OPERATIONAL`.
- Normal AI-style conversation area with `Tadashi` and `User` labels.
- Flexible multiline composer that grows within a practical limit.
- Animated particle sphere reacting to system state.
- Distinct sphere states for `READY`, `LISTENING`, `THINKING`, `SPEAKING`, and `ERROR`.
- Sphere positioned behind the interface on the right side of the chat scene.
- Compact window mode with `MINIMIZE` / `MAXIMIZE` state switching.
- Settings for accent color, default cyan-blue accent, animation speed, speech rate, always-on-top, auto-speak, hands-free mode, and startup fullscreen.
- Restore-all-settings-to-default control.
- Offline Vosk speech recognition.
- Offline Windows SAPI speech synthesis through `pyttsx3`.
- Local CPU/RAM/disk/battery monitoring through `psutil`.
- Optional NVIDIA GPU utilization monitoring.
- Python/SymPy routing for arithmetic, equations, derivatives, integrals, and simple propositional logic.
- Local self-knowledge and personality prompt.
- Deterministic identity response: `Tadashi, your assistant.`

## Design principles

### Offline first

Runtime inference, speech recognition, speech synthesis, mathematics, and system telemetry are local. No cloud API key is needed by the application.

### Python first for deterministic work

A small model is not used for tasks that Python can perform exactly. Arithmetic and symbolic mathematics are routed to SymPy, while hardware telemetry is routed to `psutil`.

### Lightweight local model

The project is designed for a small GGUF instruction model rather than a large multi-billion-parameter model. The initial configuration targets SmolLM2 360M Instruct in Q4_K_M form.

### Portable deployment

The project expects the Python runtime and application files to live together. `TADASHI.bat` resolves the portable Python executable relative to the project directory instead of depending on a fixed Windows drive letter.

## Recommended folder layout

```text
TADASHI/
├── tadashi.py
├── TADASHI.bat
├── models/
│   └── SmolLM2-360M-Instruct-Q4_K_M.gguf
│   └── vosk-model-small-en-us-0.15/
│       └── #vosk files
├── # setup files- readme.txt, requirements.txt, etc.
└── tadashi_settings.json        # generated locally
```

## Runtime assets

### Language model

Initial target:

```text
SmolLM2-360M-Instruct-Q4_K_M.gguf
```

Expected location:

```text
models/SmolLM2-360M-Instruct-Q4_K_M.gguf
```

### Speech recognition model

Initial target:

```text
vosk-model-small-en-us-0.15/
```

Expected location:

```text
models/vosk-model-small-en-us-0.15/
```

See [`MODEL_SETUP.md`](MODEL_SETUP.md) for model placement instructions.

## Python environment

Use a 64-bit portable Python environment. The development target used for the project is WinPython 64-bit with Python 3.13.x.

The core application is intentionally kept in one Python file. The additional repository files are documentation, dependency metadata, and packaging support.

## Installation

1. Extract WinPython into the project folder as:

```text
TADASHI/language/
```

2. Install the Python dependencies into that portable runtime.

3. Download the local GGUF language model and Vosk model.

4. Place the models under `models/` using the paths documented above.

5. Start the application with:

```text
TADASHI.bat
```

## Dependencies

Core Python dependencies:

- `psutil` — local system telemetry.
- `sympy` — deterministic arithmetic and symbolic mathematics.
- `sounddevice` — microphone input.
- `vosk` — offline speech recognition.
- `pyttsx3` — offline speech output through Windows SAPI.
- `llama-cpp-python` — local GGUF inference.

Optional:

- NVIDIA Management Library Python bindings for NVIDIA GPU utilization.

Tkinter is supplied with the Windows Python distribution used by the project and is used instead of a heavier desktop UI framework.

## llama.cpp / local model installation

`llama-cpp-python` can use pre-built CPU wheels. Follow the upstream installation instructions for the appropriate Windows/Python wheel rather than forcing a source compilation unnecessarily:

- <https://github.com/abetlen/llama-cpp-python>

## Usage

### Chat

Type a message and press `Ctrl+Enter`, or click `SEND`.

`Shift+Enter` inserts a new line in the composer.

### Voice

Click `MIC` and speak. With hands-free mode enabled, use the wake phrase `Tadashi` before a command.

### System monitor

Open `MONITOR` to view CPU, RAM, disk, battery, CPU-core utilization, and optional NVIDIA GPU utilization.

### Settings

Open `SETTINGS` to change the UI accent, choose the cyan-blue default, control animation and speech behavior, and restore default settings.

### Keyboard shortcuts

| Shortcut      | Action            |
| ------------- | ----------------- |
| `F11`         | Toggle fullscreen |
| `Esc`         | Exit fullscreen   |
| `Ctrl+Enter`  | Send message      |
| `Shift+Enter` | New line          |

## Identity and personality

TADASHI identifies itself as:

```text
Tadashi, your assistant.
```

The local model prompt gives Tadashi a calm, capable, attentive, moderately conversational personality while requiring it to remain factual about its capabilities and offline environment.

## Privacy model

The intended runtime architecture keeps microphone processing, speech recognition, text-to-speech, deterministic calculations, telemetry, and language-model inference on the local computer.

The repository does not contain API keys, passwords, personal settings, audio recordings, or generated user data.

## Model and asset distribution

The repository contains source code and configuration, not the full runtime model files. Keep model downloads separate from source control when possible.

GitHub blocks regular repository files larger than 100 MiB. For binary distribution, GitHub Releases support release assets below 2 GiB per file. See GitHub's current repository and release limits documentation before publishing large model assets.

## Development status

No further develpoment till further notice.

## License

GNU GPL-3.0. See [`LICENSE`](LICENSE).

## Acknowledgements

This project builds on open-source components including Python, Tkinter, SymPy, psutil, Vosk, pyttsx3, and llama.cpp / llama-cpp-python.
