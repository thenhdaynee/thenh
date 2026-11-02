import base64
import datetime
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from groq import Groq

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None


# =========================================================
# CONFIG
# =========================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WEB_URL = os.environ.get(
    "WEB_URL", "https://thenh-tin-tuc-the-thao.onrender.com/"
)

REQUEST_TIMEOUT = 20
HTTP_TIMEOUT = (3, 8)
TELEGRAM_TIMEOUT = 10

MODEL_VISION = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
MODEL_CHAT = os.environ.get("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
MODEL_CHAT_FAST = os.environ.get("GROQ_CHAT_FAST_MODEL", "openai/gpt-oss-20b")
MODEL_CHAT_FALLBACK = os.environ.get("GROQ_CHAT_FALLBACK_MODEL", "qwen/qwen3.8-27b")

GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.5-flash")
GEMINI_SEARCH_MODEL = os.environ.get("GEMINI_SEARCH_MODEL", GEMINI_VISION_MODEL)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_CHAT_MODEL = os.environ.get(
    "OPENROUTER_CHAT_MODEL", "google/gemma-4-31b-it:free"
)
OPENROUTER_VISION_MODEL = os.environ.get(
    "OPENROUTER_VISION_MODEL", "nvidia/nemotron-3.5-super-vl:free"
)

AI_FALLBACK = "__AI_FALLBACK__"
NO_SEARCH = "__NO_SEARCH__"


def _make_groq_client():
    if not GROQ_API_KEY:
        return None
    try:
        return Groq(
            api_key=GROQ_API_KEY,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,
        )
    except TypeError:
        return Groq(api_key=GROQ_API_KEY)
    except Exception as exc:
        print(f"GROQ CLIENT ERROR: {exc}")
        return None


client = _make_groq_client()

gemini_client = None
if genai and GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        print(f"GEMINI CLIENT ERROR: {exc}")

AI_AVAILABLE = bool(client or gemini_client or OPENROUTER_API_KEY)

if client:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
    print(f"=== VISION: {MODEL_VISION} ===")
    print(f"=== CHAT: {MODEL_CHAT} / {MODEL_CHAT_FAST} ===")
else:
    print("=== KHONG TIM THAY GROQ_API_KEY ===")

if gemini_client:
    print(f"=== GEMINI: {GEMINI_VISION_MODEL} ===")
if OPENROUTER_API_KEY:
    print(f"=== OPENROUTER: {OPENROUTER_CHAT_MODEL} ===")


# =========================================================
# TELEGRAM MESSENGER
# =========================================================

def _split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def send_telegram_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not configured")
        return False
    text = str(text or "").strip()
    if not text:
        return False

    ok = True
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in _split_message(text):
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=TELEGRAM_TIMEOUT,
            )
            if response.status_code != 200:
                print(f"TELEGRAM SEND HTTP {response.status_code}: {response.text[:200]}")
                ok = False
        except Exception as exc:
            print(f"TELEGRAM SEND ERROR: {exc}")
            ok = False
    return ok


def send_telegram_typing(chat_id):
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
        return response.status_code == 200
    except Exception as exc:
        print(f"TELEGRAM TYPING ERROR: {exc}")
        return False


# =========================================================
# AI HELPERS
# =========================================================

def _groq_text(model, messages, max_tokens=800, temperature=0.4):
    if not client:
        raise RuntimeError("Groq chưa được cấu hình")
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def _openrouter_text(model, messages, max_tokens=800, temperature=0.4):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter chưa được cấu hình")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": WEB_URL,
        "X-Title": "Thenh Sport News",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = requests.post(
        OPENROUTER_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:200]}"
        )
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"OpenRouter error: {data['error']}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter không trả về choices")
    return (choices[0].get("message", {}).get("content") or "").strip()


