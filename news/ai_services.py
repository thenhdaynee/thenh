import os
import json
import time
import re
import html as html_mod

import requests
from groq import Groq

# =========================================================
# CONFIG
# =========================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WEB_URL = "https://thenh-tin-tuc-the-thao.onrender.com/"

REQUEST_TIMEOUT = 25
MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_CHAT = "llama-3.3-70b-versatile"

# Rate limiting (chi hoat dong trong 1 worker)
LAST_CHAT_API_TIME = 0
LAST_REQUEST_TIME = 0
PROCESSED_UPDATES = set()

# Groq client
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

if GROQ_API_KEY:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
    print(f"=== VISION: {MODEL_VISION} ===")
    print(f"=== CHAT + WEB SEARCH: {MODEL_CHAT} ===")
else:
    print("=== KHONG TIM THAY GROQ_API_KEY ===")


# =========================================================
# TELEGRAM MESSENGER
# =========================================================

def send_telegram_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not configured")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print(f"TELEGRAM SEND ERROR: {e}")


# =========================================================
# WEB SEARCH
# =========================================================

def do_web_search(query):
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.post(url, data={"q": query}, headers=headers, timeout=15)
        res.raise_for_status()

        snippets = re.findall(
            r'<a rel="nofollow" class="result__snippet"[^>]*>(.*?)</a>',
            res.text, re.DOTALL
        )[:5]

        if not snippets:
            snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
                res.text, re.DOTALL
            )[:5]

        clean = []
        for s in snippets:
            text = re.sub(r'<[^>]+>', '', s)
            text = html_mod.unescape(text).strip()
            if text:
                clean.append(text)

        if clean:
            return "\n\n".join(clean)

        return "Khong tim thay ket qua tren web."

    except Exception as e:
        print("WEB SEARCH ERROR:", e)
        return "Loi trong qua trinh tim kiem web."


# =========================================================
# AI CHAT (Web Widget + Telegram Bot)
# =========================================================

def chat_with_ai(user_text):
    if not client:
        return "AI chua duoc cau hinh."

    search_result = do_web_search(user_text)
    reply = "Tro ly AI hien tai dang ban xu ly du lieu."

    for attempt in range(3):
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Ban la chuyen gia tro ly AI the thao thong minh hang dau. "
                        "Duoi day la du lieu tim kiem tu web, hay tra loi cau hoi cua nguoi dung "
                        "dua tren du lieu do. Luon phan hoi bang tieng Viet ngan gon, di thang vao van de, "
                        "chinh xac va co so lieu chung minh."
                    )
                },
                {
                    "role": "user",
                    "content": f"Du lieu tim kiem tu web:\n{search_result}\n\nCau hoi: {user_text}"
                }
            ]

            response = client.chat.completions.create(
                model=MODEL_CHAT,
                messages=messages,
                max_tokens=1000
            )
            reply = response.choices[0].message.content
            break

        except Exception as chat_error:
            print(f"CHAT ERROR ATTEMPT {attempt}:", chat_error)
            time.sleep(4)

    return reply


# =========================================================
# AI VISION - SINH BAI BAO TU ANH
# =========================================================

def generate_article(image_base64, keywords, cat_list):
    if not client:
        return None

    prompt = f"""Ban la mot bien tap vien, nha bao binh luan the thao chuyen nghiep.
Du vao hinh anh duoc cung cap cung tu khoa dinh huong: "{keywords}"
Hay viet mot bai bao the thao tieng Viet chuyen sau, loi cuon (do dai toi thieu 300 tu).

Ban BAT BUOC phai xuat du lieu tra ve theo dung dinh dang cau truc JSON mau sau:
{{
    "title": "Tieu de bai bao hap dan, chuan SEO",
    "content": "Noi dung bai bao phan tich chi tiet sau sac...",
    "category": {cat_list}
}}

Trong do category phai la mot trong cac slug sau: {cat_list}. Hay tu dong xac dinh the loai phu hop nhat dua vao noi dung hinh anh va tu khoa."""

    ai_text = None
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=MODEL_VISION,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                max_tokens=2500,
                response_format={"type": "json_object"}
            )
            ai_text = completion.choices[0].message.content
            print("VISION RESPONSE SUCCESS")
            break
        except Exception as ai_error:
            print(f"VISION ERROR attempt {attempt}:", ai_error)
            time.sleep(4)

    if not ai_text:
        return None

    try:
        raw_text = ai_text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        ai_data = json.loads(raw_text.strip())
        title = ai_data.get("title") or "Tin tuc the thao noi bat"
        content = ai_data.get("content") or ""
        category_slug = ai_data.get("category", "")
        return title, content, category_slug
    except Exception as e:
        print("PARSE VISION ERROR:", e)
        return None
