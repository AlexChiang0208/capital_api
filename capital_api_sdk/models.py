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
    valid_date: str = ""       # 委託有效日 yyyyMMdd (field 14)
    symbol: str = ""           # exchange contract code for futures (TMFI6), not the quote code (TM2609)
    leg1_product: str = ""     # Tandem 商品代號1: futures product id such as FITM / FITX (field 16)
    leg1_month: str = ""       # Tandem 契約年月1 yyyyMM, e.g. 202609 (field 17); the year TMFI6 only implies
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


# QueryOrderReport attribute -> 中文 (official 5-4-4 names; parentheses = wording on the 策略王 委回 screen).
# The screen shows the QUOTE code (TM2609) while the query returns the exchange code (TMFI6), and it
# folds day_trade / buy_sell / trade_type / session into one "新倉買進ROD一般" column.
QUERY_ORDER_FIELDS: dict[str, str] = {
    "market": "市場別", "product": "商品別", "exchange": "交易所別", "branch": "分公司代號", "account": "交易帳號",
    "order_no": "委託書號", "seq_no": "13碼電子流水號(委託序號)", "orig_seq_no": "原始13碼電子流水號",
    "status": "委託狀態碼", "status_name": "委託狀態", "order_date": "委託日期", "order_time": "委託時間",
    "valid_date": "委託有效日", "symbol": "商品代號(交易所契約代碼)",
    "leg1_product": "Tandem商品代號1(期貨商品代號)", "leg1_month": "Tandem契約年月1(契約年月)",
    "buy_sell": "買賣別(B/S)", "session": "盤別", "stock_flag": "國內證券委託條件",
    "trade_type": "委託條件(0 ROD/1 GTC/3 IOC/4 FOK)", "price_type": "委託方式(1 市價/2 限價/3 範圍市價)",
    "price": "委託價", "orig_price": "原始委託價格", "valid_qty": "有效委託數量",
    "orig_qty": "原始委託數量(委託量)", "filled_qty": "成交數量(成交量)", "remaining_qty": "剩餘數量",
    "day_trade": "當沖註記(Y 當沖/N 新倉/O 平倉/A 自動)", "error_mark": "是否錯誤回報", "agent": "下單來源別",
    "unit_shares": "交易單位股數", "reserved_price_mark": "證券預約單價格註記", "sale_no": "營業員",
    "avg_fill_price": "成交均價", "cancel_qty": "取消總量", "fill_date": "成交日", "fill_time": "成交時間",
}


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


# GetOpenInterestGW nFormat=1 row (official 4-2-x OnOpenInterest, 10 fields), attribute -> 中文.
# Verified live 2026-09-03: "TF,<account>,TM09,B,1,0,46560.00,,,<login>" -> 買賣別 is B/S, 手續費 /
# 交易稅 are EMPTY, and the symbol is the POSITION-TABLE code (微台 TM09 = TM + MM), which differs from
# both the quote code (TM2609) and the exchange code used by reports (TMFI6); see taifex.contract_of.
FUTURE_POSITION_FIELDS: dict[str, str] = {
    "market_type": "市場別",
    "account_no": "帳號",
    "symbol": "商品(庫存表代碼)",
    "buy_sell": "買賣別(B/S)",
    "open_qty": "未平倉(含當沖)",
    "day_trade_qty": "當沖未平倉",
    "avg_price": "成交均價(平均成本)",
    "fee": "單口手續費",
    "tax": "交易稅",
    "login_id": "LOGIN_ID",
}


@dataclass(slots=True)
class FuturePosition:
    """One GetOpenInterestGW (nFormat=1) row; field meanings in FUTURE_POSITION_FIELDS."""
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


