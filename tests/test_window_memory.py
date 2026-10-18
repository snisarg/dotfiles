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


def legacy_record(value, display, *, mode="fixed", screen_x=False):
    frame, bounds = value["frame"], display["frame"]
    record = {
        "identity": [value["app"], value["pid"]], "display": display,
        "last_frame": frame, "tracking_mode": "geometry",
        "horizontal_position": "screen" if screen_x or mode == "relative" else "available-space",
        "proportions": {
            "x": ((frame["x"] - bounds["x"]) / bounds["w"] if screen_x or mode == "relative"
                  else memory.horizontal_alignment(frame, bounds)),
            "y": (frame["y"] - bounds["y"]) / bounds["h"],
            "w": frame["w"] / bounds["w"] if mode == "relative" else frame["w"],
            "h": frame["h"] / bounds["h"],
        },
    }
    if mode == "fixed" and screen_x:
        record.pop("horizontal_position")  # Before available-space normalization.
    return record


class GeometryTests(unittest.TestCase):
    def test_each_fixed_width_app_has_only_horizontal_preferences(self):
        for app in ("iTerm2", "ChatGPT", "Google Chrome"):
            saved = memory.new_record(window(app), BIG)["width_preferences"]
            self.assertEqual(
                memory.target_width(saved, SMALL["frame"], memory.WIDTH_MODES[app]),
                {"x": -943, "w": 600},
            )
            self.assertEqual(set(saved), {"x", "w"})

    def test_relative_width_mode_scales_width_as_well(self):
        saved = memory.width_preferences(window()["frame"], BIG["frame"], "relative")
        self.assertEqual(
            memory.target_width(saved, SMALL["frame"], "relative"),
            {"x": -900, "w": 300},
        )

    def test_full_height_stays_at_both_usable_edges(self):
        original = window(frame={"x": 0, "y": 20, "w": 600, "h": 1000})
        record = memory.new_record(original, BIG)
        target = dict(original["frame"],
                      **memory.target_width(record["width_preferences"], SMALL["frame"], "fixed"))
        self.assertEqual(memory.full_height_target(record["full_height"], target, SMALL["frame"]),
                         {"x": -1000, "y": 100, "w": 600, "h": 500})

    def test_clamping_preserves_preferences_for_axes_not_manually_changed(self):
        original = window(frame={"x": 1400, "y": 20, "w": 1200, "h": 1000})
        record = memory.new_record(original, BIG)
        small = dict(y=100, h=500,
                     **memory.target_width(record["width_preferences"], SMALL["frame"], "fixed"))
        self.assertEqual(small, {"x": -1000, "y": 100, "w": 1000, "h": 500})
        record.update(display=SMALL, last_frame=small)
        moved = dict(small, y=150)
        updated = memory.updated_record(record, window(frame=moved, display=2), SMALL)
        self.assertEqual(updated["width_preferences"]["w"], 1200)
        self.assertEqual(updated["width_preferences"]["x"], 1)
        self.assertIsNone(updated["full_height"])

    def test_dialogs_and_non_resizable_or_fullscreen_windows_are_excluded(self):
        self.assertFalse(memory.eligible(dict(window(), subrole="AXDialog")))
        self.assertFalse(memory.eligible(dict(window(), **{"is-native-fullscreen": True})))
        self.assertFalse(memory.eligible(dict(window(), **{"can-resize": False})))

    def test_all_pycharm_windows_are_excluded(self):
        for title in ("project – /home/user/project/main.py",
                      "Settings – uber-one-snisarg-harness", "Preferences",
                      "Rename", "Find in Files", "Commit Changes", ""):
            self.assertFalse(memory.eligible(dict(window("PyCharm"), title=title)), title)
        self.assertNotIn("PyCharm", memory.WIDTH_MODES)
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
                saved = memory.width_preferences(original, bounds, "fixed")
                target = memory.target_width(saved, destination["frame"], "fixed")
                expected_x = destination["frame"]["x"] + alignment * (
                    destination["frame"]["w"] - 600)
                self.assertEqual(target["x"], expected_x)
                self.assertEqual(target["w"], 600)

    def test_manual_width_change_updates_alignment_even_when_left_is_unchanged(self):
        record = memory.new_record(window(), BIG)
        adjusted = window(frame={"x": 200, "y": 100, "w": 800, "h": 800})
        result = memory.updated_record(record, adjusted, BIG)
        self.assertAlmostEqual(result["width_preferences"]["x"], 200 / 1200)
        self.assertEqual(
            memory.target_width(result["width_preferences"], SMALL["frame"], "fixed")["x"],
            -967,
        )

    def test_filling_screen_does_not_erase_right_alignment(self):
        original = window(frame={"x": -600, "y": 100, "w": 600, "h": 500}, display=2)
        record = memory.new_record(original, SMALL)
        full_width = window(
            frame={"x": -1000, "y": 100, "w": 1000, "h": 500}, display=2)
        result = memory.updated_record(record, full_width, SMALL)
        self.assertEqual(result["width_preferences"]["x"], 1)
        self.assertEqual(
            memory.target_width(result["width_preferences"], BIG["frame"], "fixed")["x"],
            1000,
        )

    def test_first_full_width_window_has_finite_neutral_alignment(self):
        frame = {"x": -1000, "y": 100, "w": 1000, "h": 500}
        saved = memory.width_preferences(frame, SMALL["frame"], "fixed")
        self.assertEqual(saved["x"], 0.5)
        self.assertEqual(memory.target_width(saved, SMALL["frame"], "fixed"),
                         {"x": -1000, "w": 1000})


