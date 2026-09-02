# Official CapitalAPI 2.13.58 mapping notes

SDK 依官方 `CapitalAPI_2.13.58` 手冊（`策略王COM元件使用說明_V2.13.58.docx`、
`策略王COM元件使用說明_期貨新制商品報價元件.docx`）與 `PythonExampleV2` 範例實作。
欄位表皆自官方手冊逐欄考證，並以正式環境實測比對。

## Key method mapping

| Area | Official method/event | SDK wrapper |
|---|---|---|
| login | `SKCenterLib_SetAuthority` | `client.set_authority()` |
| login | `SKCenterLib_Login` | `client.login()` / `client.login_center()` |
| login（不佔報價連線） | `SKCenterLib_LoginSetQuote(id, pwd, "N")` | `client.login(quote_connection=False)` |
| login 失敗細節 | `SKCenterLib_GetLastLogInfo` | 自動附在 `ApiResult.broker_message` |
| order setup | `SKOrderLib_Initialize` | `client.initialize_order()` |
| cert | `ReadCertByID` | `client.initialize_order(read_cert=True)` |
| accounts | `GetUserAccount`, `OnAccount` | `client.get_accounts()` |
| reply | `SKReplyLib_ConnectByID` | `client.connect_reply()` |
| reply | `OnNewData` | `client.get_open_orders()` / `client.hub.raw_new_data` |
| order report query | `GetOrderReport`（同步、阻塞） | `client.get_order_report()` / `fetch_order_reports()` |
| fill report query | `GetFulfillReport`（同步、阻塞） | `client.get_fulfill_report()` / `fetch_fulfill_reports()` |
| quote connection | `SKQuoteLib_EnterMonitorLONG`, `SKQuoteLib_LeaveMonitor` | `client.connect_quote()`, `client.disconnect_quote()` |
| quote ready | `SKQuoteLib_IsConnected`, `OnConnection` | `client.is_quote_connected()`, `client.quote_connection_state()`, `client.is_quote_ready()`, `client.wait_quote_connected()` |
| 重連偵測 | `OnConnection nKind=3003` 次數 | `client.quote_ready_count()`（串流自動重訂閱用） |
| quote subscription | `SKQuoteLib_RequestStocks`, `OnNotifyQuoteLONG` | `client.subscribe_quotes()`, `client.get_latest_quote()` |
| 取消 quote 訂閱 | `SKQuoteLib_CancelRequestStocks` | `client.cancel_quotes()` |
| tick / best5 subscription | `SKQuoteLib_RequestTicks`, `OnNotifyTicksLONG`, `OnNotifyBest5LONG` | `client.subscribe_ticks()`, `client.get_ticks()`, `client.get_latest_best5()` |
| tick backfill | `OnNotifyHistoryTicksLONG` | 自動進 `client.hub`（`QuoteTick.history=True`） |
| tick 增量批次 / 缺口修補 | `SKQuoteLib_RequestTicks` + `SKQuoteLib_GetTickLONG` | `TickStream` / `stream_tick_batches()` |
| tick 單筆序號查閱 | `SKQuoteLib_GetTickLONG`（`SKTICK`） | `client.get_tick_by_index(..., ptr=, cache=)` |
| 連線保活 | `SKQuoteLib_RequestServerTime`, `OnNotifyServerTime` | `client.request_server_time()`（`TickStream` 每 15 秒自動呼叫） |
| live tick only（無回補） | `SKQuoteLib_RequestLiveTick` | `client.request_live_tick()` |
| stock list | `SKQuoteLib_RequestStockList`, `OnNotifyStockList` | `client.request_stock_list()` / `fetch_quote_symbol_lists()` |
| quote snapshot | `SKQuoteLib_GetStockByNoLONG` | `client.get_quote_snapshot()` |
| historical K-line | `SKQuoteLib_RequestKLineAMByDate`, `OnNotifyKLineData` | `client.request_kline()` / `fetch_quote_history()` |
| stock positions | `GetRealBalanceReport`, `OnRealBalanceReport` | `client.get_stock_positions()` |
| future positions | `GetOpenInterestGW(nFormat=1)`, `OnOpenInterest` | `client.get_future_positions()` |
| future rights | `GetFutureRights`, `OnFutureRights` | `client.get_future_rights()` |
| capital pay | `GetBalance` | `client.get_capital_pay_balance()` |
| stock order | `SendStockOrder` | `client.place_stock_order()`, `place_stock_limit()`, `place_stock_market()` |
| stock odd lot | `SendStockOddLotOrder` | `client.place_stock_odd_lot_order()` |
| future order | `SendFutureOrderCLR` | `client.place_future_order()`, `place_future_limit()`, `place_future_market()` |
| cancel | `CancelOrderBySeqNo` / `CancelOrderByBookNo` / `CancelOrderByStockNo` | `client.cancel_order_by_seq()` / `cancel_order_by_book()` / `cancel_orders_by_symbol()` |
| decrease / correct | `DecreaseOrderBySeqNo` / `CorrectPriceBySeqNo` | `client.decrease_order_by_seq()` / `correct_price_by_seq()` |
| 環境診斷 | （無官方對應） | `python -m capital_api_sdk.doctor` |