# OnFutureRights row (official 4-2-i, 41 fields): position in this dict = 0-based field index.
# 中文 follows the official table; parentheses give the name shown on the 策略王 期貨權益 screen.
# Verified 2026-09-03 against that screen: 權益數[6] = 權益總值[19]; 超額保證金[7] = 超額最佳[18];
# 可用餘額[31] = 足額可用[28] = 足額現金可用[32] = 權益數 - 部位原始保證金[15] - 委託保證金[17];
# 原始保證金[13] = 部位原始[15] + 委託[17] (the screen's "原始保證金" is [15]);
# 風險指標[34] = 維持率[24] = 權益數 / 部位原始保證金 x 100 (14257 = 142.57%).
# With no position 風險指標 / 維持率 come back as "*********" (keep as text).
FUTURE_RIGHTS_FIELDS: dict[str, str] = {
    "cash_balance": "帳戶餘額(本日餘額)",
    "floating_pnl": "未沖銷期貨浮動損益",
    "realized_fee": "已實現費用(手續費)",
    "tax": "交易稅(期交稅)",
    "premium_withheld": "預扣權利金(委託權利金)",
    "premium_paid": "權利金收付(權利金收入與支出)",
    "equity": "權益數",
    "excess_margin": "超額/追繳保證金",
    "deposit_withdraw": "存提款(存提)",
    "buy_option_value": "未沖銷買方選擇權市值",
    "sell_option_value": "未沖銷賣方選擇權市值",
    "closed_pnl": "期貨平倉損益淨額",
    "intraday_unrealized": "盤中未實現(期貨部位未實現)",
    "initial_margin": "原始保證金(部位+委託)",
    "maintenance_margin": "維持保證金(部位+委託)",
    "position_initial_margin": "部位原始保證金(畫面:原始保證金)",
    "position_maintenance_margin": "部位維持保證金(畫面:維持保證金)",
    "order_margin": "委託保證金",
    "best_excess_margin": "超額最佳保證金",
    "total_equity": "權益總值",
    "fee_withheld": "預扣費用",
    "initial_margin_dup": "原始保證金(官方重複欄)",
    "yesterday_balance": "昨日餘額(前日餘額)",
    "option_combo_margin_flag": "選擇權組合單加不加收保證金",
    "maintenance_ratio": "維持率(權益比率)",
    "currency": "幣別",
    "full_initial_margin": "足額原始保證金",
    "full_maintenance_margin": "足額維持保證金",
    "full_available": "足額可用",
    "collateral_amount": "有價證券抵繳總額",
    "securities_available": "有價可用",
    "available_balance": "可用餘額(可動用/出金保證金)",
    "full_cash_available": "足額現金可用",
    "securities_value": "有價證券價值",
    "risk_indicator": "風險指標",
    "option_expiry_diff": "選擇權到期差益",
    "option_expiry_loss": "選擇權到期差損",
    "futures_expiry_pnl": "期貨到期損益(到期履約損益)",
    "extra_margin": "加收保證金",
    "login_id": "LOGIN_ID",
    "account_no": "帳號",
}
# Text-only fields; every other FutureRights field is a number (or "*********" when undefined).
FUTURE_RIGHTS_TEXT_FIELDS = frozenset({"currency", "option_combo_margin_flag", "login_id", "account_no"})


@dataclass(slots=True)
class FutureRights:
    """One OnFutureRights row: all 41 official fields as strings, meanings in FUTURE_RIGHTS_FIELDS."""
    cash_balance: str = ""
    floating_pnl: str = ""
    realized_fee: str = ""
    tax: str = ""
    premium_withheld: str = ""
    premium_paid: str = ""
    equity: str = ""
    excess_margin: str = ""
    deposit_withdraw: str = ""
    buy_option_value: str = ""
    sell_option_value: str = ""
    closed_pnl: str = ""
    intraday_unrealized: str = ""
    initial_margin: str = ""
    maintenance_margin: str = ""
    position_initial_margin: str = ""
    position_maintenance_margin: str = ""
    order_margin: str = ""
    best_excess_margin: str = ""
    total_equity: str = ""
    fee_withheld: str = ""
    initial_margin_dup: str = ""
    yesterday_balance: str = ""
    option_combo_margin_flag: str = ""
    maintenance_ratio: str = ""
    currency: str = ""
    full_initial_margin: str = ""
    full_maintenance_margin: str = ""
    full_available: str = ""
    collateral_amount: str = ""
    securities_available: str = ""
    available_balance: str = ""
    full_cash_available: str = ""
    securities_value: str = ""
    risk_indicator: str = ""
    option_expiry_diff: str = ""
    option_expiry_loss: str = ""
    futures_expiry_pnl: str = ""
    extra_margin: str = ""
    login_id: str = ""
    account_no: str = ""
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

    @property
    def subscription_refused(self) -> bool:
        """True when RequestStocks / RequestTicks itself was rejected (typically 3030: the account's
        quote connections are exhausted). Matches the subscribe method names only, so a failed
        CancelRequestStocks at the end of a one-shot query does not count."""
        return any(error.startswith(("SKQuoteLib_RequestStocks", "SKQuoteLib_RequestTicks"))
                   for error in self.quote_errors)


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


_SIDE_CODES = {
    "B": "B", "S": "S", "0": "B", "1": "S",
    "BUY": "B", "SELL": "S", "LONG": "B", "SHORT": "S",
    "買": "B", "賣": "S", "買進": "B", "賣出": "S",
}


def normalize_side(value: Any) -> str:
    """Any buy/sell spelling -> "B" / "S"; raises ValueError for anything else.

    Accepts Side, 0/1 (FUTUREORDER.sBuySell encoding), B/S (GetOrderReport and
    GetOpenInterestGW rows, verified live 2026-09-03), buy/sell/long/short and 買/賣.
    Never guesses: a wrong side flips a position's sign in any exposure calculation.
    """
    if isinstance(value, Side):
        return "B" if value == Side.BUY else "S"
    text = str(value).strip().upper()
    if text in _SIDE_CODES:
        return _SIDE_CODES[text]
    raise ValueError(f"unknown buy/sell code: {value!r}")


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


def is_report_end_row(raw: str) -> bool:
    """True for the rows SKCOM appends to close an account query.

    Either the "##,,,,..." terminator (official: 當全部資料已經全部回傳完畢,
    將回傳一筆以「##」開頭的內容) or the "001,查無資料,帳號" empty-result row.
    Receiving one means the batch is complete, so callers can stop waiting.
    """
    return raw.startswith("##") or "查無資料" in raw
