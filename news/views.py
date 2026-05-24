import os
import json
import requests
import google.generativeai as genai
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile
from .models import Post

# =====================================================================
# 1. CÁC HÀM XỬ LÝ VIEW GIAO DIỆN WEB
# =====================================================================

def home(request):
    # MỚI NHẤT: Lấy 5 bài viết vừa đăng xong
    latest_posts = Post.objects.all().order_by('-created_at')[:5]
    
    # THỊNH HÀNH: Lấy 4 bài có lượt xem cao nhất
    trending_posts = Post.objects.all().order_by('-views_count', '-created_at')[:4]
    
    # TIN TIÊU ĐIỂM: Lấy các bài được tích chọn 'is_featured' ở Admin
    featured_posts = Post.objects.filter(is_featured=True).order_by('-created_at')[:6]

    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': featured_posts,
    }
    return render(request, 'index.html', context)


def post_detail(request, pk):
    """
    Hàm xử lý xem chi tiết bài viết.
    Mỗi lần người dùng ấn vào xem, lượt xem (views_count) sẽ tự tăng lên 1.
    """
    post = get_object_or_404(Post, pk=pk)
    
    # Tự động tăng lượt xem lên 1
    post.views_count += 1
    post.save(update_fields=['views_count']) # Tối ưu tốc độ hệ thống
    
    return render(request, 'post_detail.html', {'post': post})


# =====================================================================
# 2. BỘ NÃO XỬ LÝ WEBHOOK TELEGRAM + GEMINI AI (ĐĂNG BÀI + TƯƠNG TÁC CHAT)
# =====================================================================

# Đọc cấu hình bảo mật từ file .env hoặc cấu hình Render Environment
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Kích hoạt cấu hình cho Gemini API
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

@csrf_exempt
def telegram_ai_webhook(request):
    if request.method == "POST":
        try:
            update = json.loads(request.body.decode('utf-8'))
            if "message" not in update:
                return HttpResponse("OK")
                
            message = update["message"]
            chat_id = str(message["chat"]["id"])
            
            # Bảo mật: Ép kiểu chuỗi để so sánh chính xác chat_id của bạn, tránh người lạ hack hệ thống
            if YOUR_TELEGRAM_CHAT_ID and chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

            # Khởi tạo Model Gemini dùng chung (Ép đường dẫn tuyệt đối ổn định)
            model = genai.GenerativeModel('models/gemini-1.5-flash')

            # -----------------------------------------------------------------
            # NHÁNH 1: GỬI ẢNH KÈM CHỮ -> TỰ ĐỘNG ĐĂNG BÀI LÊN WEB
            # -----------------------------------------------------------------
            if "photo" in message and "caption" in message:
                keywords = message["caption"] 
                
                # Lấy file ảnh có độ phân giải tốt nhất từ mảng ảnh gửi về
                photo_file = message["photo"][-1]
                file_id = photo_file["file_id"]
                
                # Truy vấn Telegram API lấy đường link download trực tiếp ảnh
                file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
                file_info_res = requests.get(file_info_url).json()
                
                if file_info_res.get("ok"):
                    file_path = file_info_res["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                    
                    # Tải ảnh từ máy chủ Telegram về RAM dưới dạng nhị phân (bytes)
                    image_response = requests.get(download_url)
                    image_data = image_response.content
                    
                    # Chuẩn bị dữ liệu ảnh gửi đi cho cấu trúc của Gemini API
                    image_part = {
                        "mime_type": "image/jpeg",
                        "data": image_data
                    }
                    
                    # Tạo Prompt mẫu ép AI xử lý viết bài và trả ra cấu trúc dữ liệu JSON sạch
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
                    
                    # Gọi model Gemini để phân tích ảnh kết hợp chữ
                    ai_response = model.generate_content([prompt, image_part])
                    
                    try:
                        # Tiến hành làm sạch chuỗi văn bản nhận về từ AI (loại bỏ các thẻ markdown ```json)
                        raw_text = ai_response.text.strip()
                        if raw_text.startswith("```json"):
                            raw_text = raw_text[7:]
                        if raw_text.endswith("```"):
                            raw_text = raw_text[:-3]
                        clean_json = raw_text.strip()
                        
                        ai_data = json.loads(clean_json)
                        ai_title = ai_data.get("title")
                        ai_content = ai_data.get("content")
                        
                        # Khởi tạo bản ghi Model và gán dữ liệu
                        new_post = Post(
                            title=ai_title,
                            content=ai_content
                        )
                        
                        # Lưu ảnh trực tiếp vào trường Cloudinary thông qua bộ nhớ tạm ContentFile
                        filename = f"tele_{file_id[:10]}.jpg"
                        new_post.image_file.save(filename, ContentFile(image_data), save=False)
                        
                        # Đồng bộ lưu toàn bộ dữ liệu vào Database
                        new_post.save()
                        
                        # Phản hồi ngược kết quả thành công cho bạn trực tiếp trên khung Chat Telegram
                        reply_url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(reply_url, json={
                            "chat_id": chat_id,
                            "text": f"✅ AI đã viết bài và đăng thành công lên Web!\n\n📌 *Tiêu đề:* {ai_title}\n\n🔗 Xem trên web: [https://thenhtintucthethao.onrender.com/](https://thenhtintucthethao.onrender.com/)"
                        })
                        
                    except Exception as json_err:
                        print("Lỗi parse JSON từ AI:", json_err)
                        # Gửi thông báo lỗi về Telegram nếu cấu trúc phản hồi từ AI không hợp lệ
                        error_url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(error_url, json={
                            "chat_id": chat_id,
                            "text": f"❌ Lỗi cấu trúc bài viết từ AI. Vui lòng gửi lại ảnh với từ khóa cụ thể hơn."
                        })

            # -----------------------------------------------------------------
            # NHÁNH 2 (MỚI): CHỈ NHẮN CHỮ -> CHATBOT TƯƠNG TÁC, HỎI ĐÁP VỚI GEMINI
            # -----------------------------------------------------------------
            elif "text" in message:
                user_text = message["text"]
                
                # Bỏ qua không xử lý lệnh kích hoạt /start mặc định của Telegram
                if user_text.strip() != "/start":
                    # Gửi câu hỏi chat của bạn sang cho Gemini AI trả lời ngắn gọn, tự nhiên
                    ai_chat_response = model.generate_content(
                        f"Bạn là một trợ lý thông minh am hiểu thể thao. Hãy trả lời câu hỏi sau của người dùng bằng tiếng Việt một cách tự nhiên, ngắn gọn: {user_text}"
                    )
                    bot_reply_text = ai_chat_response.text
                    
                    # Gửi câu trả lời của AI ngược lại về khung chat Telegram
                    chat_url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
                    requests.post(chat_url, json={
                        "chat_id": chat_id,
                        "text": bot_reply_text
                    })
                    
            return HttpResponse("OK", status=200)
        except Exception as e:
            print("Lỗi hệ thống Webhook:", e)
            return HttpResponse("Error", status=500)
            
    return HttpResponse("Method not allowed", status=405)