"""
RAAH Lightweight Operational Metrics Engine
===========================================

Provides a high-performance, thread-safe in-memory metrics collector for
operational throughput, latency histograms, error rates, and queue telemetry.
Zero heavyweight external dependencies.
"""

import time
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


class MetricsCollector:
    """
    Thread-safe operational telemetry and metrics aggregator.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = datetime.now(timezone.utc).isoformat()

        # HTTP Request metrics
        self._http_requests_total: int = 0
        self._http_responses_by_status: Dict[int, int] = {}
        self._http_latency_sum_ms: float = 0.0
        self._http_latency_count: int = 0
        self._http_recent_latencies: List[float] = []

        # Dispatch execution metrics
        self._dispatch_calls_total: int = 0
        self._dispatch_latency_sum_ms: float = 0.0
        self._dispatch_errors_total: int = 0

        # Persistence & Checkpoint metrics
        self._checkpoints_total: int = 0
        self._checkpoint_duration_sum_ms: float = 0.0
        self._persistence_errors_total: int = 0

        # System failures & retries
        self._worker_restarts_total: int = 0
        self._retries_total: int = 0

        # Real-time Event Stream metrics (M13 Phase 1)
        self._active_stream_connections: int = 0
        self._total_stream_connections: int = 0
        self._disconnects_total: int = 0
        self._reconnects_total: int = 0
        self._replayed_events_total: int = 0
        self._events_emitted_total: int = 0
        self._events_dropped_total: int = 0
        self._slow_clients_total: int = 0
        self._sequence_gaps_total: int = 0
        self._broadcast_latency_sum_ms: float = 0.0
        self._broadcast_count: int = 0
        self._heartbeats_total: int = 0

        # External Telemetry Ingestion metrics (M13.5 Phase 1)
        self._ingestion_events_total: int = 0
        self._ingestion_accepted_total: int = 0
        self._ingestion_duplicates_total: int = 0
        self._ingestion_rejected_total: int = 0
        self._ingestion_stale_total: int = 0
        self._ingestion_latency_sum_ms: float = 0.0
        self._ingestion_latency_count: int = 0

    def record_http_request(self, method: str, path: str, status_code: int, duration_ms: float):
        """Record an incoming HTTP request and response duration."""
        with self._lock:
            self._http_requests_total += 1
            self._http_responses_by_status[status_code] = (
                self._http_responses_by_status.get(status_code, 0) + 1
            )
            self._http_latency_sum_ms += duration_ms
            self._http_latency_count += 1

            self._http_recent_latencies.append(duration_ms)
            if len(self._http_recent_latencies) > 500:
                self._http_recent_latencies.pop(0)

    def record_dispatch(self, duration_ms: float, success: bool = True):
        """Record a dispatch incident calculation."""
        with self._lock:
            self._dispatch_calls_total += 1
            self._dispatch_latency_sum_ms += duration_ms
            if not success:
                self._dispatch_errors_total += 1

    def record_checkpoint(self, duration_ms: float, success: bool = True):
        """Record a durable state checkpoint operation."""
        with self._lock:
            self._checkpoints_total += 1
            self._checkpoint_duration_sum_ms += duration_ms
            if not success:
                self._persistence_errors_total += 1

    def record_persistence_error(self):
        """Record a persistence layer failure."""
        with self._lock:
            self._persistence_errors_total += 1

    def record_retry(self):
        """Record an automatic backoff retry attempt."""
        with self._lock:
            self._retries_total += 1

    def record_worker_restart(self):
        """Record an automated or manual worker restart."""
        with self._lock:
            self._worker_restarts_total += 1

    # Real-time Stream Telemetry Methods (M13 Phase 1)
    def record_stream_connected(self):
        """Record a new realtime subscriber connection."""
        with self._lock:
            self._active_stream_connections += 1
            self._total_stream_connections += 1

    def record_stream_disconnected(self):
        """Record a realtime subscriber disconnection."""
        with self._lock:
            if self._active_stream_connections > 0:
                self._active_stream_connections -= 1
            self._disconnects_total += 1

    def record_reconnect(self, replayed_events_count: int = 0):
        """Record a client reconnect and replayed events."""
        with self._lock:
            self._reconnects_total += 1
            self._replayed_events_total += replayed_events_count

    def record_event_emitted(self, event_type: str, duration_ms: float):
        """Record a successfully broadcasted event and distribution duration."""
        with self._lock:
            self._events_emitted_total += 1
            self._broadcast_latency_sum_ms += duration_ms
            self._broadcast_count += 1

    def record_event_dropped(self):
        """Record an event dropped due to subscriber queue saturation."""
        with self._lock:
            self._events_dropped_total += 1

    def record_slow_client(self):
        """Record a subscriber queue overflow / slow client incident."""
        with self._lock:
            self._slow_clients_total += 1

    def record_sequence_gap(self):
        """Record a sequence gap detection requiring snapshot recovery."""
        with self._lock:
            self._sequence_gaps_total += 1

    def record_heartbeat(self):
        """Record a keep-alive heartbeat emission."""
        with self._lock:
            self._heartbeats_total += 1

    def record_ingestion(self, status: str, duration_ms: float = 0.0):
        """Record external telemetry ingestion outcome (M13.5 Phase 1)."""
        with self._lock:
            self._ingestion_events_total += 1
            st = str(status).upper()
            if st == "ACCEPTED":
                self._ingestion_accepted_total += 1
            elif st == "DUPLICATE":
                self._ingestion_duplicates_total += 1
            elif st == "REJECTED":
                self._ingestion_rejected_total += 1
            elif st == "STALE":
                self._ingestion_stale_total += 1
            if duration_ms > 0:
                self._ingestion_latency_sum_ms += duration_ms
                self._ingestion_latency_count += 1

    def get_snapshot(self) -> Dict[str, Any]:
        """Generate a complete operational metrics snapshot dictionary."""
        with self._lock:
            mean_http_lat = (
                self._http_latency_sum_ms / self._http_latency_count
                if self._http_latency_count > 0
                else 0.0
            )
            mean_disp_lat = (
                self._dispatch_latency_sum_ms / self._dispatch_calls_total
                if self._dispatch_calls_total > 0
                else 0.0
            )
            mean_chk_lat = (
                self._checkpoint_duration_sum_ms / self._checkpoints_total
                if self._checkpoints_total > 0
                else 0.0
            )
            mean_bcast_lat = (
                self._broadcast_latency_sum_ms / self._broadcast_count
                if self._broadcast_count > 0
                else 0.0
            )
            mean_ingest_lat = (
                self._ingestion_latency_sum_ms / self._ingestion_latency_count
                if self._ingestion_latency_count > 0
                else 0.0
            )

            # Calculate p95 of recent HTTP latencies
            p95_http = 0.0
            if self._http_recent_latencies:
                sorted_lats = sorted(self._http_recent_latencies)
                idx = int(len(sorted_lats) * 0.95)
                p95_http = sorted_lats[min(idx, len(sorted_lats) - 1)]

            return {
                "uptime_started_at": self._started_at,
                "http": {
                    "requests_total": self._http_requests_total,
                    "responses_by_status": dict(self._http_responses_by_status),
                    "mean_latency_ms": round(mean_http_lat, 2),
                    "p95_latency_ms": round(p95_http, 2),
                },
                "dispatch": {
                    "calls_total": self._dispatch_calls_total,
                    "mean_latency_ms": round(mean_disp_lat, 2),
                    "errors_total": self._dispatch_errors_total,
                },
                "persistence": {
                    "checkpoints_total": self._checkpoints_total,
                    "mean_checkpoint_ms": round(mean_chk_lat, 2),
                    "errors_total": self._persistence_errors_total,
                },
                "resilience": {
                    "retries_total": self._retries_total,
                    "worker_restarts_total": self._worker_restarts_total,
                },
                "realtime_stream": {
                    "active_connections": self._active_stream_connections,
                    "total_connections": self._total_stream_connections,
                    "disconnects_total": self._disconnects_total,
                    "reconnects_total": self._reconnects_total,
                    "replayed_events_total": self._replayed_events_total,
                    "events_emitted_total": self._events_emitted_total,
                    "events_dropped_total": self._events_dropped_total,
                    "slow_clients_total": self._slow_clients_total,
                    "sequence_gaps_total": self._sequence_gaps_total,
                    "mean_broadcast_ms": round(mean_bcast_lat, 4),
                    "heartbeats_total": self._heartbeats_total,
                },
                "ingestion": {
                    "events_total": self._ingestion_events_total,
                    "accepted_total": self._ingestion_accepted_total,
                    "duplicates_total": self._ingestion_duplicates_total,
                    "rejected_total": self._ingestion_rejected_total,
                    "stale_total": self._ingestion_stale_total,
                    "mean_latency_ms": round(mean_ingest_lat, 2),
                },
            }


# Global singleton instance
metrics_collector = MetricsCollector()
