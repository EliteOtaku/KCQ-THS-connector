"""配置层：env → Settings 的唯一入口。

全部可调参数集中于此，其他模块禁止散落 os.environ。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# 默认上游地址（同花顺金融数据 API）
DEFAULT_BASE_URL = "https://fuyao.aicubes.cn"
# 默认服务端口（8090=MT5 / 8080=GoTDX / 8081=Binance / 8000=BaoStock 均已占用）
DEFAULT_PORT = 8092
# 本地凭据文件（与 cloudtrade 其它组件共用）
CONFIG_PATH = Path.home() / ".cloudtrade" / "config.json"


def _read_config_key() -> str | None:
    """从 ~/.cloudtrade/config.json 的 ths.api_key 读取 key；缺失或损坏返回 None。"""
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("ths", {}).get("api_key")
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class Settings:
    """连接器运行参数。"""

    api_key: str | None
    base_url: str
    port: int
    timeout_s: float
    max_retries: int
    retry_backoff_s: float

    @property
    def has_api_key(self) -> bool:
        """是否已配置 key；未配置时 probe 降级而非崩溃。"""
        return bool(self.api_key)


def load_settings() -> Settings:
    """env → Settings。key 解析顺序：THS_API_KEY → config.json 的 ths.api_key。"""
    api_key = os.environ.get("THS_API_KEY") or _read_config_key()
    return Settings(
        api_key=api_key,
        base_url=os.environ.get("THS_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        port=int(os.environ.get("THS_PORT", DEFAULT_PORT)),
        timeout_s=float(os.environ.get("THS_TIMEOUT_S", "20.0")),
        max_retries=int(os.environ.get("THS_MAX_RETRIES", "2")),
        retry_backoff_s=float(os.environ.get("THS_RETRY_BACKOFF_S", "1.0")),
    )
