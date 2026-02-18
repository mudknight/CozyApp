#!/usr/bin/env python3
"""Tag autocompletion functionality for ComfyUI frontend."""

from gi.repository import Gtk, Gdk, Pango
import csv
import json
import urllib.request
import urllib.error


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
        self.characters = []  # character names from API
        self.loras = []  # LoRA names from API
        self.completion_popup = None
        self.listbox = None
        self.scrolled = None
        self.current_textview = None
        self.log = log_callback if log_callback else lambda x: None
        # Set of tags to exclude from completions
        self._blacklist = set()

    def set_blacklist(self, tags):
        """
        Update the tag blacklist.

        Args:
            tags: Iterable of tag strings to exclude from completions
        """
        # Normalise: lowercase and underscores like the tag data
        self._blacklist = {
            t.strip().lower().replace(' ', '_') for t in tags if t.strip()
        }

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

    def load_characters(self, url='http://localhost:8188/character_editor'):
        """
        Load character names from API endpoint.

        Args:
            url: API endpoint URL
        """
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if isinstance(data, dict):
                    self.characters = sorted(list(data.keys()))
                    self.log(
                        f"Loaded {len(self.characters)} characters "
                        f"from {url}"
                    )
                else:
                    self.log(f"Unexpected data format from {url}")
        except urllib.error.URLError as e:
            self.log(f"Could not load characters from {url}: {e}")
        except Exception as e:
            self.log(f"Error loading characters: {e}")

    def load_loras(
        self,
        url='http://localhost:8188/object_info/LoraLoader'
    ):
        """
        Load LoRA names from API endpoint.

        Args:
            url: API endpoint URL
        """
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                response_data = response.read().decode('utf-8')

                # Check if response is empty
                if not response_data.strip():
                    self.log(
                        f"Empty response from {url}. "
                        f"LoRA autocomplete disabled."
                    )
                    return

                data = json.loads(response_data)

                # Extract LoRA list from object_info response
                # Format: {"LoraLoader": {"input": {"required":
                # {"lora_name": [["lora1.safetensors", ...]]}}}
                lora_list = None
                if isinstance(data, dict) and 'LoraLoader' in data:
                    node_data = data['LoraLoader']
                    inputs = node_data.get('input', {})
                    for cat in ['required', 'optional']:
                        if 'lora_name' in inputs.get(cat, {}):
                            entry = inputs[cat]['lora_name']
                            lora_list = (
                                entry[0] if isinstance(entry, list)
                                and isinstance(entry[0], list)
                                else entry
                            )
                            break

                if lora_list:
                    # Remove file extensions if present
                    self.loras = sorted([
                        lora.rsplit('.', 1)[0] if '.' in lora else lora
                        for lora in lora_list
                    ])
                    self.log(
                        f"Loaded {len(self.loras)} LoRAs from {url}"
                    )
                else:
                    self.log(
                        f"Could not find LoRA list in response. "
                        f"LoRA autocomplete disabled."
                    )
        except urllib.error.URLError as e:
            self.log(
                f"Could not load LoRAs from {url}: {e}. "
                f"LoRA autocomplete disabled."
            )
        except json.JSONDecodeError as e:
            self.log(
                f"Invalid JSON from {url}: {e}. "
                f"LoRA autocomplete disabled."
            )
        except Exception as e:
            self.log(f"Error loading LoRAs: {e}")

    def get_completions(self, text, cursor_pos=None):
        """
        Get tag completions for the current text.

        Args:
            text: Full text buffer content
            cursor_pos: Cursor position (character offset) in text

        Returns:
            List of matching tag suggestions (max 10)
        """
        # If cursor position provided, extract current tag at that position
        if cursor_pos is not None:
            # Find start of current tag (go back to last comma or newline)
            tag_start = cursor_pos
            while tag_start > 0:
                if text[tag_start - 1] in ',\n':
                    break
                tag_start -= 1

            # Extract text from tag start to cursor
            current = text[tag_start:cursor_pos].strip()
        else:
            # Fallback to old behavior for backward compatibility
            tags = text.split(',')
            if not tags:
                return []
            current = tags[-1].strip()

        if len(current) < 2:
            return []

        # Check if we're completing a character or LoRA
        if ':' in current:
            # Handle LoRA completion: <lora:name
            if '<lora:' in current.lower():
                # Extract search term after <lora:
                lora_start = current.lower().rfind('<lora:')
                search = current[lora_start + 6:].strip().lower()

                if not search:
                    # Return all LoRAs if nothing typed yet
                    return self.loras[:10]
                # Match against the filename only (after last /),
                # using substring so partial names resolve correctly
                matches = [
                    lora for lora in self.loras
                    if search in lora.split('/')[-1].lower()
                ]
                return matches[:10]

            # Handle character completion: character:name
            prefix, search = current.rsplit(':', 1)
            prefix = prefix.strip().lower()
            search = search.strip().lower()

            if prefix == 'character':
                if not search:
                    # Return all characters if nothing typed yet
                    return self.characters[:10]
                # Substring match against the name only (after last /)
                matches = [
                    char for char in self.characters
                    if search in char.split('/')[-1].lower()
                ]
                return matches[:10]

        current = current.lower()

        # Normalize search term: spaces -> underscores,
        # escaped parens -> normal parens
        current = current.replace(' ', '_')
        current = current.replace('\\(', '(').replace('\\)', ')')

        matches = []
        seen = set()

        # Search in sorted tags (already sorted by usage)
        # Prefix matches first, then substring matches
        prefix_matches = []
        substr_matches = []
        for tag in self.sorted_tags:
            tl = tag.lower()
            if tl == current:
                continue
            # Skip blacklisted tags
            if tl in self._blacklist:
                continue
            if tl.startswith(current):
                prefix_matches.append(tag)
            elif current in tl:
                substr_matches.append(tag)
            if len(prefix_matches) >= 10:
                break

        for tag in prefix_matches + substr_matches:
            if tag not in seen:
                matches.append(tag)
                seen.add(tag)
            if len(matches) >= 10:
                break

        # Search in aliases
        if len(matches) < 10:
            for alias, original_tag in self.tag_aliases.items():
                al = alias.lower()
                if al == current:
                    continue
                # Skip aliases whose target is blacklisted
                if original_tag.lower() in self._blacklist:
                    continue
                if al.startswith(current) or current in al:
                    if original_tag not in seen:
                        matches.append(original_tag)
                        seen.add(original_tag)
                    if len(matches) >= 10:
                        break

        return matches[:10]

    def _create_popup(self, textview):
        """
        Create the completion popup structure once.

        Args:
            textview: GtkSourceView to attach popup to
        """
        self.current_textview = textview

        popover = Gtk.Popover()
        popover.set_parent(textview)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_autohide(False)
        popover.set_has_arrow(False)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        def on_row_activated(listbox, row):
            tag_label = row.get_child().get_first_child()
            if tag_label:
                tag = tag_label.get_label()
                self.insert_completion(textview, tag)
            popover.popdown()

        self.listbox.connect("row-activated", on_row_activated)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.listbox)
        self.scrolled.set_max_content_height(300)
        self.scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )

        popover.set_child(self.scrolled)

        self.completion_popup = popover

    def _populate_listbox(self, suggestions):
        """
        Populate the listbox with tag suggestions.

        Args:
            suggestions: List of tag suggestions to display
        """
        # Clear existing rows
        while True:
            row = self.listbox.get_row_at_index(0)
            if row is None:
                break
            self.listbox.remove(row)

        # Add new suggestions
        for i, tag in enumerate(suggestions):
            row = Gtk.ListBoxRow()

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

            # Check if this is a character, LoRA, or regular tag
            if tag in self.tag_data:
                # Get tag data for regular tags
                category, usage = self.tag_data.get(tag, (0, 0))
                color, cat_name = self.CATEGORY_COLORS.get(
                    category, ('#CCCCCC', 'Unknown')
                )

                # Category box
                cat_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=4
                )

                # Colored category indicator
                cat_label = Gtk.Label(label=cat_name[0])
                cat_label.set_size_request(20, 20)
                cat_label.set_markup(
                    f'<span background="{color}" '
                    f'foreground="white" weight="bold"> '
                    f'{cat_name[0]} </span>'
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
            elif tag in self.loras:
                # For LoRAs, add a badge
                lora_badge = Gtk.Label()
                lora_badge.set_markup(
                    '<span background="#FF6B6B" '
                    'foreground="white" weight="bold"> '
                    'LORA </span>'
                )
                hbox.append(lora_badge)
            else:
                # For characters, add a badge
                char_badge = Gtk.Label()
                char_badge.set_markup(
                    '<span background="#50C878" '
                    'foreground="white" weight="bold"> '
                    'CHARACTER </span>'
                )
                hbox.append(char_badge)

            row.set_child(hbox)
            self.listbox.append(row)

            # Select first row
            if i == 0:
                self.listbox.select_row(row)

    def _position_popup(self, textview):
        """
        Position the popup at the cursor location.

        Args:
            textview: GtkSourceView to position relative to
        """
        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        location = textview.get_iter_location(iter_cursor)

        rect = Gdk.Rectangle()
        rect.x = location.x
        rect.y = location.y + location.height
        rect.width = 1
        rect.height = 1

        self.completion_popup.set_pointing_to(rect)

    def show_popup(self, textview, suggestions):
        """
        Show completion popup with suggestions.

        Args:
            textview: GtkSourceView to attach popup to
            suggestions: List of tag suggestions to display
        """
        if not suggestions:
            return

        # Create popup if it doesn't exist or textview changed
        if (not self.completion_popup or
                self.current_textview != textview):
            if self.completion_popup:
                self.completion_popup.unparent()
            self._create_popup(textview)

        # Populate with new suggestions
        self._populate_listbox(suggestions)

        # Update scrolled window size
        self.scrolled.set_size_request(
            400, min(len(suggestions) * 40, 300)
        )

        # Position and show
        self._position_popup(textview)

        if not self.completion_popup.is_visible():
            self.completion_popup.popup()

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
        # Look back until comma or newline (not space, since tags
        # can have spaces)
        while not iter_start.starts_line():
            iter_start.backward_char()
            char = iter_start.get_char()
            if char in ',\n':
                iter_start.forward_char()
                break

        # Skip leading whitespace after comma
        while iter_start.get_char() in ' \t':
            iter_start.forward_char()

        # Get the text being replaced to check for special prefixes
        replaced_text = buffer.get_text(iter_start, iter_cursor, False)
        is_character = 'character:' in replaced_text.lower()
        is_lora = '<lora:' in replaced_text.lower()

        if is_lora:
            # For LoRAs, insert full syntax with default weight
            formatted_tag = f"<lora:{tag}:1.0>"
        elif is_character:
            # For characters, keep as-is and preserve the prefix
            formatted_tag = f"character:{tag}"
        else:
            # For regular tags, replace underscores and escape parens
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
                cursor = buffer.get_insert()
                iter_cursor = buffer.get_iter_at_mark(cursor)
                text = buffer.get_text(
                    buffer.get_start_iter(),
                    buffer.get_end_iter(),
                    False
                )
                cursor_pos = iter_cursor.get_offset()
                suggestions = self.get_completions(text, cursor_pos)
                if suggestions:
                    self.show_popup(textview, suggestions)
                    return True
            return False

        if not self.listbox:
            return False

        if keyval == Gdk.KEY_Escape:
            self.completion_popup.popdown()
            return True
        elif keyval == Gdk.KEY_Down:
            selected = self.listbox.get_selected_row()
            if selected:
                index = selected.get_index()
                next_row = self.listbox.get_row_at_index(index + 1)
                if next_row:
                    self.listbox.select_row(next_row)
            else:
                first_row = self.listbox.get_row_at_index(0)
                if first_row:
                    self.listbox.select_row(first_row)
            return True
        elif keyval == Gdk.KEY_Up:
            selected = self.listbox.get_selected_row()
            if selected:
                index = selected.get_index()
                if index > 0:
                    prev_row = self.listbox.get_row_at_index(index - 1)
                    if prev_row:
                        self.listbox.select_row(prev_row)
            return True
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return):
            selected = self.listbox.get_selected_row()
            if not selected:
                selected = self.listbox.get_row_at_index(0)
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
