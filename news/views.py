import json
import time
import base64
import datetime
import random
import threading
from datetime import timedelta

import requests

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.db import IntegrityError, close_old_connections
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST

import cloudinary.uploader
from .models import Post, Category, Comment, Subscriber, LiveScore, TelegramUpdate
from .ai_services import (
    AI_AVAILABLE, chat_with_ai, send_telegram_message, send_telegram_typing,
    generate_article, fetch_and_parse_scores,
    REQUEST_TIMEOUT, TELEGRAM_BOT_TOKEN, YOUR_TELEGRAM_CHAT_ID, WEB_URL
)

LAST_CHAT_API_TIME = 0
CHAT_API_LOCK = threading.Lock()

WEBHOOK_SEMAPHORE = threading.Semaphore(4)
CHAT_LOCKS = {}
CHAT_LOCKS_LOCK = threading.Lock()
UPDATE_CLEANUP_COUNTER = 0
UPDATE_CLEANUP_LOCK = threading.Lock()

# =========================================================
# HELPERS
# =========================================================

def get_live_scores():
    scores_db = LiveScore.objects.filter(match_date=datetime.date.today())
    if scores_db.exists():
        return [{"display": f"{s.team_a} {s.score_a} - {s.score_b} {s.team_b}", "status": s.status} for s in scores_db]

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

    if not AI_AVAILABLE:
        return JsonResponse({"error": "AI chưa được cấu hình"}, status=503)

    try:
        data = json.loads(request.body)
        user_text = data.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Du lieu khong hop le"}, status=400)

    if not user_text:
        return JsonResponse({"error": "Vui long nhap cau hoi"}, status=400)

    with CHAT_API_LOCK:
        current_time = time.time()
        if current_time - LAST_CHAT_API_TIME < 3:
            return JsonResponse({"error": "Vui long cho 3 giay giua cac cau hoi"}, status=429)
        LAST_CHAT_API_TIME = current_time

    reply = chat_with_ai(user_text)
    return JsonResponse({"reply": reply})





# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

def _cleanup_old_updates():
    global UPDATE_CLEANUP_COUNTER

    with UPDATE_CLEANUP_LOCK:
        UPDATE_CLEANUP_COUNTER += 1
        should_cleanup = UPDATE_CLEANUP_COUNTER % 100 == 0

    if not should_cleanup:
        return
    try:
        cutoff = timezone.now() - timedelta(days=7)
        TelegramUpdate.objects.filter(created_at__lt=cutoff).delete()
    except Exception as exc:
        print(f"UPDATE CLEANUP ERROR: {exc}")


def _get_chat_lock(chat_id):
    with CHAT_LOCKS_LOCK:
        lock = CHAT_LOCKS.get(chat_id)
        if lock is None:
            lock = threading.Lock()
            CHAT_LOCKS[chat_id] = lock
        return lock


def _mark_update(update_id, status):
    try:
        TelegramUpdate.objects.filter(update_id=update_id).update(status=status)
    except Exception as exc:
        print(f"UPDATE STATUS ERROR: {exc}")


