from django.db import models
from cloudinary.models import CloudinaryField

class Category(models.Model):
    name = models.CharField(max_length=50, verbose_name="Tên danh mục")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Slug")
    icon = models.CharField(max_length=10, blank=True, verbose_name="Icon (emoji)")

    class Meta:
        verbose_name = "Danh mục"
        verbose_name_plural = "Danh mục"

    def __str__(self):
        return f"{self.icon} {self.name}".strip()


class Post(models.Model):
    title = models.CharField(max_length=200, verbose_name="Tiêu đề")
    content = models.TextField(verbose_name="Nội dung")
    image_url = models.URLField(blank=True, null=True, verbose_name="Hoặc dán link ảnh")
    image_file = CloudinaryField('Hoặc tải ảnh lên từ máy', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    views_count = models.IntegerField(default=0, verbose_name="Lượt xem")
    is_featured = models.BooleanField(default=False, verbose_name="Tin tiêu điểm (Admin tích)")
    categories = models.ManyToManyField(Category, blank=True, verbose_name="Danh mục")

    def __str__(self):
        return self.title


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments', verbose_name="Bài viết")
    author_name = models.CharField(max_length=100, verbose_name="Tên người bình luận")
    content = models.TextField(verbose_name="Nội dung bình luận")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Ngày bình luận")

    class Meta:
        verbose_name = "Bình luận"
        verbose_name_plural = "Bình luận"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.author_name} - {self.post.title[:30]}"


class Subscriber(models.Model):
    email = models.EmailField(unique=True, verbose_name="Email")
    subscribed_at = models.DateTimeField(auto_now_add=True, verbose_name="Ngày đăng ký")

    class Meta:
        verbose_name = "Người đăng ký"
        verbose_name_plural = "Người đăng ký newsletter"

    def __str__(self):
        return self.email


class LiveScore(models.Model):
    team_a = models.CharField(max_length=20, verbose_name="Đội A")
    team_b = models.CharField(max_length=20, verbose_name="Đội B")
    score_a = models.IntegerField(default=0, verbose_name="Tỷ số A")
    score_b = models.IntegerField(default=0, verbose_name="Tỷ số B")
    status = models.CharField(
        max_length=10,
        choices=[('live', 'Live'), ('ht', 'Hiệp 1'), ('ft', 'FT')],
        default='live',
        verbose_name="Trạng thái"
    )
    match_date = models.DateField(auto_now_add=True, verbose_name="Ngày đấu")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Cập nhật lúc")

    class Meta:
        verbose_name = "Tỷ số trực tiếp"
        verbose_name_plural = "Tỷ số trực tiếp"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.team_a} {self.score_a} - {self.score_b} {self.team_b}"