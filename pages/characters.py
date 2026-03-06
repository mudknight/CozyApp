#!/usr/bin/python3
"""Characters page: grid of character cards with full CRUD support."""
import threading
import base64
import requests
import gi
import config
from widgets import crud_dialog

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


class CharactersPage(Gtk.Box):
    """Scrollable grid of character cards with CRUD toolbar."""

    def __init__(self, on_character_selected=None, log_fn=None):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            vexpand=True
        )
        self.on_character_selected = on_character_selected
        self.log_fn = log_fn
        self.all_characters = {}
        # Active category filters (set of category strings)
        self._active_categories = set()

        # Search bar row with Add button — outside the scroll area so
        # it stays visible when the user scrolls the card grid.
        search_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=20,
            margin_start=20,
            margin_end=20,
            margin_bottom=12
        )

        # Sidebar toggle button
        self._sidebar_toggle = Gtk.ToggleButton(
            icon_name='sidebar-show-symbolic',
            active=True,
            tooltip_text='Toggle sidebar'
        )
        self._sidebar_toggle.add_css_class('flat')
        self._sidebar_toggle.connect(
            'toggled', self._on_sidebar_toggled
        )
        search_row.append(self._sidebar_toggle)

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

        self.append(search_row)

        # Horizontal pane: sidebar | scroll
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            vexpand=True
        )
        self.append(content_box)

        # Sidebar wrapped in a revealer for the toggle
        self._sidebar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
            reveal_child=True,
            css_classes=['sidebar']
        )
        self._sidebar_revealer.set_child(self._build_sidebar())
        content_box.append(self._sidebar_revealer)

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
        content_box.append(scroll)

        self._setup_css()
        GLib.idle_add(self.fetch_characters)

    # ------------------------------------------------------------------ #
    # Sidebar                                                              #
    # ------------------------------------------------------------------ #

    def _build_sidebar(self):
        """Build the category filter sidebar widget."""
        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True
        )
        sidebar_scroll.set_size_request(160, -1)

        sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            margin_top=8,
            margin_bottom=8
        )

        # Clear filters button
        clear_btn = Gtk.Button(
            label='Clear Filters',
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4
        )
        clear_btn.add_css_class('flat')
        clear_btn.connect('clicked', self._on_clear_filters)
        sidebar.append(clear_btn)

        # Categories section header
        sidebar.append(Gtk.Label(
            label='Category',
            xalign=0,
            css_classes=['caption', 'dim-label'],
            margin_start=12,
            margin_top=8,
            margin_bottom=4
        ))

        # Multi-select list of categories
        self._category_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            css_classes=['navigation-sidebar']
        )
        self._category_list.connect(
            'selected-rows-changed', self._on_category_changed
        )
        self._add_toggle_gesture(self._category_list)
        sidebar.append(self._category_list)

        sidebar_scroll.set_child(sidebar)
        return sidebar_scroll

    def _make_filter_row(self, label, count):
        """Create a ListBoxRow with a label and count badge."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_start=12,
            margin_end=8,
            margin_top=5,
            margin_bottom=5,
            spacing=4
        )
        lbl = Gtk.Label(
            label=label,
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            width_chars=1
        )
        box.append(lbl)
        count_lbl = Gtk.Label(
            label=str(count),
            css_classes=['dim-label', 'caption']
        )
        box.append(count_lbl)
        row.set_child(box)
        # Store the category value on the row for retrieval
        row._filter_value = label
        return row

    @staticmethod
    def _add_toggle_gesture(listbox):
        """Make every click toggle the row without requiring Ctrl."""
        click = Gtk.GestureClick(button=1)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_pressed(gesture, n_press, x, y):
            row = listbox.get_row_at_y(int(y))
            if row is None:
                return
            if row.is_selected():
                listbox.unselect_row(row)
            else:
                listbox.select_row(row)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect('pressed', on_pressed)
        listbox.add_controller(click)

    def _populate_sidebar(self, characters):
        """Rebuild the sidebar category list from character data."""
        # Count occurrences of each category
        counts = {}
        for data in characters.values():
            for cat in data.get('categories', '').split(','):
                cat = cat.strip()
                if cat:
                    counts[cat] = counts.get(cat, 0) + 1

        # Clear existing rows
        self._category_list.handler_block_by_func(
            self._on_category_changed
        )
        child = self._category_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._category_list.remove(child)
            child = nxt
        self._category_list.handler_unblock_by_func(
            self._on_category_changed
        )

        for cat, count in sorted(counts.items()):
            self._category_list.append(
                self._make_filter_row(cat, count)
            )

    def _on_sidebar_toggled(self, btn):
        """Show or hide the sidebar."""
        self._sidebar_revealer.set_reveal_child(btn.get_active())

    def _on_clear_filters(self, _btn):
        """Deselect all sidebar rows and clear active filters."""
        self._category_list.handler_block_by_func(
            self._on_category_changed
        )
        self._category_list.unselect_all()
        self._category_list.handler_unblock_by_func(
            self._on_category_changed
        )
        self._active_categories.clear()
        self._apply_filters()

    def _on_category_changed(self, listbox):
        """Update active category filter set from current selection."""
        self._active_categories = {
            row._filter_value
            for row in listbox.get_selected_rows()
        }
        self._apply_filters()

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
        self._populate_sidebar(characters)
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

    def _post_characters(self, characters, on_done=None):
        """POST the full characters dict back to the server."""
        def worker():
            try:
                url = (
                    f"http://{config.server_address()}"
                    "/character_editor"
                )
                resp = requests.post(url, json=characters, timeout=5)
                if resp.status_code == 200:
                    if on_done:
                        on_done()
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(f"Save failed: {resp.status_code}")
            except Exception as e:
                self.log(f"Error saving characters: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _rename_character(self, old_name, new_name, data, on_done=None):
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
                    if on_done:
                        on_done()
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(
                        f"Rename failed: {resp.status_code} {resp.text}"
                    )
            except Exception as e:
                self.log(f"Error renaming character: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _handle_image(self, name, img_bytes, remove_img):
        """Upload or delete a character image based on dialog state."""
        if img_bytes:
            self._upload_image(name, img_bytes)
        elif remove_img:
            self._delete_image_api(name)

    def _upload_image(self, name, raw_bytes):
        """POST raw image bytes to the character image endpoint."""
        def worker():
            try:
                encoded_name = base64.b64encode(
                    name.encode('utf-8')
                ).decode('ascii')
                url = (
                    f"http://{config.server_address()}"
                    f"/character_editor/image/{encoded_name}"
                )
                payload = {
                    'image': base64.b64encode(raw_bytes).decode('ascii')
                }
                resp = requests.post(url, json=payload, timeout=15)
                if resp.status_code == 200:
                    GLib.idle_add(self.fetch_characters)
                else:
                    self.log(
                        f"Image upload failed: {resp.status_code}"
                    )
            except Exception as e:
                self.log(f"Error uploading image: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _delete_image_api(self, name):
        """DELETE the image for a character."""
        def worker():
            try:
                encoded_name = base64.b64encode(
                    name.encode('utf-8')
                ).decode('ascii')
                url = (
                    f"http://{config.server_address()}"
                    f"/character_editor/image/{encoded_name}"
                )
                requests.delete(url, timeout=5)
            except Exception as e:
                self.log(f"Error deleting image: {e}")

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
            self.get_root(), None, None, self._save_character,
            config.server_address()
        )

    def _on_edit_clicked(self, name, data):
        crud_dialog.make_character_dialog(
            self.get_root(), name, data,
            lambda v: self._save_character(v, old_name=name),
            config.server_address()
        )

    def _save_character(self, values, old_name=None):
        """Persist a new or edited character to the server."""
        new_name = values['name']
        img_bytes = values.get('_image_bytes')
        remove_img = values.get('_remove_image', False)
        data = {
            'character': values.get('character', ''),
            'top': values.get('top', ''),
            'bottom': values.get('bottom', ''),
            'neg': values.get('neg', ''),
            'categories': values.get('categories', '')
        }
        if old_name is None:
            chars = dict(self.all_characters)
            chars[new_name] = data
            self._post_characters(
                chars,
                on_done=lambda: self._handle_image(
                    new_name, img_bytes, remove_img
                )
            )
        elif new_name != old_name:
            self._rename_character(
                old_name, new_name, data,
                on_done=lambda: self._handle_image(
                    new_name, img_bytes, remove_img
                )
            )
        else:
            chars = dict(self.all_characters)
            chars[old_name] = data
            self._post_characters(
                chars,
                on_done=lambda: self._handle_image(
                    old_name, img_bytes, remove_img
                )
            )

    def _on_delete_clicked(self, name):
        crud_dialog.show_delete_confirm(
            self.get_root(),
            name,
            lambda: self._delete_character_api(name)
        )

    def _apply_filters(self):
        """Apply search text and category filters to the FlowBox."""
        search = self.search_entry.get_text().lower()
        child = self.flow.get_first_child()
        while child:
            card = child.get_child()
            if isinstance(card, CharacterCard):
                # Text match across name, prompts, and categories
                text_match = (
                    not search
                    or search in card.name.lower()
                    or search in card.data.get(
                        'character', ''
                    ).lower()
                    or search in card.data.get(
                        'categories', ''
                    ).lower()
                )
                # Category filter: card must have ALL selected cats
                if self._active_categories:
                    card_cats = {
                        c.strip()
                        for c in card.data.get(
                            'categories', ''
                        ).split(',')
                        if c.strip()
                    }
                    cat_match = bool(
                        self._active_categories & card_cats
                    )
                else:
                    cat_match = True
                child.set_visible(text_match and cat_match)
            child = child.get_next_sibling()

    def _on_search_changed(self, entry):
        self._apply_filters()

    @property
    def widget(self):
        return self
