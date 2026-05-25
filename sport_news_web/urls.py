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

    # CHI TIẾT BÀI VIẾT
    path(
        'post/<int:pk>/',
        views.post_detail,
        name='post_detail'
    ),

    # TELEGRAM WEBHOOK
    path(
        'telegram-webhook/',
        views.telegram_ai_webhook,
        name='telegram_ai_webhook'
    ),
]

# MEDIA FILES
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )