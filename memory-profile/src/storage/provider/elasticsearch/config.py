import os
from typing import Any
from urllib.parse import urlparse

from src.bootstrap import get_settings


def build_elasticsearch_client_config() -> dict[str, Any]:
    settings = get_settings()
    raw_host = settings.elasticsearch_host.strip()
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
    port = parsed.port or settings.elasticsearch_port
    config: dict[str, Any] = {
        "hosts": [f"{scheme}://{formatted_hostname}:{port}"],
        "basic_auth": (
            settings.elasticsearch_username,
            settings.elasticsearch_password,
        ),
        "request_timeout": settings.elasticsearch_request_timeout_seconds,
        "retry_on_timeout": settings.elasticsearch_retry_on_timeout,
        "max_retries": settings.elasticsearch_max_retries,
        "connections_per_node": settings.elasticsearch_connections_per_node
        or int(os.getenv("ELASTICSEARCH_CONNECTIONS_PER_NODE", "10")),
    }
    if scheme == "https":
        config["verify_certs"] = settings.elasticsearch_verify_certs
        if settings.elasticsearch_ca_certs:
            config["ca_certs"] = settings.elasticsearch_ca_certs
    return config
