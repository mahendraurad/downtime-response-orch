"""Load repository environment files without overriding deployment settings."""
from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_project_environment() -> None:
    """Load ``.env`` followed by ignored ``.env.local`` overrides.

    Values already supplied by the process, test runner, container, App Service,
    or Key Vault integration always win. ``.env.local`` overrides only values
    originating in the repository's base ``.env`` file.
    """
    protected = set(os.environ)
    values: dict[str, str | None] = {}
    try:
        from dotenv import dotenv_values

        for path in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local"):
            if path.exists():
                values.update(dotenv_values(path))
    except ImportError:
        for path in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local"):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")

    for name, value in values.items():
        if name not in protected and value is not None:
            os.environ[name] = value
