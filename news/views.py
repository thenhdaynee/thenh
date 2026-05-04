from django.shortcuts import render
from .models import Post # Nhập bảng Post vào

def home(request):
    # Lấy tất cả bài viết, bài mới nhất xếp lên đầu
    posts = Post.objects.all().order_by('-created_at') 
    return render(request, 'index.html', {'posts': posts}) # Gửi danh sách bài viết sang HTML