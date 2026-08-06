"""
parser.py — Parses raw resume text into a structured CandidateRecord.

Flow:
  1. Extract contact info (email, phone, LinkedIn) with regex.
  2. Detect section headings and split text into labelled chunks.
  3. Parse each chunk with specialised helpers.
  4. Attempt name extraction via spaCy NER + heuristics.
  5. Assign confidence scores per field.
"""

import re
import logging
from typing import Optional

from config import (
    EMAIL_PATTERN,
    PHONE_PATTERN,
    LINKEDIN_PATTERN,
    SECTION_HEADINGS,
    SPACY_MODEL,
)
from schemas import (
    CandidateRecord,
    PersonalDetails,
    WorkEntry,
    EducationEntry,
)

logger = logging.getLogger(__name__)

# ─── Lazy-load spaCy to keep import fast ──────────────────────────
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load(SPACY_MODEL)
        except Exception as exc:
            logger.warning("spaCy model '%s' unavailable (%s); name extraction will rely on heuristics.", SPACY_MODEL, exc)
            _nlp = False  # sentinel: tried and failed
    return _nlp if _nlp is not False else None


# ═══════════════════════════════════════════════
# CONTACT EXTRACTION
# ═══════════════════════════════════════════════

def _extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(EMAIL_PATTERN, text)))


# A bare "2020-2021"-style year range is numeric and dash-separated, so it
# satisfies PHONE_PATTERN's shape too — this is the one false-positive
# source common enough in resumes (work-experience/education date ranges)
# to guard against explicitly, rather than trying to broadly distinguish
# "looks like a phone" from "looks like two years" in the pattern itself.
_YEAR_RANGE_RE = re.compile(r"^\(?(?:19|20)\d{2}\)?\s*[\s\-–—]\s*\(?(?:19|20)\d{2}\)?$")


def _extract_phones(text: str) -> list[str]:
    raw = re.findall(PHONE_PATTERN, text)
    cleaned = []
    for p in raw:
        candidate = p.strip()
        if _YEAR_RANGE_RE.match(candidate):
            continue
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15:
            cleaned.append(candidate)
    return list(dict.fromkeys(cleaned))


def _extract_linkedin(text: str) -> str:
    m = re.search(LINKEDIN_PATTERN, text, re.IGNORECASE)
    return m.group(0).strip() if m else ""


# ═══════════════════════════════════════════════
# NAME EXTRACTION
# ═══════════════════════════════════════════════

# Words that NEVER appear in a person's name line — section headings,
# contact labels, resume boilerplate, location indicators.
_NAME_BLOCKLIST_WORDS = {
    # Section headings & resume boilerplate
    "resume", "curriculum", "vitae", "cv", "biodata", "profile",
    "objective", "summary", "contact", "address", "details",
    "experience", "education", "skills", "qualification", "qualifications",
    "work", "professional", "employment", "history", "career",
    "personal", "information", "declaration", "reference", "references",
    "hobbies", "interests", "achievements", "awards", "certifications",
    "projects", "internship", "internships", "training", "languages",
    "competencies", "expertise", "strengths", "overview",
    # Contact labels (often on same line as name in poorly formatted PDFs)
    "phone", "mobile", "tel", "telephone", "email", "e-mail", "mailto",
    "whatsapp", "fax",
    # Content words that show up in garbled extraction
    "operations", "marketing", "management", "development", "retention",
    "recruitment", "resources", "human", "services", "limited", "pvt",
    "ltd", "inc", "company", "corporation", "organization",
}

