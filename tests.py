import unittest

import retronews


class TestHtmlParser(unittest.TestCase):
    maxDiff = None

    def test_paragraphs(self):
        html = (
            "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
            + "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
        )
        lines = [
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
            "",
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
        ]
        self.assertLines(html, lines)

    def test_quotes(self):
        html = (
            "<p>&gt;&gt;Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
            + "<p>&gt; Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
            + "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
        )
        lines = [
            ">>Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            ">>te. Noster nominati recteque no has.",
            "",
            "> Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "> te. Noster nominati recteque no has.",
            "",
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
        ]
        self.assertLines(html, lines)

    def test_expanding_links(self):
        # Ensure links shortened with ellipsis are rendered in full
        html = '<a href="https://example.com/foo/bar">https://example.com/foo...</a>'
        lines = ["https://example.com/foo/bar"]
        self.assertLines(html, lines)

    def test_link_references(self):
        # Ensure links in numbered references are not shifted to the next line
        html = (
            "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus</p>"
            + "<p>[0]: <a>https://long.long.long.long.long.long.long.long.long.long.long.example.com</a></p>"
            + "<p>[1] - <a>https://long.long.long.long.long.long.long.long.long.long.long.example.com</a></p>"
            + "<p>[2] <a>https://long.long.long.long.long.long.long.long.long.long.long.example.com</a></p>"
        )
        lines = [
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "",
            "[0]: https://long.long.long.long.long.long.long.long.long.long.long.example.com",
            "",
            "[1] - https://long.long.long.long.long.long.long.long.long.long.long.example.com",
            "",
            "[2] https://long.long.long.long.long.long.long.long.long.long.long.example.com",
        ]

        self.assertLines(html, lines)

    def test_code_blocks(self):
        # This HTML is not particularly correct, but it's how HN renders code blocks
        html = (
            "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
            + "<p><pre><code>    def hello():\n        print('Hello World')\n        return None\n</code></pre>\n"
            + "Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
            + "<p><pre><code>    def hello():\n        print('Hello World')\n        return None\n</code></pre>\n"
            + "Lorem ipsum dolor sit amet, pro eu soleat civibus. "
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
        )
        lines = [
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
            "",
            "    def hello():",
            "        print('Hello World')",
            "        return None",
            "",
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
            "",
            "    def hello():",
            "        print('Hello World')",
            "        return None",
            "",
            "Lorem ipsum dolor sit amet, pro eu soleat civibus. Mel quas sensibus",
            "te. Noster nominati recteque no has.",
        ]
        self.assertLines(html, lines)

    def test_long_code_blocks(self):
        # Make sure long code doesn't get wrapped
        html = (
            "<p>Lorem ipsum dolor sit amet, pro eu soleat civibus.</p>"
            + "<p><pre><code>    lambda L: [] if L==[] else qsort([x for x in L[1:] "
            + "if x< L[0]]) + L[0:1] + qsort([x for x in L[1:] if x>=L[0]])\n</code></pre>\n"
            + "Mel quas sensibus te. Noster nominati recteque no has.</p>"
        )
        lines = [
            "Lorem ipsum dolor sit amet, pro eu soleat civibus.",
            "",
            "    lambda L: [] if L==[] else qsort([x for x in L[1:] if x< L[0]]) + L[0:1] "
            + "+ qsort([x for x in L[1:] if x>=L[0]])",
            "",
            "Mel quas sensibus te. Noster nominati recteque no has.",
        ]
        self.assertLines(html, lines)

    def assertLines(self, html: str, lines: list[str]) -> None:
        self.assertListEqual(retronews.parse_html(html), lines)


if __name__ == "__main__":
    unittest.main()
