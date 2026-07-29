# トドズ (todos)

人間が直接読み書きできる Markdown ファイルを正本とする、軽量なタスク管理ツール。

- タスクの追加に必要なのは**タイトルだけ**
- 優先度は属性として持たず、**期日とステータスから実行時に判定**して配置する
- タスク ID も履歴データベースも作らない。**ファイルは `tasks.md` と `archive.md` の 2 つだけ**
- CLI、TUI、AI エージェントが**同じタスクを同じロジックで**操作する

```markdown
## Today

- [ ] 【要返答（山田）】レビューコメントに回答する（〜2026/07/28） #review
- [ ] 【進行中】要件定義を作成する（〜2026/07/31） #todos #docs
  人間とAIエージェントの両方が操作できる仕様にする。
  - [x] 【完了】基本方針を決める
  - [ ] 【進行中】CLIコマンドを整理する
```

## インストール

```sh
curl -fsSL https://raw.githubusercontent.com/sgktmk/todos-cli/main/install.sh | sh
```

Python 3.9 以上があれば動く。**外部パッケージへの依存はない**（標準ライブラリのみ）。

`~/.local/share/todos-cli` に本体を置き、`~/.local/bin/todos` に起動スクリプトを作る。
`~/.local/bin` が `PATH` に無い場合は追加する。

<details>
<summary>その他のインストール方法</summary>

インストール先を変える:

```sh
curl -fsSL https://raw.githubusercontent.com/sgktmk/todos-cli/main/install.sh | TODOS_PREFIX=/usr/local sh
```

リポジトリから:

```sh
git clone https://github.com/sgktmk/todos-cli.git
cd todos-cli
./install.sh          # インストールする
./bin/todos --help    # インストールせず実行する
```

pip / pipx:

```sh
pipx install git+https://github.com/sgktmk/todos-cli.git
```

アンインストール:

```sh
curl -fsSL https://raw.githubusercontent.com/sgktmk/todos-cli/main/install.sh | sh -s -- --uninstall
```

`tasks.md` と `archive.md` は削除されない。

</details>

## はじめかた

```sh
todos add "READMEを書く"            # tasks.md が無ければ自動で作られる
todos add "リリース準備" -d 2026/07/31
todos list
todos                               # 引数なしで TUI が起動する
```

ファイルの置き場所は次の順で決まる。

1. `--dir` オプション
2. 環境変数 `TODOS_DIR`
3. カレントディレクトリから上へ辿って見つかった `tasks.md`
4. `~/todos`

プロジェクトごとに `tasks.md` を置けば、その配下ではそれが使われる。

## タスクの書きかた

```
- [ ] 【ステータス】タイトル（〜YYYY/MM/DD） #タグ
```

ステータス・期日・タグはすべて任意。最小形式は `- [ ] タイトル` だけ。

詳細文と子タスクは直下にインデントして書く。

```markdown
- [ ] 親タスク
  詳細な説明を書く。
  複数行の記述も許可する。
  - [ ] 子タスクA
  - [ ] 【進行中】子タスクB（〜2026/07/31）
```

### ステータス

| ステータス | 意味 | 期日が無いときの配置先 |
| --- | --- | --- |
| 未着手 | 着手していない（`【】` の省略も可） | ParkingLot |
| 進行中 | 着手済み | OpenEnded |
| 要返答 | 自分が返答する必要がある | OpenEnded |
| 回答待ち | 相手の回答を待っている | OpenEnded |
| 保留 | いったん止めている | ParkingLot |
| 完了 | 終了。`- [x]` にする | アーカイブ対象 |
| やらない | 実施しない。`~~取り消し線~~` にする | アーカイブ対象 |

要返答・回答待ちは相手を書ける。

```markdown
- [ ] 【要返答（山田）】レビューコメントに回答する
- [ ] 【回答待ち（デザインチーム）】画面案を確認する
```

ステータスが状態の正本で、チェックボックスと取り消し線は表示用。
`todos` 経由の操作では常に両方を同時に更新する。
Markdown を直接編集してずれた場合は `todos validate --fix` で揃える。