def _gemini_text(model, contents, max_tokens=800, temperature=0.4, search=False):
    if not gemini_client or not genai_types:
        raise RuntimeError("Gemini chưa được cấu hình")
    config_kwargs = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if search:
        config_kwargs["tools"] = [
            genai_types.Tool(google_search=genai_types.GoogleSearch())
        ]
    config = genai_types.GenerateContentConfig(**config_kwargs)
    response = gemini_client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    return (response.text or "").strip()


def _messages_to_prompt(messages):
    lines = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _chat_candidates(fast=False):
    if fast:
        raw = [
            ("groq", MODEL_CHAT_FAST),
            ("groq", MODEL_CHAT),
            ("openrouter", OPENROUTER_CHAT_MODEL),
            ("gemini", GEMINI_VISION_MODEL),
        ]
    else:
        raw = [
            ("groq", MODEL_CHAT),
            ("groq", MODEL_CHAT_FALLBACK),
            ("openrouter", OPENROUTER_CHAT_MODEL),
            ("openrouter", "openrouter/free"),
            ("gemini", GEMINI_VISION_MODEL),
        ]
    seen = set()
    result = []
    for provider, model in raw:
        if not model or (provider, model) in seen:
            continue
        seen.add((provider, model))
        result.append((provider, model))
    return result


def _call_text_model(provider, model, messages, max_tokens, temperature):
    if provider == "groq":
        return _groq_text(model, messages, max_tokens, temperature)
    if provider == "openrouter":
        return _openrouter_text(model, messages, max_tokens, temperature)
    if provider == "gemini":
        return _gemini_text(
            model,
            _messages_to_prompt(messages),
            max_tokens=max_tokens,
            temperature=temperature,
        )
    raise RuntimeError(f"Nhà cung cấp không hỗ trợ: {provider}")


def _chat_chain(messages, max_tokens=800, temperature=0.4, fast=False):
    errors = []
    for provider, model in _chat_candidates(fast=fast):
        try:
            text = _call_text_model(
                provider, model, messages, max_tokens, temperature
            )
            if text:
                print(f"AI OK: {provider}/{model}")
                return text
        except Exception as exc:
            errors.append(f"{provider}/{model}: {exc}")
            print(f"AI FAIL {provider}/{model}: {exc}")
    if errors:
        print(f"AI CHAIN FAILED: {errors[-1]}")
    return None


def _parse_json(text):
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass

    starts = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(raw.rfind("}"), raw.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


def _json_chain(messages, max_tokens=1500, temperature=0.2):
    for provider, model in _chat_candidates():
        try:
            text = _call_text_model(
                provider, model, messages, max_tokens, temperature
            )
            data = _parse_json(text)
            if data is not None:
                print(f"AI JSON OK: {provider}/{model}")
                return data
        except Exception as exc:
            print(f"AI JSON FAIL {provider}/{model}: {exc}")
    return None


# =========================================================
# WEB SEARCH
# =========================================================

def _parse_ddg_html(html):
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)>',
        html,
        re.DOTALL,
    )
    if not snippets:
        snippets = re.findall(
            r'<a[^>]*class="result__a"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )
    results = []
    for snippet in snippets:
        text = re.sub(r"<[^>]+>", "", snippet).strip()
        if text:
            results.append(text)
    return results[:5]


def _search_wikipedia(query):
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 5,
                "format": "json",
            },
            headers={"User-Agent": "ThenhSportNewsBot/1.0"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            if len(data) > 2 and data[2]:
                return "\n\n".join(data[2])
    except Exception as exc:
        print(f"WIKIPEDIA SEARCH ERROR: {exc}")
    return None


DDG_LOCK = threading.Lock()
DDG_FAILED_UNTIL = 0
DDG_COOLDOWN = 600


def _search_duckduckgo(query):
    global DDG_FAILED_UNTIL

    with DDG_LOCK:
        if time.time() < DDG_FAILED_UNTIL:
            return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=(3, 5),
        )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        results = _parse_ddg_html(response.text)
        if results:
            return "\n\n".join(results)
        return None
    except Exception as exc:
        with DDG_LOCK:
            DDG_FAILED_UNTIL = time.time() + DDG_COOLDOWN
        print(f"DDG SEARCH ERROR: {exc}")
        return None


