"""Bounded data recovery. Integrity/TLS failures never become zero or stale success."""
import ssl
import time
from urllib.error import URLError,HTTPError

def error_info(exc):
    reason=str(exc)
    if isinstance(exc,ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in reason:
        return dict(category='certificate',retryable=False,message='数据源证书验证失败',action='检查系统日期及网络证书；保持证书校验开启后再重试。')
    if isinstance(exc,HTTPError):
        transient=exc.code in (408,425,429) or 500<=exc.code<600
        return dict(category='rate_limit' if exc.code==429 else 'network',retryable=transient,message='公开数据源暂时不可用（HTTP %s）'%exc.code,action='稍后点击“重试缺失数据”。' if transient else '数据源拒绝请求；检查网络后再试，不会绕过访问限制。')
    if isinstance(exc,(URLError,TimeoutError,ConnectionError)) or type(exc).__name__ in ('SourceUnavailable','RemoteDisconnected'):
        return dict(category='rate_limit' if '429' in reason else 'network',retryable=True,message=reason or '公开数据连接中断',action='检查网络；自动重试耗尽后可点击“重试缺失数据”。')
    if isinstance(exc,OSError):
        return dict(category='storage',retryable=False,message='本地文件保存或读取失败',action='检查数据文件夹权限与磁盘空间；原有记录保留。')
    return dict(category='validation',retryable=False,message=reason if isinstance(exc,ValueError) else '数据未通过校验',action='查看具体缺失项，稍后重新获取；不会以旧日期、示例或补零替代。')

def retry_data(call,label,notify=lambda event:None,attempts=2,sleep=None):
    sleep=sleep or time.sleep
    for attempt in range(1,attempts+1):
        try:return call()
        except Exception as exc:
            info=error_info(exc)
            if attempt>=attempts or not info['retryable']:raise
            delay=min(4,2**(attempt-1))
            notify(dict(resource=label,attempt=attempt+1,max_attempts=attempts,delay_seconds=delay,category=info['category'],message=info['message']))
            sleep(delay)
