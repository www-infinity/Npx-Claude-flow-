import requests
import subprocess
import os
import time

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "www-infinity")


def ask_claude(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if not r.ok:
        print("  API ERR:", r.text[:200])
        return None
    return r.json()["content"][0]["text"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip()


repos = run(f"gh repo list {GITHUB_ORG} --limit 50 --json name -q '.[].name'").splitlines()
print(f"Found {len(repos)} repos")
for i, repo in enumerate(repos, 1):
    print(f"[{i}] {repo}")
    try:
        result = ask_claude(
            f"Analyze GitHub repo {GITHUB_ORG}/{repo} — what type of app is it and what's likely broken? One sentence."
        )
        if result:
            print(" ", result[:150])
    except Exception as e:
        print("  ERR:", e)
    time.sleep(1)
