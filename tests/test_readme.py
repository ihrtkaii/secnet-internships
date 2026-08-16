"""README/CSV generation: honest counts and a CSV that can't execute."""

import csv

import pytest

from intern_engine import paths, radar, readme


def _rec(jid, **extra):
    rec = {
        "id": jid, "company": "Acme", "title": "Network Engineer Intern",
        "season": "Summer 2027", "season_inferred": False,
        "category": "Network / Telecom",
        "location": "Austin, TX", "url": f"https://x/{jid}", "is_open": True,
        "posted_at": "2026-07-01T00:00:00Z", "first_seen_at": "2026-07-01T00:00:00Z",
        "sponsorship": "unknown", "skills": [],
    }
    rec.update(extra)
    return rec


SECURITY = "## 🔐 Security"
NETWORK = "## 🌐 Network & Infrastructure"


def _track(text: str, heading: str) -> str:
    """The body of one top-level track section.

    Cycle headings are `###` nested inside a `##` track heading, so a bare
    `"## Summer 2027" in text` check passes on the `###` heading by pure
    substring luck and proves nothing about where the section actually sits.
    Slicing the track out first makes the nesting the thing under test.
    """
    assert heading in text, f"no {heading!r} section in:\n{text}"
    body = text.split(heading, 1)[1]
    rest = [i for i in (body.find("\n## "),) if i != -1]
    return body[: rest[0]] if rest else body


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "README_PATH", str(tmp_path / "README.md"))
    monkeypatch.setattr(paths, "CSV_PATH", str(tmp_path / "internships.csv"))
    return tmp_path


