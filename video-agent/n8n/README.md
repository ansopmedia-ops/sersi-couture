# Script → Video in n8n

This is an n8n workflow that turns a script you paste into a form into a finished, narrated video:

```
[Script Form] → [Build Claude Request] → [AI Director (Claude)] → [Parse Storyboard] → [Render Video] → [Show Video]
  n8n form        builds the prompt         plans the scenes          checks the JSON        voice + visuals      page with player
                                                                                             + captions (ffmpeg)  + download links
```

n8n can't run ffmpeg itself, so the heavy work (voiceover, footage, captions, assembly) happens in a small companion **render service** (`../server.py`). n8n calls it over HTTP. Claude, the AI director, is called straight from n8n, so you can see and edit the prompt in the **Build Claude Request** node.

## Option 1: Run everything on your own computer or server with Docker (recommended)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed.

```bash
cd video-agent/n8n
cp .env.example .env          # optional: add PEXELS_API_KEY / ELEVENLABS_API_KEY
docker compose up -d --build
```

1. Open **http://localhost:5678** and create your n8n account.
2. **Import the workflow:** go to **Workflows → ⋯ → Import from File** and choose `script-to-video.workflow.json`.
3. **Add your Claude key:** open the **AI Director (Claude)** node, then **Credential → Create new → Anthropic**. Paste your API key from console.anthropic.com and save.
4. **Publish (activate) the workflow** with the toggle at the top right.
5. Open the **Script Form** node and copy the **Production URL** (for example `http://localhost:5678/form/script-to-video`). That's your video-maker page. Bookmark it or share it with your team.

Paste a script, pick the format and press **Make my video**. After 1–5 minutes the page shows the video with download buttons.

## Option 2: Use n8n Cloud (or an n8n you already have)

The render service has to run somewhere n8n can reach over the internet, such as a small VPS, Railway, Render or Fly.io:

```bash
cd video-agent
docker build -t script2video .
docker run -d -p 8000:8000 \
  -e PUBLIC_BASE_URL=https://render.your-domain.com \
  -e RENDER_TOKEN=pick-a-long-secret \
  -e PEXELS_API_KEY=... \
  script2video
```

Then, in the imported workflow:
1. Open the **Render Video** node. Change the URL to `https://render.your-domain.com/render`.
2. Still in **Render Video**, set **Authentication → Generic → Header Auth**. Use the header name `Authorization` and the value `Bearer pick-a-long-secret`.
3. Do steps 3–5 from Option 1.

## Customising

| Want to... | Do this |
|---|---|
| Change how the AI plans scenes | Edit the `SYSTEM` prompt in **Build Claude Request** |
| Get a cheaper or faster plan | In **Build Claude Request**, change `effort: 'medium'` to `'low'` |
| Upload the video to Google Drive or YouTube, or email it | Add a node after **Render Video**: an **HTTP Request** to `{{ $json.video_url }}` with *Response Format: File* downloads the MP4, which you can then pass to Google Drive, YouTube, Gmail, Slack and so on |
| Start from Google Sheets, Airtable or a schedule instead of a form | Swap **Script Form** for that trigger and output fields named `script`, `format`, `style`, `voice`, `rewrite` and `music_url` |
| Use stock footage | Set `PEXELS_API_KEY` for the render service (free key at pexels.com/api). Without it the video uses branded colour cards |
| Use a premium voice | Set `ELEVENLABS_API_KEY` (and optionally `ELEVENLABS_VOICE_ID`) for the render service, and pick `elevenlabs` in the form |

## Render service API

`POST /render` with JSON. Either send `script` (the service plans the scenes itself), or send a ready-made `storyboard` (what the workflow does):

```json
{ "storyboard": {"title": "...", "scenes": [{"narration": "...", "on_screen_text": "...", "stock_query": "..."}]},
  "aspect": "9:16", "voice": "auto", "style": "...", "music_url": "https://..." }
```

It returns `{ "title", "duration", "scenes", "video_url", "srt_url", "storyboard" }`. Videos are served from `/files/<id>/video.mp4`.
