# bot_main.py
# === Telegram Bot Wrapper untuk me-cli ===
# by ChatGPT (GPT-5)

import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)

# ====== Impor semua fungsi utama dari CLI asli ======
from app.service.auth import AuthInstance
from app.client.engsel import get_balance, get_tiering_info
from app.menus.package import fetch_my_packages, get_packages_by_family, show_package_details
from app.menus.hot import show_hot_menu, show_hot_menu2
from app.menus.payment import show_transaction_history
from app.menus.famplan import show_family_info
from app.menus.circle import show_circle_info
from app.menus.store.segments import show_store_segments_menu
from app.menus.store.search import show_family_list_menu, show_store_packages_menu
from app.menus.store.redemables import show_redeemables_menu
from app.client.registration import dukcapil
from app.menus.bookmark import show_bookmark_menu
from app.menus.notification import show_notification_menu
from app.menus.account import show_account_menu
from app.menus.purchase import purchase_by_family
from app.client.famplan import validate_msisdn

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN tidak ditemukan di file .env")

# ====== Helper ======
def _active_user():
    try:
        return AuthInstance.get_active_user()
    except TypeError:
        return AuthInstance.get_active_user(None)

def _profile_text(user):
    bal = get_balance(AuthInstance.api_key, user["tokens"]["id_token"])
    remain = bal.get("remaining", "0")
    exp = datetime.fromtimestamp(bal.get("expired_at", 0)).strftime("%Y-%m-%d")
    tier = get_tiering_info(AuthInstance.api_key, user["tokens"])
    return (f"<b>Nomor:</b> {user['number']} ({user['subscription_type']})\n"
            f"<b>Pulsa:</b> Rp {remain} (Aktif s.d. {exp})\n"
            f"Points: {tier.get('current_point','N/A')} | Tier: {tier.get('tier','N/A')}")

def _menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Ganti Akun", callback_data="m:1"), InlineKeyboardButton("2️⃣ Paket Saya", callback_data="m:2")],
        [InlineKeyboardButton("3️⃣ HOT 🔥", callback_data="m:3"), InlineKeyboardButton("4️⃣ HOT-2 🔥", callback_data="m:4")],
        [InlineKeyboardButton("5️⃣ By Option", callback_data="m:5"), InlineKeyboardButton("6️⃣ By Family", callback_data="m:6")],
        [InlineKeyboardButton("7️⃣ Loop Family", callback_data="m:7")],
        [InlineKeyboardButton("8️⃣ Riwayat", callback_data="m:8"), InlineKeyboardButton("9️⃣ Family Plan", callback_data="m:9")],
        [InlineKeyboardButton("🔟 Circle", callback_data="m:10")],
        [InlineKeyboardButton("📊 Store Segments", callback_data="m:11")],
        [InlineKeyboardButton("📚 Family List", callback_data="m:12")],
        [InlineKeyboardButton("📦 Store Packages", callback_data="m:13")],
        [InlineKeyboardButton("🎟️ Redeemables", callback_data="m:14")],
        [InlineKeyboardButton("🔖 Bookmark", callback_data="m:15"), InlineKeyboardButton("🔔 Notifikasi", callback_data="m:16")],
        [InlineKeyboardButton("🧾 Register", callback_data="m:R"), InlineKeyboardButton("✅ Validate", callback_data="m:V")],
        [InlineKeyboardButton("⟳ Refresh", callback_data="m:refresh")]
    ])

