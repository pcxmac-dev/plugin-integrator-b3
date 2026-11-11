"""The B3 plugin, run against the library installed beside it: its COTAHIST files read (daily and
yearly, built here as B3 writes them: 245-character records in a zip), the days they serve, the
listings and records, the refusals and the server's answers, and the app loading it from this
package with its own version and its facts."""
from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path

import pytest
import requests

from liminal_libs import composite, freshness
from liminal_libs.collector import history_floor
from liminal_libs.config import load_settings
from liminal_libs.errors import NotEntitled, RateLimited, SourceError
from liminal_libs.freshness import Delay
from liminal_libs.sources import feed_profiles, is_archive, markets, provider, rate_window, registry, supports
from liminal_libs.sources.archive import ET, ms
from liminal_libs.sources.http import PublicClient, SameHostSession
from liminal_libs.sources.registry import FeedPlugin
from liminal_libs.symbols import resolve

import plugin_integrator_b3
from plugin_integrator_b3 import BASE_URL, PER_DAY, PER_RUN, TIER, history_years
from plugin_integrator_b3.client import (B3Session, B3Source, YearIndex, b3_code, b3_session, first_day, listing_type,
                                      read_file, read_quote)

from .conftest import FakeResponse, FakeSession


def record(code, day="20261030", bdi="02", market="010", name="PETROBRAS", especi="PN      N2", o=5245, h=5366,
           l=5236, avg=5288, c=5271, n=77862, q=61743200, factor=1, isin="BRPETRACNPR6", kind="01"):  # noqa: E741
    """A COTAHIST record as B3 writes it (245 characters; the fields of PETR4 on 2026-10-30 by
    default): prices in hundredths of a real, quoted per `factor` shares."""
    s = [" "] * 245

    def put(a, b, text, number=False):  # 1-based positions, both included
        width = b - a + 1
        s[a - 1:b] = list(str(text).zfill(width) if number else str(text).ljust(width)[:width])

    put(1, 2, kind); put(3, 10, day); put(11, 12, bdi); put(13, 24, code); put(25, 27, market); put(28, 39, name)  # noqa: E702
    put(40, 49, especi); put(53, 56, "R$"); put(57, 69, o, True); put(70, 82, h, True); put(83, 95, l, True)  # noqa: E702
    put(96, 108, avg, True); put(109, 121, c, True); put(122, 134, c - 1, True); put(135, 147, c, True)  # noqa: E702
    put(148, 152, n, True); put(153, 170, q, True); put(171, 188, avg * q // factor, True); put(189, 201, 0, True)  # noqa: E702
    put(202, 202, "0"); put(203, 210, "99991231"); put(211, 217, factor, True); put(218, 230, 0, True)  # noqa: E702
    put(231, 242, isin); put(243, 245, "230")  # noqa: E702
    return "".join(s)


def cotahist(records, generated="20261030", member="COTAHIST_D30102026.TXT", trailer=True, count=None) -> bytes:
    """A COTAHIST zip: the header, the records, the trailer with their count, CRLF, latin-1."""
    lines = [f"00COTAHIST.{generated[:4]}BOVESPA {generated}".ljust(245), *records]
    if trailer:
        lines.append((f"99COTAHIST.{generated[:4]}BOVESPA {generated}"
                      + str(len(records) if count is None else count).zfill(11)).ljust(245))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, ("\r\n".join(lines) + "\r\n").encode("latin-1"))
    return buf.getvalue()


