"""Prometheus metrics, served on METRICS_PORT; compose never publishes that port."""

from prometheus_client import Counter, Gauge, Histogram

RECORDS = Counter(
    "chatbot_worker_records", "Kafka records by recorded outcome.", ["outcome"]
)  # done | duplicate | dlq_malformed | dlq_dependency
RECORD_SECONDS = Histogram(
    "chatbot_worker_record_seconds",
    "Time to handle one record, lease wait included.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60, 120),
)
QUEUE_DELAY = Histogram(
    "chatbot_worker_queue_delay_seconds",
    "Gateway enqueue (envelope ts) to the worker owning the record.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
IN_FLIGHT = Gauge("chatbot_worker_in_flight", "Records being handled.")
RETRIES = Counter("chatbot_worker_retries", "Transient failures that were retried.")
