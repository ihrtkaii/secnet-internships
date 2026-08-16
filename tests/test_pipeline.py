import asyncio
import json
from datetime import UTC, datetime

import pytest

from intern_engine import filters, models, paths, pipeline, store
from intern_engine.models import Fetch
from intern_engine.pipeline import (
    _detection_latency,
    _fetch_health_error,
    _parse_iso,
    _partial_reason_counts,
)


def test_board_deadline_releases_a_stuck_fetch(monkeypatch):
    async def slow_fetch(_company, _net):
        await asyncio.sleep(1)
        return []

    monkeypatch.setitem(pipeline.CONNECTORS, "greenhouse", slow_fetch)
    monkeypatch.setattr(pipeline, "BOARD_TIMEOUT_SECONDS", 0.001)
    company = {"name": "Acme", "slug": "acme", "ats": "greenhouse"}

    got_company, result, error = asyncio.run(
        pipeline._fetch_bounded(company, object())
    )

    assert got_company == company
    assert result.complete is False
    assert error == "board deadline exceeded (0.001s)"


class TestParseIso:
    def test_z_suffix_is_utc_aware(self):
        dt = _parse_iso("2026-07-15T21:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_naive_and_date_only_assumed_utc(self):
        # Some ATS feeds ship timestamps with no offset (or a bare date);
        # they must come back aware or datetime arithmetic explodes.
        for raw in ("2026-07-15T18:00:00", "2026-07-15"):
            dt = _parse_iso(raw)
            assert dt.tzinfo is UTC

    def test_offset_normalized_to_utc(self):
        dt = _parse_iso("2026-07-15T14:00:00-04:00")
        assert dt.hour == 18
        assert dt.tzinfo is not None

    def test_garbage_is_none(self):
        assert _parse_iso("not a date") is None
        assert _parse_iso(None) is None


class TestFetchOutcomes:
    def test_expected_result_cap_is_not_a_health_failure(self):
        result = Fetch(complete=False, incomplete_reason=models.INCOMPLETE_CAPPED)
        assert _fetch_health_error(result, None) is None

    @pytest.mark.parametrize(
        "reason", [models.INCOMPLETE_MALFORMED, models.INCOMPLETE_STALLED],
    )
    def test_malformed_and_stalled_snapshots_are_health_failures(self, reason):
        result = Fetch(complete=False, incomplete_reason=reason)
        assert _fetch_health_error(result, None) == reason

    def test_usable_partial_reasons_are_counted_but_fatal_fetches_are_not(self):
        results = [
            ({}, Fetch(complete=False, incomplete_reason=models.INCOMPLETE_CAPPED), None),
            ({}, Fetch(complete=False, incomplete_reason=models.INCOMPLETE_MALFORMED), None),
            ({}, Fetch(complete=False, incomplete_reason=models.INCOMPLETE_STALLED), "boom"),
        ]
        assert _partial_reason_counts(results) == {
            models.INCOMPLETE_CAPPED: 1,
            models.INCOMPLETE_MALFORMED: 1,
        }


def test_partial_out_of_scope_alias_closes_the_stored_owner():
    existing = {
        "greenhouse:old:1": {
            "id": "greenhouse:old:1",
            "source": "greenhouse",
            "company": "Acme",
            "company_slug": "old",
            "title": "Network Engineer Intern",
            "location": "Austin, TX",
            "url": "https://example.com/old",
            "season": "Summer 2027",
            "is_open": True,
            "first_seen_at": "2026-07-01T00:00:00Z",
            "last_seen_at": "2026-07-01T00:00:00Z",
            "canonical_id": "canonical-1",
            "aliases": ["greenhouse:new:2"],
        },
    }
    alias = models.Job(
        id="greenhouse:new:2", source="greenhouse", company="Acme",
        company_slug="new", title="Network Engineer Intern",
        location="Paris, France", url="https://example.com/new",
        canonical_id="canonical-1",
    )
    results = [
        (
            {"ats": "greenhouse", "slug": "new", "name": "Acme"},
            Fetch([alias], complete=False, incomplete_reason=models.INCOMPLETE_CAPPED),
            None,
        ),
    ]

    kept, _ok, complete, seen, *_rest = pipeline._keep_matching(
        results,
        {"cycles": ["Summer 2027"], "regions": ["US"], "role_scope": "tech"},
        {},
        existing,
    )
    store.upsert(existing, [vars(job) for job in kept], complete, seen_ids=seen)

    assert kept == []
    assert existing["greenhouse:old:1"]["is_open"] is False
    assert existing["greenhouse:old:1"]["closed_reason"] == "out-of-scope"


def test_alias_change_reuses_the_stored_verified_cycle():
    existing = {
        "greenhouse:old:1": {
            "id": "greenhouse:old:1",
            "source": "greenhouse",
            "company": "Acme",
            "company_slug": "old",
            "title": "Network Engineer Intern",
            "location": "Austin, TX",
            "url": "https://example.com/old",
            "season": "Summer 2027",
            "season_inferred": False,
            "is_open": True,
            "first_seen_at": "2026-07-01T00:00:00Z",
            "last_seen_at": "2026-07-01T00:00:00Z",
            "canonical_id": "canonical-1",
            "aliases": ["greenhouse:new:2"],
        },
    }
    alias = models.Job(
        id="greenhouse:new:2", source="greenhouse", company="Acme",
        company_slug="new", title="Network Engineer Intern",
        location="Austin, TX", url="https://example.com/new",
        canonical_id="canonical-1",
    )

    kept, *_rest = pipeline._keep_matching(
        [(
            {"ats": "greenhouse", "slug": "new", "name": "Acme"},
            Fetch([alias], complete=True),
            None,
        )],
        {"cycles": ["Summer 2027"], "regions": ["US"], "role_scope": "tech"},
        {},
        existing,
    )

    assert len(kept) == 1
    assert kept[0].season == "Summer 2027"
    assert kept[0].season_inferred is False


class TestStatsState:
    def test_stats_write_is_atomic_and_preserves_fetch_time(self, tmp_path, monkeypatch):
        path = tmp_path / "stats.json"
        monkeypatch.setattr(paths, "STATS_PATH", str(path))
        stats = {"generated_at": "2026-08-06T20:00:00Z", "open_total": 1}

        pipeline.write_stats(stats)

        loaded = pipeline.load_stats(str(path))
        assert loaded["fetched_at"] == "2026-08-06T20:00:00Z"
        assert not any(tmp_path.glob("*.tmp"))

    def test_corrupt_stats_are_fatal_instead_of_replaced_with_now(self, tmp_path):
        path = tmp_path / "stats.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(pipeline.StatsCorrupt):
            pipeline.load_stats(str(path))


class TestDetectionLatency:
    # A fixed "now" so the window is measured against the fixtures, not the
    # day the suite happens to run.
    NOW = datetime(2026, 7, 16, tzinfo=UTC)

    def test_mixed_naive_and_aware_does_not_crash(self):
        # Regression: a single offset-less posted_at next to a Z-suffixed
        # first_seen_at crashed every run with TypeError (2026-07-15 outage).
        existing = {
            "a": {"posted_at": "2026-07-15T18:00:00",   # naive from the ATS
                  "first_seen_at": "2026-07-15T21:00:00Z"},
            "b": {"posted_at": "2026-07-15T10:00:00Z",
                  "first_seen_at": "2026-07-15T11:00:00Z"},
        }
        out = _detection_latency(existing, now=self.NOW)
        assert out["sample_size"] == 2
        assert out["median_minutes"] == 120.0  # median of 180 and 60

    def test_empty_store(self):
        out = _detection_latency({})
        assert out["median_minutes"] is None
        assert out["p95_minutes"] is None
        assert out["sample_size"] == 0
        assert out["window_days"] == 7

    def test_old_postings_outside_window_excluded(self):
        # The window bounds when the role was PUBLISHED. It used to bound the
        # detection delay instead, so a role published in January and first
        # seen in July counted as a 6-day "detection".
        existing = {
            "old": {"posted_at": "2026-01-01T09:30:00Z",
                    "first_seen_at": "2026-01-07T09:30:00Z"},
        }
        assert _detection_latency(existing, now=self.NOW)["sample_size"] == 0

    def test_date_only_timestamps_are_excluded(self):
        # A bare date is assumed midnight; measuring minutes against it invents
        # up to 24h of latency. These roles are out of the metric entirely.
        existing = {
            "dateonly": {"posted_at": "2026-07-15",
                         "first_seen_at": "2026-07-15T18:00:00Z"},
            "midnight": {"posted_at": "2026-07-15T00:00:00Z",
                         "first_seen_at": "2026-07-15T18:00:00Z"},
            "exact": {"posted_at": "2026-07-15T10:00:00Z",
                      "first_seen_at": "2026-07-15T11:00:00Z"},
        }
        out = _detection_latency(existing, now=self.NOW)
        assert out["sample_size"] == 1
        assert out["median_minutes"] == 60.0

    def test_reports_p95_alongside_median(self):
        existing = {
            str(i): {"posted_at": "2026-07-15T10:00:00Z",
                     "first_seen_at": f"2026-07-15T10:{i:02d}:00Z"}
            for i in range(1, 21)
        }
        out = _detection_latency(existing, now=self.NOW)
        assert out["sample_size"] == 20
        assert out["p95_minutes"] >= out["median_minutes"]


class TestStickySeasons:
    """A season already on record wins over re-inference (see _keep_matching)."""

    CFG = {"cycles": ["Summer 2027", "Fall 2026"], "regions": ["US"],
           "role_scope": "tech", "infer_undated": True, "infer_max_age_days": 45}

    def _results(self, posted_days_ago):
        from datetime import UTC, datetime, timedelta

        from intern_engine.models import Job
        posted = (datetime.now(UTC) - timedelta(days=posted_days_ago)).strftime("%Y-%m-%d")
        job = Job(id="greenhouse:acme:1", source="greenhouse", company="Acme",
                  company_slug="acme", title="Network Engineer Intern",
                  location="New York, NY", url="https://x",
                  posted_at=f"{posted}T00:00:00Z")
        return [({"ats": "greenhouse", "slug": "acme", "name": "Acme"},
                 Fetch([job]), None)]

    def _keep(self, results, existing):
        from intern_engine.pipeline import _keep_matching
        kept, *_ = _keep_matching(results, self.CFG, {}, existing)
        return kept

    def test_fresh_undated_role_is_kept_without_inventing_a_cycle(self):
        # Used to become ("Summer 2027", True) from the posting month alone.
        # That guess was measured wrong, so the role is kept under a label
        # that asserts nothing.
        kept = self._keep(self._results(3), {})
        assert [(j.season, j.season_inferred) for j in kept] == [
            (filters.NOT_STATED, True)]

    def test_text_verified_season_on_record_beats_reinference(self):
        existing = {"greenhouse:acme:1": {"season": "Fall 2026", "season_inferred": False}}
        kept = self._keep(self._results(3), existing)
        assert [(j.season, j.season_inferred) for j in kept] == [("Fall 2026", False)]

    def test_sticky_season_outlives_inference_recency_window(self):
        # 60 days old: a fresh inference would refuse, but the role is already
        # on record — it must stay open instead of flipping closed.
        existing = {"greenhouse:acme:1": {"season": "Summer 2027", "season_inferred": True}}
        kept = self._keep(self._results(60), existing)
        assert [(j.season, j.season_inferred) for j in kept] == [("Summer 2027", True)]

    def test_stale_undated_role_without_record_still_dropped(self):
        assert self._keep(self._results(60), {}) == []

    def test_verified_offcycle_season_is_sticky_dropped(self):
        # Text verification recorded "Summer 2026": the role must stay off the
        # list — never re-inferred back in from its posting date.
        existing = {"greenhouse:acme:1": {"season": "Summer 2026", "season_inferred": False}}
        assert self._keep(self._results(3), existing) == []

    def test_explicit_offcycle_title_beats_stale_sticky_season(self):
        # A store written by older code may hold "Summer 2027" for a title
        # that literally says "Summer 2026" — the title's own year wins.
        from datetime import UTC, datetime, timedelta

        from intern_engine.models import Job
        posted = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
        job = Job(id="workday:stevens:1", source="workday", company="Stevens",
                  company_slug="stevens", title="Summer 2026 Intern: Cyber Security",
                  location="Hoboken, NJ", url="https://x",
                  posted_at=f"{posted}T00:00:00Z")
        results = [({"ats": "workday", "slug": "stevens", "name": "Stevens"},
                    Fetch([job]), None)]
        existing = {"workday:stevens:1": {"season": "Summer 2027", "season_inferred": True}}
        assert self._keep(results, existing) == []

    def test_legacy_unspecified_season_falls_through_to_unstated(self):
        # Only a real "<Term> <Year>" label is a verdict; junk labels fall
        # through as before — now landing in the unstated lane.
        existing = {"greenhouse:acme:1": {"season": "Unspecified"}}
        kept = self._keep(self._results(3), existing)
        assert [(j.season, j.season_inferred) for j in kept] == [
            (filters.NOT_STATED, True)]


class TestAgeCutoff:
    """The age filter backs an INFERENCE; it can't overrule a stated cycle."""

    CFG = {"cycles": ["Summer 2027", "Fall 2026"], "regions": ["US"],
           "role_scope": "tech", "infer_undated": True, "infer_max_age_days": 45,
           "max_age_days": 270}

    def _keep(self, title, days_ago):
        from datetime import datetime, timedelta

        from intern_engine.models import Job
        from intern_engine.pipeline import _keep_matching
        posted = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        job = Job(id="amazon:amazon:1", source="amazon", company="Amazon",
                  company_slug="amazon", title=title, location="Seattle, WA",
                  url="https://x", posted_at=f"{posted}T00:00:00Z")
        results = [({"ats": "amazon", "slug": "amazon", "name": "Amazon"},
                    Fetch([job]), None)]
        kept, *_ = _keep_matching(results, self.CFG, {}, {})
        return kept

    def test_old_but_employer_stated_cycle_is_kept(self):
        # Amazon's "Fall 2026 (US)" internship was posted early and is still
        # open and still for Fall 2026. Closing it for age was us overriding
        # what the company wrote.
        kept = self._keep("Network Engineer Internship - Fall 2026 (US)", 300)
        assert [j.season for j in kept] == ["Fall 2026"]

    def test_old_and_only_inferred_is_dropped(self):
        # No stated cycle: recency was the ONLY evidence, and it's gone.
        assert self._keep("Network Engineer Intern", 300) == []

    def test_recent_unstated_is_kept_without_a_cycle(self):
        kept = self._keep("Network Engineer Intern", 3)
        assert [(j.season, j.season_inferred) for j in kept] == [
            (filters.NOT_STATED, True)]


class TestRegionConfig:
    """regions config must be honored end-to-end (Canada was silently dropped)."""

    def _results(self, location):
        from datetime import UTC, datetime, timedelta

        from intern_engine.models import Job
        posted = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d")
        job = Job(id="greenhouse:acme:1", source="greenhouse", company="Acme",
                  company_slug="acme", title="Network Engineer Intern, Summer 2027",
                  location=location, url="https://x",
                  posted_at=f"{posted}T00:00:00Z")
        return [({"ats": "greenhouse", "slug": "acme", "name": "Acme"},
                 Fetch([job]), None)]

    def _keep(self, results, regions):
        from intern_engine.pipeline import _keep_matching
        cfg = {"cycles": ["Summer 2027", "Fall 2026"], "regions": regions,
               "role_scope": "tech"}
        kept, *_ = _keep_matching(results, cfg, {}, {})
        return kept

    def test_us_only_drops_canada(self):
        assert self._keep(self._results("Toronto, Ontario, Canada"), ["US"]) == []

    def test_us_and_canada_keeps_canada(self):
        kept = self._keep(self._results("Toronto, Ontario, Canada"), ["US", "Canada"])
        assert len(kept) == 1

    def test_us_and_canada_still_keeps_us(self):
        kept = self._keep(self._results("New York, NY"), ["US", "Canada"])
        assert len(kept) == 1


class TestDateSourceMigration:
    def test_legacy_records_get_a_shape_derived_label(self):
        from intern_engine.pipeline import _migrate_date_sources
        existing = {
            "a": {"posted_at": "2026-06-01T08:00:00-04:00"},          # real time
            "b": {"posted_at": "2026-06-01T00:00:00Z"},               # a day
            "c": {"posted_at": None},
            "d": {"posted_at": "2026-06-02T10:00:00Z",
                  "posted_at_source": "relative_derived"},            # labeled: kept
        }
        _migrate_date_sources(existing)
        assert existing["a"]["posted_at_source"] == "exact"
        assert existing["b"]["posted_at_source"] == "date_only"
        assert "posted_at_source" not in existing["c"]
        assert existing["d"]["posted_at_source"] == "relative_derived"


class TestOutOfScopeSweep:
    def test_foreign_open_record_is_closed_from_the_store_alone(self):
        # The Lausanne case: admitted under an older filter, then unreachable
        # by fetch-based closure because Medtronic's board is capped. Scope is
        # our own verdict — no fetch evidence required.
        from intern_engine.pipeline import _close_out_of_scope
        cfg = {"regions": ["US"]}
        existing = {
            "a": {"is_open": True, "location": "Lausanne, Vaud, Switzerland"},
            "b": {"is_open": True, "location": "Austin, TX"},
            "c": {"is_open": False, "location": "Paris, France"},   # already closed
            "d": {"is_open": True, "location": "—"},                # no location: leave
        }
        assert _close_out_of_scope(existing, cfg) == 1
        assert existing["a"]["is_open"] is False
        assert existing["a"]["closed_reason"] == "out-of-scope"
        assert existing["b"]["is_open"] is True
        assert existing["d"]["is_open"] is True

    def test_sweep_is_off_when_international_is_included(self):
        from intern_engine.pipeline import _close_out_of_scope
        cfg = {"regions": ["US"], "include_international": True}
        existing = {"a": {"is_open": True, "location": "Paris, France"}}
        assert _close_out_of_scope(existing, cfg) == 0
        assert existing["a"]["is_open"] is True


class TestRetireGuessedCycles:
    """Legacy records must lose cycle labels that came from the old guess."""

    CFG = {"cycles": ["Summer 2027", "Fall 2026"]}

    def _run(self, existing):
        from intern_engine.pipeline import _retire_guessed_cycles
        return _retire_guessed_cycles(existing, self.CFG), existing

    def test_guessed_cycle_becomes_not_stated(self):
        # The sticky-season path trusts any stored label that's in `cycles`,
        # so without this the fabricated "Summer 2027" would live forever.
        n, out = self._run({"a": {"season": "Summer 2027", "season_inferred": True,
                                  "enriched_at": "2026-07-01T00:00:00Z",
                                  "classifier_v": 2}})
        assert n == 1
        assert out["a"]["season"] == filters.NOT_STATED
        # and it must be re-read, since the old text reader was tag-blind
        assert "enriched_at" not in out["a"]
        assert "classifier_v" not in out["a"]

    def test_employer_stated_cycles_are_untouched(self):
        existing = {"a": {"season": "Fall 2026", "season_inferred": False,
                          "enriched_at": "2026-07-01T00:00:00Z"}}
        n, out = self._run(existing)
        assert n == 0
        assert out["a"]["season"] == "Fall 2026"
        assert out["a"]["enriched_at"] == "2026-07-01T00:00:00Z"

    def test_offcycle_tombstones_are_untouched(self):
        # "Summer 2026" isn't in cycles: it's a settled off-cycle verdict.
        existing = {"a": {"season": "Summer 2026", "season_inferred": True}}
        n, out = self._run(existing)
        assert n == 0
        assert out["a"]["season"] == "Summer 2026"

    def test_multi_cycle_list_is_dropped_with_the_guess(self):
        n, out = self._run({"a": {"season": "Summer 2027", "season_inferred": True,
                                  "seasons": ["Summer 2027", "Fall 2026"]}})
        assert n == 1
        assert "seasons" not in out["a"]


class TestOffCycleAgreement:
    """Intake and the store sweep must answer "is this off-cycle?" identically.

    They didn't: _keep_matching resolves a title with detect_season(), which
    maps a bare "2027" to Summer 2027, while _close_out_of_scope tested
    detect_seasons(), which reports term+year evidence only and returns [] for
    the same title. Every bare-year role was therefore accepted on fetch and
    closed as off-cycle on the very next run.
    """

    CYCLES = ("Summer 2027", "Fall 2026")

    # The three roles the live store carried when this was found, plus the
    # genuinely off-cycle title that must still close.
    IN_CYCLE = (
        "2027 IT Intern",                                # Solar Turbines
        "2027 IT Audit Intern",                          # Fifth Third Bank
        "R&D Intern - Wireless Systems Engineer - 2026",  # Motorola
    )
    OFF_CYCLE = (
        "Summer 2026 Network Intern",
        "Fall 2027 IT Intern",
        "2025 Network Technician Intern",
    )

    def test_a_bare_year_is_not_off_cycle(self):
        for title in self.IN_CYCLE:
            assert filters.detect_season(title, self.CYCLES) is not None, title
            assert not filters.states_offcycle_year(title, self.CYCLES), title

    def test_a_genuinely_untracked_year_still_is(self):
        for title in self.OFF_CYCLE:
            assert filters.detect_season(title, self.CYCLES) is None, title
            assert filters.states_offcycle_year(title, self.CYCLES), title

    def test_a_yearless_title_is_not_off_cycle(self):
        # No year stated at all is "unknown", not "wrong year" — those roles
        # go to the cycle-not-stated lane instead of being closed.
        for title in ("Network Engineer Intern", "SOC Analyst Co-op", ""):
            assert not filters.states_offcycle_year(title, self.CYCLES), title

    def test_the_verdict_matches_what_intake_would_decide(self):
        # The anti-drift guard: intake's rule is "detect_season found nothing
        # AND the title names a year". Any future edit to either side that
        # breaks this equality reopens the bug.
        for title in self.IN_CYCLE + self.OFF_CYCLE + ("Network Intern",):
            intake = (filters.detect_season(title, self.CYCLES) is None
                      and filters.states_explicit_year(title))
            assert filters.states_offcycle_year(title, self.CYCLES) is intake, title


class TestBareYearRolesSurviveTheStoreSweep:
    """End-to-end: the sweep must not close roles intake just accepted."""

    CFG = {"cycles": ["Summer 2027", "Fall 2026"], "regions": ["US"],
           "role_scope": "tech", "infer_undated": True, "infer_max_age_days": 120}

    def _record(self, title, season, **extra):
        rec = {
            "id": f"greenhouse:acme:{abs(hash(title)) % 9999}",
            "company": "Acme", "title": title, "season": season,
            "season_inferred": False, "location": "Orlando, FL",
            "url": "https://x/1", "is_open": True,
            "posted_at": "2026-08-01T00:00:00Z",
            "first_seen_at": "2026-08-01T00:00:00Z",
        }
        rec.update(extra)
        return rec

    def _sweep(self, records):
        existing = {r["id"]: r for r in records}
        pipeline._close_out_of_scope(existing, self.CFG, {})
        return {r["title"] for r in existing.values() if r.get("is_open")}

    def test_the_three_live_roles_stay_open(self):
        open_titles = self._sweep([
            self._record("2027 IT Intern", "Summer 2027"),
            self._record("2027 IT Audit Intern", "Summer 2027"),
            self._record("R&D Intern - Wireless Systems Engineer - 2026", "Fall 2026"),
        ])
        assert len(open_titles) == 3

    def test_a_real_off_cycle_role_is_still_closed(self):
        open_titles = self._sweep([
            self._record("2027 IT Intern", "Summer 2027"),
            self._record("Summer 2026 Network Intern", "Summer 2027"),
        ])
        assert open_titles == {"2027 IT Intern"}


class TestDroppedNoYearReasons:
    """The discarded pool, sized by sub-reason.

    93 in-scope roles were dropped against 33 published, behind a single
    counter — so there was no way to tell the recoverable half ("posted four
    months ago") from the unrecoverable one ("the ATS published no date").
    """

    CFG = {"cycles": ["Summer 2027", "Fall 2026"], "regions": ["US"],
           "role_scope": "tech", "infer_undated": True, "infer_max_age_days": 45}

    def _results(self, *jobs):
        from intern_engine.models import Fetch as _Fetch
        return [({"ats": "greenhouse", "slug": "acme", "name": "Acme"},
                 _Fetch(list(jobs)), None)]

    def _job(self, title, posted_at, jid="1"):
        from intern_engine.models import Job
        return Job(id=f"greenhouse:acme:{jid}", source="greenhouse", company="Acme",
                   company_slug="acme", title=title, location="Orlando, FL",
                   url=f"https://x/{jid}", posted_at=posted_at)

    def _run(self, *jobs, cfg=None):
        kept, _s, _c, _seen, _e, _ea, no_year, _off, rejected = \
            pipeline._keep_matching(self._results(*jobs), cfg or self.CFG, {}, {})
        return kept, no_year, rejected

    def _ago(self, days):
        from datetime import UTC, datetime, timedelta
        return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

    def test_an_undated_role_is_counted_as_undated(self):
        _kept, counts, _r = self._run(self._job("Network Engineer Intern", None))
        assert counts["undated"] == 1

    def test_an_old_dated_role_is_counted_separately(self):
        _kept, counts, _r = self._run(
            self._job("Network Engineer Intern", self._ago(200)))
        assert counts["dated-too-old"] == 1
        assert counts["undated"] == 0

    def test_a_future_dated_role_has_its_own_reason(self):
        _kept, counts, _r = self._run(
            self._job("Network Engineer Intern", self._ago(-30)))
        assert counts["dated-in-future"] == 1

    def test_an_unparsable_date_has_its_own_reason(self):
        _kept, counts, _r = self._run(
            self._job("Network Engineer Intern", "not-a-date"))
        assert counts["unparsable-date"] == 1

    def test_disabled_inference_is_distinguishable_from_a_bad_date(self):
        cfg = {**self.CFG, "infer_undated": False}
        _kept, counts, _r = self._run(
            self._job("Network Engineer Intern", self._ago(1)), cfg=cfg)
        assert counts["inference-disabled"] == 1

    def test_a_recent_dated_role_is_kept_and_counted_nowhere(self):
        kept, counts, rejected = self._run(
            self._job("Network Engineer Intern", self._ago(3)))
        assert len(kept) == 1
        assert sum(counts.values()) == 0
        assert rejected == []

    def test_every_drop_is_logged_with_its_reason_and_identity(self):
        _kept, _counts, rejected = self._run(
            self._job("Network Engineer Intern", None, jid="1"),
            self._job("NOC Technician Intern", self._ago(300), jid="2"),
        )
        by_title = {r["title"]: r for r in rejected}
        assert by_title["Network Engineer Intern"]["reason"] == "undated"
        assert by_title["NOC Technician Intern"]["reason"] == "dated-too-old"
        for row in rejected:
            assert row["company"] == "Acme"
            assert row["source"] == "greenhouse"
            assert row["url"].startswith("https://")

    def test_the_reason_never_disagrees_with_the_keep_decision(self):
        # cycle_unstated_ok is defined in terms of cycle_unstated_reason, so a
        # role is kept if and only if there is no reason to drop it.
        for posted in (None, "not-a-date", self._ago(3), self._ago(300)):
            ok = filters.cycle_unstated_ok("Network Engineer Intern", posted, 45)
            reason = filters.cycle_unstated_reason("Network Engineer Intern", posted, 45)
            assert ok is (reason is None), posted


class TestRejectedTitlesLog:
    def test_it_appends_and_keeps_only_the_newest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "REJECTED_TITLES_PATH",
                            str(tmp_path / "rejected_titles.jsonl"))
        monkeypatch.setattr(pipeline, "_REJECTED_KEEP", 5)
        for batch in range(4):
            pipeline._append_rejected_titles([
                {"reason": "undated", "title": f"Role {batch}-{i}",
                 "company": "Acme", "source": "greenhouse",
                 "posted_at": None, "url": "https://x/1"}
                for i in range(2)
            ])
        lines = (tmp_path / "rejected_titles.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert len(lines) == 5                       # capped
        assert json.loads(lines[-1])["title"] == "Role 3-1"   # newest survives
        assert json.loads(lines[0])["title"] == "Role 1-1"    # oldest evicted

    def test_it_writes_nothing_when_nothing_was_dropped(self, tmp_path, monkeypatch):
        target = tmp_path / "rejected_titles.jsonl"
        monkeypatch.setattr(paths, "REJECTED_TITLES_PATH", str(target))
        pipeline._append_rejected_titles([])
        assert not target.exists()

    def test_each_line_carries_a_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "REJECTED_TITLES_PATH",
                            str(tmp_path / "rejected_titles.jsonl"))
        pipeline._append_rejected_titles([
            {"reason": "undated", "title": "Network Intern", "company": "Acme",
             "source": "greenhouse", "posted_at": None, "url": "https://x/1"},
        ])
        row = json.loads((tmp_path / "rejected_titles.jsonl").read_text(
            encoding="utf-8").strip())
        assert row["ts"].endswith("Z")
        assert row["reason"] == "undated"
