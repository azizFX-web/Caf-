"""
Cafe Telegram Bot — buyurtma qabul qiluvchi bot
=================================================
Bu bot foydalanuvchiga "Menyu" tugmasi orqali Mini App (suzuvchi oyna)
ochadi. Foydalanuvchi u yerda buyurtma tuzib "Tasdiqlash" tugmasini
bosgach, buyurtma ma'lumotlari shu botga qaytadi va siz belgilagan
ADMIN_CHAT_ID ga (masalan, sizning shaxsiy chatingiz yoki xodimlar
guruhi) chiroyli qilib yuboriladi.

ISHGA TUSHIRISH:
  1. pip install -r requirements.txt
  2. Quyidagi 3 ta qiymatni to'ldiring (pastda, yoki .env fayl orqali):
       BOT_TOKEN      -> @BotFather dan olingan token
       WEBAPP_URL     -> index.html joylashgan HTTPS manzil
       ADMIN_CHAT_ID  -> buyurtmalar tushadigan chat ID
  3. python bot.py
"""

import asyncio
import json
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    MenuButtonWebApp,
)

logging.basicConfig(level=logging.INFO)

# ------------------------------------------------------------------
# SOZLAMALAR — o'zingizning qiymatlaringizni shu yerga kiriting
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8624003978:AAG_eiZI0LA1t6lsO94-DLkTUkXy8brieK8")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://cafe-menuae.netlify.app")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "8203697473")
# ------------------------------------------------------------------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    # Chat menyusi tugmasi (chap pastdagi "Menu") ham WebApp ochadi
    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text="🍽 Menyu", web_app=WebAppInfo(url=WEBAPP_URL)),
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍽 Buyurtma berish", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )
    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "Osiyo Cafe botiga xush kelibsiz.\n"
        "Buyurtma berish uchun pastdagi tugmani bosing 👇",
        reply_markup=keyboard,
    )


@dp.message(F.web_app_data)
async def webapp_data_handler(message: Message):
    """Mini App'dan kelgan buyurtmani qabul qilib, formatlaydi va yuboradi."""
    try:
        data = json.loads(message.web_app_data.data)
    except (ValueError, AttributeError):
        await message.answer("Buyurtmani o'qishda xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return

    items_text = "\n".join(
        f"• {i['name']} × {i['qty']} — {i['price'] * i['qty']:,} so'm".replace(",", " ")
        for i in data.get("items", [])
    )

    delivery_map = {"olib_ketish": "Olib ketish", "yetkazish": "Yetkazib berish"}
    payment_map = {"naqd": "Naqd pul", "karta": "Karta orqali"}

    order_text = (
        "🆕 <b>Yangi buyurtma!</b>\n\n"
        f"{items_text}\n\n"
        f"💰 <b>Jami:</b> {data.get('total', 0):,} so'm\n".replace(",", " ") +
        f"🚚 <b>Turi:</b> {delivery_map.get(data.get('delivery_type'), '-')}\n"
        f"💳 <b>To'lov:</b> {payment_map.get(data.get('payment_type'), '-')}\n"
        f"📞 <b>Telefon:</b> {data.get('phone', '-')}\n"
    )
    if data.get("delivery_type") == "yetkazish":
        order_text += f"📍 <b>Manzil:</b> {data.get('address', '-')}\n"
    if data.get("comment"):
        order_text += f"📝 <b>Izoh:</b> {data.get('comment')}\n"

    user = message.from_user
    order_text += f"\n👤 Mijoz: {user.full_name} (@{user.username or '—'})"

    # Admin/xodimlar chatiga yuborish
    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=order_text, parse_mode="HTML")

    # Mijozga tasdiqlash
    await message.answer(
        "✅ Buyurtmangiz qabul qilindi! Tez orada siz bilan bog'lanamiz.",
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
