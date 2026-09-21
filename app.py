import logging
import os
import sys

from flask import Flask, abort, request
import telebot

import bot_handlers
from db import init_db

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

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
bot_handlers.register(bot)

init_db()

app = Flask(__name__)


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    abort(403)


@app.route("/set_webhook")
def set_webhook_route():
    if not APP_SECRET or request.args.get("secret") != APP_SECRET:
        abort(403)
    if not BASE_URL:
        return "BASE_URL env var not set", 400
    url = f"{BASE_URL.rstrip('/')}/webhook/{WEBHOOK_SECRET}"
    bot.remove_webhook()
    ok = bot.set_webhook(url=url)
    return {"ok": ok, "url": url}


@app.route("/tick")
def tick():
    if not APP_SECRET or request.args.get("secret") != APP_SECRET:
        abort(403)
    sent = bot_handlers.run_reminder_check(bot)
    return {"sent": sent}


@app.route("/")
def index():
    return "Remidionak bot is running."


if __name__ == "__main__":
    if os.environ.get("RUN_MODE") == "polling":
        logger.info("Starting in polling mode (local testing)")
        bot.remove_webhook()
        bot.infinity_polling()
    else:
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
