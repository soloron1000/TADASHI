# TADASHI Model Setup

The repository intentionally does not include the runtime model files.

## Language model

Download the chosen GGUF build of SmolLM2 360M Instruct and rename/use the file:

```text
SmolLM2-360M-Instruct-Q4_K_M.gguf
```

Place it here:

```text
TADASHI/models/SmolLM2-360M-Instruct-Q4_K_M.gguf
```

## Vosk

Download the small English Vosk model:

```text
vosk-model-small-en-us-0.15
```

Extract it so that this directory exists:

```text
TADASHI/models/vosk-model-small-en-us-0.15/
```

The Vosk model's `am`, `conf`, `graph`, `ivector`, and related directories should be directly inside `vosk-model-small-en-us-0.15`.
