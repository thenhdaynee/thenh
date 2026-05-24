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