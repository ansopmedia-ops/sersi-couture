"""film - one prompt in, one finished AI-generated video out.

    python -m script2video.film "A baby explores a magical mossy forest..." --seconds 40

Pipeline:
  1. PLAN      Claude splits the idea into N shots and writes ONE character + look
               description that is repeated in every shot (keeps the subject consistent).
  2. GENERATE  an AI video model makes the clips:
                 --mode extend  continuous long take: first clip, then 7 s extensions (Veo)
                 --mode shots   separate clips joined with crossfades (any provider)
  3. FINISH    stitch, trim to the exact length, optional music  ->  .mp4
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .util import log, media_duration, require_ffmpeg, run_ffmpeg, slugify
from .videogen import VideoProvider, get_provider, has_audio

MODEL = "claude-opus-5-5"
XFADE = 0.6  # crossfade seconds between separate shots


class Shot(BaseModel):
    action: str
    camera: str = ""


class FilmPlan(BaseModel):
    title: str
    character: str
    look: str
    shots: list[Shot]


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "character", "look", "shots"],
    "properties": {
        "title": {"type": "string"},
        "character": {"type": "string"},
        "look": {"type": "string"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "camera"],
                "properties": {"action": {"type": "string"}, "camera": {"type": "string"}},
            },
        },
    },
}

SYSTEM = """You are a film director preparing prompts for an AI text-to-video model that can only \
generate a few seconds at a time. Break the user's idea into exactly the requested number of \
consecutive shots that together tell the whole idea in order.

