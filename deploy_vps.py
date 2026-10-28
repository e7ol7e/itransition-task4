"""Update only task4 in shared Compose. Keep Caddy/other services unchanged."""
import re
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path("/root/itransition")
SERVICE = '  task4:\n    build:\n      context: ./task4\n      dockerfile: Dockerfile\n    image: itransition-task4:prod\n    env_file:\n      - ./task4/task4.env\n    restart: unless-stopped\n    ports:\n      - "127.0.0.1:5003:8080"\n    read_only: true\n    tmpfs:\n      - /tmp:size=16m,mode=1777\n    cap_drop:\n      - ALL\n    security_opt:\n      - no-new-privileges:true\n    healthcheck:\n      test: ["CMD", "/app/.venv/bin/python", "-c", "import urllib.request; assert urllib.request.urlopen(\'http://127.0.0.1:8080/healthz\', timeout=3).read().strip() == b\'ok\'"]\n      interval: 30s\n      timeout: 5s\n      retries: 3\n      start_period: 20s\n\n'


def run(*args):
    return subprocess.run(args, cwd=ROOT, check=True)


def main():
    path = ROOT / "compose.yaml"
    original = path.read_text()
    pattern = r"(?ms)^  task4:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)"
    if len(re.findall(pattern, original)) != 1:
        raise RuntimeError("Expected exactly one existing task4 service")
    updated = re.sub(pattern, lambda _: SERVICE, original, count=1)
    previous = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", "itransition-task4-1"], text=True).strip()
    exists = subprocess.run(["docker", "image", "inspect", previous], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        previous = subprocess.check_output(["docker", "commit", "--pause=false", "itransition-task4-1", "itransition-task4:rollback"], text=True).strip()
    else:
        run("docker", "tag", previous, "itransition-task4:rollback")
    path.write_text(updated)
    try:
        run("docker", "compose", "config", "--quiet")
        run("docker", "compose", "build", "task4")
        run("docker", "compose", "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "task4")
        for port in (5003, 5002):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                assert response.read().strip() == b"ok"
        print("Task 4 healthy through Docker and Caddy; other services unchanged.")
    except Exception:
        path.write_text(original)
        run("docker", "tag", previous, "itransition-task4:prod")
        run("docker", "compose", "up", "-d", "--no-deps", "task4")
        raise


if __name__ == "__main__":
    main()
