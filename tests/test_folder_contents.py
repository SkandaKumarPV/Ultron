from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from ultron.automation import wants_folder_contents
from ultron.kernel import Kernel, RequestType, UltronRequest
from ultron.workspace.parser import parser_registry


@pytest.mark.parametrize("text", [
    "what and all is there in this folde",
    "What's inside this folder?",
    "List all the files in this folder.",
])
def test_inventory_phrasing(text):
    assert wants_folder_contents(text)
    assert not wants_folder_contents("Summarize the presentation in this folder.")


@pytest.mark.asyncio
async def test_inventory_reads_selected_folder_before_indexing(tmp_path):
    (tmp_path / "Slides").mkdir()
    (tmp_path / "Presentation.pptx").touch()
    (tmp_path / "Photo.jpg").touch()
    (tmp_path / ".env").touch()
    kernel = Kernel()
    selected = SimpleNamespace(path=tmp_path, index_status="pending")
    kernel._workspace_manager = SimpleNamespace(get_workspace=lambda _: selected)
    request = UltronRequest("inventory", RequestType.TEXT_COMMAND,
                            "what and all is there in this folde", workspace_id="selected")
    with patch("ultron.workspace.analyzer.model_manager.load", side_effect=AssertionError("No model needed")):
        response = await kernel._route_request(request)
    assert response.success and response.metadata["route"] == "folder contents"
    assert "2 files and 1 folder" in response.response_text
    assert all(name in response.response_text for name in ("Slides/", "Presentation.pptx", "Photo.jpg"))
    assert ".env" not in response.response_text


def test_powerpoint_slide_text_is_read_in_slide_order(tmp_path):
    deck = tmp_path / "Slides.pptx"
    template = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                '<a:p><a:r><a:t>{}</a:t></a:r></a:p></p:sld>')
    with ZipFile(deck, "w") as archive:
        archive.writestr("ppt/slides/slide10.xml", template.format("Final review"))
        archive.writestr("ppt/slides/slide2.xml", template.format("Sample title"))
    chunks = list(parser_registry.parse(deck))
    assert [chunk.content for chunk in chunks] == ["Slide 2\nSample title", "Slide 10\nFinal review"]
