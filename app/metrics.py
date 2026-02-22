"""
Monitoring and metrics module for ADK Middleware.
Provides Prometheus-compatible metrics and usage statistics.
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    request_id: str
    tenant_id: str
    session_id: str
    model: str
    start_time: float
    end_time: Optional[float] = None
    status: str = "pending"  # pending, success, error
    error_type: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    is_streaming: bool = False
    content_types: List[str] = field(default_factory=list)


class MetricsCollector:
    """Collects and aggregates metrics."""

    def __init__(self, retention_hours: int = 24):
        self.retention_hours = retention_hours
        self._requests: List[RequestMetrics] = []
        self._lock = asyncio.Lock()

        # Aggregated counters
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0

        # Tenant-specific counters
        self._tenant_requests: Dict[str, int] = {}
        self._tenant_tokens: Dict[str, int] = {}

        # Model-specific counters
        self._model_requests: Dict[str, int] = {}
        self._model_tokens: Dict[str, int] = {}

        # Error counters
        self._errors_by_type: Dict[str, int] = {}

        # Content type counters
        self._content_types: Dict[str, int] = {}

    async def start_request(
        self,
        request_id: str,
        tenant_id: str,
        session_id: str,
        model: str,
        is_streaming: bool = False
    ) -> RequestMetrics:
        """Record the start of a request."""
        metrics = RequestMetrics(
            request_id=request_id,
            tenant_id=tenant_id,
            session_id=session_id,
            model=model,
            start_time=time.time(),
            is_streaming=is_streaming
        )

        async with self._lock:
            self._requests.append(metrics)
            self._total_requests += 1

            # Update tenant counter
            self._tenant_requests[tenant_id] = self._tenant_requests.get(tenant_id, 0) + 1

            # Update model counter
            self._model_requests[model] = self._model_requests.get(model, 0) + 1

        return metrics

    async def end_request(
        self,
        metrics: RequestMetrics,
        success: bool = True,
        error_type: str = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        content_types: List[str] = None
    ):
        """Record the end of a request."""
        metrics.end_time = time.time()
        metrics.status = "success" if success else "error"
        metrics.error_type = error_type
        metrics.input_tokens = input_tokens
        metrics.output_tokens = output_tokens
        metrics.latency_ms = int((metrics.end_time - metrics.start_time) * 1000)
        metrics.content_types = content_types or []

        async with self._lock:
            if success:
                self._successful_requests += 1
            else:
                self._failed_requests += 1
                if error_type:
                    self._errors_by_type[error_type] = self._errors_by_type.get(error_type, 0) + 1

            # Update token counters
            total_tokens = input_tokens + output_tokens
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._total_latency_ms += metrics.latency_ms

            # Update tenant tokens
            self._tenant_tokens[metrics.tenant_id] = self._tenant_tokens.get(metrics.tenant_id, 0) + total_tokens

            # Update model tokens
            self._model_tokens[metrics.model] = self._model_tokens.get(metrics.model, 0) + total_tokens

            # Update content type counters
            for ct in metrics.content_types:
                self._content_types[ct] = self._content_types.get(ct, 0) + 1

    async def cleanup_old_requests(self):
        """Remove requests older than retention period."""
        cutoff = time.time() - (self.retention_hours * 3600)

        async with self._lock:
            original_count = len(self._requests)
            self._requests = [r for r in self._requests if r.start_time >= cutoff]
            removed = original_count - len(self._requests)

            if removed > 0:
                logger.debug(f"Cleaned up {removed} old request records")

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        # Calculate rates
        avg_latency = (
            self._total_latency_ms / self._successful_requests
            if self._successful_requests > 0 else 0
        )

        success_rate = (
            self._successful_requests / self._total_requests * 100
            if self._total_requests > 0 else 0
        )

        return {
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "success_rate_percent": round(success_rate, 2),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
            "average_latency_ms": round(avg_latency, 2),
            "by_tenant": dict(self._tenant_requests),
            "by_model": dict(self._model_requests),
            "errors_by_type": dict(self._errors_by_type),
            "content_types": dict(self._content_types),
        }

    def get_tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Get statistics for a specific tenant."""
        return {
            "tenant_id": tenant_id,
            "requests": self._tenant_requests.get(tenant_id, 0),
            "tokens": self._tenant_tokens.get(tenant_id, 0),
        }

    def get_recent_requests(self, limit: int = 100, tenant_id: str = None) -> List[Dict]:
        """Get recent requests."""
        requests = sorted(self._requests, key=lambda r: r.start_time, reverse=True)

        if tenant_id:
            requests = [r for r in requests if r.tenant_id == tenant_id]

        return [
            {
                "request_id": r.request_id,
                "tenant_id": r.tenant_id,
                "session_id": r.session_id,
                "model": r.model,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "is_streaming": r.is_streaming,
                "timestamp": datetime.fromtimestamp(r.start_time).isoformat(),
            }
            for r in requests[:limit]
        ]

    def get_prometheus_metrics(self) -> str:
        """Generate Prometheus-compatible metrics."""
        lines = []

        # Request counters
        lines.append("# HELP adk_requests_total Total number of requests")
        lines.append("# TYPE adk_requests_total counter")
        lines.append(f"adk_requests_total {self._total_requests}")

        lines.append("# HELP adk_requests_successful Total successful requests")
        lines.append("# TYPE adk_requests_successful counter")
        lines.append(f"adk_requests_successful {self._successful_requests}")

        lines.append("# HELP adk_requests_failed Total failed requests")
        lines.append("# TYPE adk_requests_failed counter")
        lines.append(f"adk_requests_failed {self._failed_requests}")

        # Token counters
        lines.append("# HELP adk_tokens_input Total input tokens")
        lines.append("# TYPE adk_tokens_input counter")
        lines.append(f"adk_tokens_input {self._total_input_tokens}")

        lines.append("# HELP adk_tokens_output Total output tokens")
        lines.append("# TYPE adk_tokens_output counter")
        lines.append(f"adk_tokens_output {self._total_output_tokens}")

        # Latency
        avg_latency = (
            self._total_latency_ms / self._successful_requests
            if self._successful_requests > 0 else 0
        )
        lines.append("# HELP adk_latency_ms_average Average request latency in milliseconds")
        lines.append("# TYPE adk_latency_ms_average gauge")
        lines.append(f"adk_latency_ms_average {avg_latency:.2f}")

        # By model
        lines.append("# HELP adk_requests_by_model Requests by model")
        lines.append("# TYPE adk_requests_by_model counter")
        for model, count in self._model_requests.items():
            model_safe = model.replace("-", "_").replace(".", "_")
            lines.append(f'adk_requests_by_model{{model="{model_safe}"}} {count}')

        # By tenant
        lines.append("# HELP adk_requests_by_tenant Requests by tenant")
        lines.append("# TYPE adk_requests_by_tenant counter")
        for tenant, count in self._tenant_requests.items():
            lines.append(f'adk_requests_by_tenant{{tenant="{tenant}"}} {count}')

        # Errors
        lines.append("# HELP adk_errors_by_type Errors by type")
        lines.append("# TYPE adk_errors_by_type counter")
        for error_type, count in self._errors_by_type.items():
            error_safe = error_type.replace("-", "_").replace(" ", "_")
            lines.append(f'adk_errors_by_type{{type="{error_safe}"}} {count}')

        return "\n".join(lines)


class CostEstimator:
    """Estimate costs based on token usage."""

    # Cost per 1K tokens (approximate, in USD)
    PRICING = {
        "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
        "gemini-1.0-pro": {"input": 0.0005, "output": 0.0015},
        "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
        "default": {"input": 0.0001, "output": 0.0004},  # Default pricing
    }

    @classmethod
    def estimate_cost(cls, model: str, input_tokens: int, output_tokens: int) -> Dict[str, float]:
        """Estimate cost for a request."""
        pricing = cls.PRICING.get(model, cls.PRICING["default"])

        input_cost = (input_tokens / 1000) * pricing["input"]
        output_cost = (output_tokens / 1000) * pricing["output"]
        total_cost = input_cost + output_cost

        return {
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "total_cost_usd": round(total_cost, 6),
        }


# Global metrics collector
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector
