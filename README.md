# capital-api-sdk

群益證券/期貨 `SKCOM.dll` 的 Python wrapper，把登入、帳務查詢、報價查詢、逐筆串流、下單與回報包成容易使用的 SDK。

依官方 `CapitalAPI 2.13.58` 手冊與 Python 範例實作，欄位對應皆經官方手冊逐欄考證。報價、帳務、回報均在正式環境實測（現貨/期貨/價差 × snapshot/ticks/orderbook/kline，含盤中零股回報、部分成交沖銷、盤後回補驗證）；下單方法對齊官方範例但**未實測**，實單前請先小量驗證。

## 環境需求

- Windows，已安裝並註冊群益 `SKCOM.dll`（Python bitness 需與 DLL 相符）。
- Python `>=3.10`。

```powershell
pip install -e .                                # 安裝本專案（含 comtypes/pywin32/python-dotenv）
pip install pandas                              # examples 顯示 DataFrame 用（選裝）
python -m capital_api_sdk.doctor                # 環境診斷：bitness / DLL / COM 註冊 / comtypes 快取
```

`doctor` 會檢查 Python 與 DLL 的 bitness、COM 註冊狀態（HKCU 使用者層＋HKLM 機器層）、以及
**comtypes 產生的快取是否過期**（升級 SKCOM.dll 後的常見地雷）；加 `--clean` 可清掉過期快取，下次載入自動重建。

### SKCOM.dll 註冊

| 情境 | 指令 |
|---|---|
| **帳號沒有系統管理員權限**（公司電腦常態） | `regsvr32 /n /i:user "C:\path\to\SKCOM.dll"`（註冊到 HKCU 使用者層，優先生效） |
| 有系統管理員權限 | 以系統管理員身分執行元件資料夾內的 `install.bat` |

**警告：沒有管理員權限時，千萬不要直接跑一般的 `regsvr32 SKCOM.dll`**——ATL 註冊流程是
「先刪舊 key 再建新 key」，刪除會成功（使用者層的 key 刪得掉）、重建卻因權限不足失敗，
結果是把原本可用的註冊刪壞（`類別未登錄`）。發生時用上表的 per-user 指令即可修復。

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

每個帳號**只有 2 條報價連線額度**；純下單/回報的程序請用
`client.login(quote_connection=False)`（走官方 `LoginSetQuote`），把報價連線留給報價程序。

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
| `"orders"` | 掛單（OnNewData 回報 cache，已依成交/取消沖銷）。 |
| `"stock"` / `"future"` | 只查現貨庫存 / 只查期貨部位＋權益數。 |

沒選到的區塊不查詢、不等待，是加速的關鍵。群益查詢會序列化處理，SDK 一律逐項查詢（並行會觸發 `1019 SK_ERROR_QUERY_IN_PROCESSING` 而掉資料）。

掛單視圖 `client.get_open_orders()` 以官方回報語意彙總：委託(N)/改量(U)/改價(P) 取最新、
取消(C)與動態退單(S)直接結案、**分批成交(D)逐筆沖銷剩量**、失敗單(OrderErr Y/T)不列入。

另有**同步回報查詢**（不依賴回報連線時間；官方限制每次查詢間隔 5 秒，SDK 已自動以 pump 等待與重試）。
回傳為**官方專用查詢格式**（5-4-4 / 5-4-5，與 OnNewData 不同）：委託列含狀態（全部成交/部分成交可消/委託失敗…）、成交量、剩餘量；成交列含成交價金與預估手續費/交易稅。盤中零股也查得到：

```python
from capital_api_sdk import fetch_order_reports, fetch_fulfill_reports

open_orders = fetch_order_reports(client)     # GetOrderReport，預設 n_format=3（可取消的掛單）
fills = fetch_fulfill_reports(client)         # GetFulfillReport，預設 n_format=1（完整成交）
rows = client.get_order_report(n_format=1)    # 物件形式：QueryOrderReport（row.status_name / is_open）
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

這是真正的一次性查詢（訂閱→伺服器立即推當前狀態→讀取→取消），資料齊全就提前返回，同一 process 第二次呼叫起通常 <1 秒。**盤後也可查**：snapshot 回最後狀態、ticks 回補當日成交明細。

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

長時間串流已內建保護：**斷線重連後自動重新訂閱**（偵測新的 STOCKS_READY 事件）、
事件快取有上限（tick 100 萬筆 / quote與best5 各 10 萬筆，舊事件自動汰除，cursor 不受影響）。

### 逐筆成交串流（TickStream）

```python
from capital_api_sdk import TickStream, ticks_to_dataframe

with TickStream(client, "TX00", market="future") as stream:
    first = stream.collect(60.0, idle_stop=True)   # 首批 = 當日回補全量
    frame = ticks_to_dataframe(first.ticks)        # ts/ptr/price/qty/bid/ask...
    while True:
        batch = stream.collect(5.0)                # 之後每批只有新增的 tick
```

`ptr` 是交易所側流水序號，序號有洞代表漏 tick；`TickStream` 每批自動用
`SKQuoteLib_GetTickLONG` 補洞（重新訂閱不會重播回補，這是唯一補法），
並每 15 秒呼叫 `RequestServerTime` 保活、斷線重連後自動重訂閱（ptr 去重）。

### 歷史 K 線

```python
from capital_api_sdk import fetch_quote_history

