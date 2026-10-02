# script2video: AI agent workflow that turns a text script into a video

Give it a plain-text script and it makes a finished, narrated MP4 with no further input. The video has a voiceover, visuals for each scene, animated headlines, burned-in captions and optional background music.

```
 script.txt
    │
    ▼
 1. PLAN      Claude (AI director) splits the script into scenes and writes for each one:
              narration · on-screen headline · stock-footage query · image prompt · mood
              → storyboard.json   (you can edit this file and re-run)
    │
    ▼
 2. VOICE     voiceover per scene: ElevenLabs → Google TTS → espeak → silent (first that works)
    │
    ▼
 3. VISUALS   your own photos/clips  →  Pexels stock video/photos  →  generated brand cards
    │
    ▼
 4. RENDER    ffmpeg per scene: cover-crop or Ken Burns zoom, headline overlay, fades
    │
    ▼
 5. ASSEMBLE  stitch scenes · burn timed captions (.srt also exported) · duck music under voice
    │
    ▼
 output/<title>.mp4
```

## Quick start

```bash
# prerequisites: Python 3.10+, ffmpeg
cd video-agent
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...      # AI director (optional, falls back to a rule-based planner)
export PEXELS_API_KEY=...                # free stock footage (optional, https://www.pexels.com/api/)
export ELEVENLABS_API_KEY=...            # studio-quality voice (optional)

python -m script2video examples/sersi-couture-promo.txt --aspect 9:16
```

With no keys set it still works: the voiceover uses free Google TTS and the visuals are gradient cards in the brand colours.

## Common recipes

| Goal | Command |
|---|---|
| Instagram Reel / TikTok / Shorts | `--aspect 9:16` |
| YouTube | `--aspect 16:9` (default) |
| Use your own product photos/clips | `--assets path/to/folder` (used in filename order, one per scene) |
| Add background music | `--music track.mp3` (automatically ducked under the voice) |
| Steer the look and copy | `--style "luxury fashion, warm gold tones, slow and elegant"` |
| Let the AI tighten the copy | `--rewrite` |
| Quick low-res preview | `--draft` |
| Review the storyboard first | `--plan-only`, edit `output/<name>/storyboard.json`, then run again without it |
| Re-plan from scratch | `--replan` |
| Different language voice | `--lang fr` |

Each step caches its results in `output/<script-name>/`. If you edit one scene's narration or headline in `storyboard.json`, only that scene's audio and clip are rebuilt on the next run.

## Run it automatically on GitHub

`.github/workflows/script-to-video.yml` builds a video whenever you push a `.txt`/`.md` script to `video-agent/scripts/`. You can also run it by hand from the **Actions → Script to Video → Run workflow** button, where you choose the script, aspect ratio, voice and style. The MP4 and its storyboard show up as a downloadable **videos** artifact on the run.

Add these keys under **Settings → Secrets and variables → Actions**. All of them are optional:

- `ANTHROPIC_API_KEY`: the AI director
- `PEXELS_API_KEY`: stock footage
- `ELEVENLABS_API_KEY` (secret) and `ELEVENLABS_VOICE_ID` (variable): premium voice

## Writing scripts

Write the script the way you'd say it. Blank lines between paragraphs make natural scene breaks. The planner drops lines like `[music]`, `(pause)`, `# Heading` or `SCENE 1:`. About 150 words gives you roughly a minute of video.

## Files

```
script2video/
  cli.py        pipeline orchestration + caching
  planner.py    Claude storyboard (structured JSON) + offline fallback
  voice.py      TTS providers with automatic fallback
  visuals.py    assets / Pexels / generated cards
  captions.py   phrase-level timed .srt
  render.py     ffmpeg scene rendering and final assembly
```
