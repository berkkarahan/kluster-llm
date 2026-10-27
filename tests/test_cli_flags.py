"""CLI contract for the feature flag command."""

from kllm.cli import main


def test_flags_lists_and_toggles(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))

    assert main(["flags"]) == 0
    out = capsys.readouterr().out
    assert "private-network-setup = disabled" in out
    assert "prima-auto-deploy = disabled" in out

    assert main(["flags", "private-network-setup", "--enable"]) == 0
    assert "private-network-setup = enabled" in capsys.readouterr().out

    assert main(["flags", "private-network-setup"]) == 0
    assert "private-network-setup = enabled" in capsys.readouterr().out

    assert main(["flags", "private-network-setup", "--disable"]) == 0
    assert "private-network-setup = disabled" in capsys.readouterr().out

    assert main(["flags", "prima-auto-deploy", "--enable"]) == 0
    assert "prima-auto-deploy = enabled" in capsys.readouterr().out

    assert main(["flags", "prima-auto-deploy"]) == 0
    assert "prima-auto-deploy = enabled" in capsys.readouterr().out

    assert main(["flags", "prima-auto-deploy", "--disable"]) == 0
    assert "prima-auto-deploy = disabled" in capsys.readouterr().out


def test_flags_unknown_name_is_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    assert main(["flags", "nope", "--enable"]) == 2
    assert "unknown feature flag" in capsys.readouterr().err


def test_run_cli_prima_auto_deploy_option(monkeypatch, tmp_path):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    from contextlib import contextmanager
    import kllm.modules.inference.router_cli as inference
    from kllm.shared import config as kllm_config

    payloads = []

    @contextmanager
    def fake_client(port):
        class Response:
            status_code = 200

            def json(self):
                return {"fit_status": "ok", "run_id": "test"}

        class Client:
            def post(self, path, json=None):
                payloads.append((path, json))
                return Response()

        yield Client()

    monkeypatch.setattr(inference, "_client", fake_client)

    # 1. Explicit --prima-auto-deploy
    assert main(["run", "--model", "toy.gguf", "--prima-auto-deploy"]) == 0
    assert payloads[-1][0] == "/v2/inference/runs"
    assert payloads[-1][1]["prima_auto_deploy"] is True

    # 2. Explicit --no-prima-auto-deploy
    assert main(["run", "--model", "toy.gguf", "--no-prima-auto-deploy"]) == 0
    assert payloads[-1][0] == "/v2/inference/runs"
    assert payloads[-1][1]["prima_auto_deploy"] is False

    # 3. Omitted when disabled (default)
    assert main(["run", "--model", "toy.gguf"]) == 0
    assert payloads[-1][0] == "/v2/inference/runs"
    assert "prima_auto_deploy" not in payloads[-1][1]

    # 4. Fallback to config when enabled globally
    kllm_config.set_feature_flag("prima-auto-deploy", True)
    assert main(["run", "--model", "toy.gguf"]) == 0
    assert payloads[-1][0] == "/v2/inference/runs"
    assert payloads[-1][1]["prima_auto_deploy"] is True

    # 5. Explicit --no-prima-auto-deploy overrides config
    assert main(["run", "--model", "toy.gguf", "--no-prima-auto-deploy"]) == 0
    assert payloads[-1][0] == "/v2/inference/runs"
    assert payloads[-1][1]["prima_auto_deploy"] is False


def test_cluster_cli_rejects_removed_overlay_option(capsys):
    for command in ("start", "join"):
        assert main([command, "--overlay", "wireguard"]) == 2
        assert "No such option: --overlay" in capsys.readouterr().err


def test_flags_persist_across_stop_and_restart(monkeypatch, tmp_path, capsys):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))

    assert main(["flags", "prima-auto-deploy", "--enable"]) == 0
    assert "prima-auto-deploy = enabled" in capsys.readouterr().out
    assert (home / "managed-network.sqlite").is_file()

    # Simulate kllm stop (wiping runtime pids/state while keeping SQLite DB)
    assert main(["stop"]) == 0
    capsys.readouterr()

    # Verify flag remains enabled after stop and in subsequent CLI/config reads
    assert main(["flags", "prima-auto-deploy"]) == 0
    assert "prima-auto-deploy = enabled" in capsys.readouterr().out
    assert main(["flags", "private-network-setup"]) == 0
    assert "private-network-setup = disabled" in capsys.readouterr().out
