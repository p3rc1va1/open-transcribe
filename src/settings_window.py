"""Native macOS settings window built with PyObjC."""

import logging
import webbrowser

import AppKit
import objc
from Foundation import NSObject, NSMakeRect

from src.config import Config
from src.model_selector import MODEL_TIER_ORDER, _discover_models, _sort_by_tier

log = logging.getLogger("open-transcribe")

_current_window = None
_current_delegate = None

AUTO_LABEL = "Auto (recommended)"

# Layout constants
WIN_WIDTH = 510
WIN_HEIGHT = 380
LABEL_X = 20
LABEL_W = 150
FIELD_X = 180
FIELD_W = 270
ROW_HEIGHT = 32
BUTTON_W = 80
BUTTON_H = 32
HELP_BTN_SIZE = 20

HELP_URLS = {
    "gemini_api_key": "https://aistudio.google.com/api-keys",
    "notion_token": "https://www.notion.so/profile/integrations/internal",
}


def _make_help_button(y, url, delegate):
    """Create a small help button (?) that opens a URL when clicked."""
    btn = AppKit.NSButton.alloc().initWithFrame_(
        NSMakeRect(FIELD_X + FIELD_W + 4, y + 1, HELP_BTN_SIZE, HELP_BTN_SIZE)
    )
    btn.setBezelStyle_(AppKit.NSBezelStyleHelpButton)
    btn.setTitle_("")
    btn.setTarget_(delegate)
    btn.setAction_(objc.selector(delegate.helpClicked_, signature=b"v@:@"))
    btn.setToolTip_(url)
    return btn


def _make_label(y, text):
    """Create a right-aligned, non-editable label at the given y position."""
    label = AppKit.NSTextField.alloc().initWithFrame_(
        NSMakeRect(LABEL_X, y, LABEL_W, 24)
    )
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setAlignment_(AppKit.NSTextAlignmentRight)
    return label


def _make_field(y, secure, placeholder, value):
    """Create a text field (plain or secure) at the given y position."""
    cls = AppKit.NSSecureTextField if secure else AppKit.NSTextField
    field = cls.alloc().initWithFrame_(NSMakeRect(FIELD_X, y, FIELD_W, 24))
    field.setEditable_(True)
    field.setBezeled_(True)
    field.setStringValue_(value or "")
    field.setPlaceholderString_(placeholder or "")
    return field


