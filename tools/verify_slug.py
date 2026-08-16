"""Prove a board exists before it enters companies.json.

    python tools/verify_slug.py "Charter Communications" greenhouse spectrum
    python tools/verify_slug.py "AdventHealth" icims adventhealth \
        --host careers-adventhealth.icims.com
    python tools/verify_slug.py --json "Verizon" workday verizon --site careers --wd wd1

Fetches the board through the real connector and the real Net client — same
rate limiting, same retries, same parsing — then reports:

    ok            the endpoint returned a payload the connector could parse
    total         postings the board returned
    intern        of those, how many read as an internship or co-op
    relevant      of those, how many pass this fork's security/networking gate
    complete      whether the snapshot was whole (a capped read is not proof)

Exit code is 0 only when the board parsed AND returned at least one posting, so
this can gate a scripted registry addition. A board that parses but has zero
intern postings today still exits 0 with `--allow-empty`: employers post
seasonally, and an empty August board is not a wrong slug.

Nothing here writes to the registry. Adding the company is a separate, manual
step — the point of this tool is that the line you paste in was checked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from intern_engine import filters_secnet, pipeline, registry  # noqa: E402
from intern_engine.models import Fetch  # noqa: E402
from intern_engine.net import HostLimiter, Net  # noqa: E402

TIMEOUT = 30.0


def _company(args) -> dict:
    """The registry record shape this ATS needs, from the CLI flags."""
    company = {"name": args.name, "ats": args.ats, "slug": args.slug}
    for field in ("site", "wd", "host", "board_key"):
        value = getattr(args, field, None)
        if value:
            company[field] = value
    return company


async def _fetch(company: dict) -> Fetch:
    connector = pipeline.CONNECTORS[company["ats"]]
    limiter = HostLimiter()
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True,
    ) as client:
        return Fetch.of(await connector(company, Net(client, limiter)))


def check(company: dict) -> dict:
    """Fetch one board and describe what came back."""
    try:
        company = registry.validate_company(company)
    except registry.RegistryCorrupt as exc:
        return {"ok": False, "error": f"registry shape: {exc}"}

    try:
        result = asyncio.run(_fetch(company))
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    interns = [j for j in result.jobs if filters_secnet.is_internship(j.title)]
    relevant = [j for j in interns if filters_secnet.is_relevant(j.title)]
    return {
        "ok": True,
        "total": len(result.jobs),
        "intern": len(interns),
        "relevant": len(relevant),
        "complete": result.complete,
        "incomplete_reason": result.incomplete_reason,
        "sample": [j.title for j in relevant[:5]] or [j.title for j in interns[:5]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("ats", choices=sorted(pipeline.CONNECTORS))
    parser.add_argument("slug")
    parser.add_argument("--site", help="Workday/Oracle career site")
    parser.add_argument("--wd", help="Workday cluster, e.g. wd1")
    parser.add_argument("--host", help="explicit board host")
    parser.add_argument("--board-key", dest="board_key")
    parser.add_argument("--allow-empty", action="store_true",
                        help="exit 0 when the board parses but lists nothing")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    company = _company(args)
    report = check(company)

    if args.as_json:
        print(json.dumps({**report, "company": company}, indent=2))
    elif not report["ok"]:
        print(f"FAIL  {args.name} [{args.ats}:{args.slug}] — {report['error']}")
    else:
        flag = "" if report["complete"] else \
            f"  (partial: {report['incomplete_reason']})"
        print(f"OK    {args.name} [{args.ats}:{args.slug}]{flag}")
        print(f"      {report['total']} postings · {report['intern']} intern/co-op "
              f"· {report['relevant']} in scope")
        for title in report["sample"]:
            print(f"        - {title}")

    if not report["ok"]:
        return 1
    return 0 if (report["total"] or args.allow_empty) else 2


if __name__ == "__main__":
    raise SystemExit(main())