### 期日

正規表記は `（〜YYYY/MM/DD）`。
`（〜2026-07-31）` `（〜2026年7月31日）` `（〜7/31）` なども認識し、
`todos validate --fix` で正規形式へ書き換える。

日付として解釈できない記述は警告するだけで、勝手には書き換えない。

## セクションと自動配置

`tasks.md` には 5 つのセクションがある。配置は**実行時に毎回計算し直す**。

| セクション | 入るタスク |
| --- | --- |
| Today | 期限切れ・本日が期限・明日が期限 |
| Tomorrow | 明後日が期限 |
| InWeek | 当週の日曜日までが期限 |
| OpenEnded | 期限なしの 進行中 / 要返答 / 回答待ち |
| ParkingLot | 期限なしの 未着手 / 保留、来週以降が期限 |

```sh
todos rollover        # すべての未完了タスクを再評価して配置し直す
```

日付が変わって配置が合わなくなると、`todos list` や TUI 起動時に再配置を提案する。
提案では「どのタスクが・なぜ・どこへ動くか」を一覧で示してから確認する。

手動で移動したタスクも次回の再配置で再評価される。そのため `m`（TUI）でルールと
違うセクションへ移したタスクがあると毎回提案が出る。止めたいときは期日を設定して
ルールと一致させるか、`--no-prompt` を付けて起動する。

同じセクション内は「期日昇順 → 要返答を優先 → 記述順」で並ぶ。

## よく使うコマンド

```sh
todos add "タスク名"                  # 追加
todos add "レビュー対応" -s 要返答 --who 山田 -d 2026/07/28
todos add "子タスク" --parent 2       # 子タスクとして追加

todos list                            # 一覧
todos list Today                      # セクション指定
todos show 3                          # 詳細

todos status 3 進行中                 # ステータス変更
todos done 3                          # 完了
todos skip 3                          # やらない
todos move 3 Today                    # セクション移動
todos edit 3                          # 外部エディタで編集

todos search レビュー                 # キーワード検索
todos search --tag backend            # タグ検索

todos rollover                        # 再配置
todos archive                         # 完了・やらないを archive.md へ
todos report today                    # 日報用の候補一覧
todos validate --fix                  # 構文検証と修正
```

タスクの指定には、一覧の**表示番号**かタイトルの**部分一致**を使う。
複数該当したときは候補から選ぶ。

詳細は [`docs/cli.md`](docs/cli.md)。

## TUI

引数なしで `todos` を実行するとタスクダッシュボードが開く。

```
 トドズ  ~/todos/tasks.md              ○5  ◐2  ◆1  2026/07/28 (火)
╭─ Sections ───────╮╭─ Tasks / Today ─────────────────────────────╮
│ ● Today        4 ││▍ ◆ 要返答（山田）  レビュー対応 〜2026/07/28│
│ ● Tomorrow     1 ││  ◐ 進行中  API を実装 〜2026/07/29 #api ≡   │
│ ● InWeek       2 ││  ├ スキーマを決める                         │
│ ● OpenEnded    1 ││  ╰ ハンドラを書く                           │
│ ● ParkingLot   5 ││  README を書く                              │
╰──────────────────╯╰─────────────────────────────────────────────╯
 j/k:移動  J/K:セクション  Tab:ペイン  Enter:詳細  ?:ヘルプ  q:終了
 Today  1/6                                                    全て
```

`j`/`k` で移動、`J`/`K` でセクション移動、`a` で追加、`o` で子タスク追加、
`c` で複製、`[`/`]` で並べ替え、`s` でステータス変更、`d` で完了、`/` で検索、
`?` でヘルプ、`q` で終了。

編集は項目ごとにキーが分かれている。`i` タイトル / `D` 期日 / `T` タグ /
`I` 詳細文 / `m` セクション。`D` と `T` は空欄で確定すると削除。まとめて直したい
ときや子タスクを削除したいときは `e` で外部エディタを開く。

