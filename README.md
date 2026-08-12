# CS2 Clip Generator

A local Windows application that turns a Counter-Strike 2 demo into real MP4
highlight clips:

```
demo  →  analysis  →  highlights  →  CS2 playback  →  recording  →  MP4
```

No server, no account, no cloud, no AI service. The internet is only touched if
*you* paste a demo URL and ask for it to be downloaded.

The clips are recorded from CS2 itself. The demo is the source of *data* (who
killed whom, on which tick, from whose point of view); the video comes from the
game replaying that moment with the camera locked to the right player.

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [First launch](#first-launch)
- [Importing a demo](#importing-a-demo)
- [Generating clips](#generating-clips)
- [CS2 playback: how the game is controlled](#cs2-playback-how-the-game-is-controlled)
- [Recording backends](#recording-backends)
- [Output layout](#output-layout)
- [Settings reference](#settings-reference)
- [Command line](#command-line)
- [Building the .exe](#building-the-exe)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Development](#development)

---

## What it does

* Imports a demo by **drag & drop**, file picker or **direct URL** (with
  progress, speed, ETA and a working Cancel button).
* Unpacks `.dem.bz2`, `.dem.gz` and `.dem.zip` (asking which demo to use when an
  archive holds several).
* Parses the demo: map, server, tickrate, rounds, players, SteamIDs, teams,
  kills, deaths, assists, headshots, weapons, damage/ADR, bomb plants,
  defuses and explosions.
* Detects highlights — **2K, 3K, 4K, ACE, clutches (1v1…1v5)** — and flavours
  each one with what the demo actually proves: headshot, AWP, noscope, wallbang,
  through smoke, jumping, knife, grenade, blinded attacker, long range.
* Scores every highlight with a fully configurable table, sorts, filters and
  searches them, and shows the match on a clickable timeline.
* Computes a clip window per highlight (short for a single kill, long for an
  ACE), clamps it to its round, and merges clips that overlap.
* Launches CS2, loads the demo, seeks to the tick, forces the **player's own
  first-person POV**, records the interval, and hands the result to FFmpeg.
* Writes `ACE_Round17_Player1.mp4` plus a JSON sidecar describing the clip.
* Joins clips into a montage with optional cross-fades, intro, outro and music.
* Caches analyses, survives crashes mid-render, and never shows a traceback.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11 x64 (the analysis half also runs on Linux) |
| Python | 3.11 or newer — only when running from source |
| CS2 | installed through Steam |
| FFmpeg | required, for encoding |
| OBS Studio | optional, recommended recorder |
| HLAE | optional, best recording quality |
| CS2 server plugin | optional, needed for *tick-accurate* control — see below |

---

## Installation

### From source

```bat
git clone https://github.com/<you>/cs2-clip-generator.git
cd cs2-clip-generator
run.bat
```

`run.bat` creates `.venv`, installs `requirements.txt` and starts the app. Later
runs start it immediately.

### FFmpeg

Install a build with the encoders you want (`gyan.dev` builds include NVENC,
AMF and QuickSync) and either put `ffmpeg.exe` on `PATH` or point Settings at
it. The app checks which encoders your FFmpeg really has and only offers those.

### OBS Studio (optional)

OBS 28+ already ships obs-websocket. Enable it in
*Tools → WebSocket Server Settings*, note the port and password, and enter them
in Settings → Recording. Press **Test** to confirm the connection.

### HLAE (optional)

Download HLAE, point Settings at `HLAE.exe`. HLAE starts CS2 itself and records
with `mirv_streams`, which is faster and cleaner than the engine's own frame
dump.

---

## First launch

The app looks for Steam, CS2, FFmpeg, OBS and HLAE and reports what it found:

```
✓ Steam — C:\Program Files (x86)\Steam
✓ CS2 — D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe
✓ FFmpeg — C:\ffmpeg\bin\ffmpeg.exe
⚠ OBS Studio not found — optional; enables the OBS recorder
⚠ HLAE not found — optional; best recording quality
```

Nothing is ever downloaded or installed for you. Missing tools get a button that
opens the vendor's own page in your browser.

---

## Importing a demo

Drop a file on the Demo page, press **Select Demo**, or paste a URL and press
**Download**. Before parsing, the file is checked:

```
✓ File exists
✓ File is not empty — 57.8 MB
✓ Valid CS2 demo
✓ Parser compatible — demoparser2 0.42.0
```

A CS:GO (Source 1) demo is rejected with that explanation rather than a parser
error. Analysis runs on a worker thread with real progress, and the result is
cached — re-opening the same demo is instant.

---

## Generating clips

1. **Highlights page** → choose a player (or *All players*).
2. Sort by score/round/time/kills, filter by kind or tag, or search
   (`ACE`, `AWP`, `round 17`, `headshot`).
3. **Generate** on a card, or **AUTO CLIP** for the best *N* above a minimum
   score.
4. **Render** page: the queue runs one clip at a time. CS2 opens, seeks, locks
   the POV, records; FFmpeg encodes.
5. **Montage** page (optional): tick clips, order them, add music, render one
   video.

**Preview** opens CS2 at a highlight without recording. **Edit** adjusts a
clip's start and end by hand (`12:31.500` → `12:47.200`) and can regenerate it.
**Manual clip** takes an arbitrary tick range and a player, with no detection at
all.

---

## CS2 playback: how the game is controlled

This is the part that decides whether an automated clip is frame-accurate or
approximate, so it is worth understanding.

CS:GO had **VDM files**: a text file next to the demo telling the engine to run
console commands at given ticks. **Source 2 dropped them, and nothing native
replaced them.** There is no supported way to say "run `startmovie` exactly at
tick 8100" from outside the game.

The app therefore supports three transports and picks the best one available:

| Backend | Accuracy | Needs |
|---|---|---|
| `plugin` | exact demo ticks | a CS2 server plugin that reads `<demo>.dem.json` |
| `netcon` | live console, real feedback | `-netconport` working on your CS2 build |
| `cfg` | wall clock + a simulated key press | nothing (Windows only) |

**plugin** — the community solution (used by CS Demo Manager) is a small server
plugin loaded through a `gameinfo.gi` search path, which executes commands when
the demo reaches a tick. The app writes exactly the actions file that plugin
expects, so if you already have it installed, your clips are tick-accurate. It
is *not* bundled and never downloaded: Settings can detect an existing
installation, let you select a binary you already trust, and patch
`gameinfo.gi` (with a backup) only after you confirm.

**netcon** — `-netconport` opens a TCP console; the app connects, seeks, pauses
the demo, sets the camera, starts the recorder and resumes. Because the demo is
*paused* while everything is set up, the recording still begins on the intended
tick. Whether the port opens depends on the CS2 build (some require the Workshop
Tools), so the app probes it and falls back instead of assuming.

**cfg** — no plugin, no console: the seek is bound to a hotkey and the app
presses it with `SendInput`. It works, but the CS2 window must be focused.

Whichever backend is used, CS2 is started with `-condebug` so console output is
mirrored to `console.log`, and `echo` markers in that file tell the app exactly
when playback reached the clip's first tick. That is what allows an *external*
recorder (OBS, FFmpeg) to be synchronised with in-game ticks.

### Player POV

The camera commands are always issued in this order:

```
spec_mode 1        (first person)
spec_player <slot>
```

Reversed, CS2 frequently ignores `spec_player` and leaves the camera in
free-roam. CS:GO's `spec_player_by_accountid` does not exist in CS2, so the
numeric **slot** is mandatory — and no Python demo parser exposes it. The app
reads it from the demo container itself:

```
slot = (CMsgPlayerInfo.userid & 0xff) + 1      # from the "userinfo" string table
```

(see `demo/slots.py`; it costs a few milliseconds even on a 2 GB demo).

---

## Recording backends

| Backend | How | Notes |
|---|---|---|
| **HLAE** | `mirv_streams record start/end` + FFmpeg preset | best quality; HLAE launches CS2 |
| **OBS** | obs-websocket `StartRecord`/`StopRecord` | real time; FFmpeg trims the margins |
| **CS2 startmovie** | `host_framerate` + `startmovie` → TGA + WAV → FFmpeg | no extra software; needs a lot of disk (~6 MB per 1080p frame) and the plugin, because Valve hid the command |
| **FFmpeg** | `gdigrab` window capture | always available; the CS2 window must be visible |

`auto` walks that list and uses the first one that reports itself usable.

---

## Output layout

```
CS2Clips/
└── de_mirage_match730_.../
    └── Player1/
        ├── ACE_Round17_Player1.mp4
        ├── ACE_Round17_Player1.json
        ├── 4K_Round12_Player1.mp4
        └── 4K_Round12_Player1.json
```

Each clip's sidecar makes the result reusable without re-analysing the demo:

```json
{
  "player": "Player1",
  "round": 17,
  "type": "ACE",
  "score": 145.0,
  "start_tick": 123456,
  "end_tick": 124200,
  "map": "de_mirage",
  "video": "ACE_Round17_Player1.mp4",
  "duration_seconds": 18.5,
  "tags": ["AWP", "HEADSHOT", "HEADSHOT_ONLY"]
}
```

---

## Settings reference

* **Paths** — CS2 executable, Steam folder, FFmpeg, OBS, HLAE, output folder,
  temporary folder.
* **Recording** — recorder, CS2 control backend, OBS WebSocket, display mode,
  hide HUD / kill-feed only / player voices, close CS2 when done, extra launch
  arguments, extra console commands, plugin management.
* **Clips** — multi-kill window, max clips, minimum score, merge overlapping,
  keep clips inside their round, and the before/after seconds per highlight kind.
* **Video** — preset (Fast / Balanced / Quality / Custom), resolution, FPS,
  codec (H.264 / H.265), bitrate, encoder (Auto / CPU / NVENC / AMF /
  QuickSync), game and voice audio, volume.
* **Maintenance** — open logs, open clips, clear cache, developer mode.

Settings, cache and logs live in `%LOCALAPPDATA%\CS2ClipGenerator`.

---

## Command line

The whole backend is usable without the GUI:

```bat
cs2clip doctor                          REM what is installed, what can encode
cs2clip analyze match.dem               REM match overview and scoreboard
cs2clip highlights match.dem --player "Player1" --json highlights.json
cs2clip render highlights.json --demo match.dem --max 5
cs2clip montage clip1.mp4 clip2.mp4 -o montage.mp4 --transition fade
cs2clip cache --clear
```

From source: `run.bat cli analyze match.dem`.

---

## Building the .exe

```bat
build.bat
```

Installs the dev dependencies, runs the tests, and produces
`dist\CS2ClipGenerator\CS2ClipGenerator.exe` via PyInstaller. FFmpeg is not
bundled.

---

## How it works

```
cs2_clip_generator/
├── app/main.py            GUI entry point
├── cli.py                 cs2clip command line
├── core/                  config, logging, errors, models, hardware, detection
├── demo/                  parser, slots, downloader, extractor, cache, validation
├── highlights/            multikill, clutch, scoring, timing, filters, titles, detector
├── cs2/                   launcher, actions file, netcon, plugin, console log,
│                          demo/player/camera controllers, playback controllers
├── recording/             base, hlae, obs, native, ffmpeg_capture, factory
├── render/                job, queue, pipeline
├── montage/               creator
├── video/                 ffmpeg (commands, encoder detection)
├── ui/                    theme, widgets, pages, workers, state, main window
└── utils/                 process, filesystem, timeutil
```

Four interfaces keep the moving parts replaceable: `DemoParserBackend`,
`PlaybackController`, `Recorder` and the FFmpeg command layer. A new parser or
recorder is a new class plus a registry entry.

---

## Troubleshooting

**"Counter-Strike 2 could not be found."**
Set the path in Settings → Paths. It is
`…\Counter-Strike Global Offensive\game\bin\win64\cs2.exe`, in whichever Steam
library holds the game.

**"FFmpeg was not found."**
Install FFmpeg and put it on `PATH`, or select `ffmpeg.exe` in Settings.

**"CS2 never reached the highlight."**
Playback was not driven successfully. Check Settings → Recording → *Check
availability*. With no plugin and no working `-netconport`, the app falls back to
the cfg + hotkey backend, which needs the CS2 window in the foreground.

**"CS2 did not write any frames."**
`startmovie` is hidden on current CS2 builds unless the server plugin is
enabled. Use OBS or HLAE, or enable the plugin.

**"Could not connect to OBS."**
Start OBS, enable the WebSocket server, check port and password, press **Test**.

**"That link returned a web page, not a demo."**
The URL points at a download *page*. Use the direct link to the `.dem`.

**The clip is off by a second or two.**
Expected on the `netcon` and `cfg` backends. The `plugin` backend is the
tick-accurate one.

**Everything failed and I want details.**
Settings → **Open Logs Folder**: `app.log`, `parser.log`, `recorder.log`,
`ffmpeg.log`, `cs2.log`.

---

## Known limitations

* **Tick-accurate control needs a plugin.** Source 2 has no VDM files. Without
  one, clip boundaries can drift by a second or two.
* **`-netconport` is build-dependent.** Some CS2 builds only open the console
  port with the Workshop Tools installed; the app probes rather than assumes.
* **CS:GO demos are not supported.** They are a different format; the app says so
  instead of failing obscurely.
* **Recording is real time** (OBS/FFmpeg): a 20 second clip takes 20 seconds plus
  loading. In-game recorders trade that for disk space.
* **Desktop audio needs a loopback device** for the FFmpeg capture backend
  (Windows has no default one); OBS and HLAE do not.
* **Clutch and alive-count detection is reconstructed** from kill events, so a
  mid-round disconnect can make a round's clutch go unreported (never
  misreported).
* **Nothing is invented.** If the demo does not prove an event, it is not
  claimed: no fake tags, no guessed flash assists.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                                            # logic + UI (offscreen) tests
CS2CLIP_TEST_DEMO=/path/to/match.dem pytest        # + real-demo integration tests
ruff check cs2_clip_generator
```

The suite covers demo parsing against a real CS2 demo, spectator-slot recovery,
multi-kill grouping, ACE and clutch detection, scoring, clip timing, clip
merging, file naming, the JSON actions file, launch arguments, download and
archive handling, the render queue and crash recovery, FFmpeg command building
(plus real encodes, concatenation and TGA-sequence encoding), and the UI.

## Licence

MIT.
