from typing import Any

from app.core.moderation.base import ModerationBase


class ModerationFactory:
    _registry: dict[str, type[ModerationBase]] = {}

    @classmethod
    def register(cls, name: str, impl_class: type[ModerationBase]) -> None:
        cls._registry[name] = impl_class

    @classmethod
    def create(
        cls,
        moderation_type: str,
        app_id: str,
        tenant_id: str,
        config: dict[str, Any],
    ) -> ModerationBase:
        impl_class = cls._registry.get(moderation_type)
        if impl_class is None:
            raise ValueError(f"Unknown moderation type: {moderation_type}")
        return impl_class(app_id=app_id, tenant_id=tenant_id, config=config)

    @classmethod
    def validate_config(cls, moderation_type: str, config: dict[str, Any]) -> None:
        impl_class = cls._registry.get(moderation_type)
        if impl_class is None:
            raise ValueError(f"Unknown moderation type: {moderation_type}")
        impl_class.validate_config(config)


def _register_builtins():
    from app.core.moderation.keywords.keywords import KeywordsModeration

    ModerationFactory.register("keywords", KeywordsModeration)


_register_builtins()
