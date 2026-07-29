"""Public Capital website data endpoints (delayed quotes, history, market info).

These are unauthenticated website endpoints, not the SKCOM real-time feed.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from .models import PublicMarketInfo, PublicStockHistoryBar, PublicStockQuote
from .parsers import decimal_or_none, int_or_none

PUBLIC_STOCK_BASE_URL = "https://stock.capital.com.tw/z/bcd"
PUBLIC_CMS_BASE_URL = "https://www.capital.com.tw/CapitalGWAPI/srvcms/api/cms"


def _cache_buster() -> str:
    return datetime.now().strftime("%H%M%S")


def _read_json(
    url: str,
    *,
    timeout: float = 10.0,
    use_proxy: bool = False,
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    opener = build_opener() if use_proxy else build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        content = response.read()
    try:
        payload = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        payload = content.decode("cp950")
    return json.loads(payload)


def fetch_public_stock_quote(symbol: str, *, timeout: float = 10.0) -> PublicStockQuote:
    """
    Fetch the public stock quote JSON used by Capital's website.

    Capital's quote page labels this data as delayed quote data.
    """
    params = urlencode({"B": str(symbol), "xyz": _cache_buster()})
    data = _read_json(
        f"{PUBLIC_STOCK_BASE_URL}/GetStkRTDataJSON.djjson?{params}",
        timeout=timeout,
        headers={"Referer": f"https://stock.capital.com.tw/z/StkPrice.html?a={symbol}"},
    )
    bid_prices = tuple(decimal_or_none(data.get(key)) for key in ("B", "B2", "B3", "B4", "B5"))
    ask_prices = tuple(decimal_or_none(data.get(key)) for key in ("A", "A2", "A3", "A4", "A5"))
    bid_qtys = tuple(int_or_none(data.get(key)) for key in ("BS", "BS2", "BS3", "BS4", "BS5"))
    ask_qtys = tuple(int_or_none(data.get(key)) for key in ("AS", "AS2", "AS3", "AS4", "AS5"))
    return PublicStockQuote(
        symbol=str(data.get("ID") or symbol),
        name=str(data.get("Name") or ""),
        date=str(data.get("Date") or ""),
        time=str(data.get("T") or ""),
        last=decimal_or_none(data.get("P")),
        open=decimal_or_none(data.get("O")),
        high=decimal_or_none(data.get("H")),
        low=decimal_or_none(data.get("L")),
        reference=decimal_or_none(data.get("PC")),
        bid=decimal_or_none(data.get("B")),
        ask=decimal_or_none(data.get("A")),
        total_volume=int_or_none(data.get("TV")),
        tick_volume=int_or_none(data.get("V")),
        bid_prices=bid_prices,
        bid_qtys=bid_qtys,
        ask_prices=ask_prices,
        ask_qtys=ask_qtys,
        raw=data,
    )


def fetch_public_stock_profile(symbol: str, *, timeout: float = 10.0) -> dict[str, Any]:
    params = urlencode({"a": str(symbol), "xyz": _cache_buster()})
    data = _read_json(
        f"{PUBLIC_STOCK_BASE_URL}/GetStkTypeDataJSON.djjson?{params}",
        timeout=timeout,
        headers={"Referer": f"https://stock.capital.com.tw/z/StkPrice.html?a={symbol}"},
    )
    return dict(data)


def fetch_public_stock_history(symbol: str, *, timeout: float = 10.0) -> list[PublicStockHistoryBar]:
    params = urlencode({"a": str(symbol)})
    data = _read_json(
        f"{PUBLIC_STOCK_BASE_URL}/GetStkHisDataJSON.djjson?{params}",
        timeout=timeout,
        headers={"Referer": f"https://stock.capital.com.tw/z/StkPrice.html?a={symbol}"},
    )
    stock_id = str(data.get("StockID") or symbol)
    normalized_symbol = stock_id[2:] if stock_id.startswith("AS") else stock_id
    return [
        PublicStockHistoryBar(
            symbol=normalized_symbol,
            date=str(row.get("V1") or ""),
            open=decimal_or_none(row.get("V2")),
            high=decimal_or_none(row.get("V3")),
            low=decimal_or_none(row.get("V4")),
            close=decimal_or_none(row.get("V5")),
            volume=int_or_none(row.get("V6")),
            transactions=int_or_none(row.get("V7")),
            raw=dict(row),
        )
        for row in data.get("Result", [])
    ]


def fetch_public_market_info(stocks: Iterable[str] | None = None, *, timeout: float = 10.0) -> list[PublicMarketInfo]:
    if stocks is None:
        url = f"{PUBLIC_CMS_BASE_URL}/InternationalMarket/Info"
    else:
        params = urlencode({"stocks": ",".join(str(stock) for stock in stocks)})
        url = f"{PUBLIC_CMS_BASE_URL}/InternationalMarket/Info?{params}"
    data = _read_json(
        url,
        timeout=timeout,
        headers={
            "Referer": "https://www.capital.com.tw/web/",
            "Origin": "https://www.capital.com.tw",
        },
    )
    rows = data.get("value", []) if isinstance(data, dict) else data
    return [
        PublicMarketInfo(
            symbol=str(row.get("stockId") or ""),
            name=str(row.get("name") or ""),
            deal=decimal_or_none(row.get("deal")),
            yesterday=decimal_or_none(row.get("yDay")),
            raw=dict(row),
        )
        for row in rows
    ]
