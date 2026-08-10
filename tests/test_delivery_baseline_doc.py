"""Content assertions for the normative delivery-baseline contract (#240).

The document in ``docs/DELIVERY-BASELINE.md`` is the frozen contract that
Story #241 (validation) and Story #242 (materialization) implement against.
These deterministic assertions anchor its load-bearing rules so the schema
cannot drift silently away from the implementations that read it.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ARTIFACT_STATES = (
    "Candidate",
    "Ready for approval",
    "Accepted",
    "Materialized",
    "Rejected",
    "Superseded",
)

IMMUTABLE_ONCE_ACCEPTED_FIELDS = (
    "source_specification_ref",
    "technical_plan_ref",
    "source_revision",
)

APPROVAL_EVIDENCE_FIELDS = ("authority", "time", "evidence_url")


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_delivery_baseline_doc_exists_and_is_normative() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "# Delivery Baseline Contract" in document
    assert "## Artifact States" in document
    assert "## Allowed Transitions" in document
    assert "## Delivery-Baseline Schema" in document


def test_all_six_artifact_states_are_defined() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    for state in ARTIFACT_STATES:
        assert f"### {state}\n" in document, f"missing artifact-state definition: {state}"


def test_every_allowed_transition_is_listed() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    for source, target in (
        ("Candidate", "Ready for approval"),
        ("Candidate", "Rejected"),
        ("Ready for approval", "Accepted"),
        ("Ready for approval", "Rejected"),
        ("Ready for approval", "Candidate"),
        ("Accepted", "Materialized"),
        ("Accepted", "Superseded"),
        ("Materialized", "Superseded"),
    ):
        assert f"| {source} | {target} |" in document, (
            f"missing allowed transition: {source} -> {target}"
        )


def test_state_machine_is_decoupled_from_project_status_model() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "### Decoupling from delivery-item Project statuses" in document
    assert "explicitly decoupled" in document
    assert "docs/PROJECT-STATUS.md" in document
    assert "No artifact state is a Project status" in document


def test_no_unresolved_questions_at_v1_rule_is_stated() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert (
        "a baseline with any unresolved question cannot reach Accepted" in document
    )
    assert "**No-unresolved-questions-at-v1 rule:**" in document


def test_editorial_change_creates_new_version_rule_is_stated() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "**Editorial-only changes create a new version.**" in document
    assert "there is no editorial exemption" in document


def test_immutable_once_accepted_fields_are_documented() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "## Immutability After Acceptance" in document
    assert "immutable once a baseline is Accepted" in document
    assert "independently verifiable" in document
    for field in IMMUTABLE_ONCE_ACCEPTED_FIELDS:
        assert field in document, f"missing immutable-once-Accepted field: {field}"


def test_storage_location_is_declared_and_distinct_from_the_schema_doc() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert (
        "`.hpw/delivery-baselines/` is the canonical storage location" in document
    )
    assert "docs/DELIVERY-BASELINE.md" in document
    assert "is **not** a baseline instance" in document


def test_approval_evidence_requires_authority_time_and_durable_url() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    for field in APPROVAL_EVIDENCE_FIELDS:
        assert f"`{field}`" in document, f"missing approval-evidence field: {field}"
    assert "durable GitHub evidence URL" in document
    assert "**A baseline cannot reach Accepted without all three.**" in document


def test_delivery_baseline_schema_fields_are_documented() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    for field in (
        "state",
        "baseline_version",
        "provenance",
        "source_revision",
        "source_specification_ref",
        "technical_plan_ref",
        "in_scope_requirements",
        "acceptance_criteria",
        "requirement_acceptance_mappings",
        "exclusions",
        "unresolved_questions",
        "approval_evidence",
    ):
        assert f"`{field}`" in document, f"missing baseline schema field: {field}"


def test_state_field_is_required_and_scoped_to_the_six_artifact_states() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "| `state` | Yes |" in document
    for state in ARTIFACT_STATES:
        assert f"`{state}`" in document, f"state field format must name: {state}"
    assert (
        "the only two fields permitted to change after a baseline reaches `Accepted`"
        in document
    )


def test_acceptance_criteria_field_defines_referenced_identifiers() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "`acceptance_criteria` is the ordered list of acceptance-criterion definitions" in document
    assert (
        "Every acceptance criterion referenced by a mapping must have a corresponding "
        "`{id, text}` entry in `acceptance_criteria`."
        in document
    )
    assert "dangling reference" in document


def test_requirement_identifiers_have_a_stable_format_and_mapping_rule() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "### Requirement Identifiers" in document
    assert "REQ-<NNN>" in document
    assert "AC-<NNN>" in document
    assert (
        "**Every requirement identifier must map to at least one acceptance criterion.**"
        in document
    )


def test_exclusions_and_unresolved_questions_are_distinct_fields() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert document.count("field distinct from `in_scope_requirements`") >= 2
    assert "### Exclusions and Non-Goals" in document
    assert "### Unresolved Questions" in document


def test_materialization_record_schema_is_documented() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "## Materialization-Record Schema" in document
    for field in (
        "target_issue_link",
        "baseline_version_consumed",
        "materialized_at",
        "idempotency_key",
    ):
        assert f"`{field}`" in document, f"missing materialization-record field: {field}"


def test_versioning_rules_state_what_requires_a_new_version() -> None:
    document = read("docs/DELIVERY-BASELINE.md")

    assert "### What Requires a New Version" in document
    assert (
        "every change to an Accepted baseline, without exception" in document
    )
    assert "One baseline version materializes exactly one Epic or Feature." in document


def test_document_contains_no_spec_kit_specific_required_fields() -> None:
    document = read("docs/DELIVERY-BASELINE.md").lower()

    # Spec Kit may be named as the loose-coupling boundary it is excluded by,
    # but no Spec Kit artefact may appear as a schema field.
    for forbidden in ("spec.md", "plan.md", "tasks.md", "specify/", ".specify"):
        assert forbidden not in document, f"Spec Kit-specific reference found: {forbidden}"
