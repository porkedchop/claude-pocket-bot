# Briefbot for the Cardputer-Adv — Implementation Plan

> **Goal.** Voice-driven prospect lookup on an M5Stack Cardputer-Adv at an Anthropic event. The user holds a key, asks "what's the employee size of Applied Intuition", releases. The device shows aggregated firmographics + talking points pulled from Revivn's internal briefbot. Keyboard input is the fallback for when the room is too loud.
>
> **Author.** Drafted from a Phase 0 research pass over `build-with-claude` (cloned at `$BUNDLE/`), the M5Stack/UIFlow docs, and the Anthropic + OpenAI + Deepgram API surfaces. Every concrete claim below is citeable to a file, line, or URL — see the "Allowed APIs" tables.

---

## Architecture at a glance

```
┌──────────────┐    ① TCP audio stream (PCM 16-bit, 16 kHz mono, 1–4 KB chunks)
│  Cardputer   │ ────────────────────────────────────────────►  ┌──────────────────┐
│  briefbot.py │                                                │ Laptop companion │
│              │    ② JSON line response (query + brief)        │  briefbot_host.py│
│              │ ◄────────────────────────────────────────────  │                  │
└──────────────┘                                                └──────────────────┘
       ▲ I2S mic (ES8311), QWERTY matrix, 240×135 LCD                   │
       │                                                                ├─► OpenAI Whisper (transcribe)
       └─ pre-loaded fixtures (offline fallback)                        ├─► briefbot internal API (lookup)
                                                                        └─► Claude API (summarize for tiny screen) [optional]
```

- **Network path = WiFi.** Both ends are on event WiFi (`cardputer` / `cardconnect`, auto-connected at boot per `wifi_event.py`).
- **Voice path requires the companion laptop.** ESP32-S3 can't transcribe locally, and Anthropic's Messages API does not accept audio content blocks, so transcription happens on the laptop. The laptop holds all credentials (briefbot token, OpenAI key, optional Anthropic key) — a lost device leaks nothing.
- **The lookup function is the demarcation point.** `briefbot_api.lookup(query: str) -> dict` is the only thing the device app talks to. Behind it: in Phase 1, fixtures; in Phase 3, real WiFi+Whisper+briefbot. The app itself doesn't change between phases.

---

## Decisions locked in (with rationale)

| # | Decision | Why |
|---|---|---|
| 1 | **Companion laptop holds keys** | Audio transcription requires it anyway; BLE is too slow for audio (~1–3 KB/s vs the 32 KB/s we need); a takeaway demo device with a customer-data API token in flash is a rotation event waiting to happen. |
| 2 | **Push-to-talk, not wake word** | Real wake-word detection on ESP32-S3 needs a dedicated DSP path we don't have. Press-and-hold is what Siri's side-button trigger actually is anyway. Bind it to **space** on the matrix keyboard (large, central, easy to find by feel). |
| 3 | **Stream audio, don't buffer** | At 16 kHz mono 16-bit = 32 KB/s, with no confirmed PSRAM on this Cardputer-Adv, anything past ~3 s exhausts the heap. `machine.I2S(...).readinto(small_buf)` in 2 KB chunks → `socket.write()` is the canonical pattern. |
| 4 | **Whisper for V1, Deepgram for V3** | Whisper API (`whisper-1`) is a single multipart POST and costs $0.006/min; latency ~1–2 s for a 3 s clip. Deepgram streaming gives partial transcripts that could render mid-hold for a better demo feel — Phase 4 polish, not V1 plumbing. |
| 5 | **Hybrid input UX** | List-pick on arrow keys for known prospects (today's meetings) + voice for free-form. Voice is the demo; the list is the safety net when the venue is loud. Free-text typing dropped — voice is faster and the demo is shorter. |
| 6 | **TCP, not BLE, between Cardputer and laptop** | Audio bandwidth requirements rule out BLE. The Claude Buddy BLE pattern in this repo is great for permission-prompt-style messages but won't carry voice. |
| 7 | **Hardcode laptop IP via a config file pushed to /flash/briefbot_config.py** | mDNS service discovery is not in core MicroPython on UIFlow 2.0; the cleanest approach for a one-off demo is a one-line config edited per laptop. A QR code on the laptop showing its IP is the V3 nicety. |
| 8 | **Two-directory layout: upstream stays in `Downloads/build-with-claude-main/`, our code lives in `claude-pocket-bot/`** | The upstream bundle is already downloaded and untouched at `~/Downloads/build-with-claude-main/`. Our briefbot work goes in `~/claude-pocket-bot/` mirrored to the same shape (`buddy/device/apps/`, `buddy/device/`). `push.py --src ~/claude-pocket-bot/buddy/device` only pushes our files into the device filesystem set up by the upstream `m5-onboard go` flash. Cleaner than forking — our git history is purely our additions, and pulling upstream updates is a fresh download, not a merge. |

---

## Open questions — needs user answer before Phase 1 starts

1. **Briefbot API contract.** What's the URL, auth header, request shape, and response shape of the actual internal briefbot endpoint? Just enough so the laptop companion can call it. (Without this, Phase 3 stays in fixture mode.)
2. **Event WiFi client isolation.** Is the venue's `cardputer` AP isolated (clients can't talk to each other) or open? Quick test in Phase 0 below; if isolated, the fallback is a personal hotspot from the laptop.
3. **Cardputer-Adv PSRAM.** Unconfirmed in published specs. We'll print `gc.mem_free()` and `esp32.idf_heap_info()` once at boot and adjust max-clip-duration constants accordingly.
4. **Demo window.** Hours, not days, right? That informs how aggressively we cut Phase 4.

