"""Row mapping for the audit exporter: faithful when well formed, lossless when not."""

from datetime import UTC, datetime

from audit.export import to_row
from chat_common.audit import AuditLog


def _details(row):
    return row[5].obj


def test_a_well_formed_entry_maps_field_for_field():
    fields = AuditLog.entry("refund_applied", actor="alice", thread_id="t-1", amount=12)
    row = to_row("1700000000000-0", fields)
    assert row[0] == "1700000000000-0"
    assert row[1] == datetime.fromisoformat(fields["ts"])
    assert row[2:5] == ("refund_applied", "alice", "t-1")
    assert _details(row) == {"amount": 12}


def test_a_missing_or_bad_timestamp_falls_back_to_the_stream_id_time():
    expected = datetime.fromtimestamp(1700000000, UTC)
    assert to_row("1700000000000-3", {"event": "e"})[1] == expected
    assert to_row("1700000000000-3", {"event": "e", "ts": "yesterday"})[1] == expected


def test_damaged_entries_keep_their_content_under_placeholders():
    row = to_row("1700000000000-0", {"details": "not json"})
    assert row[2:5] == ("unknown", "unknown", "unknown")
    assert _details(row) == {"unparsed": "not json"}
    assert _details(to_row("1700000000000-0", {"details": "[1, 2]"})) == {"unparsed": "[1, 2]"}
    assert _details(to_row("1700000000000-0", None)) == {}
