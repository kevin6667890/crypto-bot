"""Build the README screenshots, looping overview GIF, and product demo video."""

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
DEMO_DIR = ROOT / "artifacts" / "portfolio-demo"
API_URL = "http://127.0.0.1:8765/api/health"
UI_URL = "http://127.0.0.1:4173/"


def ensure_runtime_path() -> None:
    runtime_parent = (ROOT / ".runtime").resolve()
    if RUNTIME.parent != runtime_parent or ROOT not in RUNTIME.parents:
        raise RuntimeError(f"Refusing to clean unexpected runtime path: {RUNTIME}")
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    DEMO_DIR.mkdir(parents=True, exist_ok=True)


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
    playwright = FRONTEND / "node_modules" / "playwright"
    if not playwright.exists():
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is required to install the portfolio capture dependencies")
        print("Installing frontend dependencies with npm ci …", flush=True)
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)


def ensure_pillow() -> None:
    try:
        importlib.import_module("PIL")
        return
    except ModuleNotFoundError:
        tool_dir = ROOT / ".runtime" / "portfolio-python"
        print("Installing the declared project-local Pillow helper ...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(tool_dir), "Pillow>=11.0,<13"],
            check=True,
        )
        sys.path.insert(0, str(tool_dir))


def ffmpeg_executable() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        module = importlib.import_module("imageio_ffmpeg")
    except ModuleNotFoundError:
        tool_dir = ROOT / ".runtime" / "portfolio-python"
        print("Installing the declared project-local imageio-ffmpeg helper …", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(tool_dir), "imageio-ffmpeg==0.6.0"],
            check=True,
        )
        sys.path.insert(0, str(tool_dir))
        module = importlib.import_module("imageio_ffmpeg")
    return str(module.get_ffmpeg_exe())


def run_ffmpeg(executable: str, arguments: list[str]) -> None:
    subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", *arguments], check=True)


def convert_screenshots() -> list[Path]:
    from PIL import Image

    outputs: list[Path] = []
    for name in ("workspace", "market", "research", "decision-trace"):
        source = RUNTIME / f"{name}.png"
        target = ASSETS / f"{name}.webp"
        with Image.open(source) as image:
            if image.size != (1440, 900):
                raise RuntimeError(f"Unexpected screenshot size for {name}: {image.size}")
            image.save(target, "WEBP", quality=86, method=6)
        outputs.append(target)
    return outputs


def media_probe(executable: str, path: Path) -> dict[str, object]:
    result = subprocess.run(
        [executable, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = result.stderr
    import re
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    video = re.search(r"Video: ([^,]+).*?, (\d{2,5})x(\d{2,5})", text)
    if not duration or not video:
        raise RuntimeError(f"Could not inspect media file: {path}")
    seconds = int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "duration_seconds": round(seconds, 2),
        "codec": video.group(1).strip(),
        "width": int(video.group(2)),
        "height": int(video.group(3)),
        "size_bytes": path.stat().st_size,
    }


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
                "size_bytes": path.stat().st_size,
            })
    return details


def main() -> int:
    if not (ROOT / "data_cache" / "paper_trades.db").exists():
        raise RuntimeError("A real persisted data_cache/paper_trades.db is required; no demo returns are synthesized.")
    ensure_runtime_path()
    ensure_pillow()
    ensure_node_dependencies()
    ffmpeg = ffmpeg_executable()
    started: list[subprocess.Popen[bytes]] = []
    try:
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

        demo = DEMO_DIR / "crypto-bot-demo.mp4"
        run_ffmpeg(ffmpeg, [
            "-ss", "2.7", "-i", str(RUNTIME / "demo.webm"), "-t", "41.5",
            "-vf", "scale=1280:720:flags=lanczos,fps=30",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", str(demo),
        ])

        gif = ASSETS / "crypto-bot-overview.gif"
        gif_story = (
            "[0:v]trim=start=4:end=7,setpts=PTS-STARTPTS[w];"
            "[0:v]trim=start=10:end=13,setpts=PTS-STARTPTS[m];"
            "[0:v]trim=start=17:end=20,setpts=PTS-STARTPTS[r];"
            "[0:v]trim=start=26:end=29,setpts=PTS-STARTPTS[d];"
            "[w][m][r][d]concat=n=4:v=1:a=0,fps=9,scale=1120:-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=max_colors=128:stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        run_ffmpeg(ffmpeg, [
            "-i", str(demo), "-filter_complex", gif_story, "-loop", "0", str(gif),
        ])

        manifest = {
            "screenshots": verify_images(screenshots),
            "gif": media_probe(ffmpeg, gif),
            "demo": media_probe(ffmpeg, demo),
        }
        (RUNTIME / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2), flush=True)
        return 0
    finally:
        for process in reversed(started):
            stop_process(process)
        if RUNTIME.exists():
            shutil.rmtree(RUNTIME)


if __name__ == "__main__":
    raise SystemExit(main())
