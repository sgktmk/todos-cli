"""タスク操作ロジック。CLI・TUI はここを呼ぶだけで Markdown を直接触らない。"""

from __future__ import annotations

import datetime as _dt

from . import model, parser, render, schedule
from .model import Task
from .parser import Document
from .util import TodosError


# ---------------------------------------------------------------- 追加


def add(
    doc: Document,
    title: str,
    base: _dt.date,
    status: str = model.TODO,
    counterpart: str | None = None,
    due: _dt.date | None = None,
    tags: list[str] | None = None,
    detail: str | None = None,
    section: str | None = None,
    parent: Task | None = None,
) -> Task:
    """タスクを追加する。必須はタイトルのみ。"""
    title = (title or "").strip()
    if not title:
        raise TodosError("タスクのタイトルを指定してください。")
    if status not in model.STATUSES:
        raise TodosError("未知のステータス「%s」です。" % status)

    task = Task(
        title=title,
        status=status,
        counterpart=counterpart if status in model.COUNTERPART_STATUSES else None,
        due=due,
        tags=list(tags or []),
    )
    task.status_explicit = status != model.TODO
    if detail:
        task.detail = detail.rstrip("\n").split("\n")
    _sync_marks(task)

    if parent is not None:
        parent.children.append(task)
        task.parent = parent
        task.section = parent.section
        doc.reindex()
        return task

    name = section or schedule.desired_section(task, base) or model.PARKING_LOT
    target = doc.section(name)
    if target is None:
        doc.ensure_sections()
        target = doc.section(name)
    if target is None:
        raise TodosError("未知のセクション「%s」です。" % name)
    target.nodes.append(task)
    task.section = name
    doc.reindex()
    return task


# ---------------------------------------------------------------- 更新


def set_status(task: Task, status: str, counterpart: str | None = None) -> None:
    """ステータスを変更し、チェックボックスと取り消し線を同時に更新する。"""
    if status not in model.STATUSES:
        raise TodosError(
            "未知のステータス「%s」です。使用できるのは %s です。"
            % (status, "・".join(model.STATUSES))
        )
    if counterpart and status not in model.COUNTERPART_STATUSES:
        raise TodosError(
            "相手を指定できるのは %s のみです。" % "・".join(model.COUNTERPART_STATUSES)
        )
    task.status = status
    if status in model.COUNTERPART_STATUSES:
        if counterpart is not None:
            task.counterpart = counterpart or None
    else:
        task.counterpart = None
    # 未着手へ戻すときは 【未着手】 を書くかどうかの既存表記を保つ（要件9.2）
    if status != model.TODO:
        task.status_explicit = True
    _sync_marks(task)


def _sync_marks(task: Task) -> None:
    """ステータスに合わせてチェックボックスと取り消し線を揃える。"""
    task.checked = task.status == model.DONE
    parser.set_struck(task, task.status == model.SKIP)


def open_children(task: Task) -> list[Task]:
    """未完了の子孫タスク。"""
    return [c for c in task.descendants() if not c.is_terminal]


def set_title(task: Task, title: str) -> None:
    title = (title or "").strip()
    if not title:
        raise TodosError("タイトルは空にできません。")
    task.title = title


def set_due(task: Task, due: _dt.date | None) -> None:
    task.due = due


def set_detail(task: Task, detail: str | None) -> None:
    task.detail = detail.rstrip("\n").split("\n") if detail else []


def set_tags(task: Task, tags: list[str]) -> None:
    """タグを差し替える。重複は取り除き、書き戻せない文字は拒否する。"""
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.lstrip("#").strip()
        if not tag:
            continue
        if "#" in tag or any(c.isspace() for c in tag):
            raise TodosError("タグに空白と # は使えません: %s" % tag)
        if tag not in cleaned:
            cleaned.append(tag)
    task.tags = cleaned


def add_tags(task: Task, tags: list[str]) -> None:
    for tag in tags:
        tag = tag.lstrip("#").strip()
        if tag and tag not in task.tags:
            task.tags.append(tag)


def remove_tags(task: Task, tags: list[str]) -> None:
    for tag in tags:
        tag = tag.lstrip("#").strip()
        if tag in task.tags:
            task.tags.remove(tag)


def delete(doc: Document, task: Task) -> None:
    """タスクを親子構造ごと取り除く。"""
    if task.parent is not None:
        task.parent.children.remove(task)
    else:
        for section in doc.sections:
            if task in section.nodes:
                section.nodes.remove(task)
                break
    doc.reindex()


# ---------------------------------------------------------------- 検索


def search(
    doc: Document,
    keyword: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    section: str | None = None,
    include_terminal: bool = True,
) -> list[Task]:
    """キーワード・タグ・ステータス・セクションでタスクを絞り込む。"""
    key = keyword.lower() if keyword else None
    tag = tag.lstrip("#").lower() if tag else None
    out = []
    for task in doc.all_tasks():
        if section and task.section != section:
            continue
        if status and task.status != status:
            continue
        if not include_terminal and task.is_terminal:
            continue
        if tag and tag not in [t.lower() for t in task.tags]:
            continue
        if key:
            haystack = "\n".join([task.title] + task.detail).lower()
            if key not in haystack:
                continue
        out.append(task)
    return out


def resolve(doc: Document, selector: str, section: str | None = None) -> list[Task]:
    """タスク指定子から候補を返す。

    指定子は「一覧の表示番号」または「タイトルの部分一致」。
    一意に定まらない場合は複数返し、呼び出し側で選択または中断する。
    """
    selector = (selector or "").strip()
    if not selector:
        return []
    if selector.isdigit():
        task = doc.by_index(int(selector))
        if task and (section is None or task.section == section):
            return [task]
        return []
    candidates = [
        t
        for t in doc.all_tasks()
        if (section is None or t.section == section)
        and selector.lower() in t.title.lower()
    ]
    exact = [t for t in candidates if t.title == selector]
    return exact or candidates


# ---------------------------------------------------------------- アーカイブ


def archivable(doc: Document) -> list[Task]:
    """アーカイブ対象のトップレベルタスク。

    終了した子タスクは親が終了するまで元の場所に残す（要件14.2）。
    """
    out = []
    for section in doc.known_sections():
        for task in section.tasks:
            if task.is_terminal:
                out.append(task)
    return out


def archive(doc: Document, store, base: _dt.date) -> list[Task]:
    """終了タスクを archive.md へ移す。見出しの日付はアーカイブ実行日。"""
    targets = archivable(doc)
    if not targets:
        return []
    blocks = [render.render_task(t, 0) for t in targets]
    # 追記に失敗した場合でも tasks.md 側を失わないよう archive.md を先に更新する
    store.append_archive(base, blocks)
    for task in targets:
        delete(doc, task)
    doc.reindex()
    return targets


# ---------------------------------------------------------------- 日報


def report(doc: Document) -> dict:
    """日報作成用の候補一覧。履歴を持たないため当日性は保証しない。"""
    in_flight, done, skipped = [], [], []
    for task in doc.all_tasks():
        if task.status in model.IN_FLIGHT_STATUSES:
            in_flight.append(task)
        elif task.status == model.DONE:
            done.append(task)
        elif task.status == model.SKIP:
            skipped.append(task)
    return {"in_flight": in_flight, "done": done, "skipped": skipped}
