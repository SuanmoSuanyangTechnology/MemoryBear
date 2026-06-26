from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
class SpeedBearGatewayError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class SpeedBearGatewayClient:
    def __init__(self) -> None:
        self.base_url = settings.SPEEDBEAR_BASE_URL.rstrip("/")
        self.auth_key = settings.SPEEDBEAR_AUTH_KEY
        self.timeout = settings.SPEEDBEAR_TIMEOUT

    def list_gateway_models(self) -> tuple[list[Any] | Any, int | Any]:
        data = self._request("GET", "/api/openapi/v1/models")
        payload = self._unwrap_dict(data)
        return payload.get("items") or [], payload.get("total") or 0

    def list_model_pricing_groups(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/openapi/v1/models/model_pricing_group")
        return self._unwrap_list(data)

    def get_default_pricing_group_id(self) -> str:
        groups = self.list_model_pricing_groups()
        for item in groups:
            if item.get("is_default") is True or item.get("default") is True:
                group_id = item.get("id")
                if group_id:
                    return str(group_id)
        for item in groups:
            if str(item.get("name") or "").strip().lower() == "default":
                group_id = item.get("id")
                if group_id:
                    return str(group_id)
        raise SpeedBearGatewayError("未找到默认 pricing_group_id")

    def create_tenant_api_key(
        self,
        tenant_name: str,
        *,
        pricing_group_id: str,
        quota_limit: float | int,
    ) -> dict[str, Any]:
        payload = {
            "name": tenant_name,
            "pricing_group_id": pricing_group_id,
            "model_scope": "all",
            "disabled_models": [],
            "ip_whitelist": [],
            "remark": None,
            "quota_limit": quota_limit,
            "expires_at": None,
        }
        data = self._request("POST", "/api/openapi/v1/api-keys", json=payload)
        return self._unwrap_dict(data)

    def get_tenant_account_stats(
        self,
        *,
        gateway_api_key_id: str,
    ) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/api/openapi/v1/api-keys/{gateway_api_key_id}/account-summary",
        )
        return self._unwrap_dict(data)

    def update_tenant_api_key_quota_limit(
        self,
        *,
        gateway_api_key_id: str,
        quota_limit: float | int | str,
    ) -> dict[str, Any]:
        data = self._request(
            "PUT",
            f"/api/openapi/v1/api-keys/{gateway_api_key_id}",
            json={"quota_limit": quota_limit},
        )
        return self._unwrap_dict(data)

    def get_tenant_billing_page(
        self,
        *,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/api/openapi/v1/request-logs/consumption-details",
            params=query,
        )
        return self._unwrap_dict(data)

    def get_statistics(
        self,
        *,
        query: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/api/openapi/v1/request-logs/statistics",
            params=query,
        )
        return self._unwrap_list(data)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers, cookies = self._build_auth()
        request_params = dict(params or {})

        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True, cookies=cookies) as client:
                response = client.request(
                    method.upper(),
                    url,
                    headers=headers,
                    params=request_params,
                    json=json,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload = self._safe_json(exc.response)
            raise SpeedBearGatewayError(
                f"SpeedBear 接口请求失败: {exc.response.status_code}",
                status_code=exc.response.status_code,
                payload=payload,
            ) from exc
        except httpx.HTTPError as exc:
            raise SpeedBearGatewayError(f"SpeedBear 网络请求失败: {exc}") from exc

        payload = self._safe_json(response)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise SpeedBearGatewayError(
                payload.get("msg") or "SpeedBear 业务请求失败",
                status_code=response.status_code,
                payload=payload,
            )
        return payload

    def _build_auth(self) -> tuple[dict[str, str], dict[str, str]]:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}

        if not self.auth_key:
            raise SpeedBearGatewayError("未配置 SPEEDBEAR_AUTH_KEY")
        headers["Authorization"] = f"Bearer {self.auth_key}"
        return headers, cookies

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"raw_text": response.text}

    @staticmethod
    def _unwrap_dict(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            data = payload.get("data", payload)
            if isinstance(data, dict):
                return data
        return {"raw": payload}

    @staticmethod
    def _unwrap_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            data = payload.get("data", payload)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                items = data.get("items")
                if isinstance(items, list):
                    return items
        if isinstance(payload, list):
            return payload
        return []
