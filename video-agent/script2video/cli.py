"""script2video - turn a text script into a finished narrated video.

Pipeline:  script -> [plan] storyboard.json -> [voice] scene audio -> [visuals] footage/cards
           -> [render] scene clips -> [assemble] captions + music -> final .mp4

Every step caches its output in the work directory, so you can edit storyboard.json
(narration, headlines, stock queries) and re-run to regenerate only what changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from . import planner
from .captions import build_srt
from .render import LEAD, TAIL, assemble, make_overlay, render_scene
from .util import ASPECTS, find_font, hex_to_rgb, log, media_duration, require_ffmpeg, slugify
from .visuals import Visual, VisualPicker
from .voice import Narrator

DEFAULT_PALETTE = "1A1208,1E3528,C8922A,3B2A16"  # Sersi Couture ink / forest / gold / umber


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:10]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="script2video", description="Generate a full narrated video from a text script.")
    p.add_argument("script", type=Path, help="Path to a .txt/.md script")
    p.add_argument("-o", "--out", type=Path, help="Output .mp4 (default: output/<title>.mp4)")
    p.add_argument("--workdir", type=Path, help="Cache directory (default: output/<script-name>/)")
    p.add_argument("--aspect", choices=sorted(ASPECTS), default="16:9", help="16:9 YouTube, 9:16 Reels/TikTok/Shorts, 1:1, 4:5")
    p.add_argument("--draft", action="store_true", help="Render at half resolution for quick previews")
    p.add_argument("--voice", default="auto", help="auto | elevenlabs | gtts | espeak | silent")
    p.add_argument("--lang", default="en", help="Voice language code (default: en)")
    p.add_argument("--visuals", default="auto", help="auto | assets | pexels | cards")
    p.add_argument("--assets", type=Path, help="Folder of your own images/clips to use as visuals")
    p.add_argument("--music", type=Path, help="Background music file (auto-ducked under the voice)")
    p.add_argument("--music-volume", type=float, default=0.25)
    p.add_argument("--palette", default=DEFAULT_PALETTE, help="Comma-separated hex colours for cards/accents")
    p.add_argument("--font", help="TTF font for headlines")
    p.add_argument("--style", help="Brand/style notes passed to the AI director")
    p.add_argument("--rewrite", action="store_true", help="Let Claude tighten/rewrite the narration")
    p.add_argument("--no-llm", action="store_true", help="Skip Claude; use the offline rule-based planner")
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--replan", action="store_true", help="Ignore cached storyboard.json and plan again")
    p.add_argument("--plan-only", action="store_true", help="Stop after writing storyboard.json (to review/edit)")
    p.add_argument("--force", action="store_true", help="Regenerate all cached audio/visual/clip files")
    return p


def run(args: argparse.Namespace) -> Path:
    require_ffmpeg()
    script = args.script.read_text(encoding="utf-8")
    workdir = args.workdir or Path("output") / slugify(args.script.stem)
    for sub in ("audio", "visuals", "clips"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    w, h = ASPECTS[args.aspect]
    if args.draft:
        w, h = w // 2, h // 2
    size = (w, h)
    palette = [c.strip() for c in args.palette.split(",") if c.strip()]

    # 1. Plan ---------------------------------------------------------------
    sb_path = workdir / "storyboard.json"
    if sb_path.exists() and not args.replan:
        log(f"Using existing {sb_path} (pass --replan to regenerate).")
        board = planner.Storyboard.model_validate_json(sb_path.read_text(encoding="utf-8"))
    else:
        board = planner.plan(script, use_llm=not args.no_llm, style=args.style, rewrite=args.rewrite)
        sb_path.write_text(board.model_dump_json(indent=2), encoding="utf-8")
    log(f"Storyboard: '{board.title}' - {len(board.scenes)} scenes -> {sb_path}")
    if args.plan_only:
        return sb_path

    narrator = Narrator(args.voice, args.lang)
    picker = VisualPicker(args.visuals, size, palette, args.assets)
    font = find_font(args.font)
    accent = hex_to_rgb(palette[2] if len(palette) > 2 else palette[0])

    clips: list[Path] = []
    segments: list[tuple[str, float, float]] = []
    manifest = []
    t = 0.0
    for i, scene in enumerate(board.scenes):
        key = _digest(scene.narration, args.voice, args.lang)
        # 2. Voice ----------------------------------------------------------
        audio = workdir / "audio" / f"scene_{i:02d}_{key}.wav"
        if args.force or not audio.exists():
            used = narrator.speak(scene.narration, audio)
            log(f"[{i + 1}/{len(board.scenes)}] voice ({used}): {scene.narration[:60]}...")
        speech = media_duration(audio)
        duration = LEAD + speech + TAIL

        # 3. Visual ---------------------------------------------------------
        vis_key = _digest(scene.stock_query, picker.mode, f"{w}x{h}", args.palette)
        cached = sorted((workdir / "visuals").glob(f"scene_{i:02d}_*{vis_key}*"))
        if cached and not args.force and picker.mode != "assets":
            path = cached[0]
            visual = Visual(path, "video" if path.suffix == ".mp4" else "image", "cache")
        else:
            visual = picker.pick(i, scene.stock_query, workdir / "visuals")
            if visual.source != "assets":
                renamed = visual.path.with_name(f"{visual.path.stem}_{vis_key}{visual.path.suffix}")
                visual.path.replace(renamed)
                visual.path = renamed

        # 4. Render scene ---------------------------------------------------
        overlay = make_overlay(size, scene.on_screen_text, font, accent, workdir / "visuals" / f"overlay_{i:02d}.png")
        clip_key = _digest(key, str(visual.path), scene.on_screen_text, f"{w}x{h}", f"{duration:.3f}")
        clip = workdir / "clips" / f"scene_{i:02d}_{clip_key}.mp4"
        if args.force or not clip.exists():
            render_scene(visual, audio, overlay, duration, size, i, clip)
            log(f"[{i + 1}/{len(board.scenes)}] rendered {duration:.1f}s clip ({visual.source})")
        clips.append(clip)
        segments.append((scene.narration, t + LEAD, t + LEAD + speech))
        manifest.append({"scene": i, "start": round(t, 2), "duration": round(duration, 2),
                         "visual": str(visual.path), "visual_source": visual.source, "audio": str(audio)})
        t += duration

    # 5. Assemble -----------------------------------------------------------
    srt = None if args.no_captions else build_srt(segments, workdir / "captions.srt")
    out = args.out or Path("output") / f"{slugify(board.title)}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    log("Assembling final video ...")
    assemble(clips, srt, args.music, args.music_volume, size, workdir, out)
    if srt:
        shutil.copyfile(srt, out.with_suffix(".srt"))
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"Done: {out}  ({t:.1f}s, {len(clips)} scenes, {w}x{h})")
    return out


def main(argv: list[str] | None = None) -> None:
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
