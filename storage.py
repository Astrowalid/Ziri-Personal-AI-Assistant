import sqlite3
import os
import json
from datetime import datetime, timedelta, time
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Generator

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.db")

VALID_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "DONE", "SKIPPED"}
VALID_MATRIX_QUADRANTS = {"Q1", "Q2", "Q3", "Q4"}

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
    """Creates or migrates database tables to the latest v4 schema.
    
    Ensures:
    - task_status table has v4 schema with 'matrix' column and 'SKIPPED' status check.
    - user_preferences table exists.
    Safely migrates existing rows if the table was created under an older schema.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. Check if task_status table exists
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name = 'task_status'")
        table_info = cursor.fetchone()
        
        if table_info is None:
            # Table does not exist, create fresh v4 table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_status (
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('NOT_STARTED', 'IN_PROGRESS', 'DONE', 'SKIPPED')),
                    item_date TEXT,
                    last_synced_at TEXT,
                    updated_at TEXT,
                    matrix TEXT CHECK(matrix IN ('Q1', 'Q2', 'Q3', 'Q4') OR matrix IS NULL),
                    PRIMARY KEY (source_type, source_id)
                )
            """)
        else:
            # Table exists; check if migration is required
            cursor.execute("PRAGMA table_info(task_status)")
            cols = [row["name"] for row in cursor.fetchall()]
            table_sql = table_info["sql"] or ""
            
            needs_matrix = "matrix" not in cols
            needs_skipped = "SKIPPED" not in table_sql
            
            if needs_matrix or needs_skipped:
                # Safely migrate table to v4 schema preserving all rows
                cursor.execute("DROP TABLE IF EXISTS task_status_new")
                cursor.execute("""
                    CREATE TABLE task_status_new (
                        source_type TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('NOT_STARTED', 'IN_PROGRESS', 'DONE', 'SKIPPED')),
                        item_date TEXT,
                        last_synced_at TEXT,
                        updated_at TEXT,
                        matrix TEXT CHECK(matrix IN ('Q1', 'Q2', 'Q3', 'Q4') OR matrix IS NULL),
                        PRIMARY KEY (source_type, source_id)
                    )
                """)
                
                if "matrix" in cols:
                    cursor.execute("""
                        INSERT INTO task_status_new (
                            source_type, source_id, title, status, item_date, last_synced_at, updated_at, matrix
                        )
                        SELECT source_type, source_id, title, status, item_date, last_synced_at, updated_at, matrix
                        FROM task_status
                    """)
                else:
                    cursor.execute("""
                        INSERT INTO task_status_new (
                            source_type, source_id, title, status, item_date, last_synced_at, updated_at
                        )
                        SELECT source_type, source_id, title, status, item_date, last_synced_at, updated_at
                        FROM task_status
                    """)
                    
                cursor.execute("DROP TABLE task_status")
                cursor.execute("ALTER TABLE task_status_new RENAME TO task_status")

        # 2. Ensure user_preferences table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

# --- User Preferences CRUD Functions ---

def get_preference(key: str, default: Any = None, db_path: str = DB_PATH) -> Any:
    """Retrieves a user preference by key.
    
    If the stored value is JSON-encoded (e.g. dict or list), it is deserialized.
    Otherwise, returns the raw string value, or default if the key is not found.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_preferences WHERE key = ?", (key,))
        row = cursor.fetchone()
        if not row:
            return default
            
        raw_val = row["value"]
        if raw_val is None:
            return default
            
        try:
            return json.loads(raw_val)
        except (json.JSONDecodeError, TypeError):
            return raw_val

def set_preference(key: str, value: Any, db_path: str = DB_PATH) -> None:
    """Sets a user preference.
    
    Serializes dict/list or other objects to JSON string, or stores str as-is.
    Updates the updated_at timestamp. Uses parameterized query.
    """
    if isinstance(value, (dict, list)):
        stored_val = json.dumps(value)
    elif isinstance(value, str):
        stored_val = value
    else:
        stored_val = json.dumps(value)

    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (key, stored_val, now_iso))
        conn.commit()

def delete_preference(key: str, db_path: str = DB_PATH) -> bool:
    """Deletes a user preference by key. Returns True if a row was deleted, False otherwise."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_preferences WHERE key = ?", (key,))
        conn.commit()
        return cursor.rowcount > 0

