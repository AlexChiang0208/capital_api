"""台指期 (大台 TX / 小台 MTX / 微台 TM) 操作面板邏輯: 查詢包裝、防呆下單、刪單後驗證、emoji 輸出。

給 strategy/台指建倉.py 用: 主程式只留參數與呼叫, 客製邏輯放這裡; 底層規格全部在 capital_api_sdk:
  商品代碼三套 namespace 與轉碼      capital_api_sdk.taifex (contract_of / to_report_code / point_value ...)
  回報 / 庫存 / 權益數欄位與中文名    capital_api_sdk.models (QUERY_ORDER_FIELDS / FUTURE_POSITION_FIELDS / FUTURE_RIGHTS_FIELDS)
  一次性報價 (訂閱 -> 新快照 -> 退訂)  capital_api_sdk.fetch_latest_quotes
  帳務查詢 5 秒間隔 / 沒回應就丟例外   CapitalClient.get_future_positions / get_future_rights
  3030 報價重連                        CapitalClient.reconnect_quote

TaifexTrader 功能一覽:
  connect()        登入 + 找期貨帳號, 印出環境與 dry-run / 實單狀態; 同一 kernel 重跑連線格會重用連線
  rights()         資金: 權益數 / 可用餘額 / 保證金 / 浮動損益 (41 欄)
  positions()      庫存: 期貨未平倉
  open_orders()    掛單: 當前可刪掛單 (symbol 欄 = 交易所契約代碼, 刪單就用它)
                   三個查詢回傳的表欄名都是「中文 english」, 直接 .T 看; 程式要用英文欄名請走 query_*()
  quote(symbol)    報價: 商品是否存在 + 最後價 + b1/a1 + 漲跌停; 訂閱被拒 (3030) 自動重連一次
  place_limit()    下限價單: check_order() 全部通過才送
  cancel(symbol)   刪掉某商品在該帳號的所有掛單: 先查有沒有, 再刪, 再驗證
  cancel_all()     刪掉該帳號全部委託, 要 confirm=True 才動作

商品代碼速查 (2026-09 實查, 每次換月會變, 用 quote() 確認):
  報價 / 下單: 大台 TX09 / TX00, 小台 MTX09 / MTX00, 微台 TM2609 / TM0000 (價差 TX09/10 與 TX00AM 不給下)
  回報 / 刪單: TXFI6 / MXFI6 / TMFI6 (月碼 A~L + 西元年尾數); cancel() 會自動把 MTX09 轉成 MXFI6
  庫存表:     微台顯示 TM09 (TM + MM)
  目前只收錄大台 / 小台 / 微台。其他商品 (電子期、金融期、個股期貨、選擇權...) contract_of() 一律認不得:
  下單會被商品閘門擋下, 庫存 / 掛單裡出現時算不出名目 (⚠️ 或依 block_unknown_symbols 擋單)。
  要加新商品請改 capital_api_sdk/taifex.py 的 CONTRACTS, 每個代碼格式都要實機確認 (檔頭有步驟)。

輸出標示: ✅ 通過  ℹ️ 資訊  ⚠️ 警告(不擋)  🚫 防呆擋下(沒送)  ❌ API 錯誤  🧪 dry-run  📤 已送出
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# 這個模組放在 strategy/useful_fuction/, capital_api_sdk 在專案根目錄。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from capital_api_sdk import (  # noqa: E402
    FUTURE_POSITION_FIELDS,
    FUTURE_RIGHTS_FIELDS,
    FUTURE_RIGHTS_TEXT_FIELDS,
    QUERY_ORDER_FIELDS,
    ApiResult,
    CapitalClient,
    FuturesNewClose,
    FuturesReserved,
    Side,
    TradeType,
    fetch_future_positions,
    fetch_future_rights,
    fetch_latest_quotes,
    fetch_order_reports,
    normalize_side,
)
from capital_api_sdk.taifex import CONTRACTS, contract_of, is_orderable, point_value, to_report_code  # noqa: E402

__all__ = [
    "RiskLimits", "Quote", "Exposure", "GuardResult", "OrderOutcome", "TaifexTrader",
    "RIGHTS_LABELS", "POSITION_LABELS", "ORDER_LABELS",
    "to_decimal", "format_price", "notional", "net_exposure", "check_order", "print_guard",
    "query_rights", "query_positions", "query_open_orders", "query_quote", "futures_account",
    "report_result", "say",
    # 底層規格, 從 SDK 轉出來給面板 / 測試直接用
    "CONTRACTS", "contract_of", "is_orderable", "point_value", "to_report_code", "normalize_side",
    "Side", "TradeType", "FuturesNewClose", "FuturesReserved",
]


# ======================================================================
# 輸出
# ======================================================================
def say(text: str) -> None:
    """print 的安全版: 終端機是 cp950 時 emoji 印不出來, 改成 ? 而不是整行炸掉。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _fmt_money(value: Any) -> str:
    """數字加千分位, 轉不成數字就原樣回傳。"""
    try:
        return f"{Decimal(str(value)):,.0f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _labels(fields: dict[str, str]) -> dict[str, str]:
    """SDK 的欄名對照 (english -> 中文) -> 面板欄名「中文 english」。"""
    return {name: f"{chinese} {name}" for name, chinese in fields.items()} | {"raw": "原始字串 raw"}


