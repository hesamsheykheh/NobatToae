import sqlite3
import secrets
from datetime import datetime, timedelta
from contextlib import contextmanager

from config import DB_PATH, DEFAULT_WORK_START_HOUR, DEFAULT_WORK_END_HOUR, SLOT_STEP_MINUTES


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS businesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS business_admins (
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL,
                PRIMARY KEY (business_id, telegram_id)
            );

            CREATE TABLE IF NOT EXISTS user_context (
                telegram_id INTEGER PRIMARY KEY,
                business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,
                language TEXT NOT NULL DEFAULT 'fa'
            );

            CREATE TABLE IF NOT EXISTS admin_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                code TEXT UNIQUE NOT NULL,
                created_by INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                used_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS staff_schedule (
                staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL,
                start_time TEXT,
                end_time TEXT,
                is_closed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (staff_id, weekday)
            );

            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                price INTEGER,  -- NULL یعنی بدون قیمت ثابت (مثلاً برای مشاوره یا کارهای وابسته به نرخ ارز)
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS staff_services (
                staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
                service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                PRIMARY KEY (staff_id, service_id)
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL,
                name TEXT,
                phone TEXT,
                loyalty_points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(business_id, telegram_id)
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                customer_id INTEGER REFERENCES customers(id),
                user_id INTEGER NOT NULL,
                user_name TEXT,
                user_phone TEXT,
                service_id INTEGER NOT NULL REFERENCES services(id),
                staff_id INTEGER NOT NULL REFERENCES staff(id),
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                base_price INTEGER,   -- NULL یعنی خدمت قیمت ثابت نداشته
                final_price INTEGER,
                discount_code TEXT,
                points_redeemed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'confirmed',
                reminder_24h_sent INTEGER NOT NULL DEFAULT 0,
                reminder_1h_sent INTEGER NOT NULL DEFAULT 0,
                review_prompted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                reason TEXT,
                UNIQUE(business_id, date)
            );

            CREATE TABLE IF NOT EXISTS schedule (
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL,  -- 0=دوشنبه ... 6=یکشنبه (پایتون weekday())
                start_time TEXT,
                end_time TEXT,
                is_closed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (business_id, weekday)
            );

            CREATE TABLE IF NOT EXISTS discount_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                discount_type TEXT NOT NULL,  -- 'percent' یا 'fixed'
                value INTEGER NOT NULL,
                expiry_date TEXT,
                usage_limit INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(business_id, code)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL,
                user_name TEXT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
                telegram_id INTEGER NOT NULL,
                user_name TEXT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER UNIQUE NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
                business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


# =========================================================
# کسب‌وکارها
# =========================================================

def create_business(name, owner_id):
    slug = secrets.token_hex(3)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO businesses (slug, name, owner_id, created_at) VALUES (?, ?, ?, ?)",
            (slug, name, owner_id, datetime.now().isoformat()),
        )
        biz_id = cur.lastrowid
        conn.execute(
            "INSERT INTO business_admins (business_id, telegram_id) VALUES (?, ?)",
            (biz_id, owner_id),
        )
        return biz_id, slug


def get_business_by_slug(slug):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM businesses WHERE slug = ? AND active = 1", (slug,)).fetchone()


