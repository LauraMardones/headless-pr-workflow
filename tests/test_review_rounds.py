import json
import re
from pathlib import Path

import pytest

from headless_pr_workflow import cli
from headless_pr_workflow.github import GHCommandError
from headless_pr_workflow.review_policy import CODEX_BOT_LOGIN, CODEX_REVIEW_TRIGGER_PHRASE
from headless_pr_workflow.review_rounds import (
    build_report,
    closing_issue_number,
    collect_report,
    count_pr,
    matches_flow_filter,
    parse_prs,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "review_rounds.json").read_text(encoding="utf-8"))
P1 = "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Finding"
P2 = "**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Finding"
CODEX = {"login": f"{CODEX_BOT_LOGIN}[bot]"}


def pr(number=1, created="2026-01-01T00:00:00Z", merged="2026-01-01T10:00:00Z"):
    return {"number": number, "title": f"PR {number}", "body": "Closes #9", "created_at": created, "merged_at": merged,
            "changed_files": 2, "additions": 10, "deletions": 3}


def commit(sha, hour):
    return {"sha": sha, "commit": {"committer": {"date": f"2026-01-01T{hour:02d}:00:00Z"}}}


def review(rid, hour, user=CODEX):
    return {"id": rid, "user": user, "submitted_at": f"2026-01-01T{hour:02d}:30:00Z"}


def finding(rid, body=P1, reply_to=None, user=CODEX):
    return {"user": user, "in_reply_to_id": reply_to, "pull_request_review_id": rid, "body": body}


def raw(**parts):
    return {"pull": pr(), "issue_comments": [], "reviews": [], "review_comments": [], "commits": [], **parts}


def test_pr_with_no_codex_review_counts_no_fix_commits():
    row = count_pr(raw(commits=[commit("a", 1), commit("b", 2)]))
    assert (row["codex_reviews"], row["p1_findings"], row["fix_commits"], row["fix_rounds"]) == (0, 0, 0, 0)
    assert row["time_to_merge_seconds"] == 36000 and row["changed_files"] == 2 and row["closing_issue"] == 9


def test_codex_review_with_no_finding_is_no_fix_round():
    row = count_pr(raw(reviews=[review(1, 3)], commits=[commit("a", 1), commit("b", 5)]))
    assert (row["codex_reviews"], row["p1_findings"], row["fix_commits"], row["fix_rounds"]) == (1, 0, 1, 0)


def test_fix_round_with_several_commits_counts_once():
    row = count_pr(raw(reviews=[review(1, 3)], review_comments=[finding(1), finding(1)],
                       commits=[commit("a", 1), commit("b", 4), commit("c", 5), commit("d", 6)]))
    assert (row["p1_findings"], row["fix_commits"], row["fix_rounds"]) == (2, 3, 1)


def test_review_request_with_no_new_commit_is_no_fix_round():
    row = count_pr(raw(
        issue_comments=[{"body": f"{CODEX_REVIEW_TRIGGER_PHRASE}\nplease"}, {"body": f"{CODEX_REVIEW_TRIGGER_PHRASE}"}, {"body": "other"}],
        reviews=[review(1, 3)], review_comments=[finding(1)], commits=[commit("a", 1)],
    ))
    assert (row["review_requests"], row["fix_commits"], row["fix_rounds"]) == (2, 0, 0)


def test_p1_finding_on_older_head_counts_and_opens_a_round():
    row = count_pr(raw(
        reviews=[review(1, 3), review(2, 6)],
        review_comments=[finding(1), finding(2, P2)],
        commits=[commit("a", 1), commit("b", 4), commit("c", 7)],
    ))
    # The P1 review is of an older head; the later review has only P2.
    assert (row["codex_reviews"], row["p1_findings"], row["fix_rounds"], row["fix_commits"]) == (2, 1, 1, 2)


def test_two_p1_reviews_with_no_commit_between_make_one_round():
    row = count_pr(raw(reviews=[review(1, 3), review(2, 4)], review_comments=[finding(1), finding(2)],
                       commits=[commit("a", 1), commit("b", 5)]))
    assert (row["p1_findings"], row["fix_rounds"]) == (2, 1)


def test_replies_humans_and_other_badges_are_not_p1_findings():
    human = {"login": "someone"}
    row = count_pr(raw(reviews=[review(1, 3), review(2, 3, human)],
                       review_comments=[finding(1, reply_to=5), finding(1, user=human), finding(1, P2), finding(1, "**![P0 Badge](x)**")]))
    assert row["p1_findings"] == 1 and row["codex_reviews"] == 1


def test_closing_issue_parse():
    assert closing_issue_number("Closes #369 and more") == 369
    assert closing_issue_number("fixes owner/repo#12") == 12
    assert closing_issue_number("no link") is None


def test_flow_filter_labels():
    assert matches_flow_filter(["type:story", "flow:orchestration"])
    assert matches_flow_filter(["type:bug", "cp:critical-path"])
    assert not matches_flow_filter(["type:story", "prio:P1"])
    assert not matches_flow_filter(["type:epic", "flow:orchestration"])
    assert not matches_flow_filter([])


def _labelled(number, labels):
    return raw(pull=pr(number), closing_issue={"number": number + 100, "labels": labels})


