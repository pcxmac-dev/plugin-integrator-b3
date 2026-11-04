"""B3's COTAHIST files (the exchange's own quotes files, no key): every cash-market listing's day on
B3 (Sao Paulo, MIC BVMF) from the daily file of each session and the yearly file of each year.

The files are at https://bvmf.bmfbovespa.com.br/InstDados/SerHist/ (checked 2026-11-02 with one
request, the daily file of Friday 2026-10-30: HTTP 200, application/x-zip-compressed, 588 KB, served
by Cloudflare, the certificate chain verified by certifi, Last-Modified 23:55 UTC that evening, so a
session's file is out about three hours after its close):
- COTAHIST_D{DDMMYYYY}.ZIP, one a session, every listing's day in it (16,811 records that day);
- COTAHIST_A{YYYY}.ZIP, one a year, every session of the year so far (60 to 90 MB zipped, about a
  gigabyte of text, most of it options); never downloaded during the build or the tests.
Each is a zip of one text file (COTAHIST_D30102026.TXT): fixed-width records of 245 characters ended
by CRLF, ASCII that day, read as latin-1 (B3's layout allows accented names): a header (type 00,
"00COTAHIST.2026BOVESPA 20261030"), the quotes (type 01), a trailer (type 99: the count of quotes).
A quote's fields (1-based positions; B3's layout, the widths of the rb3 template):
  TIPREG 1-2, DATA 3-10 (YYYYMMDD), CODBDI 11-12, CODNEG 13-24, TPMERC 25-27, NOMRES 28-39,
  ESPECI 40-49, PRAZOT 50-52, MODREF 53-56, PREABE 57-69, PREMAX 70-82, PREMIN 83-95, PREMED 96-108,
  PREULT 109-121, PREOFC 122-134, PREOFV 135-147, TOTNEG 148-152, QUATOT 153-170, VOLTOT 171-188,
  PREEXE 189-201, INDOPC 202, DATVEN 203-210, FATCOT 211-217, PTOEXE 218-230, CODISI 231-242,
  DISMES 243-245.
Prices carry two implied decimals (PREULT 0000000005271 is R$ 52.71) and are quoted per FATCOT
shares (1 for all but three records that day: 100 for IBOV11, 1000 for two funds), so they are
divided by it here and every bar is one share's; QUATOT is the day's shares (the volume), PREMED the
average price (the vwap), TOTNEG the trades; VOLTOT = PREMED x QUATOT / FATCOT, checked.
Kept: the cash market (TPMERC 010; the options and their exercises, the forward and the fractional
market, 020, are left) of the standard lot (CODBDI 02, and 05 to 09 and 11: the same lots of
companies under sanction, concordata, recovery or intervention; Braskem's were in 07 that day), the
real estate funds (12), the agribusiness funds (13) and the ETFs (14, certificates of investment):
that day 319 + 286 + 3 + 252 of the 1,481 cash records. Left: BDRs (34, 35, 36: ESPECI DRN, DR1 to
DR3, DRE), rights and receipts (10), bonuses (22), auctions and the rest.
"""
from __future__ import annotations

import io
import os
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

import requests

from liminal_libs.errors import NotEntitled, SourceError
from liminal_libs.models import Exchange, Instrument, country, instrument_class, listing_ticker, suffix_mic
from liminal_libs.sources.archive import ET, Archive, bounds, ms
from liminal_libs.sources.http import PublicClient, SameHostSession

MIC = "BVMF"
B3 = Exchange("b3", 1, "B3", country("BR"), mic=MIC, acronym="B3", type="exchange")
START = date(1995, 1, 2)  # the first session served: B3's files reach further back, in older currencies
CASH = "010"  # TPMERC: the cash market
STANDARD_LOT = ("02", "05", "06", "07", "08", "09", "11")  # CODBDI: the standard lot, whatever the company's standing
FUNDS = ("12", "13", "14")  # CODBDI: real estate funds, agribusiness funds, ETFs
KEPT_BDI = frozenset(STANDARD_LOT + FUNDS)
RECORD = 245
DAY_CACHE = 64  # daily files held in memory for the run (about 600 quotes each), the oldest let go
LISTING_SESSIONS = 5  # the sessions whose files list the listings: a paper trading some days only is still listed
REFRESH_GAP = 20  # weekdays past a yearly file's last session: more of them asked, and the file is fetched again
LATE_DAYS = 7  # a session whose file is still missing after this many days is taken as one without a file


