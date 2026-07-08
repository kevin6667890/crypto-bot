"""Streamlit wrapper for the React trading workspace.

The Streamlit Cloud deployment serves the prebuilt Vite bundle from
frontend/dist. Rebuild it with `cd frontend && npm run build` before deploy.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"


st.set_page_config(
    page_title="Crypto-Bot Quant Trading Workspace",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .stApp { background: #f7f8fa; }
    .block-container { padding: 0 !important; max-width: none !important; }
    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    footer,
    #MainMenu { display: none !important; }
    iframe { display: block; }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_react_bundle() -> str:
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

    return html


components.html(load_react_bundle(), height=1250, scrolling=True)
