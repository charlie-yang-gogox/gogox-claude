# Running the Linear → dev → PR pipeline on gogox-claude itself

> Companion visual: `plans/ggx-claude-dev-workflow.html` (open in a browser).
> 行文中文;identifiers / 檔名 / label 維持英文。
> **狀態(2026-06-09)**:設計拍板 + 地基已在 branch `feat/ggx-claude-self-dev-workflow` 施工。

## 0. 一句話

要的流程(Linear → 確認完整度 → 確認相依 → 平行做 → PR)**已存在**,由
`/ticket-analyze` → `/ggx-dispatcher` → `/ggx-work` → `/route` → ff pipeline 組成,但它是為
**flutter app repo(CAF/DAF)** 設計的。要讓它跑在 **gogox-claude 自己**身上,缺三塊地基,本次補齊:
**(1) repo profile、(2) 一個 `prompt` platform + 驗證層、(3) 釐清自我修改 / dogfooding 的限制**。

## 1. app-repo vs. 本 repo 的關鍵差異

| 面向 | flutter app repo | gogox-claude(本 repo) |
|---|---|---|
| 「程式碼」 | Dart/Kotlin/Swift | `commands/*.md` prompt + 內嵌 bash + `workflows/*.js` |
| build | flutter/gradle/xcodebuild | **無 build**(prompt 不編譯) |
| test/verify | flutter test、device、Figma | **無自動測試** → 真正驗證是在 app repo 上 dogfood 該 skill(F1 是在 CAF-609 才現形) |
| OpenSpec | `/dev:ff` 全套 | 改一段 prose/bash,**不需要 spec** |
| profile | registry 有 7 個 | **本次新增**(`.gogox-claude.yaml`) |
| 生效方式 | app 重 build | `install.sh` symlink 進 `~/.claude`(沒重裝不生效) |
| 風險 | 改壞 app | **改壞的是 pipeline 自己** |

## 2. 目標流程(改造後)

```
/ticket-analyze  →  /ggx-dispatcher --team:GGC  →  /ggx-work → /route  →  start→edit→verify→review→ship  →  人工 review + merge + install.sh
 (完整度+相依 gate)    (平行 fan-out,只派 ready)     (skill-edit 走 bug lane = OpenSpec-free)   (draft PR)        (解鎖下一波)
```

核心不變:**analyze 已把「有相依」的票擋在 `need-dependency`,所以 dispatcher 一個 sweep 派出去的票
by construction 彼此獨立 → 平行安全。** 跨波次由「merge 完 PR 後重跑 analyze」觸發 —— draft PR 的人工
review 本來就是天然的波次檢查點,不需要再造一個全自動排乾 DAG 的 orchestrator。

## 3. 現有 skill 對應(用 / 改 / 跳 / 新建)

- **沿用**:`/ggx-dispatcher`(Step 0 解析到 `team_key=GGC` 即可跑)、`/ggx-work` + `/route`、
  `/bug:ff`(跳過 OpenSpec,最契合 skill 編輯)、`/code-review` · `/review`、`/dev:ship` · `/pull-request`。
- **改造**:`/ticket-analyze`(加 skill-edit completeness checklist:改哪個檔/改什麼/驗收;不要 Figma/repro)、
  `/dev:verify`(骨架沿用,`test_cmd` 換成 prompt-lint;verify-agent 審「契約自洽」而非編譯)、
  `/add-worktree`(`deps_install` 為空)。
- **跳過**:`/dev:figma`、ui-tweak preview/demo、`/spec-lint`(OpenSpec 用)、`/dev:ff` 的 spec 全套。
- **新建(本次)**:`.gogox-claude.yaml`、`prompt` platform、`scripts/prompt-lint.sh`。

## 4. 本次施工的地基(已完成)

### 4.1 `.gogox-claude.yaml`(repo profile,committed)
`platform: prompt` / `product: gogox-claude` / `branch_prefix: GGC` / `ticket_system: linear`。
committed 是刻意的 —— co-owner clone 即得,符合 ARCHITECTURE.md「thin shared repo」精神。

### 4.2 `commands/dev/profiles/platform/prompt.yaml`(新 platform)
為什麼不硬套 `node`:node.yaml 的 `npm ci / npm test / eslint` 本 repo 全炸(無 package.json/eslint)。
且 `platform` 是語意信號(`check-test` 會 branch by platform),掛 `node` 會誤導。`prompt` 才誠實。
- `deps_install: ""`(無套件管理)
- `test_cmd: bash scripts/prompt-lint.sh`(直接命令,**非** `/check-test` —— 它沒有 prompt branch)
- `format_cmd: ""`(暫無;候選 `npx prettier --write '**/*.md'`)

### 4.3 `scripts/prompt-lint.sh`(取代「build」的驗證層)
prompt 倉庫沒有編譯器,但有可機器檢查的東西。對「本次 diff 改到的檔」(committed + uncommitted +
untracked;`--all` 改成全 tracked)跑四項 **high-signal** 檢查:
1. `node --check` on changed `*.js` —— 真 JS 語法錯。
2. `bash -n` on changed `*.sh` —— 真 shell 語法錯(+ shellcheck if installed,缺則略過不失敗)。
3. **frontmatter lint** on changed command/skill/agent `*.md` —— 必須有 `name:` + `description:`
   (順手補上 ARCHITECTURE.md「待辦」清單裡一直 defer 的 SKILL.md 驗證)。
