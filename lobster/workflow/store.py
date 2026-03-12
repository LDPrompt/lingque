"""
工作流状态持久化

将 WorkflowRun 保存到 JSON 文件，支持审批后 resume
"""

import json
import os
import logging
from typing import Optional
from .models import WorkflowRun

logger = logging.getLogger("lobster.workflow")


class WorkflowStore:
    """工作流运行状态持久化"""

    def __init__(self, store_dir: str):
        self._dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _path(self, run_id: str) -> str:
        return os.path.join(self._dir, f"{run_id}.json")

    def save(self, run: WorkflowRun):
        """保存工作流运行状态"""
        try:
            with open(self._path(run.id), "w", encoding="utf-8") as f:
                json.dump(run.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存工作流状态失败: {e}")

    def load(self, run_id: str) -> Optional[dict]:
        """加载工作流运行状态"""
        path = self._path(run_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载工作流状态失败: {e}")
            return None

    def find_by_token(self, token: str) -> Optional[dict]:
        """通过 resume_token 查找暂停的工作流"""
        for filename in os.listdir(self._dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("resume_token") == token:
                    return data
            except Exception:
                continue
        return None

    def list_runs(self, status: str = None, limit: int = 20) -> list[dict]:
        """列出工作流运行记录"""
        runs = []
        for filename in sorted(os.listdir(self._dir), reverse=True):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if status and data.get("status") != status:
                    continue
                runs.append(data)
                if len(runs) >= limit:
                    break
            except Exception:
                continue
        return runs

    def delete(self, run_id: str):
        """删除运行记录"""
        path = self._path(run_id)
        if os.path.exists(path):
            os.remove(path)
