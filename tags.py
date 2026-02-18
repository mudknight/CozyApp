#!/usr/bin/python3
"""Tags page: grid of tag cards fetched from the server."""
import requests
import gi
import config

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango  # noqa

CARD_WIDTH = 220
CARD_HEIGHT = 100


class TagCard(Gtk.Frame):
    """A card showing a tag name with its positive and negative prompts."""

    def __init__(self, name, data, on_click=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.data = data
        self.on_click = on_click

        self.set_size_request(CARD_WIDTH, CARD_HEIGHT)

        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=10,
            margin_end=10
        )
        self.set_child(vbox)

        # Tag name
        name_label = Gtk.Label(label=name.replace('_', ' ').title())
        name_label.add_css_class('tag-card-name')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        vbox.append(name_label)

        # Positive prompt preview (green)
        positive = data.get('positive', '').strip()
        if positive:
            pos_label = Gtk.Label(label=positive)
            pos_label.add_css_class('tag-positive')
            pos_label.set_halign(Gtk.Align.START)
            pos_label.set_ellipsize(Pango.EllipsizeMode.END)
            pos_label.set_max_width_chars(30)
            vbox.append(pos_label)

        # Negative prompt preview (red)
        negative = data.get('negative', '').strip()
        if negative:
            neg_label = Gtk.Label(label=negative)
            neg_label.add_css_class('tag-negative')
            neg_label.set_halign(Gtk.Align.START)
            neg_label.set_ellipsize(Pango.EllipsizeMode.END)
            neg_label.set_max_width_chars(30)
            vbox.append(neg_label)

        # Click handling
        gesture = Gtk.GestureClick()
        gesture.connect('released', self._on_released)
        self.add_controller(gesture)

    def _on_released(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.name)


class TagsPage(Gtk.ScrolledWindow):
    """Scrollable grid of tag cards."""

    def __init__(self, on_tag_selected=None, log_fn=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self.on_tag_selected = on_tag_selected
        self.log_fn = log_fn

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(self.outer, f'set_margin_{side}')(20)

        # Search entry
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search tags..."
        )
        self.search_entry.set_margin_bottom(20)
        self.search_entry.connect(
            'search-changed', self._on_search_changed
        )
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
        GLib.idle_add(self.fetch_tags)

    def log(self, text):
        if self.log_fn:
            self.log_fn(text)
        else:
            print(text, flush=True)

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            .tag-card-name {
                font-weight: bold;
                font-size: 0.95em;
            }
            .tag-positive {
                font-size: 0.78em;
                color: #4caf50;
            }
            .tag-negative {
                font-size: 0.78em;
                color: #ef5350;
            }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def fetch_tags(self):
        """Fetch tag data from the server."""
        def worker():
            try:
                url = f"http://{config.server_address()}/tag_editor"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    GLib.idle_add(self.update_grid, data)
                else:
                    self.log(f"Failed to fetch tags: {resp.status_code}")
            except Exception as e:
                self.log(f"Error fetching tags: {e}")

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def update_grid(self, tags):
        """Update the FlowBox with tag cards."""
        self._clear_grid()
        for name, data in tags.items():
            card = TagCard(name, data, self.on_tag_selected)
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
            if isinstance(card, TagCard):
                pos = card.data.get('positive', '').lower()
                neg = card.data.get('negative', '').lower()
                visible = (
                    search_text in card.name.lower()
                    or search_text in pos
                    or search_text in neg
                )
                child.set_visible(visible)
            child = child.get_next_sibling()

    @property
    def widget(self):
        return self
