import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")  # بدون @ - برای ساخت لینک اشتراک‌گذاری

# آیدی عددی تلگرام شما (مالک/توسعه‌دهنده ربات) — پیام‌های صاحبان کسب‌وکار به اینجا ارسال می‌شه
# برای گرفتن آیدی خودتون به @userinfobot پیام بدید
PLATFORM_OWNER_ID = int(os.getenv("PLATFORM_OWNER_ID", "0")) or None

# ---------- Mini App (Telegram Web App) ----------
# آدرس HTTPS که فرانت‌اند Mini App روش میزبانی می‌شه (همون سروری که webapp_api.py رو اجرا می‌کنید)
# مثال بعد از دیپلوی روی Replit: https://your-repl-name.your-username.repl.co
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://example.com")

# پورتی که سرور API/وب روش اجرا می‌شه (Replit خودش از طریق PORT ست می‌کنه)
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))

DB_PATH = os.getenv("DB_PATH", "salon.db")

# آیدی عددی تلگرام شما؛ پشتیبان دیتابیس توی این چت فرستاده و پین می‌شه (و موقع ری‌استارت سرور از همین‌جا برمی‌گرده).
# روی Render رایگان حتماً ست کنید، وگرنه با هر ری‌استارت همه‌ی اطلاعات پاک می‌شه.
BACKUP_CHAT_ID = int(os.getenv("BACKUP_CHAT_ID", "0")) or PLATFORM_OWNER_ID

# ساعات کاری پیش‌فرض (اگر کسب‌وکاری برنامه کاری اختصاصی تنظیم نکرده باشد)
DEFAULT_WORK_START_HOUR = 9
DEFAULT_WORK_END_HOUR = 20

SLOT_STEP_MINUTES = 30
BOOKING_DAYS_AHEAD = 10

# --- باشگاه مشتریان ---
# به ازای هر چند تومان خرید، ۱ امتیاز وفاداری تعلق می‌گیرد
LOYALTY_TOMAN_PER_POINT = 10000
# ارزش هر امتیاز هنگام استفاده (به تومان تخفیف)
LOYALTY_POINT_VALUE_TOMAN = 1000
# حداقل امتیاز لازم برای استفاده
LOYALTY_MIN_REDEEM = 10

# --- یادآوری ---
REMINDER_CHECK_INTERVAL_SECONDS = 300  # هر ۵ دقیقه بررسی شود
REMINDER_24H_WINDOW_MINUTES = 10  # بازه تلورانس حول ۲۴ ساعت قبل
REMINDER_1H_WINDOW_MINUTES = 10   # بازه تلورانس حول ۱ ساعت قبل
