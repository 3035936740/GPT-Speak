"""Persistent per-group receive-only mode; retain message transport, stop AI work."""
from console_config import Config,runtime
from session_control import interrupt


def set_mode(root,group,enabled):
    if type(enabled) is not bool:raise ValueError('仅接收开关必须是布尔值')
    Config(root,group).set('receive_only','on' if enabled else 'off')
    interrupt(runtime(root,group),'interrupt')
    return {'ok':True,'selected':group,'message':'当前群已切换为仅接收，保留聊天记录，不处理AI功能' if enabled else '当前群已恢复AI处理，只处理后续新消息'}
