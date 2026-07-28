"""コマンドラインインターフェース。解析・操作ロジックは共通モジュールを使う。"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, editor, model, ops, schedule, store, util, validate
from .util import TodosError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AMBIGUOUS = 2


# ---------------------------------------------------------------- 実行文脈


class Context:
    """1回のコマンド実行で共有する状態。"""

    def __init__(self, args):
        self.args = args
        self.assume_yes = bool(getattr(args, "yes", False))
        self.json = bool(getattr(args, "json", False))
        self.base = util.today()
        self.store = store.Store(store.resolve_dir(getattr(args, "dir", None)))
        self.doc = None

    # -- 入出力

    @property
    def interactive(self) -> bool:
        return util.is_interactive() and not self.json

    def confirm(self, message: str) -> bool:
        return util.confirm(message, self.assume_yes)

    def out(self, text: str = "") -> None:
        print(text)

    def note(self, text: str) -> None:
        if not self.json:
            print(text, file=sys.stderr)

    def emit(self, payload) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    # -- 文書

    def load(self, create: bool = True):
        if not self.store.exists():
            if not create:
                raise TodosError(
                    "%s が見つかりません。`todos init` で作成できます。"
                    % self.store.tasks_path
                )
            self.store.init()
            self.note("%s を作成しました。" % self.store.tasks_path)
        self.doc = self.store.load(self.base)
        return self.doc

    def save(self) -> None:
        self.store.save(self.doc)

    # -- 起動時の提案（要件12.2 / 15.2）

    def offer_maintenance(self) -> None:
        if getattr(self.args, "no_prompt", False):
            return
        if not (self.interactive or self.assume_yes):
            return
        changed = False
        moves = schedule.misplaced(self.doc, self.base)
        if moves:
            # 件数だけでは何がどこへ動くのか分からないため、一覧と理由を先に出す。
            self.note("日付が変わり、次のタスクの配置がルールと食い違っています:")
            for line in format_moves(self.base, [(t, t.section, want) for t, want in moves]):
                self.note(line)
            self.note(schedule.RULE_SUMMARY)
            if self.confirm("再配置しますか？"):
                schedule.rollover(self.doc, self.base)
                changed = True
            else:
                self.note("配置は変えていません（次回も提案します。"
                          "止めるには --no-prompt を使います）。")
        pending = ops.archivable(self.doc)
        if pending:
            if self.confirm(
                "終了したタスクが %d 件あります。アーカイブしますか？" % len(pending)
            ):
                ops.archive(self.doc, self.store, self.base)
                changed = True
        if changed:
            self.save()


# ---------------------------------------------------------------- 表示


def format_moves(base, moves) -> list[str]:
    """(タスク, 移動前, 移動後) を「理由つきの移動一覧」として整形する。"""
    return [
        "  %s  %s  %s"
        % (
            util.pad(schedule.reason(task, base), 16),
            util.pad("%s -> %s" % (src, dst), 24),
            task.title,
        )
        for task, src, dst in moves
    ]


def format_task_line(task, show_index: bool = True) -> str:
    prefix = "%4d  " % task.index if show_index else "      "
    return "%s%s%s" % (prefix, "  " * task.depth, task.display_title())


def print_sections(ctx: Context, sections, empty_message: str = "(なし)") -> None:
    first = True
    for name, tasks in sections:
        if not first:
            ctx.out()
        first = False
        ctx.out(name)
        if not tasks:
            ctx.out("      %s" % empty_message)
            continue
        for task in tasks:
            ctx.out(format_task_line(task))
            for child in task.descendants():
                ctx.out(format_task_line(child))


def task_payload(ctx: Context, tasks) -> list:
    return [t.to_dict() for t in tasks]


# ---------------------------------------------------------------- タスク指定


def pick(ctx: Context, selector: str, section: str | None = None):
    """タスク指定子から1件に決める。曖昧なら候補を提示、非対話ならエラー。"""
    candidates = ops.resolve(ctx.doc, selector, section)
    if not candidates:
        raise TodosError("タスク「%s」が見つかりません。" % selector)
    if len(candidates) == 1:
        return candidates[0]
    if not ctx.interactive:
        lines = ["タスク「%s」を一意に特定できません。候補:" % selector]
        lines += ["  %d: %s (%s)" % (t.index, t.title, t.section) for t in candidates]
        lines.append("表示番号で指定してください。")
        raise Ambiguous("\n".join(lines))
    ctx.out("複数の候補があります:")
    for n, task in enumerate(candidates, start=1):
        ctx.out("  %d) [%d] %s (%s)" % (n, task.index, task.display_title(), task.section))
    try:
        answer = input("番号を選んでください [1-%d]: " % len(candidates)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise TodosError("中断しました。")
    if not answer.isdigit() or not 1 <= int(answer) <= len(candidates):
        raise TodosError("選択を解釈できませんでした。")
    return candidates[int(answer) - 1]


class Ambiguous(TodosError):
    """対象を一意に特定できなかった場合の例外（終了コード2）。"""


def parse_due_option(text: str | None, base):
    if text is None:
        return None
    if text.strip() in ("", "none", "なし", "-"):
        return None
    due = util.parse_date(text, base)
    if due is None:
        raise TodosError("期日「%s」を解釈できません。YYYY/MM/DD で指定してください。" % text)
    return due


def parse_status_option(text: str | None):
    if text is None:
        return None
    status = model.canonical_status(text)
    if status is None:
        raise TodosError(
            "未知のステータス「%s」です。使用できるのは %s です。"
            % (text, "・".join(model.STATUSES))
        )
    return status


def parse_section_option(text: str | None):
    if text is None:
        return None
    name = model.canonical_section(text)
    if name is None:
        raise TodosError(
            "未知のセクション「%s」です。使用できるのは %s です。"
            % (text, "・".join(model.SECTIONS))
        )
    return name


# ---------------------------------------------------------------- 各コマンド


def cmd_init(ctx: Context) -> int:
    created = ctx.store.init()
    if ctx.json:
        ctx.emit({"dir": str(ctx.store.dir), "created": created})
        return EXIT_OK
    if created:
        ctx.out("%s を作成しました。" % ctx.store.tasks_path)
    else:
        ctx.out("%s は既に存在します。" % ctx.store.tasks_path)
    return EXIT_OK


def cmd_add(ctx: Context) -> int:
    args = ctx.args
    ctx.load()
    ctx.doc.ensure_sections()
    status = parse_status_option(args.status) or model.TODO
    due = parse_due_option(args.due, ctx.base)
    section = parse_section_option(args.section)
    parent = pick(ctx, args.parent) if args.parent else None
    if parent is not None and section:
        raise TodosError(
            "--parent と --section は同時に指定できません。子タスクは親と同じセクションに置かれます。"
        )

    task = ops.add(
        ctx.doc,
        title=args.title,
        base=ctx.base,
        status=status,
        counterpart=args.who,
        due=due,
        tags=args.tag,
        detail=args.detail,
        section=section,
        parent=parent,
    )
    ctx.save()
    if ctx.json:
        ctx.emit(task.to_dict())
    else:
        ctx.out("追加しました: [%d] %s" % (task.index, task.display_title()))
        ctx.out("  セクション: %s" % task.section)
    return EXIT_OK


def cmd_list(ctx: Context) -> int:
    args = ctx.args
    ctx.load()
    ctx.offer_maintenance()
    section = parse_section_option(args.section)
    status = parse_status_option(args.status)

    groups = []
    for sec in ctx.doc.known_sections():
        if section and sec.name != section:
            continue
        tasks = sec.tasks
        if status:
            tasks = [t for t in tasks if t.status == status]
        if args.tag:
            tag = args.tag.lstrip("#").lower()
            tasks = [t for t in tasks if tag in [x.lower() for x in t.tags]]
        groups.append((sec.name, tasks))

    if ctx.json:
        ctx.emit(
            {
                "date": util.format_date(ctx.base),
                "dir": str(ctx.store.dir),
                "sections": [
                    {"name": name, "tasks": task_payload(ctx, tasks)}
                    for name, tasks in groups
                ],
            }
        )
        return EXIT_OK
    print_sections(ctx, groups)
    return EXIT_OK


def cmd_show(ctx: Context) -> int:
    ctx.load()
    task = pick(ctx, ctx.args.selector, parse_section_option(ctx.args.section))
    if ctx.json:
        ctx.emit(task.to_dict())
        return EXIT_OK
    ctx.out("[%d] %s" % (task.index, task.title))
    ctx.out("  ステータス: %s" % task.status_label())
    ctx.out("  期日      : %s" % (util.format_date(task.due) if task.due else "なし"))
    ctx.out("  タグ      : %s" % (" ".join("#" + t for t in task.tags) or "なし"))
    ctx.out("  セクション: %s" % (task.section or "-"))
    ctx.out("  行番号    : %d" % task.line_no)
    if task.detail:
        ctx.out("  詳細:")
        for line in task.detail:
            ctx.out("    %s" % line)
    if task.children:
        ctx.out("  子タスク:")
        for child in task.descendants():
            ctx.out("  %s" % format_task_line(child))
    return EXIT_OK


def cmd_edit(ctx: Context) -> int:
    args = ctx.args
    ctx.load()
    task = pick(ctx, args.selector, parse_section_option(args.section))
    touched = False

    if args.title:
        ops.set_title(task, args.title)
        touched = True
    if args.clear_due:
        ops.set_due(task, None)
        touched = True
    elif args.due:
        ops.set_due(task, parse_due_option(args.due, ctx.base))
        touched = True
    if args.add_tag:
        ops.add_tags(task, args.add_tag)
        touched = True
    if args.remove_tag:
        ops.remove_tags(task, args.remove_tag)
        touched = True
    if args.detail is not None:
        ops.set_detail(task, args.detail)
        touched = True

    if not touched or args.editor:
        replacement = editor.edit_task(ctx.doc, task)
        if replacement is None and not touched:
            ctx.out("変更はありません。")
            return EXIT_OK
        if replacement is not None:
            task = replacement

    ctx.save()
    if ctx.json:
        ctx.emit(task.to_dict())
    else:
        ctx.out("更新しました: %s" % task.display_title())
    return EXIT_OK


def cmd_status(ctx: Context) -> int:
    args = ctx.args
    ctx.load()
    status = parse_status_option(args.status)
    task = pick(ctx, args.selector, parse_section_option(args.section))
    return _apply_status(ctx, task, status, args.who)


def _apply_status(ctx: Context, task, status: str, who: str | None = None) -> int:
    if status in model.TERMINAL_STATUSES:
        pending = ops.open_children(task)
        if pending:
            message = "「%s」には未完了の子タスクが %d 件あります。続行しますか？" % (
                task.title,
                len(pending),
            )
            if not ctx.confirm(message):
                ctx.note("中止しました。")
                return EXIT_ERROR
    ops.set_status(task, status, who)
    ctx.save()
    if ctx.json:
        ctx.emit(task.to_dict())
    else:
        ctx.out("更新しました: %s" % task.display_title())
    return EXIT_OK


def cmd_done(ctx: Context) -> int:
    ctx.load()
    task = pick(ctx, ctx.args.selector, parse_section_option(ctx.args.section))
    return _apply_status(ctx, task, model.DONE)


def cmd_skip(ctx: Context) -> int:
    ctx.load()
    task = pick(ctx, ctx.args.selector, parse_section_option(ctx.args.section))
    return _apply_status(ctx, task, model.SKIP)


def cmd_move(ctx: Context) -> int:
    ctx.load()
    section = parse_section_option(ctx.args.to)
    task = pick(ctx, ctx.args.selector)
    schedule.move(ctx.doc, task, section)
    ctx.save()
    if ctx.json:
        ctx.emit(task.to_dict())
    else:
        ctx.out("移動しました: [%d] %s -> %s" % (task.index, task.title, section))
    return EXIT_OK


def cmd_search(ctx: Context) -> int:
    args = ctx.args
    ctx.load()
    results = ops.search(
        ctx.doc,
        keyword=args.keyword,
        tag=args.tag,
        status=parse_status_option(args.status),
        section=parse_section_option(args.section),
    )
    if ctx.json:
        ctx.emit({"count": len(results), "tasks": [t.to_dict(False) for t in results]})
        return EXIT_OK
    if not results:
        ctx.out("該当するタスクはありません。")
        return EXIT_OK
    for task in results:
        ctx.out("%4d  [%s] %s" % (task.index, task.section, task.display_title()))
    return EXIT_OK


def cmd_rollover(ctx: Context) -> int:
    ctx.load()
    moves = schedule.misplaced(ctx.doc, ctx.base)
    if not moves:
        if ctx.json:
            ctx.emit({"moved": []})
        else:
            ctx.out("再配置の必要はありません。")
        return EXIT_OK
    if not ctx.json:
        ctx.out("次のタスクを再配置します:")
        for line in format_moves(ctx.base, [(t, t.section, want) for t, want in moves]):
            ctx.out(line)
        ctx.out(schedule.RULE_SUMMARY)
    if not ctx.confirm("再配置を実行しますか？"):
        ctx.note("中止しました。")
        return EXIT_ERROR
    applied = schedule.rollover(ctx.doc, ctx.base)
    ctx.save()
    if ctx.json:
        ctx.emit(
            {
                "moved": [
                    {"title": t.title, "from": src, "to": dst} for t, src, dst in applied
                ]
            }
        )
    else:
        ctx.out("%d 件を再配置しました。" % len(applied))
        _hint_stuck(ctx)
    return EXIT_OK


def _hint_stuck(ctx: Context) -> None:
    """期日が迫っている保留タスクへの判断を促す（要件11.4）。"""
    today = ctx.doc.section(model.TODAY)
    if not today:
        return
    stuck = [t for t in today.tasks if t.status == model.HOLD]
    if not stuck:
        return
    ctx.out("")
    ctx.out("期日が迫っている保留タスクがあります。対応を検討してください:")
    for task in stuck:
        ctx.out("  [%d] %s" % (task.index, task.display_title()))
    ctx.out("  → 保留の解除 / 期日の変更 / 期日の削除 / やらないへの変更")


def cmd_archive(ctx: Context) -> int:
    ctx.load()
    targets = ops.archivable(ctx.doc)
    if not targets:
        if ctx.json:
            ctx.emit({"archived": []})
        else:
            ctx.out("アーカイブ対象のタスクはありません。")
        return EXIT_OK
    if not ctx.json:
        ctx.out("次のタスクを %s へ移動します:" % ctx.store.archive_path.name)
        for task in targets:
            ctx.out("  %s" % task.display_title())
    if not ctx.confirm("アーカイブを実行しますか？"):
        ctx.note("中止しました。")
        return EXIT_ERROR
    archived = ops.archive(ctx.doc, ctx.store, ctx.base)
    ctx.save()
    if ctx.json:
        ctx.emit(
            {
                "date": util.format_date(ctx.base),
                "archived": [t.to_dict() for t in archived],
            }
        )
    else:
        ctx.out("%d 件をアーカイブしました。" % len(archived))
    return EXIT_OK


def cmd_report(ctx: Context) -> int:
    ctx.load()
    data = ops.report(ctx.doc)
    if ctx.json:
        ctx.emit(
            {
                "date": util.format_date(ctx.base),
                "note": "履歴を保持しないため、当日の作業であることは保証されません。",
                "in_progress": [t.to_dict(False) for t in data["in_flight"]],
                "completed": [t.to_dict(False) for t in data["done"]],
                "skipped": [t.to_dict(False) for t in data["skipped"]],
            }
        )
        return EXIT_OK
    ctx.out("# 日報 %s" % util.format_date(ctx.base))
    _report_block(ctx, "手を付けたタスク（候補）", data["in_flight"])
    _report_block(ctx, "完了したタスク（候補）", data["done"])
    _report_block(ctx, "やらないと判断したタスク", data["skipped"])
    return EXIT_OK


def _report_block(ctx: Context, title: str, tasks) -> None:
    ctx.out("")
    ctx.out("## %s" % title)
    if not tasks:
        ctx.out("- （なし）")
        return
    for task in tasks:
        label = "【%s】" % task.status_label()
        ctx.out("- %s%s" % (label, task.title))


def cmd_validate(ctx: Context) -> int:
    ctx.load()
    found = validate.validate(ctx.doc)
    if ctx.json:
        ctx.emit(
            {
                "count": len(found),
                "issues": [i.to_dict() for i in found],
            }
        )
        return EXIT_OK if not found else EXIT_ERROR
    if not found:
        ctx.out("問題は見つかりませんでした。")
        return EXIT_OK
    ctx.out("%d 件の問題を検出しました:" % len(found))
    for issue in found:
        ctx.out(issue.format())

    if not ctx.args.fix:
        fixable = validate.fixable(found)
        if fixable:
            ctx.out("")
            ctx.out("%d 件は `todos validate --fix` で修正できます。" % len(fixable))
        return EXIT_ERROR

    applied = 0
    for issue in found:
        if not issue.fixable:
            continue
        if not ctx.confirm("%s: %s" % (issue.message, issue.fix_label)):
            continue
        if validate.apply_fix(ctx.doc, issue):
            applied += 1
    if applied:
        ctx.save()
        ctx.out("%d 件を修正しました。" % applied)
        return EXIT_OK
    ctx.out("修正は適用しませんでした。")
    return EXIT_ERROR


def cmd_tui(ctx: Context) -> int:
    from . import tui

    ctx.load()
    return tui.run(ctx)


# ---------------------------------------------------------------- 引数定義


def _common_options() -> argparse.ArgumentParser:
    """サブコマンドの前後どちらでも書ける共通オプション。

    default=SUPPRESS にすることで `todos --yes archive` と
    `todos archive --yes` の両方を同じ意味にする。
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--yes", "-y", action="store_true", default=argparse.SUPPRESS,
        help="確認を省略する",
    )
    common.add_argument(
        "--no-prompt", action="store_true", default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="JSON で出力する",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    ap = argparse.ArgumentParser(
        prog="todos",
        description="トドズ — Markdown を正本とする軽量タスク管理ツール",
    )
    ap.add_argument("--version", action="version", version="todos %s" % __version__)
    ap.add_argument("--dir", help="tasks.md を置くディレクトリ（既定: $TODOS_DIR または探索）")
    ap.add_argument("--yes", "-y", action="store_true", help="確認を省略する")
    ap.add_argument("--no-prompt", action="store_true", help="再配置・アーカイブの提案をしない")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")

    sub = ap.add_subparsers(dest="command")

    def add_cmd(name, **kwargs):
        return sub.add_parser(name, parents=[common], **kwargs)

    p = add_cmd("init", help="tasks.md と archive.md を作成する")
    p.set_defaults(func=cmd_init)

    p = add_cmd("add", help="タスクを追加する")
    p.add_argument("title", help="タスクのタイトル")
    p.add_argument("--status", "-s", help="ステータス（既定: 未着手）")
    p.add_argument("--who", help="要返答・回答待ちの相手")
    p.add_argument("--due", "-d", help="期日（YYYY/MM/DD など）")
    p.add_argument("--tag", "-t", action="append", default=[], help="タグ（複数指定可）")
    p.add_argument("--detail", help="詳細文（改行可）")
    p.add_argument("--section", help="配置先セクション（既定: 自動判定）")
    p.add_argument("--parent", help="親タスクの指定子（表示番号またはタイトル）")
    p.set_defaults(func=cmd_add)

    p = add_cmd("list", help="タスク一覧を表示する")
    p.add_argument("section", nargs="?", help="セクション名で絞り込む")
    p.add_argument("--section", dest="section_opt", help=argparse.SUPPRESS)
    p.add_argument("--status", "-s", help="ステータスで絞り込む")
    p.add_argument("--tag", "-t", help="タグで絞り込む")
    p.set_defaults(func=cmd_list)

    p = add_cmd("show", help="タスク詳細を表示する")
    p.add_argument("selector", help="表示番号またはタイトル")
    p.add_argument("--section", help="セクションで絞り込む")
    p.set_defaults(func=cmd_show)

    p = add_cmd("edit", help="タスクを編集する（オプション省略時は外部エディタ）")
    p.add_argument("selector")
    p.add_argument("--section")
    p.add_argument("--title")
    p.add_argument("--due", "-d")
    p.add_argument("--clear-due", action="store_true", help="期日を削除する")
    p.add_argument("--add-tag", action="append", default=[])
    p.add_argument("--remove-tag", action="append", default=[])
    p.add_argument("--detail")
    p.add_argument("--editor", "-e", action="store_true", help="外部エディタを開く")
    p.set_defaults(func=cmd_edit)

    p = add_cmd("status", help="ステータスを変更する")
    p.add_argument("selector")
    p.add_argument("status", help="・".join(model.STATUSES))
    p.add_argument("--who", help="要返答・回答待ちの相手")
    p.add_argument("--section")
    p.set_defaults(func=cmd_status)

    p = add_cmd("done", help="タスクを完了にする")
    p.add_argument("selector")
    p.add_argument("--section")
    p.set_defaults(func=cmd_done)

    p = add_cmd("skip", help="タスクをやらないにする")
    p.add_argument("selector")
    p.add_argument("--section")
    p.set_defaults(func=cmd_skip)

    p = add_cmd("move", help="タスクを別セクションへ移動する")
    p.add_argument("selector")
    p.add_argument("to", help="・".join(model.SECTIONS))
    p.set_defaults(func=cmd_move)

    p = add_cmd("search", help="キーワードまたはタグで検索する")
    p.add_argument("keyword", nargs="?")
    p.add_argument("--tag", "-t")
    p.add_argument("--status", "-s")
    p.add_argument("--section")
    p.set_defaults(func=cmd_search)

    p = add_cmd("rollover", help="全未完了タスクを再評価して再配置する")
    p.set_defaults(func=cmd_rollover)

    p = add_cmd("archive", help="終了タスクを archive.md へ移す")
    p.set_defaults(func=cmd_archive)

    p = add_cmd("report", help="日報用の候補一覧を出力する")
    p.add_argument("scope", nargs="?", default="today", choices=["today"])
    p.set_defaults(func=cmd_report)

    p = add_cmd("validate", help="構文検証を行う")
    p.add_argument("--fix", action="store_true", help="修正を提案して適用する")
    p.set_defaults(func=cmd_validate)

    p = add_cmd("tui", help="TUI を起動する")
    p.set_defaults(func=cmd_tui)

    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    args = ap.parse_args(argv)

    if getattr(args, "command", None) is None:
        args = ap.parse_args(argv + ["tui"])

    if getattr(args, "section_opt", None):
        args.section = args.section_opt

    ctx = Context(args)
    try:
        return args.func(ctx)
    except Ambiguous as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_AMBIGUOUS
    except TodosError as exc:
        print("エラー: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        print()
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
