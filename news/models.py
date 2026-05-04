from django.db import models

class Post(models.Model):
    title = models.CharField(max_length=200, verbose_name="Tiêu đề")
    content = models.TextField(verbose_name="Nội dung")
    
    # Lựa chọn 1: Dán link ảnh (như cũ)
    image_url = models.URLField(blank=True, null=True, verbose_name="Hoặc dán link ảnh")
    
    # Lựa chọn 2: Tải ảnh từ máy tính (mới)
    image_file = models.ImageField(upload_to='news_pics/', blank=True, null=True, verbose_name="Hoặc tải ảnh lên từ máy")
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title