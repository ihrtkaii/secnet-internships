"""Static dashboard contracts for freshness and saved roles."""

from intern_engine import dashboard, paths, radar


def _radar_row(status, **extra):
    row = {
        "company": "Acme", "last_cycle_posted": "", "posted_on": "",
        "precision": "day", "rolling": False, "confidence": "verified",
        "source": "engine", "expected": "", "days_until": None,
        "status": status, "url": "https://x/1", "note": "", "provenance": {},
    }
    row.update(extra)
    return row


def _store():
    return {
        "a": {
            "id": "a", "company": "Acme", "title": "SOC Analyst Intern",
            "season": "Summer 2027", "seasons": ["Summer 2027"],
            "season_inferred": False, "category": "SOC / Detection",
            "location": "Austin, TX", "url": "https://x/1", "is_open": True,
            "posted_at": "2026-08-05T00:00:00Z",
            "first_seen_at": "2026-08-05T01:00:00Z",
            "sponsorship": "unknown", "skills": [], "source": "greenhouse",
        }
    }


def _stats():
    return {
        "generated_at": "2026-08-06T14:08:27Z", "companies_total": 1,
        "companies_by_source": {"greenhouse": 1}, "open_total": 1,
    }


def test_dashboard_uses_fetch_time_and_prunes_ghost_saves():
    dashboard.generate(_store(), _stats())
    html = open(paths.DASHBOARD_PATH, encoding="utf-8").read()
    assert "Data as of Aug 06, 2026 at 14:08 UTC" in html
    assert "if (!currentIds[id]) delete saved[id]" in html


def test_no_signup_form_when_no_mailer_is_configured():
    """This fork runs no mailer.

    data/config.json leaves supabase_url/supabase_publishable_key empty, so
    config.signup_endpoint() returns None and _signup_section() renders
    nothing. Asserting the absence is the real contract: a subscribe box that
    POSTs into an unconfigured endpoint would collect addresses and silently
    drop them.
    """
    dashboard.generate(_store(), _stats())
    html = open(paths.DASHBOARD_PATH, encoding="utf-8").read()
    assert "/rest/v1/rpc/request_email_subscription" not in html
    assert 'id="subscribe"' not in html


class TestRadarGuard:
    """The radar is a forecast. With nothing waiting and nothing dropped it is
    only repeating the table above it, so the section is dropped entirely."""

    def _html(self, monkeypatch, rows):
        monkeypatch.setattr(radar, "rows", lambda *a, **k: rows)
        dashboard.generate(_store(), _stats())
        return open(paths.DASHBOARD_PATH, encoding="utf-8").read()

    def test_absent_when_every_row_is_an_open_now_echo(self, monkeypatch):
        html = self._html(monkeypatch, [_radar_row("open"), _radar_row("open")])
        assert 'id="radar"' not in html
        assert "Drop Radar" not in html

    def test_present_when_a_company_is_still_waiting(self, monkeypatch):
        html = self._html(monkeypatch, [
            _radar_row("open"),
            _radar_row("waiting", company="Beta", expected="2026-09-01", days_until=16),
        ])
        assert 'id="radar"' in html

    def test_present_when_a_company_already_dropped(self, monkeypatch):
        html = self._html(monkeypatch, [
            _radar_row("dropped", posted_on="2026-07-13", expected="2026-07-13"),
        ])
        assert 'id="radar"' in html

    def test_an_elapsed_window_does_not_count_as_a_forecast(self, monkeypatch):
        # A "waiting" row whose window is long past is not actionable, so it
        # cannot hold the section open on its own.
        html = self._html(monkeypatch, [
            _radar_row("open"),
            _radar_row("waiting", company="Beta", expected="2026-01-01",
                       days_until=-200),
        ])
        assert 'id="radar"' not in html


def _opening(jid, **extra):
    rec = {
        "id": jid, "company": "Copart", "title": "Software Engineering Intern",
        "season": "Summer 2027", "seasons": ["Summer 2027"],
        "season_inferred": False, "category": "Software",
        "location": "Dallas, TX - Headquarters", "url": f"https://x/{jid}",
        "is_open": True, "posted_at": "2026-08-05T00:00:00Z",
        "first_seen_at": "2026-08-05T01:00:00Z",
        "sponsorship": "unknown", "skills": [], "source": "workday",
    }
    rec.update(extra)
    return rec


def _render(store):
    dashboard.generate(store, {
        "generated_at": "2026-08-07T21:35:57Z", "companies_total": 1,
        "companies_by_source": {"workday": 1}, "open_total": len(store),
    })
    return open(paths.DASHBOARD_PATH, encoding="utf-8").read()


def test_identical_requisitions_render_as_one_row():
    html = _render({str(i): _opening(str(i)) for i in range(8)})
    assert html.count("<tr data-id=") == 1
    assert "8 openings" in html


def test_a_grouped_row_still_links_every_requisition():
    html = _render({str(i): _opening(str(i)) for i in range(3)})
    for jid in ("0", "1", "2"):
        assert f"https://x/{jid}" in html


def test_a_grouped_row_carries_its_opening_count_for_the_filter_maths():
    # The visible count is a count of JOBS, so the filter script sums this
    # attribute rather than counting rows.
    html = _render({str(i): _opening(str(i)) for i in range(3)})
    assert 'data-openings="3"' in html
    assert "parseInt(tr.dataset.openings || '1', 10)" in html


def test_distinct_roles_are_not_folded_together():
    html = _render({
        "a": _opening("a"),
        "b": _opening("b", title="Database Engineering Intern"),
    })
    assert html.count("<tr data-id=") == 2
    assert "openings<" not in html


def test_a_star_on_an_absorbed_requisition_moves_to_the_surviving_row():
    # Folding three rows into one must not delete a reader's saved role. The
    # pruning step ("remove ids no longer in the artifact") would have done
    # exactly that to any sibling id, silently.
    html = _render({str(i): _opening(str(i)) for i in range(3)})
    assert 'data-ids="0|1|2"' in html
    assert "if (saved[ids[i]]) { saved[tr.dataset.id] = saved[ids[i]]; break; }" in html
    # And every one of those ids still counts as present.
    assert "ids.forEach(function (id) { currentIds[id] = true; });" in html
