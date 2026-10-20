"""Behavior tests: no live windows or yabai commands are touched."""
import copy
import importlib.util
import json
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
        "id": 1, "pid": pid, "app": app, "display": display, "title": app,
        "frame": frame or {"x": 200, "y": 100, "w": 600, "h": 800},
        "subrole": "AXStandardWindow", "can-move": True, "can-resize": True,
        "has-ax-reference": True,
    }


BIG = {"uuid": "big", "frame": {"x": 0, "y": 20, "w": 2000, "h": 1000}}
SMALL = {"uuid": "small", "frame": {"x": -1000, "y": 100, "w": 1000, "h": 500}}


def preference(anchor, offset=0, rule="auto"):
    return {"anchor": anchor, "offset": offset, "rule": rule}


def legacy_record(value, display=BIG, *, mode="fixed", screen_x=False):
    frame, bounds = value["frame"], display["frame"]
    travel = bounds["w"] - frame["w"]
    record = {
        "identity": [value["app"], value["pid"]], "display": copy.deepcopy(display),
        "last_frame": dict(frame), "tracking_mode": "geometry",
        "horizontal_position": "screen" if screen_x or mode == "relative" else "available-space",
        "proportions": {
            "x": ((frame["x"] - bounds["x"]) / bounds["w"] if screen_x or mode == "relative"
                  else max(0, min(1, (frame["x"] - bounds["x"]) / travel)) if travel > 1 else .5),
            "y": (frame["y"] - bounds["y"]) / bounds["h"],
            "w": frame["w"] / bounds["w"] if mode == "relative" else frame["w"],
            "h": frame["h"] / bounds["h"],
        },
    }
    if mode == "fixed" and screen_x:
        record.pop("horizontal_position")
    return record


def previous_record(value, *, full_height=None, width=None, display=BIG):
    return {
        "identity": [value["app"], value["pid"]], "display": copy.deepcopy(display),
        "last_frame": dict(value["frame"]), "tracking_mode": "width" if width else "full-height",
        "horizontal_position": "available-space", "width_preferences": width,
        "full_height": full_height,
    }


class AxisTests(unittest.TestCase):
    def test_horizontal_center_band_is_fifteen_percent_of_screen_width_inclusive(self):
        # Width 600 on a 2000-point screen: center x=700, band is +/-150.
        for x, anchor in ((549, "start"), (550, "center"), (700, "center"),
                          (850, "center"), (851, "end")):
            with self.subTest(x=x):
                frame = {"x": x, "y": 20, "w": 600, "h": 400}
                saved = memory.capture_position("x", frame, BIG["frame"])
                self.assertEqual(saved["anchor"], anchor)
                if anchor == "center":
                    self.assertEqual(saved["offset"], 0)
                    self.assertEqual(memory.target_axis(saved, 600, -1000, 1000), -800)

    def test_center_threshold_is_based_on_screen_not_remaining_travel(self):
        for width in (200, 600, 1600):
            center = (2000 - width) / 2
            for delta, anchor in ((150, "center"), (151, "end"), (-151, "start")):
                saved = memory.capture_position("x", {"x": center + delta, "w": width}, BIG["frame"])
                self.assertEqual(saved["anchor"], anchor)

    def test_center_band_can_be_configured_and_does_not_change_vertical_rules(self):
        frame = {"x": 600, "y": 370, "w": 600, "h": 400}
        self.assertEqual(memory.capture_position("x", frame, BIG["frame"]), preference("center"))
        with patch.object(memory, "HORIZONTAL_CENTER_GAP_FRACTION", .05):
            self.assertEqual(memory.capture_position("x", frame, BIG["frame"]), preference("start", .3))
        self.assertEqual(memory.capture_position("y", frame, BIG["frame"]), preference("end", .25))

    def test_auto_compares_only_edge_gaps_and_ties_choose_end(self):
        # 1600 travel: compare the two gaps directly, with one boundary at 800.
        for start, anchor in ((0, "start"), (399, "start"), (400, "start"),
                              (799.9, "start"), (800, "end"), (800.1, "end"),
                              (1200, "end"), (1201, "end"),
                              (1600, "end")):
            with self.subTest(start=start):
                saved = memory.capture_axis(start, 400, 0, 2000)
                self.assertEqual(saved["anchor"], anchor)
                self.assertEqual(saved["rule"], "auto")
                self.assertEqual(memory.target_axis(saved, 400, 0, 2000), round(start))

    def test_edge_gaps_scale_with_total_extent_on_both_axes(self):
        for origin, extent, size, gap in ((0, 2000, 600, 200), (20, 1000, 300, 100)):
            for anchor in ("start", "end"):
                source = origin + gap if anchor == "start" else origin + extent - size - gap
                saved = memory.capture_axis(source, size, origin, extent)
                self.assertEqual(saved["anchor"], anchor)
                self.assertAlmostEqual(saved["offset"], .1)
                new_origin, new_extent = -300, extent / 2
                expected = (new_origin + gap / 2 if anchor == "start"
                            else new_origin + new_extent - size - gap / 2)
                self.assertEqual(memory.target_axis(saved, size, new_origin, new_extent), expected)

    def test_center_is_supported_only_when_explicitly_requested(self):
        for offset in (-.1, 0, .1):
            saved = memory.capture_axis(800 + offset * 2000, 400, 0, 2000, rule="center")
            self.assertEqual(saved, preference("center", offset, "center"))
            self.assertEqual(memory.target_axis(saved, 400, -1000, 1000), -700 + offset * 1000)

    def test_forced_references_override_classification_and_axis_can_opt_out(self):
        for anchor, offset in (("start", .4), ("center", 0), ("end", .4)):
            self.assertEqual(memory.capture_axis(800, 400, 0, 2000, rule=anchor),
                             preference(anchor, offset, anchor))
        self.assertIsNone(memory.capture_axis(0, 400, 0, 2000, rule="off"))

    def test_offscreen_capture_uses_contained_copy(self):
        for p, anchor in ((-500, "start"), (1900, "end")):
            self.assertEqual(memory.capture_axis(p, 400, 0, 2000), preference(anchor))

    def test_auto_full_axis_ties_choose_end_but_explicit_overrides_remain(self):
        for size in (2000, 2500):
            self.assertEqual(memory.capture_axis(0, size, 0, 2000), preference("end"))
            previous = preference("end", .02)
            self.assertEqual(memory.capture_axis(0, size, 0, 2000, previous), preference("end"))
            self.assertEqual(memory.capture_axis(0, size, 0, 2000, previous, "start"),
                             preference("start" , 0, "start"))
            explicit = preference("center", .02, "center")
            self.assertEqual(memory.capture_axis(0, size, 0, 2000, explicit, "center"), explicit)

    def test_exactly_centered_auto_window_scales_end_gap_not_center_offset(self):
        saved = memory.capture_axis(700, 600, 0, 2000)
        self.assertEqual(saved, preference("end", .35))
        self.assertEqual(memory.target_axis(saved, 600, 0, 1000), 50)

    def test_almost_full_axis_still_uses_strict_smaller_gap(self):
        self.assertEqual(memory.capture_axis(0, 1999, 0, 2000), preference("start"))

    def test_closer_start_edge_wins_even_when_center_is_closer_than_either_edge(self):
        for origin, extent, size, p, expected in (
                (0, 2000, 400, 600, 300),
                (20, 1000, 300, 220, 200)):
            saved = memory.capture_axis(p, size, origin, extent)
            self.assertEqual(saved, preference("start", (p - origin) / extent))
            destination_origin = 0 if origin == 0 else 100
            self.assertEqual(memory.target_axis(saved, size, destination_origin, extent / 2), expected)

    def test_execution_clamps_and_rounds_without_mutating_preference(self):
        for saved, size, expected in ((preference("end", .1), 1200, -1000),
                                      (preference("start", .3333), 100, -667),
                                      (preference("center", -.9), 200, -1000)):
            before = dict(saved)
            self.assertEqual(memory.target_axis(saved, size, -1000, 1000), expected)
            self.assertEqual(saved, before)

    def test_invalid_bounds_are_not_divided_or_relearned(self):
        for extent in (0, -100):
            with self.assertRaises(ValueError):
                memory.capture_axis(0, 100, 0, extent)
            with self.assertRaises(ValueError):
                memory.target_axis(preference("center"), 100, 0, extent)

    def test_deferred_legacy_alignment_uses_available_travel(self):
        for alignment in (0, .2, .5, .8, 1):
            saved = dict(preference("center", None), legacy_alignment=alignment)
            self.assertEqual(memory.target_axis(saved, 600, -1000, 1000),
                             round(-1000 + alignment * 400))


