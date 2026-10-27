"""Skill library and real filesystem/standalone installer contracts."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kllm.modules.connect.interactors import agent_skills as skills

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install_skills.py"


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for category, names in (
        ("operations", ["kllm-ops", "kllm-net"]),
        ("development", ["kllm-dev"]),
    ):
        for name in names:
            folder = root / category / name
            (folder / "references").mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f'---\nname: {name}\ndescription: "Run {name} tasks."\n---\n\n# Instructions\n'
                "\nRead [notes](references/notes.md).\n"
            )
            (folder / "references/notes.md").write_text("Original notes\n")
    return root


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def run_installer(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_shipped_library_metadata_and_portable_references():
    catalog = skills.discover_skills(ROOT / "skills")
    on_demand_audit_skills = {
        "kllm-audit",
        "kllm-prima-delta-audit",
        "kllm-prima-core-audit",
    }
    assert len(catalog) == 13
    assert len({s.name for s in catalog}) == 13
    assert any(s.name == "kllm-prima" and s.category == "development" for s in catalog)
    assert on_demand_audit_skills <= {
        s.name for s in catalog if s.category == "development"
    }
    assert len(skills.discover_skills(ROOT / "skills", "operations")) == 6
    assert len(skills.discover_skills(ROOT / "skills", "development")) == 7
    for skill in catalog:
        text = (skill.path / "SKILL.md").read_text()
        metadata = yaml.safe_load(text.split("---", 2)[1])
        assert metadata["name"] == skill.name
        assert isinstance(metadata["description"], str) and metadata["description"]
        if skill.name in on_demand_audit_skills:
            assert metadata.get("disable-model-invocation") is True
            assert "explicit human" in text.lower()
        else:
            assert not metadata.get("disable-model-invocation", False)
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if not target.startswith(("https://", "#")):
                resolved = (skill.path / target).resolve()
                assert resolved.is_relative_to(skill.path.resolve())
                assert resolved.is_file()
    network = next(s for s in catalog if s.name == "kllm-10gbe")
    assert {p.name for p in (network.path / "references").iterdir()} == {
        "windows.md",
        "macos.md",
        "linux.md",
    }


def test_all_agent_locations_deduplicate_shared_root(tmp_path):
    project = skills.agent_destinations(["all"], "project", tmp_path / "project", env={})
    assert len(project) == 6
    assert project[(tmp_path / "project/.agents/skills").resolve()] == ["codex", "openclaw"]
    assert {a for group in project.values() for a in group} == set(skills.AGENTS)
    user = skills.agent_destinations(["all"], "user", tmp_path, user_home=tmp_path / "user", env={})
    expected = {
        "codex": ".agents/skills",
        "claude": ".claude/skills",
        "cursor": ".cursor/skills",
        "opencode": ".config/opencode/skills",
        "hermes": ".hermes/skills",
        "openclaw": ".openclaw/skills",
        "pi": ".pi/agent/skills",
    }
    assert len(user) == 7
    for agent, relative in expected.items():
        assert user[(tmp_path / "user" / relative).resolve()] == [agent]


@pytest.mark.parametrize(
    "agent,key",
    [
        ("codex", "CODEX_HOME"),
        ("claude", "CLAUDE_CONFIG_DIR"),
        ("opencode", "OPENCODE_CONFIG_DIR"),
        ("hermes", "HERMES_HOME"),
        ("openclaw", "OPENCLAW_STATE_DIR"),
        ("pi", "PI_CODING_AGENT_DIR"),
    ],
)
def test_user_home_override(tmp_path, agent, key):
    custom = tmp_path / "custom home"
    destinations = skills.agent_destinations(
        [agent], "user", tmp_path, user_home=tmp_path / "user", env={key: str(custom)}
    )
    assert destinations == {(custom / "skills").resolve(): [agent]}


def test_xdg_and_hermes_suffix(tmp_path):
    destinations = skills.agent_destinations(
        ["opencode", "hermes"],
        "user",
        tmp_path,
        user_home=tmp_path / "user",
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HERMES_DATA_DIR_SUFFIX": "-test"},
    )
    assert destinations[(tmp_path / "xdg/opencode/skills").resolve()] == ["opencode"]
    assert destinations[(tmp_path / "user/.hermes-test/skills").resolve()] == ["hermes"]


def test_copy_update_idempotence_and_unrelated_preservation(library, tmp_path):
    catalog = skills.discover_skills(library)
    root = tmp_path / "installed"
    (root / "other").mkdir(parents=True)
    (root / "other/SKILL.md").write_text("User-owned skill")
    skills.install_skills(catalog, root)
    before = snapshot(root)
    manifest_time = (root / skills.MANIFEST).stat().st_mtime_ns
    assert skills.install_skills(catalog, root) == []
    assert snapshot(root) == before
    assert (root / skills.MANIFEST).stat().st_mtime_ns == manifest_time
    source = library / "operations/kllm-ops/references/notes.md"
    source.write_text("Updated source")
    skills.install_skills(catalog, root)
    assert (root / "kllm-ops/references/notes.md").read_text() == "Updated source"
    assert (root / "other/SKILL.md").read_text() == "User-owned skill"


def test_local_edit_blocks_update_and_force_preserves_backup(library, tmp_path):
    catalog = skills.discover_skills(library)
    root = tmp_path / "installed"
    skills.install_skills(catalog, root)
    target = root / "kllm-ops/references/notes.md"
    target.write_text("User edit")
    before = snapshot(root)
    with pytest.raises(skills.SkillError, match="locally edited"):
        skills.prepare_install(catalog, root)
    assert snapshot(root) == before
    messages = skills.apply_install(skills.prepare_install(catalog, root, force=True))
    backup = Path(messages[0].removeprefix("Backup: "))
    assert not backup.is_relative_to(root)
    assert (backup / "references/notes.md").read_text() == "User edit"
    assert target.read_text() == "Original notes\n"


def test_prune_removes_only_owned_unmodified_copies(library, tmp_path):
    root = tmp_path / "installed"
    skills.install_skills(skills.discover_skills(library), root)
    (root / "unrelated").mkdir()
    (root / "unrelated/SKILL.md").write_text("Keep me")
    skills.install_skills(skills.discover_skills(library, "operations"), root, prune=True)
    assert not (root / "kllm-dev").exists()
    skills.install_skills([], root, prune=True)
    assert (root / "unrelated/SKILL.md").read_text() == "Keep me"
    assert json.loads((root / skills.MANIFEST).read_text())["skills"] == {}


def test_prune_local_edits_are_not_deleted(library, tmp_path):
    root = tmp_path / "installed"
    skills.install_skills(skills.discover_skills(library), root)
    (root / "kllm-dev/SKILL.md").write_text("Edited")
    before = snapshot(root)
    with pytest.raises(skills.SkillError, match="Locally edited session"):
        skills.install_skills([], root, prune=True)
    assert snapshot(root) == before


@pytest.mark.parametrize("failure", ["folder", "manifest", "interrupt", "after-publish-interrupt"])
def test_replacement_failure_rolls_back_all_folders_and_manifest(
    library, tmp_path, monkeypatch, failure
):
    root = tmp_path / "installed"
    catalog = skills.discover_skills(library)
    skills.install_skills(catalog, root)
    before = snapshot(root)
    for skill in catalog:
        (skill.path / "references/notes.md").write_text("Updated")
    original = skills.os.replace

    def fail_once(source, target):
        source, target = Path(source), Path(target)
        if failure == "after-publish-interrupt" and target.name == skills.MANIFEST:
            original(source, target)
            monkeypatch.setattr(skills.os, "replace", original)
            raise KeyboardInterrupt()
        if failure == "interrupt" and source.parent.name == "new" and target.name == "kllm-ops":
            raise KeyboardInterrupt()
        if (
            failure == "folder"
            and source.parent.name == "new"
            and target.name == "kllm-ops"
            or failure == "manifest"
            and target.name == skills.MANIFEST
        ):
            raise OSError("injected replacement failure")
        return original(source, target)

    monkeypatch.setattr(skills.os, "replace", fail_once)
    expected = KeyboardInterrupt if "interrupt" in failure else OSError
    with pytest.raises(expected):
        skills.install_skills(catalog, root)
    assert snapshot(root) == before
    assert not (root / ".kllm-skills.lock").exists()
    assert not list(root.parent.glob(".kllm-skills-*"))


def test_rollback_failure_preserves_recovery_copies(library, tmp_path, monkeypatch):
    root = tmp_path / "installed"
    catalog = skills.discover_skills(library)
    skills.install_skills(catalog, root)
    original = skills.os.replace
    for skill in catalog:
        (skill.path / "references/notes.md").write_text("Updated")

    def fail(source, target):
        source, target = Path(source), Path(target)
        if target.name == skills.MANIFEST or source.parent.name == "old":
            raise OSError("replacement refused")
        return original(source, target)

    monkeypatch.setattr(skills.os, "replace", fail)
    with pytest.raises(skills.SkillError, match="needs recovery"):
        skills.install_skills(catalog, root)
    backups = list(root.parent.glob(".kllm-skills-*/old/*/SKILL.md"))
    assert len(backups) == 3
    assert all(path.read_text().startswith("---") for path in backups)


def test_destination_mutation_after_preflight_is_refused(library, tmp_path):
    catalog = skills.discover_skills(library)
    root = tmp_path / "installed"
    skills.install_skills(catalog, root)
    (catalog[0].path / "SKILL.md").write_text(
        (catalog[0].path / "SKILL.md").read_text() + "New instructions"
    )
    plan = skills.prepare_install(catalog, root)
    target = root / catalog[0].name / "SKILL.md"
    target.write_text("Concurrent local edit")
    with pytest.raises(skills.SkillError, match="changed during installation"):
        skills.apply_install(plan)
    assert target.read_text() == "Concurrent local edit"


def test_symlink_conflict_force_backs_up_link_without_touching_target(library, tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("Do not change")
    try:
        (root / "kllm-ops").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This OS/account cannot create symlinks")
    with pytest.raises(skills.SkillError, match="Conflicting"):
        skills.prepare_install(skills.discover_skills(library), root)
    messages = skills.apply_install(
        skills.prepare_install(skills.discover_skills(library), root, force=True)
    )
    backup = Path(messages[0].removeprefix("Backup: "))
    assert backup.is_symlink() and backup.resolve() == outside
    assert (outside / "secret.txt").read_text() == "Do not change"
    assert not (root / "kllm-ops").is_symlink()


def test_source_symlink_rejected(library, tmp_path):
    try:
        (library / "operations/kllm-net/escape").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("This OS/account cannot create symlinks")
    with pytest.raises(skills.SkillError, match="symlinks"):
        skills.discover_skills(library)


def test_invalid_manifest_cannot_escape_install_root(library, tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    (root / skills.MANIFEST).write_text(
        json.dumps({"version": 1, "skills": {"../victim": {"files": {"SKILL.md": "0" * 64}}}})
    )
    with pytest.raises(skills.SkillError, match="Invalid ownership manifest"):
        skills.prepare_install(skills.discover_skills(library), root, prune=True)
    assert list(root.iterdir()) == [root / skills.MANIFEST]


def test_lock_blocks_mutation(library, tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    lock = root / ".kllm-skills.lock"
    lock.write_text("another process")
    with pytest.raises(skills.SkillError, match="lock exists"):
        skills.install_skills(skills.discover_skills(library), root)
    assert lock.read_text() == "another process"
    assert not (root / skills.MANIFEST).exists()


def test_standalone_list_and_dry_run_do_not_write(tmp_path):
    destination = tmp_path / "destination"
    listed = run_installer(tmp_path, "--list", "--category", "development")
    assert listed.returncode == 0 and len(listed.stdout.splitlines()) == 7
    dry = run_installer(tmp_path, "--dry-run", "--project-root", str(destination))
    assert dry.returncode == 0 and "codex, openclaw" in dry.stdout
    assert "no files changed" in dry.stdout and not destination.exists()


def test_standalone_installs_all_agents_from_another_directory(tmp_path):
    destination = tmp_path / "destination with spaces"
    result = run_installer(tmp_path, "--project-root", str(destination))
    assert result.returncode == 0, result.stderr
    for root in skills.agent_destinations(["all"], "project", destination):
        assert len(list(root.glob("*/SKILL.md"))) == 13
        assert (root / "kllm-10gbe/references/linux.md").is_file()
    before = snapshot(destination)
    repeated = run_installer(tmp_path, "--project-root", str(destination))
    assert repeated.returncode == 0 and snapshot(destination) == before


def test_cli_preflights_every_destination_before_writing(tmp_path):
    destination = tmp_path / "destination"
    conflict = destination / ".claude/skills/kllm-connect"
    conflict.mkdir(parents=True)
    (conflict / "SKILL.md").write_text("Local custom definition")
    result = run_installer(tmp_path, "--project-root", str(destination))
    assert result.returncode == 1 and "Conflicting" in result.stderr
    assert not (destination / ".agents").exists()
    assert (conflict / "SKILL.md").read_text() == "Local custom definition"


@pytest.mark.parametrize("field,value", [("name", "wrong-name"), ("description", '""')])
def test_invalid_frontmatter_rejected(library, field, value):
    file = library / "operations/kllm-ops/SKILL.md"
    file.write_text(
        re.sub(rf"^{field}:.*$", f"{field}: {value}", file.read_text(), flags=re.MULTILINE)
    )
    with pytest.raises(skills.SkillError, match="Invalid skill"):
        skills.discover_skills(library)