# Indian cities, states, and common locality words that get mis-identified
# as names because they are 2–3 title-case words.
_LOCATION_WORDS = {
    # Major Indian cities
    "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai",
    "kolkata", "pune", "ahmedabad", "jaipur", "lucknow", "noida",
    "gurgaon", "gurugram", "chandigarh", "kochi", "indore", "bhopal",
    "patna", "coimbatore", "nagpur", "surat", "visakhapatnam", "vadodara",
    "thiruvananthapuram", "ranchi", "dehradun", "guwahati", "raipur",
    "bhubaneswar", "mangalore", "mysore", "jodhpur", "amritsar", "kanpur",
    "varanasi", "agra", "gorakhpur", "meerut", "faridabad", "ghaziabad",
    "jalandhar", "ludhiana", "allahabad", "prayagraj",
    # Indian states & UTs
    "haryana", "maharashtra", "karnataka", "telangana", "rajasthan",
    "gujarat", "kerala", "punjab", "uttarakhand", "jharkhand", "odisha",
    "bihar", "assam", "chhattisgarh", "madhya", "pradesh", "uttar",
    "andhra", "tamil", "nadu", "bengal", "west",
    # Common locality / address words
    "nagar", "vihar", "colony", "sector", "block", "road", "street",
    "lane", "enclave", "extension", "phase", "park", "garden", "gardens",
    "town", "township", "city", "metro", "district", "transport",
    "market", "bazaar", "chowk", "gate", "marg", "puram", "pur",
    "garh", "abad", "ganj",
}


def _strip_contact_labels(line: str) -> str:
    """
    Remove trailing contact-label fragments that PDF extraction sometimes
    appends to the name line.
    
    E.g. "Shoaib Akhter Phone" → "Shoaib Akhter"
         "Ravi Kumar Email:" → "Ravi Kumar"
         "Anita Phone: 98765" → "Anita"
    """
    cleaned = re.split(
        r"\b(?:phone|mobile|mob|tel|telephone|email|e-mail|contact|address|whatsapp|fax)\b",
        line, maxsplit=1, flags=re.IGNORECASE
    )[0].strip(" \t:|-–—·•")
    return cleaned


def _is_location_line(line: str) -> bool:
    """Return True if the majority of words in the line are known location words."""
    words = re.findall(r"[a-zA-Z]+", line.lower())
    if not words:
        return False
    location_count = sum(1 for w in words if w in _LOCATION_WORDS)
    return location_count >= len(words) * 0.6


def _is_section_heading(line: str) -> bool:
    """Return True if the line matches a known resume section heading."""
    stripped = line.strip()
    for pat, _ in SECTION_HEADINGS:
        # pat is itself alternation-heavy (e.g. "a|b|c") and NOT wrapped in
        # its own group, so it must be grouped here -- otherwise `^`/`$`
        # only bind to the first/last alternative of pat, not to each one,
        # and this ends up matching far more loosely than intended.
        if re.match(r"^\s*(?:" + pat + r")\s*[:\-–—]?\s*$", stripped, re.IGNORECASE):
            return True
    return False


def _is_obviously_not_a_name(candidate: str) -> bool:
    """
    Fast rejection of lines that are clearly content fragments, not names.
    Catches things like 'Retention), Operations, HR, Marketing,'.
    """
    # Lines with parentheses, semicolons, or multiple commas → content, not name
    if re.search(r"[();{}]", candidate):
        return True
    if candidate.count(",") >= 2:
        return True
    # Lines starting with a bullet, dash-list, or lowercase word
    if re.match(r"^[\-•●▪◦➤►★✓✔]", candidate):
        return True
    if candidate and candidate[0].islower():
        return True
    # Very long lines are not names
    if len(candidate) > 60:
        return True
    return False


# ─── Email-based helpers ──────────────────────────────────────────

def _email_to_slug(email: str) -> str:
    """
    Turn 'himanshikharinta@gmail.com' → 'himanshikharinta'
    Turn 'ravi.kumar_123@yahoo.co.in' → 'ravikumar'
    
    Returns the local part with digits and separators removed — a
    slug that name words can be matched against.
    """
    local = email.split("@")[0].lower()
    local = re.sub(r"[\d._\-+]+", "", local)  # strip digits & separators
    return local


def _email_to_tokens(email: str) -> list[str]:
    """
    Split the local part on common separators to get explicit name tokens.
    'ravi.kumar_123@gmail.com' → ['ravi', 'kumar']
    'akhtershoaib517@gmail.com' → ['akhtershoaib']  (no separator → single slug)
    """
    local = email.split("@")[0].lower()
    local = re.sub(r"\d+", "", local)  # strip digits
    tokens = re.split(r"[._\-+]+", local)
    return [t for t in tokens if len(t) >= 2]


