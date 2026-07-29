from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal
from typing import Any

from .com_client import CapitalClient
from .models import QuoteDataResult, QuoteSymbolListResult, RealtimeQuoteResult, StockListItem
from .quotes import DataKinds, DateLike, fetch_quote_data, fetch_quote_symbol_lists


@dataclass(frozen=True, slots=True)
class QuoteProbeCase:
    label: str
    symbol: str
    market: str | int | None = "stock"
    data: DataKinds = "snapshot"
    start_date: DateLike | None = None
    end_date: DateLike | None = None
    days: int = 30
    line_type: str | int = "day"
    minute_number: int = 1
    seconds: float = 3.0
    max_ticks: int | None = 20


@dataclass(slots=True)
class QuoteProbeSummary:
    label: str
    market: str
    symbol: str
    data: str
    ok: bool
    has_snapshot: bool = False
    tick_count: int = 0
    has_order_book: bool = False
    kline_count: int = 0
    error: str = ""
    quote_errors: list[str] = field(default_factory=list)


def fetch_quote_symbol_catalog(
    client: CapitalClient,
    groups: Iterable[str | int] = ("stock", "future", "option"),
    **kwargs: Any,
) -> dict[str, QuoteSymbolListResult]:
    """Fetch several quote symbol-list groups in one call."""
    return {str(group): fetch_quote_symbol_lists(client, group, **kwargs) for group in groups}


def select_quote_symbol(
    symbol_lists: QuoteSymbolListResult,
    *,
    preferred: Iterable[str] = (),
    contains: str | None = None,
    startswith: str | None = None,
    fallback_to_first: bool = True,
) -> StockListItem | None:
    """
    Pick a usable symbol from a symbol-list result.

    preferred is checked first. contains/startswith are useful for futures spread
    symbols such as CDF08/09, which should be refreshed after contract rolls.
    """
    items = _flatten_symbol_lists(symbol_lists)
    preferred_symbols = [str(symbol).strip().upper() for symbol in preferred if str(symbol).strip()]
    for preferred_symbol in preferred_symbols:
        for item in items:
            if item.symbol.upper() == preferred_symbol:
                return item

    for item in items:
        if contains is not None and contains not in item.symbol:
            continue
        if startswith is not None and not item.symbol.startswith(startswith):
            continue
        return item

    return items[0] if fallback_to_first and items else None


def quote_symbol_list_preview(result: QuoteSymbolListResult, *, limit: int | None = None) -> dict[str, Any]:
    """Return a notebook/interactive display dict. Pass limit=None to show all rows."""
    return {
        "markets": {
            market: [_to_plain_value(item) for item in _limited(rows, limit)]
            for market, rows in result.markets.items()
        },
        "counts": {market: len(rows) for market, rows in result.markets.items()},
        "quote_errors": result.quote_errors[-10:],
    }


def quote_symbol_catalog_preview(
    catalog: Mapping[str, QuoteSymbolListResult],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return previews for several quote symbol-list groups. Pass limit=None to show all rows."""
    return {group: quote_symbol_list_preview(result, limit=limit) for group, result in catalog.items()}


def quote_data_preview(result: QuoteDataResult, *, limit: int | None = None) -> dict[str, Any]:
    """Return a dict preview for a quote-data result. Pass limit=None to show all rows."""
    preview: dict[str, Any] = {
        "market": result.market,
        "symbol": result.symbol,
        "snapshot": _to_plain_value(result.snapshot) if result.snapshot else None,
        "ticks": [_to_plain_value(tick) for tick in _limited(result.ticks, limit)],
        "order_book": _to_plain_value(result.order_book) if result.order_book else None,
        "kline_range": (
            f"{result.kline_start_date}-{result.kline_end_date}"
            if result.kline_start_date or result.kline_end_date
            else ""
        ),
        "kline": [_to_plain_value(row) for row in _limited(result.kline, limit)],
        "counts": {
            "ticks": len(result.ticks),
            "kline": len(result.kline),
        },
        "quote_errors": result.quote_errors[-10:],
    }
    return preview


def realtime_quote_preview(result: RealtimeQuoteResult, *, limit: int | None = None) -> dict[str, Any]:
    """Return a dict preview for a multi-symbol real-time quote result. Pass limit=None to show all rows."""
    return {
        "market": result.market,
        "symbols": result.symbols,
        "snapshots": {
            symbol: _to_plain_value(snapshot)
            for symbol, snapshot in result.snapshots.items()
        },
        "ticks": {
            symbol: [_to_plain_value(tick) for tick in _limited(ticks, limit)]
            for symbol, ticks in result.ticks.items()
        },
        "order_books": {
            symbol: _to_plain_value(order_book)
            for symbol, order_book in result.order_books.items()
        },
        "counts": {
            "ticks": {symbol: len(ticks) for symbol, ticks in result.ticks.items()},
            "snapshots": sum(1 for snapshot in result.snapshots.values() if snapshot is not None),
            "order_books": sum(1 for order_book in result.order_books.values() if order_book is not None),
        },
        "quote_errors": result.quote_errors[-10:],
    }


def run_quote_probe_cases(
    client: CapitalClient,
    cases: Iterable[QuoteProbeCase],
    *,
    kline_wait_sec: float = 10.0,
    kline_retries: int = 1,
    clear: bool = False,
) -> list[QuoteProbeSummary]:
    """Run several quote fetches and return success summaries."""
    summaries: list[QuoteProbeSummary] = []
    for case in cases:
        try:
            result = fetch_quote_data(
                client,
                case.symbol,
                market=case.market,
                data=case.data,
                start_date=case.start_date,
                end_date=case.end_date,
                days=case.days,
                line_type=case.line_type,
                minute_number=case.minute_number,
                kline_wait_sec=kline_wait_sec,
                kline_retries=kline_retries,
                seconds=case.seconds,
                max_ticks=case.max_ticks,
                clear=clear,
            )
        except Exception as exc:
            summaries.append(
                QuoteProbeSummary(
                    label=case.label,
                    market=str(case.market or ""),
                    symbol=case.symbol,
                    data=_data_label(case.data),
                    ok=False,
                    error=str(exc),
                )
            )
            continue

        has_snapshot = result.snapshot is not None
        has_order_book = result.order_book is not None
        tick_count = len(result.ticks)
        kline_count = len(result.kline)
        summaries.append(
            QuoteProbeSummary(
                label=case.label,
                market=str(case.market or ""),
                symbol=case.symbol,
                data=_data_label(case.data),
                ok=has_snapshot or has_order_book or tick_count > 0 or kline_count > 0,
                has_snapshot=has_snapshot,
                tick_count=tick_count,
                has_order_book=has_order_book,
                kline_count=kline_count,
                quote_errors=result.quote_errors[-10:],
            )
        )
    return summaries


def quote_probe_summary_preview(summaries: Iterable[QuoteProbeSummary]) -> list[dict[str, Any]]:
    """Return quote probe summaries as plain dictionaries."""
    return [_to_plain_value(summary) for summary in summaries]


def _flatten_symbol_lists(result: QuoteSymbolListResult) -> list[StockListItem]:
    return [item for rows in result.markets.values() for item in rows if item.symbol]


def _data_label(data: DataKinds) -> str:
    if isinstance(data, str):
        return data
    return ",".join(str(item) for item in data)


def _limited(rows: list[Any], limit: int | None) -> list[Any]:
    if limit is None:
        return rows
    return rows[: max(0, int(limit))]


def _to_plain_value(value: Any) -> Any:
    if is_dataclass(value):
        return _to_plain_value(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _to_plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_to_plain_value(item) for item in value)
    return value
