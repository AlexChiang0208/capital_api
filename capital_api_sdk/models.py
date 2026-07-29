from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from decimal import Decimal
from typing import Any, Optional


@dataclass(slots=True)
class Account:
    login_id: str
    account_type: str
    branch: str
    account_no: str
    full_account: str
    raw: str = ""


@dataclass(slots=True)
class ApiResult:
    method: str
    code: int
    message: str = ""
    broker_message: str = ""
    raw: Any = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass(slots=True)
class RawReport:
    source: str
    raw: str
    fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrderEvent:
    """Parsed from SKReplyLib.OnNewData raw comma-separated report."""
    login_id: str
    raw: str
    fields: list[str]
    key_no: str = ""
    market_type: str = ""
    report_type: str = ""
    order_error: str = ""
    broker: str = ""
    customer_no: str = ""
    side: str = ""
    symbol: str = ""
    order_no: str = ""
    price: str = ""
    qty: str = ""
    before_qty: str = ""
    after_qty: str = ""
    date: str = ""
    time: str = ""
    ok_seq: str = ""
    order_seq: str = ""
    error_msg: str = ""
    seq_no: str = ""

    @property
    def is_fill(self) -> bool:
        return self.report_type == "D"

    @property
    def remaining_qty(self) -> Optional[int]:
        try:
            return int(self.after_qty)
        except Exception:
            return None

    @property
    def is_open_like(self) -> bool:
        rem = self.remaining_qty
        return (not self.is_fill) and (rem is None or rem > 0) and not self.error_msg


@dataclass(slots=True)
class CapitalPayBalance:
    has_capital_pay: bool
    balance: Decimal
    withdrawable_amount: Decimal
    today_buying_power: Decimal
    raw: str


@dataclass(slots=True)
class StockPosition:
    symbol: str = ""
    inventory_type: str = ""
    yesterday_inventory: str = ""
    today_buy_qty: str = ""
    today_sell_qty: str = ""
    today_buy_matched: str = ""
    today_sell_matched: str = ""
    sellable_qty: str = ""
    realtime_inventory: str = ""
    account_no: str = ""
    login_id: str = ""
    raw: str = ""


@dataclass(slots=True)
class FuturePosition:
    symbol: str = ""
    buy_sell: str = ""
    open_qty: str = ""
    day_trade_qty: str = ""
    avg_price: str = ""
    market_price: str = ""
    floating_pl: str = ""
    account_no: str = ""
    login_id: str = ""
    raw: str = ""


@dataclass(slots=True)
class FutureRights:
    equity: str = ""
    excess_margin: str = ""
    available_balance: str = ""
    initial_margin: str = ""
    maintenance_margin: str = ""
    order_margin: str = ""
    risk_indicator: str = ""
    currency: str = ""
    account_no: str = ""
    login_id: str = ""
    raw: str = ""


@dataclass(slots=True)
class QuoteConnectionEvent:
    kind: int
    code: int
    raw: tuple[int, int] = (0, 0)


@dataclass(slots=True)
class StockListItem:
    market_no: int
    symbol: str = ""
    name: str = ""
    fields: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass(slots=True)
class QuoteSnapshot:
    market_no: int | None = None
    stock_index: int | None = None
    symbol: str = ""
    name: str = ""
    market_code: str = ""
    type_no: int | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    reference: Decimal | None = None
    bid: Decimal | None = None
    bid_qty: int | None = None
    ask: Decimal | None = None
    ask_qty: int | None = None
    tick_qty: int | None = None
    total_qty: int | None = None
    yesterday_qty: int | None = None
    up_limit: Decimal | None = None
    down_limit: Decimal | None = None
    total_bid_count: int | None = None
    total_ask_count: int | None = None
    decimal_places: int = 2
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        """True when the snapshot carries real quote values, not just basic product info."""
        return any(
            value not in (None, 0)
            for value in (self.open, self.high, self.low, self.close, self.bid, self.ask, self.total_qty)
        )


@dataclass(slots=True)
class QuoteTick:
    market_no: int
    stock_index: int
    ptr: int
    date: int
    time_hms: int
    time_millis_micros: int
    bid: Decimal | None
    ask: Decimal | None
    close: Decimal | None
    qty: int
    simulate: int
    symbol: str = ""
    history: bool = False  # True when delivered by OnNotifyHistoryTicksLONG (today's backfill)
    raw: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class QuoteBest5:
    market_no: int
    stock_index: int
    bid_prices: tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None]
    bid_qtys: tuple[int, int, int, int, int]
    ask_prices: tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None]
    ask_qtys: tuple[int, int, int, int, int]
    extend_bid: Decimal | None = None
    extend_bid_qty: int = 0
    extend_ask: Decimal | None = None
    extend_ask_qty: int = 0
    simulate: int = 0
    symbol: str = ""
    raw: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class QuoteStreamEvent:
    kind: str
    symbol: str
    data: QuoteSnapshot | QuoteTick | QuoteBest5


