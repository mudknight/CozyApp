#!/usr/bin/python3
"""LoRA Manager page: inline-switched LoRAs and Models."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw  # noqa
from .loras import LorasPage  # noqa
from .models import ModelsPage  # noqa


class LoraManagerPage(Gtk.Box):
    """
    A single page containing LoRAs and Models sub-pages,
    navigated with an inline Adw.ViewSwitcher below the headerbar.
    """

    def __init__(
        self,
        on_lora_selected=None,
        on_model_selected=None,
        log_fn=None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        # Inner stack holding the two sub-pages
        self.inner_stack = Adw.ViewStack(
            hexpand=True,
            vexpand=True
        )

        # Inline switcher — sits inside the page, not the headerbar
        switcher = Adw.ViewSwitcher(
            stack=self.inner_stack,
            policy=Adw.ViewSwitcherPolicy.WIDE
        )
        switcher.set_margin_top(8)
        switcher.set_margin_bottom(8)
        switcher.set_margin_start(8)
        switcher.set_margin_end(8)
        self.append(switcher)

        self.append(self.inner_stack)

        # LoRAs sub-page
        self.loras = LorasPage(
            on_lora_selected=on_lora_selected,
            log_fn=log_fn
        )
        self.inner_stack.add_titled_with_icon(
            self.loras.widget,
            'loras', 'LoRAs',
            'zoom-in-symbolic'
        )

        # Models sub-page
        self.models = ModelsPage(
            on_model_selected=on_model_selected,
            log_fn=log_fn
        )
        self.inner_stack.add_titled_with_icon(
            self.models.widget,
            'models', 'Models',
            'drive-multidisk-symbolic'
        )

    def refresh(self):
        """Reload data for both sub-pages."""
        self.loras.refresh()
        self.models.refresh()

    def show_unavailable_message(self):
        """
        Show a message indicating the LoRA Manager API is unavailable.
        """
        # This can be called from the main window if the API check fails
        pass  # The individual pages handle their own empty states

    @property
    def widget(self):
        return self
