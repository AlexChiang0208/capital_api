"""帳務唯讀快照(read-only snapshot)。

把所有「查詢類」API(get_*)彙整成方便轉 DataFrame 的 dict,不含任何下單/改單/刪單。

效能提醒:時間幾乎都花在每個查詢的 `client.pump(wait_sec)` 固定等待上(跟資料量無關)。
所以本模組:
  1. 拆成獨立小函式(帳戶/餘額/現貨庫存/期貨未平倉/期貨權益/掛單),可單獨呼叫。
  2. `fetch_account_snapshot(..., include=...)` 只查有選到的區塊,沒選到的不查不等待。

注意:群益查詢函式會序列化處理,連續發查詢可能觸發 1019 SK_ERROR_QUERY_IN_PROCESSING
而掉資料,因此本模組一律逐項查詢(前一項收完才送下一項)。
"""
from __future__ import annotations

import warnings
from dataclasses import asdict
from typing import Any, Callable, Iterable

from .models import FutureRightsCoinType

# OnNewData 委託回報的市場別(field[1])分類:證券 vs 期貨(官方 4-3-g 定義)。
# TS 證券 / TA 盤後 / TL 零股 / TP 興櫃 / TC 盤中零股 / OS 複委託
# TF 期貨 / TO 選擇權 / OF 海期 / OO 海選
# 分類不到的代碼仍保留在 open_orders 總表。
STOCK_MARKET_TYPES = {"TS", "TA", "TL", "TP", "TC", "OS"}
FUTURE_MARKET_TYPES = {"TF", "TO", "OF", "OO"}

# fetch_account_snapshot 的所有區塊(section)名稱。
SNAPSHOT_SECTIONS = (
    "accounts", "balance",
    "stock_positions", "future_positions", "future_rights",
    "open_orders",
)

# include 參數可用的群組別名 -> 展開成實際 section。大小寫不拘,也收常見中文。
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    # 依類型
    "account": ("accounts", "balance"), "帳戶": ("accounts", "balance"), "帳戶資訊": ("accounts", "balance"),
    "balance": ("balance",), "餘額": ("balance",), "購買力": ("balance",),
    "positions": ("stock_positions", "future_positions", "future_rights"),
    "inventory": ("stock_positions", "future_positions", "future_rights"),
    "庫存": ("stock_positions", "future_positions", "future_rights"),
    "庫存資訊": ("stock_positions", "future_positions", "future_rights"),
    "orders": ("open_orders",), "掛單": ("open_orders",), "掛單資訊": ("open_orders",),
    # 依市場
    "stock": ("stock_positions",), "spot": ("stock_positions",), "現貨": ("stock_positions",),
    "future": ("future_positions", "future_rights"), "futures": ("future_positions", "future_rights"),
    "期貨": ("future_positions", "future_rights"),
}


def is_real_row(raw: str) -> bool:
    """過濾掉 SKCOM 的結束標記列(## 開頭)與「查無資料」列。"""
    return not (raw.startswith("##") or "查無資料" in raw)


def _rows_to_dict(rows: list[dict], key_fn: Callable[[dict], Any]) -> dict:
    """list[dict] -> dict of dict(方便 pd.DataFrame.from_dict(table, orient='index'))。

    key 為空或重複時自動加序號後綴,避免互相覆蓋。
    """
    out: dict[str, Any] = {}
    for i, row in enumerate(rows):
        key = str(key_fn(row) or "").strip() or f"row{i}"
        if key in out:
            key = f"{key}#{i}"
        out[key] = row
    return out


def _order_to_row(o) -> dict:
    """OrderEvent -> 乾淨的 dict(去掉內部 fields list,保留 raw 方便比對)。"""
    return {
        "market_type": o.market_type,
        "symbol": o.symbol,
        "side": o.side,
        "report_type": o.report_type,
        "order_no": o.order_no,
        "seq_no": o.seq_no,
        "price": o.price,
        "qty": o.qty,
        "before_qty": o.before_qty,
        "after_qty": o.after_qty,
        "date": o.date,
        "time": o.time,
        "error_msg": o.error_msg,
        "raw": o.raw,
    }


