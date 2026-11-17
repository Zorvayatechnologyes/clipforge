# ClipForge

Paste a YouTube stream link, get back the funny / interesting moments as
vertical clips with burned-in captions.

It ships as **a website** — a static page you can host anywhere — plus **an
engine**, a small server that does the actual work. Browsers cannot run ffmpeg
or Whisper, so the two parts talk over HTTP. Run the engine on your laptop and
you have a private site at `http://localhost:8000`; put it on a host and the
page works from your phone.

Everything runs on open-source models. No API keys, no uploads to a third party.

```
YouTube URL  ->  yt-dlp  ->  faster-whisper  ->  audio + cue-word scoring
             ->  optional local LLM (Ollama)  ->  ffmpeg vertical render  ->  clips
```

---

## Use it as a website

Two pieces, deployed separately:

| piece | what it is | where it goes |
|---|---|---|
| **the page** | one static HTML file (`web/`) | Netlify, Vercel, Cloudflare Pages, GitHub Pages, any bucket |
| **the engine** | the Docker image in this repo | Render, Railway, Fly.io, a VPS, or your own laptop |

**Fastest path (5 minutes, all local).** You do not need any hosting at all:

```bash
bash run.sh          # creates a venv, installs, starts the engine
# open http://127.0.0.1:8000 — page and engine on one address
```

**Why the engine is not hosted on the internet.** YouTube refuses to serve
videos to datacenter IPs — it answers "Sign in to confirm you're not a bot" and
returns no video formats at all. This is not a guess: the same block was
measured from a GitHub Actions runner across six player clients and two videos,
12 out of 12 attempts returning zero formats. Render, Railway, Fly.io and any
VPS sit on the same kind of IP, so a cloud-hosted engine fails at the download
step no matter how it is configured. Running the engine on your own machine —
and sharing it through the tunnel script — is the architecture that actually
works. If you must host it, pass cookies with
`CLIPFORGE_COOKIES_FROM_BROWSER`, though they expire.

**Hosted path, with cookies.** A hosted engine can work for YouTube, but only
if it presents cookies from a logged-in browser — that is what gets it past the
datacenter-IP check:

```bash
# on your own machine, once (needs the browser you use for YouTube)
yt-dlp --cookies-from-browser chrome --cookies cookies.txt \
       --skip-download "https://www.youtube.com/watch?v=jNQXAC9Kp3s"
```

Copy `cookies.txt` onto the server's data disk (Render: the `/data` mount) and
set `CLIPFORGE_COOKIES_FILE=/data/cookies.txt`. The job then downloads normally
from the cloud. Cookies expire after a few weeks to months, so you will need to
refresh the file; when it goes stale every job fails with a clear message rather
than silently returning nothing. Treat the file like a password — it is access
to your YouTube account — and never commit it.

