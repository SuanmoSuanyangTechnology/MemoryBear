import re
from dateutil import parser
from datetime import datetime

def validate_date_format(date_str: str) -> bool:
    """
    Validate if the date string is in the format YYYY-MM-DD.
    """
    pattern = r"^\d{4}-\d{1,2}-\d{1,2}$"
    return bool(re.match(pattern, date_str))


def normalize_date(date_str: str) -> str:
    """
    更强大的日期标准化函数，支持多种日期格式转换为 Y-M-D 格式

    Args:
        date_str: 各种格式的日期字符串

    Returns:
        Y-M-D 格式的标准化日期字符串
    """
    if not date_str or not isinstance(date_str, str):
        return date_str

    # 移除首尾空格
    date_str = date_str.strip().replace(' ', '').replace('/', '').replace('.', '').replace('_', '').replace('-', '')

    try:
        # 预处理：识别并规范化特殊格式
        preprocessed_str = preprocess_date_string(date_str)

        # 使用 dateutil.parser 进行解析[citation:1][citation:7]
        dt = parser.parse(preprocessed_str, dayfirst=False, yearfirst=True)

        return dt.strftime('%Y-%m-%d')

    except (ValueError, TypeError, OverflowError):
        # 如果智能解析失败，尝试格式匹配
        return fallback_parse(date_str)


def preprocess_date_string(date_str: str) -> str:
    """预处理日期字符串，处理特殊格式"""

    # 处理类似 "20259/28" 的格式（年份后直接跟月份没有分隔）
    match = re.match(r'^(\d{4,5})[/\.\-_]?(\d{1,2})[/\.\-_]?(\d{1,2})$', date_str)
    if match:
        year, month, day = match.groups()
        # 如果年份超过4位，可能是年份和月份连在一起
        if len(year) > 4:
            # 取前4位作为年份，剩余作为月份
            actual_year = year[:4]
            actual_month = year[4:] + (month if month else '')
            # 重新组合
            if day:
                return f"{actual_year}-{actual_month.zfill(2)}-{day.zfill(2)}"
            else:
                return f"{actual_year}-{actual_month.zfill(2)}"
        else:
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}" if day else f"{year}-{month.zfill(2)}"

    # 处理无分隔符的纯数字格式[citation:4]
    if re.match(r'^\d{6,8}$', date_str):
        if len(date_str) == 8:  # YYYYMMDD
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        elif len(date_str) == 6:  # YYMMDD 或 MMDDYY
            # 尝试不同解释
            if 1 <= int(date_str[:2]) <= 12:  # 可能是 MMDDYY
                return f"20{date_str[4:6]}-{date_str[:2]}-{date_str[2:4]}"
            else:  # 可能是 YYMMDD
                return f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"

    # 处理混合分隔符，统一为 -
    date_str = re.sub(r'[/\._]', '-', date_str)

    return date_str


def fallback_parse(date_str: str) -> str:
    """备选解析方案"""

    # 尝试常见的日期格式[citation:4][citation:5]
    formats_to_try = [
        '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',
        '%Y%m%d', '%y%m%d',
        '%m-%d-%Y', '%m/%d/%Y', '%m.%d.%Y',
        '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y',
        '%Y-%m', '%Y/%m', '%Y.%m'
    ]

    for fmt in formats_to_try:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    # 所有方法都失败时，返回原字符串或抛出异常
    return date_str


def normalize_date_safe(date_str: str, default: str = None) -> str:
    """
    安全的日期标准化函数，提供默认值处理

    Args:
        date_str: 日期字符串
        default: 解析失败时的默认返回值

    Returns:
        标准化日期字符串或默认值
    """
    try:
        result = normalize_date(date_str)
        # 检查结果是否是有效的日期格式
        if validate_date_format(result):
            return result
        else:
            return default if default is not None else date_str
    except:
        return default if default is not None else date_str

if __name__ == "__main__":
    start_dates = ["2025/10/28", "2025.10.28", "2025_10_28", "20251028"]
    for date in start_dates:
        print(normalize_date_safe(date))
