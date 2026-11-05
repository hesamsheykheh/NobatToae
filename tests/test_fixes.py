"""
تست‌های امنیت و منطق رزرو.
اجرا:  pip install pytest httpx  &&  python -m pytest -q
"""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import date, timedelta

import pytest

BOT_TOKEN = "123456:TEST-TOKEN"
os.environ["BOT_TOKEN"] = BOT_TOKEN
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import config
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    import webapp_api
    from fastapi.testclient import TestClient
    return TestClient(webapp_api.app), db, config


def auth(uid):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "u"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urllib.parse.urlencode(data)}


def make_business(client, owner):
    return client.post("/api/businesses", json={"name": f"biz{owner}"}, headers=auth(owner)).json()["business_id"]


def free_slot(client, biz, duration=30, staff_id=None):
    for i in range(1, 10):
        d = (date.today() + timedelta(days=i)).isoformat()
        params = {"business_id": biz, "date": d, "duration": duration}
        if staff_id:
            params["staff_id"] = staff_id
        slots = client.get("/api/availability", params=params).json()
        if slots:
            return d, slots[0]
    raise AssertionError("no free slot")


def test_admin_cannot_cancel_other_business_appointment(env):
    client, db, _ = env
    victim = make_business(client, 1)
    sid = db.add_service(victim, "cut", 30, 100000)
    d, t = free_slot(client, victim)
    appt = client.post("/api/appointments", headers=auth(2), json={
        "business_id": victim, "service_id": sid, "date": d, "time": t, "phone": "09120000000"}).json()
    attacker = make_business(client, 3)
    r = client.post(f"/api/admin/{attacker}/appointments/{appt['appointment_id']}/cancel", headers=auth(3))
    assert r.status_code == 404
    assert db.get_appointment(appt["appointment_id"])["status"] == "confirmed"
    # مدیر واقعی می‌تونه لغو کنه
    r = client.post(f"/api/admin/{victim}/appointments/{appt['appointment_id']}/cancel", headers=auth(1))
    assert r.status_code == 200


def test_service_of_other_business_rejected(env):
    client, db, _ = env
    a = make_business(client, 1)
    b = make_business(client, 2)
    sid_b = db.add_service(b, "cut", 30, 100000)
    d, t = free_slot(client, a)
    r = client.post("/api/appointments", headers=auth(5), json={
        "business_id": a, "service_id": sid_b, "date": d, "time": t, "phone": "09120000000"})
    assert r.status_code == 404


def test_no_double_booking_under_concurrency(env, monkeypatch):
    client, db, _ = env
    biz = make_business(client, 1)
    staff = db.add_staff(biz, "Ali")
    sid = db.add_service(biz, "cut", 30, 100000)
    db.link_staff_service(staff, sid)
    d, t = free_slot(client, biz, staff_id=staff)
    results = []
    # یه مکث کوتاه بین «چک ساعت خالی» و «ثبت»، تا درخواست‌های هم‌زمان واقعاً با هم تداخل کنن
    original = db.get_available_slots

    def slow_slots(*a, **k):
        res = original(*a, **k)
        time.sleep(0.05)
        return res
    monkeypatch.setattr(db, "get_available_slots", slow_slots)

    def book(uid):
        r = client.post("/api/orders", headers=auth(uid), json={
            "business_id": biz, "items": [{"service_id": sid, "quantity": 1}],
            "delivery_date": d, "delivery_time": t, "phone": "09120000000", "staff_id": staff})
        results.append(r.status_code)

    threads = [threading.Thread(target=book, args=(100 + i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(200) == 1, results


def test_loyalty_uses_only_needed_points_and_refunds_on_cancel(env):
    client, db, config = env
    biz = make_business(client, 1)
    sid = db.add_service(biz, "cut", 30, 20000)  # ۲۰ هزار تومان = ۲۰ امتیاز
    cust = db.upsert_customer(biz, 7, "c", "09120000000")
    db.add_loyalty_points(cust["id"], 100)
    d, t = free_slot(client, biz)
    r = client.post("/api/orders", headers=auth(7), json={
        "business_id": biz, "items": [{"service_id": sid, "quantity": 1}],
        "delivery_date": d, "delivery_time": t, "phone": "09120000000", "use_points": True}).json()
    assert r["total_price"] == 0
    assert db.get_customer(biz, 7)["loyalty_points"] == 80
    client.post(f"/api/orders/{r['order_id']}/cancel", headers=auth(7))
    assert db.get_customer(biz, 7)["loyalty_points"] == 100


def test_min_redeem_enforced(env):
    client, db, config = env
    biz = make_business(client, 1)
    sid = db.add_service(biz, "cut", 30, 50000)
    cust = db.upsert_customer(biz, 8, "c", "09120000000")
    db.add_loyalty_points(cust["id"], config.LOYALTY_MIN_REDEEM - 1)
    d, t = free_slot(client, biz)
    r = client.post("/api/orders", headers=auth(8), json={
        "business_id": biz, "items": [{"service_id": sid, "quantity": 1}],
        "delivery_date": d, "delivery_time": t, "phone": "09120000000", "use_points": True}).json()
    assert r["total_price"] == 50000


def test_invalid_discount_rejected(env):
    client, db, _ = env
    biz = make_business(client, 1)
    url = f"/api/admin/{biz}/discounts"
    assert client.post(url, headers=auth(1), json={"code": "X", "discount_type": "percent", "value": 150}).status_code == 400
    assert client.post(url, headers=auth(1), json={"code": "Y", "discount_type": "bogus", "value": 10}).status_code == 400
    assert client.post(url, headers=auth(1), json={"code": "OK10", "discount_type": "percent", "value": 10}).status_code == 200


def test_discount_usage_restored_on_cancel(env):
    client, db, _ = env
    biz = make_business(client, 1)
    sid = db.add_service(biz, "cut", 30, 100000)
    client.post(f"/api/admin/{biz}/discounts", headers=auth(1),
                json={"code": "ONE", "discount_type": "fixed", "value": 10000, "usage_limit": 1})
    d, t = free_slot(client, biz)
    r = client.post("/api/orders", headers=auth(9), json={
        "business_id": biz, "items": [{"service_id": sid, "quantity": 1}], "delivery_date": d,
        "delivery_time": t, "phone": "09120000000", "discount_code": "ONE"}).json()
    assert r["total_price"] == 90000
    assert db.get_discount_code(biz, "ONE")["used_count"] == 1
    client.post(f"/api/orders/{r['order_id']}/cancel", headers=auth(9))
    assert db.get_discount_code(biz, "ONE")["used_count"] == 0


def test_expired_admin_invite_rejected(env):
    client, db, config = env
    biz = make_business(client, 1)
    code = db.create_admin_invite(biz, 1)
    old = (config.local_now() - timedelta(days=config.ADMIN_INVITE_VALID_DAYS + 1)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE admin_invites SET created_at = ? WHERE code = ?", (old, code))
    ok, _, _ = db.use_admin_invite(code, 50)
    assert not ok and not db.is_business_admin(biz, 50)


def test_admin_invite_single_use(env):
    client, db, _ = env
    biz = make_business(client, 1)
    code = db.create_admin_invite(biz, 1)
    assert db.use_admin_invite(code, 50)[0]
    assert not db.use_admin_invite(code, 51)[0]
    assert not db.is_business_admin(biz, 51)


def test_local_now_uses_tehran_time():
    from datetime import datetime, timezone
    import config
    diff = config.local_now() - datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(diff.total_seconds() - 3.5 * 3600) < 60


def test_internal_errors_hidden_by_default(env, monkeypatch):
    client, db, _ = env
    import webapp_api
    monkeypatch.delenv("SHOW_ERRORS", raising=False)

    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")
    monkeypatch.setattr(webapp_api.db, "get_services", boom)
    c = type(client)(webapp_api.app, raise_server_exceptions=False)
    r = c.get("/api/business/1/services")
    assert r.status_code == 500 and "secret-internal-detail" not in r.text
