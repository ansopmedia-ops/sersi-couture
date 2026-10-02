"""Step 3 - pick a visual for each scene.

Sources (pick with --visuals, default "auto"):
  assets   your own photos/clips from --assets DIR, used in filename order
  pexels   free stock video/photos, needs PEXELS_API_KEY (https://www.pexels.com/api/)
  cards    generated branded gradient cards (offline, always works)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .util import hex_to_rgb, log

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}


@dataclass
class Visual:
    path: Path
    kind: str  # "image" | "video"
    source: str


class VisualPicker:
    def __init__(self, mode: str, size: tuple[int, int], palette: list[str], assets: Path | None = None):
        self.size = size
        self.palette = [hex_to_rgb(c) for c in palette]
        self.assets = sorted(
            p for p in (assets.iterdir() if assets else []) if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT
        )
        if mode == "auto":
            mode = "assets" if self.assets else ("pexels" if os.environ.get("PEXELS_API_KEY") else "cards")
        if mode == "assets" and not self.assets:
            raise ValueError("--visuals assets needs --assets DIR containing images or videos")
        if mode == "pexels" and not os.environ.get("PEXELS_API_KEY"):
            raise ValueError("--visuals pexels needs PEXELS_API_KEY")
        self.mode = mode
        self._used_ids: set[int] = set()

    def pick(self, index: int, query: str, out_dir: Path) -> Visual:
        if self.mode == "assets":
            src = self.assets[index % len(self.assets)]
            return Visual(src, "video" if src.suffix.lower() in VIDEO_EXT else "image", "assets")
        if self.mode == "pexels":
            try:
                found = self._pexels(index, query, out_dir)
                if found:
                    return found
                log(f"Pexels found nothing for {query!r}; using a card.")
            except Exception as exc:  # noqa: BLE001
                log(f"Pexels error for {query!r} ({exc}); using a card.")
        return Visual(self._card(index, out_dir), "image", "card")

    # -- Pexels -------------------------------------------------------------
    def _pexels(self, index: int, query: str, out_dir: Path) -> Visual | None:
        import requests

        w, h = self.size
        orientation = "portrait" if h > w else ("square" if h == w else "landscape")
        headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": orientation, "per_page": 10, "size": "medium"},
            headers=headers, timeout=30,
        )
        r.raise_for_status()
        for video in r.json().get("videos", []):
            if video["id"] in self._used_ids or video.get("duration", 0) < 3:
                continue
            files = [f for f in video["video_files"] if f.get("file_type") == "video/mp4" and f.get("width")]
            if not files:
                continue
            # smallest file that still covers the frame, else the largest
            target = min(w, h)
            best = min(files, key=lambda f: (min(f["width"], f["height"]) < target, abs(min(f["width"], f["height"]) - target)))
            dest = out_dir / f"scene_{index:02d}_pexels.mp4"
            _download(best["link"], dest)
            self._used_ids.add(video["id"])
            return Visual(dest, "video", f"pexels:{video['id']}")

        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "orientation": orientation, "per_page": 10},
            headers=headers, timeout=30,
        )
        r.raise_for_status()
        for photo in r.json().get("photos", []):
            if photo["id"] in self._used_ids:
                continue
            dest = out_dir / f"scene_{index:02d}_pexels.jpg"
            _download(photo["src"]["large2x"], dest)
            self._used_ids.add(photo["id"])
            return Visual(dest, "image", f"pexels:{photo['id']}")
        return None

    # -- Generated cards ----------------------------------------------------
    def _card(self, index: int, out_dir: Path) -> Path:
        dest = out_dir / f"scene_{index:02d}_card.png"
        w, h = self.size
        c1 = self.palette[index % len(self.palette)]
        c2 = self.palette[(index + 1) % len(self.palette)]
        img = Image.new("RGB", (w, h), c1)
        draw = ImageDraw.Draw(img)
        for y in range(h):  # diagonal-ish vertical gradient
            t = y / max(1, h - 1)
            draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        r = int(min(w, h) * 0.45)
        cx, cy = (w * (0.25 + 0.5 * (index % 2)), h * 0.35)
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 40))
        glow = glow.filter(ImageFilter.GaussianBlur(r // 3))
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
        img.save(dest)
        return dest


def _download(url: str, dest: Path) -> None:
    import requests

    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