# the session of Friday 2026-10-30 (the real file's figures for the first three), with records of every kind
DAY = [
    record("PETR4"),
    record("VALE3", name="VALE", especi="ON      NM", o=5944, h=6298, l=5942, avg=6177, c=6202, n=61220, q=34856400,
           isin="BRVALEACNOR0"),
    record("HGLG11", bdi="12", name="FII HGLG PAX", especi="CI", o=14700, h=14950, l=14645, avg=14767, c=14950, n=12168,
           q=93780, isin="BRHGLGCTF004"),
    record("BOVA11", bdi="14", name="ISHARES BOVA", especi="CI  ATZ", o=16500, h=16600, l=16400, avg=16540, c=16540,
           n=20000, q=1000000, isin="BRBOVACTF003"),
    record("BRKM5", bdi="07", name="BRASKEM", especi="PNA     N1", o=1000, h=1050, l=990, avg=1020, c=1010, n=500,
           q=10000, isin="BRBRKMACNPA4"),  # the standard lot of a company in recovery
    record("IBOV11", name="IBOVESPA", especi="IBO/", o=16793800, h=16900000, l=16700000, avg=16793800, c=16800000, n=10,
           q=359061, factor=100, isin="BRIBOVINDM18"),  # quoted per 100
    record("AAPL34", bdi="34", name="APPLE", especi="DRN", o=9300, h=9400, l=9200, avg=9386, c=9386, n=100, q=1000,
           isin="BRAAPLBDR004"),  # a BDR: left
    record("PETR4F", market="020", n=100, q=120),  # the fractional market: left
    record("PETRK270", bdi="78", market="070", name="PETRA", o=100, h=120, l=90, avg=100, c=110, n=10, q=1000),  # an option
    record("PRNR1", bdi="10", name="PRINER", especi="DIR ORD NM", o=100, h=100, l=100, avg=100, c=100, n=1, q=100),  # rights
]
THURSDAY = [record("PETR4", day="20261029", c=5200, avg=5190), record("VALE3", day="20261029", c=6000, avg=5990),
            record("KLBN11", day="20261029", name="KLABIN S/A", especi="UNT     N2", c=2000, avg=1990, isin="BRKLBNCDAM18")]
FINADOS = date(2026, 11, 2)  # a Monday, B3 closed


def _source(*answers, folder=None, years=2, today=date(2026, 11, 3), sessions=None):
    session = FakeSession(list(answers))
    client = PublicClient("https://x.test/files", session=session, sleep=lambda s: None)
    return B3Source(client, folder=folder, years=years, today=today, is_session=sessions or (lambda d: None)), session


def _files(session):
    return [url.rsplit("/", 1)[1] for url, _, _ in session.calls]


def _days(bars):
    return [datetime.fromtimestamp(b["t"] / 1000, ET).date() for b in bars]


def test_a_daily_file_is_read_as_the_cash_markets_day_of_every_listing():
    quotes = {q.code: q for q in read_file(cotahist(DAY))}
    assert sorted(quotes) == ["BOVA11", "BRKM5", "HGLG11", "IBOV11", "PETR4", "VALE3"]  # no BDR, fraction, option, rights
    p = quotes["PETR4"]
    assert p.bar == {"t": ms(datetime(2026, 10, 30, tzinfo=ET)), "o": 52.45, "h": 53.66, "l": 52.36, "c": 52.71,
                     "v": 61743200.0, "vw": 52.88, "n": 77862}
    assert (p.day, p.name, p.especi.split(), p.isin, p.currency, p.bdi, p.factor) == (
        date(2026, 10, 30), "PETROBRAS PN N2", ["PN", "N2"], "BRPETRACNPR6", "BRL", "02", 1)
    assert quotes["HGLG11"].bar["c"] == 149.5 and quotes["HGLG11"].name == "FII HGLG PAX CI"
    assert (quotes["IBOV11"].bar["c"], quotes["IBOV11"].bar["vw"], quotes["IBOV11"].bar["v"]) == (1680.0, 1679.38, 359061.0)
    assert [listing_type(quotes[c]) for c in ("PETR4", "VALE3", "HGLG11", "BOVA11", "BRKM5", "IBOV11")] == [
        "CS", "CS", "FUND", "ETF", "CS", "IBO"]
    assert read_quote(record("PETR4")[:200]) is None and read_quote(record("PETR4", c=0)) is None
    assert b3_code("PETR4.SA") == "PETR4" and b3_code("petr4") == "PETR4" and b3_code("BOVA11.SA") == "BOVA11"
    assert b3_code("7203.T") is None and b3_code("BRK.B") is None and b3_code("") is None


