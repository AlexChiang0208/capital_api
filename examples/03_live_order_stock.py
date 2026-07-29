# %%
"""
Live order template (VS Code Python Interactive).

實單前務必:
1. 先用 SKCOMTester.exe 確認登入 / 憑證 / 下單皆正常。
2. 確認帳號、商品、數量、價格。
3. enable_live_order=False 時所有下單方法只回傳 dry-run payload,不會送單;
   確認 payload 無誤後再改 enable_live_order=True。
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT))

from capital_api_sdk import CapitalClient, Side, fetch_account_snapshot, fetch_order_reports  # noqa: E402

# enable_live_order=True 會實際送單(正式環境),請小心使用!
client = CapitalClient.from_env(enable_live_order=True)
client.login(read_cert=True)

print("正式環境,請小心使用!")

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
snapshot = fetch_account_snapshot(client, include=["orders"])
pd.DataFrame(snapshot["stock_open_orders"]).T

# %%
# 查看掛單 — 方法二:同步 GetOrderReport(n_format=3 可取消的掛單;官方限制查詢間隔 5 秒)
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
# 其他可用方法(未實測,實單前請先小量驗證):
#   client.place_stock_market(symbol=..., side=..., qty=...)          # 現貨市價
#   client.place_stock_odd_lot_order(symbol=..., side=..., qty=..., price=...)  # 盤中零股
#   client.place_future_limit(symbol="TX00", side=Side.BUY, qty=1, price="20000")
#   client.place_future_market(symbol="TX00", side=Side.SELL, qty=1)
#   client.decrease_order_by_seq(seq_no, decrease_qty=1)              # 減量
#   client.correct_price_by_seq(seq_no, price="601")                  # 改價

# %%
