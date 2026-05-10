from django.shortcuts import render
from django.contrib.auth.models import User  # Thêm dòng này
from .models import Post

# Đoạn code tạo admin tự động
if not User.objects.filter(username='thenh_admin').exists():
    User.objects.create_superuser('thenh_admin', 'thenhredmiturbo@gmail.com', 'Thanh123')

def home(request):
    # Lấy tất cả bài viết, bài mới nhất xếp lên đầu
    posts = Post.objects.all().order_by('-created_at') 
    return render(request, 'index.html', {'posts': posts}) # Gửi danh sách bài viết sang HTML