"""Tolerant tool-call parsing for weaker / quantized local models.

Small local models often (a) emit malformed JSON in tool-call arguments, or (b) don't
use the OpenAI tool-calling API at all and instead *write the action as text*. This
module recovers both: `repair_json` salvages near-JSON, and `extract_tool_calls`
pulls tool intents out of free-form content (fenced ```action blocks, a bare JSON
object, or an {tool_calls:[...]} wrapper). Combined with a small text-protocol hint
in the system prompt, this lets models that can't natively tool-call still build.

Stdlib only; pure functions, fully unit-tested.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json|action|tool|tool_call|tool_calls)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_NAME_KEYS = ("tool", "name", "function", "tool_name", "action")
_ARG_KEYS = ("args", "arguments", "parameters", "params", "input", "tool_input")
_LIST_KEYS = ("tool_calls", "actions", "calls", "steps")


def _strip_fence(s: str) -> str:
    m = _FENCE_RE.search(s)
    return m.group(1).strip() if m else s.strip()


def _first_json_value(s: str) -> str | None:
    """Return the first balanced {...} or [...] substring (string-aware)."""
    start = None
    opener = None
    for i, ch in enumerate(s):
        if ch in "{[":
            start, opener = i, ch
            break
    if start is None:
        return None
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
    return None


def _all_json_values(s: str) -> list[str]:
    """Every top-level balanced {...}/[...] substring, in order."""
    out: list[str] = []
    i = 0
    while i < len(s):
        v = _first_json_value(s[i:])
        if v is None:
            break
        out.append(v)
        idx = s.find(v, i)
        i = (idx + len(v)) if idx >= 0 else (i + 1)
    return out


def _fix_common(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)          # trailing commas
    s = re.sub(r"//[^\n]*", "", s)                 # // comments
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)  # /* */ comments
    return s


def repair_json(s):
    """Best-effort parse of near-JSON into a dict/list. Returns None if hopeless."""
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str) or not s.strip():
        return None
    inner = _strip_fence(s)
    for candidate in (inner, _first_json_value(inner) or "", _fix_common(_first_json_value(inner) or inner)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    # last resort: single → double quotes (only when there are no double quotes)
    guess = _first_json_value(inner) or inner
    if "'" in guess and '"' not in guess:
        try:
            return json.loads(_fix_common(guess.replace("'", '"')))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _normalize_call(obj: dict, valid_names) -> dict | None:
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if isinstance(obj.get("function"), dict):  # OpenAI-ish nested shape
        fn = obj["function"]
        name = fn.get("name") or name
        if obj.get("arguments") is None and fn.get("arguments") is not None:
            obj = {**obj, "arguments": fn.get("arguments")}
    if not name:
        return None
    args = next((obj[k] for k in _ARG_KEYS if k in obj), {})
    if isinstance(args, str):
        args = repair_json(args) or {}
    if not isinstance(args, dict):
        args = {}
    if valid_names and name not in valid_names:
        return None
    return {"name": str(name), "arguments": args}


def extract_tool_calls(content: str, valid_names=None) -> list[dict]:
    """Pull tool calls out of free-form model text. Returns [{name, arguments}]."""
    if not content:
        return []
    valid = set(valid_names) if valid_names else None
    candidates: list[str] = [m.group(1) for m in _FENCE_RE.finditer(content)]
    candidates.extend(_all_json_values(content))
    if not candidates:
        candidates.append(content)

    out: list[dict] = []
    seen = set()
    for cand in candidates:
        obj = repair_json(cand)
        if obj is None:
            continue
        calls: list = []
        if isinstance(obj, list):
            calls = obj
        elif isinstance(obj, dict):
            for k in _LIST_KEYS:
                if isinstance(obj.get(k), list):
                    calls = obj[k]
                    break
            else:
                calls = [obj]
        for c in calls:
            if not isinstance(c, dict):
                continue
            norm = _normalize_call(c, valid)
            if norm:
                key = (norm["name"], json.dumps(norm["arguments"], sort_keys=True))
                if key not in seen:
                    seen.add(key)
                    out.append(norm)
    return out


def text_protocol_hint(tool_names: list[str]) -> str:
    """A short instruction telling models how to act via text when the tool-calling
    API isn't available — parsed back by extract_tool_calls()."""
    names = ", ".join(tool_names) if tool_names else "the available tools"
    return (
        "\n\n## If you cannot call tools via the API\n"
        "Some servers don't support function-calling. If your tool calls aren't taking "
        "effect, emit each action as a fenced block (one per action) and nothing else "
        "on those lines:\n"
        "```action\n{\"tool\": \"<one of: " + names + ">\", \"args\": { ... }}\n```\n"
        "Use the exact tool name and its real arguments. Wait for the result before the next action."
    )
