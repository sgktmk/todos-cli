"""外部エディタ連携。複数行の詳細文編集は $EDITOR / $VISUAL へ委譲する。"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from . import model, parser, render
from .parser import Document
from .util import TodosError


def editor_command() -> list[str]:
    cmd = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not cmd:
        raise TodosError(
            "外部エディタが設定されていません。環境変数 EDITOR または VISUAL を設定してください。"
        )
    return shlex.split(cmd)


def launch(text: str, suffix: str = ".md") -> str | None:
    """一時ファイルを外部エディタで開き、編集後の内容を返す。中断時は None。"""
    cmd = editor_command()
    fd, tmp = tempfile.mkstemp(prefix="todos-", suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        result = subprocess.call(cmd + [tmp])
        if result != 0:
            return None
        return Path(tmp).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


HELP_HEADER = (
    "<!-- トドズ: このタスクを編集して保存してください。\n"
    "     形式: - [ ] 【ステータス】タイトル（〜YYYY/MM/DD） #タグ\n"
    "     詳細文と子タスクはインデントして記述します。\n"
    "     この行は保存されません。 -->\n"
)


def edit_task(doc: Document, task):
    """タスク1件（子孫含む）を外部エディタで編集し、結果を取り込む。

    取り込んだ場合は置き換え後の先頭タスクを返す。変更が無ければ None。
    """
    original = "\n".join(render.render_task(task, 0))
    edited = launch(HELP_HEADER + original + "\n")
    if edited is None:
        return None
    body = _strip_comments(edited)
    if body.strip() == original.strip():
        return None

    section_name = task.section or model.PARKING_LOT
    sub = parser.parse_text("## %s\n\n%s\n" % (section_name, body))
    section = sub.section(section_name)
    new_tasks = section.tasks if section else []
    if not new_tasks:
        raise TodosError("編集結果からタスクを読み取れませんでした。変更を破棄します。")

    container, index = _locate(doc, task)
    if container is None:
        raise TodosError("編集対象のタスクが見つかりませんでした。")
    for t in new_tasks:
        t.parent = task.parent
        for node in t.walk():
            node.section = section_name
    container[index:index + 1] = new_tasks
    doc.reindex()
    return new_tasks[0]


def _strip_comments(text: str) -> str:
    """HTML コメント行（案内文）を取り除く。"""
    out, inside = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if inside:
            if stripped.endswith("-->"):
                inside = False
            continue
        if stripped.startswith("<!--"):
            if not stripped.endswith("-->"):
                inside = True
            continue
        out.append(line)
    return "\n".join(out)


def _locate(doc: Document, task):
    if task.parent is not None:
        kids = task.parent.children
        return (kids, kids.index(task)) if task in kids else (None, -1)
    for section in doc.sections:
        if task in section.nodes:
            return section.nodes, section.nodes.index(task)
    return None, -1