def test_a_file_that_is_not_whole_or_not_a_file_of_quotes_is_an_error_never_an_empty_day():
    with pytest.raises(SourceError, match="cut short"):
        read_file(cotahist(DAY, trailer=False))
    with pytest.raises(SourceError, match="counts 99 records"):
        read_file(cotahist(DAY, count=99))
    with pytest.raises(SourceError, match="no cash-market quotes"):
        read_file(cotahist([record("AAPL34", bdi="34")]))
    with pytest.raises(SourceError, match="not a COTAHIST file"):
        read_file(cotahist([])[:0] + _zip_of(b"<html>Just a moment...</html>\r\n"))
    with pytest.raises(SourceError, match="not a zip"):
        read_file(b"<html>Attention Required! | Cloudflare</html>")


def _zip_of(text: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("COTAHIST_D30102026.TXT", text)
    return buf.getvalue()


def test_recent_days_come_from_the_daily_files_one_request_a_session_for_every_listing():
    src, session = _source(FakeResponse(200, cotahist(THURSDAY, "20261029", "COTAHIST_D29102026.TXT")),
                           FakeResponse(200, cotahist(DAY)), FakeResponse(404, b"not found"),
                           sessions=lambda d: d != FINADOS)
    bars = next(src.bar_pages("PETR4.SA", date(2026, 10, 29), FINADOS, "day"))
    assert [b["c"] for b in bars] == [52.0, 52.71] and _days(bars) == [date(2026, 10, 29), date(2026, 10, 30)]
    assert _files(session) == ["COTAHIST_D29102026.ZIP", "COTAHIST_D30102026.ZIP", "COTAHIST_D02112026.ZIP"]
    assert session.calls[0][2]["User-Agent"].startswith("liminal/") and session.calls[0][1] is None
    assert [b["c"] for b in next(src.bar_pages("VALE3.SA", date(2026, 10, 29), FINADOS, "day"))] == [60.0, 62.02]
    assert len(session.calls) == 3  # the same files serve every listing
    newest = next(src.bar_pages("petr4", date(2026, 10, 29), FINADOS, "day", descending=True, limit=1))
    assert _days(newest) == [date(2026, 10, 30)]
    exact = next(src.bar_pages("PETR4.SA", datetime(2026, 10, 30, tzinfo=ET), datetime(2026, 10, 31, tzinfo=ET), "day"))
    assert _days(exact) == [date(2026, 10, 30)] and len(session.calls) == 3
    assert next(src.bar_pages("XXXX3.SA", date(2026, 10, 29), FINADOS, "day")) == []  # not in the files: no bars
    assert src.covers("PETR4.SA") and src.covers("HGLG11") and not src.covers("7203.T") and not src.covers("BRK.B")
    assert src.folder is None and src.first_day() == date(2025, 1, 1)


def test_a_missing_daily_file_is_a_holiday_or_a_file_not_out_yet():
    # B3's calendar cannot say (None): the latest weekday's missing file is not out yet, asked again next run; an
    # older weekday's a holiday, never asked again
    src, session = _source(FakeResponse(404, b""), FakeResponse(404, b""))
    with pytest.raises(SourceError, match="not out yet"):
        next(src.bar_pages("PETR4.SA", FINADOS, FINADOS, "day"))  # Monday, the latest weekday before Tuesday the 3rd
    assert next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day")) == []
    assert len(session.calls) == 2
    with pytest.raises(SourceError, match="not out yet"):  # remembered: not asked again this run
        next(src.bar_pages("PETR4.SA", FINADOS, FINADOS, "day"))
    assert len(session.calls) == 2
    # with the calendar: a holiday's missing file is a day without bars, a session's is not out yet (for a week)
    src, session = _source(FakeResponse(404, b""), FakeResponse(404, b""), FakeResponse(404, b""),
                           sessions=lambda d: d != FINADOS)
    assert next(src.bar_pages("PETR4.SA", FINADOS, FINADOS, "day")) == []
    with pytest.raises(SourceError, match="not out yet"):
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day"))  # four days ago
    assert next(src.bar_pages("PETR4.SA", date(2026, 10, 20), date(2026, 10, 20), "day")) == []  # two weeks: given up
    assert next(src.bar_pages("PETR4.SA", date(2026, 11, 4), date(2026, 11, 6), "day")) == []  # the future: nothing asked
    assert len(session.calls) == 3


def test_the_history_comes_from_the_yearly_files_kept_in_the_folder_and_indexed(tmp_path):
    y2025 = cotahist([record("PETR4", day="20251229", c=3800, avg=3790), record("PETR4", day="20251230", c=3850, avg=3840),
                      record("PETR4", day="20251231", c=3870, avg=3860), record("VALE3", day="20251231", c=5500, avg=5490),
                      record("OIBR3", day="20250612", name="OI", especi="ON      NM", c=100, avg=100, isin="BROIBRACNOR1")],
                     "20260102", "COTAHIST_A2025.TXT")
    y2026 = cotahist([record("PETR4", day="20260102", c=3900, avg=3890), record("PETR4", day="20261029", c=5200, avg=5190),
                      record("PETR4")], "20261030", "COTAHIST_A2026.TXT")
    folder = tmp_path / "sources" / "b3"
    holidays = lambda d: d != FINADOS  # noqa: E731
    src, session = _source(FakeResponse(200, y2025), FakeResponse(200, y2026), FakeResponse(404, b""), folder=folder,
                           sessions=holidays)
    bars = next(src.bar_pages("PETR4.SA", date(2024, 1, 1), FINADOS, "day"))
    assert _days(bars) == [date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2), date(2026, 10, 29),
                           date(2026, 10, 30)]
    assert bars[0]["c"] == 38.0 and bars[-1] == {"t": ms(datetime(2026, 10, 30, tzinfo=ET)), "o": 52.45, "h": 53.66,
                                                  "l": 52.36, "c": 52.71, "v": 61743200.0, "vw": 52.88, "n": 77862}
    # 2024 is before the floor (two years kept); the Monday past the 2026 file's last session is a daily file
    assert _files(session) == ["COTAHIST_A2025.ZIP", "COTAHIST_A2026.ZIP", "COTAHIST_D02112026.ZIP"]
    assert sorted(p.name for p in folder.iterdir()) == ["COTAHIST_A2025.ZIP", "COTAHIST_A2025.db", "COTAHIST_A2026.ZIP",
                                                         "COTAHIST_A2026.db"]
    assert (folder / "COTAHIST_A2026.ZIP").read_bytes() == y2026
    index = YearIndex(folder / "COTAHIST_A2025.ZIP")
    assert index.fresh() and index.last() == date(2025, 12, 31) and index.meta("quotes") == "5"
    assert index.listing("OIBR3").name == "OI ON NM" and index.listing("XXXX3") is None
    assert [b["c"] for b in next(src.bar_pages("VALE3.SA", date(2025, 1, 1), FINADOS, "day"))] == [55.0]
    assert len(session.calls) == 3  # the indexes and the day's file serve every listing
    # another run on the same folder: the indexes serve, nothing downloaded but the day past them
    again, session2 = _source(FakeResponse(404, b""), folder=folder, sessions=holidays)
    assert _days(next(again.bar_pages("PETR4.SA", date(2025, 12, 30), FINADOS, "day"))) == [
        date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2), date(2026, 10, 29), date(2026, 10, 30)]
    assert _files(session2) == ["COTAHIST_D02112026.ZIP"]
    # a past year's file ending before the year's last weekday (B3 closes on December 31): the weekday after its
    # last session is asked once a run, a daily file that is not there, and read as a day without a session
    short = cotahist([record("PETR4", day="20241230", c=3700, avg=3690)], "20250102", "COTAHIST_A2024.TXT")
    three, session3 = _source(FakeResponse(200, short), FakeResponse(404, b""), folder=folder, years=3, sessions=holidays)
    assert _days(next(three.bar_pages("PETR4.SA", date(2024, 12, 1), date(2024, 12, 31), "day"))) == [date(2024, 12, 30)]
    assert _files(session3) == ["COTAHIST_A2024.ZIP", "COTAHIST_D31122024.ZIP"]
    assert _days(next(three.bar_pages("VALE3.SA", date(2024, 12, 1), date(2024, 12, 31), "day"))) == [] and len(session3.calls) == 2
    # weeks later, more than REFRESH_GAP weekdays past the 2026 file's last session are asked for: fetched again
    newer = cotahist([record("PETR4", day="20260102", c=3900, avg=3890), record("PETR4", day="20261211", c=5500, avg=5490)],
                     "20261211", "COTAHIST_A2026.TXT")
    monday = cotahist([record("PETR4", day="20261214", c=5600, avg=5590)], "20261214", "COTAHIST_D14122026.TXT")
    later, session3 = _source(FakeResponse(200, newer), FakeResponse(200, monday), folder=folder, today=date(2026, 12, 15),
                              sessions=lambda d: True)
    bars = next(later.bar_pages("PETR4.SA", date(2026, 10, 1), date(2026, 12, 14), "day"))
    assert _files(session3) == ["COTAHIST_A2026.ZIP", "COTAHIST_D14122026.ZIP"] and [b["c"] for b in bars] == [55.0, 56.0]
    assert YearIndex(folder / "COTAHIST_A2026.ZIP").last() == date(2026, 12, 11)
    assert _days(next(later.bar_pages("PETR4.SA", date(2026, 10, 1), date(2026, 12, 14), "day"))) == [
        date(2026, 12, 11), date(2026, 12, 14)] and len(session3.calls) == 2  # once a run
    # a year just begun has no yearly file yet (404): the daily files serve it, the year asked once a run
    jan4 = cotahist([record("PETR4", day="20270104", c=5700, avg=5690)], "20270104", "COTAHIST_D04012027.TXT")
    fresh, session4 = _source(FakeResponse(404, b""), FakeResponse(200, jan4), *[FakeResponse(404, b"") for _ in range(9)],
                              folder=folder, years=3, today=date(2027, 1, 5), sessions=lambda d: True)
    assert [b["c"] for b in next(fresh.bar_pages("PETR4.SA", date(2027, 1, 4), date(2027, 1, 4), "day"))] == [57.0]
    assert [b["c"] for b in next(fresh.bar_pages("VALE3.SA", date(2027, 1, 4), date(2027, 1, 4), "day"))] == []
    assert _files(session4) == ["COTAHIST_A2027.ZIP", "COTAHIST_D04012027.ZIP"] and fresh.first_day() == date(2025, 1, 1)
    # a record from the yearly files here (2025's), for a listing not in the last sessions' files (the ten weekdays
    # before today looked at: Monday's file is read already, the rest are not there)
    assert fresh.instrument("OIBR3.SA").name == "OI ON NM" and len(session4.calls) == 11
    with pytest.raises(SourceError, match="nor in the yearly files"):
        fresh.instrument("XXXX3.SA")
    assert len(session4.calls) == 11


