import json
import time
import base64
import requests

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST

import cloudinary.uploader
from .models import Post, Category, Comment, Subscriber
from .ai_services import (
    client, chat_with_ai, send_telegram_message,
    generate_article, LAST_CHAT_API_TIME, LAST_REQUEST_TIME,
    PROCESSED_UPDATES, REQUEST_TIMEOUT,
    TELEGRAM_BOT_TOKEN, YOUR_TELEGRAM_CHAT_ID, WEB_URL
)

# =========================================================
# HELPERS
# =========================================================

def get_live_scores():
    import datetime, random
    today = datetime.date.today()
    base = today.toordinal()
    random.seed(base)

    teams = [
        ("VIE", "THA"), ("ENG", "FRA"), ("BAR", "RMA"),
        ("MCI", "LIV"), ("JUV", "MIL"), ("PSG", "BAY"),
        ("ARS", "CHE"), ("ACM", "INT"),
    ]
    statuses = ["live", "live", "ht", "live", "ft", "live", "ht", "ft"]
    chosen = random.sample(list(zip(teams, statuses)), 5)

    scores = []
    for (a, b), st in chosen:
        s1 = random.randint(0, 4)
        s2 = random.randint(0, 4)
        scores.append({
            "display": f"{a} {s1} - {s2} {b}",
            "status": st,
        })
    return scores


# =========================================================
# WEB
# =========================================================

def home(request):
    latest_posts = Post.objects.all().order_by('-created_at')[:5]
    trending_posts = Post.objects.all().order_by('-views_count', '-created_at')[:4]
    featured_posts = Post.objects.filter(is_featured=True).order_by('-created_at')[:6]
    categories = Category.objects.all()
    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': featured_posts,
        'categories': categories,
        'current_category': None,
        'query': '',
        'live_scores': get_live_scores(),
    }
    return render(request, 'index.html', context)


def category_view(request, slug):
    category = get_object_or_404(Category, slug=slug)
    all_posts = Post.objects.filter(categories=category).order_by('-created_at')
    latest_posts = all_posts[:5]
    trending_posts = Post.objects.filter(categories=category).order_by('-views_count', '-created_at')[:4]
    featured_posts = Post.objects.filter(categories=category, is_featured=True).order_by('-created_at')[:6]
    categories = Category.objects.all()
    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': featured_posts,
        'categories': categories,
        'current_category': category,
        'query': '',
        'live_scores': get_live_scores(),
    }
    return render(request, 'index.html', context)


def search(request):
    q = request.GET.get('q', '').strip()
    if q:
        all_posts = Post.objects.filter(
            Q(title__icontains=q) | Q(content__icontains=q)
        ).order_by('-created_at')
    else:
        all_posts = Post.objects.none()
    latest_posts = all_posts[:5]
    trending_posts = Post.objects.all().order_by('-views_count', '-created_at')[:4]
    categories = Category.objects.all()
    context = {
        'latest_posts': latest_posts,
        'trending_posts': trending_posts,
        'featured_posts': Post.objects.filter(is_featured=True).order_by('-created_at')[:6],
        'categories': categories,
        'current_category': None,
        'query': q,
        'live_scores': get_live_scores(),
    }
    return render(request, 'index.html', context)


def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)
    post.views_count += 1
    post.save(update_fields=['views_count'])
    comments = post.comments.all()
    categories = Category.objects.all()

    # Reading time
    word_count = len(post.content.split())
    reading_time = max(1, round(word_count / 200))

    # Related posts (same categories, exclude current)
    related_posts = Post.objects.filter(
        categories__in=post.categories.all()
    ).exclude(pk=post.pk).distinct().order_by('-views_count', '-created_at')[:4]

    context = {
        'post': post,
        'comments': comments,
        'categories': categories,
        'live_scores': get_live_scores(),
        'reading_time': reading_time,
        'related_posts': related_posts,
    }
    return render(request, 'post_detail.html', context)


def add_comment(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if request.method == 'POST':
        author_name = request.POST.get('author_name', '').strip()
        content = request.POST.get('content', '').strip()
        if author_name and content:
            Comment.objects.create(
                post=post,
                author_name=author_name,
                content=content
            )
            messages.success(request, 'Bình luận của bạn đã được gửi!')
        else:
            messages.error(request, 'Vui lòng điền đầy đủ tên và nội dung bình luận.')
    return redirect('post_detail', pk=pk)


# =========================================================
# ALL POSTS (Pagination)
# =========================================================

def all_posts(request):
    sort = request.GET.get('sort', 'latest')
    page = request.GET.get('page', 1)

    if sort == 'featured':
        posts = Post.objects.filter(is_featured=True).order_by('-created_at')
    elif sort == 'trending':
        posts = Post.objects.all().order_by('-views_count', '-created_at')
    else:
        posts = Post.objects.all().order_by('-created_at')

    paginator = Paginator(posts, 12)
    page_obj = paginator.get_page(page)
    categories = Category.objects.all()

    context = {
        'page_obj': page_obj,
        'sort': sort,
        'categories': categories,
        'live_scores': get_live_scores(),
    }
    return render(request, 'all_posts.html', context)


# =========================================================
# NEWSLETTER (AJAX)
# =========================================================

@require_POST
@csrf_exempt
def subscribe_newsletter(request):
    import json
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'message': 'Du lieu khong hop le.'})

    if not email or '@' not in email:
        return JsonResponse({'ok': False, 'message': 'Email khong hop le.'})

    Subscriber.objects.get_or_create(email=email)
    return JsonResponse({'ok': True, 'message': 'Dang ky thanh cong! Cam on ban.'})


