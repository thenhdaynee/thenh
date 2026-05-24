from django.db import models
from cloudinary.models import CloudinaryField

class Post(models.Model):
    title = models.CharField(max_length=200, verbose_name="Tiêu đề")
    content = models.TextField(verbose_name="Nội dung")
    image_url = models.URLField(blank=True, null=True, verbose_name="Hoặc dán link ảnh")
    image_file = CloudinaryField('Hoặc tải ảnh lên từ máy', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    views_count = models.IntegerField(default=0, verbose_name="Lượt xem")
    is_featured = models.BooleanField(default=False, verbose_name="Tin tiêu điểm (Admin tích)")
    def __str__(self):
        return self.title