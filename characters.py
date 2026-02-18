#!/usr/bin/python3
"""Characters page: grid of character cards fetched from the server."""
import requests
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango  # noqa

SERVER_ADDRESS = "127.0.0.1:8188"


class CharacterCard(Gtk.Frame):
    """A card representing a character."""

    def __init__(self, name, data, on_click=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.data = data
        self.on_click = on_click

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        # Name label
        name_label = Gtk.Label(label=name.title())
        name_label.add_css_class('heading')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        vbox.append(name_label)

        # Character tags
        tags_text = data.get('character', '').strip()
        if tags_text:
            tags_label = Gtk.Label(label=tags_text)
            tags_label.add_css_class('dim-label')
            tags_label.set_halign(Gtk.Align.START)
            tags_label.set_lines(2)
            tags_label.set_ellipsize(Pango.EllipsizeMode.END)
            tags_label.set_wrap(True)
            tags_label.set_max_width_chars(30)
            vbox.append(tags_label)

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
                # A simple pill style
                badge.set_margin_start(4)
                badge.set_margin_end(4)
                cat_box.append(badge)
            vbox.append(cat_box)

        self.set_child(vbox)

        # Click handling
        gesture = Gtk.GestureClick()
        gesture.connect('released', self._on_released)
        self.add_controller(gesture)

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
                background-color: alpha(@accent_bg_color, 0.1);
                color: @accent_fg_color;
                border-radius: 12px;
                padding: 2px 8px;
                font-size: 0.8em;
                font-weight: bold;
            }
            .card:hover {
                background-color: alpha(@view_fg_color, 0.05);
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
