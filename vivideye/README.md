<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="docs/promo_hero.jpg" alt="VividEye — turn an old Android phone into an AI highlight camera" width="800">
</p>

<h1 align="center">VividEye</h1>

<p align="center"><strong>Always watching. Only the highlights.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/made_with-%F0%9F%92%9B-yellow" alt="Made with 💛">
</p>

**VividEye** turns an old Android phone (developed and tested on a Huawei P20) into an AI highlight camera for pet and family homes. It records 24/7, sends only sampled frames to a cloud VLM/LLM for scoring, keeps the moments worth keeping, and lets you browse everything from any device on your home WiFi — while every video file stays on the phone itself. Top-scoring moments are even auto-synthesized into **"bullet time"** rotating clips: park a few old phones around the room as a camera array and replay the jump from every angle.

<p align="center">
  <video src="docs/promo_vivideye.mp4" controls muted playsinline width="800" poster="docs/promo_hero.jpg"></video>
</p>
<p align="center"><sub>🎬 Video not playing? Open <a href="docs/promo_vivideye.mp4">docs/promo_vivideye.mp4</a> directly.</sub></p>

---

## 🧭 How It Works

```
┌──────────────────── Huawei P20 · Termux ────────────────────┐
│                                                              │
│  IP Webcam app ─▶ ffmpeg ─▶ 10-min segments ─▶ frame        │
│                   slicing     rolling buffer    sampling   │
│                   (loopback      (24 h)         (0.5 fps)   │
│                    MJPEG :8080)                              │
└──────────────────────────────┬───────────────────────────────┘
                               │  only sampled frames
                               │  leave the house
                               ▼
                  ☁️  Cloud VLM / LLM (DashScope · Qwen)
                      score · title · tags · moments
                               │
┌──────────────────────────────┴───────────────────────────────┐
│  Highlights library (mp4 + thumbnails + SQLite)               │
│  Daily digest (Markdown, AI-written with local fallback)     │
│  Bullet-time clips (rotating view, auto at score ≥ 0.75)     │
│                              │                               │
│                   FastAPI Web UI (:8666)                     │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
              📱 💻 📺  any device on home WiFi
                 http://<phone-ip>:8666
```

**The phone is the eye, the cloud is the brain, and any device at home is the remote.** The phone records itself and serves the web UI; the cloud only ever sees a handful of low-fps sampled frames per segment — never your full video.

## 🐾 Who Is This For / What You Need

If you have pets or kids at home, an old Android phone in a drawer, and you'd rather watch 3 minutes of highlights than 24 hours of footage — this is for you.

