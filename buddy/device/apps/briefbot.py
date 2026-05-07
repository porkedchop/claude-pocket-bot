"""Briefbot for the M5 Cardputer-Adv.

V1: fixture-driven prospect lookup. Pick a company from the list, see
a paginated brief. Voice input gets layered on in Phase 2/3 (see
PLAN.md at the repo root). The lookup interface is briefbot_api.lookup,
which V1 backs with briefbot_fixtures.PROSPECTS so the device app
works completely offline.

Input
  ; , w     up
  . / s     down
  Enter     select (picker) / no-op (brief)
  ESC / Q   back (brief -> picker, picker -> exit)

Layout matches the bundle's three-zone chrome — 20 px DARK header
with ORANGE hairline at y=20, content area, 18 px hint strip at the
bottom. Font is DejaVu9; centering goes through textWidth() because
the font is proportional (see buddy_ui_cp.py for the long-form
rationale).

Exit protocol mirrors hello_cardputer.py: clear screen, brief pause,
machine.reset() back to the launcher. UIFlow 2.0 has no
return-to-launcher API on this build; soft-reboot is the only way.
"""

import sys

# Make peer modules at /flash/ importable. Same dance every Buddy
# bundle app does — UIFlow's default sys.path doesn't include /flash.
for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

import M5
import machine
from hardware import MatrixKeyboard

import briefbot_api

# Voice support is optional — only active when briefbot_config.py
# (gitignored, copied from briefbot_config.py.example) exists AND
# briefbot_audio.py was pushed alongside this app. Either missing
# import disables the voice path; the picker still works offline.
try:
    import briefbot_config as _cfg
    _LAPTOP_IP = getattr(_cfg, "LAPTOP_IP", None)
    _LAPTOP_PORT = getattr(_cfg, "LAPTOP_PORT", 5005)
except ImportError:
    _LAPTOP_IP = None
    _LAPTOP_PORT = 5005

try:
    import briefbot_audio
except ImportError as e:
    print("briefbot: voice module unavailable:", e)
    briefbot_audio = None

VOICE_ENABLED = bool(_LAPTOP_IP) and briefbot_audio is not None


_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_DARK = 0x1F1F1F
_GRAY_MID = 0x777777
_GREEN = 0x00FF00
_RED = 0xFF0000

_LCD = M5.Lcd
_W = 240
_H = 135

_MENU_X = 10
_MENU_RIGHT = _W - 10
_ROW_H = 16
_FIRST_ROW_Y = 28
_MAX_VISIBLE = 5

_S_PICKER = 0
_S_BRIEF = 1

_PICKER_HINT = ("; .  Enter  SPACE talk  Q quit"
                if VOICE_ENABLED else
                "; .  Enter pick  Q quit")


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        # Build without FONTS; fall back to default. Not fatal.
        print("briefbot: setFont fallback:", e)


def _intent(k):
    """Normalize a MatrixKeyboard return to up/down/select/back/None.

    Cardputer-Adv arrow keys report as `;` `,` `.` `/` (the labels are
    silk-screened arrows but the unshifted ASCII is what comes back).
    Enter reports as 0x0A on this firmware build, not 0x0D — accept
    both. ESC is 0x1B. Same approach as the launcher in main.py.
    """
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "select"
        if k == 0x1B:
            return "back"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch in (";", ",", "w"):
        return "up"
    if ch in (".", "/", "s"):
        return "down"
    if ch in ("\r", "\n"):
        return "select"
    if ch in ("q", "\x1b"):
        return "back"
    if ch == " ":
        return "voice"
    return None


def _draw_header(title):
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString(title, 6, 5)


def _draw_hint(text):
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, _H - 14)


def _draw_picker(prospects, cursor, scroll_top):
    _LCD.fillScreen(_BLACK)
    _draw_header("Briefbot")
    _LCD.setTextSize(1)

    visible = prospects[scroll_top:scroll_top + _MAX_VISIBLE]
    y = _FIRST_ROW_Y
    for i, (display, _key) in enumerate(visible):
        abs_i = scroll_top + i
        if abs_i == cursor:
            _LCD.fillRect(4, y - 2, _MENU_RIGHT - 4, _ROW_H - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(display, _MENU_X, y)
        y += _ROW_H

    if scroll_top > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _MENU_RIGHT - 8, _FIRST_ROW_Y)
    if scroll_top + _MAX_VISIBLE < len(prospects):
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _MENU_RIGHT - 8,
                        _FIRST_ROW_Y + (len(visible) - 1) * _ROW_H)

    _draw_hint(_PICKER_HINT)


def _wrap(text, max_chars):
    """Naive word-wrap. Returns lines each <= max_chars long.

    DejaVu9 is proportional — the right way is `_LCD.textWidth()` per
    line — but at 240 px content width, a 35-char cap at size 1 is a
    safe under-estimate for any reasonable mix of glyphs. If a brief
    has wide-glyph-heavy text and clips, drop max_chars to 32.
    """
    if not text:
        return []
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _render_brief_lines(brief):
    """Flatten a brief dict into [(color, size, text)] for paged scroll.

    The caller (`_draw_brief`) walks this list and stops when it runs
    out of vertical room, painting an arrow indicator if there's more
    below. Mixed sizes (2 for the company name, 1 for everything else)
    use different y-advances — see `_draw_brief`.
    """
    lines = []
    lines.append((_ORANGE, 2, brief.get("name", "(no name)")))

    sub = []
    if brief.get("employees"):
        sub.append(brief["employees"] + " ppl")
    if brief.get("headcount_growth_yoy"):
        sub.append(brief["headcount_growth_yoy"])
    if brief.get("stage"):
        sub.append(brief["stage"])
    if sub:
        for line in _wrap("  ".join(sub), 35):
            lines.append((_GREEN, 1, line))

    if brief.get("hq"):
        lines.append((_GRAY_MID, 1, brief["hq"]))

    if brief.get("summary"):
        lines.append((_CREAM, 1, ""))
        for line in _wrap(brief["summary"], 35):
            lines.append((_CREAM, 1, line))

    pts = brief.get("talking_points") or []
    if pts:
        lines.append((_CREAM, 1, ""))
        lines.append((_ORANGE, 1, "Talking points:"))
        for p in pts:
            wrapped = _wrap("- " + p, 35)
            for j, ln in enumerate(wrapped):
                lines.append((_CREAM, 1, ln if j == 0 else "  " + ln))

    contacts = brief.get("contacts") or []
    if contacts:
        lines.append((_CREAM, 1, ""))
        lines.append((_ORANGE, 1, "Contacts:"))
        for c in contacts:
            head = (c.get("name", "?") or "?") + " - " + (c.get("title", "?") or "?")
            for line in _wrap(head, 35):
                lines.append((_CREAM, 1, line))
            email = c.get("email")
            if email:
                lines.append((_GRAY_MID, 1, "  " + email))

    return lines


def _draw_brief(lines, scroll):
    _LCD.fillScreen(_BLACK)
    _draw_header("Brief")

    y = 26
    bottom = _H - 22
    drawn = 0
    for color, size, text in lines[scroll:]:
        adv = 18 if size == 2 else 12
        if y + adv > bottom:
            break
        _LCD.setTextSize(size)
        _LCD.setTextColor(color, _BLACK)
        _LCD.drawString(text, 6, y)
        y += adv
        drawn += 1

    has_more = (scroll + drawn) < len(lines)

    _LCD.setTextSize(1)
    if scroll > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _W - 14, 24)
    if has_more:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _W - 14, _H - 28)

    _draw_hint("; . scroll  Q back")


def _draw_no_match(label):
    _LCD.fillScreen(_BLACK)
    _draw_header("Briefbot")
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _BLACK)
    msg = "No prospect matched"
    _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, 50)
    if label:
        _LCD.setTextColor(_CREAM, _BLACK)
        s = label[:32]
        _LCD.drawString(s, (_W - _LCD.textWidth(s)) // 2, 70)
    _draw_hint("Q back")


def _draw_status(text, color=_ORANGE, sub=None):
    """Center-of-screen status overlay used during voice cycles."""
    _LCD.fillScreen(_BLACK)
    _draw_header("Briefbot")
    _LCD.setTextSize(2)
    _LCD.setTextColor(color, _BLACK)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, 46)
    if sub:
        _LCD.setTextSize(1)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString(sub, (_W - _LCD.textWidth(sub)) // 2, 78)
    _draw_hint("SPACE stop  (5s max)")


def _flash_error(text):
    """Brief red toast on the hint strip. Caller restores hint after."""
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_RED, _DARK)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, _H - 14)
    time.sleep_ms(1200)


