"""
Cafe App backend
=================
Bitta Python dasturi ichida 3 ta narsa birga ishlaydi:
  1. Telegram bot (mijozlarga Mini App'ni ochib beradi)
  2. Ochiq API (Mini App menyuni shu yerdan oladi, buyurtma shu yerga tushadi)
  3. Admin panel (menyuni boshqarish, buyurtmalar tarixini ko'rish)

SOZLASH — quyidagi qiymatlarni Railway'ning "Variables" bo'limida to'ldiring:
  BOT_TOKEN          -> @BotFather dan olingan token
  ADMIN_CHAT_ID       -> buyurtmalar tushadigan Telegram chat ID
  ADMIN_PASSWORD      -> admin panelga kirish paroli (o'zingiz o'ylab toping)
  COURIER_PASSWORD    -> kuryer paneliga kirish paroli (o'zingiz o'ylab toping)
  PUBLIC_URL          -> Railway bergan ochiq domen, masalan https://cafe-bot-production.up.railway.app
  CLICK_MERCHANT_ID   -> Click biznes kabinetidan
  CLICK_SERVICE_ID    -> Click biznes kabinetidan
  CLICK_SECRET_KEY    -> Click biznes kabinetidan
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Response, UploadFile, Form, File, HTTPException, Cookie, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, MenuButtonWebApp

# ------------------------------------------------------------------
# LOGGING — xatolarni fayl va konsolga yozib boradi (10-bo'lim)
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("cafe_bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("cafe-bot")

# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8624003978:AAG_eiZI0LA1t6lsO94-DLkTUkXy8brieK8")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "8203697473")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "cafe2026")
COURIER_PASSWORD = os.getenv("COURIER_PASSWORD", "courier2026")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://cafe-menuae.netlify.app")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")  # masalan https://xxx.up.railway.app

CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "")
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")
# ------------------------------------------------------------------

DB_PATH = os.getenv("DB_PATH", "cafe.db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_TOKEN = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
COURIER_TOKEN = hashlib.sha256(COURIER_PASSWORD.encode()).hexdigest()

# ------------------------------------------------------------------
# RATE LIMITING — bitta foydalanuvchi 60 soniyada 3 tadan ortiq
# buyurtma/promokod so'rovi yubora olmaydi (10-bo'lim, spamdan himoya)
# ------------------------------------------------------------------
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 3
_rate_hits = defaultdict(list)


def rate_limited(key: str, max_hits: int = RATE_LIMIT_MAX, window: int = RATE_LIMIT_WINDOW) -> bool:
    now = time.time()
    hits = [t for t in _rate_hits[key] if now - t < window]
    hits.append(now)
    _rate_hits[key] = hits
    return len(hits) > max_hits


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
BOT_USERNAME = ""  # /start startup'da to'ldiriladi, referal havola uchun


# ============================================================
# DATABASE
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(conn, table, column, coltype):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price INTEGER NOT NULL,
            image TEXT,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            items_json TEXT,
            total INTEGER,
            delivery_type TEXT,
            payment_type TEXT,
            phone TEXT,
            address TEXT,
            comment TEXT,
            customer_name TEXT,
            telegram_user_id TEXT,
            status TEXT DEFAULT 'yangi',
            payment_status TEXT DEFAULT 'kutilmoqda',
            click_trans_id TEXT
        )
    """)
    # 6-bo'lim: marketing uchun promo_codes va referrals
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_percent INTEGER DEFAULT 0,
            min_order INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            usage_limit INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0,
            expires_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id TEXT NOT NULL,
            referred_id TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(referred_id)
        )
    """)
    # 4-bo'lim: foydalanuvchi profili
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            address TEXT,
            referred_by TEXT,
            created_at TEXT,
            last_seen TEXT
        )
    """)
    # Eski bazalarda yo'q ustunlarni qo'shib qo'yamiz (migratsiya)
    _add_column_if_missing(conn, "orders", "promo_code", "TEXT")
    _add_column_if_missing(conn, "orders", "discount", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "orders", "cancel_reason", "TEXT")
    _add_column_if_missing(conn, "orders", "cancelled_by", "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS couriers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            phone TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    _add_column_if_missing(conn, "orders", "lat", "REAL")
    _add_column_if_missing(conn, "orders", "lng", "REAL")
    _add_column_if_missing(conn, "orders", "courier_name", "TEXT")
    conn.commit()
    # Agar menyu bo'sh bo'lsa - namunaviy taomlar bilan to'ldiramiz
    count = conn.execute("SELECT COUNT(*) c FROM menu_items").fetchone()["c"]
    if count == 0:
        sample = [
            ("Issiq ichimliklar", "Cappuccino", "Espresso, bug'langan sut", 22000),
            ("Issiq ichimliklar", "Latte", "Yumshoq va krem ta'mli", 24000),
            ("Sovuq ichimliklar", "Limonad", "Yangi limon va yalpiz", 18000),
            ("Taomlar", "Lag'mon", "Qo'lda tortilgan xamir, sabzavotlar bilan", 35000),
            ("Taomlar", "Osh", "Go'sht, sabzi, guruch", 32000),
            ("Shirinliklar", "Napoleon", "Uy sharoitida tayyorlangan", 16000),
        ]
        for cat, name, desc, price in sample:
            conn.execute(
                "INSERT INTO menu_items (category, name, description, price) VALUES (?,?,?,?)",
                (cat, name, desc, price),
            )
        conn.commit()
    conn.close()


def image_url(filename):
    if not filename:
        return None
    base = PUBLIC_URL or ""
    return f"{base}/uploads/{filename}"


# ============================================================
# ADMIN AUTH
# ============================================================
def check_admin(admin_session: str = Cookie(default=None)):
    if admin_session != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Ruxsat yo'q")
    return True


def check_courier(courier_session: str = Cookie(default=None)):
    if courier_session != COURIER_TOKEN:
        raise HTTPException(status_code=401, detail="Ruxsat yo'q")
    return True


def upsert_user(telegram_user_id, name=None, phone=None, address=None, referred_by=None):
    if not telegram_user_id:
        return
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if row:
        conn.execute(
            """UPDATE users SET
                 name=COALESCE(?, name), phone=COALESCE(?, phone),
                 address=COALESCE(?, address), last_seen=?
               WHERE telegram_user_id=?""",
            (name, phone, address, now, telegram_user_id),
        )
    else:
        conn.execute(
            """INSERT INTO users (telegram_user_id, name, phone, address, referred_by, created_at, last_seen)
               VALUES (?,?,?,?,?,?,?)""",
            (telegram_user_id, name, phone, address, referred_by, now, now),
        )
    conn.commit()
    conn.close()


def find_promo(code):
    if not code:
        return None
    conn = db()
    row = conn.execute(
        "SELECT * FROM promo_codes WHERE code=? AND active=1", (code.strip().upper(),)
    ).fetchone()
    conn.close()
    return row


def promo_valid_for(row, total):
    """Promokod hozir ishlatsa bo'ladimi tekshiradi, xato bo'lsa matn qaytaradi."""
    if not row:
        return "Promokod topilmadi"
    if row["expires_at"]:
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                return "Promokod muddati tugagan"
        except ValueError:
            pass
    if row["usage_limit"] and row["used_count"] >= row["usage_limit"]:
        return "Promokod limiti tugagan"
    if total < row["min_order"]:
        return f"Kamida {row['min_order']:,} so'mlik buyurtmaga amal qiladi".replace(",", " ")
    return None


# ============================================================
# PUBLIC API — Mini App shu yerdan foydalanadi
# ============================================================
@app.get("/api/menu")
async def get_menu(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    conn = db()
    rows = conn.execute(
        "SELECT * FROM menu_items WHERE active=1 ORDER BY category, sort_order, id"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "category": r["category"],
            "name": r["name"],
            "description": r["description"],
            "price": r["price"],
            "image": image_url(r["image"]),
        }
        for r in rows
    ]


@app.post("/api/promo/check")
async def check_promo(request: Request):
    data = await request.json()
    code = str(data.get("code", ""))
    total = int(data.get("total", 0))
    row = find_promo(code)
    err = promo_valid_for(row, total)
    if err:
        return JSONResponse({"ok": False, "error": err})
    discount = int(total * row["discount_percent"] / 100)
    return {"ok": True, "discount": discount, "discount_percent": row["discount_percent"], "new_total": total - discount}


@app.post("/api/order")
async def create_order(request: Request):
    data = await request.json()
    items = data.get("items", [])
    total = int(data.get("total", 0))
    delivery_type = data.get("delivery_type", "")
    payment_type = data.get("payment_type", "")
    phone = data.get("phone", "")
    address = data.get("address", "")
    comment = data.get("comment", "")
    customer_name = data.get("customer_name", "")
    telegram_user_id = str(data.get("telegram_user_id", ""))
    promo_code = str(data.get("promo_code", "") or "")
    lat = data.get("lat")
    lng = data.get("lng")

    # 10-bo'lim: rate limit — spamdan himoya
    limiter_key = telegram_user_id or "anon"
    if rate_limited(f"order:{limiter_key}"):
        raise HTTPException(status_code=429, detail="Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.")

    # 6-bo'lim: promokodni serverda qayta tekshiramiz (frontendga ishonmaymiz)
    discount = 0
    promo_row = find_promo(promo_code) if promo_code else None
    if promo_row and not promo_valid_for(promo_row, total):
        discount = int(total * promo_row["discount_percent"] / 100)
        total = total - discount

    conn = db()
    cur = conn.execute(
        """INSERT INTO orders (created_at, items_json, total, delivery_type, payment_type,
           phone, address, comment, customer_name, telegram_user_id, promo_code, discount, lat, lng)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(items, ensure_ascii=False),
            total, delivery_type, payment_type, phone, address, comment,
            customer_name, telegram_user_id, promo_code or None, discount, lat, lng,
        ),
    )
    order_id = cur.lastrowid
    if promo_row:
        conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id=?", (promo_row["id"],))
    conn.commit()
    conn.close()

    # 4-bo'lim: foydalanuvchi profilini yangilab/saqlab qo'yamiz
    upsert_user(telegram_user_id, name=customer_name or None, phone=phone or None, address=address or None)

    # Adminga xabar
    delivery_map = {"olib_ketish": "Olib ketish", "yetkazish": "Yetkazib berish"}
    payment_map = {"naqd": "Naqd pul", "karta": "Karta orqali (Click)"}
    items_text = "\n".join(f"• {i['name']} × {i['qty']} — {i['price']*i['qty']:,} so'm".replace(",", " ") for i in items)
    text = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n{items_text}\n\n"
    )
    if discount:
        text += f"🏷 <b>Chegirma ({promo_code}):</b> -{discount:,} so'm\n".replace(",", " ")
    text += (
        f"💰 <b>Jami:</b> {total:,} so'm\n".replace(",", " ") +
        f"🚚 <b>Turi:</b> {delivery_map.get(delivery_type,'-')}\n"
        f"💳 <b>To'lov:</b> {payment_map.get(payment_type,'-')}\n"
        f"📞 <b>Telefon:</b> {phone}\n"
    )
    if delivery_type == "yetkazish":
        text += f"📍 <b>Manzil:</b> {address}\n"
        if lat and lng:
            text += f"🗺 <b>Xarita:</b> https://maps.google.com/?q={lat},{lng}\n"
    if comment:
        text += f"📝 <b>Izoh:</b> {comment}\n"
    if customer_name:
        text += f"\n👤 Mijoz: {customer_name}"

    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e:
        log.error(f"Adminga xabar yuborishda xatolik: {e}")

    result = {"order_id": order_id, "total": total, "discount": discount}

    if payment_type == "karta" and CLICK_MERCHANT_ID and CLICK_SERVICE_ID:
        pay_url = (
            "https://my.click.uz/services/pay"
            f"?service_id={CLICK_SERVICE_ID}&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={total}&transaction_param={order_id}"
        )
        result["click_pay_url"] = pay_url

    return result


@app.get("/api/bot-info")
async def bot_info():
    return {"username": BOT_USERNAME}


@app.get("/api/profile/stats")
async def profile_stats(telegram_user_id: str):
    conn = db()
    rows = conn.execute(
        "SELECT items_json, total FROM orders WHERE telegram_user_id=? AND status!='bekor'",
        (telegram_user_id,),
    ).fetchall()
    order_count = len(rows)
    total_spent = sum(r["total"] for r in rows)

    item_counts = defaultdict(int)
    for r in rows:
        for it in json.loads(r["items_json"] or "[]"):
            item_counts[it["name"]] += it.get("qty", 0)
    favorites = sorted(item_counts.items(), key=lambda x: -x[1])[:5]

    referral_count = conn.execute(
        "SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (telegram_user_id,)
    ).fetchone()["c"]
    conn.close()

    return {
        "order_count": order_count,
        "total_spent": total_spent,
        "favorites": [{"name": n, "qty": q} for n, q in favorites],
        "referral_count": referral_count,
    }


# ============================================================
# 4-bo'lim — FOYDALANUVCHI PROFILI (Mini App uchun)
# ============================================================
@app.get("/api/profile")
async def get_profile(telegram_user_id: str):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
    conn.close()
    if not row:
        return {"telegram_user_id": telegram_user_id, "name": None, "phone": None, "address": None}
    return dict(row)


@app.post("/api/profile")
async def update_profile(request: Request):
    data = await request.json()
    telegram_user_id = str(data.get("telegram_user_id", ""))
    if not telegram_user_id:
        raise HTTPException(status_code=400, detail="telegram_user_id kerak")
    upsert_user(
        telegram_user_id,
        name=data.get("name") or None,
        phone=data.get("phone") or None,
        address=data.get("address") or None,
    )
    return {"ok": True}


# ============================================================
# 3-bo'lim — BUYURTMA TARIXI VA STATUS TRACKING (mijoz uchun)
# ============================================================
@app.get("/api/orders")
async def get_user_orders(telegram_user_id: str):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM orders WHERE telegram_user_id=? ORDER BY id DESC LIMIT 100",
        (telegram_user_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["items"] = json.loads(d.pop("items_json") or "[]")
        result.append(d)
    return result


# ============================================================
# CLICK TO'LOV WEBHOOK — Click tizimi shu manzillarga so'rov yuboradi
# ============================================================
def click_sign_ok(params: dict, complete: bool):
    parts = [
        params.get("click_trans_id", ""),
        params.get("service_id", ""),
        CLICK_SECRET_KEY,
        params.get("merchant_trans_id", ""),
    ]
    if complete:
        parts.append(params.get("merchant_prepare_id", ""))
    parts.append(params.get("amount", ""))
    parts.append(params.get("action", ""))
    parts.append(params.get("sign_time", ""))
    expected = hashlib.md5("".join(str(p) for p in parts).encode()).hexdigest()
    return expected == params.get("sign_string", "")


@app.post("/click/prepare")
async def click_prepare(request: Request):
    form = dict(await request.form())
    order_id = form.get("merchant_trans_id")

    if not click_sign_ok(form, complete=False):
        return JSONResponse({"error": -1, "error_note": "Sign xato"})

    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse({"error": -5, "error_note": "Buyurtma topilmadi"})
    if int(row["total"]) != int(float(form.get("amount", 0))):
        conn.close()
        return JSONResponse({"error": -2, "error_note": "Summa mos emas"})

    conn.execute("UPDATE orders SET click_trans_id=? WHERE id=?", (form.get("click_trans_id"), order_id))
    conn.commit()
    conn.close()

    return JSONResponse({
        "click_trans_id": form.get("click_trans_id"),
        "merchant_trans_id": order_id,
        "merchant_prepare_id": order_id,
        "error": 0,
        "error_note": "Success",
    })


@app.post("/click/complete")
async def click_complete(request: Request):
    form = dict(await request.form())
    order_id = form.get("merchant_trans_id")

    if not click_sign_ok(form, complete=True):
        return JSONResponse({"error": -1, "error_note": "Sign xato"})

    conn = db()
    if str(form.get("error", "0")) == "0":
        conn.execute("UPDATE orders SET payment_status='tolandi' WHERE id=?", (order_id,))
        conn.commit()
        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"✅ Buyurtma #{order_id} uchun to'lov qabul qilindi (Click).")
        except Exception:
            pass
    else:
        conn.execute("UPDATE orders SET payment_status='bekor_qilindi' WHERE id=?", (order_id,))
        conn.commit()
    conn.close()

    return JSONResponse({
        "click_trans_id": form.get("click_trans_id"),
        "merchant_trans_id": order_id,
        "merchant_confirm_id": order_id,
        "error": 0,
        "error_note": "Success",
    })


# ============================================================
# ADMIN — login
# ============================================================
@app.post("/api/admin/login")
async def admin_login(request: Request, response: Response):
    data = await request.json()
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Parol xato")
    response.set_cookie("admin_session", ADMIN_TOKEN, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return {"ok": True}


@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("admin_session")
    return {"ok": True}


# ============================================================
# ADMIN — menyu boshqaruvi
# ============================================================
@app.get("/api/admin/menu")
async def admin_get_menu(_=Depends(check_admin)):
    conn = db()
    rows = conn.execute("SELECT * FROM menu_items ORDER BY category, sort_order, id").fetchall()
    conn.close()
    return [
        {
            "id": r["id"], "category": r["category"], "name": r["name"],
            "description": r["description"], "price": r["price"],
            "image": image_url(r["image"]), "active": bool(r["active"]),
        }
        for r in rows
    ]


@app.post("/api/admin/menu")
async def admin_add_item(
    category: str = Form(...), name: str = Form(...), description: str = Form(""),
    price: int = Form(...), active: bool = Form(True), image: UploadFile = File(None),
    _=Depends(check_admin),
):
    filename = None
    if image and image.filename:
        ext = os.path.splitext(image.filename)[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(await image.read())

    conn = db()
    conn.execute(
        "INSERT INTO menu_items (category,name,description,price,image,active) VALUES (?,?,?,?,?,?)",
        (category, name, description, price, filename, int(active)),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/admin/menu/{item_id}")
async def admin_update_item(
    item_id: int,
    category: str = Form(...), name: str = Form(...), description: str = Form(""),
    price: int = Form(...), active: bool = Form(True), image: UploadFile = File(None),
    _=Depends(check_admin),
):
    conn = db()
    row = conn.execute("SELECT image FROM menu_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Topilmadi")

    filename = row["image"]
    if image and image.filename:
        if filename:
            old_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(old_path):
                os.remove(old_path)
        ext = os.path.splitext(image.filename)[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(await image.read())

    conn.execute(
        "UPDATE menu_items SET category=?, name=?, description=?, price=?, image=?, active=? WHERE id=?",
        (category, name, description, price, filename, int(active), item_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/admin/menu/{item_id}")
async def admin_delete_item(item_id: int, _=Depends(check_admin)):
    conn = db()
    row = conn.execute("SELECT image FROM menu_items WHERE id=?", (item_id,)).fetchone()
    if row and row["image"]:
        p = os.path.join(UPLOAD_DIR, row["image"])
        if os.path.exists(p):
            os.remove(p)
    conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================
# ADMIN — buyurtmalar tarixi
# ============================================================
@app.get("/api/admin/orders")
async def admin_get_orders(_=Depends(check_admin)):
    conn = db()
    rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 300").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["items"] = json.loads(d.pop("items_json") or "[]")
        result.append(d)
    return result


STATUS_LABELS = {
    "yangi": "🆕 Yangi",
    "tayyorlanmoqda": "👨‍🍳 Tayyorlanmoqda",
    "tayyor": "✅ Tayyor",
    "yolda": "🚚 Yetkazib beruvchi yo'lda",
    "yetkazildi": "📦 Yetkazildi / berildi",
    "bekor": "❌ Bekor qilindi",
}


async def _update_order_status(order_id: int, status: str, reason: str = None, courier_name: str = None):
    conn = db()
    row = conn.execute("SELECT telegram_user_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")

    if status == "bekor":
        conn.execute(
            "UPDATE orders SET status=?, cancel_reason=?, cancelled_by='admin' WHERE id=?",
            (status, reason or None, order_id),
        )
    elif courier_name is not None:
        conn.execute("UPDATE orders SET status=?, courier_name=? WHERE id=?", (status, courier_name, order_id))
    else:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()

    # 8-bo'lim: mijozga buyurtma holati o'zgargani haqida xabar
    if row["telegram_user_id"]:
        label = STATUS_LABELS.get(status, status)
        text = f"📦 Buyurtma #{order_id} holati yangilandi:\n{label}"
        if status == "bekor" and reason:
            text += f"\n\n📝 Sabab: {reason}"
        try:
            await bot.send_message(chat_id=int(row["telegram_user_id"]), text=text)
        except Exception as e:
            log.error(f"Mijozga status xabarini yuborishda xatolik: {e}")


@app.patch("/api/admin/orders/{order_id}")
async def admin_update_order(order_id: int, request: Request, _=Depends(check_admin)):
    data = await request.json()
    await _update_order_status(
        order_id,
        status=data.get("status"),
        reason=str(data.get("reason", "")).strip(),
        courier_name=data.get("courier_name"),
    )
    return {"ok": True}


# ============================================================
# KURYER — login
# ============================================================
@app.post("/api/courier/login")
async def courier_login(request: Request, response: Response):
    data = await request.json()
    if data.get("password") != COURIER_PASSWORD:
        raise HTTPException(status_code=401, detail="Parol xato")
    response.set_cookie("courier_session", COURIER_TOKEN, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return {"ok": True}


@app.post("/api/courier/logout")
async def courier_logout(response: Response):
    response.delete_cookie("courier_session")
    return {"ok": True}


# ============================================================
# KURYERLAR RO'YXATI (admin qo'shadi, kuryer ko'radi)
# ============================================================
@app.get("/api/admin/couriers")
async def admin_list_couriers(_=Depends(check_admin)):
    conn = db()
    rows = conn.execute("SELECT * FROM couriers ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/couriers")
async def admin_add_courier(request: Request, _=Depends(check_admin)):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Ism kerak")
    conn = db()
    try:
        conn.execute("INSERT INTO couriers (name, phone) VALUES (?,?)", (name, data.get("phone") or None))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu ismli kuryer allaqachon mavjud")
    conn.close()
    return {"ok": True}


@app.delete("/api/admin/couriers/{courier_id}")
async def admin_delete_courier(courier_id: int, _=Depends(check_admin)):
    conn = db()
    conn.execute("DELETE FROM couriers WHERE id=?", (courier_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/courier/couriers")
async def courier_list_couriers(_=Depends(check_courier)):
    conn = db()
    rows = conn.execute("SELECT name FROM couriers WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


# ============================================================
# KURYER — yetkazish buyurtmalari
# ============================================================
@app.get("/api/courier/orders")
async def courier_get_orders(_=Depends(check_courier)):
    conn = db()
    rows = conn.execute(
        """SELECT * FROM orders
           WHERE delivery_type='yetkazish' AND status IN ('tayyor','yolda')
           ORDER BY id DESC""",
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["items"] = json.loads(d.pop("items_json") or "[]")
        result.append(d)
    return result


@app.patch("/api/courier/orders/{order_id}")
async def courier_update_order(order_id: int, request: Request, _=Depends(check_courier)):
    data = await request.json()
    status = data.get("status")
    if status not in ("yolda", "yetkazildi"):
        raise HTTPException(status_code=400, detail="Kuryer faqat 'yolda' yoki 'yetkazildi' qila oladi")
    await _update_order_status(order_id, status=status, courier_name=data.get("courier_name"))
    return {"ok": True}



async def cancel_own_order(order_id: int, request: Request):
    """3-bo'lim: mijoz hali 'yangi' holatdagi buyurtmasini o'zi bekor qila oladi."""
    data = await request.json()
    telegram_user_id = str(data.get("telegram_user_id", ""))
    if not telegram_user_id:
        raise HTTPException(status_code=400, detail="telegram_user_id kerak")

    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    if row["telegram_user_id"] != telegram_user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Bu sizning buyurtmangiz emas")
    if row["status"] != "yangi":
        conn.close()
        raise HTTPException(status_code=400, detail="Buyurtma allaqachon qayta ishlanmoqda, endi bekor qilib bo'lmaydi")

    conn.execute(
        "UPDATE orders SET status='bekor', cancel_reason='Mijoz tomonidan bekor qilindi', cancelled_by='customer' WHERE id=?",
        (order_id,),
    )
    conn.commit()
    conn.close()

    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Mijoz buyurtma #{order_id} ni bekor qildi.")
    except Exception as e:
        log.error(f"Adminga bekor qilish xabarini yuborishda xatolik: {e}")

    return {"ok": True}


# ============================================================
# 5-bo'lim — ADMIN STATISTIKA
# ============================================================
@app.get("/api/admin/stats")
async def admin_stats(_=Depends(check_admin)):
    conn = db()
    now = datetime.now()
    today_start = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=7)).isoformat(timespec="seconds")
    month_start = (now - timedelta(days=30)).isoformat(timespec="seconds")

    def revenue_since(since_iso):
        r = conn.execute(
            "SELECT COALESCE(SUM(total),0) s, COUNT(*) c FROM orders WHERE created_at>=? AND status!='bekor'",
            (since_iso,),
        ).fetchone()
        return {"total": r["s"], "count": r["c"]}

    today = revenue_since(today_start)
    week = revenue_since(week_start)
    month = revenue_since(month_start)

    rows = conn.execute(
        "SELECT items_json FROM orders WHERE created_at>=? AND status!='bekor'", (month_start,)
    ).fetchall()
    item_counts = defaultdict(int)
    for r in rows:
        for it in json.loads(r["items_json"] or "[]"):
            item_counts[it["name"]] += it.get("qty", 0)
    top_items = sorted(item_counts.items(), key=lambda x: -x[1])[:10]

    active_users = conn.execute(
        "SELECT COUNT(DISTINCT telegram_user_id) c FROM orders WHERE created_at>=? AND telegram_user_id!=''",
        (month_start,),
    ).fetchone()["c"]

    conn.close()
    return {
        "today": today,
        "week": week,
        "month": month,
        "top_items": [{"name": n, "qty": q} for n, q in top_items],
        "active_users_30d": active_users,
    }


# ============================================================
# 6-bo'lim — MARKETING: PROMOKODLAR VA BROADCAST
# ============================================================
@app.get("/api/admin/promo")
async def admin_list_promo(_=Depends(check_admin)):
    conn = db()
    rows = conn.execute("SELECT * FROM promo_codes ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/promo")
async def admin_add_promo(request: Request, _=Depends(check_admin)):
    data = await request.json()
    conn = db()
    try:
        conn.execute(
            """INSERT INTO promo_codes (code, discount_percent, min_order, active, usage_limit, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (
                str(data.get("code", "")).strip().upper(),
                int(data.get("discount_percent", 0)),
                int(data.get("min_order", 0)),
                int(bool(data.get("active", True))),
                int(data.get("usage_limit", 0)),
                data.get("expires_at") or None,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu promokod allaqachon mavjud")
    conn.close()
    return {"ok": True}


@app.delete("/api/admin/promo/{promo_id}")
async def admin_delete_promo(promo_id: int, _=Depends(check_admin)):
    conn = db()
    conn.execute("DELETE FROM promo_codes WHERE id=?", (promo_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request, _=Depends(check_admin)):
    data = await request.json()
    text = str(data.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Xabar matni bo'sh")

    conn = db()
    ids = [
        r["telegram_user_id"]
        for r in conn.execute("SELECT DISTINCT telegram_user_id FROM users WHERE telegram_user_id IS NOT NULL").fetchall()
    ]
    conn.close()

    sent, failed = 0, 0
    for uid in ids:
        try:
            await bot.send_message(chat_id=int(uid), text=text)
            sent += 1
        except Exception as e:
            failed += 1
            log.warning(f"Broadcast xatoligi ({uid}): {e}")
    return {"ok": True, "sent": sent, "failed": failed, "total": len(ids)}


# ============================================================
# Admin panel HTML
# ============================================================
# ============================================================
# PWA — admin panelni telefon ekraniga "ilova" sifatida qo'shish uchun
# ============================================================
@app.get("/manifest.json")
async def manifest():
    return FileResponse("manifest.json", media_type="application/manifest+json")


@app.get("/icon-192.png")
async def icon192():
    return FileResponse("icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
async def icon512():
    return FileResponse("icon-512.png", media_type="image/png")


@app.get("/admin")
async def admin_page():
    return FileResponse("admin.html")


@app.get("/courier")
async def courier_page():
    return FileResponse("courier.html")


# ============================================================
# Telegram bot
# ============================================================
@dp.message(CommandStart())
async def start_handler(message: Message, command: CommandObject = None):
    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text="🍽 Menyu", web_app=WebAppInfo(url=WEBAPP_URL)),
    )
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍽 Buyurtma berish", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )

    user_id = str(message.from_user.id)
    name = message.from_user.full_name

    # 6-bo'lim: referal — /start?start=ref_<telegram_id> orqali
    referred_by = None
    payload = command.args if command else None
    if payload and payload.startswith("ref_"):
        referred_by = payload[4:]

    conn = db()
    existing = conn.execute("SELECT 1 FROM users WHERE telegram_user_id=?", (user_id,)).fetchone()
    conn.close()
    is_new = existing is None

    upsert_user(user_id, name=name, referred_by=referred_by if is_new else None)

    if is_new and referred_by and referred_by != user_id:
        conn = db()
        try:
            conn.execute(
                "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)",
                (referred_by, user_id, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            await bot.send_message(
                chat_id=int(referred_by),
                text=f"🎉 {name} sizning taklifingiz orqali botga qo'shildi! Rahmat.",
            )
        except Exception as e:
            log.warning(f"Referal yozishda xatolik: {e}")
        conn.close()

    await message.answer(
        "Assalomu alaykum! 👋\n\nOsiyo Cafe botiga xush kelibsiz.\n"
        "Buyurtma berish uchun pastdagi tugmani bosing 👇",
        reply_markup=keyboard,
    )


@app.on_event("startup")
async def on_startup():
    global BOT_USERNAME
    init_db()
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    except Exception as e:
        log.error(f"Bot ma'lumotini olishda xatolik: {e}")
    import asyncio
    asyncio.create_task(dp.start_polling(bot, handle_signals=False))