def test_flow_filter_uses_same_counts_as_unfiltered_run():
    raws = [_labelled(1, ["type:story", "flow:orchestration"]), _labelled(2, ["type:story"]), _labelled(3, ["type:task", "cp:critical-path"])]
    for r in raws:
        r["reviews"], r["review_comments"], r["commits"] = [review(1, 3)], [finding(1)], [commit("a", 1), commit("b", 4)]
    full = build_report(raws, repo="o/r", selection={"type": "prs", "value": "1-3"})
    flow = build_report(raws, repo="o/r", selection={"type": "prs", "value": "1-3"}, flow="orchestration-or-critical-path")
    assert [p["number"] for p in full["prs"]] == [1, 2, 3]
    assert [p["number"] for p in flow["prs"]] == [1, 3]
    assert flow["prs"][0] == full["prs"][0]
    assert flow["summary"]["total"]["fix_rounds"] == 2 and "flow:orchestration" in flow["labels_read"]


def test_summary_total_and_median():
    raws = [raw(pull=pr(n), reviews=[review(1, 3)] * n) for n in (1, 2, 6)]
    report = build_report(raws, repo="o/r", selection={"type": "last", "value": 3})
    assert report["summary"]["total"]["codex_reviews"] == 9
    assert report["summary"]["median"]["codex_reviews"] == 2


def test_recorded_fixtures():
    rows = {n: count_pr(r) for n, r in FIXTURES.items()}
    assert (rows["309"]["codex_reviews"], rows["309"]["p1_findings"], rows["309"]["fix_rounds"]) == (0, 0, 0)
    assert (rows["313"]["p1_findings"], rows["313"]["fix_rounds"]) == (1, 1)
    assert (rows["336"]["p1_findings"], rows["336"]["fix_rounds"]) == (2, 2)
    assert rows["354"]["p1_findings"] == 3 and rows["354"]["fix_rounds"] == 1
    assert rows["313"]["closing_issue"] == 311


def test_parse_prs():
    assert parse_prs("309-311") == [309, 310, 311]
    assert parse_prs("309,313,309") == [309, 313]
    for bad in ("", "a", "5-3", "1-"):
        with pytest.raises(ValueError):
            parse_prs(bad)


def _fake_fetch(merged):
    data = FIXTURES

    def fetch(args):
        if args[:2] == ["pr", "list"]:
            return [{"number": n} for n in merged]
        path = args[-1]
        found = re.search(r"/(?:pulls|issues)/(\d+)", path)
        number = int(found.group(1)) if found else None
        if path.endswith(f"pulls/{number}") and str(number) in data and number in merged:
            return {**data[str(number)]["pull"], "merged_at": data[str(number)]["pull"]["merged_at"]}
        if path.endswith(f"pulls/{number}"):
            raise GHCommandError(["gh", *args], 1, "gh: Not Found (HTTP 404)")
        if path.endswith("/comments") and "/issues/" in path:
            return data[str(number)]["issue_comments"]
        if path.endswith("/reviews"):
            return data[str(number)]["reviews"]
        if path.endswith("/comments"):
            return data[str(number)]["review_comments"]
        if path.endswith("/commits"):
            return data[str(number)]["commits"]
        if "/issues/" in path:
            return {"labels": [{"name": l} for l in data[str(merged[0])]["closing_issue"]["labels"]]}
        raise AssertionError(args)

    return fetch


def test_collect_report_lists_skipped_numbers():
    report = collect_report("o/r", prs="313,999", fetch=_fake_fetch([313]))
    assert [p["number"] for p in report["prs"]] == [313]
    assert report["skipped"] == [{"number": 999, "reason": "not a merged pull request"}]
    assert report["selection"] == {"type": "prs", "value": "313,999"}


def test_collect_report_last():
    report = collect_report("o/r", last=1, fetch=_fake_fetch([313]))
    assert report["selection"] == {"type": "last", "value": 1} and report["prs"][0]["p1_findings"] == 1


def test_collect_report_requires_exactly_one_selector():
    with pytest.raises(ValueError):
        collect_report("o/r", fetch=_fake_fetch([]))
    with pytest.raises(ValueError):
        collect_report("o/r", last=1, prs="1", fetch=_fake_fetch([]))


def test_cli_requires_exactly_one_selector(capsys):
    assert cli.main(["review-rounds", "--repo", "o/r"]) == 2
    assert cli.main(["review-rounds", "--repo", "o/r", "--last", "1", "--prs", "1"]) == 2
    assert "exactly one" in capsys.readouterr().err


def test_cli_json_shape_and_text(monkeypatch, capsys):
    monkeypatch.setattr("headless_pr_workflow.cli.collect_report", lambda repo, **kw: _real(repo, fetch=_fake_fetch([313, 336]), **kw))
    assert cli.main(["review-rounds", "--repo", "o/r", "--prs", "313,336,5", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"repo", "selection", "filter", "labels_read", "prs", "skipped", "summary"}
    assert set(data["prs"][0]) == {"number", "title", "closing_issue", "labels", "review_requests", "codex_reviews", "p1_findings",
                                   "fix_commits", "fix_rounds", "changed_files", "additions", "deletions", "merged_at", "time_to_merge_seconds"}
    assert set(data["summary"]) == {"total", "median"}
    assert cli.main(["review-rounds", "--repo", "o/r", "--prs", "313,336,5"]) == 0
    out = capsys.readouterr().out
    assert "Counted PRs: #313, #336" in out and "Skipped: #5" in out


def test_cli_reports_gh_failure(monkeypatch, capsys):
    def boom(repo, **kw):
        raise GHCommandError(["gh"], 1, "boom")

    monkeypatch.setattr("headless_pr_workflow.cli.collect_report", boom)
    assert cli.main(["review-rounds", "--repo", "o/r", "--last", "2"]) == 1
    assert "boom" in capsys.readouterr().err


_real = collect_report
