# Delivery Baseline Contract

This document is the canonical, normative reference for the **delivery-baseline** and **materialization-record** contracts used by HPW pre-delivery intake. It defines the artifact-state model, every allowed artifact-state transition, the schema of both artifacts, the immutability and versioning rules, and the canonical storage location for baseline instances.

**Story:** #240 — Define delivery-baseline, approval, and supersession contracts
**Feature:** #238 — Versioned delivery baseline and HPW intake
**Epic:** #236 — Extend HPW with pre-delivery project promotion and structured discovery intake

This document is the schema itself. It is not a baseline instance. Baseline instances live in `.hpw/delivery-baselines/` (see [Storage Location](#storage-location)).

**Scope of this document.** It defines *what* a delivery baseline is. It does not implement validation (Story #241), materialization (Story #242), or refinement/closure wiring (Story #243). Those stories implement against this contract and must not redefine it.

**Methodology independence.** The delivery baseline is an HPW-defined artifact. It contains no fields specific to Spec Kit or any other upstream discovery methodology. Any upstream methodology may produce a baseline as long as the baseline satisfies this contract; HPW consumes only what is defined here (Epic #236 / Feature #238 loose-coupling decision).

**Closure boundary.** A baseline records delivery *intent* and *traceability* only. It is not a closure gate. `/verify-closure` remains the sole technical closure authority; nothing in this document adds a second closure gate.

---

## Artifact States

A delivery baseline is always in exactly one of six artifact states. These states describe a committed artifact under `.hpw/delivery-baselines/`, not a GitHub Project delivery item.

### Candidate

The baseline artifact exists and is being drafted. Fields may be incomplete, requirement mappings may be missing, and unresolved questions may be present. A Candidate baseline carries no authority: nothing may be materialized from it, and no downstream work may cite it as approved intent.

### Ready for approval

The baseline author declares the artifact complete and submits it for Product Owner approval. Every mandatory field is populated, the source specification and technical plan are both referenced, and every in-scope requirement identifier maps to at least one acceptance criterion. The baseline has not yet been approved and still carries no delivery authority. This is an intent signal: it declares that the artifact is ready for a PO decision.

### Accepted

The Product Owner has approved the baseline. Approval evidence (authority, time, durable GitHub evidence URL) is recorded in the artifact. An Accepted baseline is **immutable**: its content is frozen and is the authoritative statement of delivery intent for exactly one target Epic or Feature. Materialization may proceed only from an Accepted baseline.

### Materialized

An Accepted baseline has been consumed to create or update its target HPW Epic or Feature issue, and a materialization record exists linking the baseline version to that target. Materialized is a fact state: it records that the intent has been projected into GitHub. The baseline content remains immutable.

### Rejected

The Product Owner reviewed the baseline and declined to approve it. A Rejected baseline is terminal and is never materialized. Rejection does not edit the artifact's content; it records the decision. Work continues by authoring a new baseline version, not by editing the Rejected one.

### Superseded

A later baseline version has replaced this one. A Superseded baseline is terminal and is retained for audit. Only an Accepted or Materialized baseline can become Superseded — superseding is how accepted history is corrected without mutating it. A Superseded baseline must record the version identifier of the baseline that supersedes it.

---

## Allowed Transitions

All artifact-state transitions and their triggering events are listed below. Transitions not listed here are not permitted.

| From | To | Triggering action or event |
|---|---|---|
| Candidate | Ready for approval | Author completes every mandatory field and submits the baseline for PO approval |
| Candidate | Rejected | Author or PO abandons the draft baseline |
| Ready for approval | Accepted | PO approves; approval evidence (authority, time, durable GitHub evidence URL) is recorded and no unresolved question remains |
| Ready for approval | Rejected | PO declines to approve the baseline |
| Ready for approval | Candidate | Approval is withdrawn or the baseline is returned to the author for changes before any approval decision is recorded |
| Accepted | Materialized | The baseline is consumed to create or update its target Epic or Feature issue and a materialization record is written |
| Accepted | Superseded | A later baseline version for the same target reaches Accepted |
| Materialized | Superseded | A later baseline version for the same target reaches Accepted |

**Terminal states:** `Rejected` and `Superseded`. No transition leaves either state.

**Prohibited transitions**, stated explicitly because they are the failure modes this contract exists to prevent:

- `Candidate` → `Accepted` (approval must pass through `Ready for approval`).
- `Candidate` → `Materialized` (only an Accepted baseline may be materialized).
- `Ready for approval` → `Materialized` (only an Accepted baseline may be materialized).
- `Accepted` → `Candidate`, `Accepted` → `Ready for approval`, `Materialized` → `Accepted` (accepted history is never reopened; author a new version instead).
- `Rejected` → any state, `Superseded` → any state.

### Decoupling from delivery-item Project statuses

This artifact-state machine is **explicitly decoupled** from the delivery-item status model in [`docs/PROJECT-STATUS.md`](PROJECT-STATUS.md). The two models describe different things and must never be merged, mapped one-to-one, or kept in sync:

- Artifact states (`Candidate`, `Ready for approval`, `Accepted`, `Materialized`, `Rejected`, `Superseded`) describe a committed baseline artifact in `.hpw/delivery-baselines/`.
- Delivery-item statuses (`Backlog`, `In refinement`, `Refined`, `Ready for implementation`, `In implementation`, `Needs rework`, `Blocked`, `In review`, `Ready to merge`, `Done`) describe a GitHub Project item's progress through the HPW workflow.

No artifact state is a Project status, and no Project status is an artifact state. A baseline reaching `Materialized` does not set, imply, or constrain the Project status of the issue it materialized into, and a delivery item's Project status never changes a baseline's artifact state. Implementations must not add artifact states to the Project `Status` field, and `docs/PROJECT-STATUS.md` is not modified by this contract.

---

## Delivery-Baseline Schema

A delivery baseline is a structured artifact with the fields below. Each field is normative: an implementation of Story #241 derives its required/optional, immutable/mutable, and format rules directly from this table and the subsections that follow, without further clarification.

| Field | Required | Mutable after Accepted | Format |
|---|---|---|---|
| `state` | Yes | **Yes — only via [Allowed Transitions](#allowed-transitions)** | Exactly one of the six artifact-state names in [Artifact States](#artifact-states): `Candidate`, `Ready for approval`, `Accepted`, `Materialized`, `Rejected`, `Superseded` |
| `baseline_version` | Yes | No | Version identifier — see [Versioning and Supersession](#versioning-and-supersession) |
| `target` | Yes | No | Exactly one HPW Epic or Feature target, as `{type, issue_url}` — see [Target](#target) |
| `provenance` | Yes | No | Origin of the baseline, as `{source_methodology, author, created_at}` — see [Provenance](#provenance) |
| `source_revision` | Yes | **No — immutable** | Full 40-character Git commit SHA of the source repository revision the baseline was derived from |
| `source_specification_ref` | Yes | **No — immutable** | Durable reference (URL or repository-relative path plus `source_revision`) to the source specification document |
| `technical_plan_ref` | Yes | **No — immutable** | Durable reference (URL or repository-relative path plus `source_revision`) to the technical plan document |
| `in_scope_requirements` | Yes | No | Ordered list of requirement identifiers — see [Requirement Identifiers](#requirement-identifiers) |
| `acceptance_criteria` | Yes | No | Ordered list of `{id, text}` entries defining every acceptance criterion referenced by `requirement_acceptance_mappings` — see [Requirement-to-Acceptance-Criterion Mapping](#requirement-to-acceptance-criterion-mapping) |
| `requirement_acceptance_mappings` | Yes | No | Mapping of every requirement identifier to one or more acceptance criteria — see [Requirement-to-Acceptance-Criterion Mapping](#requirement-to-acceptance-criterion-mapping) |
| `exclusions` | Yes (may be empty) | No | List of `{statement, reason}` non-goals — see [Exclusions and Non-Goals](#exclusions-and-non-goals) |
| `unresolved_questions` | Yes (must be empty to reach Accepted) | No | List of `{question, owner}` open questions — see [Unresolved Questions](#unresolved-questions) |
| `approval_evidence` | Yes to reach Accepted | No | Authority, time, and durable GitHub evidence URL — see [Approval Evidence](#approval-evidence) |
| `supersedes` | No | No | `baseline_version` of the baseline this version replaces; absent for a first version |
| `superseded_by` | No | Yes — write-once when this baseline becomes Superseded | `baseline_version` of the baseline that replaced this one |

`state` and `superseded_by` are the only two fields permitted to change after a baseline reaches `Accepted`. `state` changes exclusively by following an edge in the [Allowed Transitions](#allowed-transitions) table (for example `Accepted` → `Materialized`); no other field changes when `state` does. `superseded_by` is a write-once audit pointer set at the moment the baseline enters `Superseded`; it records history rather than changing intent, and it may never be rewritten once set. Every other field is frozen at `Accepted` (see [Immutability After Acceptance](#immutability-after-acceptance)).

### Target

`target` names the single HPW delivery item this baseline version materializes into. It has exactly two mandatory sub-fields:

| Sub-field | Meaning | Format |
|---|---|---|
| `type` | The delivery-item kind the baseline materializes into | Exactly one of `Epic` or `Feature`; no other value is permitted |
| `issue_url` | The HPW Epic or Feature issue the baseline targets | Full GitHub issue URL (`https://github.com/<owner>/<repo>/issues/<number>`) |

A baseline that names no target, names more than one target, or uses a `type` outside `Epic` and `Feature` is invalid and cannot reach `Ready for approval`. `issue_url` is the identity used to answer "which baseline versions belong to this target": version numbering, supersession, and the at-most-one-`Accepted`-per-target rule are all evaluated per `issue_url` (see [Target Granularity](#target-granularity)). For a baseline that creates its target issue, `issue_url` is recorded when the target issue exists; a baseline cannot reach `Accepted` with an empty `issue_url`.

### Provenance

`provenance` records where the baseline came from so that an auditor can trace intent back to its origin without depending on the authoring tool. It has exactly three mandatory sub-fields:

| Sub-field | Meaning | Format |
|---|---|---|
| `source_methodology` | The authoring methodology or tool that produced the source material | Free-text name (for example, a discovery methodology or tool name); never a required schema field of that methodology |
| `author` | Who authored the baseline | GitHub handle of the author |
| `created_at` | When the baseline version was authored | RFC 3339 timestamp in UTC |

Provenance is descriptive metadata; it must not introduce methodology-specific required fields into this schema.

### Immutable Source Revision

`source_revision` is the full 40-character Git commit SHA of the revision the baseline was derived from. Abbreviated SHAs, branch names, and tags are not acceptable: they are mutable references and would make the baseline unverifiable. The full source revision pins the source specification and technical plan to an exact, independently verifiable state.

### Source Specification and Technical Plan References

Both `source_specification_ref` and `technical_plan_ref` are **mandatory artifacts**. A baseline that references only one of them cannot reach `Ready for approval` and cannot reach `Accepted` (Feature #238 mandatory-artifact decision). Each reference must be resolvable at `source_revision`, so that a reader can retrieve the exact document content that was approved.

### Requirement Identifiers

Every in-scope requirement carries a stable identifier with this format:

```
REQ-<NNN>
```

where `<NNN>` is a zero-padded decimal sequence number of at least three digits, unique within a single baseline target (for example, `REQ-001`, `REQ-014`, `REQ-137`).

Identifier stability rules:

- An identifier, once used in an Accepted baseline, permanently denotes that requirement for that target. It is never reused for different content in a later version.
- Removing a requirement in a later baseline version retires its identifier; the identifier is not recycled.
- Renumbering existing requirements is prohibited; new requirements take the next unused number.

### Requirement-to-Acceptance-Criterion Mapping

`acceptance_criteria` is the ordered list of acceptance-criterion definitions for the baseline. Each entry is a `{id, text}` pair: `id` uses the identifier format:

```
AC-<NNN>
```

with the same zero-padding, uniqueness, and stability rules as requirement identifiers; `text` is the criterion's defining text. `acceptance_criteria` is where every `AC-<NNN>` identifier is defined — `requirement_acceptance_mappings` only references identifiers, it never defines them.

`requirement_acceptance_mappings` maps every entry in `in_scope_requirements` to one or more entries in `acceptance_criteria`, by `id`.

Mapping rules:

- **Every requirement identifier must map to at least one acceptance criterion.** A requirement with no mapped acceptance criterion is an incomplete baseline and blocks `Accepted`.
- A requirement may map to several acceptance criteria; an acceptance criterion may serve several requirements.
- **Every acceptance criterion referenced by a mapping must have a corresponding `{id, text}` entry in `acceptance_criteria`.** A mapping that references an `AC-<NNN>` with no matching entry in `acceptance_criteria` is a dangling reference and is invalid.
- Identifiers appearing in `exclusions` must not appear in `in_scope_requirements`.

### Exclusions and Non-Goals

`exclusions` is a **field distinct from `in_scope_requirements`**. It records what the baseline deliberately does not deliver, so that "not in scope" is an explicit, auditable statement rather than an absence a reader must infer. Each entry is a `{statement, reason}` pair: `statement` is the excluded behaviour or outcome and is mandatory; `reason` explains why it is excluded and is optional. An empty `exclusions` list is valid and asserts that no non-goal was identified. Exclusions never confer delivery authority and are never materialized as in-scope work.

### Unresolved Questions

`unresolved_questions` is a **field distinct from `in_scope_requirements`**. It records open questions the baseline author could not close. Each entry is a `{question, owner}` pair, both mandatory: `question` is the open question and `owner` is the GitHub handle of whoever must answer it.

**No-unresolved-questions-at-v1 rule:** a baseline with any unresolved question cannot reach Accepted. The `unresolved_questions` list must be empty for the transition `Ready for approval` → `Accepted`; a baseline with a non-empty `unresolved_questions` list stays in `Candidate` or `Ready for approval`, or is Rejected. Unresolved questions are resolved by answering them and authoring a new baseline version, never by deleting them without an answer (Feature #238 unresolved-question handoff decision).

### Approval Evidence

`approval_evidence` records the Product Owner's approval decision with exactly three mandatory fields:

| Sub-field | Meaning | Format |
|---|---|---|
| `authority` | The approving Product Owner identity | GitHub handle of the PO who approved |
| `time` | When approval was granted | RFC 3339 timestamp in UTC |
| `evidence_url` | Durable GitHub evidence for the approval | Permanent GitHub URL to the approving comment, review, or issue event |

**A baseline cannot reach Accepted without all three.** If `authority`, `time`, or `evidence_url` is missing or empty, the `Ready for approval` → `Accepted` transition is not permitted. The evidence URL must be durable: a permalink to a GitHub comment, review, or event that remains resolvable after the approving thread is closed. Approval authority rests with the Product Owner; no executor may approve a baseline on the PO's behalf (Feature #238 approval-authority decision).

---

## Immutability After Acceptance

Once a baseline reaches `Accepted`, its content is frozen. Three fields in particular are **immutable once a baseline is Accepted** and must remain independently verifiable by any reader at any later time:

1. **Source specification** (`source_specification_ref`)
2. **Technical plan** (`technical_plan_ref`)
3. **Full source revision** (`source_revision`)

Independent verifiability means a third party can resolve each reference at the recorded full source revision and confirm it matches the approved content, without trusting the authoring tool, the executor, or the current state of any branch.

**Permitted:** authoring a new baseline version that supersedes the Accepted one; advancing `state` along an edge in the [Allowed Transitions](#allowed-transitions) table; setting the write-once `superseded_by` audit pointer when `state` becomes `Superseded`.

**Prohibited:** in-place mutation of accepted history — editing any field other than `state` or `superseded_by` on an Accepted, Materialized, Rejected, or Superseded baseline, rewriting the commit that introduced it, repointing a reference to different content, or setting `state` to any value not reached by following the Allowed Transitions table.

---

## Versioning and Supersession

### Target Granularity

**One baseline version materializes exactly one Epic or Feature.** A baseline never targets several delivery items, and a delivery item never consumes several baseline versions at once (Feature #238 target-granularity decision). This keeps the baseline-to-target relationship simple and auditable.

### What Requires a New Version

A new `baseline_version` is required for **every change to an Accepted baseline, without exception**, including:

- Any change to `source_revision`, `source_specification_ref`, or `technical_plan_ref`.
- Adding, removing, or altering any requirement identifier in `in_scope_requirements`.
- Adding, removing, or altering any requirement-to-acceptance-criterion mapping, or any acceptance criterion text.
- Adding, removing, or altering any entry in `exclusions` or `unresolved_questions`.
- Any change to `provenance` or `approval_evidence`.
- **Editorial-only changes create a new version.** Typo fixes, wording clarifications, and formatting changes that alter no delivery intent still require a new baseline version; there is no editorial exemption (Feature #238 versioning decision).

The rule is deliberately absolute: any modification of accepted content is a new version, never an in-place edit. This removes the judgement call about whether a change is "material" and keeps every approved state independently reconstructible.

### Version Identifier Format

`baseline_version` uses:

```
v<N>
```

where `<N>` is a positive integer starting at `1` and incrementing by one per version for a given target (`v1`, `v2`, `v3`, …). Version numbers are never reused or renumbered.

### Supersession Rules

- A new version reaching `Accepted` supersedes the previously Accepted or Materialized version for the same target.
- The new version records `supersedes: <previous baseline_version>`; the previous version records `superseded_by: <new baseline_version>` and moves to `Superseded`.
- At most one baseline version per target may be in `Accepted` or `Materialized` at any time.
- Superseded versions are retained, never deleted, so the approval history remains auditable.
- A `Rejected` baseline is not superseded — it was never accepted. The next authored version simply starts from the next unused version number.

---

## Materialization-Record Schema

A materialization record is written when an Accepted baseline is consumed to create or update its target HPW Epic or Feature issue. It is the durable evidence that a specific baseline version produced a specific GitHub item, and it is what makes materialization idempotent.

| Field | Required | Format |
|---|---|---|
| `target_issue_link` | Yes | Full GitHub issue URL of the Epic or Feature the baseline was materialized into |
| `baseline_version_consumed` | Yes | The `baseline_version` of the Accepted baseline that was materialized |
| `materialized_at` | Yes | RFC 3339 timestamp in UTC recording when materialization completed |
| `idempotency_key` | Yes | Stable key derived from the baseline target and `baseline_version_consumed`; identical inputs always produce an identical key |

Rules:

- A materialization record is written only for a baseline in `Accepted`; writing one moves the baseline to `Materialized`.
- The `idempotency_key` is deterministic. Re-running materialization for the same target and baseline version produces the same key, so a repeated run is recognised as already applied and performs no second write.
- A materialization record is append-only evidence: it is never edited after it is written. A later baseline version produces a new record with a new key.
- `baseline_version_consumed` must match an existing baseline version for the referenced target; a record citing an unknown version is invalid.

---

## Storage Location

`.hpw/delivery-baselines/` is the canonical storage location for delivery-baseline artifact **instances** (Feature #238 storage-location decision). Every baseline version that conforms to this schema is committed under that directory, so baselines are versioned with the repository and are auditable through normal Git history.

This is distinct from the location of this document. `docs/DELIVERY-BASELINE.md` is the normative schema documentation itself and is **not** a baseline instance; it is never stored in `.hpw/delivery-baselines/`, and no file in `.hpw/delivery-baselines/` restates this schema.

Materialization records are stored alongside the baseline instances they refer to, under the same `.hpw/delivery-baselines/` location.

This story documents the location only. It does not create the directory and does not add any instance or example file.

---

## Contract Stability

The field names, artifact-state names, identifier formats, and versioning rule defined here are the frozen contract that Story #241 (validation) and Story #242 (materialization) implement against.

A breaking rename or field-shape change discovered after this contract is merged requires a superseding decision recorded in the [Decisions](#decisions) section of this document. It must never be applied as a silent edit inside downstream implementation code.

---

## Decisions

The following Epic #236 / Feature #238 Product Owner decisions are binding inputs to this contract. They are recorded here because this document is the contract they govern; they are not reopened here.

### Loose-coupling boundary — 2026-08-09

**Chosen:** HPW core consumes an HPW-defined delivery baseline; the schema contains no Spec Kit-specific or otherwise methodology-specific required fields.
**Rejected:** Consuming an upstream methodology's artifact format directly, which would couple HPW to that methodology's evolution.

### Closure responsibility — 2026-08-09

**Chosen:** The baseline records delivery intent and traceability only; `/verify-closure` remains the sole technical closure authority.
**Rejected:** Making baseline traceability a second closure gate alongside `/verify-closure`.

### Baseline approval authority and required artifacts — 2026-08-09

**Chosen:** Product Owner approval, with both a source specification and a technical plan required before a baseline can be approved.
**Rejected:** Executor self-approval, and approval on a specification alone without a technical plan.

### Unresolved-question handoff policy — 2026-08-09

**Chosen:** No unresolved questions are permitted at version-one Accepted state; a baseline with any unresolved question cannot reach Accepted.
**Rejected:** Allowing a baseline to be accepted with open questions tracked for later resolution.

### Delivery-baseline storage location — 2026-08-09

**Chosen:** `.hpw/delivery-baselines/` is the canonical location for baseline artifact instances.
**Rejected:** Storing baseline instances in `docs/`, which would conflate the schema documentation with instances of it.

### Baseline target granularity and versioning — 2026-08-09

**Chosen:** One baseline version materializes exactly one Epic or Feature, and every change — including editorial-only changes — creates a new version.
**Rejected:** Multi-target baselines, and an editorial-change exemption that would permit in-place edits to accepted history.
