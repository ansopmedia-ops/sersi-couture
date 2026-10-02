"""Step 4 - captions. Splits each scene's narration into short phrases and times them
proportionally across that scene's speech, producing an .srt for the whole video."""
from __future__ import annotations

import re
from pathlib import Path


def _phrases(text: str, max_words: int) -> list[str]:
    """Pack clauses into caption lines of at most `max_words`, splitting long clauses evenly."""
    pieces: list[str] = []
    for clause in re.split(r"(?<=[,.;:!?])\s+", " ".join(text.split())):
        words = clause.split()
        if not words:
            continue
        n = -(-len(words) // max_words)
        size = -(-len(words) // n)
        pieces += [" ".join(words[k:k + size]) for k in range(0, len(words), size)]
    out: list[str] = []
    for piece in pieces:
        # join with the previous line unless it ended a sentence or would get too long
        if out and not out[-1].endswith((".", "!", "?")) and len((out[-1] + " " + piece).split()) <= max_words:
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[tuple[str, float, float]], out: Path, max_words: int = 7) -> Path:
    """segments: (narration, speech_start, speech_end) in absolute video time."""
    lines: list[str] = []
    n = 0
    for text, start, end in segments:
        phrases = _phrases(text, max_words)
        total = sum(len(p) for p in phrases) or 1
        t = start
        for p in phrases:
            dur = (end - start) * len(p) / total
            n += 1
            lines += [str(n), f"{_ts(t)} --> {_ts(t + dur - 0.02)}", p, ""]
            t += dur
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
