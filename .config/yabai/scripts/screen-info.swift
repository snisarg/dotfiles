// Exclude the menu bar, notch and visible Dock. Convert AppKit's bottom-left
// coordinates to yabai's top-left coordinates using the primary screen.
import AppKit
import CoreGraphics

guard let primary = NSScreen.screens.first else { exit(1) }
let top = primary.frame.maxY
let displays: [[String: Any]] = NSScreen.screens.compactMap { screen in
    guard let number = screen.deviceDescription[
        NSDeviceDescriptionKey("NSScreenNumber")
    ] as? NSNumber else { return nil }
    let frame = screen.visibleFrame
    return [
        "id": number.uint32Value,
        "frame": [
            "x": frame.minX, "y": top - frame.maxY,
            "w": frame.width, "h": frame.height
        ]
    ]
}
let result: [String: Any] = [
    "displays": displays,
    "mouse_down": CGEventSource.buttonState(.combinedSessionState, button: .left)
]
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