- `character`: one precise, reusable description of every recurring subject (age, clothing, colours, \
hair, props). It is pasted into every shot prompt so the subject looks identical in each clip - be specific.
- `look`: one reusable description of setting, lighting, colour palette, lens and style \
(e.g. "photorealistic, warm golden-hour light, shallow depth of field, 35mm").
- Each shot: `action` is what visibly happens in that clip, written so it can be filmed in the given \
number of seconds (one or two simple actions, no time jumps). `camera` is the camera movement.
- In continuous mode every shot must pick up exactly where the previous one ended (same place, same \
moment, no cuts). In shots mode each shot may reframe but should still flow naturally.
- Do not write on-screen text, captions, logos or dialogue unless the user asks for them.
- `title`: a short title."""


def plan_with_claude(idea: str, n: int, seconds_per_shot: int, continuous: bool) -> FilmPlan:
    import anthropic

    client = anthropic.Anthropic()
    mode = "continuous mode (one uninterrupted take, no cuts)" if continuous else "shots mode (separate clips joined with short crossfades)"
    user = (f"Number of shots: {n}\nSeconds per shot: about {seconds_per_shot}\nMode: {mode}\n\n"
            f"<idea>\n{idea.strip()}\n</idea>")
    log(f"Planning {n} shots with {MODEL} ...")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to plan this video.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Shot plan was truncated.")
    text = "".join(b.text for b in response.content if b.type == "text")
    return FilmPlan.model_validate(json.loads(text))


def plan_offline(idea: str, n: int) -> FilmPlan:
    """No Claude: drop meta sentences ("40-second long take", "no cuts"), use the first remaining
    sentence as the subject description and spread the rest evenly across the shots."""
    meta = re.compile(r"long take|no cuts?|single (continuous )?shot|\d+[- ]second|uninterrupted", re.I)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(idea.split())) if s.strip()]
    content = [s for s in sentences if not meta.search(s)] or sentences or [idea]
    subject, rest = content[0], content[1:] or content
    shots = []
    for i in range(n):
        lo, hi = i * len(rest) // n, (i + 1) * len(rest) // n
        shots.append(Shot(action=" ".join(rest[lo:max(hi, lo + 1)])))
    return FilmPlan(title=" ".join(subject.split()[:8]), character=subject, look="", shots=shots)


def plan(idea: str, n: int, seconds_per_shot: int, continuous: bool, use_llm: bool = True) -> FilmPlan:
    if use_llm:
        try:
            return plan_with_claude(idea, n, seconds_per_shot, continuous)
        except Exception as exc:  # noqa: BLE001
            log(f"Claude planning unavailable ({type(exc).__name__}: {exc}); using the offline planner.")
    return plan_offline(idea, n)


def shot_prompt(p: FilmPlan, i: int, continuous: bool) -> str:
    shot = p.shots[i]
    camera = f" Camera: {shot.camera.rstrip('.')}." if shot.camera else ""
    if continuous and i > 0:
        return f"Continue the same uninterrupted shot with no cut. {p.character} {shot.action}{camera} {p.look}".strip()
    tail = " Single continuous shot, no cuts." if continuous else ""
    return f"{p.look} {p.character} {shot.action}{camera}{tail}".strip()


def shots_needed(seconds: float, provider: VideoProvider, mode: str) -> int:
    if mode == "extend":
        return 1 + max(0, math.ceil((seconds - provider.clip_seconds) / provider.extend_seconds))
    clip = provider.clip_seconds
    return max(1, math.ceil((seconds - XFADE) / (clip - XFADE)))


def stitch(clips: list[Path], out: Path, seconds: float | None, aspect: str) -> Path:
    """Join clips with crossfades, normalised to the first clip's size, 24 fps, stereo audio."""
    w, h = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}.get(aspect, (1280, 720))
    args, parts = [], []
    durations = []
    for i, clip in enumerate(clips):
        args += ["-i", str(clip)]
        durations.append(media_duration(clip))
        parts.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=24,setsar=1,format=yuv420p[v{i}]")
        if has_audio(clip):
            parts.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{durations[i]:.3f}[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{durations[i]:.3f}[a{i}]")
    v, a, offset = "[v0]", "[a0]", durations[0]
    for i in range(1, len(clips)):
        offset -= XFADE
        parts.append(f"{v}[v{i}]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}[vx{i}]")
        parts.append(f"{a}[a{i}]acrossfade=d={XFADE}[ax{i}]")
        v, a = f"[vx{i}]", f"[ax{i}]"
        offset += durations[i]
    trim = ["-t", f"{seconds:.3f}"] if seconds else []
    run_ffmpeg([*args, "-filter_complex", ";".join(parts), "-map", v, "-map", a, *trim,
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(out)])
    return out


def finish(video: Path, out: Path, seconds: float, music: Path | None, music_volume: float) -> Path:
    """Trim to length, fade the ending, optionally lay music under the clip's own audio."""
    dur = min(seconds, media_duration(video))
    fade = f"fade=out:st={max(0, dur - 0.8):.3f}:d=0.8"
    if music:
        graph = (f"[0:v]{fade}[v];[1:a]volume={music_volume},afade=out:st={max(0, dur - 2):.3f}:d=2[m];"
                 f"[0:a][m]amix=inputs=2:duration=first:normalize=0,afade=out:st={max(0, dur - 0.8):.3f}:d=0.8[a]")
        run_ffmpeg(["-i", str(video), "-stream_loop", "-1", "-i", str(music), "-filter_complex", graph,
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-crf", "19",
                    "-c:a", "aac", "-movflags", "+faststart", str(out)])
    else:
        run_ffmpeg(["-i", str(video), "-vf", fade, "-af", f"afade=out:st={max(0, dur - 0.8):.3f}:d=0.8",
                    "-t", f"{dur:.3f}", "-c:v", "libx264", "-crf", "19", "-c:a", "aac",
                    "-movflags", "+faststart", str(out)])
    return out


def make_film(idea: str, *, seconds: float = 40, aspect: str = "16:9", provider_name: str = "auto",
              mode: str = "auto", workdir: Path, out: Path, plan_data: dict | None = None,
              use_llm: bool = True, music: Path | None = None, music_volume: float = 0.3,
              progress: Callable[[str], None] = log) -> dict:
    require_ffmpeg()
    provider = get_provider(provider_name)
    if mode == "auto":
        mode = "extend" if provider.extend_seconds else "shots"
    if mode == "extend" and not provider.extend_seconds:
        raise ValueError(f"The {provider.name} provider can't extend videos; use mode 'shots'.")
    if aspect not in ("16:9", "9:16") and provider.name != "mock":
        raise ValueError("AI video models support 16:9 or 9:16")
    workdir.mkdir(parents=True, exist_ok=True)
    continuous = mode == "extend"
    n = shots_needed(seconds, provider, mode)

    plan_path = workdir / "plan.json"
    if plan_data:
        film = FilmPlan.model_validate(plan_data)
    elif plan_path.exists():
        film = FilmPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    else:
        film = plan(idea, n, provider.clip_seconds, continuous, use_llm)
    # pad / cut the shot list to exactly n shots
    film.shots = (film.shots + [film.shots[-1]] * n)[:n]
    plan_path.write_text(film.model_dump_json(indent=2), encoding="utf-8")
    progress(f"Plan: '{film.title}' - {n} shots, {mode} mode, provider {provider.name}")

    prompts = [shot_prompt(film, i, continuous) for i in range(n)]
    clips: list[Path] = []
    for i, prompt in enumerate(prompts):
        clip = workdir / f"clip_{i:02d}.mp4"
        if not clip.exists():
            progress(f"Generating shot {i + 1}/{n} ...")
            tmp = clip.with_name(clip.stem + "_tmp.mp4")
            if continuous and i > 0:
                provider.extend(clips[-1], prompt, tmp)
            else:
                provider.generate(prompt, aspect, tmp)
            tmp.replace(clip)
        clips.append(clip)

    progress("Finishing video ...")
    joined = clips[-1] if continuous else stitch(clips, workdir / "joined.mp4", None, aspect)
    out.parent.mkdir(parents=True, exist_ok=True)
    finish(joined, out, seconds, music, music_volume)
    (workdir / "prompts.json").write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    return {"title": film.title, "mode": mode, "provider": provider.name, "shots": n,
            "duration": round(media_duration(out), 2), "plan": film.model_dump(), "prompts": prompts}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="script2video.film", description="One prompt -> one AI-generated video.")
    p.add_argument("idea", nargs="?", help="The video idea / prompt (or use --prompt-file)")
    p.add_argument("--prompt-file", type=Path)
    p.add_argument("--seconds", type=float, default=40)
    p.add_argument("--aspect", choices=["16:9", "9:16", "1:1"], default="16:9")
    p.add_argument("--provider", default="auto", help="auto | veo | fal | mock")
    p.add_argument("--mode", default="auto", help="auto | extend (continuous take) | shots (crossfaded clips)")
    p.add_argument("--music", type=Path)
    p.add_argument("--music-volume", type=float, default=0.3)
    p.add_argument("--no-llm", action="store_true", help="Plan shots without Claude")
    p.add_argument("--workdir", type=Path)
    p.add_argument("-o", "--out", type=Path)
    args = p.parse_args(argv)
    idea = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.idea
    if not idea:
        p.error("give an idea or --prompt-file")
    name = slugify(idea[:60])
    result = make_film(idea, seconds=args.seconds, aspect=args.aspect, provider_name=args.provider, mode=args.mode,
                       workdir=args.workdir or Path("output") / f"film-{name}",
                       out=args.out or Path("output") / f"{name}.mp4", use_llm=not args.no_llm,
                       music=args.music, music_volume=args.music_volume)
    log(f"Done: {result['duration']}s, {result['shots']} shots ({result['mode']}) -> {args.out or 'output/'}")


if __name__ == "__main__":
    main()
