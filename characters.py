#!/usr/bin/python3
"""Characters page: grid of character cards with full CRUD support."""
import threading
import base64
import requests
import gi
import config
import crud_dialog

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango, GdkPixbuf  # noqa

THUMBNAIL_SIZE = 220


class CharacterCard(Gtk.Frame):
    """A card representing a character with right-click CRUD menu."""

    def __init__(self, name, data, on_click=None,
                 on_edit=None, on_delete=None):
        super().__init__(css_classes=['card'])
        self.name = name
        self.data = data
        self.on_click = on_click
        self.on_edit = on_edit
        self.on_delete = on_delete

        self.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)

        overlay = Gtk.Overlay()
        self.set_child(overlay)

        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        self.picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        overlay.set_child(self.picture)

        # Name / category overlay at the bottom of the card
        info_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        info_vbox.set_valign(Gtk.Align.END)
        info_vbox.add_css_class('character-card-info')

        name_label = Gtk.Label(label=name.title())
        name_label.add_css_class('character-card-name')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        info_vbox.append(name_label)

        categories = data.get('categories', '').strip()
        if categories:
            cat_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=4
            )
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

        # Left click — select character
        left_gesture = Gtk.GestureClick(button=1)
        left_gesture.connect('released', self._on_left_click)
        self.add_controller(left_gesture)

        # Right click — context menu
        right_gesture = Gtk.GestureClick(button=3)
        right_gesture.connect('released', self._on_right_click)
        self.add_controller(right_gesture)

        GLib.idle_add(self._load_image)

    def _load_image(self):
        """Fetch the character image in a background thread."""
        def worker():
            try:
                encoded = base64.b64encode(
                    self.name.encode('utf-8')
                ).decode('ascii')
                url = (
                    f"http://{config.server_address()}"
                    f"/character_editor/image/{encoded}"
                )
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(resp.content)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf:
                        GLib.idle_add(self._update_image, pixbuf)
            except Exception as e:
                print(
                    f"[characters] image load error for {self.name}: {e}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _update_image(self, pixbuf):
        """Render the fetched pixbuf into the picture widget."""
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        gbytes = GLib.Bytes.new(pixbuf.get_pixels())
        fmt = (
            Gdk.MemoryFormat.R8G8B8A8
            if has_alpha
            else Gdk.MemoryFormat.R8G8B8
        )
        texture = Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)
        self.picture.set_paintable(texture)

    def _on_left_click(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.name, self.data)

    def _on_right_click(self, gesture, n_press, x, y):
        crud_dialog.show_card_context_menu(
            self, x, y,
            on_edit=lambda: self.on_edit(self.name, self.data),
            on_delete=lambda: self.on_delete(self.name)
        )


