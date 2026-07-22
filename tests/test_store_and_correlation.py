import tempfile
from pathlib import Path

from auditsys.models import Event
from auditsys.store import Store
from auditsys.cli import _correlate_docker


def test_store_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = Store(Path(d) / "test.db")
        e = Event(ts="2026-07-22T10:00:00+00:00", source="auditd", category="file",
                   action="delete", actor="alice", uid=1000, target="/etc/x", host="h1",
                   detail={"foo": "bar"})
        n = store.insert_events([e])
        assert n == 1
        results = store.query(target_like="/etc/x")
        assert len(results) == 1
        assert results[0].actor == "alice"
        assert results[0].detail == {"foo": "bar"}
        store.close()


def test_cursor_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        store = Store(Path(d) / "test.db")
        assert store.get_cursor("auditd", "h1") is None
        store.set_cursor("auditd", "h1", "2026-07-22T10:00:00+00:00")
        assert store.get_cursor("auditd", "h1") == "2026-07-22T10:00:00+00:00"
        store.close()


def test_correlate_docker_joins_actor_onto_daemon_event():
    exec_event = Event(ts="2026-07-22T10:00:00+00:00", source="auditd", category="docker",
                        action="docker_exec", actor="alice", target="docker rm -f web", host="h1")
    api_event = Event(ts="2026-07-22T10:00:02+00:00", source="docker", category="docker",
                       action="docker_api", actor=None, target="container:web destroy", host="h1")
    result = _correlate_docker([exec_event, api_event], verbosity="normal")
    api_result = [e for e in result if e.source == "docker"][0]
    assert api_result.actor == "alice"


def test_correlate_docker_minimal_skips_join():
    exec_event = Event(ts="2026-07-22T10:00:00+00:00", source="auditd", category="docker",
                        action="docker_exec", actor="alice", target="docker rm -f web", host="h1")
    api_event = Event(ts="2026-07-22T10:00:02+00:00", source="docker", category="docker",
                       action="docker_api", actor=None, target="container:web destroy", host="h1")
    result = _correlate_docker([exec_event, api_event], verbosity="minimal")
    api_result = [e for e in result if e.source == "docker"][0]
    assert api_result.actor is None
