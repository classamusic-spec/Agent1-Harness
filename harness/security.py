"""Deterministic security scan — a free, model-independent gate.

The LLM `--review-focus security` reviewer is good at judgement calls; this module
is its deterministic counterpart: a static scan that catches the unambiguous, high
-signal problems by pattern — hardcoded secrets/keys, injection (SQL/command/path),
unsafe deserialization, and weak crypto — with exact file:line locations. It runs
in-process (no tokens), so it can gate every build and feed precise findings into
the repair loop.

Like the verifier, this is intentionally LLM-free so it can be unit-tested offline
and trusted. Findings are conservative: placeholders and env-var reads are ignored
to keep false positives low, and only `high` severity blocks by default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Files/dirs not worth scanning (deps, caches, VCS, build output, the example env).
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".studio", ".pytest_cache",
              ".venv", "venv", "dist", "build", ".next", ".cache", ".mypy_cache"}
_SKIP_FILES = {".env.example", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
_SCAN_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".env",
                  ".yaml", ".yml", ".sh", ".rb", ".go", ".php", ".java", ".html", ".txt", ".cfg", ".ini")
_MAX_BYTES = 400_000

# Values that look like secrets but are clearly placeholders / env reads — ignore.
_PLACEHOLDER = re.compile(
    r"(?i)(change[\-_ ]?me|your[\-_ ]?|example|placeholder|dummy|xxx+|\.\.\.|"
    r"<[^>]+>|\$\{?[a-z_]+\}?|os\.environ|process\.env|getenv|secrets\.token|token_hex|"
    r"redacted|fake|sample|test[\-_]?key|none|null|true|false)")


@dataclass
class Finding:
    severity: str   # "high" | "medium" | "low"
    rule: str
    message: str
    file: str
    line: int
    snippet: str = ""

    def summary(self) -> str:
        loc = f"{self.file}:{self.line}"
        return f"[{self.severity.upper()}] {self.rule} — {self.message} ({loc})"


@dataclass
class _Rule:
    id: str
    severity: str
    pattern: re.Pattern
    message: str
    suffixes: tuple = ()        # restrict to these file suffixes ( () = any )
    ignore_placeholder: bool = False


def _re(p: str) -> re.Pattern:
    return re.compile(p)


# Order matters only for readability; all rules are applied to every line.
_RULES: list[_Rule] = [
    # --- Hardcoded secrets / keys (high) ---
    _Rule("private-key", "high",
          _re(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
          "Hardcoded private key committed to source"),
    _Rule("aws-access-key", "high", _re(r"\bAKIA[0-9A-Z]{16}\b"),
          "Hardcoded AWS access key id"),
    _Rule("aws-secret-key", "high",
          _re(r"(?i)aws_secret_access_key\s*[=:]\s*['\"][A-Za-z0-9/+]{40}['\"]"),
          "Hardcoded AWS secret access key", ignore_placeholder=True),
    _Rule("github-token", "high", _re(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
          "Hardcoded GitHub token"),
    _Rule("slack-token", "high", _re(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
          "Hardcoded Slack token"),
    _Rule("generic-secret", "high",
          _re(r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
              r"client[_-]?secret|password|passwd|private[_-]?key)\b\s*[=:]\s*"
              r"['\"][^'\"]{8,}['\"]"),
          "Hardcoded credential — load from environment/secret store instead",
          ignore_placeholder=True),
    # --- Injection (high) ---
    _Rule("sql-injection", "high",
          _re(r"(?i)(?:execute|executemany|cursor\.execute|\.query)\s*\(\s*"
              r"(?:f['\"]|['\"][^'\"]*['\"]\s*(?:%|\+|\.format)|['\"][^'\"]*\{)"),
          "SQL built from a string/f-string — use parameterised queries",
          suffixes=(".py", ".js", ".ts", ".rb", ".php")),
    _Rule("shell-injection", "high",
          _re(r"(?i)subprocess\.(?:run|call|Popen|check_output|check_call)\([^)]*shell\s*=\s*True"),
          "subprocess with shell=True — risks command injection",
          suffixes=(".py",)),
    _Rule("os-system", "high", _re(r"\bos\.system\s*\("),
          "os.system() runs a shell — risks command injection", suffixes=(".py",)),
    _Rule("js-child-exec", "high",
          _re(r"child_process\.exec\s*\(|\brequire\(['\"]child_process['\"]\)\.exec\s*\("),
          "child_process.exec runs a shell — use execFile/spawn with args",
          suffixes=(".js", ".mjs", ".cjs", ".ts")),
    # --- Unsafe deserialization / dynamic exec (medium) ---
    _Rule("pickle-loads", "medium", _re(r"\bpickle\.loads?\s*\(|\bcPickle\."),
          "Unpickling untrusted data executes arbitrary code", suffixes=(".py",)),
    _Rule("yaml-load", "medium",
          _re(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)"),
          "yaml.load without SafeLoader can execute arbitrary code", suffixes=(".py",)),
    _Rule("py-eval-exec", "medium", _re(r"(?<![\w.])(?:eval|exec)\s*\("),
          "Dynamic eval/exec — avoid running constructed code", suffixes=(".py",)),
    _Rule("js-eval", "medium", _re(r"(?<![\w.])eval\s*\(|new Function\s*\("),
          "Dynamic eval/new Function — avoid running constructed code",
          suffixes=(".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")),
    _Rule("dom-xss", "medium",
          _re(r"\.innerHTML\s*=|dangerouslySetInnerHTML|document\.write\s*\("),
          "Unsanitised HTML sink — risks DOM XSS; use textContent/escaping",
          suffixes=(".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html")),
    # --- Weak crypto / insecure transport (medium/low) ---
    _Rule("tls-verify-off", "medium",
          _re(r"(?i)verify\s*=\s*False|rejectUnauthorized\s*:\s*false|"
              r"CURLOPT_SSL_VERIFY(?:PEER|HOST)\s*,\s*(?:0|false)"),
          "TLS certificate verification disabled"),
    _Rule("weak-hash", "low",
          _re(r"(?i)hashlib\.(?:md5|sha1)\s*\(|createHash\(['\"](?:md5|sha1)['\"]\)"),
          "Weak hash (MD5/SHA1) — use SHA-256+ (and a KDF for passwords)"),
    _Rule("debug-true", "low",
          _re(r"(?i)\bdebug\s*[=:]\s*True\b|app\.run\([^)]*debug\s*=\s*True"),
          "Debug mode enabled — disable in production"),
]

_BLOCKING_DEFAULT = ("high",)


def _looks_placeholder(line: str) -> bool:
    return bool(_PLACEHOLDER.search(line))


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan a file's text. `path` is used only for suffix selection + reporting."""
    suffix = os.path.splitext(path)[1].lower()
    findings: list[Finding] = []
    # Multiline rule: private key block (handle once on the whole text).
    lines = text.splitlines()
    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if len(line) > 2000:
            line = line[:2000]
        stripped = line.lstrip()
        # Skip comment-only lines for noisy code rules (keep them for secrets).
        is_comment = stripped.startswith(("#", "//", "*", "/*"))
        for rule in _RULES:
            if rule.suffixes and suffix not in rule.suffixes:
                continue
            if is_comment and rule.id not in (
                    "private-key", "aws-access-key", "github-token", "slack-token",
                    "generic-secret", "aws-secret-key"):
                continue
            if not rule.pattern.search(line):
                continue
            if rule.ignore_placeholder and _looks_placeholder(line):
                continue
            snippet = line.strip()[:160]
            findings.append(Finding(rule.severity, rule.id, rule.message,
                                    path, i, snippet))
    return findings


