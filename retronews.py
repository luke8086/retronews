#!/usr/bin/env python3
#
# Copyright (c) luke8086
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#

import curses
import html.parser
import json
import logging
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from textwrap import wrap
from typing import Any, Generator, List, Optional, TypedDict, TypeVar, Union

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
        self.tree = init_pair(curses.COLOR_RED, -1)
        self.cursor = init_pair(curses.COLOR_BLACK, curses.COLOR_CYAN)


@dataclass
class Message:
    msg_id: str
    story_id: str
    author: str
    title: str
    date: datetime
    loaded: bool = False
    lines: List[str] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)
    children: List["Message"] = field(default_factory=list)
    index_position: int = 0
    index_tree: str = ""


@dataclass
class AppState:
    screen: "curses._CursesWindow"
    colors: Colors
    messages: List[Message] = field(default_factory=list)
    selected_message: Optional[Message] = None
    pager_visible: bool = False
    raw_mode: bool = False
    flash: Optional[str] = None


class HNSearchHit(TypedDict):
    objectID: int
    author: str
    title: str
    created_at_i: int
    story_text: Optional[str]
    url: Optional[str]


class HNEntry(TypedDict):
    author: Optional[str]
    # FIXME: Recursive declarations are not yet supported in TypedDicts
    children: List[Any]
    created_at_i: int
    id: int
    parent_id: Optional[int]
    text: Optional[str]
    title: Optional[str]
    url: Optional[str]


class HTMLParser(html.parser.HTMLParser):
    text: str = ""

    def handle_data(self, data):
        self.text += data

    def handle_endtag(self, tag):
        if tag == "br":
            self.text += "\n"
        elif tag == "p":
            self.text += "\n\n"


def fetch(url: str) -> str:
    logging.debug(f"Fetching '{url}'...")

    headers = {"User-Agent": "retronews"}
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req).read().decode()

    return resp


def parse_html(html: str) -> List[str]:
    parser = HTMLParser()
    parser.feed(html)
    parser.close()

    return wrap(parser.text.strip())


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
    if (msg := app.selected_message) is None:
        return

    if msg.msg_id == msg.story_id:
        app_open_story(app, msg)

    app.pager_visible = True


def cmd_close(app: AppState) -> None:
    if app.pager_visible:
        app.pager_visible = False
    else:
        app_close_story(app)


def cmd_toggle_raw_mode(app: AppState) -> None:
    app.raw_mode = not app.raw_mode


def cmd_unknown(app: AppState) -> None:
    app.flash = "Unknown key"


KEY_BINDINGS = {
    ord("q"): cmd_quit,
    ord("\n"): cmd_open,
    ord(" "): cmd_open,
    ord("x"): cmd_close,
    ord("r"): cmd_toggle_raw_mode,
    curses.KEY_UP: cmd_up,
    curses.KEY_DOWN: cmd_down,
    curses.KEY_PPAGE: cmd_page_up,
    curses.KEY_NPAGE: cmd_page_down,
}


def app_load_messages(app: AppState, messages: List[Message], selected_message_id: Optional[str] = None) -> None:
    if selected_message_id is None and app.selected_message is not None:
        selected_message_id = app.selected_message.msg_id

    selected_message = None

    for i, message in enumerate(messages):
        message.index_position = i

        if message.msg_id == selected_message_id:
            selected_message = message

    if selected_message is None and len(messages) > 0:
        selected_message = messages[0]

    app.messages = messages
    app.selected_message = selected_message


def app_close_story(app: AppState) -> None:
    selected_story_id = app.selected_message.story_id if app.selected_message else None
    filtered_messages = [msg for msg in app.messages if msg.msg_id == msg.story_id]
    app_load_messages(app, filtered_messages, selected_message_id=selected_story_id)


