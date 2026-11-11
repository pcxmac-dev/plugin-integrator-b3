"""B3 (b3.com.br) for Liminal: the daily bars of every listing on B3, Brazil's exchange (Sao Paulo,
MIC BVMF), from the exchange's own public COTAHIST files, no key: a daily file of each session for
the recent days, and a yearly file for each of the last B3_HISTORY_YEARS years (default 2, the
current one counted; 60 to 90 MB each, kept under the plugin's folder in the data folder,
sources/b3/, with an index beside each).

A Liminal plugin released on its own, registered under the entry point group `liminal.providers`
as `b3`: one feed, `PLUGIN`, an instance of `B3Plugin` on the class contract (plugin API 2.0,
liminal-libs 1.3.0: liminal_libs.sources.plugin.FeedPlugin; what it declares its class attributes,
what it does its methods). It relies on the contract (the exchanges a feed carries, `exchanges`, so
a listing PETR4.SA comes to it and to no US feed; the databases a plugin's own code writes,
`databases`; its publication delay, `delays`) and nothing of the app outside it; its version is
this distribution's (pyproject.toml), so the instance's is left empty.

Plugin API 2.2 (liminal-libs 1.49.0; the user, 2026-11-11: "each plugin should reflect the the API its attached to, and
present links that provide documentation"): `apis`, what it talks to (apis.py: the COTAHIST files with B3's pages that
publish and describe them, their layout, every part of the series and the files it downloads among the rest), and
`api_facts()`, what its client learned while it ran (facts.py), asking nothing.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from liminal_libs.config import Tier
from liminal_libs.freshness import Delay
from liminal_libs.layout import locate
from liminal_libs.sources.http import PublicClient
from liminal_libs.sources.registry import Database, FeedPlugin, limiter, machine_caps

from .apis import APIS
from .apis import NAME as API_NAME
from .client import START, B3Session, B3Source, first_day
from .facts import NOTES

NAME = "b3"
BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"  # the files' folder (checked 2026-11-02; client.py)
# its plan and caps: a public file server with no published limits, one request a file (a session's
# file serves every listing's day, a year's file a year of them)
TIER = Tier(NAME, requests_per_minute=10, history_years=None, history_start=START)
PER_RUN, PER_DAY = 100, 400
RATE = (10, 60.0)
YEARS_VAR = "B3_HISTORY_YEARS"  # the yearly files fetched: the current year's and this many minus one before it
YEARS_DEFAULT = 2
# the yearly files as downloaded, and an index of each one's cash-market quotes beside it (API 1.8)
FILES = Database("sources/b3/", "the yearly COTAHIST files as downloaded (COTAHIST_A<year>.ZIP, B3_HISTORY_YEARS "
                                "of them, 60 to 90 MB each) and an index of each one's cash-market quotes beside "
                                "it (COTAHIST_A<year>.db, SQLite)", engine="files")


def history_years(settings) -> int:
    """B3_HISTORY_YEARS among the settings' plugin variables: how many calendar years of yearly files
    are fetched, the current one counted (default 2; at least 1; a value that is not a number: the
    default)."""
    raw = (settings.env.get(YEARS_VAR) or "").strip()
    try:
        years = int(raw) if raw else YEARS_DEFAULT
    except ValueError:
        years = YEARS_DEFAULT
    return max(1, years)


class B3Plugin(FeedPlugin):
    """The feed as a class (plugin API 2.2): what it declares its class attributes, what it does its
    methods (`view`: its plan with the floor the years kept set; `build`: its source on B3's file host;
    `rate` and `markets`; `api_facts`), the rest the contract's defaults."""

    api = "2.2"  # the contract it needs (the class contract: exchanges, databases, delays; 2.2: apis); its version: the distribution's
    name = NAME
    title = "B3"
    assets = frozenset({"stock"})
    order = 110
    about = ("daily bars of every B3 listing (Brazil) from the exchange's public COTAHIST files, the last "
             "B3_HISTORY_YEARS years (no key)")
    kind = "exchange"
    archival = False
    tier = TIER
    per_run, per_day = PER_RUN, PER_DAY
    limits = "none published (a public file server: a session's file about 600 KB, a year's 60 to 90 MB)"
    exchanges = frozenset({"BVMF"})  # B3's listings, stored as PETR4.SA (the library's notation), and no other
    options = (YEARS_VAR,)
    plan_choices = {YEARS_VAR: ("2", "1", "3", "5", "10")}  # its choices: the Plugins view offers it
    provides = {"stock": ("day",)}
    per_request = {"day": None}  # a file serves every listing: a year of days, or one day
    bulk_resolution = "day"
    databases = (FILES,)
    # when its data is published (the library's freshness.PLAN_DELAYS row for b3, declared here too): a
    # session's file is out the evening of the session, end of day
    delays = {NAME: {"stock": Delay("eod")}}
    # what it attaches to (plugin API 2.2): the COTAHIST files, with B3's pages and their layout (apis.py)
    apis = APIS

    def view(self, base):
        """The feed's plan: its fixed tier with the history reaching back to January 1 of the earliest
        year kept (B3_HISTORY_YEARS: a floor the collector never asks below, and that moves back when
        the user raises the years), never before B3's first session served (1995-01-02); its caps."""
        tier = replace(self.tier, history_start=first_day(history_years(base), date.today()))
        return replace(base, tier=tier, max_requests_per_run=self.per_run, max_requests_per_day=self.per_day, origin=base)

    def rate(self, settings):
        return RATE

    def markets(self, settings):
        return {"stock": self.name}

    def build(self, settings, request_budget):
        """The feed's source: the public client on B3's file host (its window and day cap held for the
        machine: the server's limits are per address; a certificate chain that fails is an error at
        once, B3Session), the plugin's folder in the data folder for the yearly files (none for a
        database in memory: the daily files alone then), and the years kept."""
        client = PublicClient(BASE_URL, rate_limiter=limiter(self.name, settings), request_budget=request_budget,
                              session=B3Session())
        folder = locate(FILES.where, settings.db_path)
        NOTES.note(API_NAME, client)  # held weakly: api_facts reads what it learned
        return B3Source(machine_caps(client, self.name, settings), folder=folder, years=history_years(settings))

    def api_facts(self):
        """What its client learned of B3's files while it ran (the requests made, the last answer's clock, the last
        file's change, the last files read), from what this process already holds: nothing is asked (facts.py)."""
        return NOTES.facts(a.name for a in self.apis)


PLUGIN = B3Plugin()
