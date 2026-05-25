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
REQUEST_TIMEOUT = 25
LAST_REQUEST_TIME = 0
PROCESSED_UPDATES = set()
WEB_URL = "https://thenh-tin-tuc-the-thao.onrender.com/"

# 2 Model tối ưu: Nhẹ cho Render, xử lý mượt trên Groq Cloud
MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"  # Ảnh → Đăng bài viết
MODEL_CHAT   = "llama-3.3-70b-versatile"                    # Chat text → Web Search gọi hàm

if GROQ_API_KEY:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
    print(f"=== VISION: {MODEL_VISION} ===")
    print(f"=== CHAT + WEB SEARCH: {MODEL_CHAT} ===")
else:
    print("=== KHÔNG TÌM THẤY GROQ_API_KEY ===")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Khai báo cấu trúc định dạng Web Search Tool cho AI nhận diện
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information, news, sports results, and real-time data",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                }
            },
            "required": ["query"]
        }
    }
}


def send_telegram_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=REQUEST_TIMEOUT
        )
    except Exception as e:
        print("SEND MSG ERROR:", e)


def do_web_search(query):
    """Thực hiện đào bới thông tin trực tuyến qua DuckDuckGo API (Free, không cần key)"""
    try:
        url = f"https://api.duckduckgo.com/?q={requests.utils.quote(query)}&format=json&no_html=1&skip_disambig=1"
        res = requests.get(url, timeout=10).json()

        results = []
        if res.get("AbstractText"):
            results.append(res["AbstractText"])

        for topic in res.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(topic["Text"])

        if results:
            return "\n".join(results)
        return "Không tìm thấy kết quả tìm kiếm trực tuyến phù hợp."

    except Exception as e:
        print("WEB SEARCH ERROR:", e)
        return "Lỗi trong quá trình kết nối cổng tìm kiếm trực tuyến."


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

        # Bộ lọc chống trùng lặp request từ Telegram gửi sang
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

        # Kiểm tra phân quyền Chat ID bảo mật
        if YOUR_TELEGRAM_CHAT_ID:
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

        if not client:
            print("Groq client lỗi hoặc chưa cấu hình biến môi trường")
            return HttpResponse("OK", status=200)

        # Chống spam dồn dập (hạn chế tối thiểu 5 giây mỗi lượt gửi)
        current_time = time.time()
        if current_time - LAST_REQUEST_TIME < 5:
            print("SPAM REQUEST BLOCKED")
            return HttpResponse("OK", status=200)
        LAST_REQUEST_TIME = current_time

        # =====================================================
        # NHÁNH 1: ẢNH + CAPTION → TỰ ĐỘNG ĐĂNG BÀI VIẾT
        # =====================================================
        if "photo" in message and "caption" in message:
            keywords = message["caption"]
            photo_file = message["photo"][-1]
            file_id = photo_file["file_id"]

            send_telegram_message(chat_id, "⏳ Đang phân tích ảnh và tiến hành sáng tác bài viết...")

            # Lấy thông tin đường dẫn ảnh từ Telegram
            file_info_res = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}",
                timeout=REQUEST_TIMEOUT
            ).json()

            if not file_info_res.get("ok"):
                send_telegram_message(chat_id, "❌ Lỗi: Không thể tải tệp hình ảnh từ máy chủ Telegram.")
                return HttpResponse("OK", status=200)

            file_path = file_info_res["result"]["file_path"]

            # Tải dữ liệu ảnh binary
            image_data = requests.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                timeout=REQUEST_TIMEOUT
            ).content

            # Chuyển đổi dữ liệu sang chuỗi Base64 truyền cho mô hình Vision
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            # TỐI ƯU PROMPT: Ép chặt cấu trúc JSON đầu ra tránh lỗi xử lý chuỗi
            prompt = f"""Bạn là một biên tập viên, nhà báo bình luận thể thao chuyên nghiệp.
Dựa vào hình ảnh được cung cấp cùng từ khóa định hướng: "{keywords}"
Hãy viết một bài báo thể thao tiếng Việt chuyên sâu, lôi cuốn (độ dài tối thiểu 300 từ).

Bạn BẮT BUỘC phải xuất dữ liệu trả về theo đúng định dạng cấu trúc JSON mẫu sau:
{{
    "title": "Tiêu đề bài báo hấp dẫn, chuẩn SEO",
    "content": "Nội dung bài báo phân tích chi tiết sâu sắc..."
}}"""

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
                send_telegram_message(chat_id, "❌ Trục trặc kỹ thuật: Hệ thống phân tích Vision không phản hồi.")
                return HttpResponse("OK", status=200)

            # Dọn dẹp ký tự thừa và tiến hành nạp cơ sở dữ liệu
            try:
                raw_text = ai_text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]

                ai_data = json.loads(raw_text.strip())
                ai_title = ai_data.get("title") or "Tin tức thể thao nổi bật"
                ai_content = ai_data.get("content") or ""

                # Quy trình lưu an toàn: Khởi tạo dữ liệu -> Lưu lấy ID -> Lưu file ảnh và đồng bộ Cloudinary
                new_post = Post(title=ai_title, content=ai_content)
                new_post.save()

                filename = f"tele_{file_id[:10]}.jpg"
                new_post.image_file.save(filename, ContentFile(image_data), save=True)

                send_telegram_message(
                    chat_id,
                    f"✅ ĐÃ TỰ ĐỘNG ĐĂNG BÀI THÀNH CÔNG!\n\n📌 Tiêu đề: {ai_title}\n\n🌐 Link đọc bài viết: {WEB_URL}"
                )

            except Exception as save_error:
                print("SAVE ERROR:", save_error)
                send_telegram_message(chat_id, f"❌ Thất bại khi ghi bài viết: {str(save_error)[:100]}")

        # =====================================================
        # NHÁNH 2: TEXT → CHATBOT HỎI ĐÁP + SỬ DỤNG WEB SEARCH TOOL
        # =====================================================
        elif "text" in message:
            user_text = message["text"]

            if user_text.strip() == "/start":
                send_telegram_message(
                    chat_id,
                    "🤖 Bot Trợ Lý Thể Thao Đa Năng Sẵn Sàng!\n\n"
                    "📸 [Gửi ảnh kèm Caption] → Hệ thống tự động dùng Llama 4 phân tích, viết bài báo chuẩn SEO dài 300 từ và đăng trực tiếp lên website của bạn.\n\n"
                    "💬 [Gửi tin nhắn văn bản] → Hỏi đáp chuyên sâu, tra cứu tỷ số, chuyển nhượng thể thao mới nhất. Bot sẽ tự động kích hoạt tính năng cào dữ liệu Google/DuckDuckGo thời gian thực để trả lời bạn!"
                )
                return HttpResponse("OK", status=200)

            # Chỉ thông báo tìm kiếm đối với các câu hỏi tin tức cần tra cứu, không gửi bừa bãi tránh spam chat
            bot_reply_text = "❌ Trợ lý AI hiện tại đang bận xử lý dữ liệu."

            for attempt in range(3):
                try:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "Bạn là chuyên gia trợ lý AI thể thao thông minh hàng đầu. "
                                "Nếu người dùng hỏi về kết quả trận đấu, tin tức chuyển nhượng, bảng xếp hạng "
                                "hoặc bất cứ sự kiện nào mới diễn ra gần đây, bạn BẮT BUỘC phải dùng công cụ "
                                "web_search để cập nhật thông tin chính xác nhất trước khi trả lời. "
                                "Luôn phản hồi bằng tiếng Việt ngắn gọn, súc tích, đi thẳng vào vấn đề và có số liệu chứng minh."
                            )
                        },
                        {
                            "role": "user",
                            "content": user_text
                        }
                    ]

                    # Bước 1: Gọi mô hình Llama 3.3 70b xem câu hỏi có cần gọi Tool tìm kiếm không
                    response1 = client.chat.completions.create(
                        model=MODEL_CHAT,
                        messages=messages,
                        tools=[WEB_SEARCH_TOOL],
                        tool_choice="auto",
                        max_tokens=1000
                    )

                    msg = response1.choices[0].message

                    # Bước 2: Kiểm tra nếu mô hình yêu cầu kích hoạt gọi Tool tra cứu internet
                    if msg.tool_calls:
                        # Gửi thông báo cho người dùng biết hệ thống đang đi tìm dữ liệu trực tuyến
                        send_telegram_message(chat_id, "🔍 Đang tìm kiếm thông tin mới nhất trên Internet...")
                        
                        tool_call = msg.tool_calls[0]
                        search_query = json.loads(tool_call.function.arguments).get("query", user_text)
                        print("EXECUTE SEARCH QUERY:", search_query)

                        # Chạy hàm cào dữ liệu DuckDuckGo
                        search_result = do_web_search(search_query)
                        print("SEARCH ENGINE RESULT SUMMARY:", search_result[:150])

                        # Bước 3: Đóng gói lịch sử hội thoại và kết quả tra cứu gửi ngược lại cho AI tổng hợp thành câu trả lời
                        messages.append({
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [
                                {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": tool_call.function.arguments
                                    }
                                }
                            ]
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": search_result
                        })

                        response2 = client.chat.completions.create(
                            model=MODEL_CHAT,
                            messages=messages,
                            max_tokens=1000
                        )
                        bot_reply_text = response2.choices[0].message.content

                    else:
                        # Nếu câu hỏi thông thường không cần dữ liệu mới, AI trả lời trực tiếp ngay lập tức
                        bot_reply_text = msg.content or "❌ Trục trặc: Không thể tạo câu trả lời."

                    break

                except Exception as chat_error:
                    print(f"CHAT AGENT ERROR ATTEMPT {attempt}:", chat_error)
                    time.sleep(4)

            # Gửi câu trả lời hoàn thiện cuối cùng về Telegram chat
            send_telegram_message(chat_id, bot_reply_text)

        return HttpResponse("OK", status=200)

    except Exception as e:
        print("CRITICAL WEBHOOK ERROR:", e)
        return HttpResponse("OK", status=200)