import os
import unittest

import retronews

TC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")


class TestHtmlParser(unittest.TestCase):
    maxDiff = None

    def checkFormatting(self, name: str):
        html_path = os.path.join(TC_DIR, f"{name}.html")
        out_path = os.path.join(TC_DIR, f"{name}.out")

        with open(html_path) as fp:
            html = fp.read()

        actual = "\n".join(retronews.parse_html(retronews.sanitize_text(html))).strip()

        if not os.path.exists(out_path):
            with open(out_path, "w") as fp:
                fp.write(actual)

        with open(out_path) as fp:
            expected = fp.read().strip()

        if actual != expected:
            sep = "\n" + "-" * 64 + "\n"
            msg = f"\n\nExpected:{sep}{expected}{sep}\n\nActual:{sep}{actual}{sep}"
            self.fail(msg)


def setup_test_cases():
    tcs = [x.split(".")[0] for x in sorted(os.listdir(TC_DIR)) if x.endswith(".html")]

    for tc in tcs:
        setattr(TestHtmlParser, tc, lambda self, tc=tc: self.checkFormatting(tc))


if __name__ == "__main__":
    setup_test_cases()
    unittest.main()
