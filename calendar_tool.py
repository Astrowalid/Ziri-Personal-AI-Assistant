import os
import pickle
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Any, Tuple
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from calendar_auth import SCOPES
from storage import upsert_task

def get_calendar_service():
    """Helper to authenticate and return the Google Calendar API service."""
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    # Refresh credentials if expired
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired Google Calendar credentials...")
            try:
                creds.refresh(Request())
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
            except Exception as e:
                raise Exception(f"Google Calendar token refresh failed ({e}). Please run calendar_auth.py to re-authenticate.")
        else:
            raise Exception("Credentials not found or invalid. Please run calendar_auth.py first.")
            
    return build('calendar', 'v3', credentials=creds)

import time as time_module

_CALENDAR_CACHE = {}
CALENDAR_CACHE_TTL = 60  # 60 seconds

def list_events(time_min_iso=None, time_max_iso=None, force_refresh=False):
    """Lists events on the primary calendar for a given timeframe.
    
    Args:
        time_min_iso (str, optional): Start of the time range in ISO 8601 format. Defaults to the start of today.
        time_max_iso (str, optional): End of the time range in ISO 8601 format. Defaults to the end of today.
        force_refresh (bool, optional): If True, bypasses the in-memory cache.
    """
    cache_key = (time_min_iso, time_max_iso)
    now_ts = time_module.time()
    if not force_refresh and cache_key in _CALENDAR_CACHE:
        cached_ts, cached_events = _CALENDAR_CACHE[cache_key]
        if now_ts - cached_ts < CALENDAR_CACHE_TTL:
            return cached_events

    service = get_calendar_service()
    
    # Get local timezone
    local_tz = datetime.now().astimezone().tzinfo
    
    # Define start and end of range
    if not time_min_iso:
        now = datetime.now(local_tz)
        start_of_today = datetime.combine(now.date(), time.min).replace(tzinfo=local_tz)
        time_min = start_of_today.isoformat()
    else:
        time_min = time_min_iso
        
    if not time_max_iso:
        now = datetime.now(local_tz)
        end_of_today = datetime.combine(now.date(), time.max).replace(tzinfo=local_tz)
        time_max = end_of_today.isoformat()
    else:
        time_max = time_max_iso
    
    print(f"Fetching events between {time_min} and {time_max}...")
    
    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    events = events_result.get('items', [])
    for event in events:
        event_id = event.get('id')
        summary = event.get('summary') or 'Untitled Event'
        start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
        if event_id:
            try:
                upsert_task(
                    source_type="calendar",
                    source_id=event_id,
                    title=summary,
                    status="NOT_STARTED",
                    item_date=start
                )
            except Exception as e:
                print(f"Warning: failed to sync calendar event '{summary}' to storage: {e}")

    _CALENDAR_CACHE[cache_key] = (now_ts, events)
    return events

def create_calendar_event(summary, start_time_iso, duration_minutes=60, description=None):
    """Creates an event on the primary calendar.
    
    Args:
        summary (str): The title of the event.
        start_time_iso (str): Start time in ISO 8601 format (e.g., '2026-08-15T15:00:00+01:00').
        duration_minutes (int): Duration of the event in minutes. Defaults to 60.
        description (str, optional): Description of the event.
    """
    service = get_calendar_service()
    
    # Parse start time
    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    
    event_body = {
        'summary': summary,
        'description': description or '',
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': str(start_dt.tzinfo or 'UTC'),
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': str(end_dt.tzinfo or 'UTC'),
        }
    }
    
    print(f"Creating event: {summary} on {start_dt.isoformat()} for {duration_minutes} mins...")
    event = service.events().insert(calendarId='primary', body=event_body).execute()
    
    # Upsert the newly created event into local storage
    if event.get('id'):
        try:
            upsert_task(
                source_type="calendar",
                source_id=event['id'],
                title=summary,
                status="NOT_STARTED",
                item_date=start_dt.isoformat()
            )
        except Exception as e:
            print(f"Warning: failed to sync newly created event to storage: {e}")

    # Invalidate list cache so newly created event is immediately visible
    _CALENDAR_CACHE.clear()

    return event

