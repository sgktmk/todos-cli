#!/usr/bin/env python3
"""トドズの単体テスト。標準ライブラリの unittest のみを使う。

実行: python3 -m unittest discover -s tests
"""

import datetime as dt
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from todos import cli, editor, model, ops, parser, render, schedule, store, util, validate  # noqa: E402
from todos import issues as iss  # noqa: E402

TUE = dt.date(2026, 7, 28)  # 火曜日。当週の日曜は 2026/08/02


class ParseTest(unittest.TestCase):
    def parse_one(self, line, base=TUE):
        doc = parser.parse_text("## Today\n\n%s\n" % line, base=base)
        tasks = doc.section("Today").tasks
        self.assertEqual(len(tasks), 1)
        return tasks[0]

    def test_minimal_form(self):
        task = self.parse_one("- [ ] タイトル")
        self.assertEqual(task.title, "タイトル")
        self.assertEqual(task.status, model.TODO)
        self.assertFalse(task.status_explicit)
        self.assertIsNone(task.due)
        self.assertEqual(task.tags, [])

    def test_canonical_form(self):
        task = self.parse_one(
            "- [ ] 【進行中】要件定義を作成する（〜2026/07/31） #todos #docs"
        )
        self.assertEqual(task.title, "要件定義を作成する")
        self.assertEqual(task.status, model.DOING)
        self.assertEqual(task.due, dt.date(2026, 7, 31))
        self.assertEqual(task.tags, ["todos", "docs"])

    def test_counterpart(self):
        task = self.parse_one("- [ ] 【要返答（山田）】レビューに回答する")
        self.assertEqual(task.status, model.NEEDS_REPLY)
        self.assertEqual(task.counterpart, "山田")
        self.assertEqual(task.status_label(), "要返答（山田）")

    def test_unknown_status_is_preserved(self):
        doc = parser.parse_text("## Today\n\n- [ ] 【なんとか】x\n", base=TUE)
        task = doc.section("Today").tasks[0]
        self.assertEqual(task.status, "なんとか")
        kinds = [i.kind for i in doc.issues]
        self.assertIn(iss.UNKNOWN_STATUS, kinds)
        # 未知のステータスでも書き戻しで失われない
        self.assertIn("【なんとか】", render.render_task_line(task))

    def test_detail_and_children(self):
        text = (
            "## Today\n\n"
            "- [ ] 親タスク\n"
            "  詳細1\n"
            "  詳細2\n"
            "  - [x] 【完了】子A\n"
            "  - [ ] 【進行中】子B（〜2026/07/31）\n"
        )
        doc = parser.parse_text(text, base=TUE)
        parent = doc.section("Today").tasks[0]
        self.assertEqual(parent.detail, ["詳細1", "詳細2"])
        self.assertEqual(len(parent.children), 2)
        self.assertEqual(parent.children[0].status, model.DONE)
        self.assertTrue(parent.children[0].checked)
        self.assertEqual(parent.children[1].due, dt.date(2026, 7, 31))
        self.assertEqual(parent.children[1].depth, 1)

    def test_strikethrough(self):
        task = self.parse_one("- [ ] 【やらない】~~やめたタスク~~")
        self.assertEqual(task.title, "やめたタスク")
        self.assertTrue(parser.is_struck(task))

    def test_non_canonical_due_is_recognized(self):
        for text, expected in [
            ("- [ ] a（〜2026-07-31）", dt.date(2026, 7, 31)),
            ("- [ ] a（〜2026年7月31日）", dt.date(2026, 7, 31)),
            ("- [ ] a (~2026/7/31)", dt.date(2026, 7, 31)),
            ("- [ ] a（期限: 2026/07/31）", dt.date(2026, 7, 31)),
            ("- [ ] a（〜7/31）", dt.date(2026, 7, 31)),
        ]:
            with self.subTest(text=text):
                task = self.parse_one(text)
                self.assertEqual(task.due, expected)
                self.assertEqual(task.title, "a")

    def test_unparsable_due_stays_in_title(self):
        doc = parser.parse_text("## Today\n\n- [ ] a（〜来週の火曜）\n", base=TUE)
        task = doc.section("Today").tasks[0]
        self.assertIsNone(task.due)
        self.assertIn("来週の火曜", task.title)
        self.assertIn(iss.UNPARSABLE_DUE, [i.kind for i in doc.issues])

    def test_parenthetical_without_date_is_not_a_due(self):
        task = self.parse_one("- [ ] 山田さん（確認担当）に聞く")
        self.assertIsNone(task.due)
        self.assertEqual(task.title, "山田さん（確認担当）に聞く")

    def test_non_task_bullet_reported(self):
        doc = parser.parse_text("## Today\n\n- ただの箇条書き\n", base=TUE)
        self.assertIn(iss.UNPARSABLE_BULLET, [i.kind for i in doc.issues])

    def test_missing_sections_reported(self):
        doc = parser.parse_text("## Today\n", base=TUE)
        missing = [i for i in doc.issues if i.kind == iss.MISSING_SECTION]
        self.assertEqual(len(missing), 4)


