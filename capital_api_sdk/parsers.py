"""Parse SKCOM raw strings and COM structs into SDK models (accounts, reports, quotes)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .models import (
    Account,
    CapitalPayBalance,
    FuturePosition,
    FutureRights,
    KLineRecord,
    OrderEvent,
    QueryFillReport,
    QueryOrderReport,
    QuoteBest5,
    QuoteSnapshot,
    QuoteTick,
    StockListItem,
    StockPosition,
)


def safe_get(fields: list[str], idx: int, default: str = "") -> str:
    return fields[idx].strip() if 0 <= idx < len(fields) else default


def parse_account(login_id: str, raw: str) -> Account:
    """
    Official PythonExampleV2 OnAccount sample derives full account as values[1] + values[3].
    Raw account data usually includes market/account type, branch, and account number.
    """
    fields = raw.split(',')
    branch = safe_get(fields, 1)
    account_no = safe_get(fields, 3)
    account_type = safe_get(fields, 0)
    full_account = f"{branch}{account_no}" if branch or account_no else raw
    return Account(login_id=login_id, account_type=account_type, branch=branch,
                   account_no=account_no, full_account=full_account, raw=raw)


def parse_capital_pay_balance(raw: str) -> CapitalPayBalance:
    """Parse SKOrderLib.GetBalance(login_id): true,balance,withdrawable,buying_power."""
    text = (raw or "").strip()
    if text == "-1":
        raise RuntimeError("GetBalance failed: returned -1")
    if text.endswith('.'):
        text = text[:-1]
    parts = [p.strip() for p in text.split(',')]
    if len(parts) != 4:
        raise ValueError(f"Unexpected GetBalance format: {raw!r}")
    return CapitalPayBalance(
        has_capital_pay=parts[0].lower() == "true",
        balance=Decimal(parts[1] or "0"),
        withdrawable_amount=Decimal(parts[2] or "0"),
        today_buying_power=Decimal(parts[3] or "0"),
        raw=raw,
    )


def parse_order_event(login_id: str, raw: str) -> OrderEvent:
    """
    Parse one OnNewData report row (official manual 4-3-g, 48 fields, 0-based):
    0 KeyNo, 1 MarketType, 2 Type, 3 OrderErr, 4 Broker, 5 CustNo,
    6 BuySell(composite), 7 ExchangeID, 8 ComId(symbol), 9 StrikePrice,
    10 OrderNo(委託書號), 11 Price, 12-19 Numerator/Denominator/Price1/Price2...,
    20 Qty, 21 BeforeQty, 22 AfterQty, 23 Date, 24 Time, 25 OkSeq, 26 SubID,
    27 SaleNo, 28 Agent, 29 TradeDate, 30 MsgNo, 31 PreOrder(A盤中/B預約),
    32-37 leg1/leg2 ComId/YearMonth/StrikePrice, 38 ExecutionNo, 39 PriceSymbol,
    40 Reserved(盤別), 41 OrderEffective, 42 CallPut, 43 OrderSeq(交易所單號),
    44 ErrorMsg, 45 CancelOrderMarkByExchange, 46 ExchangeTandemMsg, 47 SeqNo(13碼).
    """
    f = raw.split(',')
    return OrderEvent(
        login_id=login_id,
        raw=raw,
        fields=f,
        key_no=safe_get(f, 0),
        market_type=safe_get(f, 1),
        report_type=safe_get(f, 2),
        order_error=safe_get(f, 3),
        broker=safe_get(f, 4),
        customer_no=safe_get(f, 5),
        side=safe_get(f, 6),
        symbol=safe_get(f, 8),
        order_no=safe_get(f, 10),
        price=safe_get(f, 11),
        qty=safe_get(f, 20),
        before_qty=safe_get(f, 21),
        after_qty=safe_get(f, 22),
        date=safe_get(f, 23),
        time=safe_get(f, 24),
        ok_seq=safe_get(f, 25),
        order_seq=safe_get(f, 43),
        error_msg=safe_get(f, 44),
        seq_no=safe_get(f, 47),
    )


def parse_stock_position_raw(raw: str) -> StockPosition:
    """
    Parse one OnRealBalanceReport row (official manual 4-2-c, 0-based):
    0 股票代號, 1 庫存種類(T集保/C融資/L融券), 2 資額度原始, 3 資額度可用,
    4 券額度原始, 5 券額度可用, 6 昨日庫存, 7 今日委買, 8 今日委賣,
    9 今日買進成交, 10 今日賣出成交, 11 資券可回補/集保可賣出, 12 可資沖股數,
    13 可券沖股數, 14 即時庫存, 15 (忽略), 16 即時個股維持率,
    17 LOGIN_ID, 18 ACCOUNT_NO.

    today_buy_qty / today_sell_qty are today's ORDERED quantities (委買/委賣);
    the matched quantities are today_buy_matched / today_sell_matched.
    """
    f = raw.split(',')
    return StockPosition(
        symbol=safe_get(f, 0),
        inventory_type=safe_get(f, 1),
        yesterday_inventory=safe_get(f, 6),
        today_buy_qty=safe_get(f, 7),
        today_sell_qty=safe_get(f, 8),
        today_buy_matched=safe_get(f, 9),
        today_sell_matched=safe_get(f, 10),
        sellable_qty=safe_get(f, 11),
        realtime_inventory=safe_get(f, 14),
        login_id=safe_get(f, 17),
        account_no=safe_get(f, 18),
        raw=raw,
    )


def parse_future_position_raw(raw: str) -> FuturePosition:
    """
    Parse one OnOpenInterest row from GetOpenInterestGW nFormat=1 (official manual,
    0-based): 0 市場別(TM), 1 帳號, 2 商品, 3 買賣別, 4 未平倉部位,
    5 當沖未平倉部位, 6 平均成本(小數已處理), 7 單口手續費, 8 交易稅(萬分之X),
    9 LOGIN_ID. GW format 1 does NOT carry market price / floating P&L.
    """
    f = raw.split(',')
    return FuturePosition(
        market_type=safe_get(f, 0),
        account_no=safe_get(f, 1),
        symbol=safe_get(f, 2),
        buy_sell=safe_get(f, 3),
        open_qty=safe_get(f, 4),
        day_trade_qty=safe_get(f, 5),
        avg_price=safe_get(f, 6),
        fee=safe_get(f, 7),
        tax=safe_get(f, 8),
        login_id=safe_get(f, 9),
        raw=raw,
    )


def parse_future_rights_raw(raw: str) -> FutureRights:
    """
    Best-effort parser using SKDLLPythonTester FutureRights struct field order.
    Important fields: Equity index 6, ExcessMargin index 7, InitialMargin index 13,
    MaintenanceMargin index 14, OrderMargin index 17, Currency index 25,
    AvailableBalance index 31, RiskIndicator index 34.
    """
    f = raw.split(',')
    return FutureRights(
        equity=safe_get(f, 6),
        excess_margin=safe_get(f, 7),
        initial_margin=safe_get(f, 13),
        maintenance_margin=safe_get(f, 14),
        order_margin=safe_get(f, 17),
        currency=safe_get(f, 25),
        available_balance=safe_get(f, 31),
        risk_indicator=safe_get(f, 34),
        login_id=safe_get(f, 39),
        account_no=safe_get(f, 40),
        raw=raw,
    )


def parse_many(lines: Iterable[str], parser):
    return [parser(line) for line in lines if str(line).strip()]


def parse_query_order_row(login_id: str, raw: str) -> QueryOrderReport:
    """
    Parse one GetOrderReport row (official 5-4-4, nFormat 1-6/9; 0-based = 官方編號-1):
    0 市場別, 1 商品別, 2 交易所別, 3 分公司, 4 IBNO, 5 帳號, 6 子帳,
    7 委託書號, 8 13碼流水號, 9 原始13碼, 10 委託狀態, 11 委託日, 12 委託時間,
    13 歸屬日, 14 有效日, 15 商品代號, 16-21 兩腳Tandem, 22 買賣別, 23 盤別,
    24 證券委託條件, 25 委託條件, 26 委託方式, 27 委託價, 28 原始委託價,
    29 有效量, 30 原始量, 31 成交量, 32 剩餘量, 33 當沖, 34 判別T+1, 35 錯誤回報,
    36 下單來源, 37 交易單位股數, 38 預約單價格註記, 39 營業員, 40 CallPut,
    41-48 分子分母/觸發價, 49 交易所訊息, 50 退單基準價, 51 取消總量,
    52 成交日, 53 成交時間, 54-66 複委託/碎股, 67 委託時間hhmmssfff, 68 交易註記.
    Verified live 2026-09-01 (intraday odd-lot fills).
    """
    f = raw.split(',')
    return QueryOrderReport(
        login_id=login_id,
        market=safe_get(f, 0), product=safe_get(f, 1), exchange=safe_get(f, 2),
        branch=safe_get(f, 3), account=safe_get(f, 5),
        order_no=safe_get(f, 7), seq_no=safe_get(f, 8), orig_seq_no=safe_get(f, 9),
        status=safe_get(f, 10), order_date=safe_get(f, 11), order_time=safe_get(f, 12),
        symbol=safe_get(f, 15), buy_sell=safe_get(f, 22), session=safe_get(f, 23),
        stock_flag=safe_get(f, 24), trade_type=safe_get(f, 25), price_type=safe_get(f, 26),
        price=safe_get(f, 27), orig_price=safe_get(f, 28),
        valid_qty=safe_get(f, 29), orig_qty=safe_get(f, 30),
        filled_qty=safe_get(f, 31), remaining_qty=safe_get(f, 32),
        day_trade=safe_get(f, 33), error_mark=safe_get(f, 35), agent=safe_get(f, 36),
        unit_shares=safe_get(f, 37), reserved_price_mark=safe_get(f, 38),
        sale_no=safe_get(f, 39), avg_fill_price=safe_get(f, 43), cancel_qty=safe_get(f, 51),
        fill_date=safe_get(f, 52), fill_time=safe_get(f, 53),
        fields=f, raw=raw,
    )


def parse_query_fill_row(login_id: str, raw: str) -> QueryFillReport:
    """
    Parse one GetFulfillReport row (official 5-4-5, nFormat 1/5). The live rows
    carry NO 商品名稱 column, so from 商品代號 on the 0-based index is 官方編號-2:
    0 市場別, 1 商品別, 2 交易所別, 3 分公司, 4 IBNO, 5 帳號, 6 子帳,
    7 委託書號, 8 成交序號, 9 成交日, 10 成交時間, 11 歸屬日, 12 商品代號,
    13-20 兩腳Tandem+成交價1/2, 21 買賣別, 22 盤別, 23 證券委託條件, 24 委託條件,
    25 成交價, 26 成交量, 27 當沖, 28 判別T+1, 29 下單來源, 30 委託方式,
    31 委託有效日, 32 營業員, 33 預估手續費, 34 預估交易稅, 35 幣別,
    36 委託日期, 37 委託時間, 38-39 分子/分母, 40 交易單位股數,
    43 成交價金, 49 成交時間hhmmssfff. Verified live 2026-09-01.
    """
    f = raw.split(',')
    return QueryFillReport(
        login_id=login_id,
        market=safe_get(f, 0), product=safe_get(f, 1), exchange=safe_get(f, 2),
        branch=safe_get(f, 3), account=safe_get(f, 5),
        order_no=safe_get(f, 7), fill_seq=safe_get(f, 8),
        fill_date=safe_get(f, 9), fill_time=safe_get(f, 10),
        symbol=safe_get(f, 12), buy_sell=safe_get(f, 21), session=safe_get(f, 22),
        stock_flag=safe_get(f, 23), trade_type=safe_get(f, 24),
        price=safe_get(f, 25), qty=safe_get(f, 26),
        agent=safe_get(f, 29), price_type=safe_get(f, 30), sale_no=safe_get(f, 32),
        fee=safe_get(f, 33), tax=safe_get(f, 34),
        order_date=safe_get(f, 36), order_time=safe_get(f, 37),
        unit_shares=safe_get(f, 40), amount=safe_get(f, 43), fill_time_ms=safe_get(f, 49),
        fields=f, raw=raw,
    )


def parse_report_query_result(login_id: str, raw: str, kind: str = "order"):
    """
    Parse the blocking GetOrderReport / GetFulfillReport result string.

    Rows are CRLF-separated and use the DEDICATED query formats (official
    5-4-4 / 5-4-5), NOT the OnNewData layout. kind: "order" or "fill".
    M003 means no data; M999 means query error / issued within the official
    5-second interval (raised so callers can retry).
    """
    text = (raw or "").strip()
    if not text or text.startswith("M003"):
        return []
    if text.startswith("M999"):
        raise RuntimeError(f"report query rejected: {text}")
    parser = parse_query_fill_row if kind == "fill" else parse_query_order_row
    rows = [row.strip() for row in text.replace("\r\n", "\n").split("\n") if row.strip()]
    return [parser(login_id, row) for row in rows]


# ----------------------------------------------------------------------
# SKQuote COM structs / events / raw strings -> models
# ----------------------------------------------------------------------
def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-"}:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def scaled_decimal(value: Any, decimal_places: int = 2) -> Decimal | None:
    if value is None:
        return None
    try:
        raw = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return raw / (Decimal(10) ** int(decimal_places))


def quote_snapshot_from_com(stock: Any, market_no: int | None = None, stock_index: int | None = None) -> QuoteSnapshot:
    decimal_places = int(getattr(stock, "sDecimal", 2) or 2)

    def attr(name: str, default: Any = None) -> Any:
        return getattr(stock, name, default)

    raw = {
        "nStockIdx": attr("nStockIdx"),
        "sTypeNo": attr("sTypeNo"),
        "bstrMarketNo": attr("bstrMarketNo"),
        "bstrStockNo": attr("bstrStockNo"),
        "bstrStockName": attr("bstrStockName"),
        "nOpen": attr("nOpen"),
        "nHigh": attr("nHigh"),
        "nLow": attr("nLow"),
        "nClose": attr("nClose"),
        "nRef": attr("nRef"),
        "nBid": attr("nBid"),
        "nBc": attr("nBc"),
        "nAsk": attr("nAsk"),
        "nAc": attr("nAc"),
        "nTickQty": attr("nTickQty"),
        "nTQty": attr("nTQty"),
        "nYQty": attr("nYQty"),
        "nUp": attr("nUp"),
        "nDown": attr("nDown"),
        "nTBc": attr("nTBc"),
        "nTAc": attr("nTAc"),
        "sDecimal": decimal_places,
    }
    resolved_market_no = market_no if market_no is not None else int_or_none(attr("bstrMarketNo"))
    resolved_stock_index = stock_index if stock_index is not None else int_or_none(attr("nStockIdx"))
    return QuoteSnapshot(
        market_no=resolved_market_no,
        stock_index=resolved_stock_index,
        symbol=str(attr("bstrStockNo", "") or ""),
        name=str(attr("bstrStockName", "") or ""),
        market_code=str(attr("bstrMarketNo", "") or ""),
        type_no=int_or_none(attr("sTypeNo")),
        open=scaled_decimal(attr("nOpen"), decimal_places),
        high=scaled_decimal(attr("nHigh"), decimal_places),
        low=scaled_decimal(attr("nLow"), decimal_places),
        close=scaled_decimal(attr("nClose"), decimal_places),
        reference=scaled_decimal(attr("nRef"), decimal_places),
        bid=scaled_decimal(attr("nBid"), decimal_places),
        bid_qty=int_or_none(attr("nBc")),
        ask=scaled_decimal(attr("nAsk"), decimal_places),
        ask_qty=int_or_none(attr("nAc")),
        tick_qty=int_or_none(attr("nTickQty")),
        total_qty=int_or_none(attr("nTQty")),
        yesterday_qty=int_or_none(attr("nYQty")),
        up_limit=scaled_decimal(attr("nUp"), decimal_places),
        down_limit=scaled_decimal(attr("nDown"), decimal_places),
        total_bid_count=int_or_none(attr("nTBc")),
        total_ask_count=int_or_none(attr("nTAc")),
        decimal_places=decimal_places,
        raw=raw,
    )


def quote_tick_from_event(
    market_no: int,
    stock_index: int,
    ptr: int,
    date: int,
    time_hms: int,
    time_millis_micros: int,
    bid: Any,
    ask: Any,
    close: Any,
    qty: int,
    simulate: int,
    *,
    decimal_places: int = 2,
    history: bool = False,
) -> QuoteTick:
    raw = (market_no, stock_index, ptr, date, time_hms, time_millis_micros, bid, ask, close, qty, simulate)
    return QuoteTick(
        market_no=int(market_no),
        stock_index=int(stock_index),
        ptr=int(ptr),
        date=int(date),
        time_hms=int(time_hms),
        time_millis_micros=int(time_millis_micros),
        bid=scaled_decimal(bid, decimal_places),
        ask=scaled_decimal(ask, decimal_places),
        close=scaled_decimal(close, decimal_places),
        qty=int(qty),
        simulate=int(simulate),
        history=bool(history),
        raw=raw,
    )


def quote_tick_from_struct(
    tick: Any,
    market_no: int,
    stock_index: int,
    *,
    decimal_places: int = 2,
    symbol: str = "",
) -> QuoteTick:
    """Build a QuoteTick from an SKTICK COM struct (SKQuoteLib_GetTickLONG)."""
    row = quote_tick_from_event(
        int(market_no),
        int(stock_index),
        int(tick.nPtr),
        int(tick.nDate),
        int(tick.nTimehms),
        int(tick.nTimemillismicros),
        tick.nBid,
        tick.nAsk,
        tick.nClose,
        int(tick.nQty),
        int(tick.nSimulate),
        decimal_places=int(decimal_places),
    )
    row.symbol = str(symbol or "")
    return row


def quote_best5_from_event(
    market_no: int,
    stock_index: int,
    best_bid_1: Any,
    best_bid_qty_1: int,
    best_bid_2: Any,
    best_bid_qty_2: int,
    best_bid_3: Any,
    best_bid_qty_3: int,
    best_bid_4: Any,
    best_bid_qty_4: int,
    best_bid_5: Any,
    best_bid_qty_5: int,
    extend_bid: Any,
    extend_bid_qty: int,
    best_ask_1: Any,
    best_ask_qty_1: int,
    best_ask_2: Any,
    best_ask_qty_2: int,
    best_ask_3: Any,
    best_ask_qty_3: int,
    best_ask_4: Any,
    best_ask_qty_4: int,
    best_ask_5: Any,
    best_ask_qty_5: int,
    extend_ask: Any,
    extend_ask_qty: int,
    simulate: int,
    *,
    decimal_places: int = 2,
) -> QuoteBest5:
    raw = (
        market_no, stock_index,
        best_bid_1, best_bid_qty_1, best_bid_2, best_bid_qty_2, best_bid_3, best_bid_qty_3,
        best_bid_4, best_bid_qty_4, best_bid_5, best_bid_qty_5, extend_bid, extend_bid_qty,
        best_ask_1, best_ask_qty_1, best_ask_2, best_ask_qty_2, best_ask_3, best_ask_qty_3,
        best_ask_4, best_ask_qty_4, best_ask_5, best_ask_qty_5, extend_ask, extend_ask_qty,
        simulate,
    )
    return QuoteBest5(
        market_no=int(market_no),
        stock_index=int(stock_index),
        bid_prices=tuple(scaled_decimal(v, decimal_places) for v in (best_bid_1, best_bid_2, best_bid_3, best_bid_4, best_bid_5)),
        bid_qtys=(int(best_bid_qty_1), int(best_bid_qty_2), int(best_bid_qty_3), int(best_bid_qty_4), int(best_bid_qty_5)),
        ask_prices=tuple(scaled_decimal(v, decimal_places) for v in (best_ask_1, best_ask_2, best_ask_3, best_ask_4, best_ask_5)),
        ask_qtys=(int(best_ask_qty_1), int(best_ask_qty_2), int(best_ask_qty_3), int(best_ask_qty_4), int(best_ask_qty_5)),
        extend_bid=scaled_decimal(extend_bid, decimal_places),
        extend_bid_qty=int(extend_bid_qty),
        extend_ask=scaled_decimal(extend_ask, decimal_places),
        extend_ask_qty=int(extend_ask_qty),
        simulate=int(simulate),
        raw=raw,
    )


def quote_best5_from_struct(
    best5: Any,
    market_no: int,
    stock_index: int,
    *,
    decimal_places: int = 2,
    symbol: str = "",
) -> QuoteBest5:
    """Build a QuoteBest5 from an SKBEST5 COM struct (SKQuoteLib_GetBest5LONG)."""
    row = quote_best5_from_event(
        int(market_no), int(stock_index),
        best5.nBid1, int(best5.nBidQty1), best5.nBid2, int(best5.nBidQty2),
        best5.nBid3, int(best5.nBidQty3), best5.nBid4, int(best5.nBidQty4),
        best5.nBid5, int(best5.nBidQty5), best5.nExtendBid, int(best5.nExtendBidQty),
        best5.nAsk1, int(best5.nAskQty1), best5.nAsk2, int(best5.nAskQty2),
        best5.nAsk3, int(best5.nAskQty3), best5.nAsk4, int(best5.nAskQty4),
        best5.nAsk5, int(best5.nAskQty5), best5.nExtendAsk, int(best5.nExtendAskQty),
        int(best5.nSimulate),
        decimal_places=int(decimal_places),
    )
    row.symbol = str(symbol or "")
    return row


def parse_stock_list_item(market_no: int, raw: str) -> StockListItem:
    fields = [field.strip() for field in str(raw).split(",")]
    return StockListItem(
        market_no=int(market_no),
        symbol=fields[0] if fields else "",
        name=fields[1] if len(fields) > 1 else "",
        fields=fields,
        raw=str(raw),
    )


def parse_stock_list_items(market_no: int, raw: str) -> list[StockListItem]:
    rows = [row.strip() for row in str(raw).split(";") if row.strip()]
    return [parse_stock_list_item(market_no, row) for row in rows]


def parse_kline_record(symbol: str, raw: str) -> KLineRecord:
    fields = [field.strip() for field in str(raw).split(",")]
    record = KLineRecord(symbol=str(symbol), raw=str(raw), fields=fields)
    if len(fields) >= 6:
        record.date = fields[0]
        if " " in record.date:
            record.date, record.time = record.date.split(maxsplit=1)
        record.open = fields[1]
        record.high = fields[2]
        record.low = fields[3]
        record.close = fields[4]
        record.volume = fields[5]
    if len(fields) >= 7:
        record.time = fields[1]
        record.open = fields[2]
        record.high = fields[3]
        record.low = fields[4]
        record.close = fields[5]
        record.volume = fields[6]
    return record
