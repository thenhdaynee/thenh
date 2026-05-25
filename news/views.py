import os
import json
import time
import base64
import requests

from groq import Groq

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile

from .models import Post

# =========================================================
# WEB
# =========================================================

def home(request):
    latest_posts = Post.objects.all().order_by('-created_at')[:5]
    trending_posts = Post.objects.all().order_by('-views_count', '-created_at')[:4]
    featured_posts = Post.objects.filter(is_featured=True).order_by('-created_at')[:6]
    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': featured_posts,
    }
    return render(request, 'index.html', context)


def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)
    post.views_count += 1
    post.save(update_fields=['views_count'])
    return render(request, 'post_detail.html', {'post': post})


# =========================================================
# CONFIG
# =========================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
REQUEST_TIMEOUT = 15
LAST_REQUEST_TIME = 0
PROCESSED_UPDATES = set()
WEB_URL = "https://thenh-tin-tuc-the-thao.onrender.com/"

# Model cho từng tác vụ
MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"   # Xử lý ảnh → đăng bài
MODEL_CHAT   = "groq/compound"                   # Chat text → web search, tin mới nhất

if GROQ_API_KEY:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
    print(f"=== VISION MODEL: {MODEL_VISION} ===")
    print(f"=== CHAT MODEL:   {MODEL_CHAT} ===")
else:
    print("=== KHÔNG TÌM THẤY GROQ_API_KEY ===")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def send_telegram_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=REQUEST_TIMEOUT
        )
    except Exception as e:
        print("SEND MSG ERROR:", e)


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@csrf_exempt
def telegram_ai_webhook(request):

    global LAST_REQUEST_TIME
    global PROCESSED_UPDATES

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    try:
        update = json.loads(request.body.decode('utf-8'))
        print("TELEGRAM UPDATE:", update)

        # Chống update lặp
        update_id = update.get("update_id")
        if update_id in PROCESSED_UPDATES:
            return HttpResponse("OK", status=200)
        PROCESSED_UPDATES.add(update_id)
        if len(PROCESSED_UPDATES) > 100:
            PROCESSED_UPDATES.clear()

        if "message" not in update:
            return HttpResponse("OK", status=200)

        message = update["message"]
        chat_id = str(message["chat"]["id"])

        # Kiểm tra chat ID
        if YOUR_TELEGRAM_CHAT_ID:
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

        if not client:
            print("Groq client lỗi")
            return HttpResponse("OK", status=200)

        # Chống spam
        current_time = time.time()
        if current_time - LAST_REQUEST_TIME < 5:
            print("SPAM REQUEST BLOCKED")
            return HttpResponse("OK", status=200)
        LAST_REQUEST_TIME = current_time

        # =====================================================
        # NHÁNH 1: ẢNH + CAPTION → ĐĂNG BÀI
        # Dùng llama-3.2-11b-vision-preview (hỗ trợ ảnh)
        # =====================================================

        if "photo" in message and "caption" in message:

            keywords = message["caption"]
            photo_file = message["photo"][-1]
            file_id = photo_file["file_id"]

            send_telegram_message(chat_id, "⏳ Đang xử lý ảnh và viết bài...")

            # Lấy file info
            file_info_res = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}",
                timeout=REQUEST_TIMEOUT
            ).json()

            if not file_info_res.get("ok"):
                send_telegram_message(chat_id, "❌ Không tải được ảnh.")
                return HttpResponse("OK", status=200)

            file_path = file_info_res["result"]["file_path"]

            # Download ảnh
            image_data = requests.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                timeout=REQUEST_TIMEOUT
            ).content

            # Encode base64
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            prompt = f"""Bạn là biên tập viên thể thao chuyên nghiệp.
Dựa vào ảnh và từ khóa: "{keywords}"
Viết bài báo thể thao hấp dẫn bằng tiếng Việt, nội dung ít nhất 300 từ.
Trả về JSON với đúng 2 trường: title và content."""

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
                        max_tokens=2000,
                        response_format={"type": "json_object"}
                    )
                    ai_text = completion.choices[0].message.content
                    print("VISION RESPONSE:", ai_text[:200])
                    break

                except Exception as ai_error:
                    print(f"VISION ERROR attempt {attempt}:", ai_error)
                    time.sleep(5)

            if not ai_text:
                send_telegram_message(chat_id, "❌ AI vision đang quá tải. Thử lại sau.")
                return HttpResponse("OK", status=200)

            # Parse JSON và lưu bài
            try:
                ai_data = json.loads(ai_text)
                ai_title = ai_data.get("title") or "Tin thể thao mới"
                ai_content = ai_data.get("content") or ""

                print("TITLE:", ai_title)
                print("CONTENT LEN:", len(ai_content))

                # Lưu Post trước → có ID → sau đó upload ảnh lên Cloudinary
                new_post = Post(title=ai_title, content=ai_content)
                new_post.save()

                filename = f"tele_{file_id[:10]}.jpg"
                new_post.image_file.save(filename, ContentFile(image_data), save=True)

                send_telegram_message(
                    chat_id,
                    f"✅ Đăng bài thành công!\n\n📌 {ai_title}\n\n🌐 {WEB_URL}"
                )

            except Exception as save_error:
                print("SAVE ERROR:", save_error)
                send_telegram_message(chat_id, f"❌ Lỗi lưu bài: {str(save_error)[:100]}")

        # =====================================================
        # NHÁNH 2: TEXT → CHATBOT
        # Dùng groq/compound (web search, biết tin mới nhất)
        # =====================================================

        elif "text" in message:

            user_text = message["text"]

            if user_text.strip() == "/start":
                send_telegram_message(
                    chat_id,
                    "🤖 Bot AI Thể Thao đã sẵn sàng!\n\n"
                    "📸 Gửi ảnh + caption → Tự động đăng bài lên web\n"
                    "💬 Nhắn tin → Hỏi đáp thể thao với tin tức MỚI NHẤT"
                )
                return HttpResponse("OK", status=200)

            bot_reply_text = "❌ AI đang bận."

            for attempt in range(3):
                try:
                    completion = client.chat.completions.create(
                        model=MODEL_CHAT,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Bạn là trợ lý AI thể thao thông minh, có khả năng tìm kiếm "
                                    "tin tức thể thao mới nhất trên internet. "
                                    "Luôn trả lời bằng tiếng Việt, ngắn gọn, súc tích, có số liệu cụ thể."
                                )
                            },
                            {
                                "role": "user",
                                "content": user_text
                            }
                        ],
                        max_tokens=1000
                    )
                    bot_reply_text = completion.choices[0].message.content
                    print("CHAT RESPONSE:", bot_reply_text[:100])
                    break

                except Exception as chat_error:
                    print(f"CHAT ERROR attempt {attempt}:", chat_error)
                    time.sleep(5)

            send_telegram_message(chat_id, bot_reply_text)

        return HttpResponse("OK", status=200)

    except Exception as e:
        print("WEBHOOK ERROR:", e)
        return HttpResponse("OK", status=200)