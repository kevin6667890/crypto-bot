import hashlib
import json
from pathlib import Path

from scripts import download_okx_official_oi_history as subject


class Response:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


def test_download_pages_by_end_and_preserves_raw_response(
    tmp_path: Path, monkeypatch,
) -> None:
    payloads = [
        {"code": "0", "data": [["600000", "1", "2", "3"]] * 100},
        {"code": "0", "data": [["300000", "4", "5", "6"]]},
    ]
    calls: list[str] = []

    def open_url(request, timeout: int):
        calls.append(request.full_url)
        return Response(payloads.pop(0))

    monkeypatch.setattr(subject.urllib.request, "urlopen", open_url)
    monkeypatch.setattr(subject.time, "sleep", lambda _seconds: None)
    result = subject.download("BTC-USDT-SWAP", tmp_path)
    assert result["row_count"] == 2
    assert result["page_count"] == 2
    assert "end=599999" in calls[1]
    first = tmp_path / "BTC-USDT-SWAP-page-001.json"
    assert result["pages"][0]["sha256"] == hashlib.sha256(
        first.read_bytes()
    ).hexdigest()
