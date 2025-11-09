# topup_handlers.py

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import urllib.parse
import qrcode
import io
import time
import traceback

from app.service.auth import AuthInstance
from app.service.balance_service import BalanceServiceInstance
from app.client.atlantic import get_deposit_methods, create_deposit_request, request_instant_deposit, check_deposit_status

from .user_handlers import show_main_menu_bot
from app.config import (
    user_states, 
    USER_STATE_ENTER_TOPUP_AMOUNT, 
    reff_id_to_chat_id_map, 
    USER_STATE_ENTER_DEPOSIT_ID,
    # 1. TAMBAHKAN IMPORT INI di app/config.py
    USER_STATE_AWAIT_MANUAL_PROOF 
)


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
    keyboard = [[InlineKeyboardButton("💳 Top Up Manual (Admin)", callback_data='topup_manual')],
                [InlineKeyboardButton("🤖 Top Up Otomatis (QRIS INSTANT)", callback_data='topup_auto')],
                [InlineKeyboardButton("« Kembali ke Menu Utama", callback_data='menu_back_main')]]
    await query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def topup_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    action = query.data

    if action == 'topup_auto':
        await query.message.edit_text("Silakan masukkan jumlah saldo yang ingin Anda top up via QRIS INSTANT (contoh: 50000).")
        user_states[chat_id] = USER_STATE_ENTER_TOPUP_AMOUNT
    
    elif action == 'topup_manual':
        # --- LOGIKA BARU UNTUK TOPUP MANUAL ---
        
        # 1. Hapus pesan menu sebelumnya
        await query.message.delete()
        
        # 2. Set state pengguna
        user_states[chat_id] = USER_STATE_AWAIT_MANUAL_PROOF
        
        # 3. URL Gambar QRIS dari GitHub
        qris_image_url = 'https://raw.githubusercontent.com/mudziboy/mdzitr/refs/heads/main/qris.png'
        
        # 4. Kirim pesan instruksi
        text_message = '💰 *Silakan scan QRIS berikut untuk melakukan top-up saldo Anda.*\n\n📌 Admin mungkin butuh waktu untuk memvalidasi bukti, gunakan topup otomatis jika ingin cepat.'
        await context.bot.send_message(chat_id=chat_id, text=text_message, parse_mode="Markdown")
        
        # 5. Kirim foto QRIS dengan caption
        caption_text = 'Scan atau klik foto di atas.\nSetelah bayar, *kirim bukti (foto) transfer* ke bot ini.\nProses 1-2 menit, kecuali sedang sibuk atau tidur.'
        qris_message = await context.bot.send_photo(
            chat_id=chat_id, 
            photo=qris_image_url, 
            caption=caption_text, 
            parse_mode="Markdown"
        )
        
        # 6. Set timeout 5 menit menggunakan JobQueue
        context.job_queue.run_once(
            manual_topup_timeout, 
            when=300,  # 300 detik = 5 menit
            data={'chat_id': chat_id, 'message_id': qris_message.message_id},
            name=f"manual_timeout_{chat_id}" # Beri nama job agar bisa dibatalkan
        )
        # --- AKHIR LOGIKA BARU ---

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

        user_states.pop(chat_id, None)
        msg = await update.message.reply_text("⏳ Mencari metode QRIS INSTANT dan membuat invoice...")
        
        all_methods = get_deposit_methods()
        qris_instant_method = None
        
        if all_methods is None:
             await msg.edit_text("❌ Gagal mengambil daftar metode pembayaran dari server.")
             return True

        for method in all_methods:
            if method.get('name', '').upper() == 'QRIS INSTANT':
                qris_instant_method = method
                break
        
        if not qris_instant_method:
            await msg.edit_text("❌ Gagal menemukan metode 'QRIS INSTANT' di akun Anda.")
            return True

        method_code = qris_instant_method.get('metode')
        method_type = qris_instant_method.get('type')
        reff_id = f"TOPUP-{chat_id}-{int(time.time())}"
        
        deposit_data = create_deposit_request(amount, method_code, method_type, reff_id)

        if deposit_data and 'qr_string' in deposit_data:
            reff_id_to_chat_id_map[reff_id] = chat_id
            deposit_id = deposit_data.get('id')
            final_amount = deposit_data.get('nominal', amount)
            keyboard = [[InlineKeyboardButton("✅ Cek Status Pembayaran", callback_data=f"check_deposit_{deposit_id}")]]
            
            qr_image = qrcode.make(deposit_data['qr_string'])
            buffer = io.BytesIO()
            qr_image.save(buffer, 'PNG')
            buffer.seek(0)
            
            caption = (f"✅ Invoice Top Up berhasil dibuat.\n\n"
                       f"Silakan scan QRIS di atas untuk membayar *Rp {final_amount:,}*.\n\n"
                       f"ID Deposit Anda: `{deposit_id}`\n"
                       f"Gunakan ID ini untuk /cekstatus jika diperlukan.\n\n"
                       f"Setelah pembayaran berhasil, saldo akan masuk secara otomatis.")
            
            await msg.delete()
            await context.bot.send_photo(chat_id=chat_id, photo=buffer, caption=caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        
        else:
            await msg.edit_text("❌ Gagal membuat invoice QRIS INSTANT.")

    except (ValueError, TypeError):
        await update.message.reply_text("Input tidak valid. Harap masukkan angka saja.")
    
    except Exception as e:
        print(f"Error di topup_amount_handler: {traceback.format_exc()}")
        await update.message.reply_text(f"Terjadi error teknis: `{str(e)}`", parse_mode="Markdown")

    return True


async def check_deposit_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fungsi ini tidak diubah
    pass

async def prompt_deposit_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Meminta pengguna memasukkan ID Deposit setelah menekan tombol."""
    chat_id = update.effective_chat.id
    
    await context.bot.send_message(chat_id=chat_id, text="Silakan masukkan ID Deposit (Transaction ID) yang ingin Anda cek:")
    user_states[chat_id] = USER_STATE_ENTER_DEPOSIT_ID

async def handle_deposit_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Menangani input ID Deposit dari pengguna dan menampilkan statusnya."""
    chat_id = update.effective_chat.id
    if user_states.get(chat_id) != USER_STATE_ENTER_DEPOSIT_ID:
        return False

    deposit_id = update.message.text.strip()
    msg = await update.message.reply_text(f"🔎 Mengecek status untuk ID: `{deposit_id}`...", parse_mode="Markdown")
    user_states.pop(chat_id, None)

    status_data = check_deposit_status(deposit_id)

    if status_data:
        status_emoji = {
            "success": "✅ Berhasil", "pending": "⏳ Pending", "expired": "❌ Kedaluwarsa",
            "failed": "❗️ Gagal", "processing": "⚙️ Diproses"
        }
        status_text = status_data.get('status', 'N/A')
        emoji = status_emoji.get(status_text.lower(), "❓")

        pesan = (
            f"Berikut adalah status transaksi Anda:\n\n"
            f"<b>ID Deposit:</b> {status_data.get('id', 'N/A')}\n"
            f"<b>Reff ID Anda:</b> {status_data.get('reff_id', 'N/A')}\n"
            f"<b>Metode:</b> {status_data.get('metode', 'N/A')}\n"
            f"<b>Nominal:</b> Rp {int(status_data.get('nominal', 0)):,}\n"
            f"<b>Dibuat Pada:</b> {status_data.get('created_at', 'N/A')}\n"
            f"<b>Status:</b> {emoji}\n"
        )
        await msg.edit_text(pesan, parse_mode="HTML")
    else:
        await msg.edit_text("❌ ID Deposit tidak ditemukan atau terjadi kesalahan saat pengecekan.")
    
    return True


# --- FUNGSI BARU UNTUK LOGIKA MANUAL ---

async def manual_topup_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Callback job untuk membatalkan topup manual jika timeout."""
    job_data = context.job.data
    chat_id = job_data.get('chat_id')
    message_id = job_data.get('message_id')
    
    # Cek jika state masih menunggu bukti
    if user_states.get(chat_id) == USER_STATE_AWAIT_MANUAL_PROOF:
        try:
            # Hapus pesan QRIS
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            print(f"Gagal menghapus pesan QRIS (mungkin sudah dihapus): {e}")
        
        user_states.pop(chat_id, None)
        await context.bot.send_message(
            chat_id=chat_id, 
            text='⌛ Top-up Anda *expired* karena tidak mengirim bukti transfer dalam 5 menit.', 
            parse_mode='Markdown'
        )

async def handle_manual_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Menangani kiriman foto bukti transfer manual."""
    chat_id = update.effective_chat.id
    
    # Cek apakah pengguna dalam state yang benar
    if user_states.get(chat_id) != USER_STATE_AWAIT_MANUAL_PROOF:
        return False # Bukan state yang kita cari, biarkan handler lain memproses

    # Cek apakah yang dikirim adalah foto
    if not update.message.photo:
        await update.message.reply_text("Harap kirimkan *foto* bukti transfer, bukan file atau stiker.", parse_mode="Markdown")
        return True # Tetap dalam state

    # --- 2. GANTI INI DENGAN ID ADMIN ANDA ---
    ADMIN_CHAT_ID = 8372210994 
    # ---------------------------------
    
    if ADMIN_CHAT_ID == 8372210994:
         await update.message.reply_text("⛔️ *PENTING*: Bot belum dikonfigurasi! Admin perlu mengatur `ADMIN_CHAT_ID` di dalam kode `handle_manual_proof`.", parse_mode="Markdown")
         return True

    # Jika bukti diterima, batalkan job timeout
    jobs = context.job_queue.get_jobs_by_name(f"manual_timeout_{chat_id}")
    for job in jobs:
        job.schedule_removal()

    # Hapus state pengguna
    user_states.pop(chat_id, None)
    
    user = update.effective_user
    photo_file = update.message.photo[-1] # Ambil foto kualitas terbaik

    try:
        # Kirim notifikasi ke admin
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🔔 *Konfirmasi Top Up Manual*\n\n"
                 f"Dari: {user.full_name}\n"
                 f"Username: @{user.username}\n"
                 f"User ID: `{chat_id}`\n\n"
                 f"Silakan verifikasi bukti di atas dan teruskan saldo jika valid.",
            parse_mode="Markdown"
        )
        # Forward fotonya
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo_file.file_id
        )
        
        # Kirim konfirmasi ke user
        await update.message.reply_text(
            "✅ Bukti transfer Anda telah diterima dan diteruskan ke Admin.\n\n"
            "Mohon tunggu 1-5 menit untuk proses verifikasi manual."
        )

    except Exception as e:
        print(f"Gagal forward bukti ke admin: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat meneruskan bukti ke admin. Silakan hubungi admin secara manual.")

    return True # Kita telah menangani pesan ini
