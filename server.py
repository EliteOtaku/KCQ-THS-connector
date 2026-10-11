"""启动入口：uv run python ./server.py（默认 127.0.0.1:8092）。"""
from __future__ import annotations

import logging

import uvicorn

from app.config import load_settings
from app.main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> None:
    """加载配置并启动 HTTP 服务。"""
    settings = load_settings()
    app = create_app(settings)
    print(f"KCQ-THS-connector 启动于 http://127.0.0.1:{settings.port}")
    print(f"上游: {settings.base_url}  key: {'已配置' if settings.has_api_key else '未配置'}")
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
