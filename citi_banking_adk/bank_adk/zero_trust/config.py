from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ZeroTrustConfig:
    enabled: bool
    backplane_root: Path
    mas_repo_root: Path
    output_dir: Path
    mode: str = "enforce"
    refresh: bool = False
    human_approval: bool = False
    secondary_authorization: bool = False
    change_ticket: str | None = None
    shadow_allow_critical: bool = False
    agent_key_map_path: Path | None = None
    identity_registry_path: Path | None = None
    identity_registry_root_public_key_path: Path | None = None
    identity_registry_root_key_id: str | None = None

    def __post_init__(self) -> None:
        registry_settings = (
            self.identity_registry_path,
            self.identity_registry_root_public_key_path,
            self.identity_registry_root_key_id,
        )
        if any(value is not None for value in registry_settings) and not all(
            value is not None for value in registry_settings
        ):
            raise ValueError(
                "identity_registry_path, identity_registry_root_public_key_path, "
                "and identity_registry_root_key_id must be configured together"
            )
        if self.identity_registry_root_key_id is not None and not self.identity_registry_root_key_id.strip():
            raise ValueError("identity_registry_root_key_id must not be empty")

    @classmethod
    def build(
        cls,
        *,
        enabled: bool,
        backplane_root: str | Path | None,
        mas_repo_root: str | Path,
        output_dir: str | Path | None = None,
        mode: str = "enforce",
        refresh: bool = False,
        human_approval: bool = False,
        secondary_authorization: bool = False,
        change_ticket: str | None = None,
        shadow_allow_critical: bool = False,
        agent_key_map_path: str | Path | None = None,
        identity_registry_path: str | Path | None = None,
        identity_registry_root_public_key_path: str | Path | None = None,
        identity_registry_root_key_id: str | None = None,
    ) -> "ZeroTrustConfig":
        def configured_path(value: str | Path | None, env_name: str) -> Path | None:
            configured = value if value is not None else os.getenv(env_name)
            return Path(configured).resolve() if configured else None

        mas_root = Path(mas_repo_root).resolve()
        configured = backplane_root or os.getenv("CITI_ZT_BACKPLANE_ROOT")
        if configured:
            bp_root = Path(configured).resolve()
        else:
            # Common local layout used by this project:
            # D:/Lab/Citi_P3 and D:/Lab/Citi_P3_Zero_Trust_Backplane
            candidate = mas_root.parent / "Citi_P3_Zero_Trust_Backplane"
            bp_root = candidate.resolve()
        out = (
            Path(output_dir).resolve()
            if output_dir
            else (bp_root / "native_mas_runtime" / "output").resolve()
        )
        return cls(
            enabled=bool(enabled),
            backplane_root=bp_root,
            mas_repo_root=mas_root,
            output_dir=out,
            mode=mode,
            refresh=bool(refresh),
            human_approval=bool(human_approval),
            secondary_authorization=bool(secondary_authorization),
            change_ticket=change_ticket,
            shadow_allow_critical=bool(shadow_allow_critical),
            agent_key_map_path=configured_path(agent_key_map_path, "CITI_ZT_AGENT_KEY_MAP"),
            identity_registry_path=configured_path(identity_registry_path, "CITI_ZT_IDENTITY_REGISTRY"),
            identity_registry_root_public_key_path=configured_path(
                identity_registry_root_public_key_path, "CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY"
            ),
            identity_registry_root_key_id=(
                identity_registry_root_key_id
                if identity_registry_root_key_id is not None
                else os.getenv("CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID") or None
            ),
        )
