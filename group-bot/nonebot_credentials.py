"""Persist the initial gateway credential independently of empty config imports."""
import json
from pathlib import Path
import secrets

from image_archive import locked
from storage_io import atomic_write_text


def ensure_token(root, cfg):
    root = Path(root)
    path = root / 'nonebot-credentials.json'
    with locked(root / 'nonebot-credentials.lock'):
        saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        token = cfg.get('nonebot_access_token') or saved.get('access_token')
        if not token:
            if path.exists():
                raise ValueError('Saved NoneBot credential is invalid; set an access token explicitly')
            token = secrets.token_urlsafe(32)
        if saved.get('access_token') != token:
            atomic_write_text(path, json.dumps({'access_token': token}))
        if cfg.get('nonebot_access_token') != token:
            from console_config import Config
            cfg['nonebot_access_token'] = token
            Config(root).save(cfg)
    return token
