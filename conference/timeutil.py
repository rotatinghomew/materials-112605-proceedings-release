"""统一的时间处理：交换资料中的所有业务时刻都带时区偏移。"""
from datetime import datetime, timezone


def parse_time(text):
    """解析带时区的 ISO-8601 时刻；缺时区或格式非法时抛 ValueError。"""
    if not isinstance(text, str):
        raise ValueError("时间须为字符串")
    instant = datetime.fromisoformat(text)
    if instant.utcoffset() is None:
        raise ValueError("时间须包含时区偏移")
    return instant


def now():
    """当前 UTC 时刻（带时区）。"""
    return datetime.now(timezone.utc)


def format_time(instant):
    return instant.isoformat()


def latest(first, second):
    """返回两个时刻中较晚者，None 视为空。"""
    if first is None:
        return second
    if second is None:
        return first
    return first if first >= second else second
