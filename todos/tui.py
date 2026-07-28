"""タスク操作に特化した TUI。Markdown の書き換えは ops / schedule に委譲する。"""

from __future__ import annotations

import curses
import locale

from . import editor, model, ops, schedule, validate
from .util import TodosError, display_width, pad, truncate

LEFT_WIDTH = 16
MIN_WIDTH = 40
MIN_HEIGHT = 10

HELP_LINE = (
    "j/k:移動  Enter:詳細  a:追加  i:編集  s:状態  d:完了  x:やらない  "
    "m:移動  /:検索  t:タグ  r:再配置  A:整理  w:警告  e:編集  ?:ヘルプ  q:終了"
)

HELP_TEXT = [
    "キー操作",
    "",
    "  j / ↓        次のタスクへ",
    "  k / ↑        前のタスクへ",
    "  g / G        先頭 / 末尾へ",
    "  h / l / Tab  セクションとタスクの行き来",
    "  J / K        次 / 前のセクションへ",
    "  Enter        タスクの詳細を表示",
    "  a            タスクを追加",
    "  i            タイトルを簡易編集",
    "  s            ステータスを変更",
    "  d            完了にする",
    "  x            やらないにする",
    "  m            セクションを移動",
    "  /            キーワード検索",
    "  t            タグ検索",
    "  Esc          検索結果を解除",
    "  r            自動再配置 (rollover)",
    "  A            アーカイブ",
    "  w            構文上の警告を表示",
    "  e            外部エディタで編集",
    "  R            ファイルを読み直す",
    "  q            終了",
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
        self.filter = None  # (種別, 語) の組。検索結果を表示中なら非 None
        self.quit = False
        curses.curs_set(0)
        self.screen.keypad(True)
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

    # ------------------------------------------------------------ 起動時の提案

    def _startup_maintenance(self):
        if getattr(self.ctx.args, "no_prompt", False):
            return
        if schedule.needs_rollover(self.doc, self.ctx.base):
            if self.ask("日付が更新されています。タスクを再配置しますか？"):
                moved = schedule.rollover(self.doc, self.ctx.base)
                self.save()
                self.message = "%d 件を再配置しました。" % len(moved)
        pending = ops.archivable(self.doc)
        if pending:
            if self.ask("終了したタスクが %d 件あります。アーカイブしますか？" % len(pending)):
                archived = ops.archive(self.doc, self.ctx.store, self.ctx.base)
                self.save()
                self.message = "%d 件をアーカイブしました。" % len(archived)

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
                self.message = str(exc)
        return 0

    def handle(self, key):
        rows = self.rows()
        if key == curses.KEY_RESIZE:
            return
        if isinstance(key, int):
            mapping = {
                curses.KEY_DOWN: "j",
                curses.KEY_UP: "k",
                curses.KEY_LEFT: "h",
                curses.KEY_RIGHT: "l",
                curses.KEY_ENTER: "\n",
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
        elif key in ("\t", "h", "l"):
            self.focus = "sections" if self.focus == "tasks" else "tasks"
        elif key == "J":
            self.change_section(1)
        elif key == "K":
            self.change_section(-1)
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self.show_detail()
        elif key == "a":
            self.add_task()
        elif key == "i":
            self.rename_task()
        elif key == "s":
            self.change_status()
        elif key == "d":
            self.terminal_status(model.DONE)
        elif key == "x":
            self.terminal_status(model.SKIP)
        elif key == "m":
            self.move_task()
        elif key == "/":
            self.start_search("keyword")
        elif key == "t":
            self.start_search("tag")
        elif key == "\x1b":
            if self.filter:
                self.filter = None
                self.task_idx = 0
                self.message = "検索を解除しました。"
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
            self.message = "読み直しました。"
        elif key == "?":
            self.popup(HELP_TEXT)

    # ------------------------------------------------------------ カーソル

    def move_cursor(self, delta: int, total: int):
        if self.focus == "sections":
            self.change_section(delta)
            return
        if total:
            self.task_idx = max(0, min(self.task_idx + delta, total - 1))

    def set_cursor(self, index: int):
        if self.focus == "sections":
            self.section_idx = max(0, min(index, len(self.sections()) - 1))
            self.task_idx = 0
        else:
            self.task_idx = index

    def change_section(self, delta: int):
        sections = self.sections()
        if not sections:
            return
        self.filter = None
        self.section_idx = max(0, min(self.section_idx + delta, len(sections) - 1))
        self.task_idx = 0
        self.scroll = 0

    # ------------------------------------------------------------ 操作

    def add_task(self):
        title = self.prompt("追加するタスク:")
        if not title:
            return
        task = ops.add(self.doc, title, self.ctx.base)
        self.save()
        self.filter = None
        self.jump_to(task)
        self.message = "追加しました: %s (%s)" % (task.title, task.section)

    def rename_task(self):
        task = self.current_task()
        if task is None:
            return
        title = self.prompt("タイトル:", task.title)
        if title is None or not title.strip():
            return
        ops.set_title(task, title)
        self.save()
        self.message = "更新しました。"

    def change_status(self):
        task = self.current_task()
        if task is None:
            return
        choice = self.choose("ステータスを選択", list(model.STATUSES))
        if choice is None:
            return
        status = model.STATUSES[choice]
        if status in model.TERMINAL_STATUSES:
            self.terminal_status(status)
            return
        who = None
        if status in model.COUNTERPART_STATUSES:
            who = self.prompt("相手（任意）:", task.counterpart or "")
            if who is None:
                return
        ops.set_status(task, status, who or None)
        self.save()
        self.message = "ステータスを【%s】にしました。" % task.status_label()

    def terminal_status(self, status: str):
        task = self.current_task()
        if task is None:
            return
        pending = ops.open_children(task)
        if pending and not self.ask(
            "未完了の子タスクが %d 件あります。続行しますか？" % len(pending)
        ):
            self.message = "中止しました。"
            return
        ops.set_status(task, status)
        self.save()
        self.message = "【%s】にしました。" % status

    def move_task(self):
        task = self.current_task()
        if task is None:
            return
        if task.parent is not None:
            self.message = "子タスクは単独で移動できません。"
            return
        choice = self.choose("移動先セクション", list(model.SECTIONS))
        if choice is None:
            return
        schedule.move(self.doc, task, model.SECTIONS[choice])
        self.save()
        self.filter = None
        self.jump_to(task)
        self.message = "%s へ移動しました。" % task.section

    def start_search(self, kind: str):
        label = "キーワード検索:" if kind == "keyword" else "タグ検索: #"
        word = self.prompt(label)
        if not word:
            return
        self.filter = (kind, word)
        self.task_idx = 0
        self.scroll = 0
        self.focus = "tasks"
        self.message = "Esc で検索を解除します。"

    def do_rollover(self):
        moves = schedule.misplaced(self.doc, self.ctx.base)
        if not moves:
            self.message = "再配置の必要はありません。"
            return
        if not self.ask("%d 件を再配置します。実行しますか？" % len(moves)):
            return
        applied = schedule.rollover(self.doc, self.ctx.base)
        self.save()
        self.task_idx = 0
        self.message = "%d 件を再配置しました。" % len(applied)

    def do_archive(self):
        targets = ops.archivable(self.doc)
        if not targets:
            self.message = "アーカイブ対象のタスクはありません。"
            return
        if not self.ask("%d 件をアーカイブします。実行しますか？" % len(targets)):
            return
        archived = ops.archive(self.doc, self.ctx.store, self.ctx.base)
        self.save()
        self.task_idx = 0
        self.message = "%d 件をアーカイブしました。" % len(archived)

    def show_warnings(self):
        found = validate.validate(self.doc)
        if not found:
            self.popup(["構文上の警告", "", "  問題は見つかりませんでした。"])
            return
        lines = ["構文上の警告 (%d 件)" % len(found), ""]
        lines += [issue.format() for issue in found]
        self.popup(lines)

    def show_detail(self):
        task = self.current_task()
        if task is None:
            return
        lines = [
            task.title,
            "",
            "  ステータス: %s" % task.status_label(),
            "  期日      : %s" % (task.due.strftime("%Y/%m/%d") if task.due else "なし"),
            "  タグ      : %s" % (" ".join("#" + t for t in task.tags) or "なし"),
            "  セクション: %s" % (task.section or "-"),
        ]
        if task.detail:
            lines += ["", "  詳細:"] + ["    %s" % line for line in task.detail]
        if task.children:
            lines += ["", "  子タスク:"]
            lines += ["    %s%s" % ("  " * (c.depth - task.depth - 1), c.display_title())
                      for c in task.descendants()]
        self.popup(lines)

    def external_edit(self):
        task = self.current_task()
        if task is None:
            return
        curses.def_prog_mode()
        curses.endwin()
        try:
            changed = editor.edit_task(self.doc, task)
        finally:
            curses.reset_prog_mode()
            self.screen.clear()
        if changed:
            self.save()
            self.message = "外部エディタの内容を反映しました。"
        else:
            self.message = "変更はありません。"

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

    # ------------------------------------------------------------ 描画

    def geometry(self):
        height, width = self.screen.getmaxyx()
        left = min(LEFT_WIDTH, max(10, width // 4))
        right = width - left - 3
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
        title = "Tasks" if not self.filter else "Search: %s" % self.filter[1]
        self._addstr(0, 0, _hline("┌", "┬", "┐", left_w, right_w, "Sections", title))

        sections = self.sections()
        rows = self.rows()
        if self.task_idx < self.scroll:
            self.scroll = self.task_idx
        if self.task_idx >= self.scroll + body_height:
            self.scroll = self.task_idx - body_height + 1
        self.scroll = max(0, min(self.scroll, max(0, len(rows) - body_height)))

        for i in range(body_height):
            y = 1 + i
            left_text = ""
            if i < len(sections):
                section = sections[i]
                count = sum(1 for _ in section.tasks)
                left_text = " %s%s" % (
                    pad(section.name, left_w - 5),
                    ("%3d" % count) if count else "  ·",
                )
            right_text = ""
            row_index = self.scroll + i
            if row_index < len(rows):
                task, depth = rows[row_index]
                marker = ">" if row_index == self.task_idx and self.focus == "tasks" else " "
                prefix = "%s %s" % (marker, "  " * depth)
                label = task.display_title()
                if self.filter:
                    label = "[%s] %s" % (task.section, label)
                right_text = truncate(prefix + label, right_w)
            self._addstr(y, 0, "│%s│%s│" % (pad(left_text, left_w), pad(right_text, right_w)))

            if i < len(sections) and i == self.section_idx and self.focus == "sections":
                self._addstr(y, 1, pad(left_text, left_w), curses.A_REVERSE)
            if row_index < len(rows) and row_index == self.task_idx:
                attr = curses.A_REVERSE if self.focus == "tasks" else curses.A_BOLD
                self._addstr(y, left_w + 2, pad(right_text, right_w), attr)

        self._addstr(height - 4, 0, _hline("├", "┴", "┤", left_w, right_w))
        self._addstr(height - 3, 0, "│%s│" % pad(truncate(HELP_LINE, width - 2), width - 2))
        status = self.message or self._status_line(rows)
        self._addstr(height - 2, 0, "│%s│" % pad(truncate(status, width - 2), width - 2))
        self._addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")
        self.message = ""
        self.screen.refresh()

    def _status_line(self, rows) -> str:
        section = self.current_section()
        name = self.filter[1] if self.filter else (section.name if section else "-")
        position = "%d/%d" % (self.task_idx + 1, len(rows)) if rows else "0/0"
        return " %s  %s  %s" % (name, position, self.ctx.store.tasks_path)

    def _addstr(self, y: int, x: int, text: str, attr: int = 0):
        try:
            self.screen.addstr(y, x, text, attr)
        except curses.error:
            pass

    # ------------------------------------------------------------ 入力部品

    def prompt(self, label: str, initial: str = "") -> str | None:
        """フッタ行での1行入力。Esc で中断すると None を返す。"""
        height, width, _, _ = self.geometry()
        buf = list(initial)
        curses.curs_set(1)
        try:
            while True:
                text = "%s %s" % (label, "".join(buf))
                self._addstr(height - 2, 0, "│%s│" % pad(truncate(text, width - 2), width - 2))
                cursor = min(width - 2, 1 + display_width(text))
                try:
                    self.screen.move(height - 2, cursor)
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
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if buf:
                        buf.pop()
                    continue
                if isinstance(key, str) and key.isprintable():
                    buf.append(key)
        finally:
            curses.curs_set(0)

    def ask(self, question: str) -> bool:
        """y/n の確認。TUI 未初期化の起動直後にも使えるようにしている。"""
        height, width, _, _ = self.geometry()
        text = "%s [y/N]" % question
        self._addstr(height - 2, 0, pad(truncate(text, width - 1), width - 1))
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

    def choose(self, title: str, options: list[str]) -> int | None:
        """一覧から1つ選ぶモーダル。Esc で中断すると None を返す。"""
        index = 0
        while True:
            lines = [title, ""]
            for i, option in enumerate(options):
                lines.append("  %s %s" % (">" if i == index else " ", option))
            lines += ["", "  j/k で移動  Enter で決定  Esc で中止"]
            self._popup_draw(lines)
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

    def popup(self, lines: list[str]):
        """読み取り専用のモーダル。上下でスクロールし、任意キーで閉じる。"""
        offset = 0
        while True:
            height, _, _, _ = self.geometry()
            visible = max(1, height - 6)
            self._popup_draw(lines[offset:offset + visible] + ["", "  任意のキーで閉じる"])
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key in (curses.KEY_DOWN, "j") and offset + visible < len(lines):
                offset += 1
            elif key in (curses.KEY_UP, "k") and offset > 0:
                offset -= 1
            else:
                return

    def _popup_draw(self, lines: list[str]):
        height, width, _, _ = self.geometry()
        inner = max(20, min(width - 6, max((display_width(x) for x in lines), default=20) + 2))
        box_height = min(height - 2, len(lines) + 2)
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - inner - 2) // 2)
        self._addstr(top, left, "┌" + "─" * inner + "┐")
        for i in range(box_height - 2):
            text = lines[i] if i < len(lines) else ""
            self._addstr(top + 1 + i, left, "│%s│" % pad(truncate(text, inner), inner))
        self._addstr(top + box_height - 1, left, "└" + "─" * inner + "┘")
        self.screen.refresh()


def _hline(left: str, mid: str, right: str, left_w: int, right_w: int,
           left_title: str = "", right_title: str = "") -> str:
    def segment(title: str, width: int) -> str:
        text = " %s " % title if title else ""
        text = truncate(text, width)
        return text + "─" * (width - display_width(text))

    return left + segment(left_title, left_w) + mid + segment(right_title, right_w) + right
