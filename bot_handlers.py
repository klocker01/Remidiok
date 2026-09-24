import logging
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo, available_timezones

import telebot
from telebot import types

import db

logger = logging.getLogger("remidionak")

DEFAULT_TZ = "Europe/Vilnius"

# In-memory conversation state for the /add flow.
# chat_id -> {"step": "title" | "date" | "time", "title": str, "event_date": date}
PENDING = {}

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"]
SHORT_DATE_FORMATS = ["%d-%m", "%d.%m", "%d/%m", "%m-%d", "%m.%d", "%m/%d"]
SKIP_WORDS = ("-", "skip")

DEFAULT_REMINDER_TIME = time(22, 0)
REMINDER_DAYS = 14


def parse_date(text, today=None):
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # No year given (e.g. "09-28") — default to the current year, rolling
    # over to next year if that day has already passed.
    if today is None:
        today = date.today()
    for fmt in SHORT_DATE_FORMATS:
        try:
            # Parse against a leap year placeholder so "29-02" doesn't fail.
            parsed = datetime.strptime(f"{text}-2000", f"{fmt}-%Y").date()
        except ValueError:
            continue
        d = _next_occurrence(parsed.month, parsed.day, today.year)
        if d < today:
            d = _next_occurrence(parsed.month, parsed.day, d.year + 1)
        return d
    return None


def _next_occurrence(month, day, from_year):
    """First valid date with this month/day at or after from_year
    (Feb 29 skips forward to the next leap year)."""
    for year in range(from_year, from_year + 8):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(f"No valid year found for {month}-{day} from {from_year}")


def parse_time(text):
    text = text.strip()
    if text.lower() in SKIP_WORDS:
        return None
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None


def format_event_line(ev):
    d = ev["event_date"].strftime("%Y-%m-%d (%a)")
    t = ev["event_time"].strftime(" %H:%M") if ev["event_time"] else ""
    return f"{d}{t} — {ev['title']}"


def format_event_list(events, empty_message):
    if not events:
        return empty_message
    return "\n".join(f"• {format_event_line(e)}" for e in events)


