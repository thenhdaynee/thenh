import os
import json
import re
import time
import xml.etree.ElementTree as ET

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
LAST_SEARCH_TIME = 0
PROCESSED_UPDATES = set()
SEARCH_COOLDOWN = 2.0  # giay toi thieu giua 2 lan search

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

def _parse_ddg_html(html):
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)>',
        html, re.DOTALL
    )
    if not snippets:
        snippets = re.findall(
            r'<a[^>]*class="result__a"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
    results = []
    for s in snippets:
        text = re.sub(r"<[^>]+>", "", s)
        text = text.strip()
        if text:
            results.append(text)
    return results[:5]


def _search_wikipedia(query):
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": 5,
            "format": "json",
        }
        headers = {"User-Agent": "ThenhSportNewsBot/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) > 2 and data[2]:
                return "\n\n".join(data[2])
    except Exception as e:
        print(f"WIKIPEDIA SEARCH ERROR: {e}")
    return None


RSS_FEEDS = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("BBC Football", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("ESPN", "https://www.espn.com/espn/rss/news"),
    ("Sky Sports", "https://www.skysports.com/rss/12040"),
]

RSS_CACHE = {"data": None, "time": 0}
RSS_CACHE_TTL = 300


def _fetch_rss():
    global RSS_CACHE
    now = time.time()
    if RSS_CACHE["data"] and now - RSS_CACHE["time"] < RSS_CACHE_TTL:
        return RSS_CACHE["data"]

    items = []
    for name, url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers={"User-Agent": "ThenhSportNewsBot/1.0"}, timeout=10)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title = item.findtext("title", "")
                desc = item.findtext("description", "")
                desc = re.sub(r"<[^>]+>", "", desc)
                if title:
                    items.append(f"{title}\n{desc}" if desc else title)
        except Exception as e:
            print(f"RSS ERROR {name}: {e}")

    if items:
        result = "\n\n".join(items[:10])
        RSS_CACHE = {"data": result, "time": time.time()}
        return result
    return None


NEWS_KEYWORDS = [
    "tin tức", "tin thể thao", "tin bóng đá", "mới nhất",
    "hôm nay", "gần đây", "nổi bật", "tin nóng",
    "news", "sport news", "football news", "latest",
    "today", "breaking", "update",
]


def _is_news_query(query):
    q = query.lower()
    for kw in NEWS_KEYWORDS:
        if kw in q:
            return True
    return False


def do_web_search(query):
    global LAST_SEARCH_TIME

    now = time.time()
    if now - LAST_SEARCH_TIME < SEARCH_COOLDOWN:
        time.sleep(SEARCH_COOLDOWN - (now - LAST_SEARCH_TIME))
    LAST_SEARCH_TIME = time.time()

    # ===== CACH 0: RSS cho cau hoi kieu "tin tuc" =====
    if _is_news_query(query):
        rss_result = _fetch_rss()
        if rss_result:
            return rss_result

    # ===== CACH 1: DuckDuckGo HTML endpoint =====
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                results = _parse_ddg_html(resp.text)
                if results:
                    return "\n\n".join(results)
            else:
                print(f"DDG HTML attempt {attempt + 1}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"DDG HTML ERROR attempt {attempt + 1}: {e}")

        delay = 2 ** (attempt + 1)
        time.sleep(delay)

    # ===== CACH 2: Wikipedia fallback =====
    wiki_result = _search_wikipedia(query)
    if wiki_result:
        return wiki_result

    # ===== CACH 3: Groq tu tra loi =====
    if client:
        return "__GROQ_FALLBACK__"

    return "Lỗi trong quá trình tìm kiếm web."


# =========================================================
# AI CHAT (Web Widget + Telegram Bot)
# =========================================================

def chat_with_ai(user_text):
    if not client:
        return "AI chưa được cấu hình."

    search_result = do_web_search(user_text)
    is_fallback = (search_result == "__GROQ_FALLBACK__")

    reply = "Trợ lý AI hiện tại đang bận xử lý dữ liệu."

    for attempt in range(3):
        try:
            if is_fallback:
                system_prompt = (
                    "Bạn là chuyên gia trợ lý AI thể thao thông minh hàng đầu. "
                    "Trả lời câu hỏi của người dùng dựa trên kiến thức hiện có của bạn. "
                    "Luôn phản hồi bằng tiếng Việt ngắn gọn, đi thẳng vào vấn đề, chính xác. "
                    "Nếu bạn không biết câu trả lời, hãy nói: 'Không tìm thấy thông tin.'"
                )
                user_content = f"Câu hỏi: {user_text}"
            else:
                system_prompt = (
                    "Bạn là chuyên gia trợ lý AI thể thao thông minh hàng đầu. "
                    "Dữ liệu web bên dưới là NGUỒN DUY NHẤT bạn được phép dùng để trả lời. "
                    "TUYỆT ĐỐI KHÔNG được dùng kiến thức có sẵn của bạn. "
                    "Nếu dữ liệu web KHÔNG chứa câu trả lời, hãy nói: 'Không tìm thấy thông tin trên web.' "
                    "Luôn phản hồi bằng tiếng Việt ngắn gọn, đi thẳng vào vấn đề, chính xác và có cơ sở từ dữ liệu web."
                )
                user_content = f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\nCâu hỏi: {user_text}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
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

    raw_html = None
    search_result = None

    # ===== CACH 1: Fetch truc tiep tu Sky Sports =====
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        resp = requests.get("https://www.skysports.com/live-scores", headers=headers, timeout=15)
        if resp.status_code == 200:
            raw_html = resp.text
            print(f"SKY SPORTS FETCH OK: {len(raw_html)} bytes")
    except Exception as e:
        print(f"SKY SPORTS FETCH ERROR: {e}")

    # ===== CACH 2: Fallback DuckDuckGo query =====
    if not raw_html:
        queries = [
            "live scores today football june 2026 site:skysports.com",
            "football scores today live premier league june 2026",
            "live football scores today",
        ]
        for q in queries:
            result = do_web_search(q)
            if result and "Lỗi" not in result and "Không tìm thấy" not in result:
                search_result = result
                print(f"FALLBACK SEARCH OK: {q}")
                break

    # ===== Parse bang AI =====
    for attempt in range(3):
        try:
            if raw_html:
                text = re.sub(r"<[^>]+>", " ", raw_html)
                text = re.sub(r"\s+", " ", text)[:8000]
                user_content = f"Dữ liệu raw từ trang live-scores:\n{text[:5000]}\n\nHãy trích xuất danh sách trận đấu bóng đá."
            elif search_result:
                user_content = f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\nHãy trích xuất danh sách trận đấu bóng đá."
            else:
                return None

            messages = [
                {
                    "role": "system",
                    "content": "Bạn là chuyên gia bóng đá. Nhiệm vụ của bạn là trích xuất danh sách trận đấu từ dữ liệu."
                },
                {
                    "role": "user",
                    "content": (
                        f"{user_content}\n\n"
                        "Trả về JSON array, mỗi phần tử có dạng:\n"
                        '{"team_a": "TenDoiA", "team_b": "TenDoiB", "score_a": 0, "score_b": 0, "status": "live|ht|ft"}\n\n'
                        "Trong đó:\n"
                        "- team_a, team_b: tên đội bóng\n"
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
