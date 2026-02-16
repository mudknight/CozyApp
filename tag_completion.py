#!/usr/bin/env python3
"""Tag autocompletion functionality for ComfyUI frontend."""

from gi.repository import Gtk, Gdk, Pango
import csv


class TagCompletion:
    """
    Handles tag autocompletion from danbooru.csv file.
    """

    # Category color mapping
    CATEGORY_COLORS = {
        0: ('#4A90E2', 'General'),       # Blue
        1: ('#F5A623', 'Artist'),        # Orange
        2: ('#9B9B9B', 'Unused'),        # Gray
        3: ('#BD10E0', 'Copyright'),     # Purple
        4: ('#7ED321', 'Character'),     # Green
        5: ('#D0021B', 'Post'),          # Red
    }

    def __init__(self, log_callback=None):
        """
        Initialize tag completion.

        Args:
            log_callback: Optional callback function for logging messages
        """
        self.tag_data = {}  # tag -> (category, usage)
        self.tag_aliases = {}  # alias -> original_tag
        self.sorted_tags = []
        self.completion_popup = None
        self.log = log_callback if log_callback else lambda x: None

    def load_tags(self, filepath='danbooru.csv'):
        """
        Load tags from CSV file.

        Args:
            filepath: Path to the CSV file containing tags
        """
        try:
            tag_list = []
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 3:
                        tag = row[0]
                        category = int(row[1])
                        usage = int(row[2])
                        aliases = row[3].split(',') if len(row) > 3 and (
                            row[3]
                        ) else []

                        tag_list.append((tag, category, usage))
                        self.tag_data[tag] = (category, usage)

                        # Add aliases
                        for alias in aliases:
                            alias = alias.strip()
                            if alias:
                                self.tag_aliases[alias] = tag

            # Sort by usage (descending)
            tag_list.sort(key=lambda x: x[2], reverse=True)
            self.sorted_tags = [tag for tag, _, _ in tag_list]

            total_tags = (
                len(self.sorted_tags) + len(self.tag_aliases)
            )
            self.log(
                f"Loaded {len(self.sorted_tags)} tags and "
                f"{len(self.tag_aliases)} aliases from {filepath} "
                f"(total: {total_tags})"
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

        matches = []
        seen = set()

        # Search in sorted tags (already sorted by usage)
        for tag in self.sorted_tags:
            if tag.lower().startswith(current) and (
                tag.lower() != current
            ):
                if tag not in seen:
                    matches.append(tag)
                    seen.add(tag)
                if len(matches) >= 10:
                    break

        # Search in aliases
        if len(matches) < 10:
            for alias, original_tag in self.tag_aliases.items():
                if alias.lower().startswith(current) and (
                    alias.lower() != current
                ):
                    # Use original tag but show it came from alias
                    if original_tag not in seen:
                        matches.append(original_tag)
                        seen.add(original_tag)
                    if len(matches) >= 10:
                        break

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

            # Get tag data
            category, usage = self.tag_data.get(tag, (0, 0))
            color, cat_name = self.CATEGORY_COLORS.get(
                category, ('#CCCCCC', 'Unknown')
            )

            # Create horizontal box for tag display
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)

            # Tag label
            tag_label = Gtk.Label(label=tag, xalign=0)
            tag_label.set_hexpand(True)
            hbox.append(tag_label)

            # Category box
            cat_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=4
            )

            # Colored category indicator
            cat_label = Gtk.Label(label=cat_name[0])
            cat_label.set_size_request(20, 20)
            cat_label.set_markup(
                f'<span background="{color}" '
                f'foreground="white" weight="bold"> {cat_name[0]} </span>'
            )
            cat_box.append(cat_label)

            # Usage label
            usage_label = Gtk.Label(
                label=f"{usage:,}",
                xalign=1
            )
            usage_label.add_css_class('dim-label')
            usage_label.set_size_request(80, -1)
            cat_box.append(usage_label)

            hbox.append(cat_box)

            row.set_child(hbox)
            listbox.append(row)
            if i == 0:
                listbox.select_row(row)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(listbox)
        scrolled.set_max_content_height(300)
        scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scrolled.set_size_request(400, min(len(suggestions) * 40, 300))

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
            if keyval == Gdk.KEY_Tab and self.sorted_tags:
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
                # Extract tag from the hbox structure
                hbox = selected.get_child()
                if hbox:
                    tag_label = hbox.get_first_child()
                    if tag_label:
                        tag = tag_label.get_label()
                        self.insert_completion(textview, tag)
                self.completion_popup.popdown()
            return True

        return False

    def close_popup(self):
        """Close the completion popup if open."""
        if self.completion_popup:
            self.completion_popup.popdown()
