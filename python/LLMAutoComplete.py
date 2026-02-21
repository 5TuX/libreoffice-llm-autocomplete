"""
LLMAutoComplete.py — XModifyListener + XDispatchProviderInterceptor.

v17b: Intercept .uno:GoRight dispatch command. When ghost active, accept
instead of moving cursor. XKeyHandler kept for Escape only.
"""

import sys
import os
import threading
import queue
import datetime
import traceback

_LOG_FILE = os.path.join(os.path.expanduser("~"), "llmautocomplete_debug.log")


def _log(msg):
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().isoformat(), msg))
    except Exception:
        pass


_log("LLMAutoComplete.py MODULE LOADING...")

import uno
import unohelper

from com.sun.star.util import XModifyListener
from com.sun.star.awt import XKeyHandler
from com.sun.star.document import XEventListener
from com.sun.star.task import XJob
from com.sun.star.lang import XServiceInfo
from com.sun.star.frame import XDispatchProviderInterceptor, XDispatch, XDispatchProvider, XTerminateListener
from com.sun.star.util import XCloseListener

_log("LLMAutoComplete.py All imports OK")

_here = os.path.dirname(__file__)
_pypath = os.path.join(_here, "pythonpath")
if _pypath not in sys.path:
    sys.path.insert(0, _pypath)

from llm_client import LLMClient  # noqa: E402
from settings_store import load_settings, save_settings  # noqa: E402

GHOST_STYLE = "LLMSuggestion"
GHOST_COLOR = 0xAAAAAA
AI_STYLE = "AIGenerated"
AI_HIGHLIGHT_COLOR = 0xC8F7C5  # pastel green


def _lighten_color(color, factor=0.6):
    """Blend color toward white. Black text gets fixed gray instead."""
    if color == 0x000000 or color < 0:
        return GHOST_COLOR
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return (r << 16) | (g << 8) | b


def _truncate_sentence(text):
    """Truncate text after first sentence-ending punctuation."""
    for i, ch in enumerate(text):
        if ch in ".!?":
            return text[:i + 1]
    return text

# Key codes (com.sun.star.awt.Key)
# Windows LO quirk: Ctrl+Left=1026+CTRL, Ctrl+Right=1027+CTRL (not normal arrow codes)
KEY_RIGHT = 1026
KEY_DOWN = 1027  # Ctrl+Right reports as 1027 in XKeyHandler on Windows LO
KEY_TAB = 1282
KEY_ESCAPE = 1281
MOD_CTRL = 2


