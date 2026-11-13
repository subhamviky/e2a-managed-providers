"""Telemetry through OpenTelemetry, shipped to each cloud's managed backend.

Every cloud's managed telemetry accepts OTLP (AWS: ADOT collector -> CloudWatch / X-Ray;
Azure: Azure Monitor OpenTelemetry -> Application Insights; GCP: Cloud Trace / Cloud
Logging OTLP endpoints). So one BaseObservability subclass serves all of them; a cloud
subclass only adds its resource attributes. Without the OTel SDK installed (CI, offline
runs) it falls back to structured logging, never to a no-op.
"""
import json
import logging
from typing import Any, Dict, FrozenSet, List, Optional

from e2a import BaseObservability, resolve_config

from . import config_keys  # noqa: F401
from .capabilities import Capability

log = logging.getLogger("e2a.managed.telemetry")


class ManagedObservability(BaseObservability):
    capabilities: FrozenSet[str] = frozenset({Capability.TELEMETRY})
    cloud_resource: Dict[str, str] = {}

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = dict(config or {})
        self.service_name = resolve_config("otel_service_name", self.config)
        self._tracer = self._meter = None
        try:                                           # optional dependency
            from opentelemetry import metrics, trace
            self._tracer, self._meter = trace.get_tracer("e2a"), metrics.get_meter("e2a")
        except ImportError:
            pass
        self.shipped: List[dict] = []                  # last batch, for tests and debugging

    def _resource(self) -> Dict[str, str]:
        return {"service.name": self.service_name, **self.cloud_resource}

    def _ship_logs(self, enriched_logs, config, **kwargs) -> None:
        self.shipped = list(enriched_logs)
        for entry in enriched_logs:
            log.log(logging.ERROR if entry.get("level") == "ERROR" else logging.INFO,
                    json.dumps({**self._resource(), **entry}, default=str))

    def _emit_metrics(self, metrics, config, **kwargs) -> None:
        if self._meter is None:
            log.info(json.dumps({"metrics": metrics, **self._resource()}))
            return
        for name, value in metrics.items():
            self._meter.create_counter(f"e2a.{name}").add(value, {"service": self.service_name})

    def _export_traces(self, correlation_id, logs, config, **kwargs) -> None:
        if self._tracer is None:
            return
        with self._tracer.start_as_current_span("e2a.request") as span:
            span.set_attribute("e2a.correlation_id", correlation_id)
            span.set_attribute("e2a.tenant_id", str(kwargs.get("tenant_id", "")))
            for entry in logs:
                span.add_event(str(entry.get("event", "log")),
                               {k: str(v) for k, v in entry.items() if k not in ("event",)})
