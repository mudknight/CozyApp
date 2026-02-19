#!/usr/bin/python3 -u
import sys
import json
import threading
import requests
import tempfile
import os
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '5')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Adw, GLib, Gio, Gdk, GdkPixbuf, Pango  # noqa
import config  # noqa
from generate import GeneratePage  # noqa
from gallery import GalleryPage  # noqa
from presets import PresetsPage  # noqa


def setup_language_manager():
    """Register the custom prompt-tags language before any GtkSource use."""
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
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.lang', delete=False
    ) as f:
        f.write(lang_xml)
        lang_file = f.name

    lang_manager = GtkSource.LanguageManager.get_default()
    dirs = lang_manager.get_search_path()
    dirs.append(os.path.dirname(lang_file))
    lang_manager.set_search_path(dirs)
    return lang_file


# GtkSource is imported for setup_language_manager; import it here so
# the gi version requirement is satisfied before generate.py uses it.
from gi.repository import GtkSource  # noqa


class ComfyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(
            application_id="com.example.comfy_gen",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
            **kwargs
        )
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.workflow_file = None
        config.load()
        self._lang_file = setup_language_manager()

    def on_shutdown(self, app):
        """Remove the temporary language file on shutdown."""
        if self._lang_file and os.path.exists(self._lang_file):
            try:
                os.unlink(self._lang_file)
            except Exception:
                pass

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        self.workflow_file = "workflow.json"
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
        icon_theme = Gtk.IconTheme.get_for_display(
            Gdk.Display.get_default()
        )
        icon_theme.add_search_path(
            os.path.join(os.path.dirname(__file__), "assets")
        )
        self.win = ComfyWindow(
            application=app, workflow_file=self.workflow_file
        )
        self.win.present()


class ComfyWindow(Adw.ApplicationWindow):
    def __init__(self, workflow_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("CozyApp")
        self.set_default_size(1200, 900)
        self.workflow_file = workflow_file

        self.setup_css()

        # Preview state
        self.current_pixbuf = None
        self.gen_pixbuf = None
        self.gallery_selected_pixbuf = None
        self.magnifier_size = 200
        self.magnifier_enabled = False
        self._preview_user_preference = True
        self.last_cursor_x = 0
        self.last_cursor_y = 0
        self._preview_popover = None

        self.connect("close-request", self.on_close_request)

        # Root layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header bar
        self.header = Adw.HeaderBar()
        self.main_box.append(self.header)

        self.magnifier_toggle = Gtk.ToggleButton(
            icon_name="system-search-symbolic"
        )
        self.magnifier_toggle.set_tooltip_text("Toggle magnifier")
        self.magnifier_toggle.connect("toggled", self.on_toggle_magnifier)

        self.preview_toggle = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic", active=True
        )
        self.preview_toggle.connect("toggled", self.on_toggle_preview)

        self.menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=self.create_overflow_menu()
        )
        self.header.pack_end(self.menu_button)
        self.header.pack_end(self.preview_toggle)
        self.header.pack_end(self.magnifier_toggle)

        # View stack + switcher
        self.view_stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(
            stack=self.view_stack,
            policy=Adw.ViewSwitcherPolicy.WIDE
        )
        self.header.set_title_widget(switcher)

        # Outer box holds the view stack and the preview panel
        self.outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.outer_box)
        self.main_box.append(self.toast_overlay)

        self.view_stack.set_hexpand(True)
        self.outer_box.append(self.view_stack)

        # Generate page
        self.generate_page = GeneratePage(
            log_fn=self.log,
            on_image_update=self.update_image,
            on_image_final=self.update_image_final,
            on_show_toast=self._show_toast,
        )
        self.view_stack.add_titled_with_icon(
            self.generate_page.widget, 'generate', 'Generate',
            'applications-graphics-symbolic'
        )

        # Preview panel (shared across tabs)
        self._build_preview_panel()

        # Gallery page
        self.gallery = GalleryPage(
            on_view_image=self._view_gallery_image,
            on_delete_image=self._on_delete_image
        )
        self.view_stack.add_titled_with_icon(
            self.gallery.widget, 'gallery', 'Gallery',
            'image-x-generic-symbolic'
        )

        # Presets page (Characters, Styles, Tags combined)
        self.presets = PresetsPage(
            on_character_selected=self._on_character_selected,
            on_style_selected=self._on_style_selected,
            on_tag_selected=self._on_tag_selected,
            log_fn=self.log
        )
        self.view_stack.add_titled_with_icon(
            self.presets.widget, 'presets', 'Presets',
            'bookmark-new-symbolic'
        )

        self.view_stack.connect(
            'notify::visible-child', self._on_tab_changed
        )

        self.setup_keybinds()
        self.generate_page.fetch_node_info()

        if self.workflow_file:
            self.generate_page.load_workflow(self.workflow_file)

    # ------------------------------------------------------------------
    # Preview panel construction
    # ------------------------------------------------------------------

    def _build_preview_panel(self):
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

        picture_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_width=False,
            propagate_natural_height=False,
            vexpand=True,
            hexpand=True
        )

        self.picture_overlay = Gtk.Overlay()
        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN,
            can_shrink=True
        )
        self.picture_overlay.set_child(self.picture)

        self.magnifier_frame = Gtk.Frame(
            css_classes=['magnifier-frame']
        )
        self.magnifier_frame.set_visible(False)
        self.magnifier_frame.set_size_request(
            self.magnifier_size, self.magnifier_size
        )
        self.magnifier_frame.set_can_target(False)

        self.magnifier_picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.FILL
        )
        self.magnifier_picture.set_can_target(False)
        self.magnifier_frame.set_child(self.magnifier_picture)
        self.picture_overlay.add_overlay(self.magnifier_frame)

        picture_scroll.set_child(self.picture_overlay)

        self.preview_stack = Gtk.Stack(vexpand=True, hexpand=True)
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

        # Motion / scroll controllers for magnifier
        motion_ctrl = Gtk.EventControllerMotion()
        motion_ctrl.connect("motion", self.on_picture_motion)
        motion_ctrl.connect("leave", self.on_picture_leave)
        motion_ctrl.connect("enter", self.on_picture_enter)
        self.picture.add_controller(motion_ctrl)

        scroll_ctrl = Gtk.EventControllerScroll()
        scroll_ctrl.set_flags(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctrl.connect("scroll", self.on_picture_scroll)
        self.picture.add_controller(scroll_ctrl)

        self.default_cursor = None
        self.crosshair_cursor = Gdk.Cursor.new_from_name("crosshair")

        # Right-click context menu
        preview_gesture = Gtk.GestureClick()
        preview_gesture.set_button(3)
        preview_gesture.connect('pressed', self._on_preview_right_click)
        self.picture.add_controller(preview_gesture)

    # ------------------------------------------------------------------
    # Overflow menu
    # ------------------------------------------------------------------

    def create_overflow_menu(self):
        """Create the overflow menu and register its actions."""
        menu = Gio.Menu()
        menu.append("Reload", "app.reload")
        menu.append("Settings", "app.settings")
        menu.append("About", "app.about")

        reload_action = Gio.SimpleAction.new("reload", None)
        reload_action.connect("activate", self.on_reload)
        self.get_application().add_action(reload_action)

        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self.on_show_settings)
        self.get_application().add_action(settings_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_show_about)
        self.get_application().add_action(about_action)

        return menu

    def on_reload(self, action, param):
        """Reload style/model lists and all preset sub-pages."""
        self.generate_page.fetch_node_info()
        self.presets.refresh()

    def on_show_settings(self, action, param):
        """Show the settings dialog."""
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Settings")

        page = Adw.PreferencesPage()
        dialog.add(page)

        # --- Server group ---
        server_group = Adw.PreferencesGroup(
            title="ComfyUI Server",
            description="Address of the running ComfyUI instance."
        )
        page.add(server_group)

        host_row = Adw.EntryRow(title="Host")
        host_row.set_text(config.get("host"))
        server_group.add(host_row)

        port_adj = Gtk.Adjustment(
            value=config.get("port"),
            lower=1, upper=65535, step_increment=1
        )
        port_row = Adw.SpinRow(title="Port", adjustment=port_adj)
        server_group.add(port_row)

        # --- Tag blacklist group ---
        bl_group = Adw.PreferencesGroup(
            title="Tag Blacklist",
            description="Tags listed here are hidden from autocompletion."
        )
        page.add(bl_group)

        # Working copy so we can cancel without saving
        blacklist = list(config.get("tag_blacklist") or [])

        def _add_tag_row(tag):
            """Append a row for *tag* with an inline remove button."""
            row = Adw.ActionRow(title=tag)
            remove_btn = Gtk.Button(
                icon_name="list-remove-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat", "circular"]
            )
            remove_btn.set_tooltip_text("Remove")

            def on_remove(_btn, r=row, t=tag):
                if t in blacklist:
                    blacklist.remove(t)
                bl_group.remove(r)

            remove_btn.connect("clicked", on_remove)
            row.add_suffix(remove_btn)
            bl_group.add(row)

        for tag in blacklist:
            _add_tag_row(tag)

        # Entry row for adding new tags
        add_row = Adw.EntryRow(title="Add tag…")

        def on_add_tag(entry_row):
            tag = entry_row.get_text().strip().lower().replace(' ', '_')
            if tag and tag not in blacklist:
                blacklist.append(tag)
                # Insert the new tag row before the entry row
                _add_tag_row(tag)
            entry_row.set_text("")

        add_row.connect("apply", on_add_tag)
        add_row.set_show_apply_button(True)
        bl_group.add(add_row)

        def on_close(_):
            config.set("host", host_row.get_text().strip())
            config.set("port", int(port_adj.get_value()))
            config.set("tag_blacklist", list(blacklist))
            config.save()
            # Apply to the live tag completion instance
            self.generate_page.tag_completion.set_blacklist(blacklist)

        dialog.connect("closed", on_close)
        dialog.present(self)

    def on_show_about(self, action, param):
        """Show the about dialog."""
        dialog = Adw.AboutDialog.new()
        dialog.set_application_name("CozyApp")
        dialog.set_version("1.0.0")
        dialog.set_application_icon("com.example.comfy_gen")
        dialog.set_website("https://example.com")
        dialog.set_issue_url("https://github.com/example/cozyapp/issues")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_comments("A simple GTK4 application with an about page.")
        dialog.add_acknowledgement_section(
            "Contributors", ["Contributor 1", "Contributor 2"]
        )
        dialog.present(self)

    def on_close_request(self, window):
        """Clean up debounce timers before closing."""
        for timer_id in self.generate_page.debounce_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self.generate_page.debounce_timers.clear()
        return False

    # ------------------------------------------------------------------
    # Keybinds
    # ------------------------------------------------------------------

    def setup_keybinds(self):
        """Register window-level keyboard shortcuts."""
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_window_key_pressed)
        self.add_controller(key_ctrl)

    def on_window_key_pressed(self, controller, keyval, keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt = state & Gdk.ModifierType.ALT_MASK

        if ctrl and keyval == Gdk.KEY_Return:
            self.generate_page.queue_generate()
            return True
        elif ctrl and keyval == Gdk.KEY_Escape:
            self.generate_page.stop()
            return True
        elif alt and keyval == Gdk.KEY_Up:
            self.generate_page.adjust_batch(1)
            return True
        elif alt and keyval == Gdk.KEY_Down:
            self.generate_page.adjust_batch(-1)
            return True
        return False

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------

    def setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            .gallery-thumb { border-radius: 8px; }
            flowboxchild:selected .gallery-thumb {
                outline: 2px solid @accent_color;
                box-shadow: 0 0 0 3px alpha(@accent_color, 0.35);
                border-radius: 8px;
            }
            revealer { background-color: transparent; border: none; }
            .preview-panel {
                background-color: @card_bg_color;
                border-left: none;
            }
            textview.view {
                border: none;
                border-radius: 8px;
                background-color: @view_bg_color;
            }
            .prompt-focused {
                outline: 2px solid @accent_bg_color;
            }
            .quick-settings-card {
                background-color: @card_bg_color;
                border-radius: 12px;
                border: 1px solid alpha(currentColor, 0.07);
            }
            .quick-settings-card:hover {
                background-color: @card_bg_color;
            }
            gutter {
                background-color: alpha(@view_fg_color, 0.05);
                border-right: 1px solid alpha(@view_fg_color, 0.1);
            }
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
            .about-image { border-radius: 16px; }
            .queue-group > button {
                border-top-right-radius: 0;
                border-bottom-right-radius: 0;
            }
            .queue-group > spinbutton {
                border-top-left-radius: 0;
                border-bottom-left-radius: 0;
                border-left-width: 0;
            }
            .job-processing {
                background-color: alpha(@accent_bg_color, 0.1);
            }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, text):
        print(f"{text}", flush=True)

    # ------------------------------------------------------------------
    # Preview panel helpers
    # ------------------------------------------------------------------

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

    def _set_preview_visible(self, visible):
        """Show or hide the preview panel, keeping the toggle in sync."""
        self.preview_revealer.set_hexpand(visible)
        self.preview_revealer.set_reveal_child(visible)
        self.preview_toggle.handler_block_by_func(self.on_toggle_preview)
        self.preview_toggle.set_active(visible)
        self.preview_toggle.handler_unblock_by_func(self.on_toggle_preview)

    def on_toggle_preview(self, btn):
        is_active = btn.get_active()
        self._preview_user_preference = is_active
        self.preview_revealer.set_hexpand(is_active)
        self.preview_revealer.set_reveal_child(is_active)

    def on_toggle_magnifier(self, btn):
        self.magnifier_enabled = btn.get_active()
        if not self.magnifier_enabled and self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(False)

    # ------------------------------------------------------------------
    # Tab change
    # ------------------------------------------------------------------

    def _on_tab_changed(self, stack, param):
        """Auto-hide the preview panel on tabs that don't use it."""
        name = stack.get_visible_child_name()
        if name in ('generate', 'gallery'):
            self.preview_toggle.set_sensitive(True)
            self._set_preview_visible(self._preview_user_preference)
            if name == 'generate':
                if self.gen_pixbuf:
                    self._show_pixbuf_in_preview(self.gen_pixbuf)
                else:
                    self.preview_stack.set_visible_child_name('picture')
            else:
                if self.gallery_selected_pixbuf:
                    self._show_pixbuf_in_preview(
                        self.gallery_selected_pixbuf
                    )
                else:
                    self.preview_stack.set_visible_child_name('placeholder')
        else:
            self.preview_toggle.set_sensitive(False)
            self._set_preview_visible(False)
            self.preview_stack.set_visible_child_name('placeholder')

    # ------------------------------------------------------------------
    # Image update callbacks (called from GeneratePage)
    # ------------------------------------------------------------------

    def update_image(self, data):
        """Display an interim preview image."""
        loader = GdkPixbuf.PixbufLoader.new()
        try:
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
            if pix:
                self.gen_pixbuf = pix
                if self.view_stack.get_visible_child_name() == 'generate':
                    self._show_pixbuf_in_preview(pix)
        except Exception:
            try:
                loader.close()
            except Exception:
                pass

    def update_image_final(self, data, image_info=None):
        """Display the final image and add it to the gallery."""
        self.update_image(data)
        self.gallery.add_image(data, image_info)

    # ------------------------------------------------------------------
    # Delete image callback (called from GalleryPage)
    # ------------------------------------------------------------------

    def _on_delete_image(self, image_info, remove_fn):
        """Delete a generated image via the ComfyUI api-tools endpoint."""
        def worker():
            if not image_info or not image_info.get('filename'):
                GLib.idle_add(
                    self._show_toast,
                    'No filename metadata — cannot delete this image.'
                )
                return

            filename = image_info['filename']
            url = (
                f"http://{config.server_address()}"
                f"/api-tools/v1/images/output/{filename}"
            )
            try:
                resp = requests.delete(url, timeout=10)
                if resp.status_code == 200:
                    GLib.idle_add(remove_fn)
                    GLib.idle_add(self._show_toast, f'Deleted {filename}')
                elif resp.status_code == 404:
                    GLib.idle_add(
                        self._show_toast,
                        'Install ComfyUI-api-tools to enable deletion'
                    )
                else:
                    GLib.idle_add(
                        self._show_toast,
                        f'Delete failed (HTTP {resp.status_code})'
                    )
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Delete error: {e}')

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Toast
    # ------------------------------------------------------------------

    def _show_toast(self, message: str):
        """Show an Adw.Toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)

    # ------------------------------------------------------------------
    # Cross-page callbacks
    # ------------------------------------------------------------------

    def _view_gallery_image(self, pixbuf):
        """Show a gallery thumbnail in the shared preview panel."""
        self.gallery_selected_pixbuf = pixbuf
        self._show_pixbuf_in_preview(pixbuf)
        if not self.preview_revealer.get_reveal_child():
            self._preview_user_preference = True
            self._set_preview_visible(True)

    def _on_character_selected(self, name, data):
        """Insert a character tag and switch to the generate tab."""
        self.generate_page.insert_character(name)
        self._show_toast(f"Added {name.title()}")
        self.view_stack.set_visible_child_name('generate')

    def _on_style_selected(self, style_name):
        """Set the style dropdown and switch to the generate tab."""
        if self.generate_page.set_style(style_name):
            self._show_toast(f"Style set to {style_name}")
        else:
            self.log(f"Style {style_name} not found in dropdown list")
        self.view_stack.set_visible_child_name('generate')

    def _on_tag_selected(self, tag_name):
        """Insert a tag reference and switch to the generate tab."""
        self.generate_page.insert_tag(tag_name)
        self._show_toast(f"Added tag:{tag_name}")
        self.view_stack.set_visible_child_name('generate')

    # ------------------------------------------------------------------
    # Magnifier
    # ------------------------------------------------------------------

    def is_image_downscaled(self):
        if not self.current_pixbuf:
            return False
        if not self.picture.get_paintable():
            return False
        return (
            self.picture.get_width() < self.current_pixbuf.get_width()
            or self.picture.get_height() < self.current_pixbuf.get_height()
        )

    def on_picture_enter(self, controller, x, y):
        if self.is_image_downscaled() and self.magnifier_enabled:
            self.picture.set_cursor(self.crosshair_cursor)
        else:
            if self.default_cursor is None:
                self.default_cursor = self.picture.get_cursor()
            self.picture.set_cursor(self.default_cursor)

    def on_picture_scroll(self, controller, dx, dy):
        if not self.is_image_downscaled() or not self.magnifier_enabled:
            return False
        size_change = -10 if dy > 0 else 10
        new_size = max(100, min(400, self.magnifier_size + size_change))
        if new_size != self.magnifier_size:
            self.magnifier_size = new_size
            self.magnifier_frame.set_size_request(new_size, new_size)
            self.update_magnifier(self.last_cursor_x, self.last_cursor_y)
        return True

    def on_picture_motion(self, controller, x, y):
        self.last_cursor_x = x
        self.last_cursor_y = y
        if not self.is_image_downscaled() or not self.magnifier_enabled:
            if self.magnifier_frame.get_visible():
                self.magnifier_frame.set_visible(False)
            self.picture.set_cursor(self.default_cursor)
            return
        self.picture.set_cursor(self.crosshair_cursor)
        if not self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(True)
        self.update_magnifier(x, y)

    def on_picture_leave(self, controller):
        if self.magnifier_frame.get_visible():
            self.magnifier_frame.set_visible(False)
        self.picture.set_cursor(self.default_cursor)

    def update_magnifier(self, x, y):
        """Crop and display the magnified region at the cursor."""
        if not self.current_pixbuf:
            return
        orig_w = self.current_pixbuf.get_width()
        orig_h = self.current_pixbuf.get_height()
        disp_w = self.picture.get_width()
        disp_h = self.picture.get_height()
        if disp_w == 0 or disp_h == 0:
            return

        scale = min(disp_w / orig_w, disp_h / orig_h)
        x_off = (disp_w - orig_w * scale) / 2
        y_off = (disp_h - orig_h * scale) / 2

        img_x = max(0, min((x - x_off) / scale, orig_w))
        img_y = max(0, min((y - y_off) / scale, orig_h))

        mag_size = self.magnifier_size // 2
        half = mag_size // 2

        crop_x = max(0, min(int(img_x - half), orig_w - mag_size))
        crop_y = max(0, min(int(img_y - half), orig_h - mag_size))

        if mag_size <= 0:
            return

        try:
            sub = self.current_pixbuf.new_subpixbuf(
                crop_x, crop_y, mag_size, mag_size
            )
            gbytes = GLib.Bytes.new(sub.get_pixels())
            fmt = (
                Gdk.MemoryFormat.R8G8B8A8 if sub.get_has_alpha()
                else Gdk.MemoryFormat.R8G8B8
            )
            texture = Gdk.MemoryTexture.new(
                sub.get_width(), sub.get_height(),
                fmt, gbytes, sub.get_rowstride()
            )
            self.magnifier_picture.set_paintable(texture)

            mag_x = max(
                0, min(x - self.magnifier_size / 2,
                       disp_w - self.magnifier_size)
            )
            mag_y = max(
                0, min(y - self.magnifier_size / 2,
                       disp_h - self.magnifier_size)
            )
            self.magnifier_frame.set_margin_start(int(mag_x))
            self.magnifier_frame.set_margin_top(int(mag_y))
            self.magnifier_frame.set_halign(Gtk.Align.START)
            self.magnifier_frame.set_valign(Gtk.Align.START)
        except Exception as e:
            self.log(f"Magnifier error: {e}")

    # ------------------------------------------------------------------
    # Preview context menu
    # ------------------------------------------------------------------

    def _on_preview_right_click(self, gesture, n_press, x, y):
        if not self.current_pixbuf:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._show_preview_context_menu(x, y)

    def _show_preview_context_menu(self, x, y):
        if self._preview_popover:
            self._preview_popover.popdown()

        popover = Gtk.Popover()
        popover.set_parent(self.picture)
        popover.set_has_arrow(False)
        popover.set_position(Gtk.PositionType.BOTTOM)

        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=4, margin_bottom=4,
            margin_start=4, margin_end=4
        )

        def add_btn(label, cb):
            btn = Gtk.Button(label=label, has_frame=False)
            btn.set_halign(Gtk.Align.FILL)
            btn.connect('clicked', lambda b: (popover.popdown(), cb()))
            box.append(btn)

        add_btn('Copy to Clipboard', self._preview_copy)
        add_btn('Save to\u2026', self._preview_save)
        box.append(Gtk.Separator())
        add_btn('Delete', self._preview_delete)

        popover.set_child(box)
        self._preview_popover = popover
        popover.popup()

    def _preview_copy(self):
        if not self.current_pixbuf:
            return
        texture = self._pixbuf_to_texture(self.current_pixbuf)
        success, buf = self.current_pixbuf.save_to_bufferv("png", [], [])
        if success:
            gbytes = GLib.Bytes.new(buf)
            content = Gdk.ContentProvider.new_union([
                Gdk.ContentProvider.new_for_bytes("image/png", gbytes),
                Gdk.ContentProvider.new_for_value(texture)
            ])
        else:
            content = Gdk.ContentProvider.new_for_value(texture)
        self.get_clipboard().set_content(content)

    def _preview_save(self):
        if not self.current_pixbuf:
            return
        pixbuf = self.current_pixbuf
        dialog = Gtk.FileChooserNative(
            title='Save Image',
            action=Gtk.FileChooserAction.SAVE,
            accept_label='Save',
            cancel_label='Cancel',
            transient_for=self
        )
        dialog.set_current_name('image.png')
        dialog.connect(
            'response',
            lambda d, r: self._on_preview_save_response(d, r, pixbuf)
        )
        dialog.show()

    def _on_preview_save_response(self, dialog, response, pixbuf):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            if not path.lower().endswith('.png'):
                path += '.png'
            try:
                pixbuf.savev(path, 'png', [], [])
            except Exception as e:
                self.log(f'Preview save error: {e}')
        dialog.destroy()

    def _preview_delete(self):
        pixbuf = self.current_pixbuf
        if pixbuf is None:
            return
        if self.gallery_selected_pixbuf is pixbuf:
            self.gallery.delete_by_pixbuf(pixbuf)
            self.gallery_selected_pixbuf = None
        if self.gen_pixbuf is pixbuf:
            self.gen_pixbuf = None
        self.current_pixbuf = None
        self.preview_stack.set_visible_child_name('placeholder')


if __name__ == "__main__":
    # Use GL renderer to avoid Vulkan swapchain warnings
    os.environ['GSK_RENDERER'] = 'gl'
    app = ComfyApp()
    app.run(sys.argv)
