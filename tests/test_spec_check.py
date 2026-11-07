from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spec-check"
TEMPLATE_HEADINGS = ["Goal", "Scope", "Dependencies", "Open Questions"]


@pytest.fixture
def mod():
    loader = SourceFileLoader("spec_check", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def make_spec(headings=TEMPLATE_HEADINGS, questions="None.", extra="") -> str:
    parts = ["# Epic #1: Title", ""]
    for name in headings:
        parts += [f"## {name}", ""]
        parts += [questions if name == "Open Questions" else "text", ""]
    return "\n".join(parts) + extra


@pytest.fixture
def files(tmp_path):
    template = tmp_path / "template.md"
    template.write_text(make_spec(), encoding="utf-8")
    spec = tmp_path / "spec.md"
    return template, spec


def run(mod, spec, template, *extra, capsys):
    code = mod.main([str(spec), "--template", str(template), *extra])
    return code, capsys.readouterr().out


def test_complete_spec_passes(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(), encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 0
    assert out.splitlines()[0] == f"spec-check: pass {spec}"
    assert "heading:Goal: pass" in out and "open-questions: pass" in out


def test_each_missing_heading_is_named(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(["Goal", "Open Questions"]), encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1
    assert out.splitlines()[0].startswith("spec-check: fail")
    assert "heading:Scope: fail - missing" in out
    assert "heading:Dependencies: fail - missing" in out
    assert "heading:Goal: pass" in out


@pytest.mark.parametrize("questions", ["", "Who decides?", "None", "None.\n\nMore text.", "- None."])
def test_open_questions_variants_fail(mod, files, capsys, questions):
    template, spec = files
    spec.write_text(make_spec(questions=questions), encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1
    assert "open-questions: fail" in out


def test_open_questions_surrounding_blank_lines_pass(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(questions="\n\n  None.  \n\n\n"), encoding="utf-8")
    assert run(mod, spec, template, capsys=capsys)[0] == 0


def test_open_questions_detail_is_truncated(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(questions="Q" * 200), encoding="utf-8")
    _, out = run(mod, spec, template, capsys=capsys)
    line = next(l for l in out.splitlines() if l.startswith("open-questions:"))
    assert "Q" * 80 in line and "Q" * 81 not in line


def test_open_questions_ends_at_next_level2_heading_only(mod, files, capsys):
    template, spec = files
    text = make_spec(["Goal", "Scope", "Dependencies"]) + "\n## Open Questions\n\nNone.\n\n### Sub\n"
    spec.write_text(text, encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1 and "open-questions: fail" in out


def test_duplicate_heading_fails_and_is_named(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(TEMPLATE_HEADINGS[:-1]) + "\n## Scope\n\nagain\n\n## Open Questions\n\nNone.\n",
                    encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1
    assert "heading:Scope: fail - appears 2 times" in out


def test_heading_inside_code_fence_is_not_counted(mod, files, capsys):
    template, spec = files
    fenced = "\n```\n## Scope\n```\n\n~~~\n## Goal\n~~~\n"
    spec.write_text(make_spec(["Goal", "Scope", "Dependencies"]) + fenced
                    + "\n## Open Questions\n\nNone.\n", encoding="utf-8")
    assert run(mod, spec, template, capsys=capsys)[0] == 0
    # a heading that only exists inside a fence is missing
    spec.write_text(make_spec(["Goal", "Dependencies"]) + "\n```\n## Scope\n```\n\n## Open Questions\n\nNone.\n",
                    encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1 and "heading:Scope: fail - missing" in out


def test_levels_other_than_two_are_not_headings(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(["Goal", "Dependencies"]) + "\n### Scope\n#### Scope\n\n## Open Questions\n\nNone.\n",
                    encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1 and "heading:Scope: fail - missing" in out


def test_headings_come_from_the_template_at_run_time(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(), encoding="utf-8")
    assert run(mod, spec, template, capsys=capsys)[0] == 0
    template.write_text(make_spec(TEMPLATE_HEADINGS + ["Checked Against"]), encoding="utf-8")
    code, out = run(mod, spec, template, capsys=capsys)
    assert code == 1 and "heading:Checked Against: fail - missing" in out


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


def test_ref_reads_spec_and_template_at_the_commit(mod, tmp_path, monkeypatch, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "spec.md").write_text(make_spec(), encoding="utf-8")
    (tmp_path / "specs" / "template.md").write_text(make_spec(), encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "good")
    good = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True,
                          check=True).stdout.strip()
    (tmp_path / "specs" / "spec.md").write_text(make_spec(questions="Open?"), encoding="utf-8")
    git(tmp_path, "commit", "-qam", "bad")
    monkeypatch.chdir(tmp_path)

    args = ["specs/spec.md", "--template", "specs/template.md"]
    assert mod.main(args) == 1  # working tree is bad
    capsys.readouterr()
    assert mod.main([*args, "--ref", good]) == 0  # the commit is good
    assert "spec-check: pass" in capsys.readouterr().out
    # the template at the commit is used, not the working tree's
    (tmp_path / "specs" / "template.md").write_text(make_spec(TEMPLATE_HEADINGS + ["Extra"]), encoding="utf-8")
    assert mod.main([*args, "--ref", good]) == 0


@pytest.fixture
def repo_cwd(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    (tmp_path / "t.md").write_text(make_spec(), encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def assert_error(mod, argv, capsys):
    code = mod.main(argv)
    out = capsys.readouterr().out
    assert code == 2
    assert "pass" not in out.splitlines()[0]
    assert out.startswith("spec-check: error")
    return out


def test_exit_2_missing_spec(mod, files, capsys):
    template, spec = files
    assert_error(mod, [str(spec), "--template", str(template)], capsys)


def test_exit_2_missing_template(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(), encoding="utf-8")
    assert_error(mod, [str(spec), "--template", str(template) + ".nope"], capsys)


def test_exit_2_template_without_level2_heading(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(), encoding="utf-8")
    template.write_text("# Only a title\n\n### Sub\n", encoding="utf-8")
    out = assert_error(mod, [str(spec), "--template", str(template)], capsys)
    assert "no level-2 heading" in out


def test_exit_2_unknown_ref(mod, repo_cwd, capsys):
    assert_error(mod, ["t.md", "--template", "t.md", "--ref", "no-such-commit"], capsys)


def test_exit_2_path_not_in_commit(mod, repo_cwd, capsys):
    assert_error(mod, ["missing.md", "--template", "t.md", "--ref", "HEAD"], capsys)


def test_exit_2_option_like_ref(mod, repo_cwd, capsys):
    assert_error(mod, ["t.md", "--template", "t.md", "--ref=--output=x"], capsys)


def test_exit_2_invalid_arguments(mod, capsys):
    with pytest.raises(SystemExit) as exc:
        mod.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        mod.main(["a.md", "--bogus"])
    assert exc.value.code == 2


def test_json_matches_human_output(mod, files, capsys):
    template, spec = files
    spec.write_text(make_spec(["Goal", "Open Questions"], questions="Q?"), encoding="utf-8")
    code, human = run(mod, spec, template, capsys=capsys)
    code_json, raw = run(mod, spec, template, "--json", capsys=capsys)
    report = json.loads(raw)
    assert code == code_json == report["exit_code"] == 1
    assert report["verdict"] == "fail" and report["ref"] is None
    assert report["path"] == str(spec) and report["template"] == str(template)
    assert "error" not in report
    lines = [f"{c['id']}: {c['status']} - {c['detail']}" for c in report["checks"]]
    assert human.splitlines()[1:] == lines


def test_json_error_has_error_string_and_no_checks(mod, files, capsys):
    template, spec = files
    code, raw = run(mod, spec, template, "--json", capsys=capsys)
    report = json.loads(raw)
    assert code == 2 and report["verdict"] == "error" and report["exit_code"] == 2
    assert report["checks"] == [] and isinstance(report["error"], str)


def test_script_is_executable_and_runs_as_a_command():
    assert SCRIPT.stat().st_mode & 0o111 or sys.platform == "win32"
    result = subprocess.run([sys.executable, str(SCRIPT), "specs/README.md"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 1  # README is not a spec


MERGED_SPECS = sorted(p for p in (ROOT / "specs").glob("*/spec.md") if p.parent.name != "_template")


def test_merged_specs_exist():
    assert MERGED_SPECS


@pytest.mark.parametrize("spec", MERGED_SPECS, ids=lambda p: p.parent.name)
def test_every_merged_spec_passes(mod, spec, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    code = mod.main([str(spec.relative_to(ROOT))])
    out = capsys.readouterr().out
    assert code == 0, out
