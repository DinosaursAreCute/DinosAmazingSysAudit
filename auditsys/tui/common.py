"""Small shared helpers so every screen renders events/coverage consistently."""
from __future__ import annotations

from textual.widgets import DataTable, Tree
from textual.widgets.tree import TreeNode

from ..models import CoverageWindow, Event

EVENT_COLUMNS = ("Time", "Source", "Category", "Action", "Actor", "Target")


def setup_event_table(table: DataTable) -> None:
    table.clear(columns=True)
    table.add_columns(*EVENT_COLUMNS)
    table.cursor_type = "row"


def fill_event_table(table: DataTable, events: list[Event], max_rows: int = 1000) -> None:
    table.clear()
    for e in events[:max_rows]:
        table.add_row(e.ts, e.source, e.category, e.action, e.actor or "-", (e.target or "-")[:100])


def format_coverage(windows: list[CoverageWindow]) -> str:
    if not windows:
        return "No sync has run yet for this view — press [b]s[/] from the main menu, or run `audit-cli sync`."
    return "\n".join(
        f"• {w.source} ({w.host}): {w.start_ts or '?'} \u2192 {w.end_ts or '?'} — {w.note}"
        for w in windows
    )


def _build_nested(paths: list[str]) -> dict:
    """paths -> nested dict: {segment: {"path": full_path, "children": {...}}}"""
    root: dict = {"path": None, "children": {}}
    for path in paths:
        segments = [s for s in path.split("/") if s]
        node = root
        cur_path = ""
        for seg in segments:
            cur_path += "/" + seg
            child = node["children"].setdefault(seg, {"path": None, "children": {}})
            child["path"] = cur_path
            node = child
    return root


def populate_path_tree(tree: Tree, latest_by_path: dict[str, Event]) -> None:
    """Builds a directory tree purely out of paths that actually have
    recorded events (not the live filesystem — deleted files still show up,
    which is the point of a blame tree). Each node's label carries the most
    recent actor/action so you get a sense of "who did what" before you even
    click into it; clicking still drives the full filtered history below."""
    tree.clear()
    root_dict = _build_nested(list(latest_by_path.keys()))

    def render(parent_node: TreeNode, node_dict: dict) -> None:
        for seg in sorted(node_dict["children"]):
            child = node_dict["children"][seg]
            info = latest_by_path.get(child["path"])
            label = f"{seg}  [dim]({info.actor or '?'} \u00b7 {info.action})[/]" if info else seg
            if child["children"]:
                branch = parent_node.add(label, data=child["path"])
                render(branch, child)
            else:
                parent_node.add_leaf(label, data=child["path"])

    render(tree.root, root_dict)
    tree.root.expand()
