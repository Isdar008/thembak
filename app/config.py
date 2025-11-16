# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Data QRIS (baru) ===
# Expected to be provided in .env as DATA_QRIS (could be an URL or a provider key)
DATA_QRIS = os.getenv("DATA_QRIS", "")

# Admins & runtime maps
ADMIN_IDS = [1452437996]

user_states = {}
reff_id_to_chat_id_map = {}

# Definisi State Pengguna
USER_STATE_MENU_MAIN = 0
USER_STATE_ENTER_PHONE = 1
USER_STATE_ENTER_OTP = 2
USER_STATE_SELECTING_PACKAGE = 4
USER_STATE_CONFIRM_PURCHASE = 5
USER_STATE_SELECTING_PAYMENT_METHOD = 6
USER_STATE_SELECTING_EWALLET = 7
USER_STATE_ENTER_EWALLET_NUMBER = 8
USER_STATE_ENTER_TOPUP_AMOUNT = 9
USER_STATE_ADMIN_TOPUP_NUMBER = 10
USER_STATE_ADMIN_TOPUP_AMOUNT = 11
USER_STATE_ADMIN_SWITCH_NUMBER = 12
# --- STATE BARU DITAMBAHKAN DI SINI ---
USER_STATE_ENTER_DEPOSIT_ID = 13
USER_STATE_AWAIT_MANUAL_PROOF = 14
