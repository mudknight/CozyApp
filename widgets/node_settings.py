#!/usr/bin/env python3
"""Node settings dialog for ComfierUI.

Provides NodeSettingsDialog, which wraps an Adw.PreferencesDialog with
one page per workflow node type (Base, Upscale, Detailer, Nested).
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw  # noqa


def default_node_settings():
    """Return a fresh dict of default settings for each node type."""
    return {
        "base": {
            "sampler_name": "euler_ancestral_cfg_pp",
            "scheduler": "karras",
            "steps": 20,
            "cfg": 1.5,
            "denoise": 1.0,
        },
        "upscale": {
            "sampler_name": "euler_ancestral",
            "scheduler": "align_your_steps",
            "steps": 18,
            "cfg": 2.5,
            "denoise": 0.2,
            "upscale_model": "4x-UltraSharpV2_Lite.pth",
            "scale_by": 2.0,
        },
        "detailer": {
            "bbox_model": "bbox/face_yolov8m.pt",
            "fallback_model": "none",
            "threshold": 0.5,
            "steps": 20,
            "cfg": 1.5,
            "sampler": "euler_ancestral_cfg_pp",
            "scheduler": "align_your_steps",
            "denoise": 0.2,
            "upscale_method": "lanczos",
            "upscale_model": "none",
            "feather": 0.1,
            "context_padding": 0.1,
        },
        "nested": {
            "face_model": "bbox/face_yolov8m.pt",
            "eyes_pair_model": "bbox/full_eyes_detect_v1.pt",
            "eye_single_model": "bbox/Eyes.pt",
            "threshold": 0.5,
            "cfg": 2.5,
            "sampler": "euler_ancestral",
            "scheduler": "align_your_steps",
            "face_steps": 20,
            "face_denoise": 0.3,
            "face_scale": 1.5,
            "eye_steps": 20,
            "eye_denoise": 0.2,
            "eye_scale": 1.5,
            "upscale_method": "lanczos",
            "max_megapixels": 2.0,
            "feather": 0.2,
            "context_padding": 0.1,
        },
    }


def _spin_row(title, ns, key, lo, hi, step, digits=2):
    """Return an Adw.SpinRow bound live to ns[key]."""
    adj = Gtk.Adjustment(
        value=float(ns.get(key, 0)),
        lower=lo, upper=hi,
        step_increment=step
    )
    row = Adw.SpinRow(title=title, adjustment=adj)
    row.set_digits(digits)
    adj.connect(
        "value-changed",
        lambda a: ns.__setitem__(key, a.get_value())
    )
    return row


def _combo_row(title, ns, key, options):
    """Return an Adw.ComboRow bound live to ns[key]."""
    row = Adw.ComboRow(title=title)
    row.set_model(Gtk.StringList.new(options))
    cur = ns.get(key, "")
    if cur in options:
        row.set_selected(options.index(cur))

    def on_selected(r, _):
        idx = r.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(options):
            ns[key] = options[idx]

    row.connect("notify::selected", on_selected)
    return row


class NodeSettingsDialog:
    """
    Tabbed Adw.PreferencesDialog for per-node workflow settings.

    Parameters
    ----------
    node_settings : dict
        Live settings dict (mutated in-place as the user changes values).
    on_close : callable
        Called with no arguments when the dialog is dismissed.
    sampler_list, scheduler_list : list[str]
        Enum values fetched from KSampler.
    bbox_model_list, fallback_model_list : list[str]
        Detection model enums from DetailerPipeNode.
    upscale_model_list, upscale_method_list : list[str]
        Upscale enums from DetailerPipeNode.
    """

    def __init__(
        self,
        node_settings,
        on_close,
        sampler_list,
        scheduler_list,
        bbox_model_list,
        fallback_model_list,
        upscale_model_list,
        upscale_method_list,
    ):
        self._ns = node_settings
        self._on_close = on_close
        self._samplers = sampler_list
        self._schedulers = scheduler_list
        self._bbox = bbox_model_list
        self._fallback = fallback_model_list
        self._up_models = upscale_model_list
        self._up_methods = upscale_method_list

    def show(self, parent):
        """Build and present the dialog attached to parent."""
        ns = self._ns
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Node Settings")

        dialog.add(self._build_base_page(ns["base"]))
        dialog.add(self._build_upscale_page(ns["upscale"]))
        dialog.add(self._build_detailer_page(ns["detailer"]))
        dialog.add(self._build_nested_page(ns["nested"]))

        dialog.connect("closed", lambda _: self._on_close())
        dialog.present(parent)

    def _build_base_page(self, ns):
        page = Adw.PreferencesPage(
            title="Base", icon_name="go-home-symbolic"
        )

        sampler_group = Adw.PreferencesGroup(title="Sampler")
        page.add(sampler_group)
        sampler_group.add(
            _combo_row("Sampler", ns, "sampler_name", self._samplers)
        )
        sampler_group.add(
            _combo_row("Scheduler", ns, "scheduler", self._schedulers)
        )

        params_group = Adw.PreferencesGroup(title="Parameters")
        page.add(params_group)
        params_group.add(_spin_row("Steps", ns, "steps", 1, 150, 1, 0))
        params_group.add(_spin_row("CFG", ns, "cfg", 0.0, 30.0, 0.1))
        params_group.add(
            _spin_row("Denoise", ns, "denoise", 0.0, 1.0, 0.01)
        )

        return page

    def _build_upscale_page(self, ns):
        page = Adw.PreferencesPage(
            title="Upscale", icon_name="zoom-in-symbolic"
        )

        sampler_group = Adw.PreferencesGroup(title="Sampler")
        page.add(sampler_group)
        sampler_group.add(
            _combo_row("Sampler", ns, "sampler_name", self._samplers)
        )
        sampler_group.add(
            _combo_row("Scheduler", ns, "scheduler", self._schedulers)
        )

        params_group = Adw.PreferencesGroup(title="Parameters")
        page.add(params_group)
        params_group.add(_spin_row("Steps", ns, "steps", 1, 150, 1, 0))
        params_group.add(_spin_row("CFG", ns, "cfg", 0.0, 30.0, 0.1))
        params_group.add(
            _spin_row("Denoise", ns, "denoise", 0.0, 1.0, 0.01)
        )

        scale_group = Adw.PreferencesGroup(title="Upscaling")
        page.add(scale_group)
        scale_group.add(
            _combo_row("Model", ns, "upscale_model", self._up_models)
        )
        scale_group.add(
            _spin_row("Scale", ns, "scale_by", 1.0, 8.0, 0.5)
        )

        return page

    def _build_detailer_page(self, ns):
        page = Adw.PreferencesPage(
            title="Detailer", icon_name="find-location-symbolic"
        )

        detect_group = Adw.PreferencesGroup(title="Detection")
        page.add(detect_group)
        detect_group.add(
            _combo_row("BBox Model", ns, "bbox_model", self._bbox)
        )
        detect_group.add(
            _combo_row(
                "Fallback Model", ns, "fallback_model", self._fallback
            )
        )
        detect_group.add(
            _spin_row("Threshold", ns, "threshold", 0.0, 1.0, 0.01)
        )

        sampler_group = Adw.PreferencesGroup(title="Sampler")
        page.add(sampler_group)
        sampler_group.add(
            _combo_row("Sampler", ns, "sampler", self._samplers)
        )
        sampler_group.add(
            _combo_row("Scheduler", ns, "scheduler", self._schedulers)
        )

        params_group = Adw.PreferencesGroup(title="Parameters")
        page.add(params_group)
        params_group.add(_spin_row("Steps", ns, "steps", 1, 150, 1, 0))
        params_group.add(_spin_row("CFG", ns, "cfg", 0.0, 30.0, 0.1))
        params_group.add(
            _spin_row("Denoise", ns, "denoise", 0.0, 1.0, 0.01)
        )

        upscale_group = Adw.PreferencesGroup(title="Upscaling")
        page.add(upscale_group)
        upscale_group.add(
            _combo_row(
                "Method", ns, "upscale_method", self._up_methods
            )
        )
        upscale_group.add(
            _combo_row("Model", ns, "upscale_model", self._up_models)
        )
        upscale_group.add(
            _spin_row("Feather", ns, "feather", 0.0, 1.0, 0.01)
        )
        upscale_group.add(
            _spin_row(
                "Context Padding", ns, "context_padding", 0.0, 1.0, 0.01
            )
        )

        return page

    def _build_nested_page(self, ns):
        page = Adw.PreferencesPage(
            title="Nested", icon_name="emblem-system-symbolic"
        )

        detect_group = Adw.PreferencesGroup(title="Detection")
        page.add(detect_group)
        detect_group.add(
            _combo_row("Face Model", ns, "face_model", self._bbox)
        )
        detect_group.add(
            _combo_row(
                "Eyes Pair Model", ns, "eyes_pair_model", self._bbox
            )
        )
        detect_group.add(
            _combo_row(
                "Eye Single Model", ns, "eye_single_model", self._bbox
            )
        )
        detect_group.add(
            _spin_row("Threshold", ns, "threshold", 0.0, 1.0, 0.01)
        )

        sampler_group = Adw.PreferencesGroup(title="Sampler")
        page.add(sampler_group)
        sampler_group.add(
            _combo_row("Sampler", ns, "sampler", self._samplers)
        )
        sampler_group.add(
            _combo_row("Scheduler", ns, "scheduler", self._schedulers)
        )

        face_group = Adw.PreferencesGroup(title="Face")
        page.add(face_group)
        face_group.add(
            _spin_row("Steps", ns, "face_steps", 1, 150, 1, 0)
        )
        face_group.add(
            _spin_row("Denoise", ns, "face_denoise", 0.0, 1.0, 0.01)
        )
        face_group.add(
            _spin_row("Scale", ns, "face_scale", 0.1, 2.0, 0.1)
        )

        eyes_group = Adw.PreferencesGroup(title="Eyes")
        page.add(eyes_group)
        eyes_group.add(
            _spin_row("Steps", ns, "eye_steps", 1, 150, 1, 0)
        )
        eyes_group.add(
            _spin_row("Denoise", ns, "eye_denoise", 0.0, 1.0, 0.01)
        )
        eyes_group.add(
            _spin_row("Scale", ns, "eye_scale", 0.1, 2.0, 0.1)
        )

        upscale_group = Adw.PreferencesGroup(title="Upscaling")
        page.add(upscale_group)
        upscale_group.add(
            _combo_row(
                "Method", ns, "upscale_method", self._up_methods
            )
        )
        upscale_group.add(
            _spin_row(
                "Max Megapixels", ns, "max_megapixels", 0.1, 10.0, 0.1
            )
        )
        upscale_group.add(
            _spin_row("Feather", ns, "feather", 0.0, 1.0, 0.01)
        )
        upscale_group.add(
            _spin_row(
                "Context Padding", ns, "context_padding", 0.0, 1.0, 0.01
            )
        )

        return page
