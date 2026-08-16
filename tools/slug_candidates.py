"""Guess the (ats, slug) pairs a company's board is most likely to live at.

    python tools/slug_candidates.py "Palo Alto Networks"
    python tools/slug_candidates.py --json "Cox Communications"
    python tools/slug_candidates.py --batch data/slug_targets.txt \
        --out data/slug_candidates.json
    python tools/slug_candidates.py --batch data/slug_targets.txt --verify-script

These are GUESSES and nothing here touches companies.json. Every candidate is
emitted with the exact tools/verify_slug.py command that would prove or kill it,
because a guessed slug is only useful as an input to verification.

Patterns encoded here, in order of how well they hold up:

  icims       careers-<name>.icims.com — confirmed across Charter
              (careers-charter), Avantus, SOSi, Navitus and DMI. This is the
              strongest single pattern we have, so it ranks first for any
              employer that looks like an iCIMS customer.
  greenhouse  the lowercase name with punctuation and spaces removed.
  lever       same shape as greenhouse; both also see the hyphenated form.
  ashby       lowercase, concatenated.
  workday     tenant is the concatenated name, but a Workday board is only
              addressable with a site AND a cluster (wd1..wd12), neither of
              which is derivable from the name. Sites are guessed from the
              shapes tenants actually use; the cluster has to be observed.

An acronym candidate is generated for names of three or more words, which is
how public-sector employers usually name their boards (Orange County Public
Schools really is `ocps`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# Only the suffixes that are genuinely noise in a board slug. "Networks" and
# "Systems" are deliberately absent: they are part of the name people register
# (paloaltonetworks), so stripping them would demote the likeliest candidate.
_SUFFIXES = (
    "incorporated", "inc", "llc", "l.l.c", "corporation", "corp",
    "communications", "technologies", "technology", "company", "co",
    "holdings", "group", "ltd", "limited", "plc",
)

# Words that never belong in an acronym.
_STOPWORDS = {"of", "the", "and", "for", "at", "de"}

_WORKDAY_SITES = ("External", "Careers", "{slug}careers", "{Slug}_Careers")
# Clusters we have actually seen in this registry, most common first. A Workday
# board cannot be resolved without trying these.
_WORKDAY_CLUSTERS = ("wd1", "wd5", "wd3", "wd12")


def _words(name: str) -> list[str]:
    """The name's words, with punctuation removed but '&' preserved as a word."""
    cleaned = name.replace("&", " and ")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return [w for w in cleaned.lower().split() if w]


def _strip_suffixes(words: list[str]) -> list[str]:
    out = list(words)
    while len(out) > 1 and out[-1] in _SUFFIXES:
        out.pop()
    return out


def _forms(name: str) -> dict[str, list[str]]:
    """Base string forms for a name, longest/most literal first."""
    words = _words(name)
    core = _strip_suffixes(words)
    # "AT&T" -> att is far likelier than atandt, so drop the expanded 'and' too.
    no_and = [w for w in words if w != "and"] or words
    core_no_and = [w for w in core if w != "and"] or core

    # A name containing "&" is registered with the symbol dropped far more
    # often than spelled out — AT&T's board is `att`, never `atandt` — so the
    # dropped form leads whenever there was an ampersand to drop.
    order = ((no_and, core_no_and, words, core) if "&" in name
             else (words, core, no_and, core_no_and))
    joined, hyphenated = [], []
    for variant in order:
        joined.append("".join(variant))
        hyphenated.append("-".join(variant))

    acronym = ""
    meaningful = [w for w in core if w not in _STOPWORDS]
    if len(meaningful) >= 3:
        acronym = "".join(w[0] for w in meaningful)

    return {
        "joined": _dedupe(joined),
        "hyphenated": _dedupe(hyphenated),
        "acronym": [acronym] if acronym else [],
    }


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _verify_cmd(name: str, ats: str, slug: str, extra: dict) -> str:
    parts = ["python", "tools/verify_slug.py", f'"{name}"', ats, slug]
    for flag, key in (("--host", "host"), ("--site", "site"), ("--wd", "wd")):
        if extra.get(key):
            parts += [flag, extra[key]]
    return " ".join(parts)


