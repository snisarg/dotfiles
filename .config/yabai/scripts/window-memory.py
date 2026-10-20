#!/usr/bin/env python3
"""Keep window widths fixed and vertical geometry proportional across displays."""
import copy
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

DEFAULTS = {
    "enabled": True,
    "horizontal_anchor": "auto",
    "vertical_anchor": "auto",
    "width_mode": "fixed",
}
APP_OVERRIDES = {}
HORIZONTAL_CENTER_GAP_FRACTION = 0.15
AXES = {"x": ("w", "horizontal_anchor"), "y": ("h", "vertical_anchor")}
POSITION_MODEL = "horizontal-center-15-proportional-height"
WINDOW_EVENTS = ("window_created", "window_moved", "window_resized",
                 "window_focused", "window_deminimized")
DISPLAY_EVENTS = ("display_added", "display_removed", "display_moved", "display_resized")
SCRIPT = Path(__file__).resolve()
CACHE = Path.home() / "Library/Caches/yabai-window-memory"
SCREEN_TOOL = CACHE / "screen-info"
STATE = CACHE / "windows.json"
YABAI = shutil.which("yabai") or "/opt/homebrew/bin/yabai"
EPSILON = 1.0  # AX/app rounding, in screen points (not normalized fractions).


def run(*args):
    # Only queries and absolute window commands are retried: no relative deltas.
    retryable = len(args) > 2 and args[0] == YABAI and args[2] in ("query", "window")
    timeouts = (5, 10, 20) if retryable else (5,)
    for attempt, timeout in enumerate(timeouts):
        try:
            return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == len(timeouts) - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def query(*args):
    return json.loads(run(YABAI, "-m", "query", *args))


def screen_info():
    return json.loads(run(str(SCREEN_TOOL)))


def usable_displays():
    usable = {d["id"]: d["frame"] for d in screen_info()["displays"]
              if d["frame"]["w"] > 0 and d["frame"]["h"] > 0}
    return {d["index"]: {"uuid": d["uuid"], "frame": usable[d["id"]]}
            for d in query("--displays") if d["id"] in usable}


def axis_rule(settings, axis):
    value = settings[AXES[axis][1]]
    allowed = ("auto", "center", "off", "start", "end", None)
    allowed += ("left", "right") if axis == "x" else ("top", "bottom")
    if value not in allowed:
        raise ValueError("invalid %s anchor: %s" % (axis, value))
    return {None: "off", "left": "start", "top": "start", "right": "end",
            "bottom": "end"}.get(value, value)


def policy(app):
    override = APP_OVERRIDES.get(app, {})
    if set(override) - set(DEFAULTS):
        raise ValueError("unknown policy fields for %s" % app)
    result = dict(DEFAULTS, **override)
    if result["width_mode"] not in (None, "fixed", "relative"):
        raise ValueError("invalid width mode for %s" % app)
    for axis in AXES:
        axis_rule(result, axis)
    return result


def eligible(window):
    return (policy(window.get("app"))["enabled"]
            and window.get("subrole") == "AXStandardWindow"
            and window.get("can-move") and window.get("can-resize")
            and window.get("has-ax-reference")
            and not any(window.get(flag) for flag in
                        ("is-native-fullscreen", "has-fullscreen-zoom", "is-minimized", "is-hidden")))


def window_signal_filter():
    names = [app for app in APP_OVERRIDES
             if policy(app)["enabled"] != DEFAULTS["enabled"]]
    if not names and DEFAULTS["enabled"]:
        return None
    regex = "^(" + "|".join(re.escape(app) for app in sorted(names)) + ")$"
    return ("app!=" if DEFAULTS["enabled"] else "app=") + regex


def clamp(value, low, high):
    return max(low, min(value, max(low, high)))


