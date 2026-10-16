"""Session skill injection through each harness boundary, without inference."""

import json
import shutil
from pathlib import Path

import pytest
import typer

from kllm.modules.connect.interactors import agent_skills, connect
from kllm.cli import main
from kllm.modules.connect import router_cli as command

ROOT = Path(__file__).resolve().parents[1]
PROFILE_KEYS = {
    "codex": "CODEX_HOME",
    "hermes": "HERMES_HOME",
    "openclaw": "OPENCLAW_STATE_DIR",
    "pi": "PI_CODING_AGENT_DIR",
}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("KLLM_URL", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    monkeypatch.setattr(connect, "repo_root", lambda: ROOT)
    monkeypatch.setattr(connect, "_which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(connect, "_opencode_service_client", lambda _: False)
    monkeypatch.setattr(
        connect, "_ensure_inference", lambda *args: ("http://master:1234", "/models/served.gguf")
    )
    scratch = tmp_path / "sessions"
    scratch.mkdir()
    original = connect.tempfile.mkdtemp
    monkeypatch.setattr(
        connect.tempfile, "mkdtemp", lambda **kwargs: original(**{"dir": scratch, **kwargs})
    )
    return tmp_path


def build(agent, **kwargs):
    return connect.build_launch_plan(
        agent, api_key="private-test-token", url="http://master", **kwargs
    )


def skill_root(plan):
    if plan.agent in PROFILE_KEYS:
        return Path(plan.env[PROFILE_KEYS[plan.agent]]) / "skills"
    if plan.agent == "claude":
        return Path(plan.argv[plan.argv.index("--add-dir") + 1]) / ".claude/skills"
    config = json.loads(plan.env["OPENCODE_CONFIG_CONTENT"])
    sources = config["skills"] if isinstance(config["skills"], list) else config["skills"]["paths"]
    return Path(sources[-1])


@pytest.mark.parametrize("agent", connect.AGENTS)
def test_every_harness_receives_complete_selected_pack(isolated, agent):
    plan = build(agent, kllm_skills="operations", extra=["--verbose"])
    root = skill_root(plan)
    assert {p.parent.name for p in root.glob("*/SKILL.md")} == {
        s.name for s in agent_skills.discover_skills(ROOT / "skills", "operations")
    }
    assert (root / "kllm-10gbe/references/windows.md").is_file()
    assert "--verbose" in plan.argv
    assert any("operations (6)" in note for note in plan.notes)
    assert all("private-test-token" not in p.read_text() for p in root.rglob("*") if p.is_file())
    for directory in plan.cleanup_dirs:
        shutil.rmtree(directory)


@pytest.mark.parametrize("agent", list(PROFILE_KEYS))
def test_persistent_category_changes_and_none_preserve_user_skills(isolated, agent):
    first = build(agent, persist=True)
    root = skill_root(first)
    assert len(list(root.glob("*/SKILL.md"))) == 10
    assert (root / "kllm-prima/SKILL.md").is_file()
    (root / "user-custom").mkdir()
    (root / "user-custom/SKILL.md").write_text("User skill")
    second = build(agent, persist=True, kllm_skills="operations")
    assert skill_root(second) == root
    assert not (root / "kllm-develop").exists()
    assert not (root / "kllm-prima").exists()
    assert len(list(root.glob("*/SKILL.md"))) == 7
    third = build(agent, persist=True, kllm_skills="none")
    assert not third.cleanup_dirs
    assert list(root.glob("*/SKILL.md")) == [root / "user-custom/SKILL.md"]
    assert (root / "user-custom/SKILL.md").read_text() == "User skill"


def test_persistent_local_edit_blocks_prune(isolated):
    plan = build("hermes", persist=True)
    edited = skill_root(plan) / "kllm-develop/SKILL.md"
    edited.write_text("Local edited instructions")
    with pytest.raises(typer.BadParameter, match="Locally edited session"):
        build("hermes", persist=True, kllm_skills="operations")
    assert edited.read_text() == "Local edited instructions"


@pytest.mark.parametrize("v2", [False, True])
def test_opencode_preserves_other_sources_and_provider(isolated, monkeypatch, v2):
    monkeypatch.setattr(connect, "_opencode_service_client", lambda _: v2)
    existing = (
        ["/existing"] if v2 else {"paths": ["/existing"], "urls": ["https://skills.example/"]}
    )
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"skills": existing}))
    plan = build("opencode", kllm_skills="development", extra=["run", "hello"])
    cfg = json.loads(plan.env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == "kllm//models/served.gguf"
    assert cfg["provider"]["kllm"]["options"]["apiKey"] == "private-test-token"
    if v2:
        assert cfg["skills"][0] == "/existing"
        assert plan.argv[:3] == ["/fake/opencode", "run", "--standalone"]
    else:
        assert cfg["skills"]["paths"][0] == "/existing"
        assert cfg["skills"]["urls"] == existing["urls"]
    assert len(list(skill_root(plan).glob("*/SKILL.md"))) == 4


def test_claude_additional_directory_precedes_argument_separator(isolated):
    plan = build("claude", extra=["--", "prompt"])
    assert plan.argv.index("--add-dir") < plan.argv.index("--")
    assert plan.env["ANTHROPIC_MODEL"] == "/models/served.gguf"


@pytest.mark.parametrize("agent", connect.AGENTS)
def test_none_does_not_create_pack_or_extra_sources(isolated, agent):
    plan = build(agent, kllm_skills="none")
    if agent in PROFILE_KEYS:
        assert not (Path(plan.env[PROFILE_KEYS[agent]]) / "skills").exists()
    elif agent == "claude":
        assert "--add-dir" not in plan.argv
    else:
        assert "skills" not in json.loads(plan.env["OPENCODE_CONFIG_CONTENT"])


def test_missing_library_keeps_connection_behavior(isolated, monkeypatch):
    monkeypatch.setattr(connect, "repo_root", lambda: None)
    plan = build("codex")
    assert any("KLLM_REPO_ROOT" in note for note in plan.notes)
    assert not (Path(plan.env["CODEX_HOME"]) / "skills").exists()


def test_invalid_library_fails_before_inference(isolated, monkeypatch, tmp_path):
    (tmp_path / "skills").mkdir()
    monkeypatch.setattr(connect, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        connect, "_ensure_inference", lambda *args: pytest.fail("must not prepare inference")
    )
    with pytest.raises(typer.BadParameter, match="Missing or unsafe"):
        build("codex")


def test_invalid_inherited_opencode_config_cleans_scratch(isolated, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "invalid JSON")
    with pytest.raises(typer.BadParameter, match="Invalid inherited"):
        build("opencode")
    assert not list((isolated / "sessions").iterdir())


@pytest.mark.parametrize("agent", ["claude", "hermes"])
def test_interrupted_skill_preparation_removes_temporary_profile(isolated, monkeypatch, agent):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(agent_skills, "install_skills", interrupt)
    with pytest.raises(KeyboardInterrupt):
        build(agent)
    assert not list((isolated / "sessions").iterdir())


@pytest.mark.parametrize("fail", [False, True])
def test_process_exit_and_failure_remove_temporary_pack(isolated, monkeypatch, fail):
    plan = build("hermes")
    dirs = list(plan.cleanup_dirs)

    def execute(*args, **kwargs):
        if fail:
            raise OSError("cannot execute")
        return type("Exit", (), {"returncode": 7})()

    monkeypatch.setattr(connect.subprocess, "run", execute)
    if fail:
        with pytest.raises(OSError, match="cannot execute"):
            connect.run_plan(plan)
    else:
        assert connect.run_plan(plan) == 7
    assert all(not d.exists() for d in dirs)


def test_cli_no_launch_redacts_and_cleans_skill_sources(isolated, monkeypatch, capsys):
    plans = []
    original = command.print_plan

    def record(plan):
        plans.append(plan)
        assert len(list(skill_root(plan).glob("*/SKILL.md"))) == 6
        original(plan)

    monkeypatch.setattr(command, "print_plan", record)
    assert (
        main(
            [
                "connect",
                "claude",
                "--url",
                "http://master",
                "--api-key",
                "private-test-token",
                "--kllm-skills",
                "operations",
                "--no-launch",
            ]
        )
        == 0
    )
    assert all(not d.exists() for d in plans[0].cleanup_dirs)
    output = capsys.readouterr()
    assert "private-test-token" not in output.out + output.err


def test_cli_invalid_selection_never_starts_local_agent(isolated, monkeypatch):
    monkeypatch.setattr(command, "_ensure_agent", lambda *args: pytest.fail("must not start agent"))
    assert main(["connect", "hermes", "--kllm-skills", "invalid"]) != 0
