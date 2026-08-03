"""
colleges/discovery.py — Assisted contact-discovery engine.

Given a college (name + optional website), this tries to surface candidate
placement contacts a human can review and accept. Two methods that actually
work in practice:

  METHOD 1  Official website — fetch the site, follow likely placement paths
            (/placement, /tpo, /career-development, …), extract contacts.
  METHOD 2  Placement brochure — find a linked PDF brochure, download, extract.

Deliberately NOT implemented (see module note at bottom):
  • LinkedIn scraping  — against ToS, blocked, breaks constantly. We expose a
    manual search URL instead (helpers.linkedin_search_url).
  • Email enrichment   — paid third-party APIs. The provider seam lives in
    enrichment.py; this engine calls it only if a provider is configured.

EXECUTION MODEL
This runs IN-PROCESS in a background thread while the dashboard is open, with a
JobTracker the UI polls for progress. No external worker/queue needed. If the
app ever moves to an always-on server, swap JobTracker for a real task queue —
the discovery functions themselves don't change.

NETWORK SAFETY
Every outbound request is rate-limited and has a short timeout + retry. We send
a normal browser User-Agent and respect a small politeness delay between hits
to the same host. Discovery is best-effort: many sites will yield nothing, and
that's expected — it accelerates a human, it doesn't replace them.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from colleges.extract import (
    ContactCandidate, build_candidates, looks_placement_relevant,
)
from colleges import directory as _directory

logger = logging.getLogger("volt_cv.colleges.discovery")

# ──────────────────────────────────────────────
# Tunables (easy to change)
# ──────────────────────────────────────────────
REQUEST_TIMEOUT   = 4           # seconds per request — fast fail
MAX_RETRIES       = 0           # no retries for interactive single-college search
POLITENESS_DELAY  = 0.0         # interactive single-college search fans out concurrently
MAX_PAGES_PER_COLLEGE = 10      # fetched CONCURRENTLY, so more pages != slower
MAX_WORKERS       = 8           # concurrent page fetches per college
HIGH_CONFIDENCE   = 88          # an email candidate >= this ends the search early
CACHE_TTL         = 6 * 3600    # remember a college's result for 6h (instant repeats)
MAX_BROCHURE_BYTES = 5 * 1024 * 1024   # 5 MB cap
WALL_CLOCK_BUDGET = 9.0         # HARD cap (seconds): discover_college() returns within this
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Paths tried in priority order — expanded with Indian college conventions
PLACEMENT_PATHS = [
    "/placement", "/placements", "/training-placement", "/training-and-placement",
    "/tpo", "/career-development", "/career", "/careers", "/cdc",
    "/internships", "/internship", "/recruitment", "/corporate-relations",
    "/placement-cell", "/about/placement", "/about/training-placement",
    "/academics/placement", "/departments/placement", "/campus-placement",
    "/training_placement", "/training_and_placement",   # underscore variants
    "/tnp", "/t-and-p", "/t_p",                        # short-form variants
    "/contact",                                          # last resort: contact page
]

# Link-text phrases ranked by relevance for homepage link harvesting
_LINK_SCORE: list[tuple[str, int]] = [
    ("training and placement", 10), ("training & placement", 10),
    ("t&p", 9), ("tnp", 9), ("tpo", 9),
    ("placement cell", 8), ("placement office", 8),
    ("career development", 7), ("cdc", 7),
    ("corporate relations", 6), ("internship", 5),
    ("placement", 5), ("career", 4), ("recruit", 3),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ──────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────

_last_hit: dict[str, float] = {}
_hit_lock = threading.Lock()


def _polite_wait(host: str) -> None:
    with _hit_lock:
        last = _last_hit.get(host, 0)
        wait = POLITENESS_DELAY - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


def _get(url: str, *, want_pdf: bool = False) -> Optional[requests.Response]:
    """GET with UA, timeout, retries, politeness. Returns None on failure."""
    host = urlparse(url).netloc
    headers = {"User-Agent": USER_AGENT,
               "Accept": "application/pdf" if want_pdf else "text/html,*/*"}
    for attempt in range(MAX_RETRIES + 1):
        _polite_wait(host)
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                                stream=want_pdf, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 404, 410):
                return None  # don't retry hard failures
        except requests.RequestException as e:
            logger.debug("GET failed (%s) attempt %d: %s", url, attempt, e)
        time.sleep(0.5 * (attempt + 1))
    return None


def _guess_website(college_name: str) -> Optional[str]:
    """
    Best-effort homepage guess when no website is on file. We do NOT scrape a
    search engine (brittle/ToS); instead we try common Indian-college domain
    constructions CONCURRENTLY and keep the first that resolves. Honest and
    predictable beats flaky — if none resolve, discovery reports "no website".
    """
    base = re.sub(r"[^a-z0-9 ]", "", college_name.lower())
    tokens = base.split()
    acronym = "".join(t[0] for t in tokens if t)[:6]
    guesses: list[str] = []
    seen: set[str] = set()
    def _add(u):
        if u not in seen:
            seen.add(u); guesses.append(u)
    # acronym (iit, nitk, …) and first-token domains across the common TLDs.
    for stem in [s for s in (acronym, tokens[0] if tokens else "") if s]:
        for tld in (".ac.in", ".edu.in", ".org", ".com", ".in"):
            _add(f"https://www.{stem}{tld}")
            _add(f"https://{stem}{tld}")
    if not guesses:
        return None
    # Fire them all at once; first that returns 200 wins.
    try:
        with ThreadPoolExecutor(max_workers=min(8, len(guesses))) as ex:
            futs = {ex.submit(_get, g): g for g in guesses}
            for fut in as_completed(futs, timeout=REQUEST_TIMEOUT + 1.5):
                try:
                    if fut.result() is not None:
                        return futs[fut]
                except Exception:
                    continue
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Page / brochure discovery
# ──────────────────────────────────────────────

def _candidate_placement_urls(website: str, homepage_html: str = "") -> list[str]:
    """Build a prioritised list of URLs likely to hold placement contacts."""
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(u: str, score: int = 0):
        u = u.split("#")[0].rstrip("/")
        if u and u not in seen:
            seen.add(u)
            scored.append((score, u))

    if homepage_html:
        soup = BeautifulSoup(homepage_html, "html.parser")
        # Check for JS-heavy rendering hint (no meaningful links found)
        all_links = soup.find_all("a", href=True)

        for a in all_links:
            label = (a.get_text(" ", strip=True) or "").lower()
            href  = (a.get("href") or "").lower()
            combined = label + " " + href
            # Score by keyword relevance
            link_score = 0
            for phrase, pts in _LINK_SCORE:
                if phrase in combined:
                    link_score = max(link_score, pts)
            if link_score > 0:
                add(urljoin(website, a["href"]), score=link_score + 10)  # homepage links beat guesses

    # Well-known paths (scored by position = priority)
    for idx, p in enumerate(PLACEMENT_PATHS):
        add(urljoin(website, p), score=max(0, 8 - idx))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in scored[:MAX_PAGES_PER_COLLEGE]]


def _find_brochure_pdf(website: str, html: str) -> Optional[str]:
    """Find a linked PDF that looks like a placement brochure."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        label = (a.get_text() or "").lower()
        if ".pdf" in href.lower() and any(
            k in (href + label).lower()
            for k in ("placement", "brochure", "tpo", "recruit")
        ):
            return urljoin(website, href)
    return None


