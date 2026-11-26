from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from ultron.automation import explicit_action, wants_workspace
from ultron.kernel import Kernel, RequestType, UltronRequest
from ultron.tools.registry import ToolResult
from ultron.voice.assistant import AssistantState, VoiceAssistant


@pytest.mark.asyncio
async def test_browser_workflow_runs_when_folder_context_is_enabled():
    request = UltronRequest(
        "test", RequestType.WORKSPACE_QUERY,
        "open Chrome search chatpgt and ask what is ROS and read it",
        workspace_id="local",
    )
    execute = AsyncMock(return_value=ToolResult(success=True, data="ChatGPT replied: Robot Operating System."))
    with patch("ultron.kernel.tool_registry.execute_async", execute):
        result = await Kernel()._route_request(request)
    execute.assert_awaited_once_with("ask_in_browser", prompt="what is ROS")
    assert result.metadata["route"] == "browser"
    assert "Robot Operating System" in result.response_text


def test_file_prompt_is_kept_local_even_with_a_browser_mention():
    text = "Ask ChatGPT to summarize my folder"
    assert wants_workspace(text)
    assert explicit_action(text) is None
    assert explicit_action("create a high priority task to check the build") == (
        "create_task", {"title": "check the build", "priority": "high"}
    )


def test_transcribed_cgpt_command():
    assert explicit_action("Hey, welcome. Open CGPT and search what is wrong.") == (
        "ask_in_browser", {"prompt": "what is wrong."}
    )


@pytest.mark.asyncio
async def test_chrome_search_is_not_routed_to_the_file_opener():
    request = UltronRequest(
        "test", RequestType.VOICE_COMMAND,
        "Okay. Open Chrome and search Chargebee. In the search Vertizos.",
    )
    execute = AsyncMock(return_value=ToolResult(success=True, data="Opened Chrome"))
    with patch("ultron.kernel.tool_registry.execute_async", execute):
        result = await Kernel()._route_request(request)
    assert result.success
    assert execute.await_args.args == ("launch_app",)
    assert execute.await_args.kwargs["app_name"] == "chrome"
    query = parse_qs(urlsplit(execute.await_args.kwargs["args"][0]).query)
    assert query == {"q": ["Chargebee Vertizos"]}


def test_starting_voice_turns_on_the_wake_listener_until_stopped():
    assistant = VoiceAssistant()
    with patch.object(assistant, "initialize", return_value=True), patch.object(
        assistant, "_start_wake_listening"
    ) as listen:
        assert assistant.start()
        assert assistant.state is AssistantState.ARMED
        assert assistant._continuous_wake
        listen.assert_called_once()
        assistant.stop()
        assert assistant._wake_stop.is_set()
        assert assistant.state is AssistantState.OFF
