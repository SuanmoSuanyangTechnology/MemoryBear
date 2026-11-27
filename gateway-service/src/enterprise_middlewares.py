"""Enterprise middleware hook (community build: no-op).

The enterprise image ships enterprise_ext.gateway_middlewares, which adds
CORS then License around the gateway pipeline. Community builds lack the
package and start unchanged. GATEWAY_ENTERPRISE_MIDDLEWARES=false is the
explicit rollback switch.
"""
import logging
import os

from . import config
from . import redis as gredis

logger = logging.getLogger(__name__)

_DEFAULT_LICENSE_FILE = "/etc/app/license.json"
_DEFAULT_LICENSE_PUBLIC_KEY = "/etc/app/license_public.pem"


def install_enterprise_middlewares(app) -> None:
    if not config.settings.enterprise_middlewares_enabled:
        logger.warning(
            "enterprise middlewares disabled by GATEWAY_ENTERPRISE_MIDDLEWARES")
        return
    try:
        from enterprise_ext.gateway_middlewares import install_middlewares
    except ModuleNotFoundError as exc:
        # Only a missing enterprise_ext package means "community build": any
        # other module failing to import inside the enterprise package is a
        # broken enterprise image and must fail loud (CLAUDE.md 0.4).
        if not (exc.name or "").startswith("enterprise_ext"):
            raise
        return
    install_middlewares(
        app,
        redis_getter=lambda: gredis.redis,
        license_file=os.getenv("LICENSE_FILE", _DEFAULT_LICENSE_FILE),
        license_public_key_path=os.getenv(
            "LICENSE_PUBLIC_KEY_PATH", _DEFAULT_LICENSE_PUBLIC_KEY),
        static_origins=config.settings.cors_static_origins,
        cors_snapshot_key=config.settings.cors_snapshot_key,
        cors_cache_ttl_s=config.settings.cors_cache_ttl_s,
    )
