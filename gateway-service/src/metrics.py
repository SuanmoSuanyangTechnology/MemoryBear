"""Prometheus 指标（评审稿 4.1.4 指标清单）。"""
from prometheus_client import Counter, Gauge

gateway_forward_requests_total = Counter(
    "gateway_forward_requests_total", "Forwarded requests by target/status class",
    ["target", "status_class"])
gateway_forward_retries_total = Counter(
    "gateway_forward_retries_total", "Forward retries", ["target", "method"])
gateway_circuit_breaker_state = Gauge(
    "gateway_circuit_breaker_state", "Circuit breaker state (1=open)", ["target"])
gateway_upstream_errors_total = Counter(
    "gateway_upstream_errors_total", "Upstream errors", ["target", "error_type"])
gateway_streaming_active_connections = Gauge(
    "gateway_streaming_active_connections", "Active streaming connections")
gateway_route_reload_total = Counter(
    "gateway_route_reload_total", "Route file reload attempts", ["result"])
gateway_route_table_size = Gauge(
    "gateway_route_table_size", "Routes in the active resolver")
