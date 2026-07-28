"""ファイルの探索・読み書き。書き込みは原子的に行う。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import model, parser, render, util
from .util import TodosError

TASKS_FILE = "tasks.md"
ARCHIVE_FILE = "archive.md"

TEMPLATE = "\n".join(["# tasks", ""] + ["## %s\n" % s for s in model.SECTIONS])

ARCHIVE_TEMPLATE = (
    "<!-- トドズ: 完了・やらないと判断したタスクの保管先。"
    "日付見出しごとに新しい順で追記されます。 -->\n"
)


def resolve_dir(explicit: str | None = None) -> Path:
    """tasks.md を置くディレクトリを決める。

    優先順: --dir → $TODOS_DIR → カレントから上位へ探索 → ~/todos
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("TODOS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / TASKS_FILE).is_file():
            return candidate
    return Path.home() / "todos"


class Store:
    """tasks.md と archive.md への入出力をまとめて扱う。"""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.tasks_path = self.dir / TASKS_FILE
        self.archive_path = self.dir / ARCHIVE_FILE

    # ------------------------------------------------------------ 初期化

    def exists(self) -> bool:
        return self.tasks_path.is_file()

    def init(self) -> bool:
        """tasks.md が無ければ雛形を作成する。作成したら True。"""
        created = False
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.tasks_path.exists():
            _atomic_write(self.tasks_path, TEMPLATE)
            created = True
        if not self.archive_path.exists():
            _atomic_write(self.archive_path, ARCHIVE_TEMPLATE)
        return created

    # ------------------------------------------------------------ 読み書き

    def load(self, base=None) -> parser.Document:
        if not self.tasks_path.is_file():
            raise TodosError(
                "%s が見つかりません。`todos init` で作成できます。" % self.tasks_path
            )
        return parser.parse_file(self.tasks_path, base)

    def save(self, doc: parser.Document) -> None:
        doc.reindex()
        _atomic_write(self.tasks_path, render.render_document(doc))

    def read_archive(self) -> str:
        if not self.archive_path.is_file():
            return ARCHIVE_TEMPLATE
        return self.archive_path.read_text(encoding="utf-8")

    def write_archive(self, text: str) -> None:
        _atomic_write(self.archive_path, text)

    def append_archive(self, date, blocks: list[list[str]]) -> None:
        """日付見出しの下へタスクブロックを追記する（新しい日付を上に積む）。"""
        if not blocks:
            return
        heading = "# %s" % util.format_date(date)
        text = self.read_archive()
        lines = text.splitlines()
        flat: list[str] = []
        for block in blocks:
            flat.extend(block)

        idx = None
        for i, line in enumerate(lines):
            if line.strip() == heading:
                idx = i
                break

        if idx is None:
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    insert_at = i
                    break
                insert_at = i + 1
            new_block = [heading, ""] + flat + [""]
            if insert_at > 0 and lines[insert_at - 1].strip():
                new_block.insert(0, "")
            lines[insert_at:insert_at] = new_block
        else:
            end = len(lines)
            for i in range(idx + 1, len(lines)):
                if lines[i].startswith("# "):
                    end = i
                    break
            while end > idx + 1 and not lines[end - 1].strip():
                end -= 1
            lines[end:end] = flat

        out = "\n".join(lines).rstrip("\n") + "\n"
        self.write_archive(out)


def _atomic_write(path: Path, text: str) -> None:
    """同一ディレクトリの一時ファイル経由で書き込み、失敗時も元を壊さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".%s." % path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
