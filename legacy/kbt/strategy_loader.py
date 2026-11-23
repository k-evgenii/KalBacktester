"""
strategy_loader.py — one place to turn a .py file (or a built-in name) into a
strategy instance. Shared by the CLI (kbt.run), the sweep, and the WebSocket
server so there is a single loading mechanism and the engine never imports a
concrete strategy.

A strategy file must expose EITHER:
    strategy = MyStrategy(...)        # a module-level instance named `strategy`
or define exactly ONE class with an `on_event` method (instantiated with no
args).
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

# location of the built-in package strategies (kbt/strategies/*.py)
import kbt as _kbt_pkg
PKG_STRAT_DIR = Path(_kbt_pkg.__file__).parent / "strategies"

# files in the package strategies dir that are not themselves strategies
PKG_SKIP = {"__init__.py", "base.py", "types.py", "registry.py",
            "decision_builder.py", "belief.py"}


def extract_strategy(mod):
    """Pull a strategy instance out of an imported/loaded module."""
    if hasattr(mod, "strategy"):
        return mod.strategy
    candidates = [
        v for v in vars(mod).values()
        if isinstance(v, type) and hasattr(v, "on_event")
        and getattr(v, "__module__", "") == mod.__name__
    ]
    if len(candidates) == 1:
        return candidates[0]()
    raise ValueError(
        "Strategy file must define a module-level `strategy` instance, "
        "or exactly one class with an on_event() method."
    )


def load_from_path(path: Path):
    """Load a strategy file by absolute/relative filesystem path."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"strategy file not found: {path}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return extract_strategy(mod)


def load_builtin(stem: str):
    """Load a built-in strategy by module stem (kbt.strategies.<stem>).
    Uses import_module so the strategy's relative imports resolve."""
    mod = importlib.import_module(f"kbt.strategies.{stem}")
    return extract_strategy(mod)


def load_strategy(ref: str):
    """Resolve a CLI-style reference to a strategy instance.
      - if `ref` points to an existing .py file -> load_from_path
      - otherwise treat `ref` as a built-in stem  -> load_builtin
    """
    p = Path(ref)
    if p.suffix == ".py" or p.exists():
        return load_from_path(p)
    return load_builtin(ref)
