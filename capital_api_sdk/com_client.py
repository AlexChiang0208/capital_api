"""Low-level SKCOM.dll wrapper: config, COM event sinks, event cache, and CapitalClient."""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Account,
    ApiResult,
    Authority,
    CapitalApiError,
    CapitalApiNotLoaded,
    CapitalPayBalance,
    FuturePosition,
    FutureRights,
    FutureRightsCoinType,
    FuturesDayTrade,
    FuturesNewClose,
    FuturesReserved,
    KLineRecord,
    OrderEvent,
    QuoteBest5,
    QuoteConnectionEvent,
    QuoteSnapshot,
    QuoteTick,
    Side,
    StockFlag,
    StockListItem,
    StockPeriod,
    StockPosition,
    StockPriceType,
    StockPrime,
    TradeType,
)
from .parsers import (
    parse_account,
    parse_capital_pay_balance,
    parse_future_position_raw,
    parse_future_rights_raw,
    parse_kline_record,
    parse_many,
    parse_order_event,
    parse_report_query_result,
    parse_stock_list_items,
    parse_stock_position_raw,
    quote_best5_from_event,
    quote_best5_from_struct,
    quote_snapshot_from_com,
    quote_tick_from_event,
    quote_tick_from_struct,
)


# ----------------------------------------------------------------------
# Config / .env
# ----------------------------------------------------------------------
def load_dotenv_config() -> None:
    """Load .env via python-dotenv when available, else a minimal fallback parser."""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        _load_env_file(Path.cwd() / ".env")
        _load_env_file(Path(__file__).resolve().parents[1] / ".env")
        return

    load_dotenv()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(slots=True)
class CapitalConfig:
    user_id: str
    password: str
    dll_path: str = "SKCOM.dll"
    authority: Authority = Authority.PROD
    log_path: str | None = None
    cert_id: str | None = None

    @classmethod
    def from_env(cls) -> "CapitalConfig":
        load_dotenv_config()
        authority_raw = os.getenv("CAPITAL_AUTHORITY", "PROD").upper()
        authority = Authority[authority_raw] if authority_raw in Authority.__members__ else Authority(int(authority_raw))
        return cls(
            user_id=os.environ["CAPITAL_USER_ID"],
            password=os.environ["CAPITAL_PASSWORD"],
            dll_path=os.getenv("CAPITAL_SKCOM_DLL", "SKCOM.dll"),
            authority=authority,
            log_path=os.getenv("CAPITAL_LOG_PATH") or None,
            cert_id=os.getenv("CAPITAL_CERT_ID") or None,
        )


