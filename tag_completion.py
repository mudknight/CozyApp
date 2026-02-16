#!/usr/bin/env python3
"""Tag autocompletion functionality for ComfyUI frontend."""

from gi.repository import Gtk, Gdk
import csv


class TagCompletion:
    """
    Handles tag autocompletion from danbooru.csv file.
    """

    def __init__(self, log_callback=None):
        """
        Initialize tag completion.

        Args:
            log_callback: Optional callback function for logging messages
        """
        self.danbooru_tags = []
        self.completion_popup = None
        self.log = log_callback if log_callback else lambda x: None

    def load_tags(self, filepath='danbooru.csv'):
        """
        Load tags from CSV file.

        Args:
            filepath: Path to the CSV file containing tags
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if row:
                        self.danbooru_tags.append(row[0])
            self.log(
                f"Loaded {len(self.danbooru_tags)} tags from {filepath}"
            )
        except Exception as e:
            self.log(f"Could not load {filepath}: {e}")

    def get_completions(self, text):
        """
        Get tag completions for the current text.

        Args:
            text: Full text buffer content

        Returns:
            List of matching tag suggestions (max 10)
        """
        words = text.replace(',', ' ').split()
        if not words:
            return []
        current = words[-1].lower()
        if len(current) < 2:
            return []
        matches = [
            tag for tag in self.danbooru_tags
            if tag.lower().startswith(current) and tag.lower() != current
        ]
        return matches[:10]

    def show_popup(self, textview, suggestions):
        """
        Show completion popup with suggestions.

        Args:
            textview: GtkSourceView to attach popup to
            suggestions: List of tag suggestions to display
        """
        if not suggestions:
            return

        if self.completion_popup:
            self.completion_popup.popdown()

        popover = Gtk.Popover()
        popover.set_parent(textview)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_autohide(False)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        for i, tag in enumerate(suggestions):
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=tag, xalign=0)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.set_child(label)
            listbox.append(row)
            if i == 0:
                listbox.select_row(row)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(listbox)
        scrolled.set_max_content_height(200)
        scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scrolled.set_size_request(200, min(len(suggestions) * 30, 200))

        popover.set_child(scrolled)

        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        location = textview.get_iter_location(iter_cursor)

        rect = Gdk.Rectangle()
        rect.x = location.x
        rect.y = location.y + location.height
        rect.width = 1
        rect.height = 1

        popover.set_pointing_to(rect)

        def on_row_activated(listbox, row):
            tag = suggestions[row.get_index()]
            self.insert_completion(textview, tag)
            popover.popdown()

        listbox.connect("row-activated", on_row_activated)

        self.completion_popup = popover
        popover.popup()

    def insert_completion(self, textview, tag):
        """
        Insert completed tag at cursor position.

        Args:
            textview: GtkSourceView to insert into
            tag: Tag to insert
        """
        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)

        iter_start = iter_cursor.copy()
        while not iter_start.starts_line():
            iter_start.backward_char()
            char = iter_start.get_char()
            if char in ' ,\n\t':
                iter_start.forward_char()
                break

        formatted_tag = tag.replace('_', ' ')
        formatted_tag = formatted_tag.replace(
            '(', '\\('
        ).replace(')', '\\)')

        buffer.delete(iter_start, iter_cursor)
        buffer.insert(iter_start, formatted_tag + ", ")

    def should_show_completion(self, buffer, iter_cursor):
        """
        Check if completion should be shown at cursor position.

        Args:
            buffer: Text buffer
            iter_cursor: Current cursor position

        Returns:
            True if completion should be shown
        """
        offset = iter_cursor.get_offset()

        if offset == 0:
            return False

        iter_tag_start = iter_cursor.copy()

        while not iter_tag_start.starts_line():
            iter_tag_start.backward_char()
            char = iter_tag_start.get_char()
            if char == '\n' or char == ',':
                iter_tag_start.forward_char()
                break

        text_from_tag_start = buffer.get_text(
            iter_tag_start, iter_cursor, False
        )

        non_space_chars = ''.join(text_from_tag_start.split())

        if (len(non_space_chars) >= 2 and
            '\n' not in text_from_tag_start and
                ',' not in text_from_tag_start):
            return True

        return False

    def handle_key_press(self, textview, keyval):
        """
        Handle key press for completion navigation.

        Args:
            textview: GtkSourceView
            keyval: Key value

        Returns:
            True if key was handled, False otherwise
        """
        if not (self.completion_popup and
                self.completion_popup.is_visible()):
            if keyval == Gdk.KEY_Tab and self.danbooru_tags:
                buffer = textview.get_buffer()
                text = buffer.get_text(
                    buffer.get_start_iter(),
                    buffer.get_end_iter(),
                    False
                )
                suggestions = self.get_completions(text)
                if suggestions:
                    self.show_popup(textview, suggestions)
                    return True
            return False

        scrolled = self.completion_popup.get_child()
        listbox = (
            scrolled.get_child().get_child()
            if scrolled.get_child() else None
        )

        if not listbox:
            return False

        if keyval == Gdk.KEY_Escape:
            self.completion_popup.popdown()
            return True
        elif keyval == Gdk.KEY_Down:
            selected = listbox.get_selected_row()
            if selected:
                index = selected.get_index()
                next_row = listbox.get_row_at_index(index + 1)
                if next_row:
                    listbox.select_row(next_row)
            else:
                first_row = listbox.get_row_at_index(0)
                if first_row:
                    listbox.select_row(first_row)
            return True
        elif keyval == Gdk.KEY_Up:
            selected = listbox.get_selected_row()
            if selected:
                index = selected.get_index()
                if index > 0:
                    prev_row = listbox.get_row_at_index(index - 1)
                    if prev_row:
                        listbox.select_row(prev_row)
            return True
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return):
            selected = listbox.get_selected_row()
            if not selected:
                selected = listbox.get_row_at_index(0)
            if selected:
                tag = selected.get_child().get_label()
                self.insert_completion(textview, tag)
                self.completion_popup.popdown()
            return True

        return False

    def close_popup(self):
        """Close the completion popup if open."""
        if self.completion_popup:
            self.completion_popup.popdown()