def user_tz(user):
    try:
        return ZoneInfo(user["timezone"] or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def user_reminder_time(user):
    return user.get("reminder_time") or DEFAULT_REMINDER_TIME


def user_today(user):
    """Computes the user's local date and, as a side effect, purges any of
    their events that are now in the past — this is called on every command
    that touches dates, so it doubles as the cleanup hook."""
    today = datetime.now(user_tz(user)).date()
    db.delete_past_events(user["chat_id"], today)
    return today


HELP_TEXT = (
    "👋 Hi! I'm <b>Remidiok</b> — your event reminder bot.\n\n"
    "Every day at 22:00 by default (in your timezone, default — Europe/Vilnius) "
    "I'll send you the events coming up in the next 2 weeks. "
    "Change the time with /remindertime.\n\n"
    "Commands:\n"
    "/add — add an event\n"
    "/week — events in the next week\n"
    "/month — events in the next month\n"
    "/year — remaining events this year (/year next for next year)\n"
    "/list — all upcoming events with IDs (for deleting)\n"
    "/delete ID — delete an event\n"
    "/timezone search — set your timezone\n"
    "/remindertime HH:MM — when to send the daily 2-week digest\n"
    "/cancel — cancel the current action"
)


def register(bot: telebot.TeleBot):

    @bot.message_handler(commands=["start", "help"])
    def cmd_start(message):
        db.get_or_create_user(message.chat.id)
        PENDING.pop(message.chat.id, None)
        bot.reply_to(message, HELP_TEXT)

    @bot.message_handler(commands=["cancel"])
    def cmd_cancel(message):
        PENDING.pop(message.chat.id, None)
        bot.reply_to(message, "Cancelled.")

    @bot.message_handler(commands=["add"])
    def cmd_add(message):
        db.get_or_create_user(message.chat.id)
        PENDING[message.chat.id] = {"step": "title"}
        bot.reply_to(message, "What should we call the event? (Or /cancel)")

    @bot.message_handler(commands=["week"])
    def cmd_week(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_between(message.chat.id, today, today + timedelta(days=7))
        bot.reply_to(
            message,
            "📅 <b>Next week:</b>\n" + format_event_list(events, "No events."),
        )

    @bot.message_handler(commands=["month"])
    def cmd_month(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_between(message.chat.id, today, today + timedelta(days=30))
        bot.reply_to(
            message,
            "📅 <b>Next month:</b>\n" + format_event_list(events, "No events."),
        )

    @bot.message_handler(commands=["year"])
    def cmd_year(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        parts = message.text.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""

        if arg in ("", "this"):
            target_year = today.year
            start = today
        elif arg == "next":
            target_year = today.year + 1
            start = date(target_year, 1, 1)
        else:
            bot.reply_to(message, "Usage: /year or /year next")
            return

        year_end = date(target_year, 12, 31)
        events = db.get_events_between(message.chat.id, start, year_end)
        bot.reply_to(
            message,
            f"📅 <b>Events in {target_year}:</b>\n" + format_event_list(events, "No events."),
        )

    @bot.message_handler(commands=["list"])
    def cmd_list(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_from(message.chat.id, today)
        if not events:
            bot.reply_to(message, "No events.")
            return
        lines = [f"#{e['id']} {format_event_line(e)}" for e in events]
        bot.reply_to(
            message,
            "🗒 <b>Upcoming events:</b>\n" + "\n".join(lines) + "\n\nDelete: /delete ID",
        )

    @bot.message_handler(commands=["delete"])
    def cmd_delete(message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            bot.reply_to(message, "Usage: /delete ID (find the ID via /list)")
            return
        event_id = int(parts[1].strip())
        ok = db.delete_event(message.chat.id, event_id)
        bot.reply_to(message, "✅ Deleted." if ok else "No such event found.")

    @bot.message_handler(commands=["timezone"])
    def cmd_timezone(message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            bot.reply_to(
                message,
                "Usage: /timezone search_term\nE.g.: /timezone Vilnius or /timezone Warsaw",
            )
            return
        query = parts[1].strip().lower()
        matches = sorted(tz for tz in available_timezones() if query in tz.lower())[:20]
        if not matches:
            bot.reply_to(
                message,
                "Nothing found. Try another search term (e.g. a city or continent name).",
            )
            return
        markup = types.InlineKeyboardMarkup()
        for tz in matches:
            markup.add(types.InlineKeyboardButton(tz, callback_data=f"tz:{tz}"))
        bot.reply_to(message, "Choose your timezone:", reply_markup=markup)

    @bot.message_handler(commands=["remindertime"])
    def cmd_remindertime(message):
        user = db.get_or_create_user(message.chat.id)
        parts = message.text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            current = user_reminder_time(user).strftime("%H:%M")
            bot.reply_to(
                message,
                f"Daily digest time: <b>{current}</b>\nChange it: /remindertime HH:MM (e.g. /remindertime 08:30)",
            )
            return
        t = None if arg.lower() in SKIP_WORDS else parse_time(arg)
        if t is None:
            bot.reply_to(message, "I didn't understand the time. Format: HH:MM, e.g. /remindertime 08:30")
            return
        # The reminder check sends once now >= reminder_time and today's digest
        # isn't marked as sent. Reset that mark so the new time is honoured
        # as the user expects: a time still ahead today fires today (even if
        # the old time already fired), a time already past waits for tomorrow
        # instead of firing on the very next tick.
        now = datetime.now(user_tz(user))
        if t > now.time():
            last_sent, when = None, "today"
        else:
            last_sent, when = now.date(), "tomorrow"
        db.set_reminder_time(message.chat.id, t, last_sent)
        bot.reply_to(
            message,
            f"✅ I'll send the 2-week digest daily at <b>{t.strftime('%H:%M')}</b> "
            f"(next one {when}).",
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("tz:"))
    def cb_timezone(call):
        tz_name = call.data.split(":", 1)[1]
        db.get_or_create_user(call.message.chat.id)
        db.set_timezone(call.message.chat.id, tz_name)
        bot.answer_callback_query(call.id, "Timezone set ✅")
        bot.edit_message_text(
            f"✅ Timezone set: <b>{tz_name}</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )

    @bot.message_handler(
        func=lambda m: m.chat.id in PENDING and not (m.text or "").startswith("/"),
        content_types=["text"],
    )
    def handle_pending(message):
        state = PENDING.get(message.chat.id)
        if not state:
            return
        step = state["step"]

        if step == "title":
            title = message.text.strip()
            if not title:
                bot.reply_to(message, "The title can't be empty. Try again or /cancel")
                return
            state["title"] = title
            state["step"] = "date"
            bot.reply_to(
                message,
                "What's the date? (YYYY-MM-DD, DD-MM-YYYY, or just DD-MM / MM-DD for this year, "
                "e.g. 09-28)",
            )
            return

        if step == "date":
            user = db.get_or_create_user(message.chat.id)
            today = user_today(user)
            d = parse_date(message.text, today)
            if not d:
                bot.reply_to(
                    message,
                    "I didn't understand the date. Format: YYYY-MM-DD, DD-MM-YYYY, or just DD-MM / "
                    "MM-DD for this year. Try again or /cancel",
                )
                return
            state["event_date"] = d
            state["step"] = "time"
            bot.reply_to(message, "What time? (HH:MM), or type \"-\" if the time doesn't matter")
            return

        if step == "time":
            text = message.text.strip()
            t = parse_time(text)
            if text.lower() not in SKIP_WORDS and t is None:
                bot.reply_to(
                    message,
                    "I didn't understand the time. Format: HH:MM or \"-\". Try again or /cancel",
                )
                return
            event_id = db.add_event(message.chat.id, state["title"], state["event_date"], t)
            PENDING.pop(message.chat.id, None)
            line = format_event_line(
                {"event_date": state["event_date"], "event_time": t, "title": state["title"]}
            )
            bot.reply_to(message, f"✅ Event #{event_id} added: {line}")
            return


def run_reminder_check(bot: telebot.TeleBot):
    """Called by /tick. Purges past events for every user, and sends the
    digest of the next 2 weeks to any user whose local time has reached their
    reminder_time (default 22:00) and who hasn't already received today's
    reminder. Using ">=" rather than "==" means a missed tick (sleeping
    service, late pinger) still gets the reminder out before midnight."""
    sent = 0
    for user in db.get_all_users():
        # One user's failure (DB error, blocked bot, ...) must not stop the
        # digest going out to everyone after them in the list.
        try:
            if _send_digest_if_due(bot, user):
                sent += 1
        except Exception:
            logger.exception("Reminder check failed for %s", user["chat_id"])
    return sent


def _send_digest_if_due(bot, user):
    today = user_today(user)  # also purges this user's past events

    now = datetime.now(user_tz(user))

    if now.time() < user_reminder_time(user):
        return False
    if user["last_reminder_date"] == today:
        return False

    events = db.get_events_between(
        user["chat_id"], today, today + timedelta(days=REMINDER_DAYS)
    )
    text = "🌙 <b>Events for the next 2 weeks:</b>\n" + format_event_list(
        events, "No events in the next 2 weeks."
    )
    bot.send_message(user["chat_id"], text, parse_mode="HTML")
    db.update_last_reminder(user["chat_id"], today)
    return True
