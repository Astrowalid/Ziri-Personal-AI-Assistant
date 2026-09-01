import os
import sys
import asyncio
# Reconfigure stdout/stderr to support unicode/emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from datetime import datetime, timedelta, time
import dotenv
from telegram import Bot
from assistant_agent import run_agent_turn
from classroom_tool import list_classroom_assignments
from storage import get_tasks_for_date_range, reconcile_classroom_tasks

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

async def main() -> None:
    # 1. Retrieve the allowed chat ID from environment variables
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
    if not chat_id:
        print("Error: TELEGRAM_ALLOWED_CHAT_ID is missing in the environment or .env file.")
        sys.exit(1)
        
    print(f"Triggering daily check-in for Chat ID: {chat_id}...")
    
    # 2. Gather memory context: yesterday's tasks & Classroom discrepancies
    now = datetime.now().astimezone()
    yesterday = now - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday.date(), time.min).astimezone().isoformat()
    yesterday_end = datetime.combine(yesterday.date(), time.max).astimezone().isoformat()
    
    # Query yesterday's tasks from SQLite
    yesterday_tasks = get_tasks_for_date_range(yesterday_start, yesterday_end)
    yesterday_pending = [t for t in yesterday_tasks if t.get('status') != 'DONE']
    
    yesterday_summary_lines = []
    if yesterday_pending:
        for t in yesterday_pending:
            yesterday_summary_lines.append(f"- {t['title']} (Status: {t['status']}, Source: {t['source_type']})")
        yesterday_context = "\n".join(yesterday_summary_lines)
    else:
        yesterday_context = "None (all recorded tasks completed or none scheduled)."

    # 3. Check for Classroom discrepancies
    try:
        live_assignments = await asyncio.to_thread(list_classroom_assignments)
        discrepancies = reconcile_classroom_tasks(live_assignments)
    except Exception as e:
        print(f"Warning: could not fetch/reconcile Classroom assignments: {e}")
        discrepancies = []

    discrepancy_lines = []
    if discrepancies:
        for d in discrepancies:
            discrepancy_lines.append(f"- {d['warning_message']}")
        discrepancy_context = "\n".join(discrepancy_lines)
    else:
        discrepancy_context = "None"

    # 4. Ask the ADK Agent to generate the daily check-in with memory context
    prompt = (
        "Generate a friendly daily check-in message incorporating memory of yesterday's tasks and today's schedule.\n\n"
        "Here is the background memory context:\n"
        f"Yesterday's Pending Tasks:\n{yesterday_context}\n\n"
        f"Classroom Discrepancy Warnings:\n{discrepancy_context}\n\n"
        "Format the check-in clearly with these sections:\n\n"
        "1. 🔄 Yesterday's Follow-Up:\n"
        "If there were pending tasks yesterday, ask a friendly follow-up question asking if the user got to finish them. "
        "If there were no pending tasks, briefly acknowledge that yesterday was all clear.\n\n"
        "2. ⚠️ Alerts & Discrepancies (Include ONLY if there are discrepancy warnings above):\n"
        "List any discrepancy warnings clearly so the user knows they need to submit on Classroom.\n\n"
        "3. 📅 Calendar:\n"
        "[List all of today's calendar events in detail, or state if there are none scheduled]\n\n"
        "4. 📚 Classroom:\n"
        "[List the pending coursework assignments due soon from Classroom using format: - [Course]: [Assignment] — due [date] — [Submitted / Not submitted]. If none, write 'No pending assignments']\n\n"
        "Keep each section visually separated with blank lines. End by asking the user for their top priorities today."
    )

    checkin_text = await asyncio.to_thread(
        run_agent_turn,
        user_message=prompt,
        session_id="daily_checkin_session",
        user_id="daily_checkin_trigger"
    )
    
    if not checkin_text.strip():
        checkin_text = "Good morning! Here is a quick check-in. I couldn't retrieve your schedule today."
        
    # 5. Send the message via Telegram Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    print("Sending check-in message to Telegram...")
    await bot.send_message(chat_id=chat_id, text=checkin_text)
    print("Daily check-in sent successfully!")

if __name__ == '__main__':
    asyncio.run(main())
