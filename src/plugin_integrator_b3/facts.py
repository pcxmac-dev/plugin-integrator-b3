"""What the plugin learned of its APIs while it ran (plugin API 2.2: Plugin.api_facts), said without asking anything.

Each client its build makes (a source's HTTP client, the library's) and each stream the recorder opens is noted under
the name of the API it talks to (`NOTES.note`), and held weakly: a run's source, its caches and its connection are never
kept alive for a page. While one lives its words are read from it; when it goes, its last words are kept (a few short
lines an API), so the page still says what the last run learned. What the plugin learned itself (a scan's plan, the
day a table was of) is kept as it said it (`NOTES.keep`). Nothing here sends a request or reads a file.
"""
from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable, Iterable, Mapping

Words = Callable[[Mapping, bool], dict]
LIVE_MAX = 8  # holders kept an API (the newest speaks; the rest are pruned as they go)
HEADERS = (("Date", "Last answer (server's clock)"), ("Last-Modified", "Last file changed"))


def when(t: float) -> str:
    return time.strftime("%b %d %H:%M:%S", time.localtime(t))


def client_words(state: Mapping, live: bool) -> dict[str, str]:
    """A library HTTP client's words, from its attributes: the requests it made, the last answer's clock and its
    file's change, the rate-limit headers its server sent, a host that refused the key."""
    out: dict[str, str] = {}
    n = state.get("requests_made")
    if isinstance(n, int):
        budget = state.get("request_budget")
        out["Requests"] = f"{n:,}" + (" so far" if live else "") + (f" (of {budget:,} allowed)" if isinstance(budget, int) else "")
    headers = {str(k).lower(): str(v) for k, v in dict(state.get("last_headers") or {}).items()}
    for header, label in HEADERS:
        if headers.get(header.lower()):
            out[label] = headers[header.lower()]
    for header, value in dict(state.get("limits_seen") or {}).items():
        out[str(header)] = str(value)
    for host, refused in dict(state.get("_refused") or {}).items():
        out[f"Refused by {host}"] = str(refused)[:200]
    return out


def stream_words(state: Mapping, live: bool) -> dict[str, str]:
    """A live stream's words: the pairs its server confirmed (its `confirmed`)."""
    pairs = sorted(str(p) for p in state.get("confirmed") or ())
    return {"Pairs confirmed": ", ".join(pairs) if pairs else ("none yet" if live else "none")}


class ApiNotes:
    """The plugin's notes on its APIs, by API name (sources.apis.Api.name)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._live: dict[str, list[tuple[weakref.ref, Words, float, str, tuple[str, str]]]] = {}
        self._last: dict[str, dict[str, str]] = {}  # written by a finalizer too: no lock taken there
        self._kept: dict[str, dict[str, str]] = {}

    def note(self, api: str, holder, words: Words = client_words, began: str = "Run began",
             states: tuple[str, str] = ("under way", "ended")) -> None:
        """A client (or stream) made for `api`: held weakly; its words read while it lives, kept when it goes. `began`
        labels when it was made, `states` say it lives and that it went."""
        t = self._clock()
        with self._lock:
            live = [x for x in self._live.get(api, ()) if x[0]() is not None]
            live.append((weakref.ref(holder), words, t, began, states))
            self._live[api] = live[-LIVE_MAX:]
        done = weakref.finalize(holder, self._gone, api, holder.__dict__, words, t, began, states[1])
        done.atexit = False

    def keep(self, api: str, facts: Mapping) -> None:
        """Facts the plugin learned itself, kept as said until said again."""
        with self._lock:
            self._kept.setdefault(api, {}).update({str(k): "" if v is None else str(v) for k, v in facts.items()})

    def clear(self) -> None:
        with self._lock:
            self._live.clear()
            self._kept.clear()
        self._last.clear()

    def _gone(self, api: str, state: Mapping, words: Words, t: float, began: str, ended: str) -> None:
        """A holder gone (its finalizer: any thread, any moment, so no lock): its last words kept."""
        try:
            self._last[api] = {began: f"{when(t)} ({ended})", **words(state, False)}
        except Exception:  # noqa: BLE001 - words are extra: a page is drawn without them
            pass

    def facts(self, apis: Iterable[str]) -> dict[str, dict[str, str]]:
        """{api: {label: words}} for each of `apis` with anything to say: the newest live holder's words, else the last
        gone one's, then what the plugin kept; {} when nothing is known (nothing is asked)."""
        out: dict[str, dict[str, str]] = {}
        for api in apis:
            with self._lock:
                live = [(h, w, t, b, st) for r, w, t, b, st in self._live.get(api, ()) if (h := r()) is not None]
                kept = dict(self._kept.get(api, {}))
            said: dict[str, str] = {}
            if live:
                holder, words, t, began, states = live[-1]
                try:
                    said = {began: f"{when(t)} ({states[0]})", **words(vars(holder), True)}
                except Exception:  # noqa: BLE001
                    said = {}
            else:
                said = dict(self._last.get(api) or {})
            said.update(kept)
            if said:
                out[api] = said
        return out


NOTES = ApiNotes()  # the plugin's, for the process: every instance of it (the registry's copies too) reads these
