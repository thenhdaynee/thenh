from django.contrib import admin
from django.urls import path
from news import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [

    # ADMIN
    path('admin/', admin.site.urls),

    # HOME
    path('', views.home, name='home'),

    # DANH MỤC
    path('category/<slug:slug>/', views.category_view, name='category_view'),

    # TÌM KIẾM
    path('search/', views.search, name='search'),

    # CHI TIẾT BÀI VIẾT
    path('post/<int:pk>/', views.post_detail, name='post_detail'),

    # BÌNH LUẬN
    path('post/<int:pk>/comment/', views.add_comment, name='add_comment'),

    # TẤT CẢ BÀI VIẾT
    path('posts/', views.all_posts, name='all_posts'),

    # NEWSLETTER
    path('subscribe/', views.subscribe_newsletter, name='subscribe_newsletter'),

    # CHAT API (Web Widget)
    path('chat-api/', views.chat_api, name='chat_api'),

    # TELEGRAM WEBHOOK
    path('telegram-webhook/', views.telegram_ai_webhook, name='telegram_ai_webhook'),
]

# MEDIA FILES
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )