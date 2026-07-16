"""Streamlit wrapper for the React trading workspace.

The Streamlit Cloud deployment serves the prebuilt Vite bundle from
frontend/dist. Rebuild it with `cd frontend && npm run build` before deploy.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import threading
import time

import streamlit as st
import streamlit.components.v1 as components

from paper_api import PaperService


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"


st.set_page_config(
    page_title="Crypto-Bot Quant Trading Workspace",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Streamlit Community Cloud stores keys in ``st.secrets`` rather than a local
# .env file. PaperService reads the same environment variable in both cases.
if "DEEPSEEK_API_KEY" in st.secrets:
    os.environ.setdefault("DEEPSEEK_API_KEY", st.secrets["DEEPSEEK_API_KEY"])

st.markdown(
    """
    <style>
    .stApp { background: #f7f8fa; overflow: auto; }
    .block-container { padding: 0 !important; max-width: none !important; margin: 0 !important; }
    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    footer,
    #MainMenu { display: none !important; }
    iframe { display: block; border: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def start_paper_service() -> PaperService:
    """Keep paper trading on the Streamlit server, not in the visitor's browser."""
    service = PaperService()

    def loop() -> None:
        while True:
            service.cycle()
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="paper-trading-loop").start()
    return service


def load_react_bundle(paper_status: dict) -> str:
    index = DIST / "index.html"
    if not index.exists():
        return """
        <div style="font-family:Inter,Arial,sans-serif;padding:32px">
          <h1>React bundle not found</h1>
          <p>Run <code>cd frontend && npm install && npm run build</code>, then redeploy.</p>
        </div>
        """

    html = index.read_text(encoding="utf-8")
    assets = DIST / "assets"

    for css_file in assets.glob("*.css"):
        css = css_file.read_text(encoding="utf-8")
        html = html.replace(
            f'<link rel="stylesheet" crossorigin href="/assets/{css_file.name}">',
            f"<style>{css}</style>",
        )

    for js_file in assets.glob("*.js"):
        js = js_file.read_text(encoding="utf-8")
        html = html.replace(
            f'<script type="module" crossorigin src="/assets/{js_file.name}"></script>',
            f"<script type=\"module\">{js}</script>",
        )

    status_json = json.dumps(paper_status).replace("</", "<\\/")
    html = html.replace("</head>", f"<script>window.__PAPER_STATUS__={status_json};</script></head>")
    return html


paper_service = start_paper_service()
components.html(load_react_bundle(paper_service.status()), height=1400, scrolling=True)
