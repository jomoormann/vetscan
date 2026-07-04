#!/usr/bin/env python3
"""Re-parse stored report PDFs with the current importer/parser behavior."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import VetProteinService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the latest parser/import behavior to stored report PDFs."
    )
    parser.add_argument(
        "--db",
        default=os.getenv("VETSCAN_DB_PATH", str(ROOT / "data" / "vet_proteins.db")),
        help="SQLite database path.",
    )
    parser.add_argument(
        "--uploads",
        default=os.getenv("VETSCAN_UPLOADS_DIR", str(ROOT / "uploads")),
        help="Uploads directory containing stored PDFs.",
    )
    parser.add_argument(
        "--skip-unassigned",
        action="store_true",
        help="Only reprocess assigned test sessions.",
    )
    parser.add_argument(
        "--no-auto-assign",
        action="store_true",
        help="Refresh queued report metadata but do not auto-assign confident matches.",
    )
    args = parser.parse_args()

    with VetProteinService(db_path=args.db, uploads_dir=args.uploads) as service:
        stats = service.reprocess_all_reports(
            include_unassigned=not args.skip_unassigned,
            auto_assign_unassigned=not args.no_auto_assign,
        )

    print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
