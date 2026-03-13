# 測試指南（agentmem）

本專案包含：靜態檢查（ruff/mypy）、單元測試（pytest）、以及以 `python -m agentmem` 為入口的 E2E subprocess 測試。

## 1) 開發依賴

```bash
python -m pip install -e ".[dev]"
```

## 2) 靜態檢查

```bash
ruff check .
mypy
```

## 3) 自動化測試

```bash
pytest
```

其中 `tests/test_e2e_subprocess.py` 會真的用 subprocess 跑：

- `python -m agentmem init/add/recall/list`
- `python -m agentmem patch validate/apply`
- `python -m agentmem show/compact/batch`

另外 `tests/test_daemon_e2e.py` 會真的啟動 `agentmem serve`，並用 socket 發送 `ping/add/recall/shutdown`。

## 4) 手動 Smoke Test（建議）

```bash
export AGENTMEM_HOME="$(mktemp -d)"
agentmem init
mid="$(agentmem add --kind fact --tags demo --text 'hello smoke')"
agentmem recall hello --format json --limit 3
agentmem update "$mid" --tags "demo,updated" --reason "tag tweak"
agentmem list --format json
```

## 5) 簡易效能測試（可選）

以下用「大量 LTM + recall」來觀察瓶頸（主要會落在檔案解析與 BM25 計分）：

```bash
python - <<'PY'
import json, time
from pathlib import Path
from tempfile import TemporaryDirectory
from agentmem.store import AgentMemStore
from agentmem.search import bm25_search_docs
from agentmem.utils import utc_now_iso

with TemporaryDirectory() as td:
    home = Path(td)
    store = AgentMemStore(home)
    store.init_layout()
    n = 50000
    with store.paths.ltm_events.open("w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({
                "op":"add","ts":utc_now_iso(),"id":f"{i:08x}",
                "kind":"note","tags":["bench"],"importance":5,
                "text":"純文本 記憶 agent cli" if i % 10 == 0 else "alpha beta gamma",
                "source":None,"expires_at":None,"session":None
            }, ensure_ascii=False, separators=(",",":")) + "\\n")

    docs = store.load_ltm_docstats()
    t0 = time.perf_counter()
    bm25_search_docs(docs, "純文本 記憶", limit=5)
    t1 = time.perf_counter()
    print("search_ms", round((t1-t0)*1000, 1))
PY
```
