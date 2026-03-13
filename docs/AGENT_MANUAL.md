# Agent 使用說明書（agentmem）

這份文件寫給「會呼叫外部 CLI 的 AI agent」：如何用 `agentmem` 做可追溯的短期/長期記憶，並用純文本的方式安全地提交記憶變更。

## TL;DR（給 agent 的最小可行用法）

- **讀 LTM（機器可解析）**：`agentmem recall "<query>" --format json --limit 10`
- **寫 LTM（較安全，推薦）**：產出 `MemoryPatch(TOML)` → 讓人類/CI 跑 `agentmem patch validate/apply`
- **寫 LTM（直接寫入）**：`agentmem add ...` / `agentmem update <id> ...` / `agentmem forget <id>`
- **用 STM session**：`agentmem session start` → 持續 `session add` → 需要時 `session commit --auto`

### 可直接貼到 Agent 的提示片段（Prompt Snippet）

把下面這段貼進你的 agent 的 system prompt（可按需調整）：

```text
你可以呼叫本機 CLI 工具 `agentmem` 來做純文本記憶（LTM/STM）。

- 讀取記憶：優先用 `agentmem recall "<query>" --format json --limit 10`，只取前 3–7 筆結果用於上下文。
- 需要除錯排序才用 `--explain`；需要稽核/回放才用 `--as-of`。
- 寫入長期記憶時，優先產出 MemoryPatch(TOML) 交由人類/CI 審核套用；除非明確允許，否則不要直接寫入。
- 絕不把密碼、API key、token、私鑰、或敏感個資寫入 LTM。
- `kind` 請使用：fact/preference/instruction/profile/note/task/other；並加上可檢索的 tags（如 user/project/style/constraint）。
```

## 1) 記憶模型

### LTM（長期記憶）

每筆長期記憶是一個條目（entry），最重要欄位：

- `id`：條目識別碼（字串）
- `kind`：`fact|preference|instruction|profile|note|task|other`
- `tags`：可重複標籤（字串陣列/tuple）
- `importance`：0–10（預設 5）
- `text`：記憶內容（純文本）
- `source`：來源（例如 `session:<sid>`、文件路徑、URL…）
- `created_at/updated_at`：時間戳
- `forgotten_at/forget_reason`：遺忘（tombstone）資訊

LTM 的真實資料是 **append-only 的 NDJSON 事件日誌**，因此支援：

- **可追溯**：每次新增/更新/遺忘都是事件
- **Time-travel recall**：用 `--as-of` 回放某時間點的狀態（除錯/稽核用）

### STM（短期記憶）

STM 用 `session` 管理：每個 session 是一個 NDJSON 事件檔（訊息序列）。

## 2) 目錄與環境變數

`agentmem` 會把資料放在「記憶 home」底下：

優先順序：

1. `AGENTMEM_HOME`
2. 若當前目錄存在 `./.agentmem/`，則使用它（適合專案內記憶）
3. 否則用 `~/.agentmem/`

基本結構（純文本）：

```text
<home>/
  ltm.ndjson
  stm/sessions/<session_id>.ndjson
  cache/ltm_state.ndjson
  cache/ltm_state.meta.json
```

## 3) Agent I/O 合約（stdout/stderr、JSON 格式、退出碼）

### 輸出規則

- **stdout**：成功輸出（可被管線/程式解析）
- **stderr**：錯誤訊息

### `recall --format json` 的輸出格式

回傳 JSON array，每筆為：

```json
{
  "score": 1.2345,
  "matched_terms": ["純", "文", "本"],
  "term_counts": {"純": 1, "文": 1, "本": 1},
  "entry": {
    "id": "…",
    "created_at": "…",
    "updated_at": null,
    "kind": "preference",
    "tags": ["user", "preference"],
    "importance": 7,
    "text": "…",
    "source": "session:…",
    "expires_at": null,
    "session": "…",
    "forgotten_at": null,
    "forget_reason": null
  }
}
```

### 退出碼（exit code）

- `0`：成功
- `2`：輸入/操作錯誤（例如缺參數、patch 不合法、patch 空等）
- `130`：收到 Ctrl+C 中斷

## 4) 檢索策略（Agent 應該怎麼「問」）

