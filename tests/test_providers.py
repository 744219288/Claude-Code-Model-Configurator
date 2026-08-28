import json
import unittest
from unittest import mock

from installer import core
from installer.credentials import provider_api_key_target
from installer.providers import DEFAULT_PROVIDER_ID, PROVIDERS, get_model


class ProviderRegistryTests(unittest.TestCase):
    def test_every_provider_has_a_valid_default_and_unique_models(self):
        self.assertGreaterEqual(len(PROVIDERS), 4)
        for provider_id, provider in PROVIDERS.items():
            self.assertEqual(provider_id, provider.id)
            model_ids = [model.id for model in provider.models]
            self.assertEqual(len(model_ids), len(set(model_ids)))
            self.assertIn(provider.default_model, model_ids)
            self.assertTrue(provider.base_url.startswith("https://"))

    def test_credentials_are_isolated_but_deepseek_keeps_legacy_target(self):
        self.assertEqual(
            provider_api_key_target(DEFAULT_PROVIDER_ID),
            "ClaudeDeepSeekConfigurator/DeepSeekApiKey",
        )
        zhipu = provider_api_key_target("zhipu")
        minimax = provider_api_key_target("minimax")
        self.assertNotEqual(zhipu, minimax)
        self.assertIn("/zhipu/", zhipu)
        self.assertIn("/minimax/", minimax)

    def test_gateway_environment_uses_selected_provider_only(self):
        values = core.gateway_environment("glm-5.2[1m]", "glm-secret", "zhipu")
        self.assertEqual(values["ANTHROPIC_BASE_URL"], "https://open.bigmodel.cn/api/anthropic")
        self.assertEqual(values["ANTHROPIC_AUTH_TOKEN"], "glm-secret")
        self.assertEqual(values["ANTHROPIC_MODEL"], "glm-5.2[1m]")
        self.assertEqual(values["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "glm-4.7")
        self.assertEqual(values["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertNotIn("deepseek", " ".join(values.values()).lower())

    def test_cross_provider_model_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持的模型"):
            get_model("minimax", "glm-5.2[1m]")


class ProviderConnectionTests(unittest.TestCase):
    @mock.patch("installer.core.read_api_key", return_value="secret-key")
    @mock.patch("installer.core.urllib.request.urlopen")
    def test_connection_posts_provider_endpoint_and_raw_api_model(self, urlopen, read_key):
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"{}"
        urlopen.return_value.__enter__.return_value = response

        ok, message = core.test_connection("MiniMax-M3[1m]", "minimax")

        self.assertTrue(ok)
        self.assertIn("MiniMax", message)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.minimaxi.com/anthropic/v1/messages")
        self.assertEqual(json.loads(request.data)["model"], "MiniMax-M3")
        self.assertEqual(request.headers["Authorization"], "Bearer secret-key")
        read_key.assert_called_once_with("minimax")


if __name__ == "__main__":
    unittest.main()
