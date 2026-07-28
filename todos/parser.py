"""Markdown の解析。CLI・TUI ともにここだけを通してタスクを読む。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import issues as iss
from . import model, util
from .model import Task

# ---------------------------------------------------------------- 正規表現

HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")

TASK_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])\s+\[(?P<check>[ xX])\]\s?(?P<body>.*)$"
)
#: タスクとして解釈できなかった箇条書きの検出用
BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])\s+(?P<body>.*)$")

STATUS_RE = re.compile(r"^【\s*(?P<inner>[^】]*?)\s*】\s*")
COUNTERPART_RE = re.compile(r"^(?P<name>[^（(]+?)\s*[（(]\s*(?P<who>.*?)\s*[）)]$")

STRIKE_RE = re.compile(r"^~~\s*(?P<title>.+?)\s*~~$")
TRAILING_TAG_RE = re.compile(r"\s+#(?P<tag>[^\s#]+)\s*$")

CANON_DUE_RE = re.compile(r"（〜(?P<date>\d{4}/\d{2}/\d{2})）")
PAREN_RE = re.compile(r"[（(]\s*(?P<inner>[^（()）]*?)\s*[）)]")
LABEL_DUE_RE = re.compile(
    r"(?P<all>(?:期限|期日|締切|〆切|due|Due|DUE)\s*[:：]?\s*[〜~～]?\s*(?P<date>[^\s）)]+))"
)
TILDE_DUE_RE = re.compile(r"(?P<all>[〜~～]\s*(?P<date>\d[^\s）)]*))")
DATEISH_RE = re.compile(r"\d{1,4}\s*[/\-.年]\s*\d{1,2}")
DUE_LABEL_PREFIX_RE = re.compile(r"^(?:期限|期日|締切|〆切|due|Due|DUE)\s*[:：]?\s*")


# ---------------------------------------------------------------- 文書モデル


@dataclass
class RawBlock:
    """タスクとして解釈しない行の塊。書き戻し時にそのまま出力する。"""

    lines: list[str] = field(default_factory=list)


@dataclass
class Section:
    name: str | None  #: 正規セクション名。未知の見出しなら None
    heading: str  #: 生の見出し行
    nodes: list = field(default_factory=list)  #: Task | RawBlock

    @property
    def tasks(self) -> list[Task]:
        return [n for n in self.nodes if isinstance(n, Task)]


@dataclass
class Document:
    path: Path | None = None
    preamble: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    issues: list = field(default_factory=list)

    # -------------------------------------------------------- 参照

    def section(self, name: str) -> Section | None:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def known_sections(self) -> list[Section]:
        return [s for s in self.sections if s.name in model.SECTIONS]

    def top_tasks(self) -> list[Task]:
        out = []
        for s in self.known_sections():
            out.extend(s.tasks)
        return out

    def all_tasks(self) -> list[Task]:
        out = []
        for t in self.top_tasks():
            out.extend(t.walk())
        return out

    def by_index(self, index: int) -> Task | None:
        for t in self.all_tasks():
            if t.index == index:
                return t
        return None

    def ensure_sections(self) -> list[Section]:
        """必須セクションが無ければ規定の順序に沿って追加する。"""
        added = []
        for i, name in enumerate(model.SECTIONS):
            if self.section(name) is not None:
                continue
            sec = Section(name=name, heading="## %s" % name)
            self.sections.insert(self._insert_position(i), sec)
            added.append(sec)
        return added

    def _insert_position(self, order: int) -> int:
        """規定順で直前に来る既知セクションの後ろ（無ければ先頭）を返す。"""
        for prev in reversed(model.SECTIONS[:order]):
            existing = self.section(prev)
            if existing is not None:
                return self.sections.index(existing) + 1
        for i, section in enumerate(self.sections):
            if section.name in model.SECTIONS:
                return i
        return len(self.sections)

    def reindex(self) -> None:
        """表示番号と記述順を振り直す。"""
        n = 0
        for sec in self.known_sections():
            for order, task in enumerate(sec.tasks):
                task.section = sec.name
                task.order = order
                for node in task.walk():
                    n += 1
                    node.index = n
                    if node is not task:
                        node.section = sec.name


# ---------------------------------------------------------------- 期日の抽出


@dataclass
class DueMatch:
    start: int
    end: int
    date: object | None
    canonical: bool
    text: str


def find_due(text: str, base=None) -> DueMatch | None:
    """本文から期日表現を1つ取り出す。認識できない日付は date=None で返す。"""
    m = CANON_DUE_RE.search(text)
    if m:
        d = util.parse_date(m.group("date"), base)
        if d and util.format_date(d) == m.group("date"):
            return DueMatch(m.start(), m.end(), d, True, m.group(0))

    for m in PAREN_RE.finditer(text):
        inner = m.group("inner")
        cleaned = inner.strip()
        had_marker = False
        if cleaned[:1] in ("〜", "~", "～"):
            cleaned = cleaned[1:].strip()
            had_marker = True
        lm = DUE_LABEL_PREFIX_RE.match(cleaned)
        if lm:
            cleaned = cleaned[lm.end():].strip()
            had_marker = True
        cleaned = re.sub(r"\s*まで$", "", cleaned).strip()
        if not (had_marker or DATEISH_RE.search(cleaned)):
            continue
        d = util.parse_date(cleaned, base)
        if d:
            return DueMatch(m.start(), m.end(), d, False, m.group(0))
        if had_marker or DATEISH_RE.search(cleaned):
            return DueMatch(m.start(), m.end(), None, False, m.group(0))

    for pattern in (LABEL_DUE_RE, TILDE_DUE_RE):
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group("date")
        cleaned = re.sub(r"\s*まで$", "", raw).strip()
        d = util.parse_date(cleaned, base)
        if d:
            return DueMatch(m.start("all"), m.end("all"), d, False, m.group("all"))
        if DATEISH_RE.search(cleaned):
            return DueMatch(m.start("all"), m.end("all"), None, False, m.group("all"))
    return None


# ---------------------------------------------------------------- 本文の解析


def parse_body(body: str, line_no: int, base=None) -> tuple[Task, list]:
    """タスク行のチェックボックス以降を解析して Task を組み立てる。"""
    task = Task()
    problems = []
    rest = body.strip()

    m = STATUS_RE.match(rest)
    if m:
        inner = m.group("inner").strip()
        rest = rest[m.end():]
        name, who = inner, None
        cm = COUNTERPART_RE.match(inner)
        if cm:
            name, who = cm.group("name").strip(), cm.group("who").strip() or None
        if name in model.STATUSES:
            task.status = name
            task.counterpart = who if name in model.COUNTERPART_STATUSES else None
            if who and name not in model.COUNTERPART_STATUSES:
                problems.append(
                    iss.Issue(
                        iss.UNKNOWN_STATUS,
                        "ステータス「%s」に相手（%s）は指定できません。" % (name, who),
                        line_no,
                        task,
                    )
                )
        else:
            task.status = inner
            problems.append(
                iss.Issue(
                    iss.UNKNOWN_STATUS,
                    "未知のステータス「%s」です。使用できるのは %s です。"
                    % (inner, "・".join(model.STATUSES)),
                    line_no,
                    task,
                )
            )
        task.status_explicit = True
    else:
        task.status = model.TODO
        task.status_explicit = False

    tags = []
    while True:
        m = TRAILING_TAG_RE.search(rest)
        if not m:
            break
        tags.append(m.group("tag"))
        rest = rest[: m.start()]
    task.tags = list(reversed(tags))

    due = find_due(rest, base)
    if due:
        if due.date is not None:
            # 解釈できた期日だけを本文から切り出す。解釈できない記述は
            # 推測で書き換えないよう、タイトルへそのまま残す（要件22.4）。
            rest = (rest[: due.start] + " " + rest[due.end:]).strip()
            rest = re.sub(r"\s{2,}", " ", rest)
        if due.date is None:
            problems.append(
                iss.Issue(
                    iss.UNPARSABLE_DUE,
                    "期日として解釈できない記述「%s」があります。" % due.text,
                    line_no,
                    task,
                    payload={"text": due.text},
                )
            )
        else:
            task.due = due.date
            if not due.canonical:
                problems.append(
                    iss.Issue(
                        iss.NON_CANONICAL_DUE,
                        "期日「%s」が正規形式ではありません。" % due.text,
                        line_no,
                        task,
                        fixable=True,
                        fix_label="「%s」を「%s」へ書き換える"
                        % (due.text, util.format_due(due.date)),
                        payload={"text": due.text, "date": due.date},
                    )
                )

    rest = rest.strip()
    m = STRIKE_RE.match(rest)
    if m:
        task.title = m.group("title").strip()
        task.__dict__["_struck"] = True
    else:
        task.title = rest
        task.__dict__["_struck"] = False

    task.line_no = line_no
    task.issues = problems
    return task, problems


def is_struck(task: Task) -> bool:
    return bool(task.__dict__.get("_struck"))


def set_struck(task: Task, value: bool) -> None:
    task.__dict__["_struck"] = bool(value)


# ---------------------------------------------------------------- 文書の解析


def _indent_width(s: str) -> int:
    return len(s.replace("\t", "    "))


def parse_text(text: str, path: Path | None = None, base=None) -> Document:
    """tasks.md 相当のテキストを Document に解析する。"""
    doc = Document(path=path)
    lines = text.splitlines()
    current: Section | None = None
    # (indent幅, Task) のスタック
    stack: list[tuple[int, Task]] = []
    raw: list[str] = []
    pending_blanks: list[str] = []

    def flush_raw():
        nonlocal raw
        if raw:
            block = RawBlock(list(raw))
            if current is None:
                doc.preamble.extend(block.lines)
            else:
                current.nodes.append(block)
            raw = []

    def target(line_indent: int) -> Task | None:
        owner = None
        for indent, task in stack:
            if indent < line_indent:
                owner = task
            else:
                break
        return owner

    for i, line in enumerate(lines, start=1):
        hm = HEADING_RE.match(line)
        if hm:
            flush_raw()
            pending_blanks = []
            stack = []
            name = model.canonical_section(hm.group("text"))
            current = Section(name=name, heading=line)
            doc.sections.append(current)
            continue

        if current is None or current.name is None:
            # 既知のセクション以外の見出し配下は解析せず、そのまま保存する。
            # ただしタスク行が埋もれている場合だけは気づけるよう警告する。
            if current is not None and TASK_RE.match(line):
                doc.issues.append(
                    iss.Issue(
                        iss.UNKNOWN_SECTION,
                        "見出し「%s」配下のタスクは管理対象外です: %s"
                        % (current.heading.lstrip("# ").strip(), line.strip()),
                        i,
                    )
                )
            if not line.strip() and not raw:
                continue
            raw.append(line)
            continue

        if not line.strip():
            pending_blanks.append(line)
            continue

        tm = TASK_RE.match(line)
        if tm:
            flush_raw()
            pending_blanks = []
            indent = _indent_width(tm.group("indent"))
            task, problems = parse_body(tm.group("body"), i, base)
            task.bullet = tm.group("bullet")
            task.checked = tm.group("check").lower() == "x"
            doc.issues.extend(problems)

            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1] if stack else None
            if parent is None:
                current.nodes.append(task)
                task.section = current.name
            else:
                parent.children.append(task)
                task.parent = parent
                task.section = current.name
            stack.append((indent, task))
            continue

        line_indent = _indent_width(re.match(r"^[ \t]*", line).group(0))
        owner = target(line_indent) if line_indent > 0 else None
        if owner is not None:
            bm = BULLET_RE.match(line)
            if bm:
                doc.issues.append(
                    iss.Issue(
                        iss.UNPARSABLE_BULLET,
                        "タスクとして解釈できない箇条書きです: %s" % line.strip(),
                        i,
                    )
                )
            if pending_blanks:
                owner.detail.extend("" for _ in pending_blanks)
                pending_blanks = []
            owner.detail.append(_dedent_detail(line, owner))
            continue

        bm = BULLET_RE.match(line)
        if bm:
            doc.issues.append(
                iss.Issue(
                    iss.UNPARSABLE_BULLET,
                    "タスクとして解釈できない箇条書きです: %s" % line.strip(),
                    i,
                )
            )
        stack = []
        if pending_blanks:
            raw.extend(pending_blanks)
            pending_blanks = []
        raw.append(line)

    flush_raw()

    for name in model.SECTIONS:
        if doc.section(name) is None:
            doc.issues.append(
                iss.Issue(
                    iss.MISSING_SECTION,
                    "必須セクション「%s」がありません。" % name,
                    0,
                    fixable=True,
                    fix_label="セクション「%s」を追加する" % name,
                    payload={"section": name},
                )
            )

    doc.reindex()
    _trim_details(doc)
    return doc


def _dedent_detail(line: str, owner: Task) -> str:
    """詳細行から、そのタスクの標準インデント（親の深さ+1段）を取り除く。"""
    expected = (owner.depth + 1) * 2
    text = line.replace("\t", "    ")
    strip = 0
    while strip < expected and strip < len(text) and text[strip] == " ":
        strip += 1
    return text[strip:].rstrip()


def _trim_details(doc: Document) -> None:
    for task in doc.all_tasks():
        while task.detail and not task.detail[-1].strip():
            task.detail.pop()


def parse_file(path: Path, base=None) -> Document:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return parse_text(text, path, base)