class PolicyTests(unittest.TestCase):
    def test_unlisted_app_defaults_to_both_axes_and_fixed_width(self):
        self.assertEqual(memory.policy("Safari"), memory.DEFAULTS)
        record = memory.new_record(window("Safari"), BIG)
        self.assertIsNotNone(record["position"]["x"])
        self.assertIsNotNone(record["position"]["y"])
        self.assertEqual(record["width_preferences"], {"mode": "fixed", "value": 600})
        self.assertEqual(memory.APP_OVERRIDES, {})

    def test_fixed_width_apps_inherit_default_positioning(self):
        for app in ("iTerm2", "ChatGPT", "Google Chrome", "PyCharm", "jetbrains_client"):
            policy = memory.policy(app)
            self.assertEqual(policy["width_mode"], "fixed")
            self.assertEqual(policy["horizontal_anchor"], "auto")
            self.assertEqual(policy["vertical_anchor"], "auto")
            self.assertEqual(memory.new_record(window(app), BIG)["width_preferences"],
                             {"mode": "fixed", "value": 600})

    def test_overrides_are_sparse_and_do_not_mutate_defaults(self):
        original = dict(memory.DEFAULTS)
        with patch.dict(memory.APP_OVERRIDES, Safari={"vertical_anchor": "off"}):
            policy = memory.policy("Safari")
            self.assertEqual(policy["horizontal_anchor"], "auto")
            self.assertIsNone(memory.new_record(window("Safari"), BIG)["position"]["y"])
        self.assertEqual(memory.DEFAULTS, original)

    def test_pycharm_and_remote_client_track_standard_windows_not_dialogs(self):
        for app in ("PyCharm", "jetbrains_client"):
            for title in ("project – /home/user/main.py", "Settings", "Rename", ""):
                self.assertTrue(memory.eligible(dict(window(app), title=title)))
                self.assertFalse(memory.eligible(dict(window(app), title=title, subrole="AXDialog")))

    def test_dialogs_inaccessible_hidden_minimized_and_fullscreen_are_excluded(self):
        for flag, value in (("subrole", "AXDialog"), ("can-move", False),
                            ("can-resize", False), ("has-ax-reference", False),
                            ("is-native-fullscreen", True), ("has-fullscreen-zoom", True),
                            ("is-minimized", True), ("is-hidden", True)):
            self.assertFalse(memory.eligible(dict(window(), **{flag: value})), flag)

    def test_short_and_tall_windows_share_the_same_policy(self):
        for height in (200, 800, 950, 1000):
            record = memory.new_record(window("Safari", frame={"x": 200, "y": 20, "w": 600, "h": height}), BIG)
            self.assertTrue(all(record["position"].values()))
            self.assertEqual(record["height_fraction"], height / 1000)

    def test_signal_filter_uses_shared_disabled_apps(self):
        self.assertIsNone(memory.window_signal_filter())
        with patch.dict(memory.APP_OVERRIDES, Safari={"enabled": False}):
            self.assertEqual(memory.window_signal_filter(), "app!=^(Safari)$")

    def test_disabled_default_allows_an_explicit_enabled_exception(self):
        with patch.dict(memory.DEFAULTS, enabled=False), \
                patch.dict(memory.APP_OVERRIDES, {"Safari": {"enabled": True}}, clear=True):
            self.assertFalse(memory.eligible(window("Finder")))
            self.assertTrue(memory.eligible(window("Safari")))
            self.assertEqual(memory.window_signal_filter(), "app=^(Safari)$")

    def test_invalid_policy_fails_explicitly_instead_of_ignoring_mistakes(self):
        for override in ({"width_mode": "percent"}, {"horizontal_anchor": "top"},
                         {"vertical_anchor": "left"}, {"unknown": True}):
            with self.subTest(override=override), \
                    patch.dict(memory.APP_OVERRIDES, Safari=override):
                with self.assertRaises(ValueError):
                    memory.policy("Safari")

    def test_user_facing_axis_names_are_canonicalized(self):
        for horizontal, vertical, x_anchor, y_anchor in (
                ("left", "top", "start", "start"), ("right", "bottom", "end", "end"),
                (None, None, None, None)):
            with patch.dict(memory.APP_OVERRIDES, Safari={
                    "horizontal_anchor": horizontal, "vertical_anchor": vertical}):
                record = memory.new_record(window("Safari"), BIG)
            for axis, anchor in (("x", x_anchor), ("y", y_anchor)):
                if anchor is None:
                    self.assertIsNone(record["position"][axis])
                else:
                    self.assertEqual(record["position"][axis]["rule"], anchor)

    def test_pycharm_space_routing_remains_removed(self):
        config = (SCRIPT.parents[3] / ".yabairc").read_text()
        self.assertNotIn("app='^PyCharm$'", config)
        self.assertNotIn("yabai -m rule --add label=route-coding", config)


