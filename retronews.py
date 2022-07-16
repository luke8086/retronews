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
from textwrap import wrap
from typing import List, Optional, TypedDict, TypeVar

T = TypeVar("T")

MENU_HEIGHT = 1


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
        self.date = init_pair(curses.COLOR_CYAN, -1)
        self.author = init_pair(curses.COLOR_YELLOW, -1)
        self.cursor = init_pair(curses.COLOR_BLACK, curses.COLOR_CYAN)


@dataclass
class Message:
    msg_id: str
    author: str
    title: str
    date: datetime
    lines: List[str] = field(default_factory=list)
    index_position: int = 0


@dataclass
class AppState:
    screen: "curses._CursesWindow"
    colors: Colors
    messages: List[Message] = field(default_factory=list)
    selected_message: Optional[Message] = None
    pager_visible: bool = False
    flash: Optional[str] = None


class HNSearchHit(TypedDict):
    objectID: int
    author: str
    title: str
    created_at_i: int
    story_text: Optional[str]
    url: Optional[str]


def list_get(lst: List[T], index: int, default: Optional[T] = None) -> Optional[T]:
    return lst[index] if 0 <= index < len(lst) else default


def cmd_quit(_: AppState):
    sys.exit(0)


def cmd_up(app: AppState) -> None:
    pos = app.selected_message.index_position - 1 if app.selected_message else 0
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_down(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_page_up(app: AppState) -> None:
    index_height = app_get_index_height(app)
    pos = app.selected_message.index_position - index_height if app.selected_message else 0
    pos = max(pos, 0)
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_page_down(app: AppState) -> None:
    index_height = app_get_index_height(app)
    pos = app.selected_message.index_position + index_height if app.selected_message else 0
    pos = min(pos, len(app.messages) - 1)
    app.selected_message = list_get(app.messages, pos, app.selected_message)


def cmd_open(app: AppState) -> None:
    app.pager_visible = app.selected_message is not None


def cmd_close(app: AppState) -> None:
    app.pager_visible = False


def cmd_unknown(app: AppState) -> None:
    app.flash = "Unknown key"


KEY_BINDINGS = {
    ord("q"): cmd_quit,
    ord("\n"): cmd_open,
    ord(" "): cmd_open,
    ord("x"): cmd_close,
    curses.KEY_UP: cmd_up,
    curses.KEY_DOWN: cmd_down,
    curses.KEY_PPAGE: cmd_page_up,
    curses.KEY_NPAGE: cmd_page_down,
}


def app_load_messages(app: AppState, messages: List[Message]) -> None:
    for i, message in enumerate(messages):
        message.index_position = i
        message.title = f"{i} {message.title}"

    app.messages = messages
    app.selected_message = messages[0] if len(messages) > 0 else None


def app_render_pager(app: AppState, top: int, height: int) -> None:
    message = app.selected_message

    if message is None:
        return

    for i in range(height):
        line = list_get(message.lines, i) or ""
        app.screen.insstr(i + top, 0, line[: curses.COLS].ljust(curses.COLS))


def app_get_index_height(app: AppState) -> int:
    max_height = curses.LINES - 3 * MENU_HEIGHT
    return (max_height // 3) if app.pager_visible else max_height


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    app.screen.addstr(row, 0, "[                ]  [          ]")
    app.screen.addstr(row, 1, message.date.strftime("%Y-%m-%d %H:%M"), app.colors.date)
    app.screen.addstr(row, 21, message.author[:10], app.colors.author)
    app.screen.addstr(row, 34, message.title)

    if message == app.selected_message:
        app.screen.chgat(row, 0, curses.COLS, app.colors.cursor)


def app_render_index(app: AppState, height: int) -> None:
    offset = app.selected_message.index_position - height // 2 if app.selected_message else 0
    offset = min(offset, len(app.messages) - height)
    offset = max(offset, 0)

    rows_to_render = min(height, len(app.messages) - offset)

    for i in range(rows_to_render):
        app_render_index_row(app, MENU_HEIGHT + i, app.messages[i + offset])


def app_render_menus(app: AppState, index_height: int) -> None:
    top_menu_row = 0
    index_menu_row = MENU_HEIGHT + index_height
    pager_menu_row = curses.LINES - 2 * MENU_HEIGHT
    flash_menu_row = curses.LINES - MENU_HEIGHT

    app.screen.insstr(top_menu_row, 0, "top menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)
    app.screen.insstr(index_menu_row, 0, "index menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)

    if app.pager_visible:
        app.screen.insstr(pager_menu_row, 0, "pager menu".ljust(curses.COLS), app.colors.menu | curses.A_BOLD)

    app.screen.addstr(flash_menu_row, 0, app.flash or "")


def app_render(app: AppState) -> None:
    app.screen.erase()

    index_height = app_get_index_height(app)

    app_render_menus(app, index_height)
    app_render_index(app, index_height)

    if app.pager_visible:
        pager_top = index_height + 2 * MENU_HEIGHT
        pager_height = curses.LINES - pager_top - 2 * MENU_HEIGHT
        app_render_pager(app, pager_top, pager_height)

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
        lines=wrap(hit["story_text"] or hit["url"] or "", 80),
    )


def hn_search_stories() -> List[Message]:
    hits: List[HNSearchHit] = json.load(open("./tmp/index.json"))["hits"] * 5
    return [hn_parse_search_hit(hit) for hit in hits]


def main(screen: "curses._CursesWindow") -> None:
    app = app_init(screen)

    while True:
        app_render(app)
        app.flash = None
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)


if __name__ == "__main__":
    curses.wrapper(main)
