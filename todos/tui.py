"""タスク操作に特化した TUI。Markdown の書き換えは ops / schedule に委譲する。

描画は「行を作る」と「色を塗る」を分けている。行は (文字列, 役割名) の並び
(span) として組み立て、役割名から実際の色を引くのは theme.py の仕事。
"""

from __future__ import annotations

import curses
import locale
import os

from . import editor, model, ops, schedule, theme as theme_mod, util, validate
from .util import TodosError, display_width, pad, truncate

LEFT_WIDTH = 18
MIN_WIDTH = 40
MIN_HEIGHT = 10

BRAND = "トドズ"
WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")

#: フッタに詰め込む操作。幅が足りなければ後ろから削る。
HELP_ITEMS = [
    ("j/k", "移動"),
    ("J/K", "セクション"),
    ("Tab", "ペイン"),
    ("Enter", "詳細"),
    ("a", "追加"),
    ("s", "状態"),
    ("d", "完了"),
    ("x", "やらない"),
    ("m", "移動"),
    ("[/]", "並べ替え"),
    ("c", "複製"),
    ("i", "編集"),
    ("D", "期日"),
    ("T", "タグ"),
    ("I", "詳細文"),
    ("o", "子タスク"),
    ("/", "検索"),
    ("t", "タグ検索"),
    ("r", "再配置"),
    ("A", "整理"),
    ("w", "警告"),
    ("e", "エディタ"),
    ("R", "再読込"),
]
#: 幅が狭くても必ず残す操作。ヘルプへの入口を失わせない。
HELP_TAIL = [("?", "ヘルプ"), ("q", "終了")]


def help_pairs(width: int) -> list[tuple[str, str]]:
    """端末幅に収まる範囲のキーヘルプを (キー, 説明) の並びで返す。"""
    tail = "  ".join("%s:%s" % item for item in HELP_TAIL)
    budget = width - display_width(tail) - 2
    pairs: list[tuple[str, str]] = []
    used = 0
    for key, label in HELP_ITEMS:
        item = "%s:%s" % (key, label)
        cost = display_width(item) + (2 if pairs else 0)
        if used + cost > budget:
            break
        pairs.append((key, label))
        used += cost
    return pairs + HELP_TAIL


def help_line(width: int) -> str:
    """端末幅に収まる範囲でキーヘルプを組み立てる。"""
    return "  ".join("%s:%s" % pair for pair in help_pairs(width))


#: ヘルプモーダルの内容。("", "") は区切りの空行。
HELP_KEYS = [
    ("j / ↓", "次のタスクへ（末尾まで来たら先頭に戻る）"),
    ("k / ↑", "前のタスクへ（先頭まで来たら末尾に戻る）"),
    ("J / K", "次 / 前のセクションへ（端まで来たら反対側へ回る）"),
    ("g / G", "先頭 / 末尾へ"),
    ("Tab", "Sections ペインと Tasks ペインを切り替える"),
    ("Enter", "タスクの詳細を表示（Sections では Tasks へ移る）"),
    ("", ""),
    ("a", "タスクを追加（Today などでは期日を入れるか聞く）"),
    ("o", "選択中のタスクの子として追加"),
    ("c", "タスク行を複製（詳細文・子タスクは複製しない）"),
    ("i", "タイトルを簡易編集"),
    ("I", "詳細文を編集（外部エディタ）"),
    ("D", "期日を設定・削除（空欄で削除）"),
    ("T", "タグを編集（空白区切り・空欄で削除）"),
    ("s", "ステータスを変更（相手は空欄で削除）"),
    ("d", "完了にする"),
    ("x", "やらないにする"),
    ("m", "セクションを移動"),
    ("[ / ]", "兄弟の中で上へ / 下へ動かす（Shift + ↑ / ↓ も可）"),
    ("e", "外部エディタで編集"),
    ("", ""),
    ("/", "キーワード検索"),
    ("t", "タグ検索"),
    ("Esc", "検索結果を解除"),
    ("", ""),
    ("r", "自動再配置 (rollover)"),
    ("A", "アーカイブ"),
    ("w", "構文上の警告を表示・修正"),
    ("R", "ファイルを読み直す"),
    ("q", "終了"),
]

#: 入力欄（追加・タイトル編集・期日・タグ・検索）で使えるキー。
PROMPT_KEYS = [
    ("← / →", "カーソルを左 / 右へ（Ctrl-b / Ctrl-f）"),
    ("Home / End", "行頭 / 行末へ（Ctrl-a / Ctrl-e）"),
    ("Backspace", "カーソルの手前を 1 文字消す"),
    ("Delete", "カーソル位置を 1 文字消す（Ctrl-d）"),
    ("Ctrl-w", "直前の語を消す"),
    ("Ctrl-u", "カーソルより前を消す"),
    ("Ctrl-k", "カーソルより後ろを消す"),
    ("Enter / Esc", "確定 / 中止"),
]


def run(ctx) -> int:
    """TUI を起動する。ctx.doc は読み込み済みであること。"""
    locale.setlocale(locale.LC_ALL, "")
    try:
        return curses.wrapper(lambda scr: App(scr, ctx).loop())
    except curses.error as exc:  # pragma: no cover - 端末依存
        raise TodosError("TUI を起動できませんでした: %s" % exc)