---

## Phase 0 — Foundation (≈ 30 min)

The findings here come from already-completed research; this phase exists to land them as files in your working tree and verify two facts on real hardware.

### Path conventions (used throughout the plan)

```bash
# Upstream bundle (downloaded; do not modify)
export BUNDLE=/Users/bishopkammeraad/Downloads/build-with-claude-main

# Our work (this dir; everything we author)
export WORK=/Users/bishopkammeraad/claude-pocket-bot
```

### What to set up

1. **Scaffold our work tree to mirror the upstream device layout.**
   ```bash
   mkdir -p $WORK/buddy/device/apps
   mkdir -p $WORK/host
   # Optional but nice: init git in $WORK so our additions are version-tracked
   cd $WORK && git init
   ```
   Mirroring `buddy/device/{,apps/}` means `push.py --src $WORK/buddy/device` works without translation: peer modules in our `buddy/device/` land at `/flash/`, our `apps/briefbot.py` lands at `/flash/apps/`. Files we don't author (the launcher, buddy_ble, wifi_event, etc.) stay in the upstream tree and arrive on the device via the one-time flash below.

2. **One-time: flash the device with the upstream bundle.**
   ```bash
   cd $BUNDLE
   # In Claude Code, with this dir as CWD, the m5-onboard skill auto-discovers
   # via .claude/skills/. Invoke per its README:
   m5-onboard go
   # Or, equivalently, the skill's go.py script directly. Skill scripts at:
   #   $BUNDLE/.claude/skills/m5-onboard/scripts/
   ```
   This installs UIFlow + the launcher + buddy_* + the three sample apps. After this, the device boots into the launcher and shows hello / snake / claude-buddy. Our briefbot files get pushed incrementally on top of that filesystem in later phases — no re-flashing.

3. **Confirm the Cardputer's serial port.**
   ```bash
   python3 $BUNDLE/.claude/skills/m5-onboard/scripts/detect.py
   # expect /dev/cu.usbmodem* (Cardputer-Adv ships native USB)
   export PORT=/dev/cu.usbmodemXXXX
   ```

