"""The two-service launcher must not leave its backend running on failure."""

import argparse
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("workspace_launcher", Path(__file__).resolve().parents[1] / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frontend = tmp_path / "frontend"
    (frontend / "node_modules" / "next").mkdir(parents=True)
    (frontend / "package.json").write_text("{}")
    (frontend / "node_modules" / "next" / "package.json").write_text("{}")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "node_runtime", lambda: (["node", "npm-cli.js"], "runtime-path"))
    monkeypatch.setattr(module, "ensure_port_available", lambda port: None)
    return module


def arguments(tmp_path):
    return argparse.Namespace(data=tmp_path / "data", out=tmp_path / "outputs", config=tmp_path / "config.json", port=3000, api_port=8000)


def test_backend_is_cleaned_up_when_readiness_fails(launcher, tmp_path, monkeypatch):
    backend = Mock()
    started, stopped = Mock(return_value=backend), Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", started)
    monkeypatch.setattr(launcher, "stop_process_tree", stopped)
    monkeypatch.setattr(launcher, "wait_for_backend", Mock(side_effect=RuntimeError("invalid dataset")))
    with pytest.raises(RuntimeError, match="invalid dataset"):
        launcher.launch_ui(arguments(tmp_path))
    assert started.call_count == 1
    stopped.assert_called_once_with(backend)


def test_both_services_stop_on_interrupt_and_proxy_target_matches_backend(launcher, tmp_path, monkeypatch):
    backend, frontend = Mock(), Mock()
    backend.poll.return_value = None
    frontend.poll.return_value = None
    started = Mock(side_effect=[backend, frontend])
    stopped = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", started)
    monkeypatch.setattr(launcher, "wait_for_backend", Mock())
    monkeypatch.setattr(launcher, "stop_process_tree", stopped)
    monkeypatch.setattr(launcher, "sleep", Mock(side_effect=KeyboardInterrupt))
    args = arguments(tmp_path)
    args.api_port = 8111
    assert launcher.launch_ui(args) == 130
    assert [call.args[0] for call in stopped.call_args_list] == [frontend, backend]
    environment = started.call_args_list[1].kwargs["env"]
    assert environment["API_BASE_URL"] == "http://127.0.0.1:8111"
    assert environment["NEXT_TELEMETRY_DISABLED"] == "1"
    assert environment["MONEY_GRAPH_DATA"] == str(args.data.resolve())


def test_frontend_spawn_failure_stops_backend(launcher, tmp_path, monkeypatch):
    backend, stopped = Mock(), Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(side_effect=[backend, OSError("cannot launch Next")]))
    monkeypatch.setattr(launcher, "wait_for_backend", Mock())
    monkeypatch.setattr(launcher, "stop_process_tree", stopped)
    with pytest.raises(OSError, match="cannot launch Next"):
        launcher.launch_ui(arguments(tmp_path))
    stopped.assert_called_once_with(backend)
