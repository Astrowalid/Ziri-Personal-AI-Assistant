"""
sunday_planner.py - Strategic Weekly Sunday Planning Engine (v4)

Synthesizes Google Calendar timetables, Google Classroom coursework deadlines,
and SQLite execution memory into an Eisenhower Matrix weekly battle plan.

Operates in 3 collaborative phases:
1. Clarify: Highlights workload bottlenecks and asks targeted calibration questions.
2. Propose: Categorizes tasks into Q1-Q4 and maps focus blocks into free calendar gaps.
3. Commit: Books confirmed focus blocks to Google Calendar and updates SQLite task priorities.
"""

import os
import sys
import json
import re
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple

# Reconfigure stdout/stderr for unicode/emojis on Windows if available
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import dotenv
dotenv.load_dotenv()

from storage import (
    DB_PATH,
    get_preference,
    set_preference,
    list_preferences,
    get_carryover_tasks,
    update_task_status,
    update_task_matrix,
    upsert_task,
    get_task,
    VALID_MATRIX_QUADRANTS
)
from calendar_tool import (
    get_week_schedule,
    find_schedule_gaps,
    create_calendar_event
)
from classroom_tool import get_upcoming_assignments


# ---------------------------------------------------------------------------
# Section 1: One-Time Onboarding Check & Handlers
# ---------------------------------------------------------------------------

def is_onboarding_needed(db_path: Optional[str] = None) -> bool:
    """Checks if core preferences exist in user_preferences table.
    
    Verifies that preferred_name (or user_name) and energy_hours are set.
    Returns True if missing, prompting the one-time onboarding dialogue.
    
    Args:
        db_path: Optional path to SQLite database. Defaults to DB_PATH.
        
    Returns:
        bool: True if onboarding preferences are missing, False otherwise.
    """
    target_db = db_path or DB_PATH
    name = get_preference("preferred_name", db_path=target_db) or get_preference("user_name", db_path=target_db)
    energy_hours = get_preference("energy_hours", db_path=target_db)
    
    # Missing if either name or energy_hours is absent or empty
    if not name or not energy_hours:
        return True
    return False


def get_onboarding_prompt() -> str:
    """Returns a friendly, concise 1-minute onboarding message asking for core preferences.
    
    Prompts for:
    1. Preferred name
    2. Peak energy hours for deep focus (e.g., morning 9-12, afternoon 14-17, evening 18-21)
    3. Preferred focus session length (e.g., 60 min, 90 min)
    4. Regular hobbies / downtime activities (e.g., Gym on Mon/Wed)
    """
    return (
        "👋 Welcome! Before we dive into your weekly planning, let's do a quick 1-minute setup "
        "so I can schedule study blocks that match your natural rhythm:\n\n"
        "1. **Preferred Name:** What should I call you?\n"
        "2. **Peak Energy Hours:** When is your mind sharpest for deep focus? "
        "(e.g., Morning 09:00–12:00, Afternoon 14:00–17:00, or Evening 18:00–21:00)\n"
        "3. **Focus Session Length:** How long is your ideal deep work block? (e.g., 60 min or 90 min)\n"
        "4. **Regular Hobbies / Downtime:** Any recurring activities we must protect? (e.g., Gym Mon/Wed 18:00, Football Sunday)\n\n"
        "Reply with your answers in one message, and we'll be ready to plan!"
    )


def save_onboarding_preferences(
    name: str,
    energy_hours: str,
    focus_length: int = 90,
    hobbies: Optional[List[str]] = None,
    db_path: Optional[str] = None
) -> None:
    """Saves core onboarding preferences into the user_preferences table.
    
    Args:
        name: User's preferred name.
        energy_hours: String description or range (e.g., "09:00-12:00" or "Morning 9-12").
        focus_length: Preferred deep work block length in minutes (default 90).
        hobbies: List of downtime activities/hobbies to protect.
        db_path: Optional path to SQLite database.
    """
    target_db = db_path or DB_PATH
    clean_name = str(name).strip()
    clean_energy = str(energy_hours).strip()
    clean_hobbies = hobbies if hobbies is not None else []

    set_preference("preferred_name", clean_name, db_path=target_db)
    set_preference("user_name", clean_name, db_path=target_db)
    set_preference("energy_hours", clean_energy, db_path=target_db)
    set_preference("focus_length", int(focus_length), db_path=target_db)
    set_preference("max_focus_duration", int(focus_length), db_path=target_db)
    set_preference("hobbies", clean_hobbies, db_path=target_db)


# ---------------------------------------------------------------------------
# Section 2: 7-Day Context Synthesizer
# ---------------------------------------------------------------------------

def _find_bottlenecks(week_schedule: Dict[str, Any], upcoming_assignments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identifies days with heavy class loads or colliding assignment deadlines."""
    bottlenecks = []
    days_dict = week_schedule.get("days", {})

    for date_str, events in days_dict.items():
        # Count assignments due on this date
        due_on_day = [
            a for a in upcoming_assignments
            if a.get("due_date_utc") and str(a.get("due_date_utc"))[:10] == date_str
        ]
        
        event_count = len(events)
        assign_count = len(due_on_day)
        
        # Flag if day has 3+ classes, or 2+ classes with a deadline, or 2+ deadlines
        if event_count >= 3 or (event_count >= 2 and assign_count >= 1) or assign_count >= 2:
            try:
                day_name = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
            except Exception:
                day_name = date_str

            reasons = []
            if event_count >= 3:
                reasons.append(f"{event_count} scheduled commitments")
            if assign_count > 0:
                reasons.append(f"{assign_count} assignment deadline(s)")

            bottlenecks.append({
                "date": date_str,
                "day_name": day_name,
                "event_count": event_count,
                "assignment_count": assign_count,
                "assignments": [a.get("title") for a in due_on_day],
                "reason": " & ".join(reasons)
            })

    return bottlenecks


def gather_weekly_planning_context(
    start_date_iso: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Pulls 7-day outlook combining Calendar, Classroom, carryovers, and free gaps.
    
    Args:
        start_date_iso: Optional ISO start date or "YYYY-MM-DD" (defaults to today 00:00 local time).
        db_path: Optional SQLite DB path for testing or offline mode.
        
    Returns:
        Dict containing:
            - start_date, end_date
            - week_schedule (classes/events by day)
            - upcoming_assignments (unsubmitted coursework in next 7 days)
            - carryover_tasks (unfinished tasks from past 14 days)
            - daily_gaps (QUICK_TIME and DEEP_WORK slots per day)
            - quick_time_slots, deep_work_slots
            - user_preferences
            - bottlenecks (high load / crunch days)
            - metrics (summary counts)
    """
    target_db = db_path or DB_PATH
    local_tz = datetime.now().astimezone().tzinfo

    # 1. Determine start datetime
    if not start_date_iso:
        now = datetime.now(local_tz)
        start_dt = datetime.combine(now.date(), time.min).replace(tzinfo=local_tz)
    else:
        try:
            parsed = datetime.fromisoformat(start_date_iso)
        except ValueError:
            parsed = datetime.strptime(start_date_iso[:10], "%Y-%m-%d")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=local_tz)
        start_dt = datetime.combine(parsed.date(), time.min).replace(tzinfo=parsed.tzinfo)

    end_dt = start_dt + timedelta(days=7)
    start_iso = start_dt.isoformat()
    start_date_str = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")

    # 2. Pull 7 days of classes and events from Google Calendar (resilient to offline/errors)
    try:
        week_schedule = get_week_schedule(start_date_iso=start_iso, days=7)
    except Exception as e:
        print(f"Warning: Failed to fetch calendar schedule ({e}). Using empty schedule.")
        week_schedule = {
            "start_date": start_date_str,
            "end_date": end_date_str,
            "days": {
                (start_dt + timedelta(days=i)).strftime("%Y-%m-%d"): []
                for i in range(7)
            }
        }

    # 3. Pull unsubmitted assignments due in next 7 days from Google Classroom
    try:
        upcoming_assignments = get_upcoming_assignments(days=7, include_submitted=False)
    except Exception as e:
        print(f"Warning: Failed to fetch upcoming Classroom assignments ({e}).")
        upcoming_assignments = []

    # 4. Pull carryover tasks from past 14 days from SQLite
    try:
        carryovers = get_carryover_tasks(before_date_iso=start_iso, days_back=14, db_path=target_db)
    except Exception as e:
        print(f"Warning: Failed to query carryover tasks ({e}).")
        carryovers = []

    # 5. Scan timetable gaps for each of the 7 days (QUICK_TIME: 15-45m, DEEP_WORK: >=90m)
    daily_gaps: Dict[str, Dict[str, Any]] = {}
    all_quick_times: List[Dict[str, Any]] = []
    all_deep_works: List[Dict[str, Any]] = []

    for i in range(7):
        day_date = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            gaps = find_schedule_gaps(date_str=day_date, db_path=target_db)
        except Exception as e:
            print(f"Warning: Gap detection failed for {day_date}: {e}")
            gaps = []

        quick_slots = [g for g in gaps if g.get("category") == "QUICK_TIME"]
        deep_slots = [g for g in gaps if g.get("category") == "DEEP_WORK"]
        medium_slots = [g for g in gaps if g.get("category") == "MEDIUM"]

        daily_gaps[day_date] = {
            "all": gaps,
            "quick_time": quick_slots,
            "deep_work": deep_slots,
            "medium": medium_slots
        }
        all_quick_times.extend(quick_slots)
        all_deep_works.extend(deep_slots)

    # 6. User preferences
    try:
        user_prefs = list_preferences(db_path=target_db)
    except Exception as e:
        print(f"Warning: Failed to load user preferences: {e}")
        user_prefs = {}

    # 7. Identify bottlenecks
    bottlenecks = _find_bottlenecks(week_schedule, upcoming_assignments)

    total_classes = sum(len(evs) for evs in week_schedule.get("days", {}).values())

    return {
        "start_date": start_date_str,
        "end_date": end_date_str,
        "week_schedule": week_schedule,
        "upcoming_assignments": upcoming_assignments,
        "carryover_tasks": carryovers,
        "daily_gaps": daily_gaps,
        "quick_time_slots": all_quick_times,
        "deep_work_slots": all_deep_works,
        "user_preferences": user_prefs,
        "bottlenecks": bottlenecks,
        "metrics": {
            "total_classes": total_classes,
            "total_assignments": len(upcoming_assignments),
            "total_carryovers": len(carryovers),
            "total_deep_work_slots": len(all_deep_works),
            "total_quick_time_slots": len(all_quick_times)
        }
    }


# ---------------------------------------------------------------------------
# Section 3: Collaborative 3-Phase Sunday Planning Workflow
# ---------------------------------------------------------------------------

def create_clarification_briefing(context: Dict[str, Any]) -> str:
    """Step 1: Generates the initial Sunday opening message.
    
    Summarizes workload density, highlights bottleneck days, and asks 1-2 targeted
    clarifying questions to avoid blind spots before generating the schedule.
    
    Args:
        context: The unified planning context from gather_weekly_planning_context.
        
    Returns:
        Formatted briefing string ready to send to user.
    """
    prefs = context.get("user_preferences", {})
    name = prefs.get("preferred_name") or prefs.get("user_name") or "there"
    start_date = context.get("start_date", "")
    end_date = context.get("end_date", "")
    metrics = context.get("metrics", {})
    assignments = context.get("upcoming_assignments", [])
    carryovers = context.get("carryover_tasks", [])
    bottlenecks = context.get("bottlenecks", [])
    deep_slots = context.get("deep_work_slots", [])
    quick_slots = context.get("quick_time_slots", [])

    lines = []
    lines.append(f"👋 **Good evening {name}!** Let's get ahead of your week ({start_date} to {end_date}).\n")
    
    # Workload summary
    lines.append("📊 **Workload Overview:**")
    lines.append(f"- 🏛️ **Commitments:** {metrics.get('total_classes', 0)} scheduled lectures & meetings")
    lines.append(f"- 📚 **Assignments Due:** {len(assignments)} coursework deliverables")
    if assignments:
        for a in assignments[:4]:
            due_str = a.get("due_date_str") or "This week"
            lines.append(f"  • {a.get('course_name', 'Course')}: *{a.get('title')}* (Due: {due_str})")
        if len(assignments) > 4:
            lines.append(f"  • ...and {len(assignments) - 4} more")
            
    if carryovers:
        lines.append(f"- 🔄 **Unfinished Carryovers:** {len(carryovers)} task(s) from past days")
        for c in carryovers[:3]:
            lines.append(f"  • *{c.get('title')}* (from {c.get('item_date', '')[:10]})")
    lines.append("")

    # Bottlenecks
    if bottlenecks:
        lines.append("⚠️ **Bottleneck & Crunch Days:**")
        for b in bottlenecks:
            lines.append(f"- **{b['day_name']} ({b['date']}):** {b['reason']}")
        lines.append("")
    else:
        lines.append("✨ **Pacing:** Your commitments are reasonably distributed across the week.\n")

    # Focus Capacity
    lines.append("⚡ **Available Focus Capacity:**")
    lines.append(f"- **{len(deep_slots)} Deep Work block(s)** (≥90 min) open outside classes.")
    lines.append(f"- **{len(quick_slots)} Quick-Time window(s)** (15–45 min) between classes.")
    lines.append("")

    # Clarifying Questions
    lines.append("❓ **Before I draft your focus blocks, 2 quick calibration questions:**")
    if assignments:
        top_assignment = assignments[0].get("title")
        lines.append(f"1. Which deliverable will require the heaviest mental load? (e.g., *{top_assignment}* or something else?)")
    else:
        lines.append("1. Do you have any offline midterms, study goals, or project milestones to tackle this week?")

    if carryovers:
        carry_titles = ", ".join([f"'{c.get('title')}'" for c in carryovers[:2]])
        lines.append(f"2. For carryovers ({carry_titles}), did you finish any offline, or should we roll them into this week's focus plan?")
    else:
        lines.append("2. Any offline personal commitments or study sessions you want protected on your calendar?")

    lines.append("\nReply with a quick answer, and I'll generate your optimal weekly plan!")

    return "\n".join(lines)


def _extract_json_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Safely parses JSON dictionary from LLM markdown output."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    
    # Strip markdown code blocks
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
            
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Fallback bracket search
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start != -1 and end != -1 and end > start:
        candidate = raw_text[start:end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def _build_heuristic_weekly_proposal(
    context: Dict[str, Any],
    user_clarifications: str = ""
) -> Dict[str, Any]:
    """Deterministic fallback proposal engine when LLM is unavailable or offline."""
    assignments = context.get("upcoming_assignments", [])
    carryovers = context.get("carryover_tasks", [])
    deep_slots = context.get("deep_work_slots", [])
    quick_slots = context.get("quick_time_slots", [])
    prefs = context.get("user_preferences", {})
    focus_duration = int(prefs.get("focus_length", 90))
    start_date = context.get("start_date", "")

    clarif_lower = user_clarifications.lower()

    task_matrix_assignments: List[Dict[str, Any]] = []
    proposed_events: List[Dict[str, Any]] = []

    # 1. Process Carryovers
    for c in carryovers:
        c_title = c.get("title", "")
        # Check if user mentioned carryover was finished offline
        if any(w in clarif_lower for w in [c_title.lower(), "carryover", "done", "finished", "completed"]):
            task_matrix_assignments.append({
                "source_type": c.get("source_type", "classroom"),
                "source_id": c.get("source_id"),
                "title": c_title,
                "matrix": "Q1",
                "status": "DONE"
            })
        else:
            task_matrix_assignments.append({
                "source_type": c.get("source_type", "classroom"),
                "source_id": c.get("source_id"),
                "title": c_title,
                "matrix": "Q1",
                "status": "NOT_STARTED"
            })

    # 2. Prioritize Assignments into Q1, Q2, Q3
    for idx, a in enumerate(assignments):
        title = a.get("title", "Untitled Assignment")
        cw_id = a.get("assignment_id", f"assign_{idx}")
        due_str = a.get("due_date_utc") or ""
        
        is_micro = any(k in title.lower() for k in ["survey", "quiz", "poll", "feedback", "short", "reading", "forum"])
        
        # Check if due within first 2 days
        is_imminent = False
        if due_str and start_date:
            try:
                due_day = due_str[:10]
                days_diff = (datetime.strptime(due_day, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
                if days_diff <= 2:
                    is_imminent = True
            except Exception:
                pass

        if is_micro:
            quadrant = "Q3"
        elif is_imminent or idx == 0:
            quadrant = "Q1"
        else:
            quadrant = "Q2"

        task_matrix_assignments.append({
            "source_type": "classroom",
            "source_id": cw_id,
            "title": title,
            "matrix": quadrant,
            "status": "NOT_STARTED",
            "item_date": due_str
        })

    # 3. Map Q1 / Q2 tasks into available DEEP_WORK slots
    heavy_tasks = [t for t in task_matrix_assignments if t["matrix"] in ("Q1", "Q2") and t.get("status") != "DONE"]
    used_slots = set()

    for task in heavy_tasks[:4]:  # Propose up to 4 major deep focus blocks
        for slot_idx, slot in enumerate(deep_slots):
            if slot_idx in used_slots:
                continue
            
            start_iso = slot.get("start_iso")
            if not start_iso:
                continue

            dur = min(slot.get("duration_minutes", 90), focus_duration)
            proposed_events.append({
                "summary": f"Focus: {task['title']}",
                "start_time_iso": start_iso,
                "duration_minutes": dur,
                "description": f"Targeted deep focus session for '{task['title']}' ({task['matrix']})."
            })
            used_slots.add(slot_idx)
            break

    # 4. Compose proposal text
    lines = []
    lines.append("🎯 **Weekly Focus Proposal & Quadrant Matrix**\n")
    
    # Q1
    q1_tasks = [t for t in task_matrix_assignments if t["matrix"] == "Q1"]
    if q1_tasks:
        lines.append("🔥 **Q1: Must-Do Early Week (Urgent & Important)**")
        for t in q1_tasks:
            lines.append(f"- {t['title']} ({'Marked DONE' if t.get('status') == 'DONE' else 'Action required'})")
        lines.append("")

    # Q2
    q2_tasks = [t for t in task_matrix_assignments if t["matrix"] == "Q2"]
    if q2_tasks:
        lines.append("🧠 **Q2: Strategic Deep Work (Important, Not Urgent)**")
        for t in q2_tasks:
            lines.append(f"- {t['title']}")
        lines.append("")

    # Q3
    q3_tasks = [t for t in task_matrix_assignments if t["matrix"] == "Q3"]
    if q3_tasks:
        lines.append("⚡ **Q3: Quick Wins (Between Classes / 15-45m gaps)**")
        for t in q3_tasks:
            lines.append(f"- {t['title']}")
        lines.append("")

    # Proposed calendar blocks
    if proposed_events:
        lines.append("📅 **Proposed Calendar Focus Blocks (Ready to Book):**")
        for ev in proposed_events:
            start_val = ev.get("start_time_iso", "")
            try:
                ev_dt = datetime.fromisoformat(start_val)
                time_str = ev_dt.strftime("%a %b %d @ %I:%M %p")
            except Exception:
                time_str = start_val
            lines.append(f"- ⏳ **{ev['summary']}** — {time_str} ({ev['duration_minutes']} min)")
        lines.append("")

    lines.append("🔒 *No calendar events have been booked yet.*")
    lines.append("Reply **'confirm'** to book these focus sessions to Google Calendar, or let me know what adjustments you'd like!")

    return {
        "proposal_text": "\n".join(lines),
        "proposed_events": proposed_events,
        "task_matrix_assignments": task_matrix_assignments
    }


def generate_weekly_plan_proposal(
    context: Dict[str, Any],
    user_clarifications: str = ""
) -> Dict[str, Any]:
    """Step 2: Uses Gemini model to reason about tasks and free gaps to propose a weekly plan.
    
    Assigns tasks into Q1, Q2, Q3, Q4.
    Maps heavy Q1/Q2 tasks into available DEEP_WORK slots matching user energy preferences.
    Maps small Q3 micro-tasks into QUICK_TIME gaps between classes.
    
    Args:
        context: Context dictionary from gather_weekly_planning_context.
        user_clarifications: User's reply from Step 1 clarification briefing.
        
    Returns:
        Dict[str, Any]: {
            "proposal_text": str,
            "proposed_events": List[Dict],
            "task_matrix_assignments": List[Dict]
        }
    """
    # Attempt to query Gemini 3.5 Flash Lite
    try:
        from google import genai
        client = genai.Client()
        
        prefs = context.get("user_preferences", {})
        assignments = context.get("upcoming_assignments", [])
        carryovers = context.get("carryover_tasks", [])
        deep_slots = context.get("deep_work_slots", [])
        quick_slots = context.get("quick_time_slots", [])
        start_date = context.get("start_date", "")
        end_date = context.get("end_date", "")

        prompt = f"""You are Ziri, the student's personal executive assistant and strategic academic co-pilot.
Your mission is to analyze the student's 7-day academic horizon and user clarifications to create an optimal, energy-aware weekly battle plan using the Eisenhower Matrix.

USER PREFERENCES:
- Name: {prefs.get('preferred_name') or prefs.get('user_name', 'Student')}
- Peak Energy Hours: {prefs.get('energy_hours', 'Morning 09:00-12:00')}
- Preferred Deep Work Duration: {prefs.get('focus_length', 90)} minutes
- Hobbies / Protected Downtime: {prefs.get('hobbies', [])}

USER CLARIFICATIONS FROM STEP 1:
"{user_clarifications}"

UPCOMING ACADEMIC DELIVERABLES (Google Classroom):
{json.dumps([{'assignment_id': a.get('assignment_id'), 'title': a.get('title'), 'course': a.get('course_name'), 'due': a.get('due_date_utc')} for a in assignments], indent=2)}

UNFINISHED CARRYOVER TASKS (SQLite Memory):
{json.dumps([{'source_id': c.get('source_id'), 'title': c.get('title'), 'date': c.get('item_date'), 'status': c.get('status')} for c in carryovers], indent=2)}

AVAILABLE DEEP WORK SLOTS (>=90 min free blocks in Calendar):
{json.dumps([{'date': s.get('date'), 'start': s.get('start'), 'end': s.get('end'), 'duration': s.get('duration_minutes'), 'start_iso': s.get('start_iso')} for s in deep_slots[:10]], indent=2)}

AVAILABLE QUICK-TIME GAPS (15-45 min between lectures):
{json.dumps([{'date': s.get('date'), 'start': s.get('start'), 'end': s.get('end'), 'duration': s.get('duration_minutes')} for s in quick_slots[:10]], indent=2)}

RULES & INSTRUCTIONS:
1. Eisenhower Matrix Categorization:
   - Q1 (Must-Do Today / Urgent & Important): Hard deadlines within 1-2 days or critical urgent deliverables.
   - Q2 (Deep Focus / Not Urgent & Important): Heavy assignments due later in week, study/prep blocks, proactive work.
   - Q3 (Quick Wins / Urgent & Not Important): Micro-tasks, short quizzes, surveys, submissions (<30 min) to knock out in QUICK_TIME gaps between classes.
   - Q4 (Low Priority / Not Urgent & Not Important): Optional or low-impact items.
2. Energy-Aware Slot Placement:
   - Map 2 to 4 heavy Q1/Q2 tasks into open DEEP_WORK slots that align with peak energy hours.
   - Assign small Q3 micro-tasks to QUICK_TIME windows between classes (the compound effect).
   - If user clarifications indicated a carryover was completed offline, set status to 'DONE'.
3. SAFETY RULE:
   - Never book events directly! Only formulate the proposal.
   - You MUST end proposal_text asking for explicit confirmation: 'Reply **confirm** to book these focus sessions into your Google Calendar!'
4. Return Format:
   Respond with ONLY a valid JSON object matching this schema:
{{
  "proposal_text": "Markdown formatted proposal message ready to send to user...",
  "proposed_events": [
    {{
      "summary": "Focus: Machine Learning Lab",
      "start_time_iso": "2026-10-05T09:00:00+01:00",
      "duration_minutes": 90,
      "description": "Deep work focus session scheduled via Sunday Planning."
    }}
  ],
  "task_matrix_assignments": [
    {{
      "source_type": "classroom",
      "source_id": "string",
      "title": "Assignment Title",
      "matrix": "Q1",
      "status": "NOT_STARTED"
    }}
  ]
}}
"""
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt
        )

        parsed = _extract_json_response(response.text)
        if parsed and "proposal_text" in parsed and "proposed_events" in parsed and "task_matrix_assignments" in parsed:
            return parsed
        else:
            print("Warning: Gemini response could not be parsed as required JSON. Falling back to heuristic proposal.")
    except Exception as e:
        print(f"Warning: Gemini generation failed or unavailable ({e}). Using heuristic proposal engine.")

    # Resilient fallback
    return _build_heuristic_weekly_proposal(context, user_clarifications)


def commit_weekly_plan(
    proposed_events: List[Dict[str, Any]],
    task_matrix_assignments: List[Dict[str, Any]],
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Step 3: Books confirmed focus blocks to Google Calendar and saves matrix priorities.
    
    Executes ONLY after user confirmation.
    
    Args:
        proposed_events: List of event dicts with summary, start_time_iso, duration_minutes.
        task_matrix_assignments: List of task dicts with source_type, source_id, matrix, status, title.
        db_path: Optional SQLite DB path.
        
    Returns:
        Dict summarizing booked events, saved matrix items, and any errors.
    """
    target_db = db_path or DB_PATH
    booked_events: List[Dict[str, Any]] = []
    saved_matrix_items: List[Dict[str, Any]] = []
    calendar_errors: List[Dict[str, Any]] = []
    matrix_errors: List[Dict[str, Any]] = []

    # 1. Book Calendar Events
    for ev in proposed_events:
        summary = ev.get("summary") or ev.get("title", "Focus Session")
        start_time_iso = ev.get("start_time_iso")
        duration = int(ev.get("duration_minutes", 60))
        description = ev.get("description", "Focus session booked via Sunday Planning.")

        if not start_time_iso:
            calendar_errors.append({"event": ev, "error": "Missing start_time_iso"})
            continue

        try:
            created = create_calendar_event(
                summary=summary,
                start_time_iso=start_time_iso,
                duration_minutes=duration,
                description=description
            )
            booked_events.append(created)
        except Exception as e:
            # Calendar API might fail in test/offline environments
            calendar_errors.append({"event": ev, "error": str(e)})
            # Record locally in task_status to avoid losing track of planned block
            try:
                upsert_task(
                    source_type="calendar",
                    source_id=f"planned_{int(datetime.now().timestamp())}_{len(booked_events)}",
                    title=summary,
                    status="NOT_STARTED",
                    item_date=start_time_iso,
                    matrix="Q2",
                    db_path=target_db
                )
            except Exception:
                pass

    # 2. Update Task Status & Matrix in SQLite
    for task in task_matrix_assignments:
        source_type = task.get("source_type", "classroom")
        source_id = str(task.get("source_id", ""))
        matrix = task.get("matrix")
        status = task.get("status", "NOT_STARTED")
        title = task.get("title", "Untitled Task")
        item_date = task.get("item_date")

        if not source_id:
            matrix_errors.append({"task": task, "error": "Missing source_id"})
            continue

        if matrix and matrix not in VALID_MATRIX_QUADRANTS:
            matrix = None

        try:
            # Update status and matrix
            updated = update_task_status(
                source_type=source_type,
                source_id=source_id,
                new_status=status,
                title=title,
                item_date=item_date,
                matrix=matrix,
                db_path=target_db
            )
            if not updated:
                upsert_task(
                    source_type=source_type,
                    source_id=source_id,
                    title=title,
                    status=status,
                    item_date=item_date,
                    matrix=matrix,
                    db_path=target_db
                )
            saved_matrix_items.append({
                "source_type": source_type,
                "source_id": source_id,
                "title": title,
                "matrix": matrix,
                "status": status
            })
        except Exception as e:
            matrix_errors.append({"task": task, "error": str(e)})

    return {
        "booked_events": booked_events,
        "saved_matrix_items": saved_matrix_items,
        "calendar_errors": calendar_errors,
        "matrix_errors": matrix_errors,
        "summary": (
            f"Successfully booked {len(booked_events)} focus blocks to Google Calendar "
            f"and updated {len(saved_matrix_items)} task matrix priorities."
        )
    }


# ---------------------------------------------------------------------------
# Section 4: Self-Verification Test Suite
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import tempfile
    import shutil
    from storage import init_db

    print("=================================================================")
    print("Testing Sunday Planning Engine (Phase 3 of v4)")
    print("=================================================================\n")

    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_sunday_planner.db")
    init_db(db_path=test_db)

    try:
        # TEST GROUP 1: Onboarding Handlers
        print("1. Testing One-Time Onboarding Check & Handlers...")
        # Check should be needed when DB is empty
        assert is_onboarding_needed(db_path=test_db) is True, "Onboarding should be needed on fresh DB"
        
        prompt = get_onboarding_prompt()
        assert "Preferred Name" in prompt
        assert "Peak Energy Hours" in prompt
        assert "Focus Session Length" in prompt
        assert "Hobbies" in prompt
        
        # Save onboarding preferences
        save_onboarding_preferences(
            name="Walid",
            energy_hours="Morning 09:00-12:00",
            focus_length=90,
            hobbies=["Gym Mon/Wed", "Reading"],
            db_path=test_db
        )
        assert is_onboarding_needed(db_path=test_db) is False, "Onboarding should NOT be needed after saving"
        assert get_preference("preferred_name", db_path=test_db) == "Walid"
        assert get_preference("energy_hours", db_path=test_db) == "Morning 09:00-12:00"
        assert get_preference("focus_length", db_path=test_db) == 90
        print("   [PASS] Onboarding check, prompt generation, and saving preferences verified.\n")

        # TEST GROUP 2: Context Synthesizer
        print("2. Testing 7-Day Planning Context Synthesizer...")
        # Populate test database with carryover tasks and sample tasks
        past_date = (datetime.now() - timedelta(days=2)).isoformat()
        upsert_task(
            source_type="classroom",
            source_id="carryover_lab_1",
            title="Database Systems Lab 1",
            status="NOT_STARTED",
            item_date=past_date,
            db_path=test_db
        )
        upsert_task(
            source_type="classroom",
            source_id="carryover_calc_1",
            title="Calculus Problem Set 2",
            status="IN_PROGRESS",
            item_date=past_date,
            db_path=test_db
        )

        context = gather_weekly_planning_context(db_path=test_db)
        assert "start_date" in context
        assert "end_date" in context
        assert "week_schedule" in context
        assert "upcoming_assignments" in context
        assert "carryover_tasks" in context
        assert "daily_gaps" in context
        assert "user_preferences" in context
        assert "metrics" in context
        
        # Carryovers should reflect the two inserted items
        carryovers = context["carryover_tasks"]
        assert len(carryovers) >= 2, f"Expected at least 2 carryovers, found {len(carryovers)}"
        print(f"   [PASS] Context synthesizer assembled 7-day outlook with {len(carryovers)} carryover tasks.\n")

        # TEST GROUP 3: Clarification Briefing (Step 1)
        print("3. Testing Step 1: Clarification Briefing...")
        briefing = create_clarification_briefing(context)
        assert "Good evening Walid!" in briefing
        assert "Workload Overview" in briefing
        assert "Available Focus Capacity" in briefing
        assert "Database Systems Lab 1" in briefing or "carryover" in briefing.lower()
        print("   [PASS] Step 1 Clarification Briefing generated with workload summary and targeted questions.\n")

        # TEST GROUP 4: Proposal Generation (Step 2)
        print("4. Testing Step 2: Weekly Plan Proposal Generation...")
        # Provide sample user clarification
        clarifications = "Database Systems Lab is done offline. Calculus is my heaviest assignment this week."
        proposal = generate_weekly_plan_proposal(context, user_clarifications=clarifications)
        
        assert "proposal_text" in proposal
        assert "proposed_events" in proposal
        assert "task_matrix_assignments" in proposal
        assert isinstance(proposal["proposed_events"], list)
        assert isinstance(proposal["task_matrix_assignments"], list)
        
        # Verify carryover status was updated to DONE if clarification said so
        db_lab_task = next((t for t in proposal["task_matrix_assignments"] if "Database Systems" in t.get("title", "")), None)
        if db_lab_task:
            assert db_lab_task.get("status") == "DONE", "Database Systems Lab should be marked DONE based on clarification"

        print("   [PASS] Step 2 Plan proposal generated with Eisenhower quadrants and proposed focus blocks.\n")

        # TEST GROUP 5: Commit Weekly Plan (Step 3)
        print("5. Testing Step 3: Commit Weekly Plan...")
        # Sample proposed events and matrix assignments
        test_events = [
            {
                "summary": "Focus: Calculus Problem Set 2",
                "start_time_iso": (datetime.now() + timedelta(days=1)).isoformat(),
                "duration_minutes": 90,
                "description": "Deep work session for Calculus."
            }
        ]
        test_assignments = [
            {
                "source_type": "classroom",
                "source_id": "carryover_calc_1",
                "title": "Calculus Problem Set 2",
                "matrix": "Q1",
                "status": "NOT_STARTED"
            },
            {
                "source_type": "classroom",
                "source_id": "carryover_lab_1",
                "title": "Database Systems Lab 1",
                "matrix": "Q1",
                "status": "DONE"
            }
        ]

        commit_res = commit_weekly_plan(test_events, test_assignments, db_path=test_db)
        assert "saved_matrix_items" in commit_res
        assert len(commit_res["saved_matrix_items"]) == 2
        
        # Verify in database
        calc_task = get_task("classroom", "carryover_calc_1", db_path=test_db)
        assert calc_task["matrix"] == "Q1"
        assert calc_task["status"] == "NOT_STARTED"

        lab_task = get_task("classroom", "carryover_lab_1", db_path=test_db)
        assert lab_task["status"] == "DONE"

        print("   [PASS] Step 3 Commit Weekly Plan executed and verified against SQLite database.\n")
        print("=================================================================")
        print("All Sunday Planning Engine (Phase 3) verification tests PASSED!")
        print("=================================================================")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
