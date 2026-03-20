"""Tests for src/settings_window.py.

AppKit, Foundation, and objc must be mocked in sys.modules before
importing src.settings_window, because the module-level code uses them.
"""

import sys
from unittest.mock import MagicMock, call, patch

import pytest

from src.config import Config

# ── Mock AppKit / Foundation / objc at sys.modules level ──────────────


def _build_mock_appkit():
    mock = MagicMock()
    mock.NSTextAlignmentRight = 2
    mock.NSWindowStyleMaskTitled = 1
    mock.NSWindowStyleMaskClosable = 2
    mock.NSBackingStoreBuffered = 2
    mock.NSBezelStyleRounded = 1
    return mock


def _build_mock_objc():
    mock = MagicMock()
    # objc.ivar() must return a sentinel that allows instance attribute override
    mock.ivar.return_value = None
    mock.selector = lambda func, signature=None: func

    # Provide a real NSObject subclass so SettingsWindowDelegate
    # can be instantiated as a normal Python class.
    class _FakeNSObject:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    mock._FakeNSObject = _FakeNSObject
    return mock


@pytest.fixture(autouse=True)
def mock_pyobjc_modules():
    """Mock AppKit, Foundation, and objc for all tests in this file."""
    mock_appkit = _build_mock_appkit()
    mock_objc = _build_mock_objc()

    mock_foundation = MagicMock()
    mock_foundation.NSObject = mock_objc._FakeNSObject
    mock_foundation.NSMakeRect = lambda x, y, w, h: (x, y, w, h)

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
def sw_module(mock_pyobjc_modules):
    """Import (or reimport) src.settings_window with mocked PyObjC."""
    for mod_name in list(sys.modules):
        if mod_name == "src.settings_window" or mod_name.startswith("src.settings_window."):
            del sys.modules[mod_name]
    import src.settings_window

    # Reset singletons between tests
    src.settings_window._current_window = None
    src.settings_window._current_delegate = None
    return src.settings_window


# ── show_settings: window lifecycle ───────────────────────────────────


class TestShowSettings:
    def test_creates_window(self, sw_module, mock_pyobjc_modules):
        config = Config(gemini_api_key="k", notion_token="t")
        callback = MagicMock()
        mock_appkit = mock_pyobjc_modules["AppKit"]
        with patch.object(sw_module, "_build_window") as mock_build:
            mock_window = MagicMock()
            mock_window.isVisible.return_value = True
            mock_build.return_value = mock_window
            sw_module.show_settings(config, callback)

        mock_build.assert_called_once_with(config, callback, None)
        mock_window.makeKeyAndOrderFront_.assert_called_once()
        assert sw_module._current_window is mock_window
        mock_appkit.NSApp.setActivationPolicy_.assert_called()
        mock_appkit.NSApp.activateIgnoringOtherApps_.assert_called_with(True)

    def test_reuses_visible_window(self, sw_module, mock_pyobjc_modules):
        mock_window = MagicMock()
        mock_window.isVisible.return_value = True
        sw_module._current_window = mock_window
        mock_appkit = mock_pyobjc_modules["AppKit"]

        with patch.object(sw_module, "_build_window") as mock_build:
            sw_module.show_settings(Config(), MagicMock())
        mock_build.assert_not_called()
        mock_window.makeKeyAndOrderFront_.assert_called_once()
        mock_appkit.NSApp.activateIgnoringOtherApps_.assert_called_with(True)

    def test_creates_new_after_close(self, sw_module):
        mock_old = MagicMock()
        mock_old.isVisible.return_value = False
        sw_module._current_window = mock_old

        with patch.object(sw_module, "_build_window") as mock_build:
            mock_new = MagicMock()
            mock_build.return_value = mock_new
            sw_module.show_settings(Config(), MagicMock())
        mock_build.assert_called_once()

    def test_passes_gemini_client(self, sw_module):
        client = MagicMock()
        with patch.object(sw_module, "_build_window") as mock_build:
            mock_build.return_value = MagicMock()
            sw_module.show_settings(Config(), MagicMock(), gemini_client=client)
        assert mock_build.call_args[0][2] is client


# ── _build_window: field population ──────────────────────────────────