推薦流程：

1. 把當前任務/需求濃縮成 3–12 個關鍵詞（可中英混用）
2. 呼叫 `agentmem recall "<query>" --format json --limit 10`
3. 只取前 3–7 筆高分結果做上下文注入

建議：

- 需要稽核/除錯時才加 `--explain`（會增加輸出）
- 需要回放歷史狀態才加 `--as-of`（稽核/回歸測試）

## 5) 寫入策略（什麼該存、什麼不該存）

### 適合寫入 LTM 的內容

- **偏好**：語言、輸出格式、風格、工具偏好（`kind=preference`）
- **長期約束/規範**：不可做什麼、必須遵守什麼（`kind=instruction`）
- **穩定事實**：專案固定設定、常用路徑、持久的決策（`kind=fact`）
- **使用者檔案**：姓名、自我介紹、長期背景（`kind=profile`）

### 不建議寫入 LTM 的內容

- API key、Token、密碼、私密金鑰（請不要存）
- 一次性/短期上下文（先放 STM session）
- 高風險個資（除非你有明確合規需求與使用者授權）

## 6) 推薦安全模式：MemoryPatch（TOML）

MemoryPatch 的目標：**讓 agent「提案」記憶變更，而不是直接改資料**，便於人類審核、CI 套用、稽核回放。

### Patch 檔格式（v1）

```toml
format = "agentmem-patch"
version = 1

[[op]]
type = "add"
kind = "preference"
tags = ["user", "preference"]
importance = 7
text = """之後請都用繁體中文回覆。"""

[[op]]
type = "update"
id = "EXISTING_ID"
patch = { tags = ["user", "preference", "style"] }
reason = "add tag"

[[op]]
type = "forget"
id = "EXISTING_ID"
reason = "outdated"
```

### 套用流程（人類/CI）

```bash
agentmem patch validate mem.patch.toml
agentmem patch apply mem.patch.toml
```

> 備註：`add` 若不提供 `id`，`agentmem` 會用內容導出 deterministic id，重複套用不會產生「多筆狀態」（仍會追加事件，但狀態一致）。

## 7) 直接寫入模式（Agent 有寫入權時）

新增：

```bash
agentmem add --kind preference --tags user,preference --text "之後請都用繁體中文回覆。"
```

更新（可用 `--text` 或 `--stdin` 從 stdin 讀入）：

```bash
agentmem update <id> --tags "user,preference,style" --reason "add style tag"
```

遺忘（tombstone）：

```bash
agentmem forget <id> --reason "outdated"
```

## 8) STM session 建議用法

### 寫入 session（逐句/逐段）

```bash
sid="$(agentmem session start)"
agentmem session add --session "$sid" --role user --text "…"
```

### 在 session 內搜尋（機器可解析）

```bash
agentmem session recall --session "$sid" "關鍵詞" --format json --limit 5
```

### Promotion：從 STM 提升到 LTM（離線規則）

```bash
agentmem session commit --session "$sid" --auto
```

> 目前 `--auto` 是規則式萃取（不呼叫外部 LLM），適合把明顯的偏好/檔案資訊推進 LTM。

## 9) 常用配方（實務）

- 使用者指定語言/格式 → `kind=preference` + `tags=["user","preference","style"]`
- 專案硬性規範（不可上網、不可改某檔）→ `kind=instruction` + `tags=["project","constraint"]`
- 個人背景（姓名/職稱）→ `kind=profile`

## 10) 除錯與維護

- 想確認檢索排序原因：`recall --explain`（debug 用）
- 快取可安全刪除：刪 `cache/` 後會自動重建
- 遇到 lock timeout：檢查 `<home>/ltm.ndjson.lock` 是否為殘留檔（通常 2 分鐘視為 stale）

## 附：工作流示意（Mermaid）

```mermaid
flowchart TD
  A[對話/任務輸入] --> B[STM: agentmem session add]
  B --> C{需要持久化?}
  C -- 否 --> B
  C -- 是 --> D[產出 MemoryPatch(TOML)]
  D --> E[人類/CI validate+apply]
  E --> F[LTM: ltm.ndjson]
  F --> G[agentmem recall]
  G --> A
```
