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
import os
import re
import sqlite3
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from textwrap import wrap
from typing import Any, Dict, Generator, List, Optional, TypedDict, TypeVar, Union

T = TypeVar("T")

QUOTE_REX = re.compile(r"^(> ?)+")


class Colors:
    menu: int

    def __init__(self):
        idx = 0

        def init_pair(fg: int, bg: int) -> int:
            nonlocal idx
            idx = idx + 1
            curses.init_pair(idx, fg, bg)
            return curses.color_pair(idx)

        self.default = init_pair(curses.COLOR_WHITE, -1)
        self.menu = init_pair(curses.COLOR_GREEN, curses.COLOR_BLUE)
        self.date = init_pair(curses.COLOR_CYAN, -1)
        self.author = init_pair(curses.COLOR_YELLOW, -1)
        self.subject = init_pair(curses.COLOR_GREEN, -1)
        self.starred_subject = init_pair(curses.COLOR_CYAN, -1)
        self.tree = init_pair(curses.COLOR_RED, -1)
        self.quote = init_pair(curses.COLOR_YELLOW, -1)
        self.nested_quote = init_pair(curses.COLOR_BLUE, -1)
        self.code = init_pair(curses.COLOR_GREEN, -1)
        self.url = init_pair(curses.COLOR_MAGENTA, -1)
        self.cursor = init_pair(curses.COLOR_BLACK, curses.COLOR_CYAN)


@dataclass
class MessageFlags:
    read: bool = False
    starred: bool = False


@dataclass
class Message:
    msg_id: str
    story_id: str
    content_location: str
    date: datetime
    author: str
    title: str
    body: Optional[str] = None
    lines: List[str] = field(default_factory=list)
    children: List["Message"] = field(default_factory=list)
    flags: MessageFlags = field(default_factory=MessageFlags)
    read_comments: int = 0
    total_comments: int = 0
    index_position: int = 0
    index_tree: str = ""


@dataclass(frozen=True)
class Layout:
    lines: int = 0
    cols: int = 0
    top_menu_row: int = 0
    index_start: int = 0
    index_height: int = 0
    middle_menu_row: Optional[int] = None
    pager_start: Optional[int] = None
    pager_height: Optional[int] = None
    bottom_menu_row: int = 0
    flash_menu_row: int = 0


@dataclass
class AppState:
    screen: "curses._CursesWindow"
    colors: Colors
    db: sqlite3.Connection
    messages: List[Message] = field(default_factory=list)
    messages_by_id: Dict[str, Message] = field(default_factory=dict)
    selected_message: Optional[Message] = None
    layout: Layout = field(default_factory=Layout)
    pager_visible: bool = False
    pager_offset: int = 0
    raw_mode: bool = False
    flash: Optional[str] = None


class HNSearchHit(TypedDict):
    objectID: int
    author: str
    title: str
    created_at_i: int
    story_text: Optional[str]
    url: Optional[str]
    num_comments: int


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


def parse_html(html: str) -> str:
    parser = HTMLParser()
    parser.feed(html)
    parser.close()
    return parser.text.strip()


def wrap_paragraph(text: str) -> List[str]:
    # Preserve empty lines
    if len(text) == 0:
        return [""]

    # Preserve quotation symbols in subsequent lines
    match = QUOTE_REX.match(text)
    quote_symbols = match[0] if match else ""
    return wrap(text, subsequent_indent=quote_symbols, break_on_hyphens=False, break_long_words=False)


def fetch(url: str) -> str:
    logging.debug(f"Fetching '{url}'...")

    headers = {"User-Agent": "retronews"}
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req).read().decode()

    return resp


def list_get(lst: List[T], index: int, default: Optional[T] = None) -> Optional[T]:
    return lst[index] if 0 <= index < len(lst) else default


def cmd_quit(app: AppState):
    app.db.close()
    sys.exit(0)


def cmd_up(app: AppState) -> None:
    if app.pager_visible:
        app.pager_offset = max(0, app.pager_offset - 1)
    else:
        cmd_index_up(app)


def cmd_down(app: AppState) -> None:
    if app.pager_visible:
        app.pager_offset += 1
    else:
        cmd_index_down(app)


