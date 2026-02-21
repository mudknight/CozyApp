#!/usr/bin/python3
"""LoRAs page: browsable grid of LoRA cards from ComfyUI-Lora-Manager."""
import threading
import weakref
import requests
import gi
import config

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, GLib, Gdk, Pango, GdkPixbuf  # noqa

# Card thumbnail dimensions
THUMB_SIZE = 200
# Number of loras per API page
PAGE_SIZE = 48
# Module-level preview cache: URL -> scaled GdkPixbuf at THUMB_SIZE.
# Persists across reloads; only the small version is kept in memory.
_preview_cache: dict = {}
_preview_cache_lock = threading.Lock()


def _pixbuf_to_texture(pixbuf):
    """Convert a GdkPixbuf to a Gdk.MemoryTexture."""
    has_alpha = pixbuf.get_has_alpha()
    fmt = (
        Gdk.MemoryFormat.R8G8B8A8
        if has_alpha
        else Gdk.MemoryFormat.R8G8B8
    )
    gbytes = GLib.Bytes.new(pixbuf.get_pixels())
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(), pixbuf.get_height(),
        fmt, gbytes, pixbuf.get_rowstride()
    )


class LoraCard(Gtk.Frame):
    """A card for a single LoRA with thumbnail and name overlay."""

    def __init__(self, lora_data, on_click=None):
        super().__init__(css_classes=['card'])
        self.lora_data = lora_data
        self.on_click = on_click

        self.set_size_request(THUMB_SIZE, THUMB_SIZE)

        overlay = Gtk.Overlay()
        self.set_child(overlay)

        # Thumbnail picture
        self.picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        self.picture.set_size_request(THUMB_SIZE, THUMB_SIZE)
        overlay.set_child(self.picture)

        # Name / base model overlay at the bottom.
        # halign=FILL clamps the box to card width so labels can
        # ellipsize instead of pushing the card wider.
        info_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        info_box.set_valign(Gtk.Align.END)
        info_box.set_halign(Gtk.Align.FILL)
        info_box.add_css_class('lora-card-info')

        name_label = Gtk.Label(label=lora_data.get('model_name', ''))
        name_label.add_css_class('lora-card-name')
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        # width_chars=1 collapses the label's natural width so it
        # doesn't inflate the card's natural size for FlowBox layout.
        name_label.set_width_chars(1)
        info_box.append(name_label)

        base_model = lora_data.get('base_model', '').strip()
        if base_model:
            base_label = Gtk.Label(label=base_model)
            base_label.add_css_class('caption')
            base_label.add_css_class('lora-base-label')
            base_label.set_halign(Gtk.Align.START)
            base_label.set_ellipsize(Pango.EllipsizeMode.END)
            base_label.set_width_chars(1)
            info_box.append(base_label)

        overlay.add_overlay(info_box)

        # Left click to insert
        click = Gtk.GestureClick(button=1)
        click.connect('released', self._on_click)
        self.add_controller(click)

        # Right click for context menu
        rclick = Gtk.GestureClick(button=3)
        rclick.connect('released', self._on_right_click)
        self.add_controller(rclick)

        # Load preview image in the background.
        # Use a weakref so the thread doesn't keep removed cards alive.
        preview_url = lora_data.get('preview_url', '')
        if preview_url:
            threading.Thread(
                target=self._load_preview,
                args=(weakref.ref(self), preview_url),
                daemon=True
            ).start()

    @staticmethod
    def _load_preview(weak_self, url):
        """Fetch, scale, and cache preview; update card if still alive."""
        # Resolve relative URLs
        if url.startswith('/'):
            url = f"http://{config.server_address()}{url}"

        # Check module-level cache for an already-scaled pixbuf
        with _preview_cache_lock:
            pixbuf = _preview_cache.get(url)

        if pixbuf is None:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    return
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(resp.content)
                loader.close()
                full = loader.get_pixbuf()
            except Exception:
                return  # Missing previews are fine

            if not full:
                return

            # Scale to a square THUMB_SIZE crop (cover fit) so only
            # the small version is ever held in memory.
            src_w = full.get_width()
            src_h = full.get_height()
            scale = max(THUMB_SIZE / src_w, THUMB_SIZE / src_h)
            scaled_w = max(1, int(src_w * scale))
            scaled_h = max(1, int(src_h * scale))
            pixbuf = full.scale_simple(
                scaled_w, scaled_h,
                GdkPixbuf.InterpType.BILINEAR
            )
            # Let the full-size pixbuf go out of scope immediately
            del full

            with _preview_cache_lock:
                _preview_cache[url] = pixbuf

        if not pixbuf:
            return

        # Only schedule the UI update if the card is still alive
        def apply(pb=pixbuf, wr=weak_self):
            card = wr()
            if card is not None:
                card._set_texture(pb)

        GLib.idle_add(apply)

    def _set_texture(self, pixbuf):
        self.picture.set_paintable(_pixbuf_to_texture(pixbuf))

    def do_measure(self, orientation, for_size):
        """Cap natural size to THUMB_SIZE so FlowBox lays out correctly."""
        return THUMB_SIZE, THUMB_SIZE, -1, -1

    def _on_click(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.lora_data)

    def _on_right_click(self, gesture, n_press, x, y):
        """Show a context menu with card actions."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.Popover(has_arrow=False)
        popover.set_parent(self)
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
        delete_btn = Gtk.Button(label='Delete', has_frame=False)
        delete_btn.add_css_class('destructive-action')
        delete_btn.set_halign(Gtk.Align.FILL)
        delete_btn.connect(
            'clicked',
            lambda _: (popover.popdown(), self._confirm_delete())
        )
        box.append(delete_btn)
        popover.set_child(box)
        popover.popup()

    def _confirm_delete(self):
        """Show a confirmation dialog before deleting."""
        name = self.lora_data.get('model_name', 'this LoRA')
        dialog = Adw.AlertDialog(
            heading='Delete LoRA?',
            body=f'\u201c{name}\u201d will be permanently deleted from disk.'
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance(
            'delete', Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_response)
        dialog.present(self.get_root())

    def _on_delete_response(self, dialog, response):
        """Fire off the delete request if confirmed."""
        if response != 'delete':
            return
        file_path = self.lora_data.get('file_path', '')
        if not file_path:
            return
        threading.Thread(
            target=self._delete_worker,
            args=(file_path,),
            daemon=True
        ).start()

    def _delete_worker(self, file_path):
        """POST delete request to Lora Manager."""
        try:
            url = (
                f"http://{config.server_address()}"
                f"/api/lm/loras/delete"
            )
            resp = requests.post(
                url, json={'file_path': file_path}, timeout=10
            )
            if resp.status_code == 200:
                # Remove the card from the FlowBox on the main thread
                GLib.idle_add(self._remove_self)
            else:
                print(
                    f"[loras] delete error {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
        except Exception as e:
            print(f"[loras] delete exception: {e}")

    def _remove_self(self):
        """Remove this card from its FlowBox parent."""
        parent = self.get_parent()
        if parent:
            flow = parent.get_parent()
            if flow:
                flow.remove(parent)


class LorasPage:
    """
    LoRA browser tab — fetches pages from ComfyUI-Lora-Manager and
    displays them in a scrollable FlowBox grid.

    Callbacks
    ---------
    on_lora_selected(lora_data)  -- called when a card is clicked
    log_fn(text)                 -- for debug output
    """

    def __init__(self, on_lora_selected=None, log_fn=None):
        self.on_lora_selected = on_lora_selected
        self.log_fn = log_fn or print

        # Pagination state
        self._current_page = 1
        self._total_pages = 1
        self._loading = False
        self._search_text = ''
        # Active sidebar filters (sets for multi-select)
        self._active_base_models = set()
        self._active_tags = set()

        self._build_ui()
        GLib.idle_add(self._fetch_page, 1)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the outer container widget."""
        self._root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            vexpand=True
        )

        # Toolbar: search + sort
        toolbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=12,
            margin_bottom=8,
            margin_start=16,
            margin_end=16
        )

        # Sidebar toggle
        self._sidebar_toggle = Gtk.ToggleButton(
            icon_name='sidebar-show-symbolic',
            active=True,
            tooltip_text='Toggle sidebar'
        )
        self._sidebar_toggle.add_css_class('flat')
        self._sidebar_toggle.connect(
            'toggled', self._on_sidebar_toggled
        )
        toolbar.append(self._sidebar_toggle)

        self._search = Gtk.SearchEntry(
            placeholder_text='Search LoRAs…',
            hexpand=True
        )
        self._search.connect('search-changed', self._on_search_changed)
        toolbar.append(self._search)

        # Sort dropdown
        self._sort_labels = {
            'name': 'Name',
            'date_modified': 'Modified',
            'file_size': 'Size'
        }
        sort_display = Gtk.StringList.new(
            [self._sort_labels[k] for k in ['name', 'date_modified',
                                             'file_size']]
        )
        self._sort_keys = ['name', 'date_modified', 'file_size']
        self._sort_dropdown = Gtk.DropDown(model=sort_display)
        self._sort_dropdown.set_tooltip_text('Sort by')
        self._sort_dropdown.connect(
            'notify::selected', self._on_sort_changed
        )
        toolbar.append(self._sort_dropdown)

        # Install from URL button
        install_btn = Gtk.Button(
            icon_name='folder-download-symbolic',
            tooltip_text='Install LoRA from URL'
        )
        install_btn.add_css_class('flat')
        install_btn.connect('clicked', self._on_install_clicked)
        toolbar.append(install_btn)

        # Refresh button
        refresh_btn = Gtk.Button(
            icon_name='view-refresh-symbolic',
            tooltip_text='Refresh'
        )
        refresh_btn.add_css_class('flat')
        refresh_btn.connect('clicked', lambda _: self._reload())
        toolbar.append(refresh_btn)

        self._root.append(toolbar)

        # Horizontal pane: sidebar | grid
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            vexpand=True
        )
        self._root.append(content_box)

        # Sidebar wrapped in a revealer for the toggle
        self._sidebar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
            reveal_child=True,
            css_classes=['sidebar']
        )
        self._sidebar_revealer.set_child(self._build_sidebar())
        content_box.append(self._sidebar_revealer)

        # self._sidebar_sep = Gtk.Separator(
        #     orientation=Gtk.Orientation.VERTICAL
        # )
        # content_box.append(self._sidebar_sep)

        # Scrolled window containing the flow grid
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )

        # Detect scroll near bottom for infinite-style pagination
        vadj = scroll.get_vadjustment()
        vadj.connect('value-changed', self._on_scroll_changed)

        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=16,
            margin_end=16,
            margin_bottom=16,
            hexpand=True
        )

        self._flow = Gtk.FlowBox(
            max_children_per_line=12,
            min_children_per_line=1,
            row_spacing=12,
            column_spacing=12,
            selection_mode=Gtk.SelectionMode.NONE,
            valign=Gtk.Align.START,
            hexpand=True
        )
        inner.append(self._flow)

        # Spinner shown while loading
        self._spinner = Gtk.Spinner(
            margin_top=16,
            margin_bottom=16,
            halign=Gtk.Align.CENTER
        )
        inner.append(self._spinner)

        # Status / empty state
        self._status = Adw.StatusPage(
            icon_name='folder-open-symbolic',
            title='No LoRAs Found',
            description=(
                'Make sure ComfyUI-Lora-Manager is installed and '
                'accessible at the configured server address.'
            )
        )
        self._status.set_visible(False)
        content_box.append(self._status)

        scroll.set_child(inner)
        content_box.append(scroll)
        self._scroll_adj = vadj
        self._setup_css()

    def _build_sidebar(self):
        """Build the filter sidebar widget."""
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

        # Base model section
        sidebar.append(Gtk.Label(
            label='Base Model',
            xalign=0,
            css_classes=['caption', 'dim-label'],
            margin_start=12,
            margin_top=8,
            margin_bottom=4
        ))
        self._base_model_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            css_classes=['navigation-sidebar']
        )
        self._base_model_list.connect(
            'selected-rows-changed', self._on_base_model_changed
        )
        self._add_toggle_gesture(self._base_model_list)
        sidebar.append(self._base_model_list)

        # Tags section
        sidebar.append(Gtk.Label(
            label='Tags',
            xalign=0,
            css_classes=['caption', 'dim-label'],
            margin_start=12,
            margin_top=12,
            margin_bottom=4
        ))
        self._tag_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            css_classes=['navigation-sidebar']
        )
        self._tag_list.connect(
            'selected-rows-changed', self._on_tag_changed
        )
        self._add_toggle_gesture(self._tag_list)
        sidebar.append(self._tag_list)

        sidebar_scroll.set_child(sidebar)

        # Fetch sidebar data in the background
        threading.Thread(
            target=self._fetch_sidebar_data, daemon=True
        ).start()

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
        # Store the filter value on the row for retrieval on selection
        row._filter_value = label
        return row

    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css = b"""
            .lora-card-info {
                background: linear-gradient(
                    to top,
                    rgba(0,0,0,0.8) 0%,
                    rgba(0,0,0,0.4) 70%,
                    transparent 100%
                );
                padding: 8px;
                color: white;
            }
            .lora-card-name {
                font-weight: bold;
                font-size: 0.85em;
            }
            .lora-base-label {
                opacity: 0.75;
                font-size: 0.75em;
            }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _sort_key(self):
        """Return the currently selected sort key string."""
        idx = self._sort_dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._sort_keys):
            return 'name'
        return self._sort_keys[idx]

    def _fetch_page(self, page):
        """Start a background thread to fetch page *page* from the API."""
        if self._loading:
            return
        self._loading = True
        self._spinner.start()
        threading.Thread(
            target=self._fetch_worker,
            args=(page, self._search_text, self._sort_key()),
            daemon=True
        ).start()

    def _fetch_sidebar_data(self):
        """Fetch base models and tags to populate the sidebar."""
        base = config.server_address()
        try:
            r = requests.get(
                f"http://{base}/api/lm/loras/base-models", timeout=10
            )
            if r.status_code == 200:
                items = r.json().get('base_models', [])
                GLib.idle_add(self._populate_base_models, items)
        except Exception as e:
            self.log_fn(f"[loras] sidebar base-models error: {e}")
        try:
            r = requests.get(
                f"http://{base}/api/lm/loras/top-tags", timeout=10
            )
            if r.status_code == 200:
                items = r.json().get('tags', [])
                GLib.idle_add(self._populate_tags, items)
        except Exception as e:
            self.log_fn(f"[loras] sidebar tags error: {e}")

    def _populate_base_models(self, items):
        """Add base model rows to the sidebar list."""
        for item in items:
            row = self._make_filter_row(
                item['name'], item['count']
            )
            self._base_model_list.append(row)

    def _populate_tags(self, items):
        """Add tag rows to the sidebar list."""
        for item in items:
            row = self._make_filter_row(
                item['tag'], item['count']
            )
            self._tag_list.append(row)

    def _fetch_worker(self, page, search, sort_by):
        """Worker thread: call the Lora Manager API and schedule update."""
        try:
            params = {
                'page': page,
                'page_size': PAGE_SIZE,
                'sort_by': sort_by,
            }
            if search:
                params['search'] = search
            # Pass multiple base_model / tag_include params
            for bm in self._active_base_models:
                params.setdefault('base_model', [])
                if isinstance(params['base_model'], list):
                    params['base_model'].append(bm)
            for tag in self._active_tags:
                params.setdefault('tag_include', [])
                if isinstance(params['tag_include'], list):
                    params['tag_include'].append(tag)
            url = (
                f"http://{config.server_address()}"
                f"/api/lm/loras/list"
            )
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                GLib.idle_add(
                    self._on_page_received, data, page == 1
                )
            else:
                self.log_fn(
                    f"[loras] API error {resp.status_code}"
                )
                GLib.idle_add(self._on_fetch_done, True)
        except Exception as e:
            self.log_fn(f"[loras] fetch error: {e}")
            GLib.idle_add(self._on_fetch_done, True)

    def _on_page_received(self, data, clear_first):
        """Called on the main thread when a page arrives."""
        items = data.get('items', [])
        self._total_pages = data.get('total_pages', 1)
        self._current_page = data.get('page', 1)

        if clear_first:
            self._clear_grid()

        for lora in items:
            card = LoraCard(
                lora,
                on_click=self._on_card_clicked
            )
            self._flow.append(card)

        # Show empty state only on first page with no results
        empty = (clear_first and len(items) == 0)
        self._status.set_visible(empty)
        self._flow.set_visible(not empty)
        self._on_fetch_done(False)

    def _on_fetch_done(self, error=False):
        """Reset loading state and stop spinner."""
        self._loading = False
        self._spinner.stop()

    def _clear_grid(self):
        """Remove all children from the flow box."""
        child = self._flow.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt

    def _reload(self):
        """Clear and re-fetch from page 1."""
        self._current_page = 1
        self._total_pages = 1
        self._fetch_page(1)

    # ------------------------------------------------------------------
    # Scroll / pagination
    # ------------------------------------------------------------------

    def _on_scroll_changed(self, adj):
        """Load the next page when scrolled close to the bottom."""
        if self._loading:
            return
        if self._current_page >= self._total_pages:
            return
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        value = adj.get_value()
        # Trigger when within 400px of the bottom
        if upper - page_size - value < 400:
            self._fetch_page(self._current_page + 1)

    # ------------------------------------------------------------------
    # Search / sort / sidebar
    # ------------------------------------------------------------------

    @staticmethod
    def _add_toggle_gesture(listbox):
        """Make every click toggle the row instead of requiring Ctrl."""
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

    def _on_sidebar_toggled(self, btn):
        """Show or hide the sidebar and its separator."""
        visible = btn.get_active()
        self._sidebar_revealer.set_reveal_child(visible)
        # self._sidebar_sep.set_visible(visible)

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().strip()
        self._reload()

    def _on_sort_changed(self, dropdown, _param):
        self._reload()

    def _on_clear_filters(self, _btn):
        """Deselect all sidebar rows and clear active filters."""
        self._base_model_list.handler_block_by_func(
            self._on_base_model_changed
        )
        self._tag_list.handler_block_by_func(self._on_tag_changed)
        self._base_model_list.unselect_all()
        self._tag_list.unselect_all()
        self._base_model_list.handler_unblock_by_func(
            self._on_base_model_changed
        )
        self._tag_list.handler_unblock_by_func(self._on_tag_changed)
        self._active_base_models.clear()
        self._active_tags.clear()
        self._reload()

    def _on_base_model_changed(self, listbox):
        """Update active base model filter set from current selection."""
        self._active_base_models = {
            row._filter_value
            for row in listbox.get_selected_rows()
        }
        self._reload()

    def _on_tag_changed(self, listbox):
        """Update active tag filter set from current selection."""
        self._active_tags = {
            row._filter_value
            for row in listbox.get_selected_rows()
        }
        self._reload()

    # ------------------------------------------------------------------
    # Install from URL
    # ------------------------------------------------------------------

    def _on_install_clicked(self, _btn):
        """Open a dialog to install a LoRA from a download URL."""
        dialog = Adw.AlertDialog(
            heading='Install LoRA from URL',
            body='Enter a CivitAI or direct download URL.'
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('install', 'Install')
        dialog.set_response_appearance(
            'install', Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response('install')
        dialog.set_close_response('cancel')

        url_entry = Gtk.Entry(
            placeholder_text='https://civitai.com/models/…',
            hexpand=True
        )
        dialog.set_extra_child(url_entry)
        dialog.connect(
            'response',
            lambda d, r: self._on_install_response(d, r, url_entry)
        )
        dialog.present(self._root.get_root())

    def _on_install_response(self, dialog, response, url_entry):
        """Handle the install dialog response."""
        if response != 'install':
            return
        url = url_entry.get_text().strip()
        if not url:
            return
        threading.Thread(
            target=self._install_worker,
            args=(url,),
            daemon=True
        ).start()

    @staticmethod
    def _parse_civitai_url(url):
        """
        Extract model_id and model_version_id from a CivitAI URL.
        Returns (model_id, version_id) as ints or None.
        """
        import re
        version_id = None
        # modelVersionId in query string takes priority
        m = re.search(r'modelVersionId=(\d+)', url, re.IGNORECASE)
        if m:
            version_id = int(m.group(1))
        # /models/<id> in path
        m = re.search(r'/models/(\d+)', url)
        model_id = int(m.group(1)) if m else None
        return model_id, version_id

    def _install_worker(self, url):
        """POST to Lora Manager's /api/lm/download-model endpoint."""
        model_id, version_id = self._parse_civitai_url(url)
        if model_id is None and version_id is None:
            self.log_fn("[loras] install error: could not parse model ID from URL")
            return
        payload = {'use_default_paths': True}
        if version_id:
            payload['model_version_id'] = version_id
        else:
            payload['model_id'] = model_id
        try:
            api_url = (
                f"http://{config.server_address()}"
                f"/api/lm/download-model"
            )
            resp = requests.post(api_url, json=payload, timeout=30)
            if resp.status_code == 200:
                self.log_fn(f"[loras] download queued: {url}")
                GLib.idle_add(self._reload)
            else:
                self.log_fn(
                    f"[loras] install error {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
        except Exception as e:
            self.log_fn(f"[loras] install exception: {e}")

    # ------------------------------------------------------------------
    # Card click
    # ------------------------------------------------------------------

    def _on_card_clicked(self, lora_data):
        if self.on_lora_selected:
            self.on_lora_selected(lora_data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self):
        """Reload the lora list (called by the main window on reload)."""
        self._reload()

    @property
    def widget(self):
        return self._root
