import logging
import os
import sys
import threading
import time

from dotenv import load_dotenv
from flask import Flask, abort, request
import telebot

import bot_handlers
from db import init_db

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("remidionak")


def require_env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


TOKEN = require_env("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", TOKEN)
APP_SECRET = os.environ.get("APP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=False)
bot_handlers.register(bot)

init_db()

app = Flask(__name__)

if not APP_SECRET:
    logger.warning("APP_SECRET is not set: /tick and /set_webhook will refuse every request")


def check_app_secret():
    if not APP_SECRET:
        abort(403, "APP_SECRET env var not set on the server")
    if request.args.get("secret") != APP_SECRET:
        abort(403, "Wrong secret")


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        try:
            bot.process_new_updates([update])
        except Exception:
            logger.exception("Error while processing update: %s", json_str)
        return "", 200
    abort(403)


@app.route("/set_webhook")
def set_webhook_route():
    check_app_secret()
    if not BASE_URL:
        return "BASE_URL env var not set", 400
    url = f"{BASE_URL.rstrip('/')}/webhook/{WEBHOOK_SECRET}"
    bot.remove_webhook()
    ok = bot.set_webhook(url=url)
    return {"ok": ok, "url": url}


@app.route("/tick")
def tick():
    check_app_secret()
    sent = bot_handlers.run_reminder_check(bot)
    return {"sent": sent}


@app.route("/")
def index():
    return "Remidionak bot is running."


def reminder_loop(interval=60):
    """In polling mode there is no web server for the external cron to hit
    /tick on, so run the same reminder check in-process instead."""
    while True:
        try:
            bot_handlers.run_reminder_check(bot)
        except Exception:
            logger.exception("Reminder check failed")
        time.sleep(interval)


if __name__ == "__main__":
    if os.environ.get("RUN_MODE") == "polling":
        logger.info("Starting in polling mode (local testing)")
        threading.Thread(target=reminder_loop, daemon=True).start()
        bot.remove_webhook()
        bot.infinity_polling()
    else:
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
