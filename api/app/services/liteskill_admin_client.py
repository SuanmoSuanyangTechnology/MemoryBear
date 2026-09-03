"""LiteSkill（MemorySkills）内部管理接口客户端。

供运营后台 ``/sys/memory-lite/*`` 适配层调用 LiteSkill 的
``/internal/admin/memory-lite/*`` 只读接口。携带服务凭证
``X-Internal-Token`` 以证明"请求来自 Enterprise"，并透传 admin_id / request_id
用于链路追踪。参照 ``speedbear_gateway_client`` 的错误映射范式。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class LiteSkillAdminError(Exception):
    """LiteSkill 内部接口调用异常。"""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class LiteSkillAdminClient:
    """调用 LiteSkill 内部只读管理接口。"""

    def __init__(self) -> None:
        self.base_url = settings.LITESKILL_BASE_URL.rstrip("/")
        self.token = settings.LITESKILL_INTERNAL_TOKEN
        self.timeout = settings.LITESKILL_TIMEOUT

    # ── 业务便捷方法 ──────────────────────────────────────────────
    def overview(self, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return self._get("/internal/admin/memory-lite/overview", lang=lang, admin_id=admin_id, request_id=request_id)

    def users(
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
        return self._get(
            "/internal/admin/memory-lite/users",
            params=params,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    def user_detail(self, account_id: str, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return self._get(
            f"/internal/admin/memory-lite/users/{account_id}",
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    def products(self, *, lang: str | None = None, admin_id: str = "", request_id: str = "") -> Any:
        return self._get("/internal/admin/memory-lite/products", lang=lang, admin_id=admin_id, request_id=request_id)

    def update_product(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        """修改预设包（POST，目标 id 在 body 内）。"""
        return self._request(
            "POST",
            "/internal/admin/memory-lite/products/update",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    def create_product(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        return self._request(
            "POST",
            "/internal/admin/memory-lite/products/create",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    def recharge_write_quota(
        self,
        body: dict[str, Any],
        *,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        """给指定端用户加写入次数（POST，end_user_id + count 在 body 内）。"""
        return self._request(
            "POST",
            "/internal/admin/memory-lite/write-quota/recharge",
            json=body,
            lang=lang,
            admin_id=admin_id,
            request_id=request_id,
        )

    # ── 底层请求 ──────────────────────────────────────────────────
    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        lang: str | None = None,
        admin_id: str = "",
        request_id: str = "",
    ) -> Any:
        return self._request(
            "GET", path, params=params, lang=lang, admin_id=admin_id, request_id=request_id
        )

    def _request(
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
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.request(
                    method.upper(), url, headers=headers, params=request_params, json=json
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload = self._safe_json(exc.response)
            raise LiteSkillAdminError(
                self._extract_msg(payload) or f"LiteSkill 接口请求失败: {exc.response.status_code}",
                status_code=exc.response.status_code,
                payload=payload,
            ) from exc
        except httpx.HTTPError as exc:
            raise LiteSkillAdminError(f"LiteSkill 网络请求失败: {exc}") from exc

        payload = self._safe_json(response)
        return self._unwrap(payload)

    def _build_headers(self, *, admin_id: str, request_id: str, lang: str | None) -> dict[str, str]:
        if not self.token:
            raise LiteSkillAdminError("未配置 LITESKILL_INTERNAL_TOKEN")
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
        """解包 LiteSkill 统一信封 ``{code:"OK", msg, data}`` 取 data。"""
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload
