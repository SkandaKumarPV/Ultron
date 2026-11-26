from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ultron.kernel import Kernel, RequestType, UltronRequest
from ultron.workspace import analyzer


@pytest.fixture
def selected_workspace(tmp_path, monkeypatch):
    selected = SimpleNamespace(path=tmp_path, index_status="ready")
    monkeypatch.setattr(analyzer.workspace_manager, "get_workspace", lambda _: selected)
    return selected


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "can u expalin about the autoencodes which is in the folder can u summarize that document",
    "Summarize Autoencoders.txt.",
])
async def test_named_document_bypasses_unrelated_index_matches(selected_workspace, monkeypatch, question):
    (selected_workspace.path / "Autoencoders.txt").write_text(
        "Autoencoders learn to reconstruct their inputs from a latent representation.", encoding="utf-8"
    )
    (selected_workspace.path / "Generative AI.txt").write_text("A different source.", encoding="utf-8")
    monkeypatch.setattr(analyzer.local_index, "query", lambda *_: pytest.fail("Queried the broad index"))
    monkeypatch.setattr(analyzer.model_manager, "is_loaded", lambda: True)
    generate = AsyncMock(return_value="An autoencoder reconstructs its input.")
    monkeypatch.setattr(analyzer.model_manager, "generate_async", generate)

    kernel = Kernel()
    kernel._workspace_manager = analyzer.workspace_manager
    response = await kernel._route_request(UltronRequest(
        "document", RequestType.WORKSPACE_QUERY, question, workspace_id="selected"
    ))

    assert response.success
    assert response.metadata["context_sources"] == ["Autoencoders.txt"]
    prompt = generate.await_args.args[0]
    assert "latent representation" in prompt
    assert "A different source." not in prompt


@pytest.mark.asyncio
async def test_empty_named_file_reports_extraction_failure(selected_workspace, monkeypatch):
    (selected_workspace.path / "Autoencoders.txt").touch()
    monkeypatch.setattr(analyzer.local_index, "query", lambda *_: pytest.fail("Queried the broad index"))
    result = await analyzer.workspace_analyzer.query_async("selected", "Summarize Autoencoders.txt")
    assert "Autoencoders.txt" in result.answer
    assert "could not extract readable text" in result.answer


@pytest.mark.asyncio
async def test_ambiguous_titles_show_relative_paths(selected_workspace):
    for name in ("Week 1", "Week 2"):
        path = selected_workspace.path / name
        path.mkdir()
        (path / "Autoencoders.txt").touch()
    result = await analyzer.workspace_analyzer.query_async("selected", "Summarize the autoencoders document")
    assert "Which document did you mean?" in result.answer
    assert "Week 1/Autoencoders.txt" in result.answer
    assert "Week 2/Autoencoders.txt" in result.answer
