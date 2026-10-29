#!/bin/sh
# Runs once on first start of the audit database. The table is owned by the admin role; the exporter
# connects as audit_writer, which can INSERT and SELECT but never UPDATE, DELETE or TRUNCATE.
set -eu
psql -v ON_ERROR_STOP=1 -v writer="$AUDIT_WRITER_USER" -v pw="$AUDIT_WRITER_PASSWORD" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE TABLE audit_log (
    id          BIGSERIAL   PRIMARY KEY,
    stream_id   TEXT        NOT NULL UNIQUE,
    ts          TIMESTAMPTZ NOT NULL,
    event       TEXT        NOT NULL,
    actor       TEXT        NOT NULL,
    thread_id   TEXT        NOT NULL,
    details     JSONB       NOT NULL,
    exported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_thread_ts ON audit_log (thread_id, ts DESC);
CREATE INDEX audit_log_event_ts ON audit_log (event, ts DESC);
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'writer', :'pw') \gexec
SELECT format('GRANT SELECT, INSERT ON audit_log TO %I', :'writer') \gexec
SELECT format('GRANT USAGE ON SEQUENCE audit_log_id_seq TO %I', :'writer') \gexec
SQL
