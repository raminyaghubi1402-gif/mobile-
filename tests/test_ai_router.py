import json
import unittest
from urllib.error import HTTPError
from types import SimpleNamespace

from lili_trader.ai_router import (
    AIProviderError,
    LiliAIRouter,
    OpenAICompatibleProvider,
    ProviderConfig,
)
from lili_trader.ai_team import LiliAnalysisTeam


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeProvider:
    def __init__(self, name="mock", model="free-test", response="پاسخ آزمایشی"):
        self.name = name
        self.model = model
        self.response = response
        self.calls = 0
        self.error = None
    def complete(self, messages, *, max_tokens):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


class AIRouterTests(unittest.TestCase):
    def test_openai_compatible_provider_reads_key_from_environment(self):
        seen = {}
        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["authorization"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            seen["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({
                "choices": [{"message": {"content": "سلام از مدل"}}]
            })
        config = ProviderConfig(
            name="free-provider", base_url="https://example.test/v1",
            model="test-model", api_key_env="TEST_MODEL_KEY",
        )
        provider = OpenAICompatibleProvider(
            config, opener=opener, environ={"TEST_MODEL_KEY": "secret-value"}
        )
        result = provider.complete(
            [{"role": "user", "content": "سلام"}], max_tokens=100
        )
        self.assertEqual(result, "سلام از مدل")
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(seen["authorization"], "Bearer secret-value")
        self.assertEqual(seen["payload"]["model"], "test-model")
        self.assertEqual(seen["payload"]["max_tokens"], 100)

    def test_provider_error_does_not_leak_secret_or_response_body(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url, 401, "unauthorized", {},
                None,
            )
        provider = OpenAICompatibleProvider(
            ProviderConfig(
                name="secure", base_url="https://example.test/v1",
                model="model", api_key_env="SECRET_KEY",
            ),
            opener=opener, environ={"SECRET_KEY": "do-not-leak"},
        )
        with self.assertRaises(AIProviderError) as caught:
            provider.complete([{"role": "user", "content": "x"}], max_tokens=20)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn("do-not-leak", str(caught.exception))

    def test_router_uses_cache_and_avoids_duplicate_provider_calls(self):
        now = [100.0]
        provider = FakeProvider()
        router = LiliAIRouter([provider], clock=lambda: now[0])
        messages = [{"role": "user", "content": "same prompt"}]
        first = router.generate(messages)
        second = router.generate(messages)
        self.assertEqual(first.content, "پاسخ آزمایشی")
        self.assertTrue(second.cached)
        self.assertEqual(provider.calls, 1)

    def test_router_falls_back_after_provider_rate_limit_and_applies_cooldown(self):
        now = [100.0]
        provider = FakeProvider()
        provider.error = AIProviderError("limited", status_code=429)
        router = LiliAIRouter([provider], clock=lambda: now[0])
        first = router.generate([{"role": "user", "content": "first"}])
        second = router.generate([{"role": "user", "content": "second"}])
        self.assertTrue(first.fallback)
        self.assertTrue(second.fallback)
        self.assertEqual(provider.calls, 1)
        self.assertIn("429", first.errors[0])
        self.assertIn("cooldown", second.errors[0])

    def test_from_env_builds_ordered_provider_list_without_embedding_keys(self):
        env = {
            "LILI_AI_PROVIDERS": json.dumps([
                {
                    "name": "first", "base_url": "https://one.example/v1",
                    "model": "model-a", "api_key_env": "KEY_A",
                },
                {
                    "name": "second", "base_url": "https://two.example/v1",
                    "model": "model-b",
                },
            ]),
            "KEY_A": "secret-not-in-config",
        }
        router = LiliAIRouter.from_env(environ=env)
        self.assertEqual([p.name for p in router.providers], ["first", "second"])
        self.assertNotIn("secret-not-in-config", env["LILI_AI_PROVIDERS"])

    def test_provider_config_rejects_insecure_remote_http_and_embedded_credentials(self):
        for url in ("http://remote.example/v1", "https://user:pass@example.test/v1"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    ProviderConfig(name="bad", base_url=url, model="m").validate()

    def test_user_marked_free_tier_providers_are_tried_first(self):
        from lili_trader.ai_router import OpenAICompatibleProvider
        paid = OpenAICompatibleProvider(ProviderConfig(
            name="paid", base_url="https://paid.example/v1", model="p"
        ), opener=lambda *args, **kwargs: None, environ={})
        free = OpenAICompatibleProvider(ProviderConfig(
            name="free", base_url="https://free.example/v1", model="f",
            free_tier=True,
        ), opener=lambda *args, **kwargs: None, environ={})
        router = LiliAIRouter([paid, free])
        self.assertEqual([provider.name for provider in router.providers], ["free", "paid"])

    def test_router_rejects_duplicate_provider_names_and_invalid_cache_size(self):
        provider = FakeProvider()
        with self.assertRaises(ValueError):
            LiliAIRouter([provider, FakeProvider()])
        with self.assertRaises(ValueError):
            LiliAIRouter([provider], cache_max_items=0)


class AITeamTests(unittest.TestCase):
    def test_three_roles_review_evidence_without_overriding_deterministic_decision(self):
        responses = iter(("تحلیل", "نقد ریسک", "جمع‌بندی"))
        provider = FakeProvider(response="")
        def complete(messages, *, max_tokens):
            provider.calls += 1
            return next(responses)
        provider.complete = complete
        router = LiliAIRouter([provider])
        analysis = SimpleNamespace(
            symbol="BTCIRT", decision="WAIT", report_fa="گزارش قطعی موتور: داده ناکافی"
        )
        review = LiliAnalysisTeam(router).review(
            analysis, source="Nobitex public API",
            observed_at="2026-11-16T12:00:00+00:00",
        )
        self.assertEqual(provider.calls, 3)
        self.assertEqual(review.deterministic_decision, "WAIT")
        self.assertEqual(review.analyst_notes, "تحلیل")
        self.assertEqual(review.critic_notes, "نقد ریسک")
        self.assertEqual(review.final_summary, "جمع‌بندی")
        self.assertFalse(review.fallback_used)
        self.assertIn("تصمیم موتور قطعی: WAIT", review.render_fa())

    def test_no_configured_models_returns_explicit_local_fallback(self):
        analysis = SimpleNamespace(
            symbol="BTCIRT", decision="WAIT", report_fa="گزارش قطعی"
        )
        review = LiliAnalysisTeam(LiliAIRouter()).review(
            analysis, source="offline test", observed_at="now"
        )
        self.assertTrue(review.fallback_used)
        self.assertEqual(review.providers_used, ())
        self.assertIn("موتور قطعی", review.render_fa())


if __name__ == "__main__":
    unittest.main()
