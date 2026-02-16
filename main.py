import sys
import json
import uuid
import threading
import random
import requests
import websocket
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, GLib, Gio, Gdk, GdkPixbuf

SERVER_ADDRESS = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())
PROMPT_NODE_CLASS = "PromptConditioningNode"
LOADER_NODE_CLASS = "LoaderFullPipe"
SAVE_NODE_CLASS = "SaveFullPipe"


class ComfyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id="com.example.comfy_gen", **kwargs)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        self.win = ComfyWindow(application=app)
        self.win.present()


class ComfyWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("ComfyUI Prompt Editor")
        self.set_default_size(1200, 900)

        self.setup_css()
        self.style_list = []
        self.workflow_data = None

        # Main Layout container
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header
        self.header = Adw.HeaderBar()
        self.main_box.append(self.header)

        self.debug_toggle = Gtk.ToggleButton(icon_name="utilities-terminal-symbolic")
        self.debug_toggle.connect("toggled", self.on_toggle_debug)
        self.header.pack_start(self.debug_toggle)

        self.preview_toggle = Gtk.ToggleButton(icon_name="view-reveal-symbolic", active=True)
        self.preview_toggle.connect("toggled", self.on_toggle_preview)
        self.header.pack_end(self.preview_toggle)

        self.toast_overlay = Adw.ToastOverlay()
        self.main_box.append(self.toast_overlay)

        # Horizontal Split: [ Sidebar (Left) | Preview (Right) ]
        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toast_overlay.set_child(self.hbox)

        # --- Sidebar (Left Column) ---
        self.sidebar_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.sidebar_vbox.set_size_request(420, -1)
        self.hbox.append(self.sidebar_vbox)

        # Top section: Inputs
        self.input_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        for m in ["top", "start", "end"]:
            getattr(self.input_area, f"set_margin_{m}")(20)
        self.input_area.set_margin_bottom(10)
        self.sidebar_vbox.append(self.input_area)

        self.file_button = Gtk.Button(label="Load API JSON")
        self.file_button.connect("clicked", self.on_open_file)
        self.input_area.append(self.file_button)

        self.input_area.append(Gtk.Label(label="Style", xalign=0))
        self.style_dropdown = Gtk.DropDown.new_from_strings([])
        self.input_area.append(self.style_dropdown)

        self.pos_buffer = Gtk.TextBuffer()
        self.input_area.append(Gtk.Label(label="Positive Prompt", xalign=0))
        self.input_area.append(self.create_scrolled(Gtk.TextView(buffer=self.pos_buffer, wrap_mode=Gtk.WrapMode.WORD, vexpand=True)))

        self.neg_buffer = Gtk.TextBuffer()
        self.input_area.append(Gtk.Label(label="Negative Prompt", xalign=0))
        self.input_area.append(self.create_scrolled(Gtk.TextView(buffer=self.neg_buffer, wrap_mode=Gtk.WrapMode.WORD, vexpand=True)))

        self.input_area.append(Gtk.Label(label="Seed", xalign=0))
        seed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.seed_adj = Gtk.Adjustment(value=0, lower=0, upper=2**64-1, step_increment=1)
        self.seed_entry = Gtk.SpinButton(adjustment=self.seed_adj, numeric=True, hexpand=True)
        self.seed_mode_combo = Gtk.DropDown.new_from_strings(["Randomize", "Fixed"])
        seed_box.append(self.seed_entry)
        seed_box.append(self.seed_mode_combo)
        self.input_area.append(seed_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.gen_button = Gtk.Button(label="Generate", css_classes=["suggested-action"], hexpand=True)
        self.gen_button.connect("clicked", self.on_generate_clicked)
        self.stop_button = Gtk.Button(label="Stop", css_classes=["destructive-action"])
        self.stop_button.connect("clicked", self.on_stop_clicked)
        btn_box.append(self.gen_button)
        btn_box.append(self.stop_button)
        self.input_area.append(btn_box)

        self.progress_bar = Gtk.ProgressBar()
        self.input_area.append(self.progress_bar)

        # Bottom section: Debug (Split vertically from inputs)
        self.debug_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, reveal_child=False)
        self.sidebar_vbox.append(self.debug_revealer)

        debug_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        debug_box.set_size_request(-1, 200)
        debug_box.set_margin_start(20)
        debug_box.set_margin_end(20)
        debug_box.set_margin_bottom(20)
        
        self.debug_buffer = Gtk.TextBuffer()
        debug_view = Gtk.TextView(buffer=self.debug_buffer, editable=False, wrap_mode=Gtk.WrapMode.CHAR, css_classes=["debug-text"])
        debug_box.append(self.create_scrolled(debug_view))
        self.debug_revealer.set_child(debug_box)

        # --- Preview (Right Column) ---
        self.preview_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, reveal_child=True, hexpand=True)
        self.hbox.append(self.preview_revealer)

        preview_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["preview-panel"])
        preview_panel.set_size_request(500, -1)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, vexpand=True)
        for side in ["top", "bottom", "start", "end"]:
            getattr(self.picture, f"set_margin_{side}")(20)
        preview_panel.append(self.picture)
        self.preview_revealer.set_child(preview_panel)

        self.fetch_node_info()

    def setup_css(self):
        css_provider = Gtk.CssProvider()
        css_content = """
            revealer { background-color: transparent; border: none; }
            .preview-panel { background-color: shade(@window_bg_color, 1.02); border-left: 1px solid @border_color; }
            .view { border: 1px solid @border_color; border-radius: 8px; background-color: @view_bg_color; }
            .debug-text { font-family: monospace; font-size: 11px; opacity: 0.8; }
            separator { background-color: transparent; }
        """
        css_provider.load_from_data(css_content, len(css_content))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def log(self, text):
        GLib.idle_add(self._log_idle, text)

    def _log_idle(self, text):
        end_iter = self.debug_buffer.get_end_iter()
        self.debug_buffer.insert(end_iter, f"> {text}\n")

    def fetch_node_info(self):
        def worker():
            try:
                url = f"http://{SERVER_ADDRESS}/object_info/{PROMPT_NODE_CLASS}"
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    data = resp.json().get(PROMPT_NODE_CLASS, {})
                    inputs = data.get("input", {})
                    styles = None
                    for cat in ["required", "optional"]:
                        if "style" in inputs.get(cat, {}):
                            entry = inputs[cat]["style"]
                            styles = entry[0] if isinstance(entry, list) and isinstance(entry[0], list) else entry
                            break
                    if styles: GLib.idle_add(self.update_style_dropdown, styles)
            except Exception as e: self.log(f"Metadata fail: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def update_style_dropdown(self, styles):
        self.style_list = styles
        self.style_dropdown.set_model(Gtk.StringList.new(styles))
        if self.workflow_data: self.sync_ui_from_json()

    def create_scrolled(self, child):
        sc = Gtk.ScrolledWindow(child=child, propagate_natural_height=False, vexpand=True)
        sc.add_css_class("view")
        return sc

    def on_toggle_preview(self, btn):
        self.preview_revealer.set_reveal_child(btn.get_active())

    def on_toggle_debug(self, btn):
        self.debug_revealer.set_reveal_child(btn.get_active())

    def on_open_file(self, _):
        dialog = Gtk.FileDialog.new()
        dialog.open(self, None, self.on_file_dialog_response)

    def on_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                with open(file.get_path(), 'r', encoding='utf-8') as f:
                    self.workflow_data = json.load(f)
                self.sync_ui_from_json()
        except Exception as e: self.log(str(e))

    def sync_ui_from_json(self):
        if not self.workflow_data: return
        for node in self.workflow_data.values():
            if node.get("class_type") == PROMPT_NODE_CLASS:
                self.pos_buffer.set_text(str(node["inputs"].get("positive", "")))
                self.neg_buffer.set_text(str(node["inputs"].get("negative", "")))
                style_val = node["inputs"].get("style")
                if style_val in self.style_list:
                    self.style_dropdown.set_selected(self.style_list.index(style_val))
            elif node.get("class_type") == LOADER_NODE_CLASS:
                self.seed_adj.set_value(float(node["inputs"].get("seed", 0)))

    def on_stop_clicked(self, _):
        try:
            requests.post(f"http://{SERVER_ADDRESS}/interrupt", timeout=5)
            self.log("Interrupt signal sent.")
        except Exception as e: self.log(f"Stop error: {e}")

    def on_generate_clicked(self, _):
        if not self.workflow_data: return
        if self.seed_mode_combo.get_selected() == 0:
            self.seed_adj.set_value(float(random.randint(0, 2**32)))
        
        current_seed = int(self.seed_adj.get_value())
        pos = self.pos_buffer.get_text(self.pos_buffer.get_start_iter(), self.pos_buffer.get_end_iter(), False)
        neg = self.neg_buffer.get_text(self.neg_buffer.get_start_iter(), self.neg_buffer.get_end_iter(), False)
        sel_idx = self.style_dropdown.get_selected()
        style = self.style_list[sel_idx] if self.style_list and sel_idx != Gtk.INVALID_LIST_POSITION else None

        for node in self.workflow_data.values():
            if node.get("class_type") == PROMPT_NODE_CLASS:
                node["inputs"].update({"positive": pos, "negative": neg})
                if style: node["inputs"]["style"] = style
            elif node.get("class_type") == LOADER_NODE_CLASS:
                node["inputs"]["seed"] = current_seed

        self.gen_button.set_sensitive(False)
        threading.Thread(target=self.generate_logic, daemon=True).start()

    def generate_logic(self):
        ws = websocket.WebSocket()
        try:
            ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
            payload = {"prompt": self.workflow_data, "client_id": CLIENT_ID}
            resp = requests.post(f"http://{SERVER_ADDRESS}/prompt", json=payload, timeout=10)
            prompt_id = resp.json().get('prompt_id')

            while True:
                out = ws.recv()
                if isinstance(out, bytes):
                    GLib.idle_add(self.update_image, out[8:])
                    continue
                
                msg = json.loads(out)
                if msg['type'] == 'progress':
                    val = msg['data']['value'] / msg['data']['max']
                    GLib.idle_add(self.progress_bar.set_fraction, val)
                
                if msg['type'] == 'executed' and 'images' in msg['data']['output']:
                    img = msg['data']['output']['images'][0]
                    img_data = requests.get(f"http://{SERVER_ADDRESS}/view", params=img).content
                    GLib.idle_add(self.update_image, img_data)

                if msg['type'] == 'executing' and msg['data']['node'] is None:
                    break

            hist_resp = requests.get(f"http://{SERVER_ADDRESS}/history/{prompt_id}", timeout=10)
            history = hist_resp.json().get(prompt_id, {})
            for node_id, node_output in history.get('outputs', {}).items():
                if self.workflow_data.get(node_id, {}).get("class_type") == SAVE_NODE_CLASS:
                    img = node_output['images'][0]
                    data = requests.get(f"http://{SERVER_ADDRESS}/view", params=img).content
                    GLib.idle_add(self.update_image, data)
                    break
        except Exception as e: self.log(f"Gen error: {e}")
        finally:
            ws.close()
            GLib.idle_add(self.gen_button.set_sensitive, True)
            GLib.idle_add(self.progress_bar.set_fraction, 0.0)

    def update_image(self, data):
        loader = GdkPixbuf.PixbufLoader.new()
        try:
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
            if pix: self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pix))
        except: pass

if __name__ == "__main__":
    app = ComfyApp()
    app.run(sys.argv)
