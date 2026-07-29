# %%
"""
05. One-shot SKQuoteLib query example (VS Code Python Interactive / ipynb).

使用方式:
  1. 在 VS Code / Cursor 以 Python Interactive 開啟本檔,依序執行各 # %% 區塊。
  2. 每個區塊上方只有幾個必要參數,改完重跑該區塊即可。
  3. 輸出一律是 pandas DataFrame,可直接繼續分析或 .to_csv() 匯出。

只查報價、不送單。持續串流請用 examples/04_live_quote.py。
盤後也可查:snapshot 回最後成交狀態,ticks 會回補當日成交明細。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT))

from capital_api_sdk import (  # noqa: E402
    CapitalClient,
    fetch_latest_quotes,
    fetch_quote_history,
    fetch_quote_symbol_lists,
)

client = CapitalClient.from_env(enable_live_order=False)
client

# %%
# ======================================================================
# 02. 商品清單: 現貨 / 期貨 / 選擇權(完整清單)
# ======================================================================
# 參數說明:
#   MARKETS  要查哪些市場,常用:
#            "stock"    = 上市 + 上櫃現貨
#            "future"   = 期貨(含價差商品,代碼有 "/")
#            "option"   = 選擇權
#            "tradable" = stock + future
#            "all"      = 全部市場(含興櫃、盤中零股、客製化)
#            也可指定單一市場別名("listed"/"otc"/"future-market"/...)或 market number
#
# 舉例:
#   MARKETS = "stock"            → 只查現貨
#   MARKETS = "future"           → 只查期貨(找價差代碼用這個)
#   MARKETS = ["listed", 2]      → 上市 + 期貨(混用別名與編號)
MARKETS = "tradable"

symbol_lists = fetch_quote_symbol_lists(client, MARKETS)

# 每個市場一張完整 DataFrame(不截斷);Interactive 視窗可捲動、也可 .to_csv() 匯出
symbol_tables = {
    market: pd.DataFrame(
        [{"symbol": it.symbol, "name": it.name, "fields": it.fields} for it in rows]
    )
    for market, rows in symbol_lists.markets.items()
}
for market, table in symbol_tables.items():
    print(f"{market}: {len(table)} symbols")

# 顯示其中一個市場的完整清單(改 key 看其他市場: 'listed' / 'otc' / 'future-market')
symbol_tables.get("future-market", next(iter(symbol_tables.values())))

# %%
# 期貨價差商品代碼(代碼含 "/";換月後會變,使用前重查)
spread_table = pd.concat(
    [t[t["symbol"].str.contains("/", regex=False)] for t in symbol_tables.values()],
    ignore_index=True,
) if symbol_tables else pd.DataFrame()
print(f"spread symbols: {len(spread_table)}")
spread_table

# %%
# ======================================================================
# 03. 一次性即時報價: snapshot / ticks / orderbook / live
# ======================================================================
# 參數說明:
#   SYMBOLS   商品代碼,可多檔、可混市場(現貨/期貨/價差都可放同一次查詢)
#   DATA      要哪些資料:
#             "snapshot"  = 報價快照(開高低收、買賣價量、總量、漲跌停)
#             "ticks"     = 成交明細(盤後會回補當日)
#             "orderbook" = 最佳五檔
#             "live"      = 以上三種全要
#             也可自由組合: ("snapshot", "orderbook")
#   MAX_TICKS 每檔保留幾筆成交明細;None = 全部(含當日回補,一檔可能數萬筆)
#
# 舉例:
#   SYMBOLS = ("2330",);                DATA = "live"       → 台積電全部資料
#   SYMBOLS = ("2330", "0050");         DATA = "snapshot"   → 兩檔現貨快照
#   SYMBOLS = ("TX00",);                DATA = "orderbook"  → 台指近月五檔
#   SYMBOLS = ("TX08/09", "CDF08/09");  DATA = "live"       → 期貨價差(台指/台積電)
#   SYMBOLS = ("2330", "TX00");         DATA = "ticks"; MAX_TICKS = None → 當日全部成交明細
SYMBOLS = ("2330", "TX00", "TX08/09")
DATA = "live"
MAX_TICKS = 5

result = fetch_latest_quotes(client, SYMBOLS, data=DATA, max_ticks=MAX_TICKS)
if result.quote_errors:
    print("quote_errors:", result.quote_errors[-3:])

# %%
# --- snapshot 表(一檔一列) ---
snapshot_table = pd.DataFrame([
    {
        "symbol": sym, "name": s.name, "close": s.close, "open": s.open,
        "high": s.high, "low": s.low, "bid": s.bid, "bid_qty": s.bid_qty,
        "ask": s.ask, "ask_qty": s.ask_qty, "total_qty": s.total_qty,
        "reference": s.reference, "up_limit": s.up_limit, "down_limit": s.down_limit,
    }
    for sym, s in result.snapshots.items() if s is not None
]).set_index("symbol") if result.snapshots else pd.DataFrame()
snapshot_table

# %%
# --- ticks 表(成交明細;history=True 是當日回補) ---
ticks_table = pd.DataFrame([
    {
        "symbol": t.symbol or sym, "date": t.date, "time": t.time_hms,
        "close": t.close, "qty": t.qty, "bid": t.bid, "ask": t.ask,
        "history": t.history, "simulate": t.simulate,
    }
    for sym, rows in result.ticks.items() for t in rows
]) if result.ticks else pd.DataFrame()
ticks_table

# %%
# --- orderbook 表(最佳五檔,一檔五列) ---
orderbook_table = pd.DataFrame([
    {
        "symbol": sym, "level": level + 1,
        "bid_qty": b.bid_qtys[level], "bid": b.bid_prices[level],
        "ask": b.ask_prices[level], "ask_qty": b.ask_qtys[level],
    }
    for sym, b in result.order_books.items() if b is not None
    for level in range(5)
]).set_index(["symbol", "level"]) if result.order_books else pd.DataFrame()
orderbook_table

# %%
# ======================================================================
# 04. 歷史 K 線: minute / day / week / month
# ======================================================================
# 參數說明:
#   SYMBOLS       一或多檔(現貨/期貨皆可;價差商品不支援 K 線,會回 0 筆)
#   LINE_TYPE     "minute"=分K / "day"=日K / "week"=週K / "month"=月K
#   DAYS          抓最近 N 天(自動避開週末)
#   MINUTE_NUMBER 幾分K(1/3/5/15/30/60...),LINE_TYPE="minute" 才有效
#   SESSION       期貨專用: 0=全盤(含前一日夜盤) / 1=僅日盤(AM);現貨不受影響
#   START_DATE /  指定日期區間(填了就取代 DAYS),可用 "YYYYMMDD" 或 "YYYY-MM-DD";
#   END_DATE      不指定就設 None
#
# 舉例:
#   SYMBOLS = ("2330",); LINE_TYPE = "day";    DAYS = 30                  → 台積電最近30天日K
#   SYMBOLS = ("2330",); LINE_TYPE = "minute"; MINUTE_NUMBER = 5; DAYS=3  → 5分K
#   SYMBOLS = ("TX00",); LINE_TYPE = "minute"; SESSION = 0                → 台指分K(含夜盤)
#   SYMBOLS = ("TX00",); LINE_TYPE = "minute"; SESSION = 1                → 台指分K(只要日盤)
#   SYMBOLS = ("2330", "TX00"); LINE_TYPE = "week"; DAYS = 180            → 多檔週K
#   START_DATE = "2026-07-01"; END_DATE = "2026-07-25"                    → 指定區間
SYMBOLS = ("2330", "TX00")
LINE_TYPE = "minute"
DAYS = 3
MINUTE_NUMBER = 15
SESSION = 0
START_DATE = None
END_DATE = None

kline_tables: dict[str, pd.DataFrame] = {}
for symbol in SYMBOLS:
    rows = fetch_quote_history(
        client, symbol,
        start_date=START_DATE, end_date=END_DATE, days=DAYS,
        line_type=LINE_TYPE, minute_number=MINUTE_NUMBER, trade_session=SESSION,
    )
    if not rows:
        print(f"WARNING {symbol}: 0 rows(價差商品不支援 K 線;或確認代碼/日期區間)")
        continue
    table = pd.DataFrame([
        {"date": r.date, "time": r.time, "open": r.open, "high": r.high,
         "low": r.low, "close": r.close, "volume": r.volume}
        for r in rows
    ])
    for col in ("open", "high", "low", "close", "volume"):
        table[col] = pd.to_numeric(table[col], errors="coerce")
    kline_tables[symbol] = table
    print(f"{symbol}: {len(table)} rows ({table['date'].iloc[0]} ~ {table['date'].iloc[-1]})")

# 看其中一檔(改 key);全部在 kline_tables dict 內
kline_tables.get(SYMBOLS[0], pd.DataFrame())

# %%
