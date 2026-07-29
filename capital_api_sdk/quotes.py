"""High-level quote queries on top of CapitalClient.

Sections:
  markets    SKQuote market numbers and alias resolution
  common     shared plumbing (session, data kinds, dates)
  symbols    tradable symbol lists
  history    historical K-line queries
  realtime   subscribe-then-read helpers and streaming
  facade     fetch_quote_data one-stop helper and per-kind wrappers

Subscription model (official V2.13.58 rules, verified live):
  - SKQuoteLib_RequestStocks uses page 1 (fixed for regular users, max 100
    symbols); one connection holds ONE quote page, re-subscribing replaces it.
  - SKQuoteLib_RequestTicks uses one page per symbol starting from page 0; the
    first request backfills today's ticks via OnNotifyHistoryTicksLONG.
  - GetStockByNoLONG only returns realtime values AFTER RequestStocks; spread
    symbols such as TX08/09 or CDF08/09 work through the same path.
  - RequestStocksWithMarketNo / RequestTicksWithMarketNo only support intraday
    odd-lot (5/6) and custom futures/options (9/10) markets.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from .com_client import CapitalClient
from .models import (
    KLineRecord,
    QuoteBest5,
    QuoteDataResult,
    QuoteSnapshot,
    QuoteStreamEvent,
    QuoteSymbolListResult,
    QuoteTick,
    RealtimeQuoteResult,
    StockListItem,
)


# ----------------------------------------------------------------------
# Markets: SKQuote market numbers and alias resolution
# ----------------------------------------------------------------------
MARKET_LISTED = 0
MARKET_OTC = 1
MARKET_FUTURES = 2
MARKET_OPTIONS = 3
MARKET_EMERGING = 4
MARKET_INTRADAY_ODD_LOT_LISTED = 5
MARKET_INTRADAY_ODD_LOT_OTC = 6
MARKET_CUSTOM_FUTURES = 9
MARKET_CUSTOM_OPTIONS = 10

# Official rule: only these markets use the WithMarketNo subscription calls.
_WITH_MARKET_NO_MARKETS = {
    MARKET_INTRADAY_ODD_LOT_LISTED,
    MARKET_INTRADAY_ODD_LOT_OTC,
    MARKET_CUSTOM_FUTURES,
    MARKET_CUSTOM_OPTIONS,
}

# Aliases that mean "regular subscription without market number".
_REGULAR_MARKET_ALIASES = {
    "", "stock", "stocks", "future", "futures", "option", "options", "regular",
    "listed", "otc", "twse", "tpex", "emerging",
}

_MARKET_NO_ALIASES = {
    "listed": MARKET_LISTED,
    "stock-listed": MARKET_LISTED,
    "twse": MARKET_LISTED,
    "otc": MARKET_OTC,
    "stock-otc": MARKET_OTC,
    "tpex": MARKET_OTC,
    "futures-market": MARKET_FUTURES,
    "future-market": MARKET_FUTURES,
    "options-market": MARKET_OPTIONS,
    "option-market": MARKET_OPTIONS,
    "emerging": MARKET_EMERGING,
    "oddlot-listed": MARKET_INTRADAY_ODD_LOT_LISTED,
    "odd-lot-listed": MARKET_INTRADAY_ODD_LOT_LISTED,
    "oddlot-otc": MARKET_INTRADAY_ODD_LOT_OTC,
    "odd-lot-otc": MARKET_INTRADAY_ODD_LOT_OTC,
    "custom-future": MARKET_CUSTOM_FUTURES,
    "custom-futures": MARKET_CUSTOM_FUTURES,
    "custom-option": MARKET_CUSTOM_OPTIONS,
    "custom-options": MARKET_CUSTOM_OPTIONS,
}

_SYMBOL_LIST_MARKET_ALIASES = {
    **_MARKET_NO_ALIASES,
    "futures": MARKET_FUTURES,
    "future": MARKET_FUTURES,
    "options": MARKET_OPTIONS,
    "option": MARKET_OPTIONS,
    # TAIFEX spread symbols (TX08/09, CDF08/09, ...) live in the regular futures
    # market (2); market 9 only lists special custom products.
    "future-spread": MARKET_FUTURES,
    "futures-spread": MARKET_FUTURES,
    "spread-future": MARKET_FUTURES,
}

_SYMBOL_LIST_GROUPS = {
    "stock": ["listed", "otc"],
    "stocks": ["listed", "otc"],
    "spot": ["listed", "otc"],
    "cash": ["listed", "otc"],
    "future": ["future-market"],
    "futures": ["future-market"],
    "option": ["option-market"],
    "options": ["option-market"],
    "tradable": ["listed", "otc", "future-market"],
    "all": [
        "listed", "otc", "emerging",
        "oddlot-listed", "oddlot-otc",
        "future-market", "option-market",
        "custom-future", "custom-option",
    ],
}

MARKET_NO_LABELS = {
    MARKET_LISTED: "listed",
    MARKET_OTC: "otc",
    MARKET_FUTURES: "future-market",
    MARKET_OPTIONS: "option-market",
    MARKET_EMERGING: "emerging",
    MARKET_INTRADAY_ODD_LOT_LISTED: "oddlot-listed",
    MARKET_INTRADAY_ODD_LOT_OTC: "oddlot-otc",
    MARKET_CUSTOM_FUTURES: "custom-future",
    MARKET_CUSTOM_OPTIONS: "custom-option",
}


def _normalize_alias(market: str) -> str:
    return str(market).strip().lower().replace("_", "-")


def resolve_quote_market_no(market: str | int | None = None) -> int | None:
    """
    Return the market number for subscriptions that need the WithMarketNo calls.

    Only intraday odd-lot (5/6) and custom futures/options (9/10) markets use
    RequestStocksWithMarketNo / RequestTicksWithMarketNo. Every other market —
    listed, OTC, futures, options, including spread symbols — subscribes without
    a market number, so this returns None for them.
    """
    if market is None:
        return None
    if isinstance(market, int):
        return int(market) if int(market) in _WITH_MARKET_NO_MARKETS else None
    text = _normalize_alias(market)
    if text in _MARKET_NO_ALIASES:
        market_no = _MARKET_NO_ALIASES[text]
        return market_no if market_no in _WITH_MARKET_NO_MARKETS else None
    if text in _REGULAR_MARKET_ALIASES:
        return None
    choices = sorted(_REGULAR_MARKET_ALIASES | set(_MARKET_NO_ALIASES) - {""})
    raise ValueError(f"Unknown quote market: {market!r}. Available aliases: {choices}")


def resolve_symbol_list_markets(markets: str | int | Iterable[str | int] | None) -> list[int]:
    """Expand symbol-list market aliases / groups / ints into unique market numbers."""
    if markets is None:
        raw_items: list[str | int] = ["tradable"]
    elif isinstance(markets, (str, int)):
        raw_items = str(markets).split(",") if isinstance(markets, str) else [markets]
    else:
        raw_items = list(markets)

    out: list[int] = []
    for raw in raw_items:
        if isinstance(raw, int):
            market_numbers = [int(raw)]
        else:
            item = _normalize_alias(raw)
            if not item:
                continue
            if item.isdigit():
                market_numbers = [int(item)]
            elif item in _SYMBOL_LIST_GROUPS:
                market_numbers = [_SYMBOL_LIST_MARKET_ALIASES[name] for name in _SYMBOL_LIST_GROUPS[item]]
            elif item in _SYMBOL_LIST_MARKET_ALIASES:
                market_numbers = [_SYMBOL_LIST_MARKET_ALIASES[item]]
            else:
                choices = sorted(set(_SYMBOL_LIST_MARKET_ALIASES) | set(_SYMBOL_LIST_GROUPS))
                raise ValueError(f"Unknown symbol-list market: {raw!r}. Available aliases: {choices}")

        for market_no in market_numbers:
            if market_no not in out:
                out.append(market_no)

    if not out:
        raise ValueError("markets cannot be empty")
    return out


# ----------------------------------------------------------------------
# Common: session, data kinds, dates
# ----------------------------------------------------------------------
DateLike = date | datetime | str
DataKinds = str | Iterable[str]

LIVE_DATA_KINDS = ("snapshot", "ticks", "orderbook")

_DATA_KIND_ALIASES = {
    "quote": "snapshot", "quotes": "snapshot", "snapshot": "snapshot",
    "snapshots": "snapshot", "realtime": "snapshot",
    "tick": "ticks", "ticks": "ticks", "trade": "ticks", "trades": "ticks",
    "deal": "ticks", "deals": "ticks",
    "best5": "orderbook", "best-5": "orderbook", "book": "orderbook",
    "orderbook": "orderbook", "order-book": "orderbook",
    "kline": "kline", "k-line": "kline", "bar": "kline", "bars": "kline",
    "history": "kline", "historical": "kline",
}

# RequestStocks page is fixed to 1 for regular users (official manual 4-4-2).
QUOTE_PAGE_NO = 1
# RequestStocks silently truncates to the first 100 symbols.
MAX_QUOTE_SYMBOLS = 100


def ensure_quote_session(
    client: CapitalClient,
    *,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> None:
    """Login SKCenter (quote-only) and connect the SKQuote monitor when needed."""
    if auto_login:
        result = client.login_center(wait_sec=login_wait_sec)
        if not client.is_login_result_ok(result):
            client.hub.add_quote_error(
                f"{result.method} failed: code={result.code} message={result.message}"
            )
    if not auto_connect:
        return
    if not client.is_quote_ready():
        result = client.connect_quote(wait_sec=connect_wait_sec)
        if not result.ok and not _is_quote_connected(client):
            client.hub.add_quote_error(
                f"{result.method} failed: code={result.code} message={result.message}"
            )
    if not _is_quote_connected(client):
        client.hub.add_quote_error("SKQuoteLib quote monitor is not connected after EnterMonitor.")


def _is_quote_connected(client: CapitalClient) -> bool:
    try:
        return client.is_quote_connected() == 1
    except Exception:
        return False


def normalize_symbols(symbols: str | Iterable[str]) -> list[str]:
    raw_symbols = symbols.split(",") if isinstance(symbols, str) else symbols
    out = [str(symbol).strip() for symbol in raw_symbols if str(symbol).strip()]
    if not out:
        raise ValueError("symbols cannot be empty")
    return out


def normalize_data_kinds(data: DataKinds) -> list[str]:
    raw_items = data.strip().lower().split(",") if isinstance(data, str) else [str(item) for item in data]

    out: list[str] = []
    for raw in raw_items:
        item = raw.strip().lower().replace("_", "-")
        if not item:
            continue
        if item == "all":
            expanded = ["snapshot", "ticks", "orderbook", "kline"]
        elif item == "live":
            expanded = ["snapshot", "ticks", "orderbook"]
        elif item in _DATA_KIND_ALIASES:
            expanded = [_DATA_KIND_ALIASES[item]]
        else:
            raise ValueError(
                f"Unknown quote data kind: {raw!r}. "
                "Use snapshot, ticks/trades, orderbook/best5, kline/history, live, or all."
            )
        for kind in expanded:
            if kind not in out:
                out.append(kind)

    if not out:
        raise ValueError("data cannot be empty")
    return out


def _api_result_message(result) -> str:
    message = f"{result.method} failed: code={result.code}"
    if result.message:
        message += f" message={result.message}"
    if result.broker_message:
        message += f" broker_message={result.broker_message}"
    return message


def pump_until(client: CapitalClient, *, timeout_sec: float, ready: Callable[[], bool]) -> bool:
    """Pump COM messages until ready() or timeout; returns the final ready state."""
    end_time = time.monotonic() + max(0.0, float(timeout_sec))
    while True:
        if ready():
            return True
        if time.monotonic() >= end_time:
            return ready()
        client.pump(0.05)


def resolve_kline_dates(*, start_date: DateLike | None, end_date: DateLike | None, days: int) -> tuple[str, str]:
    end_obj = _to_date(end_date) if end_date is not None else date.today()
    start_obj = _to_date(start_date) if start_date is not None else end_obj - timedelta(days=int(days))
    if end_date is None:
        end_obj = _previous_weekday(end_obj)
    if start_date is None:
        start_obj = _next_weekday(start_obj)
    if start_obj > end_obj:
        raise ValueError("start_date cannot be after end_date")
    return start_obj.strftime("%Y%m%d"), end_obj.strftime("%Y%m%d")


def _next_weekday(value: date) -> date:
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _previous_weekday(value: date) -> date:
    while value.weekday() >= 5:
        value -= timedelta(days=1)
    return value


def _to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {value!r}. Use YYYYMMDD or YYYY-MM-DD.")


# ----------------------------------------------------------------------
# Symbols: tradable symbol lists
# ----------------------------------------------------------------------
def fetch_quote_symbol_lists(
    client: CapitalClient,
    markets: str | int | Iterable[str | int] | None = "tradable",
    *,
    wait_sec: float = 3.0,
    retries: int = 1,
    clear: bool = True,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> QuoteSymbolListResult:
    """
    Fetch tradable symbol lists via SKQuoteLib_RequestStockList.

    markets accepts aliases (listed, otc, future-market, ...), groups (stock,
    future, tradable, all), raw market numbers, or a mix. Futures spread symbols
    (TX08/09, CDF08/09, ...) are part of the regular futures market list.
    """
    ensure_quote_session(
        client,
        auto_login=auto_login,
        auto_connect=auto_connect,
        login_wait_sec=login_wait_sec,
        connect_wait_sec=connect_wait_sec,
    )
    rows: dict[str, list[StockListItem]] = {}
    for market_no in resolve_symbol_list_markets(markets):
        label = MARKET_NO_LABELS.get(market_no, str(market_no))
        items: list[StockListItem] = []
        for attempt in range(max(1, int(retries) + 1)):
            items = clean_symbol_list_items(
                client.request_stock_list(market_no, wait_sec=wait_sec, clear=clear or attempt > 0)
            )
            if items:
                break
        rows[label] = items

    return QuoteSymbolListResult(markets=rows, quote_errors=list(client.hub.quote_errors))


def fetch_tradable_symbols(client: CapitalClient, **kwargs) -> QuoteSymbolListResult:
    """Fetch listed/OTC spot stocks plus futures (incl. spreads) symbol lists."""
    return fetch_quote_symbol_lists(client, "tradable", **kwargs)


def fetch_stock_symbols(client: CapitalClient, **kwargs) -> QuoteSymbolListResult:
    """Fetch listed and OTC spot stock symbol lists."""
    return fetch_quote_symbol_lists(client, "stock", **kwargs)


def fetch_future_symbols(client: CapitalClient, **kwargs) -> QuoteSymbolListResult:
    """Fetch the futures market symbol list, including spread symbols like TX08/09."""
    return fetch_quote_symbol_lists(client, "future", **kwargs)


def clean_symbol_list_items(items: Iterable[StockListItem]) -> list[StockListItem]:
    """Drop SKCOM terminator rows (##, %...) and duplicated symbols."""
    out: list[StockListItem] = []
    seen: set[str] = set()
    for item in items:
        symbol = item.symbol.strip()
        if not symbol or symbol == "##" or symbol.startswith("%"):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append(item)
    return out


# ----------------------------------------------------------------------
# History: K-line queries
# ----------------------------------------------------------------------
_KLINE_LINE_TYPE_ALIASES = {
    "minute": 0, "min": 0, "m": 0, "1m": 0,
    "day": 4, "daily": 4, "d": 4,
    "week": 5, "weekly": 5, "w": 5,
    "month": 6, "monthly": 6, "mo": 6,
}


def resolve_kline_line_type(line_type: str | int = "day") -> int:
    if isinstance(line_type, int):
        return int(line_type)
    text = str(line_type).strip().lower().replace("_", "-")
    if text.isdigit():
        return int(text)
    if text in _KLINE_LINE_TYPE_ALIASES:
        return _KLINE_LINE_TYPE_ALIASES[text]
    raise ValueError("Unknown K-line type. Use minute/min/0, day/daily/4, week/5, or month/6.")


def fetch_quote_history(
    client: CapitalClient,
    symbol: str,
    *,
    start_date: DateLike | None = None,
    end_date: DateLike | None = None,
    days: int = 30,
    line_type: str | int = "day",
    out_type: int = 1,
    trade_session: int = 0,
    minute_number: int = 1,
    wait_sec: float = 10.0,
    idle_sec: float = 1.0,
    retries: int = 1,
    clear: bool = True,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> list[KLineRecord]:
    """
    Fetch historical K-line records via SKQuoteLib_RequestKLineAMByDate.

    Dates accept YYYYMMDD, YYYY-MM-DD, date, or datetime; without start_date the
    range is end_date minus days. trade_session 0 = full session (futures night
    included), 1 = AM session only. The wait stops early once rows stop arriving
    for idle_sec, so short queries return quickly.

    Known server limitation: futures SPREAD symbols (e.g. CDF08/09) accept the
    request but return 0 rows — use TAIFEX spread trade files for spread history.
    """
    ensure_quote_session(
        client,
        auto_login=auto_login,
        auto_connect=auto_connect,
        login_wait_sec=login_wait_sec,
        connect_wait_sec=connect_wait_sec,
    )
    start_text, end_text = resolve_kline_dates(start_date=start_date, end_date=end_date, days=days)
    line_type_value = resolve_kline_line_type(line_type)
    symbol_text = str(symbol)

    rows: list[KLineRecord] = []
    for attempt in range(max(1, int(retries) + 1)):
        client.request_kline(
            symbol_text,
            start_date=start_text,
            end_date=end_text,
            line_type=line_type_value,
            out_type=out_type,
            trade_session=trade_session,
            minute_number=minute_number,
            wait_sec=0.0,
            clear=clear or attempt > 0,
        )
        rows = _pump_kline_until_idle(client, symbol_text, timeout_sec=wait_sec, idle_sec=idle_sec)
        if rows:
            break
    return rows


def _pump_kline_until_idle(
    client: CapitalClient,
    symbol: str,
    *,
    timeout_sec: float,
    idle_sec: float,
) -> list[KLineRecord]:
    """Pump until K-line rows stop growing for idle_sec (or timeout)."""
    end_time = time.monotonic() + max(0.0, float(timeout_sec))
    last_count = 0
    last_change = time.monotonic()
    while time.monotonic() < end_time:
        client.pump(0.1)
        count = len(client.hub.get_kline_records(symbol))
        now = time.monotonic()
        if count != last_count:
            last_count, last_change = count, now
        elif count > 0 and now - last_change >= idle_sec:
            break
    return client.hub.get_kline_records(symbol)


def fetch_kline(client: CapitalClient, symbol: str, **kwargs) -> list[KLineRecord]:
    """Fetch historical K-line records. Alias of fetch_quote_history."""
    return fetch_quote_history(client, symbol, **kwargs)


# ----------------------------------------------------------------------
# Realtime: subscribe-then-read helpers and streaming
# ----------------------------------------------------------------------
@dataclass(slots=True)
class _RealtimeSession:
    symbols: list[str]
    kinds: list[str]
    market: str | int | None
    market_no: int | None
    tick_symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_realtime_session(
    client: CapitalClient,
    symbols: str | Iterable[str],
    *,
    market: str | int | None,
    data: DataKinds,
    helper_name: str,
    clear: bool,
    auto_login: bool,
    auto_connect: bool,
    login_wait_sec: float,
    connect_wait_sec: float,
) -> _RealtimeSession:
    """Shared prologue: normalize inputs, ensure session, subscribe quotes/ticks."""
    symbol_list = normalize_symbols(symbols)
    kinds = normalize_data_kinds(data)
    unsupported = [kind for kind in kinds if kind not in LIVE_DATA_KINDS]
    if unsupported:
        raise ValueError(f"{helper_name} does not support {unsupported}; use fetch_quote_data for kline.")

    ensure_quote_session(
        client,
        auto_login=auto_login,
        auto_connect=auto_connect,
        login_wait_sec=login_wait_sec,
        connect_wait_sec=connect_wait_sec,
    )
    if clear:
        client.hub.clear_quote_data()

    session = _RealtimeSession(
        symbols=symbol_list,
        kinds=kinds,
        market=market,
        market_no=resolve_quote_market_no(market),
    )
    if len(symbol_list) > MAX_QUOTE_SYMBOLS:
        session.errors.append(
            f"RequestStocks handles at most {MAX_QUOTE_SYMBOLS} symbols; extra symbols are ignored by SKCOM."
        )

    # Quote subscription always runs: it feeds snapshot values and lets the hub
    # map tick/best5 events (keyed by market_no+index) back to symbols.
    result = client.subscribe_quotes(symbol_list, page_no=QUOTE_PAGE_NO, market_no=session.market_no)
    if not result.ok:
        session.errors.append(_api_result_message(result))

    if "ticks" in session.kinds or "orderbook" in session.kinds:
        for page_no, symbol in enumerate(symbol_list):
            result = client.subscribe_ticks(symbol, page_no=page_no, market_no=session.market_no)
            if result.ok:
                session.tick_symbols.append(symbol)
            else:
                session.errors.append(_api_result_message(result))
    return session


def _finish_realtime_session(client: CapitalClient, session: _RealtimeSession) -> None:
    for symbol in session.tick_symbols:
        try:
            result = client.cancel_ticks(symbol)
            if not result.ok:
                session.errors.append(_api_result_message(result))
        except Exception as exc:
            session.errors.append(f"SKQuoteLib_CancelRequestTicks failed for {symbol}: {exc}")


def _read_realtime_result(
    client: CapitalClient,
    session: _RealtimeSession,
    *,
    max_ticks: int | None,
    include_history: bool = True,
) -> RealtimeQuoteResult:
    """Read snapshots / ticks / order books for the session symbols from the hub."""
    snapshots: dict[str, QuoteSnapshot | None] = {}
    ticks: dict[str, list[QuoteTick]] = {}
    order_books: dict[str, QuoteBest5 | None] = {}

    for symbol in session.symbols:
        snapshot = _read_snapshot(client, session, symbol)
        if "snapshot" in session.kinds:
            snapshots[symbol] = snapshot
        if "ticks" in session.kinds:
            rows = client.hub.get_ticks(symbol=symbol)
            if not include_history:
                rows = [row for row in rows if not row.history]
            if max_ticks is not None:
                rows = rows[-int(max_ticks):]
            ticks[symbol] = rows
        if "orderbook" in session.kinds:
            order_books[symbol] = _read_order_book(client, symbol, snapshot)

    return RealtimeQuoteResult(
        market=str(session.market or ""),
        symbols=session.symbols,
        snapshots=snapshots,
        ticks=ticks,
        order_books=order_books,
        quote_errors=list(dict.fromkeys(session.errors + list(client.hub.quote_errors))),
    )


def _read_snapshot(client: CapitalClient, session: _RealtimeSession, symbol: str) -> QuoteSnapshot | None:
    """Prefer the event cache; fall back to the direct COM getter."""
    cached = client.hub.get_latest_quote(symbol)
    if cached is not None and cached.has_data:
        return cached
    try:
        fetched = client.get_quote_snapshot(symbol, market_no=session.market_no, wait_sec=0.0)
    except Exception as exc:
        session.errors.append(f"get_quote_snapshot failed for {symbol}: {exc}")
        return cached
    if fetched.has_data or cached is None:
        return fetched
    return cached


def _read_order_book(client: CapitalClient, symbol: str, snapshot: QuoteSnapshot | None) -> QuoteBest5 | None:
    rows = client.hub.get_latest_best5(symbol=symbol)
    if rows:
        return rows[-1]
    if snapshot is not None and snapshot.market_no is not None and snapshot.stock_index is not None:
        book = client.hub.get_latest_best5_by_key(int(snapshot.market_no), int(snapshot.stock_index))
        if book is not None:
            return book
        return client.get_best5_by_index(
            int(snapshot.market_no),
            int(snapshot.stock_index),
            decimal_places=snapshot.decimal_places,
            symbol=symbol,
        )
    return None


def _realtime_data_ready(client: CapitalClient, session: _RealtimeSession) -> bool:
    """True when every requested data kind has a cached row per symbol.

    Reads the hub cache only (no COM getters) so it is cheap and side-effect
    free while pump_until polls it.
    """
    for symbol in session.symbols:
        quote = client.hub.get_latest_quote(symbol)
        if "snapshot" in session.kinds and (quote is None or not quote.has_data):
            return False
        if "ticks" in session.kinds and not client.hub.get_ticks(symbol=symbol, max_count=1):
            return False
        if "orderbook" in session.kinds:
            book = client.hub.get_latest_best5(symbol=symbol)
            if not book and quote is not None and quote.market_no is not None and quote.stock_index is not None:
                book = client.hub.get_latest_best5_by_key(int(quote.market_no), int(quote.stock_index))
            if not book:
                return False
    return True


def fetch_latest_quotes(
    client: CapitalClient,
    symbols: str | Iterable[str],
    *,
    market: str | int | None = None,
    data: DataKinds = ("snapshot", "ticks", "orderbook"),
    timeout_sec: float = 5.0,
    max_ticks: int | None = 1,
    clear: bool = False,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> RealtimeQuoteResult:
    """
    One-shot "latest state" query: subscribe, wait until every requested kind has
    data (or timeout), read, then cancel tick subscriptions.

    Thanks to the SKCOM tick backfill this also works outside trading hours:
    ticks return today's (or the last session's) trades, and snapshots return the
    last known values. max_ticks=1 keeps only the newest tick per symbol; set
    None to keep the whole backfill.

    clear defaults to False because SKCOM only backfills ticks once per symbol
    per connection — clearing the cache would make repeated one-shot queries in
    the same session lose their tick data outside trading hours.
    """
    session = _start_realtime_session(
        client, symbols,
        market=market, data=data, helper_name="fetch_latest_quotes",
        clear=clear, auto_login=auto_login, auto_connect=auto_connect,
        login_wait_sec=login_wait_sec, connect_wait_sec=connect_wait_sec,
    )
    start = time.monotonic()
    pump_until(client, timeout_sec=timeout_sec, ready=lambda: _realtime_data_ready(client, session))
    if "ticks" in session.kinds:
        # The tick backfill streams in chronological order, so "at least one
        # tick" can be satisfied mid-backfill. Drain until events go idle so the
        # newest tick really is the last trade (e.g. the closing auction).
        remaining = max(0.5, timeout_sec - (time.monotonic() - start))
        _drain_quote_events(client, timeout_sec=remaining)
    result = _read_realtime_result(client, session, max_ticks=max_ticks)
    _finish_realtime_session(client, session)
    return result


def _drain_quote_events(client: CapitalClient, *, timeout_sec: float, idle_sec: float = 0.3) -> None:
    """Pump until no new tick/quote/best5 events arrive for idle_sec (or timeout)."""
    end_time = time.monotonic() + max(0.0, float(timeout_sec))
    hub = client.hub

    def counts() -> tuple[int, int, int]:
        return len(hub.tick_events), len(hub.quote_events), len(hub.best5_events)

    last = counts()
    last_change = time.monotonic()
    while time.monotonic() < end_time:
        client.pump(0.1)
        now = time.monotonic()
        current = counts()
        if current != last:
            last, last_change = current, now
        elif now - last_change >= idle_sec:
            return


def fetch_realtime_quotes(
    client: CapitalClient,
    symbols: str | Iterable[str],
    *,
    market: str | int | None = None,
    data: DataKinds = ("snapshot", "ticks", "orderbook"),
    seconds: float = 3.0,
    max_ticks: int | None = None,
    include_history: bool = False,
    clear: bool = True,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> RealtimeQuoteResult:
    """
    Collect realtime data for a fixed window: subscribe, pump for `seconds`,
    read everything received, then cancel tick subscriptions.

    Use fetch_latest_quotes when you only need the current state; use this when
    you want every tick/quote update within the window. include_history=False
    (default) drops the one-time backfill of today's earlier ticks so the result
    only contains ticks from the collection window.
    """
    session = _start_realtime_session(
        client, symbols,
        market=market, data=data, helper_name="fetch_realtime_quotes",
        clear=clear, auto_login=auto_login, auto_connect=auto_connect,
        login_wait_sec=login_wait_sec, connect_wait_sec=connect_wait_sec,
    )
    if seconds > 0:
        client.pump(float(seconds))
    result = _read_realtime_result(client, session, max_ticks=max_ticks, include_history=include_history)
    _finish_realtime_session(client, session)
    return result


def stream_realtime_quote_events(
    client: CapitalClient,
    symbols: str | Iterable[str],
    *,
    market: str | int | None = None,
    data: DataKinds = ("snapshot", "ticks", "orderbook"),
    seconds: float | None = 60.0,
    pump_interval_sec: float = 0.2,
    include_history: bool = True,
    clear: bool = True,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> Iterable[QuoteStreamEvent]:
    """
    Subscribe and yield every new quote/tick/order-book event as it arrives.

    seconds=None streams until the caller stops iterating. Tick subscriptions
    are cancelled when the generator finishes or is closed. The first tick
    subscription per symbol also backfills today's earlier trades; pass
    include_history=False to yield live events only.
    """
    session = _start_realtime_session(
        client, symbols,
        market=market, data=data, helper_name="stream_realtime_quote_events",
        clear=clear, auto_login=auto_login, auto_connect=auto_connect,
        login_wait_sec=login_wait_sec, connect_wait_sec=connect_wait_sec,
    )
    for error in session.errors:
        if error not in client.hub.quote_errors:
            client.hub.add_quote_error(error)

    accepted = set(session.symbols)
    key_symbols: dict[tuple[int, int], str] = {}
    offsets = {"quote": 0, "tick": 0, "best5": 0}
    end_time = None if seconds is None else time.monotonic() + float(seconds)

    try:
        while end_time is None or time.monotonic() < end_time:
            client.pump(float(pump_interval_sec))
            yield from _new_stream_events(
                client, session.kinds, offsets, accepted, key_symbols,
                include_history=include_history,
            )
    finally:
        _finish_realtime_session(client, session)


def _new_stream_events(
    client: CapitalClient,
    kinds: list[str],
    offsets: dict[str, int],
    accepted: set[str],
    key_symbols: dict[tuple[int, int], str],
    *,
    include_history: bool = True,
) -> Iterable[QuoteStreamEvent]:
    # Quote events always flow (they feed the key->symbol map); only yield the
    # kinds the caller asked for.
    quote_events = client.hub.quote_events[offsets["quote"]:]
    offsets["quote"] += len(quote_events)
    for quote in quote_events:
        if quote.symbol not in accepted:
            continue
        if quote.market_no is not None and quote.stock_index is not None:
            key_symbols[(int(quote.market_no), int(quote.stock_index))] = quote.symbol
        if "snapshot" in kinds:
            yield QuoteStreamEvent(kind="quote", symbol=quote.symbol, data=quote)

    if "ticks" in kinds:
        tick_events = client.hub.tick_events[offsets["tick"]:]
        offsets["tick"] += len(tick_events)
        for tick in tick_events:
            if tick.history and not include_history:
                continue
            symbol = _stream_symbol(tick.symbol, tick.market_no, tick.stock_index, accepted, key_symbols)
            if symbol is None:
                continue
            if not tick.symbol:
                tick.symbol = symbol
            yield QuoteStreamEvent(kind="tick", symbol=symbol, data=tick)

    if "orderbook" in kinds:
        best5_events = client.hub.best5_events[offsets["best5"]:]
        offsets["best5"] += len(best5_events)
        for best5 in best5_events:
            symbol = _stream_symbol(best5.symbol, best5.market_no, best5.stock_index, accepted, key_symbols)
            if symbol is None:
                continue
            if not best5.symbol:
                best5.symbol = symbol
            yield QuoteStreamEvent(kind="orderbook", symbol=symbol, data=best5)


def _stream_symbol(
    symbol: str,
    market_no: int,
    stock_index: int,
    accepted: set[str],
    key_symbols: dict[tuple[int, int], str],
) -> str | None:
    if symbol in accepted:
        return symbol
    mapped = key_symbols.get((int(market_no), int(stock_index)))
    return mapped if mapped in accepted else None


def compact_quote_stream_event(event: QuoteStreamEvent) -> dict[str, object]:
    """Return a small printable dict for a QuoteStreamEvent."""
    row = asdict(event.data)
    row["symbol"] = row.get("symbol") or event.symbol
    if event.kind == "quote":
        keep = ["symbol", "name", "market_no", "stock_index", "close", "bid", "bid_qty", "ask", "ask_qty", "total_qty"]
    elif event.kind == "tick":
        keep = ["symbol", "market_no", "stock_index", "date", "time_hms", "close", "bid", "ask", "qty", "simulate", "history"]
    elif event.kind == "orderbook":
        keep = ["symbol", "market_no", "stock_index", "bid_prices", "bid_qtys", "ask_prices", "ask_qtys", "simulate"]
    else:
        return row
    return {key: row.get(key) for key in keep}


# ----------------------------------------------------------------------
# Facade: one-stop single-symbol helper
# ----------------------------------------------------------------------
def fetch_quote_data(
    client: CapitalClient,
    symbol: str,
    *,
    market: str | int | None = None,
    data: DataKinds = "snapshot",
    start_date: DateLike | None = None,
    end_date: DateLike | None = None,
    days: int = 30,
    line_type: str | int = "day",
    out_type: int = 1,
    trade_session: int = 0,
    minute_number: int = 1,
    kline_wait_sec: float = 10.0,
    kline_retries: int = 1,
    seconds: float = 5.0,
    max_ticks: int | None = None,
    clear: bool = False,
    auto_login: bool = True,
    auto_connect: bool = True,
    login_wait_sec: float = 0.5,
    connect_wait_sec: float = 5.0,
) -> QuoteDataResult:
    """
    One-stop quote helper for a single stock/future symbol.

    data can be snapshot, ticks/trades, orderbook/best5, kline/history, live, or
    all. Live kinds run the one-shot fetch_latest_quotes path with `seconds` as
    the timeout; kline runs fetch_quote_history. clear only affects the realtime
    cache — K-line records are always cleared per symbol before a new query.
    """
    kinds = normalize_data_kinds(data)
    symbol_text = str(symbol).strip()
    if not symbol_text:
        raise ValueError("symbol cannot be empty")

    result = QuoteDataResult(symbol=symbol_text, market=str(market or ""))
    live_kinds = [kind for kind in kinds if kind in LIVE_DATA_KINDS]
    if live_kinds:
        live = fetch_latest_quotes(
            client,
            [symbol_text],
            market=market,
            data=live_kinds,
            timeout_sec=seconds,
            max_ticks=max_ticks,
            clear=clear,
            auto_login=auto_login,
            auto_connect=auto_connect,
            login_wait_sec=login_wait_sec,
            connect_wait_sec=connect_wait_sec,
        )
        result.snapshot = live.snapshots.get(symbol_text)
        result.ticks = live.ticks.get(symbol_text, [])
        result.order_book = live.order_books.get(symbol_text)
        result.quote_errors.extend(live.quote_errors)

    if "kline" in kinds:
        result.kline_start_date, result.kline_end_date = resolve_kline_dates(
            start_date=start_date, end_date=end_date, days=days,
        )
        result.kline = fetch_quote_history(
            client,
            symbol_text,
            start_date=start_date,
            end_date=end_date,
            days=days,
            line_type=line_type,
            out_type=out_type,
            trade_session=trade_session,
            minute_number=minute_number,
            wait_sec=kline_wait_sec,
            retries=kline_retries,
            auto_login=auto_login and not live_kinds,
            auto_connect=auto_connect,
            login_wait_sec=login_wait_sec,
            connect_wait_sec=connect_wait_sec,
        )
        result.quote_errors = list(dict.fromkeys(result.quote_errors + list(client.hub.quote_errors)))

    return result


def fetch_quote_snapshot(
    client: CapitalClient,
    symbol: str,
    *,
    market: str | int | None = None,
    seconds: float = 3.0,
    **kwargs,
) -> QuoteSnapshot | None:
    """Fetch one stock/future quote snapshot with a one-shot query."""
    return fetch_quote_data(client, symbol, market=market, data="snapshot", seconds=seconds, **kwargs).snapshot


def fetch_quote_ticks(
    client: CapitalClient,
    symbol: str,
    *,
    market: str | int | None = None,
    seconds: float = 5.0,
    max_ticks: int | None = None,
    **kwargs,
) -> list[QuoteTick]:
    """Fetch executed tick/trade records (today's backfill + live) with a one-shot query."""
    return fetch_quote_data(client, symbol, market=market, data="ticks", seconds=seconds, max_ticks=max_ticks, **kwargs).ticks


def fetch_order_book(
    client: CapitalClient,
    symbol: str,
    *,
    market: str | int | None = None,
    seconds: float = 5.0,
    **kwargs,
) -> QuoteBest5 | None:
    """Fetch the latest best5/order-book snapshot with a one-shot query."""
    return fetch_quote_data(client, symbol, market=market, data="orderbook", seconds=seconds, **kwargs).order_book


def fetch_live_quote(
    client: CapitalClient,
    symbol: str,
    *,
    market: str | int | None = None,
    seconds: float = 5.0,
    max_ticks: int | None = None,
    **kwargs,
) -> QuoteDataResult:
    """Fetch snapshot, ticks, and order book with a one-shot query."""
    return fetch_quote_data(client, symbol, market=market, data="live", seconds=seconds, max_ticks=max_ticks, **kwargs)
