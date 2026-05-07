"""Briefbot for the M5 Cardputer-Adv.

Self-contained prospect lookup against the 1362-company icp_ranker
dataset baked into /flash/. No WiFi, no laptop companion, no APIs —
pure local data.

UX
  type letters    filter the company list (substring match)
  ; , w           scroll cursor up
  . / s           scroll cursor down
  Enter           open brief for highlighted company
  Backspace       delete last filter char
  ESC / Q         clear filter if non-empty, else quit to launcher

The brief view scrolls long entries with the same up/down keys and
returns to the picker on ESC.

Layout matches the bundle's three-zone chrome — DARK header band with
ORANGE hairline at y=20, content area, hint strip at the bottom. Font
is DejaVu9; centering goes through textWidth() because it's
proportional. Exit is via machine.reset() (UIFlow has no return-to-
launcher API on this build).
"""

import sys

for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

import M5
import machine
from hardware import MatrixKeyboard

import briefbot_api


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

# Picker layout: header / filter row / list / hint
_FILTER_Y = 24
_FILTER_H = 14
_FIRST_ROW_Y = 42
_ROW_H = 16
_MAX_VISIBLE = 5

_S_PICKER = 0
_S_BRIEF = 1


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        print("briefbot: setFont fallback:", e)


# ---------- key intent ----------

def _intent(k):
    """Map a MatrixKeyboard return to one of:
      ('select', None) | ('back', None) | ('backspace', None)
      ('up', None)     | ('down', None)
      ('type', char)   | (None, None)

    Cardputer-Adv arrow keys silk-screen as ; , . / so those four are
    treated as scroll, NOT typed. Everything else printable
    (letters/digits/space/hyphen) becomes filter input. Enter is 0x0A
    on this firmware — accept 0x0D too for forward-compat. Backspace
    can report as 0x08 (BS) or 0x7F (DEL); accept both.
    """
    if k is None:
        return (None, None)
    if isinstance(k, int):
        if k in (0x0A, 0x0D): return ("select", None)
        if k == 0x1B:         return ("back", None)
        if k in (0x08, 0x7F): return ("backspace", None)
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return (None, None)
    if not isinstance(k, str) or not k:
        return (None, None)
    if k in ("\r", "\n"):     return ("select", None)
    if k == "\x1b":           return ("back", None)
    if k in ("\b", "\x7f"):   return ("backspace", None)
    if k in (";", ","):       return ("up", None)
    if k in (".", "/"):       return ("down", None)
    c = k[0]
    if 0x20 <= ord(c) <= 0x7E:
        return ("type", c)
    return (None, None)


# ---------- chrome ----------

def _draw_header(title, count_text=None):
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString(title, 6, 5)
    if count_text:
        _LCD.setTextColor(_GRAY_MID, _DARK)
        _LCD.drawString(count_text,
                        _W - _LCD.textWidth(count_text) - 6, 5)


def _draw_hint(text):
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, _H - 14)


# ---------- picker ----------

def _draw_filter_row(filter_text):
    _LCD.fillRect(0, _FILTER_Y, _W, _FILTER_H, _BLACK)
    _LCD.setTextSize(1)
    if filter_text:
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString(">", 6, _FILTER_Y + 2)
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString(filter_text + "_", 16, _FILTER_Y + 2)
    else:
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString("type to filter", 6, _FILTER_Y + 2)


def _truncate_to_width(text, max_w):
    if _LCD.textWidth(text) <= max_w:
        return text
    while len(text) > 4 and _LCD.textWidth(text + "...") > max_w:
        text = text[:-1]
    return text + "..."


def _draw_picker(filtered, cursor, scroll_top, total, filter_text):
    _LCD.fillScreen(_BLACK)
    _draw_header("Briefbot", "{}/{}".format(len(filtered), total))
    _draw_filter_row(filter_text)

    if not filtered:
        _LCD.setTextSize(1)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        msg = "no matches"
        _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2,
                        _FIRST_ROW_Y + 12)
        _draw_hint("backspace to edit  Q quit")
        return

    visible = filtered[scroll_top:scroll_top + _MAX_VISIBLE]
    y = _FIRST_ROW_Y
    _LCD.setTextSize(1)
    for i, (display, _idx) in enumerate(visible):
        abs_i = scroll_top + i
        if abs_i == cursor:
            _LCD.fillRect(4, y - 2, _W - 8, _ROW_H - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(_truncate_to_width(display, _W - 18), 10, y)
        y += _ROW_H

    if scroll_top > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _W - 10, _FIRST_ROW_Y - 2)
    if scroll_top + _MAX_VISIBLE < len(filtered):
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _W - 10, y - _ROW_H + 6)

    _draw_hint("; .  Enter pick  Q back")


def _filter(all_names, q):
    """Return list of (display_name, original_index) matching `q`.
    Empty q returns everything; non-empty does substring (case-insensitive).
    """
    if not q:
        return [(n, i) for i, n in enumerate(all_names)]
    ql = q.lower()
    return [(n, i) for i, n in enumerate(all_names) if ql in n.lower()]


