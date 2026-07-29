# capital-api-sdk

群益證券/期貨 `SKCOM.dll` 的 Python wrapper，把登入、帳務查詢、報價查詢、即時報價、下單與回報包成容易使用的 SDK。

依官方 `CapitalAPI 2.13.58` 手冊與 Python 範例實作。報價與帳務查詢已在正式環境以 76 組參數組合實測（2026-07-28，現貨/期貨/價差 × snapshot/ticks/orderbook/kline，含盤後回補驗證）；下單方法對齊官方範例但**未實測**，實單前請先小量驗證。

## 環境需求

- Windows，已安裝並註冊群益 `SKCOM.dll`（Python bitness 需與 DLL 相符）。
- Python `>=3.10`。

```powershell
python -c "import struct; print(struct.calcsize('P') * 8, 'bit')"   # 檢查 bitness
pip install -e .                                                     # 安裝本專案
pip install pandas                                                   # examples 顯示 DataFrame 用（選裝）
```

## 環境變數

複製 `.env.example` 成 `.env` 填入帳密與 DLL 路徑（`.env` 已在 `.gitignore`，勿提交）：

```ini
CAPITAL_USER_ID=YOUR_USER_ID
CAPITAL_PASSWORD=YOUR_PASSWORD
CAPITAL_SKCOM_DLL=C:\path\to\SKCOM.dll
CAPITAL_AUTHORITY=PROD
CAPITAL_LOG_PATH=C:\path\to\logs
CAPITAL_CERT_ID=YOUR_CERT_ID
```

## 快速開始

```python
from capital_api_sdk import CapitalClient

client = CapitalClient.from_env(enable_live_order=False)  # False = 下單只回 dry-run payload
client.login(read_cert=True, connect_reply=True)          # connect_reply=True 才收得到委託回報

for account in client.get_accounts():
    print(account)
```

## 帳務查詢

```python
from capital_api_sdk import fetch_account_snapshot

snapshot = fetch_account_snapshot(client, include=None)   # None = 全部區塊
# 每張表都是 dict of dict，可直接 pd.DataFrame.from_dict(table, orient="index")
```

| include | 查詢內容 |
|---|---|
| `None` | 全部。 |
| `"account"` | 帳號與一戶通餘額/購買力。 |
| `"positions"` | 現貨庫存＋期貨部位＋期貨權益數。 |
| `"orders"` | 掛單（OnNewData 回報 cache）。 |
| `"stock"` / `"future"` | 只查現貨庫存 / 只查期貨部位＋權益數。 |

沒選到的區塊不查詢、不等待，是加速的關鍵。群益查詢會序列化處理，SDK 一律逐項查詢（並行會觸發 `1019 SK_ERROR_QUERY_IN_PROCESSING` 而掉資料）。

另有**同步回報查詢**（不依賴回報連線時間，適合確認當日委託/成交；官方限制每次查詢間隔 5 秒，SDK 已自動以 pump 等待與重試）：

```python
from capital_api_sdk import fetch_order_reports, fetch_fulfill_reports

open_orders = fetch_order_reports(client)     # GetOrderReport，預設 n_format=3（可取消的掛單）
fills = fetch_fulfill_reports(client)         # GetFulfillReport，預設 n_format=1（完整成交）
```

## 報價查詢

### 商品清單

```python
from capital_api_sdk import fetch_quote_symbol_lists

symbols = fetch_quote_symbol_lists(client, "tradable")   # listed + otc + future-market
# 也可用 "stock" / "future" / "option" / "all"、market number、或混用 ["listed", 2]
```

期貨**價差商品**（`TX08/09`、`CDF08/09`…）就在期貨市場清單（`future`）內，代碼含 `/`，會隨換月變動，使用前先重查。

### 一次性報價（最新狀態）

```python
from capital_api_sdk import fetch_latest_quotes

res = fetch_latest_quotes(
    client,
    ["2330", "TX00", "TX08/09"],              # 現貨/期貨/價差可混在同一次查詢
    data=("snapshot", "ticks", "orderbook"),  # 任意子集，或 "live"
    timeout_sec=5.0,
    max_ticks=1,                              # 每檔只留最新 1 筆成交；None = 保留全部回補
)
res.snapshots["2330"].close   # Decimal
res.ticks["TX00"]             # list[QuoteTick]（tick.history=True 表示當日回補）
res.order_books["TX08/09"]    # QuoteBest5 五檔
```