def _search_gemini(query):
    if not gemini_client:
        return None
    try:
        text = _gemini_text(
            GEMINI_SEARCH_MODEL,
            query,
            max_tokens=800,
            temperature=0.2,
            search=True,
        )
        if text:
            print(f"GEMINI SEARCH OK: {GEMINI_SEARCH_MODEL}")
            return text
    except Exception as exc:
        print(f"GEMINI SEARCH ERROR: {exc}")
    return None


RSS_FEEDS = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("BBC Football", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("ESPN", "https://www.espn.com/espn/rss/news"),
    ("Sky Sports", "https://www.skysports.com/rss/12040"),
]

RSS_CACHE = {"data": None, "time": 0}
RSS_CACHE_TTL = 300
RSS_LOCK = threading.Lock()


def _fetch_one_rss(name, url):
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ThenhSportNewsBot/1.0"},
            timeout=(3, 5),
        )
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "") or ""
            description = re.sub(
                r"<[^>]+>", "", item.findtext("description", "") or ""
            )
            if title:
                items.append(f"{title}\n{description}" if description else title)
        return items
    except Exception as exc:
        print(f"RSS ERROR {name}: {exc}")
        return []


def _fetch_rss():
    now = time.time()
    with RSS_LOCK:
        if RSS_CACHE["data"] and now - RSS_CACHE["time"] < RSS_CACHE_TTL:
            return RSS_CACHE["data"]

    items = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_fetch_one_rss, name, url)
            for name, url in RSS_FEEDS
        ]
        for future in as_completed(futures):
            try:
                items.extend(future.result())
            except Exception as exc:
                print(f"RSS FUTURE ERROR: {exc}")

    if not items:
        return None

    result = "\n\n".join(items[:12])
    with RSS_LOCK:
        RSS_CACHE["data"] = result
        RSS_CACHE["time"] = time.time()
    return result


NEWS_KEYWORDS = [
    "tin tức", "tin thể thao", "tin bóng đá", "mới nhất",
    "nổi bật", "tin nóng", "chuyển nhượng",
    "news", "sport news", "football news", "latest",
    "breaking", "update", "score", "tỷ số", "ty so",
]

SPORT_KEYWORDS = [
    "bóng đá", "bong da", "thể thao", "the thao", "cầu thủ", "cau thu",
    "đội", "doi", "trận", "tran", "giải", "giai", "vòng", "vong",
    "premier league", "champions league", "world cup", "euro",
    "ronaldo", "messi", "real madrid", "barcelona", "man utd",
    "manchester", "liverpool", "arsenal", "chelsea", "psg", "bayern",
]

GREETING_PHRASES = {
    "hi", "hello", "hey", "alo", "chào", "chao", "chào bạn", "chao ban",
    "xin chào", "xin chao", "cảm ơn", "cam on", "thank you", "thanks",
    "ok", "oke", "okay", "test", "bot ơi", "bot oi", "bạn là ai",
    "ban la ai", "who are you",
}


