import os
import pickle
from datetime import datetime, time, timezone, timedelta
from typing import List, Dict, Any, Optional
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from storage import upsert_task

def get_classroom_service():
    """Helper to authenticate and return the Google Classroom API service."""
    creds = None
    if os.path.exists('token_classroom.pickle'):
        with open('token_classroom.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    # Refresh credentials if expired
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired Google Classroom credentials...")
            # Import to set the OAUTHLIB_RELAX_TOKEN_SCOPE env var
            import classroom_auth
            creds.refresh(Request())
            with open('token_classroom.pickle', 'wb') as token:
                pickle.dump(creds, token)
        else:
            raise Exception("Classroom credentials not found or invalid. Please run classroom_auth.py first.")
            
    return build('classroom', 'v1', credentials=creds)

def parse_classroom_due_date(due_date_obj, due_time_obj):
    """Converts Google Classroom's due date and time objects to a timezone-aware datetime."""
    if not due_date_obj:
        return None
        
    year = due_date_obj.get('year')
    month = due_date_obj.get('month')
    day = due_date_obj.get('day')
    
    # Check if due_time is specified
    if due_time_obj:
        hours = due_time_obj.get('hours', 0)
        minutes = due_time_obj.get('minutes', 0)
        seconds = due_time_obj.get('seconds', 0)
    else:
        # Default to end of day if no time is provided
        hours, minutes, seconds = 23, 59, 59
        
    try:
        # Classroom API returns dates/times in UTC by default
        return datetime(year, month, day, hours, minutes, seconds, tzinfo=timezone.utc)
    except ValueError:
        return None

import time as time_module
from concurrent.futures import ThreadPoolExecutor

# In-memory cache for Classroom assignments to eliminate redundant network roundtrips
_CLASSROOM_CACHE = {
    "timestamp": 0.0,
    "data": []
}
CLASSROOM_CACHE_TTL = 180  # 3 minutes

def _fetch_single_course(course):
    """Fetches coursework and student submissions for a single course using a thread-safe service instance."""
    service = get_classroom_service()
    course_id = course['id']
    course_name = course['name']
    course_assignments = []
    
    try:
        coursework_result = service.courses().courseWork().list(courseId=course_id).execute()
        coursework_items = coursework_result.get('courseWork', [])
    except Exception as e:
        print(f"Error fetching coursework for course {course_name}: {e}")
        return []
        
    if not coursework_items:
        return []
        
    try:
        submissions_result = service.courses().courseWork().studentSubmissions().list(
            courseId=course_id,
            courseWorkId='-',
            userId='me'
        ).execute()
        submissions = submissions_result.get('studentSubmissions', [])
    except Exception as e:
        print(f"Error fetching submissions for course {course_name}: {e}")
        submissions = []
        
    submission_map = {sub.get('courseWorkId'): sub.get('state', 'NEW') for sub in submissions}
    
    for item in coursework_items:
        if item.get('workType') != 'ASSIGNMENT':
            continue
            
        cw_id = item['id']
        title = item['title']
        description = item.get('description', '')
        due_dt = parse_classroom_due_date(item.get('dueDate'), item.get('dueTime'))
        
        state = submission_map.get(cw_id, 'NEW')
        is_submitted = state in ['TURNED_IN', 'RETURNED']
        due_date_str = due_dt.strftime('%Y-%m-%d %H:%M') if due_dt else 'No due date'
        
        try:
            upsert_task(
                source_type="classroom",
                source_id=cw_id,
                title=title,
                status="NOT_STARTED",
                item_date=due_dt.isoformat() if due_dt else None
            )
        except Exception as e:
            print(f"Warning: failed to sync Classroom assignment '{title}' to storage: {e}")

        course_assignments.append({
            'course_id': course_id,
            'course_name': course_name,
            'assignment_id': cw_id,
            'title': title,
            'description': description,
            'due_date_utc': due_dt.isoformat() if due_dt else None,
            'due_date_str': due_date_str,
            'state': state,
            'submitted': is_submitted
        })
        
    return course_assignments

def list_classroom_assignments(force_refresh=False):
    """Fetches all active courses and their corresponding assignments with submission status.
    Uses concurrent fetching and caching to optimize response latency.
    
    Returns:
        list of dict: Unified list of assignments with titles, due dates, course names, and submission status.
    """
    now_ts = time_module.time()
    if not force_refresh and (now_ts - _CLASSROOM_CACHE["timestamp"] < CLASSROOM_CACHE_TTL):
        return _CLASSROOM_CACHE["data"]

    service = get_classroom_service()
    
    # 1. Fetch active courses
    courses_result = service.courses().list(studentId='me', courseStates=['ACTIVE']).execute()
    courses = courses_result.get('courses', [])
    
    if not courses:
        print("No active Classroom courses found.")
        _CLASSROOM_CACHE["timestamp"] = now_ts
        _CLASSROOM_CACHE["data"] = []
        return []
        
    all_assignments = []
    
    # 2. Fetch coursework for all courses concurrently using ThreadPoolExecutor
    workers = min(8, max(1, len(courses)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(_fetch_single_course, courses)
        for course_assignments in results:
            all_assignments.extend(course_assignments)
            
    # Sort assignments by due date (items with no due date at the end)
    all_assignments.sort(key=lambda x: (x['due_date_utc'] is None, x['due_date_utc']))
    
    # Update cache
    _CLASSROOM_CACHE["timestamp"] = now_ts
    _CLASSROOM_CACHE["data"] = all_assignments
    return all_assignments

def get_upcoming_assignments(
    days: int = 7,
    include_submitted: bool = False,
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """Fetches assignments due within the next `days` days from Google Classroom.
    
    Args:
        days: Number of days into the future to look (default 7).
        include_submitted: If False, excludes already submitted/returned assignments.
        force_refresh: If True, bypasses the in-memory cache.
        
    Returns:
        List of assignment dicts, filtered and sorted chronologically by due date
        (assignments with due dates first, then assignments with no due date).
    """
    assignments = list_classroom_assignments(force_refresh=force_refresh)
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    
    upcoming: List[Dict[str, Any]] = []
    for assign in assignments:
        is_submitted = assign.get('submitted', False)
        if not include_submitted and is_submitted:
            continue
            
        due_val = assign.get('due_date_utc')
        if due_val:
            try:
                if isinstance(due_val, datetime):
                    due_dt = due_val
                else:
                    due_str = str(due_val).replace('Z', '+00:00')
                    due_dt = datetime.fromisoformat(due_str)
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                if now <= due_dt <= cutoff:
                    upcoming.append(assign)
            except (ValueError, TypeError):
                continue
        else:
            # Include assignments with no due date if unsubmitted, or if include_submitted is requested
            if not is_submitted or include_submitted:
                upcoming.append(assign)
                
    # Sort chronologically by due date (items with due date first, then items with no due date)
    upcoming.sort(key=lambda x: (x.get('due_date_utc') is None, str(x.get('due_date_utc') or '')))
    return upcoming

if __name__ == '__main__':
    print("Testing Google Classroom API client...")
    try:
        assignments = list_classroom_assignments()
        print(f"\nFound {len(assignments)} assignments:")
        for idx, assign in enumerate(assignments, 1):
            status = "Submitted" if assign['submitted'] else "Not submitted"
            print(f"{idx}. [{assign['course_name']}] {assign['title']} — due: {assign['due_date_str']} — Status: {status} (state: {assign['state']})")
    except Exception as e:
        print(f"Error checking Classroom assignments: {e}")