def _name_matches_email(name: str, email: str) -> bool:
    """
    Check whether a candidate name is consistent with the email address.
    
    Uses TWO matching strategies:
      A. Substring match: each name word appears inside the email slug.
         Catches 'Himanshi Kharinta' vs 'himanshikharinta@gmail.com'.
      B. Token match: email tokens (split on . _ -) match name words.
         Catches 'Ravi Kumar' vs 'ravi.kumar@gmail.com'.
    
    Returns True if at least one name word (≥3 chars) passes either strategy.
    """
    if not email or not name:
        return False
    slug = _email_to_slug(email)
    tokens = _email_to_tokens(email)
    name_words = [w.lower() for w in name.split() if len(w) >= 3]
    if not name_words:
        return False

    # Strategy A: substring in the concatenated slug
    substr_hits = sum(1 for w in name_words if w in slug)
    if substr_hits >= min(2, len(name_words)):
        return True

    # Strategy B: exact token match
    token_set = set(tokens)
    token_hits = sum(1 for w in name_words if w in token_set)
    if token_hits >= min(2, len(name_words)):
        return True

    return False


def _extract_name_from_email(text: str, email: str) -> str:
    """
    Email-guided name search: scan the first 25 lines for a line whose
    words overlap with the email local part.  The email itself is never
    returned as the name — it only GUIDES which line to pick.
    
    This is the most reliable method for messy PDFs because emails are
    almost always extracted correctly, and people almost always put their
    own name in their email address.
    """
    slug = _email_to_slug(email)
    if len(slug) < 4:
        return ""

    best_name = ""
    best_score = 0

    for line in text.split("\n")[:25]:
        candidate = _strip_contact_labels(line.strip())
        if not candidate or len(candidate) < 3:
            continue
        # Quick rejections
        if re.search(r"@|http|www\.", candidate, re.IGNORECASE):
            continue
        if re.search(r"\d{3,}", candidate):
            continue
        if _is_obviously_not_a_name(candidate):
            continue
        if _is_location_line(candidate):
            continue
        if _is_section_heading(candidate):
            continue

        # Extract alphabetic words only
        words = re.findall(r"[A-Za-z]{2,}", candidate)
        if not (1 <= len(words) <= 5):
            continue

        # Check for blocklisted words
        if any(w.lower() in _NAME_BLOCKLIST_WORDS for w in words):
            continue

        # Score: how many name-length words (≥3 chars) appear in the email slug
        name_words = [w.lower() for w in words if len(w) >= 3]
        if not name_words:
            continue
        hits = sum(1 for w in name_words if w in slug)

        # Require at least 1 hit for single-word names, 2 for multi-word
        min_hits = min(2, len(name_words))
        if hits >= min_hits and hits > best_score:
            # Re-assemble the candidate from the cleaned words (title-case)
            best_name = " ".join(w.title() if w.isupper() or w.islower() else w for w in words)
            best_score = hits

    return best_name


# ─── spaCy-based name extraction ──────────────────────────────────

def _extract_name_spacy(text: str) -> str:
    """Use spaCy NER on the first few lines (name is almost always at the top)."""
    nlp = _get_nlp()
    if nlp is None:
        return ""
    header = "\n".join(text.split("\n")[:10])
    doc = nlp(header)
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            name = ent.text.strip()
            if len(name.split()) >= 2 and not re.search(r"\d", name):
                if not _is_location_line(name):
                    return name
    return ""


# ─── Heuristic name extraction (last resort) ─────────────────────

