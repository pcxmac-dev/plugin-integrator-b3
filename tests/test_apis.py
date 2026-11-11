"""What the plugin attaches to (plugin API 2.2): B3's COTAHIST files as B3's pages describe them, the downloads it says
it makes being the ones its source really asks for, what its client learned said without a request, and
(LIMINAL_VENDOR_PAGES=1 only) every page it names still answering. Nothing else is sent; no ledger or key is touched."""
from __future__ import annotations

import gc
import os
import re
from datetime import date

import pytest

from liminal_libs.config import load_settings
from liminal_libs.sources.apis import dead_links, problems, urls

import plugin_integrator_b3 as b3
from plugin_integrator_b3 import BASE_URL
from plugin_integrator_b3.apis import COTAHIST, NAME
from plugin_integrator_b3.facts import NOTES

from .conftest import FakeResponse
from .test_b3 import DAY, _files, _source, cotahist, record

TEMPLATES = ((re.compile(r"^COTAHIST_D\d{8}\.ZIP$"), "GET /COTAHIST_D{DDMMYYYY}.ZIP"),
             (re.compile(r"^COTAHIST_A\d{4}\.ZIP$"), "GET /COTAHIST_A{YYYY}.ZIP"),
             (re.compile(r"^COTAHIST_M\d{6}\.ZIP$"), "GET /COTAHIST_M{MMYYYY}.ZIP"))


@pytest.fixture(autouse=True)
def fresh_notes():
    NOTES.clear()
    yield
    NOTES.clear()


def _call(file: str) -> str:
    """The card's call a downloaded file is (its template), or the file itself when none matches."""
    return next((call for pattern, call in TEMPLATES if pattern.match(file)), file)


def _year_and_day(tmp_path):
    """A source whose year 2026 is a yearly file to its session of 2026-10-29, then the daily file of 2026-10-30."""
    y2026 = cotahist([record("PETR4", day="20260102", c=3900, avg=3890), record("PETR4", day="20261029", c=5200, avg=5190)],
                     "20261029", "COTAHIST_A2026.TXT")
    return _source(FakeResponse(200, y2026), FakeResponse(200, cotahist(DAY), {"Last-Modified": "Fri, 30 Oct 2026 23:55:00 GMT"}),
                   folder=tmp_path / "sources" / "b3", years=1)


def test_it_declares_b3s_files_soundly_with_every_part_and_what_it_does_with_each():
    assert b3.PLUGIN.apis == (COTAHIST,) and problems(b3.PLUGIN.apis) == [] and COTAHIST.name == NAME
    assert COTAHIST.kind == "feed" and COTAHIST.base == BASE_URL  # the card's address is the client's
    assert COTAHIST.docs.endswith("/historical-quote-data/") and COTAHIST.link("spec").endswith("SeriesHistoricas_Layout.pdf")
    assert len(COTAHIST.areas) == 10 and [a.name for a in COTAHIST.covered] == [
        "Yearly files", "Daily files", "Header and trailer records", "Cash market (TPMERC 010)",
        "Code tables (CODBDI, ESPECI, INDOPC)"]
    assert {c.area for c in COTAHIST.calls} <= {a.name for a in COTAHIST.areas}
    assert not any(c.writes or c.costs for c in COTAHIST.calls) and "CD-ROM" in COTAHIST.costs
    assert "coleta automatizada" in COTAHIST.limits and "personal use" in COTAHIST.limits
    assert COTAHIST.verified == "2026-11-11" and all(u.startswith("https://") for u in urls(b3.PLUGIN.apis))


def test_the_downloads_it_says_it_makes_are_the_ones_its_source_asks_for(tmp_path):
    src, session = _year_and_day(tmp_path)
    bars = next(src.bar_pages("PETR4.SA", date(2026, 1, 1), date(2026, 10, 30), "day"))
    assert [b["c"] for b in bars] == [39.0, 52.0, 52.71]
    assert _files(session) == ["COTAHIST_A2026.ZIP", "COTAHIST_D30102026.ZIP"]
    assert {_call(f) for f in _files(session)} == {c.name for c in COTAHIST.used}
    assert [c.name for c in COTAHIST.calls if not c.used] == ["GET /COTAHIST_M{MMYYYY}.ZIP"]


def test_api_facts_say_what_its_client_learned_and_ask_nothing(tmp_path):
    assert b3.PLUGIN.api_facts() == {}  # nothing built here yet: nothing known, nothing asked
    built = b3.PLUGIN.build(load_settings(env={}, env_file=None), 5)  # its build notes its client (no request)
    assert b3.PLUGIN.api_facts()[NAME]["Requests"] == "0 so far (of 5 allowed)" and built.client.requests_made == 0
    del built
    gc.collect()
    assert b3.PLUGIN.api_facts()[NAME]["Run began"].endswith("(ended)")
    src, _ = _year_and_day(tmp_path)  # a run's client as B3 answers it (fakes, no ledger)
    NOTES.note(NAME, src.client)
    next(src.bar_pages("PETR4.SA", date(2026, 1, 1), date(2026, 10, 30), "day"))
    said = b3.PLUGIN.api_facts()[NAME]
    assert said["Requests"] == "2 so far" and said["Last file changed"] == "Fri, 30 Oct 2026 23:55:00 GMT"
    assert said["Last daily file asked"] == "COTAHIST_D30102026.ZIP: 6 listings kept"
    assert said["Last yearly file downloaded"].startswith("COTAHIST_A2026.ZIP: ") and said["Last yearly file downloaded"].endswith(
        "to 2026-10-29")
    del src
    gc.collect()
    assert b3.PLUGIN.api_facts()[NAME]["Requests"] == "2"


@pytest.mark.skipif(os.environ.get("LIMINAL_VENDOR_PAGES") != "1", reason="asks B3's pages: set LIMINAL_VENDOR_PAGES=1")
def test_every_page_it_names_answers():
    assert dead_links(b3.PLUGIN.apis) == []
