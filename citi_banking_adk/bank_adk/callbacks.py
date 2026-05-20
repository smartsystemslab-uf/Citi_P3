import re
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse

_TOOL_NAME_CLEAN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def _clean_tool_name(name: str) -> str:
    raw = (name or "").strip()
    if raw.startswith("to=functions."):
        raw = raw[len("to=functions.") :]
    raw = raw.strip()
    match = _TOOL_NAME_CLEAN_RE.match(raw)
    return match.group(0) if match else raw


def sanitize_tool_call_names(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """Sanitize malformed tool names emitted by some OpenAI-compatible reasoning models.

    Observed bad outputs include names such as:
    - fraud_detection_agent<|channel|>commentary
    - to=functions.compliance_agent <|constrain|>JSON

    ADK supports after_model_callback specifically for response sanitization.
    """
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return llm_response

    changed = False
    for part in parts:
        function_call = getattr(part, "function_call", None)
        if not function_call:
            continue
        old_name = getattr(function_call, "name", None)
        if not old_name:
            continue
        new_name = _clean_tool_name(old_name)
        if new_name and new_name != old_name:
            function_call.name = new_name
            changed = True

    if changed:
        meta = dict(llm_response.custom_metadata or {})
        meta["tool_name_sanitized"] = True
        llm_response.custom_metadata = meta
    return llm_response
