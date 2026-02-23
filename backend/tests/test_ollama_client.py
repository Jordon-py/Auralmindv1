"""Unit tests for Ollama endpoint normalization and cloud-to-local fallback rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests

_MODULE_PATH = Path(__file__).resolve().parents[1] / "ai" / "ollama_client.py"
_MODULE_SPEC = importlib.util.spec_from_file_location("backend_ai_ollama_client", _MODULE_PATH)
if _MODULE_SPEC is None or _MODULE_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Unable to load module spec from {_MODULE_PATH}")
ollama_client = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = ollama_client
_MODULE_SPEC.loader.exec_module(ollama_client)


def _mock_response(status_code: int, *, payload: dict | None = None, text: str = "") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.text = text
    if payload is None:
        response.json.side_effect = ValueError("not-json")
    else:
        response.json.return_value = payload
    return response


class OllamaClientFallbackTests(unittest.TestCase):
    def _env(self, **overrides: str) -> dict[str, str]:
        env = {
            "AURALMIND_AI_BASE_URL": "",
            "OLLAMA_BASE_URL_CLOUD": "https://ollama.com",
            "OLLAMA_BASE_URL_LOCAL": "http://localhost:11434",
            "AURALMIND_AI_MODEL": "glm-5:cloud",
            "AURALMIND_AI_MODEL_LOCAL": "",
            "AURALMIND_AI_TIMEOUT_SEC": "5",
            "OLLAMA_API_KEY": "",
        }
        env.update(overrides)
        return env

    def test_normalizes_host_to_api_chat(self) -> None:
        with patch.dict("os.environ", self._env(OLLAMA_BASE_URL_CLOUD="https://ollama.com"), clear=False), patch.object(
            ollama_client.requests,
            "post",
            return_value=_mock_response(200, payload={"message": {"content": "ok"}}),
        ) as post_mock:
            out = ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(out["message"]["content"], "ok")
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(post_mock.call_args_list[0].args[0], "https://ollama.com/api/chat")

    def test_cloud_timeout_falls_back_local(self) -> None:
        with patch.dict("os.environ", self._env(), clear=False), patch.object(
            ollama_client.requests,
            "post",
            side_effect=[
                requests.Timeout("cloud timeout"),
                _mock_response(200, payload={"message": {"content": "local-ok"}}),
            ],
        ) as post_mock:
            out = ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(out["message"]["content"], "local-ok")
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(post_mock.call_args_list[0].args[0], "https://ollama.com/api/chat")
        self.assertEqual(post_mock.call_args_list[1].args[0], "http://localhost:11434/api/chat")

    def test_cloud_429_falls_back_local(self) -> None:
        with patch.dict("os.environ", self._env(), clear=False), patch.object(
            ollama_client.requests,
            "post",
            side_effect=[
                _mock_response(429, payload={"error": "rate_limited"}),
                _mock_response(200, payload={"message": {"content": "local-ok"}}),
            ],
        ) as post_mock:
            out = ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(out["message"]["content"], "local-ok")
        self.assertEqual(post_mock.call_count, 2)

    def test_cloud_401_falls_back_local(self) -> None:
        with patch.dict("os.environ", self._env(), clear=False), patch.object(
            ollama_client.requests,
            "post",
            side_effect=[
                _mock_response(401, payload={"error": "unauthorized"}),
                _mock_response(200, payload={"message": {"content": "local-ok"}}),
            ],
        ) as post_mock:
            out = ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(out["message"]["content"], "local-ok")
        self.assertEqual(post_mock.call_count, 2)

    def test_cloud_400_does_not_fallback(self) -> None:
        with patch.dict("os.environ", self._env(), clear=False), patch.object(
            ollama_client.requests,
            "post",
            return_value=_mock_response(400, payload={"error": "invalid request"}),
        ) as post_mock:
            with self.assertRaises(RuntimeError) as ctx:
                ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertIn("status=400", str(ctx.exception))
        self.assertEqual(post_mock.call_count, 1)

    def test_override_base_url_bypasses_fallback(self) -> None:
        with patch.dict(
            "os.environ",
            self._env(AURALMIND_AI_BASE_URL="https://custom-ollama.example.com"),
            clear=False,
        ), patch.object(
            ollama_client.requests,
            "post",
            return_value=_mock_response(500, payload={"error": "server error"}),
        ) as post_mock:
            with self.assertRaises(RuntimeError):
                ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(post_mock.call_args_list[0].args[0], "https://custom-ollama.example.com/api/chat")

    def test_local_model_env_used_on_fallback(self) -> None:
        with patch.dict(
            "os.environ",
            self._env(AURALMIND_AI_MODEL="glm-5:cloud", AURALMIND_AI_MODEL_LOCAL="llama3.2"),
            clear=False,
        ), patch.object(
            ollama_client.requests,
            "post",
            side_effect=[
                _mock_response(503, payload={"error": "upstream unavailable"}),
                _mock_response(200, payload={"message": {"content": "local-ok"}}),
            ],
        ) as post_mock:
            out = ollama_client._call_ollama([{"role": "user", "content": "ping"}])

        self.assertEqual(out["message"]["content"], "local-ok")
        self.assertEqual(post_mock.call_args_list[0].kwargs["json"]["model"], "glm-5:cloud")
        self.assertEqual(post_mock.call_args_list[1].kwargs["json"]["model"], "llama3.2")


if __name__ == "__main__":
    unittest.main()
