import json
from unittest.mock import patch

from django.test import TestCase

from . import ai_services, views
from .models import TelegramUpdate


class TelegramWebhookTests(TestCase):
    def _payload(self, update_id, text="hello"):
        return {
            "update_id": update_id,
            "message": {
                "message_id": 1,
                "chat": {"id": 123456},
                "text": text,
            },
        }

    def test_duplicate_update_is_ignored(self):
        with patch("news.views.AI_AVAILABLE", True), \
                patch("news.views.YOUR_TELEGRAM_CHAT_ID", ""), \
                patch("news.views.WEBHOOK_SEMAPHORE") as semaphore, \
                patch("news.views.threading.Thread") as thread_class:
            semaphore.acquire.return_value = True
            thread_class.return_value.start.return_value = None

            first = self.client.post(
                "/telegram-webhook/",
                data=json.dumps(self._payload(9001)),
                content_type="application/json",
            )
            second = self.client.post(
                "/telegram-webhook/",
                data=json.dumps(self._payload(9001)),
                content_type="application/json",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(TelegramUpdate.objects.count(), 1)
        self.assertEqual(thread_class.call_count, 1)


class TelegramProcessingTests(TestCase):
    def test_ai_failure_sends_one_error_message(self):
        with patch("news.views.send_telegram_typing"), \
                patch("news.views.send_telegram_message") as send_message, \
                patch("news.views.chat_with_ai", return_value=None):
            views._process_telegram_update(
                {"message": {"text": "hello"}},
                "123456",
                9002,
            )

        send_message.assert_called_once()
        self.assertIn("bận", send_message.call_args[0][1])
        self.assertNotEqual(send_message.call_args[0][1], "Đang trả lời...")

    def test_typing_action_is_used_instead_of_progress_spam(self):
        with patch("news.views.send_telegram_typing") as typing, \
                patch("news.views.send_telegram_message"), \
                patch("news.views.chat_with_ai", return_value="Xin chào"):
            views._process_telegram_update(
                {"message": {"text": "hello"}},
                "123456",
                9003,
            )

        typing.assert_called_once_with("123456")


class AiServiceTests(TestCase):
    def test_greeting_skips_web_search(self):
        with patch.object(ai_services, "AI_AVAILABLE", True), \
                patch.object(ai_services, "do_web_search") as search, \
                patch.object(ai_services, "_chat_chain", return_value="Chào bạn"):
            reply = ai_services.chat_with_ai("hi")

        self.assertEqual(reply, "Chào bạn")
        search.assert_not_called()

    def test_news_query_needs_search(self):
        self.assertTrue(ai_services._should_search("tin bóng đá hôm nay"))
        self.assertFalse(ai_services._should_search("hi"))

    def test_parse_json_handles_markdown_fence(self):
        self.assertEqual(
            ai_services._parse_json('```json\n{"title": "A"}\n```'),
            {"title": "A"},
        )
