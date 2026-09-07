"""LiteSkill（MemorySkills）内部管理接口客户端。

供运营后台 ``/sys/memory-lite/*`` 适配层调用 LiteSkill 的
``/internal/admin/memory-lite/*`` 管理接口，既包含 overview / users / products
等只读查询接口，也包含 update_product / create_product / recharge_write_quota
等写入接口。携带服务凭证 ``X-Internal-Token`` 以证明"请求来自 Enterprise"，
并透传 admin_id / request_id 用于链路追踪。参照 ``speedbear_gateway_client``
的错误映射范式。

实现约束：
- 全部方法为 ``async``：适配层端点为 ``async def``，若在此处做同步阻塞的
  HTTP 调用会卡死事件循环（LiteSkill 变慢/挂起时每次最长阻塞
  ``LITESKILL_TIMEOUT`` 秒），因此底层统一使用 ``httpx.AsyncClient``。
- 客户端实例按请求创建，但底层 ``httpx.AsyncClient`` 在模块级共享复用，
  避免每次请求新建 TCP/TLS 连接；共享实例绑定创建它的运行事件循环，
  loop 变化时自动重建（覆盖 uvicorn reload / 测试多 loop 场景）。
  进程退出前由应用 lifespan 调用 :func:`aclose_shared_client` 释放。
- 异常统一抛 :class:`LiteSkillAdminError`，按失败类别标记 ``category``，
  供适配层 ``_gateway_error`` 做对外状态码映射：
    ``upstream``  — 上游返回了 HTTP 状态（4xx/5xx/3xx），携带原始状态码
    ``transport`` — 网络错误 / 超时（未获得 HTTP 响应）
    ``config``    — 本端配置问题（未配置 token / 响应非 JSON，疑似 URL 配错）
    ``business``  — 上游 HTTP 2xx 但信封 ``code != OK``（业务拒绝）
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings


class LiteSkillAdminErrorCategory:
    """LiteSkill 内部接口调用失败的类别（供适配层做对外映射）。"""

    #: 上游返回了 HTTP 状态码（4xx / 5xx / 3xx）
    UPSTREAM = "upstream"
    #: 网络错误 / 超时，未获得 HTTP 响应
    TRANSPORT = "transport"
    #: 本端配置问题（未配置 token / 响应非 JSON，疑似 LITESKILL_BASE_URL 配错）
    CONFIG = "config"
    #: 上游 HTTP 2xx 但信封 code != OK（业务拒绝）
    BUSINESS = "business"


class LiteSkillAdminError(Exception):
    """LiteSkill 内部接口调用异常。

    Attributes:
        message: 人读提示
        category: 失败类别，见 :class:`LiteSkillAdminErrorCategory`
        status_code: 上游 HTTP 状态码；``category == UPSTREAM`` 时有值，
            其余类别通常为 ``None``
        payload: 上游响应体（若可解析为 JSON）
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: str = LiteSkillAdminErrorCategory.UPSTREAM,
        payload: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.category = category
        self.payload = payload


#: 模块级共享 AsyncClient（懒创建、绑定当前运行事件循环）
_shared_client: httpx.AsyncClient | None = None
_shared_client_loop: asyncio.AbstractEventLoop | None = None


def _get_shared_client(timeout: float) -> httpx.AsyncClient:
    """取共享 AsyncClient；绑定创建时的运行 loop，loop 变化则重建。

    重建时旧实例直接丢给 GC——旧实例通常绑定已结束的 loop（uvicorn reload /
    测试切换 loop），无需也不能在其上发起关闭。
    """
    global _shared_client, _shared_client_loop
    loop = asyncio.get_running_loop()
    if _shared_client is None or _shared_client_loop is not loop:
        _shared_client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        _shared_client_loop = loop
    return _shared_client


async def aclose_shared_client() -> None:
    """关闭共享 AsyncClient（应用 shutdown 时调用；未创建则 no-op）。

    仅在当前运行 loop 与创建时一致时才执行真正的 ``aclose``；loop 已切换
    （如 uvicorn reload 后旧进程退出）则直接丢弃引用，交由解释器回收。
    """
    global _shared_client, _shared_client_loop
    if _shared_client is None:
        return
    client = _shared_client
    bound_loop = _shared_client_loop
    _shared_client = None
    _shared_client_loop = None
    try:
        if asyncio.get_running_loop() is bound_loop:
            await client.aclose()
    except RuntimeError:
        pass  # 无运行 loop（进程退出中），交由解释器回收


