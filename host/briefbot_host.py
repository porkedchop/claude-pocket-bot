#!/usr/bin/env python3
"""Briefbot laptop companion.

The Cardputer connects over TCP, streams 16 kHz mono PCM while the
user holds space, then closes the write half. We:
  1. Wrap the PCM as a WAV.
  2. Transcribe via OpenAI Whisper.
  3. Look up the prospect, in priority order:
       (a) the icp_ranker dashboard at ~/accountbriefbot — full ranked-
           CSV row + recent signals + Google News.
       (b) Brave + Claude fresh search (for companies not in the CSV).
       (c) bundled device fixtures (offline fallback).
  4. Format for the 240x135 LCD via Claude Haiku — biased to whatever
     the user actually asked.
  5. Send a single JSON line back on the socket.

Config — host/.env first, then ~/accountbriefbot/.env auto-loaded:
  OPENAI_API_KEY     — Whisper transcription.
  ANTHROPIC_API_KEY  — Claude Haiku for screen-format step.
  BRAVE_API_KEYS     — Comma-separated; rotated on 429 (Brave fallback).
  ACCOUNTBRIEFBOT_DIR — Override the default ~/accountbriefbot path.

Run:
  python3 briefbot_host.py --port 5005
"""

import argparse
import json
import os
import socket
import sys
import time
import wave
from pathlib import Path

# ── env loading ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(_HERE / ".env")
    # Load known key stores in priority order — first set wins per key.
    # GTMOS is loaded before accountbriefbot because the latter's
    # ANTHROPIC_API_KEY has been observed to be expired; GTMOS's is the
    # active key. accountbriefbot still contributes BRAVE_API_KEYS etc.
    for ext in (
        Path.home() / "Desktop" / "GTMOS" / ".env",
        Path.home() / "accountbriefbot" / ".env",
    ):
        if ext.exists():
            load_dotenv(ext, override=False)
except ImportError:
    print("note: python-dotenv not installed; relying on shell env",
          file=sys.stderr)

# ── device fixture mirror (offline fallback) ───────────────────────────────────
_DEVICE_DIR = _HERE.parent / "buddy" / "device"
sys.path.insert(0, str(_DEVICE_DIR))

# ── icp_ranker dashboard import — the real prospect data layer ─────────────────
# server.py at module load registers Flask routes and starts a few caches but
# does NOT start serving. We only call lookup helpers, never app.run(), so the
# Flask side is dormant. _reload_company_industry() bootstraps _COMPANY_ROWS
# from the latest ranked CSV (~1300 companies × ~50 columns each).
_ABB_ROOT = Path(os.environ.get(
    "ACCOUNTBRIEFBOT_DIR", str(Path.home() / "accountbriefbot")))
_abb = None
_ABB_COUNT = 0
if _ABB_ROOT.exists() and (_ABB_ROOT / "icp_ranker").exists():
    try:
        sys.path.insert(0, str(_ABB_ROOT))
        sys.path.insert(0, str(_ABB_ROOT / "icp_ranker"))
        # cwd matters: server.py reads config.py via relative path resolution
        _orig_cwd = os.getcwd()
        os.chdir(_ABB_ROOT)
        try:
            # Silence the dashboard's verbose [contacts]/[Meta]/[News]
            # boot prints — they go to stdout and add noise to ours.
            import contextlib as _ctx
            import io as _io
            with _ctx.redirect_stdout(_io.StringIO()):
                from icp_ranker import server as _abb_mod
                _abb_mod._reload_company_industry()
            _abb = _abb_mod
            _ABB_COUNT = len(_abb._COMPANY_ROWS)
        finally:
            os.chdir(_orig_cwd)
    except Exception as e:
        print(f"note: accountbriefbot import failed: {e}", file=sys.stderr)
        _abb = None


def _has_whisper():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _has_claude():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _brave_keys():
    raw = os.environ.get("BRAVE_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _has_brave():
    return bool(_brave_keys())


# ── transcription ──────────────────────────────────────────────────────────────
def _transcribe(wav_path):
    from openai import OpenAI
    client = OpenAI()
    with open(wav_path, "rb") as f:
        r = client.audio.transcriptions.create(model="whisper-1", file=f)
    return (getattr(r, "text", "") or "").strip()


def _extract_entity(transcript):
    """Pull the company name out of a noisy transcript. The dashboard's
    _lookup_company expects a clean name (no "what's the size of ..."
    framing), so we run a one-shot Haiku call up front. Falls back to
    the raw transcript if Claude is unavailable or errors."""
    if not _has_claude() or not transcript.strip():
        return transcript
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=40,
            system=(
                "Extract the company name from the user's spoken question. "
                "Output ONLY the company name, nothing else, no quotes, no "
                "punctuation. If multiple companies are mentioned, output "
                "the primary subject. If no company is identifiable, echo "
                "the input."
            ),
            messages=[{"role": "user", "content": transcript}],
        )
        out = msg.content[0].text.strip().strip('"\'').rstrip(".")
        return out or transcript
    except Exception as e:
        print(f"  entity extract error: {e}", file=sys.stderr)
        return transcript


# ── lookup tier 1: dashboard data ──────────────────────────────────────────────
def _match_in_transcript(transcript):
    """Find the longest known company name that appears as a word-
    boundary substring of `transcript`. The dashboard's _lookup_company
    matches "query in company name" (good for partial typed names);
    we need the inverse for noisy spoken transcripts like "tell me
    about applied intuition" → match "Applied Intuition".
    """
    if _abb is None:
        return None
    import re
    norm_q = " " + _abb._norm_name(transcript) + " "
    if not norm_q.strip():
        return None
    best = None
    best_len = 0
    for row in _abb._COMPANY_ROWS:
        name = row.get("Company", "")
        if not name:
            continue
        norm_n = _abb._norm_name(name)
        if len(norm_n) < 3 or len(norm_n) <= best_len:
            continue
        pattern = r"(?:^|\s)" + re.escape(norm_n) + r"(?:\s|$)"
        if re.search(pattern, norm_q):
            best = row
            best_len = len(norm_n)
    return best


def _lookup_dashboard(query):
    """Match query against the ranked CSV and return a rich dict
    {row, signals, news} on hit, or None on miss.

    Uses word-boundary matching only — the dashboard's _lookup_company
    is too permissive for our query shape (e.g. "Ramp" via substring
    match returns "Electronic On-Ramp"). Word-boundary handles both
    clean entities ("Applied Intuition") and noisy transcripts ("tell
    me about applied intuition"); for partial typed names a separate
    UI surface would be needed (not in scope for voice).
    """
    if _abb is None:
        return None
    row = _match_in_transcript(query)
    if not row:
        return None
    name = row.get("Company") or query
    out = {"row": row, "signals": [], "news": []}
    try:
        out["signals"] = _abb._get_recent_signals(name, max_days=120) or []
    except Exception as e:
        print(f"  signals error: {e}", file=sys.stderr)
    try:
        out["news"] = _abb.fetch_news(name, count=5) or []
    except Exception as e:
        print(f"  news error: {e}", file=sys.stderr)
    return out


# ── lookup tier 2: Brave + Claude (companies not in the ranked CSV) ────────────
def _brave_search(query, count=8):
    import requests
    keys = _brave_keys()
    for key in keys:
        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json",
                         "X-Subscription-Token": key},
                params={"q": query, "count": count},
                timeout=12,
            )
            if r.status_code == 429:
                continue
            r.raise_for_status()
            return r.json().get("web", {}).get("results", []) or []
        except Exception as e:
            print(f"  brave error ({key[:8]}...): {e}", file=sys.stderr)
            continue
    return []


def _lookup_brave(query):
    """Fresh-from-the-web brief for companies not in the dashboard.
    Returns a 'rich' dict shape compatible with _format_brief.
    """
    if not (_has_brave() and _has_claude()):
        return None
    snippets = []
    for q in (
        f"{query} headcount employees funding stage HQ",
        f"{query} site:linkedin.com OR site:crunchbase.com OR site:growjo.com",
    ):
        for hit in _brave_search(q, count=8):
            snippets.append("{} | {} | {}".format(
                hit.get("title", ""),
                hit.get("description", ""),
                hit.get("url", ""),
            ))
        if len(snippets) >= 16:
            break
    if not snippets:
        return None
    return {"row": None, "signals": [], "news": [],
            "brave_snippets": snippets[:14]}