def _err(exc: Exception) -> dict:
    """把例外包成 dict of dict 形式的錯誤格(仍可轉 DataFrame)。"""
    return {"error": {"message": str(exc)}}


def _fmt_positions(rows, key_field: str) -> dict:
    """list[部位/權益 dataclass] -> dict of dict(過濾結束標記/查無資料)。"""
    return _rows_to_dict(
        [asdict(p) for p in rows if is_real_row(p.raw)],
        lambda r: r.get(key_field),
    )


def _order_table(order_events) -> dict:
    """list[OrderEvent] -> dict of dict。key 用 seq_no(cancel_order_by_seq 可直接用)。"""
    return _rows_to_dict(
        [_order_to_row(o) for o in order_events],
        lambda r: r.get("seq_no") or r.get("order_no"),
    )


def _split_order_tables(order_events) -> tuple[dict, dict, dict]:
    """list[OrderEvent] -> (全部, 證券, 期貨) 三張 dict of dict。"""
    rows = [_order_to_row(o) for o in order_events]
    key = lambda r: r.get("seq_no") or r.get("order_no")
    return (
        _rows_to_dict(rows, key),
        _rows_to_dict([o for o in rows if o["market_type"] in STOCK_MARKET_TYPES], key),
        _rows_to_dict([o for o in rows if o["market_type"] in FUTURE_MARKET_TYPES], key),
    )


def _resolve_sections(include: Iterable[str] | str | None) -> list[str]:
    """把 include(section / 群組別名 / None)展開成不重複的 section 清單。"""
    if include is None:
        return list(SNAPSHOT_SECTIONS)
    if isinstance(include, str):
        include = [include]
    out: list[str] = []
    for item in include:
        key = str(item).strip().lower()
        for sec in _SECTION_ALIASES.get(key, (key,)):
            if sec not in SNAPSHOT_SECTIONS:
                raise ValueError(
                    f"未知的 snapshot 區塊: {item!r};可用:{SNAPSHOT_SECTIONS} 或群組別名 {sorted(_SECTION_ALIASES)}"
                )
            if sec not in out:
                out.append(sec)
    return out


# ── 各自獨立、可單獨呼叫的小函式(只等自己那塊的時間)──────────────────

def fetch_accounts(client, *, wait_sec: float = 0) -> dict:
    """帳號清單(dict of dict)。帳號在 login 時已載入,預設 wait_sec=0 幾乎不等待。"""
    return _rows_to_dict(
        [asdict(a) for a in client.get_accounts(wait_sec=wait_sec)],
        lambda r: r.get("account_type") or r.get("full_account"),
    )


def fetch_balance(client) -> dict:
    """一戶通餘額 / 購買力(單筆 dict)。GetBalance 是同步呼叫,幾乎瞬間。"""
    pay = client.get_capital_pay_balance()
    return {
        "has_capital_pay": pay.has_capital_pay,
        "balance": pay.balance,                          # 帳戶餘額
        "withdrawable_amount": pay.withdrawable_amount,  # 可動用 / 可提領
        "today_buying_power": pay.today_buying_power,    # 今日購買力(買力)
    }


def fetch_stock_positions(client, *, account: str | None = None, wait_sec: float = 3) -> dict:
    """當前股票(現貨)庫存(dict of dict,key=symbol)。"""
    return _fmt_positions(client.get_stock_positions(account=account, wait_sec=wait_sec), "symbol")


def fetch_future_positions(client, *, account: str | None = None, wait_sec: float = 3) -> dict:
    """當前期貨未平倉(dict of dict,key=symbol)。"""
    return _fmt_positions(client.get_future_positions(account=account, wait_sec=wait_sec), "symbol")


def fetch_future_rights(client, *, account: str | None = None,
                        coin_type: FutureRightsCoinType | int = FutureRightsCoinType.TWD,
                        wait_sec: float = 3) -> dict:
    """期貨權益數(dict of dict,key=account_no)。"""
    return _fmt_positions(
        client.get_future_rights(account=account, coin_type=coin_type, wait_sec=wait_sec), "account_no")


def fetch_open_orders(client, *, market: str = "all", reply_pump_sec: float = 3) -> dict:
    """當前掛單(dict of dict)。market: 'all' / 'stock'(現貨)/ 'future'(期貨)。

    掛單來自 OnNewData 回報 cache(需 connect_reply=True 並 pump 收回報),
    已依成交(D)/取消(C)回報沖銷;不論 market 為何都要等回報,拆 market 只是過濾輸出。
    若要不依賴連線時間的完整掛單,改用 fetch_order_reports(n_format=3 可消單,
    盤中零股也查得到)。
    """
    client.pump(reply_pump_sec)
    orders = client.get_open_orders()
    m = str(market).strip().lower()
    if m in ("stock", "spot", "現貨"):
        orders = [o for o in orders if o.market_type in STOCK_MARKET_TYPES]
    elif m in ("future", "futures", "期貨"):
        orders = [o for o in orders if o.market_type in FUTURE_MARKET_TYPES]
    return _order_table(orders)


def _report_accounts(client, account: str | None) -> list[str | None]:
    """account=None 時展開成所有 TS/TF 交易帳號;指定帳號則只查該帳號。"""
    if account is not None:
        return [account]
    trading = [a.full_account for a in client.get_accounts() if a.account_type in ("TS", "TF")]
    return trading or [None]


def _query_order_to_row(r) -> dict:
    """QueryOrderReport -> 乾淨的 dict(官方 5-4-4 查詢格式)。"""
    return {
        "market": r.market, "product": r.product, "exchange": r.exchange,
        "symbol": r.symbol, "buy_sell": r.buy_sell,
        "status": r.status, "status_name": r.status_name, "session": r.session,
        "stock_flag": r.stock_flag, "trade_type": r.trade_type, "price_type": r.price_type,
        "price": r.price, "orig_qty": r.orig_qty, "filled_qty": r.filled_qty,
        "remaining_qty": r.remaining_qty, "avg_fill_price": r.avg_fill_price,
        "order_no": r.order_no, "seq_no": r.seq_no,
        "order_date": r.order_date, "order_time": r.order_time, "raw": r.raw,
    }


def _query_fill_to_row(r) -> dict:
    """QueryFillReport -> 乾淨的 dict(官方 5-4-5 查詢格式,含預估費稅)。"""
    return {
        "market": r.market, "product": r.product, "exchange": r.exchange,
        "symbol": r.symbol, "buy_sell": r.buy_sell, "session": r.session,
        "stock_flag": r.stock_flag, "price": r.price, "qty": r.qty,
        "amount": r.amount, "fee": r.fee, "tax": r.tax,
        "order_no": r.order_no, "fill_seq": r.fill_seq,
        "fill_date": r.fill_date, "fill_time": r.fill_time, "raw": r.raw,
    }


def fetch_order_reports(client, *, account: str | None = None, n_format: int = 3) -> dict:
    """當日委託回報查詢(同步 GetOrderReport,dict of dict,官方 5-4-4 查詢格式)。

    account=None 會依序查所有 TS/TF 交易帳號並合併(每個帳號間隔 5 秒,
    SDK 自動以 pump 等待)。key 為委託書號,value 含 status_name(全部成交/
    委託成功/部分成交...)、filled_qty、remaining_qty 等。
    n_format: 1 全部 / 2 有效 / 3 可消 / 4 已消 / 5 已成 / 6 失敗 /
              7 合併同價格 / 8 合併同商品 / 9 預約。預設 3(可取消的掛單)。
    盤中零股(盤別 F)也查得到,實測與官方欄位表逐欄核對。
    """
    rows = []
    for acc in _report_accounts(client, account):
        rows.extend(client.get_order_report(account=acc, n_format=n_format))
    return _rows_to_dict([_query_order_to_row(r) for r in rows],
                         lambda r: r.get("order_no") or r.get("seq_no"))