class LiteSkillAdminClient:
    """调用 LiteSkill 内部管理接口（含只读查询与写入操作）。"""

    def __init__(self) -> None:
        self.base_url = settings.LITESKILL_BASE_URL.rstrip("/")
        self.token = settings.LITESKILL_INTERNAL_TOKEN
        self.timeout = settings.LITESKILL_TIMEOUT

    # ── 业务便捷方法 ──────────────────────────────────────────────
    async def overview(self, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return await self._get("/internal/admin/memory-lite/overview", lang=lang, admin_id=admin_id, request_id=request_id)

    async def users(
        self,
        *,
        page: int = 1,
        pagesize: int = 20,
        keyword: str | None = None,
        status: str | None = None,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        params: dict[str, Any] = {"page": page, "pagesize": pagesize}
        if keyword:
            params["keyword"] = keyword
        if status:
            params["status"] = status
        return await self._get(
            "/internal/admin/memory-lite/users",
            params=params,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    async def user_detail(self, account_id: str, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return await self._get(
            f"/internal/admin/memory-lite/users/{account_id}",
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    async def products(self, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return await self._get("/internal/admin/memory-lite/products", lang=lang, admin_id=admin_id, request_id=request_id)

    async def update_product(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        """修改预设包（POST，目标 id 在 body 内）。"""
        return await self._request(
            "POST",
            "/internal/admin/memory-lite/products/update",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    async def create_product(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        return await self._request(
            "POST",
            "/internal/admin/memory-lite/products/create",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    async def recharge_write_quota(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        """给指定端用户加写入次数（POST，end_user_id + count 在 body 内）。"""
        return await self._request(
            "POST",
            "/internal/admin/memory-lite/write-quota/recharge",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    # ── 底层请求 ──────────────────────────────────────────────────
    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        return await self._request(
            "GET", path, params=params, lang=lang, admin_id=admin_id, request_id=request_id
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        headers = self._build_headers(admin_id=admin_id, request_id=request_id, lang=lang)
        request_params = dict(params or {})
        if lang:
            request_params.setdefault("lang", lang)

        url = f"{self.base_url}{path}"
        try:
            # 禁止自动跟随重定向：内部管理接口本不应发生重定向，
            # 若跟随跨域重定向会把 X-Internal-Token 内部服务凭证泄露给重定向目标主机。
            client = _get_shared_client(self.timeout)
            response = await client.request(
                method.upper(), url, headers=headers, params=request_params, json=json
            )
            if 300 <= response.status_code < 400:
                # 拒绝全部 3xx：内部接口不应出现任何重定向/未修改等响应。
                # 先消费响应体再抛错，保证连接归还 keep-alive 池。
                await response.aread()
                raise LiteSkillAdminError(
                    f"LiteSkill 接口返回意外 3xx 状态: {response.status_code} -> "
                    f"{response.headers.get('location', '')}",
                    status_code=response.status_code,
                    category=LiteSkillAdminErrorCategory.UPSTREAM,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload = self._safe_json(exc.response)
            raise LiteSkillAdminError(
                self._extract_msg(payload) or f"LiteSkill 接口请求失败: {exc.response.status_code}",
                status_code=exc.response.status_code,
                category=LiteSkillAdminErrorCategory.UPSTREAM,
                payload=payload,
            ) from exc
        except httpx.HTTPError as exc:
            raise LiteSkillAdminError(
                f"LiteSkill 网络请求失败: {exc}",
                category=LiteSkillAdminErrorCategory.TRANSPORT,
            ) from exc

        payload = self._safe_json(response)
        return self._unwrap(payload)

    def _build_headers(self, *, admin_id: str, request_id: str, lang: str | None) -> dict[str, str]:
        if not self.token:
            raise LiteSkillAdminError(
                "未配置 LITESKILL_INTERNAL_TOKEN，请检查环境变量",
                category=LiteSkillAdminErrorCategory.CONFIG,
            )
        headers = {"X-Internal-Token": self.token}
        if admin_id:
            headers["X-Admin-Id"] = admin_id
        if request_id:
            headers["X-Request-Id"] = request_id
        if lang:
            headers["Accept-Language"] = lang
        return headers

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"raw_text": response.text}

    @staticmethod
    def _extract_msg(payload: Any) -> str | None:
        if isinstance(payload, dict):
            return payload.get("msg") or payload.get("message")
        return None

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """解包 LiteSkill 统一信封 ``{code:"OK", msg, data}`` 取 data。

        HTTP 2xx 不代表业务成功：LiteSkill 可能在成功状态码下返回
        ``code != "OK"`` 的业务错误信封。此处先校验 ``code``，非 OK 时
        以信封中的 ``msg`` 抛出 :class:`LiteSkillAdminError`
        （category=BUSINESS，不代表服务不可用），避免调用方把错误数据
        当成功结果消费。
        """
        if isinstance(payload, dict) and "code" in payload:
            code = payload.get("code")
            if code != "OK":
                raise LiteSkillAdminError(
                    LiteSkillAdminClient._extract_msg(payload)
                    or f"LiteSkill 接口返回业务错误: {code}",
                    payload=payload,
                    category=LiteSkillAdminErrorCategory.BUSINESS,
                )
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload
