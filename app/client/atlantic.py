# app/client/atlantic.py
# Replaced Atlantic client with direct DataQRIS "orkut" API adapter
# Mirrors behavior of api-cekpayment-orkut.js (qs + headers + API_URL)

import os
import logging
import requests
from urllib.parse import urlencode
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Default constants — can be overridden with env vars
API_URL = os.getenv("DATA_QRIS_API_URL", "https://orkutapi.andyyuda41.workers.dev/api/qris-history")

# Credentials (mirrors the JS hardcoded example, but we read from env/config for safety)
# If you want to hardcode like JS, set these values in your .env or config.
DATA_QRIS_USERNAME = os.getenv("DATA_QRIS_USERNAME", "kangnaum")
DATA_QRIS_TOKEN = os.getenv("DATA_QRIS_TOKEN", "2449343:LANp7rEhloiH0d3ImSvnX8JjMgDa5eFU")

# Headers same as JS example
headers: Dict[str, str] = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Accept-Encoding': 'gzip',
    'User-Agent': 'okhttp/4.12.0'
}

def build_payload(deposit_id: Optional[str] = None) -> str:
    """
    Build form-encoded payload string like qs.stringify in the JS example.
    Includes username, token, jenis='masuk', and optional id if provided.
    Returns a urlencoded string.
    """
    payload = {
        'username': DATA_QRIS_USERNAME,
        'token': DATA_QRIS_TOKEN,
        'jenis': 'masuk'
    }
    if deposit_id:
        payload['id'] = str(deposit_id)
    # urlencode will produce x-www-form-urlencoded string
    return urlencode(payload)

# Keep old-named functions as stubs so other parts of app won't crash
def get_deposit_methods() -> Optional[list]:
    """Not supported in DataQRIS adapter."""
    logger.debug("get_deposit_methods: not supported for DataQRIS adapter.")
    return None

def create_deposit_request(amount: int, metode_kode: str, metode_type: str, reff_id: str) -> Optional[dict]:
    """Not supported in DataQRIS adapter."""
    logger.debug("create_deposit_request: not supported for DataQRIS adapter.")
    return None

def request_instant_deposit(deposit_id: str) -> Optional[dict]:
    """Not supported in DataQRIS adapter."""
    logger.debug("request_instant_deposit: not supported for DataQRIS adapter.")
    return None

def check_deposit_status(deposit_id: Optional[str] = None, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """
    POST to API_URL with form-encoded payload exactly like the JS example.
    Returns parsed JSON (dict/list) on success, or None on failure.
    """
    if not API_URL:
        logger.warning("DATA QRIS API_URL not configured.")
        return None

    payload_str = build_payload(deposit_id)
    try:
        logger.info("Posting to %s payload keys: %s", API_URL, ", ".join(["username","token","jenis"] + (["id"] if deposit_id else [])))
        resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=timeout)
        resp.raise_for_status()
        # parse JSON
        try:
            j = resp.json()
            logger.debug("Response JSON: %s", j)
            return j
        except ValueError:
            logger.exception("Response is not valid JSON")
            return None
    except requests.RequestException as e:
        logger.exception("HTTP error while checking deposit status: %s", e)
        return None
    except Exception as e:
        logger.exception("Unexpected error in check_deposit_status: %s", e)
        return None
