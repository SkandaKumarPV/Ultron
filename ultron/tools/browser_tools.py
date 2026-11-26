"""Execute an explicit ChatGPT browser request in a separate Chrome profile."""

from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

from ultron.config import config
from ultron.security.permissions import PermissionLevel
from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.tools.pc_control import LaunchAppTool


class AskInBrowserTool(BaseTool):
    name = "ask_in_browser"
    description = "Ask an explicit question on ChatGPT in Chrome and read the visible reply."
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.EXTERNAL_TRANSMIT
    parameters = [ToolParameter(name="prompt", type="string", description="The user's explicit browser prompt")]
    _browser_lock = Lock()

    def execute(self, prompt: str) -> ToolResult:
        if error := LaunchAppTool.permission_error("chrome"):
            return ToolResult(success=False, error=error)
        try:
            from playwright.sync_api import Error, TimeoutError, sync_playwright
        except ImportError:
            return ToolResult(success=False, error="Install Playwright in ULTRON's Python environment to use browser workflows.")
        if not self._browser_lock.acquire(blocking=False):
            return ToolResult(success=False, error="A ChatGPT browser request is already running.")
        try:
            with sync_playwright() as browser:
                profile = config.resolve_path("cache/chatgpt-browser")
                context = browser.chromium.launch_persistent_context(str(profile), channel="chrome", headless=False)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=25000)
                    composer = page.locator("#prompt-textarea").first
                    composer.wait_for(state="visible", timeout=20000)
                    if urlsplit(page.url).hostname != "chatgpt.com":
                        return ToolResult(success=False, error="ChatGPT redirected to a sign-in page.")
                    replies = page.locator('[data-message-author-role="assistant"]')
                    previous = replies.count()
                    composer.fill(prompt)
                    page.get_by_test_id("send-button").click(timeout=10000)
                    reply = replies.nth(previous)
                    reply.wait_for(state="visible", timeout=45000)

                    deadline = monotonic() + 90
                    text, changed = "", monotonic()
                    while monotonic() < deadline:
                        current = reply.inner_text(timeout=5000).strip()
                        if current != text:
                            text, changed = current, monotonic()
                        if text and monotonic() - changed > 2 and not page.get_by_test_id("stop-button").is_visible():
                            return ToolResult(success=True, data=f"ChatGPT replied:\n\n{text}",
                                              verification={"provider": "ChatGPT", "url": page.url})
                        page.wait_for_timeout(300)
                    return ToolResult(success=False, error="ChatGPT did not finish its reply within the time limit.")
                finally:
                    context.close()
        except TimeoutError:
            return ToolResult(success=False, error="ChatGPT did not respond. Check whether the site needs sign-in or is blocking access in Chrome.")
        except Error:
            return ToolResult(success=False, error="Could not complete the ChatGPT workflow. Check that Chrome is installed and allowed to run.")
        finally:
            self._browser_lock.release()


tool_registry.register(AskInBrowserTool())
