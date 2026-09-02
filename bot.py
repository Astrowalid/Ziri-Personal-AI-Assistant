import os
import sys
import asyncio
# Reconfigure stdout/stderr to support unicode/emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import random
from typing import Optional
from datetime import datetime, timedelta
import dotenv
from telegram import Update
from telegram.error import TimedOut, NetworkError
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from assistant_agent import run_agent_turn
import scheduler
from daily_checkin import perform_daily_checkin
from night_checkin import perform_night_checkin

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

TELEGRAM_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
if not TELEGRAM_ALLOWED_CHAT_ID:
    raise ValueError("TELEGRAM_ALLOWED_CHAT_ID is missing in the environment or .env file.")

# Determine scheduled check-in times (.env takes precedence, otherwise defaults)
CHECKIN_TIME_STR = os.getenv("CHECKIN_TIME", getattr(scheduler, "CHECKIN_TIME_STR", "09:00"))
CHECKIN_TIME_NIGHT_STR = os.getenv("CHECKIN_TIME_NIGHT", "21:00")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return
    
    welcome_text = (
        f"Hi {update.effective_user.first_name}! I am Ziri, your Personal Assistant Agent.\n\n"
        "I have access to your Google Calendar and Google Classroom.\n"
        "How can I help you today?"
    )
    await update.message.reply_text(welcome_text)

