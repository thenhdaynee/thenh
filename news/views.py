import os
import json
import time
import base64
import requests

from groq import Groq

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST

import cloudinary.uploader
from .models import Post, Category, Comment

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

    search_result = do_web_search(user_text)

    reply = "Tro ly AI hien tai dang ban xu ly du lieu."

    for attempt in range(3):
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Ban la chuyen gia tro ly AI the thao thong minh hang dau. "
                        "Duoi day la du lieu tim kiem tu web, hay tra loi cau hoi cua nguoi dung "
                        "dua tren du lieu do. Luon phan hoi bang tieng Viet ngan gon, di thang vao van de, "
                        "chinh xac va co so lieu chung minh."
                    )
                },
                {
                    "role": "user",
                    "content": f"Du lieu tim kiem tu web:\n{search_result}\n\nCau hoi: {user_text}"
                }
            ]

            response = client.chat.completions.create(
                model=MODEL_CHAT,
                messages=messages,
                max_tokens=1000
            )
            reply = response.choices[0].message.content
            break

        except Exception as chat_error:
            print(f"CHAT API ERROR ATTEMPT {attempt}:", chat_error)
            time.sleep(4)

    return JsonResponse({"reply": reply})


# =========================================================
# CONFIG
# =========================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
REQUEST_TIMEOUT = 25
LAST_REQUEST_TIME = 0
LAST_CHAT_API_TIME = 0
PROCESSED_UPDATES = set()
WEB_URL = "https://thenh-tin-tuc-the-thao.onrender.com/"

MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_CHAT   = "llama-3.3-70b-versatile"

if GROQ_API_KEY:
    print(f"=== GROQ KEY OK: {GROQ_API_KEY[:6]}... ===")
    print(f"=== VISION: {MODEL_VISION} ===")
    print(f"=== CHAT + WEB SEARCH: {MODEL_CHAT} ===")
else:
    print("=== KHONG TIM THAY GROQ_API_KEY ===")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def do_web_search(query):
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.post(url, data={"q": query}, headers=headers, timeout=15)
        res.raise_for_status()

        import re, html as html_mod
        snippets = re.findall(
            r'<a rel="nofollow" class="result__snippet"[^>]*>(.*?)</a>',
            res.text, re.DOTALL
        )[:5]

        if not snippets:
            snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
                res.text, re.DOTALL
            )[:5]

        clean = []
        for s in snippets:
            text = re.sub(r'<[^>]+>', '', s)
            text = html_mod.unescape(text).strip()
            if text:
                clean.append(text)

        if clean:
            return "\n\n".join(clean)
        return "Khong tim thay ket qua tren web."

    except Exception as e:
        print("WEB SEARCH ERROR:", e)
        return "Loi trong qua trinh tim kiem web."


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

            # Lay danh sach category de huong dan AI
            all_cats = Category.objects.all()
            cat_list = ", ".join([f'"{c.slug}"' for c in all_cats]) if all_cats else '"khac"'

            prompt = f"""Ban la mot bien tap vien, nha bao binh luan the thao chuyen nghiep.
Du vao hinh anh duoc cung cap cung tu khoa dinh huong: "{keywords}"
Hay viet mot bai bao the thao tieng Viet chuyen sau, loi cuon (do dai toi thieu 300 tu).

Ban BAT BUOC phai xuat du lieu tra ve theo dung dinh dang cau truc JSON mau sau:
{{
    "title": "Tieu de bai bao hap dan, chuan SEO",
    "content": "Noi dung bai bao phan tich chi tiet sau sac...",
    "category": {cat_list}
}}

Trong do category phai la mot trong cac slug sau: {cat_list}. Hay tu dong xac dinh the loai phu hop nhat dua vao noi dung hinh anh va tu khoa."""

            ai_text = None
            for attempt in range(3):
                try:
                    completion = client.chat.completions.create(
                        model=MODEL_VISION,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{image_base64}"
                                        }
                                    },
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ]
                            }
                        ],
                        max_tokens=2500,
                        response_format={"type": "json_object"}
                    )
                    ai_text = completion.choices[0].message.content
                    print("VISION RESPONSE SUCCESS")
                    break
                except Exception as ai_error:
                    print(f"VISION ERROR attempt {attempt}:", ai_error)
                    time.sleep(4)

            if not ai_text:
                send_telegram_message(chat_id, "Truc trac ky thuat: He thong phan tich Vision khong phan hoi.")
                return HttpResponse("OK", status=200)

            try:
                raw_text = ai_text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]

                ai_data = json.loads(raw_text.strip())
                ai_title = ai_data.get("title") or "Tin tuc the thao noi bat"
                ai_content = ai_data.get("content") or ""
                ai_category_slug = ai_data.get("category", "")

                print("TITLE:", ai_title)
                print("CATEGORY:", ai_category_slug)

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

                # Gan category tu dong
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

            search_result = do_web_search(user_text)
            send_telegram_message(chat_id, "Dang tra loi...")

            bot_reply_text = "Tro ly AI hien tai dang ban xu ly du lieu."

            for attempt in range(3):
                try:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "Ban la chuyen gia tro ly AI the thao thong minh hang dau. "
                                "Duoi day la du lieu tim kiem tu web, hay tra loi cau hoi cua nguoi dung "
                                "dua tren du lieu do. Luon phan hoi bang tieng Viet ngan gon, di thang vao van de, "
                                "chinh xac va co so lieu chung minh."
                            )
                        },
                        {
                            "role": "user",
                            "content": f"Du lieu tim kiem tu web:\n{search_result}\n\nCau hoi: {user_text}"
                        }
                    ]

                    response = client.chat.completions.create(
                        model=MODEL_CHAT,
                        messages=messages,
                        max_tokens=1000
                    )
                    bot_reply_text = response.choices[0].message.content
                    break

                except Exception as chat_error:
                    print(f"CHAT AGENT ERROR ATTEMPT {attempt}:", chat_error)
                    time.sleep(4)

            send_telegram_message(chat_id, bot_reply_text)

        return HttpResponse("OK", status=200)

    except Exception as e:
        print("CRITICAL WEBHOOK ERROR:", e)
        return HttpResponse("OK", status=200)
