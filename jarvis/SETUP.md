# Nado — JARVIS Setup Guide

> A personal AI assistant powered by Ollama (local LLM), pyttsx3, and SpeechRecognition.  
> 100 % free. No API keys required. Calm. Precise. Slightly witty.

---

## 1. Prerequisites

- Python **3.11+**
- `pip` (comes with Python)
- A working microphone (for voice mode)

---

## 2. Install Dependencies

> Use `python3 -m pip` instead of `pip` on macOS.

### macOS

```bash
# 1. Install portaudio (required by PyAudio)
brew install portaudio

# 2. Install Python packages
python3 -m pip install -r requirements.txt
```

### Ubuntu / Debian Linux

```bash
# 1. Install portaudio dev headers
sudo apt update && sudo apt install portaudio19-dev python3-pyaudio -y

# 2. Install Python packages
python3 -m pip install -r requirements.txt
```

### Windows

```powershell
# Option A — use pipwin (easiest)
python -m pip install pipwin
python -m pipwin install pyaudio

# Then install remaining packages
python -m pip install -r requirements.txt
```

> **Tip:** It is strongly recommended to work inside a virtual environment:
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate   # macOS / Linux
> .venv\Scripts\activate      # Windows
> ```

---

## 3. Install & Start Ollama (the free local LLM)

**No API key needed** — Ollama runs 100 % on your machine.

### macOS / Linux

```bash
# 1. Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull a model (llama3.2 is small and fast, ~2 GB)
ollama pull llama3.2

# 3. Start the server (leave this running in a terminal)
ollama serve
```

### Windows

1. Download the installer from **[ollama.com/download](https://ollama.com/download)**
2. Run it — Ollama starts automatically as a background service
3. Open a terminal and pull a model:

```powershell
ollama pull llama3.2
```

> **Alternative lighter models:** `phi3` (~2 GB), `gemma3:1b` (~0.8 GB), `qwen2.5:3b` (~2 GB)  
> Change the model anytime by editing `OLLAMA_MODEL` in `config.py`.

---

## 4. No API Keys Required

This stack is entirely free and runs offline:

| Component | Technology | Cost |
|---|---|---|
| LLM brain | Ollama (local) | Free |
| Text-to-Speech | pyttsx3 (OS engine) | Free |
| Speech-to-Text | Google Web Speech via SpeechRecognition | Free |
| Wake word | Speech-based keyword detection | Free |
| Computer control | PyAutoGUI + subprocess | Free |

No `.env` file needed, no accounts to create.

---

## 5. Running Nado

**Make sure `ollama serve` is running in another terminal first.**

Navigate into the `jarvis/` directory:

```bash
cd jarvis
```

### Voice mode (wake word loop)

```bash
python3 main.py
```

Say **"Nado"** (or "Nado open Spotify") to activate.  
Press `Ctrl-C` to exit.

### Text mode (no microphone needed — great for testing)

```bash
python3 main.py text
```

Type your message and press Enter.  
Type `clear` to reset conversation memory.  
Type `quit` or `exit` to stop.

---

## 6. Example Voice Commands

| Voice Command | What Nado does |
|---|---|
| *"Open Spotify"* | Launches the Spotify desktop app |
| *"Search for the best Python async tutorials"* | Opens Google search in your browser |
| *"Take a screenshot"* | Captures screen → saves PNG to your Desktop |
| *"Open YouTube"* | Opens `https://youtube.com` in the default browser |
| *"Type Hello World"* | Types the text at the current cursor position |
| *"Run ls -la"* | Executes the shell command and reads back the output |

---

## 7. Project Structure

```
jarvis/
├── main.py          # Entry point — speech wake word loop + voice pipeline
├── brain.py         # Ollama LLM wrapper with rolling conversation memory
├── actions.py       # PC / computer control action handlers
├── voice.py         # STT (SpeechRecognition) and TTS (pyttsx3 offline)
├── config.py        # All constants — no secrets needed
├── requirements.txt # Python package list
└── SETUP.md         # This file
```

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `OSError: [Errno -9996] Invalid input device` | Check your microphone is connected and permitted |
| `ConnectionRefusedError` / can't reach Ollama | Run `ollama serve` in a separate terminal |
| `model not found` error | Run `ollama pull llama3.2` |
| Wake word rarely triggers | Speak clearly; adjust `energy_threshold` in `voice.py` |
| PyAudio install fails on macOS | Run `brew install portaudio` first |
| No sound from pyttsx3 on Linux | Run `sudo apt install espeak` |

---

## 9. Next Steps

Here are planned enhancements to extend Nado further:

- **Memory persistence** — Save conversation history to a JSON file so Nado remembers context across restarts.
- **GUI overlay** — An always-on-top HUD (using `tkinter` or `PyQt6`) showing listening state and last reply.
- **Calendar integration** — Connect to Google Calendar API or macOS EventKit to manage events by voice.
- **Premium upgrade path** — Swap Ollama for Anthropic Claude or OpenAI GPT-4o when you want a cloud brain.
- **Better TTS voices** — Replace pyttsx3 with [Kokoro TTS](https://github.com/hexgrad/kokoro) (free, local, high quality) for near-ElevenLabs quality.
- **Whisper offline STT** — Replace the Google Speech endpoint with `openai-whisper` for fully offline transcription.
- **Plugin system** — A `plugins/` directory where new action modules are auto-discovered.