def b3_code(ticker: str) -> str | None:
    """B3's code of a stored ticker (PETR4 for PETR4.SA; a bare code as it is); None for a listing
    elsewhere (7203.T, BRK.B) or what is no code at all."""
    t = ticker.strip().upper()
    base, dot, tail = t.rpartition(".")
    if base and dot:
        return base if suffix_mic(tail) == MIC and base.isalnum() else None
    return t if t.isalnum() else None


def latest_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def weekdays(lo: date, hi: date) -> Iterator[date]:
    """The weekdays from lo to hi, both included."""
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def weekdays_after(after: date, upto: date) -> int:
    """How many weekdays fall after `after`, up to `upto` included."""
    return sum(1 for _ in weekdays(after + timedelta(days=1), upto))


def first_day(years: int, today: date) -> date:
    """The first session served with `years` yearly files kept, the current year's counted: January 1
    of the earliest of them, never before START."""
    return max(START, date(today.year - max(1, years) + 1, 1, 1))


def day_ms(d: date) -> int:
    """A day bar's stamp: midnight US/Eastern of the B3 session's date (the library's rule for every
    listing, abroad too)."""
    return ms(datetime(d.year, d.month, d.day, tzinfo=ET))


def b3_session(d: date) -> bool | None:
    """Whether B3 had a session on `d`, by its own calendar (the library's world_calendar:
    exchange_calendars' BVMF, which knows its holidays from a month back to a year on); None when it
    cannot say (a day outside that span, or no calendar here)."""
    try:
        from liminal_libs.sessions import world_calendar

        cal = world_calendar(MIC)
        if not cal.first_session.date() <= d <= cal.last_session.date():
            return None
        return bool(cal.is_session(d.isoformat()))
    except Exception:  # noqa: BLE001 - the calendar is a help, never a stop
        return None


class B3Session(SameHostSession):
    """The client's session: a certificate chain that does not verify is reported at once, in words
    (the library's client would try again four times, as for a dropped connection), and never worked
    around: verification stays on."""

    def get(self, url, **kwargs):
        try:
            return super().get(url, **kwargs)
        except requests.exceptions.SSLError as e:
            raise SourceError(0, "B3's certificate chain could not be verified (verification stays on; nothing was "
                                 f"read): {type(e).__name__}: {str(e)[:160]}") from e


@dataclass(frozen=True)
class Quote:
    """One kept record: a listing's day."""
    day: date
    code: str  # CODNEG: PETR4
    bdi: str  # CODBDI: 02 the standard lot, 12 a real estate fund, 14 an ETF ...
    name: str  # NOMRES and ESPECI: "PETROBRAS PN N2"
    especi: str  # the kind of paper: ON, PN, UNT, CI (a fund's quota) ...
    isin: str
    currency: str  # BRL (MODREF "R$")
    factor: int  # FATCOT: the shares a price in the file is quoted for
    bar: dict = field(default_factory=dict)  # t, o, h, l, c, v, vw, n


def read_quote(line: str) -> Quote | None:
    """A type 01 record of the cash market in a kept BDI as a Quote; None for any other record, and
    for one whose numbers cannot be read or that has no closing price."""
    if len(line) < RECORD or line[0:2] != "01" or line[24:27] != CASH or line[10:12] not in KEPT_BDI:
        return None
    try:
        d = date(int(line[2:6]), int(line[6:8]), int(line[8:10]))
        factor = int(line[210:217]) or 1
        o, h, l, vw, c = (int(line[a:b]) / 100 / factor for a, b in ((56, 69), (69, 82), (82, 95), (95, 108), (108, 121)))
        n, v = int(line[147:152]), float(int(line[152:170]))
    except ValueError:
        return None
    if c <= 0:
        return None
    o = o if o > 0 else c
    h, l = max(h, o, c), min(x for x in (l, o, c) if x > 0)
    currency = line[52:56].strip()
    return Quote(d, line[12:24].strip(), line[10:12], " ".join((line[27:39] + " " + line[39:49]).split()),
                 line[39:49].strip(), line[230:242].strip(), "BRL" if currency == "R$" else currency, factor,
                 {"t": day_ms(d), "o": o, "h": h, "l": l, "c": c, "v": v, "vw": vw if vw > 0 else None, "n": n})