def get_business(business_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()


def get_businesses_for_admin(telegram_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT b.* FROM businesses b
            JOIN business_admins ba ON ba.business_id = b.id
            WHERE ba.telegram_id = ? AND b.active = 1
            ORDER BY b.name
            """,
            (telegram_id,),
        ).fetchall()


def is_business_admin(business_id, telegram_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM business_admins WHERE business_id = ? AND telegram_id = ?",
            (business_id, telegram_id),
        ).fetchone()
        return row is not None


def add_business_admin(business_id, telegram_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO business_admins (business_id, telegram_id) VALUES (?, ?)",
            (business_id, telegram_id),
        )


def get_admin_list(business_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id FROM business_admins WHERE business_id = ?", (business_id,)
        ).fetchall()


def remove_business_admin(business_id, telegram_id):
    """ادمین را حذف می‌کند؛ هرگز مالک اصلی کسب‌وکار را حذف نمی‌کند."""
    business = get_business(business_id)
    if business and business["owner_id"] == telegram_id:
        return False
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM business_admins WHERE business_id = ? AND telegram_id = ?", (business_id, telegram_id)
        )
    return True


def create_admin_invite(business_id, created_by):
    code = secrets.token_hex(4)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_invites (business_id, code, created_by, created_at) VALUES (?, ?, ?, ?)",
            (business_id, code, created_by, datetime.now().isoformat()),
        )
    return code


def get_admin_invite(code):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admin_invites WHERE code = ?", (code,)).fetchone()


def use_admin_invite(code, telegram_id):
    """اگر کد معتبر و استفاده‌نشده باشد، کاربر را ادمین می‌کند. خروجی: (ok, business_row یا None, پیام خطا)"""
    invite = get_admin_invite(code)
    if not invite:
        return False, None, "❌ کد دعوت نامعتبر است."
    if invite["used"]:
        return False, None, "❌ این کد دعوت قبلاً استفاده شده."
    business = get_business(invite["business_id"])
    if not business:
        return False, None, "❌ کسب‌وکار مربوطه یافت نشد."
    add_business_admin(business["id"], telegram_id)
    with get_conn() as conn:
        conn.execute(
            "UPDATE admin_invites SET used = 1, used_by = ? WHERE code = ?", (telegram_id, code)
        )
    return True, business, "✅ شما با موفقیت ادمین این کسب‌وکار شدید."


# ---------- کانتکست فعال کاربر (آخرین کسب‌وکاری که وارد شده) ----------

def set_user_context(telegram_id, business_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_context (telegram_id, business_id) VALUES (?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET business_id = excluded.business_id",
            (telegram_id, business_id),
        )


def get_user_context(telegram_id):
    with get_conn() as conn:
        row = conn.execute("SELECT business_id FROM user_context WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return row["business_id"] if row else None


# =========================================================
# خدمات
# =========================================================

def add_service(business_id, name, duration_minutes, price=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO services (business_id, name, duration_minutes, price) VALUES (?, ?, ?, ?)",
            (business_id, name, duration_minutes, price),
        )
        return cur.lastrowid


def get_services(business_id, active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM services WHERE business_id = ?"
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY name"
        return conn.execute(q, (business_id,)).fetchall()


def get_service(service_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def deactivate_service(service_id):
    with get_conn() as conn:
        conn.execute("UPDATE services SET active = 0 WHERE id = ?", (service_id,))


def reactivate_service(service_id):
    with get_conn() as conn:
        conn.execute("UPDATE services SET active = 1 WHERE id = ?", (service_id,))


def update_service(service_id, name, duration_minutes, price=None):
    """price می‌تواند None باشد (یعنی بدون قیمت ثابت)؛ هر سه مقدار همیشه صریح جایگزین می‌شوند."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE services SET name = ?, duration_minutes = ?, price = ? WHERE id = ?",
            (name, duration_minutes, price, service_id),
        )


# =========================================================
# متخصص‌ها
# =========================================================

def add_staff(business_id, name):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO staff (business_id, name) VALUES (?, ?)", (business_id, name))
        return cur.lastrowid


def get_staff_list(business_id, active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM staff WHERE business_id = ?"
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY name"
        return conn.execute(q, (business_id,)).fetchall()


def get_staff_member(staff_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()


def deactivate_staff(staff_id):
    with get_conn() as conn:
        conn.execute("UPDATE staff SET active = 0 WHERE id = ?", (staff_id,))


def reactivate_staff(staff_id):
    with get_conn() as conn:
        conn.execute("UPDATE staff SET active = 1 WHERE id = ?", (staff_id,))


def update_staff_name(staff_id, name):
    with get_conn() as conn:
        conn.execute("UPDATE staff SET name = ? WHERE id = ?", (name, staff_id))


def unlink_staff_service(staff_id, service_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM staff_services WHERE staff_id = ? AND service_id = ?", (staff_id, service_id)
        )


def link_staff_service(staff_id, service_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO staff_services (staff_id, service_id) VALUES (?, ?)",
            (staff_id, service_id),
        )


def get_staff_for_service(service_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT s.* FROM staff s
            JOIN staff_services ss ON ss.staff_id = s.id
            WHERE ss.service_id = ? AND s.active = 1
            ORDER BY s.name
            """,
            (service_id,),
        ).fetchall()


# =========================================================
# برنامه کاری هفتگی و تعطیلات
# =========================================================

def set_schedule(business_id, weekday, start_time=None, end_time=None, is_closed=False):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO schedule (business_id, weekday, start_time, end_time, is_closed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(business_id, weekday) DO UPDATE SET
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                is_closed = excluded.is_closed
            """,
            (business_id, weekday, start_time, end_time, 1 if is_closed else 0),
        )


def get_schedule(business_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM schedule WHERE business_id = ? ORDER BY weekday", (business_id,)
        ).fetchall()


def get_day_hours(business_id, weekday):
    """ساعت کاری یک روز هفته را برمی‌گرداند: (start_min, end_min) یا None اگر تعطیل باشد."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM schedule WHERE business_id = ? AND weekday = ?", (business_id, weekday)
        ).fetchone()
    if row:
        if row["is_closed"]:
            return None
        return _time_to_minutes(row["start_time"]), _time_to_minutes(row["end_time"])
    # اگر تنظیم نشده، پیش‌فرض کل هفته باز است
    return DEFAULT_WORK_START_HOUR * 60, DEFAULT_WORK_END_HOUR * 60


def set_staff_schedule(staff_id, weekday, start_time=None, end_time=None, is_closed=False):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO staff_schedule (staff_id, weekday, start_time, end_time, is_closed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(staff_id, weekday) DO UPDATE SET
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                is_closed = excluded.is_closed
            """,
            (staff_id, weekday, start_time, end_time, 1 if is_closed else 0),
        )


def clear_staff_schedule(staff_id, weekday):
    """حذف تنظیم اختصاصی یک روز، تا دوباره از برنامه کاری کلی کسب‌وکار پیروی کند."""
    with get_conn() as conn:
        conn.execute("DELETE FROM staff_schedule WHERE staff_id = ? AND weekday = ?", (staff_id, weekday))


def get_staff_schedule(staff_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM staff_schedule WHERE staff_id = ? ORDER BY weekday", (staff_id,)
        ).fetchall()


def get_staff_day_hours(business_id, staff_id, weekday):
    """ساعت کاری یک متخصص در یک روز هفته: ابتدا تنظیم اختصاصی متخصص، وگرنه برنامه کلی کسب‌وکار."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM staff_schedule WHERE staff_id = ? AND weekday = ?", (staff_id, weekday)
        ).fetchone()
    if row:
        if row["is_closed"]:
            return None
        return _time_to_minutes(row["start_time"]), _time_to_minutes(row["end_time"])
    return get_day_hours(business_id, weekday)


def add_holiday(business_id, date_str, reason=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO holidays (business_id, date, reason) VALUES (?, ?, ?)",
            (business_id, date_str, reason),
        )


def get_holiday(holiday_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM holidays WHERE id = ?", (holiday_id,)).fetchone()


def remove_holiday(holiday_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))


def get_holidays(business_id, upcoming_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM holidays WHERE business_id = ?"
        params = [business_id]
        if upcoming_only:
            q += " AND date >= ?"
            params.append(datetime.now().strftime("%Y-%m-%d"))
        q += " ORDER BY date"
        return conn.execute(q, params).fetchall()


def is_holiday(business_id, date_str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM holidays WHERE business_id = ? AND date = ?", (business_id, date_str)
        ).fetchone()
        return row is not None


# =========================================================
# نوبت‌ها و اسلات‌های خالی
# =========================================================

def _time_to_minutes(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _minutes_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def get_available_slots(business_id, staff_id, date_str, duration_minutes):
    if is_holiday(business_id, date_str):
        return []

    weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    hours = get_staff_day_hours(business_id, staff_id, weekday)
    if hours is None:
        return []
    start_min, end_min = hours

    with get_conn() as conn:
        booked = conn.execute(
            """
            SELECT start_time, end_time FROM appointments
            WHERE staff_id = ? AND date = ? AND status IN ('confirmed', 'completed')
            ORDER BY start_time
            """,
            (staff_id, date_str),
        ).fetchall()

    busy_ranges = [(_time_to_minutes(r["start_time"]), _time_to_minutes(r["end_time"])) for r in booked]

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_min = datetime.now().hour * 60 + datetime.now().minute
    if date_str == today_str:
        start_min = max(start_min, ((now_min // SLOT_STEP_MINUTES) + 1) * SLOT_STEP_MINUTES)

    slots = []
    cursor = start_min
    while cursor + duration_minutes <= end_min:
        slot_end = cursor + duration_minutes
        overlap = any(cursor < b_end and slot_end > b_start for b_start, b_end in busy_ranges)
        if not overlap:
            slots.append(_minutes_to_time(cursor))
        cursor += SLOT_STEP_MINUTES

    return slots


def create_appointment(business_id, customer_id, user_id, user_name, user_phone, service_id, staff_id,
                        date_str, start_time, duration_minutes, base_price, final_price,
                        discount_code=None, points_redeemed=0):
    end_time = _minutes_to_time(_time_to_minutes(start_time) + duration_minutes)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO appointments
                (business_id, customer_id, user_id, user_name, user_phone, service_id, staff_id,
                 date, start_time, end_time, base_price, final_price, discount_code, points_redeemed,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
            """,
            (
                business_id, customer_id, user_id, user_name, user_phone, service_id, staff_id,
                date_str, start_time, end_time, base_price, final_price, discount_code, points_redeemed,
                datetime.now().isoformat(),
            ),
        )
        return cur.lastrowid


def get_user_upcoming_appointments(business_id, user_id):
    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id = ? AND a.user_id = ? AND a.status = 'confirmed' AND a.date >= ?
            ORDER BY a.date, a.start_time
            """,
            (business_id, user_id, today_str),
        ).fetchall()


def get_user_past_appointments(business_id, user_id, limit=10):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id = ? AND a.user_id = ? AND a.status IN ('completed', 'cancelled')
            ORDER BY a.date DESC, a.start_time DESC
            LIMIT ?
            """,
            (business_id, user_id, limit),
        ).fetchall()


