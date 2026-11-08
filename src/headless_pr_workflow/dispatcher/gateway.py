"""The one gateway between the dispatcher building blocks and GitHub (ADR-014 → Decision → 2).

Every call runs ``gh`` through ``subprocess`` with the token ``gh`` already uses
(``GH_TOKEN``). No other HTTP client. A failed call raises ``GatewayError`` with
the step and ``gh``'s last stderr line; the token never appears in it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

TOKEN_ENV_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")

BOARD_QUERY = """query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    projectsV2(first: 1) {
      nodes {
        id
        fields(first: 30) {
          nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
        }
      }
    }
    issue(number: $number) {
      projectItems(first: 20) {
        nodes {
          id
          project { id }
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        }
      }
    }
  }
}"""

BOARD_ITEMS_QUERY = """query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    projectsV2(first: 1) {
      nodes {
        id
        fields(first: 30) {
          nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
        }
        items(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            content { __typename ... on Issue { number state labels(first: 50) { nodes { name } } } }
            fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
          }
        }
      }
    }
  }
}"""

BOARD_ITEMS_MAX_PAGES = 50

STATUS_MUTATION = """mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
    value: { singleSelectOptionId: $optionId }
  }) { projectV2Item { id } }
}"""


class GatewayError(Exception):
    """A GitHub call that failed or returned something unusable."""

    def __init__(self, step: str, reason: str, *, not_found: bool = False) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason
        self.not_found = not_found


def mask_tokens(text: str, env: dict[str, str] | None = None) -> str:
    """Replace every token value of the environment in ``text`` with ``***``."""

    env = os.environ if env is None else env
    for name in TOKEN_ENV_NAMES:
        value = env.get(name) or ""
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text


@dataclass(frozen=True)
class BoardItem:
    """An issue's item on the repository's first Projects v2 board, read fresh."""

    project_id: str
    field_id: str
    options: dict[str, str] = field(default_factory=dict)
    item_id: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class BoardCard:
    """One item of the board. ``number`` is ``None`` for an item that is not an issue (a draft or a pull request)."""

    item_id: str
    number: int | None = None
    state: str | None = None  # "open" or "closed" for an issue
    labels: tuple[str, ...] = ()
    status: str | None = None


@dataclass(frozen=True)
class BoardSnapshot:
    """Every item of the repository's first Projects v2 board and the Status options (issue #476)."""

    options: dict[str, str] = field(default_factory=dict)
    cards: list[BoardCard] = field(default_factory=list)


class Gateway(Protocol):
    """What Block 1 needs from GitHub. ``GhGateway`` is the real one; tests use a fake."""

    def viewer_login(self) -> str: ...

    def list_comments(self, number: int) -> list[dict[str, Any]]: ...

    def post_comment(self, number: int, body: str) -> dict[str, Any]: ...

    def board_item(self, issue: int) -> BoardItem: ...

    def set_board_status(self, item: BoardItem, option_id: str) -> None: ...

    def issue_labels(self, number: int) -> list[str]: ...

    def add_label(self, number: int, name: str) -> None: ...

    def remove_label(self, number: int, name: str) -> None: ...

    def create_label(self, name: str, color: str, description: str) -> None: ...

    # Read operations of the facts reader (``facts.py``, issue #283).

    def list_open_issues(self) -> list[dict[str, Any]]: ...

    def issue_state(self, number: int) -> str: ...

    def list_dir(self, path: str) -> list[dict[str, Any]]: ...

    def newest_commit(self, path: str) -> dict[str, str] | None: ...

    def compare_commits(self, base: str, head: str) -> str: ...

    # Read operations of the status rule (``status.py``, issue #258).

    def list_open_prs(self) -> list[dict[str, Any]]: ...

    def pr_files(self, number: int) -> list[str]: ...

    def list_closed_issues(self) -> list[dict[str, Any]]: ...

    def merge_gates_pass(self, number: int, head: str) -> bool: ...

    # Board sync (``board_sync.py``, issue #476).

    def list_board_items(self) -> BoardSnapshot: ...

    def issue_body(self, number: int) -> str: ...

    def edit_issue_body(self, number: int, body: str) -> None: ...