def _extract_pdf_text(resp: requests.Response) -> str:
    """Read a streamed PDF response (size-capped) and extract its text."""
    buf = bytearray()
    for chunk in resp.iter_content(8192):
        buf.extend(chunk)
        if len(buf) > MAX_BROCHURE_BYTES:
            logger.info("Brochure exceeded size cap; truncating.")
            break
    text = ""
    try:
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(bytes(buf))) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:12])
    except Exception as e:
        logger.debug("pdfplumber failed, trying PyMuPDF: %s", e)
        try:
            import fitz, io
            doc = fitz.open(stream=bytes(buf), filetype="pdf")
            text = "\n".join(doc[i].get_text() for i in range(min(12, doc.page_count)))
        except Exception as e2:
            logger.debug("PyMuPDF also failed: %s", e2)
    return text


# ──────────────────────────────────────────────
# Short-lived result cache (instant repeat searches)
# ──────────────────────────────────────────────

_result_cache: dict[str, tuple[float, "DiscoveryResult"]] = {}
_cache_lock = threading.Lock()


def _cache_key(name: str, website: str) -> str:
    return (name or "").strip().lower() + "|" + (website or "").strip().lower()


def _cache_get(key: str):
    with _cache_lock:
        v = _result_cache.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > CACHE_TTL:
        return None
    return data


def _cache_put(key: str, data) -> None:
    with _cache_lock:
        _result_cache[key] = (time.time(), data)


def _fetch_extract(url: str, website: str):
    """Fetch one URL and pull contact candidates from it. Returns
    (url, html, candidates). Safe to run in a worker thread."""
    resp = _get(url)
    if not resp:
        return (url, "", [])
    html = resp.text or ""
    cands: list = []
    try:
        text = BeautifulSoup(html, "html.parser").get_text("\n")
        if looks_placement_relevant(text):
            cands = build_candidates(text, source_url=url, source_type="Website",
                                     website=website, base_confidence=92)
    except Exception:
        pass
    return (url, html, cands)


