import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dev_extra_installs_xdist() -> None:
    project = tomllib.loads(read("pyproject.toml"))
    dev = project["project"]["optional-dependencies"]["dev"]
    assert any(dep.startswith("pytest-xdist") for dep in dev)


def test_pytest_defaults_stay_serial() -> None:
    options = tomllib.loads(read("pyproject.toml"))["tool"]["pytest"]["ini_options"]
    assert "-n" not in options.get("addopts", "")


def test_ci_runs_full_suite_in_parallel_under_pytest_check_name() -> None:
    workflow = read(".github/workflows/tests.yml")
    assert "name: pytest" in workflow
    assert "run: python -m pytest -n auto" in workflow


def test_agents_documents_serial_targeted_and_parallel_full_commands() -> None:
    agents = read("AGENTS.md")
    assert "python -m pytest tests/path.py::test_name" in agents
    assert "python -m pytest -n auto" in agents
    assert "py -3.12 -m pytest -n auto" in agents
