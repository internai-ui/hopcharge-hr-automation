"""
colleges/directory.py — Known-college → real-website resolver.

The discovery engine's biggest failure mode was not having a correct website to
start from: guessing "www.<acronym>.ac.in" is wrong for almost every real
institution (IIT Delhi is iitd.ac.in, not iit.ac.in). This module fixes that
with a curated map of the colleges the HR team actually targets — all IITs,
NITs, IIITs, BITS campuses, IISc, and major universities — to their real
domains and, where known, their direct placement-page URLs.

resolve(name) does fuzzy matching so "iit delhi", "IITD", "I.I.T. Delhi",
"indian institute of technology delhi" all map to the same entry.

This is the zero-config path that makes discovery work today. For colleges NOT
in this directory, discovery.py falls back to (optional) web search.
"""

from __future__ import annotations

import re
from typing import Optional


# Each entry: canonical name → (homepage, [known placement page paths/urls])
# Placement URLs are full or path; discovery tries them first (highest yield).
COLLEGE_DIRECTORY: dict[str, dict] = {
    # ── IITs ───────────────────────────────────────────────
    "iit bombay":      {"site": "https://www.iitb.ac.in",  "placement": ["https://placements.iitb.ac.in", "https://www.iitb.ac.in/en/education/placement"]},
    "iit delhi":       {"site": "https://www.iitd.ac.in",  "placement": ["https://tnp.iitd.ac.in", "https://www.iitd.ac.in/en/placement"]},
    "iit madras":      {"site": "https://www.iitm.ac.in",  "placement": ["https://placement.iitm.ac.in", "https://www.iitm.ac.in/happenings/placements"]},
    "iit kanpur":      {"site": "https://www.iitk.ac.in",  "placement": ["https://pnp.iitk.ac.in", "https://www.iitk.ac.in/spo"]},
    "iit kharagpur":   {"site": "https://www.iitkgp.ac.in","placement": ["https://www.iitkgp.ac.in/career-development-centre", "https://cdc.iitkgp.ac.in"]},
    "iit roorkee":     {"site": "https://www.iitr.ac.in",  "placement": ["https://iitr.ac.in/Placement", "https://tnp.iitr.ac.in"]},
    "iit guwahati":    {"site": "https://www.iitg.ac.in",  "placement": ["https://www.iitg.ac.in/cca/placement", "https://iitg.ac.in/tpc"]},
    "iit hyderabad":   {"site": "https://www.iith.ac.in",  "placement": ["https://placements.iith.ac.in", "https://iith.ac.in/placements"]},
    "iit indore":      {"site": "https://www.iiti.ac.in",  "placement": ["https://academic.iiti.ac.in/placement.php", "https://tnp.iiti.ac.in"]},
    "iit bhu":         {"site": "https://www.iitbhu.ac.in","placement": ["https://www.iitbhu.ac.in/cell/tpc", "https://tpo.iitbhu.ac.in"]},
    "iit varanasi":    {"site": "https://www.iitbhu.ac.in","placement": ["https://www.iitbhu.ac.in/cell/tpc"]},
    "iit dhanbad":     {"site": "https://www.iitism.ac.in","placement": ["https://www.iitism.ac.in/careerhub", "https://placement.iitism.ac.in"]},
    "iit ism dhanbad": {"site": "https://www.iitism.ac.in","placement": ["https://www.iitism.ac.in/careerhub"]},
    "iit gandhinagar": {"site": "https://www.iitgn.ac.in", "placement": ["https://placement.iitgn.ac.in", "https://iitgn.ac.in/placement"]},
    "iit ropar":       {"site": "https://www.iitrpr.ac.in","placement": ["https://www.iitrpr.ac.in/placement", "https://tnp.iitrpr.ac.in"]},
    "iit patna":       {"site": "https://www.iitp.ac.in",  "placement": ["https://www.iitp.ac.in/index.php/placement", "https://tnp.iitp.ac.in"]},
    "iit mandi":       {"site": "https://www.iitmandi.ac.in","placement": ["https://www.iitmandi.ac.in/cce", "https://placement.iitmandi.ac.in"]},
    "iit jodhpur":     {"site": "https://www.iitj.ac.in",  "placement": ["https://www.iitj.ac.in/placement", "https://tnp.iitj.ac.in"]},
    "iit tirupati":    {"site": "https://www.iittp.ac.in", "placement": ["https://www.iittp.ac.in/placements"]},
    "iit palakkad":    {"site": "https://www.iitpkd.ac.in","placement": ["https://placement.iitpkd.ac.in", "https://iitpkd.ac.in/placement"]},
    "iit bhilai":      {"site": "https://www.iitbhilai.ac.in","placement": ["https://www.iitbhilai.ac.in/index.php?pid=placement"]},
    "iit goa":         {"site": "https://www.iitgoa.ac.in","placement": ["https://www.iitgoa.ac.in/placement"]},
    "iit jammu":       {"site": "https://www.iitjammu.ac.in","placement": ["https://www.iitjammu.ac.in/placement-cell", "https://tnp.iitjammu.ac.in"]},
    "iit dharwad":     {"site": "https://www.iitdh.ac.in", "placement": ["https://www.iitdh.ac.in/placement"]},

    # ── NITs ───────────────────────────────────────────────
    "nit trichy":          {"site": "https://www.nitt.edu",    "placement": ["https://www.nitt.edu/home/students/placement", "https://tnp.nitt.edu"]},
    "nit tiruchirappalli": {"site": "https://www.nitt.edu",    "placement": ["https://www.nitt.edu/home/students/placement"]},
    "nit surathkal":       {"site": "https://www.nitk.ac.in",  "placement": ["https://www.nitk.ac.in/department/career-development-centre-cdc", "https://careers.nitk.ac.in"]},
    "nit karnataka":       {"site": "https://www.nitk.ac.in",  "placement": ["https://careers.nitk.ac.in"]},
    "nit warangal":        {"site": "https://www.nitw.ac.in",  "placement": ["https://www.nitw.ac.in/main/CDC/", "https://tnp.nitw.ac.in"]},
    "nit calicut":         {"site": "https://www.nitc.ac.in",  "placement": ["https://www.nitc.ac.in/department/career-guidance-placement-unit", "https://cgpu.nitc.ac.in"]},
    "nit rourkela":        {"site": "https://www.nitrkl.ac.in","placement": ["https://www.nitrkl.ac.in/TP/", "https://tnp.nitrkl.ac.in"]},
    "nit durgapur":        {"site": "https://www.nitdgp.ac.in","placement": ["https://www.nitdgp.ac.in/p/career"]},
    "nit silchar":         {"site": "https://www.nits.ac.in",  "placement": ["https://www.nits.ac.in/departments/TandP/tnp.php"]},
    "nit jaipur":          {"site": "https://www.mnit.ac.in",  "placement": ["https://www.mnit.ac.in/cdc/"]},
    "mnit jaipur":         {"site": "https://www.mnit.ac.in",  "placement": ["https://www.mnit.ac.in/cdc/"]},
    "nit allahabad":       {"site": "https://www.mnnit.ac.in", "placement": ["https://www.mnnit.ac.in/index.php/training-placement"]},
    "mnnit allahabad":     {"site": "https://www.mnnit.ac.in", "placement": ["https://www.mnnit.ac.in/index.php/training-placement"]},
    "nit bhopal":          {"site": "https://www.manit.ac.in", "placement": ["https://www.manit.ac.in/content/training-placement"]},
    "manit bhopal":        {"site": "https://www.manit.ac.in", "placement": ["https://www.manit.ac.in/content/training-placement"]},
    "nit nagpur":          {"site": "https://www.vnit.ac.in",  "placement": ["https://vnit.ac.in/tnp/"]},
    "vnit nagpur":         {"site": "https://www.vnit.ac.in",  "placement": ["https://vnit.ac.in/tnp/"]},
    "nit kurukshetra":     {"site": "https://www.nitkkr.ac.in","placement": ["https://www.nitkkr.ac.in/training-placement/"]},
    "nit jalandhar":       {"site": "https://www.nitj.ac.in",  "placement": ["https://www.nitj.ac.in/tnp"]},
    "nit hamirpur":        {"site": "https://www.nith.ac.in",  "placement": ["https://nith.ac.in/training-and-placement"]},
    "nit jamshedpur":      {"site": "https://www.nitjsr.ac.in","placement": ["https://www.nitjsr.ac.in/Department/TrainingAndPlacement"]},
    "nit raipur":          {"site": "https://www.nitrr.ac.in", "placement": ["https://nitrr.ac.in/tnp.php"]},
    "nit goa":             {"site": "https://www.nitgoa.ac.in","placement": ["https://www.nitgoa.ac.in/placement"]},
    "nit patna":           {"site": "https://www.nitp.ac.in",  "placement": ["https://www.nitp.ac.in/Departments/TandP"]},

    # ── IIITs ──────────────────────────────────────────────
    "iiit hyderabad":  {"site": "https://www.iiit.ac.in",   "placement": ["https://placements.iiit.ac.in", "https://www.iiit.ac.in/placements"]},
    "iiit allahabad":  {"site": "https://www.iiita.ac.in",  "placement": ["https://www.iiita.ac.in/placement", "https://tnp.iiita.ac.in"]},
    "iiit delhi":      {"site": "https://www.iiitd.ac.in",  "placement": ["https://www.iiitd.ac.in/placements", "https://placement.iiitd.ac.in"]},
    "iiit bangalore":  {"site": "https://www.iiitb.ac.in",  "placement": ["https://www.iiitb.ac.in/career-development-services"]},
    "iiitb":           {"site": "https://www.iiitb.ac.in",  "placement": ["https://www.iiitb.ac.in/career-development-services"]},
    "iiit gwalior":    {"site": "https://www.iiitm.ac.in",  "placement": ["https://www.iiitm.ac.in/index.php/en/placements-en"]},

    # ── BITS / IISc / others ───────────────────────────────
    "bits pilani":     {"site": "https://www.bits-pilani.ac.in","placement": ["https://www.bits-pilani.ac.in/placement/", "https://pso.bits-pilani.ac.in"]},
    "bits goa":        {"site": "https://www.bits-pilani.ac.in","placement": ["https://www.bits-pilani.ac.in/goa/placement/"]},
    "bits hyderabad":  {"site": "https://www.bits-pilani.ac.in","placement": ["https://www.bits-pilani.ac.in/hyderabad/placement/"]},
    "iisc bangalore":  {"site": "https://www.iisc.ac.in",   "placement": ["https://officeofcareerservices.iisc.ac.in", "https://www.iisc.ac.in/students/career-services/"]},
    "iisc":            {"site": "https://www.iisc.ac.in",   "placement": ["https://officeofcareerservices.iisc.ac.in"]},
    "dtu":             {"site": "https://www.dtu.ac.in",    "placement": ["https://www.dtu.ac.in/Web/Placements/", "https://tnp.dtu.ac.in"]},
    "delhi technological university": {"site": "https://www.dtu.ac.in", "placement": ["https://tnp.dtu.ac.in"]},
    "nsut":            {"site": "https://www.nsut.ac.in",   "placement": ["https://www.nsut.ac.in/en/placement"]},
    "vit vellore":     {"site": "https://vit.ac.in",        "placement": ["https://placement.vit.ac.in", "https://vit.ac.in/placement"]},
    "vit":             {"site": "https://vit.ac.in",        "placement": ["https://placement.vit.ac.in"]},
    "manipal":         {"site": "https://manipal.edu",      "placement": ["https://manipal.edu/mu/placements.html"]},
    "manipal institute of technology": {"site": "https://manipal.edu", "placement": ["https://manipal.edu/mu/placements.html"]},
    "thapar":          {"site": "https://www.thapar.edu",   "placement": ["https://www.thapar.edu/placements", "https://cgc.thapar.edu"]},
    "srm":             {"site": "https://www.srmist.edu.in","placement": ["https://www.srmist.edu.in/placement/", "https://care.srmist.edu.in"]},
    "srm chennai":     {"site": "https://www.srmist.edu.in","placement": ["https://www.srmist.edu.in/placement/"]},
    "amity":           {"site": "https://www.amity.edu",    "placement": ["https://www.amity.edu/placement.asp"]},
    "jadavpur university": {"site": "https://www.jaduniv.edu.in","placement": ["https://www.jaduniv.edu.in/placement.php"]},
    "anna university": {"site": "https://www.annauniv.edu", "placement": ["https://www.annauniv.edu/cco/"]},
    "jiit":            {"site": "https://www.jiit.ac.in",   "placement": ["https://www.jiit.ac.in/training-placements", "https://www.jiit.ac.in/placements"]},
    "jaypee":          {"site": "https://www.jiit.ac.in",   "placement": ["https://www.jiit.ac.in/training-placements"]},
    "pes university":  {"site": "https://www.pes.edu",      "placement": ["https://placements.pes.edu", "https://www.pes.edu/placements/"]},
    "pes":             {"site": "https://www.pes.edu",      "placement": ["https://placements.pes.edu"]},
    "rvce":            {"site": "https://www.rvce.edu.in",  "placement": ["https://rvce.edu.in/placement"]},
    "bms college":     {"site": "https://www.bmsce.ac.in",  "placement": ["https://www.bmsce.ac.in/home/Placement"]},
    "msrit":           {"site": "https://www.msrit.edu",    "placement": ["https://www.msrit.edu/placement.html"]},
    "coep":            {"site": "https://www.coep.org.in",  "placement": ["https://www.coep.org.in/placement"]},
    "iiest shibpur":   {"site": "https://www.iiests.ac.in", "placement": ["https://www.iiests.ac.in/IIEST/career_placement"]},
}