class MigrationTests(unittest.TestCase):
    def test_legacy_right_alignment_preserves_width_without_per_app_height_properties(self):
        original = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        record = legacy_record(original, BIG, screen_x=True)
        migrated = memory.migrate_record(record, "fixed")
        self.assertEqual(migrated["width_preferences"]["x"], 1)
        self.assertEqual(migrated["width_preferences"]["w"], 600)
        self.assertEqual(set(migrated["width_preferences"]), {"x", "w"})
        self.assertNotIn("proportions", migrated)
        self.assertIsNone(migrated["full_height"])  # 80% isn't near-full height.
        self.assertEqual(
            memory.target_width(migrated["width_preferences"], BIG["frame"], "fixed"),
            {"x": 1400, "w": 600},
        )
        self.assertEqual(memory.migrate_record(migrated, "fixed"), migrated)

    def test_relative_width_records_are_not_reinterpreted(self):
        record = legacy_record(window(), BIG, mode="relative")
        migrated = memory.migrate_record(record, "relative")
        self.assertEqual(migrated["width_preferences"], {"x": 0.1, "w": 0.3})

    def test_legacy_near_full_height_is_migrated_from_preference_not_clamped_frame(self):
        record = legacy_record(
            window(frame={"x": 200, "y": 40, "w": 600, "h": 960}), BIG)
        record["last_frame"] = dict(record["last_frame"], y=200, h=700)
        migrated = memory.migrate_record(record, "fixed")
        self.assertEqual(migrated["full_height"], {"top": 0.02, "bottom": 0.02})
        self.assertEqual(migrated["width_preferences"], {"x": 200 / 1400, "w": 600})

    def test_existing_global_height_record_preserves_active_or_inactive_intent(self):
        for margins in (None, {"top": 0.02, "bottom": 0.02}):
            record = {
                "identity": ["Safari", 12], "display": BIG,
                "last_frame": window()["frame"], "tracking_mode": "full-height",
                "full_height": margins,
            }
            migrated = memory.migrate_record(record, None)
            self.assertEqual(migrated["full_height"], margins)
            self.assertIsNone(migrated["width_preferences"])


class EventFixture:
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
        self.restore_full = patch.object(memory, "restore_full_height").start()

    def event(self, value):
        self.settle.return_value = value
        memory.process_window(1)


