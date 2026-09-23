# Remidionak

A Telegram bot that remembers events (date + optional time) and every day at
22:00 (or a time the user picks with `/remindertime`), in each user's own timezone (default `Europe/Vilnius`), sends the
events coming up in the next 2 weeks.

Built to run for free on [Render](https://render.com) as a Flask web
service using Telegram webhooks.

## Commands

- `/add` — add an event (asks for title, date, then optional time)
- `/week` — events in the next 7 days
- `/month` — events in the next 30 days
- `/year` — remaining events this calendar year (`/year next` for next year)
- `/list` — all upcoming events with their IDs
- `/delete ID` — delete an event (ID from `/list`)
- `/timezone <search>` — search and set your timezone, e.g. `/timezone Vilnius`
- `/remindertime HH:MM` — when to send the daily 2-week digest (default 22:00);
  without an argument it shows the current time
- `/cancel` — cancel whatever `/add` flow is in progress

## Why a webhook + a `/tick` endpoint?

Render's **free** web services fall asleep after ~15 minutes of no traffic,
and there's no free background worker or cron on the free plan. So:

- Telegram talks to the bot via **webhook** (`POST /webhook/<secret>`) —
  Telegram itself wakes the service up when a user sends a message.
- The daily 22:00 reminder is driven by an **external free cron pinger**
  (e.g. [cron-job.org](https://cron-job.org)) hitting `GET /tick?secret=...`
  every ~10 minutes. That endpoint wakes the service and checks: "is it
  past this user's reminder time (default 22:00) in their timezone, and have they not been reminded
  today yet?" If so, it sends the digest and marks that user as done for
  the day.

## 1. Create the bot

Message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`,
and copy the token it gives you — that's `TELEGRAM_TOKEN`.

## 2. Create a free Postgres database

Render's free tier no longer includes a persistent free Postgres plan, so
use a separate free database instead — e.g. [Supabase](https://supabase.com):

1. Create a project (free tier).
2. Project Settings → Database → Connection string → **URI**.
3. Copy it — that's `DATABASE_URL`.

Tables are created automatically on first startup (see `db.py`).

## 3. Deploy to Render

1. Push this folder to a GitHub repo.
2. In Render, **New → Blueprint**, point it at the repo (it will pick up
   `render.yaml`), or create a **Web Service** manually with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
3. Set the environment variables (see `.env.example`):
   - `TELEGRAM_TOKEN`
   - `DATABASE_URL`
   - `WEBHOOK_SECRET` — any random string
   - `APP_SECRET` — any random string
   - `BASE_URL` — your Render URL, e.g. `https://remidionak.onrender.com`
     (you'll know this once the first deploy finishes; set it and redeploy,
     or add it right away if you already know your service name)
4. Deploy.

## 4. Register the Telegram webhook

Once deployed, visit (in a browser, once):

```
https://<your-app>.onrender.com/set_webhook?secret=<APP_SECRET>
```

You should see `{"ok": true, "url": "..."}`. From now on Telegram will
deliver messages straight to your bot.

## 5. Set up the free cron pinger

At [cron-job.org](https://cron-job.org) (or any free cron service), create
a job that sends a `GET` request every **10 minutes** to:

```
https://<your-app>.onrender.com/tick?secret=<APP_SECRET>
```

That's it — this single job both keeps the service checked-in and fires the
22:00 reminders.

## Local testing (optional)

You don't need a public URL to try it locally — run in polling mode:

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=your-token
export DATABASE_URL=your-local-or-supabase-url
export RUN_MODE=polling
python app.py
```

Message your bot on Telegram — it should respond immediately.

## Notes

- Dates for `/add` accept `YYYY-MM-DD`, `DD-MM-YYYY`, `DD.MM.YYYY`, or `DD/MM/YYYY`.
  You can also omit the year (e.g. `DD-MM` or `MM-DD`, like `09-28`) — it
  defaults to the current year, or next year if that day has already passed.
- Time accepts `HH:MM`, or `-` to skip.
- Events are one-time only (no yearly recurrence).
- Past events are deleted automatically (checked whenever you use a command,
  and on every `/tick`), and remaining event IDs are compacted back down so
  they stay small.
- Event listings show the day of the week, e.g. `2026-09-25 (Fri)`.
- In polling mode (`RUN_MODE=polling`) there's no `/tick` endpoint, so the
  bot runs the same reminder check itself once a minute in a background thread.
