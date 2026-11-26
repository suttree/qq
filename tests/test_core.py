import copy
import errno
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from qq.core import QQError, analyse, collect, correct_citations, filter_points, parse_pr, safe_path, validate
from qq.tui import paragraphs, wrapped


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.empty = {"status": "no_feedback", "summary": "No correctness issue.", "points": []}
        self.packet = {"pr": {"title": "Example"}, "coverage_notes": [],
                       "files": [{"path": "app/a.rb", "side": "head", "start_line": 1,
                                  "text": "class A\n  send_email\nend"}]}
        self.report = {"status": "feedback", "summary": "Discuss delivery.", "points": [{
            "title": "Delivery", "kind": "discussion", "timing": "later",
            "observation": "Email runs inline.", "why": "The request waits for delivery.",
            "question": "What latency is acceptable?", "principle": "Bound request work.",
            "assumptions": ["Delivery time has not been measured."],
            "evidence": [{"path": "app/a.rb", "side": "head", "line": 2, "quote": "  send_email"}]}]}

    def test_valid_evidence(self):
        self.assertEqual(validate(self.report, self.packet), self.report)

    def test_resolves_unique_quote_to_real_source_line(self):
        citation = self.report["points"][0]["evidence"][0]
        citation.update(line=1, quote="send_email")
        validate(self.report, self.packet, resolve_citations=True)
        self.assertEqual(citation["line"], 2)
        self.assertEqual(citation["quote"], "  send_email")

    def test_does_not_resolve_ambiguous_or_invented_quotes(self):
        self.packet["files"][0]["text"] += "\n  send_email"
        citation = self.report["points"][0]["evidence"][0]
        for quote in ["send_email", "imaginary_method"]:
            citation.update(line=1, quote=quote)
            with self.assertRaises(QQError):
                validate(self.report, self.packet, resolve_citations=True)

    def test_no_feedback_is_valid(self):
        validate({"status": "no_feedback", "summary": "No worthwhile discussion in this change.", "points": []}, self.packet)

    def test_missing_context_is_distinct(self):
        validate({"status": "needs_context", "summary": "Need caller's loading behaviour.", "points": []}, self.packet)

    def test_rejects_more_than_two(self):
        self.report["points"] *= 3
        with self.assertRaises(QQError):
            validate(self.report, self.packet)

    def test_rejects_fabricated_evidence(self):
        for key, value in [("path", "other.rb"), ("line", 3), ("quote", "  invented"), ("side", "base")]:
            report = copy.deepcopy(self.report)
            report["points"][0]["evidence"][0][key] = value
            with self.subTest(key=key), self.assertRaises(QQError):
                validate(report, self.packet)

    def test_rejects_inconsistent_status(self):
        self.report["status"] = "no_feedback"
        with self.assertRaises(QQError):
            validate(self.report, self.packet)

    def test_rejects_missing_explanation(self):
        self.report["points"][0]["why"] = " "
        with self.assertRaises(QQError):
            validate(self.report, self.packet)

    def test_relevance_check_can_discard_every_point(self):
        checked = filter_points(self.report, {"decisions": [
            {"index": 0, "keep": False, "kind": "discussion", "reason": "The impact is overstated."}]})
        self.assertEqual(checked["status"], "no_feedback")
        self.assertEqual(checked["points"], [])
        self.assertEqual(len(self.report["points"]), 1)

    def test_relevance_check_can_correct_kind_without_rewriting_claims(self):
        checked = filter_points(self.report, {"decisions": [
            {"index": 0, "keep": True, "kind": "strength", "reason": "A deliberate decision."}]})
        self.assertEqual(checked["points"][0]["kind"], "strength")
        self.assertEqual(checked["points"][0]["observation"], self.report["points"][0]["observation"])

    def test_missing_relevance_decision_is_an_error(self):
        with self.assertRaises(QQError):
            filter_points(self.report, {"decisions": []})

    def test_analysis_runs_final_check_before_accepting_points(self):
        self.packet.update(pr={"title": "Example"}, coverage_notes=[],
                           diff="@@ -1 +1 @@\n-old\n+new", changed_paths=["app/a.rb"])
        verdict = {"decisions": [{"index": 0, "keep": False, "kind": "discussion", "reason": "Unsupported impact."}]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, verdict]) as model:
            result = analyse(self.packet, "local-model", "http://127.0.0.1:11434", audit=audit)
        self.assertEqual(model.call_count, 3)
        checked_evidence = model.call_args.args[4]
        self.assertEqual(checked_evidence["diff"], self.packet["diff"])
        self.assertEqual(checked_evidence["changed_paths"], ["app/a.rb"])
        self.assertEqual(result["status"], "no_feedback")
        self.assertEqual(len(audit["draft"]["points"]), 1)

    def test_correctness_pass_runs_first_and_two_bugs_take_priority(self):
        bugs = copy.deepcopy(self.report)
        bugs["points"].append(dict(bugs["points"][0], title="Another issue"))
        keep = {"decisions": [{"index": 0, "keep": True, "kind": "discussion", "reason": "Supported."}]}
        keep_both = {"decisions": keep["decisions"] + [dict(keep["decisions"][0], index=1)]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[bugs, keep_both, self.report, keep]) as model:
            result = analyse(self.packet, "local-model", "http://localhost:11434", audit=audit)
        self.assertEqual(result["status"], "feedback")
        self.assertEqual(result["points"], bugs["points"])
        self.assertIn("bugs and broken contracts", model.call_args_list[0].args[3])
        self.assertIn("style choices", model.call_args_list[2].args[3])
        self.assertEqual(model.call_args_list[2].args[4]["correctness_findings"], bugs["points"])
        self.assertEqual(audit["correctness"]["raw_draft"], bugs)
        self.assertEqual(audit["raw_draft"], self.report)

    def test_retained_points_still_require_exact_source_citations(self):
        source = '  document.querySelector(".profile-tabs [aria-current]")?.scrollIntoView({ block: "nearest", inline: "nearest" });'
        self.packet["files"][0]["text"] = "// Tabs\n// Keep selected tab visible\n" + source
        self.packet.update(pr={"title": "Example"}, coverage_notes=[])
        citation = self.report["points"][0]["evidence"][0]
        citation.update(line=2, quote=source.strip().replace('"', "'"))
        self.report["points"][0]["evidence"].extend([
            dict(citation, line=1, quote="// Tabs"),
            dict(citation, line=2, quote="// Keep selected tab visible"),
        ])
        original = copy.deepcopy(self.report)
        # Quotation marks inside code are not safe to change by fuzzy matching.
        with self.assertRaises(QQError):
            validate(copy.deepcopy(self.report), self.packet, resolve_citations=True)
        correction = {"evidence": [{"start_line": 3, "end_line": 3},
                                   {"start_line": 1, "end_line": 1},
                                   {"start_line": 2, "end_line": 2}]}
        verdict = {"decisions": [{"index": 0, "keep": True, "kind": "discussion", "reason": "Supported mechanism."}]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, verdict, correction, verdict]) as model:
            result = analyse(self.packet, "local-model", "http://127.0.0.1:11434", audit=audit)
        self.assertEqual(model.call_count, 5)
        ranges = model.call_args_list[3].args[2]["properties"]["evidence"]
        self.assertEqual((ranges["minItems"], ranges["maxItems"]), (3, 3))
        self.assertEqual(result["status"], "feedback")
        self.assertEqual(audit["raw_draft"], original)
        self.assertEqual(self.report, original)
        self.assertEqual(audit["pre_citation_check"]["draft"], original)
        corrected = copy.deepcopy(result["points"][0])
        self.assertEqual(corrected["evidence"][0]["quote"], source)
        corrected["evidence"] = original["points"][0]["evidence"]
        self.assertEqual(corrected, original["points"][0])

    def test_corrected_citations_are_rechecked_before_acceptance(self):
        self.report["points"][0]["evidence"][0]["quote"] = "invented"
        keep = {"decisions": [{"index": 0, "keep": True, "kind": "discussion", "reason": "Appears supported."}]}
        correction = {"evidence": [{"start_line": 3, "end_line": 3}]}
        discard = {"decisions": [{"index": 0, "keep": False, "kind": "discussion", "reason": "The selected line does not support the claim."}]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, keep, correction, discard]) as model:
            result = analyse(self.packet, "local-model", "http://localhost:11434", audit=audit)
        self.assertEqual(model.call_count, 5)
        self.assertEqual(result["status"], "no_feedback")
        self.assertEqual(audit["decisions"], discard)
        self.assertEqual(audit["pre_citation_check"]["decisions"], keep)

    def test_unsupported_draft_is_discarded_before_quote_repair(self):
        self.report["points"][0]["evidence"][0].update(line=999, quote="invented")
        verdict = {"decisions": [{"index": 0, "keep": False, "kind": "discussion", "reason": "The source does not support the claim."}]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, verdict]) as model:
            result = analyse(self.packet, "local-model", "http://localhost:11434", audit=audit)
        self.assertEqual(model.call_count, 3)
        self.assertEqual(result["status"], "no_feedback")
        self.assertEqual(audit["raw_draft"], self.report)
        self.assertEqual(audit["decisions"], verdict)
        self.assertNotIn("citation_correction", audit)

    def test_failed_relevance_check_is_not_no_feedback(self):
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, QQError("Relevance check unavailable")]):
            with self.assertRaisesRegex(QQError, "Relevance check unavailable"):
                analyse(self.packet, "local-model", "http://localhost:11434")

    def test_bad_correction_stops_after_one_attempt(self):
        self.report["points"][0]["evidence"][0]["quote"] = "invented"
        correction = {"evidence": [self.report["points"][0]["evidence"]]}
        verdict = {"decisions": [{"index": 0, "keep": True, "kind": "discussion", "reason": "Supported mechanism."}]}
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, self.report, verdict, correction]) as model:
            with self.assertRaisesRegex(QQError, "after one correction attempt"):
                analyse(self.packet, "local-model", "http://127.0.0.1:11434", audit=audit)
        self.assertEqual(model.call_count, 4)
        self.assertEqual(audit["citation_correction"], correction)
        self.assertEqual(audit["decisions"], verdict)

    def test_inconsistent_status_gets_one_correction_and_preserves_draft(self):
        for status in ("needs_context", "no_feedback"):
            with self.subTest(status=status):
                draft = dict(self.report, status=status)
                corrected = {"status": status, "summary": "Insufficient evidence.", "points": []}
                audit = {}
                responses = [self.empty, draft, corrected]
                with patch("qq.core.model_json", side_effect=responses) as model:
                    result = analyse(self.packet, "local-model", "http://localhost:11434", audit=audit)
                self.assertEqual(model.call_count, len(responses))
                self.assertEqual(result, corrected if status == "needs_context" else
                                 dict(corrected, summary=self.empty["summary"] + " " + corrected["summary"]))
                self.assertEqual(audit["raw_draft"], draft)
                self.assertEqual(audit["status_correction"], corrected)

    def test_status_correction_with_feedback_still_checks_relevance(self):
        self.packet.update(pr={"title": "Example"}, coverage_notes=[])
        draft = dict(self.report, points=[])
        verdict = {"decisions": [{"index": 0, "keep": False, "kind": "discussion", "reason": "Unsupported."}]}
        with patch("qq.core.model_json", side_effect=[self.empty, draft, self.report, verdict]) as model:
            result = analyse(self.packet, "local-model", "http://localhost:11434")
        self.assertEqual(model.call_count, 4)
        self.assertEqual(result["status"], "no_feedback")

    def test_inconsistent_status_correction_still_fails(self):
        draft = dict(self.report, status="needs_context")
        audit = {}
        with patch("qq.core.model_json", side_effect=[self.empty, draft, draft]) as model:
            with self.assertRaisesRegex(QQError, "after one correction attempt"):
                analyse(self.packet, "local-model", "http://localhost:11434", audit=audit)
        self.assertEqual(model.call_count, 3)
        self.assertEqual(audit["status_correction"], draft)
        self.assertNotIn("decisions", audit)

    def test_correction_cannot_drop_points_citations_or_change_files(self):
        citation = self.report["points"][0]["evidence"][0]
        for evidence in [[], [[]], [[dict(citation, path="other.rb")]],
                         [[dict(citation, side="base")]], [[citation, citation]],
                         [[dict(citation, quote="invented")]], [[dict(citation, line=True)]],
                         [{"start_line": 2, "end_line": 2, "path": "other.rb"}]]:
            with self.subTest(evidence=evidence), self.assertRaises(QQError):
                correct_citations(self.report, {"evidence": evidence}, self.packet)

    def test_flat_source_ranges_preserve_point_and_citation_order(self):
        report = copy.deepcopy(self.report)
        report["points"].append(copy.deepcopy(report["points"][0]))
        report["points"][0]["evidence"].append(copy.deepcopy(report["points"][0]["evidence"][0]))
        correction = {"evidence": [{"start_line": line, "end_line": line} for line in (1, 2, 3)]}
        result = correct_citations(report, correction, self.packet)
        self.assertEqual([[cite["quote"] for cite in point["evidence"]] for point in result["points"]],
                         [["class A", "  send_email"], ["end"]])
        self.assertEqual(report["points"][0]["evidence"][0]["line"], 2)

    def test_correction_copies_exact_ruby_source_with_excerpt_offset(self):
        source = '    profiles = profiles.where("location LIKE ?", "%#{Submission.sanitize_sql_like(@location)}%") if @location.present?'
        self.packet["files"][0].update(start_line=39, text=source + "\n    if @skill.present?")
        self.report["points"][0]["evidence"][0].update(line=39, quote="profiles.where( location LIKE ? )")
        original = copy.deepcopy(self.report)
        corrected = correct_citations(self.report, {"evidence": [{"start_line": 39, "end_line": 40}]}, self.packet)
        citation = corrected["points"][0]["evidence"][0]
        self.assertEqual(citation["quote"], self.packet["files"][0]["text"])
        self.assertEqual(citation["line"], 39)
        self.assertEqual(self.report, original)
        validate(corrected, self.packet)

    def test_correction_rejects_invalid_or_unavailable_ranges(self):
        self.packet["files"][0]["start_line"] = 39
        for start, end in [(0, 0), (38, 39), (39, 42), (41, 39), (True, 40), (39, "40")]:
            with self.subTest(start=start, end=end), self.assertRaises(QQError):
                correct_citations(self.report, {"evidence": [{"start_line": start, "end_line": end}]}, self.packet)

    def test_malformed_draft_does_not_trigger_citation_retry(self):
        del self.report["points"][0]["why"]
        with patch("qq.core.model_json", return_value=self.report) as model:
            with self.assertRaises(QQError):
                analyse(self.packet, "local-model", "http://127.0.0.1:11434")
        self.assertEqual(model.call_count, 1)

    def test_cli_preserves_failed_draft_without_saving_a_review(self):
        import contextlib
        import io
        from qq.__main__ import main
        self.packet["id"] = "citation-failure"
        self.report["points"][0]["evidence"][0]["quote"] = "invented"
        correction = {"evidence": [self.report["points"][0]["evidence"]]}
        verdict = {"decisions": [{"index": 0, "keep": True, "kind": "discussion", "reason": "Supported mechanism."}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet_path = root / "input.json"
            packet_path.write_text(json.dumps(self.packet))
            stderr = io.StringIO()
            with patch("sys.argv", ["qq", "--provider", "ollama", "--packet", str(packet_path), "--state-dir", str(root), "--json"]), \
                    patch("qq.core.model_json", side_effect=[self.empty, self.report, verdict, correction]), \
                    contextlib.redirect_stderr(stderr):
                self.assertEqual(main(), 1)
            failures = list(root.glob("*.failure.json"))
            self.assertEqual(len(failures), 1)
            failure = json.loads(failures[0].read_text())
            self.assertEqual(failure["assessment"]["raw_draft"], self.report)
            self.assertEqual(failure["assessment"]["citation_correction"], correction)
            self.assertIn(str(failures[0]), stderr.getvalue())
            self.assertEqual(list(root.glob("*.review.json")), [])
            self.assertTrue((root / "citation-failure.packet.json").exists())

    def test_model_failure_is_not_no_feedback(self):
        with patch("qq.core.urlopen", side_effect=OSError("model unavailable")):
            with self.assertRaisesRegex(QQError, "did not return a usable result"):
                analyse(self.packet, "local-model", "http://127.0.0.1:11434")

    def test_ollama_receives_schema_in_both_prompt_and_format(self):
        import io
        from qq.core import model_json
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"verdict": {"enum": ["schema-sentinel"]}},
                  "required": ["verdict"]}
        envelope = {"message": {"content": '{"verdict": "schema-sentinel"}'}}
        with patch("qq.core.urlopen", return_value=io.StringIO(json.dumps(envelope))) as request:
            result = model_json("local-model", "http://localhost:11434", schema,
                                "Follow the coaching rubric.", {})
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["format"], schema)
        self.assertIn("schema-sentinel", body["messages"][0]["content"])
        self.assertIn("Follow the coaching rubric.", body["messages"][0]["content"])
        self.assertEqual(result, {"verdict": "schema-sentinel"})

    def test_connection_refused_explains_how_to_start_ollama(self):
        endpoint = "http://localhost:12345"
        refused = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        for error in (refused, URLError(refused)):
            with self.subTest(error=error), patch("qq.core.urlopen", side_effect=error):
                with self.assertRaises(QQError) as raised:
                    analyse(self.packet, "local-model", endpoint)
                message = str(raised.exception)
                self.assertIn(f"Cannot connect to Ollama at {endpoint}", message)
                self.assertIn("Ollama may be stopped", message)
                self.assertIn("ollama serve", message)
                self.assertIn("--endpoint", message)

    def test_other_model_errors_do_not_claim_ollama_is_stopped(self):
        for error in (URLError(TimeoutError("timed out")),
                      HTTPError("http://localhost:11434/api/chat", 404, "model not found", {}, None),
                      ValueError("invalid JSON")):
            if isinstance(error, HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=error), patch("qq.core.urlopen", side_effect=error):
                with self.assertRaisesRegex(QQError, "did not return a usable result"):
                    analyse(self.packet, "local-model", "http://localhost:11434")

    def test_rejects_remote_model_endpoint(self):
        with self.assertRaises(QQError):
            analyse(self.packet, "any", "https://remote.example")

    def test_pr_url_validation(self):
        self.assertEqual(parse_pr("https://github.com/suttree/commonground/pull/11"), ("suttree/commonground", 11))
        for url in ["https://evil.test/a/b/pull/1", "https://github.com/a/b/pull/1;rm", "--help"]:
            with self.assertRaises(QQError):
                parse_pr(url)

    def test_excludes_sensitive_paths(self):
        for path in [".env.production", "config/master.key", "config/credentials.yml.enc", "../outside.rb", "/tmp/out.rb"]:
            self.assertFalse(safe_path(path))

    def test_terminal_control_characters_removed(self):
        self.assertNotIn("\x1b", "".join(wrapped(["hello\x1b[2J"], 80)))

    def test_empty_state_and_missing_context_copy(self):
        review = {"packet": {}, "report": {"status": "no_feedback", "summary": "Small change.", "points": []}}
        self.assertEqual(paragraphs(review, 0, 0)[0], "No feedback worth raising")
        review["report"]["status"] = "needs_context"
        self.assertEqual(paragraphs(review, 0, 0)[0], "More context needed")


