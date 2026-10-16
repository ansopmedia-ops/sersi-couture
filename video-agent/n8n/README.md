# Script → Video in n8n

This is an n8n workflow that turns a script you paste into a form into a finished, narrated video:

```
[Script Form] → [Build Claude Request] → [AI Director (Claude)] → [Parse Storyboard] → [Render Video] → [Show Video]
  n8n form        builds the prompt         plans the scenes          checks the JSON        voice + visuals      page with player
                                                                                             + captions (ffmpeg)  + download links
```

n8n can't run ffmpeg itself, so the heavy work (voiceover, footage, captions, assembly) happens in a small companion **render service** (`../server.py`). n8n calls it over HTTP. Claude, the AI director, is called straight from n8n, so you can see and edit the prompt in the **Build Claude Request** node.

## Second workflow: Prompt → AI Video

`prompt-to-ai-video.workflow.json` makes the **footage itself** with an AI video model, starting from one prompt such as "a baby explores a magical mossy forest...":

```
[Video Idea Form] → [Build Claude Request] → [AI Director (Claude)] → [Parse Shot Plan] → [Start Video Job]
                                                                                              ↓
                       [Show Video] ← yes ← [Finished?] ← [Check Progress] ← [Wait 20s] ←──┐
                       [Show Error] ← yes ← [Failed?] ─── no ─────────────────────────────┘
```

- **Claude** splits the idea into shots. It writes one fixed description of the main character and one of the look, and repeats both in every shot so the subject stays consistent.
- **Continuous long take** (Google Veo): an 8-second first clip, then 7-second *extensions* of that same clip. You get one unbroken shot of up to about 2 minutes.
- **Separate shots** (Veo or any fal.ai model): 8-second clips joined with soft crossfades.
- n8n checks progress every 20 seconds and shows the video when it's finished. A 40-second video usually takes 10–30 minutes.

Setup is the same as below. Import the second file too and add the same Anthropic credential. Then put **one** of these in `.env` and run the start file again:

| Key | Get it at | Notes |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com/apikey | Google Veo 3.1; enables continuous takes. Billing must be enabled |
| `FAL_KEY` | fal.ai/dashboard/keys | One key for many models; choose one with `FAL_MODEL` (default `fal-ai/veo3.1`) |

AI video generation is **paid per second of video**, and a 40-second video costs real money (check the provider's current prices). Choose **mock** as the AI video model in the form to test the whole workflow for free with placeholder clips.

In the EU, UK or Switzerland, Google only allows videos of adults, so set `VEO_PERSON_GENERATION=allow_adult`. Prompts with children may be refused there.

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