class EventTests(EventFixture, unittest.TestCase):
    def test_first_event_learns_without_moving(self):
        self.event(window())
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["width_preferences"]["w"], 600)

    def test_manual_resize_then_transfer_uses_fresh_geometry_no_feedback_loop(self):
        self.event(window())
        adjusted = window(frame={"x": 400, "y": 220, "w": 750, "h": 600})
        self.event(adjusted)
        moved = window(frame={"x": -950, "y": 100, "w": 750, "h": 600}, display=2)
        expected = {"x": -920, "y": 100, "w": 750, "h": 600}
        self.restore.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore.assert_called_once_with(moved, expected, SMALL["frame"], vertical=False)
        proportions = memory.load_state()["1"]["width_preferences"]
        self.event(dict(moved, frame=expected))
        self.restore.assert_called_once()
        self.assertEqual(memory.load_state()["1"]["width_preferences"], proportions)

    def test_resolution_change_restores_width_but_not_an_ordinary_windows_height(self):
        self.event(window())
        smaller = dict(BIG, frame={"x": 0, "y": 20, "w": 1000, "h": 500})
        self.displays.return_value = {1: smaller}
        expected = {"x": 57, "y": 100, "w": 600, "h": 800}
        self.restore.return_value = window(frame=expected)
        self.event(window())
        self.restore.assert_called_once_with(window(), expected, smaller["frame"], vertical=False)

    def test_pycharm_events_do_not_learn_or_restore_geometry(self):
        self.event(window("PyCharm"))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state(), {})

    def test_reused_id_for_new_process_starts_fresh(self):
        self.event(window())
        self.event(window(pid=99, display=2))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["identity"], ["ChatGPT", 99])

    def test_app_minimum_size_does_not_overwrite_global_height_intent(self):
        original = window(frame={"x": 200, "y": 20, "w": 600, "h": 1000})
        self.event(original)
        moved = window(display=2)
        constrained = {"x": -900, "y": 140, "w": 600, "h": 450}
        self.restore.return_value = dict(moved, frame=constrained)
        self.event(moved)
        self.event(dict(moved, frame=constrained))
        self.assertEqual(memory.load_state()["1"]["full_height"], {"top": 0, "bottom": 0})
        self.event(original)
        self.assertEqual(memory.load_state()["1"]["last_frame"], original["frame"])

    def test_failed_restore_preserves_preferred_geometry_for_retry(self):
        self.event(window())
        before = memory.load_state()
        self.restore.side_effect = subprocess.CalledProcessError(1, "yabai")
        with self.assertRaises(subprocess.CalledProcessError):
            self.event(window(display=2))
        self.assertEqual(memory.load_state(), before)

    def test_migration_is_saved_without_moving_an_unchanged_window(self):
        record = legacy_record(window(), BIG, screen_x=True)
        memory.save_state({"1": record})
        self.event(window())
        self.restore.assert_not_called()
        migrated = memory.load_state()["1"]
        self.assertEqual(migrated["horizontal_position"], "available-space")
        self.assertAlmostEqual(migrated["width_preferences"]["x"], 200 / 1400)

    def test_first_transfer_migrates_using_old_screen_not_new_live_placement(self):
        original = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        record = legacy_record(original, BIG, screen_x=True)
        memory.save_state({"1": record})
        moved = window(frame={"x": -1000, "y": 100, "w": 600, "h": 800}, display=2)
        expected = {"x": -600, "y": 100, "w": 600, "h": 800}
        self.restore.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore.assert_called_once_with(moved, expected, SMALL["frame"], vertical=False)

    def test_changing_a_width_mode_keeps_global_height_intent(self):
        original = window(frame={"x": 400, "y": 20, "w": 800, "h": 1000})
        self.event(original)
        with patch.dict(memory.WIDTH_MODES, ChatGPT="relative"):
            self.event(original)
            record = memory.load_state()["1"]
            self.assertEqual(record["width_preferences"], {"x": 0.2, "w": 0.4})
            self.assertEqual(record["full_height"], {"top": 0, "bottom": 0})
            moved = window(frame={"x": -1000, "y": 200, "w": 800, "h": 300}, display=2)
            expected = {"x": -800, "y": 100, "w": 400, "h": 500}
            self.restore.return_value = dict(moved, frame=expected)
            self.event(moved)
            self.restore.assert_called_once_with(moved, expected, SMALL["frame"], vertical=True)


