#!/usr/bin/python3 -u
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
import re

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '5')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Adw, GLib, Gio, Gdk, GdkPixbuf, GtkSource, Pango  # noqa
from tag_completion import TagCompletion  # noqa
from gallery import GalleryPage  # noqa


def setup_language_manager():
    """Set up the custom language definition for
    # comments before any GtkSource usage"""
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
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.lang', delete=False
    ) as f:
        f.write(lang_xml)
        lang_file = f.name

    # Create language manager and add our file BEFORE any languages are loaded
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
        self.connect('shutdown', self.on_shutdown)
        self.workflow_file = None
        # Set up language manager early, before views are created
        self._lang_file = setup_language_manager()

    def on_shutdown(self, app):
        """Clean up temporary language file on shutdown."""
        if self._lang_file and os.path.exists(self._lang_file):
            try:
                os.unlink(self._lang_file)
            except Exception:
                pass

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
        # Add assets directory to icon theme search path
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_theme.add_search_path(os.path.join(
            os.path.dirname(__file__), "assets"))

        self.win = ComfyWindow(
            application=app, workflow_file=self.workflow_file)
        self.win.present()


class ComfyWindow(Adw.ApplicationWindow):
    def __init__(self, workflow_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("CozyApp")
        self.set_default_size(1200, 900)
        self.workflow_file = workflow_file

        self.setup_css()
        self.style_list = []
        self.model_list = []
        self.workflow_data = None
        self.tag_completion = TagCompletion(self.log)
        self.gen_queue = queue.Queue()
        self.is_processing = False
        self.debounce_timers = []
        self.current_pixbuf = None
        # Last completed generation image
        self.gen_pixbuf = None
        # Currently selected gallery image
        self.gallery_selected_pixbuf = None
        self.magnifier_size = 200
        self.magnifier_enabled = False
        self.last_cursor_x = 0
        self.last_cursor_y = 0

        # Connect to destroy signal for cleanup
        self.connect("close-request", self.on_close_request)

        # Main Layout container
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header
        self.header = Adw.HeaderBar()
        self.main_box.append(self.header)

        self.magnifier_toggle = Gtk.ToggleButton(
            icon_name="system-search-symbolic")
        self.magnifier_toggle.set_tooltip_text("Toggle magnifier")
        self.magnifier_toggle.connect("toggled", self.on_toggle_magnifier)

        self.preview_toggle = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic", active=True)
        self.preview_toggle.connect("toggled", self.on_toggle_preview)

        # Overflow menu button
        self.menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=self.create_overflow_menu()
        )
        self.header.pack_end(self.menu_button)
        self.header.pack_end(self.preview_toggle)
        self.header.pack_end(self.magnifier_toggle)

        # View stack for Generate / Gallery tabs
        self.view_stack = Adw.ViewStack()

        # View switcher in the header bar title area
        switcher = Adw.ViewSwitcher(
            stack=self.view_stack,
            policy=Adw.ViewSwitcherPolicy.WIDE
        )
        self.header.set_title_widget(switcher)

        # Outer box: [ ViewStack (Left) | Preview panel (Right) ]
        # Preview is shared across both tabs
        self.outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.outer_box)
        self.main_box.append(self.toast_overlay)

        self.view_stack.set_hexpand(True)
        self.outer_box.append(self.view_stack)

        # Horizontal layout: sidebar only (preview lives in outer_box)
        self.content_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL
        )

        # Add Generate page to the stack
        self.view_stack.add_titled_with_icon(
            self.content_box, 'generate', 'Generate',
            'applications-graphics-symbolic'
        )

        # --- Sidebar (Left Column) ---
        self.sidebar_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True
        )
        self.content_box.append(self.sidebar_vbox)

        # Top section: Inputs
        self.input_area = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        for m in ["top", "start", "end"]:
            getattr(self.input_area, f"set_margin_{m}")(20)
        self.input_area.set_margin_bottom(10)
        self.sidebar_vbox.append(self.input_area)

        style_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        # self.input_area.append(Gtk.Label(label="Style", xalign=0))
        style_box.append(Gtk.Label(label="Style", xalign=0))
        self.style_dropdown = Gtk.DropDown.new_from_strings([])
        # self.style_dropdown.set_hexpand(True)

        # Create factory for ellipsizing style dropdown with fixed 10 char width
        style_list_factory = Gtk.SignalListItemFactory()

        def setup_style_item(factory, list_item):
            label = Gtk.Label()
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_xalign(0)
            label.set_width_chars(10)
            label.set_max_width_chars(10)
            list_item.set_child(label)

        def bind_style_item(factory, list_item):
            label = list_item.get_child()
            string_obj = list_item.get_item()
            label.set_label(string_obj.get_string())

        style_list_factory.connect("setup", setup_style_item)
        style_list_factory.connect("bind", bind_style_item)
        self.style_dropdown.set_list_factory(style_list_factory)

        # Also ellipsize the selected style display
        style_button_factory = Gtk.SignalListItemFactory()
        style_button_factory.connect("setup", setup_style_item)
        style_button_factory.connect("bind", bind_style_item)
        self.style_dropdown.set_factory(style_button_factory)

        # self.input_area.append(self.style_dropdown)
        style_box.append(self.style_dropdown)

        # Model selector
        style_box.append(Gtk.Label(label="Model", xalign=0, margin_start=10))
        self.model_dropdown = Gtk.DropDown.new_from_strings([])
        self.model_dropdown.set_hexpand(True)

        # Allow the dropdown to shrink below its natural size
        self.model_dropdown.set_size_request(50, -1)

        # Create factory for ellipsizing dropdown items
        list_factory = Gtk.SignalListItemFactory()

        def setup_list_item(factory, list_item):
            label = Gtk.Label()
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_xalign(0)
            label.set_max_width_chars(1)  # Force ellipsize to kick in
            list_item.set_child(label)

        def bind_list_item(factory, list_item):
            label = list_item.get_child()
            string_obj = list_item.get_item()
            label.set_label(string_obj.get_string())

        list_factory.connect("setup", setup_list_item)
        list_factory.connect("bind", bind_list_item)
        self.model_dropdown.set_list_factory(list_factory)

        # Also ellipsize the selected item display
        button_factory = Gtk.SignalListItemFactory()
        button_factory.connect("setup", setup_list_item)
        button_factory.connect("bind", bind_list_item)
        self.model_dropdown.set_factory(button_factory)

        style_box.append(self.model_dropdown)

        self.input_area.append(style_box)

        self.pos_buffer = GtkSource.Buffer()
        self.setup_comment_highlighting(self.pos_buffer)
        self.input_area.append(Gtk.Label(label="Positive Prompt", xalign=0))
        pos_scrolled, self.pos_textview = self.create_scrolled_textview(
            self.pos_buffer)
        self.input_area.append(pos_scrolled)

        self.neg_buffer = GtkSource.Buffer()
        self.setup_comment_highlighting(self.neg_buffer)
        self.input_area.append(Gtk.Label(label="Negative Prompt", xalign=0))
        neg_scrolled, self.neg_textview = self.create_scrolled_textview(
            self.neg_buffer)
        self.input_area.append(neg_scrolled)

        # self.input_area.append(Gtk.Label(label="Seed", xalign=0))
        seed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        seed_box.append(Gtk.Label(label="Seed", xalign=0))
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
                                     "suggested-action"])
        self.gen_button.connect("clicked", self.on_generate_clicked)
        self.stop_button = Gtk.Button(
            label="Stop", css_classes=["destructive-action"])
        self.stop_button.connect("clicked", self.on_stop_clicked)
        self.stop_button.set_sensitive(False)

        # Queue label styled like a button
        self.queue_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=5,
            css_classes=["queue-badge"])
        self.queue_label = Gtk.Label(
            label="Queue: 0", xalign=0.5
        )
        self.queue_label.set_width_chars(10)
        self.queue_label.set_max_width_chars(10)
        self.queue_box.append(self.queue_label)

        # progress_box = Gtk.Box(
        #     orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        # )
        self.progress_bar = Gtk.ProgressBar(hexpand=True)
        self.progress_bar.set_valign(Gtk.Align.CENTER)

        # Current node label (inline with progress bar)
        self.current_node_label = Gtk.Label(
            label="Ready", xalign=0.5)
        self.current_node_label.set_width_chars(20)
        self.current_node_label.set_max_width_chars(20)
        self.current_node_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.current_node_label.set_valign(Gtk.Align.CENTER)

        self.queue_box.append(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.queue_box.append(self.current_node_label)

        # Order elements in button box
        btn_box.append(self.queue_box)
        btn_box.append(self.progress_bar)
        btn_box.append(self.stop_button)
        btn_box.append(self.gen_button)

        self.input_area.append(btn_box)

        # --- Preview (Right Column) ---
        self.preview_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            reveal_child=True,
            hexpand=True
        )
        self.outer_box.append(self.preview_revealer)

        preview_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=["preview-panel"]
        )
        preview_panel.set_size_request(400, -1)

        # ScrolledWindow that doesn't resize based on child
        picture_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_width=False,
            propagate_natural_height=False,
            vexpand=True,
            hexpand=True
        )
        # for side in ["top", "bottom", "start", "end"]:
        #     getattr(picture_scroll, f"set_margin_{side}")(20)

        # Create overlay for magnifier
        self.picture_overlay = Gtk.Overlay()

        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN,
            can_shrink=True
        )
        self.picture_overlay.set_child(self.picture)

        # Create magnifier frame
        self.magnifier_frame = Gtk.Frame(
            css_classes=['magnifier-frame']
        )
        self.magnifier_frame.set_visible(False)
        self.magnifier_frame.set_size_request(
            self.magnifier_size, self.magnifier_size
        )
        # Make magnifier non-interactive so it doesn't block mouse events
        self.magnifier_frame.set_can_target(False)

        self.magnifier_picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.FILL
        )
        self.magnifier_picture.set_can_target(False)
        self.magnifier_frame.set_child(self.magnifier_picture)

        self.picture_overlay.add_overlay(self.magnifier_frame)

        picture_scroll.set_child(self.picture_overlay)

        # Stack switches between picture and a placeholder
        self.preview_stack = Gtk.Stack(
            vexpand=True, hexpand=True
        )
        self.preview_stack.add_named(picture_scroll, 'picture')
        self.preview_placeholder = Adw.StatusPage(
            icon_name='image-x-generic-symbolic',
            title='No Image Selected',
            description='Click a thumbnail to preview it here.'
        )
        self.preview_stack.add_named(
            self.preview_placeholder, 'placeholder'
        )
        preview_panel.append(self.preview_stack)
        self.preview_revealer.set_child(preview_panel)

        # Add motion controller for magnifier
        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("motion", self.on_picture_motion)
        motion_controller.connect("leave", self.on_picture_leave)
        motion_controller.connect("enter", self.on_picture_enter)
        self.picture.add_controller(motion_controller)

        # Add scroll controller for magnifier size adjustment
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self.on_picture_scroll)
        self.picture.add_controller(scroll_controller)

        # Store default cursor
        self.default_cursor = None
        self.crosshair_cursor = Gdk.Cursor.new_from_name("crosshair")

        # Gallery page — clicking a thumbnail updates the shared preview
        self.gallery = GalleryPage(on_view_image=self._view_gallery_image)
        self.view_stack.add_titled_with_icon(
            self.gallery.widget, 'gallery', 'Gallery',
            'image-x-generic-symbolic'
        )
        self.view_stack.connect(
            'notify::visible-child', self._on_tab_changed
        )

        self.setup_keybinds()

        self.tag_completion.load_tags()
        self.tag_completion.load_characters()
        self.tag_completion.load_loras()

        self.fetch_node_info()

        if self.workflow_file:
            self.load_workflow_file(self.workflow_file)

    def create_overflow_menu(self):
        """Create the overflow menu with about action."""
        menu = Gio.Menu()
        menu.append("About", "app.about")

        # Add the action to the application
        action = Gio.SimpleAction.new("about", None)
        action.connect("activate", self.on_show_about)
        self.get_application().add_action(action)

        return menu

    def on_show_about(self, action, param):
        """Show the about dialog using Adw.AboutDialog."""
        dialog = Adw.AboutDialog.new()
        dialog.set_application_name("CozyApp")
        dialog.set_developer_name("Your Name")
        dialog.set_version("1.0.0")
        dialog.set_application_icon("com.example.comfy_gen")
        dialog.set_website("https://example.com")
        dialog.set_issue_url("https://github.com/example/cozyapp/issues")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_comments("A simple GTK4 application with an about page.")
        dialog.add_acknowledgement_section(
            "Contributors", ["Contributor 1", "Contributor 2"])
        dialog.present(self)

    def on_close_request(self, window):
        """Clean up resources before closing."""
        # Remove all debounce timers
        for timer_id in self.debounce_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self.debounce_timers.clear()
        return False

    def is_image_downscaled(self):
        """Check if the current image is being downscaled."""
        if not self.current_pixbuf:
            return False

        paintable = self.picture.get_paintable()
        if not paintable:
            return False

        # Get original dimensions
        orig_width = self.current_pixbuf.get_width()
        orig_height = self.current_pixbuf.get_height()

        # Get displayed dimensions
        display_width = self.picture.get_width()
        display_height = self.picture.get_height()

        # Check if image is smaller than original
        return (display_width < orig_width or
                display_height < orig_height)

    def on_picture_enter(self, controller, x, y):
        """Handle mouse entering the picture."""
        if self.is_image_downscaled() and self.magnifier_enabled:
            self.picture.set_cursor(self.crosshair_cursor)
        else:
            if self.default_cursor is None:
                self.default_cursor = self.picture.get_cursor()
            self.picture.set_cursor(self.default_cursor)

    def on_picture_scroll(self, controller, dx, dy):
        """Handle scroll wheel to adjust magnifier size."""
        if not self.is_image_downscaled() or not self.magnifier_enabled:
            return False

        # Adjust size based on scroll direction
        # dy > 0 means scroll down (smaller), dy < 0 means scroll up (larger)
        size_change = -10 if dy > 0 else 10
        new_size = self.magnifier_size + size_change

        # Limit size between 100 and 400 pixels
        new_size = max(100, min(400, new_size))

        if new_size != self.magnifier_size:
            self.magnifier_size = new_size
            self.magnifier_frame.set_size_request(
                self.magnifier_size, self.magnifier_size
            )

            # Update magnifier at last known cursor position
            self.update_magnifier(self.last_cursor_x, self.last_cursor_y)

        return True

    def on_picture_motion(self, controller, x, y):
        """Handle mouse motion over the picture."""
        # Store cursor position
        self.last_cursor_x = x
        self.last_cursor_y = y

        if not self.is_image_downscaled() or not self.magnifier_enabled:
            if self.magnifier_frame.get_visible():
                self.magnifier_frame.set_visible(False)
            self.picture.set_cursor(self.default_cursor)
            return

        # Set crosshair cursor
        self.picture.set_cursor(self.crosshair_cursor)

        # Show magnifier
        if not self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(True)

        # Update magnifier position and content
        self.update_magnifier(x, y)

    def on_picture_leave(self, controller):
        """Handle mouse leaving the picture."""
        if self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(False)
        self.picture.set_cursor(self.default_cursor)

    def update_magnifier(self, x, y):
        """Update the magnifier position and displayed region."""
        if not self.current_pixbuf:
            return

        # Get dimensions
        orig_width = self.current_pixbuf.get_width()
        orig_height = self.current_pixbuf.get_height()
        display_width = self.picture.get_width()
        display_height = self.picture.get_height()

        if display_width == 0 or display_height == 0:
            return

        # Calculate which part of the original image is shown
        scale = min(
            display_width / orig_width,
            display_height / orig_height
        )
        scaled_width = orig_width * scale
        scaled_height = orig_height * scale

        # Calculate offset (image is centered)
        x_offset = (display_width - scaled_width) / 2
        y_offset = (display_height - scaled_height) / 2

        # Convert mouse position to original image coordinates
        img_x = (x - x_offset) / scale
        img_y = (y - y_offset) / scale

        # Clamp to image bounds
        img_x = max(0, min(img_x, orig_width))
        img_y = max(0, min(img_y, orig_height))

        # Define magnified region size (in original image coordinates)
        # Show region that's half the magnifier size for 2x magnification
        mag_size = self.magnifier_size // 2
        half_size = mag_size // 2

        # Calculate crop region centered on cursor
        crop_x = int(img_x - half_size)
        crop_y = int(img_y - half_size)

        # Adjust crop position to keep it within bounds while maintaining
        # size
        if crop_x < 0:
            crop_x = 0
        elif crop_x + mag_size > orig_width:
            crop_x = orig_width - mag_size

        if crop_y < 0:
            crop_y = 0
        elif crop_y + mag_size > orig_height:
            crop_y = orig_height - mag_size

        # Ensure we're still within bounds after adjustment
        crop_x = max(0, min(crop_x, orig_width - mag_size))
        crop_y = max(0, min(crop_y, orig_height - mag_size))

        crop_width = mag_size
        crop_height = mag_size

        if crop_width <= 0 or crop_height <= 0:
            return

        # Create subpixbuf for the region
        try:
            subpixbuf = self.current_pixbuf.new_subpixbuf(
                crop_x, crop_y, crop_width, crop_height
            )

            # Convert to texture
            width = subpixbuf.get_width()
            height = subpixbuf.get_height()
            rowstride = subpixbuf.get_rowstride()
            has_alpha = subpixbuf.get_has_alpha()
            pixels = subpixbuf.get_pixels()

            gbytes = GLib.Bytes.new(pixels)
            fmt = (Gdk.MemoryFormat.R8G8B8A8 if has_alpha
                   else Gdk.MemoryFormat.R8G8B8)

            texture = Gdk.MemoryTexture.new(
                width, height, fmt, gbytes, rowstride
            )
            self.magnifier_picture.set_paintable(texture)

            # Position magnifier frame centered under cursor
            mag_width = self.magnifier_size
            mag_height = self.magnifier_size
            mag_x = x - mag_width / 2
            mag_y = y - mag_height / 2

            # Keep magnifier within picture bounds
            mag_x = max(0, min(mag_x, display_width - mag_width))
            mag_y = max(0, min(mag_y, display_height - mag_height))

            # Set position using margin
            self.magnifier_frame.set_margin_start(int(mag_x))
            self.magnifier_frame.set_margin_top(int(mag_y))
            self.magnifier_frame.set_halign(Gtk.Align.START)
            self.magnifier_frame.set_valign(Gtk.Align.START)

        except Exception as e:
            self.log(f"Magnifier error: {e}")

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
            .gallery-thumb { border-radius: 8px; }
            revealer { background-color: transparent; border: none; }
            .preview-panel { background-color: @card_bg_color; border-left: none; }
            .view { border: none; border-radius: 8px; background-color: @view_bg_color; }
            gutter { background-color: alpha(@view_fg_color, 0.05); border-right: 1px solid alpha(@view_fg_color, 0.1); }
            .magnifier-frame {
                border: 2px solid alpha(currentColor, 0.3);
                border-radius: 8px;
                background-color: @window_bg_color;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            }
            .queue-badge {
                border-radius: 9px;
                background-color: alpha(@view_fg_color, 0.1);
                color: alpha(@view_fg_color, 0.5);
                opacity: 0.7;
                min-width: 60px;
            }
            .queue-active {
                background-color: alpha(@accent_bg_color, 0.3);
                border-color: @accent_bg_color;
                color: alpha(@accent_fg_color, 0.7);
                opacity: 1.0;
            }
            .current-node-label {
                padding: 6px 10px;
                border-radius: 6px;
                background-color: alpha(@accent_bg_color, 0.2);
                border: 1px solid alpha(@accent_bg_color, 0.4);
                color: @accent_fg_color;
                font-size: 10px;
                font-family: monospace;
            }
            .about-image {
                border-radius: 16px;
            }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(
        ), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def log(self, text):
        print(f"{text}", flush=True)

    def fetch_node_info(self):
        def worker():
            try:
                # Fetch styles
                url = (
                    f"http://{SERVER_ADDRESS}/object_info/"
                    f"{PROMPT_NODE_CLASS}"
                )
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    data = resp.json().get(PROMPT_NODE_CLASS, {})
                    inputs = data.get("input", {})
                    styles = None
                    for cat in ["required", "optional"]:
                        if "style" in inputs.get(cat, {}):
                            entry = inputs[cat]["style"]
                            styles = (
                                entry[0] if isinstance(entry, list)
                                and isinstance(entry[0], list) else entry
                            )
                            break
                    if styles:
                        GLib.idle_add(self.update_style_dropdown, styles)
                resp.close()
            except Exception as e:
                self.log(f"Metadata fail: {e}")

            try:
                # Fetch models
                url = (
                    f"http://{SERVER_ADDRESS}/object_info/"
                    f"{LOADER_NODE_CLASS}"
                )
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    data = resp.json().get(LOADER_NODE_CLASS, {})
                    inputs = data.get("input", {})
                    models = None
                    for cat in ["required", "optional"]:
                        if "ckpt_name" in inputs.get(cat, {}):
                            entry = inputs[cat]["ckpt_name"]
                            models = (
                                entry[0] if isinstance(entry, list)
                                and isinstance(entry[0], list) else entry
                            )
                            break
                    if models:
                        GLib.idle_add(self.update_model_dropdown, models)
                resp.close()
            except Exception as e:
                self.log(f"Model metadata fail: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def update_style_dropdown(self, styles):
        self.style_list = styles
        self.style_dropdown.set_model(Gtk.StringList.new(styles))
        if self.workflow_data:
            self.sync_ui_from_json()
        self.load_saved_state()

    def update_model_dropdown(self, models):
        self.model_list = models
        self.model_dropdown.set_model(Gtk.StringList.new(models))
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
        key_controller.connect(
            "key-pressed", lambda controller, keyval, keycode,
            state: self.on_textview_key_press(
                textview, keyval, keycode, state))
        textview.add_controller(key_controller)

        # Connect to buffer changes for auto-completion
        buffer.connect(
            "changed", lambda buf: self.on_textview_changed(textview))

        scrolled = Gtk.ScrolledWindow(
            child=textview, propagate_natural_height=False, vexpand=True)
        scrolled.add_css_class("view")
        return scrolled, textview

    def on_toggle_preview(self, btn):
        """
        Toggle preview panel visibility.
        """
        is_active = btn.get_active()

        if is_active:
            # Show: enable expansion then reveal
            self.preview_revealer.set_hexpand(True)
            self.preview_revealer.set_reveal_child(True)
        else:
            # Hide: disable expansion then collapse
            self.preview_revealer.set_hexpand(False)
            self.preview_revealer.set_reveal_child(False)

    def on_toggle_magnifier(self, btn):
        """
        Toggle magnifier functionality.
        """
        self.magnifier_enabled = btn.get_active()
        # Hide magnifier when disabled
        if not self.magnifier_enabled and self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(False)

    def on_textview_changed(self, textview):
        """
        Handle text changes with debounce for auto-completion.
        """
        if not hasattr(textview, 'completion_debounce_id'):
            textview.completion_debounce_id = None

        if textview.completion_debounce_id:
            GLib.source_remove(textview.completion_debounce_id)
            try:
                self.debounce_timers.remove(textview.completion_debounce_id)
            except ValueError:
                pass
            textview.completion_debounce_id = None

        timer_id = GLib.timeout_add(
            150,
            lambda: self._show_completion_if_needed(textview)
        )
        textview.completion_debounce_id = timer_id
        self.debounce_timers.append(timer_id)

    def _show_completion_if_needed(self, textview):
        """
        Check if we should show completion and show it.
        """
        # Remove timer from tracking list
        if hasattr(textview, 'completion_debounce_id'):
            try:
                self.debounce_timers.remove(textview.completion_debounce_id)
            except ValueError:
                pass
            textview.completion_debounce_id = None

        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)

        if not self.tag_completion.should_show_completion(
            buffer, iter_cursor
        ):
            self.tag_completion.close_popup()
            return False

        text = buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            False
        )
        cursor_pos = iter_cursor.get_offset()
        suggestions = self.tag_completion.get_completions(
            text, cursor_pos
        )

        if suggestions:
            self.tag_completion.show_popup(textview, suggestions)
        else:
            self.tag_completion.close_popup()

        return False

    def adjust_tag_weight(self, textview, increase=True):
        """
        Adjust the weight of the tag under the cursor or selected text.
        Format: (tag:weight) or (tag1, tag2:weight).
        Weight of 1.0 removes the syntax entirely.
        """
        buffer = textview.get_buffer()

        # Check if text is selected
        selection = buffer.get_selection_bounds()
        if selection:
            # Handle selected text
            iter_start, iter_end = selection
            selected_text = buffer.get_text(iter_start, iter_end, False)

            if not selected_text.strip():
                return

            # Check if selection already has weight: (content:1.1)
            weight_pattern = r'^\((.+?):(\d+\.?\d*)\)$'
            match = re.match(weight_pattern, selected_text)

            if match:
                content = match.group(1)
                current_weight = float(match.group(2))
                new_weight = current_weight + (0.1 if increase else -0.1)
                new_weight = max(0.1, min(2.0, new_weight))

                # If weight is 1.0, remove the syntax
                if abs(new_weight - 1.0) < 0.01:
                    new_text = content
                else:
                    new_text = f"({content}:{new_weight:.1f})"
            else:
                new_weight = 1.1 if increase else 0.9

                # If weight would be 1.0, don't add syntax
                if abs(new_weight - 1.0) < 0.01:
                    new_text = selected_text
                else:
                    new_text = f"({selected_text}:{new_weight:.1f})"

            # Get the start offset before deletion
            start_offset = iter_start.get_offset()

            # Replace the selection
            buffer.delete(iter_start, iter_end)
            buffer.insert(iter_start, new_text)

            # Reselect the new text
            new_start = buffer.get_iter_at_offset(start_offset)
            new_end = buffer.get_iter_at_offset(
                start_offset + len(new_text)
            )
            buffer.select_range(new_start, new_end)
            return

        # No selection - handle single tag under cursor
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)

        # Find start and end of current tag (comma-separated)
        iter_start = iter_cursor.copy()
        iter_end = iter_cursor.copy()

        # Move start backward to comma or line start
        leading_space = ""
        while not iter_start.starts_line():
            iter_start.backward_char()
            char = iter_start.get_char()
            if char == ',':
                iter_start.forward_char()
                # Capture leading spaces
                while iter_start.get_char() == ' ':
                    leading_space += ' '
                    iter_start.forward_char()
                break

        # Move end forward to comma or line end
        while not iter_end.ends_line():
            char = iter_end.get_char()
            if char == ',':
                break
            iter_end.forward_char()

        # Get the tag text (without leading space)
        tag_text = buffer.get_text(iter_start, iter_end, False).strip()

        if not tag_text:
            return

        # Check if tag already has weight: (tag text:1.1)
        weight_pattern = r'^\((.+?):(\d+\.?\d*)\)$'
        match = re.match(weight_pattern, tag_text)

        if match:
            tag_content = match.group(1)
            current_weight = float(match.group(2))
            new_weight = current_weight + (0.1 if increase else -0.1)
            new_weight = max(0.1, min(2.0, new_weight))

            # If weight is 1.0, remove the syntax
            if abs(new_weight - 1.0) < 0.01:
                new_tag = tag_content
            else:
                new_tag = f"({tag_content}:{new_weight:.1f})"
        else:
            new_weight = 1.1 if increase else 0.9

            # If weight would be 1.0, don't add syntax
            if abs(new_weight - 1.0) < 0.01:
                new_tag = tag_text
            else:
                new_tag = f"({tag_text}:{new_weight:.1f})"

        # Move iter_start back to include leading space
        iter_start_with_space = iter_cursor.copy()
        while not iter_start_with_space.starts_line():
            iter_start_with_space.backward_char()
            char = iter_start_with_space.get_char()
            if char == ',':
                iter_start_with_space.forward_char()
                break

        # Replace the tag (preserving leading space)
        buffer.delete(iter_start_with_space, iter_end)
        buffer.insert(iter_start_with_space, leading_space + new_tag)

    def on_textview_key_press(self, textview, keyval, keycode, state):
        # Handle global keybinds first
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl and keyval == Gdk.KEY_Return:
            self.on_generate_clicked(None)
            return True
        elif ctrl and keyval == Gdk.KEY_Escape:
            self.on_stop_clicked(None)
            return True
        elif ctrl and keyval == Gdk.KEY_Up:
            self.adjust_tag_weight(textview, increase=True)
            return True
        elif ctrl and keyval == Gdk.KEY_Down:
            self.adjust_tag_weight(textview, increase=False)
            return True

        # Handle completion with tag_completion module
        if self.tag_completion.handle_key_press(textview, keyval):
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
                model_val = node["inputs"].get("ckpt_name")
                if model_val in self.model_list:
                    self.model_dropdown.set_selected(
                        self.model_list.index(model_val))

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

        model_idx = self.model_dropdown.get_selected()
        model = (
            self.model_list[model_idx]
            if self.model_list and model_idx != Gtk.INVALID_LIST_POSITION
            else None
        )

        state = {
            "style": style,
            "model": model,
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
            if "model" in state and state["model"] in self.model_list:
                self.model_dropdown.set_selected(
                    self.model_list.index(state["model"])
                )

            self.log("Loaded saved state from state.json")
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log(f"Error loading state: {e}")

    def set_current_node(self, text):
        """
        Update the current node label.

        Args:
            text: Node name to display
        """
        self.current_node_label.set_text(text if text else "Ready")

    def update_queue_label(self):
        """
        Update the queue status label.
        """
        queue_size = self.gen_queue.qsize()
        # Include currently processing image in count
        total_count = queue_size + (1 if self.is_processing else 0)
        text = f"Queue: {total_count}"
        GLib.idle_add(self._update_queue_label_ui, text, total_count)

    def _update_queue_label_ui(self, text, count):
        """Update queue label text and styling."""
        self.queue_label.set_text(text)
        if count > 0:
            if not self.queue_box.has_css_class('queue-active'):
                self.queue_box.add_css_class('queue-active')
        else:
            if self.queue_box.has_css_class('queue-active'):
                self.queue_box.remove_css_class('queue-active')

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

        model_idx = self.model_dropdown.get_selected()
        model = (
            self.model_list[model_idx]
            if self.model_list and model_idx != Gtk.INVALID_LIST_POSITION
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
                if model:
                    node["inputs"]["ckpt_name"] = model

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
        GLib.idle_add(self.stop_button.set_sensitive, True)

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
        GLib.idle_add(self.stop_button.set_sensitive, False)
        self.update_queue_label()
        self.log("Queue processing complete")

    def _topo_sort(self, workflow_data):
        """
        Return node IDs in topological execution order by walking
        the dependency graph.
        """
        # Build adjacency: node -> set of nodes it depends on
        deps = {nid: set() for nid in workflow_data}
        for nid, node in workflow_data.items():
            for val in node.get('inputs', {}).values():
                if isinstance(val, list) and len(val) == 2:
                    parent = str(val[0])
                    # Strip sub-node suffixes (e.g. "207:206" -> "207:206")
                    if parent in workflow_data:
                        deps[nid].add(parent)

        order = []
        visited = set()

        def visit(nid):
            if nid in visited:
                return
            visited.add(nid)
            for parent in deps.get(nid, []):
                visit(parent)
            order.append(nid)

        for nid in workflow_data:
            visit(nid)

        return order

    def generate_logic(self, workflow_data):
        """
        Execute a single generation request.
        """
        ws = websocket.WebSocket()
        try:
            # Build execution order for progress tracking
            exec_order = self._topo_sort(workflow_data)
            # Map node_id -> index in execution order
            node_index = {nid: i for i, nid in enumerate(exec_order)}
            total_nodes = len(exec_order)
            # Tracks index of currently executing node
            current_index = 0

            ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
            payload = {"prompt": workflow_data, "client_id": CLIENT_ID}
            resp = requests.post(
                f"http://{SERVER_ADDRESS}/prompt",
                json=payload,
                timeout=10
            )
            prompt_id = resp.json().get('prompt_id')
            resp.close()

            while True:
                out = ws.recv()
                if isinstance(out, bytes):
                    GLib.idle_add(self.update_image, out[8:])
                    continue

                msg = json.loads(out)
                if msg['type'] == 'executing':
                    node_id = msg['data']['node']
                    if node_id is None:
                        break
                    current_index = node_index.get(node_id, current_index)
                    node_class = workflow_data.get(
                        node_id, {}
                    ).get('class_type', 'Unknown')
                    GLib.idle_add(self.set_current_node, node_class)
                    # Snap bar to start of this node's slice
                    GLib.idle_add(
                        self.progress_bar.set_fraction,
                        current_index / total_nodes
                    )

                elif msg['type'] == 'progress':
                    # Fractional progress within this node's slice
                    node_progress = (
                        msg['data']['value'] / msg['data']['max']
                    )
                    overall = (
                        current_index / total_nodes +
                        node_progress / total_nodes
                    )
                    GLib.idle_add(
                        self.progress_bar.set_fraction, overall
                    )

                elif msg['type'] == 'executed':
                    # Snap bar to end of this node's slice
                    GLib.idle_add(
                        self.progress_bar.set_fraction,
                        (current_index + 1) / total_nodes
                    )

                    if 'images' in msg['data']['output']:
                        img = msg['data']['output']['images'][0]
                        img_resp = requests.get(
                            f"http://{SERVER_ADDRESS}/view", params=img
                        )
                        img_data = img_resp.content
                        img_resp.close()
                        GLib.idle_add(self.update_image, img_data)

            hist_resp = requests.get(
                f"http://{SERVER_ADDRESS}/history/{prompt_id}",
                timeout=10
            )
            history = hist_resp.json().get(prompt_id, {})
            hist_resp.close()

            for node_id, node_output in history.get('outputs', {}).items():
                if workflow_data.get(node_id, {}).get(
                    "class_type"
                ) == SAVE_NODE_CLASS:
                    img = node_output['images'][0]
                    data_resp = requests.get(
                        f"http://{SERVER_ADDRESS}/view", params=img
                    )
                    data = data_resp.content
                    data_resp.close()
                    GLib.idle_add(self.update_image_final, data)
                    break
        except Exception as e:
            self.log(f"Gen error: {e}")
        finally:
            try:
                ws.close()
            except Exception:
                pass
            GLib.idle_add(self.progress_bar.set_fraction, 0.0)
            # Hide current node label
            GLib.idle_add(self.set_current_node, None)

    def _pixbuf_to_texture(self, pixbuf):
        """Convert a GdkPixbuf to a Gdk.MemoryTexture."""
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        gbytes = GLib.Bytes.new(pixels)
        fmt = (
            Gdk.MemoryFormat.R8G8B8A8 if has_alpha
            else Gdk.MemoryFormat.R8G8B8
        )
        return Gdk.MemoryTexture.new(
            width, height, fmt, gbytes, rowstride
        )

    def _show_pixbuf_in_preview(self, pixbuf):
        """Display a pixbuf in the preview picture widget."""
        self.current_pixbuf = pixbuf
        self.picture.set_paintable(self._pixbuf_to_texture(pixbuf))
        self.preview_stack.set_visible_child_name('picture')

    def _on_tab_changed(self, stack, param):
        """Restore the appropriate preview image when switching tabs."""
        name = stack.get_visible_child_name()
        if name == 'generate':
            if self.gen_pixbuf:
                self._show_pixbuf_in_preview(self.gen_pixbuf)
            else:
                self.preview_stack.set_visible_child_name('picture')
        elif name == 'gallery':
            if self.gallery_selected_pixbuf:
                self._show_pixbuf_in_preview(self.gallery_selected_pixbuf)
            else:
                self.preview_stack.set_visible_child_name('placeholder')

    def update_image(self, data):
        loader = GdkPixbuf.PixbufLoader.new()
        try:
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
            if pix:
                # Always track the latest gen image
                self.gen_pixbuf = pix
                # Only update the visible preview on the generate tab
                if self.view_stack.get_visible_child_name() == 'generate':
                    self._show_pixbuf_in_preview(pix)
        except Exception:
            try:
                loader.close()
            except Exception:
                pass

    def update_image_final(self, data):
        """Update preview and add to gallery (final image only)."""
        self.update_image(data)
        self.gallery.add_image(data)

    def _view_gallery_image(self, pixbuf):
        """Show a gallery thumbnail in the shared preview panel."""
        self.gallery_selected_pixbuf = pixbuf
        self._show_pixbuf_in_preview(pixbuf)
        # Reveal the preview panel if it's currently hidden
        if not self.preview_revealer.get_reveal_child():
            self.preview_revealer.set_hexpand(True)
            self.preview_revealer.set_reveal_child(True)
            self.preview_toggle.set_active(True)


if __name__ == "__main__":
    # Use Cairo renderer to avoid Vulkan swapchain warnings
    os.environ['GSK_RENDERER'] = 'gl'
    app = ComfyApp()
    app.run(sys.argv)
