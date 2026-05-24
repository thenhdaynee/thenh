from django.contrib import admin
from django.urls import path
from news import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    
    # ── THÊM DÒNG NÀY: Đường dẫn xem chi tiết bài viết ──
    # <int:pk> sẽ tự động bắt ID của bài viết (ví dụ: /post/5/) và truyền vào hàm views.post_detail
    path('post/<int:pk>/', views.post_detail, name='post_detail'),
    path('telegram-webhook/', views.telegram_ai_webhook, name='telegram_ai_webhook'),
]

# Thêm dòng này để xem được ảnh trong lúc đang phát triển (DEBUG mode)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)