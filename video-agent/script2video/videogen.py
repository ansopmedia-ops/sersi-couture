"""AI text-to-video providers used by `film.py`.

  veo   Google Veo via the Gemini API (GEMINI_API_KEY). Supports `extend`, which
        continues a clip 7 s at a time - a true continuous long take.
        Model: VEO_MODEL (default veo-3.1-generate-preview).
  fal   Any fal.ai video model (FAL_KEY). Model: FAL_MODEL (default fal-ai/veo3.1).
        Extra JSON input fields can be passed with FAL_ARGS='{"resolution": "1080p"}'.
  mock  Offline placeholder clips made with ffmpeg - for testing the pipeline for free.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from .util import find_font, log, media_duration, run_ffmpeg

POLL_SECONDS = float(os.environ.get("VIDEOGEN_POLL_SECONDS", "10"))
TIMEOUT_SECONDS = 20 * 60


class VideoProvider:
    name = "base"
    clip_seconds = 8          # length of one generated clip
    extend_seconds = 0        # seconds added per extension (0 = extension unsupported)

    def generate(self, prompt: str, aspect: str, out: Path) -> Path:
        raise NotImplementedError

    def extend(self, video: Path, prompt: str, out: Path) -> Path:
        raise NotImplementedError(f"{self.name} cannot extend videos; use --mode shots")


def _download(url: str, dest: Path, headers: dict | None = None) -> Path:
    import requests

    with requests.get(url, headers=headers or {}, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
    return dest


# -- Google Veo (Gemini API) --------------------------------------------------
class VeoProvider(VideoProvider):
    name = "veo"
    clip_seconds = 8
    extend_seconds = 7
    base = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self) -> None:
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.key:
            raise ValueError("The veo provider needs GEMINI_API_KEY (https://aistudio.google.com/apikey)")
        self.model = os.environ.get("VEO_MODEL", "veo-3.1-generate-preview")
        self.base = os.environ.get("VEO_BASE_URL", self.base).rstrip("/")
        self.person_generation = os.environ.get("VEO_PERSON_GENERATION")  # e.g. allow_adult in the EU/UK

    def _run(self, instance: dict, parameters: dict, out: Path) -> Path:
        import requests

        if self.person_generation:
            parameters["personGeneration"] = self.person_generation
        headers = {"x-goog-api-key": self.key}
        r = requests.post(
            f"{self.base}/models/{self.model}:predictLongRunning",
            headers=headers, json={"instances": [instance], "parameters": parameters}, timeout=300,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Veo request failed ({r.status_code}): {r.text[:500]}")
        name = r.json()["name"]
        deadline = time.time() + TIMEOUT_SECONDS
        while True:
            op = requests.get(f"{self.base}/{name}", headers=headers, timeout=60).json()
            if op.get("done"):
                break
            if time.time() > deadline:
                raise TimeoutError(f"Veo operation {name} did not finish in time")
            time.sleep(POLL_SECONDS)
        if "error" in op:
            raise RuntimeError(f"Veo error: {op['error'].get('message', op['error'])}")
        result = op.get("response", {}).get("generateVideoResponse", {})
        samples = result.get("generatedSamples") or []
        if not samples:
            reasons = result.get("raiMediaFilteredReasons") or ["no video returned"]
            raise RuntimeError(f"Veo blocked or returned no video: {'; '.join(map(str, reasons))}")
        return _download(samples[0]["video"]["uri"], out, headers)

    def generate(self, prompt: str, aspect: str, out: Path) -> Path:
        return self._run({"prompt": prompt},
                         {"aspectRatio": aspect, "durationSeconds": self.clip_seconds, "resolution": "720p"}, out)

    def extend(self, video: Path, prompt: str, out: Path) -> Path:
        data = base64.b64encode(video.read_bytes()).decode()
        instance = {"prompt": prompt, "video": {"inlineData": {"mimeType": "video/mp4", "data": data}}}
        return self._run(instance, {"numberOfVideos": 1, "resolution": "720p"}, out)


# -- fal.ai -------------------------------------------------------------------
class FalProvider(VideoProvider):
    name = "fal"

    def __init__(self) -> None:
        self.key = os.environ.get("FAL_KEY")
        if not self.key:
            raise ValueError("The fal provider needs FAL_KEY (https://fal.ai/dashboard/keys)")
        self.model = os.environ.get("FAL_MODEL", "fal-ai/veo3.1")
        self.queue = os.environ.get("FAL_QUEUE_URL", "https://queue.fal.run").rstrip("/")
        self.extra = json.loads(os.environ.get("FAL_ARGS", "{}"))
        self.clip_seconds = int(os.environ.get("FAL_CLIP_SECONDS", "8"))

    def generate(self, prompt: str, aspect: str, out: Path) -> Path:
        import requests

        headers = {"Authorization": f"Key {self.key}"}
        # Veo-style models take "8s"; most others (Kling, etc.) take "5"/"10".
        duration = f"{self.clip_seconds}s" if "veo" in self.model else str(self.clip_seconds)
        payload = {"prompt": prompt, "aspect_ratio": aspect, "duration": duration, **self.extra}
        r = requests.post(f"{self.queue}/{self.model}", headers=headers, json=payload, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"fal request failed ({r.status_code}): {r.text[:500]}")
        sub = r.json()
        deadline = time.time() + TIMEOUT_SECONDS
        while True:
            status = requests.get(sub["status_url"], headers=headers, timeout=60).json()
            if status.get("status") == "COMPLETED":
                break
            if time.time() > deadline:
                raise TimeoutError(f"fal request {sub.get('request_id')} did not finish in time")
            time.sleep(POLL_SECONDS)
        if status.get("error"):
            raise RuntimeError(f"fal error: {status['error']}")
        result = requests.get(sub["response_url"], headers=headers, timeout=120)
        if result.status_code >= 400:
            raise RuntimeError(f"fal result failed ({result.status_code}): {result.text[:500]}")
        return _download(result.json()["video"]["url"], out)


# -- Offline mock -------------------------------------------------------------
class MockProvider(VideoProvider):
    name = "mock"
    clip_seconds = 8
    extend_seconds = 7
    _sizes = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}

    def _clip(self, prompt: str, aspect: str, seconds: int, out: Path) -> Path:
        w, h = self._sizes.get(aspect, (1280, 720))
        text_file = out.with_suffix(".txt")
        words = prompt.split()
        lines = [" ".join(words[i:i + 6]) for i in range(0, min(len(words), 36), 6)]
        text_file.write_text("MOCK CLIP\n" + "\n".join(lines))
        font = find_font()
        draw = f"drawtext=textfile='{text_file.name}':fontcolor=white:fontsize={h // 24}:x=(w-tw)/2:y=(h-th)/2:line_spacing=8"
        if font:
            draw += f":fontfile='{font}'"
        hue = (abs(hash(prompt)) % 360)
        run_ffmpeg([
            "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=24:d={seconds}",
            "-f", "lavfi", "-i", f"sine=f={220 + hue}:d={seconds}",
            "-vf", f"hue=h={hue}:s=0.5,boxblur=8,{draw}", "-af", "volume=0.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out.name,
        ], cwd=out.parent)
        text_file.unlink(missing_ok=True)
        return out

    def generate(self, prompt: str, aspect: str, out: Path) -> Path:
        return self._clip(prompt, aspect, self.clip_seconds, out)

    def extend(self, video: Path, prompt: str, out: Path) -> Path:
        w, h = _video_size(video)
        aspect = "9:16" if h > w else ("1:1" if h == w else "16:9")
        extra = self._clip(prompt, aspect, self.extend_seconds, out.with_name(out.stem + "_ext.mp4"))
        listing = out.with_suffix(".list")
        listing.write_text(f"file '{video.resolve().as_posix()}'\nfile '{extra.resolve().as_posix()}'\n")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])
        listing.unlink(missing_ok=True)
        return out


PROVIDERS = {"veo": VeoProvider, "fal": FalProvider, "mock": MockProvider}


def get_provider(name: str) -> VideoProvider:
    if name == "auto":
        name = "veo" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) else \
               "fal" if os.environ.get("FAL_KEY") else "mock"
        if name == "mock":
            log("No GEMINI_API_KEY or FAL_KEY set - using the offline mock provider (placeholder clips).")
    if name not in PROVIDERS:
        raise ValueError(f"Unknown video provider {name!r}; choose from {', '.join(PROVIDERS)} or auto")
    return PROVIDERS[name]()


def _video_size(path: Path) -> tuple[int, int]:
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def has_audio(path: Path) -> bool:
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return bool(out.strip())


__all__ = ["get_provider", "has_audio", "VideoProvider"]
