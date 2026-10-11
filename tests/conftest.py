"""测试夹具：FakeUpstream 替身，禁止测试访问网络。"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import Settings
from app.envelope import UpstreamEnvelope


class FakeUpstream:
    """ThsUpstream 替身：按 (path, params) 预置响应，不发任何网络请求。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses: dict[str, object] = {}
        self._default: object | None = None

    def set_response(self, path: str, data: dict) -> None:
        """为指定 path 预置成功响应（code=0）。"""
        self._responses[path] = data

    def set_default(self, data: dict) -> None:
        """为所有未预置的 path 设置兜底成功响应。"""
        self._default = data

    def set_raw(self, path: str, envelope: UpstreamEnvelope) -> None:
        """为指定 path 预置完整信封（用于构造错误码场景）。"""
        self._responses[path] = envelope

    async def get(self, path: str, params: dict | None = None) -> UpstreamEnvelope:
        """记录调用并返回预置响应。

        非 0 业务码按真实客户端的契约抛 ThsApiError（经同一套 classify 翻译），
        以保证替身与真身行为一致。
        """
        recorded = params or {}
        self.calls.append((path, recorded))
        response = self._responses.get(path, self._default)
        if response is None:
            raise AssertionError(f"FakeUpstream 未预置响应：{path}")
        if isinstance(response, UpstreamEnvelope):
            envelope = response
        else:
            envelope = UpstreamEnvelope(
                code=0, message="success", data=response, request_id="fake"
            )
        if envelope.code != 0:
            from app.errors import classify
            from app.upstream import ThsApiError

            kcq_code, retryable = classify(envelope.code, envelope.message)
            raise ThsApiError(
                kcq_code,
                envelope.message,
                retryable=retryable,
                upstream_code=envelope.code,
            )
        return envelope

    async def aclose(self) -> None:
        """兼容真实客户端接口。"""
        return None

    def calls_for(self, path: str) -> list[dict]:
        """取某 path 的全部调用参数。"""
        return [params for called_path, params in self.calls if called_path == path]


@pytest.fixture
def fake_upstream() -> FakeUpstream:
    """返回一个 FakeUpstream 实例。"""
    return FakeUpstream()


@pytest.fixture
def settings() -> Settings:
    """测试用配置（不读真实环境）。"""
    return Settings(
        api_key="test-key",
        base_url="https://example.invalid",
        port=8092,
        timeout_s=5.0,
        max_retries=2,
        retry_backoff_s=0.0,
    )


@pytest.fixture
def app(settings: Settings, fake_upstream: FakeUpstream):
    """构造 FastAPI 应用并注入 FakeUpstream（经构造参数，避免被 lifespan 覆盖）。"""
    from app.main import create_app

    return create_app(settings, upstream=fake_upstream)


@pytest.fixture
def client(app) -> Iterator:
    """同步 TestClient；用 with 触发 lifespan。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
