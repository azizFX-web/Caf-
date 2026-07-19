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
  PUBLIC_URL          -> Railway bergan ochiq domen, masalan https://cafe-bot-production.up.railway.app
  CLICK_MERCHANT_ID   -> Click biznes kabinetidan
  CLICK_SERVICE_ID    -> Click biznes kabinetidan
  CLICK_SECRET_KEY    -> Click biznes kabinetidan
"""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime

from fastapi import FastAPI, Request, Response, UploadFile, Form, File, HTTPException, Cookie, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, MenuButtonWebApp

# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8624003978:AAG_eiZI0LA1t6lsO94-DLkTUkXy8brieK8")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "8203697473")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "cafe2026")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://cafe-menuae.netlify.app")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")  # masalan https://xxx.up.railway.app

CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "")
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")
# ------------------------------------------------------------------

DB_PATH = os.getenv("DB_PATH", "cafe.db")
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_TOKEN = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()

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


# ============================================================
# DATABASE
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


# ============================================================
# PUBLIC API — Mini App shu yerdan foydalanadi
# ============================================================
@app.get("/api/menu")
async def get_menu():
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

    conn = db()
    cur = conn.execute(
        """INSERT INTO orders (created_at, items_json, total, delivery_type, payment_type,
           phone, address, comment, customer_name, telegram_user_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(items, ensure_ascii=False),
            total, delivery_type, payment_type, phone, address, comment,
            customer_name, telegram_user_id,
        ),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Adminga xabar
    delivery_map = {"olib_ketish": "Olib ketish", "yetkazish": "Yetkazib berish"}
    payment_map = {"naqd": "Naqd pul", "karta": "Karta orqali (Click)"}
    items_text = "\n".join(f"• {i['name']} × {i['qty']} — {i['price']*i['qty']:,} so'm".replace(",", " ") for i in items)
    text = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n{items_text}\n\n"
        f"💰 <b>Jami:</b> {total:,} so'm\n".replace(",", " ") +
        f"🚚 <b>Turi:</b> {delivery_map.get(delivery_type,'-')}\n"
        f"💳 <b>To'lov:</b> {payment_map.get(payment_type,'-')}\n"
        f"📞 <b>Telefon:</b> {phone}\n"
    )
    if delivery_type == "yetkazish":
        text += f"📍 <b>Manzil:</b> {address}\n"
    if comment:
        text += f"📝 <b>Izoh:</b> {comment}\n"
    if customer_name:
        text += f"\n👤 Mijoz: {customer_name}"

    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e:
        print("Telegram xabar yuborishda xatolik:", e)

    result = {"order_id": order_id}

    if payment_type == "karta" and CLICK_MERCHANT_ID and CLICK_SERVICE_ID:
        pay_url = (
            "https://my.click.uz/services/pay"
            f"?service_id={CLICK_SERVICE_ID}&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={total}&transaction_param={order_id}"
        )
        result["click_pay_url"] = pay_url

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


@app.patch("/api/admin/orders/{order_id}")
async def admin_update_order(order_id: int, request: Request, _=Depends(check_admin)):
    data = await request.json()
    status = data.get("status")
    conn = db()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================
# Admin panel HTML
# ============================================================
@app.get("/admin")
async def admin_page():
    return FileResponse("admin.html")


# ============================================================
# Telegram bot
# ============================================================
@dp.message(CommandStart())
async def start_handler(message: Message):
    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text="🍽 Menyu", web_app=WebAppInfo(url=WEBAPP_URL)),
    )
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍽 Buyurtma berish", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )
    await message.answer(
        "Assalomu alaykum! 👋\n\nOsiyo Cafe botiga xush kelibsiz.\n"
        "Buyurtma berish uchun pastdagi tugmani bosing 👇",
        reply_markup=keyboard,
    )


@app.on_event("startup")
async def on_startup():
    init_db()
    import asyncio
    asyncio.create_task(dp.start_polling(bot, handle_signals=False))