async def keep_typing(bot, chat_id: str, stop_event: asyncio.Event) -> None:
    """Continuously sends 'typing' chat action every 4 seconds until stop_event is set."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass

async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows manual on-demand trigger of the daily check-in briefing."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return
    
    await update.message.reply_text("⏳ Generating your daily check-in...")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
    try:
        await perform_daily_checkin(bot=context.bot, chat_id=chat_id)
    except Exception as e:
        print(f"Error during manual /checkin: {e}")
        await update.message.reply_text(f"❌ Error generating check-in: {e}")
    finally:
        stop_typing.set()
        await typing_task

async def night_checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows manual on-demand trigger of the evening follow-up."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return
    
    await update.message.reply_text("🌙 Preparing your evening follow-up...")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
    try:
        await perform_night_checkin(bot=context.bot, chat_id=chat_id)
    except Exception as e:
        print(f"Error during manual /nightcheckin: {e}")
        await update.message.reply_text(f"❌ Error generating evening follow-up: {e}")
    finally:
        stop_typing.set()
        await typing_task

CALENDAR_WAIT_PHRASES = [
    "Let me check your calendar for you... 📅",
    "Checking your schedule, give me a sec! ⏳",
    "Looking at your calendar right now... 🔍",
    "Pulling up your schedule, one moment! ⏱️",
    "Let me see what you have on your calendar... 🗓️",
    "Just a sec, checking your calendar! ⏳",
]

CLASSROOM_WAIT_PHRASES = [
    "I'm gonna check that for you on Classroom... 📚",
    "Checking your assignments on Classroom, one sec! ⏳",
    "Looking up your coursework right now... 🔍",
    "Pulling up your Classroom tasks, hang tight! ⏱️",
    "Let me check Google Classroom for you... 📝",
    "Just a sec, checking what's due on Classroom! ⏳",
]

BOTH_WAIT_PHRASES = [
    "Let me check both your calendar and Classroom for you... 🔍",
    "Checking your schedule and assignments, give me a sec! ⏳",
    "Looking into your calendar and coursework, one moment! ⏱️",
]

def get_wait_notice_phrase(message: str) -> Optional[str]:
    """Returns a randomized contextual phrase only if checking Calendar or Classroom; otherwise None."""
    msg = message.lower()
    
    # Check for Classroom-related check queries
    classroom_keywords = [
        "classroom", "assignment", "assignments", "homework", "course", 
        "courses", "coursework", "devoir", "tp", "td", "due", 
        "submission", "submitted", "turn in", "turned in"
    ]
    is_classroom = any(kw in msg for kw in classroom_keywords)
    
    # Check for Calendar/schedule check queries
    calendar_keywords = [
        "calendar", "schedule", "scheduled", "event", "events", 
        "meeting", "meetings", "agenda", "appointment", "appointments", 
        "free", "busy"
    ]
    time_queries = ["today", "tomorrow", "this week", "next week", "planned", "plan"]
    is_calendar = any(kw in msg for kw in calendar_keywords) or (
        any(t in msg for t in time_queries) and any(q in msg for q in ["what", "have", "do i have", "show", "check", "see", "any"])
    )
    
    if is_classroom and is_calendar:
        return random.choice(BOTH_WAIT_PHRASES)
    elif is_classroom:
        return random.choice(CLASSROOM_WAIT_PHRASES)
    elif is_calendar:
        return random.choice(CALENDAR_WAIT_PHRASES)
        
    return None

async def delayed_wait_notice(update: Update, user_message: str, stop_event: asyncio.Event, delay: float = 2.0) -> None:
    """Sends a contextual acknowledgement if processing takes longer than delay seconds and query checks calendar/classroom."""
    phrase = get_wait_notice_phrase(user_message)
    if not phrase:
        return

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        if not stop_event.is_set() and update.message:
            try:
                await update.message.reply_text(phrase)
            except Exception as e:
                print(f"Notice delivery warning: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pass user messages to the ADK agent and reply with the agent's response."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return

    user_message = update.message.text
    if not user_message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Maintain continuous typing status and schedule wait notice if turn takes > 2.0s
    stop_processing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_processing))
    notice_task = asyncio.create_task(delayed_wait_notice(update, user_message, stop_processing, delay=2.0))
    
    try:
        # Run the agent turn (using to_thread because run_agent_turn is blocking/sync)
        response_text = await asyncio.to_thread(
            run_agent_turn,
            user_message=user_message,
            session_id=chat_id,
            user_id=user_id
        )
    finally:
        stop_processing.set()
        await typing_task
        await notice_task
    
    # If the response is empty for some reason, provide a fallback
    if not response_text.strip():
        response_text = "I'm sorry, I encountered an issue processing that request."
        
    # Send the response back to the user with retry on transient network timeouts
    for attempt in range(1, 4):
        try:
            await update.message.reply_text(response_text)
            break
        except (TimedOut, NetworkError) as e:
            print(f"Network timeout sending reply (attempt {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(2)
            else:
                print("Failed to deliver message after 3 attempts.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    print(f"Telegram error encountered: {context.error}")

async def daily_checkin_scheduler_task(application) -> None:
    """Background worker running inside bot.py to trigger daily check-in at CHECKIN_TIME_STR."""
    try:
        target_hour, target_minute = map(int, CHECKIN_TIME_STR.strip().split(":"))
    except Exception as e:
        print(f"[Scheduler] Invalid CHECKIN_TIME_STR '{CHECKIN_TIME_STR}': {e}. Defaulting to 09:00.")
        target_hour, target_minute = 9, 0

    print(f"[Scheduler] Morning check-in scheduler started (set for {target_hour:02d}:{target_minute:02d} local time).")
    
    while True:
        now = datetime.now()
        target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        if now >= target_today:
            target_time = target_today + timedelta(days=1)
        else:
            target_time = target_today
            
        wait_seconds = (target_time - now).total_seconds()
        print(f"[Scheduler-Morning] Next check-in scheduled for: {target_time.strftime('%Y-%m-%d %H:%M:%S')} (waiting {wait_seconds:.0f}s).")
        
        await asyncio.sleep(wait_seconds)
        
        print(f"[Scheduler-Morning] Triggering scheduled morning check-in for chat_id={TELEGRAM_ALLOWED_CHAT_ID}...")
        try:
            await perform_daily_checkin(bot=application.bot, chat_id=TELEGRAM_ALLOWED_CHAT_ID)
            print("[Scheduler-Morning] Morning check-in sent successfully.")
        except Exception as e:
            print(f"[Scheduler-Morning] Error running morning check-in: {e}")
            
        # Prevent double firing in the same minute
        await asyncio.sleep(60)

async def night_checkin_scheduler_task(application) -> None:
    """Background worker running inside bot.py to trigger evening follow-up at CHECKIN_TIME_NIGHT_STR."""
    try:
        target_hour, target_minute = map(int, CHECKIN_TIME_NIGHT_STR.strip().split(":"))
    except Exception as e:
        print(f"[Scheduler] Invalid CHECKIN_TIME_NIGHT_STR '{CHECKIN_TIME_NIGHT_STR}': {e}. Defaulting to 21:00.")
        target_hour, target_minute = 21, 0

    print(f"[Scheduler] Evening follow-up scheduler started (set for {target_hour:02d}:{target_minute:02d} local time).")
    
    while True:
        now = datetime.now()
        target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        if now >= target_today:
            target_time = target_today + timedelta(days=1)
        else:
            target_time = target_today
            
        wait_seconds = (target_time - now).total_seconds()
        print(f"[Scheduler-Evening] Next follow-up scheduled for: {target_time.strftime('%Y-%m-%d %H:%M:%S')} (waiting {wait_seconds:.0f}s).")
        
        await asyncio.sleep(wait_seconds)
        
        print(f"[Scheduler-Evening] Triggering scheduled evening follow-up for chat_id={TELEGRAM_ALLOWED_CHAT_ID}...")
        try:
            await perform_night_checkin(bot=application.bot, chat_id=TELEGRAM_ALLOWED_CHAT_ID)
            print("[Scheduler-Evening] Evening follow-up sent successfully.")
        except Exception as e:
            print(f"[Scheduler-Evening] Error running evening follow-up: {e}")
            
        # Prevent double firing in the same minute
        await asyncio.sleep(60)

async def post_init(application) -> None:
    """Hook executed once the bot application is initialized and event loop is running."""
    asyncio.create_task(daily_checkin_scheduler_task(application))
    asyncio.create_task(night_checkin_scheduler_task(application))

def main() -> None:
    """Start the Telegram bot with increased HTTP timeouts and integrated schedulers."""
    print("Starting Telegram bot...")
    
    # Configure longer HTTP connection and read timeouts to prevent ConnectTimeout drops
    request_config = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )
    
    # Register error handler
    app.add_error_handler(error_handler)
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("checkin", checkin_command))
    app.add_handler(CommandHandler("nightcheckin", night_checkin_command))
    app.add_handler(CommandHandler("followup", night_checkin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is polling. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
