from typing import Any

from neo4j import basic_auth

from app.core.config import settings


def build_neo4j_driver_config() -> dict[str, Any]:
    password = settings.NEO4J_PASSWORD
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD is not set. Create a .env with NEO4J_PASSWORD "
            "or export it before running."
        )

    return {
        "uri": settings.NEO4J_URI,
        "auth": basic_auth(settings.NEO4J_USERNAME, password),
        "max_connection_pool_size": settings.NEO4J_MAX_POOL_SIZE,
        "connection_acquisition_timeout": settings.NEO4J_ACQ_TIMEOUT,
        "max_connection_lifetime": settings.NEO4J_MAX_CONN_LIFETIME,
        "connection_timeout": settings.NEO4J_CONN_TIMEOUT,
    }