def parse_json_stream(text: str) -> list[Any]:
    """The JSON values in ``text``, one after another (``gh api --paginate`` prints one per page)."""

    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            return values
        value, index = decoder.raw_decode(text, index)
        values.append(value)


def _missing_scope(detail: str) -> str | None:
    """The bookkeeping job's missing-scope message; imported only on a failed call (it is a heavy import)."""

    from ..codex_review_bookkeeping import missing_scope_message

    return missing_scope_message(detail)


_MERGE_GATE: Any = None


def _merge_gate_module() -> Any:
    """``scripts/merge-gate-summary`` loaded as a module: the one copy of the merge-gate evaluation."""

    global _MERGE_GATE
    if _MERGE_GATE is None:
        from importlib import util
        from importlib.machinery import SourceFileLoader
        from pathlib import Path

        script = Path(__file__).resolve().parents[3] / "scripts" / "merge-gate-summary"
        if not script.is_file():
            raise GatewayError("evaluate merge gates", f"{script} not found")
        loader = SourceFileLoader("hpw_merge_gate_summary", str(script))
        spec = util.spec_from_loader(loader.name, loader)
        if spec is None:
            raise GatewayError("evaluate merge gates", "scripts/merge-gate-summary could not be loaded")
        module = util.module_from_spec(spec)
        sys.modules[spec.name] = module  # its dataclass needs the module registered while it runs
        try:
            loader.exec_module(module)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        _MERGE_GATE = module
    return _MERGE_GATE


