# %%
"""
Live order template (VS Code Python Interactive).

預設 ENABLE_LIVE_ORDER=False:所有下單/刪單方法只回傳 dry-run payload,不會送單。
實單前務必:
1. 先用 SKCOMTester.exe 確認登入 / 憑證 / 下單皆正常。
2. 逐格執行、確認帳號、商品、數量、價格與 dry-run payload 無誤。
3. 自行把 ENABLE_LIVE_ORDER 改成 True 後重跑本格,再小量測試。
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT))

from capital_api_sdk import CapitalClient, Side, fetch_account_snapshot, fetch_order_reports  # noqa: E402

ENABLE_LIVE_ORDER = False  # True = 實際送單(正式環境),請自行小心切換!

client = CapitalClient.from_env(enable_live_order=ENABLE_LIVE_ORDER)
client.login(read_cert=True, connect_reply=True)

print("live order:", ENABLE_LIVE_ORDER, "(False = dry-run, 不會送單)")

# %%
# 取得帳號
accounts = client.get_accounts()
account_dict = {
    "stock": [acc.full_account for acc in accounts if acc.account_type == "TS"][0],
    "futures": [acc.full_account for acc in accounts if acc.account_type == "TF"][0],
}

# %%
# 下現貨限價單
# result = client.place_stock_limit(symbol="009816", side=Side.BUY, qty=1, price="14.82", account=account_dict["stock"])
# print(result)

# %%
# 查看掛單 — 方法一:OnNewData 回報 cache(需 connect_reply,等 3 秒)
# 已依成交/取消回報沖銷
snapshot = fetch_account_snapshot(client, include=["orders"])
pd.DataFrame(snapshot["stock_open_orders"]).T

# %%
# 查看掛單 — 方法二:同步 GetOrderReport(n_format=3 可取消;官方限制查詢間隔 5 秒)
# 回傳官方 5-4-4 查詢格式:status_name / filled_qty / remaining_qty 等(含盤中零股)
order_reports = fetch_order_reports(client, account=account_dict["stock"])
pd.DataFrame(order_reports).T

# %%
# 取消掛單 (用 seq_no 取消單筆掛單)
seq_no = list(snapshot["stock_open_orders"].keys())[0]
result = client.cancel_order_by_seq(seq_no, account=account_dict["stock"])
print(result)

# %%
# 取消掛單 (用 symbol 取消該商品所有掛單;symbol 留空 = 該帳號全部)
result = client.cancel_orders_by_symbol("009816", account=account_dict["stock"])
print(result)

# %%
# ======================================================================
# 下單方法總覽(dry-run 範本;ENABLE_LIVE_ORDER=False 時只回 payload 不送單)
# 逐格執行看 payload,實單前自行小量驗證
# ======================================================================
from capital_api_sdk import StockFlag, StockPeriod, TradeType, FuturesNewClose, FuturesDayTrade, FuturesReserved, OrderMarket  # noqa: E402

# --- 現貨:價格特殊代碼(官方 5-4 STOCKORDER) ---
print(client.place_stock_limit(symbol="2330", side=Side.BUY, qty=1, price="600"))   # 限價
print(client.place_stock_market(symbol="2330", side=Side.SELL, qty=1))              # 市價(price 固定 0)
print(client.place_stock_limit_up(symbol="2330", side=Side.SELL, qty=1))            # 掛漲停價 "H"
print(client.place_stock_limit_down(symbol="2330", side=Side.BUY, qty=1))           # 掛跌停價 "L"
print(client.place_stock_at_reference(symbol="2330", side=Side.BUY, qty=1))         # 掛平盤價(昨收) "M"

# %%
# --- 現貨:信用交易與無券(需開信用戶/簽署;flag 對應官方 sFlag) ---
print(client.place_stock_order(symbol="2330", side=Side.BUY,  qty=1, price="600", flag=StockFlag.MARGIN))     # 融資買進
print(client.place_stock_order(symbol="2330", side=Side.SELL, qty=1, price="600", flag=StockFlag.SHORT))      # 融券賣出
print(client.place_stock_order(symbol="2330", side=Side.SELL, qty=1, price="600", flag=StockFlag.DAY_SHORT))  # 無券賣出(當沖)

# %%
# --- 現貨:盤別(sPeriod)與集合競價/預約 ---
# 開盤(08:30-09:00)/收盤(13:25-13:30)集合競價沒有獨立單型:該時段送 ROD 限價單即參與撮合。
# 非交易時間送出的委託為預約單(回報 PreOrder=B;預約單另支援價格註記,見 docs)。
print(client.place_stock_order(symbol="2330", side=Side.BUY, qty=1, price="0", period=StockPeriod.AFTER_HOURS))          # 盤後定價(14:00-14:30,以收盤價撮合)
print(client.place_stock_order(symbol="2330", side=Side.BUY, qty=50, price="600", period=StockPeriod.ODD_LOT))           # 盤後零股(qty=股數)
print(client.place_stock_odd_lot_order(symbol="2330", side=Side.BUY, qty=50, price="600"))                               # 盤中零股(限價 ROD,qty=1~999 股)

# %%
# --- 期貨(官方 5-2 FUTUREORDER;"M" 市價 / "P" 範圍市價僅限 IOC/FOK) ---
print(client.place_future_limit(symbol="TX00", side=Side.BUY, qty=1, price="47000"))                                       # 限價 ROD
print(client.place_future_market(symbol="TX00", side=Side.SELL, qty=1))                                                    # 市價(自動 IOC)
print(client.place_future_order(symbol="TX00", side=Side.SELL, qty=1, price="P", trade_type=TradeType.IOC))                # 範圍市價
print(client.place_future_order(symbol="TX00", side=Side.BUY, qty=1, price="47000", day_trade=FuturesDayTrade.YES))        # 當沖
print(client.place_future_order(symbol="TX00", side=Side.SELL, qty=1, price="47000", new_close=FuturesNewClose.CLOSE))     # 平倉
print(client.place_future_order(symbol="TX00", side=Side.BUY, qty=1, price="47000", reserved=FuturesReserved.RESERVED))    # T盤預約

# %%
# --- 期貨價差單(symbol="近月/遠月",side 為近月方向) ---
print(client.place_future_limit(symbol="TX09/10", side=Side.SELL, qty=1, price="164"))

# %%
# --- 選擇權(SendOptionOrder,共用 FUTUREORDER;代碼從 option 商品清單取得) ---
print(client.place_option_order(symbol="TXO47000I6", side=Side.BUY, qty=1, price="885"))

# %%
# --- 每秒委託保護(官方 4-2-4/4-2-5;超限鎖定,unlock_order 解鎖) ---
print(client.set_max_order_qty_per_sec(OrderMarket.STOCK, 10))    # 證券每秒最多 10 張/股
print(client.set_max_order_count_per_sec(OrderMarket.FUTURES, 3)) # 期貨每秒最多 3 筆
# client.unlock_order(OrderMarket.STOCK)                          # 被鎖定後解鎖

# %%
# --- 改單/刪單 ---
#   client.cancel_order_by_seq(seq_no)             # 以 13 碼序號刪單
#   client.cancel_order_by_book(book_no)           # 以委託書號刪單
#   client.cancel_orders_by_symbol("2330")         # 刪該商品全部;symbol 留空 = 帳號全部
#   client.decrease_order_by_seq(seq_no, decrease_qty=1)   # 減量
#   client.correct_price_by_seq(seq_no, price="601")       # 改價

# %%