class RenderTest(unittest.TestCase):
    def test_round_trip_preserves_content(self):
        text = (
            "# tasks\n\n"
            "## Today\n\n"
            "- [ ] 【要返答（山田）】レビュー対応（〜2026/07/28） #review\n"
            "  詳細文\n"
            "  - [x] 【完了】子タスク\n\n"
            "## Tomorrow\n\n"
            "## InWeek\n\n"
            "## OpenEnded\n\n"
            "## ParkingLot\n\n"
            "- [ ] 【やらない】~~やめた~~\n"
        )
        doc = parser.parse_text(text, base=TUE)
        self.assertEqual(render.render_document(doc), text)

    def test_indent_is_normalized_to_two_spaces(self):
        doc = parser.parse_text("## Today\n\n- [ ] 親\n    - [ ] 子\n", base=TUE)
        out = render.render_document(doc)
        self.assertIn("\n  - [ ] 子\n", out)

    def test_unknown_heading_content_preserved(self):
        text = "## Today\n\n## メモ\n\nこれは覚え書き\n"
        doc = parser.parse_text(text, base=TUE)
        self.assertIn("これは覚え書き", render.render_document(doc))


class ScheduleTest(unittest.TestCase):
    def place(self, **kwargs):
        task = model.Task(title="x", **kwargs)
        return schedule.desired_section(task, TUE)

    def test_due_based_placement(self):
        cases = [
            (dt.date(2026, 7, 20), model.TODAY),      # 期限切れ
            (dt.date(2026, 7, 28), model.TODAY),      # 本日
            (dt.date(2026, 7, 29), model.TODAY),      # 明日（前日から Today）
            (dt.date(2026, 7, 30), model.TOMORROW),   # 明後日
            (dt.date(2026, 8, 1), model.IN_WEEK),     # 当週の土曜
            (dt.date(2026, 8, 2), model.IN_WEEK),     # 当週の日曜
            (dt.date(2026, 8, 3), model.PARKING_LOT), # 翌週の月曜
        ]
        for due, expected in cases:
            with self.subTest(due=due):
                self.assertEqual(self.place(due=due), expected)

    def test_open_ended_placement(self):
        for status in model.IN_FLIGHT_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(self.place(status=status), model.OPEN_ENDED)

    def test_parking_lot_placement(self):
        for status in (model.TODO, model.HOLD):
            with self.subTest(status=status):
                self.assertEqual(self.place(status=status), model.PARKING_LOT)

    def test_terminal_is_not_placed(self):
        for status in model.TERMINAL_STATUSES:
            with self.subTest(status=status):
                self.assertIsNone(self.place(status=status))

    def test_hold_with_near_due_goes_to_today(self):
        self.assertEqual(
            self.place(status=model.HOLD, due=dt.date(2026, 7, 29)), model.TODAY
        )

    def test_week_end_is_sunday(self):
        self.assertEqual(util.week_end(dt.date(2026, 7, 28)), dt.date(2026, 8, 2))
        self.assertEqual(util.week_end(dt.date(2026, 8, 2)), dt.date(2026, 8, 2))
        self.assertEqual(util.week_end(dt.date(2026, 8, 3)), dt.date(2026, 8, 9))

    def test_rollover_moves_all_incomplete_tasks(self):
        text = (
            "## Today\n\n- [ ] 先の予定（〜2026/12/31）\n\n"
            "## Tomorrow\n\n## InWeek\n\n## OpenEnded\n\n"
            "## ParkingLot\n\n- [ ] 【進行中】仕掛中\n"
        )
        doc = parser.parse_text(text, base=TUE)
        moves = schedule.rollover(doc, TUE)
        self.assertEqual(len(moves), 2)
        self.assertEqual([t.title for t in doc.section(model.PARKING_LOT).tasks],
                         ["先の予定"])
        self.assertEqual([t.title for t in doc.section(model.OPEN_ENDED).tasks],
                         ["仕掛中"])

    def test_sort_order_prefers_needs_reply_on_same_due(self):
        text = (
            "## Today\n\n"
            "- [ ] 【進行中】あとで（〜2026/07/28）\n"
            "- [ ] 【要返答】さきに（〜2026/07/28）\n"
            "- [ ] 【進行中】期日なし\n"
        )
        doc = parser.parse_text(text, base=TUE)
        schedule.sort_section(doc.section(model.TODAY))
        titles = [t.title for t in doc.section(model.TODAY).tasks]
        self.assertEqual(titles, ["さきに", "あとで", "期日なし"])

    def test_children_move_with_parent(self):
        text = "## Today\n\n- [ ] 親\n  - [ ] 子\n\n## ParkingLot\n"
        doc = parser.parse_text(text, base=TUE)
        parent = doc.section(model.TODAY).tasks[0]
        schedule.move(doc, parent, model.PARKING_LOT)
        self.assertEqual(doc.section(model.TODAY).tasks, [])
        moved = doc.section(model.PARKING_LOT).tasks[0]
        self.assertEqual(moved.children[0].title, "子")
        self.assertEqual(moved.children[0].section, model.PARKING_LOT)

    def test_child_cannot_move_alone(self):
        doc = parser.parse_text("## Today\n\n- [ ] 親\n  - [ ] 子\n", base=TUE)
        child = doc.section(model.TODAY).tasks[0].children[0]
        with self.assertRaises(util.TodosError):
            schedule.move(doc, child, model.PARKING_LOT)