def candidates(name: str, limit: int | None = None) -> list[dict]:
    """Ranked (ats, slug) guesses for one company, best first."""
    forms = _forms(name)
    joined, hyphenated, acronym = (
        forms["joined"], forms["hyphenated"], forms["acronym"],
    )
    primary = joined[0]

    out: list[dict] = []

    def add(ats, slug, why, **extra):
        if not slug:
            return
        out.append({"ats": ats, "slug": slug, "why": why, **extra})

    # iCIMS first: careers-<name> is the pattern we have the most evidence for.
    for form in joined[:2]:
        add("icims", f"careers-{form}", "confirmed iCIMS portal pattern",
            host=f"careers-{form}.icims.com")
    # The acronym sits high on purpose: a long multi-word public-sector name is
    # more likely to be registered short than in full (Orange County Public
    # Schools is `ocps`), and burying it below every spelling variant of the
    # full name would have ranked the right answer off the end of the list.
    if acronym:
        add("icims", f"careers-{acronym[0]}", "acronym, common for public sector",
            host=f"careers-{acronym[0]}.icims.com")
    for form in hyphenated[:1]:
        if form != joined[0]:
            add("icims", f"careers-{form}", "iCIMS pattern, hyphenated name",
                host=f"careers-{form}.icims.com")
    add("icims", primary, "iCIMS portal without the careers- prefix",
        host=f"{primary}.icims.com")

    for form in joined[:2]:
        add("greenhouse", form, "Greenhouse slug is usually the bare name")
    if acronym:
        add("greenhouse", acronym[0], "acronym, common for public sector")
    for form in hyphenated[:1]:
        if form != joined[0]:
            add("greenhouse", form, "Greenhouse slug, hyphenated name")

    for form in joined[:2]:
        add("lever", form, "Lever slug is usually the bare name")
    add("ashby", primary, "Ashby slug is usually the bare name")

    # Workday needs a site and a cluster; neither is derivable from the name.
    for site_pattern in _WORKDAY_SITES:
        site = site_pattern.format(slug=primary, Slug=primary.capitalize())
        add("workday", primary,
            f"Workday tenant + common site; try clusters {'/'.join(_WORKDAY_CLUSTERS)}",
            site=site, wd=_WORKDAY_CLUSTERS[0])

    ranked = []
    seen: set[tuple] = set()
    for row in out:
        key = (row["ats"], row["slug"], row.get("site"))
        if key in seen:
            continue
        seen.add(key)
        row["rank"] = len(ranked) + 1
        row["verify"] = _verify_cmd(name, row["ats"], row["slug"], row)
        ranked.append(row)
    return ranked[:limit] if limit else ranked


def _print(name: str, rows: list[dict]) -> None:
    print(f"\n{name}")
    for row in rows:
        extra = " ".join(
            f"{k}={row[k]}" for k in ("host", "site", "wd") if row.get(k)
        )
        print(f"  {row['rank']:>2}. {row['ats']:<12} {row['slug']:<28} {extra}")
        print(f"      {row['why']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", nargs="?")
    parser.add_argument("--batch", help="file with one company name per line")
    parser.add_argument("--out", help="write a JSON candidates file")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--verify-script", action="store_true",
                        help="print runnable verify_slug.py commands")
    parser.add_argument("--confirmed",
                        help="JSON of already-known boards to pre-fill instead "
                             "of guessing")
    args = parser.parse_args()

    confirmed = {}
    if args.confirmed:
        with open(args.confirmed, encoding="utf-8") as f:
            confirmed = json.load(f)

    if args.batch:
        with open(args.batch, encoding="utf-8") as f:
            names = [ln.strip() for ln in f
                     if ln.strip() and not ln.startswith("#")]
    elif args.name:
        names = [args.name]
    else:
        parser.error("give a company name or --batch")

    report = {}
    for name in names:
        known = confirmed.get(name)
        if known:
            # Already proven: emit the one real board, not a pile of guesses.
            row = {"rank": 0, "status": "confirmed", "why": "already verified",
                   **known}
            row["verify"] = _verify_cmd(name, row["ats"], row["slug"], row)
            report[name] = [row]
        else:
            report[name] = [{**row, "status": "guess"}
                            for row in candidates(name, args.limit)]

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
        total = sum(len(v) for v in report.values())
        print(f"{len(report)} companies · {total} candidates -> {args.out}")
    elif args.verify_script:
        print("#!/bin/sh")
        print("# Stops at the first board that parses for each company.")
        for name, rows in report.items():
            print(f"\n# --- {name} ---")
            for row in rows:
                print(row["verify"])
    elif args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for name, rows in report.items():
            _print(name, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
