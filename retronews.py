#!/usr/bin/env python3
#
# Copyright (c) luke8086
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#

import sys

if sys.version_info < (3, 9):
    sys.stderr.write("Python 3.9 or newer is required.\n")
    sys.exit(1)

import argparse
import curses
import curses.textpad
import dataclasses
import html.parser
import json
import logging
import os
import re
import sqlite3
import unicodedata
import urllib.request
import webbrowser
from datetime import datetime
from functools import partial, reduce
from textwrap import wrap
from typing import (
    Any,
    Callable,
    Generator,
    Literal,
    NewType,
    Optional,
    TypedDict,
    TypeVar,
    Union,
    cast,
    overload,
)

KEY_BINDINGS: dict[int, Callable[["AppState"], None]] = {
    ord("q"): lambda app: cmd_quit(app),
    ord("?"): lambda app: cmd_help(app),
    ord("\n"): lambda app: cmd_open(app),
    ord(" "): lambda app: cmd_open(app),
    ord("o"): lambda app: cmd_show_links(app),
    ord("x"): lambda app: cmd_close(app),
    ord("s"): lambda app: cmd_star(app),
    ord("S"): lambda app: cmd_star_thread(app),
    ord("D"): lambda app: cmd_dump(app),
    ord("r"): lambda app: cmd_toggle_raw_mode(app),
    ord("k"): lambda app: cmd_up(app),
    ord("j"): lambda app: cmd_down(app),
    ord("p"): lambda app: cmd_prev(app),
    ord("n"): lambda app: cmd_next(app),
    ord("N"): lambda app: cmd_next_unread(app),
    ord("P"): lambda app: cmd_parent(app),
    ord(";"): lambda app: cmd_mark_set(app),
    ord(","): lambda app: cmd_mark_jump(app),
    ord("R"): lambda app: cmd_reload_page(app),
    ord("<"): lambda app: cmd_load_prev_page(app),
    ord(">"): lambda app: cmd_load_next_page(app),
    ord("g"): lambda app: cmd_load_page(app),
    curses.KEY_UP: lambda app: cmd_prev(app),
    curses.KEY_DOWN: lambda app: cmd_next(app),
    curses.KEY_PPAGE: lambda app: cmd_page_up(app),
    curses.KEY_NPAGE: lambda app: cmd_page_down(app),
    curses.KEY_RESIZE: lambda app: cmd_resize(app),
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
  P                       Go to parent message
  ; ,                     Set mark, jump to mark & swap (valid within thread)
  RETURN, SPACE           Open selected message
  x                       Close current message / thread
  o                       Select link and open in browser
  1 - 5                   Change group
  R                       Refresh current page
  < >                     Go to previous / next page
  g                       Go to specific page
  k j                     Scroll pager up / down by one line
  s                       Star / unstar selected message
  S                       Star / unstar current thread
  r                       Toggle raw HTML mode

See https://github.com/luke8086/retronews for more information.

Press any key to continue..."""

Color = Literal[
    "author",
    "code",
    "cursor",
    "date",
    "default",
    "empty_pager_line",
    "deleted_message_pager_line",
    "menu",
    "menu_active",
    "nested_quote",
    "quote",
    "starred_subject",
    "header_subject",
    "tree",
    "unread_comments",
    "url",
]

COLORS: dict[Color, tuple[int, int]] = {
    "author": (curses.COLOR_YELLOW, -1),
    "code": (curses.COLOR_GREEN, -1),
    "cursor": (curses.COLOR_BLACK, curses.COLOR_CYAN),
    "date": (curses.COLOR_CYAN, -1),
    "default": (curses.COLOR_WHITE, -1),
    "empty_pager_line": (curses.COLOR_GREEN, -1),
    "deleted_message_pager_line": (curses.COLOR_RED, -1),
    "menu": (curses.COLOR_GREEN, curses.COLOR_BLUE),
    "menu_active": (curses.COLOR_YELLOW, curses.COLOR_BLUE),
    "nested_quote": (curses.COLOR_BLUE, -1),
    "quote": (curses.COLOR_YELLOW, -1),
    "starred_subject": (curses.COLOR_CYAN, -1),
    "header_subject": (curses.COLOR_GREEN, -1),
    "tree": (curses.COLOR_RED, -1),
    "unread_comments": (curses.COLOR_GREEN, -1),
    "url": (curses.COLOR_MAGENTA, -1),
}

REQUEST_TIMEOUT = 10

# Recognize ">text", "> text", ">>text", ">> text", etc.
QUOTE_REX = re.compile(r"^(> ?)+")

# Recognize "[n] link", "[n]: link", "[n] - link", etc.
REFERENCE_REX = re.compile(r"^\[\d+\][ :-]*https?://[^ ]*$")

# Recognize http/https URLs
URL_REX = re.compile(r"(https?://[^\s\),]+)")

# FIXME: Use TypeAlias after migrating to Python 3.10
Window = NewType("Window", "curses._CursesWindow")
DB = NewType("DB", "sqlite3.Connection")

T = TypeVar("T")


class ExitException(Exception):
    code: int
    message: str

    def __init__(self, code: int = 0, message: str = ""):
        self.code = code
        self.message = message

        super().__init__(message)


@dataclasses.dataclass(frozen=True)
class Group:
    label: str
    fetch: Callable[[DB, int], list["Message"]]
    page: int = 1


GROUP_TABS: list[Group] = [
    Group(label="Front HN", fetch=lambda db, page: hn_fetch_threads("news", page)),
    Group(label="New HN", fetch=lambda db, page: hn_fetch_new_threads(page)),
    Group(label="Ask HN", fetch=lambda db, page: hn_fetch_threads("ask", page)),
    Group(label="Show HN", fetch=lambda db, page: hn_fetch_threads("show", page)),
    Group(label="Lobsters", fetch=lambda db, page: lb_fetch_threads("", page)),
    Group(label="Starred", fetch=lambda db, page: group_fetch_starred_threads(db, page)),
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
    author: Optional[str]
    title: str
    body: Optional[str] = None
    lines: list[str] = dataclasses.field(default_factory=list)
    parent: Optional["Message"] = None
    children: list["Message"] = dataclasses.field(default_factory=list)
    flags: MessageFlags = dataclasses.field(default_factory=MessageFlags)
    read_comments: int = 0
    total_comments: int = 0
    index_position: int = 0
    index_tree: str = ""

    @property
    def is_loaded(self) -> bool:
        return self.body is not None

    @property
    def is_read(self) -> bool:
        return self.flags.read

    @property
    def is_shown_as_read(self) -> bool:
        # If the message is an unloaded thread, check if all comments are read
        return self.read_comments >= self.total_comments if self.is_thread and not self.is_loaded else self.is_read

    @property
    def is_thread(self) -> bool:
        return self.msg_id == self.thread_id

    @property
    def is_deleted(self) -> bool:
        return self.author is None


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
    screen: Window
    db: DB
    group: Group
    colors: dict[Color, int] = dataclasses.field(default_factory=dict)
    messages: list[Message] = dataclasses.field(default_factory=list)
    messages_by_id: dict[str, Message] = dataclasses.field(default_factory=dict)
    selected_message: Optional[Message] = None
    marked_message_id: Optional[str] = None
    layout: Layout = dataclasses.field(default_factory=Layout)
    pager_visible: bool = False
    pager_offset: int = 0
    raw_mode: bool = False
    flash: Optional[str] = None


class HNSearchHit(TypedDict):
    objectID: int
    author: Optional[str]
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


class LBUser(TypedDict):
    username: str


class LBThread(TypedDict):
    short_id: str
    short_id_url: str
    created_at: str
    title: str
    url: str
    description: str
    submitter_user: LBUser
    comment_count: int
    comments: Optional[list[Any]]


class LBComment(TypedDict):
    short_id: str
    created_at: str
    url: str
    commenting_user: LBUser
    parent_comment: Optional[str]


HTML_BLOCK_TAGS = set(("root", "p", "pre", "blockquote", "ul", "ol", "li", "hr"))
HTML_INLINE_TAGS = set(("code", "a", "em", "strong", "b", "br"))
HTML_KNOWN_TAGS = HTML_BLOCK_TAGS.union(HTML_INLINE_TAGS)
HTML_AUTOCLOSE_TAGS = set(("hr", "br"))


@dataclasses.dataclass
class HTMLNode:
    tag: str

    parent: Optional["HTMLNode"] = None

    prev_sibling: Optional["HTMLNode"] = None
    next_sibling: Optional["HTMLNode"] = None

    first_child: Optional["HTMLNode"] = None
    last_child: Optional["HTMLNode"] = None

    attrs: dict[str, Optional[str]] = dataclasses.field(default_factory=dict)
    text: str = ""
    pre: bool = False


class HTMLParser(html.parser.HTMLParser):
    root_node: HTMLNode
    current_node: HTMLNode
    pre_level = 0

    def __init__(self):
        super().__init__()

        self.root_node = self.current_node = HTMLNode(tag="root")

    def handle_data(self, data: str) -> None:
        if self.current_node.tag in HTML_AUTOCLOSE_TAGS:
            self.handle_endtag(self.current_node.tag)

        node = HTMLNode(tag="text", text=data, pre=self.pre_level > 0)
        html_node_append(self.current_node, node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.current_node.tag in HTML_AUTOCLOSE_TAGS:
            self.handle_endtag(self.current_node.tag)

        if tag not in HTML_KNOWN_TAGS:
            return

        if tag == "pre":
            self.pre_level += 1

        node = HTMLNode(tag=tag, attrs=dict(attrs), pre=self.pre_level > 0)
        html_node_append(self.current_node, node)
        self.current_node = node

    def handle_endtag(self, tag: str) -> None:
        if tag not in HTML_KNOWN_TAGS:
            return

        if tag == "pre":
            self.pre_level = max(0, self.pre_level - 1)

        while True:
            node = self.current_node

            if node.parent is None:
                break

            self.current_node = node.parent

            if node.tag == tag:
                break


def text_wrap(text: str, width=70) -> str:
    if len(text) == 0:
        # Preserve empty lines
        return ""

    if text.startswith("  "):
        # Preserve code indentation
        return text

    if REFERENCE_REX.match(text):
        # Keep reference numbers with long links in the same line
        return text

    indent = ""

    if (match := QUOTE_REX.match(text)) is not None:
        # Preserve quotation symbols in subsequent lines
        indent = match[0]

    lines = wrap(text, width, subsequent_indent=indent, break_on_hyphens=False, break_long_words=False)
    lines = [line.rstrip() for line in lines]

    return "\n".join(lines)


def text_indent(text: str, initial: str, recurring: Optional[str] = None) -> str:
    if recurring is None:
        recurring = initial

    lines = text.split("\n")
    lines = [(initial + lines[0]).rstrip()] + [(recurring + line).rstrip() for line in lines[1:]]

    return "\n".join(lines)


def text_sanitize(text: Optional[str]) -> str:
    # For safety, remove any control characters except for \n and \t
    # At least on HN some messages contain \x00 characters

    text = text or ""
    allowed_cc = set(("\n", "\t"))
    chars = (c for c in text if c in allowed_cc or unicodedata.category(c) != "Cc")
    text = "".join(chars)

    return text


def text_split_urls(text: str) -> list[str]:
    return [p for p in URL_REX.split(text) if p != ""]


def html_node_children(parent: HTMLNode) -> list[HTMLNode]:
    ret: list[HTMLNode] = []
    node = parent.first_child

    while node is not None:
        ret.append(node)
        node = node.next_sibling

    return ret


def html_node_append(parent: HTMLNode, child: HTMLNode) -> None:
    if parent.first_child is None:
        parent.first_child = child

    if parent.last_child is not None:
        parent.last_child.next_sibling = child
        child.prev_sibling = parent.last_child

    parent.last_child = child
    child.parent = parent


def html_node_unlink(node: HTMLNode) -> None:
    if node.prev_sibling:
        node.prev_sibling.next_sibling = node.next_sibling

    if node.next_sibling:
        node.next_sibling.prev_sibling = node.prev_sibling

    if node.parent and node.parent.first_child is node:
        node.parent.first_child = node.next_sibling

    if node.parent and node.parent.last_child is node:
        node.parent.last_child = node.prev_sibling

    node.parent = node.prev_sibling = node.next_sibling = None


def html_node_dump(node: HTMLNode) -> str:
    lines = []
    lines.append(f"{node.tag} {repr(node.attrs)}")

    for child in html_node_children(node):
        if child.tag == "text":
            lines.append("  text " + repr(child.text))
        else:
            lines += ["  " + line for line in html_node_dump(child).split("\n")]

    return "\n".join(lines)


def html_node_trim_whitespace(node: HTMLNode) -> None:
    if node.pre:
        node.text = node.text.rstrip("\r\n\t ")
        return

    text = node.text.strip("\r\n\t ")
    text = re.sub(r"[\r\n\t ]+", " ", text)
    text = text.replace("\x00", "\n")
    text = re.sub(r" *\n *", "\n", text)

    node.text = text


def html_node_process_inline(node: HTMLNode, inline=False) -> str:
    """Traverse tree flattening all inline nodes into text nodes"""

    if node.tag not in HTML_BLOCK_TAGS:
        # Never disable inline if already enabled
        inline = True

    text = "".join(html_node_process_inline(c, inline) for c in html_node_children(node))

    if not inline:
        return ""

    if node.tag == "text":
        text = node.text
    elif node.tag == "br":
        text = "\x00"
    elif node.tag == "em" or node.tag == "i":
        text = f"/{text}/"
    elif node.tag == "strong" or node.tag == "b":
        text = f"*{text}*"
    elif node.tag == "code" and not node.pre:
        text = f"`{text}`"
    elif node.tag == "a":
        href = node.attrs.get("href", "") or ""
        if text.endswith("...") and href.startswith(text[:-3]):
            # Workaround for link formatting on HN
            text = href
        elif text != href:
            text = f"{text} {href}"

    node.tag = "text"
    node.text = text
    node.first_child = node.last_child = None

    return text


def html_node_process_text(node: HTMLNode):
    """Traverse tree merging, trimming and pruning text nodes"""

    for child in html_node_children(node):
        html_node_process_text(child)

    # Merge adjacent text nodes
    for child in html_node_children(node):
        prev = child.prev_sibling
        if child.tag == "text" and prev is not None and prev.tag == "text" and child.pre == prev.pre:
            child.text = prev.text + child.text
            html_node_unlink(prev)

    # Trim whitespace from text nodes
    for child in html_node_children(node):
        if child.tag == "text":
            html_node_trim_whitespace(child)

    # Remove empty text nodes
    for child in html_node_children(node):
        if child.tag == "text" and child.text == "":
            html_node_unlink(child)


def html_node_render_block(node: HTMLNode, width=70) -> str:
    if node.tag == "blockquote" or node.tag == "li" or node.tag == "pre":
        width -= 2

    parts = []

    if node.tag == "hr":
        parts.append("-" * width)

    for child in html_node_children(node):
        if child.tag == "text" and child.pre:
            parts.append(child.text)

        elif child.tag == "text" and not child.pre:
            subparts = [text_wrap(p, width) for p in child.text.split("\n")]
            parts.append("\n".join(subparts))

        else:
            parts.append(html_node_render_block(child, width))

        if child.next_sibling is not None and child.tag != "li":
            parts.append("")

    text = "\n".join(parts)

    if node.tag == "blockquote":
        text = text_indent(text, "> ")
    elif node.tag == "pre":
        text = text_indent(text, "| ")
    elif node.tag == "li":
        text = text_indent(text, "- ", "  ")

    return text


def html_render(html: str) -> str:
    # This renderer works well for HN messages because their markup is simple, and it can do
    # some custom optimizations, like expanding ellipsis-shortened links, preserving quote
    # symbols in wrapped lines, and preventing references with long urls from being broken
    # into separate lines. For other backends it may make more sense to use an external app
    # (links, w3m, etc)

    html = text_sanitize(html)

    parser = HTMLParser()
    parser.feed(html)
    parser.close()

    node = parser.root_node

    log_sep = "\n" + "-" * 80 + "\n"
    logging.debug(f"Initial HTML tree{log_sep}{html_node_dump(node)}{log_sep}")

    html_node_process_inline(node)
    html_node_process_text(node)

    logging.debug(f"Processed HTML tree{log_sep}{html_node_dump(node)}{log_sep}")

    return html_node_render_block(node)


def fetch(url: str) -> str:
    logging.debug(f"Fetching '{url}'...")

    headers = {"User-Agent": "retronews"}
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT).read().decode()

    return resp


@overload
def list_get(lst: list[T], index: int, default: T) -> T:
    ...


@overload
def list_get(lst: list[T], index: int, default: Optional[T] = None) -> Optional[T]:
    ...


def list_get(lst, index, default=None):
    return lst[index] if 0 <= index < len(lst) else default


def cmd_quit(_: AppState):
    raise ExitException()


def cmd_help(app: AppState):
    app_show_help_screen(app)


def cmd_show_links(app: AppState):
    app_show_links_screen(app)


def cmd_up(app: AppState) -> None:
    cmd_pager_up(app) if app.pager_visible else cmd_prev(app)


def cmd_down(app: AppState) -> None:
    cmd_pager_down(app) if app.pager_visible else cmd_next(app)


def cmd_prev(app: AppState) -> None:
    pos = app.selected_message.index_position - 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_next(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    app_select_message(app, list_get(app.messages, pos, app.selected_message))


def cmd_next_unread(app: AppState) -> None:
    pos = app.selected_message.index_position + 1 if app.selected_message else 0
    message = next((msg for msg in app.messages[pos:] if not msg.is_shown_as_read), None)
    if message is not None:
        app_select_message(app, message)


def cmd_parent(app: AppState) -> None:
    if (msg := app.selected_message) is not None and (parent_msg := msg.parent) is not None:
        app_select_message(app, parent_msg)


def cmd_mark_set(app: AppState) -> None:
    if (msg := app.selected_message) is not None:
        app.marked_message_id = msg.msg_id
    app_show_flash(app, "Mark set")


def cmd_mark_jump(app: AppState) -> None:
    marked_msg = app.messages_by_id.get(app.marked_message_id) if app.marked_message_id else None
    cmd_mark_set(app)
    if marked_msg is not None:
        app_select_message(app, marked_msg)
    app_show_flash(app, "Mark swapped")


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
        app_load_group(app, group)


def cmd_reload_page(app: AppState) -> None:
    app_load_group(app, app.group)


def cmd_load_prev_page(app: AppState) -> None:
    app_load_group(app, group_advance_page(app.group, -1))


def cmd_load_next_page(app: AppState) -> None:
    app_load_group(app, group_advance_page(app.group, 1))


def cmd_load_page(app: AppState) -> None:
    user_input = app_prompt(app, "Go to page (empty to cancel): ")

    if user_input.isnumeric() and (page := int(user_input)) >= 1:
        app_load_group(app, group_set_page(app.group, page))
    elif len(user_input) > 0:
        app_show_flash(app, "Invalid page number")


def cmd_open(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    if msg.is_thread:
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
        cmd_next(app)


def cmd_star_thread(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    if (thread_msg := app.messages_by_id.get(msg.thread_id)) is None:
        return

    thread_msg.flags.starred = not thread_msg.flags.starred
    db_save_message(app.db, thread_msg)
    cmd_next(app)


def cmd_dump(app: AppState) -> None:
    if (msg := app.selected_message) is None:
        return

    filename = f"{msg.msg_id}.html"

    with open(filename, "w") as fp:
        fp.write(msg.body or "")

    app_show_flash(app, f"Message body dumped to {filename}")


def cmd_toggle_raw_mode(app: AppState) -> None:
    app.raw_mode = not app.raw_mode
    app_select_message(app, app.selected_message)


def cmd_resize(app: AppState) -> None:
    app_refresh_message(app)


def cmd_unknown(app: AppState) -> None:
    app.flash = "Unknown key"


def db_init(path: str) -> DB:
    path = os.path.expanduser(path)
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS messages (
            msg_id TEXT NOT NULL PRIMARY KEY,
            thread_id TEXT NOT NULL,
            date INTEGER NOT NULL,
            flags JSON NOT NULL
        );

        CREATE INDEX IF NOT EXISTS messages_starred_date ON messages (
            JSON_EXTRACT(flags, '$.starred'),
            date
        );
    """

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(create_table_sql)
    db.commit()

    return cast(DB, db)


def db_save_message(db: DB, message: Message) -> None:
    sql = """INSERT OR REPLACE INTO messages (msg_id, thread_id, date, flags) VALUES (?, ?, ?, ?)"""
    date = int(message.date.timestamp())
    flags_json = json.dumps(dataclasses.asdict(message.flags))
    db.execute(sql, (message.msg_id, message.thread_id, date, flags_json))
    db.commit()


def db_load_message_flags(db: DB, messages_by_id: dict[str, Message]) -> None:
    message_ids = list(messages_by_id.keys())
    sql = f"SELECT * FROM messages WHERE msg_id IN ({','.join('?' for _ in message_ids)})"

    for row in db.execute(sql, message_ids):
        flags = json.loads(row["flags"])
        messages_by_id[row["msg_id"]].flags = MessageFlags(**flags)


def db_load_read_comments(db: DB, messages_by_id: dict[str, Message]) -> None:
    threads_by_id = {msg.msg_id: msg for msg in messages_by_id.values() if msg.is_thread}
    thread_ids = list(threads_by_id.keys())

    sql = f"""
        SELECT thread_id, COUNT(*) AS count
        FROM messages
        WHERE thread_id IN ({','.join('?' for _ in thread_ids)}) AND JSON_EXTRACT(flags, '$.read')
        GROUP BY thread_id
    """

    for row in db.execute(sql, thread_ids):
        threads_by_id[row["thread_id"]].read_comments = row["count"]


def db_load_starred_thread_ids(db: DB, page: int = 1) -> list[str]:
    page_size = 30
    offset = (page - 1) * page_size
    sql = """
        SELECT thread_id
        FROM messages
        WHERE JSON_EXTRACT(flags, '$.starred')
        GROUP BY thread_id
        ORDER BY date DESC
        LIMIT ?
        OFFSET ?
    """

    return [row["thread_id"] for row in db.execute(sql, (page_size, offset))]


def msg_flatten_thread(msg: Message, prefix: str = "", is_last_child: bool = False) -> Generator[Message, None, None]:
    msg.index_tree = "" if msg.is_thread else f"{prefix}{'└─' if is_last_child else '├─'}> "
    yield msg

    child_count = len(msg.children)
    child_prefix = "" if msg.is_thread else f"{prefix}{'  ' if is_last_child else '│ '}"

    for i, child_node in enumerate(msg.children):
        child_is_last = i == child_count - 1
        for child in msg_flatten_thread(child_node, prefix=child_prefix, is_last_child=child_is_last):
            yield child


def msg_build_raw_lines(msg: Message) -> list[str]:
    text = text_sanitize(msg.body)

    # Unescape selected entities for better readability
    repl = {"&#x2F;": "/", "&#x27;": "'", "&quot;": '"'}
    for k, v in repl.items():
        text = text.replace(k, v)

    return reduce(lambda acc, line: acc + wrap(line, width=120, replace_whitespace=False), text.split("\n"), [])


def msg_build_lines(msg: Message) -> list[str]:
    lines = [
        f"Content-Location: {msg.content_location}",
        f"Date: {msg.date.strftime('%Y-%m-%d %H:%M')}",
        f"From: {msg.author or '<unknown>'}",
        f"Subject: {msg.title}",
        "",
    ]

    lines += html_render(msg.body or "").split("\n") if not msg.is_deleted else ["<deleted>"]

    return lines


def msg_unload(msg: Message) -> Message:
    msg.children = []
    msg.body = None
    return msg


def hn_parse_search_hit(hit: HNSearchHit) -> Message:
    return Message(
        msg_id=f"{hit['objectID']}@hn",
        thread_id=f"{hit['objectID']}@hn",
        content_location=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        date=datetime.fromtimestamp(hit["created_at_i"]),
        author=hit["author"],
        title=html.unescape(hit["title"]),
        total_comments=(hit["num_comments"] or 0) + 1,
    )


def hn_parse_entry(entry: HNEntry, thread_id: str = "", parent: Optional[Message] = None) -> Message:
    thread_id = thread_id or str(entry["id"])

    my_title = html.unescape(entry["title"]) if entry["title"] else None

    parent_title = parent.title if parent else ""
    parent_title = parent_title if parent_title.startswith("Re: ") else f"Re: {parent_title}"

    body = f"<p>{entry['url']}</p>" if entry["url"] else ""
    body = f"{body}{entry['text']}" if entry["text"] else body

    msg = Message(
        msg_id=f"{entry['id']}@hn",
        thread_id=f"{thread_id}@hn",
        content_location=f"https://news.ycombinator.com/item?id={entry['id']}",
        date=datetime.fromtimestamp(entry["created_at_i"]),
        author=entry["author"],
        title=my_title or parent_title,
        body=body,
        parent=parent,
    )

    msg.children = [hn_parse_entry(child, thread_id, msg) for child in entry["children"]]

    return msg


def hn_fetch_threads_by_id(thread_ids: list[str]) -> list[Message]:
    story_tags = ",".join(f"story_{x}" for x in thread_ids)
    url = f"https://hn.algolia.com/api/v1/search_by_date?hitsPerPage={len(thread_ids)}&tags=story,({story_tags})"
    hits = json.loads(fetch(url))["hits"]

    return [hn_parse_search_hit(hit) for hit in hits]


def hn_fetch_threads(group: str = "news", page: int = 1) -> list[Message]:
    rex = re.compile(r'href="item\?id=(\d+)"')

    html = fetch(f"https://news.ycombinator.com/{group}?p={page}")
    thread_ids = list(set(match.group(1) for match in rex.finditer(html)))

    return hn_fetch_threads_by_id(thread_ids)


def hn_fetch_new_threads(page: int = 1) -> list[Message]:
    url = f"https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=30&page={page}"
    hits = json.loads(fetch(url))["hits"]

    return [hn_parse_search_hit(hit) for hit in hits]


def hn_fetch_thread(entry_id: Union[str, int]) -> Message:
    resp = fetch(f"http://hn.algolia.com/api/v1/items/{entry_id}")
    entry: HNEntry = json.loads(resp)
    return hn_parse_entry(entry)


def lb_parse_thread(thread: LBThread) -> Message:
    comments = {}

    ret = Message(
        msg_id=f"{thread['short_id']}@lb",
        thread_id=f"{thread['short_id']}@lb",
        content_location=thread["short_id_url"],
        date=datetime.fromtimestamp(datetime.fromisoformat(thread["created_at"]).timestamp()),
        author=thread["submitter_user"]["username"],
        title=thread["title"],
        body=f"<p>{thread['url']}</p>{thread['description']}",
        total_comments=thread["comment_count"] + 1,
    )

    for comment in thread.get("comments", []) or []:
        comments[comment["short_id"]] = Message(
            msg_id=f"{comment['short_id']}@lb",
            thread_id=f"{thread['short_id']}@lb",
            content_location=comment["url"],
            date=datetime.fromtimestamp(datetime.fromisoformat(comment["created_at"]).timestamp()),
            author=comment["commenting_user"]["username"],
            title=f"Re: {thread['title']}",
            body=comment["comment"],
            children=[],
        )

    for comment in thread.get("comments", []) or []:
        msg = comments[comment["short_id"]]
        parent_msg = comments[comment["parent_comment"]] if comment["parent_comment"] else ret
        msg.parent = parent_msg
        parent_msg.children.append(msg)

    return ret


def lb_fetch_threads(group: str = "", page: int = 1) -> list[Message]:
    resp = fetch(f"https://lobste.rs/page/{page}.json")
    threads: list[LBThread] = json.loads(resp)

    return [lb_parse_thread(thread) for thread in threads]


def lb_fetch_thread(entry_id: str) -> Message:
    resp = fetch(f"https://lobste.rs/s/{entry_id}.json")
    thread: LBThread = json.loads(resp)

    return lb_parse_thread(thread)


def group_set_page(group: Group, page: int) -> Group:
    return dataclasses.replace(group, page=page)


def group_advance_page(group: Group, offset: int = 1) -> Group:
    return group_set_page(group, page=max(1, group.page + offset))


def group_fetch_starred_threads(db: DB, page: int = 1) -> list[Message]:
    thread_ids = db_load_starred_thread_ids(db, page)
    threads_by_provider: dict[str, list[str]] = {}
    threads = []

    for (source_id, provider) in (t.split("@") for t in thread_ids):
        threads_by_provider.setdefault(provider, list()).append(source_id)

    for provider, thread_ids in threads_by_provider.items():
        if provider == "hn":
            threads += hn_fetch_threads_by_id(thread_ids)
        elif provider == "lb":
            # FIXME: Find way to fetch multiple threads by id at once
            threads += [lb_fetch_thread(x) for x in thread_ids]

    threads.sort(key=lambda x: x.date, reverse=True)

    return threads


def group_fetch_thread(thread_id: str) -> Message:
    fetchers = {
        "hn": hn_fetch_thread,
        "lb": lb_fetch_thread,
    }
    (source_id, provider) = thread_id.split("@")

    return fetchers[provider](source_id)


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


def app_refresh_message(app: AppState) -> None:
    app.pager_offset = 0

    # Converting html to lines lazily on render for easier debugging
    if (msg := app.selected_message) is not None:
        msg.lines = msg_build_raw_lines(msg) if app.raw_mode else msg_build_lines(msg)


def app_select_message(app: AppState, message: Optional[Message], show_pager: bool = False) -> None:
    app.selected_message = message

    app_refresh_message(app)

    if message is None or message.body is None:
        app.pager_visible = False
        return

    if show_pager:
        app.pager_visible = True

    if app.pager_visible:
        message.flags.read = True
        db_save_message(app.db, message)
        db_load_read_comments(app.db, {message.thread_id: app.messages_by_id[message.thread_id]})


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


def app_load_group(app: AppState, group: Group) -> None:
    fn = partial(group.fetch, app.db, group.page)
    flash = f"Fetching stories from '{group.label}' (page {group.page})..."

    if (messages := app_safe_run(app, fn, flash=flash)) is None:
        return

    app_load_messages(app, messages)
    app.group = group


def app_close_thread(app: AppState) -> None:
    selected_thread_id = app.selected_message.thread_id if app.selected_message else None
    filtered_messages = [msg_unload(msg) for msg in app.messages if msg.is_thread]

    app_load_messages(app, filtered_messages, selected_message_id=selected_thread_id)


def app_open_thread(app: AppState, thread_message: Message) -> None:
    fn = partial(group_fetch_thread, thread_message.thread_id)
    flash = f"Fetching thread '{thread_message.thread_id}'..."

    if (new_thread_message := app_safe_run(app, fn, flash=flash)) is None:
        return

    app_close_thread(app)

    index_pos = thread_message.index_position
    thread_messages = list(msg_flatten_thread(new_thread_message))
    new_thread_message.total_comments = len(thread_messages)
    messages = app.messages[:index_pos] + thread_messages + app.messages[index_pos + 1 :]  # noqa: E203

    app_load_messages(app, messages, selected_message_id=thread_message.msg_id, show_pager=True)


def app_update_layout(app: AppState) -> None:
    lt = app.layout

    (lt.lines, lt.cols) = app.screen.getmaxyx()

    if lt.lines < 25 or lt.cols < 80:
        raise ExitException(1, "At least 80x25 terminal is required")

    max_index_height = lt.lines - 3
    lt.index_height = (max_index_height // 3) if app.pager_visible else max_index_height

    lt.middle_menu_row = lt.index_start + lt.index_height if app.pager_visible else None
    lt.pager_start = lt.index_start + lt.index_height + 1 if app.pager_visible else None
    lt.pager_height = lt.lines - lt.pager_start - 2 if lt.pager_start is not None else None

    lt.bottom_menu_row = lt.lines - 2
    lt.flash_menu_row = lt.lines - 1


def app_show_help_screen(app: AppState) -> None:
    app.screen.erase()
    app.screen.addstr(0, 0, HELP_SCREEN)
    app.screen.refresh()
    app.screen.getch()


def app_show_links_screen(app: AppState) -> None:
    app.screen.erase()
    app.screen.addstr(0, 0, "Select link to open:")
    app.screen.refresh()

    lines = app.selected_message.lines if app.selected_message is not None else []

    keys = "1234567890abcdefghij"
    urls = URL_REX.findall(" ".join(lines))
    items = dict(zip((ord(k) for k in keys), urls))

    if len(items) == 0:
        return app_show_flash(app, "No links available for opening")

    for (i, (key, url)) in enumerate(items.items()):
        app.screen.addstr(i + 2, 0, f"{chr(key)} - {url}")

    key = app.screen.getch()

    if key not in items.keys():
        return app_show_flash(app, "Unknown key")

    url = items[key]

    app_show_flash(app, f"Opening {url}")
    webbrowser.open(url)

    # Refresh window in case a terminal browser was used
    app.screen.clearok(True)


def app_show_flash(app: AppState, flash: Optional[str]) -> None:
    app.flash = flash
    app_render(app)


def app_prompt(app: AppState, prompt: str) -> str:
    lt = app.layout

    app.screen.insstr(lt.flash_menu_row, 0, prompt.ljust(lt.cols))
    app.screen.refresh()

    curses.curs_set(1)
    win = curses.newwin(1, lt.cols - len(prompt), lt.flash_menu_row, len(prompt))

    textbox = curses.textpad.Textbox(win)
    textbox.stripspaces = True
    ret = textbox.edit().strip()

    del win
    curses.curs_set(0)

    return ret


def app_render_index_row(app: AppState, row: int, message: Message) -> None:
    cols = app.layout.cols
    date = message.date.strftime("%Y-%m-%d %H:%M")
    author = (message.author or "<unknown>")[:10].ljust(10)

    is_response = message.title.startswith("Re:") and not message.is_thread
    is_selected = message == app.selected_message
    hide_title = is_response and row > app.layout.index_start and not message.flags.starred and not is_selected
    title = "" if hide_title else message.title

    unread = (
        str(max(min(message.total_comments - message.read_comments, 9999), 0)).rjust(4) if message.is_thread else "    "
    )

    app.screen.insstr(row, 0, f"[{date}]  [{author}]  [{unread}]  {message.index_tree}{title}")

    if is_selected:
        app.screen.chgat(row, 0, cols, app.colors["cursor"])
    else:
        read_attr = 0 if message.is_shown_as_read else curses.A_BOLD
        subject_attr = app.colors["starred_subject"] if message.flags.starred else app.colors["default"]
        subject_attr = subject_attr | read_attr

        app.screen.chgat(row, 1, 16, app.colors["date"] | read_attr)
        app.screen.chgat(row, 21, 10, app.colors["author"] | read_attr)
        app.screen.chgat(row, 35, 4, app.colors["unread_comments"] | read_attr)
        app.screen.chgat(row, 42, len(message.index_tree), app.colors["tree"])
        app.screen.chgat(row, 42 + len(message.index_tree), cols - 42 - len(message.index_tree), subject_attr)


def app_render_index(app: AppState) -> None:
    height = app.layout.index_height

    offset = app.selected_message.index_position - height // 2 if app.selected_message else 0
    offset = min(offset, len(app.messages) - height)
    offset = max(offset, 0)

    rows_to_render = min(height, len(app.messages) - offset)

    for i in range(rows_to_render):
        app_render_index_row(app, app.layout.index_start + i, app.messages[i + offset])


def app_get_pager_line_attr(app: AppState, line: str) -> int:
    if line.startswith("Content-Location: "):
        return app.colors["tree"]
    elif line.startswith("Date: "):
        return app.colors["date"]
    elif line.startswith("From: "):
        return app.colors["author"]
    elif line.startswith("Subject: "):
        return app.colors["header_subject"]
    elif line.startswith(">>") or line.startswith("> >"):
        return app.colors["nested_quote"]
    elif line.startswith(">"):
        return app.colors["quote"]
    elif line.startswith("| "):
        return app.colors["code"]
    elif line == "~":
        return app.colors["empty_pager_line"]
    elif line == "<deleted>":
        return app.colors["deleted_message_pager_line"]
    else:
        return 0


def app_render_pager_line(app: AppState, row: int, line: str) -> None:
    line_attr = app_get_pager_line_attr(app, line)

    app.screen.move(row, 0)
    app.screen.clrtoeol()
    app.screen.move(row, 0)

    for part in text_split_urls(line):
        is_url = URL_REX.fullmatch(part)
        part_attr = app.colors["url"] if is_url and line_attr == 0 else line_attr
        app.screen.addstr(part, part_attr)


def app_render_pager(app: AppState) -> None:
    message = app.selected_message
    start = app.layout.pager_start
    height = app.layout.pager_height

    if message is None or start is None or height is None:
        return

    for i in range(height):
        line = list_get(message.lines, i + app.pager_offset, "~")
        app_render_pager_line(app, i + start, line)


def app_render_top_menu(app: AppState) -> None:
    lt = app.layout
    cols = lt.cols
    app.screen.insstr(lt.top_menu_row, 0, HELP_MENU[:cols].ljust(cols), app.colors["menu"] | curses.A_BOLD)


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

    text = f"--({unread}/{total} unread)"
    if thread_message.flags.starred:
        text += "--(starred thread)"
    text = text[:cols].ljust(cols, "-")

    app.screen.insstr(row, 0, text, app.colors["menu"] | curses.A_BOLD)


def app_render_bottom_menu(app: AppState) -> None:
    lt = app.layout

    app.screen.chgat(lt.bottom_menu_row, 0, lt.cols, app.colors["menu"])
    app.screen.move(lt.bottom_menu_row, 0)

    for (i, group) in enumerate(GROUP_TABS):
        is_active = group.label == app.group.label
        color = app.colors["menu_active"] if is_active else app.colors["menu"]
        attr = color | curses.A_BOLD
        app.screen.addstr(f"{i+1}:{group.label}  ", attr)

    page_text = f"page: {app.group.page}"
    app.screen.insstr(lt.bottom_menu_row, lt.cols - len(page_text), page_text, app.colors["menu"] | curses.A_BOLD)


def app_render(app: AppState) -> None:
    app_update_layout(app)
    app.screen.erase()
    app_render_index(app)
    app_render_pager(app)
    app_render_top_menu(app)
    app_render_middle_menu(app)
    app_render_bottom_menu(app)
    app.screen.insstr(app.layout.flash_menu_row, 0, app.flash or "")
    app.screen.refresh()


def app_init_colors(app: AppState) -> None:
    for (i, (name, (fg, bg))) in enumerate(COLORS.items()):
        curses.init_pair(i + 1, fg, bg)
        app.colors[name] = curses.color_pair(i + 1)


def app_main(screen: Window, db: DB, initial_tab: int) -> int:
    curses.curs_set(0)
    curses.use_default_colors()

    group = GROUP_TABS[initial_tab - 1]

    app = AppState(screen=screen, db=db, group=group)
    app_init_colors(app)
    app_load_group(app, app.group)

    while True:
        app_render(app)
        app.flash = ""
        c = app.screen.getch()
        KEY_BINDINGS.get(c, cmd_unknown)(app)


def setup_logging(path: Optional[str]) -> None:
    if path is None:
        return logging.disable()

    format = "%(asctime)s %(levelname)s: %(message)s"
    stream = sys.stderr if path == "-" else open(path, "a")
    logging.basicConfig(format=format, level="DEBUG", stream=stream)
    logging.debug("Session started")


if __name__ == "__main__":
    tab_choices = range(1, len(GROUP_TABS) + 1)

    ap = argparse.ArgumentParser(
        formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog, max_help_position=32)
    )
    ap.add_argument("-d", "--db", metavar="PATH", default="~/.retronews.db", help="database path")
    ap.add_argument("-l", "--logfile", metavar="PATH", default=None, help="debug logfile path")
    ap.add_argument("-t", "--tab", metavar="TAB", type=int, default=1, choices=tab_choices, help="initial tab")
    ap.add_argument("-r", "--render", metavar="PATH", default=None, help="render raw html message and quit")
    args = ap.parse_args()

    setup_logging(args.logfile)

    if (path := args.render) is not None:
        with open(path) as fp:
            print(html_render(fp.read()))
        sys.exit(0)

    try:
        db = db_init(args.db)
        ret = curses.wrapper(app_main, db, args.tab)
    except ExitException as e:
        if e.message:
            sys.stderr.write(e.message + "\n")
        ret = e.code
    except BaseException as e:
        sys.stderr.write(str(e) + "\n")
        ret = 1
    finally:
        db.close()
        sys.exit(ret)
