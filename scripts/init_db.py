"""Create (or with --reset, re-create) the SQLite inventory and ledger. main.py also does this on demand."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_agents import config, db  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=str(config.DEFAULT_DB_PATH))
    p.add_argument("--reset", action="store_true", help="drop inventory and ledger first")
    args = p.parse_args()
    path = db.init_db(args.db_path, reset=args.reset)
    print(f"Inventory ready at {path}:")
    for sku in db.list_skus(path):
        item = db.get_item(path, sku)
        print(f"  {sku:10} stock={item['stock']:<3} catalog_price={item['unit_price']}")
