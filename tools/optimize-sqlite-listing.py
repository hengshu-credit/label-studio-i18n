"""Add a covering index for Label Studio's project-wide task totals on SQLite.

Stop Label Studio and back up its database before running this on the host.
No task rows or annotation results are changed. The index is safe to keep across upgrades.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    started = time.monotonic()
    with sqlite3.connect(database, timeout=60) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(task)")]
        required = ["project_id", "total_annotations", "total_predictions", "id"]
        if not set(required).issubset(columns):
            raise RuntimeError("Not a compatible Label Studio task database")
        existing = [
            row[2]
            for row in connection.execute("PRAGMA index_info(task_project_totals_idx)")
        ]
        if existing and existing != required:
            raise RuntimeError("An incompatible index already uses this name")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS task_project_totals_idx "
            "ON task(project_id, total_annotations, total_predictions, id)"
        )
        query = (
            "SELECT COUNT(id), COALESCE(SUM(total_annotations),0), "
            "COALESCE(SUM(total_predictions),0) FROM task WHERE project_id=?"
        )
        plan = [
            row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query, (1,))
        ]
        if not any("COVERING INDEX task_project_totals_idx" in row for row in plan):
            raise RuntimeError("SQLite did not select the covering totals index")
    print(
        json.dumps(
            {
                "database": str(database),
                "index": "task_project_totals_idx",
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "query_plan": plan,
            }
        )
    )


if __name__ == "__main__":
    main()
