"""Recognize explicit workflows before falling back to the local language model."""

import re
from urllib.parse import urlencode

_CHATGPT = re.compile(
    r"^(?:(?:open|launch)\s+(?:google\s+)?chrome[\s,.]+(?:and\s+)?)?"
    r"(?:open|launch|go to|search(?:\s+(?:for|in))?|ask|use)\s+"
    r"(?:chat\s*(?:gpt|gtp|pgt)|c[\s.-]*g[\s.-]*p[\s.-]*t)\b", re.I
)
_WORKSPACE = re.compile(
    r"\b(?:(?:this|the|my|current|selected|local|indexed|project)\s+(?:folder|file|workspace|repo(?:sitory)?)"
    r"|(?:in|from|about|inside)\s+(?:the\s+)?(?:folder|file|workspace|repo(?:sitory)?))\b"
    r"|\b[\w.-]+\.(?:py|md|txt|js|ts|json|yaml|yml|csv|pdf|docx|pptx|xlsx|toml|log)\b", re.I
)


def wants_folder_contents(text):
    return bool(re.fullmatch(
        r"(?:(?:what(?:'s|ʻs|’s|\s+(?:is|are))(?:\s+(?:all\s+)?(?:there|inside))?"
        r"|what\s+and\s+all\s+(?:is|are)\s+there|which\s+files(?:\s+are\s+there)?"
        r"|(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:the\s+)?(?:files|contents|items|subfolders))"
        r"\s+(?:(?:in|of|inside)\s+)?(?:this|the|my|selected|current)\s+(?:folder?|workspace))"
        r"[.!?]*",
        command_text(text), re.I,
    ))


def wants_workspace(text):
    return wants_folder_contents(text) or bool(_WORKSPACE.search(text))


def command_text(text):
    """Remove standalone greetings and polite lead-ins without altering the payload."""
    text = re.sub(r"^(?:(?:ok(?:ay)?|hey(?:,? welcome)?|hello|hi)[,.!:]+\s*)+", "", text.strip(), flags=re.I)
    return re.sub(r"^(?:please|can you|could you)\s+", "", text, flags=re.I)


def explicit_action(text):
    text = command_text(text)
    ai = _CHATGPT.match(text)
    if ai:
        tail = text[ai.end():].lstrip(" ,:;-")
        prompt = re.sub(r"^(?:and\s+)?(?:ask|search(?:\s+for)?)\b"
                        r"(?:\s+it)?(?:\s+to)?\s*[:,-]?\s*", "", tail, flags=re.I)
        prompt = re.sub(r"\s+and\s+(?:read|tell me|say)\b.*$", "", prompt, flags=re.I).strip()
        if wants_workspace(prompt):
            return None
        return ("ask_in_browser", {"prompt": prompt}) if prompt else (
            "launch_app", {"app_name": "chrome", "args": ["https://chatgpt.com/"]}
        )

    search = re.match(
        r"^(?:open|launch)\s+(?:google\s+)?chrome[\s,.]+(?:and\s+)?"
        r"search(?:\s+(?:the web|google))?(?:\s+for)?\s+(.+)$", text, re.I,
    ) or re.match(r"^(?:search\s+(?:the\s+web|google)(?:\s+for)?|google|search\s+for)\s+(.+)$",
                  text, re.I)
    if search and not wants_workspace(search[1]):
        query = re.sub(r"(?<=[.!?])\s+(?:in the search|then search(?: for)?)\s+", " ", search[1], flags=re.I)
        query = re.sub(r"(?<=\w)\.(?=\s)", "", query).strip(" .!?")
        if query:
            return "launch_app", {"app_name": "chrome", "args": ["https://www.google.com/search?" + urlencode({"q": query})]}

    path_command = re.fullmatch(
        r'open\s+(?:the\s+)?(file|folder)\s+["\']?((?:[a-z]:[\\/]|[~/])[^"\']+)["\']?', text, re.I,
    )
    if path_command:
        kind = path_command[1].lower()
        return f"open_{kind}", {f"{kind}_path": path_command[2].strip()}

    task = re.match(r"^(?:create|add|make)\s+(?:a\s+)?(?:(low|medium|high|urgent)\s+priority\s+)?"
                    r"task\b[\s:,-]+(?:(?:to|called|named)\s+)?(.+)$", text, re.I)
    if task:
        return "create_task", {"title": task[2].strip(), "priority": (task[1] or "medium").lower()}
    if re.fullmatch(r"(?:list|show)(?:\s+me)?\s+(?:my\s+|the\s+)?tasks[.!?]?", text, re.I):
        return "list_tasks", {}
    app = re.fullmatch(
        r"(?:open|launch|start)\s+(chrome|google chrome|notepad|explorer|file explorer|vs code|"
        r"visual studio code|command prompt|cmd|powershell|windows terminal|terminal)[.!?]*",
        text, re.I,
    )
    if app:
        return "launch_app", {"app_name": app[1].lower()}
    return None
