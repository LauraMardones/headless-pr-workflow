# Generic PR Review Adapter

Use this prompt when an assistant should review a GitHub pull request without implementing fixes.

```text
Review PR <PR_NUMBER> in <OWNER>/<REPO> using the headless PR workflow.

You are acting only as reviewer for the current PR head SHA. Do not implement fixes in this session.

GitHub is the source of truth. Review findings must be recorded on GitHub, not only in chat:
- Use GitHub PR review comments for line-specific findings.
- Use a GitHub PR review summary for overall findings.
- If you cannot create GitHub comments or reviews, clearly say so and provide exact findings in chat so they can be copied to GitHub.
- Do not treat chat-only feedback as workflow-authoritative unless it is also recorded on GitHub.

First refresh GitHub state:
- Run `hpw pr-context <PR_NUMBER> --repo <OWNER>/<REPO>` when available.
- Also inspect `gh pr view <PR_NUMBER> --repo <OWNER>/<REPO> --json number,title,state,isDraft,headRefOid,reviewDecision,reviews,statusCheckRollup,files`.
- Record the current head SHA before reviewing.

Validate the repo using the relevant deterministic checks. If this repo has no adapter-specific command yet, run the most relevant local tests documented by the repo.

Review for:
- correctness
- safety and failure modes
- SHA-bound approval readiness
- review/implementation separation
- portability across assistant sessions and platforms
- missing or weak deterministic tests
- whether repo-specific concerns are kept in adapters rather than core policy

Return findings ordered by severity. If no blockers remain, say so explicitly in a GitHub review summary and include the reviewed head SHA.

If no blockers remain and the PR is still draft, mark it Ready for review before leaving the final approval or solo-maintainer override summary. If you cannot change draft state, clearly say so in the GitHub review summary.

If GitHub allows formal approval and no blockers remain, approve the PR.

If GitHub blocks formal approval because the authenticated account owns the PR, leave a GitHub review summary that states:
- the reviewed head SHA
- that no blockers remain
- that the PR was marked Ready for review, or that changing draft state was unavailable
- that formal approval was attempted or unavailable because of ownership/account limitations

Do not merge the PR from this review session.
```

## Short User Invocation

```text
Use examples/assistants/generic/REVIEW-PR.md to review PR <PR_NUMBER>.
```