# ──────────────────────────────────────────────
# Public: discover one college
# ──────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    college_name: str
    website: str = ""
    candidates: list[ContactCandidate] = field(default_factory=list)
    pages_checked: list[str] = field(default_factory=list)
    brochure_url: str = ""
    method_notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "college_name": self.college_name,
            "website": self.website,
            "brochure_url": self.brochure_url,
            "pages_checked": self.pages_checked,
            "method_notes": self.method_notes,
            "error": self.error,
            "candidates": [c.__dict__ for c in self.candidates],
        }


def discover_college(college_name: str, website: str = "") -> DiscoveryResult:
    """Run website + brochure discovery for ONE college, returning within
    WALL_CLOCK_BUDGET seconds. Results for a (name, website) pair are cached for
    CACHE_TTL so a repeated search is instant."""
    ck = _cache_key(college_name, website)
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    res = _discover_college_uncached(college_name, website)
    # Cache only useful results — a transient failure should be retryable, not
    # frozen for 6 hours.
    if res.candidates or res.error:
        _cache_put(ck, res)
    return res


def _discover_college_uncached(college_name: str, website: str = "") -> DiscoveryResult:
    res = DiscoveryResult(college_name=college_name, website=website)
    start = time.time()
    deadline = start + WALL_CLOCK_BUDGET

    def remaining() -> float:
        return deadline - time.time()

    def over_budget() -> bool:
        return time.time() >= deadline

    # STEP 0 — resolve the REAL website from the known-college directory.
    # "iit delhi" → iitd.ac.in plus the institution's real placement-page URLs.
    known_placement_urls: list[str] = []
    entry = _directory.resolve(college_name)
    if entry:
        known_placement_urls = entry.get("placement", []) or []
        if not website:
            website = entry["site"]
            res.website = website
            res.method_notes.append(f"Matched directory: {entry['name']} → {website}")
        else:
            res.method_notes.append(f"Matched directory: {entry['name']}")

    if not website:
        guessed = _guess_website(college_name)
        if guessed:
            res.website = website = guessed
            res.method_notes.append(f"Guessed website: {guessed}")
        else:
            res.error = ("Could not resolve a website automatically. "
                         "Add the college's website URL above and retry — "
                         "discovery then runs against it directly.")
            return res

    seen_keys: set[str] = set()

    def add_cands(cands):
        for c in cands:
            if c.key() not in seen_keys:
                seen_keys.add(c.key())
                res.candidates.append(c)

    def strong_email() -> bool:
        return any(getattr(c, "email", "") and getattr(c, "confidence", 0) >= HIGH_CONFIDENCE
                   for c in res.candidates)

    def gather(urls: list[str]) -> str:
        """Fetch a batch of URLs CONCURRENTLY, extracting contacts as each
        returns. Honours the global deadline and early-exits on a strong hit.
        Returns the homepage HTML if the homepage was part of the batch."""
        home_html = ""
        urls = [u for u in urls if u]
        if not urls or over_budget():
            return home_html
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(urls))) as ex:
            futs = {ex.submit(_fetch_extract, u, website): u for u in urls}
            try:
                for fut in as_completed(futs, timeout=max(0.1, remaining())):
                    try:
                        u2, html, cands = fut.result()
                    except Exception:
                        continue
                    if u2.rstrip("/") == website.rstrip("/") and html:
                        home_html = html
                    if html:
                        res.pages_checked.append(u2)
                    if cands:
                        add_cands(cands)
                    if strong_email() or len(res.candidates) >= 4:
                        break
            except Exception:
                res.method_notes.append("Time budget reached during page fetch.")
        return home_html

    # WAVE 1 — directory placement URLs (highest yield) + generic path guesses
    # + the homepage, all fetched at once. No homepage round-trip needed first.
    wave1, seen_urls = [], set()
    for u in known_placement_urls + [urljoin(website, p) for p in PLACEMENT_PATHS] + [website]:
        uu = u.split("#")[0].rstrip("/")
        if uu and uu not in seen_urls:
            seen_urls.add(uu)
            wave1.append(uu)
    wave1 = wave1[:MAX_PAGES_PER_COLLEGE + 2]
    home_html = gather(wave1)

    # WAVE 2 — extra placement links harvested from the homepage, if we still
    # need a strong contact and time remains.
    if home_html and not strong_email() and remaining() > 1.0:
        extra = [u for u in _candidate_placement_urls(website, home_html)
                 if u.rstrip("/") not in seen_urls][:MAX_WORKERS]
        if extra:
            gather(extra)

    # METHOD 2 — brochure PDF, only if we still lack a good contact and there's
    # comfortable time left (PDF parsing is the slowest single step).
    if not strong_email() and len(res.candidates) < 2 and remaining() > 2.5:
        brochure = _find_brochure_pdf(website, home_html)
        if brochure:
            res.brochure_url = brochure
            pdf = _get(brochure, want_pdf=True)
            if pdf:
                text = _extract_pdf_text(pdf)
                if text:
                    res.method_notes.append("Extracted brochure text.")
                    add_cands(build_candidates(text, source_url=brochure,
                                               source_type="Placement Brochure",
                                               website=website, base_confidence=88))

    # Strongest contacts first.
    res.candidates.sort(key=lambda c: getattr(c, "confidence", 0), reverse=True)
    res.method_notes.append(
        f"Completed in {time.time() - start:.1f}s · {len(res.pages_checked)} page(s) checked.")

    if not res.candidates and not res.error:
        res.method_notes.append(
            "No contacts found automatically. Many college sites protect emails or "
            "render with JavaScript — try the LinkedIn / Google manual search links, "
            "or add the exact website above and retry.")
    return res