# ---------- brief ----------

def _wrap(text, max_chars):
    if not text:
        return []
    words = text.split()
    out = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def _render_brief_lines(brief):
    """Flatten brief dict -> [(color, size, text)] for paged scroll."""
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
        lines.append((_ORANGE, 1, "Notes:"))
        for p in pts:
            for j, ln in enumerate(_wrap("- " + p, 35)):
                lines.append((_CREAM, 1, ln if j == 0 else "  " + ln))

    contacts = brief.get("contacts") or []
    if contacts:
        lines.append((_CREAM, 1, ""))
        lines.append((_ORANGE, 1, "Owner:"))
        for c in contacts:
            head = (c.get("name", "?") or "?")
            t = (c.get("title", "") or "").strip()
            if t:
                head = head + " - " + t
            for line in _wrap(head, 35):
                lines.append((_CREAM, 1, line))

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


# ---------- main loop ----------

def run():
    print("briefbot: run() start")
    _set_font()

    all_names = briefbot_api.list_names()
    print("briefbot: loaded", len(all_names), "names")

    state = _S_PICKER
    filter_text = ""
    filtered = _filter(all_names, filter_text)
    cursor = 0
    scroll_top = 0
    brief_lines = []
    brief_scroll = 0

    if not all_names:
        _LCD.fillScreen(_BLACK)
        _draw_header("Briefbot")
        _LCD.setTextColor(_RED, _BLACK)
        _LCD.drawString("data file missing", 6, 50)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString("push briefbot_index.txt", 6, 70)
        _LCD.drawString("and briefbot_data.jsonl", 6, 84)
        _draw_hint("Q quit")
    else:
        _draw_picker(filtered, cursor, scroll_top, len(all_names), filter_text)

    time.sleep_ms(800)
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    try:
        while True:
            kb.tick()
            intent, ch = _intent(kb.get_key())

            if state == _S_PICKER:
                if not filtered and intent != "back" and intent != "backspace" and intent != "type":
                    if intent is None:
                        time.sleep_ms(40)
                        continue
                    # Allow only edit/back when no matches
                    pass

                if intent == "type" and ch is not None:
                    filter_text += ch
                    filtered = _filter(all_names, filter_text)
                    cursor = 0
                    scroll_top = 0
                    _draw_picker(filtered, cursor, scroll_top,
                                 len(all_names), filter_text)
                elif intent == "backspace":
                    if filter_text:
                        filter_text = filter_text[:-1]
                        filtered = _filter(all_names, filter_text)
                        cursor = 0
                        scroll_top = 0
                        _draw_picker(filtered, cursor, scroll_top,
                                     len(all_names), filter_text)
                elif intent == "up":
                    if filtered:
                        cursor = (cursor - 1) % len(filtered)
                        if cursor < scroll_top:
                            scroll_top = cursor
                        elif cursor >= scroll_top + _MAX_VISIBLE:
                            scroll_top = max(0, len(filtered) - _MAX_VISIBLE)
                        _draw_picker(filtered, cursor, scroll_top,
                                     len(all_names), filter_text)
                elif intent == "down":
                    if filtered:
                        cursor = (cursor + 1) % len(filtered)
                        if cursor >= scroll_top + _MAX_VISIBLE:
                            scroll_top = cursor - _MAX_VISIBLE + 1
                        elif cursor < scroll_top:
                            scroll_top = 0
                        _draw_picker(filtered, cursor, scroll_top,
                                     len(all_names), filter_text)
                elif intent == "select":
                    if filtered:
                        _, src_idx = filtered[cursor]
                        # Loading hint while we read the (lazy) brief
                        _draw_hint("loading...")
                        brief = briefbot_api.get(src_idx)
                        if brief is None:
                            _draw_hint("load failed")
                            time.sleep_ms(900)
                            _draw_picker(filtered, cursor, scroll_top,
                                         len(all_names), filter_text)
                        else:
                            brief_lines = _render_brief_lines(brief)
                            brief_scroll = 0
                            state = _S_BRIEF
                            _draw_brief(brief_lines, brief_scroll)
                elif intent == "back":
                    if filter_text:
                        # First press clears filter
                        filter_text = ""
                        filtered = _filter(all_names, filter_text)
                        cursor = 0
                        scroll_top = 0
                        _draw_picker(filtered, cursor, scroll_top,
                                     len(all_names), filter_text)
                    else:
                        return

            elif state == _S_BRIEF:
                if intent == "up":
                    if brief_scroll > 0:
                        brief_scroll -= 1
                        _draw_brief(brief_lines, brief_scroll)
                elif intent == "down":
                    if brief_scroll < len(brief_lines) - 1:
                        brief_scroll += 1
                        _draw_brief(brief_lines, brief_scroll)
                elif intent == "back":
                    state = _S_PICKER
                    _draw_picker(filtered, cursor, scroll_top,
                                 len(all_names), filter_text)

            time.sleep_ms(40)
    finally:
        try:
            _LCD.fillScreen(_BLACK)
        except Exception as e:
            print("briefbot: clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


run()
