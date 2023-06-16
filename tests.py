import os
import subprocess
import unittest

import retronews

TC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")


class TestHtmlRender(unittest.TestCase):
    maxDiff = None

    def checkRendering(self, name: str):
        html_path = os.path.join(TC_DIR, f"{name}.html")
        out_path = os.path.join(TC_DIR, f"{name}.out")

        with open(html_path) as fp:
            html = fp.read()

        actual = retronews.html_render(html)

        if not os.path.exists(out_path):
            with open(out_path, "w") as fp:
                fp.write(actual)
            return

        cmd = ["diff", "-Nru", "--color=always", out_path, "-"]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(input=actual)

        if proc.returncode != 0:
            self.fail(f"Unexpected rendering output\n{stdout}")


def setup_test_cases():
    tcs = [x.split(".")[0] for x in sorted(os.listdir(TC_DIR)) if x.endswith(".html")]

    for tc in tcs:
        setattr(TestHtmlRender, tc, lambda self, tc=tc: self.checkRendering(tc))


if __name__ == "__main__":
    setup_test_cases()
    unittest.main()