# ── lookup tier 3: device fixtures ─────────────────────────────────────────────
def _lookup_fixture(query):
    try:
        import briefbot_api
        row_or_brief = briefbot_api.lookup(query)
        if not row_or_brief:
            return None
        # Fixtures are already in brief shape — wrap minimally so the
        # caller can treat all tiers uniformly.
        return {"row": None, "signals": [], "news": [],
                "fixture_brief": row_or_brief}
    except Exception as e:
        print(f"  fixture lookup error: {e}", file=sys.stderr)
        return None


# ── formatting: rich dict → device's 7-field brief schema ──────────────────────
def _row_to_brief_direct(row):
    """No-Claude direct field mapping. Used when Anthropic key isn't set."""
    if not row:
        return None
    points = []
    for key in ("Hardware Fleet", "Hardware Evidence", "MDM Tool",
                "Productivity Suite", "Work Model"):
        v = (row.get(key) or "").strip()
        if v:
            points.append(f"{key.split(':')[0][:14]}: {v[:18]}")
        if len(points) >= 4:
            break
    return {
        "name": row.get("Company", ""),
        "employees": str(row.get("Global Employees") or ""),
        "headcount_growth_yoy": str(row.get("Employee Growth YoY") or ""),
        "stage": (row.get("Financial Status") or
                  row.get("Last Funding Round") or ""),
        "hq": row.get("Headquarters") or "",
        "summary": (row.get("Note: Hardware") or
                    row.get("Note: Industry") or "")[:70],
        "talking_points": points or [(row.get("Industry Vertical") or "")[:35]],
        "contacts": [],
    }