def _process_telegram_update(update, chat_id, update_id):
    message = update.get("message") or {}

    if "photo" in message and "caption" in message:
        send_telegram_typing(chat_id)
        keywords = message["caption"]
        photo_file = message["photo"][-1]
        file_id = photo_file["file_id"]

        file_info_res = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}",
            timeout=REQUEST_TIMEOUT,
        ).json()

        if not file_info_res.get("ok"):
            send_telegram_message(
                chat_id,
                "Lỗi: Không thể tải tệp hình ảnh từ máy chủ Telegram.",
            )
            return

        file_path = file_info_res["result"]["file_path"]
        image_response = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
            timeout=REQUEST_TIMEOUT,
        )
        if image_response.status_code != 200:
            send_telegram_message(chat_id, "Lỗi: Không tải được hình ảnh.")
            return

        image_data = image_response.content
        if len(image_data) > 20 * 1024 * 1024:
            send_telegram_message(chat_id, "Ảnh quá lớn, vui lòng gửi ảnh nhỏ hơn 20MB.")
            return

        image_base64 = base64.b64encode(image_data).decode("utf-8")
        all_cats = Category.objects.all()
        cat_list = (
            ", ".join([f'"{category.slug}"' for category in all_cats])
            if all_cats else '"khac"'
        )

        result = generate_article(image_base64, keywords, cat_list)
        if not result:
            send_telegram_message(
                chat_id,
                "Trục trặc kỹ thuật: Hệ thống phân tích Vision không phản hồi.",
            )
            return

        ai_title, ai_content, ai_category_slug = result
        try:
            unique_img_id = f"tele_{update_id}_{file_id[:8]}"
            upload_result = cloudinary.uploader.upload(
                image_data,
                public_id=unique_img_id,
                folder="news",
            )
            new_post = Post(
                title=ai_title,
                content=ai_content,
                image_file=upload_result.get("public_id"),
            )
            new_post.save()

            if ai_category_slug:
                try:
                    category = Category.objects.get(slug=ai_category_slug)
                    new_post.categories.add(category)
                except Category.DoesNotExist:
                    pass

            send_telegram_message(
                chat_id,
                (
                    "ĐÃ TỰ ĐỘNG ĐĂNG BÀI THÀNH CÔNG!\n\n"
                    f"Tiêu đề: {ai_title}\n\n"
                    f"Link đọc bài viết: {WEB_URL}"
                ),
            )
        except Exception as save_error:
            print(f"SAVE ERROR: {save_error}")
            send_telegram_message(
                chat_id,
                f"Lỗi ghi bài viết: {str(save_error)[:100]}",
            )
        return

    if "text" not in message:
        return

    user_text = message["text"]

    if user_text.strip() == "/start":
        send_telegram_message(
            chat_id,
            "Bot Trợ Lý Thể Thao Đã Sẵn Sàng!\n\n"
            "[Gửi ảnh kèm Caption] -> AI phân tích ảnh, viết bài chuẩn SEO dài 300 từ và đăng trực tiếp lên website.\n\n"
            "[Gửi tin nhắn văn bản] -> Hỏi đáp chuyên sâu, tra cứu tỷ số, chuyển nhượng thể thao mới nhất.\n\n"
            "[Gửi '/livescore' hoặc 'cập nhật tỷ số'] -> Tự động tìm kiếm và cập nhật tỷ số bóng đá hôm nay.",
        )
        return

    text_lower = user_text.strip().lower()
    if text_lower in (
        "/livescore", "/score", "cập nhật tỷ số", "cap nhat ty so",
        "update score", "tỷ số", "ty so",
    ):
        send_telegram_typing(chat_id)
        scores = fetch_and_parse_scores()
        if scores:
            LiveScore.objects.all().delete()
            valid_scores = []
            for score in scores:
                try:
                    item = {
                        "team_a": str(score.get("team_a", "??")),
                        "team_b": str(score.get("team_b", "??")),
                        "score_a": int(score.get("score_a", 0) or 0),
                        "score_b": int(score.get("score_b", 0) or 0),
                        "status": str(score.get("status", "ft")),
                    }
                except (TypeError, ValueError):
                    continue
                LiveScore.objects.create(
                    team_a=item["team_a"],
                    team_b=item["team_b"],
                    score_a=item["score_a"],
                    score_b=item["score_b"],
                    status=item["status"],
                    match_date=datetime.date.today(),
                )
                valid_scores.append(item)

            if valid_scores:
                reply = "ĐÃ CẬP NHẬT TỶ SỐ!\n\n"
                for score in valid_scores:
                    icon = {
                        "live": "🔴", "ht": "🟡", "ft": "⚪",
                    }.get(score["status"], "⚪")
                    reply += (
                        f"{icon} {score['team_a']} "
                        f"{score['score_a']}-{score['score_b']} "
                        f"{score['team_b']}\n"
                    )
                send_telegram_message(chat_id, reply)
            else:
                send_telegram_message(chat_id, "Không tìm thấy tỷ số nào. Thử lại sau.")
        else:
            send_telegram_message(chat_id, "Không tìm thấy tỷ số nào. Thử lại sau.")
        return

    send_telegram_typing(chat_id)
    bot_reply_text = chat_with_ai(user_text)
    if not bot_reply_text:
        bot_reply_text = "Trợ lý AI hiện đang bận. Vui lòng thử lại sau."
    send_telegram_message(chat_id, bot_reply_text)


