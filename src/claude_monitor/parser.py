"""
Parser for Claude Code --output-format stream-json events.

Each line from the stream is a JSON object with a `type` field.
We normalize these into typed dataclasses for the TUI to consume.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --- Event types ---

class EventKind(str, Enum):
    SESSION_START  = "session_start"
    TEXT           = "text"
    TOOL_START     = "tool_start"
    TOOL_RESULT    = "tool_result"
    PHASE_CHANGE   = "phase_change"
    TICKET_CHANGE  = "ticket_change"
    COST_UPDATE    = "cost_update"
    SESSION_END    = "session_end"
    UNKNOWN        = "unknown"


@dataclass
class Event:
    kind: EventKind
    raw: dict[str, Any]


@dataclass
class SessionStartEvent(Event):
    session_id: str
    model: str


@dataclass
class TextEvent(Event):
    text: str


@dataclass
class ToolStartEvent(Event):
    tool_id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultEvent(Event):
    tool_id: str
    content: str
    is_error: bool = False


@dataclass
class PhaseChangeEvent(Event):
    phase_number: str   # "0", "1", "2", "2.5", "3a", etc.
    phase_label: str


@dataclass
class TicketChangeEvent(Event):
    ticket_id: str      # e.g. "ANC-42"


@dataclass
class CostUpdateEvent(Event):
    total_cost_usd: float
    input_tokens: int
    output_tokens: int


@dataclass
class SessionEndEvent(Event):
    result: str
    total_cost_usd: float


# --- Phase detection ---

PHASE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"phase\s*0|pr.health.check|health.check", re.I), "0", "PR Health Check"),
    (re.compile(r"phase\s*1|pick.*(ticket|issue)|linear.*ticket", re.I), "1", "Pick Ticket"),
    (re.compile(r"phase\s*2\.5|pre.?review|pre-review.gate", re.I), "2.5", "Pre-Review Gate"),
    (re.compile(r"phase\s*2|implement", re.I), "2", "Implement"),
    (re.compile(r"phase\s*3[abc]?|push.*pr|creat.*pr|codex.*review", re.I), "3", "Push PR & Review"),
    (re.compile(r"phase\s*4|merge.gate|merging", re.I), "4", "Merge Gate"),
    (re.compile(r"phase\s*5|linear.*done|mark.*done", re.I), "5", "Mark Done"),
    (re.compile(r"phase\s*6|session.retro|retro", re.I), "6", "Session Retro"),
]

TICKET_PATTERN = re.compile(r"\b([A-Z]{2,6}-\d+)\b")


def _detect_phase(text: str) -> tuple[str, str] | None:
    for pattern, num, label in PHASE_PATTERNS:
        if pattern.search(text):
            return num, label
    return None


def _detect_ticket(text: str) -> str | None:
    m = TICKET_PATTERN.search(text)
    return m.group(1) if m else None


def _extract_tool_input_summary(name: str, input_data: dict) -> dict:
    """Return a display-friendly version of tool input."""
    if name == "Bash":
        return {"command": input_data.get("command", "")}
    if name in ("Read", "Write", "Edit"):
        return {"file": input_data.get("file_path", input_data.get("path", ""))}
    if name == "Grep":
        return {"pattern": input_data.get("pattern", ""), "path": input_data.get("path", "")}
    if name == "Glob":
        return {"pattern": input_data.get("pattern", "")}
    return input_data


def _flatten_content(content: Any) -> str:
    """Flatten content blocks or plain string to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


class StreamParser:
    """
    Stateful parser — call feed(line) for each raw JSON line.
    Yields zero or more Event objects per line.
    """

    def __init__(self) -> None:
        self._pending_tools: dict[str, ToolStartEvent] = {}  # tool_id → event

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return []

        events: list[Event] = []
        t = obj.get("type", "")

        if t == "system" and obj.get("subtype") == "init":
            events.append(SessionStartEvent(
                kind=EventKind.SESSION_START,
                raw=obj,
                session_id=obj.get("session_id", ""),
                model=obj.get("model", ""),
            ))

        elif t == "assistant":
            message = obj.get("message", {})
            for block in message.get("content", []):
                btype = block.get("type", "")

                if btype == "text":
                    text = block.get("text", "")
                    events.append(TextEvent(kind=EventKind.TEXT, raw=obj, text=text))

                    # Detect phase/ticket transitions in Claude's text
                    if phase := _detect_phase(text):
                        events.append(PhaseChangeEvent(
                            kind=EventKind.PHASE_CHANGE, raw=obj,
                            phase_number=phase[0], phase_label=phase[1],
                        ))
                    if ticket := _detect_ticket(text):
                        events.append(TicketChangeEvent(
                            kind=EventKind.TICKET_CHANGE, raw=obj, ticket_id=ticket,
                        ))

                elif btype == "tool_use":
                    tool_id = block.get("id", "")
                    name = block.get("name", "")
                    inp = _extract_tool_input_summary(name, block.get("input", {}))
                    ev = ToolStartEvent(
                        kind=EventKind.TOOL_START, raw=obj,
                        tool_id=tool_id, name=name, input=inp,
                    )
                    self._pending_tools[tool_id] = ev
                    events.append(ev)

        elif t == "user":
            message = obj.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    content = _flatten_content(block.get("content", ""))
                    is_error = block.get("is_error", False)
                    events.append(ToolResultEvent(
                        kind=EventKind.TOOL_RESULT, raw=obj,
                        tool_id=tool_id, content=content, is_error=is_error,
                    ))
                    self._pending_tools.pop(tool_id, None)

        elif t == "result":
            cost = obj.get("total_cost_usd", 0.0)
            usage = obj.get("usage", {})
            events.append(CostUpdateEvent(
                kind=EventKind.COST_UPDATE, raw=obj,
                total_cost_usd=cost,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            ))
            if obj.get("subtype") in ("success", "error_max_turns"):
                events.append(SessionEndEvent(
                    kind=EventKind.SESSION_END, raw=obj,
                    result=obj.get("result", ""),
                    total_cost_usd=cost,
                ))

        return events