def capture_axis(p, size, origin, extent, previous=None, rule="auto", center_fraction=None):
    if extent <= 0:
        raise ValueError("invalid usable screen extent")
    if rule == "off":
        return None
    if rule not in ("auto", "start", "center", "end"):
        raise ValueError("invalid anchor rule: " + str(rule))
    if size >= extent:
        if rule != "auto" and previous is not None and previous.get("rule", "auto") == rule:
            return dict(previous)
        anchor = ("center" if center_fraction is not None else "end") if rule == "auto" else rule
        return {"anchor": anchor, "offset": 0, "rule": rule}
    p = clamp(p, origin, origin + extent - size)
    gaps = {"start": p - origin, "end": origin + extent - p - size,
            "center": p + size / 2 - origin - extent / 2}
    anchor = ("start" if gaps["start"] < gaps["end"] else "end") if rule == "auto" else rule
    if rule == "auto" and center_fraction is not None and abs(gaps["start"] - gaps["end"]) <= center_fraction * extent:
        return {"anchor": "center", "offset": 0, "rule": rule}
    return {"anchor": anchor, "offset": gaps[anchor] / extent, "rule": rule}


def capture_position(axis, frame, bounds, previous=None, rule="auto"):
    size = AXES[axis][0]
    return capture_axis(frame[axis], frame[size], bounds[axis], bounds[size], previous, rule,
                        HORIZONTAL_CENTER_GAP_FRACTION if axis == "x" else None)


def target_axis(saved, size, origin, extent):
    if extent <= 0:
        raise ValueError("invalid usable screen extent")
    if saved.get("legacy_alignment") is not None:
        value = origin + saved["legacy_alignment"] * max(0, extent - size)
    else:
        offset = saved["offset"] * extent
        anchor = saved["anchor"]
        if anchor not in ("start", "center", "end"):
            raise ValueError("invalid saved anchor: " + str(anchor))
        value = {"start": origin + offset,
                 "end": origin + extent - size - offset,
                 "center": origin + (extent - size) / 2 + offset}[anchor]
    return round(clamp(value, origin, origin + extent - size))


def relative_height(frame, bounds):
    height = bounds["h"]
    if height <= 0:
        raise ValueError("invalid usable screen height")
    return clamp(frame["h"] / height, 0, 1)


def changed(frame, previous, axes):
    return any(abs(frame[a] - previous[a]) > EPSILON for a in axes)


def close_frame(a, b):
    return not changed(a, b, ("x", "y", "w", "h"))


def remember_width(frame, bounds, mode):
    return ({"mode": mode, "value": frame["w"] / bounds["w"] if mode == "relative" else frame["w"]}
            if mode is not None else None)


def new_record(window, display):
    frame, bounds, settings = window["frame"], display["frame"], policy(window["app"])
    return {
        "identity": [window["app"], window["pid"]], "display": display, "last_frame": frame,
        "position_model": POSITION_MODEL,
        "position": {a: capture_position(a, frame, bounds, rule=axis_rule(settings, a)) for a in AXES},
        "width_preferences": remember_width(frame, bounds, settings["width_mode"]),
        "height_fraction": relative_height(frame, bounds),
    }