def quotes_in(lines) -> Iterator[Quote]:
    """The kept quotes of a COTAHIST text (its binary lines), the header and the trailer checked: the
    first line names COTAHIST, the trailer's count is the quotes' (a file cut short, or of another
    layout, is an error: never a short history)."""
    first, n, trailer = True, 0, None
    for raw in lines:
        line = raw.decode("latin-1").rstrip("\r\n")
        if first:
            first = False
            if not line.startswith("00COTAHIST"):
                raise SourceError(200, f"not a COTAHIST file (it begins {line[:24]!r})")
            continue
        kind = line[0:2]
        if kind == "01":
            n += 1
            q = read_quote(line)
            if q is not None:
                yield q
        elif kind == "99":
            trailer = line
    if first:
        raise SourceError(200, "an empty file")
    if trailer is None:
        raise SourceError(200, f"the file is cut short: no trailer after {n:,} records")
    try:
        total = int(trailer[31:42])
    except ValueError:
        total = -1
    if total not in (n, n + 2):
        raise SourceError(200, f"the trailer counts {total:,} records, {n:,} read: cut short, or the layout changed")


def _member(z: zipfile.ZipFile) -> str:
    names = z.namelist()
    if not names:
        raise SourceError(200, "an empty zip")
    return next((n for n in names if n.upper().endswith(".TXT")), names[0])


def read_file(data: bytes) -> list[Quote]:
    """A daily file's quotes, from the zip's bytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z, z.open(_member(z)) as f:
            out = list(quotes_in(f))
    except zipfile.BadZipFile as e:
        raise SourceError(200, f"not a zip ({e}; the answer begins {data[:16]!r})") from e
    if not out:
        raise SourceError(200, "no cash-market quotes in the file (its layout changed?)")
    return out


def listing_type(q: Quote) -> str:
    """The directory's type of a listing: CS for a company's shares and units (ON, PN, UNT), ETF for
    an ETF's quotas (CI in BDI 14), FUND for a fund's (CI in 12 and 13; a FIDC), else the paper's
    kind as B3 names it (IBO for IBOV11)."""
    kind = q.especi.split()[0] if q.especi.split() else ""
    if kind.startswith(("ON", "PN", "UNT")):
        return "CS"
    if kind.startswith("CI"):
        return "ETF" if q.bdi == "14" else "FUND"
    if kind.startswith("FIDC"):
        return "FUND"
    return kind.rstrip("/") or "OTHER"


def _num(d: date) -> int:
    return d.year * 10_000 + d.month * 100 + d.day


def _date(n: int) -> date:
    return date(n // 10_000, n // 100 % 100, n % 100)


SCHEMA = """
CREATE TABLE bars (code TEXT NOT NULL, day INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, vw REAL, v REAL, n INTEGER,
                   PRIMARY KEY (code, day)) WITHOUT ROWID;