class AutoCompleteHandler(unohelper.Base, XModifyListener, XKeyHandler):

    def __init__(self, ctx, settings):
        self.ctx = ctx
        self.settings = dict(settings)
        self._ghost_text = ""
        self._ghost_len = 0
        self._ghost_cursor = None
        self._inserting_ghost = False
        self._advancing = False
        self._advance_timer = None
        self._timer = None
        self._ui_queue = queue.Queue()
        self._client = None
        self._query_count = 0
        self._querying = False
        self._last_error = ""
        self._status_label = None
        self._lock = threading.Lock()
        self.enabled = True
        self._last_text = ""
        self._doc_ref = None
        self._saved_color = None
        self._debounce_context = None
        self._request_context = None
        self._accepted_words = []  # stack of accepted word strings for Ctrl+Left undo
        self._cleanup_next_char = False  # strip AI style from the next typed char
        self._rebuild_client()

    # -- Public API ----------------------------------------------------------

    def update_settings(self, settings):
        with self._lock:
            self.settings = dict(settings)
            self._rebuild_client()

    def get_status(self):
        if not self.enabled:
            return "OFF | %d queries" % self._query_count
        if self._client is None:
            return "No API key"
        if self._querying:
            return "Querying... | %d queries" % self._query_count
        if self._ghost_len > 0:
            return "Ghost: %d chars | %d queries" % (self._ghost_len, self._query_count)
        if self._last_error:
            return "%s | %d queries" % (self._last_error, self._query_count)
        if self._query_count > 0:
            return "Idle | %d queries" % self._query_count
        return "Idle"

    def _update_status_label(self):
        try:
            if self._status_label is not None:
                self._status_label.Label = self.get_status()
        except Exception:
            pass

    def accept_suggestion(self):
        _log("accept_suggestion called, ghost_len=%d" % self._ghost_len)
        if self._ghost_len <= 0:
            return
        self._accept_ghost()

    def dismiss_suggestion(self):
        if self._ghost_len > 0:
            self._remove_ghost()

    def cancel_debounce(self):
        """Cancel pending debounce timer (called on cursor navigation)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # -- XKeyHandler ---------------------------------------------------------

    def keyPressed(self, event):
        try:
            key = event.KeyCode
            mods = event.Modifiers
            _log("keyPressed: code=%d mods=%d ghost=%d enabled=%s" % (
                key, mods, self._ghost_len, self.enabled))
            if not self.enabled:
                return False
            # Note: XKeyHandler reports KeyCode=1026 for BOTH Left and Right Arrow
            # on LO Writer/Windows. So we CANNOT use keyPressed for Right Arrow accept.
            # GoRightDispatch handles Right Arrow accept via dispatch interceptor.
            if self._ghost_len > 0:
                if key == KEY_DOWN and (mods & MOD_CTRL):
                    _log("ACCEPT word (Ctrl+Right via keyPressed code=1027)")
                    self._accept_ghost_word()
                    return True
                if key == KEY_RIGHT and (mods & MOD_CTRL):
                    if self._accepted_words:
                        _log("UN-ACCEPT word (Ctrl+Left via keyPressed code=%d)" % key)
                        self._unaccept_ghost_word()
                        return True
                    else:
                        _log("DISMISS ghost (Ctrl+Left, stack empty)")
                        self._remove_ghost()
                        self._last_text = self._get_prefix_text()
                        return False  # let default Ctrl+Left navigation happen
                if key == KEY_TAB and (mods & MOD_CTRL):
                    _log("ACCEPT ghost (Ctrl+Tab)")
                    self._accept_ghost()
                    return True
                if key == KEY_ESCAPE:
                    _log("DISMISS ghost (Escape)")
                    self._remove_ghost()
                    self._last_text = self._get_prefix_text()
                    return True
            return False
        except Exception as e:
            _log("keyPressed ERROR: %s" % e)
            return False

    def keyReleased(self, event):
        return False

    # -- XModifyListener -----------------------------------------------------

    def modified(self, event):
        try:
            if not self.enabled:
                return
            if self._inserting_ghost:
                return
            if self._advancing:
                _log("modified() skipped (advancing)")
                return
            _log("modified() fired")
            if self._ghost_len > 0:
                self._handle_modification_with_ghost()
            else:
                self._strip_leaked_style()
                self._reset_debounce()
        except Exception as e:
            _log("modified() ERROR: %s" % e)

    def disposing(self, source):
        _log("Handler disposing")

    # -- Ghost text logic ----------------------------------------------------

    def _handle_modification_with_ghost(self):
        try:
            doc = self._get_doc()
            if doc is None:
                return
            current_ghost = self._ghost_text
            self._ghost_cursor = None  # force position-based removal; stored cursor absorbs typed chars
            self._remove_ghost()
            new_text = self._get_prefix_text()
            old_text = self._last_text
            if not old_text or not new_text:
                self._reset_debounce()
                return
            if new_text.startswith(old_text) and len(new_text) > len(old_text):
                typed = new_text[len(old_text):]
                _log("User typed: %r, ghost starts with: %r" % (
                    typed, current_ghost[:len(typed)] if current_ghost else ""))
                if current_ghost.startswith(typed):
                    remaining = current_ghost[len(typed):]
                    self._last_text = new_text
                    # Tag write-through chars with AI style
                    # Wrap in _inserting_ghost so modified() from style changes is suppressed
                    self._inserting_ghost = True
                    try:
                        ctrl = doc.getCurrentController()
                        vc = ctrl.getViewCursor()
                        text_obj = doc.getText()
                        ai_cursor = text_obj.createTextCursorByRange(vc.getStart())
                        ai_cursor.goLeft(len(typed), True)
                        self._ensure_ai_style(doc)
                        ai_cursor.setPropertyValue("CharStyleName", AI_STYLE)
                    except Exception as e:
                        _log("Write-through AI tag ERROR: %s" % e)
                    finally:
                        self._inserting_ghost = False
                    if remaining:
                        self._insert_ghost(remaining)
                        self._advancing = True
                        if self._advance_timer is not None:
                            self._advance_timer.cancel()
                        advance_ms = self.settings.get("AdvanceMs", 20) / 1000.0
                        self._advance_timer = threading.Timer(advance_ms, self._clear_advancing)
                        self._advance_timer.daemon = True
                        self._advance_timer.start()
                    else:
                        _log("Ghost fully consumed by typing")
                        self._reset_cursor_style(vc)
                    return
                else:
                    _log("Typed chars don't match ghost, dismissing")
            self._last_text = new_text
            self._reset_debounce()
        except Exception as e:
            _log("_handle_modification_with_ghost ERROR: %s" % e)
            self._remove_ghost()
            self._reset_debounce()

    def _clear_advancing(self):
        self._advancing = False
        _log("Advancing flag cleared")

    def _reset_cursor_style(self, vc):
        """Reset view cursor formatting so next typed char uses default style."""
        try:
            vc.setPropertyToDefault("CharStyleName")
            if self._saved_color is not None:
                vc.setPropertyValue("CharColor", self._saved_color)
        except Exception:
            pass
        # LO still inherits style from adjacent AI text even after cursor reset,
        # so flag that the next typed char must be stripped too.
        self._cleanup_next_char = True

    def _strip_leaked_style(self):
        """Safety net: strip AI/ghost style from cursor and just-typed char.

        Called on every normal keystroke (no ghost active).  Handles two cases:
        1. Cursor still carries AI/ghost style (advancing blocked terminal reset)
        2. Cursor was reset but LO inherited AI style from adjacent text anyway
           (_cleanup_next_char flag set by _reset_cursor_style)
        """
        try:
            doc = self._get_doc()
            if doc is None:
                return
            vc = doc.getCurrentController().getViewCursor()
            cursor_style = vc.getPropertyValue("CharStyleName")
            needs_cursor_fix = cursor_style in (AI_STYLE, GHOST_STYLE)
            needs_char_fix = needs_cursor_fix or self._cleanup_next_char
            self._cleanup_next_char = False

            if not needs_cursor_fix and not needs_char_fix:
                return

            self._inserting_ghost = True
            try:
                if needs_cursor_fix:
                    self._reset_cursor_style(vc)
                    self._cleanup_next_char = False  # reset already re-set it; clear again
                if needs_char_fix:
                    text_obj = doc.getText()
                    fix = text_obj.createTextCursorByRange(vc.getStart())
                    if fix.goLeft(1, True):
                        cs = fix.getPropertyValue("CharStyleName")
                        if cs in (AI_STYLE, GHOST_STYLE):
                            fix.setPropertyToDefault("CharStyleName")
            finally:
                self._inserting_ghost = False
        except Exception:
            pass

    def _insert_ghost(self, text):
        _log("_insert_ghost: %r" % text[:80])
        if not text:
            return
        self._inserting_ghost = True
        try:
            doc = self._get_doc()
            if doc is None:
                return
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            cursor = text_obj.createTextCursorByRange(vc.getStart())

            # Read current text color for dynamic ghost color
            try:
                cur_color = vc.getPropertyValue("CharColor")
                self._saved_color = cur_color
                ghost_color = _lighten_color(cur_color, 0.6)
            except Exception:
                ghost_color = GHOST_COLOR

            text_obj.insertString(cursor, text, False)
            cursor.goLeft(len(text), True)
            self._ensure_ghost_style(doc, ghost_color)
            cursor.setPropertyValue("CharStyleName", GHOST_STYLE)
            self._ghost_cursor = cursor
            vc.goLeft(len(text), False)

            self._ghost_text = text
            self._ghost_len = len(text)
            _log("Ghost text inserted: %d chars" % len(text))
        except Exception as e:
            _log("_insert_ghost ERROR: %s" % e)
            self._last_error = "Insert error"
        finally:
            self._inserting_ghost = False
            self._update_status_label()

    def _remove_ghost(self, keep_stack=False):
        if self._ghost_len <= 0:
            self._ghost_text = ""
            self._ghost_len = 0
            self._ghost_cursor = None
            if not keep_stack:
                self._accepted_words = []
            return
        self._inserting_ghost = True
        try:
            doc = self._get_doc()
            if doc is None:
                return
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            if self._ghost_cursor is not None:
                self._ghost_cursor.setString("")
            else:
                text_obj = doc.getText()
                cursor = text_obj.createTextCursorByRange(vc.getStart())
                cursor.goRight(self._ghost_len, True)
                cursor.setString("")

            self._reset_cursor_style(vc)

            _log("Ghost text removed: %d chars" % self._ghost_len)
        except Exception as e:
            _log("_remove_ghost ERROR: %s" % e)
        finally:
            self._ghost_text = ""
            self._ghost_len = 0
            self._ghost_cursor = None
            self._saved_color = None
            self._inserting_ghost = False
            if not keep_stack:
                self._accepted_words = []
            self._update_status_label()

    def _accept_ghost(self):
        if self._ghost_len <= 0:
            return
        self._inserting_ghost = True
        try:
            doc = self._get_doc()
            if doc is None:
                return
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            if self._ghost_cursor is not None:
                gc = self._ghost_cursor
            else:
                text_obj = doc.getText()
                gc = text_obj.createTextCursorByRange(vc.getStart())
                gc.goRight(self._ghost_len, True)
            self._ensure_ai_style(doc)
            gc.setPropertyValue("CharStyleName", AI_STYLE)
            # Restore saved color on the accepted text AND view cursor
            if self._saved_color is not None:
                try:
                    gc.setPropertyValue("CharColor", self._saved_color)
                except Exception:
                    pass
            vc.goRight(self._ghost_len, False)
            if self._saved_color is not None:
                try:
                    vc.setPropertyValue("CharColor", self._saved_color)
                except Exception:
                    pass
            self._reset_cursor_style(vc)
            _log("Ghost text accepted: %d chars" % self._ghost_len)
            self._last_text = self._get_prefix_text()
        except Exception as e:
            _log("_accept_ghost ERROR: %s" % traceback.format_exc())
        finally:
            self._ghost_text = ""
            self._ghost_len = 0
            self._ghost_cursor = None
            self._saved_color = None
            self._inserting_ghost = False
            self._accepted_words = []
            self._update_status_label()

    @staticmethod
    def _find_word_boundary(text):
        """Find length of first 'word' in ghost text (including trailing space)."""
        if not text:
            return 0
        i, n = 0, len(text)
        while i < n and text[i] in ' \t':
            i += 1
        if i >= n:
            return n
        while i < n and text[i] not in ' \t.,;:!?()[]{}':
            i += 1
        while i < n and text[i] in '.,;:!?':
            i += 1
        if i < n and text[i] == ' ':
            i += 1
        return max(1, i)

    def _accept_ghost_word(self):
        """Accept first word of ghost text, keep remainder as ghost."""
        if self._ghost_len <= 0:
            return
        word_len = self._find_word_boundary(self._ghost_text)
        if word_len >= self._ghost_len:
            self._accept_ghost()
            return
        accepted = self._ghost_text[:word_len]
        remaining = self._ghost_text[word_len:]
        self._accepted_words.append(accepted)
        self._remove_ghost(keep_stack=True)
        self._inserting_ghost = True
        try:
            doc = self._get_doc()
            if doc is None:
                return
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            ins_cursor = text_obj.createTextCursorByRange(vc.getStart())
            text_obj.insertString(ins_cursor, accepted, False)
            # Tag accepted word with AI style
            ai_cursor = text_obj.createTextCursorByRange(vc.getStart())
            ai_cursor.goLeft(len(accepted), True)
            self._ensure_ai_style(doc)
            ai_cursor.setPropertyValue("CharStyleName", AI_STYLE)
            self._last_text = self._get_prefix_text()
            _log("Ghost word accepted: %r (%d chars), remaining: %d" % (
                accepted, word_len, len(remaining)))
        except Exception as e:
            _log("_accept_ghost_word ERROR: %s" % traceback.format_exc())
            self._inserting_ghost = False
            return
        finally:
            self._inserting_ghost = False
        self._insert_ghost(remaining)
        self._advancing = True
        if self._advance_timer is not None:
            self._advance_timer.cancel()
        advance_ms = self.settings.get("AdvanceMs", 20) / 1000.0
        self._advance_timer = threading.Timer(advance_ms, self._clear_advancing)
        self._advance_timer.daemon = True
        self._advance_timer.start()

    def _unaccept_ghost_word(self):
        """Reverse last word accept: delete from real text, prepend to ghost."""
        if not self._accepted_words:
            return
        word = self._accepted_words.pop()
        remaining = self._ghost_text
        self._remove_ghost(keep_stack=True)
        self._inserting_ghost = True
        try:
            doc = self._get_doc()
            if doc is None:
                return
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            del_cursor = text_obj.createTextCursorByRange(vc.getStart())
            del_cursor.goLeft(len(word), True)
            del_cursor.setString("")
            self._last_text = self._get_prefix_text()
            _log("Ghost word un-accepted: %r, new ghost: %d chars" % (word, len(word) + len(remaining)))
        except Exception as e:
            _log("_unaccept_ghost_word ERROR: %s" % traceback.format_exc())
            self._inserting_ghost = False
            return
        finally:
            self._inserting_ghost = False
        self._insert_ghost(word + remaining)
        self._advancing = True
        if self._advance_timer is not None:
            self._advance_timer.cancel()
        advance_ms = self.settings.get("AdvanceMs", 20) / 1000.0
        self._advance_timer = threading.Timer(advance_ms, self._clear_advancing)
        self._advance_timer.daemon = True
        self._advance_timer.start()

    # -- Debounce & API ------------------------------------------------------

    def _reset_debounce(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._debounce_context = self._get_context_text()
            delay = self.settings.get("DebounceMs", 600) / 1000.0
            self._timer = threading.Timer(delay, self._fire_request)
            self._timer.daemon = True
            self._timer.start()

    def _fire_request(self):
        try:
            prefix, suffix = self._get_context_pair()
            if not prefix.strip():
                return
            # Skip if cursor moved since debounce was set
            if prefix != self._debounce_context:
                _log("Skipping: cursor moved since debounce")
                return
            # Don't suggest if last char is sentence punctuation (but allow if followed by space)
            if prefix[-1:] in ".!?":
                _log("Skipping: context ends with sentence punctuation")
                return
            with self._lock:
                client = self._client
            if client is None:
                return
            self._request_context = prefix
            self._querying = True
            self._last_error = ""
            self._update_status_label()
            suggestion = client.complete(prefix, suffix)
            self._querying = False
            self._query_count += 1
            self._update_status_label()
            if suggestion:
                # Truncate to single sentence if setting enabled
                if self.settings.get("SingleSentence", True):
                    suggestion = _truncate_sentence(suggestion)
                if suggestion:
                    self._ui_queue.put(suggestion)
        except Exception as e:
            _log("_fire_request ERROR: %s" % e)
            self._querying = False
            self._last_error = "API error"
            self._update_status_label()

    def drain_queue(self):
        while not self._ui_queue.empty():
            try:
                suggestion = self._ui_queue.get_nowait()
                current_ctx = self._get_context_text()
                if self._request_context and not current_ctx.startswith(self._request_context):
                    _log("Discarding suggestion: cursor moved since request")
                    continue
                if self._ghost_len > 0:
                    self._remove_ghost()
                self._accepted_words = []
                self._last_text = self._get_prefix_text()
                self._insert_ghost(suggestion)
            except queue.Empty:
                break
            except Exception as e:
                _log("drain_queue ERROR: %s" % e)
                break

    # -- Helpers -------------------------------------------------------------

    def _rebuild_client(self):
        api_key = self.settings.get("ApiKey", "")
        if api_key:
            self._client = LLMClient(
                api_key=api_key,
                model=self.settings.get("Model", "claude-haiku-4-5-20251001"),
                base_url=self.settings.get("BaseUrl", "https://api.anthropic.com/v1"),
                max_tokens=self.settings.get("MaxTokens", 80),
            )
        else:
            self._client = None

    def _get_doc(self):
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            doc = desktop.getCurrentComponent()
            if doc and hasattr(doc, "getText"):
                return doc
            return None
        except Exception:
            return None

    def _get_full_text(self):
        try:
            doc = self._get_doc()
            if doc is None:
                return ""
            text_obj = doc.getText()
            cursor = text_obj.createTextCursor()
            cursor.gotoStart(False)
            cursor.gotoEnd(True)
            return cursor.getString()
        except Exception:
            return ""

    def _get_prefix_text(self):
        """Return all text before cursor (for write-through comparison)."""
        try:
            doc = self._get_doc()
            if doc is None:
                return ""
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            cursor = text_obj.createTextCursorByRange(vc.getStart())
            cursor.gotoStart(True)
            return cursor.getString()
        except Exception:
            return ""

    def _get_context_text(self):
        """Return text before cursor (prefix only, for staleness checks)."""
        try:
            doc = self._get_doc()
            if doc is None:
                return ""
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            cursor = text_obj.createTextCursorByRange(vc.getStart())
            cursor.gotoStart(True)
            full_text = cursor.getString()
            max_chars = self.settings.get("MaxContextChars", 500)
            return full_text[-max_chars:]
        except Exception:
            return ""

    def _get_context_pair(self):
        """Return (prefix, suffix) around cursor for bidirectional context."""
        try:
            doc = self._get_doc()
            if doc is None:
                return ("", "")
            ctrl = doc.getCurrentController()
            vc = ctrl.getViewCursor()
            text_obj = doc.getText()
            max_chars = self.settings.get("MaxContextChars", 500)
            # Prefix
            pre_cursor = text_obj.createTextCursorByRange(vc.getStart())
            pre_cursor.gotoStart(True)
            prefix = pre_cursor.getString()[-max_chars:]
            # Suffix
            suf_cursor = text_obj.createTextCursorByRange(vc.getStart())
            suf_cursor.gotoEnd(True)
            suffix = suf_cursor.getString()[:max_chars]
            return (prefix, suffix)
        except Exception:
            return ("", "")

    def _ensure_ghost_style(self, doc, color=None):
        try:
            styles = doc.getStyleFamilies().getByName("CharacterStyles")
            if not styles.hasByName(GHOST_STYLE):
                style = doc.createInstance("com.sun.star.style.CharacterStyle")
                styles.insertByName(GHOST_STYLE, style)
            s = styles.getByName(GHOST_STYLE)
            s.CharColor = color if color is not None else GHOST_COLOR
            s.CharPosture = uno.Enum("com.sun.star.awt.FontSlant", "ITALIC")
        except Exception:
            pass

    def _ensure_ai_style(self, doc):
        try:
            styles = doc.getStyleFamilies().getByName("CharacterStyles")
            if not styles.hasByName(AI_STYLE):
                style = doc.createInstance("com.sun.star.style.CharacterStyle")
                styles.insertByName(AI_STYLE, style)
            s = styles.getByName(AI_STYLE)
            if self.settings.get("HighlightAI", False):
                s.CharBackColor = AI_HIGHLIGHT_COLOR
                s.CharBackTransparent = False
            else:
                # Reset to defaults — just setting CharBackTransparent=True
                # leaves an explicit CharBackColor on the style which corrupts
                # character rendering (everything turns black)
                for prop in ("CharBackColor", "CharBackTransparent", "CharHighlight"):
                    try:
                        s.setPropertyToDefault(prop)
                    except Exception:
                        pass
        except Exception as e:
            _log("_ensure_ai_style ERROR: %s" % e)

    def set_ai_highlight(self, enabled):
        self.settings["HighlightAI"] = enabled
        doc = self._get_doc()
        if doc:
            self._ensure_ai_style(doc)


class GoRightDispatch(unohelper.Base, XDispatch):
    """Intercepts .uno:GoRight. Accepts ghost if active, else forwards."""

    def __init__(self, handler, original_dispatch):
        self._handler = handler
        self._original = original_dispatch

    def dispatch(self, url, args):
        self._handler.cancel_debounce()
        if self._handler._ghost_len > 0:
            _log("GoRightDispatch: ghost active, accepting")
            self._handler._accept_ghost()
        elif self._original is not None:
            self._original.dispatch(url, args)
        else:
            _log("GoRightDispatch: no original dispatch, ignoring")

    def addStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.addStatusListener(listener, url)
            except Exception:
                pass

    def removeStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.removeStatusListener(listener, url)
            except Exception:
                pass


class GoWordRightDispatch(unohelper.Base, XDispatch):
    """Intercepts .uno:GoWordRight. Accepts next word of ghost if active, else forwards."""

    def __init__(self, handler, original_dispatch):
        self._handler = handler
        self._original = original_dispatch

    def dispatch(self, url, args):
        self._handler.cancel_debounce()
        if self._handler._ghost_len > 0:
            _log("GoWordRightDispatch: ghost active, accepting word")
            self._handler._accept_ghost_word()
        elif self._original is not None:
            self._original.dispatch(url, args)
        else:
            _log("GoWordRightDispatch: no original dispatch, ignoring")

    def addStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.addStatusListener(listener, url)
            except Exception:
                pass

    def removeStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.removeStatusListener(listener, url)
            except Exception:
                pass


class GoWordLeftDispatch(unohelper.Base, XDispatch):
    """Intercepts .uno:GoWordLeft. Un-accepts last word if stack non-empty, else forwards."""

    def __init__(self, handler, original_dispatch):
        self._handler = handler
        self._original = original_dispatch

    def dispatch(self, url, args):
        self._handler.cancel_debounce()
        if self._handler._ghost_len > 0 and self._handler._accepted_words:
            _log("GoWordLeftDispatch: ghost active + stack, un-accepting word")
            self._handler._unaccept_ghost_word()
        elif self._handler._ghost_len > 0:
            _log("GoWordLeftDispatch: stack empty, dismissing ghost")
            self._handler.dismiss_suggestion()
            if self._original is not None:
                self._original.dispatch(url, args)
        elif self._original is not None:
            self._original.dispatch(url, args)
        else:
            _log("GoWordLeftDispatch: no original dispatch, ignoring")

    def addStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.addStatusListener(listener, url)
            except Exception:
                pass

    def removeStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.removeStatusListener(listener, url)
            except Exception:
                pass


class NavDismissDispatch(unohelper.Base, XDispatch):
    """Intercepts navigation commands. Dismisses ghost and cancels debounce, then forwards."""

    def __init__(self, handler, original_dispatch):
        self._handler = handler
        self._original = original_dispatch

    def dispatch(self, url, args):
        self._handler.cancel_debounce()
        if self._handler._ghost_len > 0:
            _log("NavDismissDispatch: dismissing ghost for %s" % url.Complete)
            self._handler.dismiss_suggestion()
        if self._original is not None:
            self._original.dispatch(url, args)

    def addStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.addStatusListener(listener, url)
            except Exception:
                pass

    def removeStatusListener(self, listener, url):
        if self._original is not None:
            try:
                self._original.removeStatusListener(listener, url)
            except Exception:
                pass


# Navigation commands that should cancel debounce and dismiss ghost
NAV_COMMANDS = {
    ".uno:GoLeft", ".uno:GoUp", ".uno:GoDown",
    ".uno:GoToStartOfLine", ".uno:GoToEndOfLine",
    ".uno:GoToStartOfDoc", ".uno:GoToEndOfDoc",
}


class GoRightInterceptor(unohelper.Base, XDispatchProviderInterceptor, XDispatchProvider):
    """Intercepts dispatch requests for navigation commands."""

    def __init__(self, handler):
        self._handler = handler
        self._slave = None
        self._master = None

    # XDispatchProviderInterceptor
    def getSlaveDispatchProvider(self):
        return self._slave

    def setSlaveDispatchProvider(self, provider):
        self._slave = provider

    def getMasterDispatchProvider(self):
        return self._master

    def setMasterDispatchProvider(self, provider):
        self._master = provider

    # XDispatchProvider
    def queryDispatch(self, url, target, flags):
        if url.Complete == ".uno:GoRight":
            original = None
            if self._slave is not None:
                original = self._slave.queryDispatch(url, target, flags)
            return GoRightDispatch(self._handler, original)
        if url.Complete == ".uno:GoWordRight":
            original = None
            if self._slave is not None:
                original = self._slave.queryDispatch(url, target, flags)
            return GoWordRightDispatch(self._handler, original)
        if url.Complete == ".uno:GoWordLeft":
            original = None
            if self._slave is not None:
                original = self._slave.queryDispatch(url, target, flags)
            return GoWordLeftDispatch(self._handler, original)
        if url.Complete in NAV_COMMANDS:
            original = None
            if self._slave is not None:
                original = self._slave.queryDispatch(url, target, flags)
            return NavDismissDispatch(self._handler, original)
        if self._slave is not None:
            return self._slave.queryDispatch(url, target, flags)
        return None

    def queryDispatches(self, requests):
        result = []
        for req in requests:
            result.append(self.queryDispatch(req.FeatureURL, req.FrameName, req.SearchFlags))
        return tuple(result)


class GhostCleanupListener(unohelper.Base, XCloseListener, XTerminateListener):
    """Dismisses ghost text on document close or LO quit to avoid 'unsaved changes' dialog."""

    def __init__(self, handler):
        self._handler = handler

    # XCloseListener
    def queryClosing(self, event, gets_ownership):
        _log("queryClosing: dismissing ghost")
        try:
            self._handler.dismiss_suggestion()
        except Exception as e:
            _log("queryClosing error: %s" % e)

    def notifyClosing(self, event):
        pass

    # XTerminateListener
    def queryTermination(self, event):
        _log("queryTermination: dismissing ghost")
        try:
            self._handler.dismiss_suggestion()
        except Exception as e:
            _log("queryTermination dismiss error: %s" % e)

    def notifyTermination(self, event):
        pass

    def disposing(self, event):
        pass


class DocumentEventListener(unohelper.Base, XEventListener):

    def __init__(self, ctx, handler):
        self.ctx = ctx
        self.handler = handler
        self._registered_docs = set()
        self._key_registered = False
        self._interceptor_registered = False
        self._cleanup_listener = GhostCleanupListener(handler)
        self._close_registered_frames = set()
        self._terminate_registered = False

    def notifyEvent(self, event):
        event_name = getattr(event, "EventName", "")
        if event_name not in ("OnLayoutFinished",):
            _log("DocumentEvent: %s" % event_name)
        if event_name in ("OnNew", "OnLoad", "OnViewCreated", "OnFocus"):
            self._try_register(event_name)

    def _try_register(self, reason=""):
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            doc = desktop.getCurrentComponent()
            if doc is None:
                return
            if not hasattr(doc, "getText"):
                return
            doc_id = id(doc)

            # Register XModifyListener on document (once per doc)
            if doc_id not in self._registered_docs:
                doc.addModifyListener(self.handler)
                self._registered_docs.add(doc_id)
                self.handler._doc_ref = doc
                self.handler._last_text = self.handler._get_prefix_text()
                self.handler._ensure_ai_style(doc)
                _log("XModifyListener registered on doc (%s), id=%d" % (reason, doc_id))

            ctrl = doc.getCurrentController()
            if ctrl is not None:
                # Register XKeyHandler for Escape (once)
                if not self._key_registered:
                    ctrl.addKeyHandler(self.handler)
                    self._key_registered = True
                    _log("XKeyHandler registered on controller (%s)" % reason)

                # Register dispatch interceptor for .uno:GoRight (once)
                if not self._interceptor_registered:
                    frame = ctrl.getFrame()
                    if frame is not None:
                        interceptor = GoRightInterceptor(self.handler)
                        frame.registerDispatchProviderInterceptor(interceptor)
                        self._interceptor_registered = True
                        _log("GoRightInterceptor registered on frame (%s)" % reason)

                # Register close listener per frame
                frame = ctrl.getFrame()
                if frame is not None:
                    frame_id = id(frame)
                    if frame_id not in self._close_registered_frames:
                        try:
                            frame.addCloseListener(self._cleanup_listener)
                            self._close_registered_frames.add(frame_id)
                            _log("XCloseListener registered on frame (%s)" % reason)
                        except Exception as e:
                            _log("XCloseListener registration failed: %s" % e)

                # Register terminate listener once on desktop
                if not self._terminate_registered:
                    try:
                        desktop.addTerminateListener(self._cleanup_listener)
                        self._terminate_registered = True
                        _log("XTerminateListener registered on desktop (%s)" % reason)
                    except Exception as e:
                        _log("XTerminateListener registration failed: %s" % e)
        except Exception as e:
            _log("_try_register(%s) ERROR: %s" % (reason, e))

    def disposing(self, event):
        pass


_handler = None
_doc_listener = None


class LLMAutoCompleteMain(unohelper.Base, XJob, XServiceInfo):

    IMPLEMENTATION_NAME = "com.example.llmac.Main"
    SERVICE_NAMES = ("com.example.llmac.Main",)

    def __init__(self, ctx):
        self.ctx = ctx

    def execute(self, args):
        _log("[LLMAutoComplete] Job.execute called!")
        global _handler, _doc_listener
        if _handler is not None:
            return
        settings = load_settings()
        _handler = AutoCompleteHandler(self.ctx, settings)
        import sys
        sys._llmac_handler = _handler

        def _poll_drain():
            try:
                if _handler is not None:
                    _handler.drain_queue()
            except Exception:
                pass
            interval = _handler.settings.get("PollDrainMs", 300) / 1000.0 if _handler else 0.3
            t = threading.Timer(interval, _poll_drain)
            t.daemon = True
            t.start()
        init_delay = settings.get("PollDrainInitMs", 1000) / 1000.0
        t0 = threading.Timer(init_delay, _poll_drain)
        t0.daemon = True
        t0.start()

        _doc_listener = DocumentEventListener(self.ctx, _handler)
        try:
            broadcaster = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.GlobalEventBroadcaster", self.ctx)
            broadcaster.addEventListener(_doc_listener)
        except Exception as e:
            _log("Failed to register event listener: %s" % e)

        def _delayed():
            if _doc_listener:
                _doc_listener._try_register("delayed_retry")
        for delay in (1.0, 3.0, 6.0):
            t = threading.Timer(delay, _delayed)
            t.daemon = True
            t.start()

    def getImplementationName(self):
        return self.IMPLEMENTATION_NAME

    def supportsService(self, name):
        return name in self.SERVICE_NAMES

    def getSupportedServiceNames(self):
        return self.SERVICE_NAMES


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    LLMAutoCompleteMain,
    LLMAutoCompleteMain.IMPLEMENTATION_NAME,
    LLMAutoCompleteMain.SERVICE_NAMES,
)