def _extract_name_heuristic(text: str) -> str:
    """
    Heuristic: scan the first 20 non-empty lines for the most likely name.
    
    A valid name line must:
      - Be 2–5 title-case words (or ALL-CAPS, which we title-case first)
      - Contain NO digits of 3+ characters
      - Contain NO email/URL patterns
      - Contain NO blocklisted words (section headings, contact labels, content)
      - NOT be a location, section heading, or obvious content fragment
    """
    for line in text.split("\n")[:20]:
        line = line.strip()
        if not line or len(line) < 4:
            continue

        # Strip contact labels FIRST
        candidate = _strip_contact_labels(line)
        if not candidate or len(candidate) < 4:
            continue

        # Quick rejections on the cleaned candidate
        if re.search(r"@|http|www\.|\.com|\.in|\.org|linkedin", candidate, re.IGNORECASE):
            continue
        if re.search(r"\d{3,}", candidate):
            continue
        if _is_obviously_not_a_name(candidate):
            continue

        # Normalise ALL-CAPS
        if candidate.isupper():
            candidate_check = candidate.title()
        else:
            candidate_check = candidate

        words = candidate_check.split()
        if not (2 <= len(words) <= 5):
            continue
        if any(w.lower() in _NAME_BLOCKLIST_WORDS for w in words):
            continue
        if _is_location_line(candidate):
            continue
        if _is_section_heading(candidate):
            continue

        connectors = {"de", "van", "von", "al", "el", "bin", "di", "le", "la", "du", "dos"}
        if all(w[0].isupper() or w.lower() in connectors for w in words):
            return candidate

    return ""


# ─── Final name sanitisation (belt-and-suspenders) ────────────────

# Contact-label words that must NEVER appear in the final name string,
# regardless of how they got there.  Checked as whole words only so
# names like "Phong" or "Emilia" are safe.
_NAME_POISON_WORDS = {
    "phone", "mobile", "mob", "tel", "telephone",
    "email", "e-mail", "mailto",
    "fax", "whatsapp", "contact", "address",
    "number", "no.", "no",
    "resume", "cv", "biodata", "curriculum", "vitae",
}

# Build a single compiled pattern for speed
_POISON_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _NAME_POISON_WORDS) + r")\b",
    re.IGNORECASE,
)


def _sanitize_name(name: str) -> str:
    """
    Last-resort cleanup applied to every name before it leaves the parser.
    
    Removes any stray contact-label words ('Phone', 'Mobile', 'Email', etc.)
    that PDF extraction sometimes attaches to the name — whether on the same
    line, appended after a newline, or wedged between name parts.
    
    Also collapses extra whitespace and strips trailing punctuation.
    """
    if not name:
        return ""

    # Replace any newlines / tabs with a space first (handles multiline cells)
    cleaned = re.sub(r"[\n\r\t]+", " ", name)

    # Remove poison words (whole-word match only)
    cleaned = _POISON_RE.sub("", cleaned)

    # Remove any stray punctuation left behind after poison-word removal
    # (e.g. "Priya Telephone: Deshpande" → "Priya : Deshpande" → "Priya Deshpande")
    cleaned = re.sub(r"\s*[:;,|/\\]+\s*", " ", cleaned)

    # Collapse whitespace and strip junk from edges
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t:|-–—·•.,;")

    return cleaned


# ─── Main name extraction orchestrator ────────────────────────────

def _extract_name(text: str, email: str = "") -> tuple[str, float]:
    """
    Three-tier name extraction with email cross-validation.
    
    Priority order:
      1. spaCy NER (validated against email if available)
      2. Email-guided search (most reliable for messy PDFs)
      3. Pure heuristic (validated against email if available)
      4. Unvalidated heuristic (lowest confidence)
    
    The email address is NEVER used as the name itself — it only guides
    which text line to trust.  If email is unavailable, tier 2 is skipped
    and tier 3/4 run without validation.
    
    Every name passes through _sanitize_name() before being returned —
    this strips any residual contact-label words like 'Phone' or 'Mobile'.
    """
    # ── Tier 1: spaCy NER ──
    spacy_name = _extract_name_spacy(text)
    if spacy_name:
        if email and _name_matches_email(spacy_name, email):
            return _sanitize_name(spacy_name), 0.95
        if not email:
            return _sanitize_name(spacy_name), 0.80

    # ── Tier 2: Email-guided search (only if email is available) ──
    if email:
        email_name = _extract_name_from_email(text, email)
        if email_name:
            return _sanitize_name(email_name), 0.90

    # ── Tier 3: Heuristic with email validation ──
    heuristic_name = _extract_name_heuristic(text)
    if heuristic_name:
        if email and _name_matches_email(heuristic_name, email):
            return _sanitize_name(heuristic_name), 0.85
        if not email:
            return _sanitize_name(heuristic_name), 0.60

    # ── Tier 4: spaCy found something but email didn't confirm ──
    if spacy_name:
        return _sanitize_name(spacy_name), 0.50

    # ── Tier 5: Heuristic found something email didn't confirm ──
    if heuristic_name:
        return _sanitize_name(heuristic_name), 0.40

    return "", 0.0