CREATE TABLE listings (code TEXT PRIMARY KEY, day INTEGER, bdi TEXT, name TEXT, especi TEXT, isin TEXT, currency TEXT,
                       factor INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


class YearIndex:
    """A yearly file's kept quotes in a SQLite file beside it (COTAHIST_A2026.db): built once from the
    zip in one pass (rebuilt when the zip changes), so a listing's days are read by its code at once
    and nothing of the gigabyte stays in memory. Opened for each read and closed after it."""

    def __init__(self, zip_path: Path):
        self.zip = zip_path
        self.path = zip_path.with_suffix(".db")

    def _stamp(self) -> str:
        st = self.zip.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"

    def meta(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def fresh(self) -> bool:
        """Whether the index is here and of the zip as it is now."""
        if not self.zip.exists() or not self.path.exists():
            return False
        try:
            return self.meta("stamp") == self._stamp()
        except sqlite3.Error:
            return False

    def last(self) -> date | None:
        """The last session in the file."""
        value = self.meta("last")
        return _date(int(value)) if value else None

    def build(self) -> None:
        """One pass over the zip into a new index (the old one replaced only when the new is complete)."""
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(tmp)
        try:
            con.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;" + SCHEMA)
            insert = "INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            latest: dict[str, Quote] = {}
            batch: list[tuple] = []
            n, last = 0, None
            with zipfile.ZipFile(self.zip) as z, z.open(_member(z)) as f:
                for q in quotes_in(f):
                    n += 1
                    b = q.bar
                    batch.append((q.code, _num(q.day), b["o"], b["h"], b["l"], b["c"], b["vw"], b["v"], b["n"]))
                    if last is None or q.day > last:
                        last = q.day
                    was = latest.get(q.code)
                    if was is None or q.day >= was.day:
                        latest[q.code] = q
                    if len(batch) >= 5000:
                        con.executemany(insert, batch)
                        batch.clear()
            if batch:
                con.executemany(insert, batch)
            if not n or last is None:
                raise SourceError(200, f"no cash-market quotes in {self.zip.name} (its layout changed?)")
            con.executemany("INSERT OR REPLACE INTO listings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [(q.code, _num(q.day), q.bdi, q.name, q.especi, q.isin, q.currency, q.factor)
                             for q in latest.values()])
            con.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                            [("stamp", self._stamp()), ("last", str(_num(last))), ("quotes", str(n)),
                             ("built", datetime.now(timezone.utc).isoformat(timespec="seconds"))])
            con.commit()
        except BaseException:
            con.close()
            tmp.unlink(missing_ok=True)
            raise
        con.close()
        os.replace(tmp, self.path)

    def bars(self, code: str, lo: date, hi: date) -> list[dict]:
        """A listing's day bars from lo to hi (both included), oldest first."""
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute("SELECT day, o, h, l, c, vw, v, n FROM bars WHERE code = ? AND day BETWEEN ? AND ? "
                               "ORDER BY day", (code, _num(lo), _num(hi))).fetchall()
        return [{"t": day_ms(_date(day)), "o": o, "h": h, "l": l, "c": c, "v": v, "vw": vw, "n": n}
                for day, o, h, l, c, vw, v, n in rows]

    def listing(self, code: str) -> Quote | None:
        """A listing as its latest record in the file names it (no bar)."""
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute("SELECT day, bdi, name, especi, isin, currency, factor FROM listings WHERE code = ?",
                              (code,)).fetchone()
        if row is None:
            return None
        day, bdi, name, especi, isin, currency, factor = row
        return Quote(_date(day), code, bdi, name, especi, isin, currency, factor)


