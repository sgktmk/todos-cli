"""構文検証と不整合検出。安全に直せるものだけ修正を提案する。"""

from __future__ import annotations

from . import issues as iss
from . import model, parser
from .parser import Document


def validate(doc: Document) -> list:
    """解析時に見つかった問題と、フィールド間の不整合をまとめて返す。"""
    found = list(doc.issues)
    for task in doc.all_tasks():
        found.extend(_check_task(task))
    found.sort(key=lambda i: (i.line_no or 0, i.kind))
    return found


def _check_task(task) -> list:
    out = []

    # 完了ステータスとチェックボックスの不一致
    if task.status == model.DONE and not task.checked:
        out.append(
            iss.Issue(
                iss.CHECKBOX_MISMATCH,
                "「%s」は【完了】ですがチェックボックスが [ ] です。" % task.title,
                task.line_no,
                task,
                fixable=True,
                fix_label="チェックボックスを [x] にする",
                payload={"action": "check"},
            )
        )
    elif task.checked and task.status != model.DONE:
        out.append(
            iss.Issue(
                iss.CHECKBOX_MISMATCH,
                "「%s」は [x] ですがステータスが【%s】です。"
                % (task.title, task.status),
                task.line_no,
                task,
                fixable=True,
                fix_label="ステータスを【完了】にする",
                payload={"action": "status_done"},
            )
        )

    # やらないステータスと取り消し線の不一致
    struck = parser.is_struck(task)
    if task.status == model.SKIP and not struck:
        out.append(
            iss.Issue(
                iss.STRIKE_MISMATCH,
                "「%s」は【やらない】ですが取り消し線がありません。" % task.title,
                task.line_no,
                task,
                fixable=True,
                fix_label="タイトルに取り消し線を付ける",
                payload={"action": "strike"},
            )
        )
    elif struck and task.status != model.SKIP:
        out.append(
            iss.Issue(
                iss.STRIKE_MISMATCH,
                "「%s」は取り消し線付きですがステータスが【%s】です。"
                % (task.title, task.status),
                task.line_no,
                task,
                fixable=True,
                fix_label="ステータスを【やらない】にする",
                payload={"action": "status_skip"},
            )
        )

    # 親が完了しているが未完了の子が残っている
    if task.is_terminal:
        open_kids = [c for c in task.descendants() if not c.is_terminal]
        if open_kids:
            out.append(
                iss.Issue(
                    iss.PARENT_DONE_OPEN_CHILDREN,
                    "「%s」は【%s】ですが未完了の子タスクが %d 件あります。"
                    % (task.title, task.status, len(open_kids)),
                    task.line_no,
                    task,
                )
            )
    return out


def apply_fix(doc: Document, issue) -> bool:
    """1件の問題を修正する。修正できたら True。"""
    from . import ops  # 循環インポート回避

    kind, task = issue.kind, issue.task
    if kind == iss.MISSING_SECTION:
        doc.ensure_sections()
        return True
    if kind == iss.NON_CANONICAL_DUE:
        # 解析済みの期日を正規形式で書き戻すだけでよい
        return True
    if kind == iss.CHECKBOX_MISMATCH:
        if issue.payload.get("action") == "check":
            task.checked = True
        else:
            ops.set_status(task, model.DONE)
        return True
    if kind == iss.STRIKE_MISMATCH:
        if issue.payload.get("action") == "strike":
            parser.set_struck(task, True)
        else:
            ops.set_status(task, model.SKIP)
        return True
    return False


def fixable(found: list) -> list:
    return [i for i in found if i.fixable]