`GetBalanceQuery` 官方自 V2.13.54 起不再提供，SDK 已移除；現貨庫存請用 `GetRealBalanceReport`。

## 登入與連線規則

- 每個帳號**只有 2 條報價連線額度**（官方 V2.13.21 起）。純下單/回報程序請用
  `client.login(quote_connection=False)`（走 `SKCenterLib_LoginSetQuote(..., "N")`），
  把報價連線留給報價程序。
- 登入回 `2003 SK_WARNING_LOGIN_ALREADY` 視同成功（`is_login_result_ok`）。
- 登入前必須先掛好 `SKReplyLib.OnReplyMessage` 事件並回傳 `-1`（V2.13.17 起強制；
  SDK 在 `load()` 內先掛事件再登入）。
- 登入失敗時 `SKCenterLib_GetLastLogInfo()` 有進一步原因，SDK 自動附在
  `ApiResult.broker_message`。
- `SKCenterLib_Login` 必須在主執行緒呼叫；`EnterMonitorLONG` 可在子執行緒。

### OnConnection 連線狀態（nKind）

| nKind | 意義 | SDK 狀態 |
|---|---|---|
| 3001 | 已連線（商品檔下載中） | `connected` |
| 3002 | 正常斷線 | `disconnected` |
| 3003 | `SK_SUBJECT_CONNECTION_STOCKS_READY` 商品檔就緒，**此後才能訂閱/查詢** | `ready` |
| 3021 | 異常斷線 | `disconnected` |

- 異常斷線後**元件會自行重連**，重連完成會再收到一次 3003；但**先前的訂閱全部失效**。
  SDK 的 `TickStream` 與 `stream_realtime_quote_events` 透過 `quote_ready_count()`
  偵測新的 3003 並自動重新訂閱（tick 回補會在新連線重播一次，`TickStream` 以 ptr 去重）。
- `SKQuoteLib_IsConnected`：0 斷線 / 1 連線中 / 2 商品檔下載中。

## 官方報價規則（V2.13.58 手冊，實測確認）

- `SKQuoteLib_RequestStocks(psPageNo, bstrStockNos)`：psPageNo「請固定帶 1」，一般用戶頁碼上限 1；
  單頁最多 100 檔（超過的靜默捨棄）；不存在的代碼靜默略過；
  一組 SKQuoteLib 僅可擇一使用一個即時報價訂閱（重連即重置）。頁碼超限回 `3006`。
- `SKQuoteLib_RequestTicks(psPageNo, bstrStockNo)`：**頁碼 0–49、一頁一檔**（同時最多 50 檔 tick 訂閱）；
  **頁碼 50 = 官方取消訂閱訊號**（SDK 以 `cancel_ticks()` 取消並擋下頁碼 >49 的訂閱）；
  首次訂閱回補當日 tick（`OnNotifyHistoryTicksLONG`，同簽名）。
