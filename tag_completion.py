#!/usr/bin/env python3
"""Tag autocompletion functionality for ComfyUI frontend."""

from gi.repository import Gtk, Gdk, Pango, GLib
import csv
import json
import urllib.request
import urllib.error
import config


def _max_items():
    """Return the configured max number of completion suggestions."""
    return int(config.get('completion_max_items') or 10)


class TagCompletion:
    """
    Handles tag autocompletion from danbooru.csv file.
    """

    # Category name + CSS class mapping (color defined in style.css)
    CATEGORY_COLORS = {
        0: ('#4A90E2', 'General'),       # Blue
        1: ('#D0021B', 'Artist'),        # Red
        2: ('#9B9B9B', 'Unused'),        # Gray
        3: ('#BD10E0', 'Copyright'),     # Purple
        4: ('#7ED321', 'Character'),     # Green
        5: ('#F5A623', 'Meta'),          # Yellow
    }

    # Map category index -> CSS badge class
    CATEGORY_CSS = {
        0: 'badge-general',
        1: 'badge-artist',
        2: 'badge-unused',
        3: 'badge-copyright',
        4: 'badge-character',
        5: 'badge-meta',
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
        self.tag_presets = []  # tag preset names from API
        self.completion_popup = None
        self.listbox = None
        self.scrolled = None
        self.current_textview = None
        self.log = log_callback if log_callback else lambda x: None
        # Set of tags to exclude from completions
        self._blacklist = set()
        # Pending GLib.idle_add source IDs for deferred show_popup calls
        self._pending_show_ids = []

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

    def load_tags(self, filepath=None):
        """
        Load tags from CSV file.

        Args:
            filepath: Path to the CSV file containing tags (defaults to danbooru.csv)
        """
        if filepath is None:
            filepath = config.resource_path('data/danbooru.csv')
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

            # Prepend sentinel entries so 'character' and 'tag' always
            # appear at the top of completions ahead of danbooru tags.
            _SENTINEL = 10_000_000_000
            for _name in reversed(('character', 'tag')):
                self.tag_data[_name] = (-1, _SENTINEL)
                if _name in self.sorted_tags:
                    self.sorted_tags.remove(_name)
                self.sorted_tags.insert(0, _name)

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

    def load_tag_presets(
        self,
        url='http://localhost:8188/tag_editor'
    ):
        """
        Load tag preset names from the tag_editor API endpoint.

        Args:
            url: API endpoint URL
        """
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if isinstance(data, dict):
                    self.tag_presets = sorted(list(data.keys()))
                    self.log(
                        f"Loaded {len(self.tag_presets)} tag presets "
                        f"from {url}"
                    )
                else:
                    self.log(f"Unexpected data format from {url}")
        except urllib.error.URLError as e:
            self.log(f"Could not load tag presets from {url}: {e}")
        except Exception as e:
            self.log(f"Error loading tag presets: {e}")

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
                    return self.loras[:_max_items()]
                # Match against the filename only (after last /),
                # using substring so partial names resolve correctly
                matches = [
                    lora for lora in self.loras
                    if search in lora.split('/')[-1].lower()
                ]
                return matches[:_max_items()]

            # Handle character completion (depth-aware)
            prefix, search = current.rsplit(':', 1)
            prefix = prefix.strip().lower()
            search = search.strip().lower()

            if prefix == 'character':
                # depth 1: completing character name
                if not search:
                    return self.characters[:_max_items()]
                matches = [
                    char for char in self.characters
                    if search in char.split('/')[-1].lower()
                ]
                return matches[:_max_items()]

            if prefix.startswith('character:'):
                # depth 2 (outfit) or depth 3 (top/bottom)
                parts = prefix.split(':')
                if len(parts) == 2:
                    # depth 2: completing outfit name
                    outfits = self._get_outfits(parts[1])
                    if not search:
                        return outfits[:_max_items()]
                    return [
                        o for o in outfits if search in o.lower()
                    ][:_max_items()]
                elif len(parts) == 3:
                    # depth 3: completing top or bottom
                    options = ['top', 'bottom']
                    if not search:
                        return options
                    return [
                        o for o in options if o.startswith(search)
                    ]

            # Handle tag preset completion: tag:name
            if prefix == 'tag':
                if not search:
                    return self.tag_presets[:_max_items()]
                matches = [
                    preset for preset in self.tag_presets
                    if search in preset.lower()
                ]
                return matches[:_max_items()]

        current = current.lower()

        # Normalize search term: spaces -> underscores,
        # escaped parens -> normal parens
        current = current.replace(' ', '_')
        current = current.replace('\\(', '(').replace('\\)', ')')

        matches = []
        seen = set()

        # Search in sorted tags (already sorted by usage descending)
        # Use substring matching so higher-usage tags rank above lower-usage
        # prefix-only matches (e.g. sakuragi_mano > mano_aloe for "mano")
        for tag in self.sorted_tags:
            tl = tag.lower()
            # Skip blacklisted tags
            if tl in self._blacklist:
                continue
            # Match if current appears at a word boundary (start of any
            # underscore-separated word, including the first)
            if ('_' + current) in ('_' + tl):
                if tag not in seen:
                    matches.append(tag)
                    seen.add(tag)
            if len(matches) >= _max_items():
                break

        # Search in aliases
        if len(matches) < _max_items():
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
                    if len(matches) >= _max_items():
                        break

        return matches[:_max_items()]

    def _get_outfits(self, character_name):
        """
        Return outfit names for a character.

        Args:
            character_name: Character name string

        Returns:
            List of outfit name strings
        """
        # Only 'default' exists for now; expand when outfit
        # support is added to the API
        return ['default']

    def _strip_character_colon(self, textview):
        """
        If the cursor is mid-character-flow at depth >= 2 and the
        current token ends with ':', strip that trailing colon and
        close the tag with ', '.

        Args:
            textview: GtkSourceView containing the buffer
        """
        buffer = textview.get_buffer()
        cursor_mark = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor_mark)

        iter_start = iter_cursor.copy()
        while not iter_start.starts_line():
            iter_start.backward_char()
            if iter_start.get_char() in ',\n':
                iter_start.forward_char()
                break

        # Skip leading whitespace after comma
        while iter_start.get_char() in ' \t':
            iter_start.forward_char()

        text = buffer.get_text(iter_start, iter_cursor, False)

        # Only act when inside character flow at depth >= 2
        # and the token ends with a bare colon
        if (text.lower().startswith('character:')
                and text.count(':') >= 2
                and text.endswith(':')):
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, text[:-1] + ', ')

    def _make_badge(self, text, css_class):
        """
        Create a rounded badge label styled via CSS.

        Args:
            text: Label text to display
            css_class: CSS class controlling badge background colour

        Returns:
            Gtk.Label styled as a badge
        """
        label = Gtk.Label(label=text)
        label.add_css_class('tag-badge')
        label.add_css_class(css_class)
        return label

    def _create_popup(self, textview):
        """
        Create the completion popup structure once.

        Args:
            textview: GtkSourceView to attach popup to
        """
        self.current_textview = textview

        popover = Gtk.Popover()
        popover.add_css_class('completion-popup')
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

        # No ScrolledWindow — completions are capped at 10 so scrolling
        # is never needed, and removing it lets the popover size to the
        # exact height of the list with no clamping or extra padding.
        popover.set_child(self.listbox)

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
                           spacing=10, css_classes=['item'])

            # Tag label
            tag_label = Gtk.Label(label=tag, xalign=0)
            tag_label.set_hexpand(True)
            hbox.append(tag_label)

            # Check if this is a character, LoRA, or regular tag
            if tag == 'character':
                hbox.append(self._make_badge('Character', 'badge-char'))
            elif tag == 'tag':
                hbox.append(self._make_badge('Tag', 'badge-tag'))
            elif tag in self.tag_data:
                # Get tag data for regular tags
                category, usage = self.tag_data.get(tag, (0, 0))
                _, cat_name = self.CATEGORY_COLORS.get(
                    category, ('#CCCCCC', 'Unknown')
                )
                css_class = self.CATEGORY_CSS.get(
                    category, 'badge-general'
                )

                # Category box
                cat_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=4
                )

                # Rounded category badge with full name
                cat_box.append(
                    self._make_badge(cat_name, css_class)
                )

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
                hbox.append(self._make_badge('LoRA', 'badge-lora'))
            elif tag in self.tag_presets:
                hbox.append(self._make_badge('Tag', 'badge-tag'))
            else:
                # Treat as character name
                hbox.append(
                    self._make_badge('Character', 'badge-char')
                )

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

        # Fix the width; height is determined naturally by the list.
        self.listbox.set_size_request(400, -1)

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
        is_tag_preset = replaced_text.lower().startswith('tag:')

        # Don't add a trailing comma if one already follows the cursor
        trailing = ', ' if iter_cursor.get_char() != ',' else ''

        if is_lora:
            # For LoRAs, insert full syntax with default weight
            formatted_tag = f"<lora:{tag}:1.0>"
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, formatted_tag + trailing)
        elif tag == 'character' and not is_character and not is_tag_preset:
            # Sentinel: insert prefix and immediately show character list
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, 'character:')
            self._schedule_show_popup(
                textview, self.characters[:_max_items()]
            )
        elif tag == 'tag' and not is_tag_preset and not is_character:
            # Sentinel: insert prefix and immediately show tag preset list
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, 'tag:')
            self._schedule_show_popup(
                textview, self.tag_presets[:_max_items()]
            )
        elif is_character:
            # Depth-aware: count colons to determine flow stage
            depth = replaced_text.count(':')
            parts = replaced_text.split(':')

            if depth == 1:
                # Completed name → append colon, show outfit list
                outfits = self._get_outfits(tag)
                buffer.delete(iter_start, iter_cursor)
                buffer.insert(iter_start, f'character:{tag}:')
                self._schedule_show_popup(textview, outfits)
            elif depth == 2:
                # Completed outfit → append colon, show top/bottom
                char_name = parts[1]
                buffer.delete(iter_start, iter_cursor)
                buffer.insert(
                    iter_start, f'character:{char_name}:{tag}:'
                )
                self._schedule_show_popup(
                    textview, ['top', 'bottom']
                )
            elif depth == 3:
                # Completed top/bottom → close out the tag
                char_name = parts[1]
                outfit = parts[2]
                buffer.delete(iter_start, iter_cursor)
                buffer.insert(
                    iter_start,
                    f'character:{char_name}:{outfit}:{tag}' + trailing
                )
        elif is_tag_preset:
            # For tag presets, preserve the tag: prefix
            formatted_tag = f"tag:{tag}"
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, formatted_tag + trailing)
        else:
            # For regular tags, replace underscores and escape parens
            formatted_tag = tag.replace('_', ' ')
            formatted_tag = formatted_tag.replace(
                '(', '\\('
            ).replace(')', '\\)')
            buffer.delete(iter_start, iter_cursor)
            buffer.insert(iter_start, formatted_tag + trailing)

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
            self._cancel_pending_show()
            self.completion_popup.popdown()
            # Strip trailing colon if we're mid-character-flow
            self._strip_character_colon(textview)
            return True
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self._cancel_pending_show()
            self.completion_popup.popdown()
            return False
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
                self._cancel_pending_show()
                self.completion_popup.popdown()
            return True

        return False

    def _schedule_show_popup(self, textview, suggestions):
        """
        Schedule show_popup via GLib.idle_add, tracking the source ID
        so it can be cancelled if the popup is dismissed first.

        Args:
            textview: GtkSourceView to attach popup to
            suggestions: List of tag suggestions to display
        """
        source_id = GLib.idle_add(self.show_popup, textview, suggestions)
        self._pending_show_ids.append(source_id)

    def _cancel_pending_show(self):
        """Cancel all queued deferred show_popup calls."""
        for source_id in self._pending_show_ids:
            GLib.source_remove(source_id)
        self._pending_show_ids.clear()

    def close_popup(self):
        """Close the completion popup if open."""
        self._cancel_pending_show()
        if self.completion_popup:
            self.completion_popup.popdown()
