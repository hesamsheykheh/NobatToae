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

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

import database as db
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
        await update.message.reply_text(
            f"👋 سلام {update.effective_user.first_name} عزیز!\nبرای رزرو نوبت از «{business['name']}» دکمه زیر رو بزنید:",
            reply_markup=webapp_button("📅 باز کردن اپ رزرو", f"{base}/?biz={slug}"),
        )
        return

    await update.message.reply_text(
        "👋 سلام! برای رزرو نوبت باید از لینک اختصاصی یک کسب‌وکار وارد بشید.\n\n"
        "اگه می‌خواید خودتون کسب‌وکار جدیدی (مثل سالن زیبایی) راه‌اندازی کنید، دکمه زیر رو بزنید:",
        reply_markup=webapp_button("🌐 باز کردن اپ", base + "/"),
    )


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
    context.user_data["awaiting_comment_for"] = appt_id
    await query.edit_message_text(
        f"🙏 ممنون بابت امتیاز {rating}⭐!\nاگه نظر متنی هم دارید همین‌جا بنویسید (یا این پیام رو نادیده بگیرید)."
    )


async def maybe_save_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    appt_id = context.user_data.pop("awaiting_comment_for", None)
    if appt_id:
        db.update_review_comment(appt_id, update.message.text.strip())
        await update.message.reply_text("🙏 ممنون از نظر شما!")
    # اگه منتظر نظر نبودیم، پیام رو نادیده می‌گیریم (این ربات منوی دیگه‌ای نداره)


# =========================================================
# اجرای ربات
# =========================================================

def build_application():
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("claimadmin", claim_admin))
    app.add_handler(CallbackQueryHandler(handle_rating, pattern="^rate_"))
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