- `SKQuoteLib_RequestLiveTick`：同 RequestTicks 但不回補歷史，兩者擇一使用。
- tick 回補**沒有完成事件**（只有 K 線有 `OnKLineComplete`）。唯一可用的結束訊號是
  `OnNotifyHistoryTicksLONG` 停止觸發，因此 `TickStream.collect(idle_stop=True)` 以事件靜止判定。
- `nPtr` 是「第幾筆成交明細，由 0 開始」的交易所側流水序號，序號有洞即代表漏 tick。
  重新 `RequestTicks` **不會**重播回補（每檔每連線只回補一次），要補洞只能用
  `SKQuoteLib_GetTickLONG(sMarketNo, nIndex, nPtr)` 逐筆重讀。
- 手冊明文禁止在 `OnNotifyHistoryTicksLONG` / `OnNotifyTicksLONG` 事件內呼叫
  `GetTickLONG` / `GetStockByIndexLONG`，故 `TickStream` 的補洞一律在 pump 週期之間執行。
- `nSimulate=1` 為試撮揭示、非真實成交，但**會佔用 ptr 序號**；`TickStream` 預設輸出時濾除、
  缺口偵測時保留，避免把試撮列誤判成漏 tick。
- `nTimehms` 為 `hhmmss`；`nTimemillismicros` 前三位毫秒、後三位微秒（996886 = 996ms 886us），
  且官方註明「目前 solace 只提供證券商品」的次秒資料。
- `SKQuoteLib_RequestServerTime`：官方要求長連線**每 15 秒**呼叫一次，否則閒置連線可能被防火牆切斷。
- tick 事件本身不帶 `sDecimal`，價格需由 snapshot 的 `decimal_places` 還原；SDK event sink 固定以 2 位
  縮放，`TickStream` 會依 snapshot 修正非 2 位小數的商品。
- `SKQuoteLib_GetStockByNoLONG` / `GetStockByIndexLONG`：未訂閱即時報價時僅回商品基本資料
  （名稱、昨收等），須先 `RequestStocks` 才有即時值（`nTQty` 總量亦然）。
- `RequestStocksWithMarketNo` / `RequestTicksWithMarketNo`：僅支援盤中零股-上市(5)/上櫃(6)、
  客製化期貨(9)/客製化選擇權(10)；一般市場（含價差商品）不帶 market number。
- 市場別編號：上市 0、上櫃 1、期貨 2、選擇權 3、興櫃 4、盤中零股 5/6、客製化 9/10。
- 商品代號加 `AM` 後綴 = 純日盤行情（如 `TX00AM`）；AM 代碼不可下單。
- `RequestKLineAMByDate`：`sTradeSession` 0=全盤（期貨含前一日夜盤）、1=AM 盤。
  日 K 需等收盤後統計（約 14:45）才含當日。

## 回報與帳務欄位（官方手冊逐欄考證）

### OnNewData 委託/成交回報（4-3-g，48 欄，0-based）

| idx | 欄位 | idx | 欄位 | idx | 欄位 |
|---|---|---|---|---|---|
| 0 | KeyNo 原始13碼委託序號 | 20 | Qty（依 Type：委託量/成交量/減量數/剩量） | 40 | Reserved 盤別(A T盤/B T+1盤) |
| 1 | MarketType 市場別 | 21 | BeforeQty（**C/D 為空值**） | 41 | OrderEffective 有效委託日 |
| 2 | Type 委託種類 | 22 | AfterQty（**C/D 為空值**） | 42 | CallPut |
| 3 | OrderErr（N 正常/Y 失敗/T 逾時） | 23 | Date 交易日期 | 43 | OrderSeq 交易所單號(僅海期選) |
| 4 | Broker 分公司/IB | 24 | Time 交易時間(hh:mm:ss) | 44 | ErrorMsg（OrderErr=Y 時） |
| 5 | CustNo 交易帳號 | 25 | OkSeq 成交序號 | 45 | CancelOrderMarkByExchange |
| 6 | BuySell **複合欄位**（見下） | 26 | SubID 子帳 | 46 | ExchangeTandemMsg |
| 7 | ExchangeID 交易所 | 27 | SaleNo 營業員 | 47 | SeqNo 序號13碼 |
| 8 | ComId 商品代碼 | 28 | Agent 委託介面 | | |
| 9 | StrikePrice 履約價 | 29 | TradeDate 委託日期 | | |
| 10 | OrderNo 委託書號 | 30 | MsgNo 回報流水號 | | |
| 11 | Price（N=委託價 / D=成交價） | 31 | PreOrder（A 盤中/B 預約） | | |
| 12–19 | 分子/分母/觸發價（海期用） | 32–37 | 兩腳商品/年月/履約價 | 38–39 | ExecutionNo / PriceSymbol |