def migrate_record(record):
    """Normalize old cache generations from saved source geometry, never live frames."""
    if "position" in record and "height_fraction" in record:
        if record.get("position_model") == POSITION_MODEL:
            return record
        position = dict(record["position"])
        frame, bounds = record["last_frame"], record["display"]["frame"]
        width = record["width_preferences"]
        mode = policy(record["identity"][0])["width_mode"]
        if (width["mode"] if width else None) != mode:
            preferred = dict(frame)
            if width:
                preferred["w"] = width["value"] * bounds["w"] if width["mode"] == "relative" else width["value"]
            width = remember_width(preferred, bounds, mode)
        for axis, (size, _) in AXES.items():
            saved = position[axis]
            if saved is not None and saved.get("rule", "auto") == "auto":
                if "legacy_alignment" in saved:
                    anchor = "start" if saved["legacy_alignment"] < .5 else "end"
                    position[axis] = dict(saved, anchor=anchor)
                elif axis == "x":
                    # Reclassify saved preferred geometry, not a clamped live frame.
                    preferred_size = (width["value"] * bounds["w"] if width["mode"] == "relative"
                                      else width["value"]) if width else frame["w"]
                    offset = saved["offset"] * bounds["w"]
                    start = {"start": offset, "end": bounds["w"] - preferred_size - offset,
                             "center": (bounds["w"] - preferred_size) / 2 + offset}[saved["anchor"]]
                    if abs(2 * start + preferred_size - bounds["w"]) <= HORIZONTAL_CENTER_GAP_FRACTION * bounds["w"]:
                        position[axis] = {"anchor": "center", "offset": 0, "rule": "auto"}
                    elif saved["anchor"] == "center":
                        anchor = "start" if start < (bounds["w"] - preferred_size) / 2 else "end"
                        gap = start if anchor == "start" else bounds["w"] - preferred_size - start
                        position[axis] = {"anchor": anchor, "offset": gap / bounds["w"], "rule": "auto"}
                else:
                    # Keep existing vertical intent, including constrained preferences.
                    position[axis] = dict(saved)
        fraction = record["height_fraction"]
        return dict(record, position=position, position_model=POSITION_MODEL,
                    width_preferences=width,
                    height_fraction=relative_height(frame, bounds) if fraction is None else fraction)
    if not all(k in record for k in ("identity", "display", "last_frame")) or not any(
            k in record for k in ("width_preferences", "proportions", "full_height")):
        raise ValueError("unsupported window record")
    app, frame, bounds = record["identity"][0], record["last_frame"], record["display"]["frame"]
    settings = policy(app)
    old = record.get("width_preferences") or record.get("proportions")
    preferred = dict(frame)
    margins = record.get("full_height")
    fraction = None
    if margins is not None:
        fraction = 1 - margins["top"] - margins["bottom"]
        preferred.update(y=bounds["y"] + margins["top"] * bounds["h"], h=fraction * bounds["h"])
    elif "full_height" not in record and "proportions" in record:
        props = record["proportions"]
        preferred.update(y=bounds["y"] + props["y"] * bounds["h"], h=props["h"] * bounds["h"])
        fraction = relative_height(preferred, bounds)
    template = {"app": app, "pid": record["identity"][1], "frame": preferred}
    result = new_record(template, record["display"])
    result.update(last_frame=frame,
                  height_fraction=relative_height(preferred, bounds) if fraction is None else fraction)
    if old is not None:
        mode = old.get("mode", "relative" if record.get("horizontal_position") == "screen" else "fixed")
        value = old.get("value", old.get("w"))
        result["width_preferences"] = {"mode": mode, "value": value}
        if "x" in old and result["position"]["x"] is not None:
            size = value * bounds["w"] if mode == "relative" else value
            alignment, rule = old["x"], axis_rule(settings, "x")
            available = record.get("horizontal_position") == "available-space"
            if available and mode == "fixed" and bounds["w"] - size <= EPSILON and rule == "auto":
                anchor = "start" if alignment < .5 else "end"
                pref = {"anchor": anchor, "offset": 0, "rule": rule}
                if alignment not in (0, .5, 1):
                    pref.update(offset=None, legacy_alignment=alignment)
                result["position"]["x"] = pref
            else:
                p = bounds["x"] + alignment * (bounds["w"] - size if available else bounds["w"])
                result["position"]["x"] = capture_axis(p, size, bounds["x"], bounds["w"], rule=rule,
                                                      center_fraction=HORIZONTAL_CENTER_GAP_FRACTION)
        if mode != settings["width_mode"]:
            preferred["w"] = value * bounds["w"] if mode == "relative" else value
            result["width_preferences"] = remember_width(preferred, bounds, settings["width_mode"])
    return result


def reconcile_record(record, window, display):
    result, settings = dict(record), policy(window["app"])
    result["position"] = dict(record["position"])
    for axis, (size, _) in AXES.items():
        saved, rule = record["position"][axis], axis_rule(settings, axis)
        if (saved is None and rule != "off") or (saved is not None and saved.get("rule", "auto") != rule):
            result["position"][axis] = capture_position(axis, window["frame"], display["frame"], rule=rule)
    width = record["width_preferences"]
    if (width["mode"] if width else None) != settings["width_mode"]:
        result["width_preferences"] = remember_width(window["frame"], display["frame"], settings["width_mode"])
    return result


