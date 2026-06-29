"""Tolerant unified-diff application for surgical edits.

Weak/slow local models waste tokens (and wall-clock) rewriting an entire file to
change three lines. A diff tool lets them send only the changed hunks. But local
models also get unified-diff *line numbers* wrong constantly, so this applier
ignores the `@@ -a,b +c,d @@` numbers entirely and locates each hunk by its
context: the search block (context + removed lines) is found in the file and
replaced by the result block (context + added lines). Falls back to a
whitespace-tolerant match before giving up. Pure + stdlib; fully unit-tested.
"""

from __future__ import annotations


class PatchError(ValueError):
    pass


def _parse_hunks(diff: str) -> list[list[str]]:
    """Split a unified diff into hunks (each a list of prefixed lines)."""
    lines = (diff or "").splitlines()
    hunks: list[list[str]] = []
    cur: list[str] | None = None
    for ln in lines:
        if ln.startswith("@@"):
            cur = []
            hunks.append(cur)
            continue
        if ln.startswith(("--- ", "+++ ", "diff --git", "index ")):
            continue  # file headers — ignored
        if cur is None:
            # No @@ header seen yet: tolerate a bare hunk if it looks like diff body.
            if ln[:1] in (" ", "+", "-"):
                cur = []
                hunks.append(cur)
            else:
                continue
        if ln.startswith("\\"):  # "\ No newline at end of file"
            continue
        cur.append(ln)
    return [h for h in hunks if h]


def _blocks(hunk: list[str]) -> tuple[str, str]:
    """(search, replace) text for a hunk. Context+removed → search; context+added → replace."""
    search, replace = [], []
    for ln in hunk:
        tag, body = ln[:1], ln[1:]
        if tag == " ":
            search.append(body)
            replace.append(body)
        elif tag == "-":
            search.append(body)
        elif tag == "+":
            replace.append(body)
        else:  # a line with no prefix — treat as shared context (lenient)
            search.append(ln)
            replace.append(ln)
    return "\n".join(search), "\n".join(replace)


def _apply_one(text: str, search: str, replace: str) -> str:
    if search == "":
        # Pure insertion with no anchor → append to the file.
        if not replace:
            return text
        sep = "" if text.endswith("\n") or text == "" else "\n"
        return text + sep + replace
    if search in text:
        return text.replace(search, replace, 1)
    # Whitespace-tolerant retry: match on right-stripped lines.
    def norm(s: str) -> list[str]:
        return [x.rstrip() for x in s.split("\n")]
    hay, needle = text.split("\n"), norm(search)
    for i in range(0, len(hay) - len(needle) + 1):
        if [x.rstrip() for x in hay[i:i + len(needle)]] == needle:
            new = hay[:i] + replace.split("\n") + hay[i + len(needle):]
            return "\n".join(new)
    preview = search.strip().splitlines()[0] if search.strip() else "(empty)"
    raise PatchError(f"hunk did not match the file; context not found near: {preview!r}")


def apply_patch(original: str, diff: str) -> str:
    """Apply a (line-number-tolerant) unified diff to `original`. Raises PatchError."""
    hunks = _parse_hunks(diff)
    if not hunks:
        raise PatchError("no hunks found in the patch (expected unified-diff lines)")
    text = original
    for hunk in hunks:
        search, replace = _blocks(hunk)
        text = _apply_one(text, search, replace)
    return text


def is_creation(diff: str) -> bool:
    """True when the patch only adds lines (a brand-new file)."""
    hunks = _parse_hunks(diff)
    if not hunks:
        return False
    for hunk in hunks:
        for ln in hunk:
            if ln[:1] in (" ", "-"):
                return False
    return True
