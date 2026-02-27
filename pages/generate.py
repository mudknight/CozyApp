import json
import uuid
import threading
import random
import requests
import websocket
import re
import datetime
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Adw, GLib, Gdk, GtkSource, Pango  # noqa
import config  # noqa
from tag_completion import TagCompletion  # noqa
from widgets.node_settings import NodeSettingsDialog, default_node_settings  # noqa

CLIENT_ID = str(uuid.uuid4())

# Workflow node class names
PROMPT_NODE_CLASS = "PromptConditioningNode"
LOADER_NODE_CLASS = "LoaderFullPipe"
SAVE_NODE_CLASS = "SaveFullPipe"
BASE_NODE_CLASS = "BaseNode"
UPSCALE_NODE_CLASS = "UpscaleNode"
DETAILER_NODE_CLASS = "DetailerPipeNode"
NESTED_DETAILER_NODE_CLASS = "NestedDetailerPipeNode"
BRANCH_NODE_CLASS = "ImpactConditionalBranch"

DETAILER_OPTIONS = ["None", "Face", "Nested"]


def setup_comment_highlighting(buffer):
    """Apply the custom language definition for # comments."""
    lang_manager = GtkSource.LanguageManager.get_default()
    lang = lang_manager.get_language("prompt-tags")
    if lang:
        buffer.set_language(lang)



