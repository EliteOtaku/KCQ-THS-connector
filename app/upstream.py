"""上游访问层：访问同花顺 REST API 的唯一出口。

硬约束：httpx 只允许出现在本模块；其他模块禁止直接 import httpx。
本模块负责 wire 语义（请求头、超时、重试退避）与错误分类，不含领域映射。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import Settings
from .envelope import EnvelopeParseError, UpstreamEnvelope, parse_upstream
from .errors import (
    CODE_OK,
    TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    classify,
)

logger = logging.getLogger(__name__)

# 限流时上游可能返回 429（此时不保证信封结构）
HTTP_TOO_MANY_REQUESTS = 429


class ThsApiError(Exception):
    """上游调用失败；携带已翻译的 KCQ 错误码与可重试标记。"""

    def __init__(
        self,
        kcq_code: str,
        message: str,
        *,
        retryable: bool = False,
        upstream_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kcq_code = kcq_code
        self.message = message
        self.retryable = retryable
        self.upstream_code = upstream_code


class ThsUpstream:
    """同花顺 REST 客户端：连接池复用 + 指数退避重试。

    官方明确要求限流后"不要立即连续重试"，故退避为 base * 2^attempt。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """惰性创建复用的 AsyncClient（连接池）。

        trust_env=False 是刻意的：同花顺为境内服务，实测直连可达，不需要代理。
        更关键的是，本机 HTTP_PROXY 指向 127.0.0.1:7078（用于境外站点），而
        NO_PROXY 含 `[::1]` —— httpx 0.28.1 解析该 IPv6 方括号写法会抛
        `InvalidURL: Invalid port: ':1]'`，导致客户端根本构造不出来。
        关闭 env 代理读取可同时规避这两个问题。
        """
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self._settings.api_key:
                headers["X-api-key"] = self._settings.api_key
            self._client = httpx.AsyncClient(
                base_url=self._settings.base_url,
                headers=headers,
                timeout=self._settings.timeout_s,
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict | None = None) -> UpstreamEnvelope:
        """发 GET 请求并解包；业务错误抛 ThsApiError。

        重试策略：仅对 retryable 错误重试 max_retries 次，退避 base * 2^attempt。
        """
        attempts = self._settings.max_retries + 1
        last_error: ThsApiError | None = None
        for attempt in range(attempts):
            try:
                return await self._request_once(path, params or {})
            except ThsApiError as exc:
                last_error = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                delay = self._settings.retry_backoff_s * (2**attempt)
                logger.warning(
                    "上游可重试错误(第%s次)：%s；%.1fs 后重试", attempt + 1, exc.message, delay
                )
                await asyncio.sleep(delay)
        # 理论不可达：循环内要么 return 要么 raise
        raise last_error if last_error else ThsApiError(UPSTREAM_UNAVAILABLE, "上游调用失败")

    async def _request_once(self, path: str, params: dict) -> UpstreamEnvelope:
        """单次请求：发请求 → 解析信封 → 非 0 抛错。"""
        client = self._ensure_client()
        try:
            response = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ThsApiError(TIMEOUT, f"上游超时：{exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ThsApiError(
                UPSTREAM_UNAVAILABLE, f"上游连接失败：{exc}", retryable=True
            ) from exc

        # 429 优先于信封解析：限流响应体不保证符合契约
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise ThsApiError(
                UPSTREAM_UNAVAILABLE, "上游限流(HTTP 429)", retryable=True
            )

        try:
            raw = response.json()
        except ValueError as exc:
            raise ThsApiError(
                "INVALID_RESPONSE", f"上游响应不是合法 JSON（HTTP {response.status_code}）"
            ) from exc

        try:
            envelope = parse_upstream(raw)
        except EnvelopeParseError as exc:
            raise ThsApiError("INVALID_RESPONSE", exc.message) from exc

        if envelope.code != CODE_OK:
            kcq_code, retryable = classify(envelope.code, envelope.message)
            raise ThsApiError(
                kcq_code,
                envelope.message or f"上游业务错误 code={envelope.code}",
                retryable=retryable,
                upstream_code=envelope.code,
            )
        return envelope
