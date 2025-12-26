"""时间工具 - 日期时间处理"""
import time
from datetime import datetime, timezone, timedelta
from typing import List
import pytz

from app.schemas.tool_schema import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class DateTimeTool(BuiltinTool):
    """时间工具 - 提供时间格式转换、时区转换、时间戳转换、时间计算功能"""
    
    @property
    def name(self) -> str:
        return "datetime_tool"
    
    @property
    def description(self) -> str:
        return "时间工具 - 日期时间处理：提供时间格式转化、时区转换、时间戳转换、时间计算"
    
    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="操作类型",
                required=True,
                enum=["format", "convert_timezone", "timestamp_to_datetime", "datetime_to_timestamp", "calculate", "now"]
            ),
            ToolParameter(
                name="input_value",
                type=ParameterType.STRING,
                description="输入值（时间字符串或时间戳）",
                required=False
            ),
            ToolParameter(
                name="input_format",
                type=ParameterType.STRING,
                description="输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                required=False,
                default="%Y-%m-%d %H:%M:%S"
            ),
            ToolParameter(
                name="output_format",
                type=ParameterType.STRING,
                description="输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                required=False,
                default="%Y-%m-%d %H:%M:%S"
            ),
            ToolParameter(
                name="from_timezone",
                type=ParameterType.STRING,
                description="源时区（如：UTC, Asia/Shanghai）",
                required=False,
                default="Asia/Shanghai"
            ),
            ToolParameter(
                name="to_timezone",
                type=ParameterType.STRING,
                description="目标时区（如：UTC, Asia/Shanghai）",
                required=False,
                default="Asia/Shanghai"
            ),
            ToolParameter(
                name="calculation",
                type=ParameterType.STRING,
                description="时间计算表达式（如：+1d, -2h, +30m）",
                required=False
            )
        ]
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行时间工具操作"""
        start_time = time.time()
        
        try:
            operation = kwargs.get("operation")
            
            if operation == "now":
                result = self._get_current_time(kwargs)
            elif operation == "format":
                result = self._format_datetime(kwargs)
            elif operation == "convert_timezone":
                result = self._convert_timezone(kwargs)
            elif operation == "timestamp_to_datetime":
                result = self._timestamp_to_datetime(kwargs)
            elif operation == "datetime_to_timestamp":
                result = self._datetime_to_timestamp(kwargs)
            elif operation == "calculate":
                result = self._calculate_datetime(kwargs)
            else:
                raise ValueError(f"不支持的操作类型: {operation}")
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="DATETIME_ERROR",
                execution_time=execution_time
            )

    @staticmethod
    def _get_current_time(kwargs) -> dict:
        """获取当前时间"""
        timezone_str = kwargs.get("to_timezone", "Asia/Shanghai")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        
        if timezone_str == "UTC":
            tz = timezone.utc
        else:
            tz = pytz.timezone(timezone_str)
        
        now = datetime.now(tz)

        utc_now = datetime.now(timezone.utc)
        
        return {
            "datetime": now.strftime(output_format),
            "timestamp": int(now.timestamp()),
            "timezone": timezone_str,
            "iso_format": now.isoformat(),
            "timestamp_ms": int(now.timestamp() * 1000),
            "utc_datetime": utc_now.strftime(output_format)
        }

    @staticmethod
    def _format_datetime(kwargs) -> dict:
        """格式化时间"""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        
        if not input_value:
            raise ValueError("input_value 参数是必需的")
        
        # 解析输入时间
        dt = datetime.strptime(input_value, input_format)
        
        return {
            "original": input_value,
            "formatted": dt.strftime(output_format),
            "timestamp": int(dt.timestamp()),
            "iso_format": dt.isoformat()
        }

    @staticmethod
    def _convert_timezone(kwargs) -> dict:
        """时区转换"""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        from_timezone = kwargs.get("from_timezone", "Asia/Shanghai")
        to_timezone = kwargs.get("to_timezone", "Asia/Shanghai")
        
        if not input_value:
            raise ValueError("input_value 参数是必需的")
        
        # 解析输入时间
        dt = datetime.strptime(input_value, input_format)
        
        # 设置源时区
        if from_timezone == "UTC":
            from_tz = pytz.UTC
        else:
            from_tz = pytz.timezone(from_timezone)
        
        # 设置目标时区
        if to_timezone == "UTC":
            to_tz = pytz.UTC
        else:
            to_tz = pytz.timezone(to_timezone)
        
        # 本地化时间并转换时区
        if dt.tzinfo is None:
            dt = from_tz.localize(dt)
        
        converted_dt = dt.astimezone(to_tz)
        
        return {
            "original": input_value,
            "original_timezone": from_timezone,
            "converted": converted_dt.strftime(output_format),
            "converted_timezone": to_timezone,
            "timestamp": int(converted_dt.timestamp())
        }

    @staticmethod
    def _timestamp_to_datetime(kwargs) -> dict:
        """时间戳转日期时间"""
        input_value = kwargs.get("input_value")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        timezone_str = kwargs.get("to_timezone", "Asia/Shanghai")
        
        if not input_value:
            raise ValueError("input_value 参数是必需的")
        
        # 转换时间戳
        timestamp = float(input_value)
        if timestamp > 1e12:
            timestamp = timestamp / 1000
        
        # 设置时区
        if timezone_str == "UTC":
            tz = timezone.utc
        else:
            tz = pytz.timezone(timezone_str)
        
        dt = datetime.fromtimestamp(timestamp, tz)
        
        return {
            "timestamp": timestamp,
            "datetime": dt.strftime(output_format),
            "timezone": timezone_str,
            "iso_format": dt.isoformat()
        }

    @staticmethod
    def _datetime_to_timestamp(kwargs) -> dict:
        """日期时间转时间戳"""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        timezone_str = kwargs.get("from_timezone", "Asia/Shanghai")
        
        if not input_value:
            raise ValueError("input_value 参数是必需的")
        
        # 解析输入时间
        dt = datetime.strptime(input_value, input_format)
        
        # 设置时区
        if timezone_str == "UTC":
            tz = timezone.utc
        else:
            tz = pytz.timezone(timezone_str)
        
        # 本地化时间
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        
        return {
            "datetime": input_value,
            "timezone": timezone_str,
            "timestamp": int(dt.timestamp()),
            "iso_format": dt.isoformat()
        }

    def _calculate_datetime(self, kwargs) -> dict:
        """时间计算"""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        calculation = kwargs.get("calculation")
        timezone_str = kwargs.get("from_timezone", "Asia/Shanghai")
        
        if not input_value:
            raise ValueError("input_value 参数是必需的")
        
        if not calculation:
            raise ValueError("calculation 参数是必需的")
        
        # 解析输入时间
        dt = datetime.strptime(input_value, input_format)
        
        # 设置时区
        if timezone_str == "UTC":
            tz = timezone.utc
        else:
            tz = pytz.timezone(timezone_str)
        
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        
        # 解析计算表达式
        delta = self._parse_time_delta(calculation)
        calculated_dt = dt + delta
        
        return {
            "original": input_value,
            "calculation": calculation,
            "result": calculated_dt.strftime(output_format),
            "timezone": timezone_str,
            "timestamp": int(calculated_dt.timestamp())
        }

    @staticmethod
    def _parse_time_delta(calculation: str) -> timedelta:
        """解析时间计算表达式"""
        import re
        
        # 支持的单位：d(天), h(小时), m(分钟), s(秒)
        pattern = r'([+-]?\d+)([dhms])'
        matches = re.findall(pattern, calculation.lower())
        
        if not matches:
            raise ValueError(f"无效的时间计算表达式: {calculation}")
        
        total_delta = timedelta()
        
        for value_str, unit in matches:
            value = int(value_str)
            
            if unit == 'd':
                total_delta += timedelta(days=value)
            elif unit == 'h':
                total_delta += timedelta(hours=value)
            elif unit == 'm':
                total_delta += timedelta(minutes=value)
            elif unit == 's':
                total_delta += timedelta(seconds=value)
        
        return total_delta