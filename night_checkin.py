import os
import sys
import asyncio
from datetime import datetime, timedelta, time
import dotenv
from telegram import Bot
from assistant_agent import run_agent_turn
from storage import get_tasks_for_date_range, find_tasks

# Reconfigure stdout/stderr to support unicode/emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

async def perform_night_checkin(bot: Bot = None, chat_id: str = None) -> None:
    """Performs the evening accountability check-in ('un suivi') using ground truth from assistant.db."""
    # 1. Retrieve the allowed chat ID from environment variables if not provided
    if not chat_id:
        chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
    if not chat_id:
        print("Error: TELEGRAM_ALLOWED_CHAT_ID is missing in the environment or .env file.")
        return

    print(f"Triggering night check-in ('suivi') from database for Chat ID: {chat_id}...")

    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now(local_tz)
    start_of_today = datetime.combine(now.date(), time.min).replace(tzinfo=local_tz).isoformat()
    end_of_today = datetime.combine(now.date(), time.max).replace(tzinfo=local_tz).isoformat()

    # 2. Query today's scheduled items and events directly from SQLite (assistant.db)
    today_items = get_tasks_for_date_range(start_of_today, end_of_today)
    
    # Split into calendar events and tasks
    today_events = [item for item in today_items if item.get('source_type') == 'calendar']
    today_tasks = [item for item in today_items if item.get('source_type') != 'calendar']
    
    # Also fetch any general pending tasks not yet marked DONE
    pending_tasks = find_tasks(query="", status_filter="NOT_STARTED", limit=10)
    in_progress_tasks = find_tasks(query="", status_filter="IN_PROGRESS", limit=5)

    # Format today's events
    if today_events:
        event_lines = []
        for ev in today_events:
            status_tag = "✅ Done" if ev['status'] == 'DONE' else f"⏳ {ev['status']}"
            time_str = ""
            if ev.get('item_date') and "T" in ev['item_date']:
                try:
                    time_str = datetime.fromisoformat(ev['item_date']).strftime("%I:%M %p")
                except Exception:
                    time_str = ev['item_date']
            time_prefix = f"[{time_str}] " if time_str else ""
            event_lines.append(f"- {time_prefix}{ev['title']} ({status_tag})")
        events_context = "\n".join(event_lines)
    else:
        events_context = "No specific calendar events were scheduled for today in the database."

    # Format today's tasks & pending items
    task_lines = []
    if today_tasks:
        for t in today_tasks:
            icon = "✅" if t['status'] == 'DONE' else ("⏳" if t['status'] == 'IN_PROGRESS' else "⬜")
            task_lines.append(f"{icon} {t['title']} (Status: {t['status']}, Source: {t['source_type']})")
    
    # Append any in-progress or pending tasks from Classroom / general list if not already shown
    seen_ids = {t['source_id'] for t in today_tasks}
    for pt in in_progress_tasks + pending_tasks:
        if pt['source_id'] not in seen_ids and len(task_lines) < 8:
            icon = "⏳" if pt['status'] == 'IN_PROGRESS' else "⬜"
            task_lines.append(f"{icon} {pt['title']} (Status: {pt['status']}, Source: {pt['source_type']})")
            seen_ids.add(pt['source_id'])

    tasks_context = "\n".join(task_lines) if task_lines else "No pending tasks recorded in database."

    # 3. Ask ADK Agent to formulate the executive evening accountability follow-up
    prompt = (
        "You are Ziri, acting as a personal, dedicated human executive assistant conducting an evening accountability check-in.\n"
        "Important: Respond in English.\n\n"
        "Here is the ground truth from your database for what was planned for today:\n\n"
        f"--- Today's Calendar Events & Meetings ---\n{events_context}\n\n"
        f"--- Tasks & Assignments Status ---\n{tasks_context}\n\n"
        "Please compose a warm, empathetic, highly human, and structured evening follow-up message in English with the following sections:\n\n"
        "1. 🌙 Evening Greeting:\n"
        "Warm greeting acknowledging the end of the day.\n\n"
        "2. 🎯 Today's Accountability Check:\n"
        "Directly and personally ask how the specific meetings and events from today went (for example, specifically asking how a scheduled meeting went or whether an assignment was tackled). Do not just list them—ask about them as a caring assistant following up on what was planned.\n\n"
        "3. 📝 Update & Status:\n"
        "Invite the user to reply and tell you what they finished (so you can mark it as DONE in their database), or if any unfinished task needs to be moved/rescheduled.\n\n"
        "4. 🌅 Looking Ahead to Tomorrow:\n"
        "Briefly ask if there is anything they want to prepare, schedule, or take note of for tomorrow before signing off for the night.\n\n"
        "Keep each section visually distinct with blank lines and emojis. Tone should be empathetic, supportive, professional, and clear. Language must be English."
    )

    checkin_text = await asyncio.to_thread(
        run_agent_turn,
        user_message=prompt,
        session_id="night_checkin_session",
        user_id="night_checkin_trigger"
    )

    if not checkin_text.strip():
        checkin_text = "Good evening! I hope you had a productive day. How did your tasks and meetings go today? Let me know if you'd like to update any statuses or plan for tomorrow!"

    # 4. Deliver via Telegram
    if bot is None:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

    print("Sending night check-in message to Telegram...")
    if len(checkin_text) > 4000:
        for i in range(0, len(checkin_text), 4000):
            await bot.send_message(chat_id=chat_id, text=checkin_text[i:i+4000])
    else:
        await bot.send_message(chat_id=chat_id, text=checkin_text)
    print("Night check-in sent successfully!")

async def main() -> None:
    await perform_night_checkin()

if __name__ == '__main__':
    asyncio.run(main())