def _normalize(text):
    text = (text or "").lower()
    text = re.sub(r"[^\w\sàáảãạăâđêôơưýỳỷỹỵ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_greeting(text):
    normalized = _normalize(text)
    if not normalized:
        return True
    if normalized in GREETING_PHRASES:
        return True
    words = normalized.split()
    if len(words) <= 3 and words[0] in {
        "hi", "hello", "hey", "alo", "chào", "chao", "xin",
    }:
        return True
    return False


def _is_news_query(query):
    normalized = _normalize(query)
    return any(keyword in normalized for keyword in NEWS_KEYWORDS)


def _should_search(query):
    normalized = _normalize(query)
    if not normalized or _is_greeting(normalized):
        return False
    if _is_news_query(normalized):
        return True
    if any(keyword in normalized for keyword in SPORT_KEYWORDS):
        return True
    if "?" in (query or ""):
        return True
    return len(normalized.split()) >= 5


def do_web_search(query):
    if _is_greeting(query):
        return NO_SEARCH

    if _is_news_query(query):
        rss_result = _fetch_rss()
        if rss_result:
            return rss_result
        gemini_result = _search_gemini(query)
        if gemini_result:
            return gemini_result
        ddg_result = _search_duckduckgo(query)
        if ddg_result:
            return ddg_result
        wiki_result = _search_wikipedia(query)
        if wiki_result:
            return wiki_result
    else:
        ddg_result = _search_duckduckgo(query)
        if ddg_result:
            return ddg_result
        wiki_result = _search_wikipedia(query)
        if wiki_result:
            return wiki_result
        gemini_result = _search_gemini(query)
        if gemini_result:
            return gemini_result

    if AI_AVAILABLE:
        return AI_FALLBACK
    return "Lỗi trong quá trình tìm kiếm web."


# =========================================================
# AI CHAT
# =========================================================

def chat_with_ai(user_text):
    if not AI_AVAILABLE:
        return "AI chưa được cấu hình."

    text = (user_text or "").strip()
    if not text:
        return "Bạn muốn hỏi gì về thể thao?"

    if _is_greeting(text):
        messages = [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý thể thao thân thiện. "
                    "Chào hỏi ngắn gọn bằng tiếng Việt, tối đa 2 câu. "
                    "Không cần tìm kiếm web."
                ),
            },
            {"role": "user", "content": text},
        ]
        reply = _chat_chain(messages, max_tokens=200, fast=True)
        return reply or "Chào bạn! Mình có thể giúp gì về thể thao?"

    search_result = do_web_search(text) if _should_search(text) else AI_FALLBACK

    if (
        search_result
        and search_result not in (AI_FALLBACK, NO_SEARCH)
        and not search_result.startswith("Lỗi")
    ):
        system_prompt = (
            "Bạn là chuyên gia trợ lý AI thể thao. "
            "Dữ liệu web bên dưới là nguồn chính để trả lời. "
            "Nếu dữ liệu không chứa câu trả lời, hãy nói rõ là không tìm thấy. "
            "Trả lời bằng tiếng Việt ngắn gọn, chính xác, tối đa 250 từ."
        )
        user_content = (
            f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\n"
            f"Câu hỏi: {text}"
        )
    else:
        system_prompt = (
            "Bạn là chuyên gia trợ lý AI thể thao. "
            "Trả lời bằng kiến thức hiện có, tiếng Việt ngắn gọn, chính xác, "
            "tối đa 250 từ. Nếu không chắc chắn, hãy nói rõ."
        )
        user_content = f"Câu hỏi: {text}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    reply = _chat_chain(messages, max_tokens=800)
    return reply or "Trợ lý AI hiện đang bận xử lý dữ liệu. Vui lòng thử lại sau."


# =========================================================
# LIVE SCORE
# =========================================================

def _fetch_sky_sports():
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
        response = requests.get(
            "https://www.skysports.com/live-scores",
            headers=headers,
            timeout=(3, 10),
        )
        if response.status_code == 200:
            print(f"SKY SPORTS FETCH OK: {len(response.text)} bytes")
            return response.text
    except Exception as exc:
        print(f"SKY SPORTS FETCH ERROR: {exc}")
    return None