class LayoutTests(unittest.TestCase):
    def test_three_horizontal_categories_on_a_larger_screen_with_fixed_width(self):
        source_bounds = {"uuid": "source", "frame": {"x": 0, "y": 0, "w": 1000, "h": 800}}
        destination = {"x": 100, "y": -100, "w": 2000, "h": 1600}
        for x, expected_x, anchor in ((50, 200, "start"), (200, 800, "center"), (350, 1400, "end")):
            source = window("Safari", frame={"x": x, "y": 80, "w": 600, "h": 640})
            record = memory.new_record(source, source_bounds)
            before = copy.deepcopy(record)
            target = memory.target_layout(record, {"x": 0, "y": 0, "w": 900, "h": 900}, destination)
            self.assertEqual(record["position"]["x"]["anchor"], anchor)
            self.assertEqual(target, {"x": expected_x, "y": 60, "w": 600, "h": 1280})
            self.assertEqual(record, before)

    def test_fixed_width_round_trip_recovers_after_screen_clamping_for_every_app(self):
        for app in ("Safari", "Finder", "PyCharm", "iTerm2", "ChatGPT", "Google Chrome"):
            source = window(app, frame={"x": 50, "y": 20, "w": 1800, "h": 1000})
            record = memory.new_record(source, BIG)
            small = memory.target_layout(record, source["frame"], SMALL["frame"])
            self.assertEqual(small["w"], 1000)
            returned = memory.target_layout(record, small, BIG["frame"])
            self.assertEqual(returned["w"], 1800)
            self.assertEqual(returned["x"], 100)  # Near-centered windows restore exactly centered.
            self.assertEqual(record["width_preferences"]["value"], 1800)

    def test_defaults_restore_fixed_width_and_proportional_short_height(self):
        source = window("Safari", frame={"x": 200, "y": 120, "w": 600, "h": 300})
        record = memory.new_record(source, BIG)
        current = {"x": -1000, "y": 100, "w": 650, "h": 250}
        self.assertEqual(memory.target_layout(record, current, SMALL["frame"]),
                         {"x": -900, "y": 150, "w": 600, "h": 150})

    def test_relative_width_mode_scales_width_with_independent_positions(self):
        with patch.dict(memory.APP_OVERRIDES, Safari={"width_mode": "relative"}):
            record = memory.new_record(window("Safari"), BIG)
            self.assertEqual(record["width_preferences"], {"mode": "relative", "value": .3})
            target = memory.target_layout(record, window()["frame"], SMALL["frame"])
        self.assertEqual(target["w"], 300)
        self.assertEqual(target["x"], -900)

    def test_optional_sizes_are_composed_before_both_anchors(self):
        record = memory.new_record(window(frame={"x": 1200, "y": 40, "w": 600, "h": 960}), BIG)
        self.assertEqual(memory.target_layout(record, window()["frame"], SMALL["frame"]),
                         {"x": -700, "y": 110, "w": 600, "h": 480})

    def test_each_position_axis_can_opt_out_independently_of_sizing(self):
        for disabled, other in (("x", "y"), ("y", "x")):
            record = memory.new_record(window(frame={"x": 200, "y": 20, "w": 600, "h": 1000}), BIG)
            record["position"][disabled] = None
            current = {"x": -950, "y": 140, "w": 700, "h": 300}
            target = memory.target_layout(record, current, SMALL["frame"])
            self.assertEqual(target[disabled], current[disabled])
            self.assertNotEqual(target[other], current[other])
            self.assertEqual((target["w"], target["h"]), (600, 500))

    def test_full_height_and_small_margins_qualify_independent_of_width(self):
        for width in (300, 2000):
            frame = {"x": 123, "y": 20, "w": width, "h": 1000}
            self.assertEqual(memory.relative_height(frame, BIG["frame"]), 1)
        self.assertEqual(memory.relative_height(
            {"x": 0, "y": 40, "w": 300, "h": 960}, BIG["frame"]), .96)

    def test_all_heights_scale_without_full_height_thresholds(self):
        for height in (100, 300, 940, 950, 960, 1000):
            frame = {"x": 0, "y": 20, "w": 600, "h": height}
            self.assertEqual(memory.relative_height(frame, BIG["frame"]), height / 1000)
        self.assertEqual(memory.relative_height(dict(frame, h=1200), BIG["frame"]), 1)
        with self.assertRaises(ValueError):
            memory.relative_height(frame, dict(BIG["frame"], h=0))

    def test_short_windows_preserve_both_vertical_gaps_across_sizes(self):
        for top, bottom in ((.1, .6), (.6, .1), (.35, .35), (0, 0), (.01, .02)):
            source = window(frame={"x": 200, "y": 20 + top * 1000, "w": 600,
                                   "h": (1 - top - bottom) * 1000})
            record = memory.new_record(source, BIG)
            for bounds in (SMALL["frame"], {"x": 50, "y": -800, "w": 3000, "h": 2000}):
                target = memory.target_layout(record, source["frame"], bounds)
                self.assertAlmostEqual(target["y"] - bounds["y"], top * bounds["h"], delta=1)
                self.assertAlmostEqual(bounds["y"] + bounds["h"] - target["y"] - target["h"],
                                       bottom * bounds["h"], delta=1)

    def test_vertical_optout_keeps_y_but_still_scales_height(self):
        with patch.dict(memory.APP_OVERRIDES, Safari={"vertical_anchor": "off"}):
            record = memory.new_record(window("Safari"), BIG)
        target = memory.target_layout(record, dict(window()["frame"], y=120), SMALL["frame"])
        self.assertEqual(target["h"], 400)
        self.assertEqual(target["y"], 120)

    def test_target_clamping_does_not_replace_preferred_sizes_or_positions(self):
        record = memory.new_record(window(frame={"x": 0, "y": 20, "w": 1800, "h": 1000}), BIG)
        before = copy.deepcopy(record)
        target = memory.target_layout(record, window()["frame"], SMALL["frame"])
        self.assertEqual((target["w"], target["h"]), (1000, 500))
        self.assertEqual(record, before)

    def test_near_full_height_margins_are_preserved_by_the_nearer_vertical_edge(self):
        for top, bottom, anchor in ((.005, .025, "start"), (.025, .005, "end"),
                                     (.015, .015, "end")):
            source = window("Safari", frame={
                "x": 200, "y": 20 + top * 1000, "w": 600,
                "h": (1 - top - bottom) * 1000,
            })
            record = memory.new_record(source, BIG)
            self.assertEqual(record["position"]["y"]["anchor"], anchor)
            target = memory.target_layout(record, window()["frame"], SMALL["frame"])
            self.assertEqual(target["y"], round(100 + top * 500))
            self.assertEqual(target["h"], 485)


