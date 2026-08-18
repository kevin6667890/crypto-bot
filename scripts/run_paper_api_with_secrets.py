from __future__ import annotations
import os,runpy
from pathlib import Path

def load_secret(name:str)->None:
    value_file=os.getenv(f"{name}_FILE")
    if not value_file:return
    value=Path(value_file).read_text(encoding="utf-8").strip()
    if not value:raise RuntimeError(f"{name}_FILE is empty")
    os.environ[name]=value

def main()->int:
    # The legacy DeepSeek key remains isolated from the new AI report key.
    for name in ("ADMIN_TOKEN","DEEPSEEK_API_KEY"):load_secret(name)
    runpy.run_module("dashboard.paper_api",run_name="__main__");return 0
if __name__=="__main__":raise SystemExit(main())
