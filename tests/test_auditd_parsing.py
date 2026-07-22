import pwd
from collections import namedtuple

import pytest

from auditsys.collectors.auditd_collector import AuditdCollector

PwEntry = namedtuple("PwEntry", ["pw_name"])
FAKE_USERS = {1000: "alice", 0: "root"}


@pytest.fixture(autouse=True)
def fake_pwd(monkeypatch):
    def fake_getpwuid(uid):
        if uid in FAKE_USERS:
            return PwEntry(FAKE_USERS[uid])
        raise KeyError(uid)
    monkeypatch.setattr(pwd, "getpwuid", fake_getpwuid)


def make_collector():
    return AuditdCollector(config={}, host="testhost")


SAMPLE_DOCKER_EXECVE = """\
----
type=PROCTITLE msg=audit(07/22/2026 10:15:00.123:456) : proctitle=docker system prune -af
type=PATH msg=audit(07/22/2026 10:15:00.123:456) : item=0 name=/usr/bin/docker inode=1234 mode=file,755
type=CWD msg=audit(07/22/2026 10:15:00.123:456) :  cwd=/home/alice
type=EXECVE msg=audit(07/22/2026 10:15:00.123:456) : argc=4 a0=docker a1=system a2=prune a3=-af
type=SYSCALL msg=audit(07/22/2026 10:15:00.123:456) : arch=c000003e syscall=execve success=yes exit=0 ppid=1234 pid=5678 auid=1000 uid=0 gid=0 euid=0 tty=pts0 ses=3 comm=docker exe=/usr/bin/docker key=auditsys
----
"""

SAMPLE_FILE_DELETE = """\
----
type=PATH msg=audit(07/22/2026 11:00:00.000:789) : item=0 name=/etc/important.conf inode=42 mode=file,644
type=CWD msg=audit(07/22/2026 11:00:00.000:789) :  cwd=/etc
type=SYSCALL msg=audit(07/22/2026 11:00:00.000:789) : arch=c000003e syscall=unlink success=yes exit=0 ppid=1 pid=99 auid=1000 uid=1000 gid=1000 euid=1000 tty=pts1 ses=4 comm=rm exe=/usr/bin/rm key=auditsys
----
"""


def test_group_records_splits_on_dashes():
    c = make_collector()
    groups = c._group_records(SAMPLE_DOCKER_EXECVE + SAMPLE_FILE_DELETE)
    assert len(groups) == 2


def test_docker_execve_attributes_real_login_user_through_root():
    """The core incident-response property: `docker ...` run as root (via
    sudo/su) still attributes back to auid=1000 (alice), not uid=0 (root)."""
    c = make_collector()
    groups = c._group_records(SAMPLE_DOCKER_EXECVE)
    event = c._group_to_event(groups[0])
    assert event is not None
    assert event.category == "docker"
    assert event.action == "docker_exec"
    assert event.actor == "alice"  # from auid, not "root" from uid
    assert event.target == "docker system prune -af"


def test_file_delete_attribution():
    c = make_collector()
    groups = c._group_records(SAMPLE_FILE_DELETE)
    event = c._group_to_event(groups[0])
    assert event is not None
    assert event.category == "file"
    assert event.action == "delete"
    assert event.actor == "alice"
    assert event.target == "/etc/important.conf"
