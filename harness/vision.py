"""Vision stage: turn a reference UI image into an implementation-ready brief.

Two ways to reference a UI image when building an app:

  1. Two-stage (best quality, the recommended local path): a *vision* model
     looks at the screenshot and writes a precise design brief; that brief is
     injected into the prompt for a strong, dedicated *coder* model. Each model
     is specialised — e.g. a local `qwen2.5-vl` / `llava` / `minicpm-v` for
     sight, and a local `qwen2.5-coder` / `deepseek-coder` for code.

  2. Single multimodal model: one vision+code model (or the Claude Code engine)
     reads the image directly — no separate describe step. Simpler, but the
     best coders and the best vision models are usually not the same model.

This module implements stage 1 against any OpenAI-compatible vision endpoint
(Ollama / LM Studio / vLLM) using the standard `image_url` content part with a
base64 data URL — exactly what local servers expect.
"""

from __future__ import annotations

import base64
import os

from harness.config import EngineConfig

VISION_PROMPT = """You are a senior product designer. Study this UI screenshot and write an \
implementation-ready DESIGN BRIEF that a frontend engineer can build from WITHOUT seeing the \
image. Be concrete, specific, and exhaustive. Use these sections:

## Layout & structure
Regions, grid, navigation, visual hierarchy, what's above/below the fold.
## Components
Every element (buttons, inputs, cards, lists, tabs, charts, modals, FABs, nav bars…) and their
states (default/hover/active/disabled/empty).
## Color
Approximate hex values for background, surfaces, text (primary/secondary), accent, and semantic
(success/warn/danger). Note gradients.
## Typography
Relative sizes & weights, headings vs body, casing, letter-spacing, font character (e.g. rounded
geometric sans).
## Spacing, shape & depth
Padding/margins/density, corner radii, borders, shadows/elevation.
## Iconography & content
Icons/emoji, imagery, and the actual data/labels shown.
## Motion & interaction
Implied transitions and micro-interactions.
## Responsive intent
Mobile-first? Breakpoints? How it should reflow.

Do NOT write code — describe precisely so the build matches the look and feel."""

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def mime_for(path: str) -> str:
    return _MIME.get(os.path.splitext(path)[1].lower(), "image/png")


def to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


COMPARE_PROMPT = """You are doing visual QA. Image 1 is the TARGET reference design. Image 2 is \
the CURRENT build. Compare them and report concrete, actionable visual differences a developer \
should fix (layout, alignment, color, typography, spacing, sizing, missing/extra components, \
states). Ignore placeholder text/data differences; focus on look & feel.

Respond ONLY with JSON:
{"matches": <true if it's already a faithful match>, "differences": ["short fix 1", "short fix 2", ...]}"""


def _parse_compare(text: str) -> dict:
    from harness.review import extract_json
    data = extract_json(text) or {}
    diffs = data.get("differences") or []
    if not isinstance(diffs, list):
        diffs = [str(diffs)]
    return {
        "matches": bool(data.get("matches")) and not diffs,
        "differences": [str(d) for d in diffs][:12],
        "raw": text,
    }


async def compare_ui(reference: bytes, built: bytes, engine: EngineConfig, *,
                     ref_mime: str = "image/png", built_mime: str = "image/png") -> dict:
    """Ask a vision model how the built UI differs from the reference. Returns
    {matches: bool, differences: [str], raw: str}."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError("the visual check needs 'openai': pip install 'agent1-harness[local]'") from exc
    client = AsyncOpenAI(base_url=engine.base_url, api_key=engine.resolved_api_key())
    try:
        resp = await client.chat.completions.create(
            model=engine.model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": COMPARE_PROMPT},
                {"type": "text", "text": "TARGET reference:"},
                {"type": "image_url", "image_url": {"url": to_data_url(reference, ref_mime)}},
                {"type": "text", "text": "CURRENT build:"},
                {"type": "image_url", "image_url": {"url": to_data_url(built, built_mime)}},
            ]}],
            temperature=0.1,
            max_tokens=800,
        )
        return _parse_compare(resp.choices[0].message.content or "")
    finally:
        await client.close()


async def describe_ui(image_bytes: bytes, mime: str, engine: EngineConfig, *,
                      prompt: str = VISION_PROMPT, max_tokens: int = 1600) -> str:
    """Ask an OpenAI-compatible vision model to describe a UI screenshot."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "the vision stage needs the 'openai' package: pip install 'agent1-harness[local]'"
        ) from exc

    client = AsyncOpenAI(base_url=engine.base_url, api_key=engine.resolved_api_key())
    try:
        resp = await client.chat.completions.create(
            model=engine.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": to_data_url(image_bytes, mime)}},
                ],
            }],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    finally:
        await client.close()
