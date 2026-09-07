# %%
"""台指期 操作面板

功能細節、防呆邏輯、商品代碼表都在 useful_fuction/trade_futures.py, 這裡只放參數與操作
  連線   TaifexTrader.connect()
  1 資金  trader.rights()          權益數、可用餘額、保證金、浮動損益
  2 庫存  trader.positions()       期貨未平倉
  3 掛單  trader.open_orders()     當前可刪掛單 (symbol 欄 = 券商契約代碼)
  4 報價  trader.quote("MTX09")    商品是否存在 + 最後價 + b1/a1 + 漲跌停價
  5 下單  trader.place_limit(...)  過防呆才送限價單
  6 刪單  trader.cancel("MTX09")   刪掉該商品所有掛單
"""
import sys
from pathlib import Path

# 讓 useful_fuction 找得到 (從專案根目錄或 strategy/ 執行都可以)
_here = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
for _candidate in (_here, _here / "strategy"):
    if (_candidate / "useful_fuction").is_dir():
        sys.path.insert(0, str(_candidate))
        break

from useful_fuction.trade_futures import (  # noqa: E402
    FuturesNewClose,
    FuturesReserved,
    RiskLimits,
    TaifexTrader,
    TradeType,
)

# ======================================================================
# 參數
# ======================================================================
# True  = 真的下單 / 刪單 到交易所
ENABLE_LIVE_ORDER = True

# 下單防呆: 名目價值 = 價格 x 契約乘數 x 口數 (大台 200 / 小台 50 / 微台 10 元/點)
LIMITS = RiskLimits(
    allowed_products=("MTX", "TM"),        # 只能下小台 / 微台 (TX 大台 / MTX 小台 / TM 微台), 不限月份。只留一種要有逗號: ("TM",)
    max_order_qty=2,                       # 單筆口數上限
    max_order_value=20_000_000,            # 單筆名目上限。1 口大台約 46000 x 200 = 920 萬
    max_net_value=20_000_000,              # 淨部位上限 |庫存 + 掛單 + 本次委託|
    max_price_deviation_pct=25,            # 委託價偏離最後成交價超過 3% 就擋 (None = 不檢查)
    block_unknown_symbols=False,           # 庫存 / 掛單有算不出名目的商品 (例如選擇權) 就擋單
)


# %%
# ======================================================================
# 連線 (跑一次就好)
# ======================================================================
trader = TaifexTrader.connect(enable_live_order=ENABLE_LIVE_ORDER, limits=LIMITS)


# %%
# 1. 資金: 權益數 / 可用餘額 / 保證金 / 浮動損益
trader.rights().T


# %%
# 2. 庫存: 期貨未平倉
trader.positions().T


# %%
# 3. 掛單: 當前可刪掛單
trader.open_orders().T


# %%
# 4. 報價: 商品是否存在 + 最後價 + b1/a1 + 漲跌停
# 期貨代碼參考: 大台 TX09 / TX00, 小台 MTX09 / MTX00, 微台 TM2609 / TM0000

symbol = "TM0000"

trader.quote(symbol)


# %%
# 5. 下限價單 (過防呆才送)
# outcome.ok 防呆是否通過 / outcome.result 送單結果 (None = 被擋下沒送, dry_run=True = 沒送)

symbol = "TM2609"                    # 報價端代碼: MTX09 = 小台 2026/09; 近月連續 MTX00 也可以
side = "B"                          # "B" 買 / "S" 賣
qty = 1                             # 口數
price = "42500"                     # 用字串避免 float 尾差。先跑 功能4 看最後價再填, 偏離超過 max_price_deviation_pct 會被擋
                                    # 要掛漲跌停就填 功能4 印出的 漲停 / 跌停 數字
                                    # 期貨沒有證券的 H/L 代碼; M/P 市價代碼只能配 IOC/FOK, 這裡不收, 要市價直接用 SDK

trade_type = TradeType.ROD          # ROD / IOC / FOK
new_close = FuturesNewClose.AUTO    # AUTO 自動 / NEW 新倉 / CLOSE 平倉
reserved = FuturesReserved.REGULAR  # REGULAR 盤中 (T 盤及 T+1 盤) / RESERVED T 盤預約

outcome = trader.place_limit(symbol=symbol, side=side, qty=qty, price=price, trade_type=trade_type, new_close=new_close, reserved=reserved)


# %%
# 6. 刪掉某商品的所有掛單
# 可給報價端代碼 (MTX09 -> 自動轉成 MXFI6) 或直接貼 功能3 印出的契約代碼 (MXFI6)
# 近月連續 (MTX00) 沒有月份, 不能拿來刪單
# 要刪整個帳號: trader.cancel_all(confirm=True)

symbol = "TMFI6"

trader.cancel(symbol)


# %%
