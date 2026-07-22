import tempfile
from pathlib import Path

from auditsys.models import Event
from auditsys.store import Store


def _mk_store(d):
    return Store(Path(d) / "test.db")


def test_recursive_matches_descendants_not_lookalikes():
    with tempfile.TemporaryDirectory() as d:
        store = _mk_store(d)
        events = [
            Event(ts="2026-07-22T10:00:00+00:00", source="auditd", category="file",
                  action="modify", actor="alice", target="/etc/nginx/nginx.conf", host="h1"),
            Event(ts="2026-07-22T10:00:01+00:00", source="auditd", category="file",
                  action="modify", actor="bob", target="/etc/passwd", host="h1"),
            Event(ts="2026-07-22T10:00:02+00:00", source="auditd", category="file",
                  action="modify", actor="carol", target="/etc2/unrelated.conf", host="h1"),
            Event(ts="2026-07-22T10:00:03+00:00", source="auditd", category="file",
                  action="modify", actor="dave", target="/etc", host="h1"),  # exact dir itself
        ]
        store.insert_events(events)

        recursive_results = store.query(target_path="/etc", recursive=True, category="file")
        targets = {e.target for e in recursive_results}
        assert targets == {"/etc/nginx/nginx.conf", "/etc/passwd", "/etc"}
        assert "/etc2/unrelated.conf" not in targets

        exact_results = store.query(target_path="/etc", recursive=False, category="file")
        assert {e.target for e in exact_results} == {"/etc"}
        store.close()


def test_list_actors_ordered_by_frequency():
    with tempfile.TemporaryDirectory() as d:
        store = _mk_store(d)
        events = [
            Event(ts="2026-07-22T10:00:00+00:00", source="auditd", category="file",
                  action="modify", actor="alice", target="/a", host="h1"),
            Event(ts="2026-07-22T10:00:01+00:00", source="auditd", category="file",
                  action="modify", actor="alice", target="/b", host="h1"),
            Event(ts="2026-07-22T10:00:02+00:00", source="auditd", category="file",
                  action="modify", actor="bob", target="/c", host="h1"),
        ]
        store.insert_events(events)
        actors = store.list_actors()
        assert actors[0] == "alice"
        assert "bob" in actors
        store.close()
