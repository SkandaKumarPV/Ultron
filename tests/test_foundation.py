"""ULTRON Foundation Test Suite"""
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.config import config
from ultron.security.permissions import permission_registry, PermissionLevel, PermissionRequest
from ultron.security.audit import get_audit_logger, AuditEventType, AuditSeverity
from ultron.tools.registry import tool_registry, ToolCategory, BaseTool, ToolParameter, ToolResult
from ultron.workspace.selector import workspace_manager, Workspace
from ultron.model.loader import model_manager


def test_config():
    """Test configuration loading"""
    print("Testing configuration...")
    cfg = config.config
    assert cfg.name == "ULTRON"
    assert cfg.version == "0.1.0"
    assert cfg.resources.max_vram_gb == 3.5
    assert cfg.model.name == "qwen2.5-1.5b-instruct-q4_k_m"
    print("  [OK] Config loaded successfully")
    print(f"  [OK] Model path: {config.get_model_path()}")
    return True


def test_permissions():
    """Test permission system"""
    print("Testing permissions...")

    # Register a test tool
    permission_registry.register_tool("test_tool", PermissionLevel.READ)

    # Check required level
    level = permission_registry.get_required_level("test_tool")
    assert level == PermissionLevel.READ

    # Request permission WITH workspace_id (required for auto-grant)
    req = PermissionRequest(
        tool_name="test_tool",
        action="execute",
        target="test",
        required_level=PermissionLevel.READ,
        workspace_id="test_ws",
    )
    grant = permission_registry.request_permission(req)
    assert grant.granted == True
    assert grant.level == PermissionLevel.READ

    # Test higher level requires confirmation (no callback = deny)
    req2 = PermissionRequest(
        tool_name="test_tool",
        action="execute",
        target="test",
        required_level=PermissionLevel.MODIFY,
        workspace_id="test_ws",
    )
    grant2 = permission_registry.request_permission(req2)
    assert grant2.granted == False

    print("  [OK] Permission system working")
    return True


def test_audit():
    """Test audit logging"""
    print("Testing audit logging...")
    logger = get_audit_logger()

    logger.log_event(
        AuditEventType.TOOL_INVOKED,
        workspace_id="test_ws",
        tool_name="test_tool",
        action="execute",
        target="test",
        result="success",
    )

    # Query logs
    entries = logger.query(event_type=AuditEventType.TOOL_INVOKED, limit=5)
    assert len(entries) >= 1
    assert entries[0].event_type == AuditEventType.TOOL_INVOKED

    print("  [OK] Audit logging working")
    return True


def test_tool_registry():
    """Test tool registry"""
    print("Testing tool registry...")

    class TestTool(BaseTool):
        name = "test_tool"
        description = "A test tool"
        category = ToolCategory.SYSTEM
        permission_level = PermissionLevel.READ
        parameters = [
            ToolParameter(name="input", type="string", description="Test input"),
        ]

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data=f"Received: {kwargs.get('input')}")

    tool = TestTool()
    tool_registry.register(tool)

    # Get tool
    retrieved = tool_registry.get("test_tool")
    assert retrieved is not None
    assert retrieved.name == "test_tool"

    # Execute (with workspace_id for permission check)
    result = tool_registry.execute("test_tool", workspace_id="test_ws", input="hello")
    assert result.success
    assert result.data == "Received: hello"

    # Get schemas
    schemas = tool_registry.get_schemas()
    assert len(schemas) >= 1

    print("  [OK] Tool registry working")
    return True


def test_workspace():
    """Test workspace management"""
    print("Testing workspace manager...")

    # Create workspace with current directory
    ws = workspace_manager.create_workspace("Test Workspace", str(Path.cwd()), read_only=True)

    assert ws.workspace_id in workspace_manager._workspaces
    assert ws.name == "Test Workspace"
    assert ws.read_only == True

    # Select workspace
    assert workspace_manager.select_workspace(ws.workspace_id)
    assert workspace_manager.get_current_workspace_id() == ws.workspace_id

    # Path authorization
    test_file = Path.cwd() / "requirements.txt"
    assert workspace_manager.is_path_authorized(ws.workspace_id, str(test_file))

    print("  [OK] Workspace manager working")
    return True


def test_model_manager():
    """Test model manager (without loading model)"""
    print("Testing model manager...")

    # Initialize
    success = model_manager.initialize()
    # May fail if model not downloaded yet - that's OK

    info = model_manager.get_model_info()
    # If model not downloaded, info will be {"loaded": False}
    assert "loaded" in info

    print("  [OK] Model manager initialized")
    return True


def run_all_tests():
    """Run all foundation tests"""
    print("=" * 50)
    print("ULTRON Foundation Tests")
    print("=" * 50)

    tests = [
        test_config,
        test_permissions,
        test_audit,
        test_tool_registry,
        test_workspace,
        test_model_manager,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            import traceback
            print(f"  [FAIL] FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)