class GhGateway:
    """``Gateway`` backed by the authenticated ``gh`` CLI.

    ``gh_command`` is the command prefix that runs ``gh``. Tests on Windows pass
    ``("bash", "<fake gh>")``, because Python cannot start a bash script directly there.
    """

    def __init__(self, repo: str, *, gh_command: Sequence[str] = ("gh",)) -> None:
        owner, _, name = repo.partition("/")
        self.repo = repo
        self.owner = owner
        self.name = name
        self.gh_command = tuple(gh_command)
        self._login: str | None = None

    # ── reads ────────────────────────────────────────────────────────────────

    def viewer_login(self) -> str:
        if self._login is None:
            login = (os.environ.get("DISPATCHER_LOGIN") or "").strip()
            if not login:
                payload = self._json("resolve dispatcher login", ["api", "user"])
                login = str(payload.get("login") or "") if isinstance(payload, dict) else ""
            if not login:
                raise GatewayError("resolve dispatcher login", "the login behind GH_TOKEN could not be read")
            self._login = login
        return self._login

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        step = f"read comments on #{number}"
        output = self._run(step, ["api", "--paginate", f"repos/{self.repo}/issues/{number}/comments?per_page=100"])
        return self._pages(step, output, what="comments")

    def list_open_issues(self) -> list[dict[str, Any]]:
        """Every open issue of the repository; pull requests (which the issues API also lists) are left out."""

        step = "read open issues"
        output = self._run(step, ["api", "--paginate", f"repos/{self.repo}/issues?state=open&per_page=100"])
        return [item for item in self._pages(step, output) if "pull_request" not in item]

    def issue_state(self, number: int) -> str:
        step = f"read state of #{number}"
        payload = self._json(step, ["api", f"repos/{self.repo}/issues/{number}"])
        state = payload.get("state") if isinstance(payload, dict) else None
        if state not in ("open", "closed"):
            raise GatewayError(step, "GitHub returned an issue that could not be parsed")
        return str(state)

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        """The entries of a folder on ``main``; raises with ``not_found`` when the folder does not exist."""

        step = f"list {path} on main"
        payload = self._json(step, ["api", f"repos/{self.repo}/contents/{path}?ref=main"])
        if not isinstance(payload, list):
            raise GatewayError(step, "GitHub returned a folder listing that could not be parsed")
        return [item for item in payload if isinstance(item, dict)]

    def newest_commit(self, path: str) -> dict[str, str] | None:
        """``{"sha", "date"}`` of the newest commit on ``main`` that changed ``path``; ``None`` when none did."""

        step = f"read newest commit of {path}"
        payload = self._json(step, ["api", f"repos/{self.repo}/commits?path={path}&sha=main&per_page=1"])
        if not isinstance(payload, list):
            raise GatewayError(step, "GitHub returned commits that could not be parsed")
        if not payload:
            return None
        commit = payload[0] if isinstance(payload[0], dict) else {}
        sha = commit.get("sha")
        date = ((commit.get("commit") or {}).get("committer") or {}).get("date")
        if not sha or not date:
            raise GatewayError(step, "GitHub returned a commit that could not be parsed")
        return {"sha": str(sha), "date": str(date)}

    def compare_commits(self, base: str, head: str) -> str:
        """GitHub's compare status of ``head`` against ``base``; raises with ``not_found`` for an unknown commit."""

        step = f"compare {base} with {head}"
        payload = self._json(step, ["api", f"repos/{self.repo}/compare/{base}...{head}"])
        status = payload.get("status") if isinstance(payload, dict) else None
        if status not in ("identical", "ahead", "behind", "diverged"):
            raise GatewayError(step, "GitHub returned a comparison that could not be parsed")
        return str(status)

    def list_open_prs(self) -> list[dict[str, Any]]:
        """Every open pull request (all pages), as the pulls API returns it."""

        step = "read open pull requests"
        output = self._run(step, ["api", "--paginate", f"repos/{self.repo}/pulls?state=open&per_page=100"])
        return self._pages(step, output, what="pull requests")

    def pr_files(self, number: int) -> list[str]:
        """The paths a pull request changes."""

        step = f"read files of PR #{number}"
        output = self._run(step, ["api", "--paginate", f"repos/{self.repo}/pulls/{number}/files?per_page=100"])
        return [str(item["filename"]) for item in self._pages(step, output, what="files") if item.get("filename")]

    def list_closed_issues(self) -> list[dict[str, Any]]:
        """Every closed issue of the repository; pull requests are left out."""

        step = "read closed issues"
        output = self._run(step, ["api", "--paginate", f"repos/{self.repo}/issues?state=closed&per_page=100"])
        return [item for item in self._pages(step, output) if "pull_request" not in item]

    def merge_gates_pass(self, number: int, head: str) -> bool:
        """Whether every merge gate passes on PR ``number`` at ``head``, by the evaluation of scripts/merge-gate-summary.

        Raises ``GatewayError`` when the gates cannot be read. A result for another head than ``head`` is not a pass.
        """

        step = f"evaluate merge gates of PR #{number}"
        try:
            result = _merge_gate_module().evaluate(number, self.repo)
        except GatewayError:
            raise
        except Exception as exc:  # GHCommandError, ValueError, an unloadable script: all mean "not readable"
            raise GatewayError(step, mask_tokens(str(exc).strip().splitlines()[-1][:300] if str(exc).strip() else type(exc).__name__)) from None
        return bool(result.passed) and bool(head) and head.lower().startswith(result.head.lower())

    def board_item(self, issue: int) -> BoardItem:
        step = "read board"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={BOARD_QUERY}", "-f", f"owner={self.owner}",
             "-f", f"repo={self.name}", "-F", f"number={issue}"],
        )  # fmt: skip
        if not isinstance(payload, dict):
            raise GatewayError(step, "GitHub returned a board that could not be parsed")
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message")) for e in payload["errors"] if isinstance(e, dict))
            raise GatewayError(step, f"GraphQL returned errors ({messages[:300]}); the token needs the 'project' and 'repo' scopes")
        repository = (payload.get("data") or {}).get("repository") or {}
        projects = ((repository.get("projectsV2") or {}).get("nodes")) or []
        if not projects or not isinstance(projects[0], dict) or not projects[0].get("id"):
            raise GatewayError(step, f"no Projects v2 board is visible on {self.repo}; the token needs the 'project' scope")
        project = projects[0]
        status_field = next(
            (f for f in ((project.get("fields") or {}).get("nodes") or []) if isinstance(f, dict) and f.get("name") == "Status"),
            None,
        )
        if status_field is None or not status_field.get("id"):
            raise GatewayError(step, "the board has no Status field")
        options = {
            str(o["name"]): str(o["id"])
            for o in status_field.get("options") or []
            if isinstance(o, dict) and o.get("name") and o.get("id")
        }
        items = (((repository.get("issue") or {}).get("projectItems") or {}).get("nodes")) or []
        item = next(
            (i for i in items if isinstance(i, dict) and ((i.get("project") or {}).get("id") == project["id"])),
            None,
        )
        if item is None:
            return BoardItem(project_id=project["id"], field_id=status_field["id"], options=options)
        status = (item.get("fieldValueByName") or {}).get("name")
        return BoardItem(
            project_id=project["id"], field_id=status_field["id"], options=options,
            item_id=item.get("id"), status=status,
        )  # fmt: skip

    def list_board_items(self) -> BoardSnapshot:
        """Every item of the first Projects v2 board (all pages) and its Status options."""

        step = "read board items"
        options: dict[str, str] = {}
        cards: list[BoardCard] = []
        cursor: str | None = None
        for _ in range(BOARD_ITEMS_MAX_PAGES):
            args = ["api", "graphql", "-f", f"query={BOARD_ITEMS_QUERY}", "-f", f"owner={self.owner}",
                    "-f", f"repo={self.name}"]  # fmt: skip
            if cursor:
                args += ["-f", f"cursor={cursor}"]
            payload = self._json(step, args)
            if not isinstance(payload, dict):
                raise GatewayError(step, "GitHub returned a board that could not be parsed")
            if payload.get("errors"):
                messages = "; ".join(str(e.get("message")) for e in payload["errors"] if isinstance(e, dict))
                raise GatewayError(step, f"GraphQL returned errors ({messages[:300]}); the token needs the 'project' and 'repo' scopes")
            repository = (payload.get("data") or {}).get("repository") or {}
            projects = ((repository.get("projectsV2") or {}).get("nodes")) or []
            if not projects or not isinstance(projects[0], dict) or not projects[0].get("id"):
                raise GatewayError(step, f"no Projects v2 board is visible on {self.repo}; the token needs the 'project' scope")
            project = projects[0]
            status_field = next((f for f in ((project.get("fields") or {}).get("nodes") or [])
                                 if isinstance(f, dict) and f.get("name") == "Status"), None)  # fmt: skip
            if status_field is None:
                raise GatewayError(step, "the board has no Status field")
            options = {str(o["name"]): str(o["id"]) for o in status_field.get("options") or []
                       if isinstance(o, dict) and o.get("name") and o.get("id")}  # fmt: skip
            page = project.get("items") or {}
            for node in page.get("nodes") or []:
                if not isinstance(node, dict) or not node.get("id"):
                    continue
                content = node.get("content") or {}
                is_issue = content.get("__typename") == "Issue" and isinstance(content.get("number"), int)
                labels = tuple(str(label.get("name")) for label in ((content.get("labels") or {}).get("nodes") or [])
                               if isinstance(label, dict) and label.get("name"))  # fmt: skip
                cards.append(BoardCard(
                    item_id=str(node["id"]), number=content["number"] if is_issue else None,
                    state=str(content.get("state") or "").lower() or None if is_issue else None, labels=labels,
                    status=(node.get("fieldValueByName") or {}).get("name"),
                ))  # fmt: skip
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return BoardSnapshot(options=options, cards=cards)
            cursor = info.get("endCursor")
            if not cursor:
                raise GatewayError(step, "GitHub returned a board page without a cursor")
        raise GatewayError(step, f"the board has more than {BOARD_ITEMS_MAX_PAGES} pages of items")

    def issue_body(self, number: int) -> str:
        step = f"read body of #{number}"
        payload = self._json(step, ["api", f"repos/{self.repo}/issues/{number}"])
        if not isinstance(payload, dict):
            raise GatewayError(step, "GitHub returned an issue that could not be parsed")
        return str(payload.get("body") or "")

    def issue_labels(self, number: int) -> list[str]:
        payload = self._json(f"read labels of #{number}", ["api", f"repos/{self.repo}/issues/{number}"])
        if not isinstance(payload, dict):
            raise GatewayError(f"read labels of #{number}", "GitHub returned an issue that could not be parsed")
        return [str(label.get("name")) for label in payload.get("labels") or [] if isinstance(label, dict)]

    # ── writes ───────────────────────────────────────────────────────────────

    def post_comment(self, number: int, body: str) -> dict[str, Any]:
        step = f"write comment on #{number}"
        # The body goes on stdin as JSON: no command-line length limit and no quoting.
        output = self._run(
            step, ["api", f"repos/{self.repo}/issues/{number}/comments", "-X", "POST", "--input", "-"],
            stdin=json.dumps({"body": body}),
        )
        try:
            payload = json.loads(output) if output.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def set_board_status(self, item: BoardItem, option_id: str) -> None:
        payload = self._json(
            "write status",
            ["api", "graphql", "-f", f"query={STATUS_MUTATION}", "-f", f"projectId={item.project_id}",
             "-f", f"itemId={item.item_id}", "-f", f"fieldId={item.field_id}", "-f", f"optionId={option_id}"],
        )  # fmt: skip
        if isinstance(payload, dict) and payload.get("errors"):
            messages = "; ".join(str(e.get("message")) for e in payload["errors"] if isinstance(e, dict))
            raise GatewayError("write status", f"GraphQL returned errors ({messages[:300]})")

    def edit_issue_body(self, number: int, body: str) -> None:
        # The body goes on stdin as JSON: no command-line length limit and no quoting.
        self._run(f"write body of #{number}", ["api", f"repos/{self.repo}/issues/{number}", "-X", "PATCH", "--input", "-"],
                  stdin=json.dumps({"body": body}))  # fmt: skip

    def add_label(self, number: int, name: str) -> None:
        self._run(f"add label to #{number}", ["api", f"repos/{self.repo}/issues/{number}/labels", "-X", "POST", "-f", f"labels[]={name}"])

    def remove_label(self, number: int, name: str) -> None:
        encoded = name.replace("%", "%25").replace(":", "%3A").replace(" ", "%20").replace("/", "%2F")
        self._run(f"remove label from #{number}", ["api", f"repos/{self.repo}/issues/{number}/labels/{encoded}", "-X", "DELETE"])

    def create_label(self, name: str, color: str, description: str) -> None:
        args = ["api", f"repos/{self.repo}/labels", "-X", "POST", "-f", f"name={name}", "-f", f"color={color}"]
        if description:
            args += ["-f", f"description={description}"]
        self._run("create label", args)

    # ── plumbing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _pages(step: str, output: str, *, what: str = "a list") -> list[dict[str, Any]]:
        try:
            pages = parse_json_stream(output)
        except json.JSONDecodeError:
            raise GatewayError(step, f"GitHub returned {what} that could not be parsed") from None
        items: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise GatewayError(step, f"GitHub returned {what} that could not be parsed")
            items.extend(item for item in page if isinstance(item, dict))
        return items

    def _json(self, step: str, arguments: list[str]) -> Any:
        output = self._run(step, arguments)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            raise GatewayError(step, "GitHub returned a response that could not be parsed") from None

    def _run(self, step: str, arguments: list[str], *, stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                [*self.gh_command, *arguments], input=stdin, capture_output=True, encoding="utf-8",
                errors="replace", check=False,
            )  # fmt: skip
        except FileNotFoundError:
            raise GatewayError(step, f"GitHub CLI executable not found: {self.gh_command[0]}") from None
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            lines = stderr.splitlines()
            reason = _missing_scope(stderr) or (lines[-1] if lines else f"gh exited with code {result.returncode}")
            not_found = "404" in stderr or "Not Found" in stderr
            raise GatewayError(step, mask_tokens(reason[:300]), not_found=not_found)
        return result.stdout or ""