這是真正的一次性查詢（訂閱→伺服器立即推當前狀態→讀取→取消），資料齊全就提前返回，同一 process 第二次呼叫起通常 <1 秒。**盤後也可查**：snapshot 回最後狀態、ticks 回補當日成交明細（最後一筆即收盤撮合）。

單檔便捷包裝：`fetch_quote_snapshot()` / `fetch_quote_ticks()` / `fetch_order_book()` / `fetch_live_quote()`。

### 時間窗收集與串流

```python
from capital_api_sdk import fetch_realtime_quotes, stream_realtime_quote_events, compact_quote_stream_event

# 收集 N 秒內的所有更新（預設排除當日回補，只留時間窗內的 tick）
res = fetch_realtime_quotes(client, "TX00", data=("snapshot", "ticks"), seconds=3.0)

# 持續串流（seconds=None 直到中斷）；include_history=False 只吐即時事件
for event in stream_realtime_quote_events(client, ["2330"], seconds=20.0, include_history=False):
    print(event.kind, compact_quote_stream_event(event))
```

### 歷史 K 線

```python
from capital_api_sdk import fetch_quote_history

rows = fetch_quote_history(client, "2330", days=30, line_type="day")       # day/week/month/minute
rows = fetch_quote_history(client, "TX00", days=3, line_type="minute",
                           minute_number=5, trade_session=0)               # 0=全盤(含夜盤) 1=僅日盤
```

資料停止進來（idle 1 秒）就提前返回，一般查詢 2 秒內完成。日期可用 `YYYYMMDD`、`YYYY-MM-DD` 或 `date` 物件。

### SKCOM 報價規則（官方 V2.13.58，已實測）

- `RequestStocks`（snapshot 訂閱）：**頁碼固定 1**、單頁最多 100 檔、**一條連線只能有一組訂閱**，重新呼叫會整組替換；帶其他頁碼回 `3006 SK_SUBJECT_QUOTE_PAGE_EXCEED`。
- `RequestTicks`（成交明細＋五檔）：頁碼從 0 起、一頁一檔；**首次訂閱會回補當日 tick**（`OnNotifyHistoryTicksLONG`，每檔每連線只回補一次，SDK 已處理快取與 `history` 標記）。
- `GetStockByNoLONG` 未訂閱時只回基本資料；**必須先 RequestStocks 才有即時值**（SDK 的 fetch 系列已自動處理）。
- 訂閱前需等商品檔載入完成（`OnConnection nKind=3003`；SDK 的 `ensure_quote_session` / `connect_quote` 已處理）。
- `WithMarketNo` 系列只支援盤中零股（5/6）與客製化商品（9/10），一般市場（含價差）不需要 market number。

### 期貨價差商品實測結論

| 資料 | 結果 |
|---|---|
| snapshot / ticks / orderbook | **正常**，與一般期貨相同路徑（`TX08/09`、`CDF08/09` 實測有值）。 |
| 歷史 K 線 | 請求成功但**回 0 筆**（SKCOM 伺服器限制，非參數問題）。 |

