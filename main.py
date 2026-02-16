from gi.repository import Gtk, Adw, GLib, Gio, Gdk, GdkPixbuf, GtkSource
import sys
import json
import uuid
import threading
import random
import requests
import websocket
import csv
import gi
import tempfile
import os
import queue

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '5')


def setup_language_manager():
    """Set up the custom language definition for # comments before any GtkSource usage"""
    lang_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<language id="prompt-tags" name="Prompt Tags" version="2.0" _section="Other">
  <metadata>
    <property name="globs">*.txt</property>
  </metadata>
  <styles>
    <style id="comment" name="Comment" map-to="def:comment"/>
  </styles>
  <definitions>
    <context id="prompt-tags">
      <include>
        <context id="comment" style-ref="comment">
          <start>#</start>
          <end>$</end>
        </context>
      </include>
    </context>
  </definitions>
</language>'''
    
    # Create a temporary language file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.lang', delete=False) as f:
        f.write(lang_xml)
        lang_file = f.name
    
    # Create language manager and add our temp file BEFORE any languages are loaded
    lang_manager = GtkSource.LanguageManager.get_default()
    lang_dirs = lang_manager.get_search_path()
    lang_dirs.append(os.path.dirname(lang_file))
    lang_manager.set_search_path(lang_dirs)
    
    return lang_file


SERVER_ADDRESS = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())
PROMPT_NODE_CLASS = "PromptConditioningNode"
LOADER_NODE_CLASS = "LoaderFullPipe"
SAVE_NODE_CLASS = "SaveFullPipe"


class ComfyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id="com.example.comfy_gen",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE, **kwargs)
        self.connect('activate', self.on_activate)
        self.workflow_file = None
        # Set up language manager early, before any GtkSource views are created
        self._lang_file = setup_language_manager()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        # Default workflow file
        self.workflow_file = "workflow.json"
        # Parse arguments for -w flag
        i = 1
        while i < len(args):
            if args[i] == "-w" and i + 1 < len(args):
                self.workflow_file = args[i + 1]
                i += 2
            else:
                i += 1
        self.activate()
        return 0

    def on_activate(self, app):
        self.win = ComfyWindow(application=app, workflow_file=self.workflow_file)
        self.win.present()


class ComfyWindow(Adw.ApplicationWindow):
    def __init__(self, workflow_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("ComfierUI")
        self.set_default_size(1200, 900)
        self.workflow_file = workflow_file

        self.setup_css()
        self.style_list = []
        self.workflow_data = None
        self.danbooru_tags = []
        self.gen_queue = queue.Queue()
        self.is_processing = False

        # Main Layout container
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header
        self.header = Adw.HeaderBar()
        self.main_box.append(self.header)

        self.debug_toggle = Gtk.ToggleButton(
            icon_name="utilities-terminal-symbolic")
        self.debug_toggle.connect("toggled", self.on_toggle_debug)
        self.header.pack_start(self.debug_toggle)

        self.preview_toggle = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic", active=True)
        self.preview_toggle.connect("toggled", self.on_toggle_preview)
        self.header.pack_end(self.preview_toggle)

        self.toast_overlay = Adw.ToastOverlay()
        self.main_box.append(self.toast_overlay)

        # Horizontal Split: [ Sidebar (Left) | Preview (Right) ]
        self.hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.hpaned.set_shrink_start_child(False)
        self.hpaned.set_shrink_end_child(False)
        self.toast_overlay.set_child(self.hpaned)

        # --- Sidebar (Left Column) ---
        self.sidebar_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.sidebar_vbox.set_size_request(420, -1)
        self.hpaned.set_start_child(self.sidebar_vbox)

        # Lock preview panel to 50% width
        self.connect("notify::default-width", self._on_width_changed)

        # Top section: Inputs
        self.input_area = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        for m in ["top", "start", "end"]:
            getattr(self.input_area, f"set_margin_{m}")(20)
        self.input_area.set_margin_bottom(10)
        self.sidebar_vbox.append(self.input_area)

        self.input_area.append(Gtk.Label(label="Style", xalign=0))
        self.style_dropdown = Gtk.DropDown.new_from_strings([])
        self.input_area.append(self.style_dropdown)

        self.pos_buffer = GtkSource.Buffer()
        self.setup_comment_highlighting(self.pos_buffer)
        self.input_area.append(Gtk.Label(label="Positive Prompt", xalign=0))
        pos_scrolled, self.pos_textview = self.create_scrolled_textview(self.pos_buffer)
        self.input_area.append(pos_scrolled)

        self.neg_buffer = GtkSource.Buffer()
        self.setup_comment_highlighting(self.neg_buffer)
        self.input_area.append(Gtk.Label(label="Negative Prompt", xalign=0))
        neg_scrolled, self.neg_textview = self.create_scrolled_textview(self.neg_buffer)
        self.input_area.append(neg_scrolled)

        self.input_area.append(Gtk.Label(label="Seed", xalign=0))
        seed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.seed_adj = Gtk.Adjustment(
            value=0, lower=0, upper=2**64-1, step_increment=1)
        self.seed_entry = Gtk.SpinButton(
            adjustment=self.seed_adj, numeric=True, hexpand=True)
        self.seed_mode_combo = Gtk.DropDown.new_from_strings(
            ["Randomize", "Fixed"])
        seed_box.append(self.seed_entry)
        seed_box.append(self.seed_mode_combo)
        self.input_area.append(seed_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.gen_button = Gtk.Button(label="Generate", css_classes=[
                                     "suggested-action"], hexpand=True)
        self.gen_button.connect("clicked", self.on_generate_clicked)
        self.stop_button = Gtk.Button(
            label="Stop", css_classes=["destructive-action"])
        self.stop_button.connect("clicked", self.on_stop_clicked)
        btn_box.append(self.gen_button)
        btn_box.append(self.stop_button)
        self.input_area.append(btn_box)

        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self.progress_bar = Gtk.ProgressBar()
        progress_box.append(self.progress_bar)
        self.queue_label = Gtk.Label(label="", xalign=0)
        progress_box.append(self.queue_label)
        self.input_area.append(progress_box)

        # Bottom section: Debug (Split vertically from inputs)
        self.debug_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP, reveal_child=False, vexpand=False)
        self.sidebar_vbox.append(self.debug_revealer)

        debug_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        debug_box.set_size_request(-1, 200)
        debug_box.set_margin_start(20)
        debug_box.set_margin_end(20)
        debug_box.set_margin_bottom(20)

        self.debug_buffer = Gtk.TextBuffer()
        debug_view = Gtk.TextView(buffer=self.debug_buffer, editable=False,
                                  wrap_mode=Gtk.WrapMode.CHAR, css_classes=["debug-text"])
        debug_box.append(self.create_scrolled(debug_view))
        self.debug_revealer.set_child(debug_box)

        # --- Preview (Right Column) ---
        self.preview_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, reveal_child=True, hexpand=True)
        self.hpaned.set_end_child(self.preview_revealer)

        preview_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, css_classes=["preview-panel"])
        preview_panel.set_size_request(500, -1)
        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN, vexpand=True)
        for side in ["top", "bottom", "start", "end"]:
            getattr(self.picture, f"set_margin_{side}")(20)
        preview_panel.append(self.picture)
        self.preview_revealer.set_child(preview_panel)

        self.setup_keybinds()

        self.load_danbooru_tags()

        self.fetch_node_info()

        if self.workflow_file:
            self.load_workflow_file(self.workflow_file)

    def setup_keybinds(self):
        """
        Set up global keyboard shortcuts for the window.
        Ctrl+Enter: Generate image
        Ctrl+Escape: Stop generation
        """
        key_controller = Gtk.EventControllerKey()
        key_controller.connect(
            "key-pressed", self.on_window_key_pressed
        )
        self.add_controller(key_controller)

    def on_window_key_pressed(self, controller, keyval, keycode, state):
        """
        Handle window-level keyboard shortcuts.
        """
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl and keyval == Gdk.KEY_Return:
            self.on_generate_clicked(None)
            return True
        elif ctrl and keyval == Gdk.KEY_Escape:
            self.on_stop_clicked(None)
            return True

        return False

    def setup_comment_highlighting(self, buffer):
        """Apply the custom language definition for # comments"""
        # Get the language (already set up by setup_language_manager)
        lang_manager = GtkSource.LanguageManager.get_default()
        lang = lang_manager.get_language("prompt-tags")
        if lang:
            buffer.set_language(lang)

    def setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            revealer { background-color: transparent; border: none; }
            .preview-panel { background-color: @card_bg_color; border-left: none; }
            .view { border: none; border-radius: 8px; background-color: @view_bg_color; }
            .debug-text { font-family: monospace; font-size: 11px; opacity: 0.8; }
            separator { background-color: transparent; }
            gutter { background-color: alpha(@view_fg_color, 0.05); border-right: 1px solid alpha(@view_fg_color, 0.1); }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(
        ), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_width_changed(self, *args):
        width = self.get_default_size().width
        if width > 0:
            self.hpaned.set_position(width // 2)

    def log(self, text):
        GLib.idle_add(self._log_idle, text)

    def _log_idle(self, text):
        if hasattr(self, 'debug_buffer'):
            end_iter = self.debug_buffer.get_end_iter()
            self.debug_buffer.insert(end_iter, f"> {text}\n")

    def fetch_node_info(self):
        def worker():
            try:
                url = f"http://{SERVER_ADDRESS}/object_info/{PROMPT_NODE_CLASS}"
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    data = resp.json().get(PROMPT_NODE_CLASS, {})
                    inputs = data.get("input", {})
                    styles = None
                    for cat in ["required", "optional"]:
                        if "style" in inputs.get(cat, {}):
                            entry = inputs[cat]["style"]
                            styles = entry[0] if isinstance(
                                entry, list) and isinstance(entry[0], list) else entry
                            break
                    if styles:
                        GLib.idle_add(self.update_style_dropdown, styles)
            except Exception as e:
                self.log(f"Metadata fail: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def update_style_dropdown(self, styles):
        self.style_list = styles
        self.style_dropdown.set_model(Gtk.StringList.new(styles))
        if self.workflow_data:
            self.sync_ui_from_json()
        self.load_saved_state()

    def create_scrolled(self, child):
        sc = Gtk.ScrolledWindow(
            child=child, propagate_natural_height=False, vexpand=True)
        sc.add_css_class("view")
        return sc

    def create_scrolled_textview(self, buffer):
        textview = GtkSource.View()
        textview.set_buffer(buffer)
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        textview.set_vexpand(True)
        textview.set_show_line_numbers(True)
        textview.set_highlight_current_line(False)
        
        # Set style scheme based on system dark/light mode
        style_manager = GtkSource.StyleSchemeManager.get_default()
        adw_style_manager = Adw.StyleManager.get_default()
        if adw_style_manager.get_dark():
            style_scheme = style_manager.get_scheme("Adwaita-dark")
        else:
            style_scheme = style_manager.get_scheme("Adwaita")
        if style_scheme:
            buffer.set_style_scheme(style_scheme)
        
        # Track completion state for this textview
        textview.completion_active = False
        textview.completion_debounce_id = None
        
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", lambda controller, keyval, keycode, state: self.on_textview_key_press(textview, keyval, keycode, state))
        textview.add_controller(key_controller)
        
        # Connect to buffer changes for auto-completion
        buffer.connect("changed", lambda buf: self.on_textview_changed(textview))
        
        scrolled = Gtk.ScrolledWindow(
            child=textview, propagate_natural_height=False, vexpand=True)
        scrolled.add_css_class("view")
        return scrolled, textview

    def on_toggle_preview(self, btn):
        self.preview_revealer.set_reveal_child(btn.get_active())

    def on_toggle_debug(self, btn):
        self.debug_revealer.set_reveal_child(btn.get_active())

    def load_danbooru_tags(self):
        try:
            import csv
            with open('danbooru.csv', 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row:
                        self.danbooru_tags.append(row[0])
            self.log(f"Loaded {len(self.danbooru_tags)} tags from danbooru.csv")
        except Exception as e:
            self.log(f"Could not load danbooru.csv: {e}")

    def get_tag_completions(self, text):
        # Get the last word being typed
        words = text.replace(',', ' ').split()
        if not words:
            return []
        current = words[-1].lower()
        if len(current) < 2:
            return []
        # Find matching tags
        matches = [tag for tag in self.danbooru_tags if tag.lower().startswith(current) and tag.lower() != current]
        return matches[:10]  # Limit to 10 suggestions

    def show_completion_popup(self, textview, suggestions):
        if not suggestions:
            return
        
        # Close existing popup if open
        if hasattr(self, 'completion_popup') and self.completion_popup:
            self.completion_popup.popdown()
        
        # Create completion popup using Popover
        popover = Gtk.Popover()
        popover.set_parent(textview)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_autohide(False)  # Don't autohide so we can type
        
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        
        for i, tag in enumerate(suggestions):
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=tag, xalign=0)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.set_child(label)
            listbox.append(row)
            # Select first item by default
            if i == 0:
                listbox.select_row(row)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(listbox)
        scrolled.set_max_content_height(200)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(200, min(len(suggestions) * 30, 200))
        
        popover.set_child(scrolled)
        
        # Position popup at cursor
        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        location = textview.get_iter_location(iter_cursor)
        
        # Create a rectangle for positioning
        rect = Gdk.Rectangle()
        rect.x = location.x
        rect.y = location.y + location.height
        rect.width = 1
        rect.height = 1
        
        popover.set_pointing_to(rect)
        
        # Handle selection with mouse click
        def on_row_activated(listbox, row):
            tag = suggestions[row.get_index()]
            self.insert_completion(textview, tag)
            popover.popdown()
        
        listbox.connect("row-activated", on_row_activated)
        
        self.completion_popup = popover
        popover.popup()

    def insert_completion(self, textview, tag):
        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        
        # Find start of word
        iter_start = iter_cursor.copy()
        while not iter_start.starts_line():
            iter_start.backward_char()
            char = iter_start.get_char()
            if char in ' ,\n\t':
                iter_start.forward_char()
                break
        
        # Replace underscores with spaces and escape parentheses
        formatted_tag = tag.replace('_', ' ')
        # Escape parentheses by adding backslashes
        formatted_tag = formatted_tag.replace('(', '\\(').replace(')', '\\)')
        
        # Replace the partial word with the formatted tag
        buffer.delete(iter_start, iter_cursor)
        buffer.insert(iter_start, formatted_tag + ", ")

    def on_textview_changed(self, textview):
        """Handle text changes with debounce for auto-completion"""
        # Cancel existing debounce timer
        if textview.completion_debounce_id:
            GLib.source_remove(textview.completion_debounce_id)
            textview.completion_debounce_id = None
        
        # Set up new debounce timer (150ms)
        textview.completion_debounce_id = GLib.timeout_add(150, lambda: self._show_completion_if_needed(textview))
    
    def _show_completion_if_needed(self, textview):
        """Check if we should show completion and show it"""
        textview.completion_debounce_id = None
        
        if not self.danbooru_tags:
            return False
        
        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        
        # Check if we're in a valid completion context
        if not self._should_show_completion(buffer, iter_cursor):
            if hasattr(self, 'completion_popup') and self.completion_popup:
                self.completion_popup.popdown()
            return False
        
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        suggestions = self.get_tag_completions(text)
        
        if suggestions:
            self.show_completion_popup(textview, suggestions)
        elif hasattr(self, 'completion_popup') and self.completion_popup:
            self.completion_popup.popdown()
        
        return False
    
    def _should_show_completion(self, buffer, iter_cursor):
        """Check if cursor is after 2 non-space characters at start of line or after comma"""
        offset = iter_cursor.get_offset()
        
        if offset == 0:
            return False
        
        # Find the start of the current "tag" (either line start or after comma)
        iter_tag_start = iter_cursor.copy()
        
        while not iter_tag_start.starts_line():
            iter_tag_start.backward_char()
            char = iter_tag_start.get_char()
            if char == '\n' or char == ',':
                iter_tag_start.forward_char()  # Move past the separator
                break
        
        # Get text from tag start to cursor
        text_from_tag_start = buffer.get_text(iter_tag_start, iter_cursor, False)
        
        # Count non-space characters
        non_space_chars = ''.join(text_from_tag_start.split())
        
        # Check if we have 2+ non-space characters (and no other separators)
        if len(non_space_chars) >= 2 and '\n' not in text_from_tag_start and ',' not in text_from_tag_start:
            return True
        
        return False
    
    def on_textview_key_press(self, textview, keyval, keycode, state):
        # Handle global keybinds first
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl and keyval == Gdk.KEY_Return:
            self.on_generate_clicked(None)
            return True
        elif ctrl and keyval == Gdk.KEY_Escape:
            self.on_stop_clicked(None)
            return True

        # Handle completion popup navigation
        if hasattr(self, 'completion_popup') and self.completion_popup and self.completion_popup.is_visible():
            # Get listbox from popover -> scrolled -> viewport -> listbox
            scrolled = self.completion_popup.get_child()
            listbox = scrolled.get_child().get_child() if scrolled.get_child() else None
            
            if not listbox:
                return False
            
            if keyval == Gdk.KEY_Escape:
                self.completion_popup.popdown()
                return True
            elif keyval == Gdk.KEY_Down:
                # Select next item
                selected = listbox.get_selected_row()
                if selected:
                    index = selected.get_index()
                    next_row = listbox.get_row_at_index(index + 1)
                    if next_row:
                        listbox.select_row(next_row)
                else:
                    first_row = listbox.get_row_at_index(0)
                    if first_row:
                        listbox.select_row(first_row)
                return True
            elif keyval == Gdk.KEY_Up:
                # Select previous item
                selected = listbox.get_selected_row()
                if selected:
                    index = selected.get_index()
                    if index > 0:
                        prev_row = listbox.get_row_at_index(index - 1)
                        if prev_row:
                            listbox.select_row(prev_row)
                return True
            elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return):
                # Complete with selected item (or first item if none selected)
                selected = listbox.get_selected_row()
                if not selected:
                    # Select first item if nothing is selected
                    selected = listbox.get_row_at_index(0)
                if selected:
                    tag = selected.get_child().get_label()
                    self.insert_completion(textview, tag)
                    self.completion_popup.popdown()
                return True
        
        # Check for manual Tab completion (fallback)
        if keyval == Gdk.KEY_Tab and self.danbooru_tags:
            buffer = textview.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            suggestions = self.get_tag_completions(text)
            if suggestions:
                self.show_completion_popup(textview, suggestions)
                return True
        return False

    def load_workflow_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                self.workflow_data = json.load(f)
            self.sync_ui_from_json()
            self.log(f"Loaded workflow: {filepath}")
        except Exception as e:
            self.log(f"Error loading file: {e}")

    def sync_ui_from_json(self):
        if not self.workflow_data:
            return
        for node in self.workflow_data.values():
            if node.get("class_type") == PROMPT_NODE_CLASS:
                self.pos_buffer.set_text(
                    str(node["inputs"].get("positive", "")))
                self.neg_buffer.set_text(
                    str(node["inputs"].get("negative", "")))
                style_val = node["inputs"].get("style")
                if style_val in self.style_list:
                    self.style_dropdown.set_selected(
                        self.style_list.index(style_val))
            elif node.get("class_type") == LOADER_NODE_CLASS:
                self.seed_adj.set_value(float(node["inputs"].get("seed", 0)))

    def save_current_state(self):
        """
        Save current input field values to state.json.
        """
        pos = self.pos_buffer.get_text(
            self.pos_buffer.get_start_iter(),
            self.pos_buffer.get_end_iter(), False
        )
        neg = self.neg_buffer.get_text(
            self.neg_buffer.get_start_iter(),
            self.neg_buffer.get_end_iter(), False
        )
        sel_idx = self.style_dropdown.get_selected()
        style = (
            self.style_list[sel_idx]
            if self.style_list and sel_idx != Gtk.INVALID_LIST_POSITION
            else None
        )

        state = {
            "style": style,
            "positive": pos,
            "negative": neg
        }

        try:
            with open('state.json', 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.log(f"Error saving state: {e}")

    def load_saved_state(self):
        """
        Load input field values from state.json if it exists.
        """
        try:
            with open('state.json', 'r', encoding='utf-8') as f:
                state = json.load(f)

            if "positive" in state:
                self.pos_buffer.set_text(state["positive"])
            if "negative" in state:
                self.neg_buffer.set_text(state["negative"])
            if "style" in state and state["style"] in self.style_list:
                self.style_dropdown.set_selected(
                    self.style_list.index(state["style"])
                )

            self.log("Loaded saved state from state.json")
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log(f"Error loading state: {e}")

    def update_queue_label(self):
        """
        Update the queue status label.
        """
        queue_size = self.gen_queue.qsize()
        if queue_size > 0:
            text = f"Queue: {queue_size} item{'s' if queue_size != 1 else ''}"
            GLib.idle_add(self.queue_label.set_text, text)
        else:
            GLib.idle_add(self.queue_label.set_text, "")

    def on_stop_clicked(self, _):
        try:
            requests.post(f"http://{SERVER_ADDRESS}/interrupt", timeout=5)
            self.log("Interrupt signal sent.")
        except Exception as e:
            self.log(f"Stop error: {e}")

    def on_generate_clicked(self, _):
        """
        Add a generation request to the queue.
        """
        if not self.workflow_data:
            return
        if self.seed_mode_combo.get_selected() == 0:
            self.seed_adj.set_value(float(random.randint(0, 2**32)))

        self.save_current_state()

        current_seed = int(self.seed_adj.get_value())
        pos = self.pos_buffer.get_text(
            self.pos_buffer.get_start_iter(),
            self.pos_buffer.get_end_iter(), False
        )
        neg = self.neg_buffer.get_text(
            self.neg_buffer.get_start_iter(),
            self.neg_buffer.get_end_iter(), False
        )
        sel_idx = self.style_dropdown.get_selected()
        style = (
            self.style_list[sel_idx]
            if self.style_list and sel_idx != Gtk.INVALID_LIST_POSITION
            else None
        )

        workflow_copy = json.loads(json.dumps(self.workflow_data))
        for node in workflow_copy.values():
            if node.get("class_type") == PROMPT_NODE_CLASS:
                node["inputs"].update({"positive": pos, "negative": neg})
                if style:
                    node["inputs"]["style"] = style
            elif node.get("class_type") == LOADER_NODE_CLASS:
                node["inputs"]["seed"] = current_seed

        self.gen_queue.put(workflow_copy)
        self.update_queue_label()
        self.log(f"Added to queue (queue size: {self.gen_queue.qsize()})")

        if not self.is_processing:
            threading.Thread(
                target=self.process_queue, daemon=True
            ).start()

    def process_queue(self):
        """
        Process generation requests from the queue.
        """
        self.is_processing = True

        while not self.gen_queue.empty():
            try:
                workflow_data = self.gen_queue.get()
                self.update_queue_label()
                self.log("Processing queued item...")
                self.generate_logic(workflow_data)
                self.gen_queue.task_done()
            except Exception as e:
                self.log(f"Queue processing error: {e}")

        self.is_processing = False
        self.update_queue_label()
        self.log("Queue processing complete")

    def generate_logic(self, workflow_data):
        """
        Execute a single generation request.
        """
        ws = websocket.WebSocket()
        try:
            ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
            payload = {"prompt": workflow_data, "client_id": CLIENT_ID}
            resp = requests.post(
                f"http://{SERVER_ADDRESS}/prompt", json=payload, timeout=10
            )
            prompt_id = resp.json().get('prompt_id')

            while True:
                out = ws.recv()
                if isinstance(out, bytes):
                    GLib.idle_add(self.update_image, out[8:])
                    continue

                msg = json.loads(out)
                if msg['type'] == 'progress':
                    val = msg['data']['value'] / msg['data']['max']
                    GLib.idle_add(self.progress_bar.set_fraction, val)

                if msg['type'] == 'executed' and 'images' in msg['data']['output']:
                    img = msg['data']['output']['images'][0]
                    img_data = requests.get(
                        f"http://{SERVER_ADDRESS}/view", params=img).content
                    GLib.idle_add(self.update_image, img_data)

                if msg['type'] == 'executing' and msg['data']['node'] is None:
                    break

            hist_resp = requests.get(
                f"http://{SERVER_ADDRESS}/history/{prompt_id}", timeout=10
            )
            history = hist_resp.json().get(prompt_id, {})
            for node_id, node_output in history.get('outputs', {}).items():
                if workflow_data.get(node_id, {}).get(
                    "class_type"
                ) == SAVE_NODE_CLASS:
                    img = node_output['images'][0]
                    data = requests.get(
                        f"http://{SERVER_ADDRESS}/view", params=img
                    ).content
                    GLib.idle_add(self.update_image, data)
                    break
        except Exception as e:
            self.log(f"Gen error: {e}")
        finally:
            ws.close()
            GLib.idle_add(self.progress_bar.set_fraction, 0.0)

    def update_image(self, data):
        loader = GdkPixbuf.PixbufLoader.new()
        try:
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
            if pix:
                self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pix))
        except:
            pass


if __name__ == "__main__":
    app = ComfyApp()
    app.run(sys.argv)
