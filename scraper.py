#!/usr/bin/env python3
"""
OnlineJobs.ph job search scraper.

RUN IT
    python scraper.py --append

    Newest 30 listings, appended to output/jobs.xlsx and deduped on job_id.
    Safe to run daily — a job already in the sheet is never added twice.
    The search pages are always fetched fresh, so the newest postings are
    always in view. Takes ~3 min the first time (30 detail pages at the site's
    5s crawl-delay) and well under a minute after, since detail pages already
    seen are cached (see CACHE_TTL / LIST_TTL below).

HOW MANY JOBS
    30 — one listing page — unless --limit says otherwise. That's DEFAULT_LIMIT
    below, so there's no flag to remember for the normal case.

    It's a window, not a hard cap on what exists: anything posted since the last
    run that has already been pushed off page 1 by newer postings is never seen.
    If more than 30 jobs land between runs, widen it — --limit 60 reads two
    pages for one extra request. --limit 0 takes everything (~209 listings, ~215
    requests, ~18 minutes).

COMMON VARIANTS
    --no-details      listing data only — 7 requests, ~35s
    --keyword "TEXT"  search instead of browsing everything
    --full-time       filter by employment type (--gig, --part-time too)
    --limit N         take N jobs instead of 30 (0 = no limit)
    --refresh         ignore all caching, detail pages included
    --webhook URL     POST the new jobs to an n8n webhook (see N8N.md)

FULL OPTION LIST
    python scraper.py --help

The site is server-rendered: no JS execution, no login, no API keys needed.
robots.txt permits /jobseekers/ and asks for a 5s crawl-delay, which this
script reads and honours by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.robotparser import RobotFileParser

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
import requests
from bs4 import BeautifulSoup

BASE = "https://www.onlinejobs.ph"

# Exactly what this scrapes. Pagination is offset-based, not ?page=N — offset 0
# is this URL, then /30, /60, … (built in listing_url below).
SEARCH_URL = "https://www.onlinejobs.ph/jobseekers/jobsearch"

PER_PAGE = 30

# How many jobs a run takes when --limit isn't given. 0 means all available listings.
DEFAULT_LIMIT = 0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Absolute, because these are evaluated at import time and a Task Scheduler
# run has no guaranteed working directory — relative paths would silently
# create the cache somewhere like C:\Windows\System32.
_ROOT = Path(__file__).resolve().parent
CACHE_DIR = _ROOT / ".cache"
OUT_DIR = _ROOT / "output"

# How long a cached page stays good, in seconds.

# Job detail pages: fixed the moment a posting goes up, so they cache for hours.
# This is what keeps a re-run cheap — 30 detail pages at the site's 5s
# crawl-delay is ~3 minutes, and that should be paid once, not every run.
CACHE_TTL = 3 * 60 * 60  # 3 hours

# Listing pages: never served from cache. They are the volatile end — new
# postings land at offset 0 and push everything else down, so a cached listing
# page means a run reports the same jobs as last time and silently misses
# everything posted since. Refetching one page costs a single request.
LIST_TTL = 0


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
class GoneError(Exception):
    """The page is permanently unavailable (HTTP 404/410) — an expired posting."""


class Fetcher:
    """Rate-limited, retrying, disk-caching HTTP client using curl_cffi for anti-bot browser impersonation."""

    def __init__(
        self,
        delay: float,
        use_cache: bool = True,
        timeout: int = 30,
        max_age: float | None = CACHE_TTL,
        impersonate: str = "chrome",
    ):
        self.delay = delay
        self.timeout = timeout
        self.use_cache = use_cache
        # None means "never expire" — only sensible for a single manual run.
        self.max_age = max_age
        self.impersonate = impersonate
        self._last_request = 0.0
        if HAS_CURL_CFFI:
            self.session = cffi_requests.Session(impersonate=self.impersonate)
        else:
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
        CACHE_DIR.mkdir(exist_ok=True)

    def _cache_path(self, url: str) -> Path:
        return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".html")

    def _ttl(self, url: str) -> float | None:
        """How long a cached copy of this URL stays good.

        Search-result pages expire immediately (LIST_TTL) because they are what
        changes; a job's detail page is frozen once posted and gets the long
        CACHE_TTL. None means "never expire" — only sensible for a manual run.
        """
        return LIST_TTL if url.startswith(SEARCH_URL) else self.max_age

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.delay - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, retries: int = 3) -> str:
        cached = self._cache_path(url)
        if self.use_cache and cached.exists():
            age = time.time() - cached.stat().st_mtime
            ttl = self._ttl(url)
            if ttl is None or age < ttl:
                return cached.read_text(encoding="utf-8", errors="replace")

        last_err: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_request = time.monotonic()

                if resp.status_code == 200:
                    html = resp.text
                    cached.write_text(html, encoding="utf-8")
                    return html

                # 404/410 are terminal (expired posting); everything else retries.
                if resp.status_code in (404, 410):
                    raise GoneError(f"HTTP {resp.status_code} — {url}")

                last_err = RuntimeError(f"HTTP {resp.status_code}")
            except (GoneError, KeyboardInterrupt):
                raise
            except Exception as exc:
                last_err = exc

            if attempt < retries - 1:
                backoff = (2**attempt) + random.uniform(0, 1)
                print(f"    retry {attempt + 1}/{retries - 1} in {backoff:.1f}s ({last_err})")
                time.sleep(backoff)

        raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def robots_delay() -> float:
    """Read Crawl-delay from robots.txt, defaulting to 5s on any failure."""
    try:
        rp = RobotFileParser()
        if HAS_CURL_CFFI:
            resp = cffi_requests.get(f"{BASE}/robots.txt", timeout=15, impersonate="chrome")
        else:
            resp = requests.get(f"{BASE}/robots.txt", timeout=15, headers={"User-Agent": UA})
        rp.parse(resp.text.splitlines())
        return float(rp.crawl_delay(UA) or 5.0)
    except Exception:
        return 5.0


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_of(node, sep: str = " ") -> str:
    return clean(node.get_text(sep)) if node else ""


@dataclass
class Job:
    job_id: str = ""
    title: str = ""
    url: str = ""
    job_type: str = ""          # badge: Any / Full Time / Part Time / Gig
    wage: str = ""
    posted: str = ""            # from data-temp
    summary: str = ""           # truncated description on the listing page
    tags: list[str] = field(default_factory=list)

    # populated only when detail pages are fetched
    description: str = ""
    hours_per_week: str = ""
    date_updated: str = ""
    categories: list[str] = field(default_factory=list)
    scraped_at: str = ""


def parse_listing(html: str) -> tuple[list[Job], int]:
    """Return (jobs_on_page, total_jobs_available)."""
    soup = BeautifulSoup(html, "html.parser")

    total = 0
    m = re.search(r"Displaying\s+[\d,]+\s+out of\s+([\d,]+)", soup.get_text(" "))
    if m:
        total = int(m.group(1).replace(",", ""))

    jobs: list[Job] = []
    seen: set[str] = set()

    # Each card is a div.jobpost-cat-box; the anchor wrapping it is the job URL.
    for card in soup.select("div.jobpost-cat-box"):
        link = card.find_parent("a", href=True)
        if not link:
            continue
        url = urljoin(BASE, link["href"])
        if url in seen:
            continue
        seen.add(url)

        title_node = card.select_one("h4")
        title = ""
        job_type = ""
        if title_node:
            badge = title_node.select_one("span.badge")
            if badge:
                job_type = clean(badge.get_text())
                badge.extract()
            title = text_of(title_node)

        job = Job(
            title=title,
            url=url,
            job_type=job_type,
            job_id=(re.search(r"-(\d+)/?$", url).group(1) if re.search(r"-(\d+)/?$", url) else ""),
        )

        posted_node = card.select_one("p[data-temp]")
        if posted_node:
            job.posted = posted_node.get("data-temp", "").strip()

        # Wage sits in the <dl> whose <dt> holds the round-dollar icon.
        for dl in card.select("dl"):
            if dl.select_one("i.icon-round-dollar"):
                dd = dl.select_one("dd")
                job.wage = text_of(dd)
                break

        desc = card.select_one("div.desc")
        if desc:
            for a in desc.select("a"):  # drop the trailing "See More" link
                a.extract()
            job.summary = text_of(desc)

        job.tags = [
            clean(a.get_text())
            for a in card.select("div.job-tag a")
            if clean(a.get_text())
        ]

        jobs.append(job)

    return jobs, total


def parse_detail(html: str) -> dict:
    """Extract the extra fields from a public job detail page."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}

    h1 = soup.select_one("h1.job__title")
    if h1:
        if h1.get("data-jobid"):
            out["job_id"] = h1["data-jobid"].strip()
        out["title"] = text_of(h1)

    # Label/value pairs live in <dl> blocks: <h3>LABEL</h3><p>value</p>
    labels = {
        "TYPE OF WORK": "job_type",
        "WAGE / SALARY": "wage",
        "HOURS PER WEEK": "hours_per_week",
        "DATE UPDATED": "date_updated",
    }
    for dl in soup.select("dl"):
        h3 = dl.select_one("h3")
        p = dl.select_one("p")
        if h3 and p:
            key = labels.get(clean(h3.get_text()).upper())
            if key:
                out[key] = text_of(p)

    # Full description lives in <p class="job-description">. The site wraps
    # contact details it filters out in <ojfilter> tags; the text is kept as-is.
    desc_node = soup.select_one("p.job-description, #job-description")
    if desc_node:
        for tag in desc_node.select("script, style"):
            tag.extract()
        text = desc_node.get_text("\n")          # <br> -> newline, keeps paragraphs
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        out["description"] = text.strip()

    # Employer category links, e.g. "VIEW OTHER JOB POSTS FROM: ..."
    cats = []
    for a in soup.select('a[href^="/jobseekers/search/c/"]'):
        t = clean(a.get_text())
        if t and t not in cats:
            cats.append(t)
    if cats:
        out["categories"] = cats

    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def build_search_params(args) -> dict:
    params = {"isFromJobsearchForm": "1"}
    if args.keyword:
        params["jobkeyword"] = args.keyword
    if args.skills:
        params["skill_tags"] = ",".join(args.skills)
    for key, flag in (("gig", "gig"), ("partTime", "part_time"), ("fullTime", "full_time")):
        if getattr(args, flag):
            params[key] = "1"
    return params