class TestEvidenceSplit:
    """A cycle heading is a claim; only employer-stated roles may sit under it."""

    def test_inferred_roles_get_their_own_section(self, outputs):
        store = {
            "a": _rec("a"),
            "b": _rec("b", season_inferred=True, title="Systems Administrator Intern"),
        }
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        network = _track(text, NETWORK)
        assert "### Summer 2027  (1 employer-stated)" in network
        assert "### Recently posted — cycle not stated  (1 roles)" in network
        # No guessed cycle anywhere: the lane states the absence, not a value.
        assert "~Summer 2027" not in text
        assert "Likely cycle" not in text

    def test_hero_reports_the_split(self, outputs):
        store = {
            "a": _rec("a"),
            "b": _rec("b", season_inferred=True),
            "c": _rec("c", season_inferred=True),
        }
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "1 have a cycle the employer stated" in text
        assert "2 are recent postings whose cycle isn't stated" in text

    def test_no_rolling_section_when_everything_is_stated(self, outputs):
        readme.generate({"a": _rec("a")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        # The heading, not the phrase — the legend up top names the lane in
        # prose whether or not any role is in it.
        assert "### Recently posted" not in text

    def test_role_rows_render_skill_tags(self, outputs):
        readme.generate({"a": _rec("a", skills=["Python", "React", "SQL"])})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "| Company | Role | Category | Location | Skills | Posted | Apply |" in text
        assert "| Python, SQL, React |" in text

    def test_role_rows_label_missing_skills(self, outputs):
        readme.generate({"a": _rec("a", skills=[])})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "| No skills listed |" in text


class TestTrackSplit:
    """Two readers, two tables. The cycle split lives inside the track split."""

    def test_each_track_is_a_top_level_section_with_cycles_nested_under_it(
            self, outputs):
        store = {
            "a": _rec("a", title="SOC Analyst Intern"),
            "b": _rec("b", title="Network Engineer Intern"),
        }
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        # The track headings are top-level, and each one owns its own cycle
        # subsection — not one shared cycle heading with both roles under it.
        assert f"\n{SECURITY}\n" in text
        assert f"\n{NETWORK}\n" in text
        assert "\n## Summer 2027" not in text
        for track in (SECURITY, NETWORK):
            body = _track(text, track)
            assert "### Summer 2027  (1 employer-stated)" in body
        assert "SOC Analyst Intern" in _track(text, SECURITY)
        assert "SOC Analyst Intern" not in _track(text, NETWORK)

    def test_a_security_role_does_not_leak_into_the_network_table(self, outputs):
        readme.generate({"a": _rec("a", title="SOC Analyst Intern")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert NETWORK not in text

    def test_a_dual_track_role_is_rendered_in_both_tables(self, outputs):
        # "Cloud Security Intern" names both a security and a cloud term, so
        # it belongs to both readers — filing it under security alone hid it
        # from the networking table.
        readme.generate({"a": _rec("a", title="Cloud Security Intern")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "Cloud Security Intern" in _track(text, SECURITY)
        assert "Cloud Security Intern" in _track(text, NETWORK)

    def test_a_dual_track_role_is_still_counted_once(self, outputs):
        # Rendering it twice is a layout decision, not a claim that the
        # employer opened two jobs.
        out = readme.generate({"a": _rec("a", title="Cloud Security Intern")})
        assert out["open"] == 1

    def test_off_track_roles_get_the_third_section(self, outputs):
        readme.generate({"a": _rec("a", title="IT Operations Intern")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "## 🗂️ Other" in text
        assert SECURITY not in text
        assert NETWORK not in text


class TestMultiCycleRendering:
    def test_role_appears_under_every_cycle_it_states(self, outputs):
        store = {"a": _rec("a", title="Network Engineer Intern (Fall 2026/Summer 2027)",
                           season="Summer 2027",
                           seasons=["Summer 2027", "Fall 2026"])}
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        network = _track(text, NETWORK)
        assert "### Summer 2027  (1 employer-stated)" in network
        assert "### Fall 2026  (1 employer-stated)" in network

    def test_it_is_counted_once_not_twice(self, outputs):
        store = {"a": _rec("a", seasons=["Summer 2027", "Fall 2026"])}
        out = readme.generate(store)
        assert out["open"] == 1

    def test_the_cross_reference_names_only_the_other_cycle(self, outputs):
        store = {"a": _rec("a", seasons=["Summer 2027", "Fall 2026"])}
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        # Under Summer 2027 it should point at Fall 2026, and vice versa —
        # repeating the section's own cycle back at the reader is noise.
        assert "_(also open for Fall 2026)_" in text
        assert "_(also open for Summer 2027)_" in text
        assert "also open for Summer 2027, Fall 2026" not in text


class TestCsvCompleteness:
    def test_csv_holds_every_open_role_even_when_readme_caps(self, outputs):
        # The README caps rows per company for readability; the CSV is the
        # machine-readable export and must never silently drop roles.
        store = {str(i): _rec(str(i), title=f"SWE Intern {i}") for i in range(9)}
        readme.generate(store)
        rows = list(csv.DictReader((outputs / "internships.csv").open(encoding="utf-8")))
        assert len(rows) == 9

    def test_csv_carries_the_new_fields(self, outputs):
        store = {"a": _rec("a", title="Data Co-op", location="Remote - US")}
        readme.generate(store)
        row = next(csv.DictReader((outputs / "internships.csv").open(encoding="utf-8")))
        assert row["id"]
        assert row["program"] == "Co-op"
        assert row["remote"] == "yes"

    def test_closed_roles_are_excluded(self, outputs):
        store = {"a": _rec("a"), "b": _rec("b", is_open=False)}
        readme.generate(store)
        rows = list(csv.DictReader((outputs / "internships.csv").open(encoding="utf-8")))
        assert len(rows) == 1


class TestCsvInjection:
    """Job titles are third-party text and land in a file people open in Excel."""

    def test_formula_prefixes_are_neutralized(self):
        for raw in ("=HYPERLINK(\"http://evil\",\"click\")",
                    "+1+1", "-2+3", "@SUM(A1:A9)"):
            out = readme._csv_safe(raw)
            assert out.startswith("'"), raw
            assert out[1:] == raw  # the value itself is preserved, just inert

    def test_ordinary_values_are_untouched(self):
        for raw in ("Software Engineer Intern", "Stripe", "$45/hr",
                    "2026-06-01T00:00:00Z", "New York, NY", ""):
            assert readme._csv_safe(raw) == raw

    def test_non_strings_pass_through(self):
        assert readme._csv_safe(None) is None
        assert readme._csv_safe(42) == 42


class TestHeaderCounts:
    """The README and the API must not report different totals as the same thing."""

    CFG = {"cycles": ["Summer 2027"], "regions": ["US"]}

    def test_capped_listing_reports_both_numbers(self):
        lines = readme._header(self.CFG, total_open=107, companies=3900,
                               new_week=11, shown=104)
        line = next(x for x in lines if "open roles" in x)
        assert "107 open roles" in line
        assert "104 listed below" in line

    def test_no_parenthetical_when_nothing_was_cut(self):
        lines = readme._header(self.CFG, total_open=104, companies=3900,
                               new_week=11, shown=104)
        line = next(x for x in lines if "open roles" in x)
        assert "104 open roles" in line
        assert "listed below" not in line

    def test_zero_inferred_roles_still_reports_all_stated_and_fetch_time(self):
        lines = readme._header(
            self.CFG, total_open=4, companies=3900, new_week=1,
            stated=4, inferred=0, data_as_of="2026-08-06T14:08:27Z",
        )
        assert any("4 have a cycle the employer stated · 0 are recent" in x
                   for x in lines)
        assert any("data as of Aug 06, 2026 at 14:08 UTC" in x for x in lines)


class TestIdenticalOpenings:
    """One row per job, one line per requisition kept reachable.

    Copart really has eight live "Software Engineering Intern, Dallas"
    requisitions. Eight identical rows is what a reader complained about; zero
    of them disappearing is what the data promises.
    """

    def _store(self, n=3):
        return {str(i): _rec(str(i)) for i in range(n)}

    def test_the_table_shows_one_row(self, outputs):
        readme.generate(self._store())
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert text.count("| Acme | Network Engineer Intern") == 1

    def test_the_row_says_how_many_openings(self, outputs):
        readme.generate(self._store())
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "(3 openings)" in text

    def test_every_requisition_keeps_an_apply_link(self, outputs):
        readme.generate(self._store())
        text = (outputs / "README.md").read_text(encoding="utf-8")
        for jid in ("0", "1", "2"):
            assert f"https://x/{jid}" in text

    def test_the_headline_count_still_counts_openings(self, outputs):
        # "3 open roles" stays true — the grouping is a layout decision, not a
        # claim that two of the jobs stopped existing.
        readme.generate(self._store())
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "3 open roles" in text
        assert "(1 listed below)" not in text

    def test_the_csv_still_exports_every_requisition(self, outputs):
        # The machine-readable export is where all three ids must survive.
        readme.generate(self._store())
        with open(paths.CSV_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert sorted(r["id"] for r in rows) == ["0", "1", "2"]

    def test_a_different_location_is_not_folded_away(self, outputs):
        store = self._store(2)
        store["1"]["location"] = "Seattle, WA"
        readme.generate(store)
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert text.count("| Acme | Network Engineer Intern") == 2
        # No row claims a count (the legend explaining the marker is not a row).
        rows = [ln for ln in text.splitlines() if ln.startswith("| Acme |")]
        assert rows and not any("openings)" in ln for ln in rows)


class TestRadarGuard:
    """Same rule as the dashboard: no forecast, no section."""

    def _radar_row(self, status, **extra):
        row = {
            "company": "Acme", "last_cycle_posted": "", "posted_on": "",
            "precision": "day", "rolling": False, "confidence": "verified",
            "source": "engine", "expected": "", "days_until": None,
            "status": status, "url": "https://x/1", "note": "", "provenance": {},
        }
        row.update(extra)
        return row

    def test_absent_when_every_row_is_an_open_now_echo(self, outputs, monkeypatch):
        monkeypatch.setattr(radar, "rows",
                            lambda *a, **k: [self._radar_row("open")])
        readme.generate({"a": _rec("a")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "Drop Radar" not in text
        assert 'id="drop-radar"' not in text

    def test_present_when_a_company_is_still_waiting(self, outputs, monkeypatch):
        monkeypatch.setattr(radar, "rows", lambda *a, **k: [
            self._radar_row("open"),
            self._radar_row("waiting", company="Beta", expected="2026-09-01",
                            days_until=16),
        ])
        readme.generate({"a": _rec("a")})
        text = (outputs / "README.md").read_text(encoding="utf-8")
        assert "Drop Radar" in text