def test_a_yearly_file_that_is_not_a_zip_is_an_error_and_not_kept(tmp_path):
    folder = tmp_path / "b3"
    src, session = _source(FakeResponse(200, b"<html>Attention Required! | Cloudflare</html>"), folder=folder)
    with pytest.raises(SourceError, match="not a zip"):
        next(src.bar_pages("PETR4.SA", date(2026, 1, 2), date(2026, 1, 2), "day"))
    assert _files(session) == ["COTAHIST_A2026.ZIP"] and not list(folder.iterdir())
    src, session = _source(FakeResponse(200, cotahist([record("PETR4", day="20260102")], "20260102", "COTAHIST_A2026.TXT",
                                                      trailer=False)), folder=folder)
    with pytest.raises(SourceError, match="cut short"):
        next(src.bar_pages("PETR4.SA", date(2026, 1, 2), date(2026, 1, 2), "day"))
    assert not list(folder.iterdir())


def test_listings_and_records_come_from_the_latest_sessions_files():
    # Tuesday the 3rd: Monday's file is missing (Finados), Friday's and Thursday's are there, then nothing for a while
    src, session = _source(FakeResponse(404, b""), FakeResponse(200, cotahist(DAY)),
                           FakeResponse(200, cotahist(THURSDAY, "20261029", "COTAHIST_D29102026.TXT")),
                           *[FakeResponse(404, b"") for _ in range(7)])
    rows = src.listings("BVMF")
    assert [r["ticker"] for r in rows] == ["BOVA11.SA", "BRKM5.SA", "HGLG11.SA", "IBOV11.SA", "KLBN11.SA", "PETR4.SA",
                                           "VALE3.SA"]
    assert rows[5] == {"ticker": "PETR4.SA", "name": "PETROBRAS PN N2", "exchange": "BVMF", "type": "CS", "figi": None,
                       "kind": "stock"}
    types = {r["ticker"]: r["type"] for r in rows}
    assert (types["HGLG11.SA"], types["BOVA11.SA"], types["KLBN11.SA"], types["BRKM5.SA"]) == ("FUND", "ETF", "CS", "CS")
    assert _files(session)[:3] == ["COTAHIST_D02112026.ZIP", "COTAHIST_D30102026.ZIP", "COTAHIST_D29102026.ZIP"]
    assert len(session.calls) == 10 and src.listings("BVMF") == rows and len(session.calls) == 10  # once a run
    with pytest.raises(NotImplementedError):
        src.listings("XTAI")
    inst = src.instrument("PETR4.SA")
    assert inst.kind == "stock" and inst.ticker == "PETR4.SA" and inst.name == "PETROBRAS PN N2" and inst.currency == "BRL"
    assert (inst.primary_exchange.mic, inst.primary_exchange.name, inst.primary_exchange.country.code) == ("BVMF", "B3", "BR")
    assert inst.list_date is None and inst.active and inst.figi is None
    assert inst.details["isin"] == "BRPETRACNPR6" and inst.details["date"] == "2026-10-30" and inst.details["bdi"] == "02"
    assert src.instrument("KLBN11.SA").details["date"] == "2026-10-29"  # the latest file naming it
    assert src.instrument("BOVA11.SA").kind == "etf" and src.instrument("HGLG11.SA").kind == "instrument"
    assert src.instrument("brkm5").ticker == "BRKM5.SA"
    with pytest.raises(NotEntitled):
        src.instrument("7203.T")
    with pytest.raises(SourceError, match="none of the last 5 sessions"):
        src.instrument("XXXX3.SA")
    assert len(session.calls) == 10
    assert [e.mic for e in src.exchanges()] == ["BVMF"] and src.resolutions("stock") == ("day",) and src.resolutions("fx") == ()
    assert src.serves("stock") and not src.serves("fx") and src.is_entitled("day", "PETR4.SA") and not src.archival