# =========================================================
# CHAT API (Web)
# =========================================================

@csrf_exempt
def chat_api(request):
    global LAST_CHAT_API_TIME

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not client:
        return JsonResponse({"error": "AI chua duoc cau hinh"}, status=503)

    try:
        data = json.loads(request.body)
        user_text = data.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Du lieu khong hop le"}, status=400)

    if not user_text:
        return JsonResponse({"error": "Vui long nhap cau hoi"}, status=400)

    current_time = time.time()
    if current_time - LAST_CHAT_API_TIME < 3:
        return JsonResponse({"error": "Vui long cho 3 giay giua cac cau hoi"}, status=429)
    LAST_CHAT_API_TIME = current_time

    reply = chat_with_ai(user_text)
    return JsonResponse({"reply": reply})





# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@csrf_exempt
def telegram_ai_webhook(request):
    global LAST_REQUEST_TIME
    global PROCESSED_UPDATES

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    try:
        update = json.loads(request.body.decode('utf-8'))
        print("TELEGRAM UPDATE:", update)

        update_id = update.get("update_id")
        if update_id in PROCESSED_UPDATES:
            return HttpResponse("OK", status=200)
        PROCESSED_UPDATES.add(update_id)
        if len(PROCESSED_UPDATES) > 100:
            PROCESSED_UPDATES.clear()

        if "message" not in update:
            return HttpResponse("OK", status=200)

        message = update["message"]
        chat_id = str(message["chat"]["id"])

        if YOUR_TELEGRAM_CHAT_ID:
            if chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
                return HttpResponse("Unauthorized", status=403)

        if not client:
            print("Groq client loi hoac chua cau hinh bien moi truong")
            return HttpResponse("OK", status=200)

        current_time = time.time()
        if current_time - LAST_REQUEST_TIME < 5:
            print("SPAM REQUEST BLOCKED")
            return HttpResponse("OK", status=200)
        LAST_REQUEST_TIME = current_time

        # =====================================================
        # NHANH 1: ANH + CAPTION -> TU DONG DANG BAI VIET
        # =====================================================
        if "photo" in message and "caption" in message:
            keywords = message["caption"]
            photo_file = message["photo"][-1]
            file_id = photo_file["file_id"]

            send_telegram_message(chat_id, "Dang phan tich anh va tien hanh sang tac bai viet...")

            file_info_res = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}",
                timeout=REQUEST_TIMEOUT
            ).json()

            if not file_info_res.get("ok"):
                send_telegram_message(chat_id, "Loi: Khong the tai tep hinh anh tu may chu Telegram.")
                return HttpResponse("OK", status=200)

            file_path = file_info_res["result"]["file_path"]

            image_data = requests.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                timeout=REQUEST_TIMEOUT
            ).content

            image_base64 = base64.b64encode(image_data).decode('utf-8')

            all_cats = Category.objects.all()
            cat_list = ", ".join([f'"{c.slug}"' for c in all_cats]) if all_cats else '"khac"'

            result = generate_article(image_base64, keywords, cat_list)

            if not result:
                send_telegram_message(chat_id, "Truc trac ky thuat: He thong phan tich Vision khong phan hoi.")
                return HttpResponse("OK", status=200)

            ai_title, ai_content, ai_category_slug = result

            try:
                unique_img_id = f"tele_{file_id[:8]}_{int(time.time() * 1000)}"

                upload_result = cloudinary.uploader.upload(
                    image_data,
                    public_id=unique_img_id,
                    folder="news"
                )

                new_post = Post(
                    title=ai_title,
                    content=ai_content,
                    image_file=upload_result.get("public_id")
                )
                new_post.save()

                if ai_category_slug:
                    try:
                        cat = Category.objects.get(slug=ai_category_slug)
                        new_post.categories.add(cat)
                    except Category.DoesNotExist:
                        pass

                send_telegram_message(
                    chat_id,
                    f"DA TU DONG DANG BAI THANH CONG!\n\nTieu de: {ai_title}\n\nLink doc bai viet: {WEB_URL}"
                )

            except Exception as save_error:
                print("SAVE ERROR:", save_error)
                send_telegram_message(chat_id, f"Loi ghi bai viet: {str(save_error)[:100]}")

        # =====================================================
        # NHANH 2: TEXT -> CHATBOT HOI DAP + WEB SEARCH
        # =====================================================
        elif "text" in message:
            user_text = message["text"]

            if user_text.strip() == "/start":
                send_telegram_message(
                    chat_id,
                    "Bot Tro Ly The Thao Da Nang San Sang!\n\n"
                    "[Gui anh kem Caption] -> He thong tu dong dung Llama 4 phan tich, viet bai bao chuan SEO dai 300 tu va dang truc tiep len website cua ban.\n\n"
                    "[Gui tin nhan van ban] -> Hoi dap chuyen sau, tra cuu ty so, chuyen nhuong the thao moi nhat."
                )
                return HttpResponse("OK", status=200)

            send_telegram_message(chat_id, "Dang tra loi...")
            bot_reply_text = chat_with_ai(user_text)
            send_telegram_message(chat_id, bot_reply_text)

        return HttpResponse("OK", status=200)

    except Exception as e:
        print("CRITICAL WEBHOOK ERROR:", e)
        return HttpResponse("OK", status=200)
