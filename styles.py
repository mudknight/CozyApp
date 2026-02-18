#!/usr/bin/python3
"""Styles page: grid of style cards fetched from the server."""
import requests
import gi
import urllib.parse
import base64

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango, GdkPixbuf  # noqa

SERVER_ADDRESS = "127.0.0.1:8188"
THUMBNAIL_SIZE = 220


class StyleCard(Gtk.Frame):
    """A card representing a style."""

    def __init__(self, name, on_click=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.on_click = on_click
        
        self.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)

        overlay = Gtk.Overlay()
        self.set_child(overlay)
        
        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        self.picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        overlay.set_child(self.picture)

        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_vbox.set_valign(Gtk.Align.END)
        info_vbox.add_css_class('style-card-info')
        
        name_label = Gtk.Label(label=name.title())
        name_label.add_css_class('style-card-name')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        info_vbox.append(name_label)

        overlay.add_overlay(info_vbox)

        gesture = Gtk.GestureClick()
        gesture.connect('released', self._on_released)
        self.add_controller(gesture)
        
        GLib.idle_add(self._load_image)

    def _load_image(self):
        """Fetch image in a background thread."""
        def worker():
            try:
                # Replicate JS encoding for consistency
                encoded_name = base64.b64encode(self.name.encode('utf-8')).decode('ascii')
                url = f"http://{SERVER_ADDRESS}/style_editor/image/{encoded_name}"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.content
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(data)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    
                    if pixbuf:
                        GLib.idle_add(self._update_image, pixbuf)
            except Exception:
                pass

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _update_image(self, pixbuf):
        """Update the picture widget with the fetched pixbuf."""
        w, h = pixbuf.get_width(), pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        gbytes = GLib.Bytes.new(pixels)
        fmt = Gdk.MemoryFormat.R8G8B8A8 if has_alpha else Gdk.MemoryFormat.R8G8B8
        
        texture = Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)
        self.picture.set_paintable(texture)

    def _on_released(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.name)


class StylesPage(Gtk.ScrolledWindow):
    """Scrollable grid of style cards."""

    def __init__(self, on_style_selected=None, log_fn=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self.on_style_selected = on_style_selected
        self.log_fn = log_fn

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(self.outer, f'set_margin_{side}')(20)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search styles...")
        self.search_entry.set_margin_bottom(20)
        self.search_entry.connect('search-changed', self._on_search_changed)
        self.outer.append(self.search_entry)

        self.flow = Gtk.FlowBox(
            max_children_per_line=10,
            min_children_per_line=1,
            row_spacing=12,
            column_spacing=12,
            homogeneous=True,
            selection_mode=Gtk.SelectionMode.NONE,
            valign=Gtk.Align.START
        )
        self.outer.append(self.flow)
        self.set_child(self.outer)

        self._setup_css()
        GLib.idle_add(self.fetch_styles)

    def log(self, text):
        if self.log_fn:
            self.log_fn(text)
        else:
            print(text, flush=True)

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            .style-card-info {
                background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.4) 70%, transparent 100%);
                padding: 8px;
                color: white;
            }
            .style-card-name {
                font-weight: bold;
                font-size: 0.9em;
                text-shadow: 0 1px 2px rgba(0,0,0,0.5);
            }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def fetch_styles(self):
        """Fetch style data from the server."""
        def worker():
            try:
                url = f"http://{SERVER_ADDRESS}/style_editor"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    # Assuming data is a dict where keys are style names
                    GLib.idle_add(self.update_grid, data)
                else:
                    self.log(f"Failed to fetch styles: {resp.status_code}")
            except Exception as e:
                self.log(f"Error fetching styles: {e}")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def update_grid(self, styles):
        """Update the FlowBox with style cards."""
        self._clear_grid()
        for name in styles.keys():
            card = StyleCard(name, self.on_style_selected)
            self.flow.append(card)

    def _clear_grid(self):
        while True:
            child = self.flow.get_first_child()
            if child is None:
                break
            self.flow.remove(child)

    def _on_search_changed(self, entry):
        search_text = entry.get_text().lower()
        child = self.flow.get_first_child()
        while child:
            card = child.get_child()
            if isinstance(card, StyleCard):
                visible = search_text in card.name.lower()
                child.set_visible(visible)
            child = child.get_next_sibling()

    @property
    def widget(self):
        return self
