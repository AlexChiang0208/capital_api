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
    """Parsed from SKReplyLib.OnNewData raw comma-separated report (official 4-3-g).

    Field notes (verified against the V2.13.58 manual and live reports):
      market_type  TS 證券 / TA 盤後 / TL 零股 / TP 興櫃 / TC 盤中零股 /
                   TF 期貨 / TO 選擇權 / OF 海期 / OO 海選 / OS 複委託
      report_type  N 委託 / C 取消 / U 改量 / P 改價 / D 成交 / B 改價改量 / S 動態退單
      order_error  N 正常 / Y 失敗 / T 逾時
      side         composite BuySell field, e.g. "S00R2" = 賣 + 現股(00) + ROD + 限價(2);
                   the first char is always B(買)/S(賣) -> use .buy_sell
      before_qty / after_qty are EMPTY for C(取消) and D(成交) reports; a D report's
      qty is the fill quantity of that (possibly partial) fill.
    """
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
    def buy_sell(self) -> str:
        """'B' or 'S' (first char of the composite BuySell field), '' if unknown."""
        first = self.side[:1].upper()
        return first if first in ("B", "S") else ""

    @property
    def is_fill(self) -> bool:
        return self.report_type == "D"

    @property
    def is_cancel(self) -> bool:
        """True for 取消(C) and 交易所動態退單(S) reports."""
        return self.report_type in ("C", "S")

    @property
    def is_failed(self) -> bool:
        return self.order_error in ("Y", "T") or bool(self.error_msg)

    @property
    def remaining_qty(self) -> Optional[int]:
        try:
            return int(self.after_qty)
        except Exception:
            return None

    @property
    def is_open_like(self) -> bool:
        """Best-effort per-event view: order-lifecycle report that leaves qty open.

        NOTE: a single event cannot know about later fills; use
        EventHub.get_open_orders() for the fill/cancel-aware aggregation.
        """
        if self.is_fill or self.is_cancel or self.is_failed:
            return False
        rem = self.remaining_qty
        return rem is None or rem > 0


# GetOrderReport row status codes (official 5-4-4 field 11).
ORDER_STATUS_NAMES = {
    "0": "預約", "2": "全部成交", "3": "全部取消", "4": "部分成交,剩餘已取消",
    "5": "部分成交,剩餘可取消", "6": "委託失敗", "7": "委託成功", "8": "取消失敗",
    "9": "取消中", "F": "動態退單", "F1": "動態退單-全部取消",
    "F2": "動態退單-部分成交,剩餘已取消", "F3": "動態退單-部分委託成功", "F4": "否決",
}
# Query-row 盤別 codes (official 5-4-4 field 24).
QUERY_SESSION_NAMES = {
    "A": "一般", "B": "盤後", "C": "零股", "D": "拍賣", "E": "鉅額",
    "F": "盤中零股", "G": "標借", "H": "標購", "I": "證金標購",
}


