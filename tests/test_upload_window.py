"""Tests for src/upload_window.py.

AppKit, Foundation, and objc must be mocked in sys.modules before
importing src.upload_window, because the module-level code uses them.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# ── Mock AppKit / Foundation / objc at sys.modules level ──────────────


def _build_mock_appkit():
    mock = MagicMock()
    mock.NSWindowStyleMaskTitled = 1
    mock.NSWindowStyleMaskClosable = 2
    mock.NSBackingStoreBuffered = 2
    mock.NSDragOperationCopy = 1
    mock.NSFilenamesPboardType = "NSFilenamesPboardType"
    return mock


def _build_mock_objc():
    mock = MagicMock()
    mock.ivar.return_value = None
    mock.selector = lambda func, signature=None: func

    class _FakeNSObject:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    # Provide a real super() that returns an object with initWithFrame_
    class _FakeNSView:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithFrame_(self, frame):
            return self

        def bounds(self):
            return MagicMock(size=MagicMock(width=280, height=200))

        def registerForDraggedTypes_(self, types):
            self._registered_types = types

        def setNeedsDisplay_(self, flag):
            self._needs_display = flag

    mock._FakeNSObject = _FakeNSObject
    mock._FakeNSView = _FakeNSView

    # Mock objc.super so DropZoneView.initWithFrame_ works
    # objc.super(ClassName, self) passes (cls, instance)
    def fake_super(cls, instance):
        parent = MagicMock()
        parent.initWithFrame_ = lambda frame: instance
        return parent

    mock.super = fake_super

    return mock


@pytest.fixture(autouse=True)
def mock_pyobjc_modules():
    """Mock AppKit, Foundation, and objc for all tests in this file."""
    mock_appkit = _build_mock_appkit()
    mock_objc = _build_mock_objc()

    mock_foundation = MagicMock()
    mock_foundation.NSObject = mock_objc._FakeNSObject
    mock_foundation.NSMakeRect = lambda x, y, w, h: (x, y, w, h)

    # Make NSView available so DropZoneView can inherit from it
    mock_appkit.NSView = mock_objc._FakeNSView

    saved = {}
    for name in ("AppKit", "Foundation", "objc"):
        saved[name] = sys.modules.get(name)

    sys.modules["AppKit"] = mock_appkit
    sys.modules["Foundation"] = mock_foundation
    sys.modules["objc"] = mock_objc

    yield {"AppKit": mock_appkit, "Foundation": mock_foundation, "objc": mock_objc}

    for name, old in saved.items():
        if old is not None:
            sys.modules[name] = old
        else:
            sys.modules.pop(name, None)


@pytest.fixture
def uw_module(mock_pyobjc_modules):
    """Import (or reimport) src.upload_window with mocked PyObjC."""
    for mod_name in list(sys.modules):
        if mod_name == "src.upload_window" or mod_name.startswith("src.upload_window."):
            del sys.modules[mod_name]
    import src.upload_window

    # Reset singletons between tests
    src.upload_window._current_window = None
    src.upload_window._current_delegate = None
    return src.upload_window


# ── show_upload: window lifecycle ─────────────────────────────────────


class TestShowUpload:
    def test_creates_window(self, uw_module, mock_pyobjc_modules):
        callback = MagicMock()
        mock_appkit = mock_pyobjc_modules["AppKit"]
        with patch.object(uw_module, "_build_window") as mock_build:
            mock_window = MagicMock()
            mock_window.isVisible.return_value = True
            mock_build.return_value = mock_window
            uw_module.show_upload(callback)

        mock_build.assert_called_once_with(callback)
        mock_window.makeKeyAndOrderFront_.assert_called_once()
        assert uw_module._current_window is mock_window
        mock_appkit.NSApp.setActivationPolicy_.assert_called()
        mock_appkit.NSApp.activateIgnoringOtherApps_.assert_called_with(True)

    def test_reuses_visible_window(self, uw_module, mock_pyobjc_modules):
        mock_window = MagicMock()
        mock_window.isVisible.return_value = True
        uw_module._current_window = mock_window
        mock_appkit = mock_pyobjc_modules["AppKit"]

        with patch.object(uw_module, "_build_window") as mock_build:
            uw_module.show_upload(MagicMock())
        mock_build.assert_not_called()
        mock_window.makeKeyAndOrderFront_.assert_called_once()
        mock_appkit.NSApp.activateIgnoringOtherApps_.assert_called_with(True)

    def test_creates_new_after_close(self, uw_module):
        mock_old = MagicMock()
        mock_old.isVisible.return_value = False
        uw_module._current_window = mock_old

        with patch.object(uw_module, "_build_window") as mock_build:
            mock_new = MagicMock()
            mock_build.return_value = mock_new
            uw_module.show_upload(MagicMock())
        mock_build.assert_called_once()


# ── DropZoneView ──────────────────────────────────────────────────────


class TestDropZoneView:
    def test_registers_drag_types(self, uw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        assert zone is not None
        assert mock_appkit.NSFilenamesPboardType in zone._registered_types

    def test_init_returns_none_when_super_fails(self, uw_module, mock_pyobjc_modules):
        mock_objc = mock_pyobjc_modules["objc"]
        original_super = mock_objc.super

        def failing_super(cls, instance):
            parent = MagicMock()
            parent.initWithFrame_ = lambda frame: None
            return parent

        mock_objc.super = failing_super
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        assert zone is None
        mock_objc.super = original_super

    def test_perform_drag_calls_on_drop_and_closes(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        callback = MagicMock()
        zone.on_drop = callback
        mock_window = MagicMock()
        zone.window_ref = mock_window

        sender = MagicMock()
        pboard = MagicMock()
        pboard.propertyListForType_.return_value = ["/path/to/audio.wav"]
        sender.draggingPasteboard.return_value = pboard

        result = zone.performDragOperation_(sender)

        assert result is True
        callback.assert_called_once_with("/path/to/audio.wav")
        mock_window.close.assert_called_once()

    def test_perform_drag_no_files(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        callback = MagicMock()
        zone.on_drop = callback
        mock_window = MagicMock()
        zone.window_ref = mock_window

        sender = MagicMock()
        pboard = MagicMock()
        pboard.propertyListForType_.return_value = []
        sender.draggingPasteboard.return_value = pboard

        result = zone.performDragOperation_(sender)

        assert result is True
        callback.assert_not_called()
        mock_window.close.assert_called_once()

    def test_perform_drag_none_files(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        callback = MagicMock()
        zone.on_drop = callback
        mock_window = MagicMock()
        zone.window_ref = mock_window

        sender = MagicMock()
        pboard = MagicMock()
        pboard.propertyListForType_.return_value = None
        sender.draggingPasteboard.return_value = pboard

        result = zone.performDragOperation_(sender)

        assert result is True
        callback.assert_not_called()
        mock_window.close.assert_called_once()

    def test_perform_drag_no_callback(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        zone.on_drop = None
        mock_window = MagicMock()
        zone.window_ref = mock_window

        sender = MagicMock()
        pboard = MagicMock()
        pboard.propertyListForType_.return_value = ["/path/to/audio.wav"]
        sender.draggingPasteboard.return_value = pboard

        result = zone.performDragOperation_(sender)

        assert result is True
        mock_window.close.assert_called_once()

    def test_perform_drag_no_window_ref(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        callback = MagicMock()
        zone.on_drop = callback
        zone.window_ref = None

        sender = MagicMock()
        pboard = MagicMock()
        pboard.propertyListForType_.return_value = ["/path/to/audio.wav"]
        sender.draggingPasteboard.return_value = pboard

        result = zone.performDragOperation_(sender)

        assert result is True
        callback.assert_called_once_with("/path/to/audio.wav")

    def test_dragging_entered_returns_copy(self, uw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        result = zone.draggingEntered_(MagicMock())
        assert result == mock_appkit.NSDragOperationCopy
        assert zone._highlight is True
        assert zone._needs_display is True

    def test_dragging_exited_clears_highlight(self, uw_module):
        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        zone._highlight = True
        zone.draggingExited_(MagicMock())
        assert zone._highlight is False
        assert zone._needs_display is True

    def test_draw_rect(self, uw_module, mock_pyobjc_modules):
        """drawRect_ runs without error (smoke test)."""
        mock_appkit = mock_pyobjc_modules["AppKit"]
        mock_path = MagicMock()
        mock_appkit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_.return_value = mock_path

        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        zone.drawRect_((0, 0, 280, 200))

        mock_path.stroke.assert_called_once()

    def test_draw_rect_highlighted(self, uw_module, mock_pyobjc_modules):
        """drawRect_ fills when highlighted."""
        mock_appkit = mock_pyobjc_modules["AppKit"]
        mock_path = MagicMock()
        mock_appkit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_.return_value = mock_path

        zone = uw_module.DropZoneView.alloc().initWithFrame_((0, 0, 280, 200))
        zone._highlight = True
        zone.drawRect_((0, 0, 280, 200))

        mock_path.fill.assert_called_once()
        mock_path.stroke.assert_called_once()


# ── UploadWindowDelegate ──────────────────────────────────────────────


class TestUploadWindowDelegate:
    def test_clears_singleton_and_reverts_policy(self, uw_module, mock_pyobjc_modules):
        uw_module._current_window = MagicMock()
        uw_module._current_delegate = MagicMock()
        mock_appkit = mock_pyobjc_modules["AppKit"]
        delegate = uw_module.UploadWindowDelegate.alloc().init()
        delegate.windowWillClose_(None)
        assert uw_module._current_window is None
        assert uw_module._current_delegate is None
        mock_appkit.NSApp.setActivationPolicy_.assert_called_with(mock_appkit.NSApplicationActivationPolicyAccessory)


# ── _build_window ─────────────────────────────────────────────────────


class TestBuildWindow:
    def test_creates_window_with_drop_zone(self, uw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        callback = MagicMock()
        window = uw_module._build_window(callback)

        assert window is mock_window
        mock_window.setTitle_.assert_called_with("Upload a Recording")
        mock_window.setReleasedWhenClosed_.assert_called_with(False)
        mock_window.setLevel_.assert_called_with(mock_appkit.NSFloatingWindowLevel)
        mock_window.setDelegate_.assert_called_once()
        mock_window.contentView().addSubview_.assert_called_once()
        assert uw_module._current_delegate is not None
