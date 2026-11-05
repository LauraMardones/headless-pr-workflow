"""The one gateway between the dispatcher building blocks and GitHub (ADR-014 → Decision → 2).

Every call runs ``gh`` through ``subprocess`` with the token ``gh`` already uses
(``GH_TOKEN``). No other HTTP client. A failed call raises ``GatewayError`` with
the step and ``gh``'s last stderr line; the token never appears in it.
"""

from __future__ import annotations

import json
import os
import subprocess
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
        try:
            pages = parse_json_stream(output)
        except json.JSONDecodeError:
            raise GatewayError(step, "GitHub returned comments that could not be parsed") from None
        comments: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise GatewayError(step, "GitHub returned comments that could not be parsed")
            comments.extend(item for item in page if isinstance(item, dict))
        return comments

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
