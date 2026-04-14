from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _active_run_file() -> Path:
    return _repo_root() / "artifacts" / "_active_run_id.txt"


def get_latest_run_id() -> str:
    artifacts_dir = _repo_root() / "artifacts"
    if not artifacts_dir.exists():
        raise FileNotFoundError("artifacts directory does not exist yet")

    run_dirs = [
        path
        for path in artifacts_dir.iterdir()
        if path.is_dir() and path.name.startswith("run_")
    ]
    if not run_dirs:
        raise FileNotFoundError("No existing run directories found under artifacts/")

    run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return run_dirs[0].name


def set_active_run_id(run_id: str) -> Path:
    active_run_file = _active_run_file()
    active_run_file.parent.mkdir(parents=True, exist_ok=True)
    active_run_file.write_text(run_id, encoding="utf-8")
    return active_run_file


def get_active_run_id() -> str:
    active_run_file = _active_run_file()
    if active_run_file.exists():
        run_id = active_run_file.read_text(encoding="utf-8").strip()
        if run_id:
            return run_id
    return get_latest_run_id()


def ensure_run_subdir(run_id: str, phase: str) -> Path:
    run_dir = _repo_root() / "artifacts" / run_id / phase
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