class App:
    def __init__(self, screen, ctx):
        self.screen = screen
        self.ctx = ctx
        self.section_idx = 0
        self.task_idx = 0
        self.scroll = 0
        self.focus = "tasks"
        self.message = ""
        self.message_kind = "info"
        self.filter = None  # (種別, 語) の組。検索結果を表示中なら非 None
        self.quit = False
        self.theme = theme_mod.detect().setup()
        curses.curs_set(0)
        self.screen.keypad(True)
        self.focus_first_non_empty()
        self._startup_maintenance()

    # ------------------------------------------------------------ 状態

    @property
    def doc(self):
        return self.ctx.doc

    def sections(self):
        return self.doc.known_sections()

    def current_section(self):
        sections = self.sections()
        if not sections:
            return None
        self.section_idx = max(0, min(self.section_idx, len(sections) - 1))
        return sections[self.section_idx]

    def rows(self):
        """右ペインに表示する (タスク, 深さ) の一覧。"""
        if self.filter:
            kind, word = self.filter
            found = ops.search(
                self.doc,
                keyword=word if kind == "keyword" else None,
                tag=word if kind == "tag" else None,
            )
            return [(t, 0) for t in found]
        section = self.current_section()
        if section is None:
            return []
        out = []
        for task in section.tasks:
            for node in task.walk():
                out.append((node, node.depth))
        return out

    def current_task(self):
        rows = self.rows()
        if not rows:
            return None
        self.task_idx = max(0, min(self.task_idx, len(rows) - 1))
        return rows[self.task_idx][0]

    def save(self):
        self.ctx.save()

    def reload(self):
        self.ctx.doc = self.ctx.store.load(self.ctx.base)

    def notify(self, text: str, kind: str = "info"):
        self.message = text
        self.message_kind = kind

    def focus_first_non_empty(self):
        """タスクのある最初のセクションを選ぶ。

        Today が空のまま起動すると、タスクが1件も見えない画面になり、
        セクションを移せることに気づけないため。
        """
        for i, section in enumerate(self.sections()):
            if section.tasks:
                self.section_idx = i
                self.task_idx = 0
                self.scroll = 0
                return

    # ------------------------------------------------------------ 起動時の提案

    def _startup_maintenance(self):
        if getattr(self.ctx.args, "no_prompt", False):
            return
        moves = schedule.misplaced(self.doc, self.ctx.base)
        if moves:
            # 何も描かれていない画面で問われても判断できないため、先に一覧を描く。
            self.draw()
            if self.confirm_moves(moves, "日付が変わり、いまの配置がルールと食い違っています。"):
                self.apply_rollover()
        pending = ops.archivable(self.doc)
        if pending:
            self.draw()
            if self.ask("終了したタスクが %d 件あります。アーカイブしますか？" % len(pending)):
                archived = ops.archive(self.doc, self.ctx.store, self.ctx.base)
                self.save()
                self.focus_first_non_empty()
                self.notify("%d 件をアーカイブしました。" % len(archived))

    # ------------------------------------------------------------ 主ループ

    def loop(self) -> int:
        while not self.quit:
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                break
            try:
                self.handle(key)
            except TodosError as exc:
                self.notify(str(exc), "error")
        return 0

    def handle(self, key):
        rows = self.rows()
        if key == curses.KEY_RESIZE:
            return
        if isinstance(key, int):
            # ← / → は割り当てない。ペインの行き来は Tab だけにする。
            mapping = {
                curses.KEY_DOWN: "j",
                curses.KEY_UP: "k",
                curses.KEY_ENTER: "\n",
                curses.KEY_SR: "[",   # Shift + ↑
                curses.KEY_SF: "]",   # Shift + ↓
            }
            key = mapping.get(key, "")
        if key in ("q",):
            self.quit = True
        elif key in ("j",):
            self.move_cursor(1, len(rows))
        elif key in ("k",):
            self.move_cursor(-1, len(rows))
        elif key == "g":
            self.set_cursor(0)
        elif key == "G":
            self.set_cursor(max(0, len(rows) - 1))
        elif key == "\t":
            self.focus = "sections" if self.focus == "tasks" else "tasks"
        elif key == "J":
            self.change_section(1)
        elif key == "K":
            self.change_section(-1)
        elif key in ("\n", "\r", curses.KEY_ENTER):
            # Sections ペインでは「このセクションを見る」を決定とみなす。
            if self.focus == "sections":
                self.focus = "tasks"
            else:
                self.show_detail()
        elif key == "a":
            self.add_task()
        elif key == "o":
            self.add_child()
        elif key == "i":
            self.rename_task()
        elif key == "I":
            self.edit_detail()
        elif key == "D":
            self.edit_due()
        elif key == "T":
            self.edit_tags()
        elif key == "s":
            self.change_status()
        elif key == "d":
            self.terminal_status(model.DONE)
        elif key == "x":
            self.terminal_status(model.SKIP)
        elif key == "m":
            self.move_task()
        elif key == "[":
            self.reorder(-1)
        elif key == "]":
            self.reorder(1)
        elif key == "c":
            self.duplicate_task()
        elif key == "/":
            self.start_search("keyword")
        elif key == "t":
            self.start_search("tag")
        elif key == "\x1b":
            if self.filter:
                self.filter = None
                self.task_idx = 0
                self.notify("検索を解除しました。")
        elif key == "r":
            self.do_rollover()
        elif key == "A":
            self.do_archive()
        elif key == "w":
            self.show_warnings()
        elif key == "e":
            self.external_edit()
        elif key == "R":
            self.reload()
            self.notify("読み直しました。")
        elif key == "?":
            self.show_help()

    # ------------------------------------------------------------ カーソル

    def move_cursor(self, delta: int, total: int):
        """j/k の移動。いま見ている一覧の中だけで循環する。

        末尾で次のセクションへ抜けると、どこにいるのかを見失うため
        セクションをまたがない。セクションの移動は J/K か Tab に任せる。
        """
        if self.focus == "sections":
            self.change_section(delta)
            return
        if total:
            self.task_idx = (self.task_idx + delta) % total

    def set_cursor(self, index: int):
        if self.focus == "sections":
            self.section_idx = max(0, min(index, len(self.sections()) - 1))
            self.task_idx = 0
        else:
            self.task_idx = index

    def change_section(self, delta: int):
        """セクションを移す。一覧の端まで来たら反対の端へ回る。"""
        sections = self.sections()
        if not sections:
            return
        self.filter = None
        self.section_idx = (self.section_idx + delta) % len(sections)
        self.task_idx = 0
        self.scroll = 0

    # ------------------------------------------------------------ 操作

    def add_task(self):
        title = self.prompt("追加", "")
        if not title:
            return
        task = ops.add(self.doc, title, self.ctx.base, due=self.suggest_due())
        self.save()
        self.filter = None
        self.jump_to(task)
        self.notify("追加しました: %s (%s)" % (task.title, task.section))

    def suggest_due(self):
        """いま見ているセクションの期日を入れるか聞き、入れるなら日付を返す。

        Today や Tomorrow を見ているときに作るタスクは、たいていその
        タイミングでやりたいもの。しかし期日が無いタスクは配置ルールにより
        ParkingLot へ置かれてしまい、作った直後に見失う。
        期日で決まらないセクション（OpenEnded・ParkingLot）では聞かない。
        """
        if self.filter:
            return None
        section = self.current_section()
        if section is None:
            return None
        due = schedule.section_due(section.name, self.ctx.base)
        if due is None:
            return None
        if not self.ask("%s のタスクとして期日を %s（%s）にしますか？"
                        % (section.name, util.format_date(due), self._day_note(due))):
            return None
        return due

    def _day_note(self, day) -> str:
        """期日を確認するときに添える、基準日から見た呼び名。"""
        delta = (day - self.ctx.base).days
        if 0 <= delta <= 2:
            return ("本日", "明日", "明後日")[delta]
        return "%s曜" % WEEKDAYS[day.weekday()]

    def add_child(self):
        """選択中のタスクの子として追加する（a はトップレベルに追加する）。"""
        parent = self.current_task()
        if parent is None:
            return
        title = self.prompt("「%s」の子タスク" % truncate(parent.title, 20), "")
        if not title:
            return
        child = ops.add(self.doc, title, self.ctx.base, parent=parent)
        self.save()
        self.filter = None
        self.jump_to(child)
        self.notify("「%s」の子として追加しました。" % parent.title)

    def rename_task(self):
        task = self.current_task()
        if task is None:
            return
        title = self.prompt("タイトル", task.title)
        if title is None or not title.strip():
            return
        ops.set_title(task, title)
        self.save()
        self.notify("更新しました。")

    def edit_detail(self):
        """詳細文だけを外部エディタで編集する。TUI は複数行の入力欄を持たない。"""
        task = self.current_task()
        if task is None:
            return
        if self._outside_curses(lambda: editor.edit_detail(task)):
            self.save()
            self.notify("詳細文を更新しました。")
        else:
            self.notify("変更はありません。")

    def edit_due(self):
        """期日を設定・変更・削除する。空欄で確定すると削除。"""
        task = self.current_task()
        if task is None:
            return
        text = self.prompt("期日（空で削除）",
                           util.format_date(task.due) if task.due else "")
        if text is None:
            return
        text = text.strip()
        if text:
            due = util.parse_date(text, self.ctx.base)
            if due is None:
                raise TodosError(
                    "期日として解釈できません: %s（例: 2026/07/31、7/31、7月31日）" % text
                )
            ops.set_due(task, due)
            message = "期日を %s にしました。" % util.format_date(due)
        else:
            ops.set_due(task, None)
            message = "期日を削除しました。"
        moved = self._follow_rule(task)
        self.save()
        if moved:
            self.filter = None
            self.jump_to(task)
            message += " ルールに従って %s へ移しました。" % moved
        self.notify(message)

    def edit_tags(self):
        """タグを差し替える。空白区切りで入力し、空欄で確定すると削除。"""
        task = self.current_task()
        if task is None:
            return
        text = self.prompt("タグ（空白区切り・空で削除）", " ".join(task.tags))
        if text is None:
            return
        ops.set_tags(task, text.split())
        self.save()
        if task.tags:
            self.notify("タグを %s にしました。" % " ".join("#" + t for t in task.tags))
        else:
            self.notify("タグを削除しました。")

    def _follow_rule(self, task):
        """期日を変えた結果ルールと食い違うなら配置を追従させ、移動先を返す。

        放置すると次回起動で再配置を提案されるため、その場で合わせる。
        子タスクは単独で移動できないので親の配置に従う。
        """
        if task.parent is not None:
            return None
        want = schedule.desired_section(task, self.ctx.base)
        if want is None or want == task.section:
            return None
        schedule.move(self.doc, task, want)
        return want

    def change_status(self):
        task = self.current_task()
        if task is None:
            return
        options = list(model.STATUSES)
        marks = [(self.theme.status_glyph(s), self.theme.status_role(s)) for s in options]
        choice = self.choose("ステータスを選択", options, marks)
        if choice is None:
            return
        status = options[choice]
        if status in model.TERMINAL_STATUSES:
            self.terminal_status(status)
            return
        who = None
        if status in model.COUNTERPART_STATUSES:
            who = self.prompt("相手（任意・空で削除）", task.counterpart or "")
            if who is None:
                return
        # who を or None で潰すと空欄が「変更しない」になり、相手を消せなくなる。
        ops.set_status(task, status, who)
        self.save()
        self.notify("ステータスを【%s】にしました。" % task.status_label())

    def terminal_status(self, status: str):
        task = self.current_task()
        if task is None:
            return
        pending = ops.open_children(task)
        if pending and not self.ask(
            "未完了の子タスクが %d 件あります。続行しますか？" % len(pending)
        ):
            self.notify("中止しました。")
            return
        ops.set_status(task, status)
        self.save()
        self.notify("【%s】にしました。" % status)

    def move_task(self):
        task = self.current_task()
        if task is None:
            return
        if task.parent is not None:
            self.notify("子タスクは単独で移動できません。", "error")
            return
        options = list(model.SECTIONS)
        marks = [(self.theme.glyph("section"), self.theme.section_role(s)) for s in options]
        choice = self.choose("移動先セクション", options, marks)
        if choice is None:
            return
        schedule.move(self.doc, task, options[choice])
        self.save()
        self.filter = None
        self.jump_to(task)
        self.notify("%s へ移動しました。" % task.section)

    def reorder(self, delta: int):
        """選択中のタスクを兄弟の中で上下に動かす（m はセクション間の移動）。"""
        if self.filter:
            self.notify("検索結果では並べ替えできません。", "error")
            return
        task = self.current_task()
        if task is None:
            return
        if not ops.reorder(self.doc, task, delta):
            self.notify("%sこれ以上動かせません。"
                        % ("同じ親の中で" if task.parent is not None else ""))
            return
        self.save()
        self.jump_to(task)
        self.notify("並べ替えました。")

    def duplicate_task(self):
        """タスク行だけを複製する。詳細文と子タスクは引き継がない。"""
        task = self.current_task()
        if task is None:
            return
        copy = ops.duplicate(self.doc, task)
        self.save()
        self.filter = None
        self.jump_to(copy)
        self.notify("複製しました（詳細文と子タスクは除く）。i でタイトルを直せます。")

    def start_search(self, kind: str):
        label = "検索" if kind == "keyword" else "タグ検索 #"
        word = self.prompt(label)
        if not word:
            return
        self.filter = (kind, word)
        self.task_idx = 0
        self.scroll = 0
        self.focus = "tasks"
        self.notify("Esc で検索を解除します。")

    def do_rollover(self):
        moves = schedule.misplaced(self.doc, self.ctx.base)
        if not moves:
            self.notify("再配置の必要はありません。")
            return
        if self.confirm_moves(moves, "いまの配置がルールと食い違っているタスクがあります。"):
            self.apply_rollover()
        else:
            self.notify("中止しました。配置は変えていません。")

    def apply_rollover(self):
        """再配置を実行し、どのタスクがどこへ動いたかを見せる。"""
        applied = schedule.rollover(self.doc, self.ctx.base)
        self.save()
        self.task_idx = 0
        self.focus_first_non_empty()
        if applied:
            # 確認モーダルを消し、移動後の配置を背景に出してから結果を見せる。
            self.draw()
            self.popup("再配置しました (%d 件)" % len(applied),
                       self._move_table(applied))
        self.notify("%d 件を再配置しました。" % len(applied))

    def confirm_moves(self, moves, lead: str) -> bool:
        """移動の内容と理由を見せてから y/n を聞く。

        「日付が更新されています」だけでは何がどこへ動くのか分からないため、
        件数ではなく一覧そのものを出す。
        """
        lines = self._wrapped(lead, "popup.text")
        lines.append("")
        lines += self._move_table([(task, task.section, want) for task, want in moves])
        lines.append("")
        lines += self._wrapped(schedule.RULE_SUMMARY, "popup.label")
        lines.append("")
        lines += self._wrapped(
            "中止した場合、配置は変わりません（食い違いが残るため次回も提案します）。",
            "popup.label",
        )
        lines += self._wrapped(
            "提案そのものを止めるには --no-prompt を付けて起動します。", "popup.label"
        )
        return self.popup_ask("タスクの再配置 (%d 件)" % len(moves), lines,
                              "y で実行   他のキーで中止   j/k スクロール")

    #: 理由 / 移動 / タスク を1行に並べるのに要る内寸。
    _TABLE_WIDTH = 60

    def _move_table(self, moves):
        """(タスク, 移動前, 移動後) の一覧を作る。

        幅が足りない端末では 1 行に収めるとタイトルが消えてしまうため、
        タスク名と移動先を 2 行に分けて出す。
        """
        _, width, _, _ = self.geometry()
        arrow = self.theme.glyph("arrow")
        if width - 6 < self._TABLE_WIDTH:
            lines = []
            for task, src, dst in moves:
                lines.append([("  ", None), (task.title, "popup.text")])
                lines.append([
                    ("      ", None),
                    ("%s %s %s" % (src, arrow, dst), self.theme.section_role(dst)),
                    ("  （%s）" % schedule.reason(task, self.ctx.base), "popup.label"),
                ])
            return lines
        lines = [[("  ", None), (pad("理由", 18), "popup.label"),
                  (pad("移動", 24), "popup.label"), ("タスク", "popup.label")]]
        for task, src, dst in moves:
            lines.append([
                ("  ", None),
                (pad(schedule.reason(task, self.ctx.base), 18), "popup.text"),
                (pad("%s %s %s" % (src, arrow, dst), 24), self.theme.section_role(dst)),
                (task.title, "popup.text"),
            ])
        return lines

    def _wrapped(self, text: str, role: str):
        """モーダル本文用に、端末幅で折り返した span の並びを作る。"""
        return [[("  ", None), (chunk, role)]
                for chunk in util.wrap(text, self._detail_width())]

    def do_archive(self):
        targets = ops.archivable(self.doc)
        if not targets:
            self.notify("アーカイブ対象のタスクはありません。")
            return
        if not self.ask("%d 件をアーカイブします。実行しますか？" % len(targets)):
            return
        archived = ops.archive(self.doc, self.ctx.store, self.ctx.base)
        self.save()
        self.task_idx = 0
        self.notify("%d 件をアーカイブしました。" % len(archived))

    def show_warnings(self):
        found = validate.validate(self.doc)
        if not found:
            self.popup("構文上の警告", [[("  問題は見つかりませんでした。", "popup.text")]])
            return
        lines = []
        for issue in found:
            lines.append([("  ! ", "popup.warn"), (issue.format().strip(), "popup.text")])
        title = "構文上の警告 (%d 件)" % len(found)
        repairable = validate.fixable(found)
        if not repairable:
            self.popup(title, lines)
            return
        # 直せる問題は、何をするかを見せたうえでその場で直せるようにする。
        lines.append("")
        lines.append([("  修正内容", "popup.label")])
        for issue in repairable:
            lines.append([("    ", None), (issue.fix_label, "popup.text")])
        if not self.popup_ask(
            title, lines,
            "y で %d 件を修正   他のキーで閉じる   j/k スクロール" % len(repairable),
        ):
            return
        applied = [i for i in repairable if validate.apply_fix(self.doc, i)]
        if applied:
            self.save()
        self.notify("%d 件を修正しました。" % len(applied))

    def show_help(self):
        lines = self._key_table(HELP_KEYS)
        lines.append("")
        lines.append([("  入力欄（追加・タイトル編集・期日・タグ・検索）",
                       "popup.label")])
        lines += self._key_table(PROMPT_KEYS)
        self.popup("キー操作", lines)

    def _key_table(self, keys):
        """(キー, 説明) の並びをヘルプの行にする。キー欄は一覧の中で桁を揃える。"""
        column = max(display_width(key) for key, _ in keys)
        lines = []
        for key, desc in keys:
            if not key:
                lines.append("")
                continue
            lines.append([("  ", None), (pad(key, column), "hint.key"),
                          ("  ", None), (desc, "popup.text")])
        return lines

    def show_detail(self):
        task = self.current_task()
        if task is None:
            return
        glyph = self.theme.status_glyph(task.status)
        lines = [
            [("  ", None), (pad("ステータス", 12), "popup.label"),
             (("%s %s" % (glyph, task.status_label())).strip(), self.theme.status_role(task.status))],
            [("  ", None), (pad("期日", 12), "popup.label"),
             (task.due.strftime("%Y/%m/%d") if task.due else "なし",
              self._due_role(task.due) if task.due else "popup.label")],
            [("  ", None), (pad("タグ", 12), "popup.label"),
             (" ".join("#" + t for t in task.tags) or "なし",
              "tag" if task.tags else "popup.label")],
            [("  ", None), (pad("セクション", 12), "popup.label"),
             (task.section or "-", self.theme.section_role(task.section or ""))],
        ]
        if task.detail:
            lines.append("")
            lines.append([("  詳細", "popup.label")])
            for line in task.detail:
                for chunk in util.wrap(line, self._detail_width()):
                    lines.append([("    ", None), (chunk, "popup.text")])
        if task.children:
            lines.append("")
            lines.append([("  子タスク", "popup.label")])
            for child in task.descendants():
                indent = "    " + "  " * (child.depth - task.depth - 1)
                lines.append([(indent, None),
                              (self.theme.status_glyph(child.status) + " ",
                               self.theme.status_role(child.status)),
                              (child.title, "popup.text")])
        self.popup(task.title, lines)

    def _detail_width(self) -> int:
        """詳細文を折り返す幅。

        モーダルの内寸は _popup_draw が最長行から決めるが、上限は幅 - 6 になる。
        そこから枠内の余白と字下げ 4 桁を引いた分までは切り詰められない。
        """
        _, width, _, _ = self.geometry()
        return max(20, width - 12)

    def _outside_curses(self, action):
        """curses を一旦畳んで action を実行し、戻ってから画面を作り直す。"""
        curses.def_prog_mode()
        curses.endwin()
        try:
            return action()
        finally:
            curses.reset_prog_mode()
            self.screen.clear()

    def external_edit(self):
        task = self.current_task()
        if task is None:
            return
        changed = self._outside_curses(lambda: editor.edit_task(self.doc, task))
        if changed:
            self.save()
            self.notify("外部エディタの内容を反映しました。")
        else:
            self.notify("変更はありません。")

    def jump_to(self, task):
        for i, (node, _) in enumerate(self.rows()):
            if node is task:
                self.task_idx = i
                return
        for i, section in enumerate(self.sections()):
            if section.name == task.section:
                self.section_idx = i
                break
        for i, (node, _) in enumerate(self.rows()):
            if node is task:
                self.task_idx = i
                return

    # ------------------------------------------------------------ 画面構成

    def geometry(self):
        """(高さ, 幅, 左ペインの内寸, 右ペインの内寸) を返す。

        画面は上から順に ヘッダ / ペイン / キーヘルプ / ステータスバー。
        左右のペインはそれぞれ枠を持つので、内寸の合計は幅 - 4 になる。
        """
        height, width = self.screen.getmaxyx()
        left = min(LEFT_WIDTH, max(10, width // 4))
        right = width - left - 4
        return height, width, left, right

    def draw(self):
        self.screen.erase()
        height, width, left_w, right_w = self.geometry()
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            self._addstr(0, 0, truncate("端末が小さすぎます (%dx%d 以上必要)"
                                        % (MIN_WIDTH, MIN_HEIGHT), max(0, width - 1)))
            self.screen.refresh()
            return

        body_height = height - 5
        rows = self.rows()
        sections = self.sections()
        self._clamp_scroll(len(rows), body_height)

        self._draw_header(width)
        self._draw_panes(width, left_w, right_w, body_height)
        self._draw_sections(sections, left_w, body_height)
        self._draw_tasks(rows, left_w, right_w, body_height)
        self._draw_hints(height - 2, width)
        self._draw_status(height - 1, width, rows, body_height)
        self.message = ""
        self.message_kind = "info"
        self.screen.refresh()

    def _clamp_scroll(self, total: int, body_height: int):
        if self.task_idx < self.scroll:
            self.scroll = self.task_idx
        if self.task_idx >= self.scroll + body_height:
            self.scroll = self.task_idx - body_height + 1
        self.scroll = max(0, min(self.scroll, max(0, total - body_height)))

    # -- ヘッダ

    def _draw_header(self, width: int):
        path = self.ctx.store.tasks_path
        left = [(" %s " % BRAND, "brand"), (" ", None)]
        parent = _short_path(path.parent)
        if parent:
            left.append((parent.rstrip("/") + "/", "header.dir"))
        left.append((path.name, "header.file"))

        base = self.ctx.base
        date_text = "%s (%s)" % (base.strftime("%Y/%m/%d"), WEEKDAYS[base.weekday()])
        right = self._count_spans() + [("  %s " % date_text, "header.meta")]
        # 右側が場所を取りすぎるときは件数、次に日付の順に諦める（パスを優先）
        if _spans_width(right) > width * 2 // 3:
            right = [("  %s " % date_text, "header.meta")]
        if _spans_width(right) > width // 2:
            right = []
        self._draw_line(0, 0, width, left, right)

    def _count_spans(self):
        """未処理タスクのステータス別件数。0 件のものは出さない。

        完了・やらないはアーカイブで消えていくため数えない。
        """
        counts = {}
        for task in self.doc.all_tasks():
            if task.is_terminal:
                continue
            counts[task.status] = counts.get(task.status, 0) + 1
        spans = []
        for status in model.STATUSES:
            n = counts.get(status, 0)
            if not n:
                continue
            glyph = self.theme.status_glyph(status) or status[:1]
            spans.append(("  %s%d" % (glyph, n), self.theme.status_role(status)))
        return spans

    # -- ペインの枠

    def _draw_panes(self, width: int, left_w: int, right_w: int, body_height: int):
        top, bottom = 1, 1 + body_height + 1
        self._pane_frame(top, bottom, 0, left_w, "Sections", self.focus == "sections")
        self._pane_frame(top, bottom, left_w + 2, right_w,
                         self._task_pane_title(), self.focus == "tasks")

    def _task_pane_title(self) -> str:
        if self.filter:
            kind = "Tag" if self.filter[0] == "tag" else "Search"
            return "%s: %s" % (kind, self.filter[1])
        section = self.current_section()
        return "Tasks" if section is None else "Tasks / %s" % section.name

    def _pane_frame(self, top: int, bottom: int, x: int, w: int, title: str, active: bool):
        t = self.theme
        border = t.attr("border.active" if active else "border")
        line, side = t.glyph("hline"), t.glyph("vline")
        head = truncate(" %s " % title, max(0, w - 2), "…") if title else ""
        self._addstr(top, x, t.glyph("corner_tl") + line, border)
        self._addstr(top, x + 2, head, t.attr("pane.title" if active else "pane.title.blur"))
        rest = w - 1 - display_width(head)
        if rest > 0:
            self._addstr(top, x + 2 + display_width(head), line * rest, border)
        self._addstr(top, x + 1 + w, t.glyph("corner_tr"), border)
        for y in range(top + 1, bottom):
            self._addstr(y, x, side, border)
            self._addstr(y, x + 1 + w, side, border)
        self._addstr(bottom, x,
                     t.glyph("corner_bl") + line * w + t.glyph("corner_br"), border)

    # -- 左ペイン

    def _draw_sections(self, sections, left_w: int, body_height: int):
        t = self.theme
        for i in range(body_height):
            y = 2 + i
            if i >= len(sections):
                self._draw_spans(y, 1, [], left_w)
                continue
            section = sections[i]
            count = len(section.tasks)
            selected = i == self.section_idx
            # 幅が足りないときは記号を諦めてセクション名に桁を回す
            with_glyph = left_w >= 14
            name_w = max(3, left_w - (7 if with_glyph else 5))
            mark = "%3d" % count if count else "·"
            mark = " " * max(0, 3 - display_width(mark)) + mark
            spans = [(" ", None)]
            if with_glyph:
                spans.append((_cell(t.glyph("section"), 2), t.section_role(section.name)))
            spans += [
                (pad(section.name, name_w), "section" if count else "section.empty"),
                (mark, "section.count" if count else "section.count.zero"),
                (" ", None),
            ]
            base = None
            if selected:
                base = t.attr("cursor" if self.focus == "sections" else "cursor.blur")
            self._draw_spans(y, 1, spans, left_w, base=base)

    # -- 右ペイン

    def _draw_tasks(self, rows, left_w: int, right_w: int, body_height: int):
        t = self.theme
        x = left_w + 3
        for i in range(body_height):
            y = 2 + i
            index = self.scroll + i
            if not rows:
                text = ("  該当なし  Esc で解除" if self.filter
                        else "  タスクはありません  J/K または Tab でセクション移動")
                self._draw_spans(y, x, [(text, "empty")] if i == 0 else [], right_w)
                continue
            if index >= len(rows):
                self._draw_spans(y, x, [], right_w)
                continue
            task, depth = rows[index]
            selected = index == self.task_idx
            base = None
            if selected:
                base = t.attr("cursor" if self.focus == "tasks" else "cursor.blur")
            self._draw_spans(y, x, self._task_spans(task, depth, selected), right_w, base=base)
        self._draw_scrollbar(left_w + 3 + right_w, len(rows), body_height)

    def _task_spans(self, task, depth: int, selected: bool):
        t = self.theme
        spans = [(_cell(t.glyph("cursor"), 2) if selected else "  ", "cursor.bar")]
        if depth:
            spans.append(("  " * (depth - 1), None))
            spans.append((_cell(t.glyph("tree_last" if _is_last_child(task)
                                        else "tree_mid"), 2), "tree"))
        if self.filter and task.section:
            spans.append(("[%s] " % task.section, "badge"))
        if task.status != model.TODO or task.status_explicit:
            glyph = t.status_glyph(task.status)
            chip = "%s %s  " % (glyph, task.status_label()) if glyph \
                else "【%s】" % task.status_label()
            spans.append((chip, t.status_role(task.status)))
        spans.append((task.title, "title.done" if task.is_terminal else "title"))
        if task.due:
            spans.append((" %s%s" % (t.glyph("due"), util.format_date(task.due)),
                          self._due_role(task.due)))
        for tag in task.tags:
            spans.append((" #%s" % tag, "tag"))
        if task.detail:
            # 詳細文は一覧に出せないので、あることだけ示して Enter へ誘導する。
            spans.append((" %s" % t.glyph("detail"), "detail.mark"))
        return spans

    def _due_role(self, due) -> str:
        """期日の色。近いほど強い色にする。"""
        base = self.ctx.base
        if due < base:
            return "due.over"
        if due == base:
            return "due.today"
        if due <= util.week_end(base):
            return "due.soon"
        return "due"

    def _draw_scrollbar(self, x: int, total: int, body_height: int):
        if total <= body_height:
            return
        t = self.theme
        size = max(1, body_height * body_height // total)
        span = body_height - size
        top = 0 if span <= 0 else round(self.scroll * span / (total - body_height))
        for i in range(body_height):
            inside = top <= i < top + size
            self._addstr(2 + i, x,
                         t.glyph("scroll_thumb") if inside else t.glyph("scroll_track"),
                         t.attr("scrollbar.thumb" if inside else "scrollbar"))

    # -- 下段

    def _draw_hints(self, y: int, width: int):
        spans = []
        for key, label in help_pairs(width - 2):
            if spans:
                spans.append(("  ", None))
            spans.append((key, "hint.key"))
            spans.append((":" + label, "hint.label"))
        self._draw_spans(y, 1, spans, width - 2)

    def _draw_status(self, y: int, width: int, rows, body_height: int):
        t = self.theme
        section = self.current_section()
        name = self.filter[1] if self.filter else (section.name if section else "-")
        spans = [(" %s " % truncate(name, 24, "…"), "bar.mode")]
        if t.powerline:
            spans.append((t.separator(), "bar.sep.mode"))
        spans.append((" %d/%d " % (self.task_idx + 1, len(rows)) if rows else " 0/0 ",
                      "bar.pos"))
        if t.powerline:
            spans.append((t.separator(), "bar.sep.pos"))
        if self.message:
            spans.append((" " + self.message,
                          "bar.error" if self.message_kind == "error" else "bar.msg"))
        elif self.filter:
            spans.append(("  Esc で検索を解除", "bar.dim"))
        right = [(" %s " % _scroll_label(self.scroll, len(rows), body_height), "bar.dim")]
        self._draw_line(y, 0, width, spans, right, fill_role="bar")

    # ------------------------------------------------------------ 描画部品

    def _draw_spans(self, y: int, x: int, spans, width: int, fill_role=None, base=None):
        """spans を左から書き、余りを空白で埋める。

        base に属性を渡すと span ごとの色を捨ててその属性で塗る（選択行）。
        """
        left = width
        cx = x
        for text, role in spans:
            if left <= 0:
                break
            if display_width(text) > left:
                text = truncate(text, left)
            attr = base if base is not None else self.theme.attr(role) if role else 0
            self._addstr(y, cx, text, attr)
            cx += display_width(text)
            left -= display_width(text)
        if left > 0:
            attr = base if base is not None else (self.theme.attr(fill_role) if fill_role else 0)
            self._addstr(y, cx, " " * left, attr)

    def _draw_line(self, y: int, x: int, width: int, left_spans, right_spans=(),
                   fill_role=None):
        """左寄せと右寄せを1行に収める。入りきらない右寄せは捨てる。"""
        right_w = _spans_width(right_spans)
        if right_w >= width:
            right_spans, right_w = (), 0
        self._draw_spans(y, x, left_spans, width - right_w, fill_role)
        if right_spans:
            self._draw_spans(y, x + width - right_w, right_spans, right_w, fill_role)

    def _addstr(self, y: int, x: int, text: str, attr: int = 0):
        """画面外へはみ出さないように書き込む。"""
        height, width = self.screen.getmaxyx()
        if not (0 <= y < height) or x < 0 or x >= width:
            return
        # 最下行の右端セルには書かない。書くと端末が1行スクロールしてしまう。
        limit = width - x - (1 if y == height - 1 else 0)
        text = truncate(text, limit, "")
        if not text:
            return
        try:
            self.screen.addstr(y, x, text, attr)
        except curses.error:
            pass

    # ------------------------------------------------------------ 入力部品

    def prompt(self, label: str, initial: str = "") -> str | None:
        """ステータスバーでの1行入力。Esc で中断すると None を返す。

        カーソルを動かして途中を直せる（末尾から消し直さずに済むように）。
        キーは PROMPT_KEYS のとおり。入力欄より長い文字列は横に送る。
        """
        height, width, _, _ = self.geometry()
        buf = list(initial)
        pos = len(buf)
        view = 0  # 入力欄の左端に出す文字の位置（横スクロール）
        curses.curs_set(1)
        try:
            while True:
                spans = [(" %s " % label, "bar.mode")]
                if self.theme.powerline:
                    spans.append((self.theme.separator(), "bar.sep.end"))
                # 入力欄は帯の右隣、先頭の空白 1 桁を空けた位置から始まる
                field_x = _spans_width(spans) + 1
                field_w = max(4, width - field_x)
                view = _input_view(buf, pos, view, field_w)
                spans.append((" " + "".join(buf[view:]), "bar"))
                self._draw_spans(height - 1, 0, spans, width, fill_role="bar")
                cursor = min(width - 1,
                             field_x + display_width("".join(buf[view:pos])))
                try:
                    self.screen.move(height - 1, cursor)
                except curses.error:
                    pass
                self.screen.refresh()
                try:
                    key = self.screen.get_wch()
                except curses.error:
                    continue
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(buf).strip()
                if key == "\x1b":
                    return None
                pos = self._edit(buf, pos, key)
        finally:
            curses.curs_set(0)

    def _edit(self, buf: list, pos: int, key) -> int:
        """入力欄のキーを 1 つ処理し、新しいカーソル位置を返す。

        buf はその場で書き換える。知らないキーは無視する。
        """
        if key in (curses.KEY_LEFT, "\x02"):        # ← / Ctrl-b
            return max(0, pos - 1)
        if key in (curses.KEY_RIGHT, "\x06"):       # → / Ctrl-f
            return min(len(buf), pos + 1)
        if key in (curses.KEY_HOME, "\x01"):        # Home / Ctrl-a
            return 0
        if key in (curses.KEY_END, "\x05"):         # End / Ctrl-e
            return len(buf)
        if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if pos:
                del buf[pos - 1]
                return pos - 1
            return pos
        if key in (curses.KEY_DC, "\x04"):          # Delete / Ctrl-d
            if pos < len(buf):
                del buf[pos]
            return pos
        if key == "\x15":                           # Ctrl-u: 手前を全部消す
            del buf[:pos]
            return 0
        if key == "\x0b":                           # Ctrl-k: 後ろを全部消す
            del buf[pos:]
            return pos
        if key == "\x17":                           # Ctrl-w: 直前の語を消す
            start = pos
            while start and buf[start - 1] == " ":
                start -= 1
            while start and buf[start - 1] != " ":
                start -= 1
            del buf[start:pos]
            return start
        if isinstance(key, str) and key.isprintable():
            buf.insert(pos, key)
            return pos + 1
        return pos

    def ask(self, question: str) -> bool:
        """y/n の確認。TUI 未初期化の起動直後にも使えるようにしている。"""
        height, width, _, _ = self.geometry()
        spans = [(" 確認 ", "bar.mode")]
        if self.theme.powerline:
            spans.append((self.theme.separator(), "bar.sep.end"))
        spans += [(" " + question, "bar.msg"), ("  [y/N]", "bar.dim")]
        self._draw_spans(height - 1, 0, spans, width, fill_role="bar")
        self.screen.refresh()
        while True:
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if isinstance(key, int):
                return False
            if key in ("y", "Y"):
                return True
            return False

    def choose(self, title: str, options: list[str], marks=None) -> int | None:
        """一覧から1つ選ぶモーダル。Esc で中断すると None を返す。"""
        index = 0
        while True:
            lines = []
            for i, option in enumerate(options):
                mark, role = (marks[i] if marks else ("", "popup.text"))
                lines.append([("  %d " % (i + 1), "popup.label"),
                              (_cell(mark, 2) if mark else "", role),
                              (option, "popup.text")])
            # 画面が低いときは選択中の項目が箱に入るよう窓をずらす
            visible = self._popup_rows(len(lines))
            offset = max(0, min(index - visible + 1, len(lines) - visible))
            self._popup_draw(title, lines, offset=offset, selected=index,
                             hint="j/k 移動   Enter 決定   Esc 中止")
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key in (curses.KEY_DOWN, "j"):
                index = (index + 1) % len(options)
            elif key in (curses.KEY_UP, "k"):
                index = (index - 1) % len(options)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                return index
            elif key == "\x1b":
                return None
            elif isinstance(key, str) and key.isdigit() and 1 <= int(key) <= len(options):
                return int(key) - 1

    def popup(self, title: str, lines):
        """読み取り専用のモーダル。j/k でスクロールし、他のキーで閉じる。

        j/k は端まで来ても閉じる操作にしない。詳細文を読み進めた勢いで
        モーダルが消えてしまうため（popup_ask と同じ扱い）。
        """
        offset = 0
        while True:
            visible = self._popup_rows(len(lines))
            more = len(lines) > visible
            hint = "j/k スクロール   他のキーで閉じる" if more else "任意のキーで閉じる"
            self._popup_draw(title, lines, offset=offset, hint=hint)
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key in (curses.KEY_DOWN, "j"):
                offset = min(offset + 1, max(0, len(lines) - visible))
            elif key in (curses.KEY_UP, "k"):
                offset = max(0, offset - 1)
            else:
                return

    def popup_ask(self, title: str, lines, hint: str) -> bool:
        """一覧を見せたうえで y/n を聞くモーダル。y 以外は「いいえ」。

        popup と違って j/k は閉じる操作ではなくスクロールに使う。
        長い一覧を読もうとして中止してしまうのを避けるため。
        """
        offset = 0
        while True:
            visible = self._popup_rows(len(lines))
            self._popup_draw(title, lines, offset=offset, hint=hint)
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key in (curses.KEY_DOWN, "j"):
                offset = min(offset + 1, max(0, len(lines) - visible))
            elif key in (curses.KEY_UP, "k"):
                offset = max(0, offset - 1)
            elif key in ("y", "Y"):
                return True
            else:
                return False

    def _popup_rows(self, count: int, hint: bool = True) -> int:
        """モーダルの本文に入る行数（案内行を除く）。スクロール量の上限になる。"""
        height, _, _, _ = self.geometry()
        margin = 2 if hint else 0
        box_height = min(height - 2, count + margin + 4)
        return max(1, box_height - 4 - margin)

    def _popup_draw(self, title: str, lines, offset: int = 0, selected=None,
                    hint: str | None = None):
        """中央に枠付きの箱を描く。lines は文字列か span の並び。

        lines は全行を受け取り、offset から入るだけを描く。箱の幅は見えている
        行ではなく全行から決める。スクロールのたびに箱の幅が変わると、
        読んでいる文章が組み替わって見えるため。
        """
        t = self.theme
        height, width, _, _ = self.geometry()
        rendered = [_as_spans(line) for line in lines]
        content_w = max([_spans_width(s) for s in rendered] or [0])
        content_w = max(content_w, display_width(title) + 2,
                        display_width(hint or "") + 2)
        inner = max(24, min(width - 6, content_w + 2))
        margin = 2 if hint else 0
        box_height = self._popup_rows(len(rendered), bool(hint)) + margin + 4
        rendered = rendered[offset:]
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - inner - 2) // 2)
        border = t.attr("popup.border")
        line, side = t.glyph("hline"), t.glyph("vline")

        self._addstr(top, left,
                     t.glyph("corner_tl") + line * inner + t.glyph("corner_tr"), border)
        self._addstr(top + 1, left, side, border)
        self._draw_spans(top + 1, left + 1, [(" " + truncate(title, inner - 2), "popup.title")],
                         inner, base=t.attr("popup.title"))
        self._addstr(top + 1, left + 1 + inner, side, border)
        self._addstr(top + 2, left,
                     t.glyph("tee_l") + line * inner + t.glyph("tee_r"), border)

        body = box_height - 4
        for i in range(body):
            y = top + 3 + i
            self._addstr(y, left, side, border)
            spans = rendered[i] if i < len(rendered) else []
            if hint and i == body - 1:
                spans = [("  " + hint, "popup.hint")]
            elif hint and i == body - 2:
                spans = []
            base = (t.attr("cursor")
                    if selected is not None and offset + i == selected else None)
            self._draw_spans(y, left + 1, spans, inner, base=base)
            self._addstr(y, left + 1 + inner, side, border)
        self._addstr(top + box_height - 1, left,
                     t.glyph("corner_bl") + line * inner + t.glyph("corner_br"), border)
        self.screen.refresh()


# ---------------------------------------------------------------- 補助


def _as_spans(line):
    return [(line, "popup.text")] if isinstance(line, str) else list(line)


def _input_view(buf: list, pos: int, view: int, width: int) -> int:
    """入力欄の左端に出す文字位置。カーソルが欄からはみ出さないようにずらす。"""
    view = min(view, pos)
    while view < pos and display_width("".join(buf[view:pos])) >= width:
        view += 1
    # 消して短くなったときは左へ戻す（末尾まで収まるなら全部見せる）
    while view > 0 and display_width("".join(buf[view - 1:])) < width:
        view -= 1
    return view


def _spans_width(spans) -> int:
    return sum(display_width(text) for text, _ in spans)


def _cell(text: str, width: int) -> str:
    """記号1個分など、幅を固定したい短い文字列を width 桁に整える。

    罫線や記号は端末によって1桁にも2桁にも描かれるため、切り詰めてから
    右を空白で埋め、桁ずれを起こさないようにする。
    """
    return pad(truncate(text, width, ""), width)


def _is_last_child(task) -> bool:
    return task.parent is not None and task.parent.children[-1] is task


def _short_path(path) -> str:
    """ホーム以下は ~ に畳む。"""
    text = str(path)
    home = os.path.expanduser("~")
    if home and home != "~" and text.startswith(home):
        return "~" + text[len(home):]
    return text


def _scroll_label(scroll: int, total: int, body_height: int) -> str:
    """右下に出す表示位置。yazi の All / Top / Bot に倣う。"""
    if total <= body_height:
        return "全て"
    if scroll <= 0:
        return "先頭"
    last = total - body_height
    if scroll >= last:
        return "末尾"
    return "%d%%" % round(scroll * 100 / last)
