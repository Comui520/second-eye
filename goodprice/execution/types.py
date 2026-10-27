from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class Stage(str, Enum):
    SEARCH = "search"
    DETAIL = "detail"
    PARSE = "parse"
    MEDIA = "media"
    ANALYZE = "analyze"
    AGGREGATE = "aggregate"
    NOTIFY = "notify"


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class RunToken:
    run_id: int
    task_id: int
    task_version: int
    lease_id: str


@dataclass(frozen=True)
class WorkItemDTO:
    id: int
    token: RunToken
    stage: Stage
    input_fingerprint: str
    payload: Mapping
    attempt: int
    deadline_at: datetime


@dataclass(frozen=True)
class WorkResult:
    token: RunToken
    stage: Stage
    input_fingerprint: str
    output: Mapping

    def __post_init__(self):
        object.__setattr__(self, "output", freeze(self.output))
