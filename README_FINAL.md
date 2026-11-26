# ULTRON - Complete Application Guide

ULTRON is a local, voice-first personal AI assistant for Windows. It combines
local language-model inference with workspace analysis, image generation, voice
interaction, automation, news monitoring, PC tools, and a desktop control
center.

The application is designed around local processing. Workspace files,
permissions, indexes, generated images, voice models, and runtime state remain
on the local computer unless a feature explicitly accesses a public service,
such as live RSS news, weather, or USGS earthquake data.

## What ULTRON can do

- Chat with a local language model.
- Select a folder and ask questions about its files.
- Index source code, documents, spreadsheets, PDFs, and text files.
- Generate images from natural-language prompts.
- Use push-to-talk voice commands.
- Listen for "Hey ULTRON" after the assistant is armed.
- Convert speech to text and speak responses aloud.
- Create and manage tasks and reminders.
- Run workspace tests and inspect Git status and diffs.
- Explain and analyze code.
- Perform safe workspace file edits through registered tools.
- Show live news from multiple RSS sources.
- Display weather and disaster information.
- Control selected Windows applications and folders through permissions.
- Record audit events for important actions.

## Desktop control center

`ultron_control_center.py` launches the main Qt desktop application. It contains
these tabs:

### Home

Shows the assistant overview and component status, including the language
model, voice system, image generator, registered tools, and workspace state.

### Chat

The Chat tab is the main conversational interface. It supports:

- Normal questions and commands.
- Image-generation requests such as "create an image of a futuristic city".
- Folder selection with **Browse**.
- Folder indexing with **Load & Index**.
- A **Use selected folder context for chat** option.
- Voice transcripts routed into the same conversation.

When folder context is enabled, questions are sent through the workspace
retrieval pipeline instead of being answered only from the general model.

### Voice

The Voice tab controls local speech input and output:

- Start and stop the voice engine.
- Push-to-talk recording.
- Optional hands-free wake-word listening.
- ElevenLabs Scribe v3 transcription setup.
- Text-to-speech voice selection.
- Spoken reply settings.
- Microphone diagnostics.

### Image

The Image tab provides:

- Prompt entry.
- Style selection.
- Width and height controls.
- Seed and generation settings.
- Backend status.
- Image preview.
- Recent image history.
- An option to open the generated-image folder.

### Automation

The Automation tab is not limited to calendars. It provides:

- Natural-language quick commands.
- Task creation and task listing.
- Priority and due-date support.
- One-time and recurring reminders.
- Example commands for creating tasks, setting reminders, running tests,
  checking Git, analyzing code, and explaining files.

Example buttons load editable commands into the Quick Command field. The user
can review the command before executing it.

### Monitor

The Monitor tab provides live public-source information:

- Multi-source RSS news headlines.
- Publication times, source names, summaries, and clickable links.
- USGS significant earthquake information.
- Weather lookups.
- Manual refresh controls.

The news monitor currently reads public feeds from sources such as BBC, AP,
NPR, The Guardian, and the New York Times when those feeds are reachable.

### Permissions

Permissions control which applications, folders, and features ULTRON may use.
The permission system is checked by tools before potentially sensitive actions
such as modifying files, launching programs, or controlling the PC.

### Modules and Settings

These tabs expose registered modules, feature availability, configuration
options, and application preferences.

## Application architecture

```text
ultron_main.py
    Command-line entry point

ultron_control_center.py
    Desktop Qt control center

ultron/
    kernel.py
        Central request router and response coordinator
    config.py
        Configuration loading and local path resolution
    model/
        Local language-model loading and inference
    image/
        Image generation backends and output management
    voice/
        Speech recognition, text-to-speech, recording, and wake words
    workspace/
        Parsing, chunking, embeddings, indexing, and retrieval
    tools/
        Chat, PC, image, voice, task, workspace, and automation tools
    tasks/
        SQLite task and reminder storage
    security/
        Permissions and audit logging
    super_monitor/
        News, weather, disaster, freshness, and evidence monitoring
    performance/
        Caching, metrics, and resilience helpers
    gui/
        Reusable desktop UI components
```

## Request flow

Most interactions follow this path:

1. The user types a message, speaks a command, or selects a GUI action.
2. The input becomes an `UltronRequest`.
3. The kernel classifies and routes the request.
4. Direct tool patterns are handled by registered tools when appropriate.
5. General questions are sent to the local language model.
6. Workspace questions use retrieved document context.
7. Tool results, model responses, and errors are returned to the UI.
8. Important operations are written to the audit log.

The same kernel is shared by the desktop chat, voice assistant, command-line
entry point, and automation quick commands.

## Workspace analysis

Workspace analysis is a local retrieval-augmented generation workflow.

### Indexing flow

1. Select a folder in Chat or create one through the workspace tools.
2. ULTRON creates a read-only workspace record by default.
3. Supported files are discovered and filtered by size.
4. Parsers extract text from supported file types.
5. Content is divided into overlapping chunks.
6. SQLite stores a full-text index isolated by workspace ID.

Supported formats include:

```text
.txt .md .py .js .ts .json .yaml .yml
.csv .pdf .docx .xlsx .log
```

### Question flow

