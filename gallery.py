#!/usr/bin/python3
"""Gallery page showing generated images for the current session."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, Gdk, GdkPixbuf, GLib  # noqa

THUMBNAIL_SIZE = 220


class GalleryPage(Gtk.ScrolledWindow):
    """Scrollable grid of thumbnails generated in the current session."""

    def __init__(self, on_view_image=None, on_delete_image=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        self._images = []
        # Called with a pixbuf when the user clicks a thumbnail
        self._on_view_image = on_view_image
        # Called with (image_info, remove_fn) when user deletes a thumbnail
        self._on_delete_image = on_delete_image

        # Outer box for padding
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL
        )
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(outer, f'set_margin_{side}')(12)

        # FlowBox for the thumbnail grid
        self._flow = Gtk.FlowBox(
            max_children_per_line=10,
            min_children_per_line=1,
            row_spacing=8,
            column_spacing=8,
            homogeneous=True,
            selection_mode=Gtk.SelectionMode.SINGLE,
            valign=Gtk.Align.START
        )
        self._flow.connect('child-activated', self._on_thumbnail_activated)
        self._flow.connect(
            'selected-children-changed', self._on_selection_changed
        )

        outer.append(self._flow)
        self.set_child(outer)

        # Placeholder shown when no images exist yet
        self._placeholder = Adw.StatusPage(
            icon_name='image-x-generic-symbolic',
            title='No Images Yet',
            description='Generated images will appear here.'
        )

        # Overlay so placeholder sits on top of the scroll content
        self._overlay = Gtk.Overlay()
        self._overlay.set_child(self)
        self._placeholder.set_visible(True)
        self._overlay.add_overlay(self._placeholder)

    @property
    def widget(self):
        """Return the top-level widget to pack into the stack."""
        return self._overlay

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_image(self, data: bytes, image_info: dict = None):
        """
        Add raw image bytes to the gallery.

        Called from the main thread via GLib.idle_add after generation.
        image_info should contain 'filename', 'subfolder', and 'type'
        keys from ComfyUI's output metadata.
        """
        GLib.idle_add(self._add_image_idle, data, image_info)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_image_idle(self, data: bytes, image_info: dict):
        """Create thumbnail and append to flow box (runs on main thread)."""
        pixbuf = self._pixbuf_from_bytes(data)
        if pixbuf is None:
            return False

        self._images.append(data)
        self._placeholder.set_visible(False)

        thumb = self._scale_pixbuf(pixbuf, THUMBNAIL_SIZE)
        texture = self._texture_from_pixbuf(thumb)

        picture = Gtk.Picture(
            paintable=texture,
            content_fit=Gtk.ContentFit.COVER,
            can_shrink=True
        )
        picture.set_size_request(THUMBNAIL_SIZE, THUMBNAIL_SIZE)

        # Store full pixbuf on the picture widget for the viewer
        picture._full_pixbuf = pixbuf

        frame = Gtk.Frame(css_classes=['gallery-thumb'])
        frame.set_child(picture)

        # Wrap frame in overlay to allow the delete button on hover
        thumb_overlay = Gtk.Overlay()
        thumb_overlay.set_child(frame)

        # Delete button, shown only on hover
        delete_btn = Gtk.Button(
            icon_name='user-trash-symbolic',
            css_classes=['gallery-delete-btn'],
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            margin_end=4,
            margin_top=4,
            visible=False,
            # Prevent button clicks from activating the FlowBoxChild
            can_focus=False
        )
        thumb_overlay.add_overlay(delete_btn)
        thumb_overlay.set_measure_overlay(delete_btn, False)

        # Show/hide delete button on hover
        motion = Gtk.EventControllerMotion()
        motion.connect(
            'enter',
            lambda c, x, y: delete_btn.set_visible(True)
        )
        motion.connect(
            'leave',
            lambda c: delete_btn.set_visible(False)
        )
        thumb_overlay.add_controller(motion)

        # Prepend so newest images appear at the top
        self._flow.prepend(thumb_overlay)

        # Retrieve the FlowBoxChild wrapping our overlay
        child = self._flow.get_child_at_index(0)

        # Attach metadata and removal closure to the delete button
        delete_btn._image_info = image_info
        delete_btn._child = child
        delete_btn.connect(
            'clicked',
            self._on_delete_clicked
        )

        return False

    def _on_delete_clicked(self, btn):
        """Forward deletion request to the provided callback."""
        if self._on_delete_image is None:
            return
        child = btn._child
        image_info = btn._image_info

        def remove_child():
            """Remove the thumbnail child and show placeholder if empty."""
            self._flow.remove(child)
            if self._flow.get_child_at_index(0) is None:
                self._placeholder.set_visible(True)

        self._on_delete_image(image_info, remove_child)

    def _pixbuf_from_child(self, child):
        """Extract the stored pixbuf from a FlowBoxChild."""
        # Structure: FlowBoxChild > Overlay > Frame > Picture
        thumb_overlay = child.get_child()
        if thumb_overlay is None:
            return None
        frame = thumb_overlay.get_child()
        if frame is None:
            return None
        picture = frame.get_child()
        if picture is None or not hasattr(picture, '_full_pixbuf'):
            return None
        return picture._full_pixbuf

    def _on_thumbnail_activated(self, flow_box, child):
        """Pass the full pixbuf to the viewer callback on activation."""
        pixbuf = self._pixbuf_from_child(child)
        if pixbuf and self._on_view_image:
            self._on_view_image(pixbuf)

    def _on_selection_changed(self, flow_box):
        """Preview the selected image immediately (e.g. on arrow key nav)."""
        selected = flow_box.get_selected_children()
        if not selected:
            return
        pixbuf = self._pixbuf_from_child(selected[0])
        if pixbuf and self._on_view_image:
            self._on_view_image(pixbuf)

    # ------------------------------------------------------------------
    # Static / utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pixbuf_from_bytes(data: bytes):
        """Load a GdkPixbuf from raw image bytes."""
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
        """Scale pixbuf to fit within a square of *size* px, keep aspect."""
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        scale = size / max(w, h)
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        return pixbuf.scale_simple(
            new_w, new_h, GdkPixbuf.InterpType.BILINEAR
        )

    @staticmethod
    def _texture_from_pixbuf(pixbuf):
        """Convert a GdkPixbuf to a Gdk.MemoryTexture."""
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        has_alpha = pixbuf.get_has_alpha()
        pixels = pixbuf.get_pixels()
        gbytes = GLib.Bytes.new(pixels)
        fmt = (
            Gdk.MemoryFormat.R8G8B8A8
            if has_alpha else Gdk.MemoryFormat.R8G8B8
        )
        return Gdk.MemoryTexture.new(w, h, fmt, gbytes, rowstride)