class CharactersPage(Gtk.ScrolledWindow):
    """Scrollable grid of character cards with CRUD toolbar."""

    def __init__(self, on_character_selected=None, log_fn=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self.on_character_selected = on_character_selected
        self.log_fn = log_fn
        self.all_characters = {}

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(self.outer, f'set_margin_{side}')(20)

        # Search bar row with Add button
        search_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_bottom=20
        )
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search characters...",
            hexpand=True
        )
        self.search_entry.connect(
            'search-changed', self._on_search_changed
        )
        search_row.append(self.search_entry)

        add_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_btn.set_tooltip_text("Add character")
        add_btn.connect('clicked', self._on_add_clicked)
        search_row.append(add_btn)

        self.outer.append(search_row)

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
        GLib.idle_add(self.fetch_characters)

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css = """
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
                border: 1px solid alpha(@view_fg_color, 0.1);
            }
            .card:hover {
                background-color: alpha(@view_fg_color, 0.05);
                border-color: alpha(@accent_bg_color, 0.3);
            }
            .character-card-info {
                background: linear-gradient(
                    to top,
                    rgba(0,0,0,0.8) 0%,
                    rgba(0,0,0,0.4) 70%,
                    transparent 100%
                );
                padding: 8px;
                color: white;
            }
            .character-card-name {
                font-weight: bold;
                font-size: 0.9em;
                text-shadow: 0 1px 2px rgba(0,0,0,0.5);
            }
        """
        css_provider.load_from_data(css, len(css))
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

    # ------------------------------------------------------------------ #
    # Fetch / render                                                       #
    # ------------------------------------------------------------------ #

    def fetch_characters(self):
        """Fetch character data from the server (background thread)."""
        def worker():
            try:
                url = (
                    f"http://{config.server_address()}/character_editor"
                )
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.update_grid, resp.json())
                else:
                    self.log(
                        f"Failed to fetch characters: "
                        f"{resp.status_code}"
                    )
            except Exception as e:
                self.log(f"Error fetching characters: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def update_grid(self, characters):
        """Rebuild the FlowBox with fresh character cards."""
        self.all_characters = characters
        self._clear_grid()
        for name, data in characters.items():
            card = CharacterCard(
                name, data,
                on_click=self.on_character_selected,
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

    def _post_characters(self, characters):
        """POST the full characters dict back to the server."""
        def worker():
            try:
                url = (
                    f"http://{config.server_address()}"
                    "/character_editor"
                )
                resp = requests.post(url, json=characters, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(f"Save failed: {resp.status_code}")
            except Exception as e:
                self.log(f"Error saving characters: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _rename_character(self, old_name, new_name, data):
        """Use the rename endpoint (also moves the image)."""
        def worker():
            try:
                url = (
                    f"http://{config.server_address()}"
                    "/character_editor/rename"
                )
                payload = {
                    'oldName': old_name,
                    'newName': new_name,
                    'data': data
                }
                resp = requests.post(url, json=payload, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(
                        f"Rename failed: {resp.status_code} {resp.text}"
                    )
            except Exception as e:
                self.log(f"Error renaming character: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _delete_character_api(self, name):
        """Call the DELETE endpoint for a single character."""
        def worker():
            try:
                encoded = requests.utils.quote(name, safe='')
                url = (
                    f"http://{config.server_address()}"
                    f"/character_editor/{encoded}"
                )
                resp = requests.delete(url, timeout=5)
                if resp.status_code == 200:
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(
                        f"Delete failed: {resp.status_code} {resp.text}"
                    )
            except Exception as e:
                self.log(f"Error deleting character: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # UI event handlers                                                    #
    # ------------------------------------------------------------------ #

    def _on_add_clicked(self, _btn):
        crud_dialog.make_character_dialog(
            self.get_root(), None, None, self._save_character
        )

    def _on_edit_clicked(self, name, data):
        crud_dialog.make_character_dialog(
            self.get_root(), name, data,
            lambda v: self._save_character(v, old_name=name)
        )

    def _save_character(self, values, old_name=None):
        """Persist a new or edited character to the server."""
        new_name = values['name']
        data = {
            'character': values.get('character', ''),
            'top': values.get('top', ''),
            'bottom': values.get('bottom', ''),
            'neg': values.get('neg', ''),
            'categories': values.get('categories', '')
        }
        if old_name is None:
            # Create: just add to the dict and POST
            chars = dict(self.all_characters)
            chars[new_name] = data
            self._post_characters(chars)
        elif new_name != old_name:
            # Rename: use dedicated endpoint so the image moves too
            self._rename_character(old_name, new_name, data)
        else:
            # Update in-place
            chars = dict(self.all_characters)
            chars[old_name] = data
            self._post_characters(chars)

    def _on_delete_clicked(self, name):
        crud_dialog.show_delete_confirm(
            self.get_root(),
            name,
            lambda: self._delete_character_api(name)
        )

    def _on_search_changed(self, entry):
        search = entry.get_text().lower()
        child = self.flow.get_first_child()
        while child:
            card = child.get_child()
            if isinstance(card, CharacterCard):
                visible = (
                    search in card.name.lower()
                    or search in card.data.get(
                        'character', ''
                    ).lower()
                    or search in card.data.get(
                        'categories', ''
                    ).lower()
                )
                child.set_visible(visible)
            child = child.get_next_sibling()

    @property
    def widget(self):
        return self