def _build_window(config, on_save, gemini_client):
    """Construct the settings NSWindow with labeled rows and buttons."""
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(200, 200, WIN_WIDTH, WIN_HEIGHT),
        AppKit.NSWindowStyleMaskTitled
        | AppKit.NSWindowStyleMaskClosable,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Open Transcribe Settings")
    window.setReleasedWhenClosed_(False)
    window.setLevel_(AppKit.NSFloatingWindowLevel)
    content = window.contentView()

    # Create delegate early so help buttons can reference it as target
    delegate = SettingsWindowDelegate.alloc().init()
    delegate.window = window
    delegate.on_save = on_save

    # Row y positions (top to bottom)
    rows = [280, 240, 195, 155]

    # Row 0: Gemini API Key
    content.addSubview_(_make_label(rows[0], "Gemini API Key:"))
    api_key_field = _make_field(rows[0], secure=True, placeholder="Enter API key", value=config.gemini_api_key)
    content.addSubview_(api_key_field)
    content.addSubview_(_make_help_button(rows[0], HELP_URLS["gemini_api_key"], delegate))

    # Row 1: Gemini Model (dropdown)
    content.addSubview_(_make_label(rows[1], "Gemini Model:"))
    model_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
        NSMakeRect(FIELD_X, rows[1], FIELD_W, 28), False
    )
    model_popup.addItemWithTitle_(AUTO_LABEL)

    # Populate model list
    if gemini_client:
        try:
            discovered = _discover_models(gemini_client)
            model_names = _sort_by_tier(discovered)
        except Exception:
            model_names = list(MODEL_TIER_ORDER)
    else:
        model_names = list(MODEL_TIER_ORDER)

    for name in model_names:
        model_popup.addItemWithTitle_(name)

    # Pre-select current model
    if config.gemini_model and config.gemini_model in model_names:
        model_popup.selectItemWithTitle_(config.gemini_model)
    else:
        model_popup.selectItemWithTitle_(AUTO_LABEL)

    content.addSubview_(model_popup)

    # Row 2: Notion Token
    content.addSubview_(_make_label(rows[2], "Notion Token:"))
    notion_token_field = _make_field(rows[2], secure=True, placeholder="Enter token", value=config.notion_token)
    content.addSubview_(notion_token_field)
    content.addSubview_(_make_help_button(rows[2], HELP_URLS["notion_token"], delegate))

    # Row 3: Notion Database ID
    content.addSubview_(_make_label(rows[3], "Notion Database ID:"))
    db_id_field = _make_field(rows[3], secure=False, placeholder="Enter database ID", value=config.notion_database_id)
    content.addSubview_(db_id_field)

    # Buttons
    save_btn = AppKit.NSButton.alloc().initWithFrame_(
        NSMakeRect(WIN_WIDTH - BUTTON_W - 20, 20, BUTTON_W, BUTTON_H)
    )
    save_btn.setTitle_("Save")
    save_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
    content.addSubview_(save_btn)

    cancel_btn = AppKit.NSButton.alloc().initWithFrame_(
        NSMakeRect(WIN_WIDTH - 2 * BUTTON_W - 30, 20, BUTTON_W, BUTTON_H)
    )
    cancel_btn.setTitle_("Cancel")
    cancel_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
    content.addSubview_(cancel_btn)

    # Wire remaining delegate fields and button targets
    delegate.api_key_field = api_key_field
    delegate.model_popup = model_popup
    delegate.notion_token_field = notion_token_field
    delegate.db_id_field = db_id_field

    save_btn.setTarget_(delegate)
    save_btn.setAction_(objc.selector(delegate.saveClicked_, signature=b"v@:@"))
    cancel_btn.setTarget_(delegate)
    cancel_btn.setAction_(objc.selector(delegate.cancelClicked_, signature=b"v@:@"))

    window.setDelegate_(delegate)
    # prevent delegate from being garbage collected
    global _current_delegate
    _current_delegate = delegate

    return window


class SettingsWindowDelegate(NSObject):
    """Handles Save/Cancel button clicks and window close."""

    window = objc.ivar()
    api_key_field = objc.ivar()
    model_popup = objc.ivar()
    notion_token_field = objc.ivar()
    db_id_field = objc.ivar()
    on_save = objc.ivar()

    def saveClicked_(self, sender):
        model_title = self.model_popup.titleOfSelectedItem()
        gemini_model = "" if model_title == AUTO_LABEL else model_title

        config = Config(
            gemini_api_key=self.api_key_field.stringValue(),
            gemini_model=gemini_model,
            notion_token=self.notion_token_field.stringValue(),
            notion_database_id=self.db_id_field.stringValue(),
        )
        self.on_save(config)
        self.window.close()

    def cancelClicked_(self, sender):
        self.window.close()

    def helpClicked_(self, sender):
        url = sender.toolTip()
        webbrowser.open(url)

    def windowWillClose_(self, notification):
        global _current_window, _current_delegate
        _current_window = None
        _current_delegate = None
        # Revert to accessory app (no Dock icon, no cmd-tab entry)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


def show_settings(config, on_save, gemini_client=None):
    """Show the settings window. Reuses existing window if visible."""
    global _current_window

    # Become a regular app so the window is cmd-tab accessible
    AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    if _current_window is not None and _current_window.isVisible():
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        _current_window.makeKeyAndOrderFront_(None)
        return

    window = _build_window(config, on_save, gemini_client)
    _current_window = window
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
