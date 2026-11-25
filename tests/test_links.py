import hashlib
import unittest
from unittest.mock import patch

from qq.links import evidence_links
from qq.tui import open_evidence, paragraphs


class EvidenceLinkTests(unittest.TestCase):
    def setUp(self):
        self.packet = {
            "pr": {"url": "https://github.com/example/project/pull/7", "headRefOid": "a" * 40},
            "merge_base": "b" * 40,
            "files": [{"path": "a.py", "side": "head", "start_line": 10, "text": "new\nnext"}],
            "diff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -10,2 +10,3 @@\n-old\n+new\n+next\n context\n",
        }
        self.citation = {"path": "a.py", "side": "head", "line": 10, "quote": "new\nnext"}
        self.report = {"points": [{"evidence": [self.citation]}]}
        self.review = {"packet": self.packet, "report": self.report}

    def link(self):
        return evidence_links(self.packet, self.report)[0][0]

    def test_head_range_and_removed_base_line(self):
        digest = hashlib.sha256(b"a.py").hexdigest()
        self.assertEqual(self.link(), {
            "pr_url": f"https://github.com/example/project/pull/7/files#diff-{digest}R10-R11",
            "source_url": f"https://github.com/example/project/blob/{'a' * 40}/a.py#L10-L11"})
        self.citation.update(side="base", quote="old")
        self.assertTrue(self.link()["pr_url"].endswith(f"{digest}L10"))
        self.assertIn(f"/blob/{'b' * 40}/", self.link()["source_url"])

    def test_supporting_code_or_range_outside_diff_uses_source(self):
        for update in [{"path": "other #?.py"}, {"line": 1}, {"quote": "new\nnext\ncontext\nmore"}]:
            with self.subTest(update=update):
                original = dict(self.citation)
                self.citation.update(update)
                self.assertNotIn("pr_url", self.link())
                self.assertIn("source_url", self.link())
                self.citation.update(original)
        self.citation["path"] = "other #?.py"
        self.assertIn("other%20%23%3F.py", self.link()["source_url"])

    def test_deleted_file_uses_left_side(self):
        self.packet["diff"] = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ /dev/null\n@@ -10 +0,0 @@\n-old\n"
        self.citation.update(side="base", quote="old")
        self.assertTrue(self.link()["pr_url"].endswith("L10"))

    def test_rename_links_old_base_path_to_new_diff_path(self):
        self.packet["diff"] = self.packet["diff"].replace("b/a.py", "b/new.py")
        self.citation.update(side="base", quote="old")
        digest = hashlib.sha256(b"new.py").hexdigest()
        self.assertTrue(self.link()["pr_url"].endswith(f"{digest}L10"))
        self.assertIn("/a.py#", self.link()["source_url"])

    def test_git_quoted_unicode_and_spaces(self):
        self.packet["diff"] = 'diff --git a/x b/x\n--- "a/caf\\303\\251 name.py"\n+++ "b/caf\\303\\251 name.py"\n@@ -10 +10 @@\n-old\n+new\n'
        self.citation.update(path="café name.py", quote="new")
        digest = hashlib.sha256("café name.py".encode()).hexdigest()
        self.assertTrue(self.link()["pr_url"].endswith(f"{digest}R10"))

    def test_multiple_hunks_and_truncation(self):
        self.packet["diff"] += "@@ -30,2 +31,2 @@\n-final\n+last\n[DIFF TRUNCATED]"
        self.citation.update(line=31, quote="last")
        self.assertTrue(self.link()["pr_url"].endswith("R31"))
        self.citation.update(line=32, quote="unseen")
        self.assertNotIn("pr_url", self.link())

    def test_missing_or_untrusted_pr_url_never_opens_browser(self):
        for url in [None, "file:///etc/passwd", "https://github.com.evil.test/a/b/pull/7"]:
            self.packet["pr"]["url"] = url or ""
            with patch("qq.tui.webbrowser.open") as browser:
                self.assertIn("no GitHub link", open_evidence(self.review, 0))
            browser.assert_not_called()

    def test_shortcuts_open_only_requested_citation_and_handle_failure(self):
        self.report["points"][0]["evidence"].append(dict(self.citation, path="other.py"))
        with patch("qq.tui.webbrowser.open", return_value=True) as browser:
            self.assertIn("Opened", open_evidence(self.review, 0))
            self.assertIn("/pull/7/files#", browser.call_args.args[0])
            open_evidence(self.review, 0, 1)
            self.assertIn("/other.py#", browser.call_args.args[0])
            self.assertIn("No cited line", open_evidence(self.review, 0, 2))
            self.assertEqual(browser.call_count, 2)
        with patch("qq.tui.webbrowser.open", side_effect=OSError("no browser")):
            self.assertIn("Could not open", open_evidence(self.review, 0))
        self.report["points"] = []
        self.assertIn("No cited line", open_evidence(self.review, 0))

    def test_evidence_view_displays_both_links_for_old_saved_review(self):
        lines = paragraphs(self.review, 0, 1)
        self.assertIn(self.link()["pr_url"], lines)
        self.assertIn(self.link()["source_url"], lines)
