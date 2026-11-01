#!/bin/sh
# Runs a Kafka CLI tool against kafka:9092 authenticated as `chatbot` (ADR 0015):
#   sh /opt/chatbot/cli.sh kafka-topics.sh --list
# The password comes from KAFKA_PASSWORD in the container environment, never from a file in the repo.
set -e
props=$(mktemp)
trap 'rm -f "$props"' EXIT
printf 'security.protocol=SASL_PLAINTEXT\nsasl.mechanism=PLAIN\nsasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="chatbot" password="%s";\n' "$KAFKA_PASSWORD" > "$props"
tool=$1
shift
"/opt/kafka/bin/$tool" --bootstrap-server kafka:9092 --command-config "$props" "$@"
