import logging
from datetime import datetime, date, timedelta
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
SKIP_WORDS = ("-", "skip", "no", "praleisti")


def parse_date(text):
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(text):
    text = text.strip()
    if text.lower() in SKIP_WORDS:
        return None
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None


def format_event_line(ev):
    d = ev["event_date"].strftime("%Y-%m-%d")
    t = ev["event_time"].strftime(" %H:%M") if ev["event_time"] else ""
    return f"{d}{t} — {ev['title']}"


def format_event_list(events, empty_message):
    if not events:
        return empty_message
    return "\n".join(f"• {format_event_line(e)}" for e in events)


def user_today(user):
    try:
        tz = ZoneInfo(user["timezone"] or DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ)
    return datetime.now(tz).date()


HELP_TEXT = (
    "👋 Labas! Aš esu <b>Remidionak</b> — tavo įvykių priminimų botas.\n\n"
    "Kas vakarą 22:00 (pagal tavo laiko juostą, numatyta — Europe/Vilnius) "
    "atsiųsiu artimiausios savaitės įvykius.\n\n"
    "Komandos:\n"
    "/add — pridėti įvykį\n"
    "/week — artimiausios savaitės įvykiai\n"
    "/month — artimiausio mėnesio įvykiai\n"
    "/year — visi šių metų įvykiai\n"
    "/list — visi būsimi įvykiai su ID (trynimui)\n"
    "/delete ID — ištrinti įvykį\n"
    "/timezone paieška — nustatyti laiko juostą\n"
    "/cancel — atšaukti dabartinį veiksmą"
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
        bot.reply_to(message, "Atšaukta.")

    @bot.message_handler(commands=["add"])
    def cmd_add(message):
        db.get_or_create_user(message.chat.id)
        PENDING[message.chat.id] = {"step": "title"}
        bot.reply_to(message, "Kaip pavadinsime įvykį? (Arba /cancel)")

    @bot.message_handler(commands=["week"])
    def cmd_week(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_between(message.chat.id, today, today + timedelta(days=7))
        bot.reply_to(
            message,
            "📅 <b>Artimiausia savaitė:</b>\n" + format_event_list(events, "Įvykių nėra."),
        )

    @bot.message_handler(commands=["month"])
    def cmd_month(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_between(message.chat.id, today, today + timedelta(days=30))
        bot.reply_to(
            message,
            "📅 <b>Artimiausias mėnuo:</b>\n" + format_event_list(events, "Įvykių nėra."),
        )

    @bot.message_handler(commands=["year"])
    def cmd_year(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        year_end = date(today.year, 12, 31)
        events = db.get_events_between(message.chat.id, today, year_end)
        bot.reply_to(
            message,
            f"📅 <b>Šių ({today.year}) metų įvykiai:</b>\n" + format_event_list(events, "Įvykių nėra."),
        )

    @bot.message_handler(commands=["list"])
    def cmd_list(message):
        user = db.get_or_create_user(message.chat.id)
        today = user_today(user)
        events = db.get_events_from(message.chat.id, today)
        if not events:
            bot.reply_to(message, "Įvykių nėra.")
            return
        lines = [f"#{e['id']} {format_event_line(e)}" for e in events]
        bot.reply_to(
            message,
            "🗒 <b>Būsimi įvykiai:</b>\n" + "\n".join(lines) + "\n\nTrinti: /delete ID",
        )

    @bot.message_handler(commands=["delete"])
    def cmd_delete(message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            bot.reply_to(message, "Naudojimas: /delete ID (ID rasi per /list)")
            return
        event_id = int(parts[1].strip())
        ok = db.delete_event(message.chat.id, event_id)
        bot.reply_to(message, "✅ Ištrinta." if ok else "Nerasta tokio įvykio.")

    @bot.message_handler(commands=["timezone"])
    def cmd_timezone(message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            bot.reply_to(
                message,
                "Naudojimas: /timezone paieškos_žodis\nPvz.: /timezone Vilnius arba /timezone Warsaw",
            )
            return
        query = parts[1].strip().lower()
        matches = sorted(tz for tz in available_timezones() if query in tz.lower())[:20]
        if not matches:
            bot.reply_to(
                message,
                "Nieko neradau. Bandyk kitą paieškos žodį (pvz. miesto arba žemyno pavadinimą).",
            )
            return
        markup = types.InlineKeyboardMarkup()
        for tz in matches:
            markup.add(types.InlineKeyboardButton(tz, callback_data=f"tz:{tz}"))
        bot.reply_to(message, "Pasirink laiko juostą:", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("tz:"))
    def cb_timezone(call):
        tz_name = call.data.split(":", 1)[1]
        db.get_or_create_user(call.message.chat.id)
        db.set_timezone(call.message.chat.id, tz_name)
        bot.answer_callback_query(call.id, "Laiko juosta nustatyta ✅")
        bot.edit_message_text(
            f"✅ Laiko juosta nustatyta: <b>{tz_name}</b>",
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
                bot.reply_to(message, "Pavadinimas negali būti tuščias. Bandyk dar kartą arba /cancel")
                return
            state["title"] = title
            state["step"] = "date"
            bot.reply_to(message, "Kokia data? (YYYY-MM-DD arba DD-MM-YYYY)")
            return

        if step == "date":
            d = parse_date(message.text)
            if not d:
                bot.reply_to(
                    message,
                    "Nesupratau datos. Formatas: YYYY-MM-DD arba DD-MM-YYYY. Bandyk dar kartą arba /cancel",
                )
                return
            state["event_date"] = d
            state["step"] = "time"
            bot.reply_to(message, "Koks laikas? (HH:MM), arba parašyk „-“ jei laikas nesvarbu")
            return

        if step == "time":
            text = message.text.strip()
            t = parse_time(text)
            if text.lower() not in SKIP_WORDS and t is None:
                bot.reply_to(
                    message,
                    "Nesupratau laiko. Formatas: HH:MM arba „-“. Bandyk dar kartą arba /cancel",
                )
                return
            event_id = db.add_event(message.chat.id, state["title"], state["event_date"], t)
            PENDING.pop(message.chat.id, None)
            line = format_event_line(
                {"event_date": state["event_date"], "event_time": t, "title": state["title"]}
            )
            bot.reply_to(message, f"✅ Įvykis #{event_id} pridėtas: {line}")
            return


def run_reminder_check(bot: telebot.TeleBot):
    """Called by /tick. Sends the weekly digest to any user whose local time is 22:00
    and who hasn't already received today's reminder."""
    sent = 0
    for user in db.get_all_users():
        try:
            tz = ZoneInfo(user["timezone"] or DEFAULT_TZ)
        except Exception:
            tz = ZoneInfo(DEFAULT_TZ)
        now = datetime.now(tz)
        today = now.date()

        if now.hour != 22:
            continue
        if user["last_reminder_date"] == today:
            continue

        events = db.get_events_between(user["chat_id"], today, today + timedelta(days=7))
        text = "🌙 <b>Artimiausios savaitės įvykiai:</b>\n" + format_event_list(
            events, "Ateinančią savaitę įvykių nėra."
        )
        try:
            bot.send_message(user["chat_id"], text, parse_mode="HTML")
            db.update_last_reminder(user["chat_id"], today)
            sent += 1
        except Exception:
            logger.exception("Failed to send reminder to %s", user["chat_id"])
    return sent