def test_refusals_and_the_servers_answers():
    src, session = _source()
    with pytest.raises(NotEntitled, match="daily"):
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "minute"))
    with pytest.raises(NotEntitled, match="not a B3 listing"):
        next(src.bar_pages("7203.T", date(2026, 10, 30), date(2026, 10, 30), "day"))
    assert next(src.bar_pages("PETR4.SA", date(2020, 1, 1), date(2024, 12, 31), "day")) == []  # before the floor
    assert session.calls == []
    # a 429 (after the client's retries) is a RateLimited with the wait asked for, never empty bars
    src, _ = _source(*[FakeResponse(429, b"slow down", headers={"Retry-After": "30"}) for _ in range(5)])
    with pytest.raises(RateLimited) as e:
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day"))
    assert e.value.retry_after == 30.0
    # an answer that is not a zip (a page), or a file cut short, is an error, not an empty day
    src, _ = _source(FakeResponse(200, b"<html>challenge</html>"))
    with pytest.raises(SourceError, match="not a zip"):
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day"))
    src, _ = _source(FakeResponse(200, cotahist(DAY, trailer=False)))
    with pytest.raises(SourceError, match="cut short"):
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day"))
    src, _ = _source(FakeResponse(503, b"down"), FakeResponse(503, b"down"), FakeResponse(503, b"down"),
                     FakeResponse(503, b"down"), FakeResponse(503, b"down"))
    with pytest.raises(SourceError) as e:
        next(src.bar_pages("PETR4.SA", date(2026, 10, 30), date(2026, 10, 30), "day"))
    assert e.value.status == 503


