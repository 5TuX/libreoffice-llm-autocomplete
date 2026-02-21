# -*- coding: utf-8 -*-
"""
SidebarPanel.py — Unified sidebar panel factory + panel.
Collapsible API Settings and Debugging sections with slider controls.
"""

import sys
import os
import threading
import datetime

_LOG_FILE = os.path.join(os.path.expanduser("~"), "llmautocomplete_debug.log")


def _log(msg):
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().isoformat(), msg))
    except Exception:
        pass


_log("SidebarPanel.py MODULE LOADING...")

try:
    import uno
    import unohelper
    _log("uno/unohelper imported OK")
except Exception as e:
    _log("FAILED to import uno: %s" % e)

try:
    from com.sun.star.ui import XUIElementFactory
    from com.sun.star.lang import XComponent
    from com.sun.star.ui import XUIElement, XToolPanel, XSidebarPanel, LayoutSize
    from com.sun.star.ui.UIElementType import TOOLPANEL as UET_TOOLPANEL
    from com.sun.star.awt import XActionListener, XAdjustmentListener
    _log("UNO interfaces imported OK")
except Exception as e:
    _log("FAILED to import UNO interfaces: %s" % e)

_here = os.path.dirname(__file__)
_pypath = os.path.join(_here, "pythonpath")
if _pypath not in sys.path:
    sys.path.insert(0, _pypath)

from settings_store import load_settings, save_settings, DEFAULTS  # noqa: E402

EXTENSION_ID = "com.example.llmautocomplete"
DIALOG_URL = "vnd.sun.star.extension://%s/empty_dialog.xdl" % EXTENSION_ID

def _get_handler():
    """Get handler via sys module (UNO isolates module namespaces)."""
    try:
        import sys
        return getattr(sys, '_llmac_handler', None)
    except Exception:
        return None


# -- Factory ------------------------------------------------------------------

class ElementFactory(unohelper.Base, XUIElementFactory):

    def __init__(self, ctx):
        self.ctx = uno.getComponentContext()

    def createUIElement(self, url, args):
        _log("createUIElement called: url=%s" % url)
        try:
            xParentWindow = None
            xFrame = None

            for arg in args:
                if arg.Name == "Frame":
                    xFrame = arg.Value
                elif arg.Name == "ParentWindow":
                    xParentWindow = arg.Value

            xUIElement = XUIPanel(self.ctx, xFrame, xParentWindow, url)
            xUIElement.getRealInterface()
            panelWin = xUIElement.Window
            panelWin.Visible = True

            height = build_panel_ui(panelWin, xUIElement)
            xUIElement.height = height

            _log("Panel built OK, height=%d" % height)
            return xUIElement

        except Exception as e:
            _log("createUIElement error: %s" % e)
            import traceback
            traceback.print_exc()


# -- Registration -------------------------------------------------------------

g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ElementFactory,
    "com.example.llmac.PanelFactory",
    ("com.sun.star.task.Job",),
)
_log("PanelFactory registered")


# -- Panel --------------------------------------------------------------------

class XUIPanel(unohelper.Base, XSidebarPanel, XUIElement, XToolPanel, XComponent):

    def __init__(self, ctx, frame, xParentWindow, url):
        self.ctx = ctx
        self.xParentWindow = xParentWindow
        self.window = None
        self.height = 100

    def getRealInterface(self):
        if not self.window:
            provider = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.awt.ContainerWindowProvider", self.ctx
            )
            self.window = provider.createContainerWindow(
                DIALOG_URL, "", self.xParentWindow, None
            )
        return self

    @property
    def Frame(self):
        return self.frame

    @property
    def ResourceURL(self):
        return self.name

    @property
    def Type(self):
        return UET_TOOLPANEL

    def dispose(self):
        pass

    def addEventListener(self, ev):
        pass

    def removeEventListener(self, ev):
        pass

    def createAccessible(self, parent):
        return self

    @property
    def Window(self):
        return self.window

    def getHeightForWidth(self, width):
        return LayoutSize(10000, 10000, 10000)

    def getMinimalWidth(self):
        return 300


# -- Listeners ----------------------------------------------------------------

class _ButtonListener(unohelper.Base, XActionListener):
    def __init__(self, callback):
        self._cb = callback

    def actionPerformed(self, event):
        try:
            self._cb()
        except Exception as e:
            _log("_ButtonListener ERROR: %s" % e)

    def disposing(self, event):
        pass


