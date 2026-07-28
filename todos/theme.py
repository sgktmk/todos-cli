"""TUI の配色とグリフ。

curses の色指定を「役割名 → 属性値」の1枚の表にまとめる。描画側 (tui.py) は
色番号を持たず ``theme.attr("cursor")`` のように役割で引く。配色を変えたい
ときはこのファイルだけを見ればよい。

既定は 256 色端末向けの落ち着いたパステル調（yazi など近年の TUI に近い
色味）。8/16 色しか無い端末、`NO_COLOR` を設定した端末には自動で落とす。
"""

from __future__ import annotations

import curses
import os

from . import model

# ---------------------------------------------------------------- パレット
#
# 値は 256 色端末の色番号。-1 は「端末の既定色」で、背景を塗りつぶさずに
# 端末のテーマ（透過背景を含む）をそのまま活かすために使う。

DARK = {
    "text": -1,
    "muted": 245,
    "faint": 240,
    "blue": 111,
    "lavender": 147,
    "mauve": 176,
    "red": 210,
    "peach": 216,
    "yellow": 223,
    "green": 150,
    "teal": 116,
    #: 選択行やステータスバーの下地
    "surface": 237,
    #: アクセント色の上に載せる文字色
    "on_accent": 235,
}

LIGHT = {
    "text": -1,
    "muted": 243,
    "faint": 247,
    "blue": 26,
    "lavender": 61,
    "mauve": 91,
    "red": 160,
    "peach": 130,
    "yellow": 136,
    "green": 28,
    "teal": 30,
    "surface": 254,
    "on_accent": 231,
}

#: 8/16 色端末。灰色が無いので muted / faint は既定色 + A_DIM で代用する。
BASIC = {
    "text": -1,
    "muted": -1,
    "faint": -1,
    "blue": curses.COLOR_BLUE,
    "lavender": curses.COLOR_CYAN,
    "mauve": curses.COLOR_MAGENTA,
    "red": curses.COLOR_RED,
    "peach": curses.COLOR_YELLOW,
    "yellow": curses.COLOR_YELLOW,
    "green": curses.COLOR_GREEN,
    "teal": curses.COLOR_CYAN,
    "surface": curses.COLOR_BLUE,
    "on_accent": curses.COLOR_WHITE,
}

#: パレットごとの追加属性。色数が足りない分を属性で補う。
EXTRA = {
    "basic": {"muted": curses.A_DIM, "faint": curses.A_DIM},
}

# ---------------------------------------------------------------- 役割
#
# 役割名 → (前景色, 背景色 or None, 追加属性)

ROLES = {
    # 枠と見出し
    "border": ("faint", None, 0),
    "border.active": ("blue", None, 0),
    "pane.title": ("blue", None, curses.A_BOLD),
    "pane.title.blur": ("muted", None, 0),
    "scrollbar": ("faint", None, 0),
    "scrollbar.thumb": ("blue", None, curses.A_BOLD),
    # ヘッダ
    "brand": ("on_accent", "blue", curses.A_BOLD),
    "header.dir": ("muted", None, 0),
    "header.file": ("lavender", None, curses.A_BOLD),
    "header.meta": ("muted", None, 0),
    # カーソル行
    "cursor": ("on_accent", "blue", curses.A_BOLD),
    "cursor.blur": ("text", "surface", 0),
    "cursor.bar": ("blue", None, 0),
    # セクションペイン
    "section": ("text", None, 0),
    "section.empty": ("muted", None, 0),
    "section.count": ("lavender", None, 0),
    "section.count.zero": ("faint", None, 0),
    "section.today": ("red", None, 0),
    "section.tomorrow": ("peach", None, 0),
    "section.inweek": ("yellow", None, 0),
    "section.openended": ("green", None, 0),
    "section.parkinglot": ("faint", None, 0),
    # タスク行
    "status.todo": ("muted", None, 0),
    "status.doing": ("peach", None, curses.A_BOLD),
    "status.reply": ("red", None, curses.A_BOLD),
    "status.waiting": ("mauve", None, 0),
    "status.hold": ("faint", None, 0),
    "status.done": ("green", None, 0),
    "status.skip": ("faint", None, 0),
    "title": ("text", None, 0),
    "title.done": ("muted", None, 0),
    "due": ("teal", None, 0),
    "due.soon": ("yellow", None, 0),
    "due.today": ("peach", None, curses.A_BOLD),
    "due.over": ("red", None, curses.A_BOLD),
    "tag": ("lavender", None, 0),
    "badge": ("mauve", None, 0),
    "detail.mark": ("faint", None, 0),
    "tree": ("faint", None, 0),
    "empty": ("muted", None, 0),
    # キーヘルプ
    "hint.key": ("lavender", None, curses.A_BOLD),
    "hint.label": ("muted", None, 0),
    # ステータスバー
    "bar": ("text", "surface", 0),
    "bar.dim": ("muted", "surface", 0),
    "bar.msg": ("text", "surface", curses.A_BOLD),
    "bar.error": ("red", "surface", curses.A_BOLD),
    "bar.mode": ("on_accent", "blue", curses.A_BOLD),
    "bar.pos": ("on_accent", "lavender", curses.A_BOLD),
    # パワーライン風の区切り（前景に前segの背景色、背景に次segの背景色）
    "bar.sep.mode": ("blue", "lavender", 0),
    "bar.sep.pos": ("lavender", "surface", 0),
    "bar.sep.end": ("surface", None, 0),
    # モーダル
    "popup.border": ("blue", None, 0),
    "popup.title": ("on_accent", "blue", curses.A_BOLD),
    "popup.text": ("text", None, 0),
    "popup.label": ("muted", None, 0),
    "popup.hint": ("muted", None, 0),
    "popup.warn": ("yellow", None, curses.A_BOLD),
}

