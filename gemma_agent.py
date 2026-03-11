"""
gemma_agent.py — Gemma 3 autonomous system-architect agent.

Features
--------
* Function calling: create_repo, inject_file, deploy_site, analyze_code, browse_url
* Structured JSON output (tool calls + final answers)
* Multi-turn conversation loop
* Backends: Ollama (local Gemma 3) or Google AI Studio (gemma-3-* via generateContent)

Usage
-----
    # Local via Ollama (requires `ollama pull gemma3:latest`)
    python gemma_agent.py

    # Google AI Studio
    export GOOGLE_API_KEY="your-key"
    python gemma_agent.py --backend google

    # Non-interactive single prompt
    python gemma_agent.py --prompt "Create a repo called my-site with an index.html"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import textwrap
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_REASONING_ITERATIONS = 6

# Allowed URL schemes for browse_url to prevent SSRF
_ALLOWED_SCHEMES = {"https", "http"}
# Block private/internal address ranges from browse_url
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1$)",
    re.IGNORECASE,
)

# ── Tool definitions (mirrors schemas/gemma_tools.json) ──────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_repo",
        "description": (
            "Creates a new GitHub repository and optionally injects seed files "
            "in one atomic operation."
        ),
        "parameters": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "private": {"type": "boolean", "default": False},
                "auto_init": {"type": "boolean", "default": True},
                "license": {
                    "type": "string",
                    "enum": ["mit", "apache-2.0", "gpl-3.0", "unlicense", "none"],
                    "default": "mit",
                },
                "topics": {"type": "array", "items": {"type": "string"}},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path", "content"],
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "inject_file",
        "description": (
            "Creates or overwrites a file in an existing GitHub repository "
            "via the Contents API."
        ),
        "parameters": {
            "type": "object",
            "required": ["repo", "path", "content"],
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "message": {"type": "string", "default": "chore: inject file via Gemma agent"},
                "branch": {"type": "string", "default": "main"},
                "sha": {"type": "string"},
            },
        },
    },
    {
        "name": "deploy_site",
        "description": "Enables GitHub Pages or triggers a deployment workflow for a repository.",
        "parameters": {
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string"},
                "source_branch": {"type": "string", "default": "main"},
                "source_path": {"type": "string", "enum": ["/", "/docs"], "default": "/"},
                "workflow": {"type": "string"},
            },
        },
    },
    {
        "name": "analyze_code",
        "description": (
            "Reads and analyses files in a GitHub repository, returning a structured summary."
        ),
        "parameters": {
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "focus": {
                    "type": "string",
                    "enum": ["bugs", "security", "performance", "style", "summary"],
                    "default": "summary",
                },
            },
        },
    },
    {
        "name": "browse_url",
        "description": (
            "Fetches and parses a URL, returning LLM-friendly content for further reasoning."
        ),
        "parameters": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "extract": {
                    "type": "string",
                    "enum": ["full", "main_content", "links", "metadata"],
                    "default": "main_content",
                },
                "max_length": {"type": "integer", "default": 4096},
            },
        },
    },
]

# ── Tool implementations ──────────────────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "www-infinity")


def _github_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Make an authenticated GitHub API request."""
    if dry_run:
        return {"dry_run": True, "method": method, "path": path, "body": body}
    if not GITHUB_TOKEN:
        return {"error": "GITHUB_TOKEN not set — running in dry-run mode", "method": method, "path": path}
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": exc.reason, "status": exc.code, "body": exc.read().decode()[:500]}
    except urllib.error.URLError as exc:
        return {"error": str(exc.reason)}


