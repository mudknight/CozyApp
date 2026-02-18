#!/usr/bin/python3
"""Characters page: grid of character cards fetched from the server."""
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


class CharacterCard(Gtk.Frame):
    """A card representing a character."""

    def __init__(self, name, data, on_click=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.data = data
        self.on_click = on_click
        
        # Ensure the card itself has a fixed square size
        self.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)

        # Overlay allows us to stack text on top of the image
        overlay = Gtk.Overlay()
        self.set_child(overlay)
        
        # Image area
        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        self.picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        # Placeholder/Base styling
        overlay.set_child(self.picture)

        # Info overlay (bottom-aligned)
        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_vbox.set_valign(Gtk.Align.END)
        info_vbox.add_css_class('character-card-info')
        
        # Name label
        name_label = Gtk.Label(label=name.title())
        name_label.add_css_class('character-card-name')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        info_vbox.append(name_label)

        # Category tag (if any)
        categories = data.get('categories', '').strip()
        if categories:
            cat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for cat in categories.split(','):
                cat = cat.strip()
                if not cat:
                    continue
                badge = Gtk.Label(label=cat)
                badge.add_css_class('caption')
                badge.add_css_class('pill')
                cat_box.append(badge)
            info_vbox.append(cat_box)

        overlay.add_overlay(info_vbox)

        # Click handling
        gesture = Gtk.GestureClick()
        gesture.connect('released', self._on_released)
        self.add_controller(gesture)
        
        # Start loading image
        GLib.idle_add(self._load_image)

    def _load_image(self):
        """Fetch image in a background thread."""
        def worker():
            try:
                # Replicate JS: btoa(unescape(encodeURIComponent(name)))
                # For UTF-8, this is equivalent to base64 encoding the UTF-8 bytes
                encoded_name = base64.b64encode(self.name.encode('utf-8')).decode('ascii')
                url = f"http://{SERVER_ADDRESS}/character_editor/image/{encoded_name}"
                print(f"[DEBUG] Fetching image for {self.name}: {url}")
                resp = requests.get(url, timeout=10)
                print(f"[DEBUG] Response for {self.name}: {resp.status_code}, content-type: {resp.headers.get('content-type')}")
                if resp.status_code == 200:
                    data = resp.content
                    print(f"[DEBUG] Received {len(data)} bytes for {self.name}")
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(data)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    
                    if pixbuf:
                        print(f"[DEBUG] Successfully loaded pixbuf for {self.name} ({pixbuf.get_width()}x{pixbuf.get_height()})")
                        GLib.idle_add(self._update_image, pixbuf)
                else:
                    print(f"[DEBUG] Failed to load image for {self.name}: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Error loading image for {self.name}: {e}")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _update_image(self, pixbuf):
        """Update the picture widget with the fetched pixbuf."""
        # Scale to thumbnail size
        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = max(THUMBNAIL_SIZE / w, THUMBNAIL_SIZE / h)
        
        # Convert to texture
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        gbytes = GLib.Bytes.new(pixels)
        fmt = Gdk.MemoryFormat.R8G8B8A8 if has_alpha else Gdk.MemoryFormat.R8G8B8
        
        texture = Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)
        self.picture.set_paintable(texture)

    def _on_released(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.name, self.data)


class CharactersPage(Gtk.ScrolledWindow):
    """Scrollable grid of character cards."""

    def __init__(self, on_character_selected=None, log_fn=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self.on_character_selected = on_character_selected
        self.log_fn = log_fn

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(self.outer, f'set_margin_{side}')(20)

        # Search entry
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search characters...")
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

        self.all_characters = {}
        
        # Add CSS for pills and cards
        self._setup_css()

        # Initial fetch
        GLib.idle_add(self.fetch_characters)

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            .pill {
                background-color: alpha(@accent_bg_color, 0.2);
                color: @accent_fg_color;
                border-radius: 12px;
                padding: 1px 6px;
                font-size: 0.7em;
                font-weight: bold;
            }
            .card {
                border-radius: 12px;
                overflow: hidden;
                border: 1px solid alpha(@view_fg_color, 0.1);
            }
            .card:hover {
                background-color: alpha(@view_fg_color, 0.05);
                border-color: alpha(@accent_bg_color, 0.3);
            }
            .character-card-info {
                background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.4) 70%, transparent 100%);
                padding: 8px;
                color: white;
            }
            .character-card-name {
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

    def log(self, text):
        if self.log_fn:
            self.log_fn(text)
        else:
            print(text, flush=True)

    def fetch_characters(self):
        """Fetch character data from the server."""
        def worker():
            try:
                url = f"http://{SERVER_ADDRESS}/character_editor"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    GLib.idle_add(self.update_grid, data)
                else:
                    self.log(f"Failed to fetch characters: {resp.status_code}")
            except Exception as e:
                self.log(f"Error fetching characters: {e}")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def update_grid(self, characters):
        """Update the FlowBox with character cards."""
        self.all_characters = characters
        self._clear_grid()
        
        for name, data in characters.items():
            card = CharacterCard(name, data, self.on_character_selected)
            self.flow.append(card)

    def _clear_grid(self):
        while True:
            child = self.flow.get_first_child()
            if child is None:
                break
            self.flow.remove(child)

    def _on_search_changed(self, entry):
        search_text = entry.get_text().lower()
        
        # Iterate over FlowBoxChildren
        child = self.flow.get_first_child()
        while child:
            # The card is the child of the FlowBoxChild
            card = child.get_child()
            if isinstance(card, CharacterCard):
                visible = search_text in card.name.lower() or \
                          search_text in card.data.get('character', '').lower() or \
                          search_text in card.data.get('categories', '').lower()
                child.set_visible(visible)
            child = child.get_next_sibling()

    @property
    def widget(self):
        return self
