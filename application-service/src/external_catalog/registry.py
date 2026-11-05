"""Resource-type adapter registry: one implementation per resource type."""

from __future__ import annotations

from ..errors import BizCode
from .errors import ExternalCatalogError
from .protocols import (
    DependencySpec,
    InstallContext,
    InstalledResource,
    PublishContext,
    ResourceInstallAdapter,
    ResourcePublishAdapter,
)


class AdapterRegistry:
    def __init__(self) -> None:
        self._publish: dict[str, ResourcePublishAdapter] = {}
        self._install: dict[str, ResourceInstallAdapter] = {}

    def register(self, adapter: object) -> None:
        resource_type = getattr(adapter, "resource_type", None)
        if not isinstance(resource_type, str) or not resource_type:
            raise ValueError("adapter must define a non-empty resource_type")
        if resource_type in self._publish:
            raise ValueError(f"adapter already registered: {resource_type}")
        self._publish[resource_type] = adapter  # type: ignore[assignment]
        self._install[resource_type] = adapter  # type: ignore[assignment]

    def contains(self, resource_type: str) -> bool:
        return resource_type in self._publish

    def get_publish(self, resource_type: str) -> ResourcePublishAdapter:
        adapter = self._publish.get(resource_type)
        if adapter is None:
            raise self._unsupported(resource_type)
        return adapter

    def get_install(self, resource_type: str) -> ResourceInstallAdapter:
        adapter = self._install.get(resource_type)
        if adapter is None:
            raise self._unsupported(resource_type)
        return adapter

    def get(self, resource_type: str) -> ResourcePublishAdapter:
        return self.get_publish(resource_type)

    def build_dependency(
        self, resource_type: str, ctx: PublishContext
    ) -> DependencySpec:
        return self.get_publish(resource_type).build_dependency(ctx)

    def install_dependency(
        self, resource_type: str, ctx: InstallContext
    ) -> InstalledResource:
        return self.get_install(resource_type).install_dependency(ctx)

    @staticmethod
    def _unsupported(resource_type: str) -> ExternalCatalogError:
        return ExternalCatalogError(
            BizCode.EXTERNAL_CATALOG_INVALID,
            "不支持的资源类型",
            data={"reason": "UNSUPPORTED_RESOURCE_TYPE"},
            context={"resource_type": resource_type},
        )


default_registry = AdapterRegistry()

__all__ = ["AdapterRegistry", "default_registry"]
