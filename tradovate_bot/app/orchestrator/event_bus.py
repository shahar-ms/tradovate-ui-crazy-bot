"""
Queue-based event bus used to connect the capture, strategy, and execution
components inside a single process.

Queues (bounded):
  - price_queue:   PriceTick          producer=capture,  consumer=strategy
  - intent_queue:  SignalIntent       producer=strategy, consumer=execution
  - ack_queue:     ExecutionAck       producer=execution,consumer=supervisor
  - command_queue: RuntimeCommand     producer=operator, consumer=supervisor
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass

from app.capture.models import PriceTick
from app.execution.models import ExecutionAck
from app.strategy.models import SignalIntent

from .runtime_models import RuntimeCommand

log = logging.getLogger(__name__)


@dataclass
class EventBus:
    price_queue: "queue.Queue[PriceTick]"
    intent_queue: "queue.Queue[SignalIntent]"
    ack_queue: "queue.Queue[ExecutionAck]"
    command_queue: "queue.Queue[RuntimeCommand]"

    @classmethod
    def create(cls, price_maxsize: int = 1024, intent_maxsize: int = 256,
               ack_maxsize: int = 256, cmd_maxsize: int = 128) -> "EventBus":
        return cls(
            price_queue=queue.Queue(maxsize=price_maxsize),
            intent_queue=queue.Queue(maxsize=intent_maxsize),
            ack_queue=queue.Queue(maxsize=ack_maxsize),
            command_queue=queue.Queue(maxsize=cmd_maxsize),
        )

    def backlog(self) -> dict[str, int]:
        return {
            "price": self.price_queue.qsize(),
            "intent": self.intent_queue.qsize(),
            "ack": self.ack_queue.qsize(),
            "command": self.command_queue.qsize(),
        }