def cancel_appointment(appointment_id, user_id=None):
    with get_conn() as conn:
        if user_id is not None:
            conn.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE id = ? AND user_id = ?",
                (appointment_id, user_id),
            )
        else:
            conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))


def get_appointments_by_date(business_id, date_str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id = ? AND a.date = ? AND a.status IN ('confirmed', 'completed')
            ORDER BY a.start_time
            """,
            (business_id, date_str),
        ).fetchall()


def get_all_upcoming_appointments(business_id):
    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id = ? AND a.date >= ? AND a.status = 'confirmed'
            ORDER BY a.date, a.start_time
            """,
            (business_id, today_str),
        ).fetchall()


def get_appointment(appointment_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.id = ?
            """,
            (appointment_id,),
        ).fetchone()


def get_all_appointments_for_export(business_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.date, a.start_time, a.end_time, sv.name as service_name, st.name as staff_name,
                   a.user_name, a.user_phone, a.base_price, a.final_price, a.discount_code, a.status
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id = ?
            ORDER BY a.date, a.start_time
            """,
            (business_id,),
        ).fetchall()


def get_due_reminder_candidates(status="confirmed"):
    """تمام نوبت‌های آینده با وضعیت مشخص، برای بررسی یادآوری در جاب."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, st.name as staff_name, b.name as business_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            JOIN businesses b ON b.id = a.business_id
            WHERE a.status = 'confirmed' AND a.date >= ?
            """,
            (today_str,),
        ).fetchall()


def mark_reminder_sent(appointment_id, which):
    col = "reminder_24h_sent" if which == "24h" else "reminder_1h_sent"
    with get_conn() as conn:
        conn.execute(f"UPDATE appointments SET {col} = 1 WHERE id = ?", (appointment_id,))


def get_completable_appointments():
    """نوبت‌های تایید شده‌ای که زمانشان گذشته و باید تکمیل‌شده علامت بخورند."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM appointments WHERE status = 'confirmed'"
        ).fetchall()


def mark_completed(appointment_id):
    with get_conn() as conn:
        conn.execute("UPDATE appointments SET status = 'completed' WHERE id = ?", (appointment_id,))


def get_pending_review_appointments():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.*, sv.name as service_name, b.name as business_name
            FROM appointments a
            JOIN services sv ON sv.id = a.service_id
            JOIN businesses b ON b.id = a.business_id
            WHERE a.status = 'completed' AND a.review_prompted = 0
            """
        ).fetchall()


def mark_review_prompted(appointment_id):
    with get_conn() as conn:
        conn.execute("UPDATE appointments SET review_prompted = 1 WHERE id = ?", (appointment_id,))


# =========================================================
# مشتریان
# =========================================================

def upsert_customer(business_id, telegram_id, name, phone):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO customers (business_id, telegram_id, name, phone, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(business_id, telegram_id) DO UPDATE SET
                name = excluded.name, phone = excluded.phone
            """,
            (business_id, telegram_id, name, phone, datetime.now().isoformat()),
        )
        return conn.execute(
            "SELECT * FROM customers WHERE business_id = ? AND telegram_id = ?", (business_id, telegram_id)
        ).fetchone()


def get_customer(business_id, telegram_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM customers WHERE business_id = ? AND telegram_id = ?", (business_id, telegram_id)
        ).fetchone()


def search_customers(business_id, query):
    like = f"%{query}%"
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM customers WHERE business_id = ? AND (name LIKE ? OR phone LIKE ?)
            ORDER BY name LIMIT 20
            """,
            (business_id, like, like),
        ).fetchall()


def add_loyalty_points(customer_id, points):
    with get_conn() as conn:
        conn.execute(
            "UPDATE customers SET loyalty_points = loyalty_points + ? WHERE id = ?", (points, customer_id)
        )