def fetch_fulfill_reports(client, *, account: str | None = None, n_format: int = 1) -> dict:
    """當日成交回報查詢(同步 GetFulfillReport,dict of dict,官方 5-4-5 查詢格式)。

    account=None 會依序查所有 TS/TF 交易帳號並合併(每個帳號間隔 5 秒,
    SDK 自動以 pump 等待)。value 含成交價/量/價金與預估手續費、交易稅。
    n_format: 1 完整 / 2 合併同書號 / 3 合併同價格 / 4 合併同商品 / 5 T+1成交。
    盤中零股成交也查得到(盤別 F)。
    """
    rows = []
    for acc in _report_accounts(client, account):
        rows.extend(client.get_fulfill_report(account=acc, n_format=n_format))
    return _rows_to_dict([_query_fill_to_row(r) for r in rows],
                         lambda r: r.get("fill_seq") or r.get("order_no"))


def fetch_account_snapshot(
    client,
    *,
    include: Iterable[str] | str | None = None,
    parallel: bool = False,
    stock_account: str | None = None,
    future_account: str | None = None,
    coin_type: FutureRightsCoinType | int = FutureRightsCoinType.TWD,
    wait_sec: float = 3,
    reply_pump_sec: float = 3,
) -> dict:
    """彙整指定的「唯讀」帳務資訊成一個 dict,方便直接轉成 DataFrame。

    僅呼叫查詢類 API(get_*),不會送出任何下單/改單/刪單。每個區塊各自
    try/except,單項失敗不會中斷整包(失敗記成 ``{"error": {...}}``)。

    使用前請先 ``client.login(read_cert=True, connect_reply=True)``。

    Parameters
    ----------
    include:
        要查哪些區塊,沒選到的就不查、不等待(這是加速的關鍵)。
        可給 section 名稱:``accounts`` / ``balance`` / ``stock_positions`` /
        ``future_positions`` / ``future_rights`` / ``open_orders``;
        或群組別名:``account``(帳戶+餘額)/ ``positions``(庫存)/ ``orders``(掛單)/
        ``stock``(現貨庫存)/ ``future``(期貨庫存+權益)。``None`` = 全部。
    parallel:
        已停用。群益查詢會序列化處理,同時發多筆查詢會觸發
        1019 SK_ERROR_QUERY_IN_PROCESSING 而掉資料,一律逐項查詢。

    回傳 dict(只包含有選到的 key;多為 dict of dict,可
    ``pd.DataFrame.from_dict(t, orient='index')``):
        accounts / capital_pay_balance / stock_positions / future_positions /
        future_rights / open_orders / stock_open_orders / future_open_orders
    """
    if parallel:
        warnings.warn(
            "parallel=True 已停用:群益查詢序列化處理,並行會觸發 1019 而掉資料;改用逐項查詢。",
            stacklevel=2,
        )
    sections = _resolve_sections(include)
    snapshot: dict = {}

    if "accounts" in sections:
        try:
            snapshot["accounts"] = fetch_accounts(client)
        except Exception as exc:
            snapshot["accounts"] = _err(exc)
    if "balance" in sections:
        try:
            snapshot["capital_pay_balance"] = fetch_balance(client)
        except Exception as exc:
            snapshot["capital_pay_balance"] = {"error": str(exc)}
    if "stock_positions" in sections:
        try:
            snapshot["stock_positions"] = fetch_stock_positions(client, account=stock_account, wait_sec=wait_sec)
        except Exception as exc:
            snapshot["stock_positions"] = _err(exc)
    if "future_positions" in sections:
        try:
            snapshot["future_positions"] = fetch_future_positions(client, account=future_account, wait_sec=wait_sec)
        except Exception as exc:
            snapshot["future_positions"] = _err(exc)
    if "future_rights" in sections:
        try:
            snapshot["future_rights"] = fetch_future_rights(
                client, account=future_account, coin_type=coin_type, wait_sec=wait_sec)
        except Exception as exc:
            snapshot["future_rights"] = _err(exc)
    if "open_orders" in sections:
        try:
            client.pump(reply_pump_sec)
            (snapshot["open_orders"], snapshot["stock_open_orders"],
             snapshot["future_open_orders"]) = _split_order_tables(client.get_open_orders())
        except Exception as exc:
            snapshot["open_orders"] = _err(exc)
            snapshot["stock_open_orders"] = {}
            snapshot["future_open_orders"] = {}

    return snapshot
