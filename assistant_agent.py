import os
import dotenv
from datetime import datetime
from google.genai import types
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from calendar_tool import list_events, create_calendar_event, find_calendar_events, update_calendar_event
from classroom_tool import list_classroom_assignments
from storage import find_tasks, update_task_status, get_completed_tasks, get_tasks_for_date_range

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
        "Core Behavioral & Style Rules (CRITICAL):\n"
        "1. Token Efficiency & No Fluff: Be friendly, extremely concise, and direct. Do NOT use introductory filler (e.g., 'Sure, I can help with that!'), repetitive pleasantries, or repeat what the user asked. Keep messages short, crisp, and easy to read on mobile.\n"
        "2. Mandatory Confirmation for Add/Update (SAFETY FIRST):\n"
        "   - NEVER create or update a Google Calendar event without explicit confirmation from the user.\n"
        "   - Adding an Event: When the user asks to add or schedule an event, determine the title, date, start time, and duration. DO NOT call `create_calendar_event` yet. Ask the user for confirmation first (e.g., 'I can schedule **Team Meeting** for tomorrow, Sep 3 from 2:00 PM to 3:00 PM. Should I add it to your calendar?').\n"
        "   - ONLY call `create_calendar_event` once the user explicitly confirms (e.g., 'yes', 'confirm', 'go ahead', 'do it'). Confirm briefly once created.\n"
        "   - Updating an Event: When asked to edit or reschedule an existing event, use `find_calendar_events` to locate it, but DO NOT call `update_calendar_event` yet. Propose the change to the user and ask for confirmation. ONLY call `update_calendar_event` after they confirm.\n\n"
        "Google Calendar Operations:\n"
        "1. Listing Events: You can list calendar events for any timeframe. Provide clean, compact bullet points with time and event title. The current local date, time, weekday, and timezone are in [System Context]—use it directly.\n\n"
        "Google Classroom Operations:\n"
        "1. Fetch coursework using `list_classroom_assignments`. Keep the list concise: course name, assignment title, due date, and submission status.\n\n"
        "Task Execution & Status Tracking (Long-Term Memory):\n"
        "1. When the user reports completing a task or event (e.g., 'finished study session', 'completed dentist appointment'):\n"
        "   - Call `find_tasks` with a keyword query to locate the task in your local database.\n"
        "   - Once matched, call `update_task_status` with `source_type`, `source_id`, and status ('DONE', 'IN_PROGRESS', or 'NOT_STARTED').\n"
        "   - Confirm briefly: 'Marked **[Task]** as completed! ✅'\n"
        "   - Updating task completion status is a local database tracking operation; NEVER edit or delete the event in Google Calendar.\n"
        "2. Historical queries: Use [System Context] for dates and call `get_completed_tasks`. Summarize in brief bullet points."
    ),
    tools=[
        get_current_time,
        list_events,
        create_calendar_event,
        find_calendar_events,
        update_calendar_event,
        list_classroom_assignments,
        find_tasks,
        update_task_status,
        get_completed_tasks,
        get_tasks_for_date_range
    ]
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
    now = datetime.now().astimezone()
    time_prefix = (
        f"[System Context: Current local time is {now.strftime('%A, %Y-%m-%d %H:%M:%S %Z')} "
        f"(ISO: {now.isoformat()}). Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}].]\n\n"
    )
    enriched_message = time_prefix + user_message

    # Convert input string to types.Content
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=enriched_message)]
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


