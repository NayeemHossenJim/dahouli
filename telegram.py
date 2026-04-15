import requests
from config import BOT_TOKEN, CHAT_ID

def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram config missing: BOT_TOKEN or CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text
    }
    try:
        response = requests.post(url, data=data, timeout=20)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"Telegram send failed: {exc}")
        return False