| What | Notes |
|---|---|
| An old Android phone | Android 7+; developed and tested on **Huawei P20** (P20 Pro works too) |
| Home WiFi | Phone and viewing devices on the same network |
| Constant power | 24/7 recording means the phone stays plugged in |
| A computer | **Only for the one-time deployment** — Linux, Git Bash on Windows, or WSL |
| A DashScope API key | Free to register; see the [API Key guide](#-api-key-guide) below |

## 🔓 No Root. No Bootloader Unlock. No Custom ROM.

**VividEye needs no rooting, no unlocking, and no flashing.** It runs entirely inside [Termux](https://termux.dev/) plus the [IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam) app. Your warranty, Huawei Pay/NFC, and OTA updates all stay intact, and you can uninstall everything to get the phone back to stock.

If you *do* want to deep-clean EMUI (optional, recommended for long-term duty) — debloat, battery whitelist, auto-update off — the full honest guide including what can safely be removed is in [`deploy/README-FLASH.md`](deploy/README-FLASH.md).

## 🚀 Quick Start

The complete, beginner-friendly guide (data backup, EMUI cleanup, every pitfall) lives in [`deploy/README-FLASH.md`](deploy/README-FLASH.md). The short version:

```bash
# 1) Put two APKs in deploy/apks/ (see deploy/apks/README.md):
#    termux.apk    — MUST be the F-Droid or GitHub build (Play version is abandoned)
#    ipwebcam.apk  — or install "IP Webcam" from the phone's app store
cd deploy
make deploy          # installs APKs, pushes the project, triggers on-phone bootstrap
make doctor          # post-deploy health check
```

Then on the phone: open **IP Webcam → Start video server**, and in Termux run `cd ~/vivideye && source .venv/bin/activate && python main.py start`. Open `http://<phone-ip>:8666` on any device in the house, paste your API key in **Settings**, and you're live.

**Honest timetable** (your mileage may vary):

| Step | Time |
|---|---|
| Optional EMUI cleanup & backup | ~20 min |
| `make deploy` + on-phone bootstrap | ~15 min |
| First configuration (API key, scene mode) | ~15 min |
| First highlight appears | ≤ 30 min after that |

## 🔑 API Key Guide

VividEye's "brain" is any OpenAI-compatible API — defaults to Alibaba Cloud DashScope (Qwen family).

1. Register an Alibaba Cloud account and open [Model Studio (Bailian)](https://bailian.console.aliyun.com/).
2. Activate the DashScope service (new accounts usually get a free trial quota).
3. Create an API key in the console.
4. Paste it into the **Settings** page of the VividEye web UI (stored only in `user_config.yaml` on the phone; alternatively `export VIVIDEYE_AI__API_KEY=sk-xxx`).

**Cost estimate:** with default settings (`sample_fps: 0.5`, batches of 8 segments), typical home usage runs **a few mao to a few yuan per day** (roughly $0.05–$0.50). Lower `pipeline.sample_fps` or `max_segments_per_run` if you want to spend less.

**No key yet?** Everything still runs — the recorder keeps 24/7 footage and the web UI works, but no highlights or AI analysis are produced until a key is set (segments simply stay pending; the daily digest falls back to a local template). Add the key later and the backlog gets processed.

## 📅 Daily Use

Open `http://<phone-ip>:8666` from your phone, laptop, or TV browser:

- **✨ Highlights wall** — cards with thumbnail, AI-written title, score, and tags; tap to play, ❤️ favorite, or 🗑️ delete
- **⚡ Bullet time** — highlights scored ≥ 0.75 automatically get a rotating-view clip; look for the ⚡ badge on the card and the "Play bullet time" button in the player (see [🎬 Bullet Time](#-bullet-time))
- **❤️ Favorites** — favorited highlights are never auto-deleted
- **📹 Live view** — the current camera frame, proxied from the phone
- **📖 Daily digest** — a short AI-written story of the day's best moments
- **⚙️ Settings** — scene mode (auto / pet / kid / home), API key, bullet-time switch & threshold, and a **"Process now"** button that skips the 30-minute scheduler and analyzes the newest segments immediately

## 🎬 Bullet Time

<p align="center">
  <img src="docs/bullettime_hero.jpg" alt="Bullet time — a rotating-view clip synthesized around the best moment" width="800">
</p>

When the AI scores a segment **≥ 0.75**, VividEye automatically synthesizes a short **rotating-view "bullet time" clip** — an NBA-style freeze-frame sweep — around the longest AI-identified moment and attaches it to that highlight. Nothing to click, nothing to configure: the card gets a ⚡ badge and the player gets a **⚡ Play bullet time** toggle (press again to switch back to the original clip).

**Trigger conditions (all must hold):**

- `bullet_time.enabled` is on (default, switchable on the Settings page), **and**
- the segment's AI score ≥ `bullet_time.min_score` (default `0.75`, slider on the Settings page), **and**
- the AI returned at least one concrete `moments` window — the longest one is chosen as the rotation center.

**What you get (honest version):**

| Setup | Effect |
|---|---|
| **Multi-camera** — N old phones parked around the room (`capture.cameras`) | A true multi-angle sweep: the clip rotates across the different phones' viewpoints around the frozen moment |
| **Single camera** (default) | A *virtual camera* sweep: the rotation is synthesized from digital zoom & pan on the one real angle. Looks great, but it is **not** a true 3D reconstruction — we'd rather say so plainly |

**Multi-camera example** (`user_config.yaml`) — each extra phone runs Termux + IP Webcam just like the main one; its segments land in `data/raw/<name>/`:

```yaml
capture:
  cameras:
    - name: sofa-north        # any name, used for the segment folder and logs
      url: http://192.168.1.23:8080/video    # that phone's IP Webcam stream
      audio_url: null         # optional separate audio stream
    - name: sofa-east
      url: http://192.168.1.24:8080/video
      audio_url: http://192.168.1.24:8080/audio.wav
```

**`bullet_time` settings:**

| Key | Default | Meaning |
|---|---|---|
| `bullet_time.enabled` | `true` | Master switch (also on the Settings page) |
| `bullet_time.min_score` | `0.75` | Minimum AI score to trigger synthesis (Settings-page slider) |
| `bullet_time.duration_seconds` | `4` | Length of the synthesized clip |
| `bullet_time.style` | `pingpong` | Angle-sequence style: `pingpong` / `rotate` |
| `bullet_time.virtual_mode` | `auto` | Single-camera fallback: `auto` / `real` / `virtual` |
| `bullet_time.virtual_angles` | `8` | Number of virtual viewpoints in single-camera mode |
| `bullet_time.zoom_motion` | `true` | Slow zoom within each angle (zoompan; slightly more CPU) |

> Multi-camera bullet time is storage- and WiFi-hungry — every phone records its own angle 24/7. On a single P20, the virtual mode costs almost nothing extra.

## 🔒 Privacy & Security

- **Video never leaves your home.** All raw segments and highlight clips are stored on the phone; the cloud only receives sampled frames (0.5 fps by default) and returns scores/text.
- **No cloud upload of footage, no telemetry, no accounts.**
- **Honest caveat:** the web UI has **no authentication** — anyone on the same WiFi can view highlights and settings. That's a deliberate simplicity trade-off for a home tool: use it on your own trusted home network, not on public/shared WiFi. If you need remote access, tunnel through a VPN (e.g., Tailscale/WireGuard) instead of port-forwarding.

## 💾 Storage & Maintenance

| Setting | Default | Meaning |
|---|---|---|
| `capture.retention_hours` | 24 h | Raw segments older than this are deleted (rolling buffer) |
| `storage.highlights_retention_days` | 30 days | Non-favorite highlights older than this are deleted; favorites kept forever |
| `storage.min_free_gb` | 2 GB | Recording pauses when free space drops below this, resumes automatically |

**Capacity math (640×480 MJPEG ≈ 1–3 GB/h):**

| Resolution | Per hour | 24 h rolling buffer |
|---|---|---|
| 640×480 (recommended) | ~1–3 GB | ~8–30 GB |
| 1280×720 | ~2–5 GB | ~16–50 GB — still heavy for a P20 |

A P20 has 64–128 GB total storage, so the default keeps only 24 h of raw footage (storage safety first). Raise `capture.retention_hours` in `user_config.yaml` only if you have space to spare, or drop the resolution in the IP Webcam app. Highlights themselves are tiny (seconds-long clips). `python main.py status` shows disk level and pending segments at any time.

## 🔁 After a Reboot

Honest answer: the phone won't bring everything back by itself yet.

1. Open **IP Webcam** → tap **Start video server**.
2. Open **Termux** → `cd ~/vivideye && source .venv/bin/activate && python main.py start`.

Advanced: install the [Termux:Boot](https://github.com/termux/termux-boot) companion app and add a startup script that runs the command above automatically at boot. Also re-check the EMUI battery whitelist after major system updates — EMUI loves silently killing background apps.

## 🩺 Troubleshooting

Start with `make doctor` (in `deploy/`) — it checks device connection, both APKs, the Termux process, and the 8666/8080 services, with fix hints for each failure.

| Symptom | Fix |
|---|---|
| Recording dies after a few hours | EMUI killing background apps — redo the battery whitelist steps in [`deploy/README-FLASH.md`](deploy/README-FLASH.md) §2A-5, lock both apps in Recents |
| `8666` unreachable | Termux service not running — reopen Termux, `python main.py start` |
| `8080` unreachable | IP Webcam not started — open the app, tap "Start video server" |
| No highlights after hours | Check the chain: IP Webcam running → Termux running → API key saved → press **Process now** in Settings and watch the result; also verify `pipeline.min_highlight_score` isn't set too high |
| `adb devices` shows `unauthorized`/`offline` | Re-accept the debugging prompt / enable "Allow ADB debug in charge-only mode" (a Huawei quirk) |
| `pkg`/`pip` downloads crawl or fail | Run `termux-change-repo` and pick a nearby mirror |

## ⚙️ Configuration Reference

Copy [`config_template.yaml`](config_template.yaml) to `user_config.yaml` (git-ignored) and edit — or change most of it from the web UI's Settings page. Env vars work too: `VIVIDEYE_AI__API_KEY` → `ai.api_key` (`__` = nesting).

| Key | Default | Meaning |
|---|---|---|
| `capture.source_url` | `http://127.0.0.1:8080/video` | Loopback MJPEG stream from the camera app |
| `capture.segment_seconds` | 600 | Length of each raw segment |
| `capture.retention_hours` | 24 | Rolling buffer window for raw footage |
| `capture.cameras` | `[]` | Multi-camera array: one `{name, url, audio_url}` entry per extra phone; empty = single camera (see [🎬 Bullet Time](#-bullet-time)) |
| `pipeline.run_interval_minutes` | 30 | How often new segments get analyzed |
| `pipeline.max_segments_per_run` | 8 | Batch size per run (phone- and wallet-friendly) |
| `pipeline.min_highlight_score` | 0.55 | Minimum AI score to save a highlight |
| `pipeline.scene_mode` | auto | `auto` / `pet` / `kid` / `home` — tunes recognition |
| `pipeline.sample_fps` | 0.5 | Frames per second sent to the cloud (cost knob) |
| `ai.provider` | dashscope | `dashscope` / `openai` / `compatible` (any OpenAI-style API) |
| `ai.api_key` | — | Your key; or `VIVIDEYE_AI__API_KEY` |
| `ai.vision_model` | qwen3-vl-flash | VLM doing the frame scoring |
| `bullet_time.enabled` | `true` | Auto-synthesize bullet-time clips for top highlights (Settings page) |
| `bullet_time.min_score` | 0.75 | Minimum AI score to trigger bullet time (Settings page) |
| `bullet_time.duration_seconds` | 4 | Bullet-time clip length (seconds) |
| `bullet_time.style` | `pingpong` | Angle-sequence style: `pingpong` / `rotate` |
| `bullet_time.virtual_mode` | `auto` | Single-camera fallback: `auto` / `real` / `virtual` |
| `bullet_time.virtual_angles` | 8 | Number of virtual viewpoints (single camera) |
| `bullet_time.zoom_motion` | `true` | Slow zoom within each angle (zoompan) |
| `storage.highlights_retention_days` | 30 | Non-favorite highlight lifetime (favorites forever) |
| `storage.min_free_gb` | 2 | Free-space floor before recording pauses |
| `server.port` | 8666 | Web UI port |

Full defaults live in [`vivideye/config.py`](vivideye/config.py).

## 🗺️ Roadmap

**✅ Shipped**

- 🎥 Multi-camera support (v0.2.0) — several old phones form one camera array (`capture.cameras`); pair it with [🎬 Bullet Time](#-bullet-time) for rotating-view highlights

**Planned**

- 🕹️ Multi-view "bullet time", next level: distributed compute/storage across the devices and true 3D freeze-frame reconstruction of a highlight moment
- 🧠 Local small models (ONNX) for fully offline inference on stronger phones
- 📱 A thin app shell for the web UI
- 🌍 Secure remote access recipe (Tailscale/WireGuard guide)
- 🎨 More AI fun: auto-generated posters, background music, weekly recap videos

## 🤝 Contributing & Acknowledgements

Issues and PRs are welcome — this is a hobby project maintained with love, so please be patient.

- **[VividMU](https://github.com/AllenMa97)** — the pipeline this project was derived from
- [Termux](https://termux.dev/) — a full Linux environment, no root needed
- [IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam) by Pavel Khlebovich — the eye
- [FastAPI](https://fastapi.tiangolo.com/), [ffmpeg](https://ffmpeg.org/), and the Qwen model family on DashScope

## 💛 Author & Sponsor

**AllenMa** · 851132789@qq.com

If VividEye gave an old phone in your home a second life, consider buying me a coffee — it helps more old phones come back to life.

- WeChat Pay / Alipay: **13760777424**

Investors interested in this project are welcome to reach out by email.

## 📄 License

[MIT](LICENSE) © 2026 AllenMa
