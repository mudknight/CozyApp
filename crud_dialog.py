#!/usr/bin/python3
"""CRUD dialogs for preset pages (Characters, Styles, Tags)."""
import threading
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw, GLib, Gdk, GtkSource  # noqa
from tag_completion import TagCompletion  # noqa

# ---------------------------------------------------------------------------
# Shared lazy TagCompletion singleton
# ---------------------------------------------------------------------------

_tc = None
_tc_lock = threading.Lock()


def _get_tc():
    """Return the shared TagCompletion, initialising it on first call."""
    global _tc
    with _tc_lock:
        if _tc is None:
            _tc = TagCompletion()
            threading.Thread(target=_load_tc, daemon=True).start()
    return _tc


def _load_tc():
    """Load tag data in a background thread."""
    _tc.load_tags()
    _tc.load_characters()
    _tc.load_loras()
    _tc.load_tag_presets()


# ---------------------------------------------------------------------------
# Source view helpers
# ---------------------------------------------------------------------------

def _setup_highlighting(buf):
    """Apply prompt-tags language highlighting to a GtkSource.Buffer."""
    mgr = GtkSource.LanguageManager.get_default()
    lang = mgr.get_language("prompt-tags")
    if lang:
        buf.set_language(lang)


def make_source_view(height=70):
    """
    Create a GtkSource.View wired to the shared TagCompletion.
    Returns (scrolled_window, view, buffer).
    """
    tc = _get_tc()

    buf = GtkSource.Buffer()
    _setup_highlighting(buf)

    sm = GtkSource.StyleSchemeManager.get_default()
    dark = Adw.StyleManager.get_default().get_dark()
    scheme = sm.get_scheme("Adwaita-dark" if dark else "Adwaita")
    if scheme:
        buf.set_style_scheme(scheme)

    view = GtkSource.View()
    view.set_buffer(buf)
    view.set_wrap_mode(Gtk.WrapMode.WORD)
    view.set_show_line_numbers(False)
    view.set_highlight_current_line(False)
    view.completion_debounce_id = None
    view.completion_active = False

    # Capture keys for completion navigation
    key_ctrl = Gtk.EventControllerKey()
    key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    key_ctrl.connect(
        "key-pressed",
        lambda c, kv, kc, st, v=view, t=tc: t.handle_key_press(v, kv)
    )
    view.add_controller(key_ctrl)

    buf.connect(
        "changed",
        lambda b, v=view, t=tc: _on_buf_changed(v, t)
    )

    scrolled = Gtk.ScrolledWindow(
        child=view,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        css_classes=["view"],
        height_request=height
    )
    scrolled.set_overflow(Gtk.Overflow.HIDDEN)

    focus_ctrl = Gtk.EventControllerFocus()
    focus_ctrl.connect(
        "enter",
        lambda _, s=scrolled: s.add_css_class("prompt-focused")
    )
    focus_ctrl.connect(
        "leave",
        lambda _, s=scrolled: s.remove_css_class("prompt-focused")
    )
    view.add_controller(focus_ctrl)

    return scrolled, view, buf


def _on_buf_changed(view, tc):
    """Debounce auto-complete trigger on buffer change."""
    if view.completion_debounce_id:
        GLib.source_remove(view.completion_debounce_id)
        view.completion_debounce_id = None
    view.completion_debounce_id = GLib.timeout_add(
        150, lambda v=view, t=tc: _do_completion(v, t)
    )


def _do_completion(view, tc):
    """Run one completion cycle (called after debounce)."""
    view.completion_debounce_id = None
    buf = view.get_buffer()
    cur = buf.get_iter_at_mark(buf.get_insert())
    if not tc.should_show_completion(buf, cur):
        tc.close_popup()
        return False
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    suggestions = tc.get_completions(text, cur.get_offset())
    if suggestions:
        tc.show_popup(view, suggestions)
    else:
        tc.close_popup()
    return False


# ---------------------------------------------------------------------------
# Dialog base builder
# ---------------------------------------------------------------------------

def _make_base_dialog(parent, title):
    """
    Build an Adw.Dialog shell with header bar + scrollable content.
    Returns (dialog, content_box, save_button).
    """
    dialog = Adw.Dialog(title=title, content_width=520)

    toolbar_view = Adw.ToolbarView()
    dialog.set_child(toolbar_view)

    header = Adw.HeaderBar()
    toolbar_view.add_top_bar(header)

    cancel_btn = Gtk.Button(label="Cancel")
    cancel_btn.connect("clicked", lambda _: dialog.close())
    header.pack_start(cancel_btn)

    save_btn = Gtk.Button(label="Save")
    save_btn.add_css_class("suggested-action")
    header.pack_end(save_btn)

    outer_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        propagate_natural_height=True
    )
    toolbar_view.set_content(outer_scroll)

    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12
    )
    outer_scroll.set_child(content)

    return dialog, content, save_btn