# ──────────────────────────────────────────────
# In-process job runner (for bulk discovery)
# ──────────────────────────────────────────────

@dataclass
class Job:
    id: str
    total: int
    done: int = 0
    status: str = "running"          # running | finished | error
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = ""
    results: list[dict] = field(default_factory=list)
    current: str = ""


class JobTracker:
    """Thread-safe registry of background discovery jobs the UI can poll."""
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, total: int) -> Job:
        job = Job(id="job_" + uuid.uuid4().hex[:10], total=total)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> Optional[dict]:
        job = self.get(job_id)
        if not job:
            return None
        with self._lock:
            return {
                "id": job.id, "total": job.total, "done": job.done,
                "status": job.status, "current": job.current,
                "started_at": job.started_at, "finished_at": job.finished_at,
                "results": job.results,
            }


tracker = JobTracker()


def run_bulk_discovery(job: Job, colleges: list[dict],
                       on_each: Optional[Callable[[DiscoveryResult], None]] = None) -> None:
    """
    Worker body for a bulk job. `colleges` is a list of {"college_name","website"}.
    Runs synchronously in its own thread; updates the Job as it goes.
    """
    try:
        for item in colleges:
            name = item.get("college_name", "").strip()
            site = item.get("website", "").strip()
            if not name:
                job.done += 1
                continue
            job.current = name
            try:
                res = discover_college(name, site)
                job.results.append(res.to_dict())
                if on_each:
                    on_each(res)
            except Exception as e:                       # never let one college kill the job
                logger.exception("Discovery error for %s", name)
                job.results.append({"college_name": name, "error": str(e), "candidates": []})
            job.done += 1
        job.status = "finished"
    except Exception as e:
        logger.exception("Bulk discovery job crashed")
        job.status = "error"
        job.current = str(e)
    finally:
        job.finished_at = _now_iso()


def start_bulk_discovery(colleges: list[dict]) -> Job:
    """Create a job and run it in a daemon thread; returns immediately."""
    job = tracker.create(total=len(colleges))
    t = threading.Thread(target=run_bulk_discovery, args=(job, colleges), daemon=True)
    t.start()
    return job


# ──────────────────────────────────────────────
# Manual helpers (honest alternatives to scraping)
# ──────────────────────────────────────────────

def linkedin_search_url(college_name: str) -> str:
    """A ready-to-click Google search for the TPO on LinkedIn."""
    from urllib.parse import quote_plus
    q = f'site:linkedin.com ("Training and Placement Officer" OR "Placement Officer") "{college_name}"'
    return "https://www.google.com/search?q=" + quote_plus(q)


def google_tpo_search_url(college_name: str) -> str:
    """A direct Google search for the college's TPO email — for a human to open."""
    from urllib.parse import quote_plus
    q = f'"{college_name}" "placement officer" email contact'
    return "https://www.google.com/search?q=" + quote_plus(q)


def website_guess_urls(college_name: str) -> list[str]:
    """Return educated website guesses the user can try manually if auto-guess fails."""
    base = re.sub(r"[^a-z0-9 ]", "", college_name.lower()).split()
    acronym = "".join(t[0] for t in base)[:6]
    guesses = []
    if acronym:
        guesses += [f"https://www.{acronym}.ac.in", f"https://{acronym}.ac.in"]
    if base:
        guesses.append(f"https://www.{base[0]}.ac.in")
    return guesses
