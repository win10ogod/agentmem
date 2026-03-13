# 優化項目（agentmem）

本文件列出已完成與建議中的優化方向，方便你評估下一步要衝哪個瓶頸。

## 已完成

- **BM25 搜尋加速（DocStats cache）**
  - 新增 `cache/ltm_search.ndjson`（entry + dl + tf），避免每次 recall 都重新 tokenize 全量 LTM
  - CLI 的 `recall` 在 `--as-of` 未使用時，會優先走 docstats 路徑
- **E2E subprocess 測試**
  - `tests/test_e2e_subprocess.py` 直接用 `python -m agentmem` 跑完整指令流

## 建議下一步（依優先度）

1. **CLI 啟動時間**
   - 把 `cli.py` 的部分 imports 改成 lazy import（`--version` 不需要載入 store/search）
2. **索引（更大規模 LTM）**
   - 目前 docstats cache 仍是 O(N * |query|) 掃描；若要 100k+ 條目更快，可考慮純文本 inverted index（仍可寫成 NDJSON）
3. **批次寫入**
   - 為高頻寫入場景加上 `agentmem add --batch`（stdin NDJSON）以降低 lock/IO 次數
4. **GC / 壓縮**
   - 事件日誌長期膨脹後，可提供 `compact`（產出新 events 或歸檔舊 events；保持可追溯）
5. **安全策略（可選）**
   - 增加 `--redact`/`--pii-scan`（純本機規則）避免誤存敏感資料