class LearningTests(unittest.TestCase):
    def test_x_or_width_edits_update_x_but_not_vertical_intent(self):
        record = memory.new_record(window(), BIG)
        for frame in (dict(record["last_frame"], x=1400), dict(record["last_frame"], w=1600)):
            updated = memory.updated_record(record, window(frame=frame), BIG)
            self.assertNotEqual(updated["position"]["x"], record["position"]["x"])
            self.assertEqual(updated["position"]["y"], record["position"]["y"])
            self.assertEqual(updated["height_fraction"], record["height_fraction"])
        self.assertEqual(updated["width_preferences"]["value"], 1600)

    def test_y_or_height_edits_leave_width_and_horizontal_intent_unchanged(self):
        record = memory.new_record(window(), BIG)
        for frame in (dict(record["last_frame"], y=200), dict(record["last_frame"], h=900)):
            updated = memory.updated_record(record, window(frame=frame), BIG)
            self.assertNotEqual(updated["position"]["y"], record["position"]["y"])
            self.assertEqual(updated["position"]["x"], record["position"]["x"])
            self.assertEqual(updated["width_preferences"], record["width_preferences"])

    def test_position_only_edit_does_not_replace_constrained_width(self):
        record = memory.new_record(window(frame={"x": 200, "y": 20, "w": 1500, "h": 1000}), BIG)
        record.update(display=SMALL, last_frame={"x": -1000, "y": 100, "w": 1000, "h": 500})
        changed = window(frame=dict(record["last_frame"], y=150), display=2)
        updated = memory.updated_record(record, changed, SMALL)
        self.assertEqual(updated["width_preferences"]["value"], 1500)
        self.assertEqual(updated["position"]["x"], record["position"]["x"])

    def test_manually_filling_axes_learns_horizontal_center_and_full_height(self):
        record = memory.new_record(window(frame={"x": 1200, "y": 200, "w": 600, "h": 300}), BIG)
        updated = memory.updated_record(record, window(frame={"x": 0, "y": 20, "w": 2000, "h": 1000}), BIG)
        self.assertEqual(updated["position"], {"x": preference("center"), "y": preference("end")})

    def test_manual_shortening_updates_proportional_height(self):
        record = memory.new_record(window(), BIG)
        full = window(frame={"x": 200, "y": 20, "w": 600, "h": 1000})
        record = memory.updated_record(record, full, BIG)
        self.assertEqual(record["height_fraction"], 1)
        record = memory.updated_record(record, window(), BIG)
        self.assertEqual(record["height_fraction"], .8)

    def test_clamped_position_only_edit_does_not_erase_height_intent(self):
        original = window(frame={"x": 200, "y": 40, "w": 600, "h": 960})
        record = memory.new_record(original, BIG)
        record.update(display=SMALL, last_frame={"x": -900, "y": 100, "w": 600, "h": 650})
        changed = window(frame=dict(record["last_frame"], y=120), display=2)
        updated = memory.updated_record(record, changed, SMALL)
        self.assertEqual(updated["height_fraction"], .96)

    def test_unconstrained_y_only_move_preserves_height_fraction(self):
        original = window(frame={"x": 200, "y": 40, "w": 600, "h": 960})
        record = memory.new_record(original, BIG)
        changed = window(frame=dict(original["frame"], y=100))
        self.assertEqual(memory.updated_record(record, changed, BIG)["height_fraction"], .96)

    def test_changed_policy_reseeds_only_its_own_axis_without_moving(self):
        original = window()
        record = memory.new_record(original, BIG)
        with patch.dict(memory.APP_OVERRIDES, ChatGPT={"horizontal_anchor": "off", "width_mode": "fixed"}):
            disabled = memory.reconcile_record(record, original, BIG)
        self.assertIsNone(disabled["position"]["x"])
        self.assertEqual(disabled["position"]["y"], record["position"]["y"])
        moved = window(frame=dict(original["frame"], x=1200))
        enabled = memory.reconcile_record(disabled, moved, BIG)
        self.assertEqual(enabled["position"]["x"], preference("end", .1))
        self.assertEqual(enabled["position"]["y"], record["position"]["y"])

    def test_width_policy_changes_keep_both_anchors_and_height_intent(self):
        original = window(frame={"x": 400, "y": 40, "w": 800, "h": 960})
        record = memory.new_record(original, BIG)
        for mode in ("relative", None):
            with patch.dict(memory.APP_OVERRIDES, ChatGPT={"width_mode": mode}):
                updated = memory.reconcile_record(record, original, BIG)
            self.assertEqual(updated["position"], record["position"])
            self.assertEqual(updated["height_fraction"], .96)
            self.assertEqual(updated["width_preferences"],
                             {"mode": "relative", "value": .4} if mode else None)


