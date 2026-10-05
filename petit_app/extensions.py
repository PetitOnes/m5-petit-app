"""Extensions: house-specific features loaded at startup.

Every `*.py` directly under `PETIT_APP_EXTENSIONS_DIR` (default:
`$PETIT_DATA_DIR/app_extensions`) is imported in filename order and its
`register(app, ctx)` is called. Files starting with `_` are skipped.

An extension that raises, or that tries to register a route the base app
already has, is rolled back and skipped — the dashboard itself always starts.
Extensions are plain Python running with the dashboard's privileges, so only
install ones you trust.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI

from . import config
from .auth import get_current_user
from .characters import char_dir, require_character

router = APIRouter()

_nav: list[dict] = []
_loaded: list[str] = []
_failed: list[dict] = []


def _version() -> str:
    try:
        return importlib.metadata.version("m5-petit-app")
    except importlib.metadata.PackageNotFoundError:
        return "0"


class ExtensionContext:
    """What an extension's `register(app, ctx)` receives."""

    def __init__(self) -> None:
        self.data_dir: Path = config.DATA_DIR
        self.char_dir = char_dir
        self.require_user = get_current_user
        self.require_character = require_character
        self.version: str = _version()

    def add_nav(self, label: str, path: str, order: int = 100) -> None:
        """Add one entry to the dashboard menu."""
        _nav.append({"label": str(label), "path": str(path), "order": int(order)})


def extensions_dir() -> Path:
    return Path(os.environ.get("PETIT_APP_EXTENSIONS_DIR", config.DATA_DIR / "app_extensions"))


def _route_keys(routes) -> set[tuple[str, str]]:
    """(METHOD, path) of every route, looking inside included routers too."""
    keys: set[tuple[str, str]] = set()
    for r in routes:
        inner = getattr(r, "original_router", None) or getattr(r, "router", None)
        if inner is not None and hasattr(inner, "routes"):
            keys |= _route_keys(inner.routes)
        elif hasattr(r, "path"):
            for m in getattr(r, "methods", None) or {"*"}:
                keys.add((m, r.path))
    return keys


def load_extensions(app: FastAPI) -> None:
    """Import every extension file and let it register itself on `app`."""
    _nav.clear()
    _loaded.clear()
    _failed.clear()
    ext_dir = extensions_dir()
    if not ext_dir.is_dir():
        return
    ctx = ExtensionContext()
    for path in sorted(ext_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        base_keys = _route_keys(app.router.routes)
        n_routes = len(app.router.routes)
        n_nav = len(_nav)
        try:
            spec = importlib.util.spec_from_file_location(f"petit_app_ext_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.register(app, ctx)
            clash = sorted(_route_keys(app.router.routes[n_routes:]) & base_keys)
            if clash:
                raise RuntimeError(f"route conflict {clash[0][0]} {clash[0][1]}")
        except Exception as e:  # an extension must never take the dashboard down
            del app.router.routes[n_routes:]
            del _nav[n_nav:]
            skipped = isinstance(e, RuntimeError) and str(e).startswith("route conflict")
            print(f"extension {path.name} {'skipped' if skipped else 'failed'}: {e}", file=sys.stderr)
            _failed.append({"file": path.name, "error": str(e)})
            continue
        _loaded.append(path.name)


@router.get("/api/extensions")
async def api_extensions(user: dict = Depends(get_current_user)):
    return {"loaded": list(_loaded), "failed": list(_failed)}


@router.get("/api/extensions/nav")
async def api_extensions_nav(user: dict = Depends(get_current_user)):
    return [{"label": n["label"], "path": n["path"]} for n in sorted(_nav, key=lambda n: n["order"])]