class FullHeightGeometryTests(unittest.TestCase):
    def test_full_height_and_small_margins_qualify_but_width_is_irrelevant(self):
        for width in (300, 2000):
            frame = {"x": 123, "y": 20, "w": width, "h": 1000}
            self.assertEqual(memory.full_height_margins(frame, BIG["frame"]),
                             {"top": 0, "bottom": 0})
        near_full = {"x": 123, "y": 40, "w": 300, "h": 960}
        margins = memory.full_height_margins(near_full, BIG["frame"])
        self.assertEqual(margins, {"top": 0.02, "bottom": 0.02})
        migrated = {"x": -850, "y": 200, "w": 750, "h": 300}
        self.assertEqual(memory.full_height_target(margins, migrated, SMALL["frame"]),
                         {"x": -850, "y": 110, "w": 750, "h": 480})

    def test_threshold_is_inclusive_and_both_edges_must_be_near_screen(self):
        boundary = {"x": 0, "y": 45, "w": 600, "h": 950}
        self.assertIsNotNone(memory.full_height_margins(boundary, BIG["frame"]))
        for frame in (dict(boundary, h=940),
                      dict(boundary, y=60),  # 95% tall, but top gap is too large.
                      dict(boundary, y=0, h=1020)):  # Oversized/offscreen.
            self.assertIsNone(memory.full_height_margins(frame, BIG["frame"]))

    def test_thresholds_are_configurable(self):
        frame = {"x": 0, "y": 70, "w": 600, "h": 900}
        self.assertIsNone(memory.full_height_margins(frame, BIG["frame"]))
        with patch.dict(memory.FULL_HEIGHT_SETTINGS,
                        minimum_height_fraction=0.90, maximum_edge_gap_fraction=0.06):
            self.assertEqual(memory.full_height_margins(frame, BIG["frame"]),
                             {"top": 0.05, "bottom": 0.05})

    def test_global_switch_and_exclusions_do_not_affect_explicit_geometry_apps(self):
        self.assertTrue(memory.eligible(window("Safari")))
        self.assertFalse(memory.eligible(window("PyCharm")))
        self.assertFalse(memory.eligible(window("jetbrains_client")))
        with patch.dict(memory.FULL_HEIGHT_SETTINGS, enabled=False):
            self.assertFalse(memory.eligible(window("Safari")))
            self.assertTrue(memory.eligible(window("ChatGPT")))
        with patch.dict(memory.FULL_HEIGHT_SETTINGS, excluded_apps=("Safari",)):
            self.assertFalse(memory.eligible(window("Safari")))

    def test_signals_cover_other_apps_and_keep_configured_exclusions(self):
        self.assertEqual(memory.window_signal_filter(), "app!=^(PyCharm|jetbrains_client)$")
        with patch.dict(memory.FULL_HEIGHT_SETTINGS, enabled=False):
            self.assertEqual(memory.window_signal_filter(), "app=" + memory.APP_FILTER)
        with patch.dict(memory.FULL_HEIGHT_SETTINGS, excluded_apps=()):
            self.assertIsNone(memory.window_signal_filter())