def listing_url(offset: int, params: dict) -> str:
    url = SEARCH_URL if offset == 0 else f"{SEARCH_URL}/{offset}"
    qs = urlencode(params)
    return f"{url}?{qs}" if qs else url


def scrape(args) -> list[Job]:
    delay = args.delay
    if delay is None:
        delay = robots_delay()
        print(f"robots.txt crawl-delay: {delay:g}s (override with --delay)")

    impersonate = getattr(args, "impersonate", "chrome")
    fetcher = Fetcher(
        delay=delay,
        use_cache=not args.refresh,
        max_age=CACHE_TTL,
        impersonate=impersonate,
    )
    if HAS_CURL_CFFI:
        print(f"[fetch] using curl_cffi with impersonate='{impersonate}' (anti-bot enabled)")
    else:
        print("[fetch] curl_cffi not installed; falling back to standard requests")

    params = build_search_params(args)

    # limit: 0 or None means all available jobs. If a positive int is given, stop after N.
    limit = args.limit if (args.limit is not None) else DEFAULT_LIMIT
    if args.urls:
        limit = None

    # If in append mode, read existing keys from jobs.xlsx to skip detail fetching for jobs already known
    existing_keys: set[str] = set()
    if args.append and not args.refresh:
        stem = args.stem or "jobs"
        xlsx_path = args.out / f"{stem}.xlsx"
        if xlsx_path.exists():
            try:
                from openpyxl import load_workbook
                loaded = read_existing(xlsx_path, load_workbook)
                if loaded:
                    for row in loaded:
                        k = dedup_key(row)
                        if k:
                            existing_keys.add(k)
                    print(f"[append] found {len(existing_keys)} existing jobs in {xlsx_path.name}")
            except Exception as e:
                print(f"[append] note: could not inspect existing workbook: {e}")

    all_jobs: list[Job] = []
    seen: set[str] = set()
    offset = 0
    total = None

    # --urls skips search entirely and goes straight to the given job pages.
    for u in args.urls or []:
        job = Job(url=u)
        m = re.search(r"-(\d+)/?$", u.rstrip("/"))
        if m:
            job.job_id = m.group(1)
        all_jobs.append(job)
    if args.urls:
        print(f"[urls] {len(args.urls)} job URL(s) given directly — skipping search")

    while not args.urls:
        url = listing_url(offset, params)
        print(f"[list] offset={offset:<5} {url}")
        jobs, page_total = parse_listing(fetcher.get(url))

        if total is None and page_total:
            total = page_total
            pages = (total + PER_PAGE - 1) // PER_PAGE
            print(f"       {total} jobs across ~{pages} pages")

        if not jobs:
            print("       no cards found — stopping")
            break

        fresh = [j for j in jobs if j.url not in seen]
        for j in fresh:
            seen.add(j.url)
        all_jobs.extend(fresh)
        print(f"       +{len(fresh)} new (total {len(all_jobs)})")

        if not fresh:
            print("       no new jobs on page — reached the end")
            break

        offset += PER_PAGE

        if args.max_pages and offset // PER_PAGE >= args.max_pages:
            print(f"       hit --max-pages {args.max_pages}")
            break
        if limit and len(all_jobs) >= limit:
            print(f"       hit --limit {limit}")
            break
        if total and offset >= total:
            print(f"       reached total available ({total})")
            break

    if limit:
        all_jobs = all_jobs[:limit]

    if args.no_details:
        return all_jobs

    to_fetch = [j for j in all_jobs if (j.job_id or j.url) not in existing_keys] if existing_keys else all_jobs
    if existing_keys and len(to_fetch) < len(all_jobs):
        print(f"\n[detail] skipping {len(all_jobs) - len(to_fetch)} already-saved jobs; fetching {len(to_fetch)} new job pages…")
    else:
        print(f"\n[detail] fetching {len(to_fetch)} job pages…")

    scraped_at = time.strftime("%Y-%m-%d %H:%M:%S")
    for i, job in enumerate(to_fetch, 1):
        print(f"  [{i}/{len(to_fetch)}] {job.title[:60]}")
        job.scraped_at = scraped_at
        try:
            detail = parse_detail(fetcher.get(job.url))
        except GoneError:
            # Employer pulled the posting between the search and the detail fetch.
            # Keep the listing-level row rather than dropping the job entirely.
            print("      ! expired (410) — keeping listing data only")
            continue
        except Exception as exc:
            print(f"      ! {exc}")
            continue
        for key, value in detail.items():
            if value:
                setattr(job, key, value)

    return all_jobs


