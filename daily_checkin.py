import os
import sys
import asyncio
import dotenv
from telegram import Bot
from assistant_agent import run_agent_turn

# Load environment variables
dotenv.load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the environment or .env file.")

async def main() -> None:
    # 1. Check if chat_id.txt exists
    if not os.path.exists("chat_id.txt"):
        print("Error: No chat ID saved. Please send a message or /start to the Telegram bot first.")
        sys.exit(1)
        
    with open("chat_id.txt", "r") as f:
        chat_id = f.read().strip()
        
    if not chat_id:
        print("Error: Saved chat ID is empty.")
        sys.exit(1)
        
    print(f"Triggering daily check-in for Chat ID: {chat_id}...")
    
    # 2. Ask the ADK Agent to generate the daily check-in
    prompt = (
        "Generate a friendly daily check-in message. "
        "List all of today's calendar events in detail (or state if there are none scheduled). "
        "Keep it concise and end by asking the user for their top priorities today."
    )
    
    checkin_text = await asyncio.to_thread(
        run_agent_turn,
        user_message=prompt,
        session_id="daily_checkin_session",
        user_id="daily_checkin_trigger"
    )
    
    if not checkin_text.strip():
        checkin_text = "Good morning! Here is a quick check-in. I couldn't retrieve your schedule today."
        
    # 3. Send the message via Telegram Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    print("Sending check-in message to Telegram...")
    await bot.send_message(chat_id=chat_id, text=checkin_text)
    print("Daily check-in sent successfully!")

if __name__ == '__main__':
    asyncio.run(main())