價差歷史成交請改用期交所「[前 30 個交易日期貨價差成交資料](https://www.taifex.com.tw/cht/3/futPrevious30DaysSpreadSalesData)」；盤中即時資料則從程式啟動後透過 SKCOM 訂閱累積。

## 下單與回報

下單集中在 `CapitalClient`。`enable_live_order=False`（預設）時所有下單方法只回傳 dry-run payload，不會送單；確認無誤後再改 `True`。

```python
from capital_api_sdk import CapitalClient, Side

client = CapitalClient.from_env(enable_live_order=False)
client.login(read_cert=True, connect_reply=True)

print(client.place_stock_limit(symbol="2330", side=Side.BUY, qty=1, price="600"))  # dry-run

client.place_stock_market(symbol="2330", side=Side.SELL, qty=1)
client.place_stock_odd_lot_order(symbol="2330", side=Side.BUY, qty=10, price="600")  # 盤中零股
client.place_future_limit(symbol="TX00", side=Side.BUY, qty=1, price="20000")
client.place_future_market(symbol="TX00", side=Side.SELL, qty=1)

client.cancel_order_by_seq("SEQ_NO")          # seq_no 可從掛單表取得
client.cancel_order_by_book("BOOK_NO")
client.cancel_orders_by_symbol("2330")        # symbol 留空 = 該帳號全部
client.decrease_order_by_seq("SEQ_NO", decrease_qty=1)
client.correct_price_by_seq("SEQ_NO", price="601")
```

委託/成交回報兩種來源：即時回報走 `SKReplyLib.OnNewData` 進 `client.hub`（需 `connect_reply=True`），另可用 `fetch_order_reports()` / `fetch_fulfill_reports()` 同步查詢當日回報。

**注意：下單相關方法未在正式環境實測**，實單前請先用 SKCOMTester 或小量測試逐項確認。

## Examples

| 範例 | 用途 | 狀態 |
|---|---|---|
| `examples\01_login_accounts.py` | 登入並列出帳號。 | 已實測 |
| `examples\02_query_accounts.py` | 帳務 snapshot＋同步回報查詢。 | 已實測 |
| `examples\03_live_order_stock.py` | 現貨下單/刪單範本（Interactive 分段執行）。 | 下單未實測 |
| `examples\04_live_quote.py` | 串流即時報價（現貨/期貨/價差）。 | 已實測（8 組合） |
| `examples\05_query_quote.py` | 一次性報價查詢（Interactive 分段執行）：商品清單/即時/K 線。 | 已實測（文件內每個組合皆實跑） |
| `examples\06_live_order_reports.py` | 監聽委託/成交回報，不送單。 | 已實測（無單時空跑） |

## 專案結構

| 路徑 | 說明 |
|---|---|
| `capital_api_sdk\models.py` | dataclass models、enums、exceptions。 |
| `capital_api_sdk\parsers.py` | SKCOM raw 字串 / COM struct → model。 |
| `capital_api_sdk\com_client.py` | 底層封裝：config、COM event sinks、`EventHub` 快取、`CapitalClient`。 |
| `capital_api_sdk\quotes.py` | 高階報價：商品清單、K 線、一次性/時間窗/串流報價。 |
| `capital_api_sdk\quote_workflows.py` | preview / probe helpers（互動式研究用）。 |
| `capital_api_sdk\snapshot.py` | 帳務唯讀 snapshot 與同步回報查詢。 |
| `capital_api_sdk\public_data.py` | 群益公開網站行情端點（延遲資料，免登入）。 |
| `docs\official_mapping.md` | SDK ↔ 官方 API 對照與官方報價/查詢規則。 |
| `docs\usage_quickstart.md` | 最小可用範例。 |

## 驗證

```powershell
python -m compileall capital_api_sdk examples
python -c "import capital_api_sdk"
python examples\01_login_accounts.py
```

需連線 SKCOM 的功能只能在有 DLL、帳號與憑證的 Windows 環境實測。

## 已知限制

- 期貨**價差商品 K 線**伺服器回 0 筆（即時資料正常，見上方價差章節）。
- 當日無成交的冷門商品（如深月價差），ticks / orderbook 會等滿 timeout 後回空，屬正常行為。
- 訂閱**不存在的商品代碼**時，官方 API 靜默略過、不回錯誤；snapshot 會是無資料狀態（`has_data=False`）。
- SKCOM 的 tick 回補**每檔商品每連線只回補一次**；SDK 以 hub 快取處理同一 process 的重複查詢（`fetch_latest_quotes` 因此預設 `clear=False`）。
- 群益帳務查詢會**序列化處理**：並行送查詢會觸發 `1019 SK_ERROR_QUERY_IN_PROCESSING` 而掉資料，SDK 一律逐項查詢。
- 盤中零股與客製化市場（WithMarketNo 5/6/9/10）路徑保留但未實測。
- **下單、刪單、改價、減量未實測**；實單前請先用 SKCOMTester 或小量測試逐項確認。

## 常見問題

| 問題 | 處理方式 |
|---|---|
| `ModuleNotFoundError: capital_api_sdk` | 在專案根目錄 `pip install -e .`。 |
| `REGDB_E_CLASSNOTREG` / `WinError -2147221164` | 檢查 `SKCOM.dll` 是否註冊、路徑正確、Python bitness 相符。 |
| `3006 SK_SUBJECT_QUOTE_PAGE_EXCEED` | RequestStocks 頁碼超限：固定用頁 1（SDK fetch 系列已處理）。 |
| `3031 SK_SUBJECT_NO_RELATED_MARKET_STOCKS` | 未簽署證券/期貨 API 下單聲明書，對應市場商品檔不會下載。 |
| 回報查詢回 `M999` | 查詢間隔未滿 5 秒或前次查詢未完成；等待期間要 pump（SDK 已處理）。 |
| 查不到 ticks / orderbook | 確認商品代碼（價差換月）、交易時段、`ensure_quote_session` 是否成功。 |
| K 線為空 | 確認商品與日期區間；價差商品 K 線不支援（見上）。 |
| 掛單查詢不完整 | OnNewData cache 需 `connect_reply=True` 且 pump；或改用 `fetch_order_reports()`。 |