def test_b3s_calendar_tells_a_holiday_from_a_file_not_out_yet(monkeypatch):
    from liminal_libs import sessions

    class Calendar:  # what the library's world_calendar gives (exchange_calendars' BVMF): a span, its sessions
        first_session, last_session = datetime(2026, 10, 5), datetime(2027, 12, 8)

        def is_session(self, day):
            return day not in ("2026-11-02", "2026-11-20")

    monkeypatch.setattr(sessions, "world_calendar", lambda name: Calendar() if name == "BVMF" else None)
    assert b3_session(FINADOS) is False and b3_session(date(2026, 11, 20)) is False and b3_session(date(2026, 10, 30)) is True
    assert b3_session(date(1995, 1, 2)) is None and b3_session(date(2028, 1, 3)) is None  # outside the calendar's span
    monkeypatch.setattr(sessions, "world_calendar", lambda name: (_ for _ in ()).throw(ImportError("no exchange_calendars")))
    assert b3_session(FINADOS) is None  # no calendar here: the plugin's own rule (the latest weekday is not out yet)
    src = B3Source(PublicClient("https://x.test/files", session=FakeSession([]), sleep=lambda s: None))
    assert src.is_session is b3_session and src.years == 2  # the source asks it by default


def test_a_failing_certificate_chain_is_an_error_at_once_never_worked_around(monkeypatch):
    calls = []

    def refuse(self, url, **kwargs):
        calls.append(url)
        raise requests.exceptions.SSLError("certificate verify failed: unable to get local issuer certificate")

    monkeypatch.setattr(SameHostSession, "get", refuse)
    session = B3Session()
    assert session.verify is True
    client = PublicClient("https://x.test/files", session=session, sleep=lambda s: None)
    with pytest.raises(SourceError, match="verification stays on") as e:
        client.get_bytes("/COTAHIST_D30102026.ZIP")
    assert e.value.status == 0 and "unable to get local issuer" in str(e.value) and len(calls) == 1  # no retries


