"""Print a privacy-preserving summary of the PhishAware database.

Used during the demo to show that only pseudonymous data is stored (NFR-11):

    python scripts/inspect_db.py
"""

import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "instance" / "phishaware.db"
DB_PATH = Path(os.environ.get("PHISHAWARE_DB", DEFAULT_DB))


def main():
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}. Start the app once to create it.")
        return 1
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    columns = [row["name"] for row in connection.execute("PRAGMA table_info(participant)")]
    participants = connection.execute("SELECT COUNT(*) FROM participant").fetchone()[0]
    attempts = connection.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
    responses = connection.execute("SELECT COUNT(*) FROM response").fetchone()[0]
    scenarios = connection.execute("SELECT COUNT(*) FROM scenario").fetchone()[0]

    print("PhishAware database summary")
    print(f"Participant columns: {', '.join(columns)}")
    print(f"Rows: participants={participants} attempts={attempts} "
          f"responses={responses} scenarios={scenarios}")
    print("\nLatest participants (random IDs only):")
    for row in connection.execute(
        "SELECT id, consent_version, consented_at, form_order, status "
        "FROM participant ORDER BY seq DESC LIMIT 5"
    ):
        print(f"  {row['id']}  consent v{row['consent_version']}  "
              f"order {row['form_order']}  {row['status']}")
    print("\nLatest responses:")
    for row in connection.execute(
        "SELECT a.phase, r.scenario_id, r.answer, r.is_correct FROM response r "
        "JOIN attempt a ON a.id = r.attempt_id ORDER BY r.id DESC LIMIT 5"
    ):
        result = "correct" if row["is_correct"] else "incorrect"
        print(f"  {row['phase']:<9} {row['scenario_id']}  {row['answer']:<10}  {result}")
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
