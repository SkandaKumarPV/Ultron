"""Focused tests for wake-word transcript routing and common app commands."""
from unittest.mock import patch
from types import SimpleNamespace

from ultron.voice.assistant import VoiceAssistant, strip_wake_phrase


def test_strip_wake_phrase_keeps_command():
    assert strip_wake_phrase("hey ultron open notepad") == "open notepad"
    assert strip_wake_phrase("Hey Ultra on open notepad") == "open notepad"
    assert strip_wake_phrase("Hailtron open notepad") == "open notepad"


def test_common_app_command_is_dispatched_before_kernel():
    assistant = VoiceAssistant()
    assistant._normalize_voice_command = lambda transcript: "open vs code"
    with patch("ultron.kernel.kernel.initialize"), \
            patch("ultron.kernel.kernel.process", return_value=SimpleNamespace(
                success=False, response_text="failed")), \
            patch.object(assistant, "_direct_tool_dispatch", return_value="Opening vs code.") as dispatch:
        response = assistant._execute("open vs cod", transcript="open vs cod")

    assert response == "Opening vs code."
    dispatch.assert_called_once_with("open vs code")


def test_voice_transcript_is_sent_to_llm_normalizer():
    assistant = VoiceAssistant()
    with patch("ultron.model.loader.model_manager.is_loaded", return_value=True), \
            patch("ultron.model.loader.model_manager.chat_completion",
                  return_value="open notepad") as complete:
        normalized = assistant._normalize_voice_command("open note pad")

    assert normalized == "open notepad"
    assert complete.call_args.args[0][1]["content"] == "open note pad"


def test_normalizer_prompt_requires_plain_corrected_command():
    assistant = VoiceAssistant()
    with patch("ultron.model.loader.model_manager.is_loaded", return_value=True), \
            patch("ultron.model.loader.model_manager.chat_completion",
                  return_value="open notepad") as complete:
        assistant._normalize_voice_command("open note pad")

    system_prompt = complete.call_args.args[0][0]["content"]
    assert "Return only the corrected command" in system_prompt
    assert "Do not explain" in system_prompt
    assert "Correct application names and aliases" in system_prompt


def test_voice_classifier_normalizes_commands_only():
    assistant = VoiceAssistant()
    with patch("ultron.model.loader.model_manager.is_loaded", return_value=True), \
            patch("ultron.model.loader.model_manager.chat_completion",
                  return_value="TYPE: COMMAND\nTEXT: open google chrome") as complete:
        is_command, prepared = assistant._prepare_voice_input("open goggle chrome")

    assert is_command is True
    assert prepared == "open google chrome"
    prompt = complete.call_args.args[0][0]["content"]
    assert "COMMAND or QUESTION" in prompt
    assert "If COMMAND" in prompt
    assert "If QUESTION" in prompt


def test_voice_classifier_preserves_questions():
    assistant = VoiceAssistant()
    question = "What files are in my workspace?"
    with patch("ultron.model.loader.model_manager.is_loaded", return_value=True), \
            patch("ultron.model.loader.model_manager.chat_completion",
                  return_value=f"TYPE: QUESTION\nTEXT: {question}"):
        is_command, prepared = assistant._prepare_voice_input(question)

    assert is_command is False
    assert prepared == question


def test_questions_reach_kernel_without_command_normalization():
    assistant = VoiceAssistant()
    question = "What files are in my workspace?"
    response = SimpleNamespace(success=True, response_text="workspace answer")
    with patch.object(
        assistant,
        "_prepare_voice_input",
        return_value=(False, question),
    ), patch.object(
        assistant,
        "_normalize_voice_command",
        side_effect=AssertionError("questions must not be normalized"),
    ), patch("ultron.kernel.kernel.initialize"), \
            patch("ultron.kernel.kernel.process", return_value=response):
        assert assistant._execute(question, transcript=question) == "workspace answer"


def test_direct_dispatch_resolves_google_chrome_alias():
    assistant = VoiceAssistant()
    with patch("subprocess.Popen") as popen, \
            patch("shutil.which", return_value=None), \
            patch("ultron.voice.assistant.os.path.exists", return_value=True):
        response = assistant._direct_tool_dispatch("open google chrome")

    assert response == "Opening google chrome."
    assert popen.call_args.args[0] == [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    ]


def test_transcript_reaches_response_and_tts():
    assistant = VoiceAssistant()
    assistant._execute = lambda command, **kwargs: f"Done: {command}"
    assistant.speak = lambda text, block=False: True

    response = assistant.handle_transcript("hey ultron open notepad")

    assert response == "Done: open notepad"
    assert assistant.last_transcript == "hey ultron open notepad"
    assert assistant.last_response == "Done: open notepad"


def test_push_to_talk_release_routes_complete_transcript():
    assistant = VoiceAssistant()
    assistant.enabled = True
    assistant.state = assistant.state.DORMANT
    routed = []
    assistant.handle_transcript = lambda text: routed.append(text)

    def fake_record(max_seconds, silence_ms, stop_event=None):
        assert stop_event is not None
        stop_event.wait(1)
        return "hey ultron open notepad"

    assistant._record_and_transcribe = fake_record
    with patch("ultron.voice.assistant.wake_word_detector.stop"):
        assert assistant.start_push_to_talk()
        assert assistant.stop_push_to_talk() == "hey ultron open notepad"

    assert routed == ["hey ultron open notepad"]


def test_stt_retries_on_cpu_when_cuda_runtime_is_missing(tmp_path):
    assistant = VoiceAssistant()
    assistant._stt = FakeCudaThenCpuModel()

    with patch("ultron.voice.pipeline.voice_pipeline._load_stt") as load_stt:
        def load_cpu_model(force_cpu=False):
            from ultron.voice.pipeline import voice_pipeline
            voice_pipeline._stt_model = FakeCpuModel()

        load_stt.side_effect = load_cpu_model
        assert assistant._transcribe_bytes(b"\0\0" * 1600) == "hello ultron"
        load_stt.assert_called_once_with(force_cpu=True)


class FakeCudaThenCpuModel:
    def transcribe(self, *args, **kwargs):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


class FakeCpuModel:
    def transcribe(self, *args, **kwargs):
        return iter([SimpleNamespace(text="hello ultron")]), SimpleNamespace(language="en")