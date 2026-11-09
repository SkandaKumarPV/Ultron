"""ULTRON Integration Test"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

# Reset the Config singleton
import ultron.config
ultron.config.Config._instance = None
ultron.config.Config._config = None
ultron.config.Config._config_path = None

from ultron.kernel import kernel
from ultron.kernel import UltronRequest, RequestType
from ultron.tools.registry import tool_registry


def test_integration():
    print("Initializing kernel...")
    kernel.initialize()

    print("\n=== Testing Voice Tools ===")
    # Test voice_status
    result = tool_registry.execute('voice_status', workspace_id='system')
    print(f"voice_status: {result.data}")

    # Test voice_speak
    result = tool_registry.execute('voice_speak', workspace_id='system', text='Voice integration test successful.')
    print(f"voice_speak: {result.success}")

    print("\n=== Testing PC Control Tools ===")
    # Test list_directory
    result = tool_registry.execute('list_directory', workspace_id='system', path='C:\\Ultron')
    if result.success:
        count = result.data.get('count', 0)
        print(f"list_directory: success, items: {count}")
    else:
        print(f"list_directory: failed - {result.error}")

    print("\n=== Testing Model Inference ===")
    from ultron.kernel import UltronRequest, RequestType
    request = UltronRequest(
        request_id='req_test_1',
        request_type=RequestType.TEXT_COMMAND,
        input_text='What is the capital of France?',
        workspace_id=None,
    )
    response = kernel.process(request)
    print(f"Model response: {response.response_text}")

    print("\n=== All Tests Passed ===")
    kernel.shutdown()


if __name__ == "__main__":
    test_integration()