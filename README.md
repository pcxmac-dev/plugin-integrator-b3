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

## Its API

It attaches to B3's historical quotation series, the COTAHIST files at `https://bvmf.bmfbovespa.com.br/InstDados/SerHist`
(public, no key); its page in every app shows them (`PLUGIN.apis`), with every part of the series, the files it
downloads among the rest, and what its client learned (`PLUGIN.api_facts()`):

- [Historical quote data](https://www.b3.com.br/en_us/market-data-and-indices/data-services/market-data/historical-data/equities/historical-quote-data/)
  ([Cotações históricas](https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/cotacoes-historicas/)),
  and the [files to download](https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/series-historicas/):
  yearly from 1986, monthly for the last twelve months, daily for the current year;
- the [layout of the quotes file](https://www.b3.com.br/data/files/33/67/B9/50/D84057102C784E47AC094EA8/SeriesHistoricas_Layout.pdf)
  (revision 02, 2020-10-05): the 245-character records, the markets (TPMERC) and the code tables (BDI, ESPECI);
- B3's [terms of use](https://www.b3.com.br/pt_br/termos-de-uso-e-protecao-de-dados/termos-de-uso/): the site's data for
  personal use; the Portuguese page forbids automated collection of what it offers.

## Install

Into the app's environment, from the app's folder:

```powershell
.venv\Scripts\python.exe -m pip install "plugin-integrator-b3 @ git+https://github.com/pcxmac-dev/plugin-integrator-b3@v0.3.0"
```

or with the app's plugins (`pip install -e ".[plugins]"` in liminal-charts). The app loads it on its next
start. No key; `B3_HISTORY_YEARS` is its one setting.

## Compatibility

It is written for plugin API 2.2, the class contract (`PLUGIN`, an instance of `B3Plugin`, a `FeedPlugin`:
the exchanges a feed carries, `exchanges`, the databases its own code writes, `databases`, its
publication delay, `delays`, and what it attaches to, `apis`; liminal-libs 1.49.0 or later). An app offering 2.2 or a
later 2.x loads it; an app of another major, or one older than 2.2, leaves it out and says why on the Plugins view.

## Develop

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "liminal-libs @ git+https://github.com/pcxmac-dev/liminal-libs@main"
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
```

Tests never send requests (a fake session answers with small COTAHIST zips built in the tests) and
never read your keys; no yearly file is downloaded by the build or the tests.
