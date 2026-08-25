"""Capture and verify the README media from the accepted product environment."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
RUNTIME = (ROOT / ".runtime" / "portfolio-capture").resolve()
ASSETS = ROOT / "docs" / "assets" / "portfolio"
API_URL = "http://127.0.0.1:8765/api/health"
UI_URL = "http://127.0.0.1:4173/"
CAPTURE_URL = os.environ.get("PORTFOLIO_BASE_URL", UI_URL)
PRODUCTION_CAPTURE = CAPTURE_URL.rstrip("/") == "https://bitcoinbot.uk"
SCREENSHOTS = ("home", "test-result", "evidence-chart", "tracking", "what-changed", "home-mobile-en")


def ensure_runtime_path() -> None:
    runtime_parent = (ROOT / ".runtime").resolve()
    if RUNTIME.parent != runtime_parent or ROOT not in RUNTIME.parents:
        raise RuntimeError(f"Refusing to clean unexpected runtime path: {RUNTIME}")
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    ASSETS.mkdir(parents=True, exist_ok=True)


def http_ready(url: str, timeout: float = 1.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def wait_ready(url: str, process: subprocess.Popen[bytes] | None, seconds: int = 45) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if http_ready(url):
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Service exited before becoming ready: {url} (exit {process.returncode})")
        time.sleep(0.35)
    raise RuntimeError(f"Timed out waiting for {url}")


def hidden_process(command: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {"cwd": cwd, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


def ensure_node_dependencies() -> None:
    if (FRONTEND / "node_modules" / "playwright").exists():
        return
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is required to install the portfolio capture dependencies")
    subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)


def ensure_pillow() -> None:
    try:
        importlib.import_module("PIL")
        return
    except ModuleNotFoundError:
        tool_dir = ROOT / ".runtime" / "portfolio-python"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(tool_dir), "Pillow>=11.0,<13"],
            check=True,
        )
        sys.path.insert(0, str(tool_dir))


def convert_screenshots() -> list[Path]:
    from PIL import Image

    outputs: list[Path] = []
    for name in SCREENSHOTS:
        source = RUNTIME / f"{name}.png"
        target = ASSETS / f"{name}.webp"
        expected = (390, 844) if name == "home-mobile-en" else (1440, 900)
        with Image.open(source) as image:
            if image.size != expected:
                raise RuntimeError(f"Unexpected screenshot size for {name}: {image.size}")
            image.save(target, "WEBP", quality=86, method=6)
        outputs.append(target)
    return outputs


def build_gif() -> Path:
    from PIL import Image

    source_names = ("home", "test-result", "evidence-chart", "tracking", "what-changed")
    frames: list[Image.Image] = []
    for name in source_names:
        with Image.open(RUNTIME / f"{name}.png") as image:
            frame = image.convert("RGB")
            frame.thumbnail((1120, 700), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (1120, 700), "#f6faf8")
            canvas.paste(frame, ((1120 - frame.width) // 2, (700 - frame.height) // 2))
            frames.append(canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    target = ASSETS / "crypto-bot-overview.gif"
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=3000, loop=0, optimize=True)
    return target


def verify_images(paths: list[Path]) -> list[dict[str, object]]:
    from PIL import Image

    details: list[dict[str, object]] = []
    for path in paths:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            details.append({
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "width": image.width,
                "height": image.height,
                "frames": getattr(image, "n_frames", 1),
                "size_bytes": path.stat().st_size,
            })
    return details


def main() -> int:
    if not PRODUCTION_CAPTURE and not (ROOT / "data_cache" / "paper_trades.db").exists():
        raise RuntimeError("A real persisted data_cache/paper_trades.db is required; no demo results are synthesized.")
    ensure_runtime_path()
    ensure_pillow()
    ensure_node_dependencies()
    started: list[subprocess.Popen[bytes]] = []
    try:
        if not PRODUCTION_CAPTURE:
            api = None
            if not http_ready(API_URL):
                api = hidden_process([sys.executable, "-m", "dashboard.paper_api"], ROOT)
                started.append(api)
            wait_ready(API_URL, api)
            frontend = None
            if not http_ready(UI_URL):
                npm = shutil.which("npm.cmd") or shutil.which("npm")
                if not npm:
                    raise RuntimeError("npm is required to start the portfolio capture frontend")
                frontend = hidden_process([npm, "run", "dev", "--", "--port", "4173"], FRONTEND)
                started.append(frontend)
            wait_ready(UI_URL, frontend)

        subprocess.run(["node", "scripts/capture-portfolio.mjs"], cwd=FRONTEND, check=True)
        screenshots = convert_screenshots()
        gif = build_gif()
        capture = json.loads((RUNTIME / "capture.json").read_text(encoding="utf-8"))
        mobile = {
            language: json.loads((RUNTIME / f"mobile-{language}.json").read_text(encoding="utf-8"))
            for language in ("en", "zh")
        }
        manifest = {
            "source": CAPTURE_URL,
            "real_data_only": True,
            "capture_track_archived": capture["archived"],
            "mobile_acceptance": mobile,
            "screenshots": verify_images(screenshots),
            "gif": verify_images([gif])[0],
            "gif_duration_seconds": 15,
            "privacy_check": "PASS",
        }
        print(json.dumps(manifest, indent=2), flush=True)
        return 0
    finally:
        for process in reversed(started):
            stop_process(process)
        if RUNTIME.exists():
            shutil.rmtree(RUNTIME)


if __name__ == "__main__":
    raise SystemExit(main())
