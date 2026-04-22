from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session_root(session_root: Path) -> Path:
    session_root.mkdir(parents=True, exist_ok=True)
    return session_root


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _session_file(session_root: Path, session_id: str) -> Path:
    return ensure_session_root(session_root) / session_id / "session.json"


def _write_session(session_root: Path, session: dict[str, Any]) -> dict[str, Any]:
    session_dir = ensure_session_root(session_root) / session["session_id"]
    session_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = session_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    session["session_dir"] = str(session_dir)
    session["output_dir"] = str(Path(session.get("output_dir") or artifacts_dir))
    safe = _json_safe(session)
    _session_file(session_root, session["session_id"]).write_text(
        json.dumps(safe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return safe


def create_session(
    session_root: Path,
    *,
    board_path: str,
    output_dir: str | None = None,
    session_name: str | None = None,
    description: str | None = None,
    coordinate_mode: str = "algorithm_only",
    placement_mode: str = "auto",
) -> dict[str, Any]:
    session_id = f"rs_{uuid.uuid4().hex[:10]}"
    session_dir = ensure_session_root(session_root) / session_id
    artifacts_dir = session_dir / "artifacts"
    session = {
        "session_id": session_id,
        "session_name": session_name or session_id,
        "description": description,
        "board_path": board_path,
        "working_board_path": board_path,
        "coordinate_mode": coordinate_mode,
        "placement_mode": placement_mode,
        "status": "created",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "session_dir": str(session_dir),
        "output_dir": str(Path(output_dir) if output_dir else artifacts_dir),
        "analysis": None,
        "objective": None,
        "constraints": {},
        "proposed_plan": None,
        "execution_history": [],
        "latest_checks": {},
        "placement_context": None,
        "latest_placement_validation": None,
        "placement_history": [],
        "coordinate_context": None,
        "latest_coordinate_validation": None,
        "coordinate_history": [],
        "artifacts": {
            "boards": [board_path],
            "logs": [],
        },
        "notes": [],
    }
    return _write_session(session_root, session)


def load_session(session_root: Path, session_id: str) -> dict[str, Any]:
    path = _session_file(session_root, session_id)
    if not path.exists():
        raise FileNotFoundError(f"Routing session not found: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(session_root: Path, session: dict[str, Any]) -> dict[str, Any]:
    session = deepcopy(session)
    session["updated_at"] = utc_now_iso()
    return _write_session(session_root, session)


def list_sessions(session_root: Path) -> list[dict[str, Any]]:
    root = ensure_session_root(session_root)
    sessions: list[dict[str, Any]] = []
    for session_file in sorted(root.glob("*/session.json"), reverse=True):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "session_id": data.get("session_id"),
                    "session_name": data.get("session_name"),
                    "status": data.get("status"),
                    "board_path": data.get("board_path"),
                    "working_board_path": data.get("working_board_path"),
                    "coordinate_mode": data.get("coordinate_mode"),
                    "placement_mode": data.get("placement_mode"),
                    "updated_at": data.get("updated_at"),
                    "created_at": data.get("created_at"),
                }
            )
        except Exception:
            continue
    sessions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return sessions


def add_note(session: dict[str, Any], note: str) -> dict[str, Any]:
    session.setdefault("notes", []).append(
        {
            "timestamp": utc_now_iso(),
            "note": note,
        }
    )
    return session
