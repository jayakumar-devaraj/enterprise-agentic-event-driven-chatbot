"""Prometheus metrics, served on METRICS_PORT; compose never publishes that port."""

from prometheus_client import Counter, Gauge, Histogram

RECORDS = Counter(
    "chatbot_worker_records", "Kafka records by recorded outcome.", ["outcome"]
)  # done | duplicate | dlq_malformed | dlq_dependency | dlq_internal
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
# The model server has no metrics of its own (Ollama), so its client measures it.
LLM_CALLS = Counter(
    "chatbot_worker_llm_calls", "Model-server calls by kind and outcome.", ["call", "outcome"]
)  # call: stream | condense; outcome: ok | transient | error | cancelled
LLM_FIRST_TOKEN = Histogram(
    "chatbot_worker_llm_first_token_seconds",
    "Streamed call start to the first content delta (prompt processing on the server).",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
LLM_SECONDS = Histogram(
    "chatbot_worker_llm_call_seconds",
    "Model-server call start to its end, whatever the outcome.",
    ["call"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