#: 単色端末で上の導出（背景色つき = 反転）が合わない役割だけ上書きする。
MONO = {
    "border.active": curses.A_BOLD,
    "cursor.bar": curses.A_BOLD,
    "cursor.blur": curses.A_BOLD,
    "bar": 0,
    "bar.dim": curses.A_DIM,
    "bar.msg": curses.A_BOLD,
    "bar.error": curses.A_BOLD,
    "bar.sep.mode": 0,
    "bar.sep.pos": 0,
    "bar.sep.end": 0,
}

# ---------------------------------------------------------------- グリフ

STATUS_ROLE = {
    model.TODO: "status.todo",
    model.DOING: "status.doing",
    model.NEEDS_REPLY: "status.reply",
    model.WAITING: "status.waiting",
    model.HOLD: "status.hold",
    model.DONE: "status.done",
    model.SKIP: "status.skip",
}

STATUS_GLYPH = {
    model.TODO: "○",
    model.DOING: "◐",
    model.NEEDS_REPLY: "◆",
    model.WAITING: "◇",
    model.HOLD: "▪",
    model.DONE: "✓",
    model.SKIP: "✗",
}

SECTION_ROLE = {
    model.TODAY: "section.today",
    model.TOMORROW: "section.tomorrow",
    model.IN_WEEK: "section.inweek",
    model.OPEN_ENDED: "section.openended",
    model.PARKING_LOT: "section.parkinglot",
}

#: グリフを使う場合の記号一覧。TODOS_TUI_GLYPHS=0 なら ASCII 側を使う。
GLYPHS = {
    "cursor": "▍",
    "hline": "─",
    "vline": "│",
    "tee_l": "├",
    "tee_r": "┤",
    "section": "●",
    "due": "〜",
    "detail": "≡",
    "tree_last": "╰",
    "tree_mid": "├",
    "corner_tl": "╭",
    "corner_tr": "╮",
    "corner_bl": "╰",
    "corner_br": "╯",
    "scroll_track": "│",
    "scroll_thumb": "┃",
    "sep": "",
}

ASCII_GLYPHS = {
    "cursor": ">",
    "hline": "-",
    "vline": "|",
    "tee_l": "+",
    "tee_r": "+",
    "section": "*",
    "due": "~",
    "detail": "=",
    "tree_last": "`",
    "tree_mid": "|",
    "corner_tl": "+",
    "corner_tr": "+",
    "corner_bl": "+",
    "corner_br": "+",
    "scroll_track": "|",
    "scroll_thumb": "#",
    "sep": "",
}

#: Nerd Font がある端末向けのパワーライン区切り（TODOS_TUI_POWERLINE=1）。
POWERLINE_SEP = ""


# ---------------------------------------------------------------- 本体


class Theme:
    """役割名から curses の属性値を引くための小さな表。"""

    def __init__(self, palette: str = "dark", glyphs: bool = True, powerline: bool = False):
        self.palette = palette
        self.use_glyphs = glyphs
        self.powerline = powerline
        self._attrs: dict[str, int] = {}
        self._glyphs = GLYPHS if glyphs else ASCII_GLYPHS

    # ------------------------------------------------------------ 準備

    def setup(self) -> "Theme":
        """curses の初期化後に呼ぶ。色ペアを確保して属性表を作る。"""
        colors = self._start_color()
        if colors is None:
            self.palette = "mono"
            self._attrs = {name: _mono_attr(name, spec) for name, spec in ROLES.items()}
            return self
        self._build(colors)
        return self

    def _start_color(self):
        """使えるパレットを返す。色が使えなければ None。"""
        if self.palette == "mono":
            return None
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:  # pragma: no cover - 端末依存
            return None
        if not curses.has_colors():  # pragma: no cover - 端末依存
            return None
        if curses.COLORS < 256 or self.palette == "basic":
            self.palette = "basic"
            return BASIC
        return LIGHT if self.palette == "light" else DARK

    def _build(self, colors: dict):
        extra = EXTRA.get(self.palette, {})
        pairs: dict[tuple[int, int], int] = {}
        for name, (fg_name, bg_name, attrs) in ROLES.items():
            fg = colors.get(fg_name, -1)
            bg = colors.get(bg_name, -1) if bg_name else -1
            attr = attrs | (0 if bg_name else extra.get(fg_name, 0))
            key = (fg, bg)
            if key not in pairs:
                index = len(pairs) + 1
                if index >= curses.COLOR_PAIRS:  # pragma: no cover - 端末依存
                    self._attrs[name] = attr
                    continue
                try:
                    curses.init_pair(index, fg, bg)
                except curses.error:  # pragma: no cover - 端末依存
                    self._attrs[name] = attr
                    continue
                pairs[key] = index
            self._attrs[name] = curses.color_pair(pairs[key]) | attr

    # ------------------------------------------------------------ 参照

    def attr(self, role: str) -> int:
        return self._attrs.get(role, 0)

    def glyph(self, name: str) -> str:
        return self._glyphs.get(name, "")

    def status_glyph(self, status: str) -> str:
        return STATUS_GLYPH.get(status, "•") if self.use_glyphs else ""

    def status_role(self, status: str) -> str:
        return STATUS_ROLE.get(status, "status.todo")

    def section_role(self, name: str) -> str:
        return SECTION_ROLE.get(name, "section")

    def separator(self) -> str:
        return POWERLINE_SEP if self.powerline else ""


def _mono_attr(name: str, spec) -> int:
    """色を使わない端末向けの属性を導く。"""
    if name in MONO:
        return MONO[name]
    fg, bg, attrs = spec
    if bg is not None:
        return attrs | curses.A_REVERSE
    if fg in ("muted", "faint"):
        return attrs | curses.A_DIM
    return attrs


def detect() -> Theme:
    """環境変数から設定を読み取って Theme を作る（色ペアの確保は setup で）。

    - `NO_COLOR`           : 設定されていれば単色
    - `TODOS_TUI_THEME`    : dark（既定）/ light / basic / mono
    - `TODOS_TUI_GLYPHS`   : 0 で記号を ASCII に落とす
    - `TODOS_TUI_POWERLINE`: 1 でステータスバーを Nerd Font の区切りにする
    """
    palette = (os.environ.get("TODOS_TUI_THEME") or "dark").strip().lower()
    if palette not in ("dark", "light", "basic", "mono"):
        palette = "dark"
    if os.environ.get("NO_COLOR") is not None:
        palette = "mono"
    glyphs = os.environ.get("TODOS_TUI_GLYPHS", "1").strip().lower() not in ("0", "off", "no")
    powerline = os.environ.get("TODOS_TUI_POWERLINE", "").strip().lower() in ("1", "on", "yes")
    return Theme(palette, glyphs, powerline)