def _labeled_source(label_text, initial_text='', height=70):
    """
    Return (container_box, buffer) with a heading label + source view.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    label = Gtk.Label(label=label_text, xalign=0)
    label.add_css_class("heading")
    box.append(label)

    scrolled, _view, buf = make_source_view(height=height)
    if initial_text:
        buf.set_text(initial_text)
    box.append(scrolled)

    return box, buf


def _buf_text(buf):
    """Return full text content of a GtkSource.Buffer."""
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


# ---------------------------------------------------------------------------
# Public dialog factories
# ---------------------------------------------------------------------------

def make_character_dialog(parent, name, data, on_save):
    """
    Show a character edit/create dialog.

    data keys: character, top, bottom, neg, categories
    on_save receives a dict with keys: name, character, top, bottom,
    neg, categories
    """
    title = "Add Character" if name is None else f'Edit "{name}"'
    dialog, content, save_btn = _make_base_dialog(parent, title)

    # Name + categories in a preferences group
    id_group = Adw.PreferencesGroup()
    content.append(id_group)

    name_row = Adw.EntryRow(title="Name", text=name or '')
    id_group.add(name_row)

    cat_row = Adw.EntryRow(
        title="Categories",
        text=(data.get('categories', '') if data else '')
    )
    id_group.add(cat_row)

    # Tag prompt fields — each uses a GtkSource.View
    char_box, char_buf = _labeled_source(
        "Character",
        data.get('character', '') if data else ''
    )
    content.append(char_box)

    top_box, top_buf = _labeled_source(
        "Top",
        data.get('top', '') if data else ''
    )
    content.append(top_box)

    bot_box, bot_buf = _labeled_source(
        "Bottom",
        data.get('bottom', '') if data else ''
    )
    content.append(bot_box)

    neg_box, neg_buf = _labeled_source(
        "Negative",
        data.get('neg', '') if data else ''
    )
    content.append(neg_box)

    def _on_save(_btn):
        new_name = name_row.get_text().strip()
        if not new_name:
            return
        dialog.close()
        on_save({
            'name': new_name,
            'character': _buf_text(char_buf),
            'top': _buf_text(top_buf),
            'bottom': _buf_text(bot_buf),
            'neg': _buf_text(neg_buf),
            'categories': cat_row.get_text()
        })

    save_btn.connect("clicked", _on_save)
    dialog.present(parent)


def make_style_dialog(parent, name, data, on_save):
    """
    Show a style edit/create dialog.

    on_save receives: {name, positive, negative}
    """
    title = "Add Style" if name is None else f'Edit "{name}"'
    dialog, content, save_btn = _make_base_dialog(parent, title)

    id_group = Adw.PreferencesGroup()
    content.append(id_group)
    name_row = Adw.EntryRow(title="Name", text=name or '')
    id_group.add(name_row)

    pos_box, pos_buf = _labeled_source(
        "Positive",
        data.get('positive', '') if data else ''
    )
    content.append(pos_box)

    neg_box, neg_buf = _labeled_source(
        "Negative",
        data.get('negative', '') if data else ''
    )
    content.append(neg_box)

    def _on_save(_btn):
        new_name = name_row.get_text().strip()
        if not new_name:
            return
        dialog.close()
        on_save({
            'name': new_name,
            'positive': _buf_text(pos_buf),
            'negative': _buf_text(neg_buf)
        })

    save_btn.connect("clicked", _on_save)
    dialog.present(parent)


def make_tag_dialog(parent, name, data, on_save):
    """
    Show a tag preset edit/create dialog.

    on_save receives: {name, positive, negative}
    """
    title = "Add Tag" if name is None else f'Edit "{name}"'
    dialog, content, save_btn = _make_base_dialog(parent, title)

    id_group = Adw.PreferencesGroup()
    content.append(id_group)
    name_row = Adw.EntryRow(title="Name", text=name or '')
    id_group.add(name_row)

    pos_box, pos_buf = _labeled_source(
        "Positive",
        data.get('positive', '') if data else ''
    )
    content.append(pos_box)

    neg_box, neg_buf = _labeled_source(
        "Negative",
        data.get('negative', '') if data else ''
    )
    content.append(neg_box)

    def _on_save(_btn):
        new_name = name_row.get_text().strip()
        if not new_name:
            return
        dialog.close()
        on_save({
            'name': new_name,
            'positive': _buf_text(pos_buf),
            'negative': _buf_text(neg_buf)
        })

    save_btn.connect("clicked", _on_save)
    dialog.present(parent)


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def show_delete_confirm(parent, name, on_confirm):
    """Show an Adw.AlertDialog confirming deletion of name."""
    dialog = Adw.AlertDialog(
        heading=f'Delete "{name}"?',
        body="This action cannot be undone."
    )
    dialog.add_response('cancel', "Cancel")
    dialog.add_response('delete', "Delete")
    dialog.set_response_appearance(
        'delete', Adw.ResponseAppearance.DESTRUCTIVE
    )
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')
    dialog.connect(
        'response',
        lambda d, r: on_confirm() if r == 'delete' else None
    )
    dialog.present(parent)


def show_card_context_menu(card, x, y, on_edit, on_delete):
    """Show a right-click popover with Edit and Delete actions."""
    popover = Gtk.Popover()
    popover.set_parent(card)

    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    rect.width = 1
    rect.height = 1
    popover.set_pointing_to(rect)

    vbox = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=2,
        margin_top=4,
        margin_bottom=4,
        margin_start=4,
        margin_end=4
    )

    edit_btn = Gtk.Button(label="Edit")
    edit_btn.add_css_class("flat")
    edit_btn.connect(
        "clicked", lambda _: (popover.popdown(), on_edit())
    )
    vbox.append(edit_btn)

    del_btn = Gtk.Button(label="Delete")
    del_btn.add_css_class("flat")
    del_btn.add_css_class("destructive-action")
    del_btn.connect(
        "clicked", lambda _: (popover.popdown(), on_delete())
    )
    vbox.append(del_btn)

    popover.set_child(vbox)
    popover.popup()
