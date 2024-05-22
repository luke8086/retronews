# retronews

A Python script for browsing [Hacker News](https://news.ycombinator.com/)
and [Lobsters](https://lobste.rs/) discussions with an interface emulating
classical usenet and mail readers, like slrn and mutt.

It only requires Python 3.9 and doesn't need installation, you can run it with:

```bash
$ curl -LO https://raw.githubusercontent.com/luke8086/retronews/main/retronews.py
$ python3 ./retronews.py
```

Press `?` to see available keybindings.

For rationale, see the corresponding [blog post](https://luke8086.dev/retronews.html)

<img src="screenshot.png" width="600" />


## Known issues and limitations

- The reader is read-only, there are no plans to support voting and posting
- Message formatting is not perfect, but works well enough most of the time
- Detecting if threads contain unread responses works by only checking their
  amount, it's not reliable if any responses were deleted
- No config file is planned, since the code is in Python, it's simpler to
  treat it as its own config and customize directly

## Why not a NNTP gateway?

NNTP doesn't support browsing threads by title (let alone paginated) and requesting
their messages on demand. Clients need to fetch metadata of all available messages
in all available threads in advance. Given the volume of messages on HN, synchronizing
them to the gateway is not practical. Even when attempted, some clients struggle
with the amount of data in a single group.

## Related projects

- [HN Search @ Algolia](https://hn.algolia.com/about) - The underlying API used to retrieve messages
- [nntpit](https://github.com/taviso/nntpit) - An NNTP gateway to reddit.com
- [circumflex](https://github.com/bensadeh/circumflex) - Another, more advanced TUI for HN
