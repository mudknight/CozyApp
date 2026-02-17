#!/usr/bin/python3
"""Gallery page: thumbnail grid with multi-select and context menus."""
import os
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, Gdk, GdkPixbuf, GLib  # noqa

THUMBNAIL_SIZE = 220


class GalleryPage(Gtk.ScrolledWindow):
    """Scrollable FlowBox grid with multi-selection and context menus."""

    def __init__(self, on_view_image=None, on_delete_image=None):
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def widget(self):
        """Return the top-level widget to pack into the view stack."""
        return self._overlay

    def add_image(self, data: bytes, image_info: dict = None):
        """Add raw image bytes to the gallery (thread-safe)."""
        GLib.idle_add(self._add_image_idle, data, image_info)

    def delete_by_pixbuf(self, pixbuf):
        """
        Find the gallery item whose pixbuf is *pixbuf* and delete it.

        Used by the preview context menu when the shown image was
        selected from the gallery.
        """
        for child in self._get_all_children():
            if self._pixbuf_from_child(child) is pixbuf:
                self._delete_children([child])
                break

    # ------------------------------------------------------------------
    # Adding thumbnails
    # ------------------------------------------------------------------

    def _add_image_idle(self, data: bytes, image_info: dict):
        """Create thumbnail, prepend to grid (runs on main thread)."""
        pixbuf = self._pixbuf_from_bytes(data)
        if pixbuf is None:
            return False

        self._placeholder.set_visible(False)

        thumb = self._scale_pixbuf(pixbuf, THUMBNAIL_SIZE)
        texture = self._texture_from_pixbuf(thumb)

        picture = Gtk.Picture(
            paintable=texture,
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        # Store full-res pixbuf for preview and context menu
        picture._full_pixbuf = pixbuf

        frame = Gtk.Frame(css_classes=['gallery-thumb'])
        frame.set_child(picture)

        # Newest images go to the top-left
        self._flow.prepend(frame)

        # Retrieve the FlowBoxChild that wraps our frame
        child = self._flow.get_child_at_index(0)
        # Attach image metadata for deletion
        child._image_info = image_info

        # CAPTURE-phase gesture: intercepts before FlowBox default handling
        gesture = Gtk.GestureClick()
        gesture.set_button(0)  # Listen to all buttons
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect('pressed', self._on_child_pressed, child)
        child.add_controller(gesture)

        return False

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
        pixbuf = self._pixbuf_from_child(selected[0])
        if pixbuf and self._on_view_image:
            self._on_view_image(pixbuf)

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
            add_btn(f'Save {n} images to folder\u2026',
                    lambda pbs=pbs: self._save_multiple(pbs, anchor_child))

        box.append(Gtk.Separator())
        label = 'Delete' if n == 1 else f'Delete {n} images'
        cs = list(selected)
        add_btn(label, lambda cs=cs: self._delete_children(cs))

        popover.set_child(box)
        self._active_popover = popover
        popover.popup()

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

            def make_remove(c):
                def remove():
                    self._flow.remove(c)
                    if self._flow.get_child_at_index(0) is None:
                        self._placeholder.set_visible(True)
                return remove

            if self._on_delete_image:
                self._on_delete_image(image_info, make_remove(child))
            else:
                make_remove(child)()

    # ------------------------------------------------------------------
    # Child pixbuf helper
    # ------------------------------------------------------------------

    def _pixbuf_from_child(self, child):
        """Extract stored pixbuf from FlowBoxChild > Frame > Picture."""
        frame = child.get_child()
        if frame is None:
            return None
        picture = frame.get_child()
        if picture is None or not hasattr(picture, '_full_pixbuf'):
            return None
        return picture._full_pixbuf

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

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
