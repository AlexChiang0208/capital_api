"""TAIFEX index futures conventions: contract specs, the three symbol namespaces and code translation.

SKCOM uses three different codes for the same contract (all verified live 2026-09-03):

  quote / order code    RequestStocks, SendFutureOrderCLR   大台 TX + MM (TX09; TX00 = near-month continuous)
                                                            小台 MTX + MM (MTX09), 微台 TM + YYMM (TM2609, TM0000)
  exchange code         GetOrderReport, CancelOrderByStockNo TXF / MXF / TMF + month letter A-L + year digit (TXFI6)
  position-table code   GetOpenInterestGW                    微台 TM + MM (TM09); 大台 / 小台 presumably TX09 / MTX09

Spread symbols ("TX09/10"), the day-session-only suffix (TX00AM) and options (TXO...) are deliberately
NOT recognised: every pattern is a full-string match, so they can never leak into a notional calculation.
Official 4-2-8: ordering an already expired month (TX03 after the March settlement) rolls to next year's TX03.

COVERAGE: only the three 台指 index futures (TX 大台 / MTX 小台 / TM 微台) are listed, and only those three
were verified live. Everything else (電子期 TE, 金融期 TF, 小型電子 ZEF, 個股期貨, 選擇權, 海外期貨, 組合單 ...)
is unknown on purpose: contract_of() returns None, so a notional calculation reports the row as "unknown"
instead of guessing a multiplier. The code formats are NOT uniform across products (TX + MM vs TM + YYMM,
TM09 in the position table), so never extrapolate a new product from an existing entry.

ADDING A PRODUCT = one ContractSpec entry in CONTRACTS, each field checked live before it is trusted:
  quote_pattern     the code the futures symbol list / quote() shows for that product (its own MM or YYMM style)
  report_pattern    the code GetOrderReport (n_format=1) prints after a 1-lot test order at the 跌停 price
  report_prefix     the exchange product prefix inside that report code (TXF / MXF / TMF ...), for to_report_code
  position_pattern  the code GetOpenInterestGW prints once a position exists, only if it differs from the quote code
  point_value / tick_size   from the TAIFEX contract specification (元/點, minimum tick)
Then extend the unit tests (capital_api_tests/test_sdk_account_queries.py TaifexTests) and, where the strategy
should be allowed to trade it, RiskLimits.allowed_products in strategy/useful_fuction/trade_futures.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class ContractSpec:
    name: str                   # Chinese name, for messages
    quote_pattern: str          # quote / order code (full match)
    report_pattern: str         # exchange code used by reports and cancel (full match)
    report_prefix: str          # exchange product prefix, for code translation
    point_value: int            # contract multiplier (TWD per index point)
    tick_size: Decimal          # minimum price increment
    position_pattern: str = ""  # GetOpenInterestGW code when it differs from the quote code


_MM = r"(00|0[1-9]|1[0-2])"    # two-digit month, 00 = near-month continuous

# Verified live 2026-09-03 for TX / MTX / TM only. New products go here (see "ADDING A PRODUCT" above);
# anything not listed is treated as unknown everywhere, which is the safe default.
CONTRACTS: dict[str, ContractSpec] = {
    "TX": ContractSpec("大台", rf"TX{_MM}", r"TXF[A-L]\d", "TXF", 200, Decimal(1)),
    "MTX": ContractSpec("小台", rf"MTX{_MM}", r"MXF[A-L]\d", "MXF", 50, Decimal(1)),
    # 微台 quotes are TM + YYMM (TM2609) but the position table shows TM + MM (TM09, verified 2026-09-03)
    "TM": ContractSpec("微台", r"TM(0000|\d{2}(0[1-9]|1[0-2]))", r"TMF[A-L]\d", "TMF", 10, Decimal(1),
                       position_pattern=r"TM(0[1-9]|1[0-2])"),
}
MONTH_CODES = "ABCDEFGHIJKL"    # exchange month letters A-L = January-December


def contract_of(symbol: str) -> tuple[str, ContractSpec] | None:
    """Recognise a quote code (MTX09), exchange code (MXFI6) or position code (TM09); None otherwise.

    Full-string matching keeps options (TXO47000I6), spreads (TX09/10) and TX00AM out: a prefix match
    would price an option premium with the 大台 multiplier.
    """
    text = str(symbol).strip().upper()
    for family, spec in CONTRACTS.items():
        patterns = (spec.quote_pattern, spec.report_pattern, spec.position_pattern)
        if any(pattern and re.fullmatch(pattern, text) for pattern in patterns):
            return family, spec
    return None


def point_value(symbol: str) -> int | None:
    """Contract multiplier in TWD per point: MTX09 -> 50, TX09 -> 200, TM2609 / TM09 -> 10; None if unknown."""
    found = contract_of(symbol)
    return found[1].point_value if found else None


def is_orderable(symbol: str, allowed: Iterable[str] = ("TX", "MTX", "TM")) -> bool:
    """True when symbol is a QUOTE code of one of the allowed families (any month).

    Exchange codes (MXFI6) and position codes (TM09) are rejected: SendFutureOrderCLR wants the quote code.
    """
    text = str(symbol).strip().upper()
    for family in allowed:
        spec = CONTRACTS.get(str(family).strip().upper())
        if spec and re.fullmatch(spec.quote_pattern, text):
            return True
    return False


def contract_code(product: str, year: int, month: int) -> str:
    """Exchange contract code: ("MXF", 2026, 9) -> "MXFI6"."""
    return f"{product}{MONTH_CODES[month - 1]}{year % 10}"


def third_wednesday(year: int, month: int) -> date:
    """Last trading day of a TAIFEX index futures contract: the third Wednesday of the delivery month.

    weekday(): Monday 0 ... Wednesday 2. A holiday on that Wednesday moves the real last trading day
    to the next business day; that shift is not modelled here.
    """
    first = date(year, month, 1)
    return first.replace(day=1 + (2 - first.weekday()) % 7 + 14)


def to_quote_code(symbol: str, today: date | None = None) -> str | None:
    """Position or exchange code -> quote code: TM09 -> TM2609, TMFI6 -> TM2609, MXFI6 -> MTX09.

    Quote codes (including near-month continuous TX00 / TM0000) pass through unchanged; anything
    unrecognised returns None. Needed to price a position row (GetOpenInterestGW prints TM09) with
    a live quote (RequestStocks wants TM2609). Year rules: a two-digit month (TM09) belongs to this
    year until its settlement day has passed, then to next year (same rule as to_report_code); an
    exchange year digit (TMFI6 -> 6) is resolved inside the current decade, rolling forward when it
    would land more than a year in the past (contracts never trade that far back).
    """
    text = str(symbol).strip().upper()
    found = contract_of(text)
    if found is None:
        return None
    family, spec = found
    if re.fullmatch(spec.quote_pattern, text):
        return text
    today = today or date.today()
    if re.fullmatch(spec.report_pattern, text):
        month = MONTH_CODES.index(text[-2]) + 1
        year = today.year - today.year % 10 + int(text[-1])
        if year < today.year - 1:
            year += 10
    else:                                     # position code with MM only (微台 TM09)
        month = int(text[len(family):])
        expired = today.month > month or (today.month == month and today > third_wednesday(today.year, month))
        year = today.year + 1 if expired else today.year
    if family == "TM":
        return f"TM{year % 100:02d}{month:02d}"
    return f"{family}{month:02d}"


def to_report_code(symbol: str, today: date | None = None) -> str | None:
    """Quote or position code -> exchange code: MTX09 -> MXFI6, TM2609 -> TMFI6, TM09 -> TMFI6.

    Exchange codes pass through unchanged; near-month continuous codes (TX00 / TM0000) have no month
    and return None, as does anything unrecognised. Two-digit months belong to this year until the
    month's settlement day has passed, then to next year (mirrors official 4-2-8 auto-roll).
    Note: the settlement-day night session already belongs to the next trading day on the exchange
    side; this date-only rule still answers this year's code that evening.
    """
    text = str(symbol).strip().upper()
    found = contract_of(text)
    if found is None:
        return None
    family, spec = found
    if re.fullmatch(spec.report_pattern, text):
        return text
    digits = text[len(family):]
    if set(digits) == {"0"}:
        return None
    today = today or date.today()
    if len(digits) == 4:                      # 微台 quote code YYMM
        year, month = 2000 + int(digits[:2]), int(digits[2:])
    else:                                     # MM (大台 / 小台 quote codes, 微台 position code)
        month = int(digits)
        expired = today.month > month or (today.month == month and today > third_wednesday(today.year, month))
        year = today.year + 1 if expired else today.year
    return contract_code(spec.report_prefix, year, month)
