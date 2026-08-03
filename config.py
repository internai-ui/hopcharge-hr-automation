"""
config.py — Central configuration for the resume parser.

All tuneable constants live here so nothing is scattered across modules.
"""

import os
from pathlib import Path


# Paths resolved via app_paths so the app works both as a script and a bundle.
from app_paths import DATA_HOME, OUTPUT_DIR, INPUT_DIR, CREDENTIALS_DIR, STATIC_DIR  # noqa: E402

BASE_DIR = DATA_HOME   # kept for backward-compatibility with older imports
# OUTPUT_DIR / INPUT_DIR / CREDENTIALS_DIR / STATIC_DIR come from app_paths,
# which also creates the directories at import time.

# ──────────────────────────────────────────────
# PDF extraction thresholds
# ──────────────────────────────────────────────
# If text extraction yields fewer chars than this, trigger OCR fallback
MIN_TEXT_LENGTH = 80
# OCR DPI for page rasterisation
OCR_DPI = 300
# Tesseract language (install additional packs as needed)
TESSERACT_LANG = "eng"

# ──────────────────────────────────────────────
# Regex-based section headings (case-insensitive)
# Order matters: first match wins when headings overlap.
# ──────────────────────────────────────────────
SECTION_HEADINGS = [
    # Summary / Objective
    (r"(?:career\s*)?(?:summary|objective|profile|about\s*me|professional\s*summary|career\s*profile|personal\s*profile)",
     "summary"),
    # Work experience
    (r"(?:work|professional|employment|job)\s*(?:experience|history)|experience",
     "work_experience"),
    # Education
    (r"education(?:al)?\s*(?:background|qualification|details)?|academic\s*(?:details|qualifications?|background)",
     "education"),
    # Skills
    (r"(?:key\s*|core\s*|technical\s*|professional\s*)?skills|competenc(?:ies|e)|expertise|areas?\s*of\s*expertise",
     "skills"),
    # Internships / Projects
    (r"intern(?:ship)?s?|projects?|academic\s*projects?|personal\s*projects?",
     "internships_projects"),
    # Certifications
    (r"certific(?:ation|ate)s?|courses?|training|licen[sc]es?|professional\s*development",
     "certifications_courses"),
    # Languages
    (r"languages?\s*(?:known|proficiency)?",
     "languages"),
    # Awards
    (r"awards?|achiev(?:ement|ation)s?|honours?|honors?|accomplishments?",
     "awards_achievements"),
    # Personal details
    (r"personal\s*(?:details|info(?:rmation)?|data)|additional\s*(?:details|info(?:rmation)?)",
     "personal_details"),
    # Declaration / References
    (r"declaration|references?|referees?",
     "declaration_reference"),
    # Hobbies (captured but not a primary field — stored under personal)
    (r"hobbies|interests|extra[\s-]?curricular",
     "hobbies"),
]

# ──────────────────────────────────────────────
# Contact-info regex patterns
# ──────────────────────────────────────────────
EMAIL_PATTERN = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
PHONE_PATTERN = r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,5}\)?[\s\-]?)?\d[\d\s\-]{6,12}\d"
LINKEDIN_PATTERN = r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-%.]{3,100}"

# ──────────────────────────────────────────────
# spaCy model (small model is fast; upgrade to en_core_web_md/lg for
# better NER accuracy if installed)
# ──────────────────────────────────────────────
SPACY_MODEL = "en_core_web_sm"







