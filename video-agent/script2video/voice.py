"""Step 2 - voiceover. Every provider writes a normalised 44.1 kHz stereo WAV.

Providers (pick with --voice, default "auto"):
  elevenlabs  natural studio voices, needs ELEVENLABS_API_KEY (optional ELEVENLABS_VOICE_ID)
  gtts        free Google Translate TTS, needs internet + `pip install gTTS`
  espeak      fully offline, robotic; needs espeak-ng installed
  silent      no voice - timed silence (captions + music only)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .util import log, run_ffmpeg

DEFAULT_ELEVEN_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"
WORDS_PER_SECOND = 2.6


def _normalise(src: Path, dst: Path) -> None:
    run_ffmpeg(["-i", str(src), "-ar", "44100", "-ac", "2", str(dst)])
    if src != dst:
        src.unlink(missing_ok=True)


def _elevenlabs(text: str, out: Path, lang: str) -> None:
    import requests

    voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_ELEVEN_VOICE)
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
        json={"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")},
        timeout=120,
    )
    resp.raise_for_status()
    tmp = out.with_suffix(".mp3")
    tmp.write_bytes(resp.content)
    _normalise(tmp, out)


def _gtts(text: str, out: Path, lang: str) -> None:
    from gtts import gTTS

    tmp = out.with_suffix(".mp3")
    gTTS(text=text, lang=lang).save(str(tmp))
    _normalise(tmp, out)


def _espeak(text: str, out: Path, lang: str) -> None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng not installed")
    tmp = out.with_name(out.stem + "_raw.wav")
    subprocess.run([exe, "-v", lang, "-s", "160", "-w", str(tmp), text], check=True, capture_output=True)
    _normalise(tmp, out)


def _silent(text: str, out: Path, lang: str) -> None:
    seconds = max(2.0, len(text.split()) / WORDS_PER_SECOND)
    run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.2f}", str(out)])


PROVIDERS = {"elevenlabs": _elevenlabs, "gtts": _gtts, "espeak": _espeak, "silent": _silent}


def _auto_order() -> list[str]:
    order = []
    if os.environ.get("ELEVENLABS_API_KEY"):
        order.append("elevenlabs")
    try:
        import gtts  # noqa: F401
        order.append("gtts")
    except ImportError:
        pass
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        order.append("espeak")
    order.append("silent")
    return order


class Narrator:
    def __init__(self, provider: str = "auto", lang: str = "en"):
        self.lang = lang
        self.order = _auto_order() if provider == "auto" else [provider]
        if provider != "auto" and provider not in PROVIDERS:
            raise ValueError(f"Unknown voice provider {provider!r}; choose from {', '.join(PROVIDERS)} or auto")

    def speak(self, text: str, out: Path) -> str:
        """Synthesize `text` into `out` (.wav). Returns the provider that succeeded."""
        last_err: Exception | None = None
        for name in list(self.order):
            try:
                PROVIDERS[name](text, out, self.lang)
                return name
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_err = exc
                if len(self.order) > 1:
                    log(f"Voice provider '{name}' failed ({exc}); trying next.")
                    self.order.remove(name)  # don't retry a broken provider for every scene
        raise RuntimeError(f"All voice providers failed: {last_err}")