# ──────────────────────────────────────────────
# Normalisation + fuzzy matching
# ──────────────────────────────────────────────

# Common full-form ↔ acronym expansions so users can type either.
_EXPANSIONS = [
    (r"\bindian institute of technology\b", "iit"),
    (r"\bnational institute of technology\b", "nit"),
    (r"\bindian institute of information technology\b", "iiit"),
    (r"\bindian institute of science\b", "iisc"),
    (r"\bbirla institute of technology and science\b", "bits"),
    (r"\bdelhi technological university\b", "dtu"),
    (r"\bnetaji subhas university of technology\b", "nsut"),
    (r"\bvellore institute of technology\b", "vit"),
]

# City aliases (some colleges are searched by alternate city names)
_CITY_ALIASES = {
    "mumbai": "bombay", "chennai": "madras", "trichy": "trichy",
    "tiruchirappalli": "trichy", "surathkal": "surathkal",
    "bengaluru": "bangalore", "varanasi": "bhu",
}


def _normalise(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[.\-_,]", " ", s)            # punctuation → space
    s = re.sub(r"\s+", " ", s).strip()
    # Expand full forms to acronyms
    for pattern, repl in _EXPANSIONS:
        s = re.sub(pattern, repl, s)
    # Collapse spaced acronyms: "i i t delhi" → "iit delhi"
    s = re.sub(r"\b(?:[a-z]\s){2,}[a-z]\b",
               lambda m: m.group(0).replace(" ", ""), s)
    # Apply city aliases token-wise
    tokens = [(_CITY_ALIASES.get(t, t)) for t in s.split()]
    s = " ".join(tokens)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve(name: str) -> Optional[dict]:
    """
    Resolve a college name to {"name", "site", "placement"[]} or None.
    Tries exact normalised match, then distinctive-token match.
    """
    norm = _normalise(name)
    if not norm:
        return None

    # Generic words that must NOT drive a match on their own.
    GENERIC = {"institute", "of", "technology", "university", "college",
               "national", "indian", "science", "and", "the", "school",
               "engineering", "information"}

    def distinctive(tokens: set[str]) -> set[str]:
        return {t for t in tokens if t not in GENERIC}

    # 1) Exact normalised match
    for key, val in COLLEGE_DIRECTORY.items():
        if _normalise(key) == norm:
            return {"name": key, **val}

    qtokens = set(norm.split())
    q_dist = distinctive(qtokens)

    # 2) Best match on DISTINCTIVE token overlap (acronym + city, etc.)
    best = None
    best_overlap = 0
    for key, val in COLLEGE_DIRECTORY.items():
        ktokens = set(_normalise(key).split())
        k_dist = distinctive(ktokens)
        shared = q_dist & k_dist
        if not shared:
            continue
        # Require that the shared tokens cover the directory key's distinctive
        # tokens (so "iit delhi" matches "iit delhi", not just "iit").
        if k_dist.issubset(qtokens) and len(shared) >= len(k_dist):
            overlap = len(shared)
            if overlap > best_overlap:
                best_overlap = overlap
                best = {"name": key, **val}
    if best:
        return best

    # 3) Acronym-only fallback: query is just one distinctive token that is a
    # unique acronym in the directory (e.g. "dtu", "jiit", "nsut").
    if len(q_dist) == 1:
        tok = next(iter(q_dist))
        matches = [(_normalise(k), v) for k, v in COLLEGE_DIRECTORY.items()
                   if tok in _normalise(k).split()]
        # only accept if it maps to exactly one distinct site
        sites = {v["site"] for _, v in matches}
        if len(sites) == 1 and matches:
            k = matches[0][0]
            return {"name": k, **matches[0][1]}

    return None


def all_known() -> list[str]:
    """Sorted list of canonical directory names (for UI hints / autocomplete)."""
    return sorted(COLLEGE_DIRECTORY.keys())