def updated_record(record, window, display):
    frame, before, bounds = window["frame"], record["last_frame"], display["frame"]
    result = dict(record, position=dict(record["position"]), last_frame=frame, display=display)
    settings = policy(window["app"])
    for axis, (size, _) in AXES.items():
        if changed(frame, before, (axis, size)):
            result["position"][axis] = capture_position(axis, frame, bounds,
                                                       record["position"][axis], axis_rule(settings, axis))
    if changed(frame, before, ("w",)):
        result["width_preferences"] = remember_width(frame, bounds, settings["width_mode"])
    fraction = record["height_fraction"]
    constrained = fraction is not None and abs(before["h"] - fraction * bounds["h"]) > EPSILON
    if changed(frame, before, ("h",)) or (changed(frame, before, ("y",)) and not constrained):
        result["height_fraction"] = relative_height(frame, bounds)
    return result


def target_layout(record, current_frame, bounds):
    if bounds["w"] <= 0 or bounds["h"] <= 0:
        raise ValueError("invalid usable screen bounds")
    target, width = dict(current_frame), record["width_preferences"]
    if width is not None:
        desired = width["value"] * bounds["w"] if width["mode"] == "relative" else width["value"]
        target["w"] = clamp(round(desired), 1, bounds["w"])
    if record["height_fraction"] is not None:
        target["h"] = clamp(round(record["height_fraction"] * bounds["h"]), 1, bounds["h"])
    for axis, (size, _) in AXES.items():
        if record["position"][axis] is not None:
            target[axis] = target_axis(record["position"][axis], target[size], bounds[axis], bounds[size])
    return target


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATE)


