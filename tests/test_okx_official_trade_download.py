import json
import zipfile
from pathlib import Path

from scripts.download_okx_official_trade_history import (
    EXPECTED_COLUMNS,
    files_from_metadata,
    verify_archive,
)


def test_metadata_is_sorted_deterministically() -> None:
    metadata = {"data": [{"details": [{
        "instFamily": "BTC-USDT", "instId": "",
        "groupDetails": [
            {"filename": "b.zip", "url": "u2", "sizeMB": "2", "dateTs": "2"},
            {"filename": "a.zip", "url": "u1", "sizeMB": "1", "dateTs": "1"},
        ],
    }]}]}
    assert [row["filename"] for row in files_from_metadata(metadata)] == [
        "a.zip", "b.zip"
    ]


def test_archive_schema_and_crc_are_verified(tmp_path: Path) -> None:
    archive = tmp_path / "trades.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("trades.csv", ",".join(EXPECTED_COLUMNS) + "\n")
    result = verify_archive(archive)
    assert result["zip_crc_check"] == "PASS"
    assert tuple(result["columns"]) == EXPECTED_COLUMNS