起動時はタスクがある最初のセクションを選ぶ。`j`/`k` はいま見ている一覧の中だけを
循環し、セクションをまたがない。セクションを移るのは `J`/`K` か、`Tab` で
Sections ペインへ移って選ぶ（ペインの行き来は `Tab` だけ）。

Today や Tomorrow を見ているときに `a` で追加すると、そのセクションに合う期日を
入れるか聞く。期日の無いタスクは ParkingLot へ置かれるため、作った直後に
見失わないようにするため。

`c` はタスク行（タイトル・ステータス・期日・タグ）だけを複製する。詳細文と
子タスクは複製しないので、少しだけ違うタスクを続けて作るのに使える。

ステータス・期日・タグは色分けされる（期日は近いほど強い色になる）。
配色は 256 色端末を前提とし、色数の少ない端末や `NO_COLOR` では自動的に
属性表示へ落とす。明るい端末では `TODOS_TUI_THEME=light` を指定する。

詳細文を持つタスクには行末に `≡` が付く。`Enter` の詳細モーダルで全文を読める
（端末幅で折り返す）。詳細文だけを編集したいときは `I`、タスク行や子タスクも
まとめて編集したいときは `e`。複数行の本文編集は `$EDITOR` / `$VISUAL` に委譲する。

詳細は [`docs/tui.md`](docs/tui.md)。

## 外部エディタ

`todos edit`（オプション省略時）と TUI の `I` / `e` は外部エディタを開く。
`VISUAL` を先に見て、無ければ `EDITOR` を使う。シェルの起動ファイル
（zsh なら `~/.zshrc`）に書いておく。

```sh
export EDITOR='vim'          # 端末内のエディタ
export EDITOR='code --wait'  # GUI エディタは --wait を付ける
```

GUI エディタで `--wait` を省くとプロセスが即座に終了し、編集前の内容が読まれる。

設定方法の詳細は [`docs/cli.md`](docs/cli.md#外部エディタの設定)。

## AI エージェントから使う

表示系コマンドは JSON を出力し、更新系コマンドは `--yes` で確認を省略できる。

```sh
todos list --json
todos show 3 --json
todos report today --json
todos rollover --yes
todos archive --yes
```

対象を一意に特定できない場合、変更せず終了コード 2 を返す。
エージェントには Markdown を直接編集させず、`todos` コマンド経由で操作させる。

## 開発

```sh
python3 -m unittest discover -s tests   # テスト
./bin/todos --help                      # インストールせず実行
python3 tests/drive_tui.py "jjs"        # 疑似端末で TUI の画面を確認
```

- [`docs/decisions.md`](docs/decisions.md) — 要件定義書の未確定事項に対する決定と理由
- [`docs/cli.md`](docs/cli.md) — CLI コマンド仕様
- [`docs/tui.md`](docs/tui.md) — TUI 仕様

### 構成

```
Markdown 解析 (parser)
    ↓
ドメインモデル (model)
    ↓
タスク操作 (ops / schedule / validate)
 ┌──┴──┐
CLI    TUI
```

CLI と TUI は解析・操作ロジックを共有する。TUI が独自に Markdown を書き換えることはない。

| モジュール | 役割 |
| --- | --- |
| `todos/model.py` | ステータス・セクション・Task |
| `todos/parser.py` | Markdown → ドメインモデル |
| `todos/render.py` | ドメインモデル → Markdown |
| `todos/store.py` | ファイル探索と原子的な書き込み |
| `todos/schedule.py` | 優先度判定・自動配置・並び順 |
| `todos/ops.py` | 追加・状態変更・移動・検索・アーカイブ・日報 |
| `todos/validate.py` | 構文検証と修正 |
| `todos/editor.py` | 外部エディタ連携 |
| `todos/cli.py` | コマンドライン |
| `todos/tui.py` | 端末 UI |
| `todos/theme.py` | TUI の配色とグリフ |

## 制約

- タスク ID を持たないため、同名タスクを完全には識別できない
- 履歴・完了日時を持たないため、日報の当日性は保証できない
- 人間による直接編集を許可するため、不正な状態を事前には防止できない
- TUI と外部エディタで同時に編集した場合、競合する可能性がある

## ライセンス

MIT