COLUMNS = [
    ("job_id", "Job ID", 12),
    ("title", "Title", 40),
    ("job_type", "Job Type", 12),
    ("wage", "Wage / Salary", 16),
    ("hours_per_week", "Hours / Week", 12),
    ("date_updated", "Date Updated", 14),
    ("posted", "Posted", 19),
    ("tags", "Tags", 26),
    ("categories", "Categories", 26),
    ("url", "URL", 42),
    ("description", "Job Overview (full)", 80),
    ("summary", "Listing Summary", 45),
    ("scraped_at", "Scraped At", 19),
]


def dedup_key(data: dict) -> str:
    """What makes two rows the same job. job_id is the real identity; url is
    the fallback for the rare card where the id didn't parse out of the link."""
    return str(data.get("job_id") or data.get("url") or "").strip()


def sideline(path: Path) -> Path:
    """Move a workbook we can't append into out of the way. Nothing is lost."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}-{stamp}-{n}{path.suffix}")
        n += 1
    path.rename(target)
    return target


def read_existing(path: Path, load_workbook) -> list[dict] | None:
    """Read back a workbook this version wrote, as row dicts.

    Returns None when the header isn't exactly COLUMNS — an older layout, or not
    ours at all. Appending into that would shift every column one to the left
    and do it silently, which is worse than starting over.
    """
    wb = load_workbook(path)
    ws = wb.active
    header = [ws.cell(row=1, column=c).value for c in range(1, len(COLUMNS) + 1)]
    if header != [c[1] for c in COLUMNS] or ws.max_column != len(COLUMNS):
        wb.close()
        return None

    rows: list[dict] = []
    for r in range(2, ws.max_row + 1):
        values = [ws.cell(row=r, column=c).value for c in range(1, len(COLUMNS) + 1)]
        if all(v is None for v in values):
            continue
        rows.append({key: values[i] for i, (key, _, _) in enumerate(COLUMNS)})
    wb.close()
    return rows


def write_xlsx(
    jobs: list[Job],
    out_dir: Path,
    stem: str,
    append: bool = False,
) -> tuple[Path, list[Job]]:
    """Write a formatted workbook — one row per job, full overview wrapped.

    With `append`, an existing `<stem>.xlsx` is read back and only jobs whose
    key isn't already on the sheet are added. Returns the path written and the
    jobs that were genuinely new, which is what the webhook reports — so a
    quiet night can be told apart from a busy one.

    The sheet is rebuilt rather than appended into, so existing and new rows
    are styled by the same code and can't drift apart.
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    path = out_dir / f"{stem}.xlsx"
    deferred = path.with_name(f"{path.stem}-pending{path.suffix}")

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="2F5496")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    top_wrap = Alignment(vertical="top", wrap_text=True)
    top_plain = Alignment(vertical="top")

    def style_row(ws, row: int, data: dict) -> None:
        for col, (key, _, _) in enumerate(COLUMNS, start=1):
            value = data.get(key, "")
            if isinstance(value, list):
                value = ", ".join(value)

            cell = ws.cell(row=row, column=col, value=value)
            cell.border = border
            cell.alignment = top_plain

            if key == "description":
                cell.alignment = top_wrap
            elif key == "url" and value:
                cell.hyperlink = value
                cell.font = Font(color="0563C1", underline="single")
                cell.alignment = top_wrap
            elif key in ("summary", "tags", "categories"):
                cell.alignment = top_wrap
            elif key in ("job_id", "job_type", "hours_per_week"):
                cell.alignment = Alignment(vertical="top", horizontal="center")

        # Let Excel size the row to the wrapped overview text.
        ws.row_dimensions[row].height = None

    # ---- what's already on disk ---------------------------------------
    previous: list[dict] = []
    seen: set[str] = set()

    if append:
        # Both files, because a run that hit a locked jobs.xlsx wrote its rows
        # to the -pending copy. Reading only jobs.xlsx would strand them there
        # forever, and every later run would re-scrape the same jobs.
        for source in (path, deferred):
            if not source.exists():
                continue
            loaded = read_existing(source, load_workbook)
            if loaded is None:
                moved = sideline(source)
                print(f"  ! {source.name} wasn't written by this version — "
                      f"moved to {moved.name}, starting a fresh sheet")
                continue
            for data in loaded:
                key = dedup_key(data)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                previous.append(data)

    fresh: list[Job] = []
    for job in jobs:
        key = job.job_id or job.url
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        fresh.append(job)

    # ---- build ---------------------------------------------------------
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"

    for col, (_, label, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        cell.border = border
        ws.column_dimensions[get_column_letter(col)].width = width

    row = 2
    for data in previous:
        style_row(ws, row, data)
        row += 1
    for job in fresh:
        style_row(ws, row, asdict(job))
        row += 1

    last = row - 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(last, 1)}"

    # One rolling copy of the state we're about to replace. Appending should only
    # ever add rows — but this sheet is the whole point of the project, and if a
    # rewrite ever does go wrong there is no undo. Bounded at one file: each run
    # overwrites the backup, so it can't turn into a pile.
    if append and path.exists():
        try:
            shutil.copy2(path, path.with_name(f"{path.stem}-backup{path.suffix}"))
        except OSError:
            pass

    try:
        wb.save(path)
    except PermissionError:
        # Almost always the file being open in Excel. Don't lose the run over it.
        # Fixed name, deliberately not timestamped. A nightly run would
        # otherwise leave a growing pile of jobs-HHMMSS.xlsx files, and the one
        # you actually open — jobs.xlsx — would silently keep yesterday's rows
        # while every log said the run succeeded.
        wb.save(deferred)
        print(f"  ! {path.name} is locked (open in Excel?) — wrote {deferred.name} instead")
        return deferred, fresh

    # The save landed, so jobs.xlsx now holds everything the deferred copy did.
    if append and deferred.exists():
        try:
            deferred.unlink()
        except OSError:
            pass
    return path, fresh


def write_outputs(
    jobs: list[Job],
    out_dir: Path,
    want_xlsx: bool = True,
    stem: str | None = None,
    append: bool = False,
) -> tuple[list[Path], list[Job]]:
    """Pass `stem` to pin a fixed filename (e.g. "jobs") instead of a timestamp.

    Returns (paths, new_jobs). In append mode the .jsonl/.csv are skipped: they
    record what a single run saw, and rewriting them from the accumulated sheet
    would claim this run found jobs it never looked at.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"jobs-{time.strftime('%Y%m%d-%H%M%S')}"
    paths: list[Path] = []
    fresh = list(jobs)

    if not append:
        jsonl_path = out_dir / f"{stem}.jsonl"
        csv_path = out_dir / f"{stem}.csv"

        with jsonl_path.open("w", encoding="utf-8") as fh:
            for job in jobs:
                fh.write(json.dumps(asdict(job), ensure_ascii=False) + "\n")

        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            fields = list(asdict(jobs[0]).keys()) if jobs else [c[0] for c in COLUMNS]
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for job in jobs:
                row = asdict(job)
                row["tags"] = ", ".join(row["tags"])
                row["categories"] = ", ".join(row["categories"])
                row["description"] = row["description"].replace("\n", "\\n")
                writer.writerow(row)

        paths += [jsonl_path, csv_path]

    if want_xlsx:
        try:
            xlsx_path, fresh = write_xlsx(jobs, out_dir, stem, append=append)
        except ImportError:
            print("  (openpyxl not installed — skipping .xlsx; pip install openpyxl)")
        else:
            paths.append(xlsx_path)

    return paths, fresh


def post_webhook(url: str, token: str | None, payload: dict) -> bool:
    """Hand the new jobs to n8n.

    Failure here is reported but never fatal — the spreadsheet is already
    saved, and losing an alert is not worth failing the run over. Exit status
    stays 0 so a flaky n8n doesn't look like a broken scrape.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Webhook-Token"] = token
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        print(f"  ! webhook failed: {exc}")
        return False
    if resp.status_code >= 300:
        print(f"  ! webhook returned HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    print(f"  webhook ok (HTTP {resp.status_code})")
    return True


def main() -> int:
    p = argparse.ArgumentParser(
        description="Scrape public job listings from OnlineJobs.ph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--keyword", help="search job title / company")
    p.add_argument("--skills", nargs="+", metavar="SKILL", help="skill tags (max 3 on the site)")
    p.add_argument("--gig", action="store_true", help="include Gig listings")
    p.add_argument("--part-time", action="store_true", help="include Part-Time listings")
    p.add_argument("--full-time", action="store_true", help="include Full-Time listings")
    p.add_argument("--urls", nargs="+", metavar="URL",
                   help="scrape these job detail URLs directly instead of searching")
    p.add_argument("--limit", type=int,
                   help=f"stop after N jobs (default {DEFAULT_LIMIT} = all available, 0 = no limit)")
    p.add_argument("--impersonate", default="chrome",
                   help="browser to impersonate for anti-bot via curl_cffi (default: chrome)")
    p.add_argument("--max-pages", type=int, help="stop after N listing pages")
    p.add_argument("--no-details", action="store_true",
                   help="skip detail pages (listing data only — much faster)")
    p.add_argument("--delay", type=float,
                   help="seconds between requests (default: robots.txt crawl-delay)")
    p.add_argument("--refresh", action="store_true", help="ignore the HTML cache")
    p.add_argument("--no-xlsx", action="store_true", help="skip the .xlsx workbook")
    p.add_argument("--append", action="store_true",
                   help="add only jobs not already in jobs.xlsx, instead of a new file")
    p.add_argument("--stem",
                   help="pin the output filename (defaults to jobs when --append)")
    p.add_argument("--webhook", metavar="URL",
                   help="POST the new jobs to this n8n webhook after writing")
    p.add_argument("--webhook-token", metavar="TOKEN",
                   help="sent as the X-Webhook-Token header (pair with n8n Header Auth)")
    p.add_argument("--out", type=Path, default=OUT_DIR, help="output directory")
    args = p.parse_args()

    if not (args.gig or args.part_time or args.full_time):
        # The site sends all three checked by default.
        args.gig = args.part_time = args.full_time = True

    if args.urls and args.no_details:
        # Title and every overview field come from the detail page.
        print("note: --urls always fetches detail pages; ignoring --no-details")
        args.no_details = False

    if args.append and args.no_xlsx:
        # --append accumulates into the workbook. Without it the "already seen"
        # set is empty every run, so every job would report as new.
        print("note: --append needs the workbook; ignoring --no-xlsx")
        args.no_xlsx = False

    # --append implies a stable filename — a timestamped one would be a brand
    # new file each run, so nothing would ever accumulate.
    stem = args.stem or ("jobs" if args.append else None)

    try:
        jobs = scrape(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    if not jobs:
        print("No jobs scraped.", file=sys.stderr)
        return 1

    paths, fresh = write_outputs(
        jobs, args.out, want_xlsx=not args.no_xlsx, stem=stem, append=args.append
    )

    if args.append:
        print(f"\n{len(jobs)} scraped — {len(fresh)} new, "
              f"{len(jobs) - len(fresh)} already in the sheet:")
    else:
        print(f"\n{len(jobs)} jobs written:")
    for path in paths:
        print(f"  {path}")

    if args.webhook:
        if not fresh:
            print("  webhook skipped — nothing new this run")
        else:
            post_webhook(args.webhook, args.webhook_token, {
                "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "scraped": len(jobs),
                "new": len(fresh),
                "skipped_existing": len(jobs) - len(fresh),
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "title": j.title,
                        "url": j.url,
                        "job_type": j.job_type,
                        "wage": j.wage,
                        "posted": j.posted,
                        "tags": j.tags,
                        "description": j.description,
                    }
                    for j in fresh
                ],
            })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
