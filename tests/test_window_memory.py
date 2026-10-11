"""Behavior tests: no live windows or yabai commands are touched."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".config/yabai/scripts/window-memory.py"
spec = importlib.util.spec_from_file_location("window_memory", SCRIPT)
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def window(app="ChatGPT", frame=None, display=1, pid=12):
    return {
        "id": 1, "pid": pid, "app": app, "display": display,
        "title": app,
        "frame": frame or {"x": 200, "y": 100, "w": 600, "h": 800},
        "subrole": "AXStandardWindow", "can-move": True, "can-resize": True,
        "has-ax-reference": True,
    }


BIG = {"uuid": "big", "frame": {"x": 0, "y": 20, "w": 2000, "h": 1000}}
SMALL = {"uuid": "small", "frame": {"x": -1000, "y": 100, "w": 1000, "h": 500}}


class GeometryTests(unittest.TestCase):
    def test_each_fixed_width_app_preserves_width_scales_height_and_position(self):
        for app in ("iTerm2", "ChatGPT", "Google Chrome"):
            saved = memory.new_record(window(app), BIG)["proportions"]
            self.assertEqual(
                memory.target_frame(saved, SMALL["frame"], memory.MODES[app]),
                {"x": -943, "y": 140, "w": 600, "h": 400},
            )

    def test_relative_width_mode_scales_width_as_well(self):
        saved = memory.proportions(window()["frame"], BIG["frame"], "relative")
        self.assertEqual(
            memory.target_frame(saved, SMALL["frame"], "relative"),
            {"x": -900, "y": 140, "w": 300, "h": 400},
        )

    def test_full_height_stays_at_both_usable_edges(self):
        original = window(frame={"x": 0, "y": 20, "w": 600, "h": 1000})
        saved = memory.new_record(original, BIG)["proportions"]
        self.assertEqual(memory.target_frame(saved, SMALL["frame"], "fixed"),
                         {"x": -1000, "y": 100, "w": 600, "h": 500})

    def test_clamping_preserves_preferences_for_axes_not_manually_changed(self):
        original = window(frame={"x": 1400, "y": 20, "w": 1200, "h": 1000})
        record = memory.new_record(original, BIG)
        small = memory.target_frame(record["proportions"], SMALL["frame"], "fixed")
        self.assertEqual(small, {"x": -1000, "y": 100, "w": 1000, "h": 500})
        record.update(display=SMALL, last_frame=small)
        moved = dict(small, y=150)
        updated = memory.updated_record(record, window(frame=moved, display=2), SMALL)
        self.assertEqual(updated["proportions"]["w"], 1200)
        self.assertEqual(updated["proportions"]["x"], 1)
        self.assertEqual(updated["proportions"]["y"], 0.1)

    def test_dialogs_and_unrelated_apps_are_excluded(self):
        self.assertFalse(memory.eligible(window("Slack")))
        self.assertFalse(memory.eligible(dict(window(), subrole="AXDialog")))
        self.assertFalse(memory.eligible(dict(window(), **{"is-native-fullscreen": True})))
        self.assertFalse(memory.eligible(dict(window(), **{"can-resize": False})))

    def test_all_pycharm_windows_are_excluded(self):
        for title in ("project – /home/user/project/main.py",
                      "Settings – uber-one-snisarg-harness", "Preferences",
                      "Rename", "Find in Files", "Commit Changes", ""):
            self.assertFalse(memory.eligible(dict(window("PyCharm"), title=title)), title)
        self.assertNotIn("PyCharm", memory.MODES)
        self.assertNotIn("PyCharm", memory.APP_FILTER)

    def test_pycharm_routing_rule_is_not_registered_on_startup(self):
        config = (SCRIPT.parents[3] / ".yabairc").read_text()
        self.assertNotIn("app='^PyCharm$'", config)
        self.assertNotIn("yabai -m rule --add label=route-coding", config)

    def test_left_center_right_alignment_survives_both_transfer_directions(self):
        for alignment in (0, 0.5, 1):
            for source, destination in ((BIG, SMALL), (SMALL, BIG)):
                bounds = source["frame"]
                original = {
                    "x": bounds["x"] + alignment * (bounds["w"] - 600),
                    "y": bounds["y"], "w": 600, "h": bounds["h"],
                }
                saved = memory.proportions(original, bounds, "fixed")
                target = memory.target_frame(saved, destination["frame"], "fixed")
                expected_x = destination["frame"]["x"] + alignment * (
                    destination["frame"]["w"] - 600)
                self.assertEqual(target["x"], expected_x)
                self.assertEqual(target["w"], 600)
                self.assertEqual(target["h"], destination["frame"]["h"])

    def test_manual_width_change_updates_alignment_even_when_left_is_unchanged(self):
        record = memory.new_record(window(), BIG)
        adjusted = window(frame={"x": 200, "y": 100, "w": 800, "h": 800})
        result = memory.updated_record(record, adjusted, BIG)
        self.assertAlmostEqual(result["proportions"]["x"], 200 / 1200)
        self.assertEqual(
            memory.target_frame(result["proportions"], SMALL["frame"], "fixed")["x"],
            -967,
        )

    def test_filling_screen_does_not_erase_right_alignment(self):
        original = window(frame={"x": -600, "y": 100, "w": 600, "h": 500}, display=2)
        record = memory.new_record(original, SMALL)
        full_width = window(
            frame={"x": -1000, "y": 100, "w": 1000, "h": 500}, display=2)
        result = memory.updated_record(record, full_width, SMALL)
        self.assertEqual(result["proportions"]["x"], 1)
        self.assertEqual(
            memory.target_frame(result["proportions"], BIG["frame"], "fixed")["x"],
            1000,
        )

    def test_first_full_width_window_has_finite_neutral_alignment(self):
        frame = {"x": -1000, "y": 100, "w": 1000, "h": 500}
        saved = memory.proportions(frame, SMALL["frame"], "fixed")
        self.assertEqual(saved["x"], 0.5)
        self.assertEqual(memory.target_frame(saved, SMALL["frame"], "fixed"), frame)


class MigrationTests(unittest.TestCase):
    def test_legacy_right_alignment_preserves_width_height_and_current_placement(self):
        original = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        record = memory.new_record(original, BIG)
        del record["horizontal_position"]
        record["proportions"]["x"] = 0.7  # Legacy full-screen normalization.
        migrated = memory.migrate_record(record, "fixed")
        self.assertEqual(migrated["proportions"]["x"], 1)
        for axis in ("y", "w", "h"):
            self.assertEqual(migrated["proportions"][axis], record["proportions"][axis])
        self.assertEqual(
            memory.target_frame(migrated["proportions"], BIG["frame"], "fixed"),
            original["frame"],
        )
        self.assertEqual(memory.migrate_record(migrated, "fixed"), migrated)

    def test_relative_width_records_are_not_reinterpreted(self):
        record = {"proportions": {"x": 0.7}}
        self.assertEqual(memory.migrate_record(record, "relative"), record)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        cache = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch.object(memory, "CACHE", cache).start()
        patch.object(memory, "STATE", cache / "windows.json").start()
        self.displays = patch.object(memory, "usable_displays",
                                     return_value={1: BIG, 2: SMALL}).start()
        self.settle = patch.object(memory, "wait_for_settle").start()
        self.restore = patch.object(memory, "restore").start()

    def event(self, value):
        self.settle.return_value = value
        memory.process_window(1)

    def test_first_event_learns_without_moving(self):
        self.event(window())
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["proportions"]["w"], 600)

    def test_manual_resize_then_transfer_uses_fresh_geometry_no_feedback_loop(self):
        self.event(window())
        adjusted = window(frame={"x": 400, "y": 220, "w": 750, "h": 600})
        self.event(adjusted)
        moved = window(frame={"x": -950, "y": 100, "w": 750, "h": 600}, display=2)
        expected = {"x": -920, "y": 200, "w": 750, "h": 300}
        self.restore.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore.assert_called_once_with(moved, expected, SMALL["frame"])
        proportions = memory.load_state()["1"]["proportions"]
        self.event(dict(moved, frame=expected))
        self.restore.assert_called_once()
        self.assertEqual(memory.load_state()["1"]["proportions"], proportions)

    def test_resolution_change_restores_even_on_same_display(self):
        self.event(window())
        smaller = dict(BIG, frame={"x": 0, "y": 20, "w": 1000, "h": 500})
        self.displays.return_value = {1: smaller}
        expected = {"x": 57, "y": 60, "w": 600, "h": 400}
        self.restore.return_value = window(frame=expected)
        self.event(window())
        self.restore.assert_called_once_with(window(), expected, smaller["frame"])

    def test_pycharm_events_do_not_learn_or_restore_geometry(self):
        self.event(window("PyCharm"))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state(), {})

    def test_reused_id_for_new_process_starts_fresh(self):
        self.event(window())
        self.event(window(pid=99, display=2))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["identity"], ["ChatGPT", 99])

    def test_app_minimum_size_does_not_overwrite_saved_height(self):
        self.event(window())
        moved = window(display=2)
        constrained = {"x": -900, "y": 140, "w": 600, "h": 450}
        self.restore.return_value = dict(moved, frame=constrained)
        self.event(moved)
        self.event(dict(moved, frame=constrained))
        self.assertEqual(memory.load_state()["1"]["proportions"]["h"], 0.8)
        self.event(window())
        self.assertEqual(memory.load_state()["1"]["last_frame"], window()["frame"])

    def test_failed_restore_preserves_preferred_geometry_for_retry(self):
        self.event(window())
        before = memory.load_state()
        self.restore.side_effect = subprocess.CalledProcessError(1, "yabai")
        with self.assertRaises(subprocess.CalledProcessError):
            self.event(window(display=2))
        self.assertEqual(memory.load_state(), before)

    def test_migration_is_saved_without_moving_an_unchanged_window(self):
        record = memory.new_record(window(), BIG)
        del record["horizontal_position"]
        record["proportions"]["x"] = 0.1
        memory.save_state({"1": record})
        self.event(window())
        self.restore.assert_not_called()
        migrated = memory.load_state()["1"]
        self.assertEqual(migrated["horizontal_position"], "available-space")
        self.assertAlmostEqual(migrated["proportions"]["x"], 200 / 1400)

    def test_first_transfer_migrates_using_old_screen_not_new_live_placement(self):
        original = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        record = memory.new_record(original, BIG)
        del record["horizontal_position"]
        record["proportions"]["x"] = 0.7
        memory.save_state({"1": record})
        moved = window(frame={"x": -1000, "y": 100, "w": 600, "h": 800}, display=2)
        expected = {"x": -600, "y": 140, "w": 600, "h": 400}
        self.restore.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore.assert_called_once_with(moved, expected, SMALL["frame"])


class SettlingTests(unittest.TestCase):
    def test_waits_until_mouse_released_even_if_frame_is_already_stable(self):
        with patch.object(memory, "query", return_value=window()) as query, \
                patch.object(memory, "screen_info", side_effect=[
                    {"mouse_down": True}, {"mouse_down": True},
                    {"mouse_down": False}, {"mouse_down": False},
                    {"mouse_down": False},
                ]), patch.object(memory.time, "sleep"):
            self.assertEqual(memory.wait_for_settle(1), window())
            self.assertEqual(query.call_count, 6)

    def test_cannot_resize_window_is_not_waited_on(self):
        with patch.object(memory, "query", return_value=window("Slack")), \
                patch.object(memory.time, "sleep") as sleep:
            self.assertIsNone(memory.wait_for_settle(1))
            sleep.assert_not_called()


class RestoreTests(unittest.TestCase):
    def test_move_precedes_growth_so_app_adopts_larger_screen_limits(self):
        current = window(frame={"x": -1000, "y": 100, "w": 600, "h": 500})
        adopted_destination = False

        def command(*args):
            nonlocal adopted_destination
            operation, value = args[-2:]
            a, b = map(float, value.split(":")[1:])
            if operation == "--move":
                current["frame"].update(x=a, y=b)
                adopted_destination = a >= 0
            elif operation == "--resize":
                current["frame"].update(w=a, h=b if adopted_destination else min(b, 500))

        target = {"x": 200, "y": 20, "w": 600, "h": 1000}
        with patch.object(memory, "run", side_effect=command), \
                patch.object(memory, "query", side_effect=lambda *args: current), \
                patch.object(memory.time, "sleep"):
            actual = memory.restore(current, target, BIG["frame"])
            self.assertEqual(actual["frame"], target)

    def test_position_accounts_for_actual_app_minimum_size(self):
        constrained = window(frame={"x": -900, "y": 140, "w": 800, "h": 450}, display=2)
        target = {"x": -400, "y": 300, "w": 300, "h": 200}
        with patch.object(memory, "run") as run, \
                patch.object(memory, "query", return_value=constrained), \
                patch.object(memory.time, "sleep"):
            memory.restore(window(display=2), target, SMALL["frame"])
            self.assertEqual(run.call_args_list[-1].args[-1], "abs:-829:150")

    def test_app_minimum_width_still_preserves_center_alignment(self):
        constrained = window(frame={"x": -900, "y": 100, "w": 800, "h": 400}, display=2)
        target = {"x": -650, "y": 100, "w": 300, "h": 400}
        with patch.object(memory, "run") as run, \
                patch.object(memory, "query", return_value=constrained), \
                patch.object(memory.time, "sleep"):
            memory.restore(window(display=2), target, SMALL["frame"])
            self.assertEqual(run.call_args_list[-1].args[-1], "abs:-900:100")


class MonitorChangeTests(unittest.TestCase):
    def test_yabai_timeout_is_retried_with_longer_deadline(self):
        with patch.object(memory.subprocess, "check_output", side_effect=[
            subprocess.TimeoutExpired("yabai", 5), "{}",
        ]) as output, patch.object(memory.time, "sleep"):
            self.assertEqual(memory.run(memory.YABAI, "-m", "query", "--displays"), "{}")
            self.assertEqual([c.kwargs["timeout"] for c in output.call_args_list], [5, 10])

    def test_exhausted_timeouts_raise_instead_of_becoming_new_geometry(self):
        with patch.object(memory.subprocess, "check_output",
                          side_effect=subprocess.TimeoutExpired("yabai", 20)) as output, \
                patch.object(memory.time, "sleep"):
            with self.assertRaises(subprocess.TimeoutExpired):
                memory.run(memory.YABAI, "-m", "query", "--displays")
            self.assertEqual(output.call_count, 3)

    def test_monitor_change_rechecks_after_early_inventory_timeout(self):
        with patch.object(memory, "refresh", side_effect=[
            subprocess.TimeoutExpired("yabai", 5), None, None,
        ]) as refresh, patch.object(memory.time, "sleep"):
            memory.display_change()
            self.assertEqual(refresh.call_count, 3)

    def test_one_stalled_window_does_not_prevent_other_windows_restoring(self):
        with patch.object(memory, "query", return_value=[window(), dict(window(), id=2)]), \
                patch.object(memory, "process_window", side_effect=[
                    subprocess.TimeoutExpired("yabai", 20), None,
                ]) as process, patch.object(memory.sys, "stderr"), \
                tempfile.TemporaryDirectory() as directory, \
                patch.object(memory, "CACHE", Path(directory)), \
                patch.object(memory, "STATE", Path(directory) / "windows.json"):
            memory.refresh()
            self.assertEqual([c.args[0] for c in process.call_args_list], [1, 2])


if __name__ == "__main__":
    unittest.main()
