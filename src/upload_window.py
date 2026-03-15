"""Native macOS upload window with drag-and-drop zone built with PyObjC."""

import logging

import AppKit
import objc
from Foundation import NSObject, NSMakeRect

log = logging.getLogger("open-transcribe")

_current_window = None
_current_delegate = None

# Layout constants
WIN_WIDTH = 320
WIN_HEIGHT = 240
DROP_INSET = 20
DROP_CORNER_RADIUS = 12


class DropZoneView(AppKit.NSView):
    """Custom view that accepts file drops and draws a dashed-border zone."""

    on_drop = objc.ivar()
    window_ref = objc.ivar()
    _highlight = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(DropZoneView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._highlight = False
        self.registerForDraggedTypes_([AppKit.NSFilenamesPboardType])
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, DROP_CORNER_RADIUS, DROP_CORNER_RADIUS
        )

        if self._highlight:
            AppKit.NSColor.selectedControlColor().setFill()
            path.fill()

        AppKit.NSColor.grayColor().setStroke()
        path.setLineWidth_(2.0)
        pattern = [6.0, 4.0]
        path.setLineDash_count_phase_(pattern, 2, 0.0)
        path.stroke()

        # Draw centered label
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(14),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.secondaryLabelColor(),
        }
        label = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            "\u25bc Drop audio file here", attrs
        )
        size = label.size()
        x = (bounds.size.width - size.width) / 2
        y = (bounds.size.height - size.height) / 2
        label.drawAtPoint_(AppKit.NSPoint(x, y))

    def draggingEntered_(self, sender):
        self._highlight = True
        self.setNeedsDisplay_(True)
        return AppKit.NSDragOperationCopy

    def draggingExited_(self, sender):
        self._highlight = False
        self.setNeedsDisplay_(True)

    def performDragOperation_(self, sender):
        pboard = sender.draggingPasteboard()
        files = pboard.propertyListForType_(AppKit.NSFilenamesPboardType)
        if files and len(files) > 0:
            path = files[0]
            if self.on_drop:
                self.on_drop(path)
        if self.window_ref:
            self.window_ref.close()
        return True


class UploadWindowDelegate(NSObject):
    """Handles window close to clear singleton and revert activation policy."""

    def windowWillClose_(self, notification):
        global _current_window, _current_delegate
        _current_window = None
        _current_delegate = None
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


def _build_window(on_drop):
    """Construct the upload NSWindow with a drag-and-drop zone."""
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(200, 200, WIN_WIDTH, WIN_HEIGHT),
        AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Upload a Recording")
    window.setReleasedWhenClosed_(False)
    window.setLevel_(AppKit.NSFloatingWindowLevel)

    content = window.contentView()

    drop_zone = DropZoneView.alloc().initWithFrame_(
        NSMakeRect(
            DROP_INSET,
            DROP_INSET,
            WIN_WIDTH - 2 * DROP_INSET,
            WIN_HEIGHT - 2 * DROP_INSET,
        )
    )
    drop_zone.on_drop = on_drop
    drop_zone.window_ref = window
    content.addSubview_(drop_zone)

    delegate = UploadWindowDelegate.alloc().init()
    window.setDelegate_(delegate)

    global _current_delegate
    _current_delegate = delegate

    return window


def show_upload(on_drop):
    """Show the upload window. Reuses existing window if visible."""
    global _current_window

    AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    if _current_window is not None and _current_window.isVisible():
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        _current_window.makeKeyAndOrderFront_(None)
        return

    window = _build_window(on_drop)
    _current_window = window
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