def find_calendar_events(query, time_min_iso=None, time_max_iso=None):
    """Finds events on the primary calendar matching a text query (searching titles, descriptions, etc.).
    
    Args:
        query (str): The search term to match (e.g., 'dentist').
        time_min_iso (str, optional): Start of the search range in ISO format.
                                     If not specified, all past and future events matching the query will be searched.
        time_max_iso (str, optional): End of the search range in ISO format.
    """
    service = get_calendar_service()
    
    print(f"Searching calendar for query '{query}' (time_min: {time_min_iso}, time_max: {time_max_iso})...")
    
    events_result = service.events().list(
        calendarId='primary',
        q=query,
        timeMin=time_min_iso,
        timeMax=time_max_iso,
        singleEvents=True
    ).execute()
    
    events = events_result.get('items', [])
    for event in events:
        event_id = event.get('id')
        summary = event.get('summary') or 'Untitled Event'
        start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
        if event_id:
            try:
                upsert_task(
                    source_type="calendar",
                    source_id=event_id,
                    title=summary,
                    status="NOT_STARTED",
                    item_date=start
                )
            except Exception as e:
                print(f"Warning: failed to sync found calendar event '{summary}' to storage: {e}")

    return events

def update_calendar_event(event_id, summary=None, start_time_iso=None, duration_minutes=None, description=None):
    """Updates an existing event on the primary calendar.
    Only the provided fields will be updated; other fields will remain unchanged.
    
    Args:
        event_id (str): The ID of the event to update.
        summary (str, optional): New title of the event.
        start_time_iso (str, optional): New start time in ISO format.
        duration_minutes (int, optional): New duration in minutes.
        description (str, optional): New description of the event.
    """
    service = get_calendar_service()
    
    # Get the existing event
    event = service.events().get(calendarId='primary', eventId=event_id).execute()
    
    if summary is not None:
        event['summary'] = summary
    if description is not None:
        event['description'] = description
        
    if start_time_iso is not None:
        start_dt = datetime.fromisoformat(start_time_iso)
        event['start'] = {
            'dateTime': start_dt.isoformat(),
            'timeZone': str(start_dt.tzinfo or 'UTC')
        }
        
        # Calculate end time based on new duration or current duration
        if duration_minutes is not None:
            end_dt = start_dt + timedelta(minutes=duration_minutes)
        else:
            # Parse existing start and end to preserve duration
            old_start = datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
            old_end = datetime.fromisoformat(event['end'].get('dateTime').replace('Z', '+00:00'))
            duration = old_end - old_start
            end_dt = start_dt + duration
            
        event['end'] = {
            'dateTime': end_dt.isoformat(),
            'timeZone': str(end_dt.tzinfo or 'UTC')
        }
    elif duration_minutes is not None:
        # Just update duration based on current start time
        start_dt = datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        event['end'] = {
            'dateTime': end_dt.isoformat(),
            'timeZone': str(end_dt.tzinfo or 'UTC')
        }
        
    print(f"Updating event ID {event_id}...")
    updated_event = service.events().update(
        calendarId='primary',
        eventId=event_id,
        body=event
    ).execute()
    
    # Invalidate list cache so updated event is immediately visible
    _CALENDAR_CACHE.clear()
    
    return updated_event

def get_week_schedule(
    start_date_iso: Optional[str] = None,
    days: int = 7,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """Retrieves and organizes calendar events across a multi-day window.
    
    Args:
        start_date_iso (str, optional): Start of the schedule window in ISO format (or 'YYYY-MM-DD').
                                        Defaults to 00:00:00 of today in local timezone.
        days (int, optional): Number of days to span (default 7).
        force_refresh (bool, optional): If True, bypasses the in-memory cache.
        
    Returns:
        Dict[str, Any]: {
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",
            "days": {
                "YYYY-MM-DD": [event, ...],
                ...
            }
        }
    """
    local_tz = datetime.now().astimezone().tzinfo
    if not start_date_iso:
        now = datetime.now(local_tz)
        start_dt = datetime.combine(now.date(), time.min).replace(tzinfo=local_tz)
    else:
        try:
            parsed = datetime.fromisoformat(start_date_iso)
        except ValueError:
            parsed = datetime.strptime(start_date_iso[:10], "%Y-%m-%d")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=local_tz)
        start_dt = datetime.combine(parsed.date(), time.min).replace(tzinfo=parsed.tzinfo)

    end_dt = start_dt + timedelta(days=days)

    time_min_iso = start_dt.isoformat()
    time_max_iso = end_dt.isoformat()

    events = list_events(time_min_iso=time_min_iso, time_max_iso=time_max_iso, force_refresh=force_refresh)

    # Initialize empty list for each day in range
    days_dict: Dict[str, List[Dict[str, Any]]] = {}
    for i in range(days):
        day_key = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        days_dict[day_key] = []

    for event in events:
        start_info = event.get('start', {})
        start_val = start_info.get('dateTime') or start_info.get('date')
        if not start_val:
            continue

        if 'T' in start_val:
            try:
                ev_dt = datetime.fromisoformat(start_val.replace('Z', '+00:00'))
                ev_local = ev_dt.astimezone(local_tz)
                date_key = ev_local.strftime("%Y-%m-%d")
            except Exception:
                date_key = start_val[:10]
        else:
            date_key = start_val[:10]

        if date_key in days_dict:
            days_dict[date_key].append(event)
        else:
            days_dict.setdefault(date_key, []).append(event)

    return {
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
        "days": days_dict
    }

