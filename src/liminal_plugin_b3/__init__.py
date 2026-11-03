"""B3 (b3.com.br) for Liminal: the daily bars of every listing on B3, Brazil's exchange (Sao Paulo,
MIC BVMF), from the exchange's own public COTAHIST files, no key: a daily file of each session for
the recent days, and a yearly file for each of the last B3_HISTORY_YEARS years (default 2, the
current one counted; 60 to 90 MB each, kept under the plugin's folder in the data folder,
sources/b3/, with an index beside each).

A Liminal plugin released on its own (liminal_lib.sources.registry: Provider), registered under the
entry point group `liminal.providers` as `b3`: one feed. It relies on plugin API 1.12 (the exchanges
a feed carries, Provider.exchanges, so a listing PETR4.SA comes to it and to no US feed; the
databases a plugin's own code writes, Provider.databases) and nothing of the app outside it; its
version is this distribution's (pyproject.toml), so the record's is left empty.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from liminal_lib.config import Tier
from liminal_lib.layout import locate
from liminal_lib.sources.http import PublicClient
from liminal_lib.sources.registry import Database, Provider, limiter, machine_caps

from .client import START, B3Session, B3Source, first_day

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


def view(base):
    """The feed's plan: its fixed tier with the history reaching back to January 1 of the earliest
    year kept (B3_HISTORY_YEARS: a floor the collector never asks below, and that moves back when
    the user raises the years), never before B3's first session served (1995-01-02); its caps."""
    tier = replace(TIER, history_start=first_day(history_years(base), date.today()))
    return replace(base, tier=tier, max_requests_per_run=PER_RUN, max_requests_per_day=PER_DAY, origin=base)


def build(settings, request_budget):
    """The feed's source: the public client on B3's file host (its window and day cap held for the
    machine: the server's limits are per address; a certificate chain that fails is an error at
    once, B3Session), the plugin's folder in the data folder for the yearly files (none for a
    database in memory: the daily files alone then), and the years kept."""
    client = PublicClient(BASE_URL, rate_limiter=limiter(NAME, settings), request_budget=request_budget,
                          session=B3Session())
    folder = locate(FILES.where, settings.db_path)
    return B3Source(machine_caps(client, NAME, settings), folder=folder, years=history_years(settings))


PLUGIN = Provider(
    api="1.12",  # the contract it needs (Provider.exchanges, Provider.databases); its version: the distribution's
    name=NAME, title="B3", build=build, assets=frozenset({"stock"}), order=110,
    about="daily bars of every B3 listing (Brazil) from the exchange's public COTAHIST files, the last "
          "B3_HISTORY_YEARS years (no key)",
    kind="exchange", archival=False, tier=TIER, per_run=PER_RUN, per_day=PER_DAY,
    limits="none published (a public file server: a session's file about 600 KB, a year's 60 to 90 MB)",
    exchanges=frozenset({"BVMF"}),  # B3's listings, stored as PETR4.SA (the library's notation), and no other
    options=(YEARS_VAR,),
    provides={"stock": ("day",)}, per_request={"day": None},  # a file serves every listing: a year of days, or one day
    bulk_resolution="day", view=view, rate=lambda settings: RATE,
    markets=lambda settings: {"stock": NAME},
    databases=(FILES,),
)
