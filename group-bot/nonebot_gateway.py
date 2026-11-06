"""Start NoneBot2 with the unmodified official OneBot v11 adapter."""
import argparse
from pathlib import Path

from nonebot_inbox import Inbox  # Backward-compatible import for inbox callers.


def serve(root):
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
    from console_config import Config

    cfg = Config(root).load()
    if not cfg['nonebot_access_token']:
        raise ValueError('Configure a nonempty nonebot_access_token before starting NoneBot')
    nonebot.init(driver='~fastapi', host=cfg['nonebot_listen_host'],
                 port=cfg['nonebot_port'], onebot_access_token=cfg['nonebot_access_token'],
                 onebot_ws_urls=[], api_timeout=cfg['nonebot_api_timeout_seconds'],
                 gpt_speak_root=str(Path(root).resolve()), log_level='WARNING')
    nonebot.get_driver().register_adapter(OneBotAdapter)
    plugin = nonebot.load_plugin('nonebot_plugin_gpt_speak')
    if plugin is None:
        raise RuntimeError('GPT Speak business plugin failed to load')
    nonebot.run(ws_max_size=64 * 1024 * 1024)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    serve(parser.parse_args().root)