class OpsTest(unittest.TestCase):
    def make_doc(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        return doc

    def test_add_places_by_schedule(self):
        doc = self.make_doc()
        task = ops.add(doc, "期日つき", TUE, due=dt.date(2026, 7, 28))
        self.assertEqual(task.section, model.TODAY)
        task = ops.add(doc, "仕掛中", TUE, status=model.DOING)
        self.assertEqual(task.section, model.OPEN_ENDED)
        task = ops.add(doc, "ただの追加", TUE)
        self.assertEqual(task.section, model.PARKING_LOT)

    def test_add_requires_title(self):
        with self.assertRaises(util.TodosError):
            ops.add(self.make_doc(), "   ", TUE)

    def test_status_updates_checkbox_and_strike_together(self):
        doc = self.make_doc()
        task = ops.add(doc, "x", TUE)
        ops.set_status(task, model.DONE)
        self.assertTrue(task.checked)
        self.assertFalse(parser.is_struck(task))
        ops.set_status(task, model.SKIP)
        self.assertFalse(task.checked)
        self.assertTrue(parser.is_struck(task))
        ops.set_status(task, model.DOING)
        self.assertFalse(task.checked)
        self.assertFalse(parser.is_struck(task))

    def test_counterpart_rejected_for_other_statuses(self):
        doc = self.make_doc()
        task = ops.add(doc, "x", TUE)
        with self.assertRaises(util.TodosError):
            ops.set_status(task, model.DOING, "山田")

    def test_open_children_detected(self):
        doc = self.make_doc()
        parent = ops.add(doc, "親", TUE)
        child = ops.add(doc, "子", TUE, parent=parent)
        self.assertEqual(ops.open_children(parent), [child])
        ops.set_status(child, model.DONE)
        self.assertEqual(ops.open_children(parent), [])

    def test_resolve_by_index_and_title(self):
        doc = self.make_doc()
        ops.add(doc, "レビュー対応", TUE)
        ops.add(doc, "レビュー準備", TUE)
        self.assertEqual(len(ops.resolve(doc, "レビュー")), 2)
        self.assertEqual(ops.resolve(doc, "レビュー対応")[0].title, "レビュー対応")
        self.assertEqual(ops.resolve(doc, "1")[0].index, 1)

    def test_search_by_tag_and_keyword(self):
        doc = self.make_doc()
        ops.add(doc, "API実装", TUE, tags=["backend"])
        ops.add(doc, "画面実装", TUE, tags=["frontend"], detail="backend と連携する")
        self.assertEqual([t.title for t in ops.search(doc, tag="backend")], ["API実装"])
        self.assertEqual(len(ops.search(doc, keyword="backend")), 1)
        self.assertEqual(len(ops.search(doc, keyword="実装")), 2)

    def test_report_groups_by_status(self):
        doc = self.make_doc()
        ops.add(doc, "仕掛", TUE, status=model.DOING)
        ops.add(doc, "済", TUE, status=model.DONE)
        ops.add(doc, "やめ", TUE, status=model.SKIP)
        data = ops.report(doc)
        self.assertEqual([t.title for t in data["in_flight"]], ["仕掛"])
        self.assertEqual([t.title for t in data["done"]], ["済"])
        self.assertEqual([t.title for t in data["skipped"]], ["やめ"])


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store(Path(self.tmp.name))
        self.store.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_terminal_child_stays_with_open_parent(self):
        doc = self.store.load(TUE)
        parent = ops.add(doc, "親", TUE)
        child = ops.add(doc, "子", TUE, parent=parent)
        ops.set_status(child, model.DONE)
        self.assertEqual(ops.archivable(doc), [])

    def test_archive_moves_whole_subtree(self):
        doc = self.store.load(TUE)
        parent = ops.add(doc, "親", TUE)
        ops.add(doc, "子", TUE, parent=parent)
        ops.set_status(parent, model.DONE)
        archived = ops.archive(doc, self.store, TUE)
        self.store.save(doc)
        self.assertEqual([t.title for t in archived], ["親"])
        self.assertEqual(doc.all_tasks(), [])
        text = self.store.read_archive()
        self.assertIn("# 2026/07/28", text)
        self.assertIn("- [x] 【完了】親", text)
        self.assertIn("  - [ ] 子", text)

    def test_archive_appends_under_existing_date_heading(self):
        doc = self.store.load(TUE)
        ops.set_status(ops.add(doc, "A", TUE), model.DONE)
        ops.archive(doc, self.store, TUE)
        ops.set_status(ops.add(doc, "B", TUE), model.SKIP)
        ops.archive(doc, self.store, TUE)
        text = self.store.read_archive()
        self.assertEqual(text.count("# 2026/07/28"), 1)
        self.assertIn("- [x] 【完了】A", text)
        self.assertIn("- [ ] 【やらない】~~B~~", text)

    def test_newer_archive_date_goes_on_top(self):
        doc = self.store.load(TUE)
        ops.set_status(ops.add(doc, "古い", TUE), model.DONE)
        ops.archive(doc, self.store, TUE)
        ops.set_status(ops.add(doc, "新しい", TUE), model.DONE)
        ops.archive(doc, self.store, dt.date(2026, 7, 29))
        text = self.store.read_archive()
        self.assertLess(text.index("# 2026/07/29"), text.index("# 2026/07/28"))


class ValidateTest(unittest.TestCase):
    def kinds(self, text):
        doc = parser.parse_text(text, base=TUE)
        return doc, [i.kind for i in validate.validate(doc)]

    def test_checkbox_mismatch_both_directions(self):
        _, kinds = self.kinds("## Today\n\n- [x] チェックだけ\n- [ ] 【完了】未チェック\n")
        self.assertEqual(kinds.count(iss.CHECKBOX_MISMATCH), 2)

    def test_strike_mismatch_both_directions(self):
        _, kinds = self.kinds("## Today\n\n- [ ] ~~線だけ~~\n- [ ] 【やらない】線なし\n")
        self.assertEqual(kinds.count(iss.STRIKE_MISMATCH), 2)

    def test_parent_done_with_open_children(self):
        _, kinds = self.kinds("## Today\n\n- [x] 【完了】親\n  - [ ] 子\n")
        self.assertIn(iss.PARENT_DONE_OPEN_CHILDREN, kinds)

    def test_fix_checkbox_only_edit_sets_status(self):
        doc = parser.parse_text("## Today\n\n- [x] チェックだけ\n", base=TUE)
        found = [i for i in validate.validate(doc) if i.kind == iss.CHECKBOX_MISMATCH]
        self.assertTrue(validate.apply_fix(doc, found[0]))
        self.assertEqual(doc.section("Today").tasks[0].status, model.DONE)

    def test_fix_normalizes_due_format(self):
        doc = parser.parse_text("## Today\n\n- [ ] a（〜2026-07-31）\n", base=TUE)
        found = [i for i in validate.validate(doc) if i.kind == iss.NON_CANONICAL_DUE]
        self.assertEqual(len(found), 1)
        validate.apply_fix(doc, found[0])
        self.assertIn("（〜2026/07/31）", render.render_document(doc))

    def test_unparsable_due_is_not_fixable(self):
        doc = parser.parse_text("## Today\n\n- [ ] a（〜来週）\n", base=TUE)
        found = [i for i in validate.validate(doc) if i.kind == iss.UNPARSABLE_DUE]
        self.assertTrue(found)
        self.assertFalse(found[0].fixable)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_all_sections(self):
        st = store.Store(self.dir)
        self.assertTrue(st.init())
        doc = st.load(TUE)
        self.assertEqual([s.name for s in doc.known_sections()], list(model.SECTIONS))
        self.assertFalse(st.init())

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        st = store.Store(self.dir)
        st.init()
        doc = st.load(TUE)
        ops.add(doc, "x", TUE)
        st.save(doc)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_resolve_dir_prefers_env(self):
        old = os.environ.get("TODOS_DIR")
        os.environ["TODOS_DIR"] = str(self.dir)
        try:
            self.assertEqual(store.resolve_dir(), self.dir.resolve())
        finally:
            if old is None:
                os.environ.pop("TODOS_DIR")
            else:
                os.environ["TODOS_DIR"] = old


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        os.environ["TODOS_TODAY"] = "2026/07/28"

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("TODOS_TODAY", None)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--dir", str(self.dir), *args])
        return code, buf.getvalue()

    def test_add_and_list(self):
        self.assertEqual(self.run_cli("init")[0], 0)
        code, _ = self.run_cli("add", "テスト", "-d", "2026/07/28")
        self.assertEqual(code, 0)
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("テスト", out)

    def test_json_output_is_machine_readable(self):
        import json

        self.run_cli("init")
        self.run_cli("add", "JSONテスト", "-s", "進行中")
        code, out = self.run_cli("list", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        titles = [
            t["title"] for s in data["sections"] for t in s["tasks"]
        ]
        self.assertIn("JSONテスト", titles)

    def test_yes_flag_works_before_and_after_subcommand(self):
        self.run_cli("init")
        self.run_cli("add", "済", "-s", "完了")
        code, out = self.run_cli("archive", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("1 件", out)
        self.run_cli("add", "済2", "-s", "完了")
        code, out = self.run_cli("--yes", "archive")
        self.assertEqual(code, 0)
        self.assertIn("1 件", out)

    def test_ambiguous_selector_is_an_error_when_non_interactive(self):
        self.run_cli("init")
        self.run_cli("add", "レビュー対応")
        self.run_cli("add", "レビュー準備")
        code, _ = self.run_cli("done", "レビュー")
        self.assertEqual(code, cli.EXIT_AMBIGUOUS)

    def test_unknown_status_is_rejected(self):
        self.run_cli("init")
        self.run_cli("add", "x")
        code, _ = self.run_cli("status", "1", "なんとなく")
        self.assertEqual(code, cli.EXIT_ERROR)

    def test_completing_parent_with_open_children_needs_yes(self):
        self.run_cli("init")
        self.run_cli("add", "親")
        self.run_cli("add", "子", "--parent", "1")
        code, _ = self.run_cli("done", "親")
        self.assertEqual(code, cli.EXIT_ERROR)
        code, _ = self.run_cli("done", "親", "--yes")
        self.assertEqual(code, 0)

    def test_report_lists_candidates(self):
        self.run_cli("init")
        self.run_cli("add", "仕掛", "-s", "進行中")
        self.run_cli("add", "済", "-s", "完了")
        code, out = self.run_cli("report", "today")
        self.assertEqual(code, 0)
        self.assertIn("仕掛", out)
        self.assertIn("済", out)

    def test_validate_exit_code(self):
        (self.dir / "tasks.md").write_text(
            "## Today\n\n- [x] ずれ\n\n## Tomorrow\n\n## InWeek\n\n"
            "## OpenEnded\n\n## ParkingLot\n",
            encoding="utf-8",
        )
        code, _ = self.run_cli("validate")
        self.assertEqual(code, cli.EXIT_ERROR)
        code, _ = self.run_cli("validate", "--fix", "--yes")
        self.assertEqual(code, 0)
        code, _ = self.run_cli("validate")
        self.assertEqual(code, 0)


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old_editor = os.environ.get("EDITOR")

    def tearDown(self):
        self.tmp.cleanup()
        if self.old_editor is None:
            os.environ.pop("EDITOR", None)
        else:
            os.environ["EDITOR"] = self.old_editor

    def fake_editor(self, replacement: str):
        """引数のファイルを replacement で上書きするだけのエディタを用意する。"""
        script = self.dir / "fake-editor.sh"
        payload = self.dir / "payload.md"
        payload.write_text(replacement, encoding="utf-8")
        script.write_text(
            '#!/bin/sh\ncat "%s" > "$1"\n' % payload, encoding="utf-8"
        )
        script.chmod(0o755)
        os.environ["EDITOR"] = str(script)

    def test_strip_comments_removes_guidance(self):
        text = "<!-- 案内\n     続き -->\n- [ ] タスク\n"
        self.assertEqual(editor._strip_comments(text), "- [ ] タスク")

    def test_edit_replaces_task_with_children(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        task = ops.add(doc, "元のタイトル", TUE)
        self.fake_editor(
            "- [ ] 【進行中】新しいタイトル（〜2026/07/31） #tag\n"
            "  詳細文\n"
            "  - [ ] 子タスク\n"
        )
        replacement = editor.edit_task(doc, task)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.title, "新しいタイトル")
        self.assertEqual(replacement.status, model.DOING)
        self.assertEqual(replacement.due, dt.date(2026, 7, 31))
        self.assertEqual(replacement.detail, ["詳細文"])
        self.assertEqual(len(replacement.children), 1)
        self.assertEqual(replacement.section, model.PARKING_LOT)
        self.assertEqual([t.title for t in doc.top_tasks()], ["新しいタイトル"])

    def test_edit_without_change_returns_none(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        task = ops.add(doc, "変えない", TUE)
        self.fake_editor("- [ ] 変えない\n")
        self.assertIsNone(editor.edit_task(doc, task))

    def test_edit_rejects_empty_result(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        task = ops.add(doc, "消さない", TUE)
        self.fake_editor("\n")
        with self.assertRaises(util.TodosError):
            editor.edit_task(doc, task)
        self.assertEqual([t.title for t in doc.top_tasks()], ["消さない"])


class UtilTest(unittest.TestCase):
    def test_display_width_counts_wide_characters(self):
        self.assertEqual(util.display_width("abc"), 3)
        self.assertEqual(util.display_width("あいう"), 6)
        self.assertEqual(util.display_width("あa"), 3)

    def test_pad_and_truncate_respect_display_width(self):
        self.assertEqual(util.display_width(util.pad("あい", 10)), 10)
        self.assertEqual(util.display_width(util.pad("あいうえお", 6)), 6)
        # 切り詰めた結果が指定幅を超えないこと（省略記号の幅は端末依存）
        self.assertLessEqual(util.display_width(util.truncate("あいうえお", 6)), 6)
        self.assertLess(len(util.truncate("あいうえお", 6)), len("あいうえお"))
        self.assertEqual(util.truncate("あいうえお", 20), "あいうえお")

    def test_ambiguous_width_is_configurable(self):
        old = os.environ.get("TODOS_AMBIGUOUS_WIDTH")
        try:
            os.environ["TODOS_AMBIGUOUS_WIDTH"] = "2"
            self.assertEqual(util.display_width("…"), 2)
            os.environ["TODOS_AMBIGUOUS_WIDTH"] = "1"
            self.assertEqual(util.display_width("…"), 1)
        finally:
            if old is None:
                os.environ.pop("TODOS_AMBIGUOUS_WIDTH", None)
            else:
                os.environ["TODOS_AMBIGUOUS_WIDTH"] = old

    def test_parse_date_variants(self):
        self.assertEqual(util.parse_date("2026/07/31"), dt.date(2026, 7, 31))
        self.assertEqual(util.parse_date("2026-7-31"), dt.date(2026, 7, 31))
        self.assertEqual(util.parse_date("2026年7月31日"), dt.date(2026, 7, 31))
        self.assertEqual(util.parse_date("７/３１", TUE), dt.date(2026, 7, 31))
        self.assertIsNone(util.parse_date("来週"))


if __name__ == "__main__":
    unittest.main()
