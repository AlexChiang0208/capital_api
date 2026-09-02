"""Incremental tick collection with ptr-gap detection and repair.

Why this module exists (official V2.13.58 manual, sections 4-4-3 / 4-4-l / 4-4-28):
  - SKQuoteLib_RequestTicks backfills today's trades ONCE per symbol per
    connection through OnNotifyHistoryTicksLONG, then OnNotifyTicksLONG takes
    over with live trades. Re-subscribing does NOT replay the backfill, so a
    dropped tick cannot be recovered by simply requesting again.
  - There is no "backfill finished" event (only K-line has OnKLineComplete).
    The only available end-of-backfill signal is "no new tick for idle_sec".
  - Every tick carries nPtr, the per-symbol running trade sequence starting at
    0. A hole in the ptr sequence is therefore a genuinely missing trade, and
    SKQuoteLib_GetTickLONG(market_no, index, ptr) can fetch any single one back
    on demand. That is how gaps get repaired without a fresh subscription.
  - The manual forbids calling GetTickLONG inside the notify callbacks, so
    repair runs between pump cycles, never inside an event.
  - nSimulate=1 rows are trial-matching (試撮) quotes, not real trades. They
    consume ptr numbers, so they are tracked for gap detection but dropped from
    the output by default.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .com_client import CapitalClient
from .models import ApiResult, CapitalApiError, QuoteTick
from .parsers import scaled_decimal
from .quotes import QUOTE_PAGE_NO, ensure_quote_session, pump_until, resolve_quote_market_no

if TYPE_CHECKING:
    import pandas as pd


TICK_FRAME_COLUMNS = (
    "ts", "ptr", "symbol", "price", "qty", "bid", "ask", "simulate", "history",
)


# ----------------------------------------------------------------------
# Tick helpers: timestamps, ptr gaps, time-window filtering
# ----------------------------------------------------------------------
def tick_timestamp(tick: QuoteTick) -> datetime | None:
    """Combine nDate + nTimehms + nTimemillismicros into one timestamp.

    Official SKTICK notes: nTimehms is hhmmss, and nTimemillismicros packs
    milliseconds in the leading 3 digits and microseconds in the trailing 3
    (996886 = 996ms 886us). Solace only fills the sub-second field for
    securities, so other products land on whole seconds.
    """
    date_value = int(tick.date)
    if date_value <= 0:
        return None
    year, tail = divmod(date_value, 10000)
    month, day = divmod(tail, 100)
    hour, tail = divmod(int(tick.time_hms), 10000)
    minute, second = divmod(tail, 100)

    millis, micros = divmod(max(0, int(tick.time_millis_micros or 0)), 1000)
    microsecond = millis * 1000 + micros
    if not 0 <= microsecond < 1_000_000:
        microsecond = 0

    try:
        return datetime(year, month, day, hour, minute, second, microsecond)
    except ValueError:
        return None


@dataclass(slots=True)
class TickGap:
    """A missing ptr range, both ends inclusive."""
    start_ptr: int
    end_ptr: int

    @property
    def size(self) -> int:
        return max(0, int(self.end_ptr) - int(self.start_ptr) + 1)

    def ptrs(self) -> range:
        return range(int(self.start_ptr), int(self.end_ptr) + 1)


def find_ptr_gaps(ptrs: Iterable[int]) -> list[TickGap]:
    """Find missing ptr ranges inside the observed [min, max] window.

    ptr is the exchange-side running sequence of a symbol's trades, so any hole
    between two received ptrs is a missing tick. Values above the maximum are
    simply not published yet and are never reported as a gap.
    """
    ordered = sorted({int(value) for value in ptrs})
    return [
        TickGap(previous + 1, current - 1)
        for previous, current in zip(ordered, ordered[1:])
        if current - previous > 1
    ]


def to_hms(value: int | str) -> int:
    """Normalize "09:48", "0948", "094800" or 94800 to the hhmmss int form."""
    if isinstance(value, int):
        return int(value)
    digits = "".join(char for char in str(value) if char.isdigit())
    if not digits:
        raise ValueError(f"Cannot read a hhmmss time from {value!r}")
    return int(digits.ljust(6, "0")[:6])


def filter_ticks_by_time(
    ticks: Sequence[QuoteTick],
    *,
    start: int | str | None = None,
    end: int | str | None = None,
    date: int | None = None,
    drop_simulate: bool = True,
) -> list[QuoteTick]:
    """Slice ticks by trade time; `start` is inclusive and `end` exclusive.

    Times follow nTimehms (hhmmss), so 09:00-09:48 is start="090000",
    end="094800". Pass date=YYYYMMDD to pin a single session, which matters for
    futures where one T+1 session spans two calendar dates.
    """
    start_hms = to_hms(start) if start is not None else None
    end_hms = to_hms(end) if end is not None else None

    out = []
    for tick in ticks:
        if drop_simulate and tick.simulate != 0:
            continue
        if date is not None and int(tick.date) != int(date):
            continue
        hms = int(tick.time_hms)
        if start_hms is not None and hms < start_hms:
            continue
        if end_hms is not None and hms >= end_hms:
            continue
        out.append(tick)
    return out


def ticks_to_dataframe(ticks: Sequence[QuoteTick], *, sort: bool = True) -> "pd.DataFrame":
    """Build a tick DataFrame: ts, ptr, symbol, price, qty, bid, ask, simulate, history.

    Requires pandas, which the SDK does not depend on; import errors surface to
    the caller.
    """
    import pandas as pd

    rows = sorted(ticks, key=lambda tick: int(tick.ptr)) if sort else list(ticks)
    frame = pd.DataFrame(
        [
            {
                "ts": tick_timestamp(tick),
                "ptr": int(tick.ptr),
                "symbol": tick.symbol,
                "price": _as_float(tick.close),
                "qty": int(tick.qty),
                "bid": _as_float(tick.bid),
                "ask": _as_float(tick.ask),
                "simulate": int(tick.simulate),
                "history": bool(tick.history),
            }
            for tick in rows
        ],
        columns=list(TICK_FRAME_COLUMNS),
    )
    return frame.astype({"ts": "datetime64[ns]"}) if not frame.empty else frame


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _result_message(result: ApiResult) -> str:
    message = f"{result.method} failed: code={result.code}"
    if result.message:
        message += f" message={result.message}"
    return message


# ----------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------
@dataclass(slots=True)
class TickBatch:
    """Ticks that arrived during one collect() window, ordered by ptr."""
    symbol: str
    ticks: list[QuoteTick] = field(default_factory=list)
    is_first: bool = False
    repaired: list[QuoteTick] = field(default_factory=list)
    gaps_open: list[TickGap] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.ticks)

    @property
    def missing_count(self) -> int:
        return sum(gap.size for gap in self.gaps_open)


class TickStream:
    """Subscribe one symbol and read its ticks incrementally, batch by batch.

    The first batch carries today's backfill (every trade since the session
    open); later batches carry only what arrived since the previous call. After
    each batch the ptr sequence is checked for holes and any missing tick is
    re-read through SKQuoteLib_GetTickLONG.

    Typical use:
        with TickStream(client, "2330") as stream:
            first = stream.collect(60.0, idle_stop=True)   # today's backfill
            while True:
                batch = stream.collect(5.0)                # live updates
    """

    def __init__(
        self,
        client: CapitalClient,
        symbol: str,
        *,
        market: str | int | None = None,
        page_no: int = 0,
        drop_simulate: bool = True,
        repair_gaps: bool = True,
        max_repair_per_batch: int = 1000,
        max_repair_attempts: int = 3,
        keepalive_sec: float = 15.0,
        pump_interval_sec: float = 0.1,
    ) -> None:
        self._client = client
        self._symbol = str(symbol).strip()
        if not self._symbol:
            raise ValueError("symbol cannot be empty")
        # Only intraday odd-lot (5/6) and custom futures/options (9/10) resolve
        # to a market number; everything else uses the plain subscription calls.
        self._market = market
        self._market_no_arg = resolve_quote_market_no(market)
        self._page_no = int(page_no)
        self._drop_simulate = bool(drop_simulate)
        self._repair_gaps = bool(repair_gaps)
        self._max_repair_per_batch = int(max_repair_per_batch)
        self._max_repair_attempts = int(max_repair_attempts)
        self._keepalive_sec = float(keepalive_sec)
        self._pump_interval_sec = float(pump_interval_sec)

        self._seen: dict[int, QuoteTick] = {}
        self._repair_attempts: dict[int, int] = {}
        self._errors: list[str] = []
        self._cursor = 0
        self._started = False
        self._first_batch_done = False
        self._market_no: int | None = None
        self._stock_index: int | None = None
        self._decimal_places = 2
        self._next_keepalive = 0.0
        self._ready_count = 0
        self._absorbed: list[QuoteTick] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(
        self,
        *,
        auto_login: bool = True,
        auto_connect: bool = True,
        login_wait_sec: float = 0.5,
        connect_wait_sec: float = 5.0,
        resolve_wait_sec: float = 3.0,
    ) -> None:
        if self._started:
            return
        client = self._client
        ensure_quote_session(
            client,
            auto_login=auto_login,
            auto_connect=auto_connect,
            login_wait_sec=login_wait_sec,
            connect_wait_sec=connect_wait_sec,
        )

        # RequestStocks runs first: tick events are keyed by market_no+index and
        # the snapshot is what maps those back to a symbol. It also carries
        # sDecimal, which the tick events themselves do not.
        result = client.subscribe_quotes([self._symbol], page_no=QUOTE_PAGE_NO, market_no=self._market_no_arg)
        if not result.ok:
            self._errors.append(_result_message(result))
        if not pump_until(client, timeout_sec=resolve_wait_sec, ready=self._resolve_identity):
            try:
                client.get_quote_snapshot(self._symbol, market_no=self._market_no_arg, wait_sec=0.0)
            except Exception as exc:
                self._errors.append(f"get_quote_snapshot failed for {self._symbol}: {exc}")
            self._resolve_identity()

        # Absorb whatever is already cached before subscribing: SKCOM backfills
        # only once per symbol per connection, so a second stream in the same
        # process has to reuse the first one's backfill.
        self._absorb_cached()

        result = client.subscribe_ticks(self._symbol, page_no=self._page_no, market_no=self._market_no_arg)
        if not result.ok:
            self._errors.append(_result_message(result))
        self._started = True
        self._ready_count = client.quote_ready_count()
        self._next_keepalive = time.monotonic() + self._keepalive_sec

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            result = self._client.cancel_ticks(self._symbol)
            if not result.ok:
                self._errors.append(_result_message(result))
        except Exception as exc:
            self._errors.append(f"SKQuoteLib_CancelRequestTicks failed for {self._symbol}: {exc}")

    def __enter__(self) -> "TickStream":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------
    def collect(self, seconds: float, *, idle_stop: bool = False, idle_sec: float = 0.5) -> TickBatch:
        """Pump for `seconds` and return the ticks that arrived in that window.

        idle_stop=True returns early once no new tick has arrived for idle_sec.
        Use it for the first batch: SKCOM publishes no "backfill complete"
        event, so going idle is the only end-of-backfill signal available.
        """
        if not self._started:
            raise CapitalApiError("TickStream.collect() requires start() first.")

        deadline = time.monotonic() + max(0.0, float(seconds))
        fresh: list[QuoteTick] = []
        last_change = time.monotonic()

        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            self._keepalive(now)
            self._client.pump(min(self._pump_interval_sec, max(0.01, deadline - now)))
            self._resubscribe_if_reconnected()
            new_rows = self._drain()
            if new_rows:
                fresh.extend(new_rows)
                last_change = time.monotonic()
            elif idle_stop and fresh and (time.monotonic() - last_change) >= idle_sec:
                break

        fresh.extend(self._drain())

        batch = TickBatch(symbol=self._symbol, is_first=not self._first_batch_done)
        batch.repaired = self._repair(batch)
        rows = fresh + batch.repaired
        if batch.is_first and self._absorbed:
            # A previous consumer in this process already used up the one-shot
            # backfill; its absorbed rows belong to the first batch.
            rows = self._absorbed + rows
            self._absorbed = []
        if self._drop_simulate:
            rows = [row for row in rows if row.simulate == 0]
        batch.ticks = sorted(rows, key=lambda row: int(row.ptr))
        self._first_batch_done = True
        return batch

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_identity(self) -> bool:
        """Learn market_no / stock_index / decimal places from the snapshot."""
        snapshot = self._client.hub.get_latest_quote(self._symbol)
        if snapshot is None or snapshot.market_no is None or snapshot.stock_index is None:
            return False
        self._market_no = int(snapshot.market_no)
        self._stock_index = int(snapshot.stock_index)
        self._decimal_places = int(snapshot.decimal_places or 2)
        return True

    def _absorb_cached(self) -> None:
        """Absorb ticks already in the hub (an earlier consumer's backfill).

        SKCOM backfills once per symbol per connection, so these rows are the
        only copy; they are queued for the FIRST batch so collect() still
        delivers the full day.
        """
        rows, self._cursor = self._client.hub.get_ticks_since(
            0, market_no=self._market_no, stock_index=self._stock_index
        )
        if self._market_no is None or self._stock_index is None:
            rows = [row for row in rows if row.symbol == self._symbol]
        self._absorbed = [stored for row in rows if (stored := self._store(row)) is not None]

    def _drain(self) -> list[QuoteTick]:
        rows, self._cursor = self._client.hub.get_ticks_since(
            self._cursor, market_no=self._market_no, stock_index=self._stock_index
        )
        if self._market_no is None or self._stock_index is None:
            rows = [row for row in rows if row.symbol == self._symbol]
        return [stored for row in rows if (stored := self._store(row)) is not None]

    def _store(self, tick: QuoteTick, *, rescale: bool = True) -> QuoteTick | None:
        """Record a tick under its ptr; returns None when the ptr is a duplicate."""
        ptr = int(tick.ptr)
        if ptr in self._seen:
            return None
        self._seen[ptr] = self._normalize(tick) if rescale else tick
        return self._seen[ptr]

    def _normalize(self, tick: QuoteTick) -> QuoteTick:
        """Fill in the symbol and re-apply the product's real decimal places.

        Tick events carry raw integer prices and the event sink scales them with
        the default of 2, which is right for TWSE stocks but wrong for products
        whose sDecimal differs. raw holds the untouched event arguments, so the
        correct scaling can be applied here without a second COM call.
        """
        symbol = tick.symbol or self._symbol
        if self._decimal_places == 2 or len(tick.raw) < 9:
            return tick if symbol == tick.symbol else replace(tick, symbol=symbol)
        return replace(
            tick,
            symbol=symbol,
            bid=scaled_decimal(tick.raw[6], self._decimal_places),
            ask=scaled_decimal(tick.raw[7], self._decimal_places),
            close=scaled_decimal(tick.raw[8], self._decimal_places),
        )

    def _repair(self, batch: TickBatch) -> list[QuoteTick]:
        """Re-read missing ptrs through GetTickLONG, outside the COM callbacks."""
        gaps = find_ptr_gaps(self._seen)
        if not gaps:
            return []
        if not self._repair_gaps:
            batch.gaps_open = gaps
            return []
        if self._market_no is None or self._stock_index is None:
            batch.gaps_open = gaps
            batch.errors.append(
                f"{self._symbol}: {sum(gap.size for gap in gaps)} tick(s) missing, but "
                "market_no/stock_index is unknown so GetTickLONG repair is unavailable."
            )
            return []

        repaired: list[QuoteTick] = []
        budget = self._max_repair_per_batch
        for gap in gaps:
            for ptr in gap.ptrs():
                if budget <= 0:
                    break
                attempts = self._repair_attempts.get(ptr, 0)
                if attempts >= self._max_repair_attempts:
                    continue
                self._repair_attempts[ptr] = attempts + 1
                budget -= 1
                try:
                    row = self._client.get_tick_by_index(
                        self._market_no,
                        self._stock_index,
                        ptr=ptr,
                        decimal_places=self._decimal_places,
                        symbol=self._symbol,
                        cache=False,
                    )
                except Exception as exc:
                    batch.errors.append(f"SKQuoteLib_GetTickLONG(ptr={ptr}) raised: {exc}")
                    continue
                # A ptr the server has not published yet answers with another
                # row (usually ptr 0), so only an exact match counts as repaired.
                if row is None or int(row.ptr) != ptr:
                    continue
                stored = self._store(row, rescale=False)
                if stored is not None:
                    repaired.append(stored)

        batch.gaps_open = find_ptr_gaps(self._seen)
        return repaired

    def _resubscribe_if_reconnected(self) -> None:
        """Re-issue subscriptions after a quote-session reconnect.

        A new STOCKS_READY(3003) event means the component reconnected and the
        server dropped every subscription. Re-subscribing also replays the tick
        backfill on the new connection; ptr-dedup in _store absorbs duplicates.
        """
        current = self._client.quote_ready_count()
        if current <= self._ready_count:
            return
        self._ready_count = current
        result = self._client.subscribe_quotes(
            [self._symbol], page_no=QUOTE_PAGE_NO, market_no=self._market_no_arg
        )
        if not result.ok:
            self._errors.append(_result_message(result))
        result = self._client.subscribe_ticks(
            self._symbol, page_no=self._page_no, market_no=self._market_no_arg
        )
        if not result.ok:
            self._errors.append(_result_message(result))

    def _keepalive(self, now: float) -> None:
        """Official 4-4-5: ping the quote server every 15s or a firewall may cut it."""
        if self._keepalive_sec <= 0 or now < self._next_keepalive:
            return
        self._next_keepalive = now + self._keepalive_sec
        try:
            result = self._client.request_server_time()
            if not result.ok:
                self._errors.append(_result_message(result))
        except Exception as exc:
            self._errors.append(f"SKQuoteLib_RequestServerTime raised: {exc}")

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------
    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def ticks(self) -> list[QuoteTick]:
        """Every tick seen so far, ordered by ptr (trial-matching rows included)."""
        return [self._seen[ptr] for ptr in sorted(self._seen)]

    @property
    def gaps(self) -> list[TickGap]:
        return find_ptr_gaps(self._seen)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def identity(self) -> tuple[int | None, int | None]:
        """(market_no, stock_index) once the snapshot resolved them."""
        return self._market_no, self._stock_index


def stream_tick_batches(
    client: CapitalClient,
    symbol: str,
    *,
    interval_sec: float = 5.0,
    seconds: float | None = None,
    backfill_timeout_sec: float = 60.0,
    backfill_idle_sec: float = 0.8,
    market: str | int | None = None,
    drop_simulate: bool = True,
    repair_gaps: bool = True,
    **stream_kwargs: Any,
) -> Iterator[TickBatch]:
    """Yield one TickBatch per interval; the first carries today's backfill.

    seconds=None streams until the caller stops iterating. The tick
    subscription is cancelled when the generator finishes or is closed.
    """
    stream = TickStream(
        client,
        symbol,
        market=market,
        drop_simulate=drop_simulate,
        repair_gaps=repair_gaps,
        **stream_kwargs,
    )
    stream.start()
    try:
        yield stream.collect(backfill_timeout_sec, idle_stop=True, idle_sec=backfill_idle_sec)
        deadline = None if seconds is None else time.monotonic() + float(seconds)
        while deadline is None or time.monotonic() < deadline:
            window = interval_sec if deadline is None else min(interval_sec, deadline - time.monotonic())
            if window <= 0:
                break
            yield stream.collect(window)
    finally:
        stream.stop()
