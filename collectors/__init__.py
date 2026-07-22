from .base import Collector
from .journalctl_collector import JournalctlCollector
from .auditd_collector import AuditdCollector
from .docker_collector import DockerCollector
from .stat_collector import StatCollector

ALL_COLLECTORS = {
    "journalctl": JournalctlCollector,
    "auditd": AuditdCollector,
    "docker": DockerCollector,
    "stat": StatCollector,
}

__all__ = ["Collector", "ALL_COLLECTORS"]