def test_the_app_loads_it_from_this_package_with_its_own_version():
    registry.reload()
    assert plugin_integrator_b3.PLUGIN.name == "b3" and not hasattr(plugin_integrator_b3, "PLUGINS")
    p = provider("b3")
    assert p is not None, dict(registry.errors())
    assert p.origin in {"package: plugin-integrator-b3 (plugin_integrator_b3)", "plugins/plugin-integrator-b3 (plugin_integrator_b3)"}  # its package, or its checkout in the workspace's plugin directory
    assert p.version == version("plugin-integrator-b3") and p.api == "2.2"
    assert registry.api_refusal(p.api) is None and plugin_integrator_b3.PLUGIN.version == ""
    assert not [why for where, why in registry.errors() if "b3" in where or "b3" in why]
    row = {r["name"]: r for r in registry.menu()}["b3"]
    assert (row["version"], row["api"], row["keys"], row["archive"]) == (version("plugin-integrator-b3"), "2.2", [], False)
    assert registry.plugin_feeds("b3") == ["b3"] and registry.plugin_of("b3") == "b3"
    assert [(d["where"], d["engine"]) for d in row["databases"] if d["where"].startswith("sources/")] == [("sources/b3/", "files")]
    assert any(d["where"] == "feeds/b3.db" for d in row["databases"])


