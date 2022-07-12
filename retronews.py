#!/usr/bin/env python3
#
# Copyright (c) luke8086
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#

import curses
import sys
from dataclasses import dataclass


@dataclass
class AppState:
    screen: "curses._CursesWindow"


def cmd_quit(_: AppState):
    sys.exit(0)


def cmd_unknown(_: AppState) -> None:
    pass


KEY_BINDINGS = {
    ord("q"): cmd_quit,
}


def app_init(screen: "curses._CursesWindow") -> AppState:
    app = AppState(screen=screen)
    curses.curs_set(0)
    return app


def app_render(st: AppState) -> None:
    st.screen.refresh()


def main(screen: "curses._CursesWindow") -> None:
    app = app_init(screen)

    while True:
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)
        app_render(app)


if __name__ == "__main__":
    curses.wrapper(main)
