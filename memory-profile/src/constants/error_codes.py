"""memory-profile 业务码与 HTTP 状态映射。

**本服务自有码段（2xxxx），不与老单体的 4/5 位码碰撞**（老单体最大 10004，本服务
最小 20000）。代价是同一个语义错误两边 int 值不同，故：

- 跨服务客户端请按 **``error_code`` 字符串**分支（取值 = ``BizCode.name``，如
  ``QUOTA_EXCEEDED``），**不要按 int 值**——见下"客户端契约"。
- 本服务内部一律用具名常量（``BizCode.QUOTA_EXCEEDED``），不写字面量。

## 分段（段内 1000 容量，顺序即 HTTP 严重度递增）

| 段 | 域 | HTTP |
|---|---|---|
| 20xxx | 通用（参数/校验） | 400 / 422 |
| 21xxx | 认证（令牌） | 401 |
| 22xxx | 鉴权（权限/空间） | 403 |
| 23xxx | 配额与限流 | 402 / 429 / 401 |
| 24xxx | 资源不存在 | 400（刻意见下） |
| 25xxx | 冲突与业务状态 | 409 / 200 |
| 26xxx | 记忆域（读写/分析/档案） | 400 / 500 |
| 29xxx | 系统与依赖 | 500 / 503 |

成功码固定 ``OK = 0``，不参与分段（响应包体契约与前端 ``switch(code) case 0`` 约定）。

## 客户端契约（决定 HTTP 状态怎么选，不是教科书怎么写）

前端 ``web/src/utils/request.ts`` 的行为约束：

- **HTTP 非 2xx 时，以下状态会无条件覆盖服务端 msg**：``403 404 429 500 502 504``；
  其余（``400 402 409 422 503``）保留服务端 msg。
- ``401`` 特判跳登录。
- HTTP 2xx 时走包体 ``switch(code)``：``0/200`` 成功、``401`` 跳登录、其余弹
  ``msg`` 并 reject。

由此三条规则：

1. **资源类错误用 400，不用 404**：404 会被前端改成 ``common.apiNotFound``
   （"接口不存在"），把"终端用户不存在或不属于当前工作空间"这种业务文案丢掉，
   语义还错（用户以为路由写错）。
2. **细分业务错误（校验失败等）用 422/402/409**，这些状态前端不覆盖，能露出服务端
   翻译好的文案。
3. **只有协议性失败才用 403/429/5xx**（鉴权、限流、依赖故障）——这些状态前端有专属
   提示，覆盖 msg 也无妨。

## 响应三类

| 类 | 场景 | 形态 | 入口 |
|---|---|---|---|
| A 成功 | — | 2xx + ``code=0`` | ``utils/response_utils.success()`` |
| B 业务性失败 | 请求本身没错，业务上无结果/不可用（如该用户无数据） | 200 + ``code≠0`` | ``utils/response_utils.fail()`` |
| C 协议性失败 | 鉴权/限流/依赖故障/参数非法 | HTTP_MAPPING 的状态 + ``code≠0`` | ``src/i18n/exceptions`` 异常类 |

B 类也可抛异常（拿到 HTTP_MAPPING 的状态，如 400）——两条路的客户端都能看到服务端
msg，按语义选：**"请求有误"用异常，"请求正常但无结果"用 ``fail()``**。
"""
from enum import IntEnum


class BizCode(IntEnum):
    """业务码。取值稳定，一经发布不改；新增只追加、不复用旧值。"""

    # 通用（20xxx）
    OK = 0
    BAD_REQUEST = 20000
    VALIDATION_FAILED = 20001
    MISSING_PARAMETER = 20002
    INVALID_PARAMETER = 20003

    # 认证（21xxx）
    UNAUTHORIZED = 21000
    TOKEN_INVALID = 21001
    TOKEN_EXPIRED = 21002

    # 鉴权（22xxx）
    FORBIDDEN = 22000
    WORKSPACE_NO_ACCESS = 22001
    PERMISSION_DENIED = 22002

    # 配额与限流（23xxx）
    QUOTA_EXCEEDED = 23000
    RATE_LIMIT_EXCEEDED = 23001
    API_KEY_NOT_FOUND = 23002
    API_KEY_INVALID = 23003
    API_KEY_EXPIRED = 23004

    # 资源（24xxx）
    NOT_FOUND = 24000
    END_USER_NOT_FOUND = 24001
    WORKSPACE_NOT_FOUND = 24002
    MEMORY_CONFIG_NOT_FOUND = 24003

    # 冲突与业务状态（25xxx）
    STATE_CONFLICT = 25000
    RESOURCE_ALREADY_EXISTS = 25001
    INSUFFICIENT_DATA = 25002

    # 记忆域（26xxx）
    MEMORY_READ_FAILED = 26000
    MEMORY_WRITE_FAILED = 26001
    ANALYSIS_FAILED = 26002
    PROFILE_STORAGE_ERROR = 26003
    INVALID_USER_ID = 26004
    INVALID_FILTER_PARAMS = 26005

    # 系统与依赖（29xxx）
    INTERNAL_ERROR = 29000
    DB_ERROR = 29001
    SERVICE_UNAVAILABLE = 29002


