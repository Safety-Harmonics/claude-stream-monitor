"""
Automated TUI smoke tests — run with: pytest tests/
Tests catch crashes before manual testing is needed.
"""

import json
import pytest
from claude_monitor.parser import StreamParser, EventKind
from claude_monitor.app import MonitorApp, StatusBar
from claude_monitor.widgets.feed import FeedLog


# --- Sample stream-json events (real shapes from claude --output-format stream-json) ---

INIT = {"type": "system", "subtype": "init", "session_id": "abc123def456", "model": "claude-sonnet-4-6", "tools": []}

TEXT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Starting Phase 0: PR health check."}]}}

TOOL_BASH = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "gh pr list --author @me --state open --json number,title"}}]}}

TOOL_RESULT_OK = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": '[{"number":143,"title":"feat: harness"}]'}]}]}}

TOOL_RESULT_ERR = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "is_error": True, "content": [{"type": "text", "text": "GraphQL: Resource not accessible by personal access token"}]}]}}

TOOL_READ = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_2", "name": "Read", "input": {"file_path": "src/app/incidents/[id]/page.tsx"}}]}}

RESULT = {"type": "result", "subtype": "success", "result": "Done", "session_id": "abc123def456", "total_cost_usd": 0.0421, "usage": {"input_tokens": 12450, "output_tokens": 3210}}

TEXT_PHASE = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Phase 2: Implement — working on ANC-42"}]}}

TEXT_LONG = {"type": "assistant", "message": {"content": [{"type": "text", "text": "# Header\n\n```python\ndef foo():\n    pass\n```\n\n> blockquote\n\n" * 20}]}}

TEXT_UNICODE = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Unicode: 🚀 ✓ ← → … — ° © ® ™ \u0000 \uffff"}]}}

ALL_EVENTS = [INIT, TEXT, TOOL_BASH, TOOL_RESULT_OK, TOOL_RESULT_ERR, TOOL_READ, RESULT, TEXT_PHASE, TEXT_LONG, TEXT_UNICODE]


# --- Parser tests (no TUI needed) ---

class TestParser:
    def setup_method(self):
        self.parser = StreamParser()

    def _feed(self, obj):
        return self.parser.feed(json.dumps(obj))

    def test_init_event(self):
        events = self._feed(INIT)
        assert any(e.kind == EventKind.SESSION_START for e in events)

    def test_text_event(self):
        events = self._feed(TEXT)
        assert any(e.kind == EventKind.TEXT for e in events)

    def test_tool_start(self):
        events = self._feed(TOOL_BASH)
        assert any(e.kind == EventKind.TOOL_START for e in events)
        tool = next(e for e in events if e.kind == EventKind.TOOL_START)
        assert tool.name == "Bash"

    def test_tool_result_ok(self):
        self._feed(TOOL_BASH)  # register tool first
        events = self._feed(TOOL_RESULT_OK)
        assert any(e.kind == EventKind.TOOL_RESULT for e in events)

    def test_tool_result_error(self):
        events = self._feed(TOOL_RESULT_ERR)
        results = [e for e in events if e.kind == EventKind.TOOL_RESULT]
        assert any(e.is_error for e in results)

    def test_cost_update(self):
        events = self._feed(RESULT)
        assert any(e.kind == EventKind.COST_UPDATE for e in events)
        cost = next(e for e in events if e.kind == EventKind.COST_UPDATE)
        assert cost.total_cost_usd == pytest.approx(0.0421)

    def test_phase_detection(self):
        events = self._feed(TEXT_PHASE)
        assert any(e.kind == EventKind.PHASE_CHANGE for e in events)

    def test_ticket_detection(self):
        events = self._feed(TEXT_PHASE)
        assert any(e.kind == EventKind.TICKET_CHANGE for e in events)
        ticket = next(e for e in events if e.kind == EventKind.TICKET_CHANGE)
        assert ticket.ticket_id == "ANC-42"

    def test_empty_line(self):
        assert self.parser.feed("") == []

    def test_invalid_json(self):
        assert self.parser.feed("not json") == []

    def test_unknown_type(self):
        events = self.parser.feed('{"type":"unknown_future_event","data":"x"}')
        assert events == []

    def test_all_events_no_crash(self):
        for obj in ALL_EVENTS:
            self.parser.feed(json.dumps(obj))  # must not raise


# --- TUI tests (uses Textual's test pilot) ---

@pytest.mark.asyncio
async def test_app_starts():
    """App composes and mounts without error."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#status", StatusBar) is not None
        assert app.query_one("#feed", FeedLog) is not None


@pytest.mark.asyncio
async def test_ingest_all_events_no_crash():
    """All real event types can be ingested without crashing the TUI."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for obj in ALL_EVENTS:
            app.ingest_line(json.dumps(obj))
        await pilot.pause(0.1)  # let event loop process


@pytest.mark.asyncio
async def test_status_bar_updates():
    """StatusBar reflects phase and ticket changes."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.ingest_line(json.dumps(TEXT_PHASE))
        await pilot.pause(0.1)
        status = app.query_one("#status", StatusBar)
        assert status._ticket == "ANC-42"


@pytest.mark.asyncio
async def test_quit_binding():
    """Q key exits cleanly."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("q")


@pytest.mark.asyncio
async def test_pause_binding():
    """P key toggles pause without crashing."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("p")
        assert app._paused is True
        await pilot.press("p")
        assert app._paused is False


@pytest.mark.asyncio
async def test_clear_binding():
    """C key clears feed without crashing."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.ingest_line(json.dumps(TEXT))
        await pilot.pause(0.1)
        await pilot.press("c")


@pytest.mark.asyncio
async def test_unicode_no_crash():
    """Unicode and special chars in tool output don't crash the feed."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.ingest_line(json.dumps(TEXT_UNICODE))
        await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_long_text_no_crash():
    """Long multi-block text doesn't crash."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.ingest_line(json.dumps(TEXT_LONG))
        await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_rapid_ingestion_no_crash():
    """Rapid ingestion of many lines doesn't crash."""
    app = MonitorApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(50):
            for obj in ALL_EVENTS:
                app.ingest_line(json.dumps(obj))
        await pilot.pause(0.2)