4. **footgun scan** 在 `` ```bash `` 區塊內 —— v1 只抓 `timeout`(macOS 無 coreutils 該指令,
   **正是 F1 / GGC-2 那一類**)。刻意只掃 fenced bash 區塊,避免誤判 prose。

**刻意不做**:不對每個 markdown bash 區塊跑 shellcheck —— 那些是充滿未定義變數的片段,只會全是噪音。
**已知限制(已驗證並接受)**:
- `node --check` 對 ESM(`export …`)是淺檢查;且 `workflows/*.js` 由 Workflow harness 包在 wrapper
  function 裡跑(合法使用 top-level `return`/`await`),所以**不能**用 `--input-type=module` 強制
  module 檢查 —— 會對合法 workflow script 產生 false positive(比漏報更糟)。workflow script 的深度
  驗證交給 dispatcher e2e,不是 static check。

## 5. 相依 + 波次:套到 GGC-1~9

相依偵測**優先讀 Linear 原生 `blockedBy`**。GGC 票目前只有 parent/sub-issue(非 blocking edge),
實際相依只有一條:

```
第 1 波(平行):  GGC-2 F1 · GGC-3 F2 · GGC-5 D · GGC-6 C · GGC-7 B+ · GGC-8 A2 · GGC-9 E
                       │  blockedBy(F1 未修 → iOS device 偵測壞 → 無法量測啟動時間)
                       ▼
第 2 波:          GGC-4 Phase 1.5 iOS spike  (GGC-2 的 PR merge 後解鎖)
```

流程:設 `GGC-4 blockedBy GGC-2` → `/ticket-analyze`(標 ready / need-dependency)→
`/ggx-dispatcher --team:GGC`(第 1 波平行)→ merge GGC-2 → 重跑 analyze → GGC-4 解鎖 → 第 2 波。

## 6. 本 repo 獨有的兩個陷阱

### 6.1 自我修改 — worktree **不會**弄壞 symlink(已查 install.sh 證實)
`install.sh` 把 `~/.claude/...` symlink 到 `REPO_DIR`(腳本所在目錄)的檔。git worktree(`../GGC-N`)
是另一個目錄 → 改它不碰 main worktree 的檔 → symlink 指向的目標沒變 → **不會壞,也不會自動生效**。
推論:agent 在 worktree 改的 skill,跑起來仍是 installed(=main)舊版。要人工測改好的版本:
- **(a) 推薦**:`cd ../GGC-N && ./install.sh`(REPO_DIR 自動解析成 worktree → symlink 重指過去 →
  即時生效)。代價:**全域 + 有狀態**,測完要 `cd <main> && ./install.sh` 切回;改到 pipeline 自己
  (dispatcher/verify)時格外小心 —— 裝到有 bug 的版本會污染正在用的工具。
- (b) merge 後再測;(c) 純文字改動只 review diff。

### 6.2 dogfooding 需要靶場 app repo
`/ggx-dispatcher`、`/ui-tweak`、`/dev:ff` 這種 skill 真正驗收要拿去跑一張真 app 票。所以本 repo 的
verify 兩段:**(a) prompt-lint(自動,直接吃 worktree 檔,免 symlink 切換)+ (b) dogfood(半人工,
ship 前 HITL gate)**。dogfood **不進 `--auto`**(全域切換 + 自我污染風險不該無人盯)。

## 7. 決策記錄

| # | 決策 | 結論 | 狀態 |
|---|---|---|---|
| Q1 | profile 放哪 | in-repo `.gogox-claude.yaml`(committed) | ✅ 已建 |
| Q2 | 新 `prompt` platform vs 硬套 node | 新 platform(node 指令全炸 + platform 是語意信號) | ✅ 已建 |
| Q3 | verify 最低門檻 | prompt-lint(自動)+ dogfood(ship 前 HITL,不進 --auto) | ✅ lint 已建並自測 |
| Q4 | GGC 票分類慣例 | localized prompt/bash 編輯 → `Bug`;架構/新行為 → `Feature` | ✅ 已套 GGC-2~9 |
| — | label 大小寫 | GGC team label 是 `Bug`/`Feature`(大寫),pipeline 慣例讀小寫 | ⏳ 待解:改小寫或確認 /route case-insensitive |
| — | GGC-4 blockedBy GGC-2 | 設原生 relation 讓 DAG 偵測有東西可演示 | ⏳ 本批設定 |

## 8. 尚未做(後續)

- **`/ticket-analyze` 的 skill-edit completeness checklist**:目前 checklist 只有 port/feature/bug/ui-tweak
  四個 lane,沒有「改 prompt」的清單。要跑 analyze 於 GGC 前補上(否則用 feature/bug lane 的清單將套不準)。
- **label 大小寫**:見決策表,建 profile 後第一件要解。
- **`/dev:verify` 對 prompt 平台的 verify-agent 行為**:改審「契約/引用是否自洽」而非編譯(目前是 flutter 假設)。
- **dogfood gate 正式接進 pipeline**:目前只是文件約定,未在 ship 前強制。
