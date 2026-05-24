import os
import json
import requests
import google.genai as genai
from google.genai import types
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile
from .models import Post

# =====================================================================
# 1. CÁC HÀM XỬ LÝ VIEW GIAO DIỆN WEB
# =====================================================================

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


# =====================================================================
# 2. BỘ NÃO XỬ LÝ WEBHOOK TELEGRAM + GEMINI AI
# =====================================================================

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Khởi tạo Gemini client mới
client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

@csrf_exempt
def telegram_ai_webhook(request):
    if request.method == "POST":
        try:
            update = json.loads(request.body.decode('utf-8'))
            if "message" not in update:
                return HttpResponse("OK")
                
            message = update["message"]
            chat_id = str(message["chat"]["id"])
            
            if YOUR_TELEGRAM_CHAT_ID and chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

            # -----------------------------------------------------------------
            # NHÁNH 1: GỬI ẢNH KÈM CHỮ -> TỰ ĐỘNG ĐĂNG BÀI LÊN WEB
            # -----------------------------------------------------------------
            if "photo" in message and "caption" in message:
                keywords = message["caption"] 
                
                photo_file = message["photo"][-1]
                file_id = photo_file["file_id"]
                
                file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
                file_info_res = requests.get(file_info_url).json()
                
                if file_info_res.get("ok"):
                    file_path = file_info_res["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                    
                    image_response = requests.get(download_url)
                    image_data = image_response.content
                    
                    prompt = f"""
                    Bạn là một biên tập viên tin tức thể thao chuyên nghiệp và giàu kinh nghiệm. 
                    Dựa vào bức ảnh được cung cấp và các từ khóa gợi ý sau: "{keywords}".
                    Hãy viết một bài báo hoàn chỉnh, phân tích sâu sắc, lôi cuốn bằng tiếng Việt.
                    
                    YÊU CẦU ĐẦU RA PHẢI THEO ĐÚNG CẤU TRÚC JSON DƯỚI ĐÂY (Tuyệt đối không viết kèm thêm bất cứ câu từ chào hỏi nào nằm ngoài cấu trúc JSON này):
                    {{
                        "title": "Tiêu đề bài viết hay, giật gân, chuẩn SEO",
                        "content": "Nội dung chi tiết bài viết (chia thành các đoạn văn rõ ràng, có phân tích)"
                    }}
                    """
                    
                    # Gọi Gemini với API mới
                    ai_response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            prompt,
                            types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
                        ]
                    )
                    
                    try:
                        raw_text = ai_response.text.strip()
                        if raw_text.startswith("```json"):
                            raw_text = raw_text[7:]
                        if raw_text.endswith("```"):
                            raw_text = raw_text[:-3]
                        clean_json = raw_text.strip()
                        
                        ai_data = json.loads(clean_json)
                        ai_title = ai_data.get("title")
                        ai_content = ai_data.get("content")
                        
                        new_post = Post(
                            title=ai_title,
                            content=ai_content
                        )
                        
                        filename = f"tele_{file_id[:10]}.jpg"
                        new_post.image_file.save(filename, ContentFile(image_data), save=False)
                        new_post.save()
                        
                        reply_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(reply_url, json={
                            "chat_id": chat_id,
                            "text": f"✅ AI đã viết bài và đăng thành công lên Web!\n\n📌 Tiêu đề: {ai_title}\n\n🔗 Xem trên web: https://thenhtintucthethao.onrender.com/"
                        })
                        
                    except Exception as json_err:
                        print("Lỗi parse JSON từ AI:", json_err)
                        error_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(error_url, json={
                            "chat_id": chat_id,
                            "text": "❌ Lỗi cấu trúc bài viết từ AI. Vui lòng gửi lại ảnh với từ khóa cụ thể hơn."
                        })

            # -----------------------------------------------------------------
            # NHÁNH 2: CHỈ NHẮN CHỮ -> CHATBOT TƯƠNG TÁC VỚI GEMINI
            # -----------------------------------------------------------------
            elif "text" in message:
                user_text = message["text"]
                
                if user_text.strip() != "/start":
                    ai_chat_response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=f"Bạn là một trợ lý thông minh am hiểu thể thao. Hãy trả lời câu hỏi sau của người dùng bằng tiếng Việt một cách tự nhiên, ngắn gọn: {user_text}"
                    )
                    bot_reply_text = ai_chat_response.text
                    
                    chat_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    requests.post(chat_url, json={
                        "chat_id": chat_id,
                        "text": bot_reply_text
                    })
                    
            return HttpResponse("OK", status=200)
        except Exception as e:
            print("Lỗi hệ thống Webhook:", e)
            return HttpResponse("Error", status=500)
            
    return HttpResponse("Method not allowed", status=405)