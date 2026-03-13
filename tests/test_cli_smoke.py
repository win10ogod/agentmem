from __future__ import annotations

import json
from pathlib import Path

from agentmem.cli import main


def test_cli_init_add_recall(tmp_path: Path, capsys: object) -> None:
    home = tmp_path / "mem"
    assert main(["--home", str(home), "init"]) == 0
    capsys.readouterr()

    assert main(
        [
            "--home",
            str(home),
            "add",
            "--kind",
            "fact",
            "--tags",
            "a,b",
            "--text",
            "純文本記憶",
        ]
    ) == 0
    mid = capsys.readouterr().out.strip().splitlines()[-1]
    assert mid

    assert main(["--home", str(home), "update", mid, "--tags", "a,b,c"]) == 0

    assert main(["--home", str(home), "recall", "純文本", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "純文本記憶" in out

    assert main(["--home", str(home), "list", "--format", "json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert entries[0]["id"] == mid
    assert "c" in entries[0]["tags"]