class MigrationTests(unittest.TestCase):
    def test_nearest_edge_upgrade_learns_missing_sizes_from_saved_source_not_live_screen(self):
        record = memory.new_record(window("Safari", frame={"x": 600, "y": 120, "w": 600, "h": 300}), BIG)
        record.update(position_model="nearest-edge", width_preferences=None, height_fraction=None)
        record["position"]["x"] = preference("start", .3)
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"]["x"], preference("center"))
        self.assertEqual(migrated["width_preferences"], {"mode": "fixed", "value": 600})
        self.assertEqual(migrated["height_fraction"], .3)
        self.assertEqual(memory.target_layout(migrated, window()["frame"], SMALL["frame"]),
                         {"x": -800, "y": 150, "w": 600, "h": 150})

    def test_nearest_edge_upgrade_preserves_constrained_width_height_and_edge_gap(self):
        record = memory.new_record(window(), SMALL)
        record.update(position_model="nearest-edge", width_preferences={"mode": "fixed", "value": 1800},
                      height_fraction=.6, last_frame={"x": -1000, "y": 100, "w": 1000, "h": 650})
        record["position"] = {"x": preference("end", .05), "y": preference("start", .1)}
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"], record["position"])
        self.assertEqual(migrated["width_preferences"], record["width_preferences"])
        self.assertEqual(migrated["height_fraction"], .6)
        returned = memory.target_layout(migrated, record["last_frame"], BIG["frame"])
        self.assertEqual(returned, {"x": 100, "y": 120, "w": 1800, "h": 600})

    def test_old_relative_width_converts_to_fixed_using_source_extent(self):
        record = memory.new_record(window("Safari"), BIG)
        record.update(position_model="nearest-edge", width_preferences={"mode": "relative", "value": .3})
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["width_preferences"], {"mode": "fixed", "value": 600})

    def test_current_format_is_idempotent_without_relearning(self):
        record = memory.new_record(window(), BIG)
        self.assertEqual(memory.migrate_record(record), record)

    def test_old_auto_center_records_are_reclassified_from_saved_source_edges(self):
        source = window(frame={"x": 400, "y": 200, "w": 800, "h": 300})
        record = memory.new_record(source, BIG)
        record.pop("position_model")
        record["position"] = {"x": preference("center", -.1), "y": preference("center", -.17)}
        before = copy.deepcopy(record)
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"], {
            "x": preference("start", .2), "y": preference("center", -.17)})
        self.assertEqual(migrated["position_model"], memory.POSITION_MODEL)
        for key in ("identity", "display", "last_frame", "width_preferences", "height_fraction"):
            self.assertEqual(migrated[key], record[key])
        self.assertEqual(record, before)
        self.assertEqual(memory.migrate_record(migrated), migrated)

    def test_model_upgrade_centers_near_equal_gaps_and_preserves_vertical_intent(self):
        source = window(frame={"x": 700, "y": 320, "w": 600, "h": 400})
        record = memory.new_record(source, BIG)
        record["position_model"] = "nearest-edge"
        record["position"]["x"] = preference("end", .35)
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"], {
            "x": preference("center"), "y": preference("end", .3)})
        self.assertEqual(migrated["height_fraction"], .4)

    def test_model_upgrade_keeps_explicit_center_override_and_deferred_alignment(self):
        record = memory.new_record(window(), BIG)
        record.pop("position_model")
        explicit = preference("center", -.01, "center")
        deferred = dict(preference("center", None), legacy_alignment=.3)
        record["position"] = {"x": deferred, "y": explicit}
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"]["y"], explicit)
        self.assertEqual(migrated["position"]["x"],
                         dict(preference("start", None), legacy_alignment=.3))

    def test_previous_width_and_active_height_use_preferences_not_clamped_observation(self):
        record = previous_record(window(frame={"x": 0, "y": 200, "w": 1000, "h": 700}),
                                 full_height={"top": .01, "bottom": .03}, width={"x": 1, "w": 600})
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"]["x"], preference("end"))
        self.assertEqual(migrated["position"]["y"], preference("start", .01))
        self.assertAlmostEqual(migrated["height_fraction"], .96)
        self.assertEqual(migrated["width_preferences"], {"mode": "fixed", "value": 600})
        self.assertTrue(all(k not in migrated for k in ("proportions", "full_height", "tracking_mode", "horizontal_position")))
        self.assertEqual(memory.migrate_record(migrated), migrated)

    def test_previous_inactive_height_seeds_new_proportional_policy_from_saved_frame(self):
        record = previous_record(window("Safari", frame={"x": 400, "y": 20, "w": 800, "h": 1000}))
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["height_fraction"], 1)
        self.assertEqual(migrated["width_preferences"], {"mode": "fixed", "value": 800})
        self.assertEqual(migrated["position"]["x"], preference("start", .2))

    def test_previous_arbitrary_y_history_is_not_claimed_recoverable(self):
        record = previous_record(window(frame={"x": 200, "y": 200, "w": 600, "h": 300}), width={"x": .1, "w": 600})
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"]["y"], preference("start", .18))

    def test_screen_relative_legacy_x_is_reconstructed_from_saved_preferences(self):
        source = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        record = legacy_record(source, screen_x=True)
        record["last_frame"] = dict(source["frame"], x=0, w=1000)
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["position"]["x"], preference("end"))
        self.assertEqual(migrated["width_preferences"], {"mode": "fixed", "value": 600})
        self.assertEqual(migrated["height_fraction"], .8)

    def test_relative_width_legacy_values_keep_their_units(self):
        with patch.dict(memory.APP_OVERRIDES, Safari={"width_mode": "relative"}):
            record = legacy_record(window("Safari"), mode="relative")
            migrated = memory.migrate_record(record)
        self.assertEqual(migrated["width_preferences"], {"mode": "relative", "value": .3})
        self.assertEqual(migrated["position"]["x"], preference("start", .1))

    def test_legacy_near_full_height_migrates_preferred_geometry(self):
        original = window(frame={"x": 200, "y": 40, "w": 600, "h": 960})
        record = legacy_record(original)
        record["last_frame"] = dict(original["frame"], y=200, h=700)
        migrated = memory.migrate_record(record)
        self.assertEqual(migrated["height_fraction"], .96)
        target = memory.target_layout(migrated, window()["frame"], SMALL["frame"])
        self.assertEqual((target["y"], target["h"]), (110, 480))

    def test_ambiguous_constrained_legacy_x_is_deferred_not_invented_as_zero_gap(self):
        record = previous_record(window(frame={"x": 0, "y": 100, "w": 2000, "h": 800}),
                                 width={"x": .8, "w": 2500})
        migrated = memory.migrate_record(record)
        self.assertIsNone(migrated["position"]["x"]["offset"])
        self.assertEqual(migrated["position"]["x"]["legacy_alignment"], .8)
        self.assertEqual(memory.migrate_record(migrated), migrated)
        bigger = {"uuid": "huge", "frame": {"x": 0, "y": 20, "w": 4000, "h": 1000}}
        # Once a meaningful unconstrained baseline exists, finalize exactly once.
        current = window(frame={"x": 1200, "y": 100, "w": 2500, "h": 800})
        migrated.update(display=bigger, last_frame=dict(current["frame"], x=0))
        finalized = memory.updated_record(migrated, current, bigger)
        self.assertNotIn("legacy_alignment", finalized["position"]["x"])
        self.assertEqual(finalized["position"]["x"], preference("end", .075))


class EventFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(patch.stopall)
        cache = Path(self.directory.name)
        patch.object(memory, "CACHE", cache).start()
        patch.object(memory, "STATE", cache / "windows.json").start()
        self.displays = patch.object(memory, "usable_displays", return_value={1: BIG, 2: SMALL}).start()
        self.settle = patch.object(memory, "wait_for_settle").start()
        self.restore = patch.object(memory, "restore_layout").start()
        self.restore.side_effect = lambda w, r, b: dict(w, frame=memory.target_layout(r, w["frame"], b))

    def event(self, value):
        self.settle.return_value = value
        memory.process_window(1)

    def save(self, state):
        memory.STATE.write_text(json.dumps(state))