# ═══════════════════════════════════════════════
# LOCATION EXTRACTION
# ═══════════════════════════════════════════════

_CITY_PATTERN = r"(?:Mumbai|Delhi|Bangalore|Bengaluru|Hyderabad|Chennai|Kolkata|Pune|Ahmedabad|Jaipur|Lucknow|Noida|Gurgaon|Gurugram|Chandigarh|Kochi|Indore|Bhopal|Patna|Coimbatore|Nagpur|Surat|Visakhapatnam|Vadodara|Thiruvananthapuram|Ranchi|Dehradun|New Delhi|Guwahati|Raipur|Bhubaneswar|Mangalore|Mysore|Jodhpur|Amritsar|Kanpur|Varanasi|Agra)"


def _extract_location(text: str) -> tuple[str, float]:
    """Check the curated Indian-city list first (more precise for this
    app's target audience than general-purpose NER, and immune to spaCy
    occasionally mistagging a city as something else entirely -- e.g. it
    reads "Bangalore" as a PERSON, not a GPE, often enough that leading
    with spaCy would silently drop a perfectly identifiable city). Falls
    back to spaCy's GPE/LOC entities for locations outside that list
    (international cities, etc.)."""
    header = "\n".join(text.split("\n")[:15])
    m = re.search(_CITY_PATTERN, header, re.IGNORECASE)
    if m:
        return m.group(0).strip(), 0.85
    nlp = _get_nlp()
    if nlp:
        doc = nlp(header)
        for ent in doc.ents:
            if ent.label_ in ("GPE", "LOC"):
                return ent.text.strip(), 0.70
    return "", 0.0


# ═══════════════════════════════════════════════
# SECTION SPLITTING
# ═══════════════════════════════════════════════

def _split_sections(text: str) -> dict[str, str]:
    """
    Walk through lines and detect section headings.
    Returns {section_key: content_text}.
    'header' captures everything before the first recognised heading.
    """
    # pat is alternation-heavy (e.g. "a|b|c") and not wrapped in its own
    # group, so it's grouped here with (?:...) -- otherwise `^`/`$` only
    # bind to pat's first/last alternative instead of each one, matching
    # far more loosely than intended (e.g. a bare "^...skills" with no end
    # anchor at all would match "SKILLS: Python, Django, ..." as a whole,
    # silently swallowing everything after the colon).
    # Heading alone on its line, e.g. "SKILLS" or "Skills:".
    compiled = [(re.compile(r"^\s*(?:" + pat + r")\s*[:\-–—]?\s*$", re.IGNORECASE), key) for pat, key in SECTION_HEADINGS]
    # Heading immediately followed by its content on the SAME line, e.g.
    # "Skills: Python, Django, PostgreSQL" — very common in real resumes.
    # Requires an explicit separator (: - –) so a sentence that merely
    # contains a heading word (e.g. "4 years experience.") can't match —
    # the separator only appears after a deliberate label.
    compiled_inline = [
        (re.compile(r"^\s*(?:" + pat + r")\s*[:\-–—]\s*(\S.*)$", re.IGNORECASE), key)
        for pat, key in SECTION_HEADINGS
    ]

    sections: dict[str, list[str]] = {"header": []}
    current = "header"

    for line in text.split("\n"):
        matched = False
        stripped = line.strip()
        if stripped:
            for regex, key in compiled:
                if regex.match(stripped):
                    current = key
                    sections.setdefault(current, [])
                    matched = True
                    break
            if not matched:
                # Check if this line is essentially a heading (short, upper-case)
                if len(stripped) < 40 and stripped.isupper():
                    for regex, key in compiled:
                        if regex.match(stripped.title()):
                            current = key
                            sections.setdefault(current, [])
                            matched = True
                            break
            if not matched:
                for regex, key in compiled_inline:
                    m = regex.match(stripped)
                    if m:
                        current = key
                        sections.setdefault(current, [])
                        sections[current].append(m.group(1))
                        matched = True
                        break
        if not matched:
            sections.setdefault(current, [])
            sections[current].append(line)

    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


