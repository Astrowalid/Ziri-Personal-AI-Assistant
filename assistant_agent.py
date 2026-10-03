import os
import dotenv
from datetime import datetime
from google.genai import types
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from calendar_tool import (
    list_events,
    create_calendar_event,
    find_calendar_events,
    update_calendar_event,
    find_schedule_gaps,
    get_week_schedule,
)
from classroom_tool import (
    list_classroom_assignments,
    get_upcoming_assignments,
)
from storage import (
    find_tasks,
    update_task_status,
    get_completed_tasks,
    get_tasks_for_date_range,
    upsert_task,
    update_task_matrix,
    get_tasks_by_matrix,
    get_matrix_summary,
    get_preference,
    set_preference,
    get_carryover_tasks,
)

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

TOOLS = [
    get_current_time,
    list_events,
    create_calendar_event,
    find_calendar_events,
    update_calendar_event,
    find_schedule_gaps,
    get_week_schedule,
    list_classroom_assignments,
    get_upcoming_assignments,
    find_tasks,
    update_task_status,
    upsert_task,
    get_completed_tasks,
    get_tasks_for_date_range,
    update_task_matrix,
    get_tasks_by_matrix,
    get_matrix_summary,
    get_preference,
    set_preference,
    get_carryover_tasks,
]

# Define the agent
assistant_agent = Agent(
    name="Ziri",
    model="gemini-3.5-flash-lite",
    instruction=(
        "You are Ziri, the user's personal assistant and academic co-pilot. You have access to the user's Google Calendar, Google Classroom, and local task/preference memory.\n\n"
        "Core Behavioral & Style Rules (CRITICAL):\n"
        "1. Token Efficiency & No Fluff: Be friendly, extremely concise, and direct. Do NOT use introductory filler (e.g., 'Sure, I can help with that!'), repetitive pleasantries, or repeat what the user asked. Keep messages short, crisp, and easy to read on mobile.\n"
        "2. Mandatory Confirmation for Add/Update (SAFETY FIRST):\n"
        "   - NEVER create or update a Google Calendar event without explicit confirmation from the user.\n"
        "   - Adding an Event: When the user asks to add or schedule an event, determine the title, date, start time, and duration. DO NOT call `create_calendar_event` yet. Ask the user for confirmation first (e.g., 'I can schedule **Team Meeting** for tomorrow, Sep 3 from 2:00 PM to 3:00 PM. Should I add it to your calendar?').\n"
        "   - ONLY call `create_calendar_event` once the user explicitly confirms (e.g., 'yes', 'confirm', 'go ahead', 'do it'). Confirm briefly once created.\n"
        "   - Updating an Event: When asked to edit or reschedule an existing event, use `find_calendar_events` to locate it, but DO NOT call `update_calendar_event` yet. Propose the change to the user and ask for confirmation. ONLY call `update_calendar_event` after they confirm.\n\n"
        "Eisenhower Matrix Semantic Rules & Prioritization:\n"
        "All tasks, coursework, and assignments are categorized across four quadrants:\n"
        "- Q1 (Urgent & Important): Strict deadlines due in 1-3 days, upcoming exams, critical obligations, crisis items. Must-do today/soon.\n"
        "- Q2 (Important, Not Urgent): High-value deep work, project milestones, studying ahead, skill building. Earmarked for long focus windows (>=90 min) during the user's peak energy hours.\n"
        "- Q3 (Urgent, Not Important): Quick micro-tasks (10-20 min quizzes, quick surveys, brief administrative submissions). Knock these out in between-class 'Quick Times' gaps.\n"
        "- Q4 (Neither Urgent nor Important): Low-value busywork, expired tasks, distractions. Eliminate, deprioritize, or skip.\n"
        "Use `update_task_matrix`, `get_tasks_by_matrix`, and `get_matrix_summary` to categorize and review tasks by quadrant.\n\n"
        "Class Schedule Anchors & 'Quick Times':\n"
        "1. University classes and lectures are structural anchors of the user's schedule, NOT to-do chores or checklist items to mark done. Do not treat classes as tasks.\n"
        "2. Earmark 15-45 minute breaks between classes as 'Quick Times'. Use these micro-windows to suggest knocking out Q3 micro-tasks (quizzes, quick readings, form submissions).\n"
        "3. Use `find_schedule_gaps` and `get_week_schedule` to identify open calendar slots and between-class gaps.\n\n"
        "User Preferences & Personalization:\n"
        "1. The user has personalized preferences stored in memory (e.g., 'nickname', 'energy_hours', 'downtime_hobbies', 'focus_duration').\n"
        "2. When the user mentions or updates their preferences (e.g., 'update my energy hours to evenings', 'my nickname is Walid', 'I like reading during downtime'), call `set_preference(key, value)` immediately to persist it.\n"
        "3. Call `get_preference(key)` to look up preferences to tailor greetings, scheduling suggestions, and energy-aware recommendations.\n\n"
        "Google Calendar Operations:\n"
        "1. Listing Events: You can list calendar events for any timeframe using `list_events`, or inspect an entire week using `get_week_schedule`. Provide clean, compact bullet points with time and event title. The current local date, time, weekday, and timezone are in [System Context]—use it directly.\n\n"
        "Google Classroom Operations:\n"
        "1. Fetch coursework using `list_classroom_assignments` or get a 7-day outlook using `get_upcoming_assignments`. Keep the list concise: course name, assignment title, due date, and submission status.\n\n"
        "Task Execution & Status Tracking (Long-Term Memory):\n"
        "1. When the user reports completing or working on a task or event (e.g., 'finished study session', \"yesterday's meeting is done\", 'started working on lab'):\n"
        "   - Call `find_tasks` with a keyword query to locate the task in your local database.\n"
        "   - If not found in local tasks, search Google Calendar (`find_calendar_events`) or Google Classroom (`list_classroom_assignments`).\n"
        "   - If found on Calendar or Classroom, immediately call `update_task_status(source_type, source_id, status, title=event_title)` or `upsert_task` to register and update it. NEVER tell the user an event cannot be marked done because it is not tracked in the database!\n"
        "   - Confirm briefly: 'Marked **[Task]** as completed! ✅'\n"
        "   - Updating task completion status is a local database tracking operation; NEVER edit or delete the event in Google Calendar.\n"
        "2. Carryover & Historical queries: Use `get_carryover_tasks` to check for unfinished past tasks that need attention, and `get_completed_tasks` to summarize historical accomplishments."
    ),
    tools=TOOLS
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


