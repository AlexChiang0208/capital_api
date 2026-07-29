# Official CapitalAPI 2.13.58 mapping notes

SDK 依官方 `CapitalAPI_2.13.58` 手冊（`策略王COM元件使用說明_V2.13.58.docx`、
`策略王COM元件使用說明_期貨新制商品報價元件.docx`）與 `PythonExampleV2` 範例實作。

## Key method mapping

| Area | Official method/event | SDK wrapper |
|---|---|---|
| login | `SKCenterLib_SetAuthority` | `client.set_authority()` |
| login | `SKCenterLib_Login` | `client.login()` / `client.login_center()` |
| order setup | `SKOrderLib_Initialize` | `client.initialize_order()` |
| cert | `ReadCertByID` | `client.initialize_order(read_cert=True)` |
| accounts | `GetUserAccount`, `OnAccount` | `client.get_accounts()` |
| reply | `SKReplyLib_ConnectByID` | `client.connect_reply()` |
| reply | `OnNewData` | `client.get_open_orders()` / `client.hub.raw_new_data` |
| order report query | `GetOrderReport`（同步、阻塞） | `client.get_order_report()` / `fetch_order_reports()` |
| fill report query | `GetFulfillReport`（同步、阻塞） | `client.get_fulfill_report()` / `fetch_fulfill_reports()` |
| quote connection | `SKQuoteLib_EnterMonitorLONG`, `SKQuoteLib_LeaveMonitor` | `client.connect_quote()`, `client.disconnect_quote()` |
| quote ready | `SKQuoteLib_IsConnected`, `OnConnection` | `client.is_quote_connected()`, `client.is_quote_ready()`, `client.wait_quote_connected()` |
| quote subscription | `SKQuoteLib_RequestStocks`, `OnNotifyQuoteLONG` | `client.subscribe_quotes()`, `client.get_latest_quote()` |
| tick / best5 subscription | `SKQuoteLib_RequestTicks`, `OnNotifyTicksLONG`, `OnNotifyBest5LONG` | `client.subscribe_ticks()`, `client.get_ticks()`, `client.get_latest_best5()` |
| tick backfill | `OnNotifyHistoryTicksLONG` | 自動進 `client.hub`（`QuoteTick.history=True`） |
| live tick only（無回補） | `SKQuoteLib_RequestLiveTick` | `client.request_live_tick()` |
| stock list | `SKQuoteLib_RequestStockList`, `OnNotifyStockList` | `client.request_stock_list()` / `fetch_quote_symbol_lists()` |
| quote snapshot | `SKQuoteLib_GetStockByNoLONG` | `client.get_quote_snapshot()` |
| historical K-line | `SKQuoteLib_RequestKLineAMByDate`, `OnNotifyKLineData` | `client.request_kline()` / `fetch_quote_history()` |
| stock positions | `GetRealBalanceReport`, `OnRealBalanceReport` | `client.get_stock_positions()` |
| future positions | `GetOpenInterestGW`, `OnOpenInterest` | `client.get_future_positions()` |
| future rights | `GetFutureRights`, `OnFutureRights` | `client.get_future_rights()` |
| capital pay | `GetBalance` | `client.get_capital_pay_balance()` |
| stock order | `SendStockOrder` | `client.place_stock_order()`, `place_stock_limit()`, `place_stock_market()` |
| stock odd lot | `SendStockOddLotOrder` | `client.place_stock_odd_lot_order()` |
| future order | `SendFutureOrderCLR` | `client.place_future_order()`, `place_future_limit()`, `place_future_market()` |
| cancel | `CancelOrderBySeqNo` / `CancelOrderByBookNo` / `CancelOrderByStockNo` | `client.cancel_order_by_seq()` / `cancel_order_by_book()` / `cancel_orders_by_symbol()` |
| decrease / correct | `DecreaseOrderBySeqNo` / `CorrectPriceBySeqNo` | `client.decrease_order_by_seq()` / `correct_price_by_seq()` |

`GetBalanceQuery` 官方自 V2.13.54 起不再提供，SDK 已移除；現貨庫存請用 `GetRealBalanceReport`。

## 官方報價規則（V2.13.58 手冊，2026-07 實測確認）

- `SKQuoteLib_RequestStocks(psPageNo, bstrStockNos)`：psPageNo「請固定帶 1」，一般用戶頁碼上限 1；
  單頁最多 100 檔（超過的靜默捨棄）；不存在的代碼靜默略過；
  一組 SKQuoteLib 僅可擇一使用一個即時報價訂閱（重連即重置）。頁碼超限回 `3006`。
- `SKQuoteLib_RequestTicks(psPageNo, bstrStockNo)`：psPageNo「請從 0 開始」，一頁一檔；
  首次訂閱回補當日 tick（`OnNotifyHistoryTicksLONG`，同签名）。
- `SKQuoteLib_RequestLiveTick`：同 RequestTicks 但不回補歷史，兩者擇一使用。
- `SKQuoteLib_GetStockByNoLONG` / `GetStockByIndexLONG`：未訂閱即時報價時僅回商品基本資料
  （名稱、昨收等），須先 `RequestStocks` 才有即時值。
- `SKQuoteLib_IsConnected`：0 斷線 / 1 連線中 / 2 商品檔下載中；
  訂閱動作須等 `OnConnection nKind=3003（SK_SUBJECT_CONNECTION_STOCKS_READY）`。
- `RequestStocksWithMarketNo` / `RequestTicksWithMarketNo`：僅支援盤中零股-上市(5)/上櫃(6)、
  客製化期貨(9)/客製化選擇權(10)；一般市場（含價差商品）不帶 market number。
- 市場別編號：上市 0、上櫃 1、期貨 2、選擇權 3、興櫃 4、盤中零股 5/6、客製化 9/10。
- 商品代號加 `AM` 後綴 = 純日盤行情（如 `TX00AM`）；AM 代碼不可下單。
- `RequestKLineAMByDate`：`sTradeSession` 0=全盤（期貨含前一日夜盤）、1=AM 盤。
- `GetOrderReport` / `GetFulfillReport`：阻塞式查詢，每次需間隔 5 秒；
  等待期間必須 pump COM 訊息，否則前次查詢不會標記完成、一直回 `M999`。
  `M003`=查無資料。帳號必須是 TS/TF 交易帳號（一戶通帳號會回 `M999 ... is invalid`）。

## 期貨價差商品（實測 2026-07-28）

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

### Rights

- `GetFutureRights` coin type: all = `0`, TWD = `1`, RMB = `2`

### Report query formats

- `GetOrderReport` nFormat: 1 全部 / 2 有效 / 3 可消 / 4 已消 / 5 已成 / 6 失敗 / 7 合併同價格 / 8 合併同商品 / 9 預約
- `GetFulfillReport` nFormat: 1 完整 / 2 合併同書號 / 3 合併同價格 / 4 合併同商品 / 5 T+1 成交