def redeem_loyalty_points(customer_id, points):
    with get_conn() as conn:
        conn.execute(
            "UPDATE customers SET loyalty_points = MAX(0, loyalty_points - ?) WHERE id = ?",
            (points, customer_id),
        )


# =========================================================
# کدهای تخفیف
# =========================================================

def create_discount_code(business_id, code, discount_type, value, expiry_date=None, usage_limit=None):
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO discount_codes (business_id, code, discount_type, value, expiry_date, usage_limit)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (business_id, code.upper(), discount_type, value, expiry_date, usage_limit),
        )
        return cur.lastrowid


def get_discount_code(business_id, code):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM discount_codes WHERE business_id = ? AND code = ? AND active = 1",
            (business_id, code.upper()),
        ).fetchone()


def get_discount_codes(business_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM discount_codes WHERE business_id = ? ORDER BY id DESC", (business_id,)
        ).fetchall()


def increment_discount_usage(code_id):
    with get_conn() as conn:
        conn.execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE id = ?", (code_id,))


def validate_discount(business_id, code, base_price):
    """کد تخفیف را بررسی می‌کند. خروجی: (ok: bool, final_price: int|None, message: str, code_row)"""
    if base_price is None:
        return False, None, "❌ این خدمت قیمت ثابت ندارد، امکان اعمال کد تخفیف نیست.", None

    row = get_discount_code(business_id, code)
    if not row:
        return False, base_price, "❌ کد تخفیف نامعتبر است.", None

    if row["expiry_date"]:
        if datetime.now().strftime("%Y-%m-%d") > row["expiry_date"]:
            return False, base_price, "❌ این کد تخفیف منقضی شده.", None

    if row["usage_limit"] is not None and row["used_count"] >= row["usage_limit"]:
        return False, base_price, "❌ ظرفیت استفاده از این کد تمام شده.", None

    if row["discount_type"] == "percent":
        final_price = int(base_price * (100 - row["value"]) / 100)
    else:
        final_price = max(0, base_price - row["value"])

    return True, final_price, f"✅ کد تخفیف اعمال شد.", row


# =========================================================
# نظرات و رضایت مشتری
# =========================================================

def add_review(appointment_id, business_id, telegram_id, rating, comment=None):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO reviews (appointment_id, business_id, telegram_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (appointment_id, business_id, telegram_id, rating, comment, datetime.now().isoformat()),
        )


def update_review_comment(appointment_id, comment):
    with get_conn() as conn:
        conn.execute("UPDATE reviews SET comment = ? WHERE appointment_id = ?", (comment, appointment_id))


def has_review(appointment_id):
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM reviews WHERE appointment_id = ?", (appointment_id,)).fetchone()
        return row is not None


def get_average_rating(business_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT AVG(rating) as avg_rating, COUNT(*) as cnt FROM reviews WHERE business_id = ?",
            (business_id,),
        ).fetchone()
        return row["avg_rating"], row["cnt"]


def get_recent_reviews(business_id, limit=10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reviews WHERE business_id = ? ORDER BY created_at DESC LIMIT ?",
            (business_id, limit),
        ).fetchall()


# =========================================================
# گزارش عملکرد و درآمد
# =========================================================

def add_feedback(business_id, telegram_id, user_name, text):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO feedback (business_id, telegram_id, user_name, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (business_id, telegram_id, user_name, text, datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_feedback_list(business_id, limit=20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM feedback WHERE business_id = ? ORDER BY created_at DESC LIMIT ?",
            (business_id, limit),
        ).fetchall()


def add_platform_feedback(business_id, telegram_id, user_name, text):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO platform_feedback (business_id, telegram_id, user_name, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (business_id, telegram_id, user_name, text, datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_platform_feedback_list(limit=30):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT pf.*, b.name as business_name FROM platform_feedback pf
            LEFT JOIN businesses b ON b.id = pf.business_id
            ORDER BY pf.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_revenue_report(business_id, date_from, date_to):
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt, COALESCE(SUM(final_price), 0) as revenue
            FROM appointments
            WHERE business_id = ? AND status IN ('completed', 'confirmed') AND date BETWEEN ? AND ?
            """,
            (business_id, date_from, date_to),
        ).fetchone()
        by_service = conn.execute(
            """
            SELECT sv.name, COUNT(*) as cnt, COALESCE(SUM(a.final_price), 0) as revenue
            FROM appointments a JOIN services sv ON sv.id = a.service_id
            WHERE a.business_id = ? AND a.status IN ('completed', 'confirmed') AND a.date BETWEEN ? AND ?
            GROUP BY sv.id ORDER BY revenue DESC
            """,
            (business_id, date_from, date_to),
        ).fetchall()
        return row, by_service
