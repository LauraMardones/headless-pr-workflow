import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dev_extra_installs_xdist() -> None:
    # Plain-text match: tomllib is unavailable on the supported Python 3.10.
    dev = re.search(r"^dev\s*=\s*\[(.*?)\]", read("pyproject.toml"), re.MULTILINE | re.DOTALL)
    assert dev is not None
    assert "pytest-xdist" in dev.group(1)


def test_pytest_defaults_stay_serial() -> None:
    pyproject = read("pyproject.toml")
    assert not re.search(r"^addopts\s*=.*-n\b", pyproject, re.MULTILINE)


def test_ci_runs_full_suite_in_parallel_under_pytest_check_name() -> None:
    workflow = read(".github/workflows/tests.yml")
    assert "name: pytest" in workflow
    assert "run: python -m pytest -n auto" in workflow


def test_agents_documents_serial_targeted_and_parallel_full_commands() -> None:
    agents = read("AGENTS.md")
    assert "python -m pytest tests/path.py::test_name" in agents
    assert "\npython -m pytest -n 4\n" in agents
    assert "py -3.12 -m pytest -n 4" in agents
    # Locally, -n auto (16 workers) added failures on Windows; the bounded count did not.
    assert "\npython -m pytest -n auto\n" not in agents


def test_policy_temp_repo_teardown_leaves_sibling_directories() -> None:
    from tests.test_required_check_policy import temp_repo_root

    with temp_repo_root() as first:
        with temp_repo_root() as second:
            assert first.parent == second.parent
        assert not second.exists()
        assert first.exists()
