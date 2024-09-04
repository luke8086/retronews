# retronews

A Python script for browsing [Hacker News](https://news.ycombinator.com/)
and [Lobsters](https://lobste.rs/) discussions with a nostalgic interface emulating
classical usenet and mail readers, like slrn and mutt.

It was primarily written so I could highlight interesting threads and keep
track of read / unread messages (see [blog post](https://luke8086.dev/retronews.html)).
The UI showing one message at a time also encourages slower, more focused reading.

On Unix-like systems it only depends on Python 3.9. On Windows you may also need
to install [windows-curses](https://pypi.org/project/windows-curses/).

It doesn't require installation. You can run it simply with:

```bash
$ curl -LO https://raw.githubusercontent.com/luke8086/retronews/main/retronews.py
$ python3 ./retronews.py
```

Press `?` to see available keybindings.

<img src="screenshot.png" width="600" />


## Known issues and limitations

- The reader is read-only, there are no plans to support voting and posting
- Message formatting is not perfect, but works well enough most of the time
- Detecting if threads contain unread responses works by only checking their
  count, it's not reliable if any responses were deleted
- No config file is planned, since the code is in Python, it's simpler to
  treat it as its own config and customize directly

## Why not an NNTP gateway?

NNTP doesn't support browsing threads by title (let alone paginated) and requesting
their messages on demand. Clients need to fetch metadata of all available messages
in all available threads in advance. Given the volume of messages on HN, synchronizing
them to the gateway is not practical. Even when attempted, some clients struggle
with the sheer number of messages in a single group.

## Related projects

- [HN Search @ Algolia](https://hn.algolia.com/about) - The underlying API used to retrieve messages
- [nntpit](https://github.com/taviso/nntpit) - An NNTP gateway to reddit.com
- [circumflex](https://github.com/bensadeh/circumflex) - Another, more advanced TUI for HN
