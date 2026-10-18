"""
بک‌اند Mini App تلگرام.
این فایل مستقیماً از database.py (همون چیزی که ربات تلگرامی استفاده می‌کنه) استفاده می‌کنه،
پس داده‌ها بین ربات و Mini App کاملاً مشترک و یکپارچه‌ست.

اجرا: uvicorn webapp_api:app --host 0.0.0.0 --port 8080
"""
import hashlib
import hmac
import json
import os
import time
import csv
import io
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

# مسیر مطلق پوشه‌ی این فایل، تا هر جا که سرور اجرا بشه (Render، Replit، ...) فرقی نکنه.
# چون گاهی هنگام آپلود دستی، ساختار پوشه‌ها به‌هم می‌ریزه، چند مسیر محتمل رو چک می‌کنیم:
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_PATHS = [
    os.path.join(BASE_DIR, "webapp", "index.html"),  # ساختار استاندارد
    os.path.join(BASE_DIR, "index.html"),             # اگه فایل مستقیم کنار بقیه فایل‌ها باشه
]


def _find_index_html():
    for p in _CANDIDATE_PATHS:
        if os.path.exists(p):
            return p
    return None


INDEX_HTML_PATH = _find_index_html()

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional

import database as db
from config import BOT_TOKEN, BOT_USERNAME, LOYALTY_TOMAN_PER_POINT, LOYALTY_POINT_VALUE_TOMAN

app = FastAPI(title="Salon Bot Mini App API")
db.init_db()


# =========================================================
# احراز هویت initData تلگرام
# طبق مستندات رسمی: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
# =========================================================

def validate_init_data(init_data: str, max_age_seconds: int = 86400) -> dict:
    if not init_data:
        raise HTTPException(401, "initData موجود نیست")
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            raise ValueError("no hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("hash mismatch")
        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > max_age_seconds:
            raise ValueError("expired")
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        raise HTTPException(401, "initData نامعتبر است")


def get_tg_user(x_telegram_init_data: str = Header(default="")) -> dict:
    return validate_init_data(x_telegram_init_data)


def require_admin(business_id: int, user: dict) -> dict:
    if not db.is_business_admin(business_id, user["id"]):
        raise HTTPException(403, "شما ادمین این کسب‌وکار نیستید")
    business = db.get_business(business_id)
    if not business:
        raise HTTPException(404, "کسب‌وکار یافت نشد")
    return dict(business)


def row(r):
    return dict(r) if r else None


def rows(rs):
    return [dict(r) for r in rs]


# =========================================================
# فرانت‌اند (Mini App) روی همین سرور سرو می‌شه
# =========================================================

@app.get("/")
def serve_index():
    path = _find_index_html()
    if not path:
        raise HTTPException(
            500,
            "فایل index.html پیدا نشد. این مسیرها بررسی شدن:\n" + "\n".join(_CANDIDATE_PATHS),
        )
    return FileResponse(path)


@app.get("/api/config")
def public_config():
    return {"bot_username": BOT_USERNAME}


# =========================================================
# ساخت کسب‌وکار جدید (هرکسی می‌تونه کسب‌وکار خودش رو بسازه)
# =========================================================

class NewBusinessBody(BaseModel):
    name: str


@app.post("/api/businesses")
def create_business(body: NewBusinessBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    biz_id, slug = db.create_business(body.name.strip(), u["id"])
    return {"business_id": biz_id, "slug": slug}


@app.get("/api/my-businesses")
def my_businesses(x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    return rows(db.get_businesses_for_admin(u["id"]))


# =========================================================
# بخش مشتری
# =========================================================

@app.get("/api/business/by-slug/{slug}")
def business_by_slug(slug: str):
    b = db.get_business_by_slug(slug)
    if not b:
        raise HTTPException(404, "کسب‌وکار یافت نشد")
    return row(b)


@app.get("/api/business/{business_id}/services")
def list_services(business_id: int):
    return rows(db.get_services(business_id))


@app.get("/api/services/{service_id}/staff")
def staff_for_service(service_id: int):
    return rows(db.get_staff_for_service(service_id))


@app.get("/api/availability")
def availability(business_id: int, staff_id: int, date: str, duration: int):
    return db.get_available_slots(business_id, staff_id, date, duration)


@app.get("/api/discount/validate")
def check_discount(business_id: int, code: str, base_price: int):
    ok, final_price, message, code_row = db.validate_discount(business_id, code, base_price)
    return {"ok": ok, "final_price": final_price, "message": message}


class CreateAppointmentBody(BaseModel):
    business_id: int
    service_id: int
    staff_id: int
    date: str
    time: str
    phone: str
    name: Optional[str] = None
    discount_code: Optional[str] = None
    use_points: bool = False


@app.post("/api/appointments")
def create_appointment(body: CreateAppointmentBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    service = db.get_service(body.service_id)
    if not service:
        raise HTTPException(404, "خدمت یافت نشد")

    slots = db.get_available_slots(body.business_id, body.staff_id, body.date, service["duration_minutes"])
    if body.time not in slots:
        raise HTTPException(409, "این ساعت دیگر خالی نیست")

    has_price = service["price"] is not None
    final_price = service["price"]
    discount_code, discount_code_id = None, None
    if body.discount_code:
        if not has_price:
            raise HTTPException(400, "این خدمت قیمت ثابت ندارد، امکان اعمال کد تخفیف نیست.")
        ok, final_price, msg, code_row = db.validate_discount(body.business_id, body.discount_code, service["price"])
        if not ok:
            raise HTTPException(400, msg)
        discount_code, discount_code_id = code_row["code"], code_row["id"]

    customer = db.upsert_customer(body.business_id, u["id"], body.name or u.get("first_name", ""), body.phone)
    points_redeemed = 0
    if body.use_points and has_price and customer["loyalty_points"] > 0:
        points_redeemed = customer["loyalty_points"]
        final_price = max(0, final_price - points_redeemed * LOYALTY_POINT_VALUE_TOMAN)
        db.redeem_loyalty_points(customer["id"], points_redeemed)
    if discount_code_id:
        db.increment_discount_usage(discount_code_id)

    appt_id = db.create_appointment(
        business_id=body.business_id, customer_id=customer["id"], user_id=u["id"],
        user_name=body.name or u.get("first_name", ""), user_phone=body.phone,
        service_id=body.service_id, staff_id=body.staff_id, date_str=body.date, start_time=body.time,
        duration_minutes=service["duration_minutes"], base_price=service["price"], final_price=final_price,
        discount_code=discount_code, points_redeemed=points_redeemed,
    )
    return {"appointment_id": appt_id, "final_price": final_price}


@app.get("/api/my/appointments")
def my_appointments(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    return rows(db.get_user_upcoming_appointments(business_id, u["id"]))


@app.get("/api/my/history")
def my_history(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    return rows(db.get_user_past_appointments(business_id, u["id"]))


@app.get("/api/my/profile")
def my_profile(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    c = db.get_customer(business_id, u["id"])
    return row(c)


@app.post("/api/appointments/{appointment_id}/cancel")
def cancel_my_appointment(appointment_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    db.cancel_appointment(appointment_id, user_id=u["id"])
    return {"ok": True}


class FeedbackBody(BaseModel):
    business_id: int
    text: str


@app.post("/api/feedback")
def send_feedback(body: FeedbackBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    db.add_feedback(body.business_id, u["id"], u.get("first_name", ""), body.text)
    return {"ok": True}


# =========================================================
# بخش ادمین (همه با چک is_business_admin محافظت می‌شن)
# =========================================================

@app.get("/api/admin/{business_id}/dashboard")
def admin_dashboard(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    biz = require_admin(business_id, u)
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    month_start = datetime.now().strftime("%Y-%m-01")
    today_appts = db.get_appointments_by_date(business_id, today)
    month_row, _ = db.get_revenue_report(business_id, month_start, today)
    avg, cnt = db.get_average_rating(business_id)
    upcoming = db.get_all_upcoming_appointments(business_id)
    return {
        "business_name": biz["name"], "slug": biz["slug"], "today_count": len(today_appts), "upcoming_count": len(upcoming),
        "month_revenue": month_row["revenue"], "month_count": month_row["cnt"],
        "avg_rating": avg, "review_count": cnt,
    }


@app.get("/api/admin/{business_id}/appointments/today")
def admin_today(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_appointments_by_date(business_id, datetime.now().strftime("%Y-%m-%d")))


@app.get("/api/admin/{business_id}/appointments/upcoming")
def admin_upcoming(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_all_upcoming_appointments(business_id))


@app.post("/api/admin/{business_id}/appointments/{appointment_id}/cancel")
def admin_cancel(business_id: int, appointment_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    db.cancel_appointment(appointment_id)
    return {"ok": True}


@app.get("/api/admin/{business_id}/customers/search")
def admin_search_customers(business_id: int, q: str, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.search_customers(business_id, q))


# ---------- خدمات ----------

class ServiceBody(BaseModel):
    name: str
    duration_minutes: int
    price: Optional[int] = None  # None یعنی بدون قیمت ثابت


@app.get("/api/admin/{business_id}/services")
def admin_list_services(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_services(business_id, active_only=False))


@app.post("/api/admin/{business_id}/services")
def admin_add_service(business_id: int, body: ServiceBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    sid = db.add_service(business_id, body.name, body.duration_minutes, body.price)
    return {"id": sid}


@app.patch("/api/admin/services/{service_id}")
def admin_edit_service(service_id: int, body: ServiceBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    s = db.get_service(service_id)
    if not s:
        raise HTTPException(404)
    require_admin(s["business_id"], u)
    db.update_service(service_id, body.name, body.duration_minutes, body.price)
    return {"ok": True}


@app.delete("/api/admin/services/{service_id}")
def admin_toggle_service(service_id: int, x_telegram_init_data: str = Header(default="")):
    """با وجود اسم delete، این عملیات سوییچ فعال/غیرفعال است، نه حذف واقعی."""
    u = validate_init_data(x_telegram_init_data)
    s = db.get_service(service_id)
    if not s:
        raise HTTPException(404)
    require_admin(s["business_id"], u)
    (db.reactivate_service if not s["active"] else db.deactivate_service)(service_id)
    return {"ok": True, "active": not s["active"]}


# ---------- متخصص‌ها ----------

class StaffBody(BaseModel):
    name: str


@app.get("/api/admin/{business_id}/staff")
def admin_list_staff(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_staff_list(business_id, active_only=False))


@app.post("/api/admin/{business_id}/staff")
def admin_add_staff(business_id: int, body: StaffBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    sid = db.add_staff(business_id, body.name)
    return {"id": sid}


@app.patch("/api/admin/staff/{staff_id}")
def admin_edit_staff(staff_id: int, body: StaffBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    s = db.get_staff_member(staff_id)
    if not s:
        raise HTTPException(404)
    require_admin(s["business_id"], u)
    db.update_staff_name(staff_id, body.name)
    return {"ok": True}


@app.delete("/api/admin/staff/{staff_id}")
def admin_toggle_staff(staff_id: int, x_telegram_init_data: str = Header(default="")):
    """با وجود اسم delete، این عملیات سوییچ فعال/غیرفعال است، نه حذف واقعی."""
    u = validate_init_data(x_telegram_init_data)
    s = db.get_staff_member(staff_id)
    if not s:
        raise HTTPException(404)
    require_admin(s["business_id"], u)
    (db.reactivate_staff if not s["active"] else db.deactivate_staff)(staff_id)
    return {"ok": True, "active": not s["active"]}


@app.post("/api/admin/staff/{staff_id}/services/{service_id}/toggle")
def admin_toggle_staff_service(staff_id: int, service_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    s = db.get_staff_member(staff_id)
    if not s:
        raise HTTPException(404)
    require_admin(s["business_id"], u)
    linked = {x["id"] for x in db.get_services_for_staff(staff_id)}
    if service_id in linked:
        db.unlink_staff_service(staff_id, service_id)
    else:
        db.link_staff_service(staff_id, service_id)
    return {"ok": True}


# ---------- برنامه کاری و تعطیلات ----------

class ScheduleBody(BaseModel):
    weekday: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_closed: bool = False
    staff_id: Optional[int] = None  # اگر خالی باشد یعنی برنامه کلی کسب‌وکار


@app.get("/api/admin/{business_id}/schedule")
def admin_get_schedule(business_id: int, staff_id: Optional[int] = None, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    if staff_id:
        return rows(db.get_staff_schedule(staff_id))
    return rows(db.get_schedule(business_id))


@app.post("/api/admin/{business_id}/schedule")
def admin_set_schedule(business_id: int, body: ScheduleBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    if body.staff_id:
        db.set_staff_schedule(body.staff_id, body.weekday, body.start_time, body.end_time, body.is_closed)
    else:
        db.set_schedule(business_id, body.weekday, body.start_time, body.end_time, body.is_closed)
    return {"ok": True}


class HolidayBody(BaseModel):
    date: str
    reason: Optional[str] = ""


@app.get("/api/admin/{business_id}/holidays")
def admin_list_holidays(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_holidays(business_id))


@app.post("/api/admin/{business_id}/holidays")
def admin_add_holiday(business_id: int, body: HolidayBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    db.add_holiday(business_id, body.date, body.reason or "")
    return {"ok": True}


@app.delete("/api/admin/holidays/{holiday_id}")
def admin_delete_holiday(holiday_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    h = db.get_holiday(holiday_id)
    if not h:
        raise HTTPException(404)
    require_admin(h["business_id"], u)
    db.remove_holiday(holiday_id)
    return {"ok": True}


# ---------- تخفیف ----------

class DiscountBody(BaseModel):
    code: str
    discount_type: str
    value: int
    expiry_date: Optional[str] = None
    usage_limit: Optional[int] = None


@app.get("/api/admin/{business_id}/discounts")
def admin_list_discounts(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_discount_codes(business_id))


@app.post("/api/admin/{business_id}/discounts")
def admin_add_discount(business_id: int, body: DiscountBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    did = db.create_discount_code(business_id, body.code, body.discount_type, body.value, body.expiry_date, body.usage_limit)
    return {"id": did}


# ---------- نظرات، درآمد، بازخورد ----------

@app.get("/api/admin/{business_id}/reviews")
def admin_reviews(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    avg, cnt = db.get_average_rating(business_id)
    return {"average": avg, "count": cnt, "recent": rows(db.get_recent_reviews(business_id))}


@app.get("/api/admin/{business_id}/revenue")
def admin_revenue(business_id: int, period: str = "month", x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    today = datetime.now()
    if period == "today":
        date_from = date_to = today.strftime("%Y-%m-%d")
    elif period == "week":
        date_from = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")
    else:
        date_from = today.strftime("%Y-%m-01")
        date_to = today.strftime("%Y-%m-%d")
    summary, by_service = db.get_revenue_report(business_id, date_from, date_to)
    return {"summary": row(summary), "by_service": rows(by_service)}


@app.get("/api/admin/{business_id}/export-csv")
def admin_export_csv(business_id: int, x: str = "", x_telegram_init_data: str = Header(default="")):
    # این endpoint با window.open باز می‌شه (برای دانلود فایل)، پس نمی‌تونیم هدر سفارشی بفرستیم؛
    # initData رو از کوئری‌پارامتر x هم قبول می‌کنیم (همونی که فرانت‌اند توی URL می‌فرسته)
    u = validate_init_data(x_telegram_init_data or x); require_admin(business_id, u)
    data = db.get_all_appointments_for_export(business_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["تاریخ", "ساعت", "خدمت", "متخصص", "مشتری", "تلفن", "قیمت پایه", "قیمت نهایی", "کد تخفیف", "وضعیت"])
    for r in data:
        w.writerow([r["date"], r["start_time"], r["service_name"], r["staff_name"], r["user_name"],
                    r["user_phone"], r["base_price"] if r["base_price"] is not None else "بدون قیمت ثابت",
                    r["final_price"] if r["final_price"] is not None else "بدون قیمت ثابت",
                    r["discount_code"] or "", r["status"]])
    content = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(io.BytesIO(content), media_type="text/csv",
                              headers={"Content-Disposition": "attachment; filename=appointments.csv"})


@app.get("/api/admin/{business_id}/feedback")
def admin_feedback(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    return rows(db.get_feedback_list(business_id))


# ---------- مدیریت ادمین‌ها ----------

@app.get("/api/admin/{business_id}/admins")
def admin_list_admins(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    biz = require_admin(business_id, u)
    return {"owner_id": biz["owner_id"], "admins": rows(db.get_admin_list(business_id))}


@app.post("/api/admin/{business_id}/admins/invite")
def admin_create_invite(business_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    code = db.create_admin_invite(business_id, u["id"])
    return {"code": code}


@app.delete("/api/admin/{business_id}/admins/{telegram_id}")
def admin_remove_admin(business_id: int, telegram_id: int, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data); require_admin(business_id, u)
    ok = db.remove_business_admin(business_id, telegram_id)
    if not ok:
        raise HTTPException(400, "نمی‌توانید مالک اصلی را حذف کنید")
    return {"ok": True}


class ClaimAdminBody(BaseModel):
    code: str


@app.post("/api/admin/claim")
def claim_admin(body: ClaimAdminBody, x_telegram_init_data: str = Header(default="")):
    u = validate_init_data(x_telegram_init_data)
    ok, business, message = db.use_admin_invite(body.code, u["id"])
    if not ok:
        raise HTTPException(400, message)
    return {"ok": True, "business_id": business["id"], "business_name": business["name"]}