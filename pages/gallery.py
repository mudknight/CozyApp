#!/usr/bin/python3
"""Gallery page: thumbnail grid with multi-select and context menus."""
import os
import threading
from pathlib import Path
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, Gdk, GdkPixbuf, GLib, Gio  # noqa

THUMBNAIL_SIZE = 220
# Images added per main-loop idle callback during batch load
_LOAD_BATCH_SIZE = 20


class GalleryPage(Gtk.ScrolledWindow):
    """Scrollable FlowBox grid with multi-selection and context menus."""

    def __init__(
        self, on_view_image=None,
        on_delete_image=None, on_clear_gallery=None
    ):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        # Called with pixbuf when selection changes to exactly one item
        self._on_view_image = on_view_image
        # Called with (image_info, remove_fn) for each deleted item
        self._on_delete_image = on_delete_image
        # Called with no args when the user confirms clearing the gallery
        self._on_clear_gallery = on_clear_gallery
        # Tracks the last single-activated child for shift-range select
        self._last_activated_child = None
        # Active context popover (kept to dismiss on re-open)
        self._active_popover = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(outer, f'set_margin_{side}')(12)

        # MULTIPLE mode; manual click handling below replaces default
        self._flow = Gtk.FlowBox(
            max_children_per_line=10,
            min_children_per_line=1,
            row_spacing=8,
            column_spacing=8,
            homogeneous=True,
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            valign=Gtk.Align.START
        )
        self._flow.connect(
            'selected-children-changed', self._on_selection_changed
        )
        self._flow.connect(
            'child-activated', self._on_child_activated
        )

        # Capture arrow keys before FlowBox handles them so that
        # navigation always starts from the last clicked child
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect('key-pressed', self._on_flow_key_pressed)
        self._flow.add_controller(key_ctrl)

        outer.append(self._flow)
        self.set_child(outer)

        self._placeholder = Adw.StatusPage(
            icon_name='image-x-generic-symbolic',
            title='No Images Yet',
            description='Generated images will appear here.'
        )

        self._overlay = Gtk.Overlay()
        self._overlay.set_child(self)
        self._placeholder.set_visible(True)
        self._overlay.add_overlay(self._placeholder)


    def grab_focus(self):
        """Focus the flowbox or its selected child."""
        self._flow.grab_focus()
        selected = self._flow.get_selected_children()
        if selected:
            self._flow.set_focus_child(selected[0])

    def scroll_to_top(self):
        """Reset scroll position to the top of the grid."""
        self.get_vadjustment().set_value(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def widget(self):
        """Return the top-level widget to pack into the view stack."""
        return self._overlay

    def add_image(self, cache_path: Path, image_info: dict = None):
        """
        Add a cached image to the gallery by path (thread-safe).

        Can be called from any thread. Heavy work (decode, scale) is
        done on the calling thread; only widget creation hits the main
        loop.
        """
        # Do expensive work on the calling (background) thread
        try:
            data = cache_path.read_bytes()
        except Exception as e:
            print(f"Gallery: failed to read cache file: {e}", flush=True)
            return

        pixbuf = self._pixbuf_from_bytes(data)
        if pixbuf is None:
            return

        thumb = self._scale_pixbuf(pixbuf, THUMBNAIL_SIZE)
        # Extract raw pixels so we can hand off to the main thread
        # without keeping a live pixbuf reference across threads.
        w = thumb.get_width()
        h = thumb.get_height()
        rowstride = thumb.get_rowstride()
        has_alpha = thumb.get_has_alpha()
        pixels = bytes(thumb.get_pixels())

        # Schedule only widget creation on the main thread
        GLib.idle_add(
            self._add_image_idle,
            cache_path, image_info,
            w, h, rowstride, has_alpha, pixels
        )

    def add_images_batch(self, images: list):
        """
        Add multiple cached images efficiently (thread-safe).

        images: list of (cache_path, image_info) tuples, oldest-first.
        Decodes thumbnails sequentially in a background thread and
        schedules widget creation in _LOAD_BATCH_SIZE chunks so the
        main loop stays responsive and first images appear quickly.
        GdkPixbuf is not thread-safe for concurrent use, so all
        decoding stays on a single background thread.
        """
        if not images:
            return
        threading.Thread(
            target=self._decode_batch, args=(images,), daemon=True
        ).start()

    def _decode_batch(self, images: list):
        """Background: decode thumbnails sequentially, schedule in chunks."""
        chunk = []
        # Iterate newest-first so the first chunk to hit the main loop
        # contains the most recent images, which get appended in order.
        for cache_path, image_info in reversed(images):
            try:
                data = cache_path.read_bytes()
            except Exception as e:
                print(
                    f"Gallery: read error {cache_path.name}: {e}",
                    flush=True
                )
                continue
            pixbuf = self._pixbuf_from_bytes(data)
            if pixbuf is None:
                continue
            thumb = self._scale_pixbuf(pixbuf, THUMBNAIL_SIZE)
            chunk.append((
                cache_path, image_info,
                thumb.get_width(), thumb.get_height(),
                thumb.get_rowstride(), thumb.get_has_alpha(),
                bytes(thumb.get_pixels())
            ))
            # Schedule each full chunk immediately so thumbnails
            # appear progressively rather than all at once at the end.
            if len(chunk) >= _LOAD_BATCH_SIZE:
                GLib.idle_add(self._add_chunk_idle, chunk)
                chunk = []
        if chunk:
            GLib.idle_add(self._add_chunk_idle, chunk)

    def _add_chunk_idle(self, chunk: list):
        """Main thread: create thumbnail widgets for one chunk."""
        self._placeholder.set_visible(False)
        for entry in chunk:
            cache_path, image_info, w, h, rs, alpha, px = entry
            self._add_image_idle(
                cache_path, image_info, w, h, rs, alpha, px,
                prepend=False
            )
        return False

    def clear(self):
        """Remove all children from the gallery and show placeholder."""
        for child in self._get_all_children():
            self._flow.remove(child)
        self._last_activated_child = None
        self._placeholder.set_visible(True)

    def delete_by_cache_path(self, cache_path: Path):
        """
        Find the gallery item with the given cache path and delete it.

        Used by the preview context menu when the shown image was
        selected from the gallery.
        """
        for child in self._get_all_children():
            if self._cache_path_from_child(child) == cache_path:
                self._delete_children([child])
                break

    # ------------------------------------------------------------------
    # Adding thumbnails
    # ------------------------------------------------------------------

    def _add_image_idle(
        self, cache_path, image_info,
        w, h, rowstride, has_alpha, pixels,
        prepend=True
    ):
        """Create thumbnail widget and insert into grid (main thread)."""
        self._placeholder.set_visible(False)

        # Rebuild texture from raw pixels — no decode on main thread
        gbytes = GLib.Bytes.new(pixels)
        fmt = (
            Gdk.MemoryFormat.R8G8B8A8
            if has_alpha else Gdk.MemoryFormat.R8G8B8
        )
        texture = Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)

        picture = Gtk.Picture(
            paintable=texture,
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        # Store path instead of full-res pixbuf
        picture._cache_path = cache_path

        # Create overlay container
        overlay = Gtk.Overlay()
        overlay.set_child(picture)

        # Add generation time overlay if available
        if image_info and 'generation_time' in image_info:
            gen_time = image_info['generation_time']
            time_label = Gtk.Label(
                label=self._format_time(gen_time),
                css_classes=['gen-time-overlay']
            )
            time_label.set_halign(Gtk.Align.END)
            time_label.set_valign(Gtk.Align.END)
            time_label.set_margin_end(6)
            time_label.set_margin_bottom(6)
            overlay.add_overlay(time_label)

        frame = Gtk.Frame(css_classes=['gallery-thumb'])
        frame.set_child(overlay)

        if prepend:
            # New generation images go to the top-left
            self._flow.prepend(frame)
            child = self._flow.get_child_at_index(0)
        else:
            # Batch-loaded images are appended in newest-first order
            self._flow.append(frame)
            child = self._flow.get_child_at_index(
                self._flow_child_count() - 1
            )

        # Attach image metadata for deletion
        child._image_info = image_info

        # CAPTURE-phase gesture: intercepts before FlowBox default handling
        gesture = Gtk.GestureClick()
        gesture.set_button(0)  # Listen to all buttons
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect('pressed', self._on_child_pressed, child)
        child.add_controller(gesture)

        return False

    def _flow_child_count(self):
        """Return the number of children currently in the flowbox."""
        count = 0
        while self._flow.get_child_at_index(count) is not None:
            count += 1
        return count

    # ------------------------------------------------------------------
    # Click / selection handling
    # ------------------------------------------------------------------

    def _get_all_children(self):
        """Return a list of all FlowBoxChild widgets in order."""
        children, i = [], 0
        while True:
            c = self._flow.get_child_at_index(i)
            if c is None:
                break
            children.append(c)
            i += 1
        return children

    def _on_child_pressed(self, gesture, n_press, x, y, child):
        """Handle left-click (with modifiers) and right-click."""
        button = gesture.get_current_button()
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Force keyboard focus to the FlowBox
        self._flow.grab_focus()

        # Sync selection and update navigation reference
        if button == 1 and not (ctrl or shift):
            self._flow.unselect_all()
            self._flow.select_child(child)
            self._last_activated_child = child
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return

        if button == 3:
            # Right-click: ensure clicked item is selected
            if not child.is_selected():
                self._flow.unselect_all()
                self._flow.select_child(child)
                self._last_activated_child = child
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_context_menu(child, x, y)
            return

        if button != 1:
            return

        if shift and self._last_activated_child is not None:
            # Range select from last activated to this child
            children = self._get_all_children()
            try:
                i1 = children.index(self._last_activated_child)
                i2 = children.index(child)
            except ValueError:
                i1, i2 = 0, 0
            start, end = min(i1, i2), max(i1, i2)
            self._flow.unselect_all()
            for c in children[start:end + 1]:
                self._flow.select_child(c)
        elif ctrl:
            # Toggle this child without affecting others
            if child.is_selected():
                self._flow.unselect_child(child)
            else:
                self._flow.select_child(child)
            self._last_activated_child = child
        else:
            # Plain click: single select
            self._flow.unselect_all()
            self._flow.select_child(child)
            self._last_activated_child = child

        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_selection_changed(self, flow_box):
        """Preview the image when exactly one item is selected."""
        selected = flow_box.get_selected_children()
        if len(selected) != 1:
            return
        child = selected[0]
        self._flow.set_focus_child(child)

        # Lazy-load full-res pixbuf from disk for the preview callback
        path = self._cache_path_from_child(child)
        pixbuf = self._pixbuf_from_child(child)
        if pixbuf and self._on_view_image:
            self._on_view_image(pixbuf, path)

    def _get_num_columns(self):
        """Count columns by comparing y-allocations of consecutive items."""
        children = self._get_all_children()
        if not children:
            return 1
        # Get the y-position of the first child
        ok, first_bounds = children[0].compute_bounds(self._flow)
        if not ok:
            return 1
        first_y = first_bounds.get_y()
        # Count how many children share the same row as the first
        count = 0
        for child in children:
            ok, bounds = child.compute_bounds(self._flow)
            if not ok:
                break
            if abs(bounds.get_y() - first_y) < 1:
                count += 1
            else:
                break
        return max(1, count)

    def _on_flow_key_pressed(self, controller, keyval, keycode, state):
        """Override arrow key navigation to use _last_activated_child."""
        if keyval == Gdk.KEY_Left:
            self.select_prev()
            return True
        if keyval == Gdk.KEY_Right:
            self.select_next()
            return True
        if keyval == Gdk.KEY_Up:
            self._select_offset(-self._get_num_columns())
            return True
        if keyval == Gdk.KEY_Down:
            self._select_offset(self._get_num_columns())
            return True
        return False

    def _on_child_activated(self, flow_box, child):
        """Update last activated child on activation (e.g. keyboard nav)."""
        self._last_activated_child = child

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, anchor_child, x, y):
        """Build and show a right-click popover near (x, y)."""
        selected = self._flow.get_selected_children()
        n = len(selected)

        if self._active_popover:
            self._active_popover.popdown()

        popover = Gtk.Popover()
        popover.set_parent(anchor_child)
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

        pixbufs = [
            pb for pb in
            (self._pixbuf_from_child(c) for c in selected)
            if pb
        ]

        def add_btn(label, cb):
            btn = Gtk.Button(label=label, has_frame=False)
            btn.set_halign(Gtk.Align.FILL)
            btn.connect('clicked', lambda b: (popover.popdown(), cb()))
            box.append(btn)

        if n == 1 and pixbufs:
            pb = pixbufs[0]
            add_btn('Copy to Clipboard',
                    lambda pb=pb: self._copy_to_clipboard(pb))
            add_btn('Save to\u2026',
                    lambda pb=pb: self._save_single(pb, anchor_child))
        elif n > 1 and pixbufs:
            pbs = list(pixbufs)
            add_btn(f'Copy {n} Images',
                    lambda pbs=pbs: self._copy_multiple_to_clipboard(pbs))
            add_btn(f'Save {n} images to folder\u2026',
                    lambda pbs=pbs: self._save_multiple(pbs, anchor_child))

        box.append(Gtk.Separator())
        label = 'Delete' if n == 1 else f'Delete {n} images'
        cs = list(selected)
        add_btn(label, lambda cs=cs: self._delete_children(cs))

        popover.set_child(box)
        self._active_popover = popover
        popover.popup()

    def _select_offset(self, offset):
        """Select the child at *offset* from the current, clamped to bounds."""
        children = self._get_all_children()
        if not children:
            return
        if self._last_activated_child in children:
            current = children.index(self._last_activated_child)
        else:
            # Default to start/end depending on direction
            current = 0 if offset > 0 else len(children) - 1
        # Clamp to valid range instead of wrapping
        idx = max(0, min(current + offset, len(children) - 1))
        child = children[idx]
        self._flow.unselect_all()
        self._flow.select_child(child)
        self._flow.set_focus_child(child)
        self._last_activated_child = child

    def select_next(self):
        """Select the next item, using last click as the reference."""
        children = self._get_all_children()
        if not children:
            return

        idx = 0
        # Use the last clicked child as the arrow-nav reference
        if self._last_activated_child in children:
            idx = (
                children.index(self._last_activated_child) + 1
            ) % len(children)

        child = children[idx]
        self._flow.unselect_all()
        self._flow.select_child(child)
        self._flow.set_focus_child(child)
        self._last_activated_child = child

    def select_prev(self):
        """Select the previous item, using last click as the reference."""
        children = self._get_all_children()
        if not children:
            return

        idx = len(children) - 1
        # Use the last clicked child as the arrow-nav reference
        if self._last_activated_child in children:
            idx = (
                children.index(self._last_activated_child) - 1
            ) % len(children)

        child = children[idx]
        self._flow.unselect_all()
        self._flow.select_child(child)
        self._flow.set_focus_child(child)
        self._last_activated_child = child

    def _copy_to_clipboard(self, pixbuf):
        """Copy pixbuf to the system clipboard as an image."""
        texture = self._texture_from_pixbuf(pixbuf)

        # Encode to PNG for maximum compatibility with other apps
        success, buffer = pixbuf.save_to_bufferv("png", [], [])

        if success:
            gbytes = GLib.Bytes.new(buffer)
            # Offer both the PNG bytes and the texture
            content = Gdk.ContentProvider.new_union([
                Gdk.ContentProvider.new_for_bytes("image/png", gbytes),
                Gdk.ContentProvider.new_for_value(texture)
            ])
        else:
            content = Gdk.ContentProvider.new_for_value(texture)

        self.get_clipboard().set_content(content)

    def _copy_multiple_to_clipboard(self, pixbufs):
        """Copy multiple images to the clipboard using their cache paths."""
        selected = self._flow.get_selected_children()
        file_list = []
        for child in selected:
            path = self._cache_path_from_child(child)
            if path and path.exists():
                file_list.append(Gio.File.new_for_path(str(path)))

        if not file_list:
            return

        # Build a FileList for apps that accept file drops (e.g. Telegram)
        gdk_file_list = Gdk.FileList.new_from_list(file_list)
        content = Gdk.ContentProvider.new_for_value(gdk_file_list)

        # Fallback: first image as PNG bytes and texture for other apps
        first_pb = pixbufs[0]
        texture = self._texture_from_pixbuf(first_pb)
        success, buffer = first_pb.save_to_bufferv("png", [], [])

        fallback_providers = [content,
                              Gdk.ContentProvider.new_for_value(texture)]
        if success:
            fallback_providers.append(
                Gdk.ContentProvider.new_for_bytes(
                    "image/png", GLib.Bytes.new(buffer)
                )
            )

        union_content = Gdk.ContentProvider.new_union(fallback_providers)
        self.get_clipboard().set_content(union_content)

    def _save_single(self, pixbuf, anchor):
        """Show a file-save dialog for a single image."""
        dialog = Gtk.FileChooserNative(
            title='Save Image',
            action=Gtk.FileChooserAction.SAVE,
            accept_label='Save',
            cancel_label='Cancel',
            transient_for=anchor.get_root()
        )
        dialog.set_current_name('image.png')
        dialog.connect(
            'response',
            lambda d, r: self._on_save_single_response(d, r, pixbuf)
        )
        dialog.show()

    def _on_save_single_response(self, dialog, response, pixbuf):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            if not path.lower().endswith('.png'):
                path += '.png'
            try:
                pixbuf.savev(path, 'png', [], [])
            except Exception as e:
                print(f'Gallery save error: {e}', flush=True)
        dialog.destroy()

    def _save_multiple(self, pixbufs, anchor):
        """Show a folder chooser and save all pixbufs into it."""
        dialog = Gtk.FileChooserNative(
            title='Save Images to Folder',
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label='Save Here',
            cancel_label='Cancel',
            transient_for=anchor.get_root()
        )
        dialog.connect(
            'response',
            lambda d, r: self._on_save_multiple_response(d, r, pixbufs)
        )
        dialog.show()

    def _on_save_multiple_response(self, dialog, response, pixbufs):
        if response == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file().get_path()
            for i, pixbuf in enumerate(pixbufs, start=1):
                path = os.path.join(folder, f'image_{i:03d}.png')
                try:
                    pixbuf.savev(path, 'png', [], [])
                except Exception as e:
                    print(f'Gallery save error ({path}): {e}', flush=True)
        dialog.destroy()

    def _delete_children(self, children):
        """Request deletion of each child, calling the delete callback."""
        for child in children:
            image_info = getattr(child, '_image_info', None)
            cache_path = self._cache_path_from_child(child)

            def make_remove(c):
                def remove():
                    self._flow.remove(c)
                    if self._flow.get_child_at_index(0) is None:
                        self._placeholder.set_visible(True)
                return remove

            if self._on_delete_image:
                self._on_delete_image(
                    image_info, cache_path, make_remove(child)
                )
            else:
                make_remove(child)()

    # ------------------------------------------------------------------
    # Child helpers
    # ------------------------------------------------------------------

    def _cache_path_from_child(self, child):
        """Extract the stored cache path from a FlowBoxChild."""
        widget = child.get_child()
        while widget:
            if hasattr(widget, '_cache_path'):
                return widget._cache_path
            if hasattr(widget, 'get_child'):
                widget = widget.get_child()
            else:
                break
        return None

    def _pixbuf_from_child(self, child):
        """Lazy-load a full-res pixbuf from the child's cache path."""
        path = self._cache_path_from_child(child)
        if path is None:
            return None
        try:
            return self._pixbuf_from_bytes(path.read_bytes())
        except Exception as e:
            print(f"Gallery: failed to load image: {e}", flush=True)
            return None

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time(seconds: float) -> str:
        """
        Format generation time in a human-readable way.

        Returns time in format like '1m 23s' or '45s'.
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"

    @staticmethod
    def _pixbuf_from_bytes(data: bytes):
        """Load a GdkPixbuf from raw bytes."""
        loader = GdkPixbuf.PixbufLoader.new()
        try:
            loader.write(data)
            loader.close()
            return loader.get_pixbuf()
        except Exception:
            try:
                loader.close()
            except Exception:
                pass
            return None

    @staticmethod
    def _scale_pixbuf(pixbuf, size: int):
        """Scale pixbuf to fit within a square of *size* px."""
        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = size / max(w, h)
        return pixbuf.scale_simple(
            max(1, int(w * scale)),
            max(1, int(h * scale)),
            GdkPixbuf.InterpType.BILINEAR
        )

    @staticmethod
    def _texture_from_pixbuf(pixbuf):
        """Convert GdkPixbuf to Gdk.MemoryTexture."""
        w, h = pixbuf.get_width(), pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        gbytes = GLib.Bytes.new(pixels)
        fmt = (
            Gdk.MemoryFormat.R8G8B8A8
            if has_alpha else Gdk.MemoryFormat.R8G8B8
        )
        return Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)