def find_schedule_gaps(
    date_str: str,
    day_start_hour: int = 8,
    day_end_hour: int = 21,
    min_gap_minutes: int = 15,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Analyzes existing calendar events for a specific day and detects free time gaps.
    
    Merges overlapping or adjacent events to determine busy intervals between
    day_start_hour:00 and day_end_hour:00, inverts the busy intervals to find free gaps
    >= min_gap_minutes, and categorizes them:
        - "QUICK_TIME": duration between 15 and 45 minutes (prime micro-windows between commitments)
        - "MEDIUM": duration between 46 and 89 minutes
        - "DEEP_WORK": duration >= 90 minutes (uninterrupted focus blocks)
        
    Args:
        date_str (str): Target day in "YYYY-MM-DD" format.
        day_start_hour (int, optional): Earliest scanning hour (default 8 -> 08:00).
        day_end_hour (int, optional): Latest scanning hour (default 21 -> 21:00).
        min_gap_minutes (int, optional): Minimum duration in minutes for a free block (default 15).
        db_path (str, optional): SQLite DB path for stored tasks or offline testing.
        
    Returns:
        List[Dict[str, Any]]: List of gap objects:
            [{"date": date_str, "start": "HH:MM", "end": "HH:MM", "duration_minutes": X,
              "category": "QUICK_TIME" | "DEEP_WORK" | "MEDIUM",
              "start_iso": ..., "end_iso": ...}, ...]
    """
    local_tz = datetime.now().astimezone().tzinfo
    day_dt = datetime.strptime(date_str, "%Y-%m-%d")
    window_start = datetime.combine(day_dt.date(), time(hour=day_start_hour, minute=0)).replace(tzinfo=local_tz)
    window_end = datetime.combine(day_dt.date(), time(hour=day_end_hour, minute=0)).replace(tzinfo=local_tz)

    events: List[Dict[str, Any]] = []

    # 1. Fetch events from database if db_path is provided
    if db_path is not None:
        try:
            from storage import get_tasks_for_date_range
            day_start_iso = datetime.combine(day_dt.date(), time.min).replace(tzinfo=local_tz).isoformat()
            day_end_iso = datetime.combine(day_dt.date(), time.max).replace(tzinfo=local_tz).isoformat()
            db_tasks = get_tasks_for_date_range(day_start_iso, day_end_iso, db_path=db_path)
            for t in db_tasks:
                st = t.get("item_date")
                if st:
                    events.append({
                        "id": t.get("source_id"),
                        "summary": t.get("title", ""),
                        "start": {"dateTime": st if "T" in st else f"{st}T00:00:00"},
                        "end": {"dateTime": t.get("end_date") or t.get("end_time") or t.get("end_datetime")}
                    })
        except Exception as e:
            print(f"Warning: failed reading tasks from db_path ({e})")

    # If no db_path was provided, or db_path returned no events, try list_events
    if not events:
        try:
            day_start_iso = datetime.combine(day_dt.date(), time.min).replace(tzinfo=local_tz).isoformat()
            day_end_iso = datetime.combine(day_dt.date(), time.max).replace(tzinfo=local_tz).isoformat()
            events = list_events(time_min_iso=day_start_iso, time_max_iso=day_end_iso)
        except Exception as e:
            # Fallback to local storage if Google API is unreachable
            try:
                from storage import get_tasks_for_date_range, DB_PATH
                day_start_iso = datetime.combine(day_dt.date(), time.min).replace(tzinfo=local_tz).isoformat()
                day_end_iso = datetime.combine(day_dt.date(), time.max).replace(tzinfo=local_tz).isoformat()
                fallback_db = db_path or DB_PATH
                db_tasks = get_tasks_for_date_range(day_start_iso, day_end_iso, db_path=fallback_db)
                for t in db_tasks:
                    st = t.get("item_date")
                    if st:
                        events.append({
                            "id": t.get("source_id"),
                            "summary": t.get("title", ""),
                            "start": {"dateTime": st if "T" in st else f"{st}T00:00:00"},
                            "end": {"dateTime": t.get("end_date") or t.get("end_time")}
                        })
            except Exception:
                events = []

    busy_intervals: List[Tuple[datetime, datetime]] = []

    for event in events:
        start_info = event.get('start', {})
        end_info = event.get('end', {})

        # Check for all-day event
        if 'date' in start_info and 'dateTime' not in start_info:
            ev_date = start_info['date']
            end_date = end_info.get('date', ev_date)
            # If date_str falls within all-day range [ev_date, end_date]
            if ev_date <= date_str <= end_date:
                busy_intervals.append((window_start, window_end))
                continue

        start_str = start_info.get('dateTime') or start_info.get('date')
        if not start_str:
            continue

        try:
            ev_start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=local_tz)
            else:
                ev_start = ev_start.astimezone(local_tz)
        except Exception:
            continue

        end_str = end_info.get('dateTime') or end_info.get('date') if end_info else None
        if end_str:
            try:
                ev_end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                if ev_end.tzinfo is None:
                    ev_end = ev_end.replace(tzinfo=local_tz)
                else:
                    ev_end = ev_end.astimezone(local_tz)
            except Exception:
                ev_end = ev_start + timedelta(minutes=60)
        else:
            ev_end = ev_start + timedelta(minutes=60)

        # Clamp to [window_start, window_end]
        b_start = max(window_start, ev_start)
        b_end = min(window_end, ev_end)

        if b_end > b_start:
            busy_intervals.append((b_start, b_end))

    # Merge overlapping and adjacent busy intervals
    busy_intervals.sort(key=lambda x: x[0])
    merged_busy: List[Tuple[datetime, datetime]] = []
    for b_start, b_end in busy_intervals:
        if not merged_busy:
            merged_busy.append((b_start, b_end))
        else:
            last_start, last_end = merged_busy[-1]
            if b_start <= last_end:  # Adjacent or overlapping
                merged_busy[-1] = (last_start, max(last_end, b_end))
            else:
                merged_busy.append((b_start, b_end))

    # Invert busy intervals to find free gaps
    free_gaps: List[Dict[str, Any]] = []
    curr = window_start

    for b_start, b_end in merged_busy:
        if b_start > curr:
            gap_duration = int(round((b_start - curr).total_seconds() / 60))
            if gap_duration >= min_gap_minutes:
                if gap_duration >= 90:
                    cat = "DEEP_WORK"
                elif gap_duration >= 46:
                    cat = "MEDIUM"
                else:
                    cat = "QUICK_TIME"

                free_gaps.append({
                    "date": date_str,
                    "start": curr.strftime("%H:%M"),
                    "end": b_start.strftime("%H:%M"),
                    "duration_minutes": gap_duration,
                    "category": cat,
                    "start_iso": curr.isoformat(),
                    "end_iso": b_start.isoformat(),
                })
        curr = max(curr, b_end)

    if curr < window_end:
        gap_duration = int(round((window_end - curr).total_seconds() / 60))
        if gap_duration >= min_gap_minutes:
            if gap_duration >= 90:
                cat = "DEEP_WORK"
            elif gap_duration >= 46:
                cat = "MEDIUM"
            else:
                cat = "QUICK_TIME"

            free_gaps.append({
                "date": date_str,
                "start": curr.strftime("%H:%M"),
                "end": window_end.strftime("%H:%M"),
                "duration_minutes": gap_duration,
                "category": cat,
                "start_iso": curr.isoformat(),
                "end_iso": window_end.isoformat(),
            })

    return free_gaps

if __name__ == '__main__':
    # Local test
    print("Testing Google Calendar APIs...")
    try:
        events = list_events()
        print(f"\nFound {len(events)} events for today:")
        for idx, event in enumerate(events, 1):
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f"{idx}. [{start}] {event.get('summary')} - {event.get('description', '')}")
            
        print("\nTesting event creation...")
        # Create a test event 2 hours from now for 30 minutes
        local_tz = datetime.now().astimezone().tzinfo
        test_start = datetime.now(local_tz) + timedelta(hours=2)
        test_summary = "Test Calendar Event from ADK Bot"
        created_event = create_calendar_event(
            summary=test_summary,
            start_time_iso=test_start.isoformat(),
            duration_minutes=30,
            description="This is a test event created during development of v1."
        )
        print(f"Successfully created event: {created_event.get('htmlLink')}")
        
    except Exception as e:
        print(f"Error occurred during testing: {e}")
