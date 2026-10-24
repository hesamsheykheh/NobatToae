"""
ربات حداقلی (Thin Bot).
این ربات دیگه هیچ منوی دکمه‌ای یا جریان مکالمه‌ای نداره -- همه‌چیز (رزرو، پنل ادمین،
ساخت کسب‌وکار) داخل Mini App (webapp/index.html + webapp_api.py) انجام می‌شه.

کاری که این فایل انجام می‌ده:
1. /start -> باز کردن Mini App (با پاس دادن اسلاگ کسب‌وکار در صورت وجود لینک اختصاصی)
2. /claimadmin <code> -> پذیرش کد دعوت ادمین
3. دریافت امتیاز ۱ تا ۵ ستاره بعد از نوبت (پوش نوتیفیکیشن از jobs.py) + دریافت نظر متنی اختیاری
4. اجرای جاب‌های پس‌زمینه (یادآوری، تکمیل خودکار نوبت، درخواست نظر) از jobs.py
"""
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

import database as db
import backup
from config import BACKUP_CHAT_ID
from config import BOT_TOKEN, MINI_APP_URL

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def webapp_button(text, url):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, web_app=WebAppInfo(url=url))]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    base = MINI_APP_URL.rstrip("/")

    if args:
        slug = args[0]
        business = db.get_business_by_slug(slug)
        if not business:
            await update.message.reply_text("⚠️ این لینک رزرو معتبر نیست یا کسب‌وکار غیرفعال شده.")
            return
        # یادمون می‌مونه این کاربر با لینک کدوم کسب‌وکار اومده؛ دکمه‌ی Open کنار کادر پیام ازش استفاده می‌کنه
        db.set_user_context(update.effective_user.id, business["id"])
        await update.message.reply_text(
            f"👋 سلام {update.effective_user.first_name} عزیز!\nبرای رزرو نوبت از «{business['name']}» دکمه زیر رو بزنید:",
            reply_markup=webapp_button("📅 باز کردن اپ رزرو", f"{base}/?biz={slug}"),
        )
        return

    await update.message.reply_text(
        "👋 سلام! دکمه‌ی «ورود» (کنار کادر پیام) یا دکمه‌ی زیر رو بزنید.\n\n"
        "• اگه کسب‌وکار دارید، مستقیم وارد پنل مدیریتش می‌شید\n"
        "• اگه با لینک یه کسب‌وکار اومدید، صفحه‌ی همون کسب‌وکار باز می‌شه\n"
        "• و می‌تونید همین‌جا کسب‌وکار خودتون رو هم بسازید",
        reply_markup=webapp_button("🌐 ورود", base + "/"),
    )


async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BACKUP_CHAT_ID or update.effective_user.id != BACKUP_CHAT_ID:
        await update.message.reply_text("⛔️ این دستور فقط برای مدیر اصلی ربات فعاله.")
        return
    sent = await backup.make_backup(context.bot, force=True)
    await update.message.reply_text("✅ پشتیبان ساخته و پین شد." if sent else "ℹ️ پشتیبان‌گیری ممکن نیست (دیتابیس پیدا نشد).")


async def claim_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("لطفاً کد دعوت رو هم بنویسید. مثال:\n/claimadmin a1b2c3d4")
        return
    ok, business, message = db.use_admin_invite(context.args[0], update.effective_user.id)
    await update.message.reply_text(message)
    if ok:
        base = MINI_APP_URL.rstrip("/")
        await update.message.reply_text(
            f"حالا می‌تونید پنل «{business['name']}» رو مدیریت کنید:",
            reply_markup=webapp_button("🔧 باز کردن پنل مدیریت", f"{base}/?admin=1&biz={business['id']}"),
        )


# =========================================================
# امتیازدهی و نظر بعد از نوبت (نوتیفیکیشنی که jobs.py می‌فرسته)
# =========================================================

async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, appt_id, rating = query.data.split("_")
    appt_id, rating = int(appt_id), int(rating)
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("این نوبت یافت نشد.")
        return
    db.add_review(appt_id, appt["business_id"], update.effective_user.id, rating)
    context.user_data["awaiting_comment_for"] = {"type": "appointment", "id": appt_id}
    await query.edit_message_text(
        f"🙏 ممنون بابت امتیاز {rating}⭐!\nاگه نظر متنی هم دارید همین‌جا بنویسید (یا این پیام رو نادیده بگیرید)."
    )


async def handle_order_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, order_id, rating = query.data.split("_")
    order_id, rating = int(order_id), int(rating)
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_text("این سفارش یافت نشد.")
        return
    db.add_order_review(order_id, order["business_id"], update.effective_user.id, rating)
    context.user_data["awaiting_comment_for"] = {"type": "order", "id": order_id}
    await query.edit_message_text(
        f"🙏 ممنون بابت امتیاز {rating}⭐!\nاگه نظر متنی هم دارید همین‌جا بنویسید (یا این پیام رو نادیده بگیرید)."
    )


async def maybe_save_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.pop("awaiting_comment_for", None)
    if pending:
        text = update.message.text.strip()
        if pending["type"] == "order":
            db.update_order_review_comment(pending["id"], text)
        else:
            db.update_review_comment(pending["id"], text)
        await update.message.reply_text("🙏 ممنون از نظر شما!")
    # اگه منتظر نظر نبودیم، پیام رو نادیده می‌گیریم (این ربات منوی دیگه‌ای نداره)


# =========================================================
# اجرای ربات
# =========================================================

async def post_init(app):
    """دکمه‌ی منوی کنار کادر پیام (همونی که «Open» نوشته) رو برای همه‌ی کاربرا روی Mini App ست می‌کنه."""
    try:
        base = MINI_APP_URL.rstrip("/")
        if base and base != "https://example.com":
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🌐 ورود", web_app=WebAppInfo(url=base + "/"))
            )
            logger.info("✅ دکمه‌ی منوی ربات روی Mini App ست شد")
    except Exception as e:
        logger.warning(f"ست کردن دکمه‌ی منو ناموفق بود (از BotFather هم می‌تونید دستی بذارید): {e}")


def build_application():
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("claimadmin", claim_admin))
    app.add_handler(CommandHandler("backupnow", backup_now))
    app.add_handler(CallbackQueryHandler(handle_rating, pattern="^rate_"))
    app.add_handler(CallbackQueryHandler(handle_order_rating, pattern="^rateo_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, maybe_save_comment))

    import jobs
    jobs.register(app)

    return app


def main():
    app = build_application()
    logger.info("✅ ربات حداقلی (فقط لانچر Mini App + نوتیفیکیشن‌ها) در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