def fetch_and_parse_scores():
    if not AI_AVAILABLE:
        return None

    raw_html = _fetch_sky_sports()
    search_result = None

    if not raw_html:
        today = datetime.date.today().strftime("%Y-%m-%d")
        score_query = f"live football scores today {today}"
        search_result = _search_gemini(score_query)
        if not search_result:
            search_result = _search_duckduckgo(score_query)
        if not search_result:
            search_result = _search_wikipedia(score_query)

    if raw_html:
        text = re.sub(r"<[^>]+>", " ", raw_html)
        text = re.sub(r"\s+", " ", text)[:6000]
        user_content = (
            f"Dữ liệu từ trang live-scores:\n{text}\n\n"
            "Hãy trích xuất danh sách trận đấu bóng đá."
        )
    elif search_result:
        user_content = (
            f"Dữ liệu tìm kiếm từ web:\n{search_result}\n\n"
            "Hãy trích xuất danh sách trận đấu bóng đá."
        )
    else:
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "Bạn là chuyên gia bóng đá. Chỉ trả về JSON hợp lệ, "
                "không thêm văn bản khác."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_content}\n\n"
                "Trả về JSON object dạng:\n"
                '{"scores": [{"team_a": "TenDoiA", "team_b": "TenDoiB", '
                '"score_a": 0, "score_b": 0, "status": "live|ht|ft"}]}\n\n'
                "Nếu không có trận nào, trả về {\"scores\": []}."
            ),
        },
    ]

    data = _json_chain(messages, max_tokens=1500, temperature=0.1)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        scores = data.get("scores")
        if isinstance(scores, list):
            return scores
        for value in data.values():
            if isinstance(value, list):
                return value
    return None


# =========================================================
# AI VISION
# =========================================================

def _vision_prompt(keywords, cat_list):
    return f"""Bạn là biên tập viên thể thao chuyên nghiệp.
Dựa vào hình ảnh và từ khóa định hướng: "{keywords}"
Hãy viết một bài báo thể thao tiếng Việt chuyên sâu, hấp dẫn, tối thiểu 300 từ.

Chỉ trả về DUY NHẤT một JSON object hợp lệ theo dạng:
{{
    "title": "Tiêu đề bài báo hấp dẫn, chuẩn SEO",
    "content": "Nội dung bài báo phân tích chi tiết...",
    "category": "slug"
}}

Trường category phải là một trong các slug sau: {cat_list}.
Không thêm markdown, không thêm giải thích ngoài JSON."""


def _groq_vision(image_base64, prompt):
    if not client:
        raise RuntimeError("Groq chưa được cấu hình")
    response = client.chat.completions.create(
        model=MODEL_VISION,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        max_tokens=3000,
        temperature=0.7,
    )
    return (response.choices[0].message.content or "").strip()


def _gemini_vision(image_base64, prompt):
    if not gemini_client or not genai_types:
        raise RuntimeError("Gemini chưa được cấu hình")
    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as exc:
        raise RuntimeError(f"Ảnh base64 không hợp lệ: {exc}")
    part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    return _gemini_text(
        GEMINI_VISION_MODEL,
        [part, prompt],
        max_tokens=3000,
        temperature=0.7,
    )


def _openrouter_vision(image_base64, prompt):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter chưa được cấu hình")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    return _openrouter_text(
        OPENROUTER_VISION_MODEL,
        messages,
        max_tokens=3000,
        temperature=0.7,
    )


def generate_article(image_base64, keywords, cat_list):
    if not AI_AVAILABLE:
        return None

    prompt = _vision_prompt(keywords, cat_list)
    candidates = [
        ("groq", MODEL_VISION, _groq_vision),
        ("gemini", GEMINI_VISION_MODEL, _gemini_vision),
        ("openrouter", OPENROUTER_VISION_MODEL, _openrouter_vision),
    ]

    for provider, model, caller in candidates:
        try:
            ai_text = caller(image_base64, prompt)
            data = _parse_json(ai_text)
            if not isinstance(data, dict):
                print(f"VISION JSON FAIL {provider}/{model}")
                continue
            title = str(data.get("title") or "").strip()
            content = str(data.get("content") or "").strip()
            category_slug = str(data.get("category") or "").strip().strip('"')
            if not title or len(content) < 50:
                print(f"VISION DATA FAIL {provider}/{model}")
                continue
            print(f"VISION OK: {provider}/{model}")
            return title, content, category_slug
        except Exception as exc:
            print(f"VISION FAIL {provider}/{model}: {exc}")

    return None