@contextmanager
def state_store():
    with (CACHE / "state.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = load_state()
        state = {key: migrate_record(copy.deepcopy(value)) for key, value in before.items()}
        yield state
        if state != before:
            save_state(state)


def wait_for_settle(window_id):
    previous, stable = None, 0
    for _ in range(600):  # Coalesce AX events; never snap during a held drag.
        window = query("--windows", "--window", str(window_id))
        if not eligible(window):
            return None
        current = (window["display"], window["frame"])
        if current == previous and not window.get("is-grabbed") and not screen_info()["mouse_down"]:
            stable += 1
            if stable >= 3:
                return window
        else:
            stable = 0
        previous = current
        time.sleep(.1)
    return None


def restore_layout(window, record, bounds):
    """One actuator: move first, refresh uncontrolled sizes, then anchor actual size."""
    window_id = str(window["id"])
    target = target_layout(record, window["frame"], bounds)
    run(YABAI, "-m", "window", window_id, "--move", "abs:{x}:{y}".format(**target))
    time.sleep(.1)
    actual = query("--windows", "--window", window_id)
    target = target_layout(record, actual["frame"], bounds)
    if changed(target, actual["frame"], ("w", "h")):
        run(YABAI, "-m", "window", window_id, "--resize", "abs:{w}:{h}".format(**target))
        time.sleep(.1)
        actual = query("--windows", "--window", window_id)
    final = dict(actual["frame"])
    for axis, (size, _) in AXES.items():
        if record["position"][axis] is not None:
            final[axis] = target_axis(record["position"][axis], final[size], bounds[axis], bounds[size])
    if changed(final, actual["frame"], ("x", "y")):
        run(YABAI, "-m", "window", window_id, "--move", "abs:{x}:{y}".format(**final))
        time.sleep(.1)
        actual = query("--windows", "--window", window_id)
    return actual


def finalize_legacy(record, frame, bounds):
    position = dict(record["position"])
    for axis, (size, _) in AXES.items():
        saved = position[axis]
        if saved is not None and "legacy_alignment" in saved and bounds[size] - frame[size] > EPSILON:
            width = record["width_preferences"]
            if axis == "x" and width is not None:
                preferred = width["value"] * bounds["w"] if width["mode"] == "relative" else width["value"]
                if preferred >= bounds["w"] - EPSILON or abs(preferred - frame["w"]) > EPSILON:
                    continue  # A clamped/app-constrained size is not a new migration baseline.
            position[axis] = capture_position(axis, frame, bounds, rule=saved["rule"])
    return dict(record, position=position)


def process_window(window_id, seed=False):
    with (CACHE / ("window-%s.lock" % window_id)).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with state_store() as state:
            window = query("--windows", "--window", str(window_id)) if seed else wait_for_settle(window_id)
            if window is None or not eligible(window):
                return
            display = usable_displays().get(window["display"])
            if display is None:
                return
            key, identity = str(window_id), [window["app"], window["pid"]]
            old = state.get(key)
            if old is None or old["identity"] != identity:
                state[key] = new_record(window, display)
                return
            record = reconcile_record(old, window, display)
            if record["display"] != display:
                if not close_frame(target_layout(record, window["frame"], display["frame"]), window["frame"]):
                    actual = restore_layout(window, record, display["frame"])
                    if actual["display"] != window["display"] or usable_displays().get(actual["display"]) != display:
                        return  # Topology changed again: keep prior intent retryable.
                    window = actual
                record = finalize_legacy(record, window["frame"], display["frame"])
                record = dict(record, display=display, last_frame=window["frame"])
            elif not close_frame(record["last_frame"], window["frame"]):
                record = updated_record(record, window, display)
            state[key] = record  # Own events are no-ops; context only saves changes.


def refresh(seed=False):
    windows = query("--windows")
    with state_store() as state:
        snapshot = dict(state)
    displays = usable_displays() if snapshot else {}
    for window in windows:
        if not eligible(window):
            continue
        record = snapshot.get(str(window["id"]))
        if (record is not None and record["identity"] == [window["app"], window["pid"]]
                and record["display"] == displays.get(window["display"])
                and close_frame(record["last_frame"], window["frame"])
                and reconcile_record(record, window, record["display"]) == record):
            continue
        try:
            process_window(window["id"], seed=seed)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            print("window-memory: deferred window %s: %s" % (window["id"], error), file=sys.stderr)
    if not seed:
        with state_store() as state:
            live = {str(w["id"]) for w in query("--windows")}
            for key in set(state) - live:
                del state[key]


def install():
    source = SCRIPT.with_name("screen-info.swift")
    if not SCREEN_TOOL.exists() or source.stat().st_mtime > SCREEN_TOOL.stat().st_mtime:
        subprocess.run(["xcrun", "swiftc", str(source), "-o", str(SCREEN_TOOL)],
                       check=True, timeout=120, stderr=subprocess.PIPE, text=True)
    with state_store() as state:
        for key, record in list(state.items()):
            settings = policy(record["identity"][0])
            if not settings["enabled"]:
                del state[key]
            else:
                position = dict(record["position"])
                for axis in AXES:
                    if axis_rule(settings, axis) == "off":
                        position[axis] = None
                state[key] = dict(record, position=position)
    refresh(seed=True)
    command = "%s %s" % (shlex.quote(shutil.which("python3") or "/usr/bin/python3"), shlex.quote(str(SCRIPT)))
    for event in WINDOW_EVENTS + DISPLAY_EVENTS:
        label = "window-memory-" + event
        subprocess.run([YABAI, "-m", "signal", "--remove", label],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        action = command + (' window "$YABAI_WINDOW_ID"' if event in WINDOW_EVENTS else " displays")
        args = [YABAI, "-m", "signal", "--add", "label=" + label, "event=" + event, "action=" + action]
        app_filter = window_signal_filter() if event in WINDOW_EVENTS else None
        run(*(args + [app_filter] if app_filter else args))


def display_change():
    last_error = None
    for delay in (.5, 1, 2):
        time.sleep(delay)
        try:
            refresh()
            last_error = None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error
    if last_error is not None:
        raise last_error


def main():
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.argv[1:] == ["install"]:
        install()
    elif sys.argv[1:] == ["displays"]:
        display_change()
    elif len(sys.argv) == 3 and sys.argv[1] == "window" and sys.argv[2].isdigit():
        process_window(int(sys.argv[2]))
    else:
        raise ValueError("usage: window-memory.py install|displays|window <id>")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print("window-memory: " + (error.stderr or "").strip(), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("window-memory: " + str(error), file=sys.stderr)
        sys.exit(1)
