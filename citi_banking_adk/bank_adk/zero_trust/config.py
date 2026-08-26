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
    ) -> "ZeroTrustConfig":
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
        )