# ====== Command / Menu ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _active_user()
    if not user:
        await update.message.reply_text("Silakan login dulu di menu akun CLI (belum login).", parse_mode="HTML")
        return
    text = _profile_text(user)
    await update.message.reply_text(f"<b>ME-CLI Telegram Bot</b>\n{text}\n\n<b>Pilih menu di bawah:</b>", 
                                    parse_mode="HTML", reply_markup=_menu_kb())

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = _active_user()

    if not user and data not in {"m:1"}:
        await q.edit_message_text("Belum login di ME-CLI. Jalankan menu akun dulu.", parse_mode="HTML")
        return

    # Menu mapping
    if data == "m:1":
        show_account_menu(); await q.message.reply_text("Akun login diubah."); return
    if data == "m:2":
        fetch_my_packages(); await q.message.reply_text("Paket ditampilkan."); return
    if data == "m:3":
        show_hot_menu(); await q.message.reply_text("🔥 HOT 1 tampil."); return
    if data == "m:4":
        show_hot_menu2(); await q.message.reply_text("🔥 HOT 2 tampil."); return
    if data == "m:5":
        await q.message.reply_text("Ketik Option Code:")
        context.user_data["await"] = "option"
        return
    if data == "m:6":
        await q.message.reply_text("Ketik Family Code:")
        context.user_data["await"] = "family"
        return
    if data == "m:7":
        await q.message.reply_text("Ketik Family Code (loop):")
        context.user_data["await"] = "loop"
        return
    if data == "m:8":
        show_transaction_history(AuthInstance.api_key, user["tokens"]); await q.message.reply_text("Riwayat tampil."); return
    if data == "m:9":
        show_family_info(AuthInstance.api_key, user["tokens"]); await q.message.reply_text("Family info tampil."); return
    if data == "m:10":
        show_circle_info(AuthInstance.api_key, user["tokens"]); await q.message.reply_text("Circle tampil."); return
    if data == "m:11":
        show_store_segments_menu(False); await q.message.reply_text("Store segments tampil."); return
    if data == "m:12":
        show_family_list_menu(user['subscription_type'], False); await q.message.reply_text("Family list tampil."); return
    if data == "m:13":
        show_store_packages_menu(user['subscription_type'], False); await q.message.reply_text("Store packages tampil."); return
    if data == "m:14":
        show_redeemables_menu(False); await q.message.reply_text("Redeemables tampil."); return
    if data == "m:15":
        show_bookmark_menu(); await q.message.reply_text("Bookmark tampil."); return
    if data == "m:16":
        show_notification_menu(); await q.message.reply_text("Notifikasi tampil."); return
    if data == "m:R":
        await q.message.reply_text("Masukkan MSISDN (628xxx):")
        context.user_data["await"] = "reg_msisdn"
        return
    if data == "m:V":
        await q.message.reply_text("Masukkan MSISDN untuk validasi:")
        context.user_data["await"] = "validate"
        return
    if data == "m:refresh":
        await start(update, context)
        return

async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    user = _active_user()
    await_state = context.user_data.get("await")

    if await_state == "option":
        show_package_details(AuthInstance.api_key, user["tokens"], txt, False)
        await update.message.reply_text("Detail paket tampil.")
    elif await_state == "family":
        get_packages_by_family(txt); await update.message.reply_text("Paket family tampil.")
    elif await_state == "loop":
        purchase_by_family(txt, False, False, 0, 1); await update.message.reply_text("Loop family dieksekusi.")
    elif await_state == "reg_msisdn":
        context.user_data["msisdn"] = txt
        context.user_data["await"] = "reg_nik"
        await update.message.reply_text("Masukkan NIK:")
        return
    elif await_state == "reg_nik":
        context.user_data["nik"] = txt
        context.user_data["await"] = "reg_kk"
        await update.message.reply_text("Masukkan KK:")
        return
    elif await_state == "reg_kk":
        msisdn = context.user_data.pop("msisdn")
        nik = context.user_data.pop("nik")
        dukcapil(AuthInstance.api_key, msisdn, txt, nik)
        await update.message.reply_text("Registrasi dikirim.")
    elif await_state == "validate":
        validate_msisdn(AuthInstance.api_key, user["tokens"], txt)
        await update.message.reply_text("Nomor tervalidasi.")
    context.user_data.pop("await", None)

async def _post_init(app: Application):
    try: await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception: pass

def main():
    print("🚀 Bot ME-CLI dijalankan...")
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(router, pattern="^m:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
