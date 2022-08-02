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
        html = '<a href="https://example.com/foo/bar">https://example.com/foo...</a>'
        lines = ["https://example.com/foo/bar"]
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

    def assertLines(self, html: str, lines: list[str]) -> None:
        self.assertListEqual(retronews.parse_html(html), lines)


if __name__ == "__main__":
    unittest.main()