class HistoricalContextTests(unittest.TestCase):
    def test_uses_historical_head_and_merge_base_without_touching_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()
            git("init", "-q")
            git("config", "user.email", "test@example.com")
            git("config", "user.name", "Test")
            git("remote", "add", "origin", "https://github.com/example/project.git")
            padding = "# Earlier code in this historical file.\n" * 150
            (repo / "model.rb").write_text(padding + "class Model\nend\n")
            view = repo / "report.html.haml"
            view.write_text("# Earlier view code.\n" * 200 + "= report.historical\n")
            (repo / "caller.rb").write_text("report.historical\n")
            (repo / "model_test.rb").write_text("assert report.historical\n")
            for index in range(800):
                (repo / f"a{index:03}.rb").touch()
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "model.rb").write_text(padding + "class Model\n  def historical; end\nend\n")
            (repo / "expected.xml").write_text("<Feed><Job /></Feed>\n")
            git("add", "model.rb", "expected.xml")
            git("commit", "-qm", "feature")
            head = git("rev-parse", "HEAD")
            (repo / "model.rb").write_text("UNCOMMITTED CURRENT WORK\n")
            (repo / ".env").write_text("DO_NOT_INCLUDE=secret\n")
            meta = {"headRefOid": head, "baseRefOid": base, "number": 1,
                    "title": "Test", "body": "", "url": "https://github.com/example/project/pull/1"}
            from qq.core import run as real_run
            def fake_run(*args, **kwargs):
                return json.dumps(meta) if args[0] == "gh" else real_run(*args, **kwargs)
            with patch("qq.core.run", fake_run):
                packet = collect(meta["url"], repo)
            excerpts = {file["path"]: file for file in packet["files"]}
            self.assertIn("def historical", excerpts["model.rb"]["text"])
            self.assertGreater(excerpts["model.rb"]["start_line"], 1)
            self.assertIn("= report.historical", excerpts["report.html.haml"]["text"])
            self.assertGreater(excerpts["report.html.haml"]["start_line"], 1)
            self.assertEqual(excerpts["expected.xml"]["text"], "<Feed><Job /></Feed>")
            self.assertNotIn("UNCOMMITTED", json.dumps(packet))
            self.assertNotIn("DO_NOT_INCLUDE", json.dumps(packet))
            self.assertEqual((repo / "model.rb").read_text(), "UNCOMMITTED CURRENT WORK\n")
            self.assertEqual(packet["merge_base"], base)


if __name__ == "__main__":
    unittest.main()
