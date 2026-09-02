# Quickstart

```powershell
python -m capital_api_sdk.doctor   # 先確認環境:bitness / DLL / COM 註冊 / comtypes 快取
```

```python
from capital_api_sdk import (
    CapitalClient, Side,
    fetch_account_snapshot, fetch_order_reports,
    fetch_latest_quotes, fetch_quote_history, fetch_quote_symbol_lists,
)

client = CapitalClient.from_env(enable_live_order=False)   # False = 下單只回 dry-run
client.login(read_cert=True, connect_reply=True)
# 純下單/回報程序請用 client.login(quote_connection=False), 不佔用報價連線額度(每帳號僅 2 條)

# 帳務(唯讀)
snapshot = fetch_account_snapshot(client, include=["balance", "positions"])
open_orders = fetch_order_reports(client)      # 同步查當日可取消掛單(官方 5-4-4 查詢格式,含零股)
live_open = client.get_open_orders()           # OnNewData cache, 已依成交/取消沖銷

# 報價:一次性查詢(現貨/期貨/價差可混合;盤後也可查)
res = fetch_latest_quotes(client, ["2330", "TX00", "TX08/09"], data="live", max_ticks=1)
print(res.snapshots["2330"].close, res.ticks["TX00"], res.order_books["TX08/09"])

# 歷史 K 線(價差商品不支援,回 0 筆)
rows = fetch_quote_history(client, "2330", days=30, line_type="day")

# 商品清單(價差代碼在 "future" 清單內,含 "/")
symbols = fetch_quote_symbol_lists(client, "tradable")

# 下單(dry-run;實單需 enable_live_order=True,未實測請先小量驗證)
print(client.place_stock_limit(symbol="2330", side=Side.BUY, qty=1, price="600"))
```

詳細說明見 [README](../README.md);SDK 與官方 API 對照、回報/帳務欄位表見 [official_mapping.md](official_mapping.md)。
