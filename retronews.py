#!/usr/bin/env python3
#
# Copyright (c) luke8086
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#

import curses
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, TypedDict, TypeVar

T = TypeVar("T")


class Colors:
    menu: int

    def __init__(self):
        idx = 0

        def init_pair(fg: int, bg: int) -> int:
            nonlocal idx
            idx = idx + 1
            curses.init_pair(idx, fg, bg)
            return curses.color_pair(idx)

        self.menu = init_pair(curses.COLOR_GREEN, curses.COLOR_BLUE)


@dataclass
class Message:
    msg_id: str
    author: str
    title: str
    date: datetime
    index_position: int = 0


@dataclass
class AppState:
    screen: "curses._CursesWindow"
    colors: Colors
    messages: List[Message] = field(default_factory=list)
    selected_message: Optional[Message] = None
    pager_visible: bool = False


class HNSearchHit(TypedDict):
    objectID: int
    author: str
    title: str
    created_at_i: int


def list_get(lst: List[T], index: int, default: Optional[T]) -> Optional[T]:
    return lst[index] if 0 <= index < len(lst) else default


def cmd_quit(_: AppState):
    sys.exit(0)


def cmd_up(app: AppState) -> None:
    pos = app.selected_message.index_position - 1 if app.selected_message else 0
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_down(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_unknown(_: AppState) -> None:
    pass


KEY_BINDINGS = {
    ord("q"): cmd_quit,
    curses.KEY_UP: cmd_up,
    curses.KEY_DOWN: cmd_down,
}


def app_load_messages(app: AppState, messages: List[Message]) -> None:
    for i, message in enumerate(messages):
        message.index_position = i
        message.title = f"{i} {message.title}"

    app.messages = messages
    app.selected_message = messages[0] if len(messages) > 0 else None


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    cursor = "->" if message == app.selected_message else "  "
    app.screen.insstr(row, 0, f"{cursor} [{message.date}]  [{message.author[:10]:10}]  {message.title}")


def app_render_index(app: AppState, top: int, height: int) -> None:
    offset = app.selected_message.index_position - height // 2 if app.selected_message else 0
    offset = min(offset, len(app.messages) - height)
    offset = max(offset, 0)

    rows_to_render = min(height, len(app.messages) - offset)

    for i in range(rows_to_render):
        app_render_index_row(app, i + top, app.messages[i + offset])


def app_render_menus(app: AppState, index_top: int, index_bottom: int) -> None:
    app.screen.insstr(index_top - 1, 0, "top menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)
    app.screen.insstr(index_bottom + 1, 0, "index menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)

    if app.pager_visible:
        app.screen.insstr(curses.LINES - 1, 0, "pager menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)


def app_render(app: AppState) -> None:
    app.screen.erase()

    index_top = 1
    index_bottom = ((curses.LINES - 2) // 3) if app.pager_visible else curses.LINES - 2
    index_height = index_bottom - index_top + 1

    app_render_index(app, index_top, index_height)
    app_render_menus(app, index_top, index_bottom)

    app.screen.refresh()


def app_init(screen: "curses._CursesWindow") -> AppState:
    curses.curs_set(0)
    curses.use_default_colors()

    app = AppState(screen=screen, colors=Colors())
    app_load_messages(app, hn_search_stories())

    return app


def hn_parse_search_hit(hit: HNSearchHit) -> Message:
    return Message(
        msg_id=f"{hit['objectID']}@hn",
        author=hit["author"],
        title=hit["title"],
        date=datetime.fromtimestamp(hit["created_at_i"]),
    )


def hn_search_stories() -> List[Message]:
    hits: List[HNSearchHit] = json.load(open("./tmp/index.json"))["hits"]
    return [hn_parse_search_hit(hit) for hit in hits]


def main(screen: "curses._CursesWindow") -> None:
    app = app_init(screen)

    while True:
        app_render(app)
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)


if __name__ == "__main__":
    curses.wrapper(main)
