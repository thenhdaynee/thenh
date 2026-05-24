from django.shortcuts import render, get_object_or_404
from .models import Post

def home(request):
    # 1. MỚI NHẤT: Lấy 5 bài viết vừa đăng xong (Dùng cho cả bài lớn và 4 bài nhỏ Block 1)
    latest_posts = Post.objects.all().order_by('-created_at')[:5]
    
    # 2. THỊNH HÀNH: Lấy 4 bài có lượt xem (views_count) cao nhất (Dùng cho Block 2)
    # Sắp xếp giảm dần theo lượt xem, nếu bằng lượt xem thì bài mới hơn xếp trên
    trending_posts = Post.objects.all().order_by('-views_count', '-created_at')[:4]
    
    # 3. TIN TIÊU ĐIỂM: Lấy các bài được tích chọn 'is_featured' ở Admin (Dùng cho Block 4)
    featured_posts = Post.objects.filter(is_featured=True).order_by('-created_at')[:6]

    # Gửi tất cả các danh sách đã lọc riêng biệt sang HTML template
    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': featured_posts,
    }
    return render(request, 'index.html', context)


def post_detail(request, pk):
    """
    Hàm xử lý xem chi tiết bài viết.
    Mỗi lần người dùng ấn vào xem, lượt xem (views_count) của bài đó sẽ tự tăng lên 1.
    """
    post = get_object_or_404(Post, pk=pk)
    
    # Tự động tăng lượt xem lên 1
    post.views_count += 1
    post.save(update_fields=['views_count']) # Chỉ cập nhật cột views_count để tối ưu tốc độ hệ thống
    
    return render(request, 'post_detail.html', {'post': post})
import os
import json
import requests
import google.generativeai as genai
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile
from .models import Post  # Đã chuẩn theo Model Post của bạn

# Đọc cấu hình bảo mật từ file .env
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Cấu hình Gemini API
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
            
            # 1. Bảo mật: Chỉ chính bạn (trùng CHAT_ID) mới được quyền đăng bài
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID):
                return HttpResponse("Unauthorized", status=403)

            # 2. Kiểm tra nếu bạn gửi Ảnh kèm Caption (Từ khóa)
            if "photo" in message and "caption" in message:
                keywords = message["caption"] 
                
                # Lấy file ảnh chất lượng cao nhất từ Telegram
                photo_file = message["photo"][-1]
                file_id = photo_file["file_id"]
                
                # Gọi API Telegram lấy đường dẫn tải ảnh
                file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
                file_info_res = requests.get(file_info_url).json()
                
                if file_info_res.get("ok"):
                    file_path = file_info_res["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                    
                    # Tải ảnh về bộ nhớ tạm dưới dạng bytes
                    image_response = requests.get(download_url)
                    image_data = image_response.content
                    
                    # --- GỬI SANG GEMINI AI VIẾT BÀI ---
                    image_part = {
                        "mime_type": "image/jpeg",
                        "data": image_data
                    }
                    
                    prompt = f"""
                    Bạn là một biên tập viên tin tức thể thao chuyên nghiệp và giàu kinh nghiệm. 
                    Dựa vào bức ảnh được cung cấp và các từ khóa gợi ý sau: "{keywords}".
                    Hãy viết một bài báo hoàn chỉnh, phân tích sâu sắc, lôi cuốn bằng tiếng Việt.
                    
                    YÊU CẦU ĐẦU RA PHẢI THEO ĐÚNG CẤU TRÚC JSON DƯỚI ĐÂY (Tuyệt đối không viết thêm chữ nào khác ngoài cấu trúc JSON này):
                    {{
                        "title": "Tiêu đề bài viết hay, giật gân, chuẩn SEO",
                        "content": "Nội dung chi tiết bài viết (chia thành các đoạn văn rõ ràng, có phân tích)"
                    }}
                    """
                    
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    ai_response = model.generate_content([prompt, image_part])
                    
                    try:
                        # Làm sạch chuỗi JSON trả về từ AI đề phòng markdown thừa
                        clean_json = ai_response.text.strip().strip("```json").strip("```").strip()
                        ai_data = json.loads(clean_json)
                        
                        ai_title = ai_data.get("title")
                        ai_content = ai_data.get("content")
                        
                        # 3. TỰ ĐỘNG LƯU VÀO DATABASE WEB DJANGO
                        new_post = Post(
                            title=ai_title,
                            content=ai_content
                        )
                        # Đặt tên file ảnh và gán vào trường image_file (CloudinaryField tự động upload lên Cloudinary)
                        filename = f"tele_{file_id[:10]}.jpg"
                        new_post.image_file.save(filename, ContentFile(image_data), save=False)
                        
                        # Lưu toàn bộ Object bài viết vào database
                        new_post.save()
                        
                        # Bot nhắn tin phản hồi báo thành công cho bạn ngay trên Telegram
                        reply_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(reply_url, json={
                            "chat_id": chat_id,
                            "text": f"✅ AI đã viết bài và đăng thành công lên Web!\n\n📌 *Tiêu đề:* {ai_title}\n\n🔗 Xem trên web: https://thenhtintucthethao.onrender.com/"
                        })
                        
                    except Exception as json_err:
                        print("Lỗi parse JSON từ AI:", json_err)
                        # Gửi thông báo lỗi về Telegram nếu AI viết sai định dạng
                        error_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(error_url, json={
                            "chat_id": chat_id,
                            "text": f"❌ Lỗi xử lý bài viết từ AI. Vui lòng thử lại với từ khóa rõ ràng hơn."
                        })
                    
            return HttpResponse("OK", status=200)
        except Exception as e:
            print("Lỗi hệ thống Webhook:", e)
            return HttpResponse("Error", status=500)
            
    return HttpResponse("Method not allowed", status=405)