1. The index looks for relevant passages and filenames.
2. The local language model receives the retrieved context.
3. ULTRON answers from the indexed files. If the model cannot load, it returns
   matching excerpts.

If a folder has not been indexed, ULTRON reports that it is not ready instead of
silently answering as though the folder were available.

## Image generation

Image generation is implemented in `ultron/image/` and exposed through image
tools.

### Backends

- **Stable Diffusion:** used when the configured diffusion model and runtime
  are available.
- **Procedural fallback:** provides local lightweight image generation when
  diffusion is unavailable.

### Image features

- Natural-language prompts.
- Configurable size.
- Style selection.
- Optional seed.
- Diffusion steps and guidance controls.
- Generation status.
- Recent image history.
- Automatic filenames.

Generated images are stored under:

```text
.ultron/generated_images/
```

## Voice assistant

Voice functionality is isolated in `ultron/voice/`.

PyAudio captures speech. ElevenLabs Scribe v3 turns the recording into text.
The kernel handles the transcript, and Piper or Windows SAPI can speak the
reply. See [voice setup](README.md#voice-assistant) for the API key and launch
commands.

- **Push-to-talk**. Hold `Alt+grave` in the control center or use **Hold to
  Speak** in the Voice tab after starting the voice engine. Release to submit.
- **Wake phrase**. Press `Alt+Shift+V` or select **Arm 'Hey ULTRON'**, then say
  "Hey ULTRON" and the command. Enabling voice starts wake listening.
  Detected speech is transcribed by ElevenLabs to check the wake phrase.
  Saying the wake phrase alone starts a capture for the command.

Use **Diagnose Microphone** in the Voice tab to check Windows mic access.

## Automation and tools

### Tasks and reminders

Tasks are stored in a local SQLite database and support:

- Title and description.
- Low, medium, high, and urgent priority.
- Due dates.
- Tags.
- Pending, in-progress, completed, cancelled, and overdue states.

Reminders support:

- A trigger time.
- A message.
- Daily, weekly, or monthly recurrence.
- Windows Task Scheduler integration where configured.

### Workspace automation

Available workspace automation includes:

- Explain code.
- Analyze code structure.
- Run tests.
- Generate documentation.
- Safe file writes with backups and checkpoints.
- Safe search-and-replace edits.
- Git status.
- Git diff.
- Git commits.

### Advanced automation

Advanced coding tools include:

- Rename symbols.
- Extract methods.
- Inline variables.
- Generate tests.
- Build dependency graphs.
- Scan for common security issues.
- Assist with debugging.

### PC control

Depending on permissions, ULTRON can list directories, open files and folders,
launch applications, and perform other registered computer-control actions.

## Live monitoring

The Super Monitor is implemented under `ultron/super_monitor/`.

It includes:

- RSS news aggregation.
- Source deduplication.
- Publication timestamps.
- News search and filtering.
- Weather monitoring.
- USGS disaster and earthquake monitoring.
- Source freshness tracking.
- Event timeline support.
- Evidence organization.

Live feeds require an internet connection and can fail when a source is
unavailable, rate-limited, or blocked. Other local features continue to work.

## Local data and configuration

Runtime data is stored under `.ultron/`:

```text
.ultron/
├── config/              Configuration and permissions
├── models/              Local language and optional wake-word models
├── voices/              Piper voice files
├── search/              Workspace full-text indexes
├── generated_images/    Generated image output
├── news_cache/          News-related cache data
├── workspaces/          Workspace metadata
└── tasks.db             Task and reminder database
```

The runtime directory is local application state and should not be committed
to source control. The repository `.gitignore` excludes generated output,
caches, logs, databases, and downloaded model artifacts.

## Installation

Recommended environment: Windows with Python 3.11.

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Initialize the core application:

```powershell
python ultron_main.py --init-only
```

Download the default local language model:

```powershell
python scripts/download_model.py qwen2.5-1.5b-instruct-q4_k_m
```

Voice input requires PyAudio and ElevenLabs credentials. Piper voice files
are optional when Windows speech output is available.

## Running ULTRON

Start interactive text mode:

```powershell
python ultron_main.py
```

Run a single command:

```powershell
python ultron_main.py "What files are in my workspace?"
```

Start the desktop control center:

```powershell
python ultron_control_center.py
```

## Testing

Run all tests:

```powershell
python -m pytest tests
```

Run the foundation tests:

```powershell
python -m pytest tests/test_foundation.py -q
```

Voice and microphone tests may require Windows audio hardware and permissions.
Model-heavy tests may take longer and require downloaded model files.

## Current limitations

- Calendar synchronization with Google Calendar, Outlook, or iCal is not
  currently implemented.
- Live news, weather, and disaster data depend on external network services.
- Diffusion image generation requires additional model/runtime resources.
- Voice recognition requires compatible audio hardware and ElevenLabs access.
- While wake listening is enabled, detected speech is sent to ElevenLabs to
  check for “Hey ULTRON”.
- Some optional monitoring components may be unavailable when their third-party
  dependencies are not installed.

## Security and privacy

ULTRON uses workspace isolation, read-only workspace defaults, permission
checks, and audit events to make local automation more controlled.

Users should still review commands before allowing file modifications,
application launches, Git commits, or other actions that change the computer.
Never place API keys, passwords, private tokens, or other secrets in the
repository or in prompts.