def _format_brief(rich, transcript):
    """Compress a rich lookup result into the 240x135 device schema.

    Tier-aware: dashboard rows get the full Revivn-context treatment;
    Brave snippets get the same prompt with web text instead of a row;
    fixture briefs pass through verbatim (already in the right shape).
    """
    if not rich:
        return None
    if rich.get("fixture_brief"):
        return rich["fixture_brief"]

    if not _has_claude():
        return _row_to_brief_direct(rich.get("row"))

    import anthropic
    client = anthropic.Anthropic()
    sys_prompt = (
        "You brief sales prospects for Revivn — corporate IT-asset-"
        "disposition: buys back used laptops + corporate hardware, "
        "refurbishes, resells. Output JSON for a 240x135 LCD render.\n\n"
        "REQUIRED KEYS (use empty string or [] when unknown — never null):\n"
        '  name (str), employees (str e.g. "3844" or "501-1000"),\n'
        '  headcount_growth_yoy (str e.g. "+38%" or ""),\n'
        '  stage (str e.g. "Series E ($6B)" or "Public"),\n'
        '  hq (str e.g. "Mountain View, CA"),\n'
        '  summary (<=70 char str),\n'
        '  talking_points (list of 3-5 str, each <=35 chars),\n'
        '  contacts (list of {name,title,email}; can be []).\n\n'
        "PRIORITIZE Revivn-relevant facts in talking_points: Hardware Fleet, "
        "MDM Tool, Productivity Suite, Work Model, recent layoff/funding/M&A "
        "signals, headcount growth direction. If the user's question targets "
        "a specific field, reorder talking_points so bullet 1 answers it. "
        "Telegraphic style. No markdown. Output ONLY JSON."
    )
    payload = {}
    if rich.get("row"):
        # Hand Claude the columns most likely to matter; trim noise.
        keep = ("Company", "Domain", "Headquarters", "Founded",
                "Financial Status", "Last Funding Round", "Funding Year",
                "Current Valuation", "Revenue Estimate", "Global Employees",
                "Employee Growth YoY", "Industry Vertical", "Hardware Fleet",
                "Hardware Evidence", "MDM Tool", "Productivity Suite",
                "Work Model", "Key Investors", "CRM Status", "CRM Stage",
                "Account Owner", "Note: Hardware", "Note: Employees",
                "Note: Industry", "Note: Tech Stack")
        payload["row"] = {k: rich["row"].get(k, "") for k in keep
                          if rich["row"].get(k) not in (None, "", "—")}
    if rich.get("signals"):
        payload["signals"] = rich["signals"][:5]
    if rich.get("news"):
        payload["news"] = [
            {"title": n.get("title", ""), "date": n.get("published", "")}
            for n in rich["news"][:5]
        ]
    if rich.get("brave_snippets"):
        payload["web_snippets"] = rich["brave_snippets"]

    user_msg = (
        f"User asked: {transcript}\n\n"
        f"DATA:\n{json.dumps(payload, default=str)[:5500]}"
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=sys_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            parts = text.split("\n", 1)
            text = parts[1] if len(parts) == 2 else text
            text = text.rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        print(f"  format error: {e}", file=sys.stderr)
        # Last-ditch: direct field map if we have a row.
        return _row_to_brief_direct(rich.get("row"))


# ── socket plumbing ────────────────────────────────────────────────────────────
def _save_wav(pcm, out_dir, sample_rate):
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"briefbot-{ts}.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm)
    return path


def _send_json(conn, obj):
    payload = json.dumps(obj, separators=(",", ":")).encode() + b"\n"
    try:
        conn.sendall(payload)
    except OSError as e:
        print(f"  send error: {e}", file=sys.stderr)


def _handle(conn, addr, out_dir, sample_rate):
    print(f"[{time.strftime('%H:%M:%S')}] connect {addr}")

    chunks = []
    while True:
        try:
            buf = conn.recv(4096)
        except OSError as e:
            print(f"  recv error: {e}")
            break
        if not buf:
            break
        chunks.append(buf)
    pcm = b"".join(chunks)
    secs = len(pcm) / float(sample_rate * 2) if pcm else 0.0
    print(f"  received {len(pcm)} bytes (~{secs:.1f}s)")

    if not pcm:
        _send_json(conn, {"ok": False, "err": "no audio"})
        return

    wav_path = _save_wav(pcm, out_dir, sample_rate)
    print(f"  -> {wav_path}")

    if not _has_whisper():
        _send_json(conn, {"ok": False, "err": "no whisper key"})
        return

    try:
        transcript = _transcribe(wav_path)
        print(f"  transcript: {transcript!r}")
    except Exception as e:
        print(f"  whisper error: {e}", file=sys.stderr)
        _send_json(conn, {"ok": False, "err": "transcribe failed"})
        return

    if not transcript:
        _send_json(conn, {"ok": False, "err": "empty transcript"})
        return

    entity = _extract_entity(transcript)
    print(f"  entity: {entity!r}")

    rich = _lookup_dashboard(entity)
    tier = "dashboard"
    if rich is None:
        rich = _lookup_brave(entity)
        tier = "brave"
    if rich is None:
        rich = _lookup_fixture(entity)
        tier = "fixture"

    if rich is None:
        _send_json(conn, {"ok": False, "transcript": transcript, "err": "no match"})
        return

    brief = _format_brief(rich, transcript)
    if brief is None:
        _send_json(conn, {"ok": False, "transcript": transcript, "err": "format failed"})
        return

    print(f"  [{tier}] -> {brief.get('name', '?')}")
    _send_json(conn, {"ok": True, "transcript": transcript, "brief": brief})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--out-dir", default=str(_HERE / "audio_dump"))
    ap.add_argument("--sample-rate", type=int, default=16000)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)

    print(f"briefbot host listening on {args.bind}:{args.port}")
    print(f"  whisper:    {'on' if _has_whisper() else 'OFF (no OPENAI_API_KEY)'}")
    if _abb is not None:
        print(f"  dashboard:  on ({_ABB_COUNT} companies @ {_ABB_ROOT})")
    else:
        print(f"  dashboard:  off (set ACCOUNTBRIEFBOT_DIR or clone to ~/accountbriefbot)")
    if _has_brave() and _has_claude():
        print(f"  brave fbk:  on (Brave x{len(_brave_keys())} + Claude haiku-4-5)")
    else:
        print(f"  brave fbk:  off (need BRAVE_API_KEYS + ANTHROPIC_API_KEY)")
    print(f"  audio dump: {out_dir}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(1)

    try:
        while True:
            conn, addr = srv.accept()
            try:
                _handle(conn, addr, out_dir, args.sample_rate)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
