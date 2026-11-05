"""NapCat business errors are distinct from uncertain HTTP delivery failures."""
import re


class QQAccountMismatch(ValueError):
    pass


class QQNotInGroup(ValueError):
    pass


def check_account(login, expected):
    if str(login.get('user_id')) != str(expected):
        raise QQAccountMismatch(f'Bot QQ 号不匹配，配置为 {expected}，NapCat 登录为 {login.get("user_id") or "未知"}')


def group_directory(groups):
    if isinstance(groups, list) and all(isinstance(row, dict) and 'group_id' in row for row in groups):
        return {str(row['group_id']):row for row in groups}
    return None


class NapCatAPIError(RuntimeError):
    def __init__(self, action, result):
        from debug_events import safe_log
        self.action = action
        self.retcode = result.get('retcode')
        self.status = result.get('status')
        self.detail = safe_log(result.get('message') or result.get('wording') or '未提供错误说明')[:300]
        super().__init__(f'NapCat API failed: {action}; retcode={self.retcode}; {self.detail}')

    @property
    def missing_forward_source(self):
        return self.action == 'forward_group_single_msg' and bool(re.search(
            r'无法找到消息|消息不存在|(?:message|msg).*(?:not found|does not exist)|'
            r'(?:action|api).*(?:not found|not supported)|不支持.*(?:接口|操作)', self.detail, re.I))

    @property
    def retryable(self):
        # A timeout may mean the message was accepted. Do not blindly send it again.
        return not re.search(r'timeout|timed out|超时', self.detail, re.I) and bool(re.search(
            r'繁忙|频率|限流|稍后重试|rate.?limit|too many requests|busy', self.detail, re.I))