class EventTests(EventFixture, unittest.TestCase):
    def test_manual_horizontal_move_reclassifies_without_immediate_snapping(self):
        source = window("Safari", frame={"x": 200, "y": 120, "w": 600, "h": 300})
        self.event(source)
        centered = dict(source, frame=dict(source["frame"], x=600))
        self.event(centered)
        self.restore.assert_not_called()
        saved = memory.load_state()["1"]
        self.assertEqual(saved["last_frame"]["x"], 600)
        self.assertEqual(saved["position"]["x"], preference("center"))
        self.event(window("Safari", frame={"x": -1000, "y": 100, "w": 700, "h": 250}, display=2))
        restored = memory.load_state()["1"]["last_frame"]
        self.assertEqual(restored, {"x": -800, "y": 150, "w": 600, "h": 150})
        self.event(window("Safari", frame=restored, display=2))
        self.assertEqual(memory.load_state()["1"]["position"]["x"], preference("center"))

    def test_first_event_learns_without_moving(self):
        self.event(window())
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["width_preferences"]["value"], 600)

    def test_manual_resize_then_transfer_uses_fresh_geometry_without_feedback(self):
        self.event(window())
        adjusted = window(frame={"x": 200, "y": 120, "w": 750, "h": 300})
        self.event(adjusted)
        saved = memory.load_state()["1"]
        moved = window(frame={"x": -950, "y": 100, "w": 700, "h": 250}, display=2)
        self.event(moved)
        self.restore.assert_called_once_with(moved, saved, SMALL["frame"])
        self.assertEqual(memory.load_state()["1"]["last_frame"], {"x": -900, "y": 150, "w": 750, "h": 150})
        own = dict(moved, frame=memory.load_state()["1"]["last_frame"])
        self.event(own)
        self.restore.assert_called_once()
        self.assertEqual(memory.load_state()["1"]["position"], saved["position"])

    def test_unlisted_short_window_restores_width_height_and_position(self):
        source = window("Safari", frame={"x": 200, "y": 120, "w": 600, "h": 300})
        self.event(source)
        self.event(window("Safari", frame={"x": -1000, "y": 100, "w": 700, "h": 250}, display=2))
        self.assertEqual(memory.load_state()["1"]["last_frame"], {"x": -900, "y": 150, "w": 600, "h": 150})

    def test_resolution_change_on_same_display_restores_position_and_optional_sizes(self):
        self.event(window(frame={"x": 200, "y": 40, "w": 600, "h": 960}))
        self.displays.return_value = {1: dict(BIG, frame={"x": 0, "y": 30, "w": 1000, "h": 500})}
        self.event(window())
        self.assertEqual(memory.load_state()["1"]["last_frame"], {"x": 100, "y": 40, "w": 600, "h": 480})

    def test_disabled_app_event_does_not_learn_or_restore(self):
        with patch.dict(memory.APP_OVERRIDES, Safari={"enabled": False}):
            self.event(window("Safari"))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state(), {})

    def test_reused_id_for_different_process_seeds_without_moving(self):
        self.event(window())
        self.event(window(pid=99, display=2))
        self.restore.assert_not_called()
        self.assertEqual(memory.load_state()["1"]["identity"], ["ChatGPT", 99])

    def test_failed_restore_preserves_all_preferences_for_retry(self):
        self.event(window())
        before = memory.load_state()
        self.restore.side_effect = subprocess.TimeoutExpired("yabai", 20)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.event(window(display=2))
        self.assertEqual(memory.load_state(), before)

    def test_topology_change_during_restore_does_not_commit_stale_observations(self):
        self.event(window())
        before = memory.load_state()
        self.restore.side_effect = lambda w, r, b: dict(w, display=3)
        self.event(window(display=2))
        self.assertEqual(memory.load_state(), before)

    def test_app_constraints_and_own_events_do_not_overwrite_size_or_anchors(self):
        source = window(frame={"x": 200, "y": 40, "w": 600, "h": 960})
        self.event(source)
        saved = memory.load_state()["1"]
        constrained = window(frame={"x": -1000, "y": 100, "w": 800, "h": 650}, display=2)
        self.restore.side_effect = lambda *args: constrained
        self.event(window(display=2))
        self.event(constrained)
        actual = memory.load_state()["1"]
        for key in ("position", "width_preferences", "height_fraction"):
            self.assertEqual(actual[key], saved[key])

    def test_short_window_is_not_promoted_by_os_clamping_during_unplug(self):
        self.event(window("Safari"))
        clamped = window("Safari", frame={"x": -1000, "y": 100, "w": 600, "h": 500}, display=2)
        self.event(clamped)
        actual = dict(clamped, frame=memory.load_state()["1"]["last_frame"])
        self.event(actual)
        self.assertEqual(memory.load_state()["1"]["height_fraction"], .8)

    def test_migration_persists_without_moving_unchanged_windows(self):
        self.save({"1": legacy_record(window(), screen_x=True)})
        self.event(window())
        self.restore.assert_not_called()
        saved = memory.load_state()["1"]
        self.assertEqual(saved["position"]["x"], preference("start", .1))
        self.assertNotIn("proportions", saved)

    def test_old_center_model_upgrade_persists_without_moving_unchanged_window(self):
        source = window(frame={"x": 400, "y": 200, "w": 800, "h": 300})
        record = memory.new_record(source, BIG)
        record.pop("position_model")
        record["position"] = {"x": preference("center", -.1), "y": preference("center", -.17)}
        self.save({"1": record})
        self.event(source)
        self.restore.assert_not_called()
        actual = memory.load_state()["1"]
        self.assertEqual(actual["position"], {
            "x": preference("start", .2), "y": preference("center", -.17)})
        self.assertEqual(actual["position_model"], memory.POSITION_MODEL)
        self.assertEqual(actual["last_frame"], source["frame"])

    def test_first_transfer_migrates_using_old_bounds_not_new_live_placement(self):
        source = window(frame={"x": 1400, "y": 100, "w": 600, "h": 800})
        self.save({"1": legacy_record(source, screen_x=True)})
        self.event(window(frame={"x": -1000, "y": 100, "w": 600, "h": 500}, display=2))
        self.assertEqual(memory.load_state()["1"]["last_frame"]["x"], -600)
        self.assertEqual(memory.load_state()["1"]["height_fraction"], .8)

    def test_unknown_display_or_invalid_bounds_does_not_replace_preferences(self):
        self.event(window())
        before = memory.load_state()
        self.displays.return_value = {}
        self.event(window(display=3))
        self.assertEqual(memory.load_state(), before)

    def test_near_full_height_and_anchor_intent_survive_round_trip(self):
        source = window("Safari", frame={"x": 1200, "y": 40, "w": 600, "h": 960})
        self.event(source)
        saved = memory.load_state()["1"]
        self.event(window("Safari", frame={"x": -1000, "y": 100, "w": 700, "h": 300},
                          display=2))
        self.assertEqual(memory.load_state()["1"]["last_frame"],
                         {"x": -700, "y": 110, "w": 600, "h": 480})
        # Every app retains its preferred width on each destination.
        self.event(window("Safari", frame={"x": 0, "y": 20, "w": 800, "h": 480}))
        actual = memory.load_state()["1"]
        self.assertEqual(actual["last_frame"], {"x": 1200, "y": 40, "w": 600, "h": 960})
        self.assertEqual(actual["position"], saved["position"])
        self.assertEqual(actual["height_fraction"], saved["height_fraction"])

    def test_deferred_legacy_anchor_finalizes_after_unconstrained_transfer(self):
        legacy = previous_record(
            window(frame={"x": 0, "y": 100, "w": 2000, "h": 800}),
            width={"x": .8, "w": 2500})
        self.save({"1": legacy})
        huge = {"uuid": "huge", "frame": {"x": 0, "y": 20, "w": 4000, "h": 1000}}
        self.displays.return_value = {1: BIG, 2: huge}
        self.event(window(frame={"x": 0, "y": 100, "w": 2500, "h": 800}, display=2))
        saved = memory.load_state()["1"]
        self.assertEqual(saved["last_frame"]["x"], 1200)
        self.assertEqual(saved["position"]["x"], preference("end", .075))
        self.event(window(frame=saved["last_frame"], display=2))
        self.assertEqual(memory.load_state()["1"]["position"], saved["position"])

    def test_deferred_legacy_anchor_survives_transfer_with_app_size_constraint(self):
        for extent, actual_width in ((2400, 2000), (4000, 2700)):
            with self.subTest(extent=extent, actual_width=actual_width):
                legacy = previous_record(
                    window(frame={"x": 0, "y": 100, "w": 2000, "h": 800}),
                    width={"x": .8, "w": 2500})
                self.save({"1": legacy})
                destination = {"uuid": "other", "frame": {"x": 0, "y": 20, "w": extent, "h": 1000}}
                self.displays.return_value = {1: BIG, 2: destination}
                # Cover app max-width below a screen-clamped request, and an
                # app minimum width larger than a preferred size that does fit.
                constrained = window(frame={
                    "x": .8 * (extent - actual_width), "y": 100,
                    "w": actual_width, "h": 800,
                }, display=2)
                self.restore.side_effect = lambda *args: constrained
                self.event(window(display=2))
                saved = memory.load_state()["1"]["position"]["x"]
                self.assertIsNone(saved["offset"])
                self.assertEqual(saved["legacy_alignment"], .8)

    def test_install_axis_optout_then_reenable_reseeds_without_replaying_old_anchor(self):
        source = window("Safari", frame={"x": 200, "y": 120, "w": 600, "h": 300})
        self.event(source)
        with patch.dict(memory.APP_OVERRIDES, Safari={"vertical_anchor": "off"}), \
                patch.object(memory, "run"), patch.object(memory.subprocess, "run"), \
                patch.object(memory, "refresh"):
            memory.install()
            self.assertIsNone(memory.load_state()["1"]["position"]["y"])
        current = window("Safari", frame={"x": 200, "y": 620, "w": 600, "h": 300})
        self.event(current)
        self.restore.assert_not_called()
        saved = memory.load_state()["1"]
        self.assertEqual(saved["position"]["y"], preference("end", .1))
        self.assertEqual(saved["last_frame"], current["frame"])