def _do_voice_cycle(kb):
    """Run a voice query end-to-end.

    Returns (brief, err). On success brief is a dict and err is None;
    on failure brief is None and err is a short toast string. The
    caller repaints the picker; this function leaves the screen on
    the last status overlay so a quick toast can land on top.
    """
    if not VOICE_ENABLED:
        return None, "voice off"

    _draw_status("Listening", _ORANGE, "speak now")

    try:
        sock, buf = briefbot_audio.open_stream(_LAPTOP_IP, _LAPTOP_PORT)
    except Exception as e:
        print("briefbot: connect:", e)
        return None, "connect failed"

    start = time.ticks_ms()
    try:
        while True:
            briefbot_audio.stream_chunk(sock, buf)
            kb.tick()
            stop = _intent(kb.get_key()) == "voice"
            timed_out = time.ticks_diff(time.ticks_ms(), start) > 5000
            if stop or timed_out:
                break
    except Exception as e:
        print("briefbot: stream:", e)
        try:
            sock.close()
        except Exception:
            pass
        return None, "stream error"

    _draw_status("Transcribing", _GRAY_MID, "(~2s)")

    try:
        line = briefbot_audio.end_stream(sock)
    except Exception as e:
        print("briefbot: end_stream:", e)
        return None, "no response"

    if not line:
        return None, "empty response"

    try:
        import json
        resp = json.loads(line)
    except Exception as e:
        print("briefbot: json:", e, "line=", line[:80])
        return None, "bad response"

    if not resp.get("ok"):
        return None, (resp.get("err") or "no match")[:24]

    brief = resp.get("brief")
    if not brief:
        return None, "empty brief"

    return brief, None


def _list_prospects():
    """Return [(display_name, key)] pairs from the local fixture.

    The API module exposes `lookup()` but not a full enumeration —
    deliberately, so the device-side picker doesn't pretend to be the
    canonical "today's meetings" list once Phase 3 lands. For V1 we
    reach into the fixture module here; in Phase 3 the laptop pushes
    the real list and this function reads from a cache instead.
    """
    try:
        import briefbot_fixtures
        items = []
        for key, brief in briefbot_fixtures.PROSPECTS.items():
            items.append((brief.get("name", key), key))
        items.sort()
        return items
    except Exception as e:
        print("briefbot: list error:", e)
        return []


def run():
    print("briefbot: run() start, voice=",
          "ON" if VOICE_ENABLED else "off",
          "ip=", _LAPTOP_IP)
    _set_font()

    prospects = _list_prospects()

    state = _S_PICKER
    cursor = 0
    scroll_top = 0
    brief_lines = []
    brief_scroll = 0

    if prospects:
        _draw_picker(prospects, cursor, scroll_top)
    else:
        _LCD.fillScreen(_BLACK)
        _draw_header("Briefbot")
        _LCD.setTextSize(1)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString("No fixtures loaded", 6, 50)
        _draw_hint("Q quit")

    # MatrixKeyboard timing matches the rest of the bundle:
    # 800 ms cold-boot pre-init (matrix IC settle), then 400 ms post-
    # init debounce so the launch keypress doesn't double as a select.
    time.sleep_ms(800)
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    try:
        while True:
            kb.tick()
            intent = _intent(kb.get_key())

            if state == _S_PICKER:
                if not prospects:
                    if intent == "back":
                        return
                elif intent == "up":
                    cursor = (cursor - 1) % len(prospects)
                    if cursor < scroll_top:
                        scroll_top = cursor
                    elif cursor >= scroll_top + _MAX_VISIBLE:
                        scroll_top = max(0, len(prospects) - _MAX_VISIBLE)
                    _draw_picker(prospects, cursor, scroll_top)
                elif intent == "down":
                    cursor = (cursor + 1) % len(prospects)
                    if cursor >= scroll_top + _MAX_VISIBLE:
                        scroll_top = cursor - _MAX_VISIBLE + 1
                    elif cursor < scroll_top:
                        scroll_top = 0
                    _draw_picker(prospects, cursor, scroll_top)
                elif intent == "select":
                    _, key = prospects[cursor]
                    brief = briefbot_api.lookup(key)
                    state = _S_BRIEF
                    brief_scroll = 0
                    if brief is None:
                        brief_lines = []
                        _draw_no_match(key)
                    else:
                        brief_lines = _render_brief_lines(brief)
                        _draw_brief(brief_lines, brief_scroll)
                elif intent == "voice" and VOICE_ENABLED:
                    brief, err = _do_voice_cycle(kb)
                    if brief:
                        brief_lines = _render_brief_lines(brief)
                        brief_scroll = 0
                        state = _S_BRIEF
                        _draw_brief(brief_lines, brief_scroll)
                    else:
                        _draw_picker(prospects, cursor, scroll_top)
                        if err:
                            _flash_error(err)
                            _draw_hint(_PICKER_HINT)
                elif intent == "back":
                    return

            elif state == _S_BRIEF:
                if intent == "up":
                    if brief_lines and brief_scroll > 0:
                        brief_scroll -= 1
                        _draw_brief(brief_lines, brief_scroll)
                elif intent == "down":
                    if brief_lines and brief_scroll < len(brief_lines) - 1:
                        brief_scroll += 1
                        _draw_brief(brief_lines, brief_scroll)
                elif intent == "back":
                    state = _S_PICKER
                    _draw_picker(prospects, cursor, scroll_top)

            time.sleep_ms(40)
    finally:
        try:
            _LCD.fillScreen(_BLACK)
        except Exception as e:
            print("briefbot: clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


run()
