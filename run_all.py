"""
این فایل هم ربات تلگرام (polling) و هم سرور Mini App (FastAPI/uvicorn) رو
با هم، توی یک پروسه، اجرا می‌کنه -- مناسب برای میزبانی روی یک Replit واحد.

اجرا: python run_all.py
"""
import threading
import uvicorn

from config import WEBAPP_PORT
import backup
import bot as bot_module


def run_webapp():
    import webapp_api
    uvicorn.run(webapp_api.app, host="0.0.0.0", port=WEBAPP_PORT)


if __name__ == "__main__":
    # اگه دیتابیس خالیه (مثلاً Render سرور رو ری‌استارت کرده)، از پشتیبان تلگرام برمی‌گردونیم
    backup.restore_if_needed()

    # سرور Mini App توی یک ترد جدا اجرا می‌شه
    threading.Thread(target=run_webapp, daemon=True).start()

    # ربات تلگرام (polling) توی ترد اصلی اجرا می‌شه
    application = bot_module.build_application()
    print("✅ ربات و سرور Mini App هر دو در حال اجرا هستند...")
    application.run_polling(allowed_updates=bot_module.Update.ALL_TYPES)
