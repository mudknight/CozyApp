#!/usr/bin/python3
"""LoRAs page: browsable grid of LoRA cards from ComfyUI-Lora-Manager."""
import threading
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

        # Load preview image in the background
        preview_url = lora_data.get('preview_url', '')
        if preview_url:
            threading.Thread(
                target=self._load_preview,
                args=(preview_url,),
                daemon=True
            ).start()

    def _load_preview(self, url):
        """Fetch preview image from the Lora Manager static URL."""
        # URLs from the API can be relative (/lm-static/…) or absolute
        if url.startswith('/'):
            url = f"http://{config.server_address()}{url}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(resp.content)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf:
                    GLib.idle_add(self._set_texture, pixbuf)
        except Exception:
            pass  # Missing previews are fine — card stays blank

    def _set_texture(self, pixbuf):
        self.picture.set_paintable(_pixbuf_to_texture(pixbuf))

    def do_measure(self, orientation, for_size):
        """Cap natural size to THUMB_SIZE so FlowBox lays out correctly."""
        return THUMB_SIZE, THUMB_SIZE, -1, -1

    def _on_click(self, gesture, n_press, x, y):
        if self.on_click:
            self.on_click(self.lora_data)


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

        self._search = Gtk.SearchEntry(
            placeholder_text='Search LoRAs…',
            hexpand=True
        )
        self._search.connect('search-changed', self._on_search_changed)
        toolbar.append(self._search)

        # Sort dropdown
        self._sort_model = Gtk.StringList.new([
            'name', 'date_modified', 'file_size'
        ])
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
        self._root.append(self._status)

        scroll.set_child(inner)
        self._root.append(scroll)
        self._scroll_adj = vadj
        self._setup_css()

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
    # Search / sort
    # ------------------------------------------------------------------

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().strip()
        self._reload()

    def _on_sort_changed(self, dropdown, _param):
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