class TestBuildWindow:
    def test_fields_populated_from_config(self, sw_module, mock_pyobjc_modules):
        config = Config(
            gemini_api_key="my-key",
            gemini_model="gemini-2.5-pro",
            notion_token="my-token",
            notion_database_id="my-db",
        )
        mock_appkit = mock_pyobjc_modules["AppKit"]

        # Track NSTextField and NSSecureTextField instantiations
        fields_created = []

        def track_field(cls_name):
            def alloc():
                field = MagicMock()
                field._cls_name = cls_name
                field.initWithFrame_.return_value = field
                fields_created.append(field)
                return field

            return alloc

        mock_appkit.NSSecureTextField.alloc = track_field("NSSecureTextField")
        mock_appkit.NSTextField.alloc = track_field("NSTextField")

        # Mock popup
        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        # Mock buttons
        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        # Mock window
        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        sw_module._build_window(config, MagicMock(), gemini_client=None)

        # Verify secure fields got correct values
        secure_fields = [f for f in fields_created if f._cls_name == "NSSecureTextField"]
        assert len(secure_fields) == 2
        # API key field
        secure_fields[0].setStringValue_.assert_called_with("my-key")
        # Notion token field
        secure_fields[1].setStringValue_.assert_called_with("my-token")

    def test_secure_fields_for_api_key_and_token(self, sw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]

        secure_count = 0
        plain_count = 0

        def count_secure():
            nonlocal secure_count
            secure_count += 1
            field = MagicMock()
            field.initWithFrame_.return_value = field
            return field

        def count_plain():
            nonlocal plain_count
            plain_count += 1
            field = MagicMock()
            field.initWithFrame_.return_value = field
            return field

        mock_appkit.NSSecureTextField.alloc = count_secure
        mock_appkit.NSTextField.alloc = count_plain

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        sw_module._build_window(Config(), MagicMock(), gemini_client=None)

        assert secure_count == 2  # api_key + notion_token
        # plain fields: 4 labels + 1 db_id field = 5 total NSTextField allocs
        assert plain_count >= 1  # at least the db_id field

    def test_model_dropdown_no_client(self, sw_module, mock_pyobjc_modules):
        """Without gemini_client, dropdown gets Auto + MODEL_TIER_ORDER."""
        mock_appkit = mock_pyobjc_modules["AppKit"]
        from src.model_selector import MODEL_TIER_ORDER

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        sw_module._build_window(Config(), MagicMock(), gemini_client=None)

        add_calls = mock_popup.addItemWithTitle_.call_args_list
        titles = [c[0][0] for c in add_calls]
        assert titles[0] == "Auto (recommended)"
        assert titles[1:] == list(MODEL_TIER_ORDER)

    def test_model_dropdown_with_client(self, sw_module, mock_pyobjc_modules):
        """With gemini_client, dropdown gets Auto + discovered models."""
        mock_appkit = mock_pyobjc_modules["AppKit"]

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        client = MagicMock()
        with (
            patch.object(
                sw_module,
                "_discover_models",
                return_value=["gemini-2.5-flash", "gemini-2.0-flash"],
            ),
            patch.object(
                sw_module,
                "_sort_by_tier",
                return_value=["gemini-2.5-flash", "gemini-2.0-flash"],
            ),
        ):
            sw_module._build_window(Config(), MagicMock(), gemini_client=client)

        add_calls = mock_popup.addItemWithTitle_.call_args_list
        titles = [c[0][0] for c in add_calls]
        assert titles == ["Auto (recommended)", "gemini-2.5-flash", "gemini-2.0-flash"]

    def test_model_dropdown_client_error_fallback(self, sw_module, mock_pyobjc_modules):
        """If discover_models raises, falls back to MODEL_TIER_ORDER."""
        mock_appkit = mock_pyobjc_modules["AppKit"]
        from src.model_selector import MODEL_TIER_ORDER

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        client = MagicMock()
        with patch.object(sw_module, "_discover_models", side_effect=RuntimeError("API error")):
            sw_module._build_window(Config(), MagicMock(), gemini_client=client)

        add_calls = mock_popup.addItemWithTitle_.call_args_list
        titles = [c[0][0] for c in add_calls]
        assert titles[0] == "Auto (recommended)"
        assert titles[1:] == list(MODEL_TIER_ORDER)

    def test_current_model_preselected(self, sw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        config = Config(gemini_model="gemini-2.5-flash")
        sw_module._build_window(config, MagicMock(), gemini_client=None)

        mock_popup.selectItemWithTitle_.assert_called_with("gemini-2.5-flash")

    def test_unknown_model_selects_auto(self, sw_module, mock_pyobjc_modules):
        mock_appkit = mock_pyobjc_modules["AppKit"]

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        mock_btn = MagicMock()
        mock_btn.initWithFrame_.return_value = mock_btn
        mock_appkit.NSButton.alloc.return_value = mock_btn

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        config = Config(gemini_model="nonexistent-model")
        sw_module._build_window(config, MagicMock(), gemini_client=None)

        mock_popup.selectItemWithTitle_.assert_called_with("Auto (recommended)")


# ── SettingsWindowDelegate: save/cancel ──────────────────────────────


class TestDelegateSave:
    def test_save_auto_model(self, sw_module):
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        delegate.window = MagicMock()
        delegate.api_key_field = MagicMock()
        delegate.api_key_field.stringValue.return_value = "key1"
        delegate.model_popup = MagicMock()
        delegate.model_popup.titleOfSelectedItem.return_value = "Auto (recommended)"
        delegate.notion_token_field = MagicMock()
        delegate.notion_token_field.stringValue.return_value = "tok1"
        delegate.db_id_field = MagicMock()
        delegate.db_id_field.stringValue.return_value = "db1"
        delegate.on_save = MagicMock()

        delegate.saveClicked_(None)

        saved_config = delegate.on_save.call_args[0][0]
        assert saved_config.gemini_api_key == "key1"
        assert saved_config.gemini_model == ""
        assert saved_config.notion_token == "tok1"
        assert saved_config.notion_database_id == "db1"
        delegate.window.close.assert_called_once()

    def test_save_specific_model(self, sw_module):
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        delegate.window = MagicMock()
        delegate.api_key_field = MagicMock()
        delegate.api_key_field.stringValue.return_value = "key2"
        delegate.model_popup = MagicMock()
        delegate.model_popup.titleOfSelectedItem.return_value = "gemini-2.5-pro"
        delegate.notion_token_field = MagicMock()
        delegate.notion_token_field.stringValue.return_value = "tok2"
        delegate.db_id_field = MagicMock()
        delegate.db_id_field.stringValue.return_value = "db2"
        delegate.on_save = MagicMock()

        delegate.saveClicked_(None)

        saved_config = delegate.on_save.call_args[0][0]
        assert saved_config.gemini_model == "gemini-2.5-pro"

    def test_save_calls_callback_and_closes(self, sw_module):
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        delegate.window = MagicMock()
        delegate.api_key_field = MagicMock()
        delegate.api_key_field.stringValue.return_value = ""
        delegate.model_popup = MagicMock()
        delegate.model_popup.titleOfSelectedItem.return_value = "Auto (recommended)"
        delegate.notion_token_field = MagicMock()
        delegate.notion_token_field.stringValue.return_value = ""
        delegate.db_id_field = MagicMock()
        delegate.db_id_field.stringValue.return_value = ""
        callback = MagicMock()
        delegate.on_save = callback

        delegate.saveClicked_(None)

        callback.assert_called_once()
        delegate.window.close.assert_called_once()


class TestDelegateCancel:
    def test_cancel_closes_without_callback(self, sw_module):
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        delegate.window = MagicMock()
        delegate.on_save = MagicMock()

        delegate.cancelClicked_(None)

        delegate.on_save.assert_not_called()
        delegate.window.close.assert_called_once()


class TestDelegateHelp:
    def test_help_opens_url_from_tooltip(self, sw_module):
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        sender = MagicMock()
        sender.toolTip.return_value = "https://example.com/help"

        with patch.object(sw_module.webbrowser, "open") as mock_open:
            delegate.helpClicked_(sender)

        mock_open.assert_called_once_with("https://example.com/help")


class TestHelpButtons:
    def test_help_buttons_added_for_credentials(self, sw_module, mock_pyobjc_modules):
        """Help buttons are created for gemini_api_key and notion_token rows."""
        mock_appkit = mock_pyobjc_modules["AppKit"]

        # Track NSButton allocs to count help buttons
        buttons_created = []

        def track_btn():
            btn = MagicMock()
            btn.initWithFrame_.return_value = btn
            buttons_created.append(btn)
            return btn

        mock_appkit.NSButton.alloc = track_btn

        mock_popup = MagicMock()
        mock_popup.initWithFrame_pullsDown_.return_value = mock_popup
        mock_appkit.NSPopUpButton.alloc.return_value = mock_popup

        field = MagicMock()
        field.initWithFrame_.return_value = field
        mock_appkit.NSSecureTextField.alloc.return_value = field
        mock_appkit.NSTextField.alloc.return_value = field

        mock_window = MagicMock()
        mock_window.initWithContentRect_styleMask_backing_defer_.return_value = mock_window
        mock_appkit.NSWindow.alloc.return_value = mock_window

        sw_module._build_window(Config(), MagicMock(), gemini_client=None)

        # 2 help buttons + Save + Cancel = 4 NSButton allocs
        assert len(buttons_created) == 4
        # Help buttons have setBezelStyle_ called with NSBezelStyleHelpButton
        help_btns = [
            b
            for b in buttons_created
            if any(c == call(mock_appkit.NSBezelStyleHelpButton) for c in b.setBezelStyle_.call_args_list)
        ]
        assert len(help_btns) == 2

        # Verify tooltips contain the expected URLs
        tooltips = [b.setToolTip_.call_args[0][0] for b in help_btns]
        assert "https://aistudio.google.com/api-keys" in tooltips
        assert "https://www.notion.so/profile/integrations/internal" in tooltips


class TestDelegateWindowClose:
    def test_clears_current_window_and_reverts_policy(self, sw_module, mock_pyobjc_modules):
        sw_module._current_window = MagicMock()
        sw_module._current_delegate = MagicMock()
        mock_appkit = mock_pyobjc_modules["AppKit"]
        delegate = sw_module.SettingsWindowDelegate.alloc().init()
        delegate.windowWillClose_(None)
        assert sw_module._current_window is None
        assert sw_module._current_delegate is None
        mock_appkit.NSApp.setActivationPolicy_.assert_called_with(mock_appkit.NSApplicationActivationPolicyAccessory)
