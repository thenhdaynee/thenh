import os
import json
import time

import requests
from groq import Groq
from duckduckgo_search import DDGS

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
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "Không tìm thấy kết quả trên web."
        snippets = [r.get("body", "") for r in results if r.get("body")]
        if snippets:
            return "\n\n".join(snippets)
        return "Không tìm thấy kết quả trên web."
    except Exception as e:
        print("WEB SEARCH ERROR:", e)
        return "Lỗi trong quá trình tìm kiếm web."


# =========================================================
# AI CHAT (Web Widget + Telegram Bot)
# =========================================================

def chat_with_ai(user_text):
    if not client:
        return "AI chưa được cấu hình."

    search_result = do_web_search(user_text)

    if "Không tìm thấy kết quả" in search_result or "Lỗi" in search_result:
        return "Xin lỗi, hiện tại không thể tìm kiếm thông tin trên web cho câu hỏi của bạn."

    reply = "Trợ lý AI hiện tại đang bận xử lý dữ liệu."

    for attempt in range(3):
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Bạn là chuyên gia trợ lý AI thể thao thông minh hàng đầu. "
                        "Dữ liệu web bên dưới là NGUỒN DUY NHẤT bạn được phép dùng để trả lời. "
                        "TUYỆT ĐỐI KHÔNG được dùng kiến thức có sẵn của bạn. "
                        "KHÔNG bao giờ đề cập đến hạn mức kiến thức, ngày cập nhật dữ liệu, hay 'dữ liệu chỉ cập nhật đến năm X'. "
                        "Nếu dữ liệu web KHÔNG chứa câu trả lời, hãy nói: 'Không tìm thấy thông tin trên web.' "
                        "Luôn phản hồi bằng tiếng Việt ngắn gọn, đi thẳng vào vấn đề, chính xác và có cơ sở từ dữ liệu web."
                    )
                },
                {
                    "role": "user",
                    "content": f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\nCâu hỏi: {user_text}"
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
# LIVE SCORE - FETCH + PARSE
# =========================================================

def fetch_and_parse_scores():
    if not client:
        return None

    search_result = do_web_search("tỷ số bóng đá hôm nay tháng 6 năm 2026")

    for attempt in range(3):
        try:
            messages = [
                {
                    "role": "system",
                    "content": "Bạn là chuyên gia bóng đá. Nhiệm vụ của bạn là trích xuất danh sách trận đấu từ dữ liệu web."
                },
                {
                    "role": "user",
                    "content": (
                        f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\n"
                        "Hãy trích xuất tất cả các trận đấu bóng đá có trong dữ liệu trên. "
                        "Trả về JSON array, mỗi phần tử có dạng:\n"
                        '{"team_a": "TenDoiA", "team_b": "TenDoiB", "score_a": 0, "score_b": 0, "status": "live|ht|ft"}\n\n'
                        "Trong đó:\n"
                        "- team_a, team_b: viết tắt hoặc tên đầy đủ\n"
                        "- score_a, score_b: số bàn thắng (0 nếu chưa có)\n"
                        "- status: 'live' nếu đang đá, 'ht' nếu hết hiệp 1, 'ft' nếu kết thúc\n\n"
                        "Chỉ trả về JSON array, không thêm text nào khác. "
                        "Nếu không có trận nào, trả về []."
                    )
                }
            ]

            response = client.chat.completions.create(
                model=MODEL_CHAT,
                messages=messages,
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
            raw = response.choices[0].message.content.strip()
            print("SCORE PARSE RAW:", raw)

            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "scores" in data:
                return data["scores"]
            if isinstance(data, dict):
                vals = [v for v in data.values() if isinstance(v, list)]
                if vals:
                    return vals[0]
            return None

        except Exception as e:
            print(f"SCORE PARSE ERROR attempt {attempt}:", e)
            time.sleep(4)

    return None


# =========================================================
# AI VISION - SINH BAI BAO TU ANH
# =========================================================

def generate_article(image_base64, keywords, cat_list):
    if not client:
        return None

    prompt = f"""Bạn là một biên tập viên, nhà báo bình luận thể thao chuyên nghiệp.
Dựa vào hình ảnh được cung cấp cùng từ khóa định hướng: "{keywords}"
Hãy viết một bài báo thể thao tiếng Việt chuyên sâu, lôi cuốn (độ dài tối thiểu 300 từ).

Bạn BẮT BUỘC phải xuất dữ liệu trả về theo đúng định dạng cấu trúc JSON mẫu sau:
{{
    "title": "Tiêu đề bài báo hấp dẫn, chuẩn SEO",
    "content": "Nội dung bài báo phân tích chi tiết sâu sắc...",
    "category": {cat_list}
}}

Trong đó category phải là một trong các slug sau: {cat_list}. Hãy tự động xác định thể loại phù hợp nhất dựa vào nội dung hình ảnh và từ khóa."""

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
        title = ai_data.get("title") or "Tin tức thể thao nổi bật"
        content = ai_data.get("content") or ""
        category_slug = ai_data.get("category", "")
        return title, content, category_slug
    except Exception as e:
        print("PARSE VISION ERROR:", e)
        return None