def tool_create_repo(params: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    name = params["name"]
    body: dict[str, Any] = {
        "name": name,
        "description": params.get("description", ""),
        "private": params.get("private", False),
        "auto_init": params.get("auto_init", True),
        "license_template": params.get("license", "mit"),
    }
    org = GITHUB_ORG
    result = _github_request("POST", f"/orgs/{org}/repos", body, dry_run=dry_run)

    # Inject seed files
    files: list[dict[str, Any]] = params.get("files", [])
    injected: list[str] = []
    for f in files:
        inject_result = tool_inject_file(
            {
                "repo": f"{org}/{name}",
                "path": f["path"],
                "content": f["content"],
                "message": f"feat: seed {f['path']}",
            },
            dry_run=dry_run,
        )
        injected.append(f["path"])
        if "error" in inject_result and not dry_run:
            result.setdefault("inject_errors", []).append(inject_result)

    if injected:
        result["injected_files"] = injected

    # Apply topics
    topics: list[str] = params.get("topics", [])
    if topics:
        _github_request(
            "PUT",
            f"/repos/{org}/{name}/topics",
            {"names": topics},
            dry_run=dry_run,
        )
        result["topics"] = topics

    return result


def tool_inject_file(params: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    repo = params["repo"]
    path = params["path"]
    content_b64 = base64.b64encode(params["content"].encode()).decode()
    body: dict[str, Any] = {
        "message": params.get("message", "chore: inject file via Gemma agent"),
        "content": content_b64,
        "branch": params.get("branch", "main"),
    }
    if "sha" in params:
        body["sha"] = params["sha"]
    return _github_request("PUT", f"/repos/{repo}/contents/{path}", body, dry_run=dry_run)


def tool_deploy_site(params: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    repo = params["repo"]
    if "workflow" in params:
        return _github_request(
            "POST",
            f"/repos/{repo}/actions/workflows/{params['workflow']}/dispatches",
            {"ref": params.get("source_branch", "main")},
            dry_run=dry_run,
        )
    body: dict[str, Any] = {
        "source": {
            "branch": params.get("source_branch", "main"),
            "path": params.get("source_path", "/"),
        }
    }
    return _github_request("POST", f"/repos/{repo}/pages", body, dry_run=dry_run)


def tool_analyze_code(params: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    repo = params["repo"]
    focus = params.get("focus", "summary")
    paths = params.get("paths", [])

    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "focus": focus,
            "message": "Would fetch repo tree and file contents for analysis",
        }

    # Fetch repo tree
    tree_resp = _github_request("GET", f"/repos/{repo}/git/trees/HEAD?recursive=1")
    if "error" in tree_resp:
        return tree_resp

    tree = tree_resp.get("tree", [])
    blob_paths = [
        item["path"]
        for item in tree
        if item.get("type") == "blob"
        and not item["path"].startswith(".")
        and (not paths or item["path"] in paths)
    ][:10]  # cap at 10 files

    snippets: dict[str, str] = {}
    for fp in blob_paths:
        fc = _github_request("GET", f"/repos/{repo}/contents/{fp}")
        if "content" in fc:
            try:
                decoded = base64.b64decode(fc["content"]).decode(errors="replace")
                snippets[fp] = decoded[:2000]
            except Exception:
                pass

    return {
        "repo": repo,
        "focus": focus,
        "files_sampled": list(snippets.keys()),
        "content": snippets,
    }


def tool_browse_url(params: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    url = params["url"]
    max_length = params.get("max_length", 4096)
    extract = params.get("extract", "main_content")

    if dry_run:
        return {"dry_run": True, "url": url, "extract": extract}

    # Basic SSRF guard: only allow http/https and block private hosts
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return {"error": f"Scheme '{parsed.scheme}' is not allowed", "url": url}
        host = parsed.hostname or ""
        if _BLOCKED_HOSTS.match(host):
            return {"error": "Requests to private/internal addresses are not allowed", "url": url}
    except Exception as exc:
        return {"error": f"Invalid URL: {exc}", "url": url}

    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GemmaAgent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read(max_length * 4).decode(errors="replace")
    except Exception as exc:
        return {"error": str(exc), "url": url}

    # Minimal HTML → text strip
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return {"url": url, "extract": extract, "content": text[:max_length]}


TOOL_REGISTRY = {
    "create_repo": tool_create_repo,
    "inject_file": tool_inject_file,
    "deploy_site": tool_deploy_site,
    "analyze_code": tool_analyze_code,
    "browse_url": tool_browse_url,
}

# ── Backend: Ollama ───────────────────────────────────────────────────────────

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma3:latest")


def _ollama_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Call Ollama /api/chat with tool definitions."""
    payload = {
        "model": GEMMA_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Ollama typically runs over HTTP on localhost; use default context for HTTPS if configured
    ctx = ssl.create_default_context() if OLLAMA_BASE.startswith("https") else None
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return json.loads(resp.read().decode())


# ── Backend: Google AI Studio ─────────────────────────────────────────────────

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_MODEL = os.environ.get("GOOGLE_GEMMA_MODEL", "gemma-3-27b-it")


def _to_google_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "functionDeclarations": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            }
        ]
    }


def _google_generate(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call Google Generative Language API (generateContent)."""
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    google_tools = [_to_google_tool(t) for t in tools]
    payload = {"contents": contents, "tools": google_tools}
    data = json.dumps(payload).encode()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GOOGLE_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    )
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return json.loads(resp.read().decode())


# ── Agentic reasoning loop ────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent(
    """
    You are Gemma — an autonomous system architect for the Pewpi-Infinity ecosystem.
    You can reason through complex tasks step-by-step, call tools to interact with
    GitHub (creating repos, injecting files, deploying sites), browse URLs, and
    analyse code.

    When you need to perform an action, emit a JSON tool_call block, then wait
    for the result before continuing. Always explain your reasoning before calling
    a tool. Provide a concise, structured final answer.
    """
).strip()


class GemmaAgent:
    def __init__(self, backend: str = "ollama", dry_run: bool = False, verbose: bool = True):
        self.backend = backend
        self.dry_run = dry_run
        self.verbose = verbose
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def _log(self, *args: Any) -> None:
        if self.verbose:
            print(*args, flush=True)

    def _call_model(self) -> dict[str, Any]:
        messages = [m for m in self.history if m["role"] != "system"]
        if self.backend == "google":
            return _google_generate(messages, TOOLS)
        return _ollama_chat(self.history, TOOLS)

    def _extract_tool_calls(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalise tool calls from either backend into a list of {name, parameters}."""
        calls: list[dict[str, Any]] = []

        # Ollama format
        msg = response.get("message", {})
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            calls.append({"name": fn.get("name"), "parameters": fn.get("arguments", {})})

        # Google format
        for candidate in response.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "functionCall" in part:
                    fc = part["functionCall"]
                    calls.append({"name": fc.get("name"), "parameters": fc.get("args", {})})

        # Fallback: parse JSON from text
        if not calls:
            text = self._extract_text(response)
            for m in re.finditer(r"```json\s*(\{[\s\S]*?\})\s*```", text):
                try:
                    obj = json.loads(m.group(1))
                    if "tool_call" in obj or "name" in obj:
                        entry = obj.get("tool_call", obj)
                        if "name" in entry:
                            calls.append(entry)
                except json.JSONDecodeError:
                    pass

        return calls

    def _extract_text(self, response: dict[str, Any]) -> str:
        # Ollama
        if "message" in response:
            return response["message"].get("content", "")
        # Google
        for candidate in response.get("candidates", []):
            parts = candidate.get("content", {}).get("parts", [])
            texts = [p.get("text", "") for p in parts if "text" in p]
            if texts:
                return "\n".join(texts)
        return ""

    def _execute_tool(self, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        fn = TOOL_REGISTRY.get(name)
        if fn is None:
            return {"error": f"Unknown tool: {name}"}
        self._log(f"\n  ⚙️  Calling tool: {name}")
        self._log(f"     Parameters: {json.dumps(parameters, indent=2)}")
        result = fn(parameters, dry_run=self.dry_run)
        self._log(f"  ✅ Tool result: {json.dumps(result, indent=2)[:400]}")
        return result

    def step(self, user_message: str) -> str:
        """Add user message, run agentic loop until final answer, return response text."""
        self.history.append({"role": "user", "content": user_message})
        self._log(f"\n🧠 Reasoning about: {user_message!r}\n")

        max_iterations = MAX_REASONING_ITERATIONS
        for iteration in range(max_iterations):
            self._log(f"  🔄 Iteration {iteration + 1}")
            try:
                response = self._call_model()
            except Exception as exc:
                error_msg = f"Model call failed: {exc}"
                self._log(f"  ❌ {error_msg}")
                self.history.append({"role": "assistant", "content": error_msg})
                return error_msg

            text = self._extract_text(response)
            tool_calls = self._extract_tool_calls(response)

            if text:
                self._log(f"  💬 Model: {text[:200]}{'…' if len(text) > 200 else ''}")

            if not tool_calls:
                # Final answer
                self.history.append({"role": "assistant", "content": text})
                return text

            # Execute each tool call and feed results back
            self.history.append({"role": "assistant", "content": text or "(tool call)"})
            for tc in tool_calls:
                result = self._execute_tool(tc["name"], tc.get("parameters", {}))
                result_text = json.dumps(
                    {"tool": tc["name"], "result": result}, indent=2
                )
                self.history.append({"role": "user", "content": f"Tool result:\n```json\n{result_text}\n```"})

        fallback = "I completed the tool calls. Please review the results above."
        self.history.append({"role": "assistant", "content": fallback})
        return fallback

    def chat(self) -> None:
        """Interactive REPL."""
        print("Gemma Agent — type your request, 'history' to see conversation, or 'quit' to exit.\n")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break
            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "q"}:
                break
            if user_input.lower() == "history":
                print(json.dumps(self.history, indent=2))
                continue
            answer = self.step(user_input)
            print(f"\nGemma: {answer}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Gemma 3 autonomous agent")
    parser.add_argument(
        "--backend",
        choices=["ollama", "google"],
        default="ollama",
        help="Model backend: 'ollama' (local) or 'google' (AI Studio, requires GOOGLE_API_KEY)",
    )
    parser.add_argument("--prompt", default="", help="Single prompt (non-interactive)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate tool execution without making real GitHub API calls",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress reasoning trace")
    args = parser.parse_args()

    agent = GemmaAgent(
        backend=args.backend,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    if args.prompt:
        answer = agent.step(args.prompt)
        print(answer)
    else:
        agent.chat()


if __name__ == "__main__":
    main()
