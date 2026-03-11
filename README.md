# ∞ Infinity — Claude Flow

AI-powered multi-agent workflow platform built on [Anthropic Claude](https://anthropic.com) and [Gemma 3](https://developers.googleblog.com/introducing-gemma3/).

## Quickstart

```bash
# 1. Bootstrap a mesh hive with 5 agents
npx claude-flow@alpha hive init --topology mesh --agents 5

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. Run the worker script
python claude_worker.py
```

## `claude_worker.py`

The bundled worker script (`claude_worker.py`) lists every repository in a
GitHub organisation and asks Claude to analyse each one — identifying the type
of application and surfacing anything that might be broken.

The target GitHub organisation defaults to `www-infinity` and can be overridden
with the `GITHUB_ORG` environment variable:

```bash
export GITHUB_ORG="your-org"
python claude_worker.py
```

**Requirements:**

```
pip install requests
gh auth login   # GitHub CLI — https://cli.github.com
```

## `gemma_agent.py` — Gemma 3 Autonomous Agent

A fully autonomous system-architect agent powered by Gemma 3 with **function calling**, **structured JSON output**, and an **agentic reasoning loop**.

### Features

| Capability | Description |
|---|---|
| **Function calling** | `create_repo`, `inject_file`, `deploy_site`, `analyze_code`, `browse_url` |
| **Multi-turn reasoning** | Iterative tool-call → result → reasoning loop |
| **Structured JSON output** | Every tool call is a validated JSON object |
| **Two backends** | Ollama (local Gemma 3) or Google AI Studio |

### Usage

```bash
# Local via Ollama (requires `ollama pull gemma3:latest`)
python gemma_agent.py

# Google AI Studio
export GOOGLE_API_KEY="your-key"
python gemma_agent.py --backend google

# Single prompt (non-interactive)
python gemma_agent.py --prompt "Create a repo called my-site with an index.html"

# Dry run — no real GitHub API calls
python gemma_agent.py --dry-run --prompt "Build a landing page for Pewpi"
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub PAT (required for real API calls) |
| `GITHUB_ORG` | `www-infinity` | Target organisation |
| `GOOGLE_API_KEY` | — | Required for `--backend google` |
| `GOOGLE_GEMMA_MODEL` | `gemma-3-27b-it` | Google model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `GEMMA_MODEL` | `gemma3:latest` | Ollama model tag |

## Tool Schemas

The `schemas/gemma_tools.json` file contains the canonical JSON Schema
definitions for all five tool calls: `create_repo`, `inject_file`,
`deploy_site`, `analyze_code`, `browse_url` — including worked examples.

## Website

A static site lives in `website/`. It includes a **full AI chat interface**
(`chat.html`) with live reasoning trace, tool-call JSON visualisation, and
demo mode (no API key required).

```bash
npx serve website
# then open http://localhost:3000/chat.html
```

## License

MIT