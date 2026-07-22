"""auditsys: modular system audit / blame tool.

Collectors normalize data from auditd / journalctl / docker / fs-stat into a
single sqlite event store. CLI and TUI both query that store — neither talks
to the raw system logs directly.
"""

__version__ = "0.2.0"