class FullHeightEventTests(EventFixture, unittest.TestCase):
    def full_window(self, **kwargs):
        return window("Safari", frame={"x": 400, "y": 20, "w": 800, "h": 1000},
                      **kwargs)

    def test_initial_full_height_is_learned_without_moving(self):
        self.event(self.full_window())
        self.restore.assert_not_called()
        self.restore_full.assert_not_called()
        record = memory.load_state()["1"]
        self.assertEqual(record["tracking_mode"], "full-height")
        self.assertEqual(record["full_height"], {"top": 0, "bottom": 0})
        self.assertNotIn("proportions", record)  # No horizontal size preferences.

    def test_transfer_restores_height_but_keeps_post_migration_width_and_x(self):
        self.event(self.full_window())
        moved = window("Safari", frame={"x": -900, "y": 200, "w": 750, "h": 300},
                       display=2)
        expected = {"x": -900, "y": 100, "w": 750, "h": 500}
        self.restore_full.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore_full.assert_called_once_with(moved, expected, SMALL["frame"])
        self.restore.assert_not_called()
        self.event(dict(moved, frame=expected))  # Own events must not loop.
        self.restore_full.assert_called_once()
        self.assertEqual(memory.load_state()["1"]["full_height"], {"top": 0, "bottom": 0})

    def test_small_top_bottom_margins_survive_round_trip(self):
        original = window("Safari", frame={"x": 400, "y": 40, "w": 800, "h": 960})
        self.event(original)
        moved = window("Safari", frame={"x": -800, "y": 200, "w": 650, "h": 300},
                       display=2)
        expected = {"x": -800, "y": 110, "w": 650, "h": 480}
        self.restore_full.return_value = dict(moved, frame=expected)
        self.event(moved)
        self.restore_full.assert_called_once_with(moved, expected, SMALL["frame"])
        back = window("Safari", frame={"x": 600, "y": 100, "w": 1000, "h": 480})
        expected_back = {"x": 600, "y": 40, "w": 1000, "h": 960}
        self.restore_full.return_value = dict(back, frame=expected_back)
        self.event(back)
        self.assertEqual(self.restore_full.call_args.args,
                         (back, expected_back, BIG["frame"]))
        self.assertEqual(memory.load_state()["1"]["full_height"],
                         {"top": 0.02, "bottom": 0.02})

    def test_same_display_resolution_change_restores_full_height(self):
        self.event(self.full_window())
        smaller = dict(BIG, frame={"x": 0, "y": 30, "w": 1000, "h": 500})
        self.displays.return_value = {1: smaller}
        current = window("Safari", frame={"x": 100, "y": 100, "w": 700, "h": 400})
        expected = {"x": 100, "y": 30, "w": 700, "h": 500}
        self.restore_full.return_value = dict(current, frame=expected)
        self.event(current)
        self.restore_full.assert_called_once_with(current, expected, smaller["frame"])

    def test_manual_shortening_opts_out_even_when_next_screen_clamps_to_full_height(self):
        self.event(self.full_window())
        self.event(window("Safari", frame={"x": 400, "y": 100, "w": 800, "h": 800}))
        self.assertIsNone(memory.load_state()["1"]["full_height"])
        clamped = window("Safari", frame={"x": -1000, "y": 100, "w": 800, "h": 500},
                         display=2)
        self.event(clamped)
        self.event(clamped)  # Focusing this full-height-looking frame isn't intent.
        self.restore_full.assert_not_called()
        self.assertIsNone(memory.load_state()["1"]["full_height"])

    def test_short_window_is_not_promoted_by_os_clamping_during_unplug(self):
        self.event(window("Safari"))  # 80% tall; track only an inactive baseline.
        self.assertIsNone(memory.load_state()["1"]["full_height"])
        self.event(window("Safari", frame={"x": -1000, "y": 100, "w": 600, "h": 500},
                          display=2))
        self.assertIsNone(memory.load_state()["1"]["full_height"])
        self.restore_full.assert_not_called()
        self.restore.assert_not_called()

    def test_manually_making_a_window_full_height_enables_the_rule(self):
        self.event(window("Safari"))
        self.event(self.full_window())
        self.assertEqual(memory.load_state()["1"]["full_height"], {"top": 0, "bottom": 0})

    def test_horizontal_edits_and_app_height_constraints_do_not_erase_intent(self):
        self.event(self.full_window())
        moved = window("Safari", frame={"x": -900, "y": 100, "w": 700, "h": 800},
                       display=2)
        constrained = dict(moved, frame={"x": -900, "y": 100, "w": 700, "h": 650})
        self.restore_full.return_value = constrained
        self.event(moved)
        self.event(dict(constrained, frame=dict(constrained["frame"], x=-850, w=750)))
        self.assertEqual(memory.load_state()["1"]["full_height"], {"top": 0, "bottom": 0})

    def test_failed_vertical_restore_keeps_original_intent_for_retry(self):
        self.event(self.full_window())
        before = memory.load_state()
        self.restore_full.side_effect = subprocess.TimeoutExpired("yabai", 20)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.event(window("Safari", display=2))
        self.assertEqual(memory.load_state(), before)

    def test_selected_apps_only_add_width_preferences_to_the_global_height_policy(self):
        self.event(window("ChatGPT"))
        record = memory.load_state()["1"]
        self.assertEqual(record["tracking_mode"], "width")
        self.assertEqual(set(record["width_preferences"]), {"x", "w"})
        self.assertIsNone(record["full_height"])  # The same 95% rule as Safari.

    def test_manually_shortened_windows_do_not_scale_height_for_any_app(self):
        for app in ("Safari", "ChatGPT", "iTerm2", "Google Chrome"):
            with self.subTest(app=app):
                self.restore.reset_mock()
                self.restore_full.reset_mock()
                self.event(window(app, frame={"x": 400, "y": 20, "w": 800, "h": 1000}))
                self.event(window(app, frame={"x": 400, "y": 100, "w": 800, "h": 800}))
                record = memory.load_state()["1"]
                self.assertIsNone(record["full_height"])
                frame = {"x": -1000, "y": 150, "w": 800, "h": 350}
                if app in memory.WIDTH_MODES:
                    frame.update(memory.target_width(
                        record["width_preferences"], SMALL["frame"], memory.WIDTH_MODES[app]))
                self.event(window(app, frame=frame, display=2))
                self.restore.assert_not_called()
                self.restore_full.assert_not_called()
                self.assertEqual(memory.load_state()["1"]["last_frame"]["h"], 350)

    def test_disabling_global_height_does_not_leave_a_per_app_height_override(self):
        self.event(window(frame={"x": 400, "y": 20, "w": 800, "h": 1000}))
        moved = window(frame={"x": -950, "y": 150, "w": 700, "h": 250}, display=2)
        expected = {"x": -933, "y": 150, "w": 800, "h": 250}
        self.restore.return_value = dict(moved, frame=expected)
        with patch.dict(memory.FULL_HEIGHT_SETTINGS, enabled=False):
            self.event(moved)
        self.restore.assert_called_once_with(moved, expected, SMALL["frame"], vertical=False)