# ----------------------------------------------------------------------
# EventHub: in-memory state built from SKCOM events
# ----------------------------------------------------------------------
@dataclass
class EventHub:
    """In-memory state built from SKCOM events."""
    accounts_by_login: dict[str, list[Account]] = field(default_factory=lambda: defaultdict(list))
    raw_reply_messages: list[tuple[str, str]] = field(default_factory=list)
    raw_new_data: list[OrderEvent] = field(default_factory=list)
    raw_order_reports: list[OrderEvent] = field(default_factory=list)
    raw_fill_reports: list[OrderEvent] = field(default_factory=list)
    raw_stock_positions: list[str] = field(default_factory=list)
    raw_future_positions: list[str] = field(default_factory=list)
    raw_future_rights: list[str] = field(default_factory=list)
    quote_connections: list[QuoteConnectionEvent] = field(default_factory=list)
    quote_server_times: list[tuple[int, int, int, int]] = field(default_factory=list)
    raw_stock_lists: dict[int, list[StockListItem]] = field(default_factory=lambda: defaultdict(list))
    quote_events: list[QuoteSnapshot] = field(default_factory=list)
    quotes_by_key: dict[tuple[int | None, int | None], QuoteSnapshot] = field(default_factory=dict)
    quotes_by_symbol: dict[str, QuoteSnapshot] = field(default_factory=dict)
    tick_events: list[QuoteTick] = field(default_factory=list)
    best5_events: list[QuoteBest5] = field(default_factory=list)
    best5_by_key: dict[tuple[int, int], QuoteBest5] = field(default_factory=dict)
    kline_records: list[KLineRecord] = field(default_factory=list)
    quote_errors: list[str] = field(default_factory=list)
    async_order_results: list[tuple[int, int, str]] = field(default_factory=list)
    proxy_order_results: list[tuple[int, int, str]] = field(default_factory=list)
    complete_logins: set[str] = field(default_factory=set)
    connection_status: list[tuple[str, int]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add_account(self, login_id: str, raw: str) -> None:
        account = parse_account(login_id, raw)
        with self._lock:
            existing = self.accounts_by_login[login_id]
            if not any(a.full_account == account.full_account for a in existing):
                existing.append(account)

    def get_accounts(self, login_id: str | None = None) -> list[Account]:
        with self._lock:
            if login_id is not None:
                return list(self.accounts_by_login.get(login_id, []))
            out: list[Account] = []
            for accounts in self.accounts_by_login.values():
                out.extend(accounts)
            return out

    def add_reply_message(self, login_id: str, message: str) -> None:
        with self._lock:
            self.raw_reply_messages.append((login_id, message))

    def add_new_data(self, login_id: str, raw: str) -> None:
        ev = parse_order_event(login_id, raw)
        with self._lock:
            self.raw_new_data.append(ev)
            if ev.is_fill:
                self.raw_fill_reports.append(ev)
            else:
                self.raw_order_reports.append(ev)

    def get_open_orders(self) -> list[OrderEvent]:
        """
        Best-effort open-order view from reply reports.
        Keyed by OrderNo > KeyNo > SeqNo > OrderSeq; fills are excluded.

        OrderNo(委託書號) is stable across a single order's lifecycle reports
        (委託 N / 取消 C / 改量 U / 改價 P), so the cancel report overrides the
        original order report and the order drops out. SeqNo(欄位 47) must NOT be
        the primary key: it is a per-report sequence number — the N and C reports
        of the same order carry different SeqNo, so keying by it leaves the stale
        "委託" report behind and cancelled orders wrongly show as open.
        """
        with self._lock:
            latest: dict[str, OrderEvent] = {}
            for ev in self.raw_order_reports:
                key = ev.order_no or ev.key_no or ev.seq_no or ev.order_seq or ev.raw
                latest[key] = ev
            return [ev for ev in latest.values() if ev.is_open_like]

    def clear_stock_positions(self) -> None:
        with self._lock:
            self.raw_stock_positions.clear()

    def clear_future_positions(self) -> None:
        with self._lock:
            self.raw_future_positions.clear()

    def clear_future_rights(self) -> None:
        with self._lock:
            self.raw_future_rights.clear()

    def add_quote_connection(self, kind: int, code: int) -> None:
        with self._lock:
            self.quote_connections.append(QuoteConnectionEvent(kind=int(kind), code=int(code), raw=(int(kind), int(code))))

    def add_quote_server_time(self, hour: int, minute: int, second: int, total: int) -> None:
        with self._lock:
            self.quote_server_times.append((int(hour), int(minute), int(second), int(total)))

    def add_stock_list(self, market_no: int, raw: str) -> None:
        items = parse_stock_list_items(market_no, raw)
        with self._lock:
            self.raw_stock_lists[int(market_no)].extend(items)

    def get_stock_list(self, market_no: int | None = None) -> list[StockListItem]:
        with self._lock:
            if market_no is not None:
                return list(self.raw_stock_lists.get(int(market_no), []))
            out: list[StockListItem] = []
            for rows in self.raw_stock_lists.values():
                out.extend(rows)
            return out

    def clear_stock_list(self, market_no: int | None = None) -> None:
        with self._lock:
            if market_no is None:
                self.raw_stock_lists.clear()
            else:
                self.raw_stock_lists[int(market_no)].clear()

    def add_quote(self, quote: QuoteSnapshot) -> None:
        with self._lock:
            self.quote_events.append(quote)
            self.quotes_by_key[(quote.market_no, quote.stock_index)] = quote
            if quote.symbol:
                self.quotes_by_symbol[quote.symbol] = quote
                if quote.market_no is not None and quote.stock_index is not None:
                    self._backfill_quote_symbol_locked(
                        quote.symbol,
                        int(quote.market_no),
                        int(quote.stock_index),
                    )

    def _backfill_quote_symbol_locked(self, symbol: str, market_no: int, stock_index: int) -> None:
        key = (int(market_no), int(stock_index))
        for tick in self.tick_events:
            if not tick.symbol and (int(tick.market_no), int(tick.stock_index)) == key:
                tick.symbol = symbol
        for best5 in self.best5_events:
            if not best5.symbol and (int(best5.market_no), int(best5.stock_index)) == key:
                best5.symbol = symbol

    def get_latest_quotes(self) -> dict[str, QuoteSnapshot]:
        with self._lock:
            return dict(self.quotes_by_symbol)

    def get_latest_quote(self, symbol: str) -> QuoteSnapshot | None:
        with self._lock:
            return self.quotes_by_symbol.get(str(symbol))

    def add_tick(self, tick: QuoteTick) -> None:
        with self._lock:
            quote = self.quotes_by_key.get((tick.market_no, tick.stock_index))
            if quote and not tick.symbol:
                tick.symbol = quote.symbol
            self.tick_events.append(tick)

    def get_ticks(self, symbol: str | None = None, max_count: int | None = None) -> list[QuoteTick]:
        with self._lock:
            ticks = list(self.tick_events)
        if symbol is not None:
            ticks = [tick for tick in ticks if tick.symbol == str(symbol)]
        if max_count is not None:
            ticks = ticks[-int(max_count):]
        return ticks

    def get_ticks_by_key(self, market_no: int, stock_index: int, max_count: int | None = None) -> list[QuoteTick]:
        key = (int(market_no), int(stock_index))
        with self._lock:
            ticks = [
                tick
                for tick in self.tick_events
                if (int(tick.market_no), int(tick.stock_index)) == key
            ]
        if max_count is not None:
            ticks = ticks[-int(max_count):]
        return ticks

    def add_best5(self, best5: QuoteBest5) -> None:
        with self._lock:
            quote = self.quotes_by_key.get((best5.market_no, best5.stock_index))
            if quote and not best5.symbol:
                best5.symbol = quote.symbol
            self.best5_events.append(best5)
            self.best5_by_key[(best5.market_no, best5.stock_index)] = best5

    def get_latest_best5(self, symbol: str | None = None) -> list[QuoteBest5]:
        with self._lock:
            rows = list(self.best5_by_key.values())
        if symbol is not None:
            rows = [row for row in rows if row.symbol == str(symbol)]
        return rows

    def get_latest_best5_by_key(self, market_no: int, stock_index: int) -> QuoteBest5 | None:
        with self._lock:
            return self.best5_by_key.get((int(market_no), int(stock_index)))

    def add_kline(self, symbol: str, raw: str) -> None:
        record = parse_kline_record(symbol, raw)
        with self._lock:
            self.kline_records.append(record)

    def get_kline_records(self, symbol: str | None = None) -> list[KLineRecord]:
        with self._lock:
            records = list(self.kline_records)
        if symbol is not None:
            records = [record for record in records if record.symbol == str(symbol)]
        return records

    def clear_kline_records(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self.kline_records.clear()
            else:
                self.kline_records[:] = [record for record in self.kline_records if record.symbol != str(symbol)]

    def add_quote_error(self, message: str) -> None:
        with self._lock:
            self.quote_errors.append(str(message))

    def clear_quote_errors(self) -> None:
        with self._lock:
            self.quote_errors.clear()

    def clear_quote_data(self) -> None:
        """Clear cached quote/tick/best5/kline data. Connection events are kept so
        the STOCKS_READY state survives cache clears within one session."""
        with self._lock:
            self.quote_server_times.clear()
            self.raw_stock_lists.clear()
            self.quote_events.clear()
            self.quotes_by_key.clear()
            self.quotes_by_symbol.clear()
            self.tick_events.clear()
            self.best5_events.clear()
            self.best5_by_key.clear()
            self.kline_records.clear()
            self.quote_errors.clear()


# ----------------------------------------------------------------------
# COM event sinks (method names must match the COM event interfaces)
# ----------------------------------------------------------------------
class ReplyEventSink:
    def __init__(self, hub: "EventHub") -> None:
        self._hub = hub

    def OnReplyMessage(self, bstrUserID, bstrMessages):
        self._hub.add_reply_message(str(bstrUserID), str(bstrMessages))
        return -1

    def OnComplete(self, bstrUserID):
        self._hub.complete_logins.add(str(bstrUserID))

    def OnSolaceReplyConnection(self, bstrUserID, nErrorCode):
        self._hub.connection_status.append((str(bstrUserID), int(nErrorCode)))

    def OnSolaceReplyDisconnect(self, bstrUserID, nErrorCode):
        self._hub.connection_status.append((str(bstrUserID), int(nErrorCode)))

    def OnNewData(self, bstrUserID, bstrData):
        self._hub.add_new_data(str(bstrUserID), str(bstrData))


class OrderEventSink:
    def __init__(self, hub: "EventHub") -> None:
        self._hub = hub

    def OnAccount(self, bstrLogInID, bstrAccountData):
        self._hub.add_account(str(bstrLogInID), str(bstrAccountData))

    def OnRealBalanceReport(self, bstrData):
        self._hub.raw_stock_positions.append(str(bstrData))

    def OnOpenInterest(self, bstrData):
        self._hub.raw_future_positions.append(str(bstrData))

    def OnFutureRights(self, bstrData):
        self._hub.raw_future_rights.append(str(bstrData))

    def OnAsyncOrder(self, nThreadID, nCode, bstrMessage):
        self._hub.async_order_results.append((int(nThreadID), int(nCode), str(bstrMessage)))

    def OnProxyOrder(self, nStampID, nCode, bstrMessage):
        self._hub.proxy_order_results.append((int(nStampID), int(nCode), str(bstrMessage)))


class QuoteEventSink:
    def __init__(self, client: "CapitalClient") -> None:
        self._client = client

    @property
    def _hub(self):
        return self._client.hub

    def OnConnection(self, nKind, nCode):
        self._hub.add_quote_connection(int(nKind), int(nCode))

    def OnNotifyServerTime(self, sHour, sMinute, sSecond, nTotal):
        self._hub.add_quote_server_time(int(sHour), int(sMinute), int(sSecond), int(nTotal))

    def OnNotifyStockList(self, sMarketNo, bstrStockData):
        self._hub.add_stock_list(int(sMarketNo), str(bstrStockData))

    def OnNotifyCommodityListWithTypeNo(self, sMarketNo, bstrCommodityData):
        self._hub.add_stock_list(int(sMarketNo), str(bstrCommodityData))

    def OnNotifyQuoteLONG(self, sMarketNo, nStockidx):
        client = self._client
        try:
            stock = client.sk.SKSTOCKLONG()
            value = client.sk_quote.SKQuoteLib_GetStockByIndexLONG(int(sMarketNo), int(nStockidx), stock)
            stock_obj = value[0] if isinstance(value, tuple) and value else stock
            self._hub.add_quote(quote_snapshot_from_com(stock_obj, int(sMarketNo), int(nStockidx)))
        except Exception as exc:
            self._hub.add_quote_error(f"OnNotifyQuoteLONG: {exc}")

    def OnNotifyQuote(self, sMarketNo, nStockidx):
        self.OnNotifyQuoteLONG(sMarketNo, nStockidx)

    def OnNotifyTicksLONG(self, sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate):
        try:
            self._hub.add_tick(
                quote_tick_from_event(
                    int(sMarketNo), int(nStockidx), int(nPtr), int(lDate), int(lTimehms),
                    int(lTimemillismicros), nBid, nAsk, nClose, int(nQty), int(nSimulate)
                )
            )
        except Exception as exc:
            self._hub.add_quote_error(f"OnNotifyTicksLONG: {exc}")

    def OnNotifyTicks(self, sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate):
        self.OnNotifyTicksLONG(sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate)

    def OnNotifyHistoryTicksLONG(self, sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate):
        # First RequestTicks for a symbol backfills today's ticks through this event,
        # so tick queries work even after the trading session ends.
        try:
            self._hub.add_tick(
                quote_tick_from_event(
                    int(sMarketNo), int(nStockidx), int(nPtr), int(lDate), int(lTimehms),
                    int(lTimemillismicros), nBid, nAsk, nClose, int(nQty), int(nSimulate),
                    history=True,
                )
            )
        except Exception as exc:
            self._hub.add_quote_error(f"OnNotifyHistoryTicksLONG: {exc}")

    def OnNotifyHistoryTicks(self, sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate):
        self.OnNotifyHistoryTicksLONG(sMarketNo, nStockidx, nPtr, lDate, lTimehms, lTimemillismicros, nBid, nAsk, nClose, nQty, nSimulate)

    def OnNotifyBest5LONG(
        self,
        sMarketNo,
        nStockidx,
        nBestBid1,
        nBestBidQty1,
        nBestBid2,
        nBestBidQty2,
        nBestBid3,
        nBestBidQty3,
        nBestBid4,
        nBestBidQty4,
        nBestBid5,
        nBestBidQty5,
        nExtendBid,
        nExtendBidQty,
        nBestAsk1,
        nBestAskQty1,
        nBestAsk2,
        nBestAskQty2,
        nBestAsk3,
        nBestAskQty3,
        nBestAsk4,
        nBestAskQty4,
        nBestAsk5,
        nBestAskQty5,
        nExtendAsk,
        nExtendAskQty,
        nSimulate,
    ):
        try:
            self._hub.add_best5(
                quote_best5_from_event(
                    int(sMarketNo), int(nStockidx),
                    nBestBid1, int(nBestBidQty1), nBestBid2, int(nBestBidQty2),
                    nBestBid3, int(nBestBidQty3), nBestBid4, int(nBestBidQty4),
                    nBestBid5, int(nBestBidQty5), nExtendBid, int(nExtendBidQty),
                    nBestAsk1, int(nBestAskQty1), nBestAsk2, int(nBestAskQty2),
                    nBestAsk3, int(nBestAskQty3), nBestAsk4, int(nBestAskQty4),
                    nBestAsk5, int(nBestAskQty5), nExtendAsk, int(nExtendAskQty),
                    int(nSimulate),
                )
            )
        except Exception as exc:
            self._hub.add_quote_error(f"OnNotifyBest5LONG: {exc}")

    def OnNotifyBest5(self, *args):
        self.OnNotifyBest5LONG(*args)

    def OnNotifyKLineData(self, bstrStockNo, bstrData):
        self._hub.add_kline(str(bstrStockNo), str(bstrData))


# ----------------------------------------------------------------------
# CapitalClient
# ----------------------------------------------------------------------
SK_WARNING_LOGIN_ALREADY = 2003
# SKQuoteLib_IsConnected: 0 disconnected, 1 connected, 2 downloading stock data
QUOTE_CONNECTED = 1
QUOTE_DOWNLOADING = 2
# OnConnection kind for "stock data ready"; symbol queries should wait for this
SK_SUBJECT_CONNECTION_STOCKS_READY = 3003
# Official manual: GetOrderReport / GetFulfillReport queries must be >= 5s apart
REPORT_QUERY_INTERVAL_SEC = 5.5


class CapitalClient:
    """
    High-level SDK for Capital / 群益 SKCOM.dll.

    The implementation is intentionally Windows-only at runtime. Imports of comtypes and
    pythoncom are lazy so this package can still be inspected / type-checked elsewhere.
    """

    def __init__(
        self,
        config: CapitalConfig,
        *,
        enable_live_order: bool = False,
        async_order: bool = False,
        strict: bool = False,
    ) -> None:
        self.config = config
        self.enable_live_order = enable_live_order
        self.async_order = async_order
        self.strict = strict
        self.hub = EventHub()
        self._loaded = False
        self._last_report_query_at: float | None = None
        self._event_handlers: list[Any] = []
        self._comtypes = None
        self._pythoncom = None
        self.sk = None
        self.sk_center = None
        self.sk_reply = None
        self.sk_order = None
        self.sk_quote = None

    @classmethod
    def from_env(cls, **kwargs) -> "CapitalClient":
        return cls(CapitalConfig.from_env(), **kwargs)

    # ------------------------------------------------------------------
    # COM setup / event sinks
    # ------------------------------------------------------------------
    def load(self) -> "CapitalClient":
        if self._loaded:
            return self
        if sys.platform != "win32":
            raise CapitalApiNotLoaded("SKCOM.dll uses Windows COM; run this SDK on Windows.")

        import comtypes.client  # type: ignore
        import pythoncom  # type: ignore

        dll_file = Path(self.config.dll_path)
        if dll_file.name != str(dll_file) and not dll_file.is_file():
            raise CapitalApiNotLoaded(f"SKCOM.dll not found at CAPITAL_SKCOM_DLL={dll_file}")
        comtypes.client.GetModule(str(dll_file))
        import comtypes.gen.SKCOMLib as sk  # type: ignore

        self._comtypes = comtypes
        self._pythoncom = pythoncom
        self.sk = sk
        self.sk_center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
        self.sk_reply = comtypes.client.CreateObject(sk.SKReplyLib, interface=sk.ISKReplyLib)
        self.sk_order = comtypes.client.CreateObject(sk.SKOrderLib, interface=sk.ISKOrderLib)
        self.sk_quote = comtypes.client.CreateObject(sk.SKQuoteLib, interface=sk.ISKQuoteLib)

        get_events = comtypes.client.GetEvents
        self._event_handlers += [
            get_events(self.sk_reply, ReplyEventSink(self.hub)),
            get_events(self.sk_order, OrderEventSink(self.hub)),
            get_events(self.sk_quote, QuoteEventSink(self)),
        ]
        self._loaded = True
        return self

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def pump(self, seconds: float = 0.5) -> None:
        self._ensure_loaded()
        end = time.time() + seconds
        while time.time() < end:
            self._pythoncom.PumpWaitingMessages()
            time.sleep(0.02)

    def pump_forever(self, interval: float = 0.05) -> None:
        self._ensure_loaded()
        while True:
            self._pythoncom.PumpWaitingMessages()
            time.sleep(interval)

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------
    def get_message(self, code: int) -> str:
        self._ensure_loaded()
        try:
            return str(self.sk_center.SKCenterLib_GetReturnCodeMessage(int(code)))
        except Exception:
            return ""

    def _result(self, method: str, code: int, *, broker_message: str = "", raw: Any = None, dry_run: bool = False) -> ApiResult:
        result = ApiResult(method=method, code=int(code), message=self.get_message(int(code)) if not dry_run else "DRY_RUN", broker_message=broker_message, raw=raw, dry_run=dry_run)
        if self.strict and not result.ok:
            raise CapitalApiError(f"{method} failed: {result.code} {result.message} {broker_message}")
        return result

    def _result_from_return(self, method: str, value: Any) -> ApiResult:
        """comtypes may return the out-params as a tuple; the return code is the last int."""
        code = 0
        if isinstance(value, (tuple, list)):
            code = next((int(item) for item in reversed(value) if isinstance(item, int)), 0)
        elif isinstance(value, int):
            code = int(value)
        return self._result(method, code, raw=value)

    @staticmethod
    def _out_param(value: Any, default: Any) -> Any:
        """Extract the first out-param struct from a comtypes return value."""
        return value[0] if isinstance(value, (tuple, list)) and value else default

    # ------------------------------------------------------------------
    # Login / setup
    # ------------------------------------------------------------------
    def set_authority(self, authority: Authority | int | None = None) -> ApiResult:
        self._ensure_loaded()
        auth = self.config.authority if authority is None else Authority(authority)
        code = self.sk_center.SKCenterLib_SetAuthority(int(auth))
        return self._result("SKCenterLib_SetAuthority", code, raw=auth)

    def set_log_path(self, path: str | None = None) -> ApiResult:
        self._ensure_loaded()
        log_path = path or self.config.log_path
        if not log_path:
            return self._result("SKCenterLib_SetLogPath", 0, broker_message="no log path configured")
        code = self.sk_center.SKCenterLib_SetLogPath(log_path)
        return self._result("SKCenterLib_SetLogPath", code, raw=log_path)

    def _login_center(self) -> ApiResult:
        self._ensure_loaded()
        self.set_authority()
        self.set_log_path()
        code = self.sk_center.SKCenterLib_Login(self.config.user_id, self.config.password)
        return self._result("SKCenterLib_Login", code)

    def login_center(self, *, wait_sec: float = 0.5) -> ApiResult:
        """Login only through SKCenterLib; useful for quote-only workflows."""
        result = self._login_center()
        if wait_sec > 0:
            self.pump(wait_sec)
        return result

    @staticmethod
    def is_login_result_ok(result: ApiResult) -> bool:
        return result.ok or int(result.code) == SK_WARNING_LOGIN_ALREADY

    def login(self, *, read_cert: bool = True, connect_reply: bool = True, wait_sec: float = 2.0) -> ApiResult:
        result = self._login_center()
        self.initialize_order(read_cert=read_cert, wait_sec=wait_sec)
        if connect_reply:
            self.connect_reply(wait_sec=wait_sec)
        return result

    def initialize_order(self, *, read_cert: bool = True, wait_sec: float = 1.0) -> ApiResult:
        self._ensure_loaded()
        code = self.sk_order.SKOrderLib_Initialize()
        result = self._result("SKOrderLib_Initialize", code)
        if read_cert:
            cert_id = self.config.cert_id or self.config.user_id
            if hasattr(self.sk_order, "ReadCertByID"):
                cert_code = self.sk_order.ReadCertByID(cert_id)
                self._result("ReadCertByID", cert_code, raw=cert_id)
        self.sk_order.GetUserAccount()
        self.pump(wait_sec)
        return result

    def connect_reply(self, wait_sec: float = 1.0) -> ApiResult:
        self._ensure_loaded()
        if not hasattr(self.sk_reply, "SKReplyLib_ConnectByID"):
            return self._result("SKReplyLib_ConnectByID", -999, broker_message="method not available")
        code = self.sk_reply.SKReplyLib_ConnectByID(self.config.user_id)
        result = self._result("SKReplyLib_ConnectByID", code)
        self.pump(wait_sec)
        return result

    def get_accounts(self, wait_sec: float = 1.0):
        self._ensure_loaded()
        self.sk_order.GetUserAccount()
        self.pump(wait_sec)
        return self.hub.get_accounts(self.config.user_id)

    def _default_account(self, prefix: str | None = None) -> str:
        accounts = self.hub.get_accounts(self.config.user_id)
        if prefix:
            for acc in accounts:
                if acc.account_type.upper() == prefix.upper() or acc.full_account.upper().startswith(prefix.upper()):
                    return acc.full_account
        # Prefer trading accounts; the account list may also contain non-trading
        # rows (e.g. the bank/capital-pay account with an empty type).
        for wanted in ("TS", "TF"):
            for acc in accounts:
                if acc.account_type.upper() == wanted:
                    return acc.full_account
        for acc in accounts:
            if acc.account_type.strip():
                return acc.full_account
        if accounts:
            return accounts[0].full_account
        raise CapitalApiError("No account loaded. Call login() / get_accounts() first or pass account explicitly.")

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------
    @staticmethod
    def _symbols_text(symbols: str | Iterable[str]) -> str:
        if isinstance(symbols, str):
            return symbols.replace(" ", "")
        return ",".join(str(symbol).strip() for symbol in symbols if str(symbol).strip())

    def connect_quote(self, *, wait_sec: float = 5.0) -> ApiResult:
        """Enter the quote monitor and wait until stock data is ready (nKind=3003)."""
        self._ensure_loaded()
        use_long = hasattr(self.sk_quote, "SKQuoteLib_EnterMonitorLONG")
        method = "SKQuoteLib_EnterMonitorLONG" if use_long else "SKQuoteLib_EnterMonitor"

        try:
            if self.is_quote_connected() == QUOTE_CONNECTED:
                return self._result(method, 0, broker_message="quote monitor already connected")
        except Exception:
            pass

        code = self.sk_quote.SKQuoteLib_EnterMonitorLONG() if use_long else self.sk_quote.SKQuoteLib_EnterMonitor()
        result = self._result(method, code)
        if wait_sec > 0:
            self.wait_quote_connected(wait_sec)
        return result

    def disconnect_quote(self, *, wait_sec: float = 0.5) -> ApiResult:
        self._ensure_loaded()
        code = self.sk_quote.SKQuoteLib_LeaveMonitor()
        result = self._result("SKQuoteLib_LeaveMonitor", code)
        if wait_sec > 0:
            self.pump(wait_sec)
        return result

    def is_quote_connected(self) -> int:
        """SKQuoteLib_IsConnected: 0 disconnected, 1 connected, 2 downloading stock data."""
        self._ensure_loaded()
        return int(self.sk_quote.SKQuoteLib_IsConnected())

    def is_quote_ready(self) -> bool:
        """True once OnConnection reported SK_SUBJECT_CONNECTION_STOCKS_READY (nKind=3003)."""
        return any(
            event.kind == SK_SUBJECT_CONNECTION_STOCKS_READY
            for event in self.hub.quote_connections
        )

    def wait_quote_connected(self, timeout_sec: float = 2.0) -> bool:
        """Pump until the quote monitor reports STOCKS_READY (falls back to IsConnected==1)."""
        self._ensure_loaded()
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        connected = False
        while True:
            if self.is_quote_ready():
                return True
            try:
                connected = connected or self.is_quote_connected() == QUOTE_CONNECTED
            except Exception:
                pass

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return connected
            self.pump(min(0.1, max(0.02, remaining)))

    def request_stock_list(self, market_no: int, *, wait_sec: float = 2.0, clear: bool = True) -> list[StockListItem]:
        self._ensure_loaded()
        if clear:
            self.hub.clear_stock_list(int(market_no))
        code = self.sk_quote.SKQuoteLib_RequestStockList(int(market_no))
        self._result("SKQuoteLib_RequestStockList", code, raw=market_no)
        self.pump(wait_sec)
        return self.hub.get_stock_list(int(market_no))

    def subscribe_quotes(
        self,
        symbols: str | Iterable[str],
        *,
        page_no: int = 1,
        market_no: int | None = None,
        wait_sec: float = 0.0,
    ) -> ApiResult:
        """
        Subscribe realtime quotes (OnNotifyQuoteLONG) via SKQuoteLib_RequestStocks.

        Official V2.13.58 rules: page_no is fixed to 1 for regular users, at most
        100 symbols per call, and one SKQuoteLib instance may hold only ONE quote
        subscription page — calling again with page 1 REPLACES the subscribed set.
        A second page number returns 3006 SK_SUBJECT_QUOTE_PAGE_EXCEED.

        market_no is only for intraday odd-lot (5/6) and custom futures/options
        (9/10), which use SKQuoteLib_RequestStocksWithMarketNo. Regular markets
        (listed/OTC/futures/options, including spread symbols like TX08/09) must
        pass market_no=None.
        """
        self._ensure_loaded()
        symbol_text = self._symbols_text(symbols)
        if market_no is None:
            value = self.sk_quote.SKQuoteLib_RequestStocks(int(page_no), symbol_text)
            result = self._result_from_return("SKQuoteLib_RequestStocks", value)
        else:
            value = self.sk_quote.SKQuoteLib_RequestStocksWithMarketNo(int(page_no), int(market_no), symbol_text)
            result = self._result_from_return("SKQuoteLib_RequestStocksWithMarketNo", value)
        if wait_sec > 0:
            self.pump(wait_sec)
        return result

    def subscribe_ticks(
        self,
        symbol: str,
        *,
        page_no: int = 0,
        market_no: int | None = None,
        wait_sec: float = 0.0,
    ) -> ApiResult:
        """
        Subscribe ticks + best5 (OnNotifyTicksLONG / OnNotifyBest5LONG) via
        SKQuoteLib_RequestTicks. The first request for a symbol also backfills
        today's ticks through OnNotifyHistoryTicksLONG.

        Official rules: page numbers start from 0 and each page holds exactly one
        symbol; reusing a page replaces that page's symbol. market_no follows the
        same 5/6/9/10-only rule as subscribe_quotes.
        """
        self._ensure_loaded()
        if market_no is None:
            value = self.sk_quote.SKQuoteLib_RequestTicks(int(page_no), str(symbol))
            result = self._result_from_return("SKQuoteLib_RequestTicks", value)
        else:
            value = self.sk_quote.SKQuoteLib_RequestTicksWithMarketNo(int(page_no), int(market_no), str(symbol))
            result = self._result_from_return("SKQuoteLib_RequestTicksWithMarketNo", value)
        if wait_sec > 0:
            self.pump(wait_sec)
        return result

    def request_live_tick(
        self,
        symbol: str,
        *,
        page_no: int = 0,
        wait_sec: float = 0.0,
    ) -> ApiResult:
        """Like subscribe_ticks but WITHOUT today's tick backfill (live ticks only)."""
        self._ensure_loaded()
        value = self.sk_quote.SKQuoteLib_RequestLiveTick(int(page_no), str(symbol))
        result = self._result_from_return("SKQuoteLib_RequestLiveTick", value)
        if wait_sec > 0:
            self.pump(wait_sec)
        return result

    def cancel_ticks(self, symbol: str) -> ApiResult:
        self._ensure_loaded()
        code = self.sk_quote.SKQuoteLib_CancelRequestTicks(str(symbol))
        return self._result("SKQuoteLib_CancelRequestTicks", code, raw=symbol)

    def get_quote_snapshot(self, symbol: str, *, market_no: int | None = None, wait_sec: float = 0.2) -> QuoteSnapshot:
        self._ensure_loaded()
        stock = self.sk.SKSTOCKLONG()
        if market_no is not None and hasattr(self.sk_quote, "SKQuoteLib_GetStockByMarketAndNo"):
            method = "SKQuoteLib_GetStockByMarketAndNo"
            value = self.sk_quote.SKQuoteLib_GetStockByMarketAndNo(int(market_no), str(symbol), stock)
        else:
            method = "SKQuoteLib_GetStockByNoLONG"
            value = self.sk_quote.SKQuoteLib_GetStockByNoLONG(str(symbol), stock)
        result = self._result_from_return(method, value)
        quote = quote_snapshot_from_com(self._out_param(value, stock), market_no=market_no)
        if quote.has_data:
            # Empty getter results (e.g. before RequestStocks) must not overwrite
            # a good cached snapshot in the hub.
            self.hub.add_quote(quote)
        if wait_sec > 0:
            self.pump(wait_sec)
        if self.strict and not result.ok:
            raise CapitalApiError(f"{method} failed: {result.code} {result.message}")
        return quote

    def get_tick_by_index(
        self,
        market_no: int,
        stock_index: int,
        *,
        ptr: int,
        decimal_places: int = 2,
        symbol: str = "",
    ) -> QuoteTick | None:
        self._ensure_loaded()
        tick = self.sk.SKTICK()
        value = self.sk_quote.SKQuoteLib_GetTickLONG(int(market_no), int(stock_index), int(ptr), tick)
        result = self._result_from_return("SKQuoteLib_GetTickLONG", value)
        if not result.ok:
            self.hub.add_quote_error(f"SKQuoteLib_GetTickLONG failed: code={result.code} message={result.message}")
            if self.strict:
                raise CapitalApiError(f"SKQuoteLib_GetTickLONG failed: {result.code} {result.message}")
            return None
        row = quote_tick_from_struct(
            self._out_param(value, tick),
            int(market_no),
            int(stock_index),
            decimal_places=int(decimal_places),
            symbol=symbol,
        )
        self.hub.add_tick(row)
        return row

    def get_best5_by_index(
        self,
        market_no: int,
        stock_index: int,
        *,
        decimal_places: int = 2,
        symbol: str = "",
    ) -> QuoteBest5 | None:
        self._ensure_loaded()
        best5 = self.sk.SKBEST5()
        value = self.sk_quote.SKQuoteLib_GetBest5LONG(int(market_no), int(stock_index), best5)
        result = self._result_from_return("SKQuoteLib_GetBest5LONG", value)
        if not result.ok:
            self.hub.add_quote_error(f"SKQuoteLib_GetBest5LONG failed: code={result.code} message={result.message}")
            if self.strict:
                raise CapitalApiError(f"SKQuoteLib_GetBest5LONG failed: {result.code} {result.message}")
            return None
        row = quote_best5_from_struct(
            self._out_param(value, best5),
            int(market_no),
            int(stock_index),
            decimal_places=int(decimal_places),
            symbol=symbol,
        )
        self.hub.add_best5(row)
        return row

    def get_latest_quotes(self) -> dict[str, QuoteSnapshot]:
        return self.hub.get_latest_quotes()

    def get_latest_quote(self, symbol: str) -> QuoteSnapshot | None:
        return self.hub.get_latest_quote(symbol)

    def get_ticks(self, symbol: str | None = None, *, max_count: int | None = None) -> list[QuoteTick]:
        return self.hub.get_ticks(symbol=symbol, max_count=max_count)

    def get_latest_best5(self, symbol: str | None = None) -> list[QuoteBest5]:
        return self.hub.get_latest_best5(symbol=symbol)

    def request_kline(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        line_type: int = 4,
        out_type: int = 1,
        trade_session: int = 0,
        minute_number: int = 1,
        wait_sec: float = 3.0,
        clear: bool = True,
    ) -> list[KLineRecord]:
        """
        Request historical K-line data through SKQuoteLib_RequestKLineAMByDate.

        line_type follows the official sample: 0=minute, 4=day, 5=week, 6=month.
        out_type 1 is the newer callback format. trade_session 0=all, 1=AM.
        """
        self._ensure_loaded()
        if clear:
            self.hub.clear_kline_records(str(symbol))
        code = self.sk_quote.SKQuoteLib_RequestKLineAMByDate(
            str(symbol),
            int(line_type),
            int(out_type),
            int(trade_session),
            str(start_date),
            str(end_date),
            int(minute_number),
        )
        result = self._result("SKQuoteLib_RequestKLineAMByDate", code, raw={
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "line_type": line_type,
            "out_type": out_type,
            "trade_session": trade_session,
            "minute_number": minute_number,
        })
        if not result.ok:
            self.hub.add_quote_error(
                f"SKQuoteLib_RequestKLineAMByDate failed for {symbol}: code={result.code} message={result.message}"
            )
        self.pump(wait_sec)
        return self.hub.get_kline_records(str(symbol))

    # ------------------------------------------------------------------
    # Positions / rights / balances
    # ------------------------------------------------------------------
    def get_stock_positions(self, account: str | None = None, wait_sec: float = 2.0) -> list[StockPosition]:
        self._ensure_loaded()
        acc = account or self._default_account("TS")
        self.hub.clear_stock_positions()
        code = self.sk_order.GetRealBalanceReport(self.config.user_id, acc)
        self._result("GetRealBalanceReport", code, raw=acc)
        self.pump(wait_sec)
        return parse_many(self.hub.raw_stock_positions, parse_stock_position_raw)

    def _sync_report_query(self, method_name: str, account: str | None, n_format: int, *, retries: int = 1) -> list[OrderEvent]:
        """
        Run a blocking SKOrderLib report query (GetOrderReport / GetFulfillReport).

        The official manual requires >= 5 seconds between report queries. The
        interval must be spent PUMPING COM messages (not sleeping): without
        pumping, the component never marks the previous query finished and keeps
        answering M999. Retries once when the server still answers M999.
        """
        self._ensure_loaded()
        acc = account or self._default_account(None)
        method = getattr(self.sk_order, method_name)
        for attempt in range(int(retries) + 1):
            wait = self._report_query_wait_sec()
            if wait > 0:
                self.pump(wait)
            else:
                self.pump(0.1)
            self._last_report_query_at = time.monotonic()
            raw = str(method(self.config.user_id, acc, int(n_format)))
            try:
                return parse_report_query_result(self.config.user_id, raw)
            except RuntimeError:
                if attempt >= retries:
                    raise
        return []

    def _report_query_wait_sec(self) -> float:
        if self._last_report_query_at is None:
            return 0.0
        elapsed = time.monotonic() - self._last_report_query_at
        return max(0.0, REPORT_QUERY_INTERVAL_SEC - elapsed)

    def get_order_report(self, account: str | None = None, n_format: int = 3) -> list[OrderEvent]:
        """
        Query today's order reports synchronously (SKOrderLib.GetOrderReport).

        account must be a TS/TF trading account; None uses the default trading
        account. n_format: 1 all, 2 valid, 3 cancellable, 4 cancelled, 5 filled,
        6 failed, 7 merged by price, 8 merged by symbol, 9 reserved. Default 3
        returns the currently cancellable open orders, which is the most
        reliable open-order view (unlike the OnNewData cache it does not depend
        on connect time).
        """
        return self._sync_report_query("GetOrderReport", account, n_format)

    def get_fulfill_report(self, account: str | None = None, n_format: int = 1) -> list[OrderEvent]:
        """
        Query today's fill reports synchronously (SKOrderLib.GetFulfillReport).

        account must be a TS/TF trading account; None uses the default trading
        account. n_format: 1 full, 2 merged by book no, 3 merged by price,
        4 merged by symbol, 5 T+1 fills.
        """
        return self._sync_report_query("GetFulfillReport", account, n_format)

    def get_future_positions(self, account: str | None = None, n_format: int = 1, wait_sec: float = 2.0) -> list[FuturePosition]:
        self._ensure_loaded()
        acc = account or self._default_account("TF")
        self.hub.clear_future_positions()
        code = self.sk_order.GetOpenInterestGW(self.config.user_id, acc, int(n_format))
        self._result("GetOpenInterestGW", code, raw={"account": acc, "format": n_format})
        self.pump(wait_sec)
        return parse_many(self.hub.raw_future_positions, parse_future_position_raw)

    def get_future_rights(self, account: str | None = None, coin_type: FutureRightsCoinType | int = FutureRightsCoinType.TWD, wait_sec: float = 2.0) -> list[FutureRights]:
        self._ensure_loaded()
        acc = account or self._default_account("TF")
        self.hub.clear_future_rights()
        code = self.sk_order.GetFutureRights(self.config.user_id, acc, int(coin_type))
        self._result("GetFutureRights", code, raw={"account": acc, "coin_type": int(coin_type)})
        self.pump(wait_sec)
        return parse_many(self.hub.raw_future_rights, parse_future_rights_raw)

    def get_capital_pay_balance(self) -> CapitalPayBalance:
        self._ensure_loaded()
        if not hasattr(self.sk_order, "GetBalance"):
            raise CapitalApiError("SKOrderLib.GetBalance is not available in this SKCOM.dll version.")
        raw = self.sk_order.GetBalance(self.config.user_id)
        return parse_capital_pay_balance(str(raw))

    def get_open_orders(self):
        return self.hub.get_open_orders()

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def _normalize_send_result(self, method: str, value: Any) -> ApiResult:
        broker_message = ""
        code = 0
        if isinstance(value, tuple):
            # Official PythonExampleV2 uses: bstrMessage, nCode = Send...
            if len(value) >= 2 and isinstance(value[1], int):
                broker_message, code = str(value[0]), int(value[1])
            elif len(value) >= 2 and isinstance(value[0], int):
                code, broker_message = int(value[0]), str(value[1])
            else:
                broker_message = str(value)
        elif isinstance(value, int):
            code = int(value)
        else:
            broker_message = str(value)
        return self._result(method, code, broker_message=broker_message, raw=value)

    def _dry_run_result(self, method: str, payload: dict[str, Any]) -> ApiResult | None:
        """Return a DRY_RUN result when live orders are disabled; None when live."""
        if self.enable_live_order:
            return None
        return ApiResult(method=method, code=0, message="DRY_RUN", broker_message="live order disabled; nothing sent", raw=payload, dry_run=True)

    def place_stock_order(
        self,
        *,
        symbol: str,
        side: Side | int | str,
        qty: int,
        price: str | float | int = "0",
        account: str | None = None,
        price_type: StockPriceType | int = StockPriceType.LIMIT,
        trade_type: TradeType | int = TradeType.ROD,
        flag: StockFlag | int = StockFlag.CASH,
        period: StockPeriod | int = StockPeriod.REGULAR,
        prime: StockPrime | int = StockPrime.LISTED_OTC,
    ) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account("TS")
        side_val = self._side_value(side)
        payload = dict(symbol=symbol, side=side_val, qty=qty, price=str(price), account=acc, price_type=int(price_type))
        dry = self._dry_run_result("SendStockOrder", payload)
        if dry:
            return dry
        order = self.sk.STOCKORDER()
        order.bstrFullAccount = acc
        order.bstrStockNo = symbol
        order.sPrime = int(prime)
        order.sPeriod = int(period)
        order.sFlag = int(flag)
        order.sBuySell = side_val
        order.bstrPrice = str(price)
        order.nQty = int(qty)
        order.nTradeType = int(trade_type)
        order.nSpecialTradeType = int(price_type)
        value = self.sk_order.SendStockOrder(self.config.user_id, self.async_order, order)
        return self._normalize_send_result("SendStockOrder", value)

    def place_stock_limit(self, symbol: str, side: Side | int | str, qty: int, price: str | float | int, **kwargs) -> ApiResult:
        return self.place_stock_order(symbol=symbol, side=side, qty=qty, price=price, price_type=StockPriceType.LIMIT, **kwargs)

    def place_stock_market(self, symbol: str, side: Side | int | str, qty: int, price: str | float | int = "0", **kwargs) -> ApiResult:
        return self.place_stock_order(symbol=symbol, side=side, qty=qty, price=price, price_type=StockPriceType.MARKET, **kwargs)

    def place_stock_odd_lot_order(self, *, symbol: str, side: Side | int | str, qty: int, price: str | float | int, account: str | None = None) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account("TS")
        side_val = self._side_value(side)
        payload = dict(symbol=symbol, side=side_val, qty=qty, price=str(price), account=acc, period="INTRADAY_ODD_LOT")
        dry = self._dry_run_result("SendStockOddLotOrder", payload)
        if dry:
            return dry
        order = self.sk.STOCKORDER()
        order.bstrFullAccount = acc
        order.bstrStockNo = symbol
        order.sPeriod = int(StockPeriod.INTRADAY_ODD_LOT)
        order.sFlag = int(StockFlag.CASH)
        order.sBuySell = side_val
        order.bstrPrice = str(price)
        order.nQty = int(qty)
        value = self.sk_order.SendStockOddLotOrder(self.config.user_id, self.async_order, order)
        return self._normalize_send_result("SendStockOddLotOrder", value)

    def place_future_order(
        self,
        *,
        symbol: str,
        side: Side | int | str,
        qty: int,
        price: str | float | int = "M",
        account: str | None = None,
        trade_type: TradeType | int = TradeType.ROD,
        day_trade: FuturesDayTrade | int = FuturesDayTrade.NO,
        new_close: FuturesNewClose | int = FuturesNewClose.AUTO,
        reserved: FuturesReserved | int = FuturesReserved.REGULAR,
    ) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account("TF")
        side_val = self._side_value(side)
        payload = dict(symbol=symbol, side=side_val, qty=qty, price=str(price), account=acc, new_close=int(new_close))
        dry = self._dry_run_result("SendFutureOrderCLR", payload)
        if dry:
            return dry
        order = self.sk.FUTUREORDER()
        order.bstrFullAccount = acc
        order.bstrStockNo = symbol
        order.sTradeType = int(trade_type)
        order.sBuySell = side_val
        order.sDayTrade = int(day_trade)
        order.sNewClose = int(new_close)
        order.bstrPrice = str(price)
        order.nQty = int(qty)
        order.sReserved = int(reserved)
        value = self.sk_order.SendFutureOrderCLR(self.config.user_id, self.async_order, order)
        return self._normalize_send_result("SendFutureOrderCLR", value)

    def place_future_limit(self, symbol: str, side: Side | int | str, qty: int, price: str | float | int, **kwargs) -> ApiResult:
        return self.place_future_order(symbol=symbol, side=side, qty=qty, price=price, **kwargs)

    def place_future_market(self, symbol: str, side: Side | int | str, qty: int, **kwargs) -> ApiResult:
        return self.place_future_order(symbol=symbol, side=side, qty=qty, price="M", trade_type=TradeType.IOC, **kwargs)

    def cancel_order_by_seq(self, seq_no: str, account: str | None = None) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account(None)
        dry = self._dry_run_result("CancelOrderBySeqNo", dict(seq_no=seq_no, account=acc))
        if dry:
            return dry
        value = self.sk_order.CancelOrderBySeqNo(self.config.user_id, self.async_order, acc, str(seq_no))
        return self._normalize_send_result("CancelOrderBySeqNo", value)

    def cancel_order_by_book(self, book_no: str, account: str | None = None) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account(None)
        dry = self._dry_run_result("CancelOrderByBookNo", dict(book_no=book_no, account=acc))
        if dry:
            return dry
        value = self.sk_order.CancelOrderByBookNo(self.config.user_id, self.async_order, acc, str(book_no))
        return self._normalize_send_result("CancelOrderByBookNo", value)

    def cancel_orders_by_symbol(self, symbol: str = "", account: str | None = None) -> ApiResult:
        """If symbol is empty, official sample says it cancels all orders for the account."""
        self._ensure_loaded()
        acc = account or self._default_account(None)
        dry = self._dry_run_result("CancelOrderByStockNo", dict(symbol=symbol, account=acc))
        if dry:
            return dry
        value = self.sk_order.CancelOrderByStockNo(self.config.user_id, self.async_order, acc, str(symbol))
        return self._normalize_send_result("CancelOrderByStockNo", value)

    def decrease_order_by_seq(self, seq_no: str, decrease_qty: int, account: str | None = None) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account(None)
        dry = self._dry_run_result("DecreaseOrderBySeqNo", dict(seq_no=seq_no, decrease_qty=decrease_qty, account=acc))
        if dry:
            return dry
        value = self.sk_order.DecreaseOrderBySeqNo(self.config.user_id, self.async_order, acc, str(seq_no), int(decrease_qty))
        return self._normalize_send_result("DecreaseOrderBySeqNo", value)

    def correct_price_by_seq(self, seq_no: str, price: str | float | int, account: str | None = None, trade_type: TradeType | int = TradeType.ROD) -> ApiResult:
        self._ensure_loaded()
        acc = account or self._default_account(None)
        dry = self._dry_run_result("CorrectPriceBySeqNo", dict(seq_no=seq_no, price=str(price), account=acc, trade_type=int(trade_type)))
        if dry:
            return dry
        value = self.sk_order.CorrectPriceBySeqNo(self.config.user_id, self.async_order, acc, str(seq_no), str(price), int(trade_type))
        return self._normalize_send_result("CorrectPriceBySeqNo", value)

    @staticmethod
    def _side_value(side: Side | int | str) -> int:
        if isinstance(side, (Side, int)):
            return int(side)
        text = str(side).lower()
        if text in {"buy", "b", "long", "買", "買進"}:
            return int(Side.BUY)
        if text in {"sell", "s", "short", "賣", "賣出"}:
            return int(Side.SELL)
        raise ValueError(f"Unknown side: {side!r}")
