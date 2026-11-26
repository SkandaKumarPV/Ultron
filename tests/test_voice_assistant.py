"""Focused tests for wake-word transcript routing and common app commands."""
from unittest.mock import patch
from types import SimpleNamespace
from threading import Event

from ultron.voice.assistant import VoiceAssistant, strip_wake_phrase


def test_strip_wake_phrase_keeps_command():
    assert strip_wake_phrase("hey ultron open notepad") == "open notepad"
    assert strip_wake_phrase("Hey Ultra on open notepad") == "open notepad"
    assert strip_wake_phrase("Hailtron open notepad") == "open notepad"


def test_normalized_voice_command_respects_kernel_permission_denial():
    assistant = VoiceAssistant()
    assistant._normalize_voice_command = lambda transcript: "open vs code"
    denied = SimpleNamespace(success=False, response_text="VS Code permission denied")
    with patch.object(assistant, "_prepare_voice_input", return_value=(True, "open vs cod")), patch(
        "ultron.kernel.kernel.initialize"
    ), patch("ultron.kernel.kernel.process", return_value=denied) as process:
        response = assistant._execute("open vs cod")

    assert response == "VS Code permission denied"
    assert process.call_args.args[0].input_text == "open vs code"


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


def test_voice_alias_reaches_the_kernel_without_llm_correction():
    assistant = VoiceAssistant()
    with patch.object(assistant, "_prepare_voice_input", side_effect=AssertionError("Known alias")), patch(
        "ultron.kernel.kernel.initialize"
    ), patch("ultron.kernel.kernel.process", return_value=SimpleNamespace(success=True, response_text="Done")) as process:
        assert assistant._execute("Hey, welcome. Open CGPT and search what is wrong.") == "Done"
    assert process.call_args.args[0].input_text == "Open CGPT and search what is wrong."


def test_transcript_reaches_response_and_tts():
    assistant = VoiceAssistant()
    assistant._execute = lambda command, **kwargs: f"Done: {command}"
    def play(text):
        assert text == "Done: open notepad"
        assert assistant.state == assistant.state.SPEAKING
        return True
    assistant._speak_sync = play

    response = assistant.handle_transcript("hey ultron open notepad")

    assert response == "Done: open notepad"
    assert assistant.last_transcript == "hey ultron open notepad"
    assert assistant.last_response == "Done: open notepad"
    assert assistant.state == assistant.state.DORMANT


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


def test_finished_capture_leaves_recording_state_even_without_release():
    assistant = VoiceAssistant()
    assistant.enabled = True
    assistant.state = assistant.state.DORMANT
    with patch.object(assistant, "_record_and_transcribe", return_value=None):
        assert assistant.start_push_to_talk()
        assistant._manual_thread.join(1)
        assert not assistant._manual_thread.is_alive()
        assert assistant.state == assistant.state.DORMANT


def test_assistant_uses_hosted_transcription():
    assistant = VoiceAssistant()
    audio = b"\0\0" * 1600
    with patch("ultron.voice.assistant.transcribe_pcm", return_value="hello ultron") as transcribe:
        assert assistant._transcribe_bytes(audio) == "hello ultron"
    transcribe.assert_called_once_with(audio, sample_rate=16000, channels=1)


def test_armed_mode_routes_only_verified_wake_phrase():
    assistant = VoiceAssistant()
    with patch.object(assistant, "_record_and_transcribe") as record, patch.object(
        assistant, "handle_transcript"
    ) as handle:
        for text in ("open notepad", "Hey ULTRON, open notepad"):
            assistant.state = assistant.state.ARMED
            record.return_value = text
            assistant._listen_for_wake(Event())
        handle.assert_called_once_with("Hey ULTRON, open notepad")
