import os
import sys
import asyncio
# Reconfigure stdout/stderr to support unicode/emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import dotenv
from telegram import Update
from telegram.error import TimedOut, NetworkError
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from assistant_agent import run_agent_turn

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

TELEGRAM_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
if not TELEGRAM_ALLOWED_CHAT_ID:
    raise ValueError("TELEGRAM_ALLOWED_CHAT_ID is missing in the environment or .env file.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return
    
    welcome_text = (
        f"Hi {update.effective_user.first_name}! I am Ziri, your Personal Assistant Agent.\n\n"
        "I have access to your Google Calendar and Google Classroom. You can ask me:\n"
        "• 'What do I have scheduled for today?'\n"
        "• 'Add dentist at 3pm tomorrow'\n"
        "• 'What assignments do I have for my ML class?'\n\n"
        "How can I help you today?"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pass user messages to the ADK agent and reply with the agent's response."""
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        return

    user_message = update.message.text
    if not user_message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Show typing status (safely ignore if it times out)
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception as e:
        print(f"Warning: Failed to send typing indicator: {e}")
    
    # Run the agent turn (using to_thread because run_agent_turn is blocking/sync)
    response_text = await asyncio.to_thread(
        run_agent_turn,
        user_message=user_message,
        session_id=chat_id,
        user_id=user_id
    )
    
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

def main() -> None:
    """Start the Telegram bot with increased HTTP timeouts."""
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
        .build()
    )
    
    # Register error handler
    app.add_error_handler(error_handler)
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is polling. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
