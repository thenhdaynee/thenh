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

    trending_posts = Post.objects.all().order_by(
        '-views_count',
        '-created_at'
    )[:4]

    featured_posts = Post.objects.filter(
        is_featured=True
    ).order_by('-created_at')[:6]

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

    return render(
        request,
        'post_detail.html',
        {'post': post}
    )


# =========================================================
# TELEGRAM + GROQ
# =========================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 15
LAST_REQUEST_TIME = 0
PROCESSED_UPDATES = set()

# =========================================================
# GROQ CHECK
# =========================================================

if GROQ_API_KEY:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
else:
    print("=== KHÔNG TÌM THẤY GROQ_API_KEY ===")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


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

        # =====================================================
        # CHỐNG UPDATE LẶP
        # =====================================================
        update_id = update.get("update_id")

        if update_id in PROCESSED_UPDATES:
            return HttpResponse("OK", status=200)

        PROCESSED_UPDATES.add(update_id)

        if len(PROCESSED_UPDATES) > 100:
            PROCESSED_UPDATES.clear()

        # =====================================================
        # CHECK MESSAGE
        # =====================================================
        if "message" not in update:
            return HttpResponse("OK", status=200)

        message = update["message"]
        chat_id = str(message["chat"]["id"])

        # =====================================================
        # CHECK CHAT ID
        # =====================================================
        if YOUR_TELEGRAM_CHAT_ID:
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

        # =====================================================
        # CHECK GROQ
        # =====================================================
        if not client:
            print("Groq client lỗi hoặc thiếu API Key")
            return HttpResponse("OK", status=200)

        # =====================================================
        # CHỐNG SPAM REQUEST
        # =====================================================
        current_time = time.time()

        if current_time - LAST_REQUEST_TIME < 5:
            print("SPAM REQUEST BLOCKED")
            return HttpResponse("OK", status=200)

        LAST_REQUEST_TIME = current_time

        # =====================================================
        # NHÁNH ĐĂNG BÀI TỪ ẢNH
        # =====================================================
        if "photo" in message and "caption" in message:
            keywords = message["caption"]
            photo_file = message["photo"][-1]
            file_id = photo_file["file_id"]

            # Lấy file info
            file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            file_info_res = requests.get(file_info_url, timeout=REQUEST_TIMEOUT).json()

            if not file_info_res.get("ok"):
                return HttpResponse("OK", status=200)

            file_path = file_info_res["result"]["file_path"]

            # Download ảnh
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            image_response = requests.get(download_url, timeout=REQUEST_TIMEOUT)
            image_data = image_response.content

            # Encode ảnh sang base64 cho Groq
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            prompt = f"""Bạn là một biên tập viên thể thao chuyên nghiệp.
Dựa vào ảnh và từ khóa: "{keywords}"
Hãy viết bài báo thể thao hấp dẫn bằng tiếng Việt.
CHỈ trả về JSON nguyên bản không bọc Markdown (không bọc trong ```json ... ```):
{{
    "title": "Tiêu đề hay, giật gân, chuẩn SEO",
    "content": "Nội dung bài viết chi tiết, phân tích sâu"
}}"""

            ai_response = None
            for attempt in range(3):
                try:
                    # Đổi model vision phù hợp của Llama 3.2 Vision trên Groq nếu xử lý ảnh
                    completion = client.chat.completions.create(
                        model="llama-3.2-90b-vision-preview",
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
                    ai_response = completion.choices[0].message.content
                    break
                except Exception as ai_error:
                    print("GROQ IMAGE ERROR:", ai_error)
                    time.sleep(3)

            if not ai_response:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "❌ AI đang quá tải khi phân tích ảnh. Thử lại sau."
                    },
                    timeout=REQUEST_TIMEOUT
                )
                return HttpResponse("OK", status=200)

            # Parse JSON và lưu vào DB
            try:
                raw_text = ai_response.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]

                ai_data = json.loads(raw_text.strip())
                ai_title = ai_data.get("title", "Tin thể thao mới")
                ai_content = ai_data.get("content", "")

                # FIX LỖI TẠI ĐÂY: Lưu Model cha trước để tránh lỗi NoneType khi lưu File liên kết
                new_post = Post(title=ai_title, content=ai_content)
                new_post.save() 

                filename = f"tele_{file_id[:10]}.jpg"
                new_post.image_file.save(
                    filename,
                    ContentFile(image_data),
                    save=True # Thực hiện cập nhật chỉnh sửa lưu ảnh trực tiếp
                )

                # Thông báo thành công
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": (
                            f"✅ AI đã đăng bài thành công!\n\n"
                            f"📌 {ai_title}\n\n"
                            f"🌐 https://thenh-tin-tuc-the-thao.onrender.com/"
                        )
                    },
                    timeout=REQUEST_TIMEOUT
                )

            except Exception as json_error:
                print("JSON ERROR:", json_error)
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "❌ Lỗi cấu trúc xử lý dữ liệu AI."
                    },
                    timeout=REQUEST_TIMEOUT
                )

        # =====================================================
        # CHATBOT TEXT
        # =====================================================
        elif "text" in message:
            user_text = message["text"]

            if user_text.strip() == "/start":
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "🤖 Bot AI thể thao đã hoạt động!"
                    },
                    timeout=REQUEST_TIMEOUT
                )
                return HttpResponse("OK", status=200)

            bot_reply_text = "❌ AI đang bận."
            for attempt in range(3):
                try:
                    completion = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {
                                "role": "system",
                                "content": "Bạn là trợ lý AI thể thao. Trả lời ngắn gọn bằng tiếng Việt."
                            },
                            {
                                "role": "user",
                                "content": user_text
                            }
                        ],
                        max_tokens=1000
                    )
                    bot_reply_text = completion.choices[0].message.content
                    break
                except Exception as chat_error:
                    print("CHAT ERROR:", chat_error)
                    time.sleep(3)

            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": bot_reply_text
                },
                timeout=REQUEST_TIMEOUT
            )

        return HttpResponse("OK", status=200)

    except Exception as e:
        print("WEBHOOK ERROR:", e)
        return HttpResponse("OK", status=200)