class B3Source(Archive):
    """B3's cash-market day bars from its COTAHIST files: the recent days from the daily files (one
    request serves every listing's day, read once a run), the history from the yearly files kept in
    the plugin's folder (`folder`, sources/b3/ in the data folder: one request a year, its index
    beside it), the last `years` calendar years of them. Not an archive: each session's file comes
    the same evening. Without a folder (a database in memory) the daily files serve every day."""

    name = "b3"
    assets = ("stock",)
    archival = False

    def __init__(self, client: PublicClient, folder: Path | None = None, years: int = 2, today: date | None = None,
                 is_session: Callable[[date], bool | None] | None = None):
        super().__init__(client)
        self.folder = folder
        self.years = max(1, years)
        self.today = today
        self.is_session = is_session or b3_session  # B3's calendar: whether a day had a session (None: unknown)
        self._days: dict[date, dict[str, Quote] | None] = {}  # a session's quotes by code; None: no file
        self._years: dict[int, YearIndex | None] = {}  # the yearly files' indexes; None: no file for the year
        self._fetched: set[int] = set()  # yearly files downloaded this run (once a run each)
        self._recent_quotes: dict[str, Quote] | None = None

    def _today(self) -> date:
        return self.today or datetime.now(ET).date()

    def first_day(self) -> date:
        """The first session served: January 1 of the earliest year kept (first_day)."""
        return first_day(self.years, self._today())

    def resolutions(self, asset: str) -> tuple[str, ...]:
        return ("day",) if asset == "stock" else ()

    def covers(self, ticker: str) -> bool:
        return b3_code(ticker) is not None

    def exchanges(self) -> list[Exchange]:
        return [B3]

    # -- the daily files ---------------------------------------------------------------------------

    def _day(self, d: date) -> dict[str, Quote] | None:
        """A session's quotes by code from its daily file, read once a run (DAY_CACHE of them kept);
        None when B3 has no file for the day (a 404), remembered too."""
        if d in self._days:
            return self._days[d]
        try:
            data, _ = self.client.get_bytes(f"/COTAHIST_D{d:%d%m%Y}.ZIP")
        except SourceError as e:
            if e.status != 404:
                raise
            quotes = None
        else:
            quotes = {q.code: q for q in read_file(data)}
        while len(self._days) >= DAY_CACHE:
            del self._days[next(iter(self._days))]
        self._days[d] = quotes
        return quotes

    def _quotes_on(self, d: date) -> dict[str, Quote]:
        """A session's quotes. A weekday without a file is a day without a session (a holiday: no
        bars, nothing to ask again) unless B3's calendar says there was one, or cannot say and the day
        is today or the latest weekday before it (the one asked for the morning after): then the file
        is not out yet, and the day is asked again next run (LATE_DAYS at most)."""
        quotes = self._day(d)
        if quotes is not None:
            return quotes
        today = self._today()
        session = self.is_session(d)
        if session is None:
            session = d >= latest_weekday(today - timedelta(days=1))
        if session and (today - d).days <= LATE_DAYS:
            raise SourceError(404, f"B3's file for the session of {d} is not out yet (it comes that evening); asked again next run")
        return {}

    def _recent(self) -> dict[str, Quote]:
        """Every listing's latest quote over the last LISTING_SESSIONS sessions with a file (today's
        when it is read already, then the weekdays before; one without a file is passed): a paper
        that trades some days only is still listed, and named."""
        if self._recent_quotes is not None:
            return self._recent_quotes
        today = self._today()
        d = today if self._days.get(today) else latest_weekday(today - timedelta(days=1))
        out: dict[str, Quote] = {}
        found = 0
        for _ in range(LISTING_SESSIONS + 5):
            quotes = self._day(d)
            if quotes:
                found += 1
                for code, q in quotes.items():
                    out.setdefault(code, q)  # newest first: the first seen is the latest
                if found == LISTING_SESSIONS:
                    break
            d = latest_weekday(d - timedelta(days=1))
        if not out:
            raise SourceError(404, f"no daily file of B3 in the last {LISTING_SESSIONS + 5} weekdays")
        self._recent_quotes = out
        return out

    # -- the yearly files --------------------------------------------------------------------------

    def _zip(self, year: int) -> Path:
        return self.folder / f"COTAHIST_A{year}.ZIP"

    def _build(self, index: YearIndex) -> None:
        try:
            index.build()
        except zipfile.BadZipFile as e:
            index.zip.unlink(missing_ok=True)
            raise SourceError(200, f"{index.zip.name} is not a zip ({e}); removed") from e
        except SourceError:
            index.zip.unlink(missing_ok=True)
            raise

    def _fetch(self, year: int) -> YearIndex | None:
        """The yearly file downloaded into the folder and indexed; None when B3 has none for the year
        (a 404: a year just begun), the index here before kept then."""
        try:
            data, _ = self.client.get_bytes(f"/COTAHIST_A{year}.ZIP")
        except SourceError as e:
            if e.status == 404:
                return self._years.get(year)
            raise
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._zip(year)
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        del data
        index = YearIndex(path)
        index.path.unlink(missing_ok=True)
        os.replace(part, path)
        self._build(index)
        return index

    def _year(self, year: int, upto: date) -> YearIndex | None:
        """The year's index, its file downloaded when it is not in the folder, or again when more than
        REFRESH_GAP weekdays past its last session are asked for (`upto`; fewer come from the daily
        files, a request each): once a run. None without a folder, and for a year B3 has no file for."""
        if self.folder is None:
            return None
        if year not in self._years:
            path = self._zip(year)
            index = YearIndex(path) if path.exists() else None
            if index is not None and not index.fresh():
                self._build(index)
            self._years[year] = index
        index = self._years[year]
        if year in self._fetched:
            return index
        last = index.last() if index is not None else None
        if index is not None and (last is None or weekdays_after(last, upto) <= REFRESH_GAP):
            return index
        self._fetched.add(year)
        self._years[year] = self._fetch(year)
        return self._years[year]

    def _index_here(self, year: int) -> YearIndex | None:
        """A year's index when it is in the folder already (nothing downloaded)."""
        if self.folder is None:
            return None
        index = self._years.get(year)
        if index is None and year not in self._years:
            path = self._zip(year)
            index = YearIndex(path) if path.exists() else None
        return index if index is not None and index.fresh() else None

    # -- the contract ------------------------------------------------------------------------------

    def bar_pages(self, ticker, start, end, timespan, multiplier=1, descending=False, asset="stock",
                  limit: int | None = None) -> Iterator[list[dict]]:
        if timespan != "day":
            raise NotEntitled(0, "B3's COTAHIST files are daily")
        code = b3_code(ticker)
        if code is None:
            raise NotEntitled(0, f"{ticker} is not a B3 listing (PETR4.SA)")
        a, b = bounds(start, end, ET)
        first, last = max(_date_of(start), self.first_day()), min(_date_of(end), self._today())
        bars: list[dict] = []
        for year in range(first.year, last.year + 1):
            lo, hi = max(first, date(year, 1, 1)), min(last, date(year, 12, 31))
            index = self._year(year, hi)
            covered = index.last() if index is not None else None
            if index is not None:
                bars += index.bars(code, lo, hi)
            for d in weekdays(lo if covered is None else max(lo, covered + timedelta(days=1)), hi):
                q = self._quotes_on(d).get(code)
                if q is not None:
                    bars.append(q.bar)
        bars = [x for x in bars if a <= x["t"] < b]
        bars.sort(key=lambda x: -x["t"] if descending else x["t"])
        yield bars[:limit] if limit else bars

    def listings(self, exchange: str) -> list[dict]:
        """Every listing in the last LISTING_SESSIONS sessions' files, under its stored name
        (PETR4.SA), named as B3 names it (PETROBRAS PN N2), typed (listing_type)."""
        if exchange.strip().upper() != MIC:
            raise NotImplementedError(f"b3 lists B3's listings only ({MIC})")
        return [{"ticker": listing_ticker(q.code, MIC), "name": q.name, "exchange": MIC, "type": listing_type(q),
                 "figi": None, "kind": "stock"} for q in sorted(self._recent().values(), key=lambda q: q.code)]

    def instrument(self, ticker: str, resolve_exchange: bool = True) -> Instrument:
        """A listing's record from the files: its name and ISIN as the latest file naming it has them
        (the last sessions', then the yearly files in the folder), its exchange B3, its currency; no
        listing date (the files do not say: the library's rule for a listing abroad applies)."""
        code = b3_code(ticker)
        if code is None:
            raise NotEntitled(0, f"{ticker} is not a B3 listing (PETR4.SA)")
        q = self._recent().get(code)
        if q is None:
            for year in range(self._today().year, self.first_day().year - 1, -1):
                index = self._index_here(year)
                q = index.listing(code) if index is not None else None
                if q is not None:
                    break
        if q is None:
            raise SourceError(404, f"B3: {code} is in none of the last {LISTING_SESSIONS} sessions' files"
                                   + (" nor in the yearly files here" if self.folder is not None else ""))
        kind = listing_type(q)
        return instrument_class(kind)(ticker=listing_ticker(code, MIC), name=q.name, primary_exchange=B3,
                                      currency=q.currency, list_date=None,
                                      details={"code": q.code, "name": q.name, "especi": q.especi, "type": kind,
                                               "isin": q.isin, "bdi": q.bdi, "factor": q.factor,
                                               "date": q.day.isoformat(), "source": "B3 COTAHIST"})


def _date_of(x) -> date:
    if isinstance(x, datetime):
        return (x if x.tzinfo else x.replace(tzinfo=ET)).astimezone(ET).date()
    return x