# ═══════════════════════════════════════════════
# PER-SECTION PARSERS
# ═══════════════════════════════════════════════

def _parse_work_experience(text: str) -> list[dict]:
    """
    Split work experience into individual entries using a two-pass approach.

    Pass 1: Find all date-range lines and their indices.
    Pass 2: Group lines around date boundaries. The line immediately before
            a date line (if it has no date itself) is the title/company.
            Lines after the date are description until the next title/date block.
    """
    date_range_re = re.compile(
        r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*)?'?\d{2,4}"
        r"\s*[\-–—to]+\s*"
        r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*)?(?:'?\d{2,4}|present|current|till\s*date|ongoing)",
        re.IGNORECASE,
    )

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return []

    # Pass 1: tag each line
    date_indices = []
    for i, line in enumerate(lines):
        if date_range_re.search(line):
            date_indices.append(i)

    if not date_indices:
        # No dates found — return everything as one block
        return [{"company": "", "title": "", "duration": "", "description": text.strip()}]

    entries: list[dict] = []

    for pos, di in enumerate(date_indices):
        dm = date_range_re.search(lines[di])
        duration = dm.group(0).strip()
        remainder = date_range_re.sub("", lines[di]).strip(" \t|–—-,")

        # Look for title/company on the line(s) immediately before this date
        # (back to the previous date line or start)
        prev_boundary = date_indices[pos - 1] + 1 if pos > 0 else 0
        pre_lines = lines[prev_boundary:di]

        # Last pre-line is most likely the title/company
        if pre_lines:
            title_company_line = pre_lines[-1]
        else:
            title_company_line = remainder

        title, company = _split_title_company(title_company_line)

        # Description: lines after this date, up to the next date block's
        # pre-title line.
        next_boundary = date_indices[pos + 1] if pos + 1 < len(date_indices) else len(lines)
        # If the next date has a pre-title line, stop one line before it
        if pos + 1 < len(date_indices) and date_indices[pos + 1] > di + 1:
            desc_end = date_indices[pos + 1] - 1  # leave last line for next entry's title
        else:
            desc_end = next_boundary
        desc_lines = lines[di + 1:desc_end]
        description = " ".join(desc_lines)

        entries.append({
            "company": company,
            "title": title,
            "duration": duration,
            "description": description,
        })

    # Trailing lines after the last date block
    last_di = date_indices[-1]
    trailing = lines[last_di + 1:]
    # These were already handled above via desc_end, but check for any leftover
    # (only if there's exactly one date and trailing wasn't captured)
    if len(date_indices) == 1 and entries and not entries[-1]["description"] and trailing:
        entries[-1]["description"] = " ".join(trailing)

    return entries


def _split_title_company(text: str) -> tuple[str, str]:
    """Heuristic split of 'Title — Company, City' into (title, company)."""
    for sep in (" — ", " – ", " - ", " at ", " @ "):
        if sep in text:
            parts = text.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


