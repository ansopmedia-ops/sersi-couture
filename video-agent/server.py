"""Tiny HTTP rendering service so n8n (or Zapier, Make, a website...) can call script2video.

    python server.py                    # listens on 0.0.0.0:8000

POST /render   JSON body:
    script        str   the narration script (required unless `storyboard` is given)
    storyboard    obj   optional {"title", "scenes": [...]} - skip planning and render this exactly
    aspect        str   16:9 | 9:16 | 1:1 | 4:5           (default 9:16)
    voice         str   auto | elevenlabs | gtts | espeak | silent
    visuals       str   auto | pexels | cards
    style         str   brand/style notes for the planner
    lang          str   voice language code
    music_url     str   optional background-music URL (auto-ducked)
    captions      bool  burn in captions (default true)
    draft         bool  half resolution, faster
  -> {"id", "title", "duration", "scenes", "video_url", "srt_url", "storyboard"}

GET  /files/<id>/<video.mp4|video.srt|storyboard.json>
GET  /health

Env: PORT, PUBLIC_BASE_URL (how browsers reach this service, default http://localhost:PORT),
     RENDER_TOKEN (if set, requests need "Authorization: Bearer <token>"), JOBS_DIR,
     plus the usual ANTHROPIC_API_KEY / PEXELS_API_KEY / ELEVENLABS_API_KEY.
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from script2video.cli import build_parser, run
from script2video.util import ASPECTS, log, media_duration

PORT = int(os.environ.get("PORT", "8000"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{PORT}").rstrip("/")
TOKEN = os.environ.get("RENDER_TOKEN", "")
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs")).resolve()
SERVED = {"video.mp4": "video/mp4", "video.srt": "application/x-subrip", "storyboard.json": "application/json"}

_render_lock = threading.Lock()  # one render at a time - ffmpeg already uses every core


def render(body: dict) -> dict:
    script = (body.get("script") or "").strip()
    storyboard = body.get("storyboard")
    if not script and not storyboard:
        raise ValueError("Send a 'script' (text) or a 'storyboard' object.")
    aspect = body.get("aspect", "9:16")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {', '.join(ASPECTS)}")

    job_id = uuid.uuid4().hex
    job = JOBS_DIR / job_id
    work = job / "work"
    work.mkdir(parents=True)
    script_path = job / "script.txt"
    script_path.write_text(script or "\n\n".join(s["narration"] for s in storyboard["scenes"]), encoding="utf-8")
    if storyboard:
        if isinstance(storyboard, str):
            storyboard = json.loads(storyboard)
        # the pipeline reuses an existing storyboard.json instead of planning
        (work / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")

    argv = [str(script_path), "--workdir", str(work), "--out", str(job / "video.mp4"), "--aspect", aspect]
    for key in ("voice", "visuals", "style", "lang"):
        if body.get(key):
            argv += [f"--{key}", str(body[key])]
    if body.get("draft"):
        argv.append("--draft")
    if body.get("captions") is False:
        argv.append("--no-captions")
    if body.get("music_url"):
        import requests

        music = job / "music"
        r = requests.get(body["music_url"], timeout=120)
        r.raise_for_status()
        music.write_bytes(r.content)
        argv += ["--music", str(music)]

    with _render_lock:
        log(f"job {job_id}: rendering")
        out = run(build_parser().parse_args(argv))
    (job / "storyboard.json").write_bytes((work / "storyboard.json").read_bytes())
    board = json.loads((job / "storyboard.json").read_text(encoding="utf-8"))
    base = f"{PUBLIC_BASE_URL}/files/{job_id}"
    return {
        "id": job_id,
        "title": board.get("title", ""),
        "duration": round(media_duration(out), 2),
        "scenes": len(board.get("scenes", [])),
        "video_url": f"{base}/video.mp4",
        "srt_url": f"{base}/video.srt" if (job / "video.srt").exists() else None,
        "storyboard": board,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "script2video/0.1"

    def _send(self, code: int, payload: dict | None = None, *, body: bytes | None = None, ctype: str = "application/json") -> None:
        data = body if body is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if not TOKEN or self.headers.get("Authorization", "") == f"Bearer {TOKEN}":
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"ok": True})
        m = re.fullmatch(r"/files/([0-9a-f]{32})/([a-z0-9.]+)", self.path)
        if not m or m.group(2) not in SERVED:
            return self._send(404, {"error": "not found"})
        path = JOBS_DIR / m.group(1) / m.group(2)
        if not path.exists():
            return self._send(404, {"error": "not found"})
        self._send(200, body=path.read_bytes(), ctype=SERVED[m.group(2)])

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/render":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, render(body))
        except (ValueError, KeyError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Render service on :{PORT}  (public URL {PUBLIC_BASE_URL})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
