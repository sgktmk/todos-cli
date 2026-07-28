"""構文検証で検出した問題の表現。"""

from __future__ import annotations

from dataclasses import dataclass, field

# 検出する問題の種類
UNKNOWN_STATUS = "unknown_status"
UNPARSABLE_DUE = "unparsable_due"
NON_CANONICAL_DUE = "non_canonical_due"
CHECKBOX_MISMATCH = "checkbox_mismatch"
STRIKE_MISMATCH = "strike_mismatch"
PARENT_DONE_OPEN_CHILDREN = "parent_done_open_children"
MISSING_SECTION = "missing_section"
UNKNOWN_SECTION = "unknown_section"
UNPARSABLE_BULLET = "unparsable_bullet"
MISPLACED_TASK = "misplaced_task"


@dataclass
class Issue:
    """1件の構文上の問題。fixable なものだけ自動修正を提案する。"""

    kind: str
    message: str
    line_no: int = 0
    task: object = field(default=None, repr=False)
    fixable: bool = False
    #: 修正内容の説明（利用者への確認文に使う）
    fix_label: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "line": self.line_no,
            "fixable": self.fixable,
            "fix": self.fix_label or None,
        }

    def format(self) -> str:
        head = "  行%d: " % self.line_no if self.line_no else "  "
        return "%s%s" % (head, self.message)