# Matches both "CGPA: 8.9" / "GPA 3.8/4.0" (label first, the more common
# real-world ordering) and "8.9 CGPA" / "85%" / "8.9/10" (value first) —
# the old pattern only covered the value-first case, so a "CGPA: 8.9" line
# matched nothing and fell through to being misread as an institution name.
_SCORE_RE = re.compile(
    r"(?:CGPA|GPA)\s*[:\-]?\s*\d+\.?\d*(?:\s*/\s*\d+)?"
    r"|\d+\.?\d*\s*(?:CGPA|GPA)"
    r"|\d+\.?\d*\s*%"
    r"|\d+\.?\d*\s*/\s*\d+",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _line_is_mostly_score_or_year(stripped: str, score_m, year_m) -> bool:
    """True if `stripped` is essentially JUST a score/year fragment (e.g.
    'CGPA: 8.9' or 'Year: 2019') rather than prose that happens to contain
    one (e.g. an institution name with a founding year in it) — used to
    decide a line should NOT be taken as the institution name."""
    remainder = stripped
    if score_m:
        remainder = remainder.replace(score_m.group(0), "")
    if year_m:
        remainder = remainder.replace(year_m.group(0), "")
    remainder = re.sub(r"[\s:;,\-–—.|]+", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"(?i:year|score|percentage|marks|result)", "", remainder)
    return len(remainder) <= 2


def _parse_education(text: str) -> list[dict]:
    """Split education into entries, using degree keywords or date patterns."""
    degree_re = re.compile(
        r"\b(?:B\.?(?:Tech|Sc|Com|A|E|Ed|Arch|Pharm)|"
        r"M\.?(?:Tech|Sc|Com|A|Ed|B\.?A|Phil)|"
        r"Ph\.?D|MBA|BBA|BCA|MCA|LLB|LLM|"
        r"Diploma|HSC|SSC|SSLC|CBSE|ICSE|"
        r"(?:10th|12th|X|XII)(?:\s*(?:Grade|Standard|Class))?|"
        r"(?:High\s*School|Secondary|Senior\s*Secondary|Intermediate)|"
        r"Bachelor|Master|Doctor(?:ate)?|Associate)\b",
        re.IGNORECASE,
    )
    entries = []
    lines = text.split("\n")
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if degree_re.search(stripped):
            if current:
                entries.append(current)
            current = {"institution": "", "degree": stripped, "year": "", "score": ""}
            ym = _YEAR_RE.search(stripped)
            if ym:
                current["year"] = ym.group(0)
            sm = _SCORE_RE.search(stripped)
            if sm:
                current["score"] = sm.group(0).strip()
        elif current is not None:
            ym = _YEAR_RE.search(stripped)
            sm = _SCORE_RE.search(stripped)
            if sm and not current["score"]:
                current["score"] = sm.group(0).strip()
            if ym and not current["year"]:
                current["year"] = ym.group(0)
            # Only a line that ISN'T mostly a score/year fragment can be the
            # institution name -- a bare "CGPA: 8.9" line should never end
            # up there just because "institution" was still empty.
            if not current["institution"] and not _line_is_mostly_score_or_year(stripped, sm, ym):
                current["institution"] = stripped
        else:
            current = {"institution": stripped, "degree": "", "year": "", "score": ""}

    if current:
        entries.append(current)

    if not entries and text.strip():
        entries.append({"institution": "", "degree": text.strip(), "year": "", "score": ""})

    return entries


def _parse_skills(text: str) -> list[str]:
    """Extract a deduplicated list of skills from a skills section."""
    # Skills are usually comma-, pipe-, or newline-separated
    raw = re.split(r"[,\n|•●▪◦➤►★✓✔\-]{1}", text)
    skills = []
    seen = set()
    for s in raw:
        s = s.strip(" \t.·")
        s = re.sub(r"\s+", " ", s)
        if s and len(s) > 1 and s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)
    return skills


def _parse_languages(text: str) -> list[str]:
    raw = re.split(r"[,\n|•●▪◦\-]{1}", text)
    langs = []
    seen = set()
    for s in raw:
        s = s.strip(" \t.·")
        s = re.sub(r"\s+", " ", s)
        if s and len(s) > 1 and s.lower() not in seen:
            seen.add(s.lower())
            langs.append(s)
    return langs


def _parse_certifications(text: str) -> list[str]:
    items = re.split(r"\n|[•●▪◦➤►]", text)
    certs = []
    seen = set()
    for item in items:
        item = item.strip(" \t.-·")
        if item and len(item) > 3 and item.lower() not in seen:
            seen.add(item.lower())
            certs.append(item)
    return certs