4. **Probe the device for memory + WiFi reachability.**
   ```bash
   python3 $BUNDLE/buddy/scripts/repl_run.py --port $PORT --script \
     "import gc, esp32; print('free=', gc.mem_free()); print(esp32.idf_heap_info(esp32.HEAP_DATA))"
   python3 $BUNDLE/buddy/scripts/repl_run.py --port $PORT --script \
     "import socket; s=socket.socket(); s.settimeout(2); \
      print(s.connect_ex(socket.getaddrinfo('LAPTOP_IP', 5005)[0][-1]))"
   ```
   Replace `LAPTOP_IP` with the laptop's IP on the event WiFi (`ipconfig getifaddr en0` on macOS). A `connect_ex` return of `0` (or even `ECONNREFUSED` = `61`) means the network path works; `ETIMEDOUT` = client isolation, fall back to laptop hotspot.

### Verification checklist

- `gc.mem_free()` ≥ 100 KB at idle → V1 voice cap of 3 sec audio is safe.
- TCP probe to laptop IP returns 0 or 61, not timeout.
- After `m5-onboard go`, the device boots into the launcher and the three sample apps (hello, snake, claude-buddy) are selectable.
- `$WORK/buddy/device/apps/` and `$WORK/host/` exist, are empty, and `$WORK` is a git repo.

### Allowed APIs / patterns reference (copy from these files)

| Need | Source | Why |
|---|---|---|
| App skeleton (chrome, font, key loop, exit) | `buddy/device/apps/hello_cardputer.py:1-220` | Smallest working example; the three-zone layout (header / content / hint) and 40 ms `kb.tick()` cadence are the bundle's conventions |
| Color palette | `buddy/device/apps/hello_cardputer.py:49-53` and `buddy/device/buddy_ui_cp.py:` (palette block) | `BLACK / ORANGE 0xCC785C / CREAM 0xF0EEE6 / DARK 0x1F1F1F / GRAY_MID 0x777777` |
| Key intent normalization (incl. arrows + 0x0A Enter) | `buddy/device/main.py:358-396` | Cardputer's arrow-labeled keys report as `; , . /` and Enter is 0x0A on this firmware; copy this function near-verbatim |
| `MatrixKeyboard()` debounce after entry | `buddy/device/apps/hello_cardputer.py:182` and `buddy/device/main.py:491-497` | 800 ms cold-boot pre-init, then 400 ms post-init — empirically required, not optional |
| Exit via `machine.reset()` | `buddy/device/apps/hello_cardputer.py:204-213` | UIFlow has no return-to-launcher API; soft reboot is the only way |
| Auto-discovered apps directory | `buddy/device/main.py:230-256` | Drop `briefbot.py` into `/flash/apps/` and the launcher picks it up; underscores in filenames render as spaces in the menu |

### Anti-patterns (do NOT do)

- ❌ Don't `import urequests` — UIFlow 2.0 ships `requests2`, not `urequests` (https://uiflow-micropython.readthedocs.io/en/2.3.3/software/requests2.html). Even then, we're not making HTTP calls from the device in V1.
- ❌ Don't pass `verify=` or `timeout=` to `requests2` — neither is documented in the public API table; assume neither works.
- ❌ Don't use `M5.Mic.record(buf, rate)` for live streaming — it blocks until the buffer is full, which means a fixed-size pre-allocation. Use `machine.I2S` directly for chunk-by-chunk reads.
- ❌ Don't use `bluetooth.BLE().active(True)` after WiFi is already up without a 1000 ms drain delay — the radio coexistence arbiter can C-fault the chip (`$BUNDLE/buddy/device/apps/claude_buddy.py:165-188`). Not relevant in V1 (we're WiFi-only) but worth knowing.

---

## Phase 1 — Demoable stub app, no voice yet (≈ 60 min)

End state: pick "Briefbot" from the launcher → arrow-pick a prospect from a fixture list → see a paginated brief on the screen → ESC back to launcher cleanly. No network. No audio. This is your "the venue WiFi went down" fallback path, and it ships first because it lets you verify the whole render pipeline before audio plumbing.

### Files to create

```
buddy/device/apps/briefbot.py          ← app, ~250 lines
buddy/device/briefbot_api.py           ← lookup() interface, fixture-backed in V1
buddy/device/briefbot_fixtures.py      ← 5–10 hand-curated prospect briefs
```

### `briefbot_fixtures.py` — shape

