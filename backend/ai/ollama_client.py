from __future__ import annotations
"""
Ollama chat client used by AuralMind AI routes.

Request routing behavior:
1. If `AURALMIND_AI_BASE_URL` is set, call that endpoint only.
2. Otherwise call Ollama Cloud first (default: https://ollama.com/api).
3. On eligible failures, fall back to local Ollama (default: http://localhost:11434/api).

Endpoint construction follows Ollama's official chat API shape (`POST /api/chat`):
https://docs.ollama.com/api/chat
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

import requests


_DEFAULT_CLOUD_BASE_URL = "https://ollama.com"
_DEFAULT_LOCAL_BASE_URL = "http://localhost:11434"
_DEFAULT_CLOUD_MODEL = "glm-5:cloud"

logger = logging.getLogger("auralmind.ai.ollama_client")


@dataclass(frozen=True)
class _EndpointCandidate:
    name: str
    base_url: str
    model: str


class _EndpointAttemptError(RuntimeError):
    def __init__(
        self,
        *,
        candidate: _EndpointCandidate,
        category: str,
        detail: str,
        status_code: int | None = None,
        allow_fallback: bool = False,
    ) -> None:
        super().__init__(detail)
        self.candidate = candidate
        self.category = category
        self.detail = detail
        self.status_code = status_code
        self.allow_fallback = allow_fallback


def _clean_env(value: str | None) -> str:
    return str(value or "").strip().strip("'\"")


def _normalize_api_base(raw_base: str) -> str:
    cleaned = _clean_env(raw_base).rstrip("/")
    if not cleaned:
        return ""

    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        if cleaned.lower().endswith("/api"):
            return cleaned
        return f"{cleaned}/api"

    path = (parsed.path or "").rstrip("/")
    if path.lower().endswith("/api"):
        normalized_path = path or "/api"
    elif path:
        normalized_path = f"{path}/api"
    else:
        normalized_path = "/api"

    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _get_timeout() -> float:
    raw = _clean_env(os.getenv("AURALMIND_AI_TIMEOUT_SEC"))
    if not raw:
        return 30.0
    try:
        return float(raw)
    except ValueError:
        return 30.0


def _get_keep_alive() -> int | str | None:
    raw = _clean_env(os.getenv("AURALMIND_AI_KEEP_ALIVE"))
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def _get_cloud_model() -> str:
    return _clean_env(os.getenv("AURALMIND_AI_MODEL")) or _DEFAULT_CLOUD_MODEL


def _get_local_model() -> str:
    local_model = _clean_env(os.getenv("AURALMIND_AI_MODEL_LOCAL"))
    if local_model:
        return local_model
    return _get_cloud_model()


def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    api_key = _clean_env(os.getenv("OLLAMA_API_KEY"))
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat"


def _build_endpoint_candidates() -> list[_EndpointCandidate]:
    override = _clean_env(os.getenv("AURALMIND_AI_BASE_URL"))
    if override:
        return [
            _EndpointCandidate(
                name="override",
                base_url=_normalize_api_base(override),
                model=_get_cloud_model(),
            )
        ]

    cloud_base = _clean_env(os.getenv("OLLAMA_BASE_URL_CLOUD")) or _DEFAULT_CLOUD_BASE_URL
    local_base = _clean_env(os.getenv("OLLAMA_BASE_URL_LOCAL")) or _DEFAULT_LOCAL_BASE_URL

    cloud_candidate = _EndpointCandidate(
        name="cloud",
        base_url=_normalize_api_base(cloud_base),
        model=_get_cloud_model(),
    )
    local_candidate = _EndpointCandidate(
        name="local",
        base_url=_normalize_api_base(local_base),
        model=_get_local_model(),
    )

    candidates = [cloud_candidate]
    # Avoid duplicate retries if cloud and local endpoints resolve to the same API base.
    if local_candidate.base_url != cloud_candidate.base_url:
        candidates.append(local_candidate)
    return candidates


def _is_fallback_http_status(status_code: int) -> bool:
    return status_code in {401, 403, 429} or status_code >= 500


def _response_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("detail") or payload.get("message")
            if detail:
                return str(detail)
    except ValueError:
        pass
    return (response.text or "").strip() or f"HTTP {response.status_code}"


def _post_ollama_once(candidate: _EndpointCandidate, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = _chat_url(candidate.base_url)
    try:
        response = requests.post(
            url,
            json=payload,
            headers=_build_headers(),
            timeout=_get_timeout(),
        )
    except requests.Timeout as exc:
        raise _EndpointAttemptError(
            candidate=candidate,
            category="timeout",
            detail=str(exc),
            allow_fallback=True,
        ) from exc
    except requests.ConnectionError as exc:
        raise _EndpointAttemptError(
            candidate=candidate,
            category="connection_error",
            detail=str(exc),
            allow_fallback=True,
        ) from exc
    except requests.RequestException as exc:
        raise _EndpointAttemptError(
            candidate=candidate,
            category="request_error",
            detail=str(exc),
            allow_fallback=True,
        ) from exc

    if response.status_code >= 400:
        detail = _response_error_detail(response)
        if candidate.name == "local" and response.status_code == 404 and "model" in detail.lower():
            detail = (
                f"{detail}. Local fallback model may be unavailable; set "
                "AURALMIND_AI_MODEL_LOCAL to a locally installed model."
            )
        raise _EndpointAttemptError(
            candidate=candidate,
            category="http_error",
            status_code=response.status_code,
            detail=detail,
            allow_fallback=_is_fallback_http_status(response.status_code),
        )

    try:
        return response.json()
    except ValueError as exc:
        raise _EndpointAttemptError(
            candidate=candidate,
            category="invalid_json",
            detail="Ollama response was not valid JSON",
            allow_fallback=False,
        ) from exc


def _format_attempt_failures(attempts: list[_EndpointAttemptError]) -> str:
    if not attempts:
        return "Ollama request failed before any endpoint attempt was made."
    parts = []
    for failure in attempts:
        endpoint = _chat_url(failure.candidate.base_url)
        status = f" status={failure.status_code}" if failure.status_code is not None else ""
        parts.append(
            f"{failure.candidate.name} endpoint={endpoint}{status} "
            f"category={failure.category} detail={failure.detail}"
        )
    return "Ollama request failed across configured endpoints: " + " | ".join(parts)


def _post_ollama(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidates = _build_endpoint_candidates()
    failures: list[_EndpointAttemptError] = []
    for index, candidate in enumerate(candidates):
        candidate_payload = dict(payload)
        candidate_payload["model"] = candidate.model
        try:
            response = _post_ollama_once(candidate, candidate_payload)
            if candidate.name == "local" and index > 0:
                logger.info(
                    "Ollama local fallback succeeded endpoint=%s model=%s",
                    candidate.base_url,
                    candidate.model,
                )
            return response
        except _EndpointAttemptError as exc:
            failures.append(exc)
            has_next = index < len(candidates) - 1

            if candidate.name == "cloud":
                logger.warning(
                    "Ollama cloud request failed status=%s category=%s detail=%s",
                    exc.status_code,
                    exc.category,
                    exc.detail,
                )

            if has_next and exc.allow_fallback:
                next_candidate = candidates[index + 1]
                logger.info(
                    "Attempting Ollama fallback from %s to %s endpoint=%s",
                    candidate.name,
                    next_candidate.name,
                    next_candidate.base_url,
                )
                continue
            break

    raise RuntimeError(_format_attempt_failures(failures))


def _extract_json(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(payload[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise RuntimeError("Ollama response content was not valid JSON")


def _call_ollama(
    messages: list[Dict[str, Any]],
    *,
    tools: Optional[list[Dict[str, Any]]] = None,
    response_format: Optional[Any] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "stream": False,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    if response_format is not None:
        payload["format"] = response_format
    keep_alive = _get_keep_alive()
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    return _post_ollama(payload)


def _normalize_tool_args(args: Any) -> Dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _run_tool_call(
    call: Dict[str, Any],
    tool_executor: Dict[str, Callable[..., Any]],
) -> tuple[str, Dict[str, Any]]:
    function = call.get("function") or {}
    name = function.get("name") or "unknown_tool"
    args = _normalize_tool_args(function.get("arguments"))
    fn = tool_executor.get(name)
    if not fn:
        return name, {"error": f"Unknown tool: {name}"}
    try:
        result = fn(**args)
    except Exception as exc:
        return name, {"error": str(exc)}
    if isinstance(result, dict):
        return name, result
    return name, {"result": result}


async def _ollama_chat_loop(
    *,
    system: str,
    user: str,
    tools: Optional[list[Dict[str, Any]]] = None,
    tool_executor: Optional[Dict[str, Callable[..., Any]]] = None,
    response_format: Optional[Any] = None,
    max_steps: int = 4,
) -> Dict[str, Any]:
    messages: list[Dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for _ in range(max_steps):
        data = await asyncio.to_thread(
            _call_ollama,
            messages,
            tools=tools,
            response_format=response_format,
        )
        message = data.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            if not tool_executor:
                raise RuntimeError("Tool calls requested but no tool executor provided")
            messages.append(message)
            for call in tool_calls:
                name, result = _run_tool_call(call, tool_executor)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(result),
                    }
                )
            continue
        return message
    raise RuntimeError("Tool loop exceeded max steps")


async def ollama_chat_json(
    system: str,
    user: str,
    schema: Dict[str, Any],
    *,
    tools: Optional[list[Dict[str, Any]]] = None,
    tool_executor: Optional[Dict[str, Callable[..., Any]]] = None,
    max_steps: int = 4,
) -> Dict[str, Any]:
    message = await _ollama_chat_loop(
        system=system,
        user=user,
        tools=tools,
        tool_executor=tool_executor,
        response_format=schema,
        max_steps=max_steps,
    )
    content = message.get("content", "")
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama response content was empty")
    return _extract_json(content)


async def preload_model() -> None:
    keep_alive = _get_keep_alive()
    payload: Dict[str, Any] = {
        "stream": False,
        "messages": [{"role": "user", "content": "warmup"}],
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    await asyncio.to_thread(_post_ollama, payload)


async def ollama_chat_text(
    system: str,
    user: str,
    *,
    tools: Optional[list[Dict[str, Any]]] = None,
    tool_executor: Optional[Dict[str, Callable[..., Any]]] = None,
    max_steps: int = 4,
) -> str:
    message = await _ollama_chat_loop(
        system=system,
        user=user,
        tools=tools,
        tool_executor=tool_executor,
        response_format='json',
        max_steps=max_steps,
    )
    content = message.get("content", "")
    if not isinstance(content, str):
        return json.dumps(content)
    return content