def _parse_personal_details(text: str) -> PersonalDetails:
    pd = PersonalDetails()
    # DOB
    dob_m = re.search(
        r"(?:d\.?o\.?b\.?|date\s*of\s*birth|birth\s*date)\s*[:\-–]?\s*(\d{1,2}[\s/\-\.]\w{2,9}[\s/\-\.]\d{2,4}|\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4})",
        text, re.IGNORECASE,
    )
    if dob_m:
        pd.dob = dob_m.group(1).strip()
    # Gender
    gm = re.search(r"(?:gender|sex)\s*[:\-–]?\s*(male|female|other|non[\s-]?binary)", text, re.IGNORECASE)
    if gm:
        pd.gender = gm.group(1).strip().title()
    # Marital status
    mm = re.search(r"(?:marital\s*status|married|unmarried|single)\s*[:\-–]?\s*(married|unmarried|single|divorced|widowed)?", text, re.IGNORECASE)
    if mm:
        pd.marital_status = (mm.group(1) or mm.group(0)).strip().title()
    # Nationality
    nm = re.search(r"(?:nationality|citizen(?:ship)?)\s*[:\-–]?\s*(\w[\w\s]{1,30})", text, re.IGNORECASE)
    if nm:
        pd.nationality = nm.group(1).strip().split("\n")[0].strip()
    # Father's name
    fm = re.search(r"(?:father'?s?\s*name)\s*[:\-–]?\s*(.+)", text, re.IGNORECASE)
    if fm:
        pd.father_name = fm.group(1).strip()
    return pd


def _parse_generic_list(text: str) -> list[str]:
    """Fallback: split on bullets/newlines and return cleaned items."""
    items = re.split(r"\n|[•●▪◦➤►]", text)
    result = []
    seen = set()
    for item in items:
        item = item.strip(" \t.-·")
        if item and len(item) > 2 and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


# ═══════════════════════════════════════════════
# MAIN PARSE ENTRY POINT
# ═══════════════════════════════════════════════

def parse_resume(raw_text: str, source_file: str = "") -> CandidateRecord:
    """
    Parse raw resume text into a structured CandidateRecord.
    """
    record = CandidateRecord(source_file=source_file, raw_text=raw_text)
    confidence: dict[str, float] = {}

    # ── Contact info ──
    emails = _extract_emails(raw_text)
    if emails:
        record.email = emails[0]
        confidence["email"] = 0.95

    phones = _extract_phones(raw_text)
    if phones:
        record.phone_number = phones[0]
        confidence["phone_number"] = 0.85

    record.linkedin_profile = _extract_linkedin(raw_text)
    if record.linkedin_profile:
        confidence["linkedin_profile"] = 0.95

    # ── Name (pass email for cross-validation) ──
    name, name_conf = _extract_name(raw_text, email=record.email)
    record.full_name = name
    if name:
        confidence["full_name"] = name_conf

    # ── Sections ──
    sections = _split_sections(raw_text)

    # ── Location ── constrained to the header (before the first detected
    # section) rather than a blind first-N-raw-lines slice, so a resume
    # with an early section (e.g. SKILLS right after contact info) can't
    # feed section content into spaCy's location NER and pick up a false
    # positive (a tech term or tool name occasionally gets mistagged as a
    # place, e.g. "Django").
    loc, loc_conf = _extract_location(sections.get("header", raw_text))
    record.location_city = loc
    if loc:
        confidence["location_city"] = loc_conf

    if "summary" in sections:
        record.summary_objective_profile = sections["summary"]
        confidence["summary_objective_profile"] = 0.80

    if "work_experience" in sections:
        record.work_experience = _parse_work_experience(sections["work_experience"])
        confidence["work_experience"] = 0.75

    if "education" in sections:
        record.education = _parse_education(sections["education"])
        confidence["education"] = 0.75

    if "skills" in sections:
        record.skills = _parse_skills(sections["skills"])
        confidence["skills"] = 0.80

    if "languages" in sections:
        record.languages = _parse_languages(sections["languages"])
        confidence["languages"] = 0.80

    if "certifications_courses" in sections:
        record.certifications_courses = _parse_certifications(sections["certifications_courses"])
        confidence["certifications_courses"] = 0.80

    if "personal_details" in sections:
        record.personal_details = _parse_personal_details(sections["personal_details"])
        confidence["personal_details"] = 0.70

    if "internships_projects" in sections:
        record.internships_projects = _parse_generic_list(sections["internships_projects"])
        confidence["internships_projects"] = 0.70

    if "awards_achievements" in sections:
        record.awards_achievements = _parse_generic_list(sections["awards_achievements"])
        confidence["awards_achievements"] = 0.75

    if "declaration_reference" in sections:
        record.declaration_reference = sections["declaration_reference"]
        confidence["declaration_reference"] = 0.80

    record.field_confidence = confidence
    return record
