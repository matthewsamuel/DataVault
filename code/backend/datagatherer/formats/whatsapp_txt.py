"""
WhatsApp 'Export chat' (.txt, or a .zip with _chat.txt and the media)

The phone writes one line per message in its own locale, so the format differs per phone:

    iOS        [12-01-2024 21:34:56] Anna: Hoi                (Dutch)
               [12/01/2024, 21:34:56] Anna: Hi                (English, UK)
               [1/12/24, 9:34:56 PM] Anna: Hi                 (English, US)
    Android    12-01-2024 21:34 - Anna: Hoi                   (Dutch)
               12/01/2024, 21:34 - Anna: Hi                   (English, UK)
               1/12/24, 9:34 PM - Anna: Hi                    (English, US)

A line that does not start with a date continues the message above. Lines without
'Sender: ' are system messages (someone joined, the encryption notice). Attachments are
'<bijgevoegd: 0001-PHOTO-….jpg>' / '<attached: …>' (iOS) or 'IMG-….jpg (bestand bijgevoegd)'
/ '(file attached)' (Android); without media the export says 'afbeelding weggelaten' /
'<Media weggelaten>' / 'image omitted'.

Day and month order: a first number above 12 means day first, a second one above 12
means month first; otherwise AM/PM means month first (US) and anything else day first.
The times are local (no zone in the file): the caller says in which zone to read them.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_INVISIBLE = "‎‏‪‬﻿"
_DATE = r"(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})"
_TIME = r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?(?:\s*([AaPp])\.?\s*[Mm]\.?)?"
# Between date and time: a comma or a space, or a small word ('01/06/2024 à 21:34' in French)
_SEP = r"(?:,?\s+(?:à|um|alle|a las|om)\s+|,?\s+)"
IOS_LINE = re.compile(r"^\[" + _DATE + _SEP + _TIME + r"\]\s(.*)$")
ANDROID_LINE = re.compile(r"^" + _DATE + _SEP + _TIME + r"\s[-–]\s(.*)$")
ATTACHED = re.compile(r"<(?:attached|bijgevoegd|anexo|adjunto|pièce jointe|angehängt):\s*([^>]+)>", re.I)
ANDROID_ATTACHED = re.compile(r"^(.+?\.\w{2,5})\s\((?:file attached|bestand bijgevoegd)\)", re.I)
OMITTED = re.compile(r"^(?:<?(?:media omitted|media weggelaten)>?|(?:image|video|audio|sticker|GIF|document|"
                     r"afbeelding|audio|sticker|document) (?:omitted|weggelaten))$", re.I)
SENDER = re.compile(r"^([^:]{1,80}?):\s(.*)$", re.S)

NAME_PATTERNS = (
    re.compile(r"^WhatsApp[ -]?Chat (?:with|met|-|–)\s*(.+?)(?:\.txt|\.zip)?$", re.I),
    re.compile(r"^WhatsApp[ -]chat met (.+?)(?:\.txt|\.zip)?$", re.I),
)


def chat_name(filename: str) -> Optional[str]:
    """'WhatsApp Chat - Anna.zip' / 'WhatsApp-chat met Anna.txt' / 'WhatsApp Chat with Anna.txt' -> 'Anna'."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    for pattern in NAME_PATTERNS:
        m = pattern.match(base)
        if m:
            name = re.sub(r"\s*\(\d+\)$", "", m.group(1).strip())   # 'Anna (1).zip' from a second download
            return name or None
    return None


def looks_like_chat(text: str) -> bool:
    """True when the first lines are WhatsApp export lines."""
    lines = [l.strip(_INVISIBLE + " ") for l in text.splitlines()[:20] if l.strip()]
    return sum(1 for l in lines if IOS_LINE.match(l) or ANDROID_LINE.match(l)) >= max(1, min(2, len(lines)))


def _split(text: str) -> Tuple[List[Tuple[tuple, str]], str]:
    """[(date-time groups, rest)] per message and the platform ('ios' or 'android')."""
    entries: List[Tuple[tuple, str]] = []
    platform = None
    for raw in text.splitlines():
        line = raw.strip(_INVISIBLE).rstrip("\r")
        clean = line.lstrip(_INVISIBLE + " ")
        m = IOS_LINE.match(clean)
        if m and platform in (None, "ios"):
            platform = "ios"
        else:
            m = ANDROID_LINE.match(clean) if platform in (None, "android") else None
            if m:
                platform = "android"
        if m:
            entries.append((m.groups()[:7], m.group(8)))
        elif entries:
            groups, rest = entries[-1]
            entries[-1] = (groups, rest + "\n" + line)
    return entries, platform or "ios"


def _order(entries: List[Tuple[tuple, str]]) -> str:
    """'ymd', 'dmy' or 'mdy' for the whole file."""
    firsts = [int(g[0]) for g, _ in entries]
    seconds = [int(g[1]) for g, _ in entries]
    if any(len(g[0]) == 4 for g, _ in entries):
        return "ymd"
    if any(v > 12 for v in firsts):
        return "dmy"
    if any(v > 12 for v in seconds):
        return "mdy"
    return "mdy" if any(g[6] for g, _ in entries) else "dmy"


def _when(groups: tuple, order: str, zone: ZoneInfo) -> Optional[datetime]:
    a, b, c, hh, mm, ss, ampm = groups[0], groups[1], groups[2], groups[3], groups[4], groups[5], groups[6]
    if order == "ymd":
        y, mo, d = int(a), int(b), int(c)
    elif order == "dmy":
        d, mo, y = int(a), int(b), int(c)
    else:
        mo, d, y = int(a), int(b), int(c)
    if y < 100:
        y += 2000
    hour = int(hh)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "p" else 0)
    try:
        return datetime(y, mo, d, hour, int(mm), int(ss or 0), tzinfo=zone)
    except ValueError:
        return None


def parse(text: str, zone: ZoneInfo) -> Dict[str, Any]:
    """
    {platform, precision ('s' or 'min'), messages[{ts_ms, sender, text, kind, attachments,
    n}]}; n numbers identical (time, sender, text) messages so repeats stay apart.
    """
    entries, platform = _split(text)
    groups = entries
    order = _order(groups)
    precision = "s" if any(g[5] for g, _ in groups) else "min"
    messages = []
    seen: Dict[tuple, int] = {}
    for g, rest in groups:
        when = _when(g, order, zone)
        if when is None:
            continue
        m = SENDER.match(rest)
        if m:
            sender, body = m.group(1).strip(_INVISIBLE + " "), m.group(2)
            kind = "text"
        else:
            sender, body, kind = None, rest, "system"
        body = body.strip(_INVISIBLE + " \n")
        attachments = []
        if kind == "text":
            hit = ATTACHED.search(body) or ANDROID_ATTACHED.match(body)
            if hit:
                attachments.append({"name": hit.group(1).strip()})
                body = (body[:hit.start()] + body[hit.end():]).strip(_INVISIBLE + " \n")
                kind = "media" if not body else "text"
            elif OMITTED.match(body.strip("‎ ")):
                body, kind = "", "media"
        ts_ms = int(when.timestamp() * 1000)
        ident = (ts_ms, sender, body)
        seen[ident] = seen.get(ident, 0) + 1
        messages.append({"ts_ms": ts_ms, "sender": sender, "text": body or None, "kind": kind,
                         "attachments": attachments, "n": seen[ident]})
    return {"platform": platform, "precision": precision, "messages": messages}
