#!/usr/bin/env python3
"""トドズの単体テスト。標準ライブラリの unittest のみを使う。

実行: python3 -m unittest discover -s tests
"""

import curses
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from todos import (  # noqa: E402
    cli, editor, model, ops, parser, render, schedule, store, theme, tui, util, validate,
)
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

    def test_section_due_is_the_inverse_of_placement(self):
        for section in model.SECTIONS:
            with self.subTest(section=section):
                due = schedule.section_due(section, TUE)
                if due is None:
                    continue
                self.assertEqual(self.place(due=due), section)

    def test_section_due_covers_only_date_driven_sections(self):
        self.assertEqual(schedule.section_due(model.TODAY, TUE), TUE)
        self.assertEqual(schedule.section_due(model.TOMORROW, TUE),
                         dt.date(2026, 7, 30))
        self.assertEqual(schedule.section_due(model.IN_WEEK, TUE),
                         dt.date(2026, 7, 31))  # 当週の金曜（日曜ではなく稼働日の終わり）
        self.assertIsNone(schedule.section_due(model.OPEN_ENDED, TUE))
        self.assertIsNone(schedule.section_due(model.PARKING_LOT, TUE))

    def test_section_due_for_in_week_is_always_a_friday(self):
        monday = dt.date(2026, 7, 27)
        for base, expected in ((monday, dt.date(2026, 7, 31)), (TUE, dt.date(2026, 7, 31))):
            with self.subTest(base=base):
                due = schedule.section_due(model.IN_WEEK, base)
                self.assertEqual(due, expected)
                self.assertEqual(due.weekday(), 4)  # 金曜
                self.assertEqual(schedule.desired_section(
                    model.Task(title="x", due=due), base), model.IN_WEEK)

    def test_section_due_gives_up_when_no_weekday_is_left_in_the_week(self):
        # 水曜以降は当週の金曜が Today / Tomorrow の範囲に入る（または過ぎている）
        for base in (dt.date(2026, 7, 29),   # 水
                     dt.date(2026, 7, 30),   # 木
                     dt.date(2026, 7, 31),   # 金
                     dt.date(2026, 8, 1),    # 土
                     dt.date(2026, 8, 2)):   # 日
            with self.subTest(base=base):
                self.assertIsNone(schedule.section_due(model.IN_WEEK, base))

    def reason(self, **kwargs):
        return schedule.reason(model.Task(title="x", **kwargs), TUE)

    def test_reason_explains_each_placement(self):
        cases = [
            ({"due": dt.date(2026, 7, 20)}, "期限切れ"),
            ({"due": dt.date(2026, 7, 28)}, "本日が期限"),
            ({"due": dt.date(2026, 7, 29)}, "明日が期限"),
            ({"due": dt.date(2026, 7, 30)}, "明後日が期限"),
            ({"due": dt.date(2026, 8, 2)}, "今週が期限"),
            ({"due": dt.date(2026, 8, 3)}, "来週以降が期限"),
            ({"status": model.TODO}, "期日なし"),
            ({"status": model.HOLD}, "期日なし"),
            ({"status": model.DOING}, "期日なし・仕掛中"),
            ({"status": model.DONE}, "終了済み"),
        ]
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(self.reason(**kwargs), expected)

    def test_reason_fits_the_preview_column(self):
        # TUI の理由列は 18 桁。はみ出すと表が崩れる
        for status in model.STATUSES:
            for due in (None, dt.date(2026, 7, 20), dt.date(2026, 9, 1)):
                task = model.Task(title="x", status=status, due=due)
                with self.subTest(status=status, due=due):
                    self.assertLessEqual(
                        util.display_width(schedule.reason(task, TUE)), 18
                    )

    def test_rollover_converges_so_it_is_not_proposed_again(self):
        """一度承諾したら次回は提案されないこと（同じ基準日なら食い違いは消える）。"""
        doc = parser.parse_text(
            "## Today\n\n"
            "- [ ] 期日なし\n"
            "- [ ] 【進行中】期日なしで進行中\n"
            "- [ ] 先の予定（〜2026/09/30）\n"
            "## Tomorrow\n\n- [ ] 明日（〜2026/07/29）\n"
            "## InWeek\n\n## OpenEnded\n\n## ParkingLot\n\n"
            "- [ ] 【要返答】期限切れ（〜2026/07/01）\n",
            base=TUE,
        )
        self.assertTrue(schedule.needs_rollover(doc, TUE))
        schedule.rollover(doc, TUE)
        self.assertFalse(schedule.needs_rollover(doc, TUE))
        # 書き出して読み直しても再提案されない
        again = parser.parse_text(render.render_document(doc), base=TUE)
        self.assertFalse(schedule.needs_rollover(again, TUE))

    def test_manual_move_against_the_rule_is_reported_again(self):
        """手動移動は要件11.6のとおり再評価対象。提案が再度出るのは仕様。"""
        doc = parser.parse_text(
            "## Today\n\n## Tomorrow\n\n## InWeek\n\n## OpenEnded\n\n"
            "## ParkingLot\n\n- [ ] 期日なしだが今日やりたい\n",
            base=TUE,
        )
        task = doc.section(model.PARKING_LOT).tasks[0]
        schedule.move(doc, task, model.TODAY)
        self.assertTrue(schedule.needs_rollover(doc, TUE))
        self.assertEqual(schedule.misplaced(doc, TUE), [(task, model.PARKING_LOT)])

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

    # ------------------------------------------------------------ 並べ替え

    def test_reorder_moves_a_task_within_its_section(self):
        doc = self.make_doc()
        for title in ("1件目", "2件目", "3件目"):
            ops.add(doc, title, TUE, section=model.TODAY)
        second = doc.section(model.TODAY).tasks[1]
        self.assertTrue(ops.reorder(doc, second, -1))
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["2件目", "1件目", "3件目"])
        self.assertTrue(ops.reorder(doc, second, 1))
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["1件目", "2件目", "3件目"])

    def test_reorder_stops_at_the_ends(self):
        doc = self.make_doc()
        first = ops.add(doc, "1件目", TUE, section=model.TODAY)
        ops.add(doc, "2件目", TUE, section=model.TODAY)
        self.assertFalse(ops.reorder(doc, first, -1))
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["1件目", "2件目"])

    def test_reorder_keeps_children_under_their_parent(self):
        doc = self.make_doc()
        parent = ops.add(doc, "親", TUE, section=model.TODAY)
        ops.add(doc, "子1", TUE, parent=parent)
        second = ops.add(doc, "子2", TUE, parent=parent)
        other = ops.add(doc, "別の親", TUE, section=model.TODAY)
        self.assertTrue(ops.reorder(doc, second, -1))
        self.assertEqual([c.title for c in parent.children], ["子2", "子1"])
        # 兄弟の外へは出ない
        self.assertFalse(ops.reorder(doc, second, -1))
        self.assertEqual(len(other.children), 0)

    def test_reorder_survives_a_round_trip(self):
        doc = self.make_doc()
        ops.add(doc, "1件目", TUE, section=model.TODAY)
        second = ops.add(doc, "2件目", TUE, section=model.TODAY)
        ops.reorder(doc, second, -1)
        again = parser.parse_text(render.render_document(doc), base=TUE)
        self.assertEqual([t.title for t in again.section(model.TODAY).tasks],
                         ["2件目", "1件目"])

    def test_reorder_leaves_other_lines_in_place(self):
        doc = parser.parse_text(
            "## Today\n\n覚え書き\n\n- [ ] 1件目\n- [ ] 2件目\n", base=TUE
        )
        second = doc.section(model.TODAY).tasks[1]
        ops.reorder(doc, second, -1)
        text = render.render_document(doc)
        self.assertLess(text.index("覚え書き"), text.index("2件目"))

    # ------------------------------------------------------------ 複製

    def test_duplicate_copies_the_task_line_only(self):
        doc = self.make_doc()
        task = ops.add(doc, "日報を書く", TUE, status=model.WAITING,
                       counterpart="山田", due=dt.date(2026, 7, 28),
                       tags=["work"], detail="ひな形は共有ドライブ")
        ops.add(doc, "子", TUE, parent=task)
        copy = ops.duplicate(doc, task)
        self.assertEqual(copy.title, task.title)
        self.assertEqual(copy.status, model.WAITING)
        self.assertEqual(copy.counterpart, "山田")
        self.assertEqual(copy.due, dt.date(2026, 7, 28))
        self.assertEqual(copy.tags, ["work"])
        # 詳細文と子タスクは引き継がない
        self.assertEqual(copy.detail, [])
        self.assertEqual(copy.children, [])
        self.assertIsNot(copy.tags, task.tags)

    def test_duplicate_lands_right_below_the_original(self):
        doc = self.make_doc()
        for title in ("1件目", "2件目"):
            ops.add(doc, title, TUE, section=model.TODAY)
        first = doc.section(model.TODAY).tasks[0]
        ops.duplicate(doc, first)
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["1件目", "1件目", "2件目"])

    def test_duplicate_of_a_child_stays_a_child(self):
        doc = self.make_doc()
        parent = ops.add(doc, "親", TUE, section=model.TODAY)
        child = ops.add(doc, "子", TUE, parent=parent)
        copy = ops.duplicate(doc, child)
        self.assertIs(copy.parent, parent)
        self.assertEqual([c.title for c in parent.children], ["子", "子"])
        self.assertEqual(copy.section, model.TODAY)

    def test_duplicate_survives_a_round_trip(self):
        doc = self.make_doc()
        task = ops.add(doc, "返事を書く", TUE, status=model.WAITING,
                       counterpart="山田", tags=["work"])
        ops.duplicate(doc, task)
        again = parser.parse_text(render.render_document(doc), base=TUE)
        copies = [t for t in again.all_tasks() if t.title == "返事を書く"]
        self.assertEqual(len(copies), 2)
        for copy in copies:
            self.assertEqual(copy.status, model.WAITING)
            self.assertEqual(copy.counterpart, "山田")
            self.assertEqual(copy.tags, ["work"])

    def test_report_groups_by_status(self):
        doc = self.make_doc()
        ops.add(doc, "仕掛", TUE, status=model.DOING)
        ops.add(doc, "済", TUE, status=model.DONE)
        ops.add(doc, "やめ", TUE, status=model.SKIP)
        data = ops.report(doc)
        self.assertEqual([t.title for t in data["in_flight"]], ["仕掛"])
        self.assertEqual([t.title for t in data["done"]], ["済"])
        self.assertEqual([t.title for t in data["skipped"]], ["やめ"])


class TagOpsTest(unittest.TestCase):
    def task(self, tags=()):
        return model.Task(title="x", tags=list(tags))

    def test_set_tags_normalizes_hashes_and_duplicates(self):
        task = self.task(["old"])
        ops.set_tags(task, ["#api", "backend", "api", "  ", "#api"])
        self.assertEqual(task.tags, ["api", "backend"])

    def test_set_tags_clears_with_empty_list(self):
        task = self.task(["api"])
        ops.set_tags(task, [])
        self.assertEqual(task.tags, [])

    def test_set_tags_rejects_unwritable_tags(self):
        for bad in ("a b", "a#b", "タグ\tつき"):
            with self.subTest(tag=bad):
                task = self.task(["keep"])
                with self.assertRaises(util.TodosError):
                    ops.set_tags(task, [bad])
                self.assertEqual(task.tags, ["keep"])

    def test_set_tags_round_trips_through_markdown(self):
        doc = parser.parse_text("## Today\n\n- [ ] タグ入れ替え #before\n", base=TUE)
        task = doc.section("Today").tasks[0]
        ops.set_tags(task, ["after", "後ろ"])
        again = parser.parse_text(render.render_document(doc), base=TUE)
        self.assertEqual(again.all_tasks()[0].tags, ["after", "後ろ"])


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

    def test_rollover_lists_reasons_before_confirming(self):
        (self.dir / "tasks.md").write_text(
            "## Today\n\n- [ ] 期日なしの用事\n"
            "## Tomorrow\n\n## InWeek\n\n## OpenEnded\n\n## ParkingLot\n",
            encoding="utf-8",
        )
        code, out = self.run_cli("rollover", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("期日なし", out)
        self.assertIn("Today -> ParkingLot", out)
        self.assertIn("期日なしの用事", out)
        self.assertIn("配置ルール", out)

    def test_format_moves_aligns_columns(self):
        doc = parser.parse_text("## Today\n\n- [ ] あ\n- [ ] い\n", base=TUE)
        first, second = doc.section("Today").tasks
        lines = cli.format_moves(
            TUE,
            [(first, "Today", "ParkingLot"), (second, "Tomorrow", "Today")],
        )
        heads = [line.index(t.title) for line, t in zip(lines, (first, second))]
        self.assertEqual(len(set(heads)), 1)

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

    def task_with_detail(self):
        doc = parser.parse_text(
            "## Today\n\n"
            "- [ ] 【進行中】残るタイトル\n"
            "  古い詳細\n"
            "  - [ ] 残る子\n",
            base=TUE,
        )
        return doc.section("Today").tasks[0]

    def test_edit_detail_replaces_only_the_detail(self):
        task = self.task_with_detail()
        self.fake_editor("新しい詳細\n2行目\n")
        self.assertTrue(editor.edit_detail(task))
        self.assertEqual(task.detail, ["新しい詳細", "2行目"])
        # タイトル・ステータス・子タスクには触らない
        self.assertEqual(task.title, "残るタイトル")
        self.assertEqual(task.status, model.DOING)
        self.assertEqual([c.title for c in task.children], ["残る子"])

    def test_edit_detail_without_change_returns_false(self):
        task = self.task_with_detail()
        self.fake_editor("古い詳細\n")
        self.assertFalse(editor.edit_detail(task))
        self.assertEqual(task.detail, ["古い詳細"])

    def test_edit_detail_can_clear_the_detail(self):
        task = self.task_with_detail()
        self.fake_editor("\n")
        self.assertTrue(editor.edit_detail(task))
        self.assertEqual(task.detail, [])

    def test_edit_detail_rejects_bullet_lines(self):
        task = self.task_with_detail()
        self.fake_editor("- [ ] 子タスクのつもり\n")
        with self.assertRaises(util.TodosError):
            editor.edit_detail(task)
        self.assertEqual(task.detail, ["古い詳細"])

    def test_edit_detail_round_trips_through_the_file(self):
        task = self.task_with_detail()
        self.fake_editor("書き直した詳細\n")
        editor.edit_detail(task)
        doc = parser.parse_text(
            "## Today\n\n%s\n" % "\n".join(render.render_task(task, 0)), base=TUE
        )
        again = doc.section("Today").tasks[0]
        self.assertEqual(again.detail, ["書き直した詳細"])
        self.assertEqual([c.title for c in again.children], ["残る子"])


class TuiEditTest(unittest.TestCase):
    """curses を起動せず、入力欄の応答だけ差し替えて編集操作を検証する。"""

    def make_app(self, text):
        # 必須セクションが欠けていると解析時に MISSING_SECTION 警告が付くので、
        # 与えられた見出しに足りない分を補ってから解析する。
        body = text
        for name in model.SECTIONS:
            if "## %s" % name not in body:
                body += "\n## %s\n" % name
        doc = parser.parse_text(body, base=TUE)
        app = tui.App.__new__(tui.App)
        app.ctx = type("Ctx", (), {"doc": doc, "base": TUE})()
        app.theme = theme.Theme("mono").setup()
        app.section_idx = 0
        app.task_idx = 0
        app.scroll = 0
        app.focus = "tasks"
        app.filter = None
        app.message = ""
        app.saved = 0
        app.save = lambda: setattr(app, "saved", app.saved + 1)
        app.notify = lambda text, kind="info": setattr(app, "message", text)
        return app, doc

    def answer(self, app, *values):
        """prompt が順に values を返すようにする。"""
        it = iter(values)
        app.prompt = lambda label, initial="": next(it)

    def select_section(self, app, name):
        app.section_idx = [s.name for s in app.sections()].index(name)
        app.task_idx = 0

    # ------------------------------------------------------------ 追加

    def test_add_in_today_offers_the_section_due(self):
        app, doc = self.make_app("## Today\n")
        self.select_section(app, model.TODAY)
        self.answer(app, "資料をまとめる")
        asked = []
        app.ask = lambda question: asked.append(question) or True
        app.add_task()
        task = doc.section(model.TODAY).tasks[0]
        self.assertEqual(task.due, TUE)
        self.assertIn("2026/07/28", asked[0])
        self.assertIn("本日", asked[0])
        self.assertEqual(app.saved, 1)

    def test_add_in_tomorrow_offers_the_date_that_keeps_it_there(self):
        app, doc = self.make_app("## Tomorrow\n")
        self.select_section(app, model.TOMORROW)
        self.answer(app, "明日やる")
        app.ask = lambda question: True
        app.add_task()
        task = doc.section(model.TOMORROW).tasks[0]
        self.assertEqual(task.due, dt.date(2026, 7, 30))

    def test_add_in_in_week_offers_the_friday(self):
        app, doc = self.make_app("## InWeek\n")
        self.select_section(app, model.IN_WEEK)
        self.answer(app, "今週中にやる")
        asked = []
        app.ask = lambda question: asked.append(question) or True
        app.add_task()
        task = doc.section(model.IN_WEEK).tasks[0]
        self.assertEqual(task.due, dt.date(2026, 7, 31))
        self.assertIn("金曜", asked[0])

    def test_add_without_the_due_keeps_the_old_behaviour(self):
        app, doc = self.make_app("## Today\n")
        self.select_section(app, model.TODAY)
        self.answer(app, "期日は要らない")
        app.ask = lambda question: False
        app.add_task()
        task = doc.section(model.PARKING_LOT).tasks[0]
        self.assertIsNone(task.due)

    def test_add_does_not_ask_in_sections_without_a_date(self):
        for name in (model.OPEN_ENDED, model.PARKING_LOT):
            with self.subTest(section=name):
                app, doc = self.make_app("## %s\n" % name)
                self.select_section(app, name)
                self.answer(app, "いつかやる")
                app.ask = lambda question: self.fail("期日を聞かないこと")
                app.add_task()
                self.assertIsNone(doc.all_tasks()[0].due)

    def test_add_does_not_ask_while_searching(self):
        app, doc = self.make_app("## Today\n\n- [ ] 既存（〜2026/07/28）\n")
        self.select_section(app, model.TODAY)
        app.filter = ("keyword", "既存")
        self.answer(app, "検索中に追加")
        app.ask = lambda question: self.fail("検索結果からは聞かないこと")
        app.add_task()
        self.assertIsNone(doc.section(model.PARKING_LOT).tasks[0].due)

    # ------------------------------------------------------------ 並べ替え・複製

    def test_reorder_moves_the_selected_task_and_follows_it(self):
        app, doc = self.make_app("## Today\n\n- [ ] 1件目\n- [ ] 2件目\n")
        app.task_idx = 1
        second = app.current_task()
        app.reorder(-1)
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["2件目", "1件目"])
        self.assertIs(app.current_task(), second)  # カーソルは動かしたタスクに残る
        self.assertEqual(app.saved, 1)

    def test_reorder_at_the_end_saves_nothing(self):
        app, _ = self.make_app("## Today\n\n- [ ] 1件目\n- [ ] 2件目\n")
        app.reorder(-1)
        self.assertEqual(app.saved, 0)
        self.assertIn("動かせません", app.message)

    def test_reorder_is_refused_while_searching(self):
        app, doc = self.make_app("## Today\n\n- [ ] 1件目\n- [ ] 2件目\n")
        app.filter = ("keyword", "件目")
        app.task_idx = 1
        app.reorder(-1)
        self.assertEqual([t.title for t in doc.section(model.TODAY).tasks],
                         ["1件目", "2件目"])
        self.assertEqual(app.saved, 0)

    def test_duplicate_selects_the_copy(self):
        app, doc = self.make_app("## Today\n\n- [ ] ひな形（〜2026/07/28）\n  詳細文\n")
        original = app.current_task()
        app.duplicate_task()
        tasks = doc.section(model.TODAY).tasks
        self.assertEqual([t.title for t in tasks], ["ひな形", "ひな形"])
        self.assertIs(app.current_task(), tasks[1])
        self.assertIsNot(app.current_task(), original)
        self.assertEqual(tasks[1].due, dt.date(2026, 7, 28))
        self.assertEqual(tasks[1].detail, [])
        self.assertEqual(app.saved, 1)

    # ------------------------------------------------------------ 期日

    def test_edit_due_sets_and_follows_the_rule(self):
        app, doc = self.make_app("## ParkingLot\n\n- [ ] 期日を付ける\n")
        app.section_idx = [s.name for s in app.sections()].index(model.PARKING_LOT)
        task = doc.section(model.PARKING_LOT).tasks[0]
        self.answer(app, "7/29")
        app.edit_due()
        self.assertEqual(task.due, dt.date(2026, 7, 29))
        # 期日を入れたらルールどおり Today へ移す（放置すると再配置を提案される）
        self.assertEqual(task.section, model.TODAY)
        self.assertIn("Today", app.message)
        self.assertEqual(app.saved, 1)

    def test_edit_due_clears_on_empty_input(self):
        app, doc = self.make_app("## Today\n\n- [ ] 【進行中】消す（〜2026/07/28）\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, "")
        app.edit_due()
        self.assertIsNone(task.due)
        # 期日なしの進行中は OpenEnded
        self.assertEqual(task.section, model.OPEN_ENDED)

    def test_edit_due_rejects_garbage_and_keeps_the_old_value(self):
        app, doc = self.make_app("## Today\n\n- [ ] そのまま（〜2026/07/28）\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, "きのう")
        with self.assertRaises(util.TodosError):
            app.edit_due()
        self.assertEqual(task.due, dt.date(2026, 7, 28))
        self.assertEqual(app.saved, 0)

    def test_edit_due_cancelled_changes_nothing(self):
        app, doc = self.make_app("## Today\n\n- [ ] そのまま（〜2026/07/28）\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, None)
        app.edit_due()
        self.assertEqual(task.due, dt.date(2026, 7, 28))
        self.assertEqual(app.saved, 0)

    def test_edit_due_does_not_move_child_tasks(self):
        app, doc = self.make_app("## ParkingLot\n\n- [ ] 親\n  - [ ] 子\n")
        app.section_idx = [s.name for s in app.sections()].index(model.PARKING_LOT)
        app.task_idx = 1
        child = doc.section(model.PARKING_LOT).tasks[0].children[0]
        self.assertIs(app.current_task(), child)
        self.answer(app, "7/29")
        app.edit_due()
        self.assertEqual(child.due, dt.date(2026, 7, 29))
        self.assertEqual(child.section, model.PARKING_LOT)

    # ------------------------------------------------------------ タグ

    def test_edit_tags_replaces_the_whole_set(self):
        app, doc = self.make_app("## Today\n\n- [ ] タグつき #work #urgent\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, "api #backend api")
        app.edit_tags()
        self.assertEqual(task.tags, ["api", "backend"])  # # は任意、重複は除く

    def test_edit_tags_clears_on_empty_input(self):
        app, doc = self.make_app("## Today\n\n- [ ] タグつき #work\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, "")
        app.edit_tags()
        self.assertEqual(task.tags, [])

    def test_edit_tags_survives_a_round_trip(self):
        app, doc = self.make_app("## Today\n\n- [ ] タグつき #work\n")
        task = doc.section(model.TODAY).tasks[0]
        self.answer(app, "api backend")
        app.edit_tags()
        again = parser.parse_text(render.render_document(doc), base=TUE)
        self.assertEqual(again.all_tasks()[0].tags, ["api", "backend"])

    # ------------------------------------------------------------ 子タスク

    def test_add_child_attaches_to_the_selected_task(self):
        app, doc = self.make_app("## Today\n\n- [ ] 親タスク\n")
        parent = doc.section(model.TODAY).tasks[0]
        self.answer(app, "子タスク")
        app.add_child()
        self.assertEqual([c.title for c in parent.children], ["子タスク"])
        self.assertIs(parent.children[0].parent, parent)
        self.assertEqual(parent.children[0].section, model.TODAY)
        # トップレベルは増えない
        self.assertEqual(len(doc.section(model.TODAY).tasks), 1)

    def test_add_child_survives_a_round_trip(self):
        app, doc = self.make_app("## Today\n\n- [ ] 親タスク\n")
        self.answer(app, "子タスク")
        app.add_child()
        again = parser.parse_text(render.render_document(doc), base=TUE)
        top = again.section(model.TODAY).tasks[0]
        self.assertEqual([c.title for c in top.children], ["子タスク"])

    # ------------------------------------------------------------ 相手

    def test_counterpart_can_be_cleared_from_the_status_flow(self):
        app, doc = self.make_app("## Today\n\n- [ ] 【要返答（山田）】確認\n")
        task = doc.section(model.TODAY).tasks[0]
        app.choose = lambda *a, **k: list(model.STATUSES).index(model.NEEDS_REPLY)
        self.answer(app, "")
        app.change_status()
        self.assertEqual(task.status, model.NEEDS_REPLY)
        self.assertIsNone(task.counterpart)
        self.assertEqual(task.status_label(), model.NEEDS_REPLY)

    def test_counterpart_can_be_replaced(self):
        app, doc = self.make_app("## Today\n\n- [ ] 【要返答（山田）】確認\n")
        task = doc.section(model.TODAY).tasks[0]
        app.choose = lambda *a, **k: list(model.STATUSES).index(model.WAITING)
        self.answer(app, "田中")
        app.change_status()
        self.assertEqual(task.counterpart, "田中")

    # ------------------------------------------------------------ 警告の修正

    def test_show_warnings_applies_fixes_on_yes(self):
        app, doc = self.make_app(
            "## Today\n\n- [x] 【未着手】ずれている（〜2026/07/28）\n"
        )
        asked = {}
        app.popup_ask = lambda title, lines, hint: asked.setdefault("hint", hint) or True
        app.popup = lambda *a, **k: self.fail("修正できるなら確認モーダルを出すこと")
        app.show_warnings()
        self.assertEqual(doc.all_tasks()[0].status, model.DONE)
        self.assertIn("1 件を修正", asked["hint"])
        self.assertEqual(app.saved, 1)
        self.assertEqual(validate.validate(doc), [])

    def test_show_warnings_keeps_the_file_on_no(self):
        app, doc = self.make_app(
            "## Today\n\n- [x] 【未着手】ずれている（〜2026/07/28）\n"
        )
        app.popup_ask = lambda title, lines, hint: False
        app.show_warnings()
        self.assertEqual(doc.all_tasks()[0].status, model.TODO)
        self.assertEqual(app.saved, 0)

    def test_show_warnings_only_displays_when_nothing_is_fixable(self):
        app, doc = self.make_app(
            "## Today\n\n- [ ] 【完了】親（〜2026/07/28）\n  - [ ] 未完了の子\n"
        )
        # 完了とチェックボックスのずれは直せるので、直せない問題だけを残す
        doc.all_tasks()[0].checked = True
        shown = {}
        app.popup = lambda title, lines: shown.setdefault("title", title)
        app.popup_ask = lambda *a, **k: self.fail("直せない問題で確認を求めないこと")
        app.show_warnings()
        self.assertIn("警告", shown["title"])


class TuiPromptTest(unittest.TestCase):
    """入力欄の行編集。curses は起動せず、キー入力だけを流す。"""

    def make_app(self, keys):
        app = tui.App.__new__(tui.App)
        app.theme = theme.Theme("mono").setup()
        app.geometry = lambda: (24, 40, 10, 26)
        it = iter(keys)
        app.screen = type("S", (), {
            "get_wch": staticmethod(lambda: next(it)),
            "move": staticmethod(lambda y, x: None),
            "refresh": staticmethod(lambda: None),
        })()
        app._draw_spans = lambda *a, **k: None
        # curses を初期化していないので、カーソルの表示切り替えだけ黙らせる
        original = tui.curses.curs_set
        tui.curses.curs_set = lambda n: None
        self.addCleanup(setattr, tui.curses, "curs_set", original)
        return app

    def type(self, initial, keys):
        keys = list(keys) + ["\n"]
        return self.make_app(keys).prompt("追加", initial)

    def test_left_and_right_move_the_cursor(self):
        # 「ABC」の B の前へ戻って X を入れる
        keys = [curses.KEY_LEFT, curses.KEY_LEFT, "X", curses.KEY_RIGHT, "Y"]
        self.assertEqual(self.type("ABC", keys), "AXBYC")

    def test_backspace_deletes_before_the_cursor(self):
        self.assertEqual(self.type("ABC", [curses.KEY_LEFT, "\x7f"]), "AC")

    def test_delete_removes_the_character_under_the_cursor(self):
        keys = [curses.KEY_HOME, curses.KEY_DC]
        self.assertEqual(self.type("ABC", keys), "BC")

    def test_home_and_end_jump_to_both_ends(self):
        keys = [curses.KEY_HOME, "1", curses.KEY_END, "9"]
        self.assertEqual(self.type("ABC", keys), "1ABC9")

    def test_control_keys_cut_around_the_cursor(self):
        self.assertEqual(self.type("ABCDE", [curses.KEY_LEFT, "\x15"]), "E")
        self.assertEqual(self.type("ABCDE", [curses.KEY_LEFT, "\x0b"]), "ABCD")
        self.assertEqual(self.type("会議の 準備 メモ", ["\x17"]), "会議の 準備")

    def test_editing_works_on_full_width_text(self):
        keys = [curses.KEY_LEFT, "の"]
        self.assertEqual(self.type("会議準備", keys), "会議準の備")

    def test_escape_cancels_even_after_editing(self):
        app = self.make_app([curses.KEY_HOME, "X", "\x1b"])
        self.assertIsNone(app.prompt("追加", "ABC"))

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(self.type("ABC", [curses.KEY_F1, "D"]), "ABCD")

    def test_view_follows_the_cursor_in_a_narrow_field(self):
        buf = list("あいうえおかきくけこ")  # 表示幅 20
        # 幅 10 の欄では左が隠れる。末尾のカーソルにも 1 桁要るので 4 文字分だけ残す
        self.assertEqual(tui._input_view(buf, len(buf), 0, 10), 6)
        # 行頭へ戻せば左端から見せる
        self.assertEqual(tui._input_view(buf, 0, 5, 10), 0)
        # 全部収まるなら送らない
        self.assertEqual(tui._input_view(buf, len(buf), 3, 40), 0)


class TuiNavigationTest(unittest.TestCase):
    """curses を起動せずに TUI の移動ロジックだけを検証する。"""

    def make_app(self, doc):
        app = tui.App.__new__(tui.App)
        app.ctx = type("Ctx", (), {"doc": doc})()
        app.section_idx = 0
        app.task_idx = 0
        app.scroll = 0
        app.focus = "tasks"
        app.filter = None
        app.message = ""
        return app

    def doc_with_task_in(self, section_name):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        ops.add(doc, "あとで読む", TUE, section=section_name)
        return doc

    def test_startup_selects_first_non_empty_section(self):
        app = self.make_app(self.doc_with_task_in(model.PARKING_LOT))
        app.focus_first_non_empty()
        self.assertEqual(app.current_section().name, model.PARKING_LOT)
        self.assertEqual(len(app.rows()), 1)

    def test_startup_keeps_today_when_it_has_tasks(self):
        doc = self.doc_with_task_in(model.PARKING_LOT)
        ops.add(doc, "今日のタスク", TUE, section=model.TODAY)
        app = self.make_app(doc)
        app.focus_first_non_empty()
        self.assertEqual(app.current_section().name, model.TODAY)

    def test_startup_on_empty_file_stays_on_today(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        app = self.make_app(doc)
        app.focus_first_non_empty()
        self.assertEqual(app.current_section().name, model.TODAY)

    def test_j_stays_in_the_section_when_it_is_empty(self):
        app = self.make_app(self.doc_with_task_in(model.PARKING_LOT))
        self.assertEqual(app.current_section().name, model.TODAY)
        for _ in range(4):
            app.move_cursor(1, len(app.rows()))
        # j/k はセクションをまたがない（移動は J/K か Tab）
        self.assertEqual(app.current_section().name, model.TODAY)

    def test_j_moves_task_when_section_has_tasks(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        ops.add(doc, "1件目", TUE, section=model.TODAY)
        ops.add(doc, "2件目", TUE, section=model.TODAY)
        app = self.make_app(doc)
        app.move_cursor(1, len(app.rows()))
        self.assertEqual(app.current_section().name, model.TODAY)
        self.assertEqual(app.current_task().title, "2件目")

    def test_j_loops_inside_the_section(self):
        doc = parser.parse_text("", base=TUE)
        doc.ensure_sections()
        ops.add(doc, "1件目", TUE, section=model.TODAY)
        ops.add(doc, "2件目", TUE, section=model.TODAY)
        app = self.make_app(doc)
        for _ in range(2):  # 末尾のさらに下は先頭へ戻る
            app.move_cursor(1, len(app.rows()))
        self.assertEqual(app.current_section().name, model.TODAY)
        self.assertEqual(app.current_task().title, "1件目")
        app.move_cursor(-1, len(app.rows()))  # 先頭のさらに上は末尾へ
        self.assertEqual(app.current_task().title, "2件目")

    def test_section_move_wraps_around(self):
        app = self.make_app(self.doc_with_task_in(model.PARKING_LOT))
        last = len(app.sections()) - 1
        app.change_section(-1)
        self.assertEqual(app.section_idx, last)
        app.change_section(1)
        self.assertEqual(app.section_idx, 0)

    def test_pane_switching_is_tab_only(self):
        app = self.make_app(self.doc_with_task_in(model.TODAY))
        app.quit = False
        for key in ("h", "l"):  # 昔の割り当ては効かない
            app.handle(key)
            self.assertEqual(app.focus, "tasks")
        app.handle("\t")
        self.assertEqual(app.focus, "sections")
        app.handle("\t")
        self.assertEqual(app.focus, "tasks")

    def test_enter_in_the_sections_pane_moves_to_the_tasks_pane(self):
        app = self.make_app(self.doc_with_task_in(model.TODAY))
        app.quit = False
        app.focus = "sections"
        app.handle("\n")  # 詳細モーダルではなく Tasks ペインへ
        self.assertEqual(app.focus, "tasks")

    def test_empty_search_result_does_not_move_section(self):
        app = self.make_app(self.doc_with_task_in(model.PARKING_LOT))
        app.filter = ("keyword", "該当しない語")
        self.assertEqual(app.rows(), [])
        app.move_cursor(1, 0)
        self.assertEqual(app.current_section().name, model.TODAY)
        self.assertIsNotNone(app.filter)

    def test_reopening_shows_tasks_added_in_a_previous_session(self):
        """TUI で追加 → 終了 → 再起動、でタスクが見えること。

        「再起動すると見えないが、何か追加すると以前の分も見える」という
        報告の再現。原因は読み込みではなくカーソル位置だった。
        """
        with tempfile.TemporaryDirectory() as tmp:
            st = store.Store(Path(tmp))
            st.init()

            # セッション1: タスクを追加して保存する
            doc = st.load(TUE)
            ops.add(doc, "買い物", TUE)
            st.save(doc)

            # セッション2: 読み直した時点でモデルに載っていること
            reloaded = st.load(TUE)
            self.assertEqual([t.title for t in reloaded.all_tasks()], ["買い物"])
            self.assertEqual(
                [t.title for t in reloaded.section(model.PARKING_LOT).tasks], ["買い物"]
            )

            # 起動直後の画面にそのタスクが出ること
            app = self.make_app(reloaded)
            app.focus_first_non_empty()
            self.assertEqual([t.title for t, _ in app.rows()], ["買い物"])

    def test_help_line_always_keeps_help_and_quit(self):
        for width in (20, 30, 46, 60, 78, 120, 200):
            with self.subTest(width=width):
                line = tui.help_line(width)
                self.assertLessEqual(util.display_width(line), width)
                self.assertIn("?:ヘルプ", line)
                self.assertIn("q:終了", line)

    def test_help_line_shows_section_keys_at_usual_widths(self):
        for width in (46, 80, 120):
            with self.subTest(width=width):
                self.assertIn("J/K:セクション", tui.help_line(width))


class TuiRenderTest(unittest.TestCase):
    """curses を起動せずに、行の組み立てと配色の割り当てを検証する。"""

    def make_app(self, doc, palette="mono"):
        app = tui.App.__new__(tui.App)
        app.ctx = type("Ctx", (), {"doc": doc, "base": TUE})()
        app.theme = theme.Theme(palette).setup()
        app.section_idx = 0
        app.task_idx = 0
        app.focus = "tasks"
        app.filter = None
        return app

    def parse_one(self, line):
        doc = parser.parse_text("## Today\n\n%s\n" % line, base=TUE)
        return doc, doc.section("Today").tasks[0]

    def test_task_row_keeps_title_status_due_and_tags(self):
        doc, task = self.parse_one("- [ ] 【進行中】API を実装（〜2026/07/29） #api")
        app = self.make_app(doc)
        spans = app._task_spans(task, 0, selected=False)
        text = "".join(t for t, _ in spans)
        self.assertIn("進行中", text)
        self.assertIn("API を実装", text)
        self.assertIn("2026/07/29", text)
        self.assertIn("#api", text)
        roles = [role for _, role in spans]
        self.assertIn("status.doing", roles)
        self.assertIn("tag", roles)

    def test_due_role_reflects_urgency(self):
        doc, _ = self.parse_one("- [ ] 何か")
        app = self.make_app(doc)
        self.assertEqual(app._due_role(TUE - dt.timedelta(days=1)), "due.over")
        self.assertEqual(app._due_role(TUE), "due.today")
        self.assertEqual(app._due_role(TUE + dt.timedelta(days=2)), "due.soon")
        self.assertEqual(app._due_role(TUE + dt.timedelta(days=30)), "due")

    def test_ascii_theme_falls_back_to_bracket_status(self):
        doc, task = self.parse_one("- [ ] 【完了】終わった")
        app = self.make_app(doc)
        app.theme = theme.Theme("mono", glyphs=False).setup()
        text = "".join(t for t, _ in app._task_spans(task, 0, selected=False))
        self.assertIn("【完了】", text)

    def test_cell_width_is_stable_for_ambiguous_glyphs(self):
        old = os.environ.get("TODOS_AMBIGUOUS_WIDTH")
        try:
            for setting in ("1", "2"):
                os.environ["TODOS_AMBIGUOUS_WIDTH"] = setting
                with self.subTest(ambiguous=setting):
                    self.assertEqual(util.display_width(tui._cell("▍", 2)), 2)
                    self.assertEqual(util.display_width(tui._cell("●", 2)), 2)
                    self.assertEqual(util.display_width(tui._cell("あ", 2)), 2)
        finally:
            if old is None:
                os.environ.pop("TODOS_AMBIGUOUS_WIDTH", None)
            else:
                os.environ["TODOS_AMBIGUOUS_WIDTH"] = old

    def stub_keys(self, app, keys):
        """get_wch が順に keys を返す画面を差し込む。"""
        it = iter(keys)
        app.screen = type("S", (), {"get_wch": staticmethod(lambda: next(it))})()

    def test_popup_ask_accepts_only_y(self):
        app = self.make_app(parser.parse_text("## Today\n", base=TUE))
        app.geometry = lambda: (20, 80, 18, 58)
        app._popup_draw = lambda *a, **k: None
        for keys, expected in ([["y"], True], [["Y"], True], [["\n"], False],
                               [["\x1b"], False], [["n"], False]):
            with self.subTest(keys=keys):
                self.stub_keys(app, keys)
                self.assertEqual(app.popup_ask("t", [["x"]], "hint"), expected)

    def stub_popup_draw(self, app, heads):
        """描画の代わりに、いま先頭に見えている行を記録する。"""
        app._popup_draw = lambda title, lines, offset=0, selected=None, hint=None: \
            heads.append(lines[offset][0][0])

    def test_popup_ask_scrolls_without_closing(self):
        app = self.make_app(parser.parse_text("## Today\n", base=TUE))
        app.geometry = lambda: (20, 80, 18, 58)
        heads = []
        self.stub_popup_draw(app, heads)
        self.stub_keys(app, ["j", "j", "k", "y"])
        lines = [[("行%d" % i, None)] for i in range(30)]
        self.assertTrue(app.popup_ask("t", lines, "hint"))
        self.assertEqual(heads, ["行0", "行1", "行2", "行1"])

    def test_popup_scrolls_without_closing_at_either_end(self):
        """詳細を読み進めた勢いでモーダルが閉じないこと。"""
        app = self.make_app(parser.parse_text("## Today\n", base=TUE))
        app.geometry = lambda: (20, 80, 18, 58)  # 本文は 12 行ぶん見える
        heads = []
        self.stub_popup_draw(app, heads)
        lines = [[("行%d" % i, None)] for i in range(14)]
        # 末尾まで送っても、先頭で戻しても閉じない。閉じるのは他のキー。
        self.stub_keys(app, ["j", "j", "j", "k", "k", "k", "\x1b"])
        app.popup("t", lines)
        self.assertEqual(heads, ["行0", "行1", "行2", "行2", "行1", "行0", "行0"])

    def test_popup_keeps_its_width_while_scrolling(self):
        """スクロールで箱の幅が変わらないこと（幅は全行から決める）。"""
        app = self.make_app(parser.parse_text("## Today\n", base=TUE))
        app.geometry = lambda: (20, 80, 18, 58)
        app.screen = type("S", (), {"refresh": staticmethod(lambda: None)})()
        app._draw_spans = lambda *a, **k: None
        drawn = []
        app._addstr = lambda y, x, text, attr=0: drawn.append(text)
        lines = [[("短い", None)]] + [[("あ" * 20, None)] for _ in range(20)]
        widths = []
        for offset in (0, 1, 5):
            drawn.clear()
            app._popup_draw("t", lines, offset=offset, hint="hint")
            widths.append(util.display_width(drawn[0]))  # 最初に描くのは上枠
        self.assertEqual(len(set(widths)), 1)

    def test_popup_highlights_the_selected_row_after_scrolling(self):
        """窓をずらしても、帯が付くのは選択中の行だけであること。"""
        app = self.make_app(parser.parse_text("## Today\n", base=TUE))
        app.geometry = lambda: (13, 60, 18, 38)
        app.screen = type("S", (), {"refresh": staticmethod(lambda: None)})()
        app._addstr = lambda y, x, text, attr=0: None
        rows = []
        app._draw_spans = lambda y, x, spans, w, fill_role=None, base=None: rows.append(
            ("".join(t for t, _ in spans), base is not None))
        lines = [[("項目%d" % i, None)] for i in range(7)]
        app._popup_draw("t", lines, offset=2, selected=6, hint="hint")
        banded = [text for text, highlighted in rows if highlighted]
        self.assertEqual(banded, [" t", "項目6"])  # タイトル帯と選択行だけ

    def test_move_table_shows_reason_and_destination(self):
        doc = parser.parse_text(
            "## Today\n\n- [ ] 期日なしの用事\n", base=TUE
        )
        app = self.make_app(doc)
        app.geometry = lambda: (24, 100, 18, 78)
        task = doc.section("Today").tasks[0]
        text = "\n".join("".join(t for t, _ in spans)
                         for spans in app._move_table([(task, "Today", "ParkingLot")]))
        self.assertIn("期日なし", text)
        self.assertIn("Today", text)
        self.assertIn("ParkingLot", text)
        self.assertIn("期日なしの用事", text)

    def test_move_table_keeps_titles_on_narrow_terminals(self):
        doc = parser.parse_text(
            "## Today\n\n- [ ] とても長いタスク名をつけた予定表の項目\n", base=TUE
        )
        app = self.make_app(doc)
        app.geometry = lambda: (24, 40, 10, 26)
        task = doc.section("Today").tasks[0]
        rows = ["".join(t for t, _ in spans)
                for spans in app._move_table([(task, "Today", "ParkingLot")])]
        # 狭い端末では2行に分け、タイトルを切り詰めない
        self.assertEqual(len(rows), 2)
        self.assertIn("とても長いタスク名をつけた予定表の項目", rows[0])
        self.assertIn("ParkingLot", rows[1])

    def test_detail_marker_marks_only_tasks_with_detail(self):
        doc = parser.parse_text(
            "## Today\n\n- [ ] 詳細つき\n  説明\n- [ ] 詳細なし\n", base=TUE
        )
        app = self.make_app(doc)
        having, lacking = doc.section("Today").tasks
        mark = app.theme.glyph("detail")
        self.assertIn(mark, "".join(t for t, _ in app._task_spans(having, 0, False)))
        self.assertNotIn(mark, "".join(t for t, _ in app._task_spans(lacking, 0, False)))

    def detail_modal_lines(self, doc, width):
        app = self.make_app(doc)
        app.geometry = lambda: (24, width, 18, width - 22)
        captured = []
        app.popup = lambda title, lines: captured.extend(lines)
        app.show_detail()
        return ["".join(t for t, _ in spans) for spans in captured
                if not isinstance(spans, str)]

    def test_detail_modal_wraps_instead_of_truncating(self):
        body = "あ" * 60
        doc = parser.parse_text("## Today\n\n- [ ] 長い詳細\n  %s\n" % body, base=TUE)
        rows = [line for line in self.detail_modal_lines(doc, 80) if "あ" in line]
        self.assertGreater(len(rows), 1)
        for line in rows:
            self.assertNotIn("…", line)
            self.assertLessEqual(util.display_width(line), 80 - 6)
        self.assertEqual("".join(line.strip() for line in rows), body)

    def test_detail_modal_wraps_on_narrow_terminals(self):
        body = "あ" * 60
        doc = parser.parse_text("## Today\n\n- [ ] 長い詳細\n  %s\n" % body, base=TUE)
        rows = [line for line in self.detail_modal_lines(doc, 40) if "あ" in line]
        for line in rows:
            self.assertLessEqual(util.display_width(line), 40 - 6)
        self.assertEqual("".join(line.strip() for line in rows), body)

    def test_scroll_label(self):
        self.assertEqual(tui._scroll_label(0, 3, 10), "全て")
        self.assertEqual(tui._scroll_label(0, 30, 10), "先頭")
        self.assertEqual(tui._scroll_label(20, 30, 10), "末尾")
        self.assertEqual(tui._scroll_label(10, 30, 10), "50%")


class ThemeTest(unittest.TestCase):
    def with_env(self, **env):
        old = {k: os.environ.get(k) for k in env}
        try:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return theme.detect()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_no_color_disables_colors(self):
        detected = self.with_env(NO_COLOR="1", TODOS_TUI_THEME=None)
        self.assertEqual(detected.palette, "mono")

    def test_theme_name_from_env(self):
        self.assertEqual(self.with_env(NO_COLOR=None, TODOS_TUI_THEME="light").palette, "light")
        self.assertEqual(self.with_env(NO_COLOR=None, TODOS_TUI_THEME="なにか").palette, "dark")

    def test_glyphs_can_be_turned_off(self):
        plain = self.with_env(NO_COLOR=None, TODOS_TUI_GLYPHS="0")
        self.assertEqual(plain.status_glyph(model.DONE), "")
        self.assertEqual(plain.glyph("vline"), "|")

    def test_mono_setup_uses_attributes_instead_of_colors(self):
        import curses

        mono = theme.Theme("mono").setup()
        self.assertEqual(mono.attr("cursor") & curses.A_REVERSE, curses.A_REVERSE)
        self.assertEqual(mono.attr("title"), 0)

    def test_every_role_has_a_mono_attribute(self):
        mono = theme.Theme("mono").setup()
        for role in theme.ROLES:
            with self.subTest(role=role):
                self.assertIsInstance(mono.attr(role), int)


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

    def test_wrap_keeps_every_line_within_width(self):
        text = "あ" * 30
        lines = util.wrap(text, 20)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(util.display_width(line), 20)
        self.assertEqual("".join(lines), text)

    def test_wrap_prefers_spaces_when_present(self):
        self.assertEqual(
            util.wrap("alpha bravo charlie delta", 12), ["alpha bravo", "charlie", "delta"]
        )

    def test_wrap_does_not_emit_blank_lines_for_indented_text(self):
        lines = util.wrap("    " + "あ" * 30, 20)
        self.assertTrue(all(line.strip() for line in lines))
        self.assertTrue(lines[0].startswith("    "))

    def test_wrap_passes_short_and_empty_text_through(self):
        self.assertEqual(util.wrap("短い", 20), ["短い"])
        self.assertEqual(util.wrap("", 20), [""])
        self.assertEqual(util.wrap("幅ゼロ", 0), ["幅ゼロ"])

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
