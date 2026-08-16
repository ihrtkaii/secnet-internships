"""iCIMS job portals: the ATS behind carriers, hospitals, utilities and schools.

Portals live on a per-customer host — usually `{slug}.icims.com`, sometimes
`careers-{slug}.icims.com` or a vanity domain — so the registry may pin one
explicitly with `host`. The public search endpoint is paginated with a 0-based
`pr` page cursor.

UNVERIFIED FIELD NAMES. This module was written without live access to an iCIMS
portal (egress policy blocked every careers host), so the *container* is treated
as strict and the *fields* as tolerant: we require a real `jobs` list before
trusting a payload at all, but read each value through a short list of the key
spellings iCIMS portals are known to use. If the shape turns out to be wrong,
`clean_listing` returns None, the fetch is marked malformed/incomplete, and the
pipeline refuses to close anything — the failure is loud in health metrics and
harmless to the store. Do one live smoke test before trusting this source.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from ..models import (
    INCOMPLETE_CAPPED,
    INCOMPLETE_MALFORMED,
    INCOMPLETE_STALLED,
    Fetch,
    Job,
    clean_listing,
    source_board_key,
)
from ..net import Net

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

_PAGE_SIZE = 50    # portal default; the cursor is a page number, not an offset
_MAX_PAGES = 6     # 300 postings per term is far past any real intern listing
# iCIMS matches the keyword literally, so "intern" alone misses "Co-Op
# Technician" — the same split Workday needs.
_SEARCH_TERMS = ("intern", "co-op")

_ISO_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Key spellings seen across iCIMS portal versions, best first.
_ID_KEYS = ("jobId", "id", "jobPostId")
_TITLE_KEYS = ("jobTitle", "title")
_LOCATION_KEYS = ("jobLocation", "location", "locationDescription")
_URL_KEYS = ("jobUrl", "url", "applyUrl")
_DATE_KEYS = ("postedDate", "datePosted", "postingDate")
_REQ_KEYS = ("jobReqId", "requisitionId", "externalJobId")


def _host(company: dict) -> str:
    """The portal host, explicit when the registry pinned one."""
    host = (company.get("host") or "").strip()
    return host or f"{company['slug']}.icims.com"


def _first(posting: dict, keys: tuple[str, ...]) -> str | None:
    """The first key that carries a usable scalar, or None."""
    for key in keys:
        value = posting.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _location(posting: dict) -> str:
    """A display location, flattening the object form some portals send."""
    for key in _LOCATION_KEYS:
        value = posting.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            parts = [
                str(value.get(part)).strip()
                for part in ("city", "state", "country")
                if isinstance(value.get(part), str) and value.get(part).strip()
            ]
            if parts:
                return ", ".join(parts)
    return "—"


def _posted(posting: dict) -> str | None:
    """An ISO day from the portal's date field, or None.

    Portals render dates in several human formats ("Posted 3 weeks ago",
    "August 1, 2026") and only some emit a machine date. Anything we cannot
    read unambiguously stays None: a guessed Posted date silently reorders a
    page whose whole promise is newest-first.
    """
    raw = _first(posting, _DATE_KEYS)
    if not raw:
        return None
    match = _ISO_DAY.match(raw)
    if not match:
        return None
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return f"{match.group(1)}T00:00:00Z"


async def fetch(company: dict, net: Net) -> Fetch:
    slug = company["slug"]
    host = _host(company)
    base = f"https://{host}/"
    search_url = f"https://{host}/jobs/search"

    jobs: dict[str, Job] = {}   # the two search terms overlap
    incomplete_reason: str | None = None

    for term in _SEARCH_TERMS:
        exhausted = False
        seen_pages: set[tuple[str, ...]] = set()
        for page in range(_MAX_PAGES):
            params = {
                "ss": 1,             # portal's "show search results" flag
                "searchKeyword": term,
                "pr": page,          # 0-based page cursor
                "in_iframe": 1,
                "format": "json",
            }
            data = await net.get_json(search_url, params=params, headers=HEADERS)
            postings = clean_listing(data, "jobs")
            if postings is None:
                # Malformed 200 or an error envelope. Leave the snapshot
                # partial so nothing this employer posted can be closed.
                incomplete_reason = INCOMPLETE_MALFORMED
                break

            signature = tuple(
                str(_first(posting, _ID_KEYS) or _first(posting, _TITLE_KEYS) or "")
                for posting in postings
            )
            if postings and signature in seen_pages:
                # Some portals ignore `pr` and serve page one forever. That is
                # pagination failure, not a result cap, and it is not safe
                # evidence for closing anything.
                incomplete_reason = INCOMPLETE_STALLED
                break
            seen_pages.add(signature)

            for posting in postings:
                job_id = _first(posting, _ID_KEYS)
                if not job_id:
                    continue   # no stable identity: Fetch.of would reject it anyway
                href = _first(posting, _URL_KEYS) or f"/jobs/{job_id}/job"
                requisition = _first(posting, _REQ_KEYS)
                job = Job(
                    id=f"icims:{slug}:{job_id}",
                    source="icims",
                    company=company["name"],
                    company_slug=slug,
                    title=_first(posting, _TITLE_KEYS) or "",
                    location=_location(posting),
                    url=urljoin(base, href),
                    posted_at=_posted(posting),
                    board_key=source_board_key(company, "icims", slug),
                    # No canonical identity claimed: one iCIMS requisition can
                    # appear on several portals, but we have no cross-portal
                    # evidence here, and a wrong merge deletes a real opening.
                    canonical_id=None,
                    requisition_id=requisition,
                )
                jobs.setdefault(job.id, job)

            if len(postings) < _PAGE_SIZE:
                exhausted = True   # last page of this term
                break

        if not exhausted:
            incomplete_reason = incomplete_reason or INCOMPLETE_CAPPED

    return Fetch(
        list(jobs.values()),
        complete=incomplete_reason is None,
        incomplete_reason=incomplete_reason,
    )
