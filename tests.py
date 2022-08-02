import unittest

import retronews


class TestHtmlParser(unittest.TestCase):
    def test_expanding_links(self):
        html = '<a href="https://example.com/foo/bar">https://example.com/foo...</a>'
        lines = retronews.parse_html(html)
        self.assertListEqual(lines, ["https://example.com/foo/bar"])


if __name__ == "__main__":
    unittest.main()
