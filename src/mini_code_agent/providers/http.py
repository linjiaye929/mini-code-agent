from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Final

import httpx
from httpx_sse import ServerSentEvent, SSEError, aconnect_sse
from pydantic import JsonValue, TypeAdapter, ValidationError

from mini_code_agent.providers.base import ProviderError, ProviderErrorCode

_MAX_BASE_URL_LENGTH: Final = 2_048
_MAX_TIMEOUT_SECONDS: Final = 600.0
_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_MAX_REQUEST_ID_LENGTH: Final = 128
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])

type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ProviderSseConnection:
    events: AsyncIterator[ServerSentEvent]
    request_id: str | None


class ProviderHttpTransport:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_response_bytes: int = 4 * 1024 * 1024,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        if not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds must be between 0 and 600")
        if not 0 < max_response_bytes <= _MAX_RESPONSE_BYTES:
            raise ValueError("max_response_bytes must be between 1 and 16777216")

        self._max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def post_json(
        self,
        path: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> tuple[JsonObject, str | None]:
        url = self._url(path)
        try:
            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as response:
                _raise_for_status(response.status_code)
                request_id = _extract_request_id(response.headers)
                content = await self._read_bounded(response)
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise _timeout_error() from None
        except httpx.RequestError:
            raise _network_error() from None

        return decode_json_object(content), request_id

    def stream_sse(
        self,
        path: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> AbstractAsyncContextManager[ProviderSseConnection]:
        return self._stream_sse(path, headers=headers, payload=payload)

    @asynccontextmanager
    async def _stream_sse(
        self,
        path: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> AsyncGenerator[ProviderSseConnection]:
        url = self._url(path)
        try:
            async with aconnect_sse(
                self._client,
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as event_source:
                response = event_source.response
                _raise_for_status(response.status_code)
                _reject_large_content_length(
                    response.headers,
                    self._max_response_bytes,
                )
                yield ProviderSseConnection(
                    events=self._bounded_sse_events(event_source.aiter_sse()),
                    request_id=_extract_request_id(response.headers),
                )
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise _timeout_error() from None
        except SSEError:
            raise _invalid_response_error() from None
        except httpx.RequestError:
            raise _network_error() from None

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        _reject_large_content_length(response.headers, self._max_response_bytes)
        chunks: list[bytes] = []
        byte_count = 0
        async for chunk in response.aiter_bytes():
            byte_count += len(chunk)
            if byte_count > self._max_response_bytes:
                raise _oversized_response_error()
            chunks.append(chunk)
        return b"".join(chunks)

    async def _bounded_sse_events(
        self,
        events: AsyncIterator[ServerSentEvent],
    ) -> AsyncIterator[ServerSentEvent]:
        byte_count = 0
        try:
            async for event in events:
                byte_count += len(event.data.encode("utf-8"))
                if byte_count > self._max_response_bytes:
                    raise _oversized_response_error()
                yield event
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise _timeout_error() from None
        except SSEError:
            raise _invalid_response_error() from None
        except httpx.RequestError:
            raise _network_error() from None
        except UnicodeError:
            raise _invalid_response_error() from None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        normalized_path = path.strip("/")
        if (
            not normalized_path
            or ".." in normalized_path.split("/")
            or "?" in normalized_path
            or "#" in normalized_path
            or "://" in normalized_path
        ):
            raise ValueError("provider endpoint path is invalid")
        return f"{self._base_url}/{normalized_path}"


def _validate_base_url(value: str) -> str:
    if not value or len(value) > _MAX_BASE_URL_LENGTH:
        raise ValueError("base_url must contain at most 2048 characters")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ValueError("base_url is invalid") from None

    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise ValueError("base_url must be an HTTP(S) origin or path without credentials or query")
    return value.rstrip("/")


def _extract_request_id(headers: httpx.Headers) -> str | None:
    for name in ("request-id", "x-request-id"):
        value = headers.get(name)
        if value:
            return value[:_MAX_REQUEST_ID_LENGTH]
    return None


def _reject_large_content_length(headers: httpx.Headers, limit: int) -> None:
    value = headers.get("content-length")
    if value is None:
        return
    try:
        content_length = int(value)
    except ValueError:
        raise _invalid_response_error() from None
    if content_length < 0:
        raise _invalid_response_error()
    if content_length > limit:
        raise _oversized_response_error()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def decode_json_object(content: bytes) -> JsonObject:
    try:
        decoded = content.decode("utf-8")
        value = json.loads(decoded, parse_constant=_reject_json_constant)
        return _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError):
        raise _invalid_response_error() from None


def _raise_for_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        return
    if status_code in {401, 403}:
        raise ProviderError(
            ProviderErrorCode.AUTHENTICATION,
            "Provider authentication failed.",
            retryable=False,
        )
    if status_code == 429:
        raise ProviderError(
            ProviderErrorCode.RATE_LIMIT,
            "Provider request was rate limited.",
            retryable=True,
        )
    if status_code in {408, 504}:
        raise _timeout_error()
    if status_code >= 500:
        raise ProviderError(
            ProviderErrorCode.SERVER,
            "Provider service is temporarily unavailable.",
            retryable=True,
        )
    raise ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "Provider rejected the request.",
        retryable=False,
    )


def _timeout_error() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.TIMEOUT,
        "Provider request timed out.",
        retryable=True,
    )


def _network_error() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.SERVER,
        "Provider network request failed.",
        retryable=True,
    )


def _invalid_response_error() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "Provider returned an invalid response.",
        retryable=False,
    )


def _oversized_response_error() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.INVALID_RESPONSE,
        "Provider response exceeded the configured size limit.",
        retryable=False,
    )
