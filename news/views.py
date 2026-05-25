import os
import json
import time
import requests

from google import genai
from google.genai import types

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
# TELEGRAM + GEMINI
# =========================================================

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 15

LAST_REQUEST_TIME = 0
PROCESSED_UPDATES = set()

if GEMINI_KEY:
    print(f"=== GEMINI KEY OK: {GEMINI_KEY[:6]}... ===")
else:
    print("=== KHÔNG TÌM THẤY GEMINI_API_KEY ===")

client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None


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
        # CHỐNG UPDATE BỊ GỬI LẶP
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
        # CHECK USER
        # =====================================================

        if YOUR_TELEGRAM_CHAT_ID:
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

        # =====================================================
        # CHECK GEMINI
        # =====================================================

        if not client:
            print("Gemini client lỗi")
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
        # NHÁNH ĐĂNG BÀI
        # =====================================================

        if "photo" in message and "caption" in message:

            keywords = message["caption"]

            photo_file = message["photo"][-1]

            file_id = photo_file["file_id"]

            # =====================================================
            # GET FILE INFO
            # =====================================================

            file_info_url = (
                f"https://api.telegram.org/bot"
                f"{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            )

            file_info_res = requests.get(
                file_info_url,
                timeout=REQUEST_TIMEOUT
            ).json()

            if not file_info_res.get("ok"):
                return HttpResponse("OK", status=200)

            file_path = file_info_res["result"]["file_path"]

            # =====================================================
            # DOWNLOAD IMAGE
            # =====================================================

            download_url = (
                f"https://api.telegram.org/file/bot"
                f"{TELEGRAM_BOT_TOKEN}/{file_path}"
            )

            image_response = requests.get(
                download_url,
                timeout=REQUEST_TIMEOUT
            )

            image_data = image_response.content

            # =====================================================
            # PROMPT
            # =====================================================

            prompt = f"""
            Bạn là một biên tập viên thể thao chuyên nghiệp.

            Dựa vào ảnh và từ khóa:
            "{keywords}"

            Hãy viết bài báo thể thao hấp dẫn bằng tiếng Việt.

            CHỈ trả về JSON:

            {{
                "title": "Tiêu đề",
                "content": "Nội dung"
            }}
            """

            # =====================================================
            # GEMINI
            # =====================================================

            try:

                ai_response = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=[
                        prompt,
                        types.Part.from_bytes(
                            data=image_data,
                            mime_type="image/jpeg"
                        )
                    ]
                )

            except Exception as ai_error:

                print("GEMINI ERROR:", ai_error)

                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "❌ Gemini đang quá tải. Thử lại sau."
                    },
                    timeout=REQUEST_TIMEOUT
                )

                return HttpResponse("OK", status=200)

            # =====================================================
            # JSON PARSE
            # =====================================================

            try:

                raw_text = ai_response.text.strip()

                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]

                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]

                ai_data = json.loads(raw_text.strip())

                ai_title = ai_data.get("title", "Tin thể thao mới")

                ai_content = ai_data.get("content", "")

                # =====================================================
                # SAVE POST
                # =====================================================

                new_post = Post(
                    title=ai_title,
                    content=ai_content
                )

                filename = f"tele_{file_id[:10]}.jpg"

                new_post.image_file.save(
                    filename,
                    ContentFile(image_data),
                    save=False
                )

                new_post.save()

                # =====================================================
                # TELEGRAM SUCCESS
                # =====================================================

                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": (
                            f"✅ AI đã đăng bài thành công!\n\n"
                            f"📌 {ai_title}\n\n"
                            f"🌐 https://thenhtintucthethao.onrender.com/"
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
                        "text": "❌ Lỗi xử lý dữ liệu AI."
                    },
                    timeout=REQUEST_TIMEOUT
                )

        # =====================================================
        # CHATBOT
        # =====================================================

        elif "text" in message:

            user_text = message["text"]

            if user_text.strip() == "/start":

                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "🤖 Bot AI thể thao đã hoạt động."
                    },
                    timeout=REQUEST_TIMEOUT
                )

                return HttpResponse("OK", status=200)

            try:

                ai_chat_response = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=f"""
                    Bạn là trợ lý AI thể thao.

                    Trả lời ngắn gọn bằng tiếng Việt:

                    {user_text}
                    """
                )

                bot_reply_text = ai_chat_response.text

            except Exception as chat_error:

                print("CHAT ERROR:", chat_error)

                bot_reply_text = "❌ AI đang bận. Vui lòng thử lại."

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