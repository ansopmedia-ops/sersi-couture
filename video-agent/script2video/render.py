"""Step 5 - render each scene clip with ffmpeg, then stitch, caption and score the final video."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .util import FPS, run_ffmpeg
from .visuals import Visual

LEAD = 0.25  # silence before speech in each scene (s)
TAIL = 0.45  # breathing room after speech (s)
FADE = 0.3


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    lines: list[str] = []
    for word in text.split():
        if lines and draw.textlength(f"{lines[-1]} {word}", font=font) <= max_width:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return "\n".join(lines)


def make_overlay(size: tuple[int, int], headline: str, font_path: str | None, accent: tuple[int, int, int], out: Path) -> Path:
    """Transparent PNG: bottom shade for caption legibility + optional headline."""
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade_h = int(h * 0.38)
    shade = Image.linear_gradient("L").resize((w, shade_h))  # 0 at top -> 255 at bottom
    img.paste(Image.new("RGBA", (w, shade_h), (0, 0, 0, 255)), (0, h - shade_h), shade.point(lambda v: int(v * 0.6)))

    if headline.strip():
        draw = ImageDraw.Draw(img)
        size_px = int(min(w, h) * 0.085)
        font = ImageFont.truetype(font_path, size_px) if font_path else ImageFont.load_default(size=size_px)
        text = _wrap(draw, headline.upper(), font, int(w * 0.84))
        box = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=size_px // 4)
        tw, th = box[2] - box[0], box[3] - box[1]
        x, y = (w - tw) // 2, int(h * (0.16 if h > w else 0.12))
        bar_w = int(size_px * 1.6)
        draw.rectangle([(w - bar_w) // 2, y - size_px // 2, (w + bar_w) // 2, y - size_px // 2 + max(4, size_px // 12)], fill=accent + (255,))
        for dx, dy in ((3, 3), (2, 2)):
            draw.multiline_text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 150), align="center", spacing=size_px // 4)
        draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255), align="center", spacing=size_px // 4)
    img.save(out)
    return out


def render_scene(visual: Visual, audio: Path, overlay: Path, duration: float, size: tuple[int, int], index: int, out: Path) -> Path:
    w, h = size
    frames = int(round(duration * FPS))
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if visual.kind == "video":
        v_in = ["-stream_loop", "-1", "-i", str(visual.path)]
        base = f"[0:v]{cover},fps={FPS},setsar=1"
    else:
        # Ken Burns: slow zoom, alternating zoom-in / zoom-out per scene
        v_in = ["-i", str(visual.path)]
        z = f"1+0.12*on/{frames}" if index % 2 == 0 else f"1.12-0.12*on/{frames}"
        base = (
            f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},setsar=1"
        )
    graph = (
        f"{base}[bg];"
        f"[1:v]format=rgba,fade=in:st=0.15:d=0.5:alpha=1[ov];"
        f"[bg][ov]overlay=0:0:shortest=1,"
        f"fade=in:st=0:d={FADE},fade=out:st={duration - FADE:.3f}:d={FADE},format=yuv420p[v];"
        f"[2:a]adelay={int(LEAD * 1000)}:all=1,apad,atrim=0:{duration:.3f},"
        f"afade=out:st={duration - 0.15:.3f}:d=0.15[a]"
    )
    run_ffmpeg([
        *v_in,
        "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
        "-i", str(audio),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        str(out),
    ])
    return out


def assemble(clips: list[Path], srt: Path | None, music: Path | None, music_volume: float,
             size: tuple[int, int], workdir: Path, out: Path) -> Path:
    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips))
    joined = workdir / "joined.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])

    w, h = size
    args = ["-i", joined.name]
    vf = "null"
    if srt is not None:
        portrait = h > w
        style = (
            "FontName=DejaVu Sans,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,"
            f"Outline=1.6,Shadow=0,Alignment=2,FontSize={14 if portrait else 18},MarginV={70 if portrait else 22}"
        )
        vf = f"subtitles={srt.name}:force_style='{style}'"  # relative path: avoids ffmpeg filter escaping
    filters = f"[0:v]{vf}[v]"
    a_map = "0:a"
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music.resolve())]
        filters += (
            f";[0:a]asplit=2[voice][key];[1:a]volume={music_volume}[bed];"
            "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=500[duck];"
            "[voice][duck]amix=inputs=2:duration=first:normalize=0[a]"
        )
        a_map = "[a]"
    run_ffmpeg([
        *args, "-filter_complex", filters, "-map", "[v]", "-map", a_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest",
        str(out.resolve()),
    ], cwd=workdir)
    return out
