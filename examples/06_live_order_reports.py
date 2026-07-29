"""
Receive real-time order/fill reports through SKReplyLib.OnNewData.

This example does not send orders. It logs incoming reports, shows the
best-effort open-order cache, and finishes with a sync GetOrderReport query.

Environment knobs:
  CAPITAL_REPLY_SECONDS=60
  CAPITAL_READ_CERT=1
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capital_api_sdk import CapitalClient, fetch_order_reports  # noqa: E402

run_seconds = float(os.getenv("CAPITAL_REPLY_SECONDS", "60"))
read_cert = os.getenv("CAPITAL_READ_CERT", "1") != "0"

client = CapitalClient.from_env(enable_live_order=False)
client.login(read_cert=read_cert, connect_reply=True)

print(f"Connected reply server; pumping {run_seconds:.0f}s")
end = time.time() + run_seconds
seen_reports = 0

while time.time() < end:
    client.pump(1)

    reports = client.hub.raw_new_data
    if len(reports) > seen_reports:
        for event in reports[seen_reports:]:
            print("[report]", asdict(event))
        seen_reports = len(reports)

if not client.hub.raw_new_data:
    print("No order/fill reports received during this run.")

print("\n== open orders (OnNewData cache) ==")
for event in client.get_open_orders():
    print(asdict(event))

print("\n== order reports (sync GetOrderReport, cancellable) ==")
for key, row in fetch_order_reports(client).items():
    print(f"[{key}] {row}")
