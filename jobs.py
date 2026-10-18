import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import database as db
from config import REMINDER_CHECK_INTERVAL_SECONDS, REMINDER_24H_WINDOW_MINUTES, REMINDER_1H_WINDOW_MINUTES, LOYALTY_TOMAN_PER_POINT

logger = logging.getLogger(__name__)


def rating_keyboard(appointment_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{i}⭐", callback_data=f"rate_{appointment_id}_{i}") for i in range(1, 6)
    ]])


def _appt_datetime(appt):
    return datetime.strptime(f"{appt['date']} {appt['start_time']}", "%Y-%m-%d %H:%M")


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for appt in db.get_due_reminder_candidates():
        try:
            appt_dt = _appt_datetime(appt)
        except ValueError:
            continue
        delta_minutes = (appt_dt - now).total_seconds() / 60

        if not appt["reminder_24h_sent"] and abs(delta_minutes - 24 * 60) <= REMINDER_24H_WINDOW_MINUTES:
            try:
                await context.bot.send_message(
                    chat_id=appt["user_id"],
                    text=(
                        f"🔔 یادآوری: فردا ساعت {appt['start_time']} نوبت شما برای "
                        f"«{appt['service_name']}» با {appt['staff_name']} در {appt['business_name']} است."
                    ),
                )
            except Exception as e:
                logger.warning(f"reminder 24h failed for {appt['id']}: {e}")
            db.mark_reminder_sent(appt["id"], "24h")

        if not appt["reminder_1h_sent"] and abs(delta_minutes - 60) <= REMINDER_1H_WINDOW_MINUTES:
            try:
                await context.bot.send_message(
                    chat_id=appt["user_id"],
                    text=(
                        f"⏰ یک ساعت دیگر نوبت شما برای «{appt['service_name']}» "
                        f"با {appt['staff_name']} در {appt['business_name']} است."
                    ),
                )
            except Exception as e:
                logger.warning(f"reminder 1h failed for {appt['id']}: {e}")
            db.mark_reminder_sent(appt["id"], "1h")


async def complete_past_appointments(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for appt in db.get_completable_appointments():
        try:
            end_dt = datetime.strptime(f"{appt['date']} {appt['end_time']}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if end_dt < now:
            db.mark_completed(appt["id"])
            if appt["customer_id"] and appt["final_price"] is not None:
                points = appt["final_price"] // LOYALTY_TOMAN_PER_POINT
                if points > 0:
                    db.add_loyalty_points(appt["customer_id"], points)


async def prompt_reviews(context: ContextTypes.DEFAULT_TYPE):
    for appt in db.get_pending_review_appointments():
        try:
            await context.bot.send_message(
                chat_id=appt["user_id"],
                text=f"⭐ نوبت شما برای «{appt['service_name']}» در {appt['business_name']} انجام شد.\nلطفاً به این نوبت امتیاز بدید:",
                reply_markup=rating_keyboard(appt["id"]),
            )
        except Exception as e:
            logger.warning(f"review prompt failed for {appt['id']}: {e}")
        db.mark_review_prompted(appt["id"])


async def run_all_jobs(context: ContextTypes.DEFAULT_TYPE):
    await send_reminders(context)
    await complete_past_appointments(context)
    await prompt_reviews(context)


def register(app):
    app.job_queue.run_repeating(run_all_jobs, interval=REMINDER_CHECK_INTERVAL_SECONDS, first=10)
