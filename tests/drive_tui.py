#!/usr/bin/env python3
"""疑似端末で TUI を起動し、キー入力を流して画面を取得する開発用スクリプト。

使い方: python3 tests/drive_tui.py "jjs" [幅 高さ]
"""

import os
import pty
import re
import select
import sys
import time
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def drive(keys: str, cols: int = 80, rows: int = 24) -> str:
    env = dict(os.environ, TERM="xterm-256color", LINES=str(rows), COLUMNS=str(cols))
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, os.path.join(ROOT, "bin", "todos")], env)
    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        time.sleep(0.6)
        out = read_all(fd)
        for key in keys:
            os.write(fd, key.encode())
            time.sleep(0.25)
            out = read_all(fd, out)
        return out
    finally:
        try:
            os.write(fd, b"q")
            time.sleep(0.2)
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def read_all(fd, acc: str = "") -> str:
    while True:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if not ready:
            return acc
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return acc
        if not chunk:
            return acc
        acc += chunk.decode("utf-8", "replace")


def render(stream: str, cols: int = 80, rows: int = 24) -> str:
    """端末エスケープを解釈して最終画面を組み立てる（必要最小限）。

    curses は行がまるごとずれるとき、書き直す代わりにスクロール領域
    (DECSTBM) と改行で画面をずらす。これを解釈しないと、モーダルの
    スクロールなどが「最終行だけ書き換わった」ように見えてしまう。
    """
    grid = [[" "] * cols for _ in range(rows)]
    y = x = 0
    i = 0
    top_margin, bottom_margin = 0, rows - 1

    def scroll_up(n=1):
        for _ in range(n):
            del grid[top_margin]
            grid.insert(bottom_margin, [" "] * cols)

    def scroll_down(n=1):
        for _ in range(n):
            del grid[bottom_margin]
            grid.insert(top_margin, [" "] * cols)

    while i < len(stream):
        ch = stream[i]
        if ch == "\x1b":
            m = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z])", stream[i:])
            if not m:
                # ESC M（逆送り）/ ESC D（送り）/ ESC E（改行）
                simple = re.match(r"\x1b([MDE])", stream[i:])
                if simple:
                    cmd = simple.group(1)
                    if cmd == "M":
                        scroll_down() if y == top_margin else None
                        y = max(top_margin, y - 1)
                    else:
                        scroll_up() if y == bottom_margin else None
                        y = min(bottom_margin, y + 1)
                        if cmd == "E":
                            x = 0
                    i += 2
                    continue
                m2 = re.match(r"\x1b[()][A-Za-z0-9]", stream[i:])
                i += len(m2.group(0)) if m2 else 1
                continue
            params, cmd = m.group(1), m.group(2)
            nums = [int(p) for p in params.split(";") if p.isdigit()]
            if cmd == "r" and "?" not in params:  # スクロール領域 (DECSTBM)
                top_margin = (nums[0] - 1) if nums else 0
                bottom_margin = (nums[1] - 1) if len(nums) > 1 else rows - 1
                top_margin = max(0, min(top_margin, rows - 1))
                bottom_margin = max(top_margin, min(bottom_margin, rows - 1))
                y = x = 0
            elif cmd == "S":
                scroll_up(nums[0] if nums else 1)
            elif cmd == "T":
                scroll_down(nums[0] if nums else 1)
            elif cmd == "L":  # 行の挿入
                for _ in range(nums[0] if nums else 1):
                    del grid[bottom_margin]
                    grid.insert(y, [" "] * cols)
            elif cmd == "M":  # 行の削除
                for _ in range(nums[0] if nums else 1):
                    del grid[y]
                    grid.insert(bottom_margin, [" "] * cols)
            elif cmd == "H":
                y = (nums[0] - 1) if nums else 0
                x = (nums[1] - 1) if len(nums) > 1 else 0
            elif cmd == "J":
                grid = [[" "] * cols for _ in range(rows)]
                y = x = 0
            elif cmd == "K":
                for col in range(x, cols):
                    grid[y][col] = " "
            elif cmd == "d":  # 行の絶対移動 (VPA)
                y = min(rows - 1, (nums[0] - 1) if nums else 0)
            elif cmd == "X":  # 指定桁数を空白にする (ECH)
                for col in range(x, min(cols, x + (nums[0] if nums else 1))):
                    grid[y][col] = " "
            elif cmd == "C":
                x += nums[0] if nums else 1
            elif cmd == "D":
                x = max(0, x - (nums[0] if nums else 1))
            elif cmd == "G":
                x = (nums[0] - 1) if nums else 0
            elif cmd == "A":
                y = max(0, y - (nums[0] if nums else 1))
            elif cmd == "B":
                y = min(rows - 1, y + (nums[0] if nums else 1))
            i += len(m.group(0))
            continue
        if ch == "\r":
            x = 0
        elif ch == "\n":
            if y == bottom_margin:
                scroll_up()
            else:
                y = min(y + 1, rows - 1)
            x = 0
        elif ch == "\b":
            x = max(0, x - 1)
        elif ch >= " ":
            wide = unicodedata.east_asian_width(ch) in ("W", "F")
            if 0 <= y < rows and 0 <= x < cols:
                grid[y][x] = ch
                # 全角文字は2セルを占める。後半セルを空にしないと、
                # 上書きしたときに前の描画が残って見える。
                if wide and x + 1 < cols:
                    grid[y][x + 1] = ""
            x += 2 if wide else 1
        i += 1
    return "\n".join("".join(row).rstrip() for row in grid).rstrip()


if __name__ == "__main__":
    keys = sys.argv[1] if len(sys.argv) > 1 else ""
    cols = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    rows = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    print(render(drive(keys, cols, rows), cols, rows))
