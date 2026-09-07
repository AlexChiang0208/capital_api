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

## 兩個最常踩的坑

- **訂閱成功卻收不到任何報價**: 多半是帳號沒有該市場行情權限(純期貨帳號訂 `2330` 最常見)。
  訂閱會回 `SK_SUCCESS` 但事件永不觸發,屬靜默失敗。先用
  `fetch_quote_symbol_lists(client, "stock")` 驗證,回 0 筆就是沒權限,改用期貨代碼或換帳號。
- **帳務查詢要間隔 5 秒**: 同類查詢連續呼叫會被元件拒絕(回 1019、不觸發事件)。SDK 會自動等滿間隔再查,
  收不到結束標記會重試一次,仍失敗丟 `CapitalApiError`(不會回空清單冒充「沒部位」)。
  SDK 的等待是等官方結束標記(`##` / `OnComplete`),不是固定 sleep,`wait_sec` 只是等待上限。
- **一次性報價只要快照**: `fetch_latest_quotes(..., data="snapshot")` 有 last / bid / ask / 漲跌停;
  `ticks` / `orderbook` 會讓 SKCOM 回補整天成交明細(上萬到十幾萬筆事件),只在真的需要時用。

詳細說明見 [README](../README.md);SDK 與官方 API 對照、回報/帳務欄位表見 [official_mapping.md](official_mapping.md)。
