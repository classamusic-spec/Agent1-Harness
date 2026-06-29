"""Tests for tolerant tool-call parsing: JSON repair + extracting actions from text."""

from __future__ import annotations

from harness import toolparse


def test_repair_valid_json():
    assert toolparse.repair_json('{"a": 1}') == {"a": 1}
    assert toolparse.repair_json({"a": 1}) == {"a": 1}  # passthrough


def test_repair_trailing_comma_and_fences():
    assert toolparse.repair_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert toolparse.repair_json('here you go: {"path": "x.py", "content": "hi",}') == \
        {"path": "x.py", "content": "hi"}


def test_repair_single_quotes():
    assert toolparse.repair_json("{'tool': 'write_file'}") == {"tool": "write_file"}


def test_repair_hopeless_returns_none():
    assert toolparse.repair_json("not json at all") is None
    assert toolparse.repair_json("") is None


def test_extract_fenced_action_block():
    content = ('I will create the file.\n'
               '```action\n{"tool": "write_file", "args": {"path": "a.py", "content": "x=1"}}\n```')
    calls = toolparse.extract_tool_calls(content, ["write_file", "run"])
    assert calls == [{"name": "write_file", "arguments": {"path": "a.py", "content": "x=1"}}]


def test_extract_bare_json_object():
    calls = toolparse.extract_tool_calls('{"name": "run", "arguments": {"cmd": "pytest"}}', ["run"])
    assert calls == [{"name": "run", "arguments": {"cmd": "pytest"}}]


def test_extract_tool_calls_wrapper_list():
    content = '{"tool_calls": [{"tool":"a","args":{}}, {"tool":"b","args":{"x":1}}]}'
    calls = toolparse.extract_tool_calls(content, ["a", "b"])
    assert [c["name"] for c in calls] == ["a", "b"]
    assert calls[1]["arguments"] == {"x": 1}


def test_extract_openai_nested_function_shape():
    content = '{"function": {"name": "write_file", "arguments": "{\\"path\\": \\"z\\"}"}}'
    calls = toolparse.extract_tool_calls(content, ["write_file"])
    assert calls == [{"name": "write_file", "arguments": {"path": "z"}}]


def test_extract_filters_unknown_tools_and_dedupes():
    content = '```action\n{"tool":"nope","args":{}}\n```\n{"tool":"ok","args":{}}\n{"tool":"ok","args":{}}'
    calls = toolparse.extract_tool_calls(content, ["ok"])
    assert calls == [{"name": "ok", "arguments": {}}]   # unknown dropped, dupe removed


def test_extract_nothing_from_prose():
    assert toolparse.extract_tool_calls("Just some explanation, no actions here.", ["write_file"]) == []


def test_text_protocol_hint_lists_tools():
    hint = toolparse.text_protocol_hint(["write_file", "run"])
    assert "write_file" in hint and "```action" in hint