- **MarketType**：`TS` 證券 / `TA` 盤後 / `TL` 零股 / `TP` 興櫃 / `TC` 盤中零股 /
  `TF` 期貨 / `TO` 選擇權 / `OF` 海期 / `OO` 海選 / `OS` 複委託。
- **Type**：`N` 委託 / `C` 取消 / `U` 改量 / `P` 改價 / `D` 成交 / `B` 改價改量 / `S` 動態退單。
- **BuySell 為複合欄位**，第 1 碼固定 B(買)/S(賣)（SDK `OrderEvent.buy_sell`）。
  證券：`[0]`=B/S、`[1,2]`=00現股/03融資/04融券/08無券/20零股…、`[3]`=I/R/F、`[4]`=1市價/2限價。
  期選：`[0]`=B/S、`[1]`=Y當沖/N新倉/O平倉、`[2]`=I/R/F、`[3]`=1市價/2限價/3範圍市價/4停損限價。
  例（實測）：盤中零股賣出限價 ROD = `S00R2`。
- **成交(D)可分批**：每筆 D 的 Qty 是該筆成交量；C/D 報告的 Before/AfterQty 為空。
  `client.get_open_orders()` 依此彙總：最新委託類報告的 AfterQty 減去累計成交量，
  C/S 直接視為結案，OrderErr Y/T 不列入。

### GetOrderReport / GetFulfillReport（4-2-97 / 4-2-98 ＋ 5-4-4 / 5-4-5 格式）

- 同步阻塞查詢；rows 以 `\r\n` 分隔；`M003` 查無資料、`M999` 查詢錯誤。
- **rows 使用專用查詢格式（5-4-4 委託 / 5-4-5 成交），與 OnNewData 完全不同**，
  SDK 以 `QueryOrderReport` / `QueryFillReport` 解析（實測與官方欄位表逐欄核對）。
- **限制每次查詢間隔 5 秒**，且等待期間必須 pump COM 訊息，否則前次查詢不會標記完成、一直回 `M999`
  （SDK `_sync_report_query` 已自動處理間隔與重試）。
- 帳號必須是 TS/TF 交易帳號（一戶通帳號回 `M999 ... is invalid`）。
- 手冊備註寫「回報不含盤中零股」，**實測 2.13.58 查得到盤中零股**（盤別欄 = `F`）。
- `GetOrderReport` nFormat：1 全部 / 2 有效 / 3 可消 / 4 已消 / 5 已成 / 6 失敗 / 7 合併同價格 / 8 合併同商品 / 9 預約
  （7/8 為合併格式，欄位不同，讀 `row.fields`/`raw`）。
- `GetFulfillReport` nFormat：1 完整 / 2 合併同書號 / 3 合併同價格 / 4 合併同商品 / 5 T+1 成交。

