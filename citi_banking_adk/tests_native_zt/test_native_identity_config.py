from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


_ENV_SETTINGS = {
    "agent_key_map_path": "CITI_ZT_AGENT_KEY_MAP",
    "identity_registry_path": "CITI_ZT_IDENTITY_REGISTRY",
    "identity_registry_root_public_key_path": "CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY",
    "identity_registry_root_key_id": "CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID",
}


@pytest.fixture
def config_class(monkeypatch):
    for env_name in _ENV_SETTINGS.values():
        monkeypatch.delenv(env_name, raising=False)
    package_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "native_identity_config_test", package_root / "bank_adk" / "zero_trust" / "config.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module.ZeroTrustConfig


def _settings(tmp_path):
    return {
        "agent_key_map_path": tmp_path / "agents.json",
        "identity_registry_path": tmp_path / "registry.yaml",
        "identity_registry_root_public_key_path": tmp_path / "root-public.pem",
        "identity_registry_root_key_id": "registry-root-v1",
    }


def _build(config_class, tmp_path, **kwargs):
    return config_class.build(
        enabled=True, backplane_root=tmp_path / "backplane", mas_repo_root=tmp_path / "mas", **kwargs
    )


def test_build_reads_operator_identity_environment(config_class, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    for name, env_name in _ENV_SETTINGS.items():
        monkeypatch.setenv(env_name, str(settings[name]))
    config = _build(config_class, tmp_path)
    for name, value in settings.items():
        assert getattr(config, name) == value


def test_explicit_identity_config_overrides_environment(config_class, tmp_path, monkeypatch):
    for env_name in _ENV_SETTINGS.values():
        monkeypatch.setenv(env_name, "unused-environment-value")
    settings = _settings(tmp_path)
    config = _build(config_class, tmp_path, **settings)
    for name, value in settings.items():
        assert getattr(config, name) == value


@pytest.mark.parametrize(
    "missing",
    ["identity_registry_path", "identity_registry_root_public_key_path", "identity_registry_root_key_id"],
)
def test_partial_trust_anchor_configuration_is_rejected(config_class, tmp_path, missing):
    settings = _settings(tmp_path)
    settings.pop(missing)
    with pytest.raises(ValueError, match="must be configured together"):
        _build(config_class, tmp_path, **settings)


def test_empty_root_key_id_is_rejected(config_class, tmp_path):
    settings = _settings(tmp_path)
    settings["identity_registry_root_key_id"] = "  "
    with pytest.raises(ValueError, match="must not be empty"):
        _build(config_class, tmp_path, **settings)


def test_absent_identity_configuration_does_not_invent_credentials(config_class, tmp_path):
    config = _build(config_class, tmp_path)
    assert all(getattr(config, name) is None for name in _ENV_SETTINGS)


def test_relative_config_paths_resolve_from_operator_working_directory(config_class, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _build(
        config_class,
        tmp_path,
        agent_key_map_path="agents.json",
        identity_registry_path="registry.yaml",
        identity_registry_root_public_key_path="root-public.pem",
        identity_registry_root_key_id="registry-root-v1",
    )
    for name, value in _settings(tmp_path).items():
        assert getattr(config, name) == value


@pytest.mark.parametrize("disable_zero_trust", [False, True])
def test_cli_passes_identity_configuration_to_runner(
    config_class, tmp_path, monkeypatch, capsys, disable_zero_trust
):
    # Keep this wiring test offline: no ADK imports, model requests, or key reads.
    received = {}

    async def fake_run_message(message, **kwargs):
        received.update(kwargs)
        return {"message": message}

    fake_dotenv = ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda: None
    fake_bank = ModuleType("bank_adk")
    fake_bank.__path__ = []
    fake_runner = ModuleType("bank_adk.runner")
    fake_runner.run_message = fake_run_message
    fake_zt = ModuleType("bank_adk.zero_trust")
    fake_zt.ZeroTrustConfig = config_class
    for name, module in {
        "dotenv": fake_dotenv,
        "bank_adk": fake_bank,
        "bank_adk.runner": fake_runner,
        "bank_adk.zero_trust": fake_zt,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    # Avoid changing the process event-loop policy while loading the CLI.
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda policy: None)
    spec = importlib.util.spec_from_file_location(
        "native_identity_cli_test", Path(__file__).resolve().parents[1] / "run_cli.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    settings = _settings(tmp_path)
    argv = ["run_cli.py", "--message", "hello"]
    flags = {
        "agent_key_map_path": "--zt-agent-key-map",
        "identity_registry_path": "--zt-identity-registry",
        "identity_registry_root_public_key_path": "--zt-identity-registry-root-public-key",
        "identity_registry_root_key_id": "--zt-identity-registry-root-key-id",
    }
    for name, flag in flags.items():
        argv.extend([flag, str(settings[name])])
    if disable_zero_trust:
        argv.append("--no-zero-trust")
    monkeypatch.setattr(sys, "argv", argv)
    asyncio.run(cli.main())
    config = received["zero_trust_config"]
    if disable_zero_trust:
        assert config is None
    else:
        assert config.enabled is True
        for name, value in settings.items():
            assert getattr(config, name) == value
    assert '"message": "hello"' in capsys.readouterr().out
