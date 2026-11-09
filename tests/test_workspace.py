"""ULTRON Workspace Integration Test"""
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.config import config
from ultron.workspace.selector import workspace_manager
from ultron.workspace.parser import parser_registry
from ultron.workspace.chunker import chunker
from ultron.workspace.embedder import embedder
from ultron.workspace.retriever import retriever
from ultron.workspace.analyzer import workspace_analyzer
from ultron.tools.workspace_tools import *
from ultron.tools.pc_control import *
from ultron.security.audit import get_audit_logger, AuditEventType


def test_parser_registry():
    """Test parser registry with sample files"""
    print("Testing parser registry...")

    # Create test files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Text file
        txt_file = tmp / "test.txt"
        txt_file.write_text("This is a test document.\nIt has multiple lines.\nFor testing the parser.")

        # Python file
        py_file = tmp / "test.py"
        py_file.write_text("# Test Python file\ndef hello():\n    print('Hello, World!')\n\nif __name__ == '__main__':\n    hello()")

        # Test text parser
        chunks = list(parser_registry.parse(txt_file))
        assert len(chunks) == 1
        assert "test document" in chunks[0].content

        # Test Python parser
        chunks = list(parser_registry.parse(py_file))
        assert len(chunks) == 1
        assert "def hello" in chunks[0].content

        print("  [OK] Parser registry working")
    return True


def test_chunker():
    """Test chunking"""
    print("Testing chunker...")

    from ultron.workspace.parser import ParsedChunk

    # Long text that needs chunking
    long_text = " ".join([f"Sentence {i}." for i in range(100)])

    chunk = ParsedChunk(
        content=long_text,
        source_path="test.txt",
        chunk_index=0,
        start_char=0,
        end_char=len(long_text),
    )

    chunks = chunker.chunk(chunk)
    assert len(chunks) > 1
    assert all(len(c.content) <= 600 for c in chunks)  # chunk_size + overlap

    print("  [OK] Chunker working")
    return True


def test_workspace_flow():
    """Test full workspace flow"""
    print("Testing workspace flow...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Create test documents
        doc1 = tmp / "doc1.txt"
        doc1.write_text("""
Project Alpha Status Report

Date: 2024-01-15
Status: In Progress

Key Achievements:
- Completed backend API design
- Database schema finalized
- Authentication module implemented

Next Steps:
- Frontend integration
- User acceptance testing
- Documentation
""")

        doc2 = tmp / "doc2.txt"
        doc2.write_text("""
Project Alpha Technical Notes

Architecture: Microservices
Database: PostgreSQL
API: RESTful with JSON

Modules:
1. Auth Service - JWT based authentication
2. Data Service - CRUD operations
3. Notification Service - Email and SMS

Known Issues:
- Rate limiting not yet implemented
- Need to add caching layer
""")

        # Create workspace
        ws = workspace_manager.create_workspace("Test Project", str(tmp), read_only=True)
        workspace_manager.select_workspace(ws.workspace_id)

        # Index workspace
        result = workspace_analyzer.index_workspace(ws.workspace_id)
        assert result["success"], f"Indexing failed: {result}"
        assert result["chunks_indexed"] > 0

        # Query workspace
        answer_result = workspace_analyzer.query(ws.workspace_id, "What is the project status?")
        assert "In Progress" in answer_result.answer or "progress" in answer_result.answer.lower()

        answer_result2 = workspace_analyzer.query(ws.workspace_id, "What database is used?")
        assert "PostgreSQL" in answer_result2.answer or "postgresql" in answer_result2.answer.lower()

        # Test tool
        from ultron.tools.registry import tool_registry
        tool_result = tool_registry.execute("query_workspace", workspace_id=ws.workspace_id, question="What are the key achievements?")
        assert tool_result.success
        assert "backend API" in tool_result.data["answer"] or "API" in tool_result.data["answer"]

        print("  [OK] Workspace flow working")
    return True


def test_pc_control_tools():
    """Test PC control tools"""
    print("Testing PC control tools...")

    from ultron.tools.registry import tool_registry

    # Test list_directory (safe)
    result = tool_registry.execute("list_directory", path=str(Path.cwd()))
    assert result.success
    assert "items" in result.data

    # Test launch_app with notepad (allowed)
    result = tool_registry.execute("launch_app", app_name="notepad", args=[])
    # May fail if notepad not in PATH, but should not crash
    print(f"  Launch notepad: {result.success}")

    print("  [OK] PC control tools working")
    return True


def test_embedder_retriever():
    """Test embedder and retriever"""
    print("Testing embedder and retriever...")

    # Initialize embedder
    embedder.initialize()

    # Test embedding
    embeddings = embedder.embed(["Hello world", "Test sentence"])
    assert embeddings.shape == (2, 384)  # all-MiniLM-L6-v2 dimension

    # Test retriever init
    retriever.initialize()

    print("  [OK] Embedder and retriever working")
    return True


def run_all_tests():
    """Run all integration tests"""
    print("=" * 50)
    print("ULTRON Workspace Integration Tests")
    print("=" * 50)

    tests = [
        test_parser_registry,
        test_chunker,
        test_embedder_retriever,
        test_workspace_flow,
        test_pc_control_tools,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            import traceback
            print(f"  [FAIL] {test.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)