def scan_workspace(workspace: str, *, max_findings: int = 200) -> list[Finding]:
    """Walk and scan a workspace. Returns findings sorted by severity then location."""
    root = os.path.abspath(workspace)
    out: list[Finding] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn in _SKIP_FILES:
                continue
            if not (fn.endswith(_SCAN_SUFFIXES) or fn == ".env"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(full) > _MAX_BYTES:
                    continue
                text = open(full, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(full, root)
            out.extend(scan_text(rel, text))
            if len(out) >= max_findings:
                break
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: (order.get(f.severity, 9), f.file, f.line))
    return out[:max_findings]


def blocking(findings: list[Finding], severities=_BLOCKING_DEFAULT) -> list[Finding]:
    return [f for f in findings if f.severity in severities]


def counts(findings: list[Finding]) -> dict[str, int]:
    c = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        c[f.severity] = c.get(f.severity, 0) + 1
    return c


def to_report(findings: list[Finding]) -> str:
    if not findings:
        return "No security findings."
    c = counts(findings)
    header = f"{c['high']} high, {c['medium']} medium, {c['low']} low"
    return header + "\n" + "\n".join("  " + f.summary() for f in findings)


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Deterministic security scan.")
    ap.add_argument("path", nargs="?", default=".", help="directory to scan")
    ap.add_argument("--block", default="high",
                    help="comma-separated severities that cause a non-zero exit (default: high)")
    args = ap.parse_args(argv)
    findings = scan_workspace(args.path)
    print(to_report(findings))
    sev = tuple(s.strip() for s in args.block.split(",") if s.strip())
    return 1 if blocking(findings, sev) else 0


if __name__ == "__main__":
    raise SystemExit(_main())
