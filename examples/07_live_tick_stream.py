"""
Streaming tick DataFrame example (extends 04_live_quote.py).

Edit the settings below, then run:
  python examples/07_live_tick_stream.py

What it does:
  1. Subscribes SYMBOL and waits out today's tick backfill. SKQuoteLib_RequestTicks
     replays every trade since the session open through OnNotifyHistoryTicksLONG,
     so this works mid-session (10:30 still returns 09:00 onwards) and after the close.
  2. Prints one DataFrame per interval: the first holds the whole backfill, each
     later one holds only the ticks that arrived since the previous print.
  3. Re-checks the ptr sequence after every batch. ptr is the exchange-side running
     trade number, so a hole in it means a lost tick. Those are re-read one by one
     with SKQuoteLib_GetTickLONG - re-subscribing would NOT help, because SKCOM
     backfills a symbol only once per connection.

Requires pandas. Read-only: enable_live_order=False, no order call anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from capital_api_sdk import (  # noqa: E402
    CapitalClient,
    TickBatch,
    filter_ticks_by_time,
    stream_tick_batches,
    ticks_to_dataframe,
)

# ===== Settings =====
SYMBOL = "2330"
MARKET = "stock"              # "stock" / "future"; odd-lot needs "oddlot-listed"
INTERVAL_SEC = 5.0            # print a new DataFrame every N seconds
RUN_SECONDS = 60.0            # streaming time after the backfill; None = until Ctrl+C
BACKFILL_TIMEOUT_SEC = 60.0   # cap for the first batch (a busy morning is tens of thousands of ticks)
BACKFILL_IDLE_SEC = 0.8       # backfill is done once no tick arrives for this long
DROP_SIMULATE = True          # drop nSimulate=1 trial-matching (試撮) rows
REPAIR_GAPS = True            # re-read missing ptrs via SKQuoteLib_GetTickLONG
MAX_PRINT_ROWS = 50           # max rows per printed DataFrame (split head/tail when it overflows)
TIME_WINDOW = None            # e.g. ("090000", "094800") to also slice the backfill
# ====================


def print_frame(frame: pd.DataFrame, *, max_rows: int) -> None:
    if frame.empty:
        print("  (empty)", flush=True)
        return
    if len(frame) <= max_rows:
        print(frame.to_string(index=False), flush=True)
        return
    head = max_rows // 2
    print(frame.head(head).to_string(index=False), flush=True)
    print(f"  ... {len(frame) - max_rows} more rows ...", flush=True)
    print(frame.tail(max_rows - head).to_string(index=False, header=False), flush=True)


def describe(batch: TickBatch, index: int) -> str:
    label = "backfill" if batch.is_first else f"+{INTERVAL_SEC:g}s"
    parts = [f"=== batch {index} ({label}) {batch.symbol} rows={len(batch)}"]
    if batch.repaired:
        parts.append(f"repaired={len(batch.repaired)}")
    if batch.gaps_open:
        parts.append(f"still_missing={batch.missing_count}")
    return " ".join(parts) + " ==="


def main() -> int:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    client = CapitalClient.from_env(enable_live_order=False)
    print(
        f"streaming ticks symbol={SYMBOL} market={MARKET} interval={INTERVAL_SEC}s "
        f"run={RUN_SECONDS} repair_gaps={REPAIR_GAPS}",
        flush=True,
    )

    total = 0
    try:
        for index, batch in enumerate(
            stream_tick_batches(
                client,
                SYMBOL,
                market=MARKET,
                interval_sec=INTERVAL_SEC,
                seconds=RUN_SECONDS,
                backfill_timeout_sec=BACKFILL_TIMEOUT_SEC,
                backfill_idle_sec=BACKFILL_IDLE_SEC,
                drop_simulate=DROP_SIMULATE,
                repair_gaps=REPAIR_GAPS,
            ),
            start=1,
        ):
            total += len(batch)
            print(describe(batch, index), flush=True)
            print_frame(ticks_to_dataframe(batch.ticks), max_rows=MAX_PRINT_ROWS)

            if batch.is_first and TIME_WINDOW:
                start, end = TIME_WINDOW
                window = filter_ticks_by_time(batch.ticks, start=start, end=end)
                print(f"--- backfill sliced to {start}-{end}: {len(window)} ticks ---", flush=True)
                print_frame(ticks_to_dataframe(window), max_rows=MAX_PRINT_ROWS)

            for error in batch.errors:
                print(f"  ! {error}", flush=True)
    except KeyboardInterrupt:
        print("stopped by user", flush=True)

    print(f"total ticks printed: {total}", flush=True)
    if client.hub.quote_errors:
        print("Quote callback errors:", flush=True)
        for error in client.hub.quote_errors[-10:]:
            print(error, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
