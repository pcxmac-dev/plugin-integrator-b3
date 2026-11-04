# plugin-integrator-b3

B3 for [Liminal](https://github.com/pcxmac-dev/liminal-charts): the daily bars of every listing on B3,
Brazil's exchange (São Paulo, MIC BVMF), read from the exchange's own public COTAHIST files. No key.

The feed `b3` serves the cash market's standard lot (ON, PN and units, a company's standing aside),
the real estate and agribusiness funds and the ETFs, one bar a session: open, high, low, close, the
day's shares as the volume, the average price as the vwap, the trades. BDRs, rights, bonuses and
odd lots are left. A listing is stored under the library's name for it, `PETR4.SA` (`B3:PETR4` and
`PETR4::BVMF` mean the same); its day bar is stamped at midnight New York of the session's date,
like every stock's. Daily bars only.

How the files are read:

- the recent days from the daily files (`COTAHIST_D<DDMMYYYY>.ZIP`, about 600 KB, out the evening
  of each session): one request serves every listing's day, read once a run;
- the history from the yearly files (`COTAHIST_A<YYYY>.ZIP`, 60 to 90 MB each): one request a year,
  the file kept under the plugin's folder in the data folder (`sources/b3/`) with an index of its
  cash-market quotes beside it (`COTAHIST_A<YYYY>.db`, SQLite), so a listing's days are read at
  once and nothing of the file stays in memory. `B3_HISTORY_YEARS` (default 2: the current year and
  the one before; the Plugins view or the key script set it) says how many years are fetched; the
  feed's history floor is January 1 of the earliest of them, never before 1995-01-02, and it moves
  back when you raise the years. A yearly file is fetched again only when more than twenty weekdays
  past its last session are asked for (the days in between come from the daily files).

A weekday without a daily file is a day without a session, a holiday, unless B3's calendar (the
library's, from exchange_calendars) says there was one or cannot say and the day is the latest
weekday: then the file is not out yet and the day is asked again next run. A file cut short, a
page in place of a zip, or a certificate chain that does not verify is an error, never an empty day;
SSL verification stays on.

The listings (`listings("BVMF")`, the directory's) and a listing's record (its name as B3 writes
it, `PETROBRAS PN N2`, its ISIN, no listing date) come from the last five sessions' files, then
from the yearly files kept here. Its plan: 10 requests a minute, 100 a run, 400 a day (`b3`).

A Liminal provider plugin released on its own. The app finds it through the entry point group
`liminal.providers` and lists it on the Plugins view and in `liminal-charts plugins`
as `package: plugin-integrator-b3`, with this package's version.

## Install

Into the app's environment, from the app's folder:

```powershell
.venv\Scripts\python.exe -m pip install "plugin-integrator-b3 @ git+https://github.com/pcxmac-dev/plugin-integrator-b3@v0.2.0"
```

or with the app's plugins (`pip install -e ".[plugins]"` in liminal-charts). The app loads it on its next
start. No key; `B3_HISTORY_YEARS` is its one setting.

## Compatibility

It is written for plugin API 2.0, the class contract (`PLUGIN`, an instance of `B3Plugin`, a `FeedPlugin`:
the exchanges a feed carries, `exchanges`, the databases its own code writes, `databases`, and its
publication delay, `delays`; liminal-libs 1.3.0 or later). An app offering 2.0 or a later 2.x loads it; an
app of another major leaves it out and says why on the Plugins view.

## Develop

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "liminal-libs @ git+https://github.com/pcxmac-dev/liminal-libs@main"
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
```

Tests never send requests (a fake session answers with small COTAHIST zips built in the tests) and
never read your keys; no yearly file is downloaded by the build or the tests.
