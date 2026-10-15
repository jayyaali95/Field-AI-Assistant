import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import config


@dataclass(frozen=True)
class User:
    id: str
    name: str
    title: str
    groups: tuple[str, ...]

    @property
    def is_lead(self) -> bool:
        return "role-lead" in self.groups


@cache
def _users_file(data_dir: Path) -> dict:
    return json.loads((data_dir / "users.json").read_text())


def users(data_dir: Path = config.DATA_DIR) -> dict[str, User]:
    return {
        u["id"]: User(u["id"], u["name"], u["title"], tuple(u["groups"]))
        for u in _users_file(data_dir)["users"]
    }


def group_descriptions(data_dir: Path = config.DATA_DIR) -> dict[str, str]:
    return _users_file(data_dir)["groups"]
