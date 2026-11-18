from typing import Any

from neo4j import basic_auth

from src.bootstrap import get_settings


def build_neo4j_driver_config() -> dict[str, Any]:
    settings = get_settings()
    password = settings.neo4j_password
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD is not set. Create a .env with NEO4J_PASSWORD "
            "or export it before running."
        )

    return {
        "uri": settings.neo4j_uri,
        "auth": basic_auth(settings.neo4j_username, password),
        "max_connection_pool_size": settings.neo4j_max_pool_size,
        "connection_acquisition_timeout": settings.neo4j_acquisition_timeout_seconds,
        "max_connection_lifetime": settings.neo4j_max_connection_lifetime_seconds,
        "connection_timeout": settings.neo4j_connection_timeout_seconds,
    }
