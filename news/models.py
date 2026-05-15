from django.db import models
from cloudinary.models import CloudinaryField

class Post(models.Model):
    title = models.CharField(max_length=200, verbose_name="Tiêu đề")
    content = models.TextField(verbose_name="Nội dung")
    image_url = models.URLField(blank=True, null=True, verbose_name="Hoặc dán link ảnh")
    image_file = CloudinaryField('Hoặc tải ảnh lên từ máy', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title