rows = fetch_quote_history(client, "2330", days=30, line_type="day")       # day/week/month/minute
rows = fetch_quote_history(client, "TX00", days=3, line_type="minute",
                           minute_number=5, trade_session=0)               # 0=全盤(含夜盤) 1=僅日盤
```

資料停止進來（idle 1 秒）就提前返回，一般查詢 2 秒內完成。日期可用 `YYYYMMDD`、`YYYY-MM-DD` 或 `date` 物件。歷史 K 線（含分 K）需等收盤後統計（約 14:45）才含當日。
**成交量定義（實測）**：K 線 volume 只含盤中撮合量（分 K 加總＝日 K），不含鉅額/盤後等其他管道，因此會小於公開網站/交易所的當日總量；即時 `total_qty` 與逐筆 tick 加總一致。

### SKCOM 報價規則（官方 V2.13.58，已實測）

- `RequestStocks`（snapshot 訂閱）：**頁碼固定 1**、單頁最多 100 檔、**一條連線只能有一組訂閱**，重新呼叫會整組替換；帶其他頁碼回 `3006 SK_SUBJECT_QUOTE_PAGE_EXCEED`。
- `RequestTicks`（成交明細＋五檔）：**頁碼 0–49、一頁一檔**（同時最多 50 檔）；頁碼 50 是官方「取消訂閱」訊號，SDK 已擋下並提供 `cancel_ticks()`；**首次訂閱會回補當日 tick**（每檔每連線只回補一次，SDK 已處理快取與 `history` 標記）。
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

下單集中在 `CapitalClient`。`enable_live_order=False`（預設）時所有下單方法只回傳 dry-run payload，不會送單；確認無誤後再改 `True`。完整範本見 `examples\03_live_order_stock.py`（每種單型一格）。

```python
from capital_api_sdk import CapitalClient, Side, StockFlag, StockPeriod, OrderMarket

client = CapitalClient.from_env(enable_live_order=False)
client.login(read_cert=True, connect_reply=True)

# 現貨：限價 / 市價 / 價格特殊代碼（官方 5-4：M 平盤、H 漲停、L 跌停）
client.place_stock_limit(symbol="2330", side=Side.BUY, qty=1, price="600")
client.place_stock_market(symbol="2330", side=Side.SELL, qty=1)         # 市價（price 固定 0）
client.place_stock_limit_up(symbol="2330", side=Side.SELL, qty=1)       # 掛漲停價 "H"
client.place_stock_limit_down(symbol="2330", side=Side.BUY, qty=1)      # 掛跌停價 "L"
client.place_stock_at_reference(symbol="2330", side=Side.BUY, qty=1)    # 掛平盤價 "M"

# 現貨：信用/無券（sFlag）與盤別（sPeriod）
client.place_stock_order(symbol="2330", side=Side.BUY, qty=1, price="600", flag=StockFlag.MARGIN)   # 融資
client.place_stock_order(symbol="2330", side=Side.SELL, qty=1, price="600", flag=StockFlag.SHORT)   # 融券
client.place_stock_order(symbol="2330", side=Side.SELL, qty=1, price="600", flag=StockFlag.DAY_SHORT)  # 無券賣出
client.place_stock_order(symbol="2330", side=Side.BUY, qty=1, price="0", period=StockPeriod.AFTER_HOURS)  # 盤後定價
client.place_stock_order(symbol="2330", side=Side.BUY, qty=50, price="600", period=StockPeriod.ODD_LOT)   # 盤後零股
client.place_stock_odd_lot_order(symbol="2330", side=Side.BUY, qty=50, price="600")                       # 盤中零股

# 期貨/選擇權（"M" 市價、"P" 範圍市價僅限 IOC/FOK；價差單 symbol="近月/遠月"）
client.place_future_limit(symbol="TX00", side=Side.BUY, qty=1, price="20000")
client.place_future_market(symbol="TX00", side=Side.SELL, qty=1)
client.place_future_limit(symbol="TX09/10", side=Side.SELL, qty=1, price="164")   # 期貨價差
client.place_option_order(symbol="TXO47000I6", side=Side.BUY, qty=1, price="885") # 選擇權

# 改/刪單與每秒保護
client.cancel_order_by_seq("SEQ_NO")          # seq_no 可從掛單表取得
client.cancel_orders_by_symbol("2330")        # symbol 留空 = 該帳號全部
client.decrease_order_by_seq("SEQ_NO", decrease_qty=1)
client.correct_price_by_seq("SEQ_NO", price="601")
client.set_max_order_qty_per_sec(OrderMarket.STOCK, 10)   # 每秒委託量上限（超限鎖定，unlock_order 解鎖）
```

集合競價與預約單：開盤（08:30–09:00）/收盤（13:25–13:30）集合競價沒有獨立單型，該時段送 ROD 限價單即參與撮合；非交易時間送出的委託自動成為預約單（回報 `PreOrder=B`）。

委託/成交回報兩種來源：即時回報走 `SKReplyLib.OnNewData` 進 `client.hub`（需 `connect_reply=True`），另可用 `fetch_order_reports()` / `fetch_fulfill_reports()` 同步查詢當日回報（專用查詢格式，含盤中零股）。
OnNewData 的買賣別請用 `OrderEvent.buy_sell`（B/S）；原始 `side` 欄位是官方複合欄位（如 `S00R2` = 賣+現股+ROD+限價）。

**注意：查詢/保護設定已實測，送單本身未實測**，實單前請先用 SKCOMTester 或小量測試逐項確認。

## Examples

| 範例 | 用途 | 狀態 |
|---|---|---|
| `examples\01_login_accounts.py` | 登入並列出帳號。 | 已實測 |
| `examples\02_query_accounts.py` | 帳務 snapshot＋同步回報查詢。 | 已實測 |
| `examples\03_live_order_stock.py` | 下單範本（Interactive 分段執行，預設 dry-run）：現貨限價/市價/漲跌停/平盤、融資融券無券、盤後/零股、期貨/價差/選擇權、每秒保護。 | 送單未實測（dry-run 已驗證） |
| `examples\04_live_quote.py` | 串流即時報價（現貨/期貨/價差）。 | 已實測 |
| `examples\05_query_quote.py` | 一次性報價查詢（Interactive 分段執行）：商品清單/即時/K 線。 | 已實測 |
| `examples\06_live_order_reports.py` | 監聽委託/成交回報，不送單。 | 已實測 |
| `examples\07_live_tick_stream.py` | 逐筆成交串流：首批回補當日全量，之後每 N 秒印新增 DataFrame，並自動偵測/修補 ptr 缺口。需 pandas。 | 已實測 |

## 專案結構

| 路徑 | 說明 |
|---|---|
| `capital_api_sdk\models.py` | dataclass models、enums、exceptions。 |
| `capital_api_sdk\parsers.py` | SKCOM raw 字串 / COM struct → model（欄位對應經官方手冊考證）。 |
| `capital_api_sdk\com_client.py` | 底層封裝：config、COM event sinks、`EventHub` 快取、`CapitalClient`。 |
| `capital_api_sdk\quotes.py` | 高階報價：商品清單、K 線、一次性/時間窗/串流報價。 |
| `capital_api_sdk\tick_stream.py` | 逐筆成交增量串流：`TickStream`、ptr 缺口偵測與 `GetTickLONG` 修補、DataFrame 轉換。 |
| `capital_api_sdk\quote_workflows.py` | preview / probe helpers（互動式研究用）。 |
| `capital_api_sdk\snapshot.py` | 帳務唯讀 snapshot 與同步回報查詢。 |
| `capital_api_sdk\public_data.py` | 群益公開網站行情端點（延遲資料，免登入）。 |
| `capital_api_sdk\doctor.py` | 環境診斷：bitness / DLL / COM 註冊 / comtypes 快取（`--clean` 清過期快取）。 |
| `docs\official_mapping.md` | SDK ↔ 官方 API 對照、回報/帳務欄位表與官方規則。 |
| `docs\usage_quickstart.md` | 最小可用範例。 |

## 驗證

```powershell
python -m capital_api_sdk.doctor
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
- 期貨未平倉（GW 格式 1）**不含市價與浮動損益**（官方欄位即無此資料）。
- 盤中零股與客製化市場（WithMarketNo 5/6/9/10）報價路徑保留但未實測。
- **下單、刪單、改價、減量未實測**；實單前請先用 SKCOMTester 或小量測試逐項確認。

## 常見問題

| 問題 | 處理方式 |
|---|---|
| `ModuleNotFoundError: capital_api_sdk` | 在專案根目錄 `pip install -e .`。 |
| `REGDB_E_CLASSNOTREG` / `WinError -2147221164` | 跑 `python -m capital_api_sdk.doctor`：檢查 DLL 註冊、路徑、Python bitness。 |
| 升級 SKCOM.dll 後出現怪錯誤 | comtypes 快取過期：`python -m capital_api_sdk.doctor --clean`。 |
| `3006 SK_SUBJECT_QUOTE_PAGE_EXCEED` | RequestStocks 頁碼超限：固定用頁 1（SDK fetch 系列已處理）。 |
| `3031 SK_SUBJECT_NO_RELATED_MARKET_STOCKS` | 未簽署證券/期貨 API 下單聲明書，對應市場商品檔不會下載。 |
| 回報查詢回 `M999` | 查詢間隔未滿 5 秒或前次查詢未完成；等待期間要 pump（SDK 已處理）。 |
| 查不到 ticks / orderbook | 確認商品代碼（價差換月）、交易時段、`ensure_quote_session` 是否成功。 |
| K 線為空 | 確認商品與日期區間；價差商品 K 線不支援（見上）；當日 K 線約 14:45 後才有。 |
| 掛單查詢不完整 | OnNewData cache 需 `connect_reply=True` 且 pump；或改用 `fetch_order_reports()`。 |
| 長時間串流斷線 | SDK 已自動保活（15 秒 RequestServerTime）並在重連後自動重訂閱。 |