class GeneratePage:
    """
    The generate tab: inputs, queue controls, and workflow logic.

    Callbacks
    ---------
    on_image_update(data)         -- interim preview image bytes
    on_image_final(data, info)    -- final image bytes + metadata dict
    on_show_toast(message)        -- display a toast in the window
    """

    def __init__(
        self, log_fn, on_image_update, on_image_final, on_show_toast
    ):
        self.log = log_fn
        self._on_image_update = on_image_update
        self._on_image_final = on_image_final
        self._on_show_toast = on_show_toast

        self.style_list = []
        self.model_list = []
        self.resolution_list = []
        self.sampler_list = []
        self.scheduler_list = []
        self.bbox_model_list = []
        self.fallback_model_list = []
        self.upscale_model_list = []
        self.upscale_method_list = []
        self.workflow_data = None
        self.node_settings = default_node_settings()

        self.tag_completion = TagCompletion(self.log)

        # Job queue state
        self.job_list = []
        self.job_list_lock = threading.Lock()
        self.current_job_id = None
        self.is_processing = False
        self.debounce_timers = []

        # Stop control: event signals the queue loop to exit,
        # _active_ws holds the live socket so stop() can close it
        # immediately and unblock any blocking ws.recv() call.
        self._stop_requested = threading.Event()
        self._active_ws = None
        self._active_ws_lock = threading.Lock()

        self._build_ui()

        self.tag_completion.load_tags()
        self.tag_completion.load_characters()
        self.tag_completion.load_loras()
        self.tag_completion.load_tag_presets()
        # Apply any saved blacklist immediately
        self.tag_completion.set_blacklist(
            config.get("tag_blacklist") or []
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the sidebar widget tree."""
        self._sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True
        )

        self._input_area = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            vexpand=True
        )
        for side in ("top", "start", "end"):
            getattr(self._input_area, f"set_margin_{side}")(20)
        self._input_area.set_margin_bottom(10)
        self._sidebar.append(self._input_area)

        self._build_quick_settings()
        self._build_prompt_area()
        self._build_button_row()

    def _build_quick_settings(self):
        """Wrap the settings rows in a collapsible labeled card section."""
        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            css_classes=["quick-settings-card"]
        )

        # --- Header row ---
        card_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10
        )
        card_header.set_margin_start(12)
        card_header.set_margin_end(12)
        card_header.set_margin_top(6)
        card_header.set_margin_bottom(6)

        card_header.append(Gtk.Label(
            label="Quick Settings",
            xalign=0,
            css_classes=["heading"]
        ))

        # Node settings button sits immediately after the label
        self.node_settings_button = Gtk.Button(
            icon_name="emblem-system-symbolic",
            tooltip_text="Node settings",
            css_classes=["flat", "circular"]
        )
        self.node_settings_button.connect(
            "clicked",
            lambda _: self.show_node_settings_dialog(
                self._sidebar.get_root()
            )
        )
        card_header.append(self.node_settings_button)

        # Spacer pushes the collapse button to the far right
        card_header.append(Gtk.Box(hexpand=True))

        # Collapse toggle button on the far right
        self._qs_expanded = True
        self._qs_toggle_btn = Gtk.Button(
            icon_name="pan-up-symbolic",
            tooltip_text="Collapse",
            css_classes=["flat", "circular"]
        )
        self._qs_toggle_btn.connect("clicked", self._on_qs_toggle)
        card_header.append(self._qs_toggle_btn)

        card.append(card_header)

        # Separator shown only when expanded
        self._qs_separator = Gtk.Separator(
            orientation=Gtk.Orientation.HORIZONTAL
        )
        card.append(self._qs_separator)

        # Revealer wraps the inner rows
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10
        )
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, f"set_margin_{side}")(12)

        inner.append(self._build_style_model_row())
        inner.append(self._build_seed_row())
        inner.append(self._build_options_row())

        self._qs_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=200,
            reveal_child=True
        )
        self._qs_revealer.set_child(inner)
        card.append(self._qs_revealer)

        self._input_area.append(card)

    def _on_qs_toggle(self, _):
        """Toggle the quick settings revealer and update the icon."""
        self._qs_expanded = not self._qs_expanded
        self._qs_revealer.set_reveal_child(self._qs_expanded)
        self._qs_separator.set_visible(self._qs_expanded)
        self._qs_toggle_btn.set_icon_name(
            "pan-up-symbolic" if self._qs_expanded else "pan-down-symbolic"
        )
        self._qs_toggle_btn.set_tooltip_text(
            "Collapse" if self._qs_expanded else "Expand"
        )

    def _build_style_model_row(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        box.append(Gtk.Label(label="Style", xalign=0))

        self.style_dropdown = Gtk.DropDown.new_from_strings([])
        box.append(self.style_dropdown)

        box.append(
            Gtk.Label(label="Model", xalign=0, margin_start=10)
        )
        self.model_dropdown = Gtk.DropDown.new_from_strings([])
        self.model_dropdown.set_hexpand(True)
        self.model_dropdown.set_size_request(50, -1)
        box.append(self.model_dropdown)

        return box

    def _build_seed_row(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        box.append(Gtk.Label(label="Seed", xalign=0))
        self.seed_adj = Gtk.Adjustment(
            value=0, lower=0, upper=2**64 - 1, step_increment=1
        )
        self.seed_entry = Gtk.SpinButton(
            adjustment=self.seed_adj, numeric=True, hexpand=True
        )
        self.seed_mode_combo = Gtk.DropDown.new_from_strings(
            ["Randomize", "Fixed"]
        )
        box.append(self.seed_entry)
        box.append(self.seed_mode_combo)
        return box

    def _build_options_row(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )

        box.append(Gtk.Label(label="Resolution", xalign=0))
        self.resolution_dropdown = Gtk.DropDown.new_from_strings([])
        self.resolution_dropdown.set_hexpand(True)
        box.append(self.resolution_dropdown)

        box.append(
            Gtk.Label(label="Portrait", xalign=0, margin_start=10)
        )
        self.portrait_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        box.append(self.portrait_toggle)

        box.append(
            Gtk.Label(label="Quality", xalign=0, margin_start=10)
        )
        self.quality_tags_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        box.append(self.quality_tags_toggle)

        box.append(
            Gtk.Label(label="Embeddings", xalign=0, margin_start=10)
        )
        self.embeddings_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        box.append(self.embeddings_toggle)

        box.append(
            Gtk.Label(label="Detailer", xalign=0, margin_start=10)
        )
        self.detailer_dropdown = Gtk.DropDown.new_from_strings(
            DETAILER_OPTIONS
        )
        box.append(self.detailer_dropdown)

        return box

    def _build_prompt_area(self):
        self.pos_buffer = GtkSource.Buffer()
        setup_comment_highlighting(self.pos_buffer)
        self._input_area.append(
            Gtk.Label(label="Positive Prompt", xalign=0)
        )
        pos_scrolled, self.pos_textview = self._make_textview(
            self.pos_buffer
        )
        self._input_area.append(pos_scrolled)

        self.neg_buffer = GtkSource.Buffer()
        setup_comment_highlighting(self.neg_buffer)
        self._input_area.append(
            Gtk.Label(label="Negative Prompt", xalign=0)
        )
        neg_scrolled, self.neg_textview = self._make_textview(
            self.neg_buffer
        )
        self._input_area.append(neg_scrolled)

    def _build_button_row(self):
        btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )

        self.gen_button = Gtk.Button(
            label="Queue", css_classes=["suggested-action"]
        )
        self.gen_button.connect("clicked", self.on_generate_clicked)

        self.stop_button = Gtk.Button(
            label="Stop", css_classes=["destructive-action"]
        )
        self.stop_button.connect("clicked", self.on_stop_clicked)
        self.stop_button.set_sensitive(False)

        self.batch_adj = Gtk.Adjustment(
            value=1, lower=1, upper=99, step_increment=1
        )
        self.batch_entry = Gtk.SpinButton(
            adjustment=self.batch_adj, numeric=True
        )
        self.batch_entry.set_tooltip_text("Number of images to queue")
        self.batch_entry.set_width_chars(3)

        self.progress_bar = Gtk.ProgressBar(hexpand=True)
        self.progress_bar.set_valign(Gtk.Align.CENTER)

        self.current_node_label = Gtk.Label(label="Ready", xalign=0.5)
        self.current_node_label.set_width_chars(20)
        self.current_node_label.set_max_width_chars(20)
        self.current_node_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.current_node_label.set_valign(Gtk.Align.CENTER)

        # Queue status button with popover
        queue_btn_content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=5
        )
        self.queue_label = Gtk.Label(label="0", xalign=0.5)
        self.queue_label.set_width_chars(2)
        self.queue_label.set_max_width_chars(2)
        queue_btn_content.append(self.current_node_label)
        queue_btn_content.append(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        )
        queue_btn_content.append(self.queue_label)

        self._job_listbox = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["job-list"]
        )
        self._job_listbox.set_placeholder(Gtk.Label(
            label="No jobs queued",
            css_classes=["dim-label"],
            margin_top=12,
            margin_bottom=12
        ))
        popover_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            min_content_height=0,
            max_content_height=300
        )
        popover_scroll.set_child(self._job_listbox)

        popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        popover_box.set_margin_top(8)
        popover_box.set_margin_bottom(8)
        popover_box.set_margin_start(8)
        popover_box.set_margin_end(8)
        popover_box.set_size_request(280, -1)
        popover_box.append(
            Gtk.Label(label="Queued Jobs", css_classes=["heading"])
        )
        popover_box.append(popover_scroll)

        self._queue_popover = Gtk.Popover(
            css_classes=["queue-popover"]
        )
        self._queue_popover.set_child(popover_box)
        self._queue_popover.set_autohide(True)

        self.queue_box = Gtk.MenuButton(
            popover=self._queue_popover,
            css_classes=["queue-badge"]
        )
        self.queue_box.set_child(queue_btn_content)
        self.queue_box.set_direction(Gtk.ArrowType.NONE)

        self.queue_group = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            css_classes=["queue-group"]
        )
        self.queue_group.append(self.gen_button)
        self.queue_group.append(self.batch_entry)

        btn_box.append(self.queue_box)
        btn_box.append(self.progress_bar)
        btn_box.append(self.stop_button)
        btn_box.append(self.queue_group)
        self._input_area.append(btn_box)

    def _make_textview(self, buffer):
        """Create a GtkSource.View with key/change handlers attached."""
        textview = GtkSource.View()
        textview.set_buffer(buffer)
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        textview.set_vexpand(True)
        textview.set_show_line_numbers(True)
        textview.set_highlight_current_line(False)

        style_manager = GtkSource.StyleSchemeManager.get_default()
        adw_style_manager = Adw.StyleManager.get_default()
        scheme_name = (
            "Adwaita-dark" if adw_style_manager.get_dark() else "Adwaita"
        )
        scheme = style_manager.get_scheme(scheme_name)
        if scheme:
            buffer.set_style_scheme(scheme)

        textview.completion_active = False
        textview.completion_debounce_id = None

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect(
            "key-pressed",
            lambda ctrl, kv, kc, st: self._on_textview_key_press(
                textview, kv, kc, st
            )
        )
        textview.add_controller(key_ctrl)

        buffer.connect(
            "changed",
            lambda buf: self._on_textview_changed(textview)
        )

        scrolled = Gtk.ScrolledWindow(
            child=textview,
            propagate_natural_height=False,
            vexpand=True,
            css_classes=["view", "prompt-scroll"]
        )
        scrolled.set_overflow(Gtk.Overflow.HIDDEN)

        # Toggle .prompt-focused on the scrolled window when the
        # textview gains or loses keyboard focus.
        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect(
            "enter", lambda _: scrolled.add_css_class("prompt-focused")
        )
        focus_ctrl.connect(
            "leave", lambda _: scrolled.remove_css_class("prompt-focused")
        )
        textview.add_controller(focus_ctrl)

        return scrolled, textview

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def widget(self):
        """The top-level widget to embed in the view stack."""
        return self._sidebar

    def fetch_node_info(self):
        """Fetch dropdown options from the ComfyUI server in a thread."""
        threading.Thread(
            target=self._fetch_node_info_worker, daemon=True
        ).start()

    def load_workflow(self, filepath):
        """Load a workflow JSON file as the generation template."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                self.workflow_data = json.load(f)
            self.sync_ui_from_json()
            self.log(f"Loaded workflow: {filepath}")
        except Exception as e:
            self.log(f"Error loading workflow: {e}")

    def insert_character(self, name):
        """Append a character tag to the positive prompt."""
        tag = f"character:{name}:default:top, "
        start, end = self.pos_buffer.get_bounds()
        current = self.pos_buffer.get_text(start, end, False)
        if tag.strip() not in current:
            self.pos_buffer.insert(end, tag)

    def insert_tag(self, name):
        """Append a tag reference to the positive prompt."""
        tag = f"tag:{name}, "
        _, end = self.pos_buffer.get_bounds()
        self.pos_buffer.insert(end, tag)

    def set_style(self, style_name):
        """Select a style in the style dropdown by name."""
        if style_name in self.style_list:
            self.style_dropdown.set_selected(
                self.style_list.index(style_name)
            )
            return True
        return False

    def set_model(self, model_name):
        """Select a model in the model dropdown by name or stem."""
        # 1. Try exact match
        if model_name in self.model_list:
            self.model_dropdown.set_selected(
                self.model_list.index(model_name)
            )
            return True

        # 2. Try matching by stem (path/to/name without extension)
        for i, full_path in enumerate(self.model_list):
            # Strip extension from the dropdown item
            stem = (
                full_path.rsplit('.', 1)[0]
                if '.' in full_path
                else full_path
            )
            if stem == model_name:
                self.model_dropdown.set_selected(i)
                return True

        # 3. Try matching just the filename without path or extension
        # (Handling cases where the folder is missing from model_name)
        search_file = model_name.split('/')[-1]
        for i, full_path in enumerate(self.model_list):
            filename = full_path.split('/')[-1]
            stem = (
                filename.rsplit('.', 1)[0]
                if '.' in filename
                else filename
            )
            if stem == search_file:
                self.model_dropdown.set_selected(i)
                return True

        return False

    def queue_generate(self):
        """Trigger a generation (keyboard shortcut entry point)."""
        self.on_generate_clicked(None)

    def stop(self):
        """Send a stop/interrupt signal (keyboard shortcut entry point)."""
        self.on_stop_clicked(None)

    def adjust_batch(self, delta):
        """Increment or decrement the batch count by delta."""
        self.batch_adj.set_value(self.batch_adj.get_value() + delta)

    # ------------------------------------------------------------------
    # Node info fetching
    # ------------------------------------------------------------------

    def _fetch_node_info_worker(self):
        """Worker thread: fetch styles, models, and resolutions."""
        self._fetch_input_list(
            PROMPT_NODE_CLASS, "style",
            self.update_style_dropdown, "Metadata"
        )
        self._fetch_input_list(
            LOADER_NODE_CLASS, "ckpt_name",
            self.update_model_dropdown, "Model metadata"
        )
        self._fetch_input_list(
            BASE_NODE_CLASS, "resolution",
            self.update_resolution_dropdown, "Resolution metadata"
        )
        # Fetch enum lists used by the node settings dialog.
        # Use BaseNode rather than KSampler so custom schedulers/samplers
        # registered by custom nodes are included.
        samplers = self._fetch_enum(BASE_NODE_CLASS, 'sampler_name')
        schedulers = self._fetch_enum(BASE_NODE_CLASS, 'scheduler')
        bbox = self._fetch_enum('DetailerPipeNode', 'bbox_model')
        fallback = self._fetch_enum(
            'DetailerPipeNode', 'fallback_model'
        )
        up_models = self._fetch_enum(
            'DetailerPipeNode', 'upscale_model'
        )
        up_methods = self._fetch_enum(
            'DetailerPipeNode', 'upscale_method'
        )
        GLib.idle_add(
            self._store_enum_lists,
            samplers, schedulers, bbox, fallback, up_models, up_methods
        )

    def _fetch_enum(self, node_class, key):
        """Fetch a single enum list from object_info; return [] on fail."""
        try:
            url = (
                f"http://{config.server_address()}"
                f"/object_info/{node_class}"
            )
            resp = requests.get(url, timeout=3)
            if resp.status_code != 200:
                return []
            data = resp.json().get(node_class, {})
            inputs = data.get("input", {})
            for cat in ("required", "optional"):
                entry = inputs.get(cat, {}).get(key)
                if entry is None:
                    continue
                # Standard enum: [["a", "b", ...], {...}]
                if (isinstance(entry, list)
                        and isinstance(entry[0], list)):
                    return entry[0]
                # COMBO with options dict: [["COMBO"], {"options": [...]}]
                if (isinstance(entry, list) and len(entry) > 1
                        and isinstance(entry[1], dict)
                        and "options" in entry[1]):
                    return entry[1]["options"]
            resp.close()
        except Exception as e:
            self.log(f"Enum fetch {node_class}/{key}: {e}")
        return []

    def _store_enum_lists(
        self, samplers, schedulers, bbox, fallback, up_models, up_methods
    ):
        """Store fetched enum lists (called on the main thread)."""
        self.sampler_list = samplers
        self.scheduler_list = schedulers
        self.bbox_model_list = bbox
        self.fallback_model_list = fallback
        self.upscale_model_list = up_models
        self.upscale_method_list = up_methods

    def _fetch_input_list(self, node_class, key, callback, label):
        """Fetch a single enum input list from object_info."""
        try:
            url = (
                f"http://{config.server_address()}"
                f"/object_info/{node_class}"
            )
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                data = resp.json().get(node_class, {})
                inputs = data.get("input", {})
                result = None
                for cat in ("required", "optional"):
                    if key in inputs.get(cat, {}):
                        entry = inputs[cat][key]
                        result = (
                            entry[0]
                            if isinstance(entry, list)
                            and isinstance(entry[0], list)
                            else entry
                        )
                        break
                if result:
                    GLib.idle_add(callback, result)
            resp.close()
        except Exception as e:
            self.log(f"{label} fail: {e}")

    def update_style_dropdown(self, styles):
        self.style_list = styles
        self.style_dropdown.set_model(Gtk.StringList.new(styles))
        if self.workflow_data:
            self.sync_ui_from_json()
        self.load_saved_state()

    def update_model_dropdown(self, models):
        self.model_list = models
        self.model_dropdown.set_model(Gtk.StringList.new(models))
        if self.workflow_data:
            self.sync_ui_from_json()
        self.load_saved_state()

    def update_resolution_dropdown(self, resolutions):
        self.resolution_list = resolutions
        self.resolution_dropdown.set_model(
            Gtk.StringList.new(resolutions)
        )
        if self.workflow_data:
            self.sync_ui_from_json()
        self.load_saved_state()

    # ------------------------------------------------------------------
    # Node settings dialog
    # ------------------------------------------------------------------

    def show_node_settings_dialog(self, parent):
        """Open the tabbed node settings dialog."""
        NodeSettingsDialog(
            node_settings=self.node_settings,
            on_close=self.save_current_state,
            sampler_list=self.sampler_list,
            scheduler_list=self.scheduler_list,
            bbox_model_list=self.bbox_model_list,
            fallback_model_list=self.fallback_model_list,
            upscale_model_list=self.upscale_model_list,
            upscale_method_list=self.upscale_method_list,
        ).show(parent)

    # ------------------------------------------------------------------
    # Workflow sync / state persistence
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Workflow sync / state persistence
    # ------------------------------------------------------------------

    def sync_ui_from_json(self):
        """Populate UI widgets from the loaded workflow template."""
        if not self.workflow_data:
            return

        # Pre-find IDs needed for detailer inference
        upscale_id = next(
            (nid for nid, n in self.workflow_data.items()
             if n.get("class_type") == UPSCALE_NODE_CLASS), None
        )
        detailer_id = next(
            (nid for nid, n in self.workflow_data.items()
             if n.get("class_type") == DETAILER_NODE_CLASS), None
        )

        for node in self.workflow_data.values():
            ct = node.get("class_type")
            inp = node["inputs"]

            if ct == PROMPT_NODE_CLASS:
                self.pos_buffer.set_text(str(inp.get("positive", "")))
                self.neg_buffer.set_text(str(inp.get("negative", "")))
                style_val = inp.get("style")
                if style_val in self.style_list:
                    self.style_dropdown.set_selected(
                        self.style_list.index(style_val)
                    )
                self.quality_tags_toggle.set_active(
                    bool(inp.get("quality_tags", False))
                )
                self.embeddings_toggle.set_active(
                    bool(inp.get("embeddings", False))
                )

            elif ct == LOADER_NODE_CLASS:
                self.seed_adj.set_value(float(inp.get("seed", 0)))
                model_val = inp.get("ckpt_name")
                if model_val in self.model_list:
                    self.model_dropdown.set_selected(
                        self.model_list.index(model_val)
                    )

            elif ct == BASE_NODE_CLASS:
                res_val = inp.get("resolution")
                if res_val in self.resolution_list:
                    self.resolution_dropdown.set_selected(
                        self.resolution_list.index(res_val)
                    )
                self.portrait_toggle.set_active(
                    bool(inp.get("portrait", False))
                )
                ns = self.node_settings["base"]
                ns["sampler_name"] = inp.get(
                    "sampler_name", ns["sampler_name"]
                )
                ns["scheduler"] = inp.get("scheduler", ns["scheduler"])
                ns["steps"] = inp.get("steps", ns["steps"])
                ns["cfg"] = inp.get("cfg", ns["cfg"])
                ns["denoise"] = inp.get("denoise", ns["denoise"])

            elif ct == UPSCALE_NODE_CLASS:
                ns = self.node_settings["upscale"]
                ns["sampler_name"] = inp.get(
                    "sampler_name", ns["sampler_name"]
                )
                ns["scheduler"] = inp.get("scheduler", ns["scheduler"])
                ns["steps"] = inp.get("steps", ns["steps"])
                ns["cfg"] = inp.get("cfg", ns["cfg"])
                ns["denoise"] = inp.get("denoise", ns["denoise"])
                ns["upscale_model"] = inp.get(
                    "upscale_model", ns["upscale_model"]
                )
                ns["scale_by"] = inp.get("scale_by", ns["scale_by"])

            elif ct == DETAILER_NODE_CLASS:
                ns = self.node_settings["detailer"]
                for key in (
                    "bbox_model", "fallback_model", "threshold",
                    "steps", "cfg", "sampler", "scheduler", "denoise",
                    "upscale_method", "upscale_model",
                    "feather", "context_padding"
                ):
                    if key in inp:
                        ns[key] = inp[key]

            elif ct == NESTED_DETAILER_NODE_CLASS:
                ns = self.node_settings["nested"]
                for key in (
                    "face_model", "eyes_pair_model", "eye_single_model",
                    "threshold", "cfg", "sampler", "scheduler",
                    "face_steps", "face_denoise", "face_scale",
                    "eye_steps", "eye_denoise", "eye_scale",
                    "upscale_method", "max_megapixels",
                    "feather", "context_padding"
                ):
                    if key in inp:
                        ns[key] = inp[key]

            elif ct == BRANCH_NODE_CLASS:
                # Infer detailer mode from cond and ff_value target
                cond = inp.get("cond", False)
                ff_src = inp.get("ff_value", [None])[0]
                if cond:
                    self.detailer_dropdown.set_selected(2)  # Nested
                elif str(ff_src) == str(detailer_id):
                    self.detailer_dropdown.set_selected(1)  # Face
                else:
                    self.detailer_dropdown.set_selected(0)  # None

    def save_current_state(self):
        """Persist current input values to state.json."""
        pos = self.pos_buffer.get_text(
            self.pos_buffer.get_start_iter(),
            self.pos_buffer.get_end_iter(), False
        )
        neg = self.neg_buffer.get_text(
            self.neg_buffer.get_start_iter(),
            self.neg_buffer.get_end_iter(), False
        )

        def _dropdown_val(dropdown, lst):
            idx = dropdown.get_selected()
            return (
                lst[idx]
                if lst and idx != Gtk.INVALID_LIST_POSITION
                else None
            )

        state = {
            "style": _dropdown_val(self.style_dropdown, self.style_list),
            "model": _dropdown_val(self.model_dropdown, self.model_list),
            "resolution": _dropdown_val(
                self.resolution_dropdown, self.resolution_list
            ),
            "portrait": self.portrait_toggle.get_active(),
            "quality_tags": self.quality_tags_toggle.get_active(),
            "embeddings": self.embeddings_toggle.get_active(),
            "detailer": DETAILER_OPTIONS[
                self.detailer_dropdown.get_selected()
            ],
            "node_settings": self.node_settings,
            "positive": pos,
            "negative": neg,
            "qs_expanded": self._qs_expanded,
        }
        config.save_state(state)

    def load_saved_state(self):
        """Restore input values from state.json if it exists."""
        state = config.load_state()
        if not state:
            return

        if "positive" in state:
            self.pos_buffer.set_text(state["positive"])
        if "negative" in state:
            self.neg_buffer.set_text(state["negative"])
        if "style" in state and state["style"] in self.style_list:
            self.style_dropdown.set_selected(
                self.style_list.index(state["style"])
            )
        if "model" in state and state["model"] in self.model_list:
            self.model_dropdown.set_selected(
                self.model_list.index(state["model"])
            )
        if (
            "resolution" in state
            and state["resolution"] in self.resolution_list
        ):
            self.resolution_dropdown.set_selected(
                self.resolution_list.index(state["resolution"])
            )
        if "portrait" in state:
            self.portrait_toggle.set_active(bool(state["portrait"]))
        if "quality_tags" in state:
            self.quality_tags_toggle.set_active(
                bool(state["quality_tags"])
            )
        if "embeddings" in state:
            self.embeddings_toggle.set_active(
                bool(state["embeddings"])
            )
        if "detailer" in state and state["detailer"] in DETAILER_OPTIONS:
            self.detailer_dropdown.set_selected(
                DETAILER_OPTIONS.index(state["detailer"])
            )
        if "node_settings" in state:
            saved_ns = state["node_settings"]
            for section in self.node_settings:
                if section in saved_ns:
                    self.node_settings[section].update(
                        saved_ns[section]
                    )
        # Restore quick settings revealer state
        if "qs_expanded" in state:
            expanded = bool(state["qs_expanded"])
            self._qs_expanded = expanded
            self._qs_revealer.set_reveal_child(expanded)
            self._qs_separator.set_visible(expanded)
            self._qs_toggle_btn.set_icon_name(
                "pan-up-symbolic" if expanded else "pan-down-symbolic"
            )
            self._qs_toggle_btn.set_tooltip_text(
                "Collapse" if expanded else "Expand"
            )
        self.log("Loaded saved state")

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def set_current_node(self, text):
        """Update the current-node status label."""
        self.current_node_label.set_text(text if text else "Ready")

    def update_queue_label(self):
        """Schedule a UI update for the queue count badge."""
        with self.job_list_lock:
            count = len(self.job_list)
        GLib.idle_add(self._update_queue_label_ui, str(count), count)

    def _update_queue_label_ui(self, text, count):
        self.queue_label.set_text(text)
        if count > 0:
            if not self.queue_box.has_css_class("queue-active"):
                self.queue_box.add_css_class("queue-active")
        else:
            if self.queue_box.has_css_class("queue-active"):
                self.queue_box.remove_css_class("queue-active")

    def _add_job_row(self, job):
        """Create and prepend a row for the job in the queue popover."""
        row_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=6, margin_bottom=6,
            margin_start=6, margin_end=6
        )
        time_label = Gtk.Label(
            label=job["added_at"].strftime("%H:%M:%S"),
            hexpand=True, xalign=0
        )
        row_box.append(time_label)

        cancel_btn = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER
        )
        cancel_btn.connect("clicked", lambda b: self._cancel_job(job))
        row_box.append(cancel_btn)

        row = Gtk.ListBoxRow()
        row.set_child(row_box)
        job["row"] = row
        self._job_listbox.prepend(row)
        row.show()

    def _remove_job_row_widget(self, job):
        if job.get("row"):
            self._job_listbox.remove(job["row"])
        return False

    def _mark_job_processing(self, job):
        if job.get("row"):
            job["row"].add_css_class("job-processing")
        return False

    def _cancel_job(self, job):
        """Cancel a pending job or interrupt the active one."""
        with self.job_list_lock:
            status = job["status"]
            if status == "pending":
                job["status"] = "cancelled"

        if status == "processing":
            self.on_stop_clicked(None)
        else:
            if job.get("row"):
                self._job_listbox.remove(job["row"])
            with self.job_list_lock:
                try:
                    self.job_list.remove(job)
                except ValueError:
                    pass
            self.update_queue_label()

    # ------------------------------------------------------------------
    # Generate / stop handlers
    # ------------------------------------------------------------------

    def on_stop_clicked(self, _):
        """Stop the current job only; pending jobs continue afterwards."""
        # Flag lets _generate_logic suppress the expected socket error
        self._stop_requested.set()

        # Close the active websocket so ws.recv() unblocks immediately
        with self._active_ws_lock:
            if self._active_ws is not None:
                try:
                    self._active_ws.close()
                except Exception:
                    pass

        try:
            requests.post(
                f"http://{config.server_address()}/interrupt",
                timeout=5
            )
            self.log("Interrupt signal sent.")
        except Exception as e:
            self.log(f"Stop error: {e}")

    def on_generate_clicked(self, _):
        """Queue one or more generation jobs."""
        if not self.workflow_data:
            return

        self.save_current_state()

        pos = self.pos_buffer.get_text(
            self.pos_buffer.get_start_iter(),
            self.pos_buffer.get_end_iter(), False
        )
        neg = self.neg_buffer.get_text(
            self.neg_buffer.get_start_iter(),
            self.neg_buffer.get_end_iter(), False
        )

        def _dd(dropdown, lst):
            idx = dropdown.get_selected()
            return (
                lst[idx]
                if lst and idx != Gtk.INVALID_LIST_POSITION
                else None
            )

        style = _dd(self.style_dropdown, self.style_list)
        model = _dd(self.model_dropdown, self.model_list)
        resolution = _dd(self.resolution_dropdown, self.resolution_list)
        portrait = self.portrait_toggle.get_active()
        quality_tags = self.quality_tags_toggle.get_active()
        embeddings = self.embeddings_toggle.get_active()
        detailer = DETAILER_OPTIONS[self.detailer_dropdown.get_selected()]

        batch_count = int(self.batch_adj.get_value())
        randomize = self.seed_mode_combo.get_selected() == 0
        base_seed = int(self.seed_adj.get_value())

        for i in range(batch_count):
            seed = (
                random.randint(0, 2**32)
                if randomize or i > 0
                else base_seed
            )
            if i == 0:
                GLib.idle_add(self.seed_adj.set_value, float(seed))

            wf = json.loads(json.dumps(self.workflow_data))

            for node in wf.values():
                ct = node.get("class_type")
                if ct == PROMPT_NODE_CLASS:
                    node["inputs"].update(
                        {"positive": pos, "negative": neg}
                    )
                    if style:
                        node["inputs"]["style"] = style
                    node["inputs"]["quality_tags"] = quality_tags
                    node["inputs"]["embeddings"] = embeddings
                elif ct == LOADER_NODE_CLASS:
                    node["inputs"]["seed"] = seed
                    if model:
                        node["inputs"]["ckpt_name"] = model
                elif ct == BASE_NODE_CLASS:
                    if resolution:
                        node["inputs"]["resolution"] = resolution
                    node["inputs"]["portrait"] = portrait
                    bns = self.node_settings["base"]
                    node["inputs"]["sampler_name"] = bns["sampler_name"]
                    node["inputs"]["scheduler"] = bns["scheduler"]
                    node["inputs"]["steps"] = int(bns["steps"])
                    node["inputs"]["cfg"] = bns["cfg"]
                    node["inputs"]["denoise"] = bns["denoise"]
                elif ct == UPSCALE_NODE_CLASS:
                    uns = self.node_settings["upscale"]
                    node["inputs"]["sampler_name"] = uns["sampler_name"]
                    node["inputs"]["scheduler"] = uns["scheduler"]
                    node["inputs"]["steps"] = int(uns["steps"])
                    node["inputs"]["cfg"] = uns["cfg"]
                    node["inputs"]["denoise"] = uns["denoise"]
                    node["inputs"]["upscale_model"] = uns["upscale_model"]
                    node["inputs"]["scale_by"] = uns["scale_by"]
                elif ct == DETAILER_NODE_CLASS:
                    dns = self.node_settings["detailer"]
                    node["inputs"]["bbox_model"] = dns["bbox_model"]
                    node["inputs"]["fallback_model"] = (
                        dns["fallback_model"]
                    )
                    node["inputs"]["threshold"] = dns["threshold"]
                    node["inputs"]["steps"] = int(dns["steps"])
                    node["inputs"]["cfg"] = dns["cfg"]
                    node["inputs"]["sampler"] = dns["sampler"]
                    node["inputs"]["scheduler"] = dns["scheduler"]
                    node["inputs"]["denoise"] = dns["denoise"]
                    node["inputs"]["upscale_method"] = (
                        dns["upscale_method"]
                    )
                    node["inputs"]["upscale_model"] = dns["upscale_model"]
                    node["inputs"]["feather"] = dns["feather"]
                    node["inputs"]["context_padding"] = (
                        dns["context_padding"]
                    )
                elif ct == NESTED_DETAILER_NODE_CLASS:
                    nns = self.node_settings["nested"]
                    node["inputs"]["face_model"] = nns["face_model"]
                    node["inputs"]["eyes_pair_model"] = (
                        nns["eyes_pair_model"]
                    )
                    node["inputs"]["eye_single_model"] = (
                        nns["eye_single_model"]
                    )
                    node["inputs"]["threshold"] = nns["threshold"]
                    node["inputs"]["cfg"] = nns["cfg"]
                    node["inputs"]["sampler"] = nns["sampler"]
                    node["inputs"]["scheduler"] = nns["scheduler"]
                    node["inputs"]["face_steps"] = int(nns["face_steps"])
                    node["inputs"]["face_denoise"] = nns["face_denoise"]
                    node["inputs"]["face_scale"] = nns["face_scale"]
                    node["inputs"]["eye_steps"] = int(nns["eye_steps"])
                    node["inputs"]["eye_denoise"] = nns["eye_denoise"]
                    node["inputs"]["eye_scale"] = nns["eye_scale"]
                    node["inputs"]["upscale_method"] = (
                        nns["upscale_method"]
                    )
                    node["inputs"]["max_megapixels"] = (
                        nns["max_megapixels"]
                    )
                    node["inputs"]["feather"] = nns["feather"]
                    node["inputs"]["context_padding"] = (
                        nns["context_padding"]
                    )

            # Apply detailer mode by patching the branch node
            upscale_nid = next(
                (nid for nid, n in wf.items()
                 if n.get("class_type") == UPSCALE_NODE_CLASS), None
            )
            detailer_nid = next(
                (nid for nid, n in wf.items()
                 if n.get("class_type") == DETAILER_NODE_CLASS), None
            )
            branch = next(
                (n for n in wf.values()
                 if n.get("class_type") == BRANCH_NODE_CLASS), None
            )
            if branch and detailer_nid and upscale_nid:
                if detailer == "None":
                    branch["inputs"]["cond"] = False
                    branch["inputs"]["ff_value"] = [upscale_nid, 0]
                elif detailer == "Face":
                    branch["inputs"]["cond"] = False
                    branch["inputs"]["ff_value"] = [detailer_nid, 0]
                else:  # Nested
                    branch["inputs"]["cond"] = True
                    branch["inputs"]["ff_value"] = [detailer_nid, 0]

            job = {
                "id": str(uuid.uuid4()),
                "workflow": wf,
                "added_at": datetime.datetime.now(),
                "status": "pending",
                "row": None,
            }
            with self.job_list_lock:
                self.job_list.append(job)
            self._add_job_row(job)

        self.update_queue_label()
        self.log(
            f"Queued {batch_count} item(s) "
            f"(queue size: {len(self.job_list)})"
        )

        if not self.is_processing:
            threading.Thread(
                target=self._process_queue, daemon=True
            ).start()

    # ------------------------------------------------------------------
    # Queue processing
    # ------------------------------------------------------------------

    def _process_queue(self):
        self.is_processing = True
        GLib.idle_add(self.stop_button.set_sensitive, True)

        while True:
            with self.job_list_lock:
                job = None
                for j in self.job_list:
                    if j["status"] == "pending":
                        j["status"] = "processing"
                        self.current_job_id = j["id"]
                        job = j
                        break
            if job is None:
                break

            GLib.idle_add(self._mark_job_processing, job)
            self.update_queue_label()
            self.log("Processing queued item...")
            try:
                self._generate_logic(job["workflow"])
            except Exception as e:
                self.log(f"Queue processing error: {e}")

            with self.job_list_lock:
                try:
                    self.job_list.remove(job)
                except ValueError:
                    pass
                self.current_job_id = None
            GLib.idle_add(self._remove_job_row_widget, job)
            # Clear stop flag so the next pending job runs normally
            self._stop_requested.clear()

        self.is_processing = False
        GLib.idle_add(self.stop_button.set_sensitive, False)
        self.update_queue_label()
        self.log("Queue processing complete")

    def _topo_sort(self, workflow_data):
        """Return node IDs in topological execution order."""
        deps = {nid: set() for nid in workflow_data}
        for nid, node in workflow_data.items():
            for val in node.get("inputs", {}).values():
                if isinstance(val, list) and len(val) == 2:
                    parent = str(val[0])
                    if parent in workflow_data:
                        deps[nid].add(parent)

        order = []
        visited = set()

        def visit(nid):
            if nid in visited:
                return
            visited.add(nid)
            for parent in deps.get(nid, []):
                visit(parent)
            order.append(nid)

        for nid in workflow_data:
            visit(nid)
        return order

    def _generate_logic(self, workflow_data):
        """Execute a single generation request over WebSocket."""
        start_time = datetime.datetime.now()
        ws = websocket.WebSocket()
        with self._active_ws_lock:
            self._active_ws = ws
        try:
            exec_order = self._topo_sort(workflow_data)
            node_index = {nid: i for i, nid in enumerate(exec_order)}
            total_nodes = len(exec_order)
            current_index = 0

            ws.connect(
                f"ws://{config.server_address()}"
                f"/ws?clientId={CLIENT_ID}"
            )
            payload = {"prompt": workflow_data, "client_id": CLIENT_ID}
            resp = requests.post(
                f"http://{config.server_address()}/prompt",
                json=payload,
                timeout=10
            )
            prompt_id = resp.json().get("prompt_id")
            resp.close()

            while True:
                out = ws.recv()
                if isinstance(out, bytes):
                    GLib.idle_add(self._on_image_update, out[8:])
                    continue

                msg = json.loads(out)

                if msg["type"] == "executing":
                    node_id = msg["data"]["node"]
                    msg_pid = msg["data"].get("prompt_id")
                    # Only break on completion of *our* prompt; stale
                    # signals from a previously cancelled job that the
                    # server finally finished would otherwise terminate
                    # the loop prematurely and skip the history fetch.
                    if node_id is None and msg_pid == prompt_id:
                        break
                    # Skip progress/label updates for other prompts
                    if msg_pid not in (prompt_id, None):
                        continue
                    current_index = node_index.get(
                        node_id, current_index
                    )
                    node_class = workflow_data.get(
                        node_id, {}
                    ).get("class_type", "Unknown")
                    GLib.idle_add(self.set_current_node, node_class)
                    GLib.idle_add(
                        self.progress_bar.set_fraction,
                        current_index / total_nodes
                    )

                elif msg["type"] == "progress":
                    node_progress = (
                        msg["data"]["value"] / msg["data"]["max"]
                    )
                    overall = (
                        current_index / total_nodes
                        + node_progress / total_nodes
                    )
                    GLib.idle_add(
                        self.progress_bar.set_fraction, overall
                    )

                elif msg["type"] == "executed":
                    GLib.idle_add(
                        self.progress_bar.set_fraction,
                        (current_index + 1) / total_nodes
                    )
                    if "images" in msg["data"]["output"]:
                        img = msg["data"]["output"]["images"][0]
                        img_resp = requests.get(
                            f"http://{config.server_address()}/view",
                            params=img
                        )
                        GLib.idle_add(
                            self._on_image_update, img_resp.content
                        )
                        img_resp.close()

            hist_resp = requests.get(
                f"http://{config.server_address()}/history/{prompt_id}",
                timeout=10
            )
            history = hist_resp.json().get(prompt_id, {})
            hist_resp.close()

            for node_id, node_output in history.get("outputs", {}).items():
                if workflow_data.get(node_id, {}).get(
                    "class_type"
                ) == SAVE_NODE_CLASS:
                    img = node_output["images"][0]
                    data_resp = requests.get(
                        f"http://{config.server_address()}/view",
                        params=img
                    )
                    data = data_resp.content
                    data_resp.close()
                    # Calculate generation time in seconds
                    end_time = datetime.datetime.now()
                    gen_time = (end_time - start_time).total_seconds()
                    img["generation_time"] = gen_time
                    GLib.idle_add(self._on_image_final, data, img)
                    break

        except Exception as e:
            # A closed socket raises an error when stop is requested;
            # only log genuine errors.
            if not self._stop_requested.is_set():
                self.log(f"Gen error: {e}")
        finally:
            with self._active_ws_lock:
                self._active_ws = None
            try:
                ws.close()
            except Exception:
                pass
            GLib.idle_add(self.progress_bar.set_fraction, 0.0)
            GLib.idle_add(self.set_current_node, None)

    # ------------------------------------------------------------------
    # Text view helpers
    # ------------------------------------------------------------------

    def _on_textview_changed(self, textview):
        """Debounce handler for tag auto-completion."""
        if not hasattr(textview, "completion_debounce_id"):
            textview.completion_debounce_id = None

        if textview.completion_debounce_id:
            GLib.source_remove(textview.completion_debounce_id)
            try:
                self.debounce_timers.remove(
                    textview.completion_debounce_id
                )
            except ValueError:
                pass
            textview.completion_debounce_id = None

        timer_id = GLib.timeout_add(
            150,
            lambda: self._show_completion_if_needed(textview)
        )
        textview.completion_debounce_id = timer_id
        self.debounce_timers.append(timer_id)

    def _show_completion_if_needed(self, textview):
        if hasattr(textview, "completion_debounce_id"):
            try:
                self.debounce_timers.remove(
                    textview.completion_debounce_id
                )
            except ValueError:
                pass
            textview.completion_debounce_id = None

        buffer = textview.get_buffer()
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)

        if not self.tag_completion.should_show_completion(
            buffer, iter_cursor
        ):
            self.tag_completion.close_popup()
            return False

        text = buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            False
        )
        suggestions = self.tag_completion.get_completions(
            text, iter_cursor.get_offset()
        )
        if suggestions:
            self.tag_completion.show_popup(textview, suggestions)
        else:
            self.tag_completion.close_popup()
        return False

    def adjust_tag_weight(self, textview, increase=True):
        """Adjust weight of tag under cursor or selected text."""
        buffer = textview.get_buffer()
        selection = buffer.get_selection_bounds()

        if selection:
            iter_start, iter_end = selection
            selected_text = buffer.get_text(iter_start, iter_end, False)
            if not selected_text.strip():
                return

            weight_pattern = r"^\((.+?):(\d+\.?\d*)\)$"
            match = re.match(weight_pattern, selected_text)
            if match:
                content = match.group(1)
                new_weight = float(match.group(2)) + (
                    0.1 if increase else -0.1
                )
                new_weight = max(0.1, min(2.0, new_weight))
                new_text = (
                    content if abs(new_weight - 1.0) < 0.01
                    else f"({content}:{new_weight:.1f})"
                )
            else:
                new_weight = 1.1 if increase else 0.9
                new_text = (
                    selected_text if abs(new_weight - 1.0) < 0.01
                    else f"({selected_text}:{new_weight:.1f})"
                )

            start_offset = iter_start.get_offset()
            buffer.delete(iter_start, iter_end)
            buffer.insert(iter_start, new_text)
            new_start = buffer.get_iter_at_offset(start_offset)
            new_end = buffer.get_iter_at_offset(
                start_offset + len(new_text)
            )
            buffer.select_range(new_start, new_end)
            return

        # No selection — operate on tag under cursor
        cursor = buffer.get_insert()
        iter_cursor = buffer.get_iter_at_mark(cursor)
        iter_start = iter_cursor.copy()
        iter_end = iter_cursor.copy()

        leading_space = ""
        while not iter_start.starts_line():
            iter_start.backward_char()
            if iter_start.get_char() == ",":
                iter_start.forward_char()
                while iter_start.get_char() == " ":
                    leading_space += " "
                    iter_start.forward_char()
                break

        while not iter_end.ends_line():
            if iter_end.get_char() == ",":
                break
            iter_end.forward_char()

        tag_text = buffer.get_text(iter_start, iter_end, False).strip()
        if not tag_text:
            return

        weight_pattern = r"^\((.+?):(\d+\.?\d*)\)$"
        match = re.match(weight_pattern, tag_text)
        if match:
            tag_content = match.group(1)
            new_weight = float(match.group(2)) + (
                0.1 if increase else -0.1
            )
            new_weight = max(0.1, min(2.0, new_weight))
            new_tag = (
                tag_content if abs(new_weight - 1.0) < 0.01
                else f"({tag_content}:{new_weight:.1f})"
            )
        else:
            new_weight = 1.1 if increase else 0.9
            new_tag = (
                tag_text if abs(new_weight - 1.0) < 0.01
                else f"({tag_text}:{new_weight:.1f})"
            )

        # Rewind to include leading space
        iter_start_with_space = iter_cursor.copy()
        while not iter_start_with_space.starts_line():
            iter_start_with_space.backward_char()
            if iter_start_with_space.get_char() == ",":
                iter_start_with_space.forward_char()
                break

        buffer.delete(iter_start_with_space, iter_end)
        buffer.insert(iter_start_with_space, leading_space + new_tag)

    def _on_textview_key_press(self, textview, keyval, keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt = state & Gdk.ModifierType.ALT_MASK

        if ctrl and keyval == Gdk.KEY_Return:
            self.queue_generate()
            return True
        elif ctrl and keyval == Gdk.KEY_Escape:
            self.stop()
            return True
        elif ctrl and keyval == Gdk.KEY_Up:
            self.adjust_tag_weight(textview, increase=True)
            return True
        elif ctrl and keyval == Gdk.KEY_Down:
            self.adjust_tag_weight(textview, increase=False)
            return True
        elif alt and keyval == Gdk.KEY_Up:
            self.adjust_batch(1)
            return True
        elif alt and keyval == Gdk.KEY_Down:
            self.adjust_batch(-1)
            return True

        if self.tag_completion.handle_key_press(textview, keyval):
            return True
        return False
