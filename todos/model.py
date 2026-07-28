"""トドズのドメインモデル。CLI と TUI はこのモデルだけを介してタスクを扱う。"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from . import util

# ---------------------------------------------------------------- ステータス

TODO = "未着手"
DOING = "進行中"
NEEDS_REPLY = "要返答"
WAITING = "回答待ち"
HOLD = "保留"
DONE = "完了"
SKIP = "やらない"

STATUSES = (TODO, DOING, NEEDS_REPLY, WAITING, HOLD, DONE, SKIP)

#: 終了状態。自動配置の対象外であり、アーカイブ対象でもある。
TERMINAL_STATUSES = (DONE, SKIP)
#: 期限がないとき OpenEnded に置く「仕掛中」ステータス。
IN_FLIGHT_STATUSES = (DOING, NEEDS_REPLY, WAITING)
#: 相手（人・チーム）を括弧内に書けるステータス。
COUNTERPART_STATUSES = (NEEDS_REPLY, WAITING)

#: 同一期日内の並び順。小さいほど上。
STATUS_ORDER = {
    NEEDS_REPLY: 0,
    DOING: 1,
    TODO: 2,
    WAITING: 3,
    HOLD: 4,
    DONE: 5,
    SKIP: 6,
}

# ---------------------------------------------------------------- セクション

TODAY = "Today"
TOMORROW = "Tomorrow"
IN_WEEK = "InWeek"
OPEN_ENDED = "OpenEnded"
PARKING_LOT = "ParkingLot"

SECTIONS = (TODAY, TOMORROW, IN_WEEK, OPEN_ENDED, PARKING_LOT)

_SECTION_LOOKUP = {s.lower(): s for s in SECTIONS}
_SECTION_LOOKUP.update(
    {
        "today": TODAY,
        "tomorrow": TOMORROW,
        "inweek": IN_WEEK,
        "in-week": IN_WEEK,
        "in_week": IN_WEEK,
        "week": IN_WEEK,
        "openended": OPEN_ENDED,
        "open-ended": OPEN_ENDED,
        "open_ended": OPEN_ENDED,
        "open": OPEN_ENDED,
        "parkinglot": PARKING_LOT,
        "parking-lot": PARKING_LOT,
        "parking_lot": PARKING_LOT,
        "parking": PARKING_LOT,
    }
)


def canonical_section(name: str) -> str | None:
    """利用者入力のセクション名を正規名へ寄せる。未知なら None。"""
    if not name:
        return None
    return _SECTION_LOOKUP.get(name.strip().lower())


def canonical_status(name: str) -> str | None:
    """利用者入力のステータス名を正規名へ寄せる。未知なら None。"""
    if not name:
        return None
    s = name.strip()
    if s in STATUSES:
        return s
    aliases = {
        "todo": TODO,
        "doing": DOING,
        "wip": DOING,
        "needs-reply": NEEDS_REPLY,
        "needsreply": NEEDS_REPLY,
        "reply": NEEDS_REPLY,
        "waiting": WAITING,
        "hold": HOLD,
        "pending": HOLD,
        "done": DONE,
        "skip": SKIP,
        "wontdo": SKIP,
        "wont-do": SKIP,
    }
    return aliases.get(s.lower())


# ---------------------------------------------------------------- タスク


@dataclass
class Task:
    """1件のタスク。子タスクを再帰的に保持する。"""

    title: str = ""
    status: str = TODO
    counterpart: str | None = None
    due: _dt.date | None = None
    tags: list[str] = field(default_factory=list)
    checked: bool = False
    detail: list[str] = field(default_factory=list)
    children: list["Task"] = field(default_factory=list)

    # --- 表記の保存（人間が書いた形をなるべく壊さないため）
    #: 【未着手】と明示されていたか。False なら 【】 を書き戻さない。
    status_explicit: bool = True
    bullet: str = "-"

    # --- 解析時に付与されるメタ情報（永続化しない）
    parent: "Task | None" = field(default=None, repr=False, compare=False)
    section: str | None = field(default=None, compare=False)
    index: int = field(default=0, compare=False)
    line_no: int = field(default=0, compare=False)
    order: int = field(default=0, compare=False)
    #: 解析時に見つかった問題（validate が使う）
    issues: list = field(default_factory=list, repr=False, compare=False)

    # ------------------------------------------------------------ 便利メソッド

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def depth(self) -> int:
        d, node = 0, self.parent
        while node is not None:
            d += 1
            node = node.parent
        return d

    @property
    def root(self) -> "Task":
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def walk(self):
        """自身と全子孫を先行順で列挙する。"""
        yield self
        for child in self.children:
            yield from child.walk()

    def descendants(self):
        for child in self.children:
            yield from child.walk()

    def status_label(self) -> str:
        if self.counterpart and self.status in COUNTERPART_STATUSES:
            return "%s（%s）" % (self.status, self.counterpart)
        return self.status

    def display_title(self) -> str:
        """一覧表示用の1行文字列（ステータス・期日・タグ込み）。"""
        parts = []
        if self.status != TODO or self.status_explicit:
            parts.append("【%s】" % self.status_label())
        parts.append(self.title)
        if self.due:
            parts.append(util.format_due(self.due))
        if self.tags:
            parts.append(" " + " ".join("#" + t for t in self.tags))
        return "".join(parts)

    def to_dict(self, include_children: bool = True) -> dict:
        data = {
            "index": self.index,
            "section": self.section,
            "title": self.title,
            "status": self.status,
            "counterpart": self.counterpart,
            "due": util.format_date(self.due) if self.due else None,
            "tags": list(self.tags),
            "checked": self.checked,
            "detail": "\n".join(self.detail) if self.detail else None,
            "depth": self.depth,
            "line": self.line_no,
        }
        if include_children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


def sort_key(task: Task):
    """セクション内の並び順キー: 期日昇順 → ステータス優先度 → 元の記述順。"""
    return (
        task.due or _dt.date.max,
        STATUS_ORDER.get(task.status, 99),
        task.order,
    )
