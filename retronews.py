#!/usr/bin/env python3
#
# Copyright (c) luke8086
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#

import curses
import dataclasses
import html.parser
import json
import logging
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime
from functools import partial
from textwrap import wrap
from typing import Any, Callable, Generator, Optional, TypedDict, TypeVar, Union

KEY_BINDINGS = {
    ord("q"): lambda app: cmd_quit(app),
    ord("?"): lambda app: cmd_help(app),
    ord("\n"): lambda app: cmd_open(app),
    ord(" "): lambda app: cmd_open(app),
    ord("x"): lambda app: cmd_close(app),
    ord("s"): lambda app: cmd_star(app),
    ord("S"): lambda app: cmd_star_thread(app),
    ord("r"): lambda app: cmd_toggle_raw_mode(app),
    ord("k"): lambda app: cmd_up(app),
    ord("j"): lambda app: cmd_down(app),
    ord("p"): lambda app: cmd_index_up(app),
    ord("n"): lambda app: cmd_index_down(app),
    ord("N"): lambda app: cmd_index_next_unread(app),
    ord("<"): lambda app: cmd_load_prev_page(app),
    ord(">"): lambda app: cmd_load_next_page(app),
    curses.KEY_UP: lambda app: cmd_index_up(app),
    curses.KEY_DOWN: lambda app: cmd_index_down(app),
    curses.KEY_PPAGE: lambda app: cmd_page_up(app),
    curses.KEY_NPAGE: lambda app: cmd_page_down(app),
} | {ord(str(i)): lambda app, i=i: cmd_load_tab(app, i) for i in range(1, 10)}

HELP_MENU = "q:Quit  ?:Help  p:Prev  n:Next  N:Next-Unread  j:Down  k:Up  x:Close  s:Star"

HELP_SCREEN = """\
Available commands:

  q                       Quit retronews
  ?                       Show this help message
  UP, DOWN                Go up / down by one message / pager line
  PG UP, PG DOWN          Gp up / down by one page of messages / pager lines
  p, n                    Go to previous / next message
  N                       Go to next unread message
  RETURN, SPACE           Open selected message
  x                       Close current message / thread
  1 - 4                   Change group
  <, >                    Go to previous / next page
  k, j                    Scroll pager up / down by one line
  s                       Star / unstar selected message
  S                       Star / unstar current thread

See https://github.com/luke8086/retronews for more information.

Press any key to continue...
"""

REQUEST_TIMEOUT = 10

T = TypeVar("T")

QUOTE_REX = re.compile(r"^(> ?)+")


class Colors:
    _last_index: int = 0

    def __init__(self):
        pair = self._add_pair

        self.author = pair(curses.COLOR_YELLOW, -1)
        self.code = pair(curses.COLOR_GREEN, -1)
        self.cursor = pair(curses.COLOR_BLACK, curses.COLOR_CYAN)
        self.date = pair(curses.COLOR_CYAN, -1)
        self.default = pair(curses.COLOR_WHITE, -1)
        self.empty_pager_line = pair(curses.COLOR_GREEN, -1)
        self.menu = pair(curses.COLOR_GREEN, curses.COLOR_BLUE)
        self.menu_active = pair(curses.COLOR_YELLOW, curses.COLOR_BLUE)
        self.nested_quote = pair(curses.COLOR_BLUE, -1)
        self.quote = pair(curses.COLOR_YELLOW, -1)
        self.starred_subject = pair(curses.COLOR_CYAN, -1)
        self.subject = pair(curses.COLOR_GREEN, -1)
        self.tree = pair(curses.COLOR_RED, -1)
        self.unread_comments = pair(curses.COLOR_GREEN, -1)
        self.url = pair(curses.COLOR_MAGENTA, -1)

    def _add_pair(self, fg: int, bg: int) -> int:
        self._last_index = self._last_index + 1
        curses.init_pair(self._last_index, fg, bg)
        return curses.color_pair(self._last_index)


@dataclasses.dataclass(frozen=True)
class Group:
    provider: str
    name: str
    page: int = 1
    label: str = ""


