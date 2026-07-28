#!/bin/sh
# トドズ (todos) インストーラ
#
#   curl -fsSL https://raw.githubusercontent.com/sgktmk/todos-cli/main/install.sh | sh
#
# 環境変数:
#   TODOS_PREFIX  インストール先 (既定: $HOME/.local)
#   TODOS_REF     取得するブランチまたはタグ (既定: main)
#   TODOS_REPO    リポジトリ (既定: sgktmk/todos-cli)

set -eu

REPO="${TODOS_REPO:-sgktmk/todos-cli}"
REF="${TODOS_REF:-main}"
PREFIX="${TODOS_PREFIX:-$HOME/.local}"
LIB_DIR="$PREFIX/share/todos-cli"
BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/todos"

say() { printf '%s\n' "$*"; }
die() { printf 'エラー: %s\n' "$*" >&2; exit 1; }

uninstall() {
    rm -rf "$LIB_DIR"
    rm -f "$LAUNCHER"
    say "todos を削除しました。"
    say "  tasks.md と archive.md は削除していません。"
    exit 0
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
fi

# ---------------------------------------------------------------- Python の確認

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON="$(find_python)" || die "Python 3.9 以上が必要です。python3 をインストールしてください。"
say "Python: $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

"$PYTHON" -c 'import curses' 2>/dev/null || say "警告: curses が使えないため TUI は起動できません（CLI は利用できます）。"

# ---------------------------------------------------------------- ソースの取得

SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
WORK=""

cleanup() { if [ -n "$WORK" ]; then rm -rf "$WORK"; fi; }
trap cleanup EXIT

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/todos/__init__.py" ]; then
    SRC="$SCRIPT_DIR"
    say "ソース: $SRC (ローカル)"
else
    WORK="$(mktemp -d)"
    URL="https://codeload.github.com/$REPO/tar.gz/$REF"
    say "ソース: $URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$URL" -o "$WORK/src.tar.gz" || die "ダウンロードに失敗しました: $URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$WORK/src.tar.gz" "$URL" || die "ダウンロードに失敗しました: $URL"
    else
        die "curl または wget が必要です。"
    fi
    tar -xzf "$WORK/src.tar.gz" -C "$WORK" || die "展開に失敗しました。"
    SRC="$(find "$WORK" -maxdepth 1 -type d -name '*todos-cli*' | head -n 1)"
    [ -n "$SRC" ] || die "展開結果を特定できませんでした。"
fi

[ -f "$SRC/todos/cli.py" ] || die "ソースに todos パッケージが見つかりません。"

# ---------------------------------------------------------------- 配置

mkdir -p "$LIB_DIR" "$BIN_DIR"
rm -rf "$LIB_DIR/todos"
cp -R "$SRC/todos" "$LIB_DIR/todos"
find "$LIB_DIR/todos" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$LAUNCHER" <<EOF
#!/bin/sh
# トドズ起動スクリプト (install.sh が生成)
PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m todos "\$@"
EOF
chmod +x "$LAUNCHER"

VERSION="$("$LAUNCHER" --version 2>/dev/null || echo 'todos')"
say ""
say "$VERSION をインストールしました。"
say "  本体   : $LIB_DIR/todos"
say "  コマンド: $LAUNCHER"

case ":${PATH}:" in
    *":$BIN_DIR:"*)
        say ""
        say "使い始めるには:"
        say "  todos add \"最初のタスク\"    # tasks.md を自動作成します"
        say "  todos                        # TUI を起動します"
        ;;
    *)
        say ""
        say "$BIN_DIR が PATH にありません。シェルの設定に次を追加してください:"
        say "  export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac
