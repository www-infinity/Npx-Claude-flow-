# ∞ Infinity — Claude Flow

AI-powered multi-agent workflow platform built on [Anthropic Claude](https://anthropic.com).

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

## Website

A static marketing site lives in the `website/` directory. Open
`website/index.html` in your browser or serve it with any static file server:

```bash
npx serve website
```

## License

MIT