import os
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings


def build_elasticsearch_client_config() -> dict[str, Any]:
    raw_host = settings.ELASTICSEARCH_HOST.strip()
    normalized_host = (
        raw_host if "://" in raw_host else f"https://{raw_host}"
    )
    parsed = urlparse(normalized_host)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError(
            "ELASTICSEARCH_HOST must be a valid http or https URL"
        )
    scheme = parsed.scheme
    hostname = parsed.hostname
    formatted_hostname = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port or settings.ELASTICSEARCH_PORT
    config: dict[str, Any] = {
        "hosts": [f"{scheme}://{formatted_hostname}:{port}"],
        "basic_auth": (
            settings.ELASTICSEARCH_USERNAME,
            settings.ELASTICSEARCH_PASSWORD,
        ),
        "request_timeout": settings.ELASTICSEARCH_REQUEST_TIMEOUT,
        "retry_on_timeout": settings.ELASTICSEARCH_RETRY_ON_TIMEOUT,
        "max_retries": settings.ELASTICSEARCH_MAX_RETRIES,
        "connections_per_node": int(
            os.getenv("ELASTICSEARCH_CONNECTIONS_PER_NODE", "10")
        ),
    }
    if scheme == "https":
        config["verify_certs"] = settings.ELASTICSEARCH_VERIFY_CERTS
        if settings.ELASTICSEARCH_CA_CERTS:
            config["ca_certs"] = settings.ELASTICSEARCH_CA_CERTS
    return config
