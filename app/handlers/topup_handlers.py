# app/handlers/topup_handlers.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import qrcode
import io
import time
import traceback
import random
import re
import requests
import os
import sqlite3
import logging

from app.service.auth import AuthInstance
from app.service.balance_service import BalanceServiceInstance

# Import client functions (assume app/client/atlantic.py provides these)
from app.client.atlantic import (
    get_deposit_methods,
    create_deposit_request,
    request_instant_deposit,
    check_deposit_status,
)

from .user_handlers import show_main_menu_bot
from app.config import (
    user_states,
    USER_STATE_ENTER_TOPUP_AMOUNT,
    reff_id_to_chat_id_map,
    USER_STATE_ENTER_DEPOSIT_ID,
)

logger = logging.getLogger(__name__)
# ensure logger prints (if main config doesn't)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Module-level pending deposit store (in-memory)
global_pending_deposits = {}  # unique_code -> deposit info dict

# DB file for persistence across restarts (optional)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "pending_deposits.db")

# job name for periodic check
JOB_NAME = "qris_pending_checker"

# ensure data dir & db table exist
def ensure_db():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_deposits (
                unique_code TEXT PRIMARY KEY,
                user_id INTEGER,
                amount INTEGER,
                original_amount INTEGER,
                timestamp INTEGER,
                status TEXT,
                qr_message_id INTEGER,
                deposit_id TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to ensure DB: %s", e)

ensure_db()

def db_insert_pending(unique_code, user_id, amount, original_amount, timestamp, status, qr_message_id, deposit_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO pending_deposits (unique_code, user_id, amount, original_amount, timestamp, status, qr_message_id, deposit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (unique_code, user_id, amount, original_amount, timestamp, status, qr_message_id, deposit_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("DB insert error: %s", e)

def db_delete_pending(unique_code):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM pending_deposits WHERE unique_code = ?", (unique_code,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("DB delete error: %s", e)

def db_load_all_pending():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT unique_code, user_id, amount, original_amount, timestamp, status, qr_message_id, deposit_id FROM pending_deposits")
        rows = cur.fetchall()
        conn.close()
        res = {}
        for r in rows:
            res[r[0]] = {
                "unique_code": r[0],
                "userId": int(r[1]),
                "amount": int(r[2]),
                "original_amount": int(r[3]),
                "timestamp": int(r[4]),
                "status": r[5],
                "qr_message_id": r[6],
                "deposit_id": r[7]
            }
        return res
    except Exception as e:
        logger.error("DB load error: %s", e)
        return {}

# load persisted pending into memory on import
try:
    global_pending_deposits.update(db_load_all_pending())
    logger.info("Loaded %d pending deposits from DB", len(global_pending_deposits))
except Exception as e:
    logger.error("Failed load persisted pending deposits: %s", e)

# small helpers
def generate_random_number(a=1, b=300):
    return random.randint(a, b)

def is_url(s: str) -> bool:
    if not s:
        return False
    s = str(s).strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("ftp://")

# ----- Topup handlers -----

async def topup_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    active_user = AuthInstance.get_active_user(chat_id)
    if not active_user:
        await context.bot.send_message(chat_id=chat_id, text="Silakan login terlebih dahulu.")
        return

    balance = BalanceServiceInstance.get_balance(chat_id)
    message = (f"Saldo Aplikasi Anda saat ini: *Rp {balance:,.0f}*\n\n"
               "Silakan pilih metode Top Up di bawah ini:")
    keyboard = [
        [InlineKeyboardButton("🤖 Top Up Otomatis (QRIS INSTANT)", callback_data='topup_auto')],
        [InlineKeyboardButton("« Kembali ke Menu Utama", callback_data='menu_back_main')]
    ]
    await query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def topup_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    action = query.data

    if action == 'topup_auto':
        await query.message.edit_text("Silakan masukkan jumlah saldo yang ingin Anda top up via QRIS INSTANT (contoh: 50000).")
        user_states[chat_id] = USER_STATE_ENTER_TOPUP_AMOUNT

# ----- New: process deposit similar to the JS flow -----
async def topup_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    text = update.message.text
    if user_states.get(chat_id) != USER_STATE_ENTER_TOPUP_AMOUNT:
        return False

    try:
        amount = int(text.strip())
        if amount < 1000:
            await update.message.reply_text("Jumlah top up minimal adalah Rp 1,000.")
            return True

        # generate unique code and final amount
        userId = chat_id
        unique_code = f"user-{userId}-{int(time.time()*1000)}"
        suffix = generate_random_number(1, 300)
        final_amount = int(amount) + int(suffix)
        admin_fee = final_amount - int(amount)

        user_states.pop(chat_id, None)
        msg = await update.message.reply_text("⏳ Membuat invoice QRIS dan memproses pembayaran...")

        # use create_deposit_request to create QR (this should call Raja server as implemented)
        deposit_data = create_deposit_request(final_amount, None, None, unique_code)

        if not deposit_data:
            await msg.edit_text("❌ Gagal membuat QRIS. Silakan coba lagi nanti.")
            return True

        # normalize returned data
        deposit_id = deposit_data.get('id') or unique_code
        final_amount_ret = deposit_data.get('nominal') or deposit_data.get('amount') or final_amount
        image_url = deposit_data.get('image_url') or deposit_data.get('image') or deposit_data.get('imageqris') or None
        qr_string = deposit_data.get('qr_string') or deposit_data.get('qr') or None

        caption = (
            f"📝 *Detail Pembayaran:*\n\n"
            f"💰 Jumlah: Rp {int(final_amount_ret):,}\n"
            f"- Nominal Top Up: Rp {int(amount):,}\n"
            f"- Admin Fee : Rp {int(admin_fee):,}\n\n"
            f"⚠️ *Penting:* Mohon transfer sesuai nominal\n"
            f"⏱️ Waktu: 5 menit\n\n"
            f"⚠️ *Catatan:*\n"
            f"- Pembayaran akan otomatis terverifikasi\n"
            f"- Jika pembayaran berhasil, saldo akan otomatis ditambahkan"
        )

        # send QR as photo (prefer image_url)
        try:
            if image_url and is_url(str(image_url)):
                # download image to buffer
                resp = requests.get(image_url, timeout=20)
                resp.raise_for_status()
                buffer = io.BytesIO(resp.content)
                buffer.seek(0)
                qr_msg = await context.bot.send_photo(chat_id=chat_id, photo=buffer, caption=caption, parse_mode="Markdown")
            elif qr_string:
                # if qr_string is payload text -> generate QR image
                qr_img = qrcode.make(qr_string)
                buffer = io.BytesIO()
                qr_img.save(buffer, "PNG")
                buffer.seek(0)
                qr_msg = await context.bot.send_photo(chat_id=chat_id, photo=buffer, caption=caption, parse_mode="Markdown")
            else:
                await msg.edit_text("❌ Provider tidak mengembalikan QR. Silakan coba lagi nanti.")
                return True
        except Exception as e:
            logger.error("Failed to send QR image: %s", e)
            await msg.edit_text("❌ Gagal mengirim QRIS. Silakan coba lagi nanti.")
            return True

        # try delete the user's input message (like JS)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception:
            pass

        # persist pending deposit in memory & DB
        pending = {
            "unique_code": unique_code,
            "amount": int(final_amount_ret),
            "original_amount": int(amount),
            "userId": int(userId),
            "timestamp": int(time.time()*1000),
            "status": "pending",
            "qr_message_id": getattr(qr_msg, "message_id", None),
            "deposit_id": deposit_id
        }
        global_pending_deposits[unique_code] = pending
        db_insert_pending(unique_code, userId, int(final_amount_ret), int(amount), pending["timestamp"], "pending", pending["qr_message_id"], deposit_id)
        logger.info("Created pending deposit %s for user %s amount %s", unique_code, userId, final_amount_ret)

        # ensure periodic checker job is scheduled (once)
        # schedule every 20 seconds
        try:
            jobs = context.job_queue.get_jobs_by_name(JOB_NAME)
            if not jobs:
                context.job_queue.run_repeating(check_qris_status_job, interval=20, first=20, name=JOB_NAME)
                logger.info("Scheduled periodic QRIS checker job")
        except Exception as e:
            logger.error("Failed to schedule job queue: %s", e)

        return True

    except (ValueError, TypeError):
        await update.message.reply_text("Input tidak valid. Harap masukkan angka saja.")
    except Exception as e:
        logger.error("Error in topup_amount_handler: %s", traceback.format_exc())
        await update.message.reply_text(f"Terjadi error teknis: `{str(e)}`", parse_mode="Markdown")

    return True

# ---- prompt and handle deposit ID input (unchanged) ----
async def check_deposit_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

async def prompt_deposit_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="Silakan masukkan ID Deposit (Transaction ID) yang ingin Anda cek:")
    user_states[chat_id] = USER_STATE_ENTER_DEPOSIT_ID

async def handle_deposit_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    if user_states.get(chat_id) != USER_STATE_ENTER_DEPOSIT_ID:
        return False

    deposit_id = update.message.text.strip()
    msg = await update.message.reply_text(f"🔎 Mengecek status untuk ID: `{deposit_id}`...", parse_mode="Markdown")
    user_states.pop(chat_id, None)

    status_data = check_deposit_status(deposit_id)

    if status_data:
        # best-effort parsing: if provider returned normalized dict, use fields; else show raw
        if isinstance(status_data, dict):
            status_text = str(status_data.get("status", "N/A"))
            nominal = status_data.get("nominal", 0) or status_data.get("amount", 0)
            pesan = (
                f"Berikut adalah status transaksi Anda:\n\n"
                f"<b>ID Deposit:</b> {status_data.get('id', deposit_id)}\n"
                f"<b>Reff ID Anda:</b> {status_data.get('reff_id', '')}\n"
                f"<b>Metode:</b> {status_data.get('metode', 'QRIS')}\n"
                f"<b>Nominal:</b> Rp {int(nominal):,}\n"
                f"<b>Dibuat Pada:</b> {status_data.get('created_at','N/A')}\n"
                f"<b>Status:</b> {status_text}\n"
            )
            await msg.edit_text(pesan, parse_mode="HTML")
        else:
            # unknown format, just print raw
            await msg.edit_text("Hasil pengecekan (raw):\n" + str(status_data))
    else:
        await msg.edit_text("❌ ID Deposit tidak ditemukan atau terjadi kesalahan saat pengecekan.")

    return True

# ----- Periodic checker job -----
async def check_qris_status_job(context: ContextTypes.DEFAULT_TYPE):
    """
    JobQueue callback run periodically.
    For each pending deposit in global_pending_deposits:
      - If older than 5 minutes -> mark expired, delete message, notify user & DB
      - Else call check_deposit_status (which for ORKUT may return text or json) and try to find a matching transaction
      - If match found -> process success (notify user, TODO credit balance), cleanup
    """
    try:
        now_ms = int(time.time()*1000)
        # copy keys to avoid runtime dict change
        for unique_code, deposit in list(global_pending_deposits.items()):
            try:
                # skip non-pending
                if deposit.get("status") != "pending":
                    continue

                age_ms = now_ms - int(deposit.get("timestamp", now_ms))
                # expire after 5 minutes (300000 ms)
                if age_ms > 5 * 60 * 1000:
                    # delete QR message if exists
                    try:
                        if deposit.get("qr_message_id"):
                            await context.bot.delete_message(chat_id=deposit["userId"], message_id=deposit["qr_message_id"])
                    except Exception:
                        pass
                    # notify user
                    try:
                        await context.bot.send_message(deposit["userId"], "❌ *Pembayaran Expired*\n\nWaktu pembayaran telah habis. Silakan klik Top Up lagi untuk mendapatkan QR baru.", parse_mode="Markdown")
                    except Exception:
                        pass
                    # cleanup
                    db_delete_pending(unique_code)
                    del global_pending_deposits[unique_code]
                    continue

                # check provider for transactions (call check_deposit_status WITHOUT id to fetch recent)
                raw = None
                try:
                    raw = check_deposit_status(None)
                except Exception as e:
                    logger.error("check_deposit_status error: %s", e)
                    raw = None

                transactions = []
                # If raw is str -> parse like JS
                if isinstance(raw, str):
                    blocks = [b.strip() for b in raw.split('------------------------') if b.strip()]
                    for block in blocks:
                        kredit_match = re.search(r'Kredit\s*:\s*([\d\.]+)', block)
                        tanggal_match = re.search(r'Tanggal\s*:\s*(.+)', block)
                        brand_match = re.search(r'Brand\s*:\s*(.+)', block)
                        if kredit_match:
                            kredit_val = int(kredit_match.group(1).replace('.', ''))
                            transaksi = {
                                "tanggal": tanggal_match.group(1).strip() if tanggal_match else "-",
                                "kredit": kredit_val,
                                "brand": brand_match.group(1).strip() if brand_match else "-"
                            }
                            transactions.append(transaksi)
                elif isinstance(raw, dict):
                    # If dict with 'data' as list
                    candidate_list = []
                    if "data" in raw and isinstance(raw["data"], list):
                        candidate_list = raw["data"]
                    elif isinstance(raw.get("result"), list):
                        candidate_list = raw.get("result")
                    elif isinstance(raw, list):
                        candidate_list = raw
                    # try to extract numeric fields
                    for it in candidate_list:
                        try:
                            kredit = None
                            for k in ("kredit","amount","nominal","jumlah","total"):
                                if k in it:
                                    try:
                                        kredit = int(str(it[k]).replace('.',''))
                                    except Exception:
                                        kredit = None
                                        continue
                            if kredit:
                                transactions.append({"tanggal": it.get("tanggal") or it.get("date") or "", "kredit": kredit, "brand": it.get("brand") or it.get("merchant") or ""})
                        except Exception:
                            continue
                else:
                    # unknown format -> skip
                    transactions = []

                # debug
                if transactions:
                    logger.debug("Parsed transactions for %s: %s", unique_code, transactions)

                # find a transaction matching expected amount
                expected = int(deposit["amount"])
                matched = None
                for t in transactions:
                    try:
                        if int(t.get("kredit")) == expected:
                            matched = t
                            break
                    except Exception:
                        continue

                if matched:
                    # success! notify user, cleanup, and (TODO) credit balance
                    try:
                        await context.bot.send_message(deposit["userId"], f"✅ *Pembayaran Terdeteksi*\n\nJumlah: Rp {int(expected):,}\nBrand: {matched.get('brand','-')}\nTanggal: {matched.get('tanggal','-')}\n\nSaldo akan dikreditkan otomatis jika sistem mendukungnya.", parse_mode="Markdown")
                    except Exception:
                        pass

                    # TODO: credit user's balance here, e.g.:
                    # BalanceServiceInstance.add_balance(deposit["userId"], deposit["original_amount"])
                    # atau panggil fungsi internal yang sesuai.
                    # Karena struktur internal balance service tidak di-spesifikasikan, saya hanya letakkan TODO.

                    # cleanup DB & memory
                    try:
                        db_delete_pending(unique_code)
                    except Exception:
                        pass
                    try:
                        del global_pending_deposits[unique_code]
                    except KeyError:
                        pass

                    # delete QR message
                    try:
                        if deposit.get("qr_message_id"):
                            await context.bot.delete_message(chat_id=deposit["userId"], message_id=deposit["qr_message_id"])
                    except Exception:
                        pass

            except Exception as inner_e:
                logger.error("Error processing pending %s: %s", unique_code, inner_e)
                continue

    except Exception as e:
        logger.error("check_qris_status_job top-level error: %s", e)