class FullHeightRestoreTests(unittest.TestCase):
    def test_native_commands_change_only_vertical_geometry(self):
        current = window("Safari", frame={"x": -875, "y": 200, "w": 700, "h": 300},
                         display=2)
        target = dict(current["frame"], y=110, h=480)

        def command(*args):
            operation, value = args[-2:]
            if operation == "--move":
                x, y = map(float, value.split(":")[1:])
                current["frame"].update(x=x, y=y)
            else:
                kind, width, height = value.split(":")
                self.assertEqual(kind, "abs")
                self.assertEqual(float(width), current["frame"]["w"])
                current["frame"]["h"] = float(height)

        with patch.object(memory, "run", side_effect=command), \
                patch.object(memory, "query", side_effect=lambda *args: current), \
                patch.object(memory.time, "sleep"):
            result = memory.restore_full_height(current, target, SMALL["frame"])
        self.assertEqual(result["frame"], target)
        self.assertEqual(result["frame"]["w"], 700)
        self.assertEqual(result["frame"]["x"], -875)

    def test_resize_retry_is_safe_when_timed_out_request_already_applied(self):
        current = window("Safari", frame={"x": -875, "y": 200, "w": 700, "h": 300},
                         display=2)
        target = dict(current["frame"], y=110, h=480)
        resize_count = 0

        def output(args, **kwargs):
            nonlocal resize_count
            operation, value = args[-2:]
            first, second = map(float, value.split(":")[1:])
            if operation == "--move":
                current["frame"].update(x=first, y=second)
            else:
                current["frame"].update(w=first, h=second)
                resize_count += 1
                if resize_count == 1:
                    raise subprocess.TimeoutExpired("yabai", 5)
            return ""

        with patch.object(memory.subprocess, "check_output", side_effect=output), \
                patch.object(memory, "query", side_effect=lambda *args: current), \
                patch.object(memory.time, "sleep"):
            result = memory.restore_full_height(current, target, SMALL["frame"])
        self.assertEqual(resize_count, 2)
        self.assertEqual(result["frame"], target)


