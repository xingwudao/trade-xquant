from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_xquant.models import RebalanceTask


class LocalTaskFileAdapter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_pending_tasks(self, account_id: str, limit: int = 10) -> list[RebalanceTask]:
        raw_tasks = self._load_tasks()
        tasks: list[RebalanceTask] = []
        for raw_task in raw_tasks:
            if raw_task.get("account_id") != account_id:
                continue
            tasks.append(RebalanceTask.model_validate({**raw_task, "raw": raw_task}))
            if len(tasks) >= limit:
                break
        return tasks

    def _load_tasks(self) -> list[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            raw_tasks = data
        elif isinstance(data, dict):
            raw_tasks = data.get("tasks", [])
        else:
            raise ValueError("local task file must contain a task list or an object with tasks")
        if not isinstance(raw_tasks, list):
            raise ValueError("local task file tasks must be a list")
        return [self._require_mapping(task) for task in raw_tasks]

    def _require_mapping(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("local task entries must be JSON objects")
        return value