class StateStoreTests(EventFixture, unittest.TestCase):
    def test_nested_preference_mutations_are_persisted(self):
        self.save({"1": memory.new_record(window(), BIG)})
        with memory.state_store() as state:
            state["1"]["position"]["x"]["offset"] = .125
        self.assertEqual(memory.load_state()["1"]["position"]["x"]["offset"], .125)
        self.assertFalse(memory.STATE.with_suffix(".tmp").exists())

    def test_failure_discards_partial_mutation_instead_of_writing_it(self):
        original = {"1": memory.new_record(window(), BIG)}
        self.save(original)
        before = memory.STATE.read_text()
        with self.assertRaises(RuntimeError):
            with memory.state_store() as state:
                state["1"]["position"]["x"]["offset"] = .9
                raise RuntimeError("application failed")
        self.assertEqual(memory.STATE.read_text(), before)

    def test_normalization_is_saved_even_without_other_mutation(self):
        self.save({"1": legacy_record(window(), screen_x=True)})
        with memory.state_store() as state:
            self.assertIn("position", state["1"])
        self.assertIn("position", memory.load_state()["1"])
        with patch.object(memory, "save_state") as save:
            with memory.state_store():
                pass
            save.assert_not_called()


class RestoreTests(unittest.TestCase):
    def restore(self, current, record, bounds, *, move_adjustment=None, minimum=(0, 0)):
        operations = []
        def command(*args):
            operation, value = args[-2:]
            operations.append((operation, value))
            a, b = map(float, value.split(":")[1:])
            self.assertTrue(value.startswith("abs:"))
            if operation == "--move":
                current["frame"].update(x=a, y=b)
                if move_adjustment and len(operations) == 1:
                    current["frame"].update(move_adjustment)
            elif operation == "--resize":
                current["frame"].update(w=max(a, minimum[0]), h=max(b, minimum[1]))
            else:
                self.fail(operation)
        with patch.object(memory, "run", side_effect=command), \
                patch.object(memory, "query", side_effect=lambda *args: copy.deepcopy(current)), \
                patch.object(memory.time, "sleep"):
            result = memory.restore_layout(copy.deepcopy(current), record, bounds)
        return result, operations

    def test_position_only_restore_preserves_post_move_dimensions_and_skips_resize(self):
        record = memory.new_record(window("Safari", frame={"x": 1200, "y": 620, "w": 600, "h": 300}), BIG)
        record["width_preferences"] = None
        record["height_fraction"] = None
        current = window("Safari", frame={"x": -1000, "y": 100, "w": 600, "h": 250}, display=2)
        actual, operations = self.restore(current, record, SMALL["frame"], move_adjustment={"w": 650, "h": 280})
        self.assertFalse(any(op == "--resize" for op, _ in operations))
        self.assertEqual(actual["frame"], {"x": -750, "y": 270, "w": 650, "h": 280})

    def test_width_only_resize_preserves_post_move_height_and_opted_out_y(self):
        record = memory.new_record(window(), BIG)
        record["position"]["y"] = None
        record["height_fraction"] = None
        current = window(frame={"x": -950, "y": 150, "w": 900, "h": 700}, display=2)
        actual, operations = self.restore(current, record, SMALL["frame"], move_adjustment={"y": 120, "h": 350})
        self.assertEqual(actual["frame"], {"x": -900, "y": 120, "w": 600, "h": 350})
        self.assertIn(("--resize", "abs:600:350"), operations)

    def test_height_only_resize_preserves_post_move_width_and_opted_out_x(self):
        record = memory.new_record(window("Safari", frame={"x": 200, "y": 40, "w": 600, "h": 960}), BIG)
        record["position"]["x"] = None
        record["width_preferences"] = None
        current = window("Safari", frame={"x": -875, "y": 200, "w": 700, "h": 300}, display=2)
        actual, operations = self.restore(current, record, SMALL["frame"], move_adjustment={"x": -850, "w": 750})
        self.assertEqual(actual["frame"], {"x": -850, "y": 110, "w": 750, "h": 480})
        self.assertIn(("--resize", "abs:750:480"), operations)

    def test_both_actual_minimum_dimensions_drive_final_anchors(self):
        record = memory.new_record(window(frame={"x": 1200, "y": 40, "w": 600, "h": 960}), BIG)
        current = window(frame={"x": -1000, "y": 100, "w": 700, "h": 300}, display=2)
        actual, operations = self.restore(current, record, SMALL["frame"], minimum=(800, 650))
        self.assertEqual(actual["frame"], {"x": -900, "y": 100, "w": 800, "h": 650})
        self.assertEqual(operations[-1][0], "--move")
        self.assertEqual(record["width_preferences"]["value"], 600)
        self.assertEqual(record["height_fraction"], .96)

    def test_backend_position_constraint_returns_actual_without_loop_or_relearning(self):
        source = window(frame={"x": 0, "y": 20, "w": 2053, "h": 1000})
        with patch.dict(memory.APP_OVERRIDES,
                        ChatGPT={"width_mode": "fixed", "vertical_anchor": "center"}):
            record = memory.new_record(source, BIG)
        saved = copy.deepcopy(record)
        bounds = {"x": 3360, "y": -851, "w": 1890, "h": 3360}
        current = window(frame={"x": 3360, "y": -821, "w": 1890, "h": 1000}, display=2)
        operations = []

        def command(*args):
            operation, value = args[-2:]
            first, second = map(float, value.split(":")[1:])
            operations.append((operation, first, second))
            if operation == "--resize":
                # AX/app's height ceiling is smaller than its usable screen.
                current["frame"].update(w=min(first, 1890), h=min(second, 3330))
            else:
                # It also rejects positioning above this app-imposed boundary.
                current["frame"].update(x=first, y=max(second, -821))

        with patch.object(memory, "run", side_effect=command), \
                patch.object(memory, "query", side_effect=lambda *args: copy.deepcopy(current)), \
                patch.object(memory.time, "sleep"):
            actual = memory.restore_layout(copy.deepcopy(current), record, bounds)
        self.assertEqual(actual["frame"], {"x": 3360, "y": -821, "w": 1890, "h": 3330})
        # An explicit center override still gives -836 with actual height 3330.
        self.assertEqual(operations[-1], ("--move", 3360, -836))
        self.assertEqual(sum(op == "--move" and y == -836 for op, _, y in operations), 1)
        self.assertEqual(len(operations), 3)  # Move, resize, final move; no retry loop.
        self.assertEqual(record, saved)

    def test_move_precedes_growth_to_adopt_destination_size_limits(self):
        source = window(frame={"x": 200, "y": 20, "w": 600, "h": 1000})
        record = memory.new_record(source, BIG)
        current = window(frame={"x": -1000, "y": 100, "w": 600, "h": 500}, display=2)
        actual, operations = self.restore(current, record, BIG["frame"])
        self.assertEqual(operations[0][0], "--move")
        self.assertEqual(actual["frame"], source["frame"])

    def test_absolute_resize_retry_safe_when_timed_out_request_already_applied(self):
        source = window("Safari", frame={"x": 200, "y": 40, "w": 600, "h": 960})
        record = memory.new_record(source, BIG)
        current = window("Safari", frame={"x": -875, "y": 200, "w": 700, "h": 300}, display=2)
        resize_count = 0
        def output(args, **kwargs):
            nonlocal resize_count
            operation, value = args[-2:]
            a, b = map(float, value.split(":")[1:])
            self.assertTrue(value.startswith("abs:"))
            if operation == "--move":
                current["frame"].update(x=a, y=b)
            else:
                current["frame"].update(w=a, h=b)
                resize_count += 1
                if resize_count == 1:
                    raise subprocess.TimeoutExpired("yabai", 5)
            return ""
        with patch.object(memory.subprocess, "check_output", side_effect=output), \
                patch.object(memory, "query", side_effect=lambda *args: copy.deepcopy(current)), \
                patch.object(memory.time, "sleep"):
            actual = memory.restore_layout(copy.deepcopy(current), record, SMALL["frame"])
        self.assertEqual(resize_count, 2)
        self.assertEqual(actual["frame"], {"x": -900, "y": 110, "w": 600, "h": 480})


