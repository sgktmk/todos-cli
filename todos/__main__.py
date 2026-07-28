"""`python3 -m todos` および単体スクリプトからの起動点。"""

import sys

if __package__ in (None, ""):  # pragma: no cover - 直接実行された場合
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from todos.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
