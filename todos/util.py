"""日付・文字幅・端末入出力まわりの小さなユーティリティ。"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import unicodedata

# ---------------------------------------------------------------- 日付

CANON_DATE = "%Y/%m/%d"


def today() -> _dt.date:
    """今日の日付。TODOS_TODAY でテスト用に固定できる。"""
    override = os.environ.get("TODOS_TODAY")
    if override:
        d = parse_date(override)
        if d:
            return d
    return _dt.date.today()


def format_date(d: _dt.date) -> str:
    return d.strftime(CANON_DATE)


def format_due(d: _dt.date) -> str:
    """正規形式の期日表記を返す。"""
    return "（〜%s）" % format_date(d)


_DATE_PATTERNS = (
    re.compile(r"^(?P<y>\d{4})[/\-.](?P<m>\d{1,2})[/\-.](?P<d>\d{1,2})$"),
    re.compile(r"^(?P<y>\d{4})年\s*(?P<m>\d{1,2})月\s*(?P<d>\d{1,2})日$"),
    re.compile(r"^(?P<m>\d{1,2})[/\-.](?P<d>\d{1,2})$"),
    re.compile(r"^(?P<m>\d{1,2})月\s*(?P<d>\d{1,2})日$"),
)


def parse_date(text: str, base: _dt.date | None = None) -> _dt.date | None:
    """日付らしき文字列を date に変換する。解釈できなければ None。

    年が省略された場合は、基準日から前後6か月に最も近い年を採用する。
    """
    if not text:
        return None
    s = normalize_digits(text.strip())
    for pat in _DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = m.groupdict()
        month, day = int(g["m"]), int(g["d"])
        if "y" in g and g.get("y"):
            year = int(g["y"])
            try:
                return _dt.date(year, month, day)
            except ValueError:
                return None
        ref = base or today()
        for year in (ref.year, ref.year + 1, ref.year - 1):
            try:
                cand = _dt.date(year, month, day)
            except ValueError:
                continue
            if -183 <= (cand - ref).days <= 183:
                return cand
        try:
            return _dt.date(ref.year, month, day)
        except ValueError:
            return None
    return None


_ZEN = "０１２３４５６７８９"


def normalize_digits(s: str) -> str:
    """全角数字・全角記号を半角へ寄せる（日付解釈用）。"""
    out = []
    for ch in s:
        if ch in _ZEN:
            out.append(str(_ZEN.index(ch)))
        elif ch == "／":
            out.append("/")
        elif ch == "－" or ch == "ー":
            out.append("-")
        elif ch == "．":
            out.append(".")
        else:
            out.append(ch)
    return "".join(out)


def week_end(base: _dt.date) -> _dt.date:
    """当週の日曜日（週は日曜で区切る。基準日が日曜ならその日）。"""
    # weekday(): 月=0 ... 日=6
    return base + _dt.timedelta(days=(6 - base.weekday()) % 7)


# ---------------------------------------------------------------- 文字幅


def ambiguous_width() -> int:
    """East Asian Ambiguous 文字の幅。TODOS_AMBIGUOUS_WIDTH=2 で全角扱いにする。

    「…」「■」などは端末設定によって1桁にも2桁にも描画される。既定は1桁。
    日本語環境で罫線がずれる場合は 2 を指定する。
    """
    return 2 if os.environ.get("TODOS_AMBIGUOUS_WIDTH") == "2" else 1


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    kind = unicodedata.east_asian_width(ch)
    if kind in ("W", "F"):
        return 2
    if kind == "A":
        return ambiguous_width()
    return 1


def display_width(s: str) -> int:
    """端末上の表示幅。"""
    return sum(char_width(c) for c in s)


def truncate(s: str, width: int, ellipsis: str = "…") -> str:
    """表示幅が width を超えないように切り詰める。"""
    if display_width(s) <= width:
        return s
    if width <= 0:
        return ""
    ew = display_width(ellipsis)
    out, acc = [], 0
    for ch in s:
        w = char_width(ch)
        if acc + w > width - ew:
            break
        out.append(ch)
        acc += w
    return "".join(out) + ellipsis


def pad(s: str, width: int) -> str:
    """表示幅 width になるよう右側を空白で埋める（超過分は切り詰め）。"""
    s = truncate(s, width)
    return s + " " * max(0, width - display_width(s))


def wrap(s: str, width: int) -> list[str]:
    """表示幅 width で折り返した行の一覧を返す。空文字列なら [""]。

    日本語には単語境界が無いため、空白があればそこで折り、無ければ文字単位で折る。
    """
    if width <= 0 or display_width(s) <= width:
        return [s]
    out: list[str] = []
    cur: list[str] = []
    acc = 0
    for ch in s:
        w = char_width(ch)
        if acc + w > width and cur:
            cut = _break_at(cur)
            head = "".join(cur[:cut]).rstrip() if cut else ""
            if head:
                out.append(head)
                cur = cur[cut:]
            else:
                # 空白が字下げしか無い場合。空行を挟まないよう文字単位で折る。
                out.append("".join(cur))
                cur = []
            acc = display_width("".join(cur))
        cur.append(ch)
        acc += w
    if cur:
        out.append("".join(cur))
    return out


def _break_at(chars: list[str]) -> int:
    """最後の空白の直後の位置を返す。行頭の空白は折り返し位置にしない。"""
    for i in range(len(chars) - 1, 0, -1):
        if chars[i] == " ":
            return i + 1
    return 0


# ---------------------------------------------------------------- 端末


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm(message: str, assume_yes: bool = False, default: bool = False) -> bool:
    """y/N 確認。--yes 指定時と非対話時の挙動を一箇所にまとめる。"""
    if assume_yes:
        return True
    if not is_interactive():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input("%s %s " % (message, suffix)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


class TodosError(Exception):
    """利用者向けのエラーメッセージを伴う例外。"""
