import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import database as db
import backup
from config import REMINDER_CHECK_INTERVAL_SECONDS, REMINDER_24H_WINDOW_MINUTES, REMINDER_1H_WINDOW_MINUTES, LOYALTY_TOMAN_PER_POINT

logger = logging.getLogger(__name__)


def rating_keyboard(appointment_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{i}⭐", callback_data=f"rate_{appointment_id}_{i}") for i in range(1, 6)
    ]])


def order_rating_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{i}⭐", callback_data=f"rateo_{order_id}_{i}") for i in range(1, 6)
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


def _order_datetime(order):
    # اگه متخصص داشته و end_time ست شده، همون آخر وقت ملاک تکمیل‌شدنه؛ وگرنه خود لحظه‌ی تحویل
    t = order["end_time"] or order["delivery_time"]
    return datetime.strptime(f"{order['delivery_date']} {t}", "%Y-%m-%d %H:%M")


async def complete_past_orders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for order in db.get_completable_orders():
        try:
            order_dt = _order_datetime(order)
        except ValueError:
            continue
        if order_dt < now:
            db.mark_order_completed(order["id"])
            if order["customer_id"] and order["total_price"] is not None:
                points = order["total_price"] // LOYALTY_TOMAN_PER_POINT
                if points > 0:
                    db.add_loyalty_points(order["customer_id"], points)


async def prompt_order_reviews(context: ContextTypes.DEFAULT_TYPE):
    for order in db.get_pending_review_orders():
        items = db.get_order_items(order["id"])
        items_txt = "، ".join(f"{i['product_name']} ×{i['quantity']}" for i in items) or "سفارش شما"
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"⭐ سفارش شما ({items_txt}) در {order['business_name']} تحویل داده شد.\nلطفاً امتیاز بدید:",
                reply_markup=order_rating_keyboard(order["id"]),
            )
        except Exception as e:
            logger.warning(f"order review prompt failed for {order['id']}: {e}")
        db.mark_order_review_prompted(order["id"])


async def send_order_reminders(context: ContextTypes.DEFAULT_TYPE):
    """یادآوری ۲۴ ساعته و ۱ ساعته برای سفارش‌های زمان‌دار (سفارش‌های بدون تاریخ/ساعت یادآوری ندارن)."""
    now = datetime.now()
    for o in db.get_order_reminder_candidates():
        try:
            dt = datetime.strptime(f"{o['delivery_date']} {o['delivery_time']}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        delta = (dt - now).total_seconds() / 60
        items = db.get_order_items(o["id"])
        items_txt = "، ".join(f"{i['product_name']} ×{i['quantity']}" for i in items) or "سفارش شما"
        who = f" با {o['staff_name']}" if o["staff_name"] else ""

        if not o["reminder_24h_sent"] and abs(delta - 24 * 60) <= REMINDER_24H_WINDOW_MINUTES:
            try:
                await context.bot.send_message(
                    chat_id=o["user_id"],
                    text=f"🔔 یادآوری: فردا ساعت {o['delivery_time']} سفارش شما ({items_txt}){who} در {o['business_name']} است.",
                )
            except Exception as e:
                logger.warning(f"order reminder 24h failed for {o['id']}: {e}")
            db.mark_order_reminder_sent(o["id"], "24h")

        if not o["reminder_1h_sent"] and abs(delta - 60) <= REMINDER_1H_WINDOW_MINUTES:
            try:
                await context.bot.send_message(
                    chat_id=o["user_id"],
                    text=f"⏰ یک ساعت دیگر سفارش شما ({items_txt}){who} در {o['business_name']} است.",
                )
            except Exception as e:
                logger.warning(f"order reminder 1h failed for {o['id']}: {e}")
            db.mark_order_reminder_sent(o["id"], "1h")


async def run_all_jobs(context: ContextTypes.DEFAULT_TYPE):
    await send_order_reminders(context)
    await send_reminders(context)
    await complete_past_appointments(context)
    await prompt_reviews(context)
    await complete_past_orders(context)
    await prompt_order_reviews(context)


async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if await backup.make_backup(context.bot):
            logger.info("🗄 پشتیبان جدید به تلگرام فرستاده شد")
    except Exception as e:
        logger.warning(f"پشتیبان‌گیری ناموفق بود: {e}")


def register(app):
    app.job_queue.run_repeating(run_all_jobs, interval=REMINDER_CHECK_INTERVAL_SECONDS, first=10)
    # هر ۶۰ ثانیه چک می‌کنه؛ فقط اگه دیتابیس تغییر کرده باشه پشتیبان می‌فرسته
    app.job_queue.run_repeating(backup_job, interval=60, first=20)
