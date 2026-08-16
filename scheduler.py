import time
import datetime
import subprocess
import sys

# Fixed time to run the daily check-in (24-hour format local time)
CHECKIN_TIME_STR = "18:46"

def run_checkin() -> None:
    """Runs daily_checkin.py script using the same Python interpreter."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] Triggering daily check-in script...")
    try:
        # Run daily_checkin.py using the current python executable
        subprocess.run([sys.executable, "daily_checkin.py"], check=True)
        print(f"[{now_str}] Daily check-in run completed successfully.")
    except Exception as e:
        print(f"[{now_str}] Error running daily check-in: {e}")

def main() -> None:
    print(f"Starting local scheduler. Daily check-in set for {CHECKIN_TIME_STR} local time.")
    target_hour, target_minute = map(int, CHECKIN_TIME_STR.split(":"))
    
    while True:
        now = datetime.datetime.now()
        # Define target time today
        target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        # If target time has already passed today, set target to tomorrow
        if now >= target_today:
            target_time = target_today + datetime.timedelta(days=1)
        else:
            target_time = target_today
            
        time_to_wait = (target_time - now).total_seconds()
        next_run_str = target_time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"Next check-in scheduled at: {next_run_str}. Sleeping for {time_to_wait:.1f} seconds...")
        
        # Sleep in small 10-second steps to keep the process responsive to termination (Ctrl+C)
        slept = 0.0
        while slept < time_to_wait:
            time.sleep(min(10.0, time_to_wait - slept))
            slept += 10.0
            
        run_checkin()

if __name__ == '__main__':
    main()
