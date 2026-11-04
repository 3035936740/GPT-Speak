"""Ordinary model input limits and the budget remaining after reserved text."""
from sys import maxsize


DEFAULT_INPUT_CHARS = 19200
MAX_INPUT_CHARS = 2147483647


def configured_limit(cfg):
    return cfg.get('max_prompt_chars', DEFAULT_INPUT_CHARS)


def input_budget(cfg):
    return configured_limit(cfg) or maxsize


def remaining_limit(cfg, reserved_chars):
    limit = configured_limit(cfg)
    return max(1, limit - reserved_chars) if limit else 0