class _SliderListener(unohelper.Base, XAdjustmentListener):
    def __init__(self, label_model, fmt):
        self._label_model = label_model
        self._fmt = fmt  # e.g. "Debounce: %d ms"

    def adjustmentValueChanged(self, event):
        try:
            self._label_model.Label = self._fmt % event.Value
        except Exception:
            pass

    def disposing(self, event):
        pass


# -- Slider definitions -------------------------------------------------------

SLIDERS = [
    # (key, display_name, min, max, step, unit)
    ("AdvanceMs",      "Advance timer",   5,   100,   5, "ms"),
    ("DebounceMs",     "Debounce",      100,  3000, 100, "ms"),
    ("PollDrainInitMs","Poll drain init",200,  5000, 100, "ms"),
    ("PollDrainMs",    "Poll drain",     50,  2000,  50, "ms"),
    ("StatusPollInitMs","Status poll init",500,10000, 500, "ms"),
    ("StatusPollMs",   "Status poll",   100,  5000, 100, "ms"),
]


# -- UI builder ---------------------------------------------------------------

def build_panel_ui(panelWin, panel):
    """Build settings UI inside panelWin. Returns total height."""
    ctx = uno.getComponentContext()
    smgr = ctx.ServiceManager

    dm = smgr.createInstance("com.sun.star.awt.UnoControlDialogModel")
    panelWin.setModel(dm)

    settings = load_settings()

    dm.Name = "LLMAutoCompleteDialog"
    dm.Width = 150
    dm.Height = 2000

    h_lbl = 10
    h_fld = 14
    gap = 4
    pad = 6
    w = 136

    # --- Layout tracking for reflow ---
    # Each item: (control_name, height, section_or_None)
    # section_or_None: None=always visible, "api"=API section, "debug"=Debug section
    layout_items = []
    sections = {
        "api":   {"collapsed": True, "btn": "btn_section_api",   "title": "API Settings"},
        "debug": {"collapsed": True, "btn": "btn_section_debug", "title": "Debugging"},
    }

    def _reflow():
        """Recompute all Y positions based on collapsed state."""
        yy = 6
        collapsed = {name for name, sec in sections.items() if sec["collapsed"]}
        for (ctrl_name, ctrl_h, section) in layout_items:
            if section and section in collapsed:
                try:
                    dm.getByName(ctrl_name).EnableVisible = False
                except Exception:
                    pass
                continue
            try:
                mdl = dm.getByName(ctrl_name)
                mdl.PositionY = yy
                if section:
                    mdl.EnableVisible = True
            except Exception:
                pass
            yy += ctrl_h
        return yy

    def add_label(name, text, bold=False, multiline=False, height=None, section=None):
        h = height or h_lbl
        mdl = dm.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        mdl.Name = name
        mdl.Label = text
        mdl.PositionX = pad
        mdl.PositionY = 0  # will be set by reflow
        mdl.Width = w
        mdl.Height = h
        if bold:
            mdl.FontWeight = 150
        if multiline:
            mdl.MultiLine = True
        dm.insertByName(name, mdl)
        layout_items.append((name, h + gap, section))

    def add_textfield(name, value, masked=False, section=None):
        mdl = dm.createInstance("com.sun.star.awt.UnoControlEditModel")
        mdl.Name = name
        mdl.Text = str(value)
        mdl.PositionX = pad
        mdl.PositionY = 0
        mdl.Width = w
        mdl.Height = h_fld
        if masked:
            mdl.EchoChar = 42
        dm.insertByName(name, mdl)
        layout_items.append((name, h_fld + gap, section))

    def add_button(name, text, height=20, section=None, bold=False):
        mdl = dm.createInstance("com.sun.star.awt.UnoControlButtonModel")
        mdl.Name = name
        mdl.Label = text
        mdl.PositionX = pad
        mdl.PositionY = 0
        mdl.Width = w
        mdl.Height = height
        if bold:
            mdl.FontWeight = 150
        dm.insertByName(name, mdl)
        layout_items.append((name, height + gap, section))

    def add_checkbox(name, text, checked=False, section=None):
        mdl = dm.createInstance("com.sun.star.awt.UnoControlCheckBoxModel")
        mdl.Name = name
        mdl.Label = text
        mdl.PositionX = pad
        mdl.PositionY = 0
        mdl.Width = w
        mdl.Height = h_lbl
        mdl.State = 1 if checked else 0
        dm.insertByName(name, mdl)
        layout_items.append((name, h_lbl + gap, section))

    def add_slider(key, display_name, value, min_val, max_val, step, unit, section=None):
        # Label: "Name: VALUE ms"
        lbl_name = "lbl_" + key
        fmt = "%s: %%d %s" % (display_name, unit)
        mdl_lbl = dm.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        mdl_lbl.Name = lbl_name
        mdl_lbl.Label = fmt % value
        mdl_lbl.PositionX = pad
        mdl_lbl.PositionY = 0
        mdl_lbl.Width = w
        mdl_lbl.Height = h_lbl
        dm.insertByName(lbl_name, mdl_lbl)
        layout_items.append((lbl_name, h_lbl, section))

        # Horizontal scrollbar
        sb_name = "sb_" + key
        mdl_sb = dm.createInstance("com.sun.star.awt.UnoControlScrollBarModel")
        mdl_sb.Name = sb_name
        mdl_sb.PositionX = pad
        mdl_sb.PositionY = 0
        mdl_sb.Width = w
        mdl_sb.Height = h_fld
        mdl_sb.Orientation = 0  # horizontal
        mdl_sb.ScrollValueMin = min_val
        mdl_sb.ScrollValueMax = max_val
        mdl_sb.ScrollValue = max(min_val, min(max_val, value))
        mdl_sb.LineIncrement = step
        mdl_sb.BlockIncrement = step * 5
        mdl_sb.VisibleSize = 0
        dm.insertByName(sb_name, mdl_sb)
        layout_items.append((sb_name, h_fld + gap, section))

        return (lbl_name, sb_name, fmt)

    # =====================================================================
    # BUILD LAYOUT (top to bottom)
    # =====================================================================

    # -- Enable/Disable toggle --
    handler = _get_handler()
    is_enabled = handler.enabled if handler is not None else True
    btn_label = "Disable Autocomplete" if is_enabled else "Enable Autocomplete"
    add_button("btn_toggle", btn_label)

    # -- Shortcuts help --
    shortcuts_text = "Right Arrow: accept all\nCtrl+Right: accept word\nEscape: dismiss"
    add_label("lbl_shortcuts", shortcuts_text if is_enabled else "",
              multiline=True, height=h_lbl * 3)

    # -- Status label --
    add_label("status_label", "Starting...")

    # -- Single sentence checkbox --
    add_checkbox("chk_single_sentence", "Single sentence only",
                 settings.get("SingleSentence", True))

    # -- API Settings section toggle --
    add_button("btn_section_api", "+ API Settings", height=16, bold=True)

    # -- API Settings fields (hidden by default) --
    add_label("lbl_api_key", "API Key:", section="api")
    add_textfield("api_key", settings.get("ApiKey", ""), masked=True, section="api")
    add_label("lbl_model", "Model:", section="api")
    add_textfield("model", settings.get("Model", ""), section="api")
    add_label("lbl_base_url", "Base URL:", section="api")
    add_textfield("base_url", settings.get("BaseUrl", ""), section="api")
    add_label("lbl_max_tokens", "Max tokens:", section="api")
    add_textfield("max_tokens", settings.get("MaxTokens", 80), section="api")
    add_label("lbl_max_ctx", "Max context chars:", section="api")
    add_textfield("max_context_chars", settings.get("MaxContextChars", 500), section="api")

    # -- Debugging section toggle --
    add_button("btn_section_debug", "+ Debugging", height=16, bold=True)

    # -- Slider controls (hidden by default) --
    slider_info = []  # (lbl_name, sb_name, fmt, key) for wiring + reset
    for (key, display_name, min_val, max_val, step, unit) in SLIDERS:
        value = settings.get(key, DEFAULTS.get(key, min_val))
        info = add_slider(key, display_name, value, min_val, max_val, step, unit,
                          section="debug")
        slider_info.append(info + (key,))

    # -- Reset Defaults button --
    add_button("btn_reset_delays", "Reset Defaults", height=16, section="debug")

    # -- Save Settings (always visible) --
    add_button("btn_save", "Save Settings")

    # =====================================================================
    # INITIAL REFLOW (sections collapsed)
    # =====================================================================
    final_y = _reflow()

    # =====================================================================
    # WIRE LISTENERS
    # =====================================================================

    # -- Toggle button --
    def on_toggle():
        try:
            h = _get_handler()
            if h is not None:
                h.enabled = not h.enabled
                if not h.enabled:
                    h.dismiss_suggestion()
                lbl = "Disable Autocomplete" if h.enabled else "Enable Autocomplete"
                panelWin.getControl("btn_toggle").getModel().Label = lbl
                sc = "Right Arrow: accept all\nCtrl+Right: accept word\nEscape: dismiss" if h.enabled else ""
                panelWin.getControl("lbl_shortcuts").getModel().Label = sc
        except Exception as e:
            _log("on_toggle ERROR: %s" % e)

    try:
        panelWin.getControl("btn_toggle").addActionListener(_ButtonListener(on_toggle))
    except Exception as e:
        _log("Wire toggle ERROR: %s" % e)

    # -- Section toggles --
    def _make_section_toggler(section_name):
        def toggler():
            sec = sections[section_name]
            sec["collapsed"] = not sec["collapsed"]
            prefix = "+" if sec["collapsed"] else "-"
            try:
                dm.getByName(sec["btn"]).Label = "%s %s" % (prefix, sec["title"])
            except Exception:
                pass
            _reflow()
        return toggler

    try:
        panelWin.getControl("btn_section_api").addActionListener(
            _ButtonListener(_make_section_toggler("api")))
        panelWin.getControl("btn_section_debug").addActionListener(
            _ButtonListener(_make_section_toggler("debug")))
    except Exception as e:
        _log("Wire section toggles ERROR: %s" % e)

    # -- Slider adjustment listeners --
    for (lbl_name, sb_name, fmt, key) in slider_info:
        try:
            sb_ctrl = panelWin.getControl(sb_name)
            lbl_mdl = dm.getByName(lbl_name)
            sb_ctrl.addAdjustmentListener(_SliderListener(lbl_mdl, fmt))
        except Exception as e:
            _log("Wire slider %s ERROR: %s" % (key, e))

    # -- Reset Defaults --
    def on_reset():
        for (lbl_name, sb_name, fmt, key) in slider_info:
            default = DEFAULTS.get(key, 0)
            try:
                panelWin.getControl(sb_name).setValue(default)
                dm.getByName(lbl_name).Label = fmt % default
            except Exception as e:
                _log("Reset %s ERROR: %s" % (key, e))

    try:
        panelWin.getControl("btn_reset_delays").addActionListener(
            _ButtonListener(on_reset))
    except Exception as e:
        _log("Wire reset ERROR: %s" % e)

    # -- Save --
    def on_save():
        try:
            def _get_text(name):
                try:
                    return panelWin.getControl(name).getText()
                except Exception:
                    return ""

            def _get_int(name, default):
                try:
                    return int(panelWin.getControl(name).getText())
                except Exception:
                    return default

            def _get_check(name):
                try:
                    return panelWin.getControl(name).getState() == 1
                except Exception:
                    return False

            def _get_slider(key, default):
                try:
                    return int(panelWin.getControl("sb_" + key).getValue())
                except Exception:
                    return default

            new_settings = {
                "ApiKey":          _get_text("api_key"),
                "Model":           _get_text("model"),
                "BaseUrl":         _get_text("base_url"),
                "MaxTokens":       _get_int("max_tokens", 80),
                "MaxContextChars": _get_int("max_context_chars", 500),
                "SingleSentence":  _get_check("chk_single_sentence"),
                "AdvanceMs":       _get_slider("AdvanceMs", 20),
                "DebounceMs":      _get_slider("DebounceMs", 600),
                "PollDrainInitMs": _get_slider("PollDrainInitMs", 1000),
                "PollDrainMs":     _get_slider("PollDrainMs", 300),
                "StatusPollInitMs":_get_slider("StatusPollInitMs", 2000),
                "StatusPollMs":    _get_slider("StatusPollMs", 500),
            }
            save_settings(ctx, new_settings)
            h = _get_handler()
            if h is not None:
                h.update_settings(new_settings)
            try:
                panelWin.getControl("status_label").setText("Saved!")
            except Exception:
                pass
        except Exception as e:
            _log("save error: %s" % e)

    try:
        panelWin.getControl("btn_save").addActionListener(_ButtonListener(on_save))
    except Exception:
        pass

    # -- Status polling --
    _last_status = [None]
    _status_ctrl = [None]
    try:
        _status_ctrl[0] = panelWin.getControl("status_label")
    except Exception:
        pass

    poll_interval = settings.get("StatusPollMs", 500) / 1000.0
    poll_init = settings.get("StatusPollInitMs", 2000) / 1000.0

    def poll():
        try:
            h = _get_handler()
            if h is not None and _status_ctrl[0] is not None:
                status = h.get_status()
                if status != _last_status[0]:
                    _last_status[0] = status
                    try:
                        _status_ctrl[0].setText(status)
                    except Exception:
                        pass
        except Exception:
            pass
        t = threading.Timer(poll_interval, poll)
        t.daemon = True
        t.start()

    poll_timer = threading.Timer(poll_init, poll)
    poll_timer.daemon = True
    poll_timer.start()

    return final_y + 10
