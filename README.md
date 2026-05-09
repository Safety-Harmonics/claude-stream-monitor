# claude-stream-monitor

TUI monitor for Claude Code `--output-format stream-json` output. Displays tool calls, Claude responses, phase tracking, cost, and token usage in a live terminal interface — styled to resemble Claude Code's own TUI.

Raw JSONL logs are written to disk automatically for post-hoc analysis.

## Usage

**Live monitoring via Docker:**
```bash
docker logs -f <container> | claude-monitor
```

**Direct from claude CLI:**
```bash
claude --output-format stream-json --verbose -p "your prompt" | claude-monitor
```

**Replay a saved log:**
```bash
cat logs/agent-20260509.jsonl | claude-monitor --replay
```

## Installation

```bash
# With uv (recommended)
uv tool install claude-stream-monitor

# Or in a virtual env
pip install claude-stream-monitor
```

## Key Bindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `a` | Toggle auto-scroll |
| `c` | Clear feed |
| `p` | Pause display (log continues writing) |

## Options

```
--log-dir PATH    Directory for JSONL logs (default: ./logs)
--log-file PATH   Explicit log file path
--replay          Replay mode — read stdin at full speed
--no-log          Disable log file writing
```

## Log format

Raw JSONL logs are written to `logs/agent-YYYYMMDD-HHMMSS.jsonl` by default — one Claude Code stream-json event per line. These can be replayed, grepped, or fed into analysis tools.

```bash
# Find all tool calls in a session
cat logs/agent-20260509.jsonl | jq 'select(.type == "assistant") | .message.content[] | select(.type == "tool_use") | {name, input}'

# Total cost across sessions
cat logs/*.jsonl | jq 'select(.type == "result") | .total_cost_usd' | awk '{s+=$1} END {print s}'
```
