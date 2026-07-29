"""Read-only account snapshot: accounts, balance, positions, rights, open orders."""
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capital_api_sdk import CapitalClient, fetch_account_snapshot, fetch_order_reports  # noqa: E402

# enable_live_order=False:本檔只做唯讀查詢,絕不送出任何下單/改單/刪單。
client = CapitalClient.from_env(enable_live_order=False)
# connect_reply=True 才會連上回報主機,當日委託(掛單)才能透過 OnNewData 累積進 cache。
client.login(read_cert=True, connect_reply=True)


def show_table(title: str, table: dict) -> None:
    """把一張 dict of dict 表印出來:有 pandas 就轉 DataFrame,否則逐列印。"""
    print(f"\n== {title} ==")
    if not table:
        print("(無資料)")
        return
    try:
        print(pd.DataFrame.from_dict(table, orient="index").to_string())
    except Exception:
        for key, row in table.items():
            print(f"[{key}] {row}")


# 想拿哪些就改這裡;None = 全部。
# 群組別名:account(帳戶+餘額)/ positions(庫存)/ orders(掛單)/ stock(現貨)/ future(期貨)
# 細項:accounts / balance / stock_positions / future_positions / future_rights / open_orders
# 例:只看餘額+現貨庫存 → INCLUDE = ["balance", "stock"];只看掛單 → INCLUDE = ["orders"]
INCLUDE = None

t0 = time.time()
snapshot = fetch_account_snapshot(client, include=INCLUDE)
print(f"\nfetch_account_snapshot 耗時:{time.time() - t0:.1f} 秒")

# 一戶通餘額 / 購買力(單筆 dict,有選到才印)
if "capital_pay_balance" in snapshot:
    print("\n== 一戶通餘額 / 購買力 ==")
    print(snapshot["capital_pay_balance"])

# 其餘皆為 dict of dict,可直接 pd.DataFrame.from_dict(table, orient="index")
for name, table in snapshot.items():
    if name == "capital_pay_balance":
        continue
    show_table(name, table)

# 另一種掛單查法:同步 GetOrderReport(n_format=3 只回可取消的掛單)。
# 不依賴回報連線時間,但官方限制每次查詢間隔 5 秒(SDK 已自動等待)。
show_table("order_reports (GetOrderReport, cancellable)", fetch_order_reports(client))
