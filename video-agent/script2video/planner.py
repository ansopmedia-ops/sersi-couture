"""Step 1 - turn a raw text script into a scene-by-scene storyboard.

Uses Claude (structured JSON output) when ANTHROPIC_API_KEY / an `ant auth` profile
is available, otherwise falls back to a simple rule-based splitter.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from .util import log

MODEL = "claude-opus-5-5"


class Scene(BaseModel):
    narration: str = Field(description="Exact words the voiceover speaks in this scene")
    on_screen_text: str = Field(default="", description="Short headline shown on screen (<= 6 words) or empty")
    stock_query: str = Field(description="2-4 word concrete search query for stock footage")
    image_prompt: str = Field(default="", description="Detailed prompt for an AI image generator")
    mood: str = Field(default="neutral")


class Storyboard(BaseModel):
    title: str
    scenes: list[Scene]


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "scenes"],
    "properties": {
        "title": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "on_screen_text", "stock_query", "image_prompt", "mood"],
                "properties": {
                    "narration": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                    "stock_query": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "mood": {"type": "string"},
                },
            },
        },
    },
}

_SYSTEM = """You are a video director. You receive a narration script and break it into a storyboard \
for an automatically produced voiceover video.

Rules:
- Keep the author's wording. Copy the script into `narration` scene by scene; only make light edits \
for speakability (expand abbreviations, fix obvious typos) unless the user explicitly asks for a rewrite.
- Drop stage directions, headings and notes like "[music]" or "SCENE 1:" from narration.
- Each scene should take 4-12 seconds to speak (roughly 10-30 words). Split long paragraphs.
- `stock_query`: 2-4 concrete, filmable words for a stock-footage search (e.g. "tailor sewing fabric", \
not "craftsmanship").
- `image_prompt`: one vivid sentence describing the ideal shot (subject, setting, lighting, camera), \
usable by an image generator. No text in the image.
- `on_screen_text`: a short punchy headline (max 6 words) for scenes that benefit from one - the opening, \
key points, and the call to action. Use an empty string otherwise.
- `mood`: one word (e.g. calm, energetic, luxurious, dramatic, warm).
- `title`: a short title for the whole video."""


def plan_with_claude(script: str, style: str | None, rewrite: bool) -> Storyboard:
    import anthropic

    client = anthropic.Anthropic()
    instructions = []
    if style:
        instructions.append(f"Visual style / brand notes: {style}")
    if rewrite:
        instructions.append("You MAY rewrite the narration to be tighter and more engaging.")
    user = "\n".join(instructions + ["Script:", "<script>", script.strip(), "</script>"])

    log(f"Planning storyboard with {MODEL} ...")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to plan this script.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Storyboard was truncated (script too long?). Try splitting the script.")
    text = "".join(b.text for b in response.content if b.type == "text")
    return Storyboard.model_validate(json.loads(text))


def _keywords(text: str, n: int = 3) -> str:
    stop = set(
        "the a an and or but of to in on for with at by from is are was were be been it this that these those "
        "you your we our they their i my me he she his her its as so not no do does did have has had will "
        "can could would should may might just more most very than then there here what when where which who "
        "how why all any each every some such into over about".split()
    )
    words = [w for w in re.findall(r"[a-zA-Z']{4,}", text.lower()) if w not in stop]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return " ".join(sorted(seen, key=len, reverse=True)[:n]) or "abstract background"


def plan_offline(script: str, max_words: int = 28) -> Storyboard:
    """Rule-based fallback: paragraph/sentence chunking, keyword stock queries."""
    log("Planning storyboard offline (no Claude credentials or --no-llm).")
    lines = [ln for ln in script.splitlines() if not re.match(r"^\s*(\[.*\]|\(.*\)|#.*|scene\s*\d+.*:?)\s*$", ln, re.I)]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    scenes: list[Scene] = []
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(para.split()))
        chunk: list[str] = []
        for sent in sentences:
            if chunk and len(" ".join(chunk + [sent]).split()) > max_words:
                scenes.append(_scene(" ".join(chunk)))
                chunk = []
            chunk.append(sent)
        if chunk:
            scenes.append(_scene(" ".join(chunk)))
    if not scenes:
        raise ValueError("Script is empty.")
    title = re.sub(r"[.!?].*$", "", scenes[0].narration)[:60]
    words = title.split()
    scenes[0].on_screen_text = title if len(words) <= 8 else " ".join(words[:6]) + "..."
    return Storyboard(title=title, scenes=scenes)


def _scene(text: str) -> Scene:
    q = _keywords(text)
    return Scene(narration=text, stock_query=q, image_prompt=f"Cinematic shot of {q}, soft natural light")


def plan(script: str, *, use_llm: bool, style: str | None = None, rewrite: bool = False) -> Storyboard:
    if use_llm:
        try:
            return plan_with_claude(script, style, rewrite)
        except Exception as exc:  # noqa: BLE001 - surface and fall back
            log(f"Claude planning unavailable ({type(exc).__name__}: {exc}); falling back to offline planner.")
    return plan_offline(script)
