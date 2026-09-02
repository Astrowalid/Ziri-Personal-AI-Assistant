import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Generator

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.db")

VALID_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "DONE"}

@contextmanager
def get_connection(db_path: str = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that provides a SQLite connection and guarantees it is closed."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path: str = DB_PATH) -> None:
    """Creates the task_status table if it does not exist."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_status (
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('NOT_STARTED', 'IN_PROGRESS', 'DONE')),
                item_date TEXT,
                last_synced_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (source_type, source_id)
            )
        """)
        conn.commit()

def upsert_task(
    source_type: str,
    source_id: str,
    title: str,
    status: str = "NOT_STARTED",
    item_date: Optional[str] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Inserts a task or updates its metadata (title, item_date, last_synced_at).
    If the task already exists, its existing status is preserved unless explicitly overwritten.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")
    
    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        # Check if record already exists to preserve status
        cursor.execute(
            "SELECT status FROM task_status WHERE source_type = ? AND source_id = ?",
            (source_type, source_id)
        )
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute("""
                UPDATE task_status
                SET title = ?,
                    item_date = COALESCE(?, item_date),
                    last_synced_at = ?
                WHERE source_type = ? AND source_id = ?
            """, (title, item_date, now_iso, source_type, source_id))
        else:
            cursor.execute("""
                INSERT INTO task_status (
                    source_type, source_id, title, status, item_date, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (source_type, source_id, title, status, item_date, now_iso, now_iso))
        conn.commit()
    
    task = get_task(source_type, source_id, db_path=db_path)
    if task is None:
        raise RuntimeError(f"Failed to retrieve task after upsert: {source_type}:{source_id}")
    return task

def get_task(source_type: str, source_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Fetches a single task by its composite primary key (source_type, source_id)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM task_status WHERE source_type = ? AND source_id = ?",
            (source_type, source_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def update_task_status(
    source_type: str,
    source_id: str,
    new_status: str,
    title: Optional[str] = None,
    item_date: Optional[str] = None,
    db_path: str = DB_PATH
) -> bool:
    """Updates the status and updated_at timestamp of a task.
    If the task does not exist in local storage yet and title is provided, it will be automatically registered with the given status.
    
    Args:
        source_type: The source ('calendar' or 'classroom').
        source_id: The verbatim Google event_id or courseWork_id.
        new_status: New status ('NOT_STARTED', 'IN_PROGRESS', or 'DONE').
        title: Optional title of the event/task (used to auto-register if not yet tracked).
        item_date: Optional date/time string of the event/task.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}. Must be one of {VALID_STATUSES}")
    
    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE task_status
            SET status = ?,
                updated_at = ?
            WHERE source_type = ? AND source_id = ?
        """, (new_status, now_iso, source_type, source_id))
        conn.commit()
        if cursor.rowcount > 0:
            return True
            
        # If not found but title is provided, auto-register it immediately
        if title:
            cursor.execute("""
                INSERT INTO task_status (
                    source_type, source_id, title, status, item_date, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (source_type, source_id, title, new_status, item_date, now_iso, now_iso))
            conn.commit()
            return True

        return False

def find_tasks(
    query: str,
    status_filter: Optional[str] = None,
    source_type: Optional[str] = None,
    limit: int = 10,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Searches tasks using SQL LIKE on title, with optional status and source_type filtering.
    Returns candidate rows with their verbatim source_id and metadata for disambiguation.
    """
    sql = "SELECT * FROM task_status WHERE title LIKE ?"
    params: List[Any] = [f"%{query}%"]
    
    if status_filter:
        if status_filter not in VALID_STATUSES:
            raise ValueError(f"Invalid status_filter: {status_filter}. Must be one of {VALID_STATUSES}")
        sql += " AND status = ?"
        params.append(status_filter)
        
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
        
    sql += " ORDER BY item_date ASC, updated_at DESC LIMIT ?"
    params.append(limit)
    
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_tasks_for_date_range(
    start_date: str,
    end_date: str,
    status_filter: Optional[str] = None,
    source_type: Optional[str] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Retrieves tasks within an ISO date string range (inclusive).
    Applies optional status and source_type filters.
    """
    sql = "SELECT * FROM task_status WHERE item_date >= ? AND item_date <= ?"
    params: List[Any] = [start_date, end_date]
    
    if status_filter:
        if status_filter not in VALID_STATUSES:
            raise ValueError(f"Invalid status_filter: {status_filter}. Must be one of {VALID_STATUSES}")
        sql += " AND status = ?"
        params.append(status_filter)
        
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
        
    sql += " ORDER BY item_date ASC"
    
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_completed_tasks(
    start_date: str,
    end_date: str,
    source_type: Optional[str] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieves all tasks marked as DONE within the given date range."""
    return get_tasks_for_date_range(
        start_date=start_date,
        end_date=end_date,
        status_filter="DONE",
        source_type=source_type,
        db_path=db_path
    )

def reconcile_classroom_tasks(
    classroom_assignments: List[Dict[str, Any]],
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Reconciles live Google Classroom assignments against local database records.
    Implements Classroom-dominant reconciliation (Decision #3 in v3.md):
    - If live Classroom shows submitted == True, updates local DB status to 'DONE' if not already 'DONE'.
    - If local DB status is 'DONE', but live Classroom shows submitted == False (not-submitted),
      flags a discrepancy warning dict for the user.
      
    Returns:
        List of discrepancy warning dictionaries:
        [
            {
                "source_id": str,
                "title": str,
                "course_name": str,
                "local_status": "DONE",
                "classroom_state": str,
                "warning_message": str
            },
            ...
        ]
    """
    discrepancies: List[Dict[str, Any]] = []
    
    for assign in classroom_assignments:
        cw_id = assign.get("assignment_id")
        title = assign.get("title", "Untitled Assignment")
        course_name = assign.get("course_name", "Unknown Course")
        is_submitted = assign.get("submitted", False)
        classroom_state = assign.get("state", "NEW")
        
        if not cw_id:
            continue
            
        task = get_task(source_type="classroom", source_id=cw_id, db_path=db_path)
        
        if task:
            local_status = task.get("status")
            
            # Case 1: Discrepancy - Marked DONE locally, but not submitted in Classroom
            if local_status == "DONE" and not is_submitted:
                warning_msg = (
                    f"⚠️ Discrepancy: '{title}' ({course_name}) is marked DONE in your records, "
                    f"but Google Classroom shows it is not yet submitted (State: {classroom_state})."
                )
                discrepancies.append({
                    "source_id": cw_id,
                    "title": title,
                    "course_name": course_name,
                    "local_status": local_status,
                    "classroom_state": classroom_state,
                    "warning_message": warning_msg
                })
            # Case 2: Submitted in Classroom, but local status was not yet updated to DONE
            elif is_submitted and local_status != "DONE":
                update_task_status(
                    source_type="classroom",
                    source_id=cw_id,
                    new_status="DONE",
                    db_path=db_path
                )
        else:
            # If not yet in DB, upsert with appropriate status
            initial_status = "DONE" if is_submitted else "NOT_STARTED"
            upsert_task(
                source_type="classroom",
                source_id=cw_id,
                title=title,
                status=initial_status,
                item_date=assign.get("due_date_utc"),
                db_path=db_path
            )
            
    return discrepancies

if __name__ == "__main__":
    import tempfile
    import shutil

    # Run standalone tests using a temporary isolated database to avoid polluting production DB
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_assistant.db")
    print(f"Running Step 2 verification tests against test database: {test_db}\n")
    
    try:
        init_db(db_path=test_db)
        
        # 1. Test Upsert & Get
        print("1. Testing upsert_task & get_task...")
        task1 = upsert_task(
            source_type="calendar",
            source_id="cal_dentist_001",
            title="Dentist Appointment (Dr. Smith)",
            status="NOT_STARTED",
            item_date="2026-09-01T14:00:00+01:00",
            db_path=test_db
        )
        assert task1["title"] == "Dentist Appointment (Dr. Smith)"
        assert task1["status"] == "NOT_STARTED"
        
        fetched = get_task("calendar", "cal_dentist_001", db_path=test_db)
        assert fetched is not None
        assert fetched["source_id"] == "cal_dentist_001"
        print("   [PASS] Upsert and get succeeded.")

        # 2. Test find_tasks Case 1: Single clean match
        print("\n2. Testing find_tasks Case 1: Single clean match...")
        matches = find_tasks("Dentist", db_path=test_db)
        assert len(matches) == 1, f"Expected 1 match, got {len(matches)}"
        assert matches[0]["source_id"] == "cal_dentist_001"
        print(f"   [PASS] Query 'Dentist' returned exactly 1 candidate: '{matches[0]['title']}'")

        # 3. Test find_tasks Case 2: Ambiguous match (multiple similar items)
        print("\n3. Testing find_tasks Case 2: Ambiguous match...")
        upsert_task(
            source_type="classroom",
            source_id="cls_math_lab_1",
            title="Math Lab 1: Calculus Vectors",
            status="NOT_STARTED",
            item_date="2026-09-02T23:59:00+01:00",
            db_path=test_db
        )
        upsert_task(
            source_type="classroom",
            source_id="cls_math_lab_2",
            title="Math Lab 2: Matrix Multiplication",
            status="NOT_STARTED",
            item_date="2026-09-05T23:59:00+01:00",
            db_path=test_db
        )
        ambiguous_matches = find_tasks("Math Lab", db_path=test_db)
        assert len(ambiguous_matches) == 2, f"Expected 2 matches, got {len(ambiguous_matches)}"
        ids = [m["source_id"] for m in ambiguous_matches]
        assert "cls_math_lab_1" in ids and "cls_math_lab_2" in ids
        print(f"   [PASS] Query 'Math Lab' returned both candidates for disambiguation: {[m['title'] for m in ambiguous_matches]}")

        # 4. Test find_tasks Case 3: No match
        print("\n4. Testing find_tasks Case 3: No-match case...")
        no_matches = find_tasks("Physics Experiment Nonexistent", db_path=test_db)
        assert len(no_matches) == 0, f"Expected 0 matches, got {len(no_matches)}"
        assert no_matches == []
        print("   [PASS] Query 'Physics Experiment Nonexistent' safely returned empty list []")

        # 5. Test update_task_status
        print("\n5. Testing update_task_status...")
        updated = update_task_status("calendar", "cal_dentist_001", "IN_PROGRESS", db_path=test_db)
        assert updated is True
        task_in_progress = get_task("calendar", "cal_dentist_001", db_path=test_db)
        assert task_in_progress["status"] == "IN_PROGRESS"
        
        updated_done = update_task_status("calendar", "cal_dentist_001", "DONE", db_path=test_db)
        assert updated_done is True
        task_done = get_task("calendar", "cal_dentist_001", db_path=test_db)
        assert task_done["status"] == "DONE"
        print("   [PASS] State transition NOT_STARTED -> IN_PROGRESS -> DONE verified.")

        # Test upsert preserving status
        upsert_task(
            source_type="calendar",
            source_id="cal_dentist_001",
            title="Dentist Appointment (Dr. Smith - Updated Room)",
            item_date="2026-09-01T14:00:00+01:00",
            db_path=test_db
        )
        task_after_sync = get_task("calendar", "cal_dentist_001", db_path=test_db)
        assert task_after_sync["title"] == "Dentist Appointment (Dr. Smith - Updated Room)"
        assert task_after_sync["status"] == "DONE", "Upsert must preserve existing DONE status on sync"
        print("   [PASS] Re-syncing existing task updated metadata while preserving DONE status.")

        # 6. Test date range & completed tasks
        print("\n6. Testing date range queries & get_completed_tasks...")
        completed = get_completed_tasks(
            start_date="2026-09-01T00:00:00",
            end_date="2026-09-01T23:59:59",
            db_path=test_db
        )
        assert len(completed) == 1
        assert completed[0]["source_id"] == "cal_dentist_001"
        
        all_september = get_tasks_for_date_range(
            start_date="2026-09-01T00:00:00",
            end_date="2026-09-07T23:59:59",
            db_path=test_db
        )
        assert len(all_september) == 3
        print(f"   [PASS] Date range queries verified (1 completed, {len(all_september)} total in range).")

        print("\nALL STEP 2 TESTS PASSED SUCCESSFULLY.")
    finally:
        shutil.rmtree(temp_dir)