GROUP_TABS: list[Group] = [
    Group(provider="hn", name="news", label="Front Page"),
    Group(provider="hn-new", name="", label="New"),
    Group(provider="hn", name="ask", label="Ask HN"),
    Group(provider="hn", name="show", label="Show HN"),
]


@dataclasses.dataclass
class MessageFlags:
    read: bool = False
    starred: bool = False


@dataclasses.dataclass
class Message:
    msg_id: str
    thread_id: str
    content_location: str
    date: datetime
    author: str
    title: str
    body: Optional[str] = None
    lines: list[str] = dataclasses.field(default_factory=list)
    children: list["Message"] = dataclasses.field(default_factory=list)
    flags: MessageFlags = dataclasses.field(default_factory=MessageFlags)
    read_comments: int = 0
    total_comments: int = 0
    index_position: int = 0
    index_tree: str = ""


@dataclasses.dataclass
class Layout:
    lines: int = 0
    cols: int = 0
    top_menu_row: int = 0
    index_start: int = 1
    index_height: int = 0
    middle_menu_row: Optional[int] = None
    pager_start: Optional[int] = None
    pager_height: Optional[int] = None
    bottom_menu_row: int = 0
    flash_menu_row: int = 0


@dataclasses.dataclass
class AppState:
    screen: "curses._CursesWindow"
    colors: Colors
    db: sqlite3.Connection
    group: Group
    messages: list[Message] = dataclasses.field(default_factory=list)
    messages_by_id: dict[str, Message] = dataclasses.field(default_factory=dict)
    selected_message: Optional[Message] = None
    layout: Layout = dataclasses.field(default_factory=Layout)
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
    children: list[Any]
    created_at_i: int
    id: int
    parent_id: Optional[int]
    text: Optional[str]
    title: Optional[str]
    url: Optional[str]


class HTMLParser(html.parser.HTMLParser):
    text: str = ""
    current_link: Optional[str] = None

    def handle_data(self, data):
        if self.current_link is None or self.current_link == data:
            # Data is not a link or it's identical to the link
            self.text += data.replace("\n", " ")
        elif data.endswith("...") and self.current_link.startswith(data[:-3]):
            # Replace HN-shortened URL with the full one
            self.text += self.current_link
        else:
            # Insert both the text and the full link
            self.text += f"{data} ({self.current_link})"

    def handle_starttag(self, tag, attr):
        if tag == "a":
            self.current_link = dict(attr).get("href")
        elif tag == "i":
            self.text += "*"

    def handle_endtag(self, tag):
        if tag == "br":
            self.text += "\n"
        elif tag == "p":
            self.text += "\n\n"
        elif tag == "a":
            self.current_link = None
        elif tag == "i":
            self.text += "*"


def parse_html(html: str) -> str:
    parser = HTMLParser()
    parser.feed(html)
    parser.close()
    return parser.text.strip()


def wrap_paragraph(text: str) -> list[str]:
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
    resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT).read().decode()

    return resp


def list_get(lst: list[T], index: int, default: Optional[T] = None) -> Optional[T]:
    return lst[index] if 0 <= index < len(lst) else default


def cmd_quit(app: AppState):
    app.db.close()
    sys.exit(0)


def cmd_help(app: AppState):
    app_show_help_screen(app)


def cmd_up(app: AppState) -> None:
    cmd_pager_up(app) if app.pager_visible else cmd_index_up(app)


def cmd_down(app: AppState) -> None:
    cmd_pager_down(app) if app.pager_visible else cmd_index_down(app)