def _run_background_update(update, chat_id, update_id):
    close_old_connections()
    chat_lock = _get_chat_lock(chat_id)

    if not chat_lock.acquire(blocking=False):
        send_telegram_message(
            chat_id,
            "Mình đang xử lý tin trước, vui lòng đợi một chút.",
        )
        _mark_update(update_id, "skipped")
        WEBHOOK_SEMAPHORE.release()
        close_old_connections()
        return

    try:
        _process_telegram_update(update, chat_id, update_id)
        _mark_update(update_id, "done")
    except Exception as exc:
        print(f"BACKGROUND UPDATE ERROR: {exc}")
        _mark_update(update_id, "failed")
        send_telegram_message(
            chat_id,
            "Có lỗi xảy ra khi xử lý tin nhắn. Vui lòng thử lại sau.",
        )
    finally:
        chat_lock.release()
        WEBHOOK_SEMAPHORE.release()
        close_old_connections()


@csrf_exempt
def telegram_ai_webhook(request):
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    try:
        update = json.loads(request.body.decode("utf-8"))
        print("TELEGRAM UPDATE:", update)

        if "message" not in update:
            return HttpResponse("OK", status=200)

        message = update["message"]
        chat_id = str(message["chat"]["id"])
        update_id = update.get("update_id")

        if update_id is None:
            return HttpResponse("OK", status=200)
        try:
            update_id = int(update_id)
        except (TypeError, ValueError):
            return HttpResponse("OK", status=200)

        if YOUR_TELEGRAM_CHAT_ID and chat_id != str(YOUR_TELEGRAM_CHAT_ID).strip():
            print(f"UNAUTHORIZED CHAT: {chat_id}")
            return HttpResponse("OK", status=200)

        try:
            _, created = TelegramUpdate.objects.get_or_create(
                update_id=update_id,
                defaults={"chat_id": chat_id, "status": "processing"},
            )
        except IntegrityError:
            return HttpResponse("OK", status=200)

        if not created:
            print(f"DUPLICATE UPDATE IGNORED: {update_id}")
            return HttpResponse("OK", status=200)

        _cleanup_old_updates()

        if not AI_AVAILABLE:
            print("AI chua duoc cau hinh")
            send_telegram_message(chat_id, "AI chưa được cấu hình trên máy chủ.")
            _mark_update(update_id, "no_ai")
            return HttpResponse("OK", status=200)

        if not WEBHOOK_SEMAPHORE.acquire(blocking=False):
            send_telegram_message(
                chat_id,
                "Hệ thống đang bận xử lý nhiều yêu cầu. Vui lòng thử lại sau.",
            )
            _mark_update(update_id, "rejected")
            return HttpResponse("OK", status=200)

        try:
            thread = threading.Thread(
                target=_run_background_update,
                args=(update, chat_id, update_id),
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            WEBHOOK_SEMAPHORE.release()
            _mark_update(update_id, "failed")
            print(f"THREAD START ERROR: {exc}")

        return HttpResponse("OK", status=200)

    except Exception as exc:
        print("CRITICAL WEBHOOK ERROR:", exc)
        return HttpResponse("OK", status=200)