class SignalRegistrationTests(unittest.TestCase):
    def test_install_registers_broad_or_explicit_filters_from_settings(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory, \
                    patch.object(memory, "CACHE", Path(directory)), \
                    patch.object(memory, "STATE", Path(directory) / "windows.json"), \
                    patch.dict(memory.FULL_HEIGHT_SETTINGS, enabled=enabled), \
                    patch.object(memory, "run") as command, \
                    patch.object(memory.subprocess, "run"), \
                    patch.object(memory, "refresh"):
                memory.install()
                registrations = [c.args for c in command.call_args_list]
                self.assertEqual(len(registrations), 9)
                window_signals = [args for args in registrations
                                  if any(a.startswith("event=window_") for a in args)]
                self.assertEqual(len(window_signals), 5)
                expected = memory.window_signal_filter()
                self.assertTrue(all(expected in args for args in window_signals))


class RefreshTests(EventFixture, unittest.TestCase):
    def test_unchanged_windows_do_not_repeat_per_window_settling_on_rescans(self):
        self.event(window())
        with patch.object(memory, "query", return_value=[window()]), \
                patch.object(memory, "process_window") as process:
            memory.refresh()
            process.assert_not_called()

    def test_active_layouts_are_processed_before_inactive_baselines(self):
        finder = window("Finder", display=2)
        finder["id"] = 3
        safari = window("Safari", frame={"x": 0, "y": 20, "w": 600, "h": 1000}, display=2)
        safari["id"] = 2
        chatgpt = window(display=2)
        memory.save_state({
            str(w["id"]): memory.new_record(w, BIG)
            for w in (finder, safari, chatgpt)
        })
        with patch.object(memory, "query", return_value=[finder, safari, chatgpt]), \
                patch.object(memory, "process_window") as process:
            memory.refresh()
            self.assertEqual([c.args[0] for c in process.call_args_list], [2, 1, 3])


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
        with patch.object(memory, "query",
                          return_value=dict(window("Slack"), **{"can-resize": False})), \
                patch.object(memory.time, "sleep") as sleep:
            self.assertIsNone(memory.wait_for_settle(1))
            sleep.assert_not_called()


class RestoreTests(unittest.TestCase):
    def test_width_only_restore_keeps_height_and_y_adjusted_by_the_app(self):
        current = window(frame={"x": 0, "y": 100, "w": 900, "h": 700})
        target = {"x": 200, "y": 100, "w": 600, "h": 999}
        move_count = 0

        def command(*args):
            nonlocal move_count
            operation, value = args[-2:]
            first, second = map(float, value.split(":")[1:])
            if operation == "--move":
                move_count += 1
                current["frame"].update(x=first, y=second)
                if move_count == 1:
                    current["frame"].update(y=120, h=500)  # App/macOS adjustment.
            else:
                self.assertEqual(second, 500)
                current["frame"].update(w=first, h=second)

        with patch.object(memory, "run", side_effect=command), \
                patch.object(memory, "query", side_effect=lambda *args: current), \
                patch.object(memory.time, "sleep"):
            actual = memory.restore(current, target, BIG["frame"], vertical=False)
        self.assertEqual(actual["frame"], {"x": 200, "y": 120, "w": 600, "h": 500})

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
