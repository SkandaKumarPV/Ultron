#!/usr/bin/env python3
"""ULTRON Main Entry Point"""
import sys
import argparse
import signal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from ultron.config import config
from ultron.kernel import kernel
from ultron.model.loader import model_manager
from ultron.workspace.selector import workspace_manager
from ultron.security.audit import get_audit_logger, AuditEventType, AuditSeverity
from ultron.tools.registry import tool_registry
from ultron.tools.pc_control import *
from ultron.tools.workspace_tools import *


def setup_signal_handlers():
    """Handle graceful shutdown"""
    def signal_handler(sig, frame):
        print("\nShutting down ULTRON...")
        kernel.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def print_banner():
    """Print ULTRON banner"""
    cfg = config.config
    print(f"""
+============================================================+
|                      ULTRON v{cfg.version}                         |
|         Voice-First Personal AI Assistant                  |
|         Private Workspace Intelligence                     |
+============================================================+
""")


def print_status():
    """Print system status"""
    print(kernel.get_status())
    print()


def run_text_mode():
    """Run interactive text mode"""
    print("ULTRON Text Mode - Type 'help' for commands, 'exit' to quit\n")

    # Initialize kernel
    kernel.initialize()

    # Load model
    print("Loading model...")
    if not model_manager.load():
        print("WARNING: Model not loaded. Some features unavailable.")
    else:
        print("Model loaded successfully.")

    while True:
        try:
            user_input = input("> ").strip()
            if not user_input:
                continue

            if user_input.lower() in ('exit', 'quit', 'q'):
                break

            if user_input.lower() == 'help':
                print("""
Commands:
  help           - Show this help
  status         - Show system status
  workspace      - Workspace commands
  model          - Model commands
  tools          - List available tools
  exit/quit/q    - Exit ULTRON
  
Or just ask a question or give a command naturally.
""")
                continue

            if user_input.lower() == 'status':
                print_status()
                continue

            if user_input.lower() == 'tools':
                tools = tool_registry.get_all()
                for t in tools:
                    print(f"  {t.name} ({t.category.value}): {t.description}")
                continue

            # Process through kernel
            from ultron.kernel import UltronRequest, RequestType
            request = UltronRequest(
                request_id=f"req_{int(__import__('time').time()*1000)}",
                request_type=RequestType.TEXT_COMMAND,
                input_text=user_input,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            response = kernel.process(request)

            if response.spoken_response:
                print(f"ULTRON: {response.spoken_response}")
            else:
                print(f"ULTRON: {response.response_text}")

            if response.tool_results:
                for tr in response.tool_results:
                    if not tr.success:
                        print(f"  [Tool Error: {tr.error}]")

        except KeyboardInterrupt:
            print("\nUse 'exit' to quit")
        except EOFError:
            break
        except Exception as e:
            print(f"Error: {e}")

    kernel.shutdown()
    print("Goodbye!")


def run_single_query(query: str):
    """Run a single query and exit"""
    kernel.initialize()

    print("Loading model...")
    model_manager.load()

    from ultron.kernel import UltronRequest, RequestType
    request = UltronRequest(
        request_id=f"req_{int(__import__('time').time()*1000)}",
        request_type=RequestType.TEXT_COMMAND,
        input_text=query,
        workspace_id=workspace_manager.get_current_workspace_id(),
    )
    response = kernel.process(request)

    import sys
    try:
        print(response.response_text)
    except UnicodeEncodeError:
        print(response.response_text.encode('ascii', 'replace').decode('ascii'))

    if response.tool_results:
        for tr in response.tool_results:
            if tr.verification:
                try:
                    print(f"  [Verified: {tr.verification}]")
                except UnicodeEncodeError:
                    print(f"  [Verified: {str(tr.verification).encode('ascii', 'replace').decode('ascii')}]")

    kernel.shutdown()


def main():
    parser = argparse.ArgumentParser(description="ULTRON - Voice-First AI Assistant")
    parser.add_argument('query', nargs='*', help='Single query to process')
    parser.add_argument('--status', action='store_true', help='Show status and exit')
    parser.add_argument('--init-only', action='store_true', help='Initialize and exit')
    parser.add_argument('--voice', action='store_true', help='Enable voice mode (not yet implemented)')
    args = parser.parse_args()

    setup_signal_handlers()
    print_banner()

    # Load config
    try:
        config.load()
        print("Configuration loaded.")
    except Exception as e:
        print(f"Config error: {e}")
        sys.exit(1)

    if args.status:
        kernel.initialize()
        print_status()
        kernel.shutdown()
        return

    if args.init_only:
        kernel.initialize()
        model_manager.initialize()
        print("Initialized successfully")
        kernel.shutdown()
        return

    if args.query:
        run_single_query(" ".join(args.query))
    else:
        run_text_mode()


if __name__ == "__main__":
    main()