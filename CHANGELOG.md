# Changelog

## 0.2.0 (2026-11-03)

Renamed `plugin-integrator-b3` (the package `plugin_integrator_b3`; it was `liminal-plugin-b3`) on 2026-11-03: the naming by system (plugins are `plugin-<app>-<name>`), on liminal-libs 1.4.0 (`liminal_libs`, the library renamed with it).

The plugin is a class on plugin API 2.0 (the class contract, liminal-libs 1.3.0; the user: "all the plugins under
liminal should basically follow inheritance/oop paradigm"): `PLUGIN` is an instance of `B3Plugin`, a `FeedPlugin`,
what it declares its class attributes (its exchange, its database, `B3_HISTORY_YEARS` and its choices ...) and what
it does its methods (`view`, `build`, `rate`, `markets`). It declares its publication delay itself (`delays`: `b3`,
end of day; the library's row stays). Every fact, limit and string is as in 1.0.1. Its version is the release's,
0.2.0, as every Liminal plugin's on `release/0.2.0`; requires liminal-libs 1.3.0.

## 1.0.1 (2026-11-03)

`B3_HISTORY_YEARS` has its choices (2, 1, 3, 5, 10 years) in the record, so the app's Plugins view offers it.

## 1.0.0 (2026-11-02)

The first release (the user: "update existing plugins, add exchanges where applicable and symbols, and if new
sources, create a new plugin"): the feed `b3`, the daily bars of every listing on B3 (Brazil, MIC BVMF, stored as
`PETR4.SA`) from the exchange's public COTAHIST files, no key. The recent days come from the daily files
(`COTAHIST_D<DDMMYYYY>.ZIP`: one request a session serves every listing), the history from the yearly files
(`COTAHIST_A<YYYY>.ZIP`, 60 to 90 MB each, kept under `sources/b3/` in the data folder with a SQLite index of
their cash-market quotes beside each), `B3_HISTORY_YEARS` of them (default 2, the current year counted; the
history floor follows it, never before 1995-01-02). Kept: the cash market's standard lot (CODBDI 02, and 05 to
09 and 11: companies under sanction or recovery keep their bars), the real estate and agribusiness funds (12,
13) and the ETFs (14); prices with two implied decimals divided by the quotation factor, the day's shares as the
volume, the average price as the vwap, the trades. The listings and a listing's record (name, ISIN, no listing
date) come from the last sessions' files. A missing daily file is a holiday unless B3's calendar (the library's)
says there was a session, or the day is the latest weekday: then it is asked again next run. A file cut short,
a page instead of a zip, or a certificate chain that does not verify is an error (verification stays on).
Plugin API 1.12 (`Provider.exchanges`, `Provider.databases`); its market `b3`, kind `exchange`, plan `b3`
(10 requests a minute, 100 a run, 400 a day).
