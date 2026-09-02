"""Capital Securities / Futures SKCOM.dll Python SDK.

Module map:
  models.py           dataclass models, enums, exceptions
  parsers.py          SKCOM raw strings / COM structs -> models
  com_client.py       CapitalConfig, COM event sinks, EventHub, CapitalClient
  quotes.py           high-level quote queries (symbol lists, K-line, realtime, streaming)
  tick_stream.py      incremental tick batches, ptr-gap detection/repair, DataFrame
  quote_workflows.py  interactive probe / preview helpers
  snapshot.py         read-only account snapshot + batch tools
  public_data.py      public Capital website endpoints (delayed data)
  doctor.py           environment preflight (DLL / COM registration / comtypes cache)
"""
from .com_client import CapitalClient, CapitalConfig, EventHub
from .models import (
    # enums
    Authority,
    Side,
    TradeType,
    StockPrime,
    StockPeriod,
    StockFlag,
    StockPriceType,
    FuturesDayTrade,
    FuturesNewClose,
    FuturesReserved,
    FutureRightsCoinType,
    MarketType,
    OrderMarket,
    # order price special codes
    STOCK_PRICE_REFERENCE,
    STOCK_PRICE_LIMIT_UP,
    STOCK_PRICE_LIMIT_DOWN,
    FUTURES_PRICE_MARKET,
    FUTURES_PRICE_RANGE_MARKET,
    # report code tables
    ORDER_STATUS_NAMES,
    QUERY_SESSION_NAMES,
    # exceptions
    CapitalApiError,
    CapitalApiNotLoaded,
    CapitalApiLiveOrderDisabled,
    CapitalApiCallError,
    # models
    ApiResult,
    Account,
    OrderEvent,
    QueryOrderReport,
    QueryFillReport,
    StockPosition,
    FuturePosition,
    FutureRights,
    CapitalPayBalance,
    QuoteConnectionEvent,
    StockListItem,
    QuoteSnapshot,
    QuoteTick,
    QuoteBest5,
    QuoteStreamEvent,
    KLineRecord,
    RealtimeQuoteResult,
    QuoteDataResult,
    QuoteSymbolListResult,
    PublicStockQuote,
    PublicStockHistoryBar,
    PublicMarketInfo,
)
from .public_data import (
    fetch_public_market_info,
    fetch_public_stock_history,
    fetch_public_stock_profile,
    fetch_public_stock_quote,
)
from .quotes import (
    MARKET_LISTED,
    MARKET_OTC,
    MARKET_FUTURES,
    MARKET_OPTIONS,
    MARKET_EMERGING,
    MARKET_INTRADAY_ODD_LOT_LISTED,
    MARKET_INTRADAY_ODD_LOT_OTC,
    MARKET_CUSTOM_FUTURES,
    MARKET_CUSTOM_OPTIONS,
    compact_quote_stream_event,
    ensure_quote_session,
    fetch_future_symbols,
    fetch_kline,
    fetch_latest_quotes,
    fetch_live_quote,
    fetch_order_book,
    fetch_quote_data,
    fetch_quote_history,
    fetch_quote_snapshot,
    fetch_quote_symbol_lists,
    fetch_quote_ticks,
    fetch_realtime_quotes,
    fetch_stock_symbols,
    fetch_tradable_symbols,
    resolve_kline_line_type,
    resolve_quote_market_no,
    stream_realtime_quote_events,
)
from .tick_stream import (
    TICK_FRAME_COLUMNS,
    TickBatch,
    TickGap,
    TickStream,
    filter_ticks_by_time,
    find_ptr_gaps,
    stream_tick_batches,
    tick_timestamp,
    ticks_to_dataframe,
    to_hms,
)
from .quote_workflows import (
    QuoteProbeCase,
    QuoteProbeSummary,
    fetch_quote_symbol_catalog,
    quote_data_preview,
    quote_probe_summary_preview,
    quote_symbol_catalog_preview,
    quote_symbol_list_preview,
    realtime_quote_preview,
    run_quote_probe_cases,
    select_quote_symbol,
)
from .snapshot import (
    fetch_account_snapshot,
    fetch_accounts,
    fetch_balance,
    fetch_stock_positions,
    fetch_future_positions,
    fetch_future_rights,
    fetch_open_orders,
    fetch_order_reports,
    fetch_fulfill_reports,
    is_real_row,
    SNAPSHOT_SECTIONS,
    STOCK_MARKET_TYPES,
    FUTURE_MARKET_TYPES,
)

