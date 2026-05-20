import json
from pathlib import Path
from ..infra.repository import repo
from ..settings import settings
from .search import rank_records

def _load(name: str):
    return json.loads((Path(settings.data_dir)/name).read_text(encoding="utf-8"))

def get_operations_snapshot(date: str = "") -> str:
    """Return internal operations, queue, and workload data for planning and forecasting."""
    rows = _load("operations_data.json")
    if date:
        rows = [row for row in rows if row.get("date") == date] or rows
    return json.dumps(rows, indent=2)

def persist_validated_schedule(schedule_json: str) -> str:
    """Persist a validated staffing schedule after the validation agent approves it."""
    return json.dumps(repo.save_schedule(schedule_json), indent=2)

def get_support_playbooks(query: str) -> str:
    """Retrieve internal support guidance playbooks and operating recommendations."""
    return json.dumps(rank_records(query, _load("support_playbooks.json"), ["title","body","tags"], 4), indent=2)
