import os
import dotenv
from datetime import datetime
from google.genai import types
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from calendar_tool import list_events, create_calendar_event, find_calendar_events, update_calendar_event
from classroom_tool import list_classroom_assignments
from storage import find_tasks, update_task_status

# Load environment variables
dotenv.load_dotenv()

def get_current_time():
    """Returns the current date and time in the user's local timezone.
    Use this to determine what 'today', 'tomorrow', 'this week', 'next Monday', or other relative/absolute times mean.
    """
    now = datetime.now().astimezone()
    return {
        "current_time_iso": now.isoformat(),
        "weekday": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d")
    }

# Define the agent
assistant_agent = Agent(
    name="Ziri",
    model="gemini-3.5-flash-lite",
    instruction=(
        "You are Ziri, the user's personal assistant. You have access to the user's Google Calendar and Google Classroom.\n\n"
        "Google Calendar Operations:\n"
        "1. You can list calendar events for any timeframe (e.g. today, this week, a particular month), find/search for events, create new events, and update existing events.\n"
        "2. To resolve relative or custom timeframes (both past and future, such as 'yesterday', 'last week', 'today', 'this week', 'next Monday', 'September', etc.) for either Calendar or Classroom queries, "
        "you MUST first call `get_current_time` to check the current date and time. Use this information to calculate the necessary ISO timestamps for calendar event listings or to filter the dates of classroom assignments.\n"
        "3. Create Event: Convert the requested event time into an exact ISO format string (including the timezone offset) and call `create_calendar_event`.\n"
        "4. Update/Edit Event: If the user asks you to edit, change, reschedule, or add descriptions/notes to an existing event on Google Calendar, "
        "you must first use `find_calendar_events` with a search query to locate the event and get its ID. "
        "Once you have the event ID, use `update_calendar_event` to apply the changes. Always use the exact ISO time format for updates.\n\n"
        "Google Classroom Operations:\n"
        "1. You can fetch/list assignments (coursework) and their submission status (whether the user has turned them in or not) from Google Classroom using `list_classroom_assignments`.\n"
        "2. When answering queries about assignments or coursework, you must explicitly identify the items as coming 'from Classroom' rather than listing them bare or mixing them with Calendar events.\n\n"
        "Task Execution & Status Tracking (Long-Term Memory):\n"
        "1. When the user reports progress on a task or event (e.g. 'I finished my study session', 'I am working on the math assignment', 'completed dentist appointment'):\n"
        "   - Call `find_tasks` with a keyword query to find the task in your local database.\n"
        "   - If multiple candidates match, ask the user to clarify which one they mean.\n"
        "   - If no candidate matches, inform the user you could not find the task in your records.\n"
        "   - Once the candidate is matched, call `update_task_status` with `source_type`, `source_id`, and the new status ('NOT_STARTED', 'IN_PROGRESS', or 'DONE').\n"
        "   - IMPORTANT: Updating task completion status is a local tracking operation in your database; NEVER edit or delete the event in Google Calendar when the user says they finished it.\n\n"
        "Always confirm back to the user with a friendly, concise message when you run operations."
    ),
    tools=[get_current_time, list_events, create_calendar_event, find_calendar_events, update_calendar_event, list_classroom_assignments, find_tasks, update_task_status]
)

# Initialize the Runner with InMemorySessionService
session_service = InMemorySessionService()
runner = Runner(
    agent=assistant_agent, 
    session_service=session_service, 
    app_name="Ziri",
    auto_create_session=True
)

def run_agent_turn(user_message: str, session_id: str = "default_session", user_id: str = "default_user") -> str:
    """Helper to run a single turn of the agent and return the final text response."""
    # Convert input string to types.Content
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)]
    )
    
    # Run the agent
    events = runner.run(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message
    )
    
    # Process agent events to find the final text response
    final_response = ""
    for event in events:
        if hasattr(event, 'content') and event.content:
            if hasattr(event.content, 'parts') and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        final_response += part.text
        
    print(f"Final Agent Response: {final_response}")
    return final_response

if __name__ == '__main__':
    print("Testing ADK Agent locally...")
    # Test 1: Ask about today's events
    print("\n--- Test 1: Asking about today's events ---")
    run_agent_turn("What do I have planned for today?")
    
    # Test 2: Add an event
    print("\n--- Test 2: Adding an event ---")
    run_agent_turn("schedule dentist at 3pm tomorrow")

    # Test 3: Ask about Classroom assignments
    print("\n--- Test 3: Asking about coursework ---")
    run_agent_turn("what assignments do I have for my ML course and did I submit them?")