def test_it_keeps_its_facts(monkeypatch):
    registry.reload()
    s = load_settings(env={}, env_file=None)
    view = s.for_source("b3")
    today = date.today()
    assert view.tier == replace(TIER, history_start=date(today.year - 1, 1, 1)) and view.tier.name == "b3"
    assert (view.max_requests_per_run, view.max_requests_per_day) == (PER_RUN, PER_DAY) == (100, 400)
    assert rate_window("b3", s) == (10, 60.0) and not is_archive("b3") and markets("b3", s) == {"stock": "b3"}
    assert supports("b3", resolve("PETR4.SA")) and supports("b3", resolve("B3:PETR4")) and supports("b3", resolve("PETR4::BVMF"))
    assert not supports("b3", resolve("AAPL")) and not supports("b3", resolve("2330.TW")) and not supports("b3", resolve("EURUSD"))
    assert composite.feed_kind("b3", "stock") == "exchange"
    p = provider("b3")
    assert p.exchanges == {"BVMF"} and p.options == ("B3_HISTORY_YEARS",) and p.keys == () and p.keys_set(s) is None
    assert p.plan_choices["B3_HISTORY_YEARS"][0] == "2"  # the default first: the Plugins view offers them
    assert p.provides == {"stock": ("day",)} and p.request_bars("day") is None and p.bulk_resolution == "day"
    assert history_floor(view.tier, date(2026, 11, 3)) == date(today.year - 1, 1, 1)
    assert freshness.plan_delay(view.tier, "stock").kind == "eod"
    assert feed_profiles(s)["b3"].venues == {"stock": "b3"} and feed_profiles(s)["b3"].assets == frozenset({"stock"})
    # the years kept move the floor: more years, further back, never before 1995-01-02; a bad value is the default
    assert history_years(load_settings(env={"B3_HISTORY_YEARS": "40"}, env_file=None)) == 40
    assert load_settings(env={"B3_HISTORY_YEARS": "40"}, env_file=None).for_source("b3").tier.history_start == date(1995, 1, 2)
    assert load_settings(env={"B3_HISTORY_YEARS": "1"}, env_file=None).for_source("b3").tier.history_start == date(today.year, 1, 1)
    assert history_years(load_settings(env={"B3_HISTORY_YEARS": "x"}, env_file=None)) == 2
    assert history_years(load_settings(env={"B3_HISTORY_YEARS": "0"}, env_file=None)) == 1
    assert first_day(2, date(2026, 11, 3)) == date(2025, 1, 1) and first_day(100, date(2026, 11, 3)) == date(1995, 1, 2)
    # built: the public client on B3's host, its window and day cap held for the machine under its name, the
    # folder beside the main file (none for a database in memory)
    held = []
    monkeypatch.setattr(plugin_integrator_b3, "limiter", lambda name, st: held.append(("window", name)))
    monkeypatch.setattr(plugin_integrator_b3, "machine_caps", lambda client, name, st: held.append(("day", name)) or client)
    src = plugin_integrator_b3.PLUGIN.build(load_settings(env={"B3_HISTORY_YEARS": "3"}, env_file=None), 5)
    assert held == [("window", "b3"), ("day", "b3")] and src.name == "b3" and src.years == 3
    assert src.client.base_url == BASE_URL and isinstance(src.client._session, B3Session) and src.client.request_budget == 5
    assert src.folder == s.db_path.parent / "sources" / "b3"
    assert plugin_integrator_b3.PLUGIN.build(replace(s, db_path=Path(":memory:")), None).folder is None


def test_it_is_a_class_on_plugin_api_2_2():
    """The plugin is an instance of its class (plugin API 2.2, liminal-libs 1.49.0): what it declares its class
    attributes, what it does its methods; the registry loads it as that class."""
    assert isinstance(plugin_integrator_b3.PLUGIN, plugin_integrator_b3.B3Plugin) and isinstance(plugin_integrator_b3.PLUGIN, FeedPlugin)
    assert plugin_integrator_b3.PLUGIN.api == "2.2" and not plugin_integrator_b3.PLUGIN.is_record
    p = registry.reload()["b3"]
    assert type(p) is plugin_integrator_b3.B3Plugin and p.api == "2.2" and p.name == "b3"
    # the hooks it gives, the rest the contract's defaults
    assert p.gives("build") and p.gives("view") and p.gives("rate") and p.gives("markets")
    assert not p.gives("scan") and not p.gives("has_keys") and not p.gives("carries") and not p.has_settings
    # it declares its publication delay itself, the library's row for it still in place
    assert p.delays == {"b3": {"stock": Delay("eod")}} and p.delays["b3"] == freshness.PLAN_DELAYS["b3"]