# 业务码 → HTTP 状态。**每个码都必须登记，无兜底**：漏登记即抛 KeyError（在
# http_status_for），而不是像老单体那样静默兜底 400（会把 5xx 变成 400）。
HTTP_MAPPING: dict[BizCode, int] = {
    BizCode.OK: 200,

    # 通用：400 保留服务端 msg；422 用于细分校验失败（前端不覆盖，文案能露出）
    BizCode.BAD_REQUEST: 400,
    BizCode.VALIDATION_FAILED: 422,
    BizCode.MISSING_PARAMETER: 400,
    BizCode.INVALID_PARAMETER: 400,

    # 认证：必须真 401，前端才会跳登录
    BizCode.UNAUTHORIZED: 401,
    BizCode.TOKEN_INVALID: 401,
    BizCode.TOKEN_EXPIRED: 401,

    # 鉴权：403 前端有专属文案
    BizCode.FORBIDDEN: 403,
    BizCode.WORKSPACE_NO_ACCESS: 403,
    BizCode.PERMISSION_DENIED: 403,

    # 配额与限流
    BizCode.QUOTA_EXCEEDED: 402,
    BizCode.RATE_LIMIT_EXCEEDED: 429,
    BizCode.API_KEY_NOT_FOUND: 401,
    BizCode.API_KEY_INVALID: 401,
    BizCode.API_KEY_EXPIRED: 401,

    # 资源：刻意为 400 而非 404——前端会把 404 的文案覆盖成"接口不存在"，
    # 丢掉服务端翻译好的业务文案（见模块 docstring 规则 1）
    BizCode.NOT_FOUND: 400,
    BizCode.END_USER_NOT_FOUND: 400,
    BizCode.WORKSPACE_NOT_FOUND: 400,
    BizCode.MEMORY_CONFIG_NOT_FOUND: 400,

    # 冲突与业务状态
    BizCode.STATE_CONFLICT: 409,
    BizCode.RESOURCE_ALREADY_EXISTS: 409,
    # 业务状态而非错误："数据不足"不该报 4xx
    BizCode.INSUFFICIENT_DATA: 200,

    # 记忆域
    BizCode.MEMORY_READ_FAILED: 500,
    BizCode.MEMORY_WRITE_FAILED: 500,
    BizCode.ANALYSIS_FAILED: 500,
    BizCode.PROFILE_STORAGE_ERROR: 500,
    BizCode.INVALID_USER_ID: 400,
    BizCode.INVALID_FILTER_PARAMS: 400,

    # 系统与依赖
    BizCode.INTERNAL_ERROR: 500,
    BizCode.DB_ERROR: 500,
    BizCode.SERVICE_UNAVAILABLE: 503,
}


def http_status_for(code: BizCode) -> int:
    """业务码 → HTTP 状态。未登记即报错，不静默兜底。"""
    try:
        return HTTP_MAPPING[code]
    except KeyError:
        raise KeyError(
            f"BizCode.{code.name}({code.value}) 未登记 HTTP 状态，"
            f"请在 src/constants/error_codes.py 的 HTTP_MAPPING 中补全"
        ) from None


def biz_code_by_name(name: str) -> BizCode | None:
    """``error_code`` 字符串 → 业务码；未知名称返回 None。

    响应体 ``error_code`` 取值即 ``BizCode.name``（如 "QUOTA_EXCEEDED"），
    与老单体同名错误的**字符串**保持一致，客户端按字符串分支时跨服务通用。
    """
    return BizCode.__members__.get(name)


__all__ = ["HTTP_MAPPING", "BizCode", "biz_code_by_name", "http_status_for"]