1. **Engine → Render.** Fork/push this repo to GitHub, then on
   [render.com](https://render.com) choose *New → Blueprint* and point it at the
   repo. `render.yaml` is already configured. Wait for
   `https://<your-service>.onrender.com/api/health` to return JSON.
   *Railway:* `railway.json` works the same way. *Fly.io:* `fly launch && fly deploy`.
   *VPS:* `docker compose up -d --build`.
2. **Page → Netlify.** Drag the `web/` folder onto
   [app.netlify.com/drop](https://app.netlify.com/drop). You get a URL in seconds.
   `web/netlify.toml` already sets headers.
3. **Connect them.** Open your new site, paste the engine URL into the **Engine**
   box at the top, press **Connect**. It is remembered in the browser, so you do
   this once per device.

Two hosting gotchas worth knowing:

- **Do not use a free tier for the engine.** ffmpeg and Whisper need real CPU and
  at least 2 GB RAM; a 512 MB free instance will fall over mid-render. The
  configs in this repo assume a paid-but-small instance and a persistent disk.
- **Mixed content.** A page on `https://` cannot call an engine on plain
  `http://`, *except* `http://localhost`, which browsers allow. So a hosted page
  can talk to an engine on your own machine, but a hosted engine must be served
  over HTTPS.

There is no authentication on the engine. Anyone who can reach the URL can spend
your CPU. Put it behind a proxy with auth before sharing the link, or leave it on
localhost.

## Why it finds the right bits

Ranking runs in two layers, and each one works without the other.

**Layer 1 — signals (always on, no model).** Every candidate window is scored on
loudness, dynamic range, the rate of sharp audio bursts (laughter and shouting
look like rapid amplitude spikes), onset flux, plus how many cue words the
transcript contains — `hahaha`, `no way`, `clip that`, `bruh`, `i'm dead`,
`watch this` and about fifty more, weighted by how strongly they signal a
payoff. Speech rate and dead-air coverage stop it picking silent stretches.

**Layer 2 — a local LLM (optional, much better).** If [Ollama](https://ollama.com)
is running, ClipForge takes only the most promising ~25 minutes of transcript
(never the whole 3-hour stream) and asks a small model which moments would work
as standalone clips. It returns titles, reasons and a 0–10 score. Those windows
then compete with the audio-scored ones and win when they overlap. On a 3-hour
VOD this adds two or three minutes, not an hour.

If Ollama is not running the app still works and says so in the job warnings.

## Where the clips come from

By default the picks are **spread across the whole video**: a three-hour stream
gives you moments from across the run, not six clips from the same five loud
minutes. Turn it off with `--no-spread` or `CLIPFORGE_SPREAD=false` if you would
rather have the absolute best-scoring moments regardless of where they sit.

## What a clip looks like

- 1080×1920 vertical, source centred, with a blurred, darkened copy of the frame
  filling the space above and below (nothing is cropped away).
- Bold outlined captions burned in from word-level timings, 3–4 words per card,
  placed above the Shorts UI safe area.
- Loudness normalised to −16 LUFS so quiet streamers and loud ones match.
- A `.srt` sidecar and a `.jpg` poster per clip, plus `clips.json` with every
  score and reason.

Untick *Vertical* for the original framing.

---

## Install

**No terminal needed.** Download and unzip the project, then double-click:

| your machine | double-click |
|---|---|
| Windows | `Start ClipForge.bat` |
| macOS | `Start ClipForge.command` |
| Linux | run `bash run.sh` |

Want to use it from your phone or another computer? Double-click
**`Share ClipForge`** instead. It does the same thing and then opens a free
Cloudflare tunnel, printing a public `https://….trycloudflare.com` address you
paste into the Engine box on the site. The address changes each time and only
lives while that window is open.

It creates its own Python environment, installs what it needs, and opens
`http://127.0.0.1:8000` in your browser. The first run takes a few minutes;
after that it starts in seconds. Keep the window open while you use it.

The manual route, if you prefer it:

You need Python 3.10+ and **ffmpeg** on your PATH.

```bash
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt install ffmpeg
# Windows
winget install Gyan.FFmpeg
```

Then:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

No system ffmpeg? `pip install imageio-ffmpeg` provides a bundled binary and
ClipForge finds it automatically.

## Quickstart

Website:

```bash
python -m clipforge serve        # http://127.0.0.1:8000
```

Command line:

```bash
python -m clipforge "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
python -m clipforge "https://youtu.be/xxxx" -n 8 --min 15 --max 45
python -m clipforge ./local-recording.mp4 --horizontal --no-subs
```

Clips land in `data/clips/<job-id>/`. The first run downloads the Whisper model
(`small` ≈ 480 MB) once, then it is cached.

## Making it smarter with a local LLM (optional but worth it)

```bash
ollama pull qwen2.5:7b-instruct
ollama serve
```

ClipForge detects it on `http://127.0.0.1:11434` and switches on automatically.

| model | pull | notes |
|---|---|---|
| `qwen2.5:7b-instruct` | `ollama pull qwen2.5:7b-instruct` | best quality/speed default |
| `llama3.1:8b` | `ollama pull llama3.1:8b` | strong reasoning, slightly slower |
| `qwen2.5:3b-instruct` | `ollama pull qwen2.5:3b-instruct` | fast on a laptop CPU |

If the engine runs in a container, point it at your host:
`CLIPFORGE_OLLAMA_URL=http://host.docker.internal:11434`.

---

## Options

Environment variables (or a `.env` file, see `.env.example`):

| variable | default | meaning |
|---|---|---|
| `CLIPFORGE_WHISPER_MODEL` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `CLIPFORGE_WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `CLIPFORGE_WHISPER_COMPUTE` | `int8` | `int8`, `float16`, `int8_float16` |
| `CLIPFORGE_LANGUAGE` | auto | force a language, e.g. `en`, `hi` |
| `CLIPFORGE_MIN_CLIP` / `MAX_CLIP` | `18` / `55` | clip length bounds, seconds |
| `CLIPFORGE_MAX_CLIPS` | `6` | how many clips per job |
| `CLIPFORGE_SPREAD` | `true` | spread clips across the whole video instead of clustering them |
| `CLIPFORGE_VERTICAL` | `true` | 9:16 reframe |
| `CLIPFORGE_SUBTITLES` | `true` | burn captions |
| `CLIPFORGE_SUB_FONT` | `DejaVu Sans` | caption font family |
| `CLIPFORGE_USE_LLM` | `true` | allow the Ollama pass |
| `CLIPFORGE_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `CLIPFORGE_OLLAMA_MODEL` | `qwen2.5:7b-instruct` | model to ask |
| `CLIPFORGE_ALLOWED_ORIGINS` | `*` | comma-separated origins allowed by CORS |
| `CLIPFORGE_MAX_SOURCE_HEIGHT` | `1080` | download resolution cap |
| `CLIPFORGE_COOKIES_FROM_BROWSER` | — | `chrome`, `firefox`, `edge`… for age-gated videos |
| `CLIPFORGE_COOKIES_FILE` | — | Netscape `cookies.txt`; required for a cloud-hosted engine |
| `CLIPFORGE_WORK` / `CLIPFORGE_OUTPUT` | `data/work` / `data/clips` | folders |
| `CLIPFORGE_KEEP_SOURCE` | `false` | keep the downloaded video |
| `CLIPFORGE_FFMPEG` | auto | explicit ffmpeg path |
| `CLIPFORGE_HOST` / `CLIPFORGE_PORT` | `127.0.0.1` / `8000` | where `serve` binds |

Lock the page down to one origin once it is deployed:

```bash
CLIPFORGE_ALLOWED_ORIGINS=https://your-site.netlify.app
```

CLI flags: `-n/--clips`, `--min`, `--max`, `--horizontal`, `--no-subs`,
`--no-llm`, `--no-spread`, `--model`, `--language`, `-o/--out`, `--json`.

## HTTP API

| method | path | purpose |
|---|---|---|
| `GET` | `/api/health` | ffmpeg / whisper / yt-dlp / Ollama status |
| `POST` | `/api/jobs` | `{"url": "...", "max_clips": 6, "vertical": true}` → job |
| `GET` | `/api/jobs/{id}` | status, progress, clips |
| `GET` | `/api/jobs/{id}/archive` | zip of every clip + srt + manifest |
| `GET` | `/media/{id}/{file}` | the rendered file |

```bash
curl -X POST localhost:8000/api/jobs -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/xxxx","max_clips":4}'
curl localhost:8000/api/jobs/<id>
```

## Deploying

```bash
docker compose up -d --build     # engine on http://localhost:8000
```

| target | file | notes |
|---|---|---|
| Render | `render.yaml` | Blueprint deploy, persistent disk included |
| Railway | `railway.json` | Dockerfile builder |
| Fly.io | `fly.toml` | `fly launch --no-deploy && fly deploy` |
| Netlify / Vercel / CF Pages | `web/netlify.toml` | static page only |

A 3-hour stream on 2 vCPUs is roughly 20–40 minutes of work. For a GPU host set
`CLIPFORGE_WHISPER_DEVICE=cuda` and `CLIPFORGE_WHISPER_COMPUTE=float16` —
transcription gets 10–20× faster.

## Layout

```
clipforge/
├── clipforge/            the engine (Python)
│   ├── config.py         settings from env
│   ├── ffmpeg.py         binary discovery, probing, audio extraction
│   ├── download.py       yt-dlp wrapper
│   ├── transcribe.py     faster-whisper, word timings
│   ├── audio.py          loudness / burst / flux envelope
│   ├── highlight.py      candidate windows, scoring, Ollama pass
│   ├── subtitles.py      ASS + SRT
│   ├── render.py         the vertical ffmpeg graph
│   ├── pipeline.py       the whole job
│   ├── jobs.py           queue with progress
│   ├── api.py            FastAPI + CORS
│   └── cli.py            command line
├── web/                  the website (static, deployable on its own)
│   ├── index.html        the whole app, one file
│   ├── netlify.toml      headers + publish config
│   ├── 404.html
│   └── robots.txt
├── tests/                scoring, API, and a synthetic end-to-end render
├── Start ClipForge.bat   double-click launcher (Windows)
├── Start ClipForge.command  double-click launcher (macOS)
├── Share ClipForge.bat   launcher + public tunnel (Windows)
├── Share ClipForge.command  launcher + public tunnel (macOS)
├── Dockerfile            the engine image
├── render.yaml / fly.toml / railway.json
└── requirements.txt
```

## Troubleshooting

**The page says "engine isn't connected".** The engine is not running, or it is
at a different address. Start it (`bash run.sh`) and press *Connect*. If the page
is on `https://` and the engine on plain `http://` somewhere other than
localhost, the browser blocks the request — give the engine HTTPS.

**"Sign in to confirm you're not a bot".** YouTube rate-limiting your IP. Pass
cookies: `CLIPFORGE_COOKIES_FROM_BROWSER=chrome` (log in to YouTube in that
browser first), or update yt-dlp: `pip install -U yt-dlp`.

**"this looks like a stream that is still live".** yt-dlp cannot pull a VOD from
a stream that has not ended. Wait for the VOD, or record it yourself and pass the
local file.

**Captions render as boxes.** The caption font is missing. Install DejaVu
(`apt install fonts-dejavu`) or set `CLIPFORGE_SUB_FONT` to a font you have.

**Clips are boring / miss the obvious joke.** Raise the clip count to see more
candidates, use a bigger Whisper model (better transcript = better cue-word
hits), and turn on Ollama. The audio-only ranker is good at finding *loud*, not
always at finding *funny*; the LLM pass is what fixes that.

**It is slow.** Transcription dominates. `tiny`/`base` are 3–6× faster than
`small`. Cached transcripts are reused when you re-run the same job id.

## Tests

```bash
python -m pytest tests/ -q          # 36 tests, ~4 min (renders real clips)
python -m pytest tests/test_scoring.py tests/test_api.py -q   # ~5 s
python tests/make_sample.py         # synthetic video, no network needed
```

The sample script builds a fake 4-minute "stream" with varying loudness, so the
whole pipeline can be exercised without downloading anything.

## Legal

Downloading and re-uploading someone else's stream can breach YouTube's Terms of
Service and the creator's copyright. Use this on your own streams, on material
you have permission to reuse, or for private editing. ClipForge does not bypass
DRM and does not fetch anything you could not watch in a browser.