RIGHTS_LABELS = _labels(FUTURE_RIGHTS_FIELDS)
POSITION_LABELS = _labels(FUTURE_POSITION_FIELDS)
ORDER_LABELS = _labels(QUERY_ORDER_FIELDS)


# ======================================================================
# 數值 / 名目價值
# ======================================================================
def to_decimal(value: Any) -> Decimal | None:
    """字串 / 數字 -> Decimal; None / 空字串 / 非數字 -> None。價格用 Decimal 避免 float 尾差。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def format_price(price: Decimal) -> str:
    """Decimal -> 送單用字串, 去掉多餘的 0: 42500.0 -> "42500", 42500.5 -> "42500.5"。"""
    text = format(price.normalize(), "f")
    return text if text else "0"


def notional(symbol: str, price: Any, qty: Any, side: Any) -> Decimal:
    """帶號名目價值 = 價格 x 契約乘數 x 口數, 買 +, 賣 -。

    風控用: 認不得的商品、算不出的價格 / 口數 / 買賣別直接丟 ValueError,
    不靜默當 0, 以免低估曝險。
    """
    found = contract_of(symbol)
    if found is None:
        raise ValueError(f"未知契約乘數: {symbol!r}")
    price_value = to_decimal(price)
    if price_value is None:
        raise ValueError(f"{symbol} 價格無法計價: {price!r}")
    qty_value = to_decimal(qty)
    if qty_value is None:
        raise ValueError(f"{symbol} 口數無法解讀: {qty!r}")
    sign = -1 if normalize_side(side) == "S" else 1
    return price_value * found[1].point_value * abs(qty_value) * sign


@dataclass
class Exposure:
    total: Decimal = Decimal(0)                       # 帶號淨名目 (買 +, 賣 -)
    unknown: list[str] = field(default_factory=list)  # 算不出名目的列, 例如 "TXO47000I6: 未知契約乘數"


def net_exposure(rows: Iterable[dict], price_key: str, qty_key: str, side_key: str = "buy_sell") -> Exposure:
    """把庫存表 / 掛單表 (dict 列) 加總成帶號淨名目。

    算不出來的列不會偷偷當 0, 會列在 Exposure.unknown, 由 check_order 依
    RiskLimits.block_unknown_symbols 決定要擋還是只警告。
    """
    result = Exposure()
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        qty = to_decimal(row.get(qty_key))
        if qty is None:
            result.unknown.append(f"{symbol}: 口數無法解讀 {row.get(qty_key)!r}")
            continue
        if qty == 0:
            continue
        try:
            result.total += notional(symbol, row.get(price_key), qty, row.get(side_key))
        except ValueError as exc:
            result.unknown.append(f"{symbol}: {exc}")
    return result


# ======================================================================
# 防呆參數 / 報價 / 檢查結果
# ======================================================================
@dataclass(frozen=True)
class RiskLimits:
    """下單防呆參數。名目價值 = 價格 x 契約乘數 x 口數。"""
    allowed_products: tuple[str, ...] = ("TX", "MTX", "TM")  # 可下單家族: "TX" 大台 / "MTX" 小台 / "TM" 微台, 不限月份
    max_order_qty: int = 10                        # 單筆口數上限
    max_order_value: int = 10_000_000              # 單筆名目上限 (元)。1 口大台約 46000 x 200 = 920 萬
    max_net_value: int = 20_000_000                # 淨部位名目上限 |庫存 + 掛單 + 本次委託|
    max_price_deviation_pct: float | None = 3.0    # 委託價偏離最後成交價超過此 % 就擋; None = 不檢查
    block_unknown_symbols: bool = True             # 庫存/掛單有算不出名目的商品 (例如選擇權) 時擋單

    def __post_init__(self) -> None:
        # allowed_products 只認 CONTRACTS 的家族代碼, 大小寫不拘; 給字串 "TM" (常見筆誤 ("TM") 少了逗號,
        # 在 Python 裡是字串不是 tuple) 也當成 ("TM",)。認不得的直接丟錯, 不要靜默變成全部擋單。
        raw = (self.allowed_products,) if isinstance(self.allowed_products, str) else tuple(self.allowed_products)
        products = tuple(str(item).strip().upper() for item in raw)
        unknown = [item for item in products if item not in CONTRACTS]
        if not products or unknown:
            raise ValueError(f"allowed_products 只能是 {tuple(CONTRACTS)} (TX 大台 / MTX 小台 / TM 微台),"
                             f" 收到 {self.allowed_products!r}")
        object.__setattr__(self, "allowed_products", products)   # frozen dataclass 只能這樣改


@dataclass
class Quote:
    symbol: str
    name: str = ""
    exists: bool = False                 # False = 報價端查不到這個代碼, 別拿去下單
    last: Decimal | None = None          # 最後成交價 (盤後 = 上一節最後一筆)
    bid1: Decimal | None = None
    bid1_qty: int | None = None
    ask1: Decimal | None = None
    ask1_qty: int | None = None
    up_limit: Decimal | None = None      # 漲停價, 要掛漲停就填這個數字
    down_limit: Decimal | None = None    # 跌停價
    reference: Decimal | None = None     # 參考價 (昨收)
    errors: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"{self.symbol} {self.name}  (exists={self.exists})",
            f"last={self.last}  bid1={self.bid1} x {self.bid1_qty}  ask1={self.ask1} x {self.ask1_qty}",
            f"漲停={self.up_limit}  跌停={self.down_limit}  昨收={self.reference}",
        ]
        if not self.exists:
            lines.append(f"❌ 報價端查不到 {self.symbol}, 檢查代碼 (見模組檔頭商品代碼表)")
        elif self.last is None:
            lines.append("⚠️ 查到商品但沒有即時價 = 報價沒訂閱成功, 只拿到商品基本資料。"
                         "同一帳號最多 2 條報價連線, 關掉其他有連報價的 process 再重跑連線格。")
        lines.extend(f"⚠️ {error}" for error in self.errors)
        return "\n".join(lines)


@dataclass
class GuardResult:
    ok: bool
    reasons: list[str]                       # 擋單原因, 非空就不送
    warnings: list[str]                      # 提醒, 不擋
    symbol: str = ""
    side: str = ""                           # "B" / "S"
    qty: int = 0
    price: Decimal | None = None             # 委託價 (通過檢查後拿這個送)
    last: Decimal | None = None              # 計價用最後成交價
    point_value: int | None = None
    order_value: Decimal = Decimal(0)        # 本次委託帶號名目 (用最後價)
    held_value: Decimal = Decimal(0)         # 庫存帶號名目 (用平均成本)
    pending_value: Decimal = Decimal(0)      # 掛單帶號名目 (用委託價 x 剩餘口數)
    net_value: Decimal = Decimal(0)          # 庫存 + 掛單 + 本次
    deviation_pct: Decimal | None = None     # 委託價相對最後價的偏離 %


def check_order(symbol: str, side: Any, qty: Any, price: Any, quote: Quote, *,
                held: Exposure, pending: Exposure, limits: RiskLimits) -> GuardResult:
    """下單防呆 (純計算, 不碰 API, 好測試)。依序檢查:

      1 商品   只能是 allowed_products 的報價端代碼
               (擋掉價差 / AM 純日盤 / 交易所代碼 / 庫存表代碼 / 選擇權 / 亂打的代碼)
      2 買賣別 必須認得出 B/S
      3 口數   正整數, 且 <= max_order_qty
      4 價格   必須是正數 (不收 M / P 市價代碼), 對齊跳動點, 在今日漲跌停內,
               且與最後成交價偏離 <= max_price_deviation_pct (抓少打一位數這種 fat finger)
      5 單筆名目 |最後價 x 乘數 x 口數| <= max_order_value
               (用最後價不用委託價: 掛遠價成交時的曝險是市價那個量級, 用委託價會低估)
      6 淨部位 |庫存 + 掛單 + 本次| <= max_net_value
               庫存用平均成本、掛單用委託價 x 剩餘口數計價; 算不出名目的列 (Exposure.unknown)
               依 block_unknown_symbols 決定擋單或只警告。反向單會讓淨部位變小, 自然放行。

    所有能檢查的都檢查完再回傳, reasons 會一次列出全部問題。
    """
    reasons: list[str] = []
    warnings: list[str] = []
    text = str(symbol).strip().upper()
    result = GuardResult(ok=False, reasons=reasons, warnings=warnings, symbol=text)

    # 1 商品
    if not is_orderable(text, limits.allowed_products):
        reasons.append(f"{text} 不在允許下單清單 {limits.allowed_products}"
                       " (要用報價端代碼, 例如 MTX09 / TX10 / TM2609)")
        return result
    spec = contract_of(text)[1]
    result.point_value = spec.point_value

    # 2 買賣別
    try:
        result.side = normalize_side(side)
    except ValueError:
        reasons.append(f"看不懂的買賣別: {side!r} (收 B/S, 0/1, buy/sell, 買/賣)")
        return result

    # 3 口數
    qty_value = to_decimal(qty)
    if qty_value is None or qty_value < 1 or qty_value != qty_value.to_integral_value():
        reasons.append(f"口數必須是正整數: {qty!r}")
        return result
    result.qty = int(qty_value)
    if result.qty > limits.max_order_qty:
        reasons.append(f"口數 {result.qty} 超過單筆上限 {limits.max_order_qty}")

    # 計價基準: 沒有最後成交價就算不出名目, 直接擋
    if quote.last is None:
        reasons.append(f"{text} 沒有最後成交價, 無法計算名目 (exists={quote.exists})")
        return result
    result.last = quote.last

    # 4 價格
    price_value = to_decimal(price)
    if price_value is None or price_value <= 0:
        reasons.append(f"委託價必須是正數, 不收 M/P 市價代碼: {price!r}"
                       " (要市價請直接呼叫 client.place_future_market)")
    else:
        result.price = price_value
        if price_value % spec.tick_size != 0:
            reasons.append(f"委託價 {price_value} 沒對齊跳動點 {spec.tick_size}")
        if quote.up_limit is not None and price_value > quote.up_limit:
            reasons.append(f"委託價 {price_value} 高於漲停 {quote.up_limit}")
        if quote.down_limit is not None and price_value < quote.down_limit:
            reasons.append(f"委託價 {price_value} 低於跌停 {quote.down_limit}")
        if quote.up_limit is None or quote.down_limit is None:
            warnings.append("報價沒有漲跌停價, 略過漲跌停檢查")
        result.deviation_pct = (price_value - quote.last) / quote.last * 100
        if limits.max_price_deviation_pct is not None:
            cap = Decimal(str(limits.max_price_deviation_pct))
            if abs(result.deviation_pct) > cap:
                reasons.append(f"委託價 {price_value} 偏離最後價 {quote.last} 達 {result.deviation_pct:+.2f}%,"
                               f" 超過 {cap}%")

    # 5 單筆名目 (用最後價)
    result.order_value = notional(text, quote.last, result.qty, result.side)
    if abs(result.order_value) > limits.max_order_value:
        reasons.append(f"單筆名目 {abs(result.order_value):,.0f} 超過上限 {limits.max_order_value:,}")

    # 6 淨部位
    result.held_value, result.pending_value = held.total, pending.total
    result.net_value = held.total + pending.total + result.order_value
    if abs(result.net_value) > limits.max_net_value:
        reasons.append(f"淨部位名目 {abs(result.net_value):,.0f} 超過上限 {limits.max_net_value:,}")
    unknown = held.unknown + pending.unknown
    if unknown:
        message = "庫存/掛單有算不出名目的商品, 淨部位檢查不完整: " + "; ".join(unknown)
        (reasons if limits.block_unknown_symbols else warnings).append(message)

    result.ok = not reasons
    return result


def print_guard(check: GuardResult, limits: RiskLimits, available_balance: Any = None) -> None:
    """把 check_order 的結果印成幾行, 看得到每一項數字是怎麼算的。"""
    side_text = {"B": "買", "S": "賣"}.get(check.side, check.side or "?")
    say(f"── 下單防呆 {check.symbol} {side_text} {check.qty} 口 @ {check.price} ──")
    if check.last is not None:
        deviation = f"{check.deviation_pct:+.2f}%" if check.deviation_pct is not None else "n/a"
        say(f"   最後價 {check.last}  乘數 {check.point_value}  委託價偏離 {deviation}"
            f" (上限 {limits.max_price_deviation_pct}%)")
        say(f"   單筆名目 {abs(check.order_value):,.0f} / 上限 {limits.max_order_value:,}")
        say(f"   淨部位   {abs(check.net_value):,.0f} / 上限 {limits.max_net_value:,}"
            f"   (庫存 {check.held_value:+,.0f} + 掛單 {check.pending_value:+,.0f}"
            f" + 本次 {check.order_value:+,.0f})")
    if available_balance is not None:
        say(f"   可用餘額 {_fmt_money(available_balance)}")
    for warning in check.warnings:
        say(f"⚠️ {warning}")


# ======================================================================
# 查詢 (唯讀): 把 SDK 回的 dict of dict 變成 DataFrame, 規格與等待邏輯都在 SDK
# ======================================================================
def _frame(table: dict) -> pd.DataFrame:
    """SDK 的 dict of dict -> DataFrame。空的回空表, 不會炸。"""
    return pd.DataFrame.from_dict(table, orient="index")


def _numeric_if_possible(series: pd.Series) -> pd.Series:
    """整欄都轉得成數字才轉 (顯示好看), 否則原樣保留, 不把看不懂的字串 (例如 "*********") 變成 NaN。"""
    converted = pd.to_numeric(series, errors="coerce")
    non_empty = series.astype(str).str.strip().ne("")
    return series if (converted.isna() & non_empty).any() else converted


def _price_or_none(value: Any) -> Decimal | None:
    """0 / None / 空字串一律當成沒資料 (期貨不會有 0 元的成交價或委買賣價)。"""
    if value in (None, "", 0):
        return None
    return Decimal(str(value))


def futures_account(client: CapitalClient) -> str:
    """登入帳號底下的期貨帳號 (account_type TF)。沒有就丟例外。"""
    accounts = [acc.full_account for acc in client.get_accounts() if acc.account_type.upper() == "TF"]
    if not accounts:
        say("❌ 這個登入沒有期貨帳號 (TF), 無法操作台指期")
        raise RuntimeError("no futures (TF) account under this login")
    if len(accounts) > 1:
        say(f"⚠️ 有 {len(accounts)} 個期貨帳號, 用第一個; 要換請自行傳 account=")
    return accounts[0]


def query_rights(client: CapitalClient, account: str) -> pd.DataFrame:
    """資金: 期貨權益數 (GetFutureRights, 台幣)。41 欄全部由 SDK 解出 (欄名見 FUTURE_RIGHTS_FIELDS), 這裡只把數字欄轉成數字。

    5 秒內重查會由 SDK 先等滿間隔; 查詢沒回應 SDK 會重試一次, 仍失敗丟 CapitalApiError (不會回空表冒充)。
    """
    frame = _frame(fetch_future_rights(client, account=account))
    if frame.empty:
        say("⚠️ 權益數查無資料")
        return frame
    for column in frame.columns:
        if column not in FUTURE_RIGHTS_TEXT_FIELDS and column != "raw":
            frame[column] = _numeric_if_possible(frame[column])
    return frame


def query_positions(client: CapitalClient, account: str) -> pd.DataFrame:
    """庫存: 期貨未平倉 (GetOpenInterestGW nFormat=1, 欄名見 FUTURE_POSITION_FIELDS)。

    symbol 是庫存表代碼 (微台 TM09), contract_of() 認得。官方格式 1 不含市價與浮動損益;
    帳戶層級看 query_rights 的 floating_pnl, 逐商品要自己拿 quote().last 減 avg_price 再乘 point_value()。
    空表就是真的沒部位: 查詢沒回應時 SDK 會丟 CapitalApiError。
    """
    return _frame(fetch_future_positions(client, account=account))


def query_open_orders(client: CapitalClient, account: str) -> pd.DataFrame:
    """掛單: 當前可刪掛單 (GetOrderReport n_format=3, 同步查詢, 欄名見 QUERY_ORDER_FIELDS)。

    不用 OnNewData cache: cache 只有本 process 連線期間收到的回報, 重開 kernel 就空了。
    symbol 欄是交易所契約代碼 (MXFI6 / TMFI6), 刪單要用它。
    官方限制兩次委託查詢間隔 5 秒, SDK 會自動等, 所以連續呼叫會慢幾秒。
    """
    return _frame(fetch_order_reports(client, account=account, n_format=3))


def query_quote(client: CapitalClient, symbol: str, *, seconds: float = 3.0, with_ticks: bool = False,
                retry_on_subscribe_error: bool = True) -> Quote:
    """報價: 商品是否存在 + 最後成交價 + b1/a1 + 今日漲跌停價, 全部來自報價快照 (SKSTOCKLONG)。

    走 SDK 的一次性查詢 fetch_latest_quotes: 訂閱 -> 等到「這次訂閱後」推來的新快照 -> 讀取 -> 退訂,
    不留背景訂閱, 也不會拿上一次查詢的舊快取冒充。盤後也能查 (沒有新事件就等滿 seconds 秒再從元件本地表讀),
    最後價是上一節最後一筆, b1/a1 為 0 -> None。
    預設只要快照: b1/a1 快照裡就有; 五檔 / ticks 要走 RequestTicks, SKCOM 會回補整天成交明細, 很貴, with_ticks=True 才抓。

    訂閱被拒時 (3030 行情連線額度滿) 用 client.reconnect_quote() 重連再試一次;
    仍失敗就把價格全清成 None, 絕不拿過期價格冒充 (place_limit 會因 last=None 擋單)。
    """
    kinds = ("snapshot", "ticks") if with_ticks else ("snapshot",)

    def fetch():
        # auto_login=False: connect() 已登入, 不必每次再打一次 SKCenterLib_Login (省 0.5 秒)
        return fetch_latest_quotes(client, [symbol], market="future", data=kinds,
                                   timeout_sec=seconds, max_ticks=1, auto_login=False)

    client.hub.clear_quote_errors()    # 之前累積的錯誤別混進這次結果
    result = fetch()
    if retry_on_subscribe_error and result.subscription_refused:
        say("⚠️ 報價訂閱被拒 (3030 = 行情連線超過帳號上限), 重連報價伺服器再試一次...")
        if client.reconnect_quote():
            client.hub.clear_quote_errors()
            result = fetch()
            say("✅ 報價重連成功" if not result.subscription_refused
                else "❌ 重連後訂閱仍被拒: 同帳號報價連線最多 2 條, 關掉其他有連報價的程式或重啟 kernel")
        else:
            say("❌ 報價重連失敗: 同帳號報價連線最多 2 條, 關掉其他有連報價的程式 (策略王 / 舊 kernel),"
                " 或重啟本 kernel 再跑一次連線格")

    snap = result.snapshots.get(symbol)
    quote = Quote(
        symbol=symbol,
        name=snap.name if snap else "",
        exists=bool(snap and (snap.name or snap.has_data)),
        last=_price_or_none(snap.close if snap else None),
        bid1=_price_or_none(snap.bid if snap else None),
        bid1_qty=(snap.bid_qty or None) if snap else None,
        ask1=_price_or_none(snap.ask if snap else None),
        ask1_qty=(snap.ask_qty or None) if snap else None,
        up_limit=_price_or_none(snap.up_limit if snap else None),
        down_limit=_price_or_none(snap.down_limit if snap else None),
        reference=_price_or_none(snap.reference if snap else None),
        errors=list(result.quote_errors),
    )
    if result.subscription_refused:
        # 訂閱失敗時 snapshot 可能是舊快取, 價格全部清空 -> place_limit 會因 last=None 擋單,
        # 絕不拿過期價格當防呆基準。
        quote.last = quote.bid1 = quote.ask1 = None
        quote.bid1_qty = quote.ask1_qty = None
        quote.up_limit = quote.down_limit = quote.reference = None
        quote.errors.append("報價訂閱失敗, 已捨棄可能過期的即時價 (last / b1 a1 / 漲跌停)")
    return quote


# ======================================================================
# 送單結果
# ======================================================================
def report_result(action: str, result: ApiResult) -> None:
    """把 SDK 的 ApiResult 印成一行: 🧪 dry-run / 📤 已送出 / ❌ 失敗。"""
    if result.dry_run:
        say(f"🧪 DRY-RUN: {action}防呆通過, 但 enable_live_order=False, 沒有送單。payload={result.raw}")
    elif result.ok:
        say(f"📤 {action}已送出 (code 0)。訊息: {result.broker_message}"
            "  *0 只代表送達交易所, 結果以回報為準")
    else:
        say(f"❌ {action}失敗 code={result.code} {result.message} {result.broker_message}")


@dataclass
class OrderOutcome:
    symbol: str
    ok: bool = False                     # 防呆是否通過
    check: GuardResult | None = None
    quote: Quote | None = None
    available_balance: Any = None
    result: ApiResult | None = None      # None = 被擋下沒送; dry_run=True = 沒送; code 0 = 送達交易所

    @property
    def sent(self) -> bool:
        return bool(self.result is not None and not self.result.dry_run and self.result.ok)


# ======================================================================
# 操作面板
# ======================================================================
# 本 kernel 最後一個登入成功的 client。connect() 重跑時重用它:
# 舊 client 不會被 GC (COM event handler 還抓著), 每重跑一次連線格就多一條
# 殭屍報價連線, 帳號額度只有 2 條, 吃滿後每次報價都要走 3030 重連補救 (慢 20~30 秒)。
_LAST_CLIENT: CapitalClient | None = None


def _client_alive(client) -> bool:
    """舊 client 是否還活著 (COM 元件能回應)。壞掉就放棄重用, 走全新登入。"""
    try:
        client.is_quote_connected()
        return True
    except Exception:
        return False


class TaifexTrader:
    """一個期貨帳號的台指期操作面板: 查詢 / 防呆下單 / 刪單。

    所有方法都當場重查 (不吃舊值); 只有 place_limit / cancel / cancel_all 會送東西:
    client.enable_live_order=True 就真的送到 .env 指定的環境, False 時 SDK 直接回 DRY_RUN, 不碰交易所。
    """

    def __init__(self, client: CapitalClient, account: str | None = None, limits: RiskLimits | None = None):
        self.client = client
        self.limits = limits or RiskLimits()
        self.account = account or futures_account(client)

    @classmethod
    def connect(cls, *, enable_live_order: bool = False, limits: RiskLimits | None = None,
                quote_connection: bool = True, reuse: bool = True) -> "TaifexTrader":
        """讀 .env 登入 (憑證 + 回報連線), 找期貨帳號, 印出環境摘要。

        重跑這格是安全的: 同一個 kernel 已經連過就重用那條連線 (只更新 live 旗標與防呆參數),
        不會再建新連線去吃報價額度。要完全重來 (換帳號 / 連線疑似壞掉) 請重啟 kernel。
        quote_connection=False 走 LoginSetQuote 不佔報價連線 (同帳號只有 2 條), 但這樣 quote() 會拿不到即時價。
        """
        global _LAST_CLIENT
        if reuse and _LAST_CLIENT is not None and _client_alive(_LAST_CLIENT):
            client = _LAST_CLIENT
            client.enable_live_order = enable_live_order
            say("ℹ️ 重用本 kernel 的既有連線 (重跑連線格不會多吃報價額度)")
            if quote_connection and client.is_quote_connected() != 1:
                client.reconnect_quote()
            trader = cls(client, limits=limits)
            trader.banner()
            return trader

        client = CapitalClient.from_env(enable_live_order=enable_live_order)
        result = client.login(read_cert=True, connect_reply=True, quote_connection=quote_connection)
        if not client.is_login_result_ok(result):
            say(f"❌ 登入失敗 code={result.code} {result.message} {result.broker_message}")
            raise RuntimeError(f"login failed: {result.code} {result.message}")
        _LAST_CLIENT = client
        trader = cls(client, limits=limits)
        trader.banner()
        return trader

    def banner(self) -> None:
        live = self.client.enable_live_order
        say(f"{'📤' if live else '🧪'} 環境={self.client.config.authority.name}  live_order={live}"
            f"  ({'實單模式, place_limit / cancel 會真的送出' if live else 'dry-run, 不會送單'})")
        say(f"✅ 期貨帳號 {self.account}")
        say(f"ℹ️ 防呆 {self.limits}")

    # ---- 查詢 ----------------------------------------------------------
    def rights(self) -> pd.DataFrame:
        """資金: 印一行摘要, 回傳整張表 (欄名「中文 english」, 直接 .T 看)。"""
        frame = query_rights(self.client, self.account)
        if frame.empty:
            return frame
        row = frame.iloc[0]
        say(f"✅ 權益數 {_fmt_money(row['equity'])}  可用餘額 {_fmt_money(row['available_balance'])}"
            f"  浮動損益 {_fmt_money(row['floating_pnl'])}  委託保證金 {_fmt_money(row['order_margin'])}"
            f"  風險指標 {row['risk_indicator']}")
        return frame.rename(columns=RIGHTS_LABELS)

    def positions(self) -> pd.DataFrame:
        """庫存: 期貨未平倉, 印淨名目 (平均成本計), 回傳表欄名「中文 english」。"""
        frame = query_positions(self.client, self.account)
        if frame.empty:
            say("ℹ️ 沒有未平倉部位")
        else:
            exposure = net_exposure(frame.to_dict("records"), "avg_price", "open_qty")
            say(f"✅ 未平倉 {len(frame)} 筆, 淨名目 (平均成本計) {exposure.total:+,.0f}")
            for item in exposure.unknown:
                say(f"⚠️ 算不出名目: {item}")
        return frame.rename(columns=POSITION_LABELS)

    def open_orders(self) -> pd.DataFrame:
        """掛單: 當前可刪掛單 (symbol 欄就是刪單要用的代碼), 回傳表欄名「中文 english」。"""
        frame = query_open_orders(self.client, self.account)
        if frame.empty:
            say("ℹ️ 沒有可刪掛單")
        else:
            say(f"✅ 可刪掛單 {len(frame)} 筆, 代碼: {sorted(set(frame['symbol']))}")
        return frame.rename(columns=ORDER_LABELS)

    def quote(self, symbol: str, *, with_ticks: bool = False) -> Quote:
        """報價: 印出商品是否存在 / 最後價 / b1 a1 / 漲跌停, 回傳 Quote。3030 會自動重連一次。"""
        quote = query_quote(self.client, str(symbol).strip().upper(), with_ticks=with_ticks)
        say(quote.describe())
        return quote

    def reconnect_quote(self) -> bool:
        """手動重連報價伺服器 (報價一直抓不到時用); quote() 遇 3030 也會自動做一次。"""
        ok = self.client.reconnect_quote()
        say("✅ 報價重連完成" if ok else "❌ 報價重連失敗: 同帳號報價連線最多 2 條, 關掉其他有連報價的程式或重啟 kernel")
        return ok

    # ---- 下單 ----------------------------------------------------------
    def place_limit(self, symbol: str, side: Any, qty: int, price: Any, *,
                    trade_type: TradeType = TradeType.ROD,
                    new_close: FuturesNewClose = FuturesNewClose.AUTO,
                    reserved: FuturesReserved = FuturesReserved.REGULAR) -> OrderOutcome:
        """防呆下限價單。

        流程: 擋商品 -> 查報價 -> 查庫存 / 掛單 / 權益數 (全部當場重查, 不吃舊值)
              -> check_order() -> 全過才 place_future_limit()。
        回傳 OrderOutcome: result=None 代表被擋下完全沒送; result.dry_run=True 也沒送。
        正常約 2~4 秒 (報價 0.1~2s + 未平倉 0.6s + 委託查詢 0.2s + 權益數 0.5s)。
        5 秒內剛查過同類查詢會變慢: SDK 會先等滿 5.5 秒間隔再查 (委託 / 未平倉 / 權益數各自計算)。
        """
        text = str(symbol).strip().upper()
        outcome = OrderOutcome(symbol=text)

        # 便宜的檢查先做 (商品 / 買賣別 / 口數), 不合格就不用花後面好幾秒查詢
        if not is_orderable(text, self.limits.allowed_products):
            say(f"🚫 {text} 不在允許下單清單 {self.limits.allowed_products}, 沒有送出"
                " (要用報價端代碼, 例如 MTX09 / TX10 / TM2609)")
            return outcome
        try:
            normalize_side(side)
        except ValueError:
            say(f"🚫 看不懂的買賣別: {side!r} (收 B/S, 0/1, buy/sell, 買/賣), 沒有送出")
            return outcome
        qty_value = to_decimal(qty)
        if qty_value is None or qty_value < 1 or qty_value != qty_value.to_integral_value():
            say(f"🚫 口數必須是正整數: {qty!r}, 沒有送出")
            return outcome
        if int(qty_value) > self.limits.max_order_qty:
            say(f"🚫 口數 {int(qty_value)} 超過單筆上限 {self.limits.max_order_qty}, 沒有送出")
            return outcome

        # 這一段是下單前最花時間的部分: 同類查詢 5 秒內重發會被元件拒絕, SDK 會先等滿間隔,
        # 剛跑過 positions() / open_orders() / rights() 再下單會多等幾秒, 是正常現象不是卡住。
        say("ℹ️ 下單前置查詢: 報價 -> 庫存 -> 掛單 -> 權益數 (約 3~8 秒)...")
        try:
            quote = query_quote(self.client, text)
            outcome.quote = quote
            if not quote.exists or quote.last is None:
                say(quote.describe())
                say(f"🚫 {text} 查不到報價或最後成交價, 沒有送出")
                return outcome
            positions = query_positions(self.client, self.account)
            orders = query_open_orders(self.client, self.account)
            rights = query_rights(self.client, self.account)
        except Exception as exc:
            say(f"❌ 下單前查詢失敗, 沒有送出: {exc!r}")
            return outcome

        held = net_exposure(positions.to_dict("records"), "avg_price", "open_qty")
        pending = net_exposure(orders.to_dict("records"), "price", "remaining_qty")
        check = check_order(text, side, qty, price, quote, held=held, pending=pending, limits=self.limits)
        outcome.check = check
        outcome.ok = check.ok
        outcome.available_balance = rights["available_balance"].iloc[0] if not rights.empty else None

        print_guard(check, self.limits, outcome.available_balance)
        if not check.ok:
            say("🚫 下單被防呆擋下, 沒有送出:")
            for reason in check.reasons:
                say(f"   - {reason}")
            return outcome
        say("✅ 防呆全部通過")

        # 實單時 SendFutureOrderCLR 是同步呼叫, 會等券商主機回覆才返回 (通常 1~3 秒)。
        # 如果停在這行之後很久, 卡的就是送單本身, 不是前面的查詢。
        say("📨 送單中...")
        outcome.result = self.client.place_future_limit(
            symbol=text, side=check.side, qty=check.qty, price=format_price(check.price),
            account=self.account, trade_type=trade_type, new_close=new_close, reserved=reserved,
        )
        report_result("下單", outcome.result)
        return outcome

    # ---- 刪單 ----------------------------------------------------------
    def cancel(self, symbol: str, *, verify: bool = True) -> pd.DataFrame:
        """刪掉某商品在本帳號的所有掛單 (CancelOrderByStockNo)。

        symbol 可給報價端 (MTX09 -> 自動轉 MXFI6) 或直接貼 open_orders() 印出的交易所代碼。
        真正的把關是「掛單表裡有沒有這個代碼」: 轉出來的代碼與原字串都拿去比對, 比不到就不呼叫 API,
        並列出現有可刪代碼讓你直接貼 (所以券商代碼格式跟預期不同也刪得到)。
        近月連續 (MTX00 / TM0000) 沒有月份轉不出來, 通常比不到, 請改給明確月份。
        verify=True 刪後重查一次 (回傳碼 0 只代表送達交易所, 不代表刪成功)。
        空字串會刪掉整個帳號所有委託, 這裡不接受, 要刪全部請用 cancel_all(confirm=True)。
        刪單前的查詢失敗 (例如 M999) 會直接丟例外, 不會在不確定掛單狀態下呼叫刪單;
        刪單送出後的驗證查詢失敗只印 ⚠️ (刪單已經送出去了, 不能讓例外蓋掉這件事), 請稍後再跑 open_orders() 確認。
        回傳: 刪單前該商品的掛單表 (verify 成功時改回傳刪後剩餘的)。
        """
        text = str(symbol).strip().upper()
        if not text:
            say("🚫 symbol 不能是空字串 (那會刪掉整個帳號所有委託), 要刪全部請用 cancel_all(confirm=True)")
            return pd.DataFrame()
        code = to_report_code(text)
        candidates = [text] if code in (None, text) else [text, code]
        if code not in (None, text):
            say(f"ℹ️ {text} -> 交易所契約代碼 {code}")

        orders = query_open_orders(self.client, self.account)
        matched = self._rows_for(orders, candidates)
        if matched.empty:
            say(f"⚠️ {' / '.join(candidates)} 目前沒有可刪掛單, 不呼叫刪單 API。"
                f"現有可刪代碼: {sorted(set(orders.get('symbol', [])))}")
            return matched
        target = str(matched["symbol"].iloc[0]).strip()      # 券商掛單表上實際用的代碼
        remaining_qty = pd.to_numeric(matched["remaining_qty"], errors="coerce").fillna(0).sum()
        say(f"🗑️ 準備刪除 {target} 共 {len(matched)} 筆掛單, 剩餘口數合計 {remaining_qty:,.0f}")

        result = self.client.cancel_orders_by_symbol(target, account=self.account)
        report_result("刪單", result)
        if not verify or result.dry_run or not result.ok:
            return matched

        self.client.pump(3)
        try:
            left = self._rows_for(query_open_orders(self.client, self.account), [target])
        except Exception as exc:
            say(f"⚠️ 刪單已送出, 但刪後驗證查詢失敗: {exc!r}。請稍後再跑 open_orders() 確認")
            return matched
        say(f"{'✅' if left.empty else '⚠️'} 刪單後 {target} 剩餘可刪掛單 {len(left)} 筆")
        return left

    def cancel_all(self, *, confirm: bool = False) -> ApiResult | None:
        """刪掉本帳號全部委託 (CancelOrderByStockNo 帶空字串)。一定要 confirm=True 才動作。"""
        if not confirm:
            say("🚫 cancel_all 需要 confirm=True 才會執行 (這會刪掉帳號下所有商品的委託)")
            return None
        result = self.client.cancel_orders_by_symbol("", account=self.account)
        report_result("全部刪單", result)
        return result

    @staticmethod
    def _rows_for(orders: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
        """掛單表裡 symbol 等於任一候選代碼的列。"""
        if orders.empty or "symbol" not in orders:
            return orders
        symbols = orders["symbol"].astype(str).str.strip().str.upper()
        return orders[symbols.isin([str(code).strip().upper() for code in codes])]
