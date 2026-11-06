"""Compatibility imports for callers routed exclusively through NoneBot2."""
from pathlib import Path
import httpx
from nonebot_bridge import DEFAULTS, validate


class NapCatHTTPClient(httpx.AsyncClient):
    """HTTP client for the authenticated NoneBot bridge."""


async def request(client, env, action, params=None, cfg=None):
    from nonebot_bridge import request as bridge_request
    if cfg is None:
        from console_config import Config
        cfg = Config(Path(__file__).resolve().parent).load()
    return await bridge_request(client, {**DEFAULTS, **cfg}, action, params)
