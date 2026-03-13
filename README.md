# agentmem

一個**純文本（pure-text）**、可打包成 **CLI 工具**的 AI agent 記憶套件（Python 3.11+），同時支援：

- **短期記憶（STM）**：以 session 為單位的對話/筆記暫存
- **長期記憶（LTM）**：可搜尋、可追溯、可遺忘（tombstone）的長期條目

## 核心理念：純文本 + 可追溯

所有資料都以 UTF-8 文本檔儲存（NDJSON / TOML），可用任何編輯器檢視、grep、diff、備份。

預設目錄（可用 `AGENTMEM_HOME` 或 `--home` 覆寫）：

```text
~/.agentmem/
  ltm.ndjson                 # LTM 事件日誌（append-only）
  stm/
    sessions/
      <session_id>.ndjson    # STM session 事件日誌（append-only）
  cache/
    ltm_state.ndjson         # LTM 衍生快取（可刪除重建）
    ltm_state.meta.json      # 快取指紋（用來判斷是否過期）
```

## 差異化特色（我在主流記憶套件中很少看到「同時」具備）

1. **MemoryPatch（TOML）**：用純文本 patch 提交/審核記憶變更，CLI 可 `validate/apply`。適合把「寫入權」收回到人類或 CI。
2. **Time-travel recall**：可用 `--as-of` 以某個時間點回放記憶狀態（事件溯源）。
3. **Explainable retrieval**：`recall --explain` 顯示命中詞、分數與排序原因（可重現、可除錯）。

> 備註：不宣稱「世界唯一」，但這些組合在多數向量記憶/黑盒記憶方案裡並不常見，且本專案保持純文本與可審核性。

## 安裝（開發模式）

```bash
python -m pip install -e ".[dev]"
```

## Agent 使用說明書

給「會呼叫 CLI 的 AI agent」的整合手冊：`docs/AGENT_MANUAL.md`

## 快速開始

初始化（可選，會建立必要目錄）：

```bash
agentmem init
```

寫入 LTM（從參數或 stdin）：

```bash
agentmem add --kind fact --tags project,cli --text "我偏好用純文本管理記憶。"
```

搜尋 LTM：

```bash
agentmem recall "純文本 記憶" --limit 5 --explain
```

開始 STM session、寫入訊息：

```bash
sid="$(agentmem session start)"
agentmem session add --session "$sid" --role user --text "之後請都用繁體中文回覆。"
agentmem session show --session "$sid"
```

把 STM 自動萃取並提交到 LTM：

```bash
agentmem session commit --session "$sid" --auto
```

## MemoryPatch

建立模板：

```bash
agentmem patch template > mem.patch.toml
```

驗證並套用：

```bash
agentmem patch validate mem.patch.toml
agentmem patch apply mem.patch.toml
```

## 指令一覽

- `agentmem init`：建立目錄結構（輸出 home 路徑）
- `agentmem add` / `agentmem update` / `agentmem forget` / `agentmem list`：LTM 基本操作
- `agentmem recall`：LTM 搜尋（支援 `--as-of`、`--explain`、`--format json|md`）
- `agentmem session start|add|show|recall|commit`：STM session 流程（`commit --auto` 離線規則萃取）
- `agentmem patch template|validate|apply`：MemoryPatch（TOML）審核/套用
- `agentmem completion bash|zsh|fish`：輸出 shell completion 腳本

## 環境變數

- `AGENTMEM_HOME`：指定記憶 home 目錄（也可用 `--home` 覆寫）
