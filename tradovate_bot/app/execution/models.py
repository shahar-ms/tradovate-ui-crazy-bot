from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.utils.time_utils import now_ms


ActionT = Literal["BUY", "SELL", "CANCEL_ALL"]
StatusT = Literal["ok", "failed", "unknown", "blocked"]
ExecModeT = Literal["click", "hotkey"]


def _new_intent_id() -> str:
    return uuid.uuid4().hex[:12]


class ExecutionIntent(BaseModel):
    intent_id: str = Field(default_factory=_new_intent_id)
    action: ActionT
    ts_ms: int = Field(default_factory=now_ms)
    reason: str = ""
    expected_side: Optional[Literal["long", "short", "flat"]] = None
    metadata: dict = Field(default_factory=dict)


class ExecutionAck(BaseModel):
    intent_id: str
    action: ActionT
    status: StatusT
    ts_ms: int = Field(default_factory=now_ms)
    message: str = ""
    screen_guard_passed: bool = True
    evidence_path: Optional[str] = None
    mode: ExecModeT = "click"


class Hotkeys(BaseModel):
    buy: Optional[str] = None
    sell: Optional[str] = None
    cancel_all: Optional[str] = None


class ExecutionConfig(BaseModel):
    move_duration_ms: int = 80
    pre_click_delay_ms: int = 40
    post_click_delay_ms: int = 120
    double_click_enabled: bool = False
    enable_hotkey_fallback: bool = False
    hotkeys: Hotkeys = Field(default_factory=Hotkeys)
    max_unknown_acks_before_halt: int = 2
    anchor_match_threshold: float = 0.90
    dry_run: bool = True
    ack_evidence_save: bool = True
