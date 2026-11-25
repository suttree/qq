"""Build navigation URLs from collected evidence, never from model output."""
from __future__ import annotations

import ast
import hashlib
import re
from urllib.parse import quote

from .core import QQError, parse_pr


def _path(header: str) -> str | None:
    if header == "/dev/null":
        return None
    if header.startswith('"'):
        try:
            header = ast.literal_eval(header)
            # Git quotes non-ASCII path bytes with octal escapes.
            header = header.encode("latin1").decode("utf-8")
        except (ValueError, SyntaxError, UnicodeError):
            return None
    return header[2:] if header.startswith(("a/", "b/")) else None


def _diff_lines(diff: str) -> dict[tuple[str, str, int], str]:
    """Map visible diff lines to GitHub's current file path, including renames."""
    result = {}
    old_path = new_path = None
    old = new = old_left = new_left = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            old_path = new_path = None
            in_hunk = False
        elif not in_hunk and line.startswith("--- "):
            old_path = _path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            new_path = _path(line[4:])
        elif match := re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line):
            old, old_left, new, new_left = (int(match[1]), int(match[2] or 1),
                                           int(match[3]), int(match[4] or 1))
            in_hunk = True
        elif in_hunk:
            diff_path = new_path or old_path
            if line.startswith((" ", "-")) and old_left:
                if old_path and diff_path:
                    result[old_path, "base", old] = diff_path
                old += 1
                old_left -= 1
            if line.startswith((" ", "+")) and new_left:
                if new_path and diff_path:
                    result[new_path, "head", new] = diff_path
                new += 1
                new_left -= 1
            if not old_left and not new_left:
                in_hunk = False
    return result


def evidence_links(packet: dict, report: dict) -> list[list[dict]]:
    """Return links in point/citation order; older saved reviews need no migration."""
    try:
        owner, number = parse_pr(packet["pr"]["url"])
    except (KeyError, QQError):
        return [[{} for _ in point["evidence"]] for point in report["points"]]
    root = f"https://github.com/{owner}"
    pr = f"{root}/pull/{number}"
    visible = _diff_lines(packet.get("diff", ""))
    points = []
    for point in report["points"]:
        links = []
        for citation in point["evidence"]:
            path, side, start = citation["path"], citation["side"], citation["line"]
            end = start + len(citation["quote"].splitlines()) - 1
            link = {}
            sha = packet["pr"].get("headRefOid") if side == "head" else packet.get("merge_base")
            if sha and re.fullmatch(r"[a-f0-9]{40,64}", sha):
                suffix = f"-L{end}" if end > start else ""
                link["source_url"] = f"{root}/blob/{sha}/{quote(path, safe='/')}#L{start}{suffix}"
            diff_path = visible.get((path, side, start))
            if diff_path and all(visible.get((path, side, line)) == diff_path
                                 for line in range(start, end + 1)):
                digest = hashlib.sha256(diff_path.encode()).hexdigest()
                marker = "R" if side == "head" else "L"
                suffix = f"-{marker}{end}" if end > start else ""
                link["pr_url"] = f"{pr}/files#diff-{digest}{marker}{start}{suffix}"
            links.append(link)
        points.append(links)
    return points
