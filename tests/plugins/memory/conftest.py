from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def pytest_configure() -> None:
    sys.path.insert(0, str(ROOT))
    sys.modules.setdefault("plugins", types.ModuleType("plugins"))
    sys.modules.setdefault("plugins.memory", types.ModuleType("plugins.memory"))
    if "plugins.memory.recall" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "plugins.memory.recall",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugins.memory.recall"] = module
    spec.loader.exec_module(module)
