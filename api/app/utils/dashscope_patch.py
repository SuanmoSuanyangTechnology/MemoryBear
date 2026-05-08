"""
DashScope SDK 补丁：修复 __getattr__ 违反 Python 属性访问协议的 bug。

背景
----
DashScope SDK 的 DictMixin（所有响应类的基类）的 __getattr__ 实现为：

    def __getattr__(self, attr):
        return self[attr]

当属性/键不存在时，它抛出 KeyError。但按照 Python 数据模型规范，
__getattr__ 应当抛出 AttributeError，否则 hasattr()/getattr(obj, name, default)
等内置函数会失效。

实际影响
--------
requests 库在构造 HTTPError 时会调用 hasattr(response, "request")
（见 requests/exceptions.py:22），当 DashScope 响应对象参与异常链路时，
hasattr 会因 KeyError 直接崩溃，掩盖了真正的 HTTP 错误（如 429 限流、超时）。

此时抛出的异常表现为 KeyError('request')，极具误导性，并导致项目内已有的
429 自动重试逻辑无法捕获真正的限流错误。

参考
----
DashScope SDK 官方 Issue #114：
https://github.com/dashscope/dashscope-sdk-python/issues/114

修复
----
对 DictMixin.__getattr__ 进行 monkey-patch，将 KeyError 转换为 AttributeError，
使其符合 Python 语义。补丁应用于基类，因此所有派生响应类型（DashScopeAPIResponse、
GenerationResponse、MultiModalConversationResponse 等）都能一次性受益。

使用方式
--------
在应用入口（main.py / celery_worker.py）的最顶部导入本模块，
在任何 DashScope 调用发生前完成补丁注入：

    import app.utils.dashscope_patch  # noqa: F401
"""

import logging

logger = logging.getLogger(__name__)

try:
    from dashscope.api_entities.dashscope_response import DictMixin

    # 防止被重复应用（例如 main 和 celery worker 都导入时）
    if not getattr(DictMixin, "_redbear_getattr_patched", False):
        _orig_getattr = DictMixin.__getattr__

        def _safe_getattr(self, attr):
            """符合 Python 语义的 __getattr__：键缺失抛 AttributeError 而非 KeyError。"""
            try:
                return _orig_getattr(self, attr)
            except KeyError as e:
                # 使用 `from None` 抑制 KeyError 链，避免异常信息里出现误导性的
                # "During handling of the above exception..." 堆栈
                raise AttributeError(attr) from None

        DictMixin.__getattr__ = _safe_getattr
        DictMixin._redbear_getattr_patched = True  # type: ignore[attr-defined]
        logger.info(
            "DashScope SDK 补丁已生效：DictMixin.__getattr__ 在缺失键时抛 AttributeError"
        )
except ImportError:
    # DashScope SDK 未安装时跳过，不影响其他 provider
    logger.debug("未安装 dashscope，跳过 DashScope SDK 补丁")
except Exception as e:
    # 补丁失败不应阻止应用启动
    logger.warning(f"应用 DashScope SDK 补丁失败，将继续启动: {e}")
