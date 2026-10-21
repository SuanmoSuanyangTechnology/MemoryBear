"""Datetime tool - date and time handling."""
import re
import time
from datetime import UTC, datetime, timedelta
from typing import List

import pytz

from ...schemas.tool_schema import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class DateTimeTool(BuiltinTool):
    """Datetime tool - format, timezone, timestamp conversion and time calculation."""

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
                enum=["format", "convert_timezone", "timestamp_to_datetime", "now", "datetime_to_timestamp"]
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
        """Execute a datetime operation."""
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
                data=result["result_data"],
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
        """Get the current time."""
        timezone_str = kwargs.get("to_timezone", "Asia/Shanghai")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")

        if timezone_str == "UTC":
            tz = UTC
        else:
            tz = pytz.timezone(timezone_str)

        now = datetime.now(tz)

        utc_now = datetime.now(UTC)

        return {
            "timezone": timezone_str,
            "iso_format": now.isoformat(),
            "result_data": {
                "datetime": now.strftime(output_format),
                "timestamp": int(now.timestamp()),
                "timestamp_ms": int(now.timestamp() * 1000),
                "utc_datetime": utc_now.strftime(output_format),
            }
        }

    @staticmethod
    def _format_datetime(kwargs) -> dict:
        """Format a datetime string."""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")

        if not input_value:
            raise ValueError("input_value 参数是必需的")

        # Parse the input time
        dt = datetime.strptime(input_value, input_format)

        return {
            "original": input_value,
            "formatted": dt.strftime(output_format),
            "timestamp": int(dt.timestamp()),
            "iso_format": dt.isoformat(),
            "result_data": dt.strftime(output_format)
        }

    @staticmethod
    def _convert_timezone(kwargs) -> dict:
        """Convert a datetime between timezones."""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        from_timezone = kwargs.get("from_timezone", "Asia/Shanghai")
        to_timezone = kwargs.get("to_timezone", "Asia/Shanghai")

        if not input_value:
            raise ValueError("input_value 参数是必需的")

        # Parse the input time
        dt = datetime.strptime(input_value, input_format)

        # Resolve the source timezone
        if from_timezone == "UTC":
            from_tz = pytz.UTC
        else:
            from_tz = pytz.timezone(from_timezone)

        # Resolve the target timezone
        if to_timezone == "UTC":
            to_tz = pytz.UTC
        else:
            to_tz = pytz.timezone(to_timezone)

        # Localize and convert
        if dt.tzinfo is None:
            dt = from_tz.localize(dt)

        converted_dt = dt.astimezone(to_tz)

        return {
            "original": input_value,
            "original_timezone": from_timezone,
            "converted": converted_dt.strftime(output_format),
            "converted_timezone": to_timezone,
            "timestamp": int(converted_dt.timestamp()),
            "result_data": converted_dt.strftime(output_format)
        }

    @staticmethod
    def _timestamp_to_datetime(kwargs) -> dict:
        """Convert a timestamp to a datetime string."""
        input_value = kwargs.get("input_value")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        timezone_str = kwargs.get("to_timezone", "Asia/Shanghai")

        if not input_value:
            raise ValueError("input_value 参数是必需的")

        # Convert the timestamp (accept seconds or milliseconds)
        timestamp = float(input_value)
        if timestamp > 1e12:
            timestamp = timestamp / 1000

        # Resolve the timezone
        if timezone_str == "UTC":
            tz = UTC
        else:
            tz = pytz.timezone(timezone_str)

        dt = datetime.fromtimestamp(timestamp, tz)

        return {
            "timestamp": timestamp,
            "datetime": dt.strftime(output_format),
            "timezone": timezone_str,
            "iso_format": dt.isoformat(),
            "result_data": dt.strftime(output_format)
        }

    @staticmethod
    def _datetime_to_timestamp(kwargs) -> dict:
        """Convert a datetime string to a timestamp in milliseconds."""
        input_value = kwargs.get("input_value").strip()
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        timezone_str = kwargs.get("from_timezone", "Asia/Shanghai")

        if not input_value:
            raise ValueError("input_value 参数是必需的")

        # Parse the input time
        dt = datetime.strptime(input_value, input_format)

        # Resolve the timezone
        if timezone_str == "UTC":
            tz = UTC
        else:
            tz = pytz.timezone(timezone_str)

        # Localize the naive datetime
        if dt.tzinfo is None:
            dt = tz.localize(dt)

        return {
            "datetime": input_value,
            "timezone": timezone_str,
            "timestamp": int(dt.timestamp() * 1000),
            "iso_format": dt.isoformat(),
            "result_data": int(dt.timestamp() * 1000)
        }

    def _calculate_datetime(self, kwargs) -> dict:
        """Calculate a datetime with a delta expression."""
        input_value = kwargs.get("input_value")
        input_format = kwargs.get("input_format", "%Y-%m-%d %H:%M:%S")
        output_format = kwargs.get("output_format", "%Y-%m-%d %H:%M:%S")
        calculation = kwargs.get("calculation")
        timezone_str = kwargs.get("from_timezone", "Asia/Shanghai")

        if not input_value:
            raise ValueError("input_value 参数是必需的")

        if not calculation:
            raise ValueError("calculation 参数是必需的")

        # Parse the input time
        dt = datetime.strptime(input_value, input_format)

        # Resolve the timezone
        if timezone_str == "UTC":
            tz = UTC
        else:
            tz = pytz.timezone(timezone_str)

        if dt.tzinfo is None:
            dt = tz.localize(dt)

        # Parse the calculation expression
        delta = self._parse_time_delta(calculation)
        calculated_dt = dt + delta

        return {
            "original": input_value,
            "calculation": calculation,
            "result": calculated_dt.strftime(output_format),
            "timezone": timezone_str,
            "timestamp": int(calculated_dt.timestamp()),
            "result_data": calculated_dt.strftime(output_format)
        }

    @staticmethod
    def _parse_time_delta(calculation: str) -> timedelta:
        """Parse a time calculation expression."""
        # Supported units: d (days), h (hours), m (minutes), s (seconds)
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
