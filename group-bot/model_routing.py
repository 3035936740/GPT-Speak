"""Select the model for internal work while preserving the reply configuration."""

FOLLOW_REPLY = 'inherit_reply'
DECISION_SETTINGS = ('decision_model', 'decision_reasoning_effort', 'decision_fast_mode', 'role_models')


def decision_config(cfg, role=None):
    result = {**cfg, '_model_stage': 'decision', 'fast_mode': cfg.get('decision_fast_mode', False)}
    model = cfg.get('decision_model')
    if model and model != FOLLOW_REPLY:
        result.update(model=model, reasoning_effort=cfg.get('decision_reasoning_effort', 'low'))
    if role:
        overrides = cfg.get('role_models', {}).get(role, {})
        for field in ('model', 'reasoning_effort'):
            if overrides.get(field):
                result[field] = overrides[field]
        if type(overrides.get('fast_mode')) is bool:
            result['fast_mode'] = overrides['fast_mode']
    return result


def validate_decision_models(catalog, cfg):
    from configure_launch import validate

    models = cfg.get('role_models', {})
    validate_role_models(models)
    if cfg.get('decision_fast_mode') or cfg.get('decision_model') not in (None, '', FOLLOW_REPLY):
        selected = decision_config(cfg)
        validate(catalog, selected['model'], selected['reasoning_effort'], selected['fast_mode'])
    for role in models:
        selected = decision_config(cfg, role)
        try:
            validate(catalog, selected['model'], selected['reasoning_effort'], selected['fast_mode'])
        except ValueError as error:
            raise ValueError(f'{role}（{selected["model"]}）：{error}') from error


def validate_role_models(models):
    if not isinstance(models, dict):
        raise ValueError('role_models 必须是 JSON 对象')
    for role, override in models.items():
        if role not in {'context', 'emotion', 'vision'} or not isinstance(override, dict):
            raise ValueError('无效分工模型配置')
        if 'fast_mode' in override and type(override['fast_mode']) is not bool:
            raise ValueError(f'{role} 的 Fast 必须为布尔值')
