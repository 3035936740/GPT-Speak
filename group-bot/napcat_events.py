"""Compatibility entry point for NoneBot's shared reverse event inbox."""
async def poll(client, cfg, env):
    from nonebot_bridge import poll as bridge_poll, DEFAULTS
    return await bridge_poll(client, {**DEFAULTS, **cfg})
