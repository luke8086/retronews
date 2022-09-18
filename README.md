# retronews

A Python script for browsing [Hacker News](https://news.ycombinator.com/)
comments with an interface emulating classical usenet and mail readers,
like slrn and mutt.

It only requires Python 3.9 and doesn't need installation, you can run it with:

```bash
$ curl -LO https://raw.githubusercontent.com/luke8086/retronews/main/retronews.py
$ python3 ./retronews.py
```

For rationale, see the corresponding [blog post](https://luke8086.neocities.org/retronews.html)

<img src="screenshot.png" width="600" />

## Default key bindings

```
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
  1 - 5                   Change group
  R                       Refresh current page
  < >                     Go to previous / next page
  g                       Go to specific page
  k j                     Scroll pager up / down by one line
  s                       Star / unstar selected message
  S                       Star / unstar current thread
  r                       Toggle raw HTML mode
```


## Known issues and limitations

- The script is read-only, there are no plans to support voting and posting
- The message renderer has glitches, but I prefer to keep it simple than solve
  every corner case
- No config file is planned, since the code is in Python, it's simpler to
  treat it as its own config and customize directly

## Why not a NNTP gateway?

NNTP operates on a flat list of messages.
The client asks for message headers N to M, uses them to draw a tree, and then
asks one by one for the full content when messages are selected.
There's no support for requesting a list of top-level messages first (let
alone paginated), and fetching their responses only when individual threads are expanded.

The gateway would need to download all threads to display at once, and since the
underlying API can take several seconds per story, it'd be unacceptably slow.
It also wouldn't be possible to request threads from older pages or specified by id.

## Related projects

- [HN Search @ Algolia](https://hn.algolia.com/about) - The underlying API used to retrieve messages
- [nntpit](https://github.com/taviso/nntpit) - An NNTP gateway to reddit.com