def list_preferences(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Returns all stored user preferences as a dictionary mapping key to deserialized value."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_preferences ORDER BY key ASC")
        rows = cursor.fetchall()
        prefs: Dict[str, Any] = {}
        for row in rows:
            raw_val = row["value"]
            if raw_val is None:
                prefs[row["key"]] = None
                continue
            try:
                prefs[row["key"]] = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                prefs[row["key"]] = raw_val
        return prefs

# --- Task Functions ---

def upsert_task(
    source_type: str,
    source_id: str,
    title: str,
    status: str = "NOT_STARTED",
    item_date: Optional[str] = None,
    matrix: Optional[str] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Inserts a task or updates its metadata (title, item_date, last_synced_at, matrix).
    If the task already exists, its existing status is preserved unless explicitly overwritten.
    If matrix is provided, updates the matrix quadrant. If matrix is None on an existing task,
    the existing matrix quadrant is preserved.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")
    if matrix is not None and matrix not in VALID_MATRIX_QUADRANTS:
        raise ValueError(f"Invalid matrix quadrant: {matrix}. Must be one of {VALID_MATRIX_QUADRANTS} or None")

    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        # Check if record already exists to preserve status
        cursor.execute(
            "SELECT status, matrix FROM task_status WHERE source_type = ? AND source_id = ?",
            (source_type, source_id)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE task_status
                SET title = ?,
                    item_date = COALESCE(?, item_date),
                    matrix = COALESCE(?, matrix),
                    last_synced_at = ?
                WHERE source_type = ? AND source_id = ?
            """, (title, item_date, matrix, now_iso, source_type, source_id))
        else:
            cursor.execute("""
                INSERT INTO task_status (
                    source_type, source_id, title, status, item_date, matrix, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (source_type, source_id, title, status, item_date, matrix, now_iso, now_iso))
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
    matrix: Optional[str] = None,
    db_path: str = DB_PATH
) -> bool:
    """Updates the status and updated_at timestamp of a task.
    If the task does not exist in local storage yet and title is provided, it will be automatically registered with the given status.
    If matrix is provided, also updates the matrix quadrant.
    
    Args:
        source_type: The source ('calendar' or 'classroom').
        source_id: The verbatim Google event_id or courseWork_id.
        new_status: New status ('NOT_STARTED', 'IN_PROGRESS', 'DONE', or 'SKIPPED').
        title: Optional title of the event/task (used to auto-register if not yet tracked).
        item_date: Optional date/time string of the event/task.
        matrix: Optional matrix quadrant ('Q1', 'Q2', 'Q3', 'Q4' or None).
        db_path: Path to SQLite database.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}. Must be one of {VALID_STATUSES}")
    if matrix is not None and matrix not in VALID_MATRIX_QUADRANTS:
        raise ValueError(f"Invalid matrix quadrant: {matrix}. Must be one of {VALID_MATRIX_QUADRANTS} or None")

    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if matrix is not None:
            cursor.execute("""
                UPDATE task_status
                SET status = ?,
                    matrix = ?,
                    updated_at = ?
                WHERE source_type = ? AND source_id = ?
            """, (new_status, matrix, now_iso, source_type, source_id))
        else:
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
                    source_type, source_id, title, status, item_date, matrix, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (source_type, source_id, title, new_status, item_date, matrix, now_iso, now_iso))
            conn.commit()
            return True

        return False

def update_task_matrix(
    source_type: str,
    source_id: str,
    matrix: Optional[str],
    db_path: str = DB_PATH
) -> bool:
    """Updates the matrix quadrant of an existing task.
    
    Args:
        source_type: The source ('calendar' or 'classroom').
        source_id: The verbatim Google event_id or courseWork_id.
        matrix: One of 'Q1', 'Q2', 'Q3', 'Q4' or None to clear quadrant.
        db_path: Path to SQLite database.
        
    Returns:
        True if the task was found and updated, False otherwise.
    """
    if matrix is not None and matrix not in VALID_MATRIX_QUADRANTS:
        raise ValueError(f"Invalid matrix quadrant: {matrix}. Must be one of {VALID_MATRIX_QUADRANTS} or None")

    now_iso = datetime.now().astimezone().isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE task_status
            SET matrix = ?,
                updated_at = ?
            WHERE source_type = ? AND source_id = ?
        """, (matrix, now_iso, source_type, source_id))
        conn.commit()
        return cursor.rowcount > 0

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