def cmd_index_up(app: AppState) -> None:
    pos = app.selected_message.index_position - 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_index_down(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_index_next_unread(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    message = next((msg for msg in app.messages[pos:] if not msg_is_read(msg)), None)
    if message is not None:
        app_select_message(app, message)


def cmd_pager_up(app: AppState) -> None:
    app.pager_offset = max(0, app.pager_offset - 1)


def cmd_pager_down(app: AppState) -> None:
    if app.selected_message is not None and app.layout.pager_height is not None:
        app.pager_offset = min(app.pager_offset + 1, max(0, len(app.selected_message.lines) - app.layout.pager_height))


def cmd_page_up(app: AppState) -> None:
    cmd_pager_page_up(app) if app.pager_visible else cmd_index_page_up(app)


def cmd_page_down(app: AppState) -> None:
    cmd_pager_page_down(app) if app.pager_visible else cmd_index_page_down(app)


def cmd_index_page_up(app: AppState) -> None:
    pos = app.selected_message.index_position - app.layout.index_height if app.selected_message else 0
    pos = max(pos, 0)
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_index_page_down(app: AppState) -> None:
    pos = app.selected_message.index_position + app.layout.index_height if app.selected_message else 0
    pos = min(pos, len(app.messages) - 1)
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_pager_page_up(app: AppState) -> None:
    if app.layout.pager_height is not None:
        app.pager_offset = max(0, app.pager_offset - app.layout.pager_height)


def cmd_pager_page_down(app: AppState) -> None:
    if (message := app.selected_message) is not None and (pager_height := app.layout.pager_height) is not None:
        app.pager_offset = min(app.pager_offset + pager_height, max(0, len(message.lines) - pager_height))


def cmd_load_tab(app: AppState, tab: int) -> None:
    if group := list_get(GROUP_TABS, tab - 1):
        cmd_load_group(app, group)


def cmd_load_group(app: AppState, group: Group) -> None:
    app.group = group
    app_fetch_threads(app)


def cmd_load_prev_page(app: AppState) -> None:
    app.group = group_advance_page(app.group, -1)
    app_fetch_threads(app)


def cmd_load_next_page(app: AppState) -> None:
    app.group = group_advance_page(app.group, 1)
    app_fetch_threads(app)


def cmd_open(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    if msg_is_thread(msg):
        app_open_thread(app, msg)
    else:
        app_select_message(app, msg, show_pager=True)


def cmd_close(app: AppState) -> None:
    if app.pager_visible:
        app.pager_visible = False
    else:
        app_close_thread(app)


def cmd_star(app: AppState) -> None:
    if (msg := app.selected_message) is not None:
        msg.flags.starred = not msg.flags.starred
        db_save_message(app.db, msg)
        cmd_index_down(app)


def cmd_star_thread(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    if (thread_msg := app.messages_by_id.get(msg.thread_id)) is None:
        return

    thread_msg.flags.starred = not thread_msg.flags.starred
    db_save_message(app.db, thread_msg)
    cmd_index_down(app)


def cmd_toggle_raw_mode(app: AppState) -> None:
    app.raw_mode = not app.raw_mode
    app_select_message(app, app.selected_message)


def cmd_unknown(app: AppState) -> None:
    app.flash = "Unknown key"


def db_init() -> sqlite3.Connection:
    path = os.path.expanduser("~/.retronews.db")
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT NOT NULL PRIMARY KEY,
            thread_id TEXT NOT NULL,
            flags JSON NOT NULL);

        CREATE INDEX IF NOT EXISTS messages_thread_id ON messages (thread_id);
    """

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(create_table_sql)
    db.commit()

    return db


def db_save_message(db: sqlite3.Connection, message: Message) -> None:
    sql = """INSERT OR REPLACE INTO messages (id, thread_id, flags) VALUES (?, ?, ?)"""
    flags_json = json.dumps(dataclasses.asdict(message.flags))
    db.execute(sql, (message.msg_id, message.thread_id, flags_json))
    db.commit()


def db_load_message_flags(db: sqlite3.Connection, messages_by_id: dict[str, Message]) -> None:
    message_ids = list(messages_by_id.keys())
    sql = f"SELECT * FROM messages WHERE id IN ({','.join('?' for _ in message_ids)})"

    for row in db.execute(sql, message_ids):
        flags = json.loads(row["flags"])
        messages_by_id[row["id"]].flags = MessageFlags(**flags)


def db_load_read_comments(db: sqlite3.Connection, messages_by_id: dict[str, Message]) -> None:
    threads_by_id = {msg.msg_id: msg for msg in messages_by_id.values() if msg_is_thread(msg)}
    thread_ids = list(threads_by_id.keys())

    sql = f"""
        SELECT thread_id, COUNT(*) AS count
        FROM messages
        WHERE thread_id IN ({','.join('?' for _ in thread_ids)}) AND JSON_EXTRACT(flags, '$.read')
        GROUP BY thread_id
    """

    for row in db.execute(sql, thread_ids):
        threads_by_id[row["thread_id"]].read_comments = row["count"]


def app_safe_run(app: AppState, fn: Callable[[], T], flash: Optional[str]) -> Optional[T]:
    if flash is not None:
        app_show_flash(app, flash)

    ret = None

    try:
        ret = fn()
    except Exception as e:
        app_show_flash(app, f"Error: {e}")
    else:
        if flash is not None:
            app_show_flash(app, None)

    return ret


def app_show_help_screen(app: AppState) -> None:
    app.screen.erase()
    app.screen.addstr(0, 0, HELP_SCREEN)
    app.screen.refresh()
    app.screen.getch()


def app_show_flash(app: AppState, flash: Optional[str]) -> None:
    app.flash = flash
    app_render(app)


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
        db_load_read_comments(app.db, {message.thread_id: app.messages_by_id[message.thread_id]})

    app.pager_offset = 0


def app_load_messages(
    app: AppState, messages: list[Message], selected_message_id: Optional[str] = None, show_pager: bool = False
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


def app_fetch_threads(app: AppState) -> None:
    fn = partial(group_search_threads, app.group)
    flash = f"Fetching stories from '{app.group.label}' (page {app.group.page})..."

    if (messages := app_safe_run(app, fn, flash=flash)) is not None:
        app_load_messages(app, messages)


def app_close_thread(app: AppState) -> None:
    selected_thread_id = app.selected_message.thread_id if app.selected_message else None
    filtered_messages = [msg_unload(msg) for msg in app.messages if msg_is_thread(msg)]

    app_load_messages(app, filtered_messages, selected_message_id=selected_thread_id)


def app_open_thread(app: AppState, thread_message: Message) -> None:
    fn = partial(group_fetch_thread, thread_message.thread_id)
    flash = f"Fetching thread '{thread_message.thread_id}'..."

    if (new_thread_message := app_safe_run(app, fn, flash=flash)) is None:
        return

    app_close_thread(app)

    index_pos = thread_message.index_position
    thread_messages = list(app_flatten_thread(new_thread_message, prefix="", is_last_child=False, is_top=True))
    new_thread_message.total_comments = len(thread_messages)
    messages = app.messages[:index_pos] + thread_messages + app.messages[index_pos + 1 :]  # noqa: E203

    app_load_messages(app, messages, selected_message_id=thread_message.msg_id, show_pager=True)


def app_flatten_thread(msg: Message, prefix, is_last_child, is_top=False) -> Generator[Message, None, None]:
    msg.index_tree = "" if is_top else f"{prefix}{'└─' if is_last_child else '├─'}> "
    yield msg

    children_count = len(msg.children)

    for i, child_node in enumerate(msg.children):
        tree_prefix = "" if is_top else f"{prefix}{'  ' if is_last_child else '│ '}"
        for child in app_flatten_thread(child_node, tree_prefix, i == children_count - 1):
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
    elif line == "~":
        return app.colors.empty_pager_line
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
        line = list_get(message.lines, i + app.pager_offset)
        line = "~" if line is None else line
        app_render_pager_line(app, i + start, line)


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    cols = app.layout.cols
    date = message.date.strftime("%Y-%m-%d %H:%M")
    author = message.author[:10].ljust(10)

    is_response = message.title.startswith("Re:") and not msg_is_thread(message)
    hide_title = is_response and row > app.layout.index_start and not message.flags.starred
    title = "" if hide_title else message.title

    unread = (
        str(max(min(message.total_comments - message.read_comments, 9999), 0)).rjust(4)
        if msg_is_thread(message)
        else "    "
    )

    app.screen.insstr(row, 0, f"[{date}]  [{author}]  [{unread}]  {message.index_tree}{title}")

    if message == app.selected_message:
        app.screen.chgat(row, 0, cols, app.colors.cursor)
    else:
        is_read = msg_is_read(message)
        read_attr = 0 if is_read else curses.A_BOLD
        subject_attr = app.colors.starred_subject if message.flags.starred else app.colors.default
        subject_attr = subject_attr | read_attr

        app.screen.chgat(row, 1, 16, app.colors.date | read_attr)
        app.screen.chgat(row, 21, 10, app.colors.author | read_attr)
        app.screen.chgat(row, 35, 4, app.colors.unread_comments | read_attr)
        app.screen.chgat(row, 42, len(message.index_tree), app.colors.tree)
        app.screen.chgat(row, 42 + len(message.index_tree), cols - 42 - len(message.index_tree), subject_attr)


def app_render_index(app: AppState) -> None:
    height = app.layout.index_height

    offset = app.selected_message.index_position - height // 2 if app.selected_message else 0
    offset = min(offset, len(app.messages) - height)
    offset = max(offset, 0)

    rows_to_render = min(height, len(app.messages) - offset)

    for i in range(rows_to_render):
        app_render_index_row(app, app.layout.index_start + i, app.messages[i + offset])


def app_render_top_menu(app: AppState) -> None:
    lt = app.layout
    cols = lt.cols
    app.screen.insstr(lt.top_menu_row, 0, HELP_MENU[:cols].ljust(cols), app.colors.menu | curses.A_BOLD)


def app_render_middle_menu(app: AppState) -> None:
    if (row := app.layout.middle_menu_row) is None:
        return

    if (message := app.selected_message) is None:
        return

    if (thread_message := app.messages_by_id.get(message.thread_id)) is None:
        return

    cols = app.layout.cols
    total = thread_message.total_comments
    unread = total - thread_message.read_comments

    text = f"--({unread}/{total} unread)--"[:cols].ljust(cols, "-")

    app.screen.insstr(row, 0, text, app.colors.menu | curses.A_BOLD)


def app_render_bottom_menu(app: AppState) -> None:
    lt = app.layout

    app.screen.chgat(lt.bottom_menu_row, 0, lt.cols, app.colors.menu)
    app.screen.move(lt.bottom_menu_row, 0)

    for (i, group) in enumerate(GROUP_TABS):
        attr = app.colors.menu | curses.A_BOLD
        text = f"{i+1}:{group.label}"

        if group.provider == app.group.provider and group.name == app.group.name:
            attr = app.colors.menu_active | curses.A_BOLD
            text = f"{text} ({app.group.page})"

        app.screen.addstr(f"{text}  ", attr)


def app_update_layout(app: AppState) -> None:
    lt = app.layout

    (lt.lines, lt.cols) = app.screen.getmaxyx()

    max_index_height = lt.lines - 3
    lt.index_height = (max_index_height // 3) if app.pager_visible else max_index_height

    lt.middle_menu_row = lt.index_start + lt.index_height if app.pager_visible else None
    lt.pager_start = lt.index_start + lt.index_height + 1 if app.pager_visible else None
    lt.pager_height = lt.lines - lt.pager_start - 2 if lt.pager_start is not None else None

    lt.bottom_menu_row = lt.lines - 2
    lt.flash_menu_row = lt.lines - 1


def app_render(app: AppState) -> None:
    app_update_layout(app)
    app.screen.erase()

    app_render_index(app)

    if app.pager_visible:
        app_render_pager(app)

    app_render_top_menu(app)
    app_render_middle_menu(app)
    app_render_bottom_menu(app)
    app.screen.insstr(app.layout.flash_menu_row, 0, app.flash or "")

    app.screen.refresh()


def app_init_logging() -> None:
    format = "%(asctime)s %(levelname)s: %(message)s"
    stream = open("tmp/retronews.log", "a")
    logging.basicConfig(format=format, level="DEBUG", stream=stream)
    logging.debug("Session started")


def app_init(screen: "curses._CursesWindow") -> AppState:
    db = db_init()

    curses.curs_set(0)
    curses.use_default_colors()

    group = GROUP_TABS[0]

    app = AppState(screen=screen, colors=Colors(), db=db, group=group)
    app_fetch_threads(app)

    return app


def msg_build_lines(msg: Message) -> list[str]:
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


def msg_unload(msg: Message) -> Message:
    msg.children = []
    msg.body = None
    return msg


def msg_is_loaded(msg: Message) -> bool:
    return msg.body is not None


def msg_is_thread(msg: Message) -> bool:
    return msg.msg_id == msg.thread_id


def msg_is_read(msg: Message) -> bool:
    # If the message is an unloaded thread, check if all comments are read
    if msg_is_thread(msg) and not msg_is_loaded(msg):
        return msg.read_comments >= msg.total_comments

    return msg.flags.read


def hn_parse_search_hit(hit: HNSearchHit) -> Message:
    return Message(
        msg_id=f"{hit['objectID']}@hn",
        thread_id=f"{hit['objectID']}@hn",
        content_location=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        date=datetime.fromtimestamp(hit["created_at_i"]),
        author=hit["author"],
        title=hit["title"],
        total_comments=(hit["num_comments"] or 0) + 1,
    )


def hn_parse_entry(entry: HNEntry, thread_id: str = "", parent_title: str = "") -> Message:
    thread_id = thread_id or str(entry["id"])

    body = f"<p>{entry['url']}</p>" if entry["url"] else ""
    body = f"{body}{entry['text']}" if entry["text"] else body

    return Message(
        msg_id=f"{entry['id']}@hn",
        thread_id=f"{thread_id}@hn",
        content_location=f"https://news.ycombinator.com/item?id={entry['id']}",
        date=datetime.fromtimestamp(entry["created_at_i"]),
        author=entry["author"] or "unknown",
        title=entry["title"] or f"Re: {parent_title}",
        body=body,
        children=[hn_parse_entry(child, thread_id, entry["title"] or parent_title) for child in entry["children"]],
    )


def hn_search_threads(group: str = "news", page: int = 1) -> list[Message]:
    rex = re.compile(r'href="item\?id=(\d+)"')

    html = fetch(f"https://news.ycombinator.com/{group}?p={page}")
    thread_ids = set(match.group(1) for match in rex.finditer(html))

    story_tags = ",".join(f"story_{x}" for x in thread_ids)
    url = f"https://hn.algolia.com/api/v1/search_by_date?hitsPerPage=200&tags=story,({story_tags})"
    hits = json.loads(fetch(url))["hits"]

    return [hn_parse_search_hit(hit) for hit in hits]


def hn_search_new_threads(_: str, page: int = 1) -> list[Message]:
    url = f"https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=30&page={page}"
    hits = json.loads(fetch(url))["hits"]

    return [hn_parse_search_hit(hit) for hit in hits]


def hn_fetch_thread(entry_id: Union[str, int]) -> Message:
    resp = fetch(f"http://hn.algolia.com/api/v1/items/{entry_id}")
    entry: HNEntry = json.loads(resp)
    return hn_parse_entry(entry)


def group_advance_page(group: Group, offset: int = 1) -> Group:
    return dataclasses.replace(group, page=max(1, group.page + offset))


def group_search_threads(group: Group) -> list[Message]:
    searchers: dict[str, Callable[[str, int], list[Message]]] = {
        "hn": hn_search_threads,
        "hn-new": hn_search_new_threads,
    }
    searcher = searchers[group.provider]
    return searcher(group.name, group.page)


def group_fetch_thread(thread_id: str) -> Message:
    (source_id, provider) = thread_id.split("@")
    return {"hn": hn_fetch_thread}[provider](source_id)


def main(screen: "curses._CursesWindow") -> None:
    app = app_init(screen)

    while True:
        app_render(app)
        app.flash = ""
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)


if __name__ == "__main__":
    app_init_logging()
    curses.wrapper(main)
