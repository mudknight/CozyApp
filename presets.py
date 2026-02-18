#!/usr/bin/python3
"""Presets page: inline-switched Characters, Styles, and Tags."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw  # noqa
from characters import CharactersPage  # noqa
from styles import StylesPage  # noqa
from tags import TagsPage  # noqa


class PresetsPage(Gtk.Box):
    """
    A single page containing Characters, Styles, and Tags sub-pages,
    navigated with an inline Adw.ViewSwitcher below the headerbar.
    """

    def __init__(
        self,
        on_character_selected=None,
        on_style_selected=None,
        on_tag_selected=None,
        log_fn=None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        # Inner stack holding the three sub-pages
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
        self.append(switcher)

        self.append(Gtk.Separator())
        self.append(self.inner_stack)

        # Characters sub-page
        self.characters = CharactersPage(
            on_character_selected=on_character_selected,
            log_fn=log_fn
        )
        self.inner_stack.add_titled_with_icon(
            self.characters.widget,
            'characters', 'Characters',
            'avatar-default-symbolic'
        )

        # Styles sub-page
        self.styles = StylesPage(
            on_style_selected=on_style_selected,
            log_fn=log_fn
        )
        self.inner_stack.add_titled_with_icon(
            self.styles.widget,
            'styles', 'Styles',
            'applications-graphics-symbolic'
        )

        # Tags sub-page
        self.tags = TagsPage(
            on_tag_selected=on_tag_selected,
            log_fn=log_fn
        )
        self.inner_stack.add_titled_with_icon(
            self.tags.widget,
            'tags', 'Tags',
            'bookmark-new-symbolic'
        )

    def refresh(self):
        """Reload data for all three sub-pages."""
        self.characters.fetch_characters()
        self.styles.fetch_styles()
        self.tags.fetch_tags()

    @property
    def widget(self):
        return self