def app_open_story(app: AppState, story_message: Message) -> None:
    source_id = story_message.story_id.split("@")[0]

    app.flash = f"Fetching story '{story_message.story_id}'..."
    app_render(app)

    new_story_message = hn_parse_entry(hn_fetch_entry(source_id))

    app_close_story(app)

    index_pos = story_message.index_position
    story_messages = list(app_flatten_story(new_story_message, prefix="", is_last_child=False, is_top=True))
    messages = app.messages[:index_pos] + story_messages + app.messages[index_pos + 1 :]  # noqa: E203

    app_load_messages(app, messages, selected_message_id=story_message.msg_id)


def app_flatten_story(msg: Message, prefix, is_last_child, is_top=False) -> Generator[Message, None, None]:
    msg.index_tree = "" if is_top else f"{prefix}{'└─' if is_last_child else '├─'}> "
    yield msg

    children_count = len(msg.children)

    for i, child_node in enumerate(msg.children):
        tree_prefix = "" if is_top else f"{prefix}{'  ' if is_last_child else '│ '}"
        for child in app_flatten_story(child_node, tree_prefix, i == children_count - 1):
            yield child


def app_render_pager(app: AppState, top: int, height: int) -> None:
    message = app.selected_message

    if message is None:
        return

    lines = message.raw_lines if app.raw_mode else message.lines

    for i in range(height):
        line = list_get(lines, i) or ""
        app.screen.insstr(i + top, 0, line[: curses.COLS].ljust(curses.COLS))


def app_get_index_height(app: AppState) -> int:
    max_height = curses.LINES - 3 * MENU_HEIGHT
    return (max_height // 3) if app.pager_visible else max_height


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    app.screen.addstr(row, 0, "[                ]  [          ]")
    app.screen.addstr(row, 1, message.date.strftime("%Y-%m-%d %H:%M"), app.colors.date)
    app.screen.addstr(row, 21, message.author[:10], app.colors.author)
    app.screen.addstr(row, 34, message.index_tree, app.colors.tree)
    app.screen.addstr(row, 34 + len(message.index_tree), message.title)

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
    app.flash = ""


def app_init_logging() -> None:
    format = "%(asctime)s %(levelname)s: %(message)s"
    stream = open("tmp/retronews.log", "a")
    logging.basicConfig(format=format, level="DEBUG", stream=stream)
    logging.debug("Session started")


def app_init(screen: "curses._CursesWindow") -> AppState:
    curses.curs_set(0)
    curses.use_default_colors()

    app = AppState(screen=screen, colors=Colors())
    app_load_messages(app, hn_search_stories())

    return app


def hn_parse_search_hit(hit: HNSearchHit) -> Message:
    return Message(
        msg_id=f"{hit['objectID']}@hn",
        story_id=f"{hit['objectID']}@hn",
        author=hit["author"],
        title=hit["title"],
        date=datetime.fromtimestamp(hit["created_at_i"]),
        loaded=False,
        lines=[],
        raw_lines=[],
    )


def hn_parse_entry(entry: HNEntry, story_id: str = "", parent_title: str = "") -> Message:
    story_id = story_id or str(entry["id"])

    content = f"<p>{entry['url']}</p>" if entry["url"] else ""
    content = f"{content}{entry['text']}" if entry["text"] else content

    return Message(
        msg_id=f"{entry['id']}@hn",
        story_id=f"{story_id}@hn",
        author=entry["author"] or "unknown",
        title=entry["title"] or f"Re: {parent_title}",
        date=datetime.fromtimestamp(entry["created_at_i"]),
        loaded=True,
        lines=parse_html(content),
        raw_lines=wrap(content),
        children=[hn_parse_entry(child, story_id, entry["title"] or parent_title) for child in entry["children"]],
    )


def hn_search_stories() -> List[Message]:
    hits: List[HNSearchHit] = json.load(open("./tmp/index.json"))["hits"]
    return [hn_parse_search_hit(hit) for hit in hits]


def hn_fetch_entry(entry_id: Union[str, int]) -> HNEntry:
    resp = fetch(f"http://hn.algolia.com/api/v1/items/{entry_id}")
    return json.loads(resp)


def main(screen: "curses._CursesWindow") -> None:
    app = app_init(screen)

    while True:
        app_render(app)
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)


if __name__ == "__main__":
    app_init_logging()
    curses.wrapper(main)
