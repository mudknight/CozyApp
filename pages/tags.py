#!/usr/bin/python3
"""Tags page: grid of tag cards with full CRUD support."""
import threading
import requests
import gi
import config
from widgets import crud_dialog

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango  # noqa

CARD_WIDTH = 220
CARD_HEIGHT = 100


class TagCard(Gtk.Frame):
    """A card showing a tag's prompts with right-click CRUD menu."""

    def __init__(self, name, data, on_click=None,
                 on_edit=None, on_delete=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.data = data
        self.on_click = on_click
        self.on_edit = on_edit
        self.on_delete = on_delete

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

        # Positive prompt preview
        positive = data.get('positive', '').strip()
        if positive:
            pos_label = Gtk.Label(label=positive)
            pos_label.add_css_class('tag-positive')
            pos_label.set_halign(Gtk.Align.START)
            pos_label.set_ellipsize(Pango.EllipsizeMode.END)
            pos_label.set_max_width_chars(30)
            vbox.append(pos_label)

        # Negative prompt preview
        negative = data.get('negative', '').strip()
        if negative:
            neg_label = Gtk.Label(label=negative)
            neg_label.add_css_class('tag-negative')
            neg_label.set_halign(Gtk.Align.START)
            neg_label.set_ellipsize(Pango.EllipsizeMode.END)
            neg_label.set_max_width_chars(30)
            vbox.append(neg_label)

        # Left click — select
        left_gesture = Gtk.GestureClick(button=1)
        left_gesture.connect('released', self._on_left_click)
        self.add_controller(left_gesture)

        # Right click — context menu
        right_gesture = Gtk.GestureClick(button=3)
        right_gesture.connect('released', self._on_right_click)
        self.add_controller(right_gesture)

    def _on_left_click(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.name)

    def _on_right_click(self, gesture, n_press, x, y):
        crud_dialog.show_card_context_menu(
            self, x, y,
            on_edit=lambda: self.on_edit(self.name, self.data),
            on_delete=lambda: self.on_delete(self.name)
        )


class TagsPage(Gtk.Box):
    """Scrollable grid of tag cards with CRUD toolbar."""

    def __init__(self, on_tag_selected=None, log_fn=None):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            vexpand=True
        )
        self.on_tag_selected = on_tag_selected
        self.log_fn = log_fn
        self.all_tags = {}

        # Search bar row — outside the scroll area so it stays visible.
        search_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=20,
            margin_start=20,
            margin_end=20,
            margin_bottom=12
        )
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search tags...",
            hexpand=True
        )
        self.search_entry.connect(
            'search-changed', self._on_search_changed
        )
        search_row.append(self.search_entry)

        add_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_btn.set_tooltip_text("Add tag")
        add_btn.connect('clicked', self._on_add_clicked)
        search_row.append(add_btn)

        self.append(search_row)

        # Scrollable area contains only the flow grid
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['bottom', 'start', 'end']:
            getattr(self.outer, f'set_margin_{side}')(20)

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
        scroll.set_child(self.outer)
        self.append(scroll)

        self._setup_css()
        GLib.idle_add(self.fetch_tags)

    def log(self, text):
        if self.log_fn:
            self.log_fn(text)
        else:
            print(text, flush=True)

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css = """
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
        css_provider.load_from_data(css, len(css))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ------------------------------------------------------------------ #
    # Fetch / render                                                       #
    # ------------------------------------------------------------------ #

    def fetch_tags(self):
        """Fetch tag data from the server (background thread)."""
        def worker():
            try:
                url = f"http://{config.server_address()}/tag_editor"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.update_grid, resp.json())
                else:
                    self.log(f"Failed to fetch tags: {resp.status_code}")
            except Exception as e:
                self.log(f"Error fetching tags: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def update_grid(self, tags):
        """Rebuild the FlowBox with fresh tag cards."""
        self.all_tags = tags
        self._clear_grid()
        for name, data in tags.items():
            card = TagCard(
                name, data,
                on_click=self.on_tag_selected,
                on_edit=self._on_edit_clicked,
                on_delete=self._on_delete_clicked
            )
            self.flow.append(card)

    def _clear_grid(self):
        while True:
            child = self.flow.get_first_child()
            if child is None:
                break
            self.flow.remove(child)

    # ------------------------------------------------------------------ #
    # API helpers                                                          #
    # ------------------------------------------------------------------ #

    def _post_tags(self, tags):
        """POST the full tags dict back to the server."""
        def worker():
            try:
                url = f"http://{config.server_address()}/tag_editor"
                resp = requests.post(url, json=tags, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.fetch_tags)
                else:
                    self.log(f"Save failed: {resp.status_code}")
            except Exception as e:
                self.log(f"Error saving tags: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # UI event handlers                                                    #
    # ------------------------------------------------------------------ #

    def _on_add_clicked(self, _btn):
        crud_dialog.make_tag_dialog(
            self.get_root(), None, None, self._save_tag
        )

    def _on_edit_clicked(self, name, data):
        crud_dialog.make_tag_dialog(
            self.get_root(), name, data,
            lambda v: self._save_tag(v, old_name=name)
        )

    def _save_tag(self, values, old_name=None):
        """Persist a new or edited tag to the server."""
        new_name = values['name']
        data = {
            'positive': values.get('positive', ''),
            'negative': values.get('negative', '')
        }
        tags = dict(self.all_tags)
        if old_name and old_name != new_name:
            del tags[old_name]
        tags[new_name] = data
        self._post_tags(tags)

    def _on_delete_clicked(self, name):
        crud_dialog.show_delete_confirm(
            self.get_root(),
            name,
            lambda: self._delete_tag(name)
        )

    def _delete_tag(self, name):
        tags = dict(self.all_tags)
        if name in tags:
            del tags[name]
        self._post_tags(tags)

    def _on_search_changed(self, entry):
        search = entry.get_text().lower()
        child = self.flow.get_first_child()
        while child:
            card = child.get_child()
            if isinstance(card, TagCard):
                pos = card.data.get('positive', '').lower()
                neg = card.data.get('negative', '').lower()
                visible = (
                    search in card.name.lower()
                    or search in pos
                    or search in neg
                )
                child.set_visible(visible)
            child = child.get_next_sibling()

    @property
    def widget(self):
        return self