**5-4-4 委託查詢列（0-based，SDK 具名欄位）**：0 市場別(TW/TS/TF/OS/OF)、1 商品別(STO 股票/FUT 期貨/OPT 選擇權)、
2 交易所別(TSEA 上市/TSEB 上櫃/OTC 興櫃/TAIFEX)、3 分公司、5 帳號、7 委託書號、8 13碼流水號、
**10 委託狀態**、11/12 委託日期/時間、15 商品代號、22 買賣別(B/S)、**23 盤別**、**24 證券委託條件**、
25 委託條件(0 ROD/1 GTC/2 開盤/3 IOC/4 FOK/7 收盤)、26 委託方式(1 市價/2 限價/3 範圍市價)、
27 委託價、29 有效量、30 原始量、31 成交量、32 剩餘量、33 當沖(Y/N/O/A)、35 錯誤回報(Y/N)、
37 交易單位股數、38 預約單價格註記、43 成交均價、51 取消總量、52/53 成交日期/時間、67 委託時間(hhmmssfff)。

- 委託狀態：`0` 預約 / `2` 全部成交 / `3` 全部取消 / `4` 部分成交剩餘已取消 /
  `5` 部分成交剩餘可取消 / `6` 委託失敗 / `7` 委託成功 / `8` 取消失敗 / `9` 取消中 / `F*` 動態退單。
- 盤別：`A` 一般 / `B` 盤後 / `C` 零股 / `D` 拍賣 / `E` 鉅額 / `F` 盤中零股 / `G` 標借 / `H` 標購 / `I` 證金標購。
- 證券委託條件：`0` 現股 / `1` 代資 / `2` 代券 / `3` 融資 / `4` 融券 / `5,6` 借券賣出 / `8` 無券賣出。
- 預約單價格註記（僅證券預約單）：空白 委託價 / `0` 平盤(昨收) / `1` 漲停 / `2` 跌停 /
  `h` 漲停下一檔 / `l` 跌停上一檔 / `C` 漲 1/2 / `c` 跌 1/2。

**5-4-5 成交查詢列（0-based，實測無「商品名稱」欄）**：0-7 同上、8 成交序號、9/10 成交日期/時間、
12 商品代號、21 買賣別、22 盤別、23 證券委託條件、25 成交價、26 成交量、30 委託方式、
**33 預估手續費（證券 1.425‰，實測吻合）**、**34 預估交易稅（1‰ 或 3‰）**、36/37 委託日期/時間、
40 交易單位股數、43 成交價金、49 成交時間(hhmmssfff)。

### OnRealBalanceReport 現貨庫存（4-2-c，19 欄，0-based）

0 股票代號、1 庫存種類(T 集保/C 融資/L 融券)、2 資額度原始、3 資額度可用、
4 券額度原始、5 券額度可用、**6 昨日庫存**、**7 今日委買**、**8 今日委賣**、
**9 今日買進成交**、**10 今日賣出成交**、**11 資券可回補/集保可賣出**、12 可資沖股數、
13 可券沖股數、**14 即時庫存**、15 (忽略)、16 即時個股維持率、**17 LOGIN_ID**、**18 ACCOUNT_NO**。
查詢結束回 `##` 開頭列。注意 7/8 是「委託」量、9/10 才是「成交」量（實測驗證）。

### OnOpenInterest 期貨未平倉（GetOpenInterestGW nFormat=1，10 欄，0-based）

0 市場別(TM)、1 帳號、2 商品、3 買賣別、4 未平倉部位、5 當沖未平倉部位、
6 平均成本(小數已處理)、7 單口手續費、8 交易稅(萬分之X)、9 LOGIN_ID。
GW 格式 1 **不含市價與浮動損益**；查無資料回 `001,查無資料,帳號`。

### OnFutureRights 期貨權益數（41 欄，0-based，SDK 取用欄位）

6 權益數、7 超額保證金、13 原始保證金、14 維持保證金、17 委託保證金、
25 幣別、31 可用餘額、34 風險指標、39 LOGIN_ID、40 ACCOUNT_NO（完整 41 欄見手冊 4-2-i）。

## 期貨價差商品（實測）

- 價差代碼（`TX08/09`、`MTX08/09`、`CDF08/09`…）在**期貨市場清單（market 2）**內；
  market 9（客製化期貨）只有特殊商品。