@dataclass(slots=True)
class QueryOrderReport:
    """One GetOrderReport row (official 5-4-4, nFormat 1-6/9).

    NOTE: this sync-query row format is COMPLETELY DIFFERENT from OnNewData.
    Code meanings also differ from the order-sending enums:
      status      see ORDER_STATUS_NAMES (0 預約 / 2 全部成交 / 5 部分成交可消 / 7 委託成功 ...)
      session     盤別, see QUERY_SESSION_NAMES (A 一般 / B 盤後 / C 零股 / F 盤中零股 ...)
      stock_flag  0 現股 / 1 代資 / 2 代券 / 3 融資 / 4 融券 / 5,6 借券賣出 / 8 無券賣出
      trade_type  0 ROD / 1 GTC / 2 開盤(AT_THE_OPENING) / 3 IOC / 4 FOK / 7 收盤(AT_THE_CLOSE)
      price_type  1 市價 / 2 限價 / 3 範圍市價(期)或停損(海期) / 4 停損限價 / 5 收市價
    """
    login_id: str = ""
    market: str = ""           # TW/TS/TF/OS/OF
    product: str = ""          # STO 股票 / FUT 期貨 / OPT 選擇權 / ASO
    exchange: str = ""         # TSEA 上市 / TSEB 上櫃 / OTC 興櫃 / TAIFEX
    branch: str = ""
    account: str = ""
    order_no: str = ""         # 委託書號
    seq_no: str = ""           # 13碼電子流水號
    orig_seq_no: str = ""
    status: str = ""
    order_date: str = ""
    order_time: str = ""
    symbol: str = ""
    buy_sell: str = ""         # B/S
    session: str = ""
    stock_flag: str = ""
    trade_type: str = ""
    price_type: str = ""
    price: str = ""
    orig_price: str = ""
    valid_qty: str = ""        # 有效委託數量
    orig_qty: str = ""
    filled_qty: str = ""
    remaining_qty: str = ""
    day_trade: str = ""        # Y 當沖 / N 新倉 / O 平倉 / A 自動
    error_mark: str = ""       # Y 錯誤回報 / N 正常
    agent: str = ""
    unit_shares: str = ""      # 交易單位股數 (1000=整股)
    reserved_price_mark: str = ""  # 證券預約單價格註記: 空白 委託價 / 0 平盤 / 1 漲停 / 2 跌停 / h,l,C,c
    sale_no: str = ""
    avg_fill_price: str = ""
    cancel_qty: str = ""
    fill_date: str = ""
    fill_time: str = ""
    fields: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def status_name(self) -> str:
        return ORDER_STATUS_NAMES.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        """Cancellable / working states: 5 部分成交剩餘可取消, 7 委託成功, 0 預約."""
        return self.status in ("0", "5", "7")


@dataclass(slots=True)
class QueryFillReport:
    """One GetFulfillReport row (official 5-4-5, nFormat 1/5).

    fee 為預估手續費(證券千分之1.425), tax 為預估交易稅(千分之1或3);
    session/stock_flag/trade_type 代碼同 QueryOrderReport。
    """
    login_id: str = ""
    market: str = ""
    product: str = ""
    exchange: str = ""
    branch: str = ""
    account: str = ""
    order_no: str = ""
    fill_seq: str = ""         # 成交序號
    fill_date: str = ""
    fill_time: str = ""
    symbol: str = ""
    buy_sell: str = ""
    session: str = ""
    stock_flag: str = ""
    trade_type: str = ""
    price: str = ""            # 成交價
    qty: str = ""              # 成交量
    price_type: str = ""
    agent: str = ""
    sale_no: str = ""
    fee: str = ""
    tax: str = ""
    order_date: str = ""
    order_time: str = ""
    unit_shares: str = ""
    amount: str = ""           # 成交價金
    fill_time_ms: str = ""     # hhmmssfff
    fields: list[str] = field(default_factory=list)
    raw: str = ""


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
    """One GetOpenInterestGW (nFormat=1) row; see parsers.parse_future_position_raw."""
    market_type: str = ""
    symbol: str = ""
    buy_sell: str = ""
    open_qty: str = ""
    day_trade_qty: str = ""
    avg_price: str = ""
    fee: str = ""
    tax: str = ""
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
# Order price special codes (official 5-4 STOCKORDER / 5-2 FUTUREORDER)
# ----------------------------------------------------------------------
# STOCKORDER.bstrPrice: numeric limit price, or one of these codes.
# A MARKET order instead uses price="0" with StockPriceType.MARKET.
STOCK_PRICE_REFERENCE = "M"    # 參考價(昨收/平盤價)
STOCK_PRICE_LIMIT_UP = "H"     # 漲停價
STOCK_PRICE_LIMIT_DOWN = "L"   # 跌停價
# FUTUREORDER.bstrPrice: numeric limit price, or one of these codes.
# Official rule: the codes are only valid with IOC or FOK (not ROD).
FUTURES_PRICE_MARKET = "M"        # 市價
FUTURES_PRICE_RANGE_MARKET = "P"  # 範圍市價(一定範圍市價單)


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


class OrderMarket(IntEnum):
    """nMarketType for SetMaxQty / SetMaxCount / UnlockOrder (official 4-2-4/4-2-5)."""
    STOCK = 0            # TS 證券
    FUTURES = 1          # TF 期貨
    OPTIONS = 2          # TO 選擇權
    FOREIGN_STOCK = 3    # OS 複委託
    OVERSEA_FUTURES = 4  # OF 海外期貨
    OVERSEA_OPTIONS = 5  # OO 海外選擇權


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