def cmd_index_up(app: AppState) -> None:
    pos = app.selected_message.index_position - 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_index_down(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_page_up(app: AppState) -> None:
    pos = app.selected_message.index_position - app.layout.index_height if app.selected_message else 0
    pos = max(pos, 0)
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_page_down(app: AppState) -> None:
    pos = app.selected_message.index_position + app.layout.index_height if app.selected_message else 0
    pos = min(pos, len(app.messages) - 1)
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_open(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    if msg.msg_id == msg.story_id:
        app_open_story(app, msg)
    else:
        app_select_message(app, msg, show_pager=True)


def cmd_close(app: AppState) -> None:
    if app.pager_visible:
        app.pager_visible = False
    else:
        app_close_story(app)


def cmd_star(app: AppState) -> None:
    if (msg := app.selected_message) is not None:
        msg.flags.starred = not msg.flags.starred
        db_save_message(app.db, msg)
        cmd_index_down(app)


def cmd_toggle_raw_mode(app: AppState) -> None:
    app.raw_mode = not app.raw_mode
    app_select_message(app, app.selected_message)


def cmd_unknown(app: AppState) -> None:
    app.flash = "Unknown key"


KEY_BINDINGS = {
    ord("q"): cmd_quit,
    ord("\n"): cmd_open,
    ord(" "): cmd_open,
    ord("x"): cmd_close,
    ord("s"): cmd_star,
    ord("r"): cmd_toggle_raw_mode,
    ord("k"): cmd_up,
    ord("j"): cmd_down,
    ord("p"): cmd_index_up,
    ord("n"): cmd_index_down,
    curses.KEY_UP: cmd_index_up,
    curses.KEY_DOWN: cmd_index_down,
    curses.KEY_PPAGE: cmd_page_up,
    curses.KEY_NPAGE: cmd_page_down,
}


def db_init() -> sqlite3.Connection:
    path = os.path.expanduser("~/.retronews.db")
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT NOT NULL PRIMARY KEY,
            story_id TEXT NOT NULL,
            flags JSON NOT NULL)
    """

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(create_table_sql)
    db.commit()

    return db


def db_save_message(db: sqlite3.Connection, message: Message) -> None:
    sql = """INSERT OR REPLACE INTO messages (id, story_id, flags) VALUES (?, ?, ?)"""
    db.execute(sql, (message.msg_id, message.story_id, json.dumps(asdict(message.flags))))
    db.commit()


def db_load_message_flags(db: sqlite3.Connection, messages_by_id: Dict[str, Message]) -> None:
    message_ids = list(messages_by_id.keys())
    sql = f"SELECT * FROM messages WHERE id IN ({','.join('?' for _ in message_ids)})"

    for row in db.execute(sql, message_ids):
        flags = json.loads(row["flags"])
        messages_by_id[row["id"]].flags = MessageFlags(**flags)


def db_load_read_comments(db: sqlite3.Connection, messages_by_id: Dict[str, Message]) -> None:
    stories_by_id = {msg.msg_id: msg for msg in messages_by_id.values() if msg.msg_id == msg.story_id}
    story_ids = list(stories_by_id.keys())

    sql = f"""
        SELECT story_id, COUNT(*) AS count
        FROM messages
        WHERE story_id IN ({','.join('?' for _ in story_ids)}) AND JSON_EXTRACT(flags, '$.read')
        GROUP BY story_id
    """

    for row in db.execute(sql, story_ids):
        stories_by_id[row["story_id"]].read_comments = row["count"]


def app_select_message(app: AppState, message: Optional[Message], show_pager: bool = False) -> None:
    app.selected_message = message

    if message is None or message.body is None:
        app.pager_visible = False
        return

    message.lines = wrap(message.body) if app.raw_mode else msg_build_lines(message)

    if show_pager:
        app.pager_visible = True

    if app.pager_visible:
        message.flags.read = True
        db_save_message(app.db, message)
        db_load_read_comments(app.db, {message.story_id: app.messages_by_id[message.story_id]})

    app.pager_offset = 0


def app_load_messages(
    app: AppState, messages: List[Message], selected_message_id: Optional[str] = None, show_pager: bool = False
) -> None:
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
    app.messages_by_id = {msg.msg_id: msg for msg in messages}

    db_load_message_flags(app.db, app.messages_by_id)
    db_load_read_comments(app.db, app.messages_by_id)

    app_select_message(app, selected_message, show_pager)


def app_close_story(app: AppState) -> None:
    selected_story_id = app.selected_message.story_id if app.selected_message else None
    filtered_messages = [msg for msg in app.messages if msg.msg_id == msg.story_id]

    for msg in filtered_messages:
        msg.children = []

    app_load_messages(app, filtered_messages, selected_message_id=selected_story_id)


def app_open_story(app: AppState, story_message: Message) -> None:
    source_id = story_message.story_id.split("@")[0]

    app.flash = f"Fetching story '{story_message.story_id}'..."
    app_render(app)

    new_story_message = hn_parse_entry(hn_fetch_entry(source_id))

    app_close_story(app)

    index_pos = story_message.index_position
    story_messages = list(app_flatten_story(new_story_message, prefix="", is_last_child=False, is_top=True))
    new_story_message.total_comments = len(story_messages)
    messages = app.messages[:index_pos] + story_messages + app.messages[index_pos + 1 :]  # noqa: E203

    app_load_messages(app, messages, selected_message_id=story_message.msg_id, show_pager=True)


def app_flatten_story(msg: Message, prefix, is_last_child, is_top=False) -> Generator[Message, None, None]:
    msg.index_tree = "" if is_top else f"{prefix}{'└─' if is_last_child else '├─'}> "
    yield msg

    children_count = len(msg.children)

    for i, child_node in enumerate(msg.children):
        tree_prefix = "" if is_top else f"{prefix}{'  ' if is_last_child else '│ '}"
        for child in app_flatten_story(child_node, tree_prefix, i == children_count - 1):
            yield child


def app_get_pager_line_attr(app: AppState, line: str) -> int:
    if line.startswith("Content-Location: "):
        return app.colors.tree
    elif line.startswith("Date: "):
        return app.colors.date
    elif line.startswith("From: "):
        return app.colors.author
    elif line.startswith("Subject: "):
        return app.colors.subject
    elif line.startswith(">>") or line.startswith("> >"):
        return app.colors.nested_quote
    elif line.startswith(">"):
        return app.colors.quote
    elif line.startswith("  "):
        return app.colors.code
    else:
        return 0


def app_render_pager_line(app: AppState, row: int, line: str) -> None:
    line_attr = app_get_pager_line_attr(app, line)

    app.screen.move(row, 0)

    for word in line.split(" "):
        is_url = word.startswith("http://") or word.startswith("https://")
        word_attr = app.colors.url if is_url and line_attr == 0 else line_attr
        app.screen.addstr(word, word_attr)
        app.screen.addstr(" ")


def app_render_pager(app: AppState) -> None:
    message = app.selected_message
    start = app.layout.pager_start
    height = app.layout.pager_height

    if message is None or start is None or height is None:
        return

    for i in range(height):
        line = list_get(message.lines, i + app.pager_offset) or ""
        app_render_pager_line(app, i + start, line)


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    cols = app.layout.cols
    date = message.date.strftime("%Y-%m-%d %H:%M")
    author = message.author[:10].ljust(10)

    app.screen.insstr(row, 0, f"[{date}]  [{author}]  {message.index_tree}{message.title}")

    if message == app.selected_message:
        app.screen.chgat(row, 0, cols, app.colors.cursor)
    else:
        is_read = message.flags.read and (len(message.children) > 0 or message.read_comments >= message.total_comments)
        subject_attr = app.colors.starred_subject if message.flags.starred else app.colors.default
        subject_attr = subject_attr if is_read else subject_attr | curses.A_BOLD

        app.screen.chgat(row, 1, 16, app.colors.date)
        app.screen.chgat(row, 21, 10, app.colors.author)
        app.screen.chgat(row, 34, len(message.index_tree), app.colors.tree)
        app.screen.chgat(row, 34 + len(message.index_tree), cols - 34 - len(message.index_tree), subject_attr)


def app_render_index(app: AppState) -> None:
    height = app.layout.index_height

    offset = app.selected_message.index_position - height // 2 if app.selected_message else 0
    offset = min(offset, len(app.messages) - height)
    offset = max(offset, 0)

    rows_to_render = min(height, len(app.messages) - offset)

    for i in range(rows_to_render):
        app_render_index_row(app, app.layout.index_start + i, app.messages[i + offset])


def app_render_menus(app: AppState) -> None:
    lt = app.layout

    top_menu = "q:Quit  n:Next  p:Prev  j:Down  k:Up  <space>:Open  x:Close  s:Star  r:Raw"

    app.screen.insstr(lt.top_menu_row, 0, top_menu[: lt.cols].ljust(lt.cols), app.colors.menu | curses.A_BOLD)

    if lt.middle_menu_row is not None:
        app.screen.insstr(lt.middle_menu_row, 0, "middle menu".ljust(lt.cols), app.colors.menu | curses.A_BOLD)

    app.screen.insstr(lt.bottom_menu_row, 0, "bottom menu".ljust(lt.cols), app.colors.menu | curses.A_BOLD)
    app.screen.insstr(lt.flash_menu_row, 0, app.flash or "")


def app_render(app: AppState) -> None:
    app.layout = app_compute_layout(app)
    app.screen.erase()

    app_render_menus(app)
    app_render_index(app)

    if app.pager_visible:
        app_render_pager(app)

    app.screen.refresh()
    app.flash = ""


def app_compute_layout(app: AppState) -> Layout:
    (lines, cols) = app.screen.getmaxyx()

    index_start = 1
    max_index_height = lines - 3
    index_height = (max_index_height // 3) if app.pager_visible else max_index_height

    if app.pager_visible:
        middle_menu_row = index_start + index_height
        pager_start = index_start + index_height + 1
        pager_height = lines - pager_start - 2
    else:
        middle_menu_row = None
        pager_start = None
        pager_height = None

    return Layout(
        lines=lines,
        cols=cols,
        top_menu_row=0,
        index_start=index_start,
        index_height=index_height,
        middle_menu_row=middle_menu_row,
        pager_start=pager_start,
        pager_height=pager_height,
        bottom_menu_row=lines - 2,
        flash_menu_row=lines - 1,
    )


def app_init_logging() -> None:
    format = "%(asctime)s %(levelname)s: %(message)s"
    stream = open("tmp/retronews.log", "a")
    logging.basicConfig(format=format, level="DEBUG", stream=stream)
    logging.debug("Session started")


def app_init(screen: "curses._CursesWindow") -> AppState:
    db = db_init()

    curses.curs_set(0)
    curses.use_default_colors()

    app = AppState(screen=screen, colors=Colors(), db=db)
    app_load_messages(app, hn_search_stories())

    return app


def msg_build_lines(msg: Message) -> List[str]:
    lines = [
        f"Content-Location: {msg.content_location}",
        f"Date: {msg.date.strftime('%Y-%m-%d %H:%M')}",
        f"From: {msg.author}",
        f"Subject: {msg.title}",
        "",
    ]

    text = parse_html(msg.body or "")

    for p in text.split("\n"):
        lines += wrap_paragraph(p)

    return lines


def hn_parse_search_hit(hit: HNSearchHit) -> Message:
    return Message(
        msg_id=f"{hit['objectID']}@hn",
        story_id=f"{hit['objectID']}@hn",
        content_location=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        date=datetime.fromtimestamp(hit["created_at_i"]),
        author=hit["author"],
        title=hit["title"],
        total_comments=hit["num_comments"] or 0,
    )


def hn_parse_entry(entry: HNEntry, story_id: str = "", parent_title: str = "") -> Message:
    story_id = story_id or str(entry["id"])

    body = f"<p>{entry['url']}</p>" if entry["url"] else ""
    body = f"{body}{entry['text']}" if entry["text"] else body

    return Message(
        msg_id=f"{entry['id']}@hn",
        story_id=f"{story_id}@hn",
        content_location=f"https://news.ycombinator.com/item?id={entry['id']}",
        date=datetime.fromtimestamp(entry["created_at_i"]),
        author=entry["author"] or "unknown",
        title=entry["title"] or f"Re: {parent_title}",
        body=body,
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
