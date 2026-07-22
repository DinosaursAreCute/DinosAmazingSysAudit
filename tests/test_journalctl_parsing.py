from auditsys.collectors.journalctl_collector import JournalctlCollector


def make_collector():
    return JournalctlCollector(config={}, host="testhost")


def test_parse_sudo_success():
    c = make_collector()
    entry = {
        "__REALTIME_TIMESTAMP": "1753180800000000",
        "MESSAGE": "alice : TTY=pts/0 ; PWD=/home/alice ; USER=root ; "
                   "COMMAND=/usr/bin/docker system prune -af",
    }
    events = list(c._parse_sudo_entry(entry))
    assert len(events) == 1
    e = events[0]
    assert e.category == "sudo"
    assert e.action == "sudo_exec"
    assert e.actor == "alice"
    assert "docker system prune" in e.target
    assert e.detail["as_user"] == "root"


def test_parse_sudo_failure():
    c = make_collector()
    entry = {
        "__REALTIME_TIMESTAMP": "1753180800000000",
        "MESSAGE": "pam_unix(sudo:auth): authentication failure; "
                   "logname=bob uid=1001 euid=0 tty=/dev/pts/1 ruser=bob rhost=  user=bob",
    }
    events = list(c._parse_sudo_entry(entry))
    assert len(events) == 1
    assert events[0].action == "sudo_fail"
    assert events[0].actor == "bob"


def test_parse_ssh_accepted():
    c = make_collector()
    entry = {
        "__REALTIME_TIMESTAMP": "1753180800000000",
        "MESSAGE": "Accepted publickey for carol from 10.0.0.5 port 51234 ssh2: RSA ...",
    }
    events = list(c._parse_sshd_entry(entry))
    assert len(events) == 1
    assert events[0].action == "login"
    assert events[0].actor == "carol"
    assert events[0].target == "10.0.0.5"


def test_parse_ssh_failed():
    c = make_collector()
    entry = {
        "__REALTIME_TIMESTAMP": "1753180800000000",
        "MESSAGE": "Failed password for invalid user root from 203.0.113.9 port 4444 ssh2",
    }
    events = list(c._parse_sshd_entry(entry))
    assert len(events) == 1
    assert events[0].action == "login_fail"
    assert events[0].actor == "root"


def test_parse_ssh_disconnect():
    c = make_collector()
    entry = {
        "__REALTIME_TIMESTAMP": "1753180800000000",
        "MESSAGE": "Disconnected from user carol 10.0.0.5 port 51234",
    }
    events = list(c._parse_sshd_entry(entry))
    assert len(events) == 1
    assert events[0].action == "logout"
