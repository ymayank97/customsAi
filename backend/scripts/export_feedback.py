"""Export corrections for expert review before training; never auto-certify labels."""
import argparse
import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import TrainingFeedback, get_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--user-id", type=int, help="Export only this user's corrections")
    scope.add_argument("--local", action="store_true", help="Export local corrections without a signed-in user")
    args = parser.parse_args()
    with Session(get_engine()) as session, args.output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["description", "cth", "verified", "group_id"])
        writer.writeheader()
        rows = session.scalars(select(TrainingFeedback).where(TrainingFeedback.user_id == args.user_id))
        for row in rows:
            writer.writerow({"description": row.item_description, "cth": row.corrected_cth,
                             "verified": "false", "group_id": row.job_id})
    print(f"Exported to {args.output}; review labels and set verified=true before training.")


if __name__ == "__main__":
    main()