- snapshot / ticks / best5 與一般期貨同路徑，實測有值。
- `RequestKLineAMByDate` 對價差代碼請求成功但回 0 筆（伺服器限制，非參數問題）；
  歷史請改用期交所「前 30 個交易日期貨價差成交資料」：
  <https://www.taifex.com.tw/cht/3/futPrevious30DaysSpreadSalesData>

## Enum mapping from official Config.py

### Stock

- `sPrime`: listed/OTC = `0`, emerging = `1`
- `sPeriod`: regular = `0`, after-hours = `1`, odd-lot = `2`, intraday odd-lot = `4`
- `sFlag`: cash = `0`, margin = `1`, short = `2`, day-short/no-borrow = `3`
- `sBuySell`: buy = `0`, sell = `1`
- `nTradeType`: ROD = `0`, IOC = `1`, FOK = `2`
- `nSpecialTradeType`: market = `1`, limit = `2`

### Futures

- `sBuySell`: buy = `0`, sell = `1`
- `sDayTrade`: no = `0`, yes = `1`
- `sNewClose`: new = `0`, close = `1`, auto = `2`
- `sTradeType`: ROD = `0`, IOC = `1`, FOK = `2`
- `sReserved`: regular = `0`, reserved = `1`
- 期貨價格字串：`M` = 市價、`P` = 範圍市價，其餘為限價數字

### Rights

- `GetFutureRights` coin type: all = `0`, TWD = `1`, RMB = `2`

## 下單參數完整定義（官方 5-2 / 5-4 物件，功能已實作、送單未實測）

### 證券 STOCKORDER（5-4）

| 欄位 | 值 | SDK |
|---|---|---|
| `bstrPrice` | 限價數字；或 **`M` 參考價(昨收)、`H` 漲停價、`L` 跌停價**；市價單固定填 `0` | `price=` / `place_stock_limit_up()` / `place_stock_limit_down()` / `place_stock_at_reference()` |
| `nSpecialTradeType` | 1 市價（Price 須為 0）/ 2 限價（Price 不可為 0） | `price_type=StockPriceType` |
| `nTradeType` | 0 ROD / 1 IOC / 2 FOK（證券逐筆） | `trade_type=TradeType` |
| `sFlag` | 0 現股 / 1 融資 / 2 融券 / 3 無券賣出 | `flag=StockFlag`（信用戶/簽署後可用） |
| `sPeriod` | 0 盤中 / 1 盤後定價 / 2 盤後零股 / 4 盤中零股 | `period=StockPeriod`；盤中零股用 `place_stock_odd_lot_order` |
| `sPrime` | 0 上市上櫃 / 1 興櫃 | `prime=StockPrime` |
| `nQty` | 整股＝張數；零股＝股數（盤中零股 1–999 股） | `qty=` |

- 盤中零股（5-4-2）僅現股、限價、ROD（實測回報 BuySell 複合欄 `S00R2` 佐證）。
- **集合競價**：開盤（08:30–09:00 收單、09:00 撮合）與收盤（13:25–13:30）沒有獨立單型，
  該時段送 ROD 限價單即參與集合競價。
- **預約單**：非交易時間送出的委託自動成為預約單（OnNewData `PreOrder=B`、查詢狀態 `0`）；
  證券預約單支援價格註記（平盤/漲停/跌停/漲停下一檔…，見 5-4-4 欄 38），SDK 送單時
  以 `price="M"/"H"/"L"` 即可涵蓋常用情境。

### 期貨/選擇權 FUTUREORDER（5-2）

| 欄位 | 值 | SDK |
|---|---|---|
| `bstrPrice` | 限價數字；或 **`M` 市價、`P` 範圍市價——僅限 IOC/FOK**（ROD 不可用代碼） | `price=`；`place_future_market()` 自動帶 IOC |
| `sTradeType` | 0 ROD / 1 IOC / 2 FOK | `trade_type=` |
| `sNewClose` | 0 新倉 / 1 平倉 / 2 自動 | `new_close=FuturesNewClose` |
| `sDayTrade` | 0 否 / 1 當沖（限可當沖商品） | `day_trade=FuturesDayTrade` |
| `sReserved` | 0 盤中(T＋T+1) / 1 T盤預約（限 SendFutureOrderCLR） | `reserved=FuturesReserved` |
| `bstrStockNo` | 商品代碼；**價差單填「近月/遠月」如 `TX09/10`，`sBuySell` 為近月方向** | `place_future_limit(symbol="TX09/10", ...)` |

