"""Login and list accounts (read-only smoke test)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capital_api_sdk import CapitalClient  # noqa: E402

client = CapitalClient.from_env(enable_live_order=False)
print(client.login(read_cert=True))

for account in client.get_accounts():
    print(account)
