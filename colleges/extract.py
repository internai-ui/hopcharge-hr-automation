"""
colleges/extract.py — Contact-extraction engine (v2).

Pure functions that pull candidate contact details out of raw text or HTML.
No network, no I/O — fast and unit-testable.

v2 fixes over v1
────────────────
BUG 1  Indian landlines not extracted: STD+local format (011-2659xxxx,
       0431-250xxxx) — fixed by accepting 8–12 digit sequences and
       normalising to the local number.
BUG 3  Multiple contacts sharing one generic email (tpo@…) got de-duped
       too aggressively, losing secondary contacts — fixed by relaxing
       de-dup: same email + same name = dup; same email + different name = keep.
BUG 4  Name/email pairs more than ±2 lines apart (separated by dept, city,
       fax lines) were not merged — fixed by widening the scan window to ±5
       and adding a second "scan outward" pass.
BUG 5  Placement-relevant keyword list missed "T&P" and "internship" short
       forms — fixed by adding abbreviations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────
# Patterns
# ──────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_OBFUS_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\(\[]?\s*(?:at|AT)\s*[\)\]]?\s*"
    r"([A-Za-z0-9.\-]+(?:\s*[\(\[]?\s*(?:dot|DOT)\s*[\)\]]?\s*[A-Za-z0-9.\-]+)+)"
)

# ── Phone (v2) ──────────────────────────────────────────────────────
# Indian numbers come in many shapes:
#   Mobile : +91-9876543210 / 9876543210 (10 digits starting 6-9)
#   Landline: 011-26591712 / 0431-2503013 / +91-11-26591712
#             STD code (2-6 digits with leading 0) + local (7-8 digits)
# Strategy: find runs of digits + separators that total 8-13 significant
# digits; normalise to a clean 10-digit mobile or 8-digit local.
_PHONE_CHUNK = re.compile(
    r"(?:\+?91[\-\s]?)?0?(?:\d[\-\s]?){7,11}\d"
)

PLACEMENT_KEYWORDS = [
    # full phrases
    "training and placement", "training & placement", "t&p",
    "placement officer", "placement cell", "placement office",
    "tpo", "career development", "career development centre", "cdc",
    "corporate relations", "internship coordinator", "placement coordinator",
    "dean placement", "head placement", "placement director",
    "placement in-charge", "corporate interface",
    # shorter / abbreviation forms (BUG 5 fix)
    "t & p", "placement", "internship", "campus hiring", "campus recruitment",
    "talent acquisition", "student placement",
]

DESIGNATION_PATTERNS = [
    "training and placement officer", "training & placement officer",
    "placement officer", "placement coordinator", "placement director",
    "dean of placement", "dean placement", "head of placement",
    "head, training and placement", "head, t&p", "head t&p",
    "career development officer", "corporate relations officer",
    "internship coordinator", "assistant placement officer",
    "deputy placement officer", "professor in-charge",
    "professor in charge", "faculty in-charge", "faculty in charge",
    "placement in-charge", "placement in charge", "placement director",
    "training officer",  # common at NITs
]

_NAME_RE = re.compile(
    r"\b((?:Dr|Prof|Mr|Mrs|Ms|Shri|Smt)\.?\s+"
    r"[A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})"
)

_STOP_WORDS = {
    "training", "placement", "career", "coordinator", "officer", "cell",
    "dean", "head", "professor", "faculty", "incharge", "in", "charge",
    "corporate", "relations", "internship", "deputy", "assistant",
    "director", "development", "and", "the",
    # contact-label words that often follow a name on the same/next line
    "email", "e-mail", "phone", "tel", "telephone", "mobile", "mob",
    "fax", "contact", "office", "ext", "extension",
}


# ──────────────────────────────────────────────
# Field-level extractors
# ──────────────────────────────────────────────

def _clean_phone(raw: str) -> str:
    """Normalise: mobile (10-dig, starts 6-9), landline STD+local (10-dig any start), local (8-dig)."""
    digits = re.sub(r"[^\d]", "", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    sig = digits[1:] if digits.startswith("0") and len(digits) in (10, 11) else digits
    if len(sig) == 10 and sig[0] in "6789":  return sig   # mobile
    if len(sig) == 10:                        return sig   # landline
    if len(sig) == 8:                         return sig   # local 8-digit
    return ""


def find_emails(text: str) -> list[str]:
    found: list[str] = []
    for m in _EMAIL_RE.findall(text or ""):
        found.append(m.strip().lower())
    for user, domain in _OBFUS_RE.findall(text or ""):
        domain = re.sub(r"\s*[\(\[]?\s*(?:dot|DOT)\s*[\)\]]?\s*", ".", domain)
        domain = re.sub(r"\s+", "", domain).strip(".")
        candidate = f"{user}@{domain}".lower()
        if _EMAIL_RE.fullmatch(candidate):
            found.append(candidate)
    out, seen = [], set()
    for e in found:
        if e in seen:
            continue
        if any(bad in e for bad in ("@2x", ".png", ".jpg", ".gif", ".svg",
                                     "example.com", "@sentry", "noreply")):
            continue
        seen.add(e)
        out.append(e)
    return out


def find_phones(text: str) -> list[str]:
    """Extract phone numbers including Indian landlines."""
    out, seen = [], set()
    for m in re.finditer(_PHONE_CHUNK, text or ""):
        clean = _clean_phone(m.group(0))
        if clean and clean not in seen:
            seen.add(clean); out.append(clean)
    return out


def find_names(text: str) -> list[str]:
    out, seen = [], set()
    for m in _NAME_RE.findall(text or ""):
        n = re.sub(r"\s+", " ", m).strip()
        # Trim trailing role/stop words
        parts = n.split()
        while len(parts) > 1 and parts[-1].lower().strip(".,&") in _STOP_WORDS:
            parts.pop()
        n = " ".join(parts)
        if n.lower() not in seen and len(n) >= 5:
            seen.add(n.lower())
            out.append(n)
    return out


def find_designation(line: str) -> str:
    low = (line or "").lower()
    for d in DESIGNATION_PATTERNS:
        if d in low:
            return d.title().replace(" And ", " & ").replace("Tpo", "TPO")
    return ""


def looks_placement_relevant(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in PLACEMENT_KEYWORDS)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _email_domain_matches(email: str, website: str) -> bool:
    if not email or not website:
        return False
    dom = email.split("@")[-1].lower()
    site = re.sub(r"^https?://(www\.)?", "", website.lower()).split("/")[0].split(":")[0]
    return dom.endswith(site) or site.endswith(dom)


def _name_at(lines: list[str], anchor: int, window: int = 5) -> str:
    """
    Search outward from `anchor` within `window` lines for a name. (BUG 4 fix)
    Try progressively wider bands: ±2, then ±5.
    """
    for radius in (2, window):
        chunk = " ".join(lines[max(0, anchor - radius): anchor + radius + 1])
        names = find_names(chunk)
        if names:
            return names[0]
    return ""


def _desig_at(lines: list[str], anchor: int, window: int = 5) -> str:
    for radius in (2, window):
        for line in lines[max(0, anchor - radius): anchor + radius + 1]:
            d = find_designation(line)
            if d:
                return d
    return ""


# ──────────────────────────────────────────────
# Candidate assembly
# ──────────────────────────────────────────────

@dataclass
class ContactCandidate:
    name: str = ""
    designation: str = ""
    email: str = ""
    phone: str = ""
    context: str = ""
    source_url: str = ""
    source_type: str = ""
    confidence: int = 0

    def key(self) -> str:
        """
        De-dup key (BUG 3 fix): include name so the same generic email
        (tpo@…) used for multiple people is NOT collapsed into one.
        """
        email_part = (self.email or "").lower()
        name_part  = re.sub(r"\s+", "", self.name.lower())
        if email_part:
            return f"{email_part}__{name_part}" if name_part else email_part
        return (self.phone or "") or name_part


def build_candidates(text: str, *, source_url: str, source_type: str,
                     website: str = "", base_confidence: int = 90) -> list[ContactCandidate]:
    text = text or ""
    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]
    emails = find_emails(text)
    phones = find_phones(text)

    candidates: dict[str, ContactCandidate] = {}

    # ── Pass 1: anchor on emails ──
    for email in emails:
        # Find first line containing this email
        idx = next((i for i, l in enumerate(lines) if email.lower() in l.lower()), None)
        ctx = ""
        if idx is not None:
            ctx = " | ".join(lines[max(0, idx - 3): idx + 4])[:300]

        # Prefer name found ON THE SAME LINE as the email (Bug 3 fix);
        # fall back to the wider window only if the same line has no name.
        same_line_names = find_names(lines[idx]) if idx is not None else []
        name  = same_line_names[0] if same_line_names else _name_at(lines, idx if idx is not None else 0)
        desig = _desig_at(lines, idx if idx is not None else 0)

        conf = base_confidence
        if _email_domain_matches(email, website):
            conf = min(100, conf + 5)
        if not name:
            conf -= 8
        if not desig:
            conf -= 5

        c = ContactCandidate(name=name, designation=desig, email=email,
                             context=ctx, source_url=source_url,
                             source_type=source_type, confidence=max(40, conf))
        k = c.key()
        if k not in candidates:
            candidates[k] = c
        # BUG 3 fix: if same email exists but different name, keep both
        else:
            existing = candidates[k]
            if name and existing.name.lower() != name.lower():
                # give this one a fresh key with the name appended
                new_key = k + "__" + re.sub(r"\s+", "", name.lower())
                candidates[new_key] = c

    # ── Pass 2: designation lines without email ──
    for i, l in enumerate(lines):
        desig = find_designation(l)
        if not desig:
            continue
        name = find_names(l) or find_names(" ".join(lines[max(0, i-2): i+3]))
        if not name:
            continue
        name = name[0]
        # Skip if we already have this person via email
        name_key = re.sub(r"\s+", "", name.lower())
        if any(name_key in k for k in candidates):
            continue
        ph = ""
        win_phones = find_phones(" ".join(lines[max(0, i-2): i+3]))
        if win_phones:
            ph = win_phones[0]
        c = ContactCandidate(
            name=name, designation=desig, phone=ph,
            context=l[:300], source_url=source_url, source_type=source_type,
            confidence=max(40, base_confidence - 25),
        )
        candidates[c.key()] = c

    # ── Pass 3: attach loose phones to candidates missing one ──
    used_phones = {c.phone for c in candidates.values() if c.phone}
    for phone in phones:
        if phone in used_phones:
            continue
        for c in sorted(candidates.values(), key=lambda x: x.confidence, reverse=True):
            if not c.phone:
                c.phone = phone
                used_phones.add(phone)
                break

    return sorted(candidates.values(), key=lambda c: c.confidence, reverse=True)