class RefreshTests(EventFixture, unittest.TestCase):
    def test_unchanged_windows_skip_per_window_settling(self):
        self.event(window())
        with patch.object(memory, "query", return_value=[window()]), \
                patch.object(memory, "process_window") as process:
            memory.refresh()
            process.assert_not_called()

    def test_changed_policy_is_not_skipped_by_noop_refresh(self):
        self.event(window())
        with patch.dict(memory.APP_OVERRIDES, ChatGPT={"width_mode": "relative"}), \
                patch.object(memory, "query", return_value=[window()]), \
                patch.object(memory, "process_window") as process:
            memory.refresh()
            process.assert_called_once_with(1, seed=False)

    def test_stalled_window_does_not_prevent_others_from_restoring(self):
        with patch.object(memory, "query", return_value=[window(), dict(window(), id=2)]), \
                patch.object(memory, "process_window", side_effect=[subprocess.TimeoutExpired("yabai", 20), None]) as process, \
                patch.object(memory.sys, "stderr"):
            memory.refresh()
        self.assertEqual([c.args[0] for c in process.call_args_list], [1, 2])

    def test_refresh_prunes_closed_windows_but_seed_inventory_does_not(self):
        self.save({"99": memory.new_record(window(), BIG)})
        with patch.object(memory, "query", return_value=[]):
            memory.refresh(seed=True)
            self.assertIn("99", memory.load_state())
            memory.refresh()
            self.assertEqual(memory.load_state(), {})

    def test_malformed_cache_is_not_silently_discarded(self):
        memory.STATE.write_text("not valid json")
        with self.assertRaises(ValueError):
            memory.load_state()
        self.assertEqual(memory.STATE.read_text(), "not valid json")


class SettlingAndSignalTests(unittest.TestCase):
    def test_waits_for_mouse_release_even_if_frame_already_stable(self):
        with patch.object(memory, "query", return_value=window()) as query, \
                patch.object(memory, "screen_info", side_effect=[
                    {"mouse_down": True}, {"mouse_down": True},
                    {"mouse_down": False}, {"mouse_down": False}, {"mouse_down": False},
                ]), patch.object(memory.time, "sleep"):
            self.assertEqual(memory.wait_for_settle(1), window())
            self.assertEqual(query.call_count, 6)

    def test_unresizable_window_is_not_waited_on(self):
        with patch.object(memory, "query", return_value=dict(window("Slack"), **{"can-resize": False})), \
                patch.object(memory.time, "sleep") as sleep:
            self.assertIsNone(memory.wait_for_settle(1))
            sleep.assert_not_called()

    def test_unsettled_drag_does_not_restore(self):
        with patch.object(memory, "query", return_value=dict(window(), **{"is-grabbed": True})), \
                patch.object(memory.time, "sleep"), patch.object(memory, "screen_info"):
            self.assertIsNone(memory.wait_for_settle(1))

    def test_install_registers_all_nine_events_without_app_exclusions(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(memory, "CACHE", Path(directory)), \
                patch.object(memory, "STATE", Path(directory) / "windows.json"), \
                patch.object(memory, "run") as command, \
                patch.object(memory.subprocess, "run"), patch.object(memory, "refresh"):
            memory.install()
            registrations = [c.args for c in command.call_args_list]
        self.assertEqual(len(registrations), 9)
        signals = [args for args in registrations if any(a.startswith("event=window_") for a in args)]
        self.assertEqual(len(signals), 5)
        self.assertTrue(all(not any(a.startswith(("app=", "app!=")) for a in args)
                            for args in signals))
        self.assertEqual({arg for args in registrations for arg in args if arg.startswith("event=display_")},
                         {"event=display_" + event for event in ("added", "removed", "moved", "resized")})


class RetryTests(unittest.TestCase):
    def test_query_timeout_retries_with_longer_deadline(self):
        with patch.object(memory.subprocess, "check_output", side_effect=[subprocess.TimeoutExpired("yabai", 5), "{}"]) as output, \
                patch.object(memory.time, "sleep"):
            self.assertEqual(memory.run(memory.YABAI, "-m", "query", "--displays"), "{}")
        self.assertEqual([c.kwargs["timeout"] for c in output.call_args_list], [5, 10])

    def test_exhausted_timeouts_raise_instead_of_becoming_geometry(self):
        with patch.object(memory.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("yabai", 20)) as output, \
                patch.object(memory.time, "sleep"):
            with self.assertRaises(subprocess.TimeoutExpired):
                memory.run(memory.YABAI, "-m", "query", "--displays")
        self.assertEqual(output.call_count, 3)

    def test_display_change_rescans_after_early_inventory_timeout(self):
        with patch.object(memory, "refresh", side_effect=[subprocess.TimeoutExpired("yabai", 5), None, None]) as refresh, \
                patch.object(memory.time, "sleep"):
            memory.display_change()
        self.assertEqual(refresh.call_count, 3)

    def test_display_change_reports_persistent_failure(self):
        with patch.object(memory, "refresh", side_effect=subprocess.TimeoutExpired("yabai", 20)), \
                patch.object(memory.time, "sleep"):
            with self.assertRaises(subprocess.TimeoutExpired):
                memory.display_change()


if __name__ == "__main__":
    unittest.main()
