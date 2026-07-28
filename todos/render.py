"""ドメインモデルから Markdown への書き戻し。"""

from __future__ import annotations

from . import model, parser, util
from .model import Task
from .parser import Document, RawBlock


def render_task_line(task: Task, depth: int | None = None) -> str:
    """タスク1行分（子や詳細を含まない）を組み立てる。"""
    depth = task.depth if depth is None else depth
    indent = " " * (depth * 2)
    check = "x" if task.checked else " "
    parts = ["%s%s [%s] " % (indent, task.bullet, check)]
    if task.status != model.TODO or task.status_explicit:
        parts.append("【%s】" % task.status_label())
    title = task.title
    if parser.is_struck(task) and title:
        title = "~~%s~~" % title
    parts.append(title)
    if task.due:
        parts.append(util.format_due(task.due))
    for tag in task.tags:
        parts.append(" #%s" % tag)
    return "".join(parts).rstrip()


def render_task(task: Task, depth: int | None = None) -> list[str]:
    """タスク本体・詳細文・子タスクを行のリストとして返す。"""
    depth = task.depth if depth is None else depth
    lines = [render_task_line(task, depth)]
    detail_indent = " " * ((depth + 1) * 2)
    for line in task.detail:
        lines.append((detail_indent + line).rstrip() if line.strip() else "")
    for child in task.children:
        lines.extend(render_task(child, depth + 1))
    return lines


def render_document(doc: Document) -> str:
    """Document 全体を Markdown テキストへ戻す。"""
    out: list[str] = []
    out.extend(_strip_trailing_blanks(doc.preamble))
    for section in doc.sections:
        if out and out[-1].strip():
            out.append("")
        out.append(section.heading)
        out.append("")
        body: list[str] = []
        for node in section.nodes:
            if isinstance(node, RawBlock):
                block = _strip_trailing_blanks(node.lines)
                if block:
                    if body and body[-1].strip():
                        body.append("")
                    body.extend(block)
            else:
                body.extend(render_task(node, 0))
        out.extend(_strip_trailing_blanks(body))
    text = "\n".join(_strip_trailing_blanks(out))
    return text + "\n" if text else ""


def _strip_trailing_blanks(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out
