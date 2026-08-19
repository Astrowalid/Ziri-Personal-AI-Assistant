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
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from assistant_agent import run_agent_turn

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

def save_chat_id(chat_id: str) -> None:
    """Saves the user's chat ID to a file so the daily check-in scheduler can read it."""
    with open("chat_id.txt", "w") as f:
        f.write(chat_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    chat_id = str(update.effective_chat.id)
    save_chat_id(chat_id)
    
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
    user_message = update.message.text
    if not user_message:
        return
        
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)
    
    # Save the chat ID for scheduled triggers
    save_chat_id(chat_id)
    
    # Show typing status to let the user know the bot is thinking
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
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
        
    # Send the response back to the user
    await update.message.reply_text(response_text)

def main() -> None:
    """Start the Telegram bot."""
    print("Starting Telegram bot...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is polling. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