@dataclass(slots=True)
class KLineRecord:
    symbol: str
    raw: str
    fields: list[str] = field(default_factory=list)
    date: str = ""
    time: str = ""
    open: str = ""
    high: str = ""
    low: str = ""
    close: str = ""
    volume: str = ""


@dataclass(slots=True)
class RealtimeQuoteResult:
    market: str
    symbols: list[str]
    snapshots: dict[str, QuoteSnapshot | None] = field(default_factory=dict)
    ticks: dict[str, list[QuoteTick]] = field(default_factory=dict)
    order_books: dict[str, QuoteBest5 | None] = field(default_factory=dict)
    quote_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QuoteDataResult:
    symbol: str
    market: str = ""
    snapshot: QuoteSnapshot | None = None
    ticks: list[QuoteTick] = field(default_factory=list)
    order_book: QuoteBest5 | None = None
    kline: list[KLineRecord] = field(default_factory=list)
    kline_start_date: str = ""
    kline_end_date: str = ""
    quote_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QuoteSymbolListResult:
    markets: dict[str, list[StockListItem]] = field(default_factory=dict)
    quote_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PublicStockQuote:
    symbol: str
    name: str = ""
    date: str = ""
    time: str = ""
    last: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    reference: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    total_volume: int | None = None
    tick_volume: int | None = None
    bid_prices: tuple[Decimal | None, ...] = field(default_factory=tuple)
    bid_qtys: tuple[int | None, ...] = field(default_factory=tuple)
    ask_prices: tuple[Decimal | None, ...] = field(default_factory=tuple)
    ask_qtys: tuple[int | None, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublicStockHistoryBar:
    symbol: str
    date: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: int | None = None
    transactions: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublicMarketInfo:
    symbol: str
    name: str = ""
    deal: Decimal | None = None
    yesterday: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------
class Authority(IntEnum):
    """SKCenterLib_SetAuthority values from official PythonExampleV2 Config.py."""
    PROD = 0          # 正式環境
    PROD_SGX = 1      # 正式環境 SGX
    TEST = 2          # 測試環境
    TEST_SGX = 3      # 測試環境 SGX


class Side(IntEnum):
    BUY = 0
    SELL = 1


class TradeType(IntEnum):
    ROD = 0
    IOC = 1
    FOK = 2


class StockPrime(IntEnum):
    LISTED_OTC = 0    # 上市上櫃
    EMERGING = 1      # 興櫃


class StockPeriod(IntEnum):
    REGULAR = 0       # 盤中
    AFTER_HOURS = 1   # 盤後
    ODD_LOT = 2       # 零股
    INTRADAY_ODD_LOT = 4  # 盤中零股, used by SendStockOddLotOrder


class StockFlag(IntEnum):
    CASH = 0          # 現股
    MARGIN = 1        # 融資
    SHORT = 2         # 融券
    DAY_SHORT = 3     # 無券


class StockPriceType(IntEnum):
    MARKET = 1        # 市價
    LIMIT = 2         # 限價


class FuturesDayTrade(IntEnum):
    NO = 0
    YES = 1


class FuturesNewClose(IntEnum):
    NEW = 0
    CLOSE = 1
    AUTO = 2


class FuturesReserved(IntEnum):
    REGULAR = 0       # 盤中 / T盤及T+1盤
    RESERVED = 1      # T盤預約


class FutureRightsCoinType(IntEnum):
    ALL = 0
    TWD = 1
    RMB = 2


class MarketType(StrEnum):
    STOCK = "TS"
    FUTURE = "TF"
    OPTION = "TO"
    ODD_LOT = "TL"
    AFTER_HOURS = "TA"
    FOREIGN_STOCK = "OS"
    OVERSEA_FUTURE = "OF"


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------
class CapitalApiError(RuntimeError):
    """Base SDK exception."""


class CapitalApiNotLoaded(CapitalApiError):
    """Raised when SKCOM.dll / COM components were not loaded."""


class CapitalApiLiveOrderDisabled(CapitalApiError):
    """Raised when a live order was requested while live order mode is disabled."""


class CapitalApiCallError(CapitalApiError):
    """Raised when an API method returns a non-zero code and strict mode is enabled."""
