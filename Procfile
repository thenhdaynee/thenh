web: python manage.py migrate --noinput && gunicorn sport_news_web.wsgi:application --worker-class gthread --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