```python
# Hand-edited dict mapping company-name (lowercase, normalized) → brief dict.
# Brief keys are stable across V1/V3 — Phase 3 just swaps the data source.
PROSPECTS = {
    "applied intuition": {
        "name": "Applied Intuition",
        "domain": "appliedintuition.com",
        "employees": "501-1000",
        "headcount_growth_yoy": "+38%",
        "stage": "Series E ($6B post)",
        "hq": "Mountain View, CA",
        "summary": "AV simulation + dev tools. Defense expansion.",
        "talking_points": [
            "EOL hardware refresh in Q3 — fits our pitch.",
            "Hiring 40+ infra eng in Bay Area.",
            "CTO Qasar's recent post on sim infra cost.",
        ],
        "contacts": [
            {"name": "Qasar Younis", "title": "CEO", "email": "qasar@..."},
        ],
    },
    # ... 5–10 entries covering today's meetings
}
```

### `briefbot_api.py` — V1 implementation

```python
# Single public function. The device app never imports fixtures directly.
# In Phase 3 this file gets a real network branch behind a feature flag,
# but the signature and return shape stay identical.
import briefbot_fixtures as _fix

def lookup(query):
    """Return brief dict for a prospect, or None if not found.
    `query` is a free-form string — company name, domain, whatever the
    user said. V1: lowercase + strip and key into fixtures. V3: ship to
    the laptop companion which does fuzzy match against the real corpus.
    """
    if not query:
        return None
    return _fix.PROSPECTS.get(query.strip().lower())
```

### `briefbot.py` — app outline (DO NOT WRITE YET, this is the spec)

Three modes (state machine):

1. **Picker mode** (entry state): list of fixture company names, arrow-key navigated, Enter selects.
2. **Brief mode**: paginated render of the selected brief. Sections (header / body / hint) match the bundle's chrome conventions. Down-arrow scrolls a long brief; ESC returns to picker.
3. **Empty state**: "No matching prospect" with a hint to try arrow-keying.

Functions to implement (target file size ~250 lines):

- `_set_font()` — copy from `hello_cardputer.py:67-73`
- `_draw_chrome(title)` — header band + orange hairline + hint strip; copy structure from `hello_cardputer.py:76-107`
- `_intent(k)` — copy `main.py:358-396` so arrows + Enter + ESC work
- `_draw_picker(prospects, cursor)` — copy menu-row pattern from `main.py:299-355`, dropping the burst-animation column (your content area is the full width)
- `_wrap(text, max_chars)` — naive word-wrap, return list of lines; max_chars ≈ 35 at DejaVu9 size 1
- `_draw_brief(brief, scroll)` — render header (company, employees, stage), then bulleted talking points, then contact cards. Use `_LCD.textWidth()` for any centering; never estimate from char count (`buddy_ui_cp.py:18-32` has the rationale).
- `run()` — main loop, mirrors `claude_buddy.py:294-361`. 40 ms poll, three keypress paths (Enter, arrows, ESC). Final block does `M5.Lcd.fillScreen(BLACK)` then `machine.reset()`.
- Bare `run()` call at module bottom — the launcher imports apps; module-level `run()` is the entrypoint convention (`hello_cardputer.py:220`).

### Iteration loop

```bash
# (assumes $BUNDLE, $WORK, $PORT exported from Phase 0)

# push our three new files — --src points at OUR mirror, not upstream
python3 $BUNDLE/buddy/scripts/push.py --port $PORT --src $WORK/buddy/device \
  --files apps/briefbot.py briefbot_api.py briefbot_fixtures.py

# tail serial for tracebacks while testing
python3 $BUNDLE/buddy/scripts/tail_serial.py --port $PORT --seconds 30
```

After push, `push.py` triggers `machine.reset()` automatically ($BUNDLE`/buddy/scripts/push.py:190-192`); the launcher boots, scans `/flash/apps/`, and the new "Briefbot" entry appears.

### Verification checklist

- "Briefbot" appears in the launcher menu (filename underscores → spaces, title-cased per `main.py:230-255`).
- Picker shows all fixture entries; arrows scroll without flicker.
- Selecting a prospect renders the brief; long talking-point text wraps cleanly without clipping.
- ESC reliably returns to the launcher (clean reset, no boot loop).
- Serial tail shows no exceptions during a full picker → brief → ESC cycle.

### Anti-patterns

- ❌ Don't centre text by computing `len(text) * CHAR_W` — DejaVu9 is proportional. Use `_LCD.textWidth(text)`.
- ❌ Don't call `_LCD.fillScreen(BLACK)` on every loop tick — only on chrome redraws when the screen content actually changed. Diff-based partial repaint of just the picker rows or the brief content area is cheap and avoids flicker.
- ❌ Don't filter the picker via free-text typing in V1. Voice is the typing replacement; arrow keys are enough for the fixture list.

---

## Phase 2 — Audio capture and TCP transport (≈ 90 min)

End state: hold space on the Cardputer → laptop receives a clean WAV file and writes it to `/tmp/briefbot-N.wav`. No transcription yet. No briefbot integration. This phase exists to nail the audio quality and the network round trip independently of any cloud APIs.

### Files to create / modify

```
buddy/device/briefbot_audio.py         ← new peer module: I2S init + streaming
buddy/device/apps/briefbot.py          ← edit: add a third "voice mode" gated by a feature flag
buddy/device/briefbot_config.py        ← new: { LAPTOP_IP, LAPTOP_PORT }, gitignored
host/briefbot_host.py                  ← new: TCP server that writes incoming PCM to WAV
host/requirements.txt                  ← bleak (later), pyaudio not needed
```

### Device side: `briefbot_audio.py`

Two functions, public:

- `start_stream(ip, port)` — open a `socket.socket(AF_INET, SOCK_STREAM)`, `connect((ip, port))`, return `(sock, i2s)` where `i2s` is a `machine.I2S(...)` instance configured for **16 kHz mono 16-bit** input on the ES8311 codec pins (G41/G46/G43/G42 per the M5 Cardputer-Adv pinmap).
- `stream_chunk(sock, i2s, buf)` — `i2s.readinto(buf)` then `sock.write(buf[:n])`. Caller loops until key release.

Critical implementation notes:

- **Sample rate.** `M5.Mic.setSampleRate(16000)` is unconfirmed in the documented list (8000/11025/22050/32000/44100). Try 16000 first via `machine.I2S` (the I2S API supports arbitrary rates per the chip clock divider); if it fails, fall back to **22050** and the laptop resamples. The lookup pipeline doesn't care which.
- **Chunk size.** 2048-byte buffer = 64 ms at 16 kHz mono 16-bit. Small enough that key-release latency is barely perceptible; large enough that we're not slamming the WiFi stack.
- **No `M5.Mic.record()` — it blocks until the buffer fills.** Use raw `machine.I2S`. This was the key finding from Phase 0 audio research.
- **WiFi already on** when this code runs, so no BLE/coexistence dance needed (unlike `claude_buddy.py:165-188`).

### Device side: `briefbot.py` voice-mode addition

Add a fourth state to the state machine:

4. **Voice-listen mode**: triggered by holding space. Show a "Listening…" overlay (orange dot + countdown). On release: close socket, transition to "Awaiting brief…" while waiting for the laptop's JSON response over the same socket (or a separate inbound TCP read). On timeout (8 s) or error, flash a toast and return to picker.

Hold-detection pattern: `kb.tick()` returns key-down events; `kb.is_pressed(key)` polled per loop tick is the right way to detect held vs released. Confirm the exact API in `MatrixKeyboard` source by `repl_run.py --script "from hardware import MatrixKeyboard; help(MatrixKeyboard)"` — verify before assuming.

### Laptop side: `briefbot_host.py`

Standalone Python script. `python3 host/briefbot_host.py --port 5005 --out-dir /tmp` and it:

1. `socket.socket().bind(('0.0.0.0', 5005)).listen()`. One client at a time is fine for V2.
2. On accept, `recv(4096)` in a loop. EOF on the socket = end of clip (caller closed on key release). Append all bytes to a buffer.
3. Wrap the raw PCM in a 44-byte WAV header (`wave.open(...,'wb').writeframes(pcm)`).
4. Write to `/tmp/briefbot-{timestamp}.wav`. Print a summary line: `received N bytes ≈ N/32000 sec → /tmp/briefbot-...wav`.

### Verification checklist

- Hold space for 3 s, release. Laptop prints `received ~96000 bytes ≈ 3.0 sec → /tmp/briefbot-001.wav`.
- `afplay /tmp/briefbot-001.wav` on macOS plays back recognizable speech, not garbled noise.
- 10 consecutive recordings without device crash. Serial tail shows no `ENOMEM`.
- Latency from key-release to "received" log line is < 100 ms (proves we're streaming, not buffering).

### Allowed APIs / patterns reference

| Need | Source | Detail |
|---|---|---|
| `machine.I2S` constructor | https://docs.micropython.org/en/latest/library/machine.I2S.html | `I2S(0, sck=Pin(41), ws=Pin(43), sd=Pin(46), mode=I2S.RX, bits=16, format=I2S.MONO, rate=16000, ibuf=8192)` |
| `socket.write` semantics | https://docs.micropython.org/en/latest/library/socket.html | Blocking by default; no short-write semantics on blocking sockets |
| Cardputer-Adv I2S pinmap | https://docs.m5stack.com/en/core/Cardputer-Adv | G41 SCLK, G46 ASDOUT, G43 LRCK, G42 DSDIN — verify against the schematic before committing |
| Connection lifecycle | event-WiFi already up via `wifi_event.py:37-96` | No connect dance needed; just `getaddrinfo` + `connect` |

### Anti-patterns

- ❌ Don't try mDNS service discovery — not in core MicroPython on this build. Hardcode laptop IP in `briefbot_config.py`.
- ❌ Don't use `M5.Mic.record()` and then upload the whole buffer at end — exhausts RAM at >3 s clips.
- ❌ Don't stream-while-rendering the "Listening…" overlay — keep the audio loop tight (no LCD writes during readinto/write). Render the overlay once on entry, redraw the timer in a separate timer-driven path or just skip it.

---

## Phase 3 — Real transcription + briefbot lookup (≈ 90 min)

End state: hold space → say "what's the employee size of Applied Intuition" → release → brief renders on the screen with real data. Fixture path remains as the offline fallback when the laptop is asleep.

### Files to modify / create

```
host/briefbot_host.py            ← extend: WAV → Whisper → briefbot → JSON-line response
host/.env                        ← OPENAI_API_KEY, BRIEFBOT_URL, BRIEFBOT_TOKEN; gitignored
buddy/device/briefbot.py         ← edit: render the JSON response from the laptop
```

### Laptop pipeline (host/briefbot_host.py)

```python
# Pseudocode — Phase 3 layout
def handle_client(conn):
    pcm = recv_until_eof(conn)              # from Phase 2
    wav = wrap_wav(pcm, sample_rate=16000)  # in-memory bytes

    # 1. Transcribe
    transcript = openai_whisper(wav)        # POST /v1/audio/transcriptions, model=whisper-1

    # 2. Extract entity (start naive: the whole transcript IS the query)
    query = transcript                      # V3 polish: regex / Claude entity extraction

    # 3. Look up
    brief = briefbot_search(query)          # POST to internal API

    # 4. Optional: Claude-summarize for the 240×135 screen
    if CLAUDE_SUMMARIZE:
        brief = claude_compress(brief, transcript)  # claude-haiku-4-5 or sonnet-4-6

    # 5. Send back as a single JSON line
    conn.write(json.dumps({"ok": True, "transcript": transcript, "brief": brief}).encode() + b"\n")
```

### Whisper call (Allowed API: https://platform.openai.com/docs/api-reference/audio/createTranscription)

```python
import openai
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
with open(wav_path, "rb") as f:
    r = client.audio.transcriptions.create(model="whisper-1", file=f)
transcript = r.text
```

`whisper-1` is the default and cheapest at $0.006/min. `gpt-4o-mini-transcribe` is half the price ($0.003/min) if you want to swap. Latency ~1–2 s for a 3 s clip. **Audio is NOT a Claude content block** (verified — see "Anti-patterns"), so we cannot skip Whisper.

### Briefbot call (open question — fill in once contract is known)

```python
def briefbot_search(query):
    r = requests.post(
        os.environ["BRIEFBOT_URL"],
        json={"q": query},
        headers={"Authorization": f"Bearer {os.environ['BRIEFBOT_TOKEN']}"},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()  # shape TBD by the user
```

The brief shape returned here defines what `briefbot.py` renders. Recommend matching the fixture shape from Phase 1 so no device-side changes are needed beyond unwrapping `{"brief": ...}`.

### Optional: Claude summarization for the small screen

Briefbot likely returns more data than fits on 240×135. Pipe through Claude with a system prompt that constrains output: ≤7 short bullet points, ≤35 chars each, headline + employees + stage + 3 talking points + 1 contact. Use **`claude-haiku-4-5-20251001`** for sub-second latency at this size.

```python
import anthropic
ac = anthropic.Anthropic()
msg = ac.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=400,
    system="Compress this prospect brief to fit a 240x135 LCD. Output JSON with keys: name, line2, bullets (list of 5 strings, each ≤35 chars). Speak telegraphically.",
    messages=[{"role": "user", "content": json.dumps(raw_brief)}],
)
```

This is the "Anthropic event magic" beat — voice in, Claude-shaped out, on a tiny screen.

### Device side: render the response

`briefbot.py` voice-mode now reads the JSON line from the same socket after key release, parses, and routes to `_draw_brief(brief)`. Same render code as Phase 1; only the data source changed. If `requests` on the laptop fails, the laptop sends `{"ok": false, "err": "..."}` and the device flashes a toast.

### Verification checklist

- Hold space, say "Applied Intuition", release. Brief renders within 3–4 s.
- Whisper transcript visible in laptop console; matches what was said.
- Test 5 different prospects from the live briefbot dataset — all render.
- Pull the laptop's network cable mid-stream → device flashes "Connection lost" toast, doesn't crash, returns to picker.
- The fixture path from Phase 1 still works when the laptop is offline (the device falls back if connect fails immediately).

### Anti-patterns

- ❌ Don't pass audio to Claude expecting transcription. Audio is not a Messages API content type — only `text`, `image`, `document`, `search_result`, `thinking`, `tool_use`, `tool_result`, `server_tool_use`, `container_upload` (https://platform.claude.com/docs/en/api/messages). Confirmed with anthropic-sdk-python issue #1198 still open as of Feb 2026.
- ❌ Don't put the OpenAI / briefbot tokens in `briefbot_config.py` on the device. Tokens live in `host/.env` on the laptop, period. The device only knows the laptop's IP.
- ❌ Don't `r.json()` on a multi-KB Whisper response in MicroPython — but this isn't in scope on the device side. The laptop does Whisper and returns a small summarized blob.

---

## Phase 4 — Polish (optional, ≈ 60–120 min)

Pick from this menu based on remaining time:

1. **Live partial transcripts.** Swap Whisper for **Deepgram nova-3** streaming over WebSocket (`wss://api.deepgram.com/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&interim_results=true`). Laptop forwards partial transcripts to the device on a second TCP frame; device renders them under the "Listening…" overlay so you watch your words appear as you speak. Sub-second perceived latency. $0.0077/min. Worth it for the demo if you have 60+ min left. (https://developers.deepgram.com/reference/speech-to-text/listen-streaming)

2. **"Today's meetings" picker.** Replace the static fixtures with a JSON file the laptop pushes on every Cardputer boot via the same TCP server: device opens socket on boot → laptop pushes `today.json` → device caches in RAM (don't write to flash; meetings change daily). The picker now shows actual upcoming meetings.

3. **Claude entity extraction.** "What's the employee size of Applied Intuition" → Claude pulls `entity="Applied Intuition"` and `field="employee_size"` and the laptop renders just that field on the screen, big and bold ("**Applied Intuition: 501–1000 employees**"). Distinct from the full-brief view; let arrow-down show the rest.

4. **Battery + WiFi pip in the header.** The launcher already has this for WiFi (`main.py:220-227`). Mirror it in briefbot.py for visual consistency.

5. **QR code on the laptop.** `host/briefbot_host.py --serve-qr` opens a small Tk window with a QR encoding `briefbot://<ip>:<port>`. Wave the QR at someone else's Cardputer's camera (no camera — never mind). Skip.

---

## Final phase — End-to-end verification

Before declaring done, run this checklist:

- [ ] `git remote -v` shows fork + upstream.
- [ ] Cold boot the device; wait for launcher → "Briefbot" visible.
- [ ] Pick Briefbot → picker mode shows fixture list.
- [ ] Arrow + Enter on a fixture → brief renders cleanly, no clipping.
- [ ] ESC → back to launcher (not boot-looping).
- [ ] Hold space, speak, release → real brief renders within 4 s. Repeat 3×.
- [ ] Pull laptop offline → voice-mode flashes "Host unreachable" and falls back to picker; fixtures still work.
- [ ] Speak a prospect not in briefbot → laptop returns `{"ok": false, "err": "no match"}`, device shows toast, returns to picker.
- [ ] Power-cycle the device 3× → app launches reliably each time.
- [ ] Grep for `urequests` in the codebase — should return nothing (we use `requests2` if we use anything, and only on the laptop with `openai`/`requests`).
- [ ] Grep `briefbot_config.py` for any token-looking string — should be only `LAPTOP_IP`/`LAPTOP_PORT`. Tokens never on device.

---

## Reference cheat sheet (one-glance)

```bash
# Set once per shell
export BUNDLE=/Users/bishopkammeraad/Downloads/build-with-claude-main
export WORK=/Users/bishopkammeraad/claude-pocket-bot
export PORT=$(python3 $BUNDLE/.claude/skills/m5-onboard/scripts/detect.py)

# One-time, from $BUNDLE (use the m5-onboard skill — auto-discovered when CWD is $BUNDLE):
#   m5-onboard go

# Push our device files (peer modules to /flash/, apps to /flash/apps/)
python3 $BUNDLE/buddy/scripts/push.py --port $PORT --src $WORK/buddy/device \
  --files apps/briefbot.py briefbot_api.py briefbot_fixtures.py briefbot_audio.py briefbot_config.py

# Tail serial for prints/tracebacks
python3 $BUNDLE/buddy/scripts/tail_serial.py --port $PORT --seconds 30

# One-shot REPL probe
python3 $BUNDLE/buddy/scripts/repl_run.py --port $PORT --script "import gc; print(gc.mem_free())"

# Laptop companion (lives in our work tree)
python3 $WORK/host/briefbot_host.py --port 5005
```

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Event WiFi has client isolation | Detected in Phase 0 probe; fall back to laptop personal hotspot |
| No PSRAM → low free heap | Streaming approach already accounts for this; the fixture path doesn't allocate |
| `machine.I2S` at 16 kHz fails on the ES8311 driver | Fall back to 22050; laptop resamples (trivially, with `numpy` or `audioop`) |
| Whisper API key leak from the laptop | `.env` file is gitignored; key rotation post-event regardless |
| BLE coexistence radio fault if voice-mode triggers BLE init | Not in scope — V1 is WiFi-only. If Phase 4 adds BLE, follow the 1000 ms drain pattern from `claude_buddy.py:165-188` |
| Briefbot returns a brief that doesn't fit the fixture shape | Phase 3 includes a Claude-compress step that normalizes to the fixture shape; fall back to a flat-text dump if compression fails |

---

## Minimum demoable cut

If you have **30 min**: Phase 1 only. Arrow-pick a prospect from a fixture list, see a brief. The story is "I built a sales-prospect tool on the Anthropic Cardputer in 30 minutes."

If you have **3 hours**: Phases 1+2+3. Voice in, real brief out. The story is "I held the space bar, asked about a company, and Claude+Whisper served the answer."

If you have **a full day**: add Phase 4. Live partial transcripts, today's-meetings picker, Claude-shaped output. The story is the demo people line up to try.
