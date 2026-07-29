"""
Streaming real-time quote example.

Edit the settings below, then run:
  python examples/04_live_quote.py

Symbols follow SKQuoteLib codes; check with examples/05_query_quote.py block 02:
  Stock:                  MARKET = "stock";  SYMBOLS = ["2330"] or ["2317", "0050"]
  Index future:           MARKET = "future"; SYMBOLS = ["TX00"] (near month)
  TSMC stock future:      MARKET = "future"; SYMBOLS = ["CDF00"]
  Futures spread:         MARKET = "future"; SYMBOLS = ["TX08/09"] or ["CDF08/09"]
Spread symbols change with contract rolls; refresh them from the symbol list.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capital_api_sdk import (  # noqa: E402
    CapitalClient,
    compact_quote_stream_event,
    stream_realtime_quote_events,
)

# ===== Settings =====
MARKET = "stock"                                 # "stock" / "future" (label only; spreads work too)
SYMBOLS = ["1101", "0056"]
DATA_KINDS = ("snapshot", "ticks", "orderbook")  # any subset
RUN_SECONDS = 20.0                               # None = stream until Ctrl+C
PUMP_INTERVAL_SEC = 0.2
INCLUDE_HISTORY = False                          # True = also yield today's backfilled ticks first
# ====================


def main() -> int:
    client = CapitalClient.from_env(enable_live_order=False)
    print(f"streaming market={MARKET} symbols={SYMBOLS} data={DATA_KINDS} seconds={RUN_SECONDS}", flush=True)

    try:
        for event in stream_realtime_quote_events(
            client,
            SYMBOLS,
            market=MARKET,
            data=DATA_KINDS,
            seconds=RUN_SECONDS,
            pump_interval_sec=PUMP_INTERVAL_SEC,
            include_history=INCLUDE_HISTORY,
        ):
            print(f"[{event.kind}] {compact_quote_stream_event(event)}", flush=True)
    except KeyboardInterrupt:
        print("stopped by user", flush=True)

    if client.hub.quote_errors:
        print("Quote callback errors:", flush=True)
        for error in client.hub.quote_errors[-10:]:
            print(error, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