def get_tasks_by_matrix(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    matrix_quadrant: Optional[str] = None,
    include_done: bool = False,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Returns tasks filtered by date range and optional matrix quadrant.
    
    Args:
        start_date: Optional ISO date string start (inclusive).
        end_date: Optional ISO date string end (inclusive).
        matrix_quadrant: 'Q1', 'Q2', 'Q3', 'Q4', 'UNASSIGNED', or None (for all).
        include_done: If False, excludes tasks with status 'DONE'.
        db_path: Path to SQLite database.
        
    Returns:
        List of matching task dictionaries.
    """
    conditions: List[str] = []
    params: List[Any] = []

    if start_date:
        conditions.append("item_date >= ?")
        params.append(start_date)

    if end_date:
        conditions.append("item_date <= ?")
        params.append(end_date)

    if matrix_quadrant is not None:
        quadrant_upper = matrix_quadrant.upper()
        if quadrant_upper == "UNASSIGNED":
            conditions.append("matrix IS NULL")
        elif quadrant_upper in VALID_MATRIX_QUADRANTS:
            conditions.append("matrix = ?")
            params.append(quadrant_upper)
        else:
            raise ValueError(f"Invalid matrix_quadrant: {matrix_quadrant}. Must be one of {VALID_MATRIX_QUADRANTS}, 'UNASSIGNED', or None")

    if not include_done:
        conditions.append("status != 'DONE'")

    sql = "SELECT * FROM task_status"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY item_date ASC, updated_at DESC"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_matrix_summary(
    start_date: str,
    end_date: str,
    db_path: str = DB_PATH,
    include_done: bool = True
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns a dictionary mapping each matrix quadrant ('Q1', 'Q2', 'Q3', 'Q4', 'UNASSIGNED')
    to its list of tasks for the given date range.
    
    Args:
        start_date: ISO date string start (inclusive).
        end_date: ISO date string end (inclusive).
        db_path: Path to SQLite database.
        include_done: If True (default), includes completed tasks.
        
    Returns:
        Dict with keys 'Q1', 'Q2', 'Q3', 'Q4', 'UNASSIGNED'.
    """
    summary: Dict[str, List[Dict[str, Any]]] = {
        "Q1": [],
        "Q2": [],
        "Q3": [],
        "Q4": [],
        "UNASSIGNED": []
    }
    tasks = get_tasks_by_matrix(
        start_date=start_date,
        end_date=end_date,
        matrix_quadrant=None,
        include_done=include_done,
        db_path=db_path
    )
    for task in tasks:
        quadrant = task.get("matrix")
        if quadrant in summary:
            summary[quadrant].append(task)
        else:
            summary["UNASSIGNED"].append(task)
    return summary

def get_carryover_tasks(
    before_date_iso: Optional[str] = None,
    days_back: int = 14,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Queries unfinished tasks scheduled before before_date_iso within a lookback window.
    
    Filters tasks where status IN ('NOT_STARTED', 'IN_PROGRESS') and item_date is before
    before_date_iso (defaults to start of today in local timezone), within days_back days.
    
    Args:
        before_date_iso (str, optional): Upper bound ISO date or datetime string (exclusive).
                                         Defaults to start of today (00:00:00) in local timezone.
        days_back (int, optional): Number of days back from before_date to query (default 14).
        db_path (str, optional): Path to SQLite database file.
        
    Returns:
        List[Dict[str, Any]]: List of carried-over unfinished task dictionaries,
                              ordered chronologically by item_date ASC, updated_at DESC.
    """
    local_tz = datetime.now().astimezone().tzinfo
    if not before_date_iso:
        now = datetime.now(local_tz)
        before_dt = datetime.combine(now.date(), time.min).replace(tzinfo=local_tz)
        before_bound = before_dt.isoformat()
    else:
        try:
            before_dt = datetime.fromisoformat(before_date_iso)
        except ValueError:
            before_dt = datetime.strptime(before_date_iso[:10], "%Y-%m-%d")
        if before_dt.tzinfo is None:
            before_dt = before_dt.replace(tzinfo=local_tz)
        before_bound = before_date_iso

    cutoff_dt = before_dt - timedelta(days=days_back)
    cutoff_bound = cutoff_dt.isoformat()

    # Normalise bounds so string comparison in SQLite matches whether item_date has 'T' or not
    before_norm = before_bound if "T" in before_bound else f"{before_bound}T00:00:00"
    cutoff_norm = cutoff_bound if "T" in cutoff_bound else f"{cutoff_bound}T00:00:00"

    sql = """
        SELECT * FROM task_status
        WHERE status IN ('NOT_STARTED', 'IN_PROGRESS')
          AND item_date IS NOT NULL
          AND (CASE WHEN instr(item_date, 'T') > 0 THEN item_date ELSE item_date || 'T00:00:00' END) < ?
          AND (CASE WHEN instr(item_date, 'T') > 0 THEN item_date ELSE item_date || 'T00:00:00' END) >= ?
        ORDER BY item_date ASC, updated_at DESC
    """
    params = [before_norm, cutoff_norm]

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

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
    print(f"Running Phase 1 verification tests against test database: {test_db}\n")

    try:
        # --- TEST GROUP 1: Migration from legacy v3 schema ---
        print("1. Testing legacy v3 schema migration...")
        # Manually create legacy v3 schema
        with sqlite3.connect(test_db) as legacy_conn:
            legacy_conn.execute("""
                CREATE TABLE task_status (
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
            legacy_conn.execute("""
                INSERT INTO task_status (source_type, source_id, title, status, item_date, last_synced_at, updated_at)
                VALUES ('classroom', 'legacy_task_1', 'Calculus Problem Set 1', 'DONE', '2026-09-01T10:00:00', '2026-09-01T08:00:00', '2026-09-01T08:00:00')
            """)
            legacy_conn.commit()

        # Run init_db to trigger migration
        init_db(db_path=test_db)

        # Verify legacy row is preserved
        migrated_task = get_task("classroom", "legacy_task_1", db_path=test_db)
        assert migrated_task is not None, "Legacy row must be preserved after migration"
        assert migrated_task["title"] == "Calculus Problem Set 1"
        assert migrated_task["status"] == "DONE"
        assert migrated_task["matrix"] is None

        # Verify SKIPPED status is now accepted
        skipped_task = upsert_task(
            source_type="calendar",
            source_id="cal_skipped_01",
            title="Optional Networking Mixer",
            status="SKIPPED",
            matrix="Q4",
            db_path=test_db
        )
        assert skipped_task["status"] == "SKIPPED"
        assert skipped_task["matrix"] == "Q4"
        print("   [PASS] Legacy migration preserved rows, added matrix column, and enabled SKIPPED status.")

        # --- TEST GROUP 2: User Preferences CRUD ---
        print("\n2. Testing User Preferences CRUD...")
        # String preference
        set_preference("user_name", "Walid", db_path=test_db)
        assert get_preference("user_name", db_path=test_db) == "Walid"

        # List preference
        energy_slots = ["09:00-11:00", "15:00-17:00"]
        set_preference("energy_hours", energy_slots, db_path=test_db)
        fetched_energy = get_preference("energy_hours", db_path=test_db)
        assert fetched_energy == energy_slots, f"Expected {energy_slots}, got {fetched_energy}"

        # Dict preference
        hobbies_dict = {"fitness": ["Gym", "Running"], "relax": "Reading"}
        set_preference("hobbies", hobbies_dict, db_path=test_db)
        fetched_hobbies = get_preference("hobbies", db_path=test_db)
        assert fetched_hobbies == hobbies_dict

        # Integer & Boolean preference
        set_preference("max_focus_duration", 90, db_path=test_db)
        assert get_preference("max_focus_duration", db_path=test_db) == 90
        set_preference("auto_rollover", True, db_path=test_db)
        assert get_preference("auto_rollover", db_path=test_db) is True

        # Default fallback for missing key
        assert get_preference("nonexistent_key", default="DEFAULT", db_path=test_db) == "DEFAULT"
        assert get_preference("nonexistent_key", db_path=test_db) is None

        # List all preferences
        all_prefs = list_preferences(db_path=test_db)
        assert "user_name" in all_prefs and all_prefs["user_name"] == "Walid"
        assert "energy_hours" in all_prefs and all_prefs["energy_hours"] == energy_slots
        assert "hobbies" in all_prefs and all_prefs["hobbies"] == hobbies_dict
        assert "max_focus_duration" in all_prefs and all_prefs["max_focus_duration"] == 90
        assert "auto_rollover" in all_prefs and all_prefs["auto_rollover"] is True

        # Delete preference
        deleted = delete_preference("auto_rollover", db_path=test_db)
        assert deleted is True
        assert get_preference("auto_rollover", db_path=test_db) is None
        assert delete_preference("auto_rollover", db_path=test_db) is False
        print("   [PASS] User preferences CRUD verified for strings, lists, dicts, numbers, booleans.")

        # --- TEST GROUP 3: Task Matrix & Status Updates ---
        print("\n3. Testing Matrix Quadrant & Status operations...")
        # Create task with matrix
        t1 = upsert_task(
            source_type="classroom",
            source_id="cls_assignment_q1",
            title="ML Lab Submission",
            status="NOT_STARTED",
            item_date="2026-09-02T23:59:00",
            matrix="Q1",
            db_path=test_db
        )
        assert t1["matrix"] == "Q1"

        # Update matrix quadrant via update_task_matrix
        ok = update_task_matrix("classroom", "cls_assignment_q1", "Q2", db_path=test_db)
        assert ok is True
        assert get_task("classroom", "cls_assignment_q1", db_path=test_db)["matrix"] == "Q2"

        # Clear matrix quadrant to None
        ok = update_task_matrix("classroom", "cls_assignment_q1", None, db_path=test_db)
        assert ok is True
        assert get_task("classroom", "cls_assignment_q1", db_path=test_db)["matrix"] is None

        # Re-assign back to Q1
        update_task_matrix("classroom", "cls_assignment_q1", "Q1", db_path=test_db)

        # Update status and matrix simultaneously
        ok = update_task_status("classroom", "cls_assignment_q1", new_status="IN_PROGRESS", matrix="Q2", db_path=test_db)
        assert ok is True
        t1_updated = get_task("classroom", "cls_assignment_q1", db_path=test_db)
        assert t1_updated["status"] == "IN_PROGRESS"
        assert t1_updated["matrix"] == "Q2"

        # Upsert preserving matrix when matrix=None
        upsert_task(
            source_type="classroom",
            source_id="cls_assignment_q1",
            title="ML Lab Submission - Updated Name",
            item_date="2026-09-02T23:59:00",
            db_path=test_db
        )
        t1_synced = get_task("classroom", "cls_assignment_q1", db_path=test_db)
        assert t1_synced["matrix"] == "Q2", "Upsert with matrix=None must preserve existing matrix assignment"
        assert t1_synced["title"] == "ML Lab Submission - Updated Name"

        # Test invalid matrix quadrant error handling
        try:
            update_task_matrix("classroom", "cls_assignment_q1", "Q99", db_path=test_db)
            assert False, "Should have raised ValueError for invalid quadrant"
        except ValueError:
            pass

        # Test invalid status error handling
        try:
            update_task_status("classroom", "cls_assignment_q1", "INVALID_STATUS", db_path=test_db)
            assert False, "Should have raised ValueError for invalid status"
        except ValueError:
            pass
        print("   [PASS] Matrix assignment, preservation, and status updates verified.")

        # --- TEST GROUP 4: Matrix Query Helpers ---
        print("\n4. Testing Matrix Query Helpers...")
        # Populate tasks across quadrants
        upsert_task("calendar", "q1_task", "Submit Assignment", "NOT_STARTED", "2026-09-03T10:00:00", matrix="Q1", db_path=test_db)
        upsert_task("calendar", "q2_task", "Midterm Prep Block", "NOT_STARTED", "2026-09-03T14:00:00", matrix="Q2", db_path=test_db)
        upsert_task("calendar", "q3_task", "Complete 10min Survey", "DONE", "2026-09-03T16:00:00", matrix="Q3", db_path=test_db)
        upsert_task("calendar", "q4_task", "Browse Socials", "SKIPPED", "2026-09-03T18:00:00", matrix="Q4", db_path=test_db)
        upsert_task("calendar", "unassigned_task", "Unplanned Chore", "NOT_STARTED", "2026-09-03T20:00:00", matrix=None, db_path=test_db)

        # get_tasks_by_matrix with include_done=False
        active_q3 = get_tasks_by_matrix(matrix_quadrant="Q3", include_done=False, db_path=test_db)
        assert len(active_q3) == 0, "DONE task should be excluded when include_done=False"

        # get_tasks_by_matrix with include_done=True
        all_q3 = get_tasks_by_matrix(matrix_quadrant="Q3", include_done=True, db_path=test_db)
        assert len(all_q3) == 1
        assert all_q3[0]["source_id"] == "q3_task"

        # get_tasks_by_matrix for UNASSIGNED
        unassigned = get_tasks_by_matrix(matrix_quadrant="UNASSIGNED", db_path=test_db)
        unassigned_ids = [t["source_id"] for t in unassigned]
        assert "unassigned_task" in unassigned_ids

        # get_matrix_summary
        summary = get_matrix_summary(
            start_date="2026-09-03T00:00:00",
            end_date="2026-09-03T23:59:59",
            db_path=test_db,
            include_done=True
        )
        assert "Q1" in summary and len(summary["Q1"]) == 1
        assert "Q2" in summary and len(summary["Q2"]) == 1
        assert "Q3" in summary and len(summary["Q3"]) == 1
        assert "Q4" in summary and len(summary["Q4"]) == 1
        assert "UNASSIGNED" in summary and len(summary["UNASSIGNED"]) == 1
        print("   [PASS] Matrix query helpers and get_matrix_summary verified.")

        # --- TEST GROUP 5: Preserved v3 existing functionality ---
        print("\n5. Verifying preserved v3 functionality (search, completed tasks)...")
        found = find_tasks("Midterm Prep", db_path=test_db)
        assert len(found) == 1
        assert found[0]["source_id"] == "q2_task"

        completed = get_completed_tasks("2026-09-03T00:00:00", "2026-09-03T23:59:59", db_path=test_db)
        assert len(completed) == 1
        assert completed[0]["source_id"] == "q3_task"
        print("   [PASS] Existing search and completion functions working properly.")

        # --- TEST GROUP 6: Carryover Tasks (Phase 2) ---
        print("\n6. Testing get_carryover_tasks...")
        # Populate tasks for carryover test
        # Base date: 2026-09-04T00:00:00
        # Carryover candidate 1: 2026-09-02 NOT_STARTED (within 14 days, before base date) -> should be included
        # Carryover candidate 2: 2026-09-03 IN_PROGRESS (within 14 days, before base date) -> should be included
        # Completed task: 2026-09-02 DONE -> should be excluded
        # Skipped task: 2026-09-02 SKIPPED -> should be excluded
        # Future task: 2026-09-05 NOT_STARTED -> should be excluded
        # Too old task: 2026-08-15 NOT_STARTED (> 14 days ago) -> should be excluded
        upsert_task("calendar", "carry_1", "Carryover 1", "NOT_STARTED", "2026-09-02T10:00:00", db_path=test_db)
        upsert_task("calendar", "carry_2", "Carryover 2", "IN_PROGRESS", "2026-09-03T15:00:00", db_path=test_db)
        upsert_task("calendar", "carry_done", "Carryover Done", "DONE", "2026-09-02T12:00:00", db_path=test_db)
        upsert_task("calendar", "carry_skip", "Carryover Skipped", "SKIPPED", "2026-09-02T13:00:00", db_path=test_db)
        upsert_task("calendar", "future_task", "Future Task", "NOT_STARTED", "2026-09-05T10:00:00", db_path=test_db)
        upsert_task("calendar", "old_task", "Too Old Task", "NOT_STARTED", "2026-08-15T10:00:00", db_path=test_db)

        carryovers = get_carryover_tasks(before_date_iso="2026-09-04T00:00:00", days_back=14, db_path=test_db)
        carry_ids = [t["source_id"] for t in carryovers]
        assert "carry_1" in carry_ids, "carry_1 should be in carryovers"
        assert "carry_2" in carry_ids, "carry_2 should be in carryovers"
        assert "carry_done" not in carry_ids, "DONE task should NOT be in carryovers"
        assert "carry_skip" not in carry_ids, "SKIPPED task should NOT be in carryovers"
        assert "future_task" not in carry_ids, "Future task should NOT be in carryovers"
        assert "old_task" not in carry_ids, "Task older than days_back should NOT be in carryovers"
        print("   [PASS] get_carryover_tasks verified with status, future, and age bounds.")

        print("\nALL STORAGE VERIFICATION TESTS PASSED SUCCESSFULLY.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