__all__ = [
    "CapitalClient", "CapitalConfig", "EventHub",
    "Authority", "Side", "TradeType", "StockPrime", "StockPeriod", "StockFlag", "StockPriceType",
    "FuturesDayTrade", "FuturesNewClose", "FuturesReserved", "FutureRightsCoinType", "MarketType", "OrderMarket",
    "STOCK_PRICE_REFERENCE", "STOCK_PRICE_LIMIT_UP", "STOCK_PRICE_LIMIT_DOWN",
    "FUTURES_PRICE_MARKET", "FUTURES_PRICE_RANGE_MARKET",
    "ORDER_STATUS_NAMES", "QUERY_SESSION_NAMES",
    "CapitalApiError", "CapitalApiNotLoaded", "CapitalApiLiveOrderDisabled", "CapitalApiCallError",
    "ApiResult", "Account", "OrderEvent", "QueryOrderReport", "QueryFillReport",
    "StockPosition", "FuturePosition", "FutureRights", "CapitalPayBalance",
    "QuoteConnectionEvent", "StockListItem", "QuoteSnapshot", "QuoteTick", "QuoteBest5", "KLineRecord",
    "QuoteStreamEvent", "RealtimeQuoteResult", "QuoteDataResult", "QuoteSymbolListResult",
    "PublicStockQuote", "PublicStockHistoryBar", "PublicMarketInfo",
    "QuoteProbeCase", "QuoteProbeSummary",
    "MARKET_LISTED", "MARKET_OTC", "MARKET_FUTURES", "MARKET_OPTIONS", "MARKET_EMERGING",
    "MARKET_INTRADAY_ODD_LOT_LISTED", "MARKET_INTRADAY_ODD_LOT_OTC", "MARKET_CUSTOM_FUTURES", "MARKET_CUSTOM_OPTIONS",
    "fetch_public_market_info", "fetch_public_stock_history", "fetch_public_stock_profile", "fetch_public_stock_quote",
    "ensure_quote_session", "fetch_kline", "fetch_live_quote", "fetch_order_book",
    "fetch_quote_data", "fetch_quote_history", "fetch_quote_symbol_lists", "fetch_quote_snapshot", "fetch_quote_ticks",
    "fetch_future_symbols", "fetch_latest_quotes", "fetch_realtime_quotes", "stream_realtime_quote_events", "compact_quote_stream_event",
    "fetch_stock_symbols", "fetch_tradable_symbols",
    "resolve_kline_line_type", "resolve_quote_market_no",
    "TickStream", "TickBatch", "TickGap", "TICK_FRAME_COLUMNS", "stream_tick_batches",
    "ticks_to_dataframe", "filter_ticks_by_time", "find_ptr_gaps", "tick_timestamp", "to_hms",
    "fetch_quote_symbol_catalog", "quote_data_preview", "quote_probe_summary_preview",
    "quote_symbol_catalog_preview", "quote_symbol_list_preview", "realtime_quote_preview",
    "run_quote_probe_cases", "select_quote_symbol",
    "fetch_account_snapshot", "fetch_accounts", "fetch_balance",
    "fetch_stock_positions", "fetch_future_positions", "fetch_future_rights", "fetch_open_orders",
    "fetch_order_reports", "fetch_fulfill_reports",
    "is_real_row", "SNAPSHOT_SECTIONS", "STOCK_MARKET_TYPES", "FUTURE_MARKET_TYPES",
]
