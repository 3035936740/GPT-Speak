"""Back off unavailable QQ connections without delaying an operator's retry/stop."""
import asyncio


def retry_delay(failures, *, absent=False):
    return 60 if absent else min(60, 10 * 2 ** min(max(0, failures - 1), 3))


async def wait_retry(folder, cfg, settings, seconds, refresh_name='group-directory-refresh'):
    keys=('enabled','private_reply_enabled','expected_self_id','napcat_connection_mode',
          'napcat_host','napcat_port','napcat_path','napcat_secure','napcat_token','mcp_env_file')
    original=tuple(cfg.get(key) for key in keys)
    for _ in range(int(seconds)):
        current=settings()
        marker=folder/refresh_name
        if marker.exists():
            marker.unlink(missing_ok=True)
            return 'refresh'
        if ((folder/'STOP').exists() or tuple(current.get(key) for key in keys)!=original):
            return
        await asyncio.sleep(1)
