# ULTRON

ULTRON is a local, voice-first personal AI assistant for Windows. It combines a
local language model with workspace search, image generation, voice input and
spoken responses, PC automation tools, and a desktop control center.

Workspace files and language-model inference stay on the local machine. Voice
input sends recorded speech to the configured transcription provider. Downloaded models, permissions, indexes, logs, and generated output are stored
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
   JSON, YAML, CSV, PDF, DOCX, PPTX, XLSX, and log files.
4. Split parsed content into overlapping chunks.
5. Save and search text in a local SQLite full-text index for that folder.
6. Send relevant passages to the configured local language model for an answer.

Select a folder in **Chat**, then select **Load & Index**. ULTRON automatically
routes explicit file and folder questions to the selected workspace. The
**Use selected folder context for chat** checkbox also applies workspace
context to follow-up questions. Reindex after changing the folder contents.

- “What's in this folder?” lists top-level files and subfolders.
- “Summarize the Autoencoders document” looks for a matching filename or title
  anywhere in the selected workspace. It can handle small typos in document
  titles and asks for a path when multiple files share the best match.
- Questions across the workspace search a local SQLite index and send matching
  excerpts to Qwen. If the model cannot load, ULTRON shows matching excerpts
  and filenames.

PDFs and slides need extractable text. Scans and text embedded in images
require OCR, which the workspace reader does not provide.

### Voice assistant

Voice functionality is isolated in `ultron/voice/`. It supports both
push-to-talk and an Alexa-style armed listening flow.

PyAudio captures the microphone. Deepgram, Cartesia, or ElevenLabs transcribes
the recording, and the ULTRON kernel handles the text. Piper or Windows SAPI
can speak the reply.

Save the secret key in the project's ignored `.env` file as
`DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, or `ELEVENLABS_API_KEY`. When multiple
are set, ULTRON prefers Deepgram, then Cartesia, then ElevenLabs. Then launch ULTRON:

```powershell
python ultron_control_center.py
```

A key in the process environment overrides the project file.

- **Push-to-talk**. Hold `Alt+grave` in the control center, or select
  **Start Voice Engine** in the Voice tab and hold **Hold to Speak**. Release
  to transcribe and run the command.
- **Wake phrase**. Enable the assistant, then say "Hey ULTRON" followed by
  your command. After the reply, ULTRON returns to wake listening. Disable
  voice to stop listening. In this mode, detected speech is sent to the configured provider
  to check the wake phrase.
- **Terminal**. Run `python scripts/voice_terminal.py` and follow the prompts.

Voice input requires internet access.
Use **Diagnose Microphone** in the Voice tab to check Windows mic access.

### Automation

The **Automation** tab provides editable workflow presets and shows the
result inline. Chat and voice use the same routing:

- "Create a high priority task to review the build" runs the local task tool.
- "Tell me about this folder" searches the indexed folder and calls the local model.
- "Open Chrome and ask ChatGPT what ROS is" opens Chrome in a separate ULTRON
  browser profile, submits the question to ChatGPT, and returns the visible
  reply. Voice requests read the reply aloud. If Chrome or ChatGPT requires
  sign-in or blocks automation, the workflow reports that failure.
- "Search the web for ROS documentation" opens a Google search in Chrome.

Browser workflows require Chrome and the Playwright Python package. ULTRON
sends the explicit ChatGPT question without attaching workspace files.

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
- Enough storage for local language, Piper voice, embedding, and optional image
  models
- Optional GPU support for faster inference; CPU fallbacks are used when
  supported

Voice engines and image backends may require additional model files or native
runtime packages. The application continues to provide non-voice and
non-diffusion features when optional components are unavailable.

## Installation

From the repository root, with Python 3.11 installed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
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

Optional Piper voice files go in `.ultron/voices/`.

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
- Voice capture and speech output settings.
- Image backend and output behavior.
- Permission and security settings.

The `.ultron/` directory is local application state. It may contain model
files, voice files, workspace indexes, generated images, permissions, and
runtime logs. These files should not be committed to source control.
