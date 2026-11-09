# ULTRON

ULTRON is a local, voice-first personal AI assistant for Windows. It combines a
local language model with workspace search, image generation, voice input and
spoken responses, PC automation tools, and a desktop control center.

The application is designed to keep data and inference on the local machine.
Downloaded models, permissions, indexes, logs, and generated output are stored
under `.ultron/` rather than in the source packages.

## Main capabilities

### Image generation

Image generation is isolated in `ultron/image/` and exposed to the assistant
through `ultron/tools/image_tools.py`.

The image generator provides:

- Text-to-image generation through a Stable Diffusion backend when the
  configured diffusion model is available.
- A local procedural backend for lightweight image creation when diffusion is
  unavailable.
- Configurable prompt, width, height, style, steps, guidance, and seed values.
- Generation status and recent image history.
- Automatic output naming and storage in
  `.ultron/generated_images/`.

The `generate_image`, `image_status`, and `image_history` tools allow the
kernel and control center to use the same image-generation service. Generated
images are runtime artifacts and are intentionally excluded from Git.

### Workspace analysis

Workspace analysis lets ULTRON answer questions about selected local folders
using retrieval-augmented generation rather than sending files to a remote
service.

The workflow is:

1. Select or create a workspace with read-only access enabled by default.
2. Scan supported files within the workspace size limits.
3. Parse supported formats such as Markdown, Python, JavaScript, TypeScript,
   JSON, YAML, CSV, PDF, DOCX, XLSX, and log files.
4. Split parsed content into overlapping chunks.
5. Generate embeddings with the configured sentence-transformers model.
6. Store vectors in a separate ChromaDB collection for that workspace.
7. Retrieve the most relevant chunks for each question.
8. Ask the local language model to answer only from the retrieved context and
   return source information and a confidence score.

Workspace indexes are isolated by workspace ID. The default retrieval
configuration uses the five most relevant results and a similarity threshold of
`0.3`. Workspace data is stored locally under `.ultron/chroma/` and can be
removed without changing the source files.

The Chat tab includes **Browse** and **Load & Index** controls for selecting a
folder. After indexing, enable **Use selected folder context for chat** to ask
normal chat questions about that folder and receive answers grounded in its
files.

### Voice assistant

Voice functionality is isolated in `ultron/voice/`. It supports both
push-to-talk and an Alexa-style armed listening flow.

The voice pipeline uses:

- PyAudio for microphone capture.
- faster-whisper for local speech-to-text.
- Piper for local text-to-speech.
- openWakeWord for acoustic wake-word detection.
- Transcript verification to confirm the spoken keyword `ULTRON`.
- The ULTRON kernel to execute the recognized command.

Typical hands-free flow:

1. Turn the assistant on from the control center.
2. Press `Alt+grave` or use the microphone control to arm listening.
3. Say "Hey ULTRON" followed by a command.
4. ULTRON verifies the wake phrase, sends the command to the kernel, and
   speaks the response.

Push-to-talk is also available for environments where continuous listening is
not desired. Voice model files are kept under `.ultron/voices/`. If Windows
blocks microphone access, the voice subsystem reports an actionable Privacy
settings message instead of failing silently.

### Automation

The Automation tab is not limited to calendar use. It provides examples for
common local workflows and sends reviewed natural-language commands through
the same kernel used by chat and voice:

- Create prioritized tasks and recurring or one-time reminders.
- Run tests in the current workspace.
- Inspect Git status and changes.
- Analyze code structure or explain a source file.
- Use the broader workspace and PC-control tools through permissions.

The example buttons load safe, editable commands into the Quick Command field;
the user reviews the command before selecting **Execute**.

## Project structure

```text
ULTRON/
+-- ultron/                      Core application package
|   +-- image/                   Image generation backends
|   +-- voice/                   STT, TTS, recording, and wake word
|   +-- workspace/               Parsing, chunking, embeddings, retrieval
|   +-- tools/                   Assistant and automation tools
|   +-- model/                   Local language-model loading
|   +-- security/                Permissions and audit logging
|   +-- gui/                     Desktop UI components
+-- frontend/                    Web dashboard assets
+-- assets/                      Icons and static application assets
+-- scripts/                     Model download and manual voice utilities
+-- tests/                       Automated tests
+-- ultron_main.py               Command-line entry point
+-- ultron_control_center.py     Desktop control-center entry point
+-- requirements.txt             Python dependencies
+-- .ultron/                     Local models and runtime state
```

## Requirements

- Windows
- Python 3.11 recommended
- A microphone for voice input
- Enough storage for local language, speech, embedding, and optional image
  models
- Optional GPU support for faster inference; CPU fallbacks are used when
  supported

Voice engines and image backends may require additional model files or native
runtime packages. The application continues to provide non-voice and
non-diffusion features when optional components are unavailable.

## Installation

From the repository root:

```powershell
python -m pip install -r requirements.txt
```

Initialize the application:

```powershell
python ultron_main.py --init-only
```

Download the default local language model:

```powershell
python scripts/download_model.py qwen2.5-1.5b-instruct-q4_k_m
```

Voice setup expects the configured Piper voice files in `.ultron/voices/`.
Wake-word models can be downloaded through openWakeWord or replaced with a
local `ultron.onnx` or `hey_ultron.onnx` model in `.ultron/models/`.

## Running ULTRON

Start the command-line assistant:

```powershell
python ultron_main.py
```

Run one command and exit:

```powershell
python ultron_main.py "What files are in my workspace?"
```

Start the desktop control center:

```powershell
python ultron_control_center.py
```

The control center provides access to chat, voice, automation, monitoring,
permissions, modules, and settings.

## Tests

Run the automated test suite:

```powershell
python -m pytest tests
```

Foundation tests that do not require microphone hardware or long-running model
inference can be run directly:

```powershell
python -m pytest tests/test_foundation.py -q
```

## Configuration and data

Runtime configuration is loaded from `.ultron/config/config.yaml`. Important
settings include:

- Local language-model path and resource limits.
- Workspace supported extensions, file-size limits, chunking, and retrieval.
- Voice engine, model, sample rate, push-to-talk, and wake-word settings.
- Image backend and output behavior.
- Permission and security settings.

The `.ultron/` directory is local application state. It may contain model
files, voice files, workspace indexes, generated images, permissions, and
runtime logs. These files should not be committed to source control.
