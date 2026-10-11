"""应用装配层：创建 FastAPI 实例、挂载路由、统一异常处理。

ThsApiError 在此统一转成 KCQ 错误信封，保证 HTTP 状态码恒为 200。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .envelope import fail
from .errors import INVALID_REQUEST, INVALID_RESPONSE, UNKNOWN, http_status_for
from .routes import router
from .upstream import ThsApiError, ThsUpstream

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, upstream: ThsUpstream | None = None) -> FastAPI:
    """创建应用；settings 缺省时从环境加载，upstream 缺省时新建（测试可注入替身）。"""
    resolved = settings or load_settings()
    injected = upstream

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """进程生命周期：装配上游客户端并最终关闭连接池。

        upstream 由外部注入（测试替身）时沿用注入实例，避免被真实客户端覆盖。
        """
        app.state.upstream = injected if injected is not None else ThsUpstream(resolved)
        app.state.settings = resolved
        if not resolved.has_api_key:
            logger.warning("未配置 THS_API_KEY：probe 将返回 degraded")
        try:
            yield
        finally:
            await app.state.upstream.aclose()

    app = FastAPI(title="KCQ-THS-connector", lifespan=lifespan)

    # 与 GoTDX/MT5 一致：允许 5175 等本地前端直连
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    @app.exception_handler(ThsApiError)
    async def handle_ths_error(_: Request, exc: ThsApiError) -> JSONResponse:
        """上游错误 → KCQ 错误信封。

        HTTP 状态码必须非 2xx：KCQ httpTransport 只在 !res.ok 分支解析错误信封，
        否则会丢失错误码并退化为通用 FETCH_FAILED，源流转随之失效。
        """
        return JSONResponse(
            status_code=http_status_for(exc.kcq_code),
            content=fail(exc.kcq_code, exc.message),
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        """请求体结构问题 → INVALID_REQUEST。"""
        return JSONResponse(
            status_code=http_status_for(INVALID_REQUEST),
            content=fail(INVALID_REQUEST, f"请求不合法：{exc}"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        """兜底：未预期异常按 UNKNOWN 返回，保留错误信封结构。"""
        logger.exception("未预期异常")
        return JSONResponse(
            status_code=http_status_for(UNKNOWN),
            content=fail(UNKNOWN, f"连接器内部错误：{exc}"),
        )

    return app


# INVALID_RESPONSE 供上游解析失败路径使用（保持导入完整性）
__all__ = ["create_app", "INVALID_RESPONSE"]