- 選擇權下單 `SendOptionOrder` 共用 FUTUREORDER：SDK `place_option_order()`。
- 選擇權複式單（兩腳 `bstrStockNo2`/`sBuySell2`，價差價 = 兩腳價差）SDK 未包裝，手冊 5-2 有詳細填法。
- 期權另可用 `bstrCIDTandem` + `bstrSettlementMonth`（如 FITX + 202609）指定契約。

### 每秒委託保護（4-2-4 / 4-2-5，已實測）

`SetMaxQty(nMarketType, qty)` / `SetMaxCount(nMarketType, count)`：**每秒**委託量/筆數上限，
超限即鎖定該市場下單、需 `UnlockOrder(nMarketType)` 解鎖；<=0 表示無限制。
nMarketType：0 TS / 1 TF / 2 TO / 3 OS / 4 OF / 5 OO（SDK `OrderMarket` enum，
`set_max_order_qty_per_sec` / `set_max_order_count_per_sec` / `unlock_order`）。

### 其他下單補充

- 非同步委託（`bAsyncOrder=True`）：呼叫立即返回，訊息為 ThreadID；
  實際結果由 `OnAsyncOrder(nThreadID, nCode, bstrMessage)` 回傳（SDK 收進
  `client.hub.async_order_results`）。同步委託則直接在回傳訊息取得 13 碼委託序號/錯誤。
- 回傳值 0 僅表示「成功送至交易所」，交易結果請以回報為準（官方 V2.13.58 說明）。
- 智慧單（STP 停損 / MST 移動停損 / MIT 觸價 / OCO）各有獨立函式與刪單函式
  （`SendFutureSTPOrderV1`、`SendFutureMSTOrderV1`、`SendFutureOCOOrderV1`…），SDK 未包裝；
  欄位重點：觸發價 `bstrTrigger`（不可 0、不可用 P 代碼）、移動停損另有 `bstrMovingPoint`、
  MIT 需自填 `bstrDealPrice` 與 `nTriggerDirection`(1 GTE/2 LTE)；
  智慧單的 `sTradeType` 代碼是 **0 ROD / 3 IOC / 4 FOK**（與一般單不同）。

## 環境注意事項

- SKCOM.dll 為 ActiveX COM 元件，需註冊後才能使用；Python bitness 必須與 DLL 相符。
  - 有管理員權限：以系統管理員執行元件資料夾的 `install.bat`（機器層 HKLM；
    32 位元 DLL 在 64 位元 Windows 要用 `SysWoW64\regsvr32.exe`）。
  - **無管理員權限：`regsvr32 /n /i:user "SKCOM.dll"`** 註冊到 HKCU 使用者層；
    HKCR 合併視圖中 HKCU 優先於 HKLM，效果等同。
  - **勿在無管理員權限下跑一般 `regsvr32`**：ATL 註冊先刪後建，刪除（HKCU 層）會成功、
    重建（HKLM 層）會因權限失敗，導致既有註冊被刪壞（`REGDB_E_CLASSNOTREG 類別未登錄`）；
    以 per-user 指令重註冊即可修復（實測驗證）。
- **升級 SKCOM.dll 後 comtypes 產生的快取模組會過期**，可能出現難解錯誤；
  `python -m capital_api_sdk.doctor --clean` 會檢查並清掉舊快取（下次 `load()` 自動重建）。
- 在 VM / 容器內執行請確保時鐘同步（Hyper-V 時間漂移會影響交易時間戳）。
  官方僅支援實體 Windows；Wine / 容器方案社群實驗均未成功，不建議。
