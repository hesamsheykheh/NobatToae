"""
پشتیبان‌گیری خودکار دیتابیس از طریق تلگرام.

چرا لازمه؟  روی Render رایگان، فایل‌های سرور (از جمله دیتابیس SQLite) هر بار که سرور
ری‌استارت، دیپلوی یا خاموش بشه پاک می‌شن. این ماژول:

  ۱) هر بار که دیتابیس تغییر کنه، یه نسخه‌ی کامل از اون رو توی چت خصوصی شما می‌فرسته و «پین» می‌کنه
  ۲) موقع بالا اومدن سرور، اگه دیتابیس خالی بود، آخرین نسخه‌ی پین‌شده رو خودکار برمی‌گردونه

تنظیم: متغیر BACKUP_CHAT_ID (آیدی عددی تلگرام شما). اگه ست نشه، پشتیبان‌گیری غیرفعاله.
"""
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import urllib.request

from config import BOT_TOKEN, DB_PATH, BACKUP_CHAT_ID

logger = logging.getLogger(__name__)

BACKUP_FILENAME = "salon_backup.db"
_last_hash = None      # هش آخرین نسخه‌ای که فرستادیم (تا بی‌دلیل تکراری نفرستیم)
_last_message_id = None  # پیام پشتیبان قبلی، تا بعد از ارسال نسخه‌ی جدید پاک بشه


# ---------------------------------------------------------------- تماس با API تلگرام
def _api(method, params=None, timeout=30):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(params or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "telegram error"))
    return data["result"]


def _download(file_path, dest, timeout=60):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as out:
        out.write(resp.read())


# ---------------------------------------------------------------- ابزارهای دیتابیس
def db_has_data(path=None):
    """آیا دیتابیس وجود داره و حداقل یک کسب‌وکار توش ثبت شده؟"""
    path = path or DB_PATH
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _is_valid_backup(path):
    try:
        conn = sqlite3.connect(path)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.execute("SELECT COUNT(*) FROM businesses").fetchone()
            return ok
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def snapshot(path=None):
    """یه کپی سازگار (حتی وسط نوشتن) از دیتابیس می‌گیره و مسیر فایل موقتش رو برمی‌گردونه."""
    path = path or DB_PATH
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return tmp


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- بازیابی هنگام راه‌اندازی
def restore_if_needed():
    """اگه دیتابیس خالیه و پشتیبانی توی تلگرام هست، برمی‌گردونه. True یعنی بازیابی شد."""
    if not BACKUP_CHAT_ID:
        logger.warning("⚠️ BACKUP_CHAT_ID ست نشده؛ پشتیبان‌گیری فعال نیست و با هر ری‌استارت سرور، اطلاعات پاک می‌شه!")
        return False
    if db_has_data():
        logger.info("دیتابیس موجوده؛ بازیابی لازم نیست.")
        return False
    try:
        chat = _api("getChat", {"chat_id": BACKUP_CHAT_ID})
        doc = (chat.get("pinned_message") or {}).get("document")
        if not doc or not str(doc.get("file_name", "")).startswith("salon_backup"):
            logger.info("پشتیبانی توی تلگرام پیدا نشد (اولین اجراست؟).")
            return False
        info = _api("getFile", {"file_id": doc["file_id"]})
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        _download(info["file_path"], tmp)
        if not _is_valid_backup(tmp):
            logger.error("فایل پشتیبان خراب بود؛ بازیابی انجام نشد.")
            os.remove(tmp)
            return False
        os.replace(tmp, DB_PATH)
        logger.info("✅ دیتابیس از پشتیبان تلگرام بازیابی شد.")
        return True
    except Exception as e:
        logger.error(f"بازیابی ناموفق بود: {e}")
        return False


# ---------------------------------------------------------------- ساخت پشتیبان جدید
async def make_backup(bot, force=False):
    """اگه دیتابیس نسبت به آخرین پشتیبان تغییر کرده باشه (یا force)، نسخه‌ی جدید می‌فرسته. True یعنی فرستاده شد."""
    global _last_hash, _last_message_id
    if not BACKUP_CHAT_ID or not os.path.exists(DB_PATH):
        return False
    tmp = snapshot()
    try:
        digest = _sha256(tmp)
        if digest == _last_hash and not force:
            return False
        with open(tmp, "rb") as f:
            msg = await bot.send_document(
                chat_id=BACKUP_CHAT_ID, document=f, filename=BACKUP_FILENAME,
                caption="🗄 پشتیبان خودکار دیتابیس (این پیام رو پاک نکنید؛ برای بازیابی استفاده می‌شه)",
                disable_notification=True,
            )
        try:
            await bot.pin_chat_message(chat_id=BACKUP_CHAT_ID, message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            logger.warning(f"پین کردن پشتیبان ناموفق بود: {e}")
        if _last_message_id:
            try:
                await bot.delete_message(chat_id=BACKUP_CHAT_ID, message_id=_last_message_id)
            except Exception:
                pass
        _last_hash, _last_message_id = digest, msg.message_id
        return True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
