#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generated color bars on the private trial socket; no Android connection."""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk


def main():
    if os.getuid() != 1000 or len(sys.argv) != 1 or os.environ.get("WAYLAND_DISPLAY") != "wayland-capture-pattern-20261009":
        raise RuntimeError("Only the isolated pattern compositor is permitted")
    application = Gtk.Application(application_id="org.teslaandroid.CapturePatternTest", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        window = Gtk.ApplicationWindow(application=app, title="Isolated capture pattern")
        window.set_default_size(320, 240)
        bars = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        labels = []
        for name in ["red", "green", "blue"]:
            label = Gtk.Label(label=name)
            label.set_hexpand(True)
            label.set_vexpand(True)
            label.add_css_class("pattern-" + name)
            bars.append(label)
            labels.append(label)
        provider = Gtk.CssProvider()
        provider.load_from_data(b".pattern-red { background: #ff3030; color: #ffffff; } "
                                b".pattern-green { background: #30ff30; color: #101010; } "
                                b".pattern-blue { background: #3030ff; color: #ffffff; } "
                                b".pattern-off { background: #000000; color: #000000; }")
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.set_child(bars)
        window.fullscreen()
        window.present()
        state = [False]

        def pulse():
            state[0] = not state[0]
            if state[0]:
                labels[0].add_css_class("pattern-off")
            else:
                labels[0].remove_css_class("pattern-off")
            return True

        GLib.timeout_add(200, pulse)
        GLib.timeout_add_seconds(30, lambda: (app.quit(), False)[1])

    application.connect("activate", activate)
    application.run([])


if __name__ == "__main__":
    main()
