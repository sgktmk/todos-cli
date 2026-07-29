"""優先度判定と自動配置。CLI・TUI が共有する唯一の配置ロジック。"""

from __future__ import annotations

import datetime as _dt

from . import model, util
from .model import Task
from .parser import Document


def desired_section(task: Task, base: _dt.date) -> str | None:
    """タスクを置くべきセクションを実行時に判定する。

    終了ステータス（完了・やらない）は自動配置の対象外なので None を返す。

    - 期限切れ・本日・明日が期限        -> Today
    - 明後日が期限                      -> Tomorrow
    - 当週の日曜日までが期限            -> InWeek
    - それより先が期限                  -> ParkingLot
    - 期限なし かつ 進行中/要返答/回答待ち -> OpenEnded
    - 期限なし かつ 未着手/保留          -> ParkingLot
    """
    if task.status in model.TERMINAL_STATUSES:
        return None
    if task.due is not None:
        if task.due <= base + _dt.timedelta(days=1):
            return model.TODAY
        if task.due == base + _dt.timedelta(days=2):
            return model.TOMORROW
        if task.due <= util.week_end(base):
            return model.IN_WEEK
        return model.PARKING_LOT
    if task.status in model.IN_FLIGHT_STATUSES:
        return model.OPEN_ENDED
    return model.PARKING_LOT


def section_due(section: str, base: _dt.date) -> _dt.date | None:
    """そのセクションに置くための期日を返す。desired_section の逆引き。

    Today / Tomorrow などを見ているときに作るタスクは「そのタイミングで
    やりたいもの」なので、期日の候補を提示するために使う。
    期日で決まらないセクション（OpenEnded・ParkingLot）は None。

    返した日付を持つタスクは desired_section が同じセクションを指す。
    """
    if section == model.TODAY:
        return base
    if section == model.TOMORROW:
        return base + _dt.timedelta(days=2)
    if section == model.IN_WEEK:
        due = util.week_end(base)
        # 週末が近いと当週日曜が Today / Tomorrow の範囲に入る。
        # そのときは InWeek に留まる期日が存在しない。
        return due if due > base + _dt.timedelta(days=2) else None
    return None


#: 配置ルールの要約。再配置を提案するときの説明に使う。
RULE_SUMMARY = (
    "配置ルール: 期限切れ・本日・明日が期限は Today、明後日は Tomorrow、"
    "当週の日曜までは InWeek、それより先は ParkingLot。"
    "期日なしは 進行中・要返答・回答待ち が OpenEnded、未着手・保留 が ParkingLot。"
)


def reason(task: Task, base: _dt.date) -> str:
    """desired_section がその判定になった理由を短く返す（説明表示用）。"""
    if task.status in model.TERMINAL_STATUSES:
        return "終了済み"
    if task.due is not None:
        if task.due < base:
            return "期限切れ"
        if task.due == base:
            return "本日が期限"
        if task.due == base + _dt.timedelta(days=1):
            return "明日が期限"
        if task.due == base + _dt.timedelta(days=2):
            return "明後日が期限"
        if task.due <= util.week_end(base):
            return "今週が期限"
        return "来週以降が期限"
    if task.status in model.IN_FLIGHT_STATUSES:
        return "期日なし・仕掛中"
    return "期日なし"


def misplaced(doc: Document, base: _dt.date) -> list[tuple[Task, str]]:
    """本来と異なるセクションにある未完了タスクを列挙する。"""
    out = []
    for section in doc.known_sections():
        for task in section.tasks:
            want = desired_section(task, base)
            if want is not None and want != section.name:
                out.append((task, want))
    return out


def needs_rollover(doc: Document, base: _dt.date) -> bool:
    """再配置が必要かどうか。状態ファイルを持たないため配置の差分で判定する。"""
    return bool(misplaced(doc, base))


def rollover(doc: Document, base: _dt.date) -> list[tuple[Task, str, str]]:
    """すべての未完了タスクを共通ロジックで再評価し、配置と並び順を更新する。

    戻り値は (タスク, 移動前セクション, 移動後セクション) の一覧。
    """
    doc.ensure_sections()
    moves = []
    for task, want in misplaced(doc, base):
        moves.append((task, task.section, want))
        move(doc, task, want, sort=False)
    sort_all(doc)
    doc.reindex()
    return moves


def move(doc: Document, task: Task, section_name: str, sort: bool = True) -> None:
    """タスク（親子構造ごと）を別セクションへ移す。トップレベルのみ移動可能。"""
    if task.parent is not None:
        raise util.TodosError(
            "子タスクは単独で移動できません。親タスク「%s」を移動してください。"
            % task.parent.title
        )
    target = doc.section(section_name)
    if target is None:
        doc.ensure_sections()
        target = doc.section(section_name)
    if target is None:
        raise util.TodosError("未知のセクション「%s」です。" % section_name)
    src = doc.section(task.section) if task.section else None
    if src is not None and task in src.nodes:
        src.nodes.remove(task)
    else:
        for sec in doc.sections:
            if task in sec.nodes:
                sec.nodes.remove(task)
                break
    target.nodes.append(task)
    task.section = section_name
    for node in task.walk():
        node.section = section_name
    if sort:
        sort_section(target)
    doc.reindex()


def sort_section(section) -> None:
    """セクション内のタスクを並べ替える。タスク以外の行は元の位置より前に残す。"""
    tasks = section.tasks
    others = [n for n in section.nodes if not isinstance(n, Task)]
    section.nodes = others + sorted(tasks, key=model.sort_key)


def sort_all(doc: Document) -> None:
    for section in doc.known_sections():
        sort_section(section)
