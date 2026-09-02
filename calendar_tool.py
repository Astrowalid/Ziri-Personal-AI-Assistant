import os
import pickle
from datetime import datetime, time, timedelta
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
    
    return events_result.get('items', [])

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
