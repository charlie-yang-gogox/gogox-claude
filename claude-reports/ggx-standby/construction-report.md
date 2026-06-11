# /ggx-standby + /ggx-pr-resolver 建構報告

baseline commit `03834a4`，branch `feat/ggx-standby`。本報告涵蓋 6 個 workstream（W1-W6）的實作、最終驗證、fix 輪數、deviations/open_issues、plan §6 逐項勾選，以及未解決的 cross-check BLOCK。

---

## 1. 總覽表

| Workstream | 檔案 | 做了什麼 | 驗證狀態 | fix 輪數 |
|---|---|---|---|---|
| **W1** resolve-pr-comments | `skills/dev/resolve-pr-comments/SKILL.md` | 新增 `--auto`（自動通過 Step 4 策略表 gate、抑制 DEFER 回覆 D16）與 `--force-with-lease`（callee push 慣例 D14）兩個 flag；Step 4/5b/5d/5f/6 加 `--auto` 分支；Step 5b build-sanity gate 在 --auto 下變 terminal `needs-human: comment-fix-failed-tests`（M3）。預設行為 byte-for-byte 不變。 | CLEAR（2 warn） | 0 |
| **W2** resolve-conflict-hybrid | `commands/dev/resolve-conflict.md` | 新增 `--callee` 混合模式（供 /ggx-pr-resolver step 5）：rebase 單一 caller 提供的 worktree 到顯式 base ref（D12/M5，絕不假設 origin/trunk）、非互動 auto-abort → `needs-human: conflict`、tests+format+commit、NO push（D14）。single/batch 模式 byte 不變。 | CLEAR（1 warn，指向 caller 檔案的 enum 缺口） | 0 |
| **W3** code-review-inline | `commands/dev/code-review.md` | 新增 opt-in `--post-inline`（M2/D17）：依 code-review 真實 severity bucket 路由（Critical🔴+Improvements🟡→inline；Minor🟢→digest；Positive✅→drop），批次 `event=COMMENT` review，hunk 外用 file-level fallback，不丟 finding；不加 SHA-pin（D17）。預設與 /dev:review 路徑不變。 | CLEAR（1 warn，gh api 旗標細節非 load-bearing） | 0 |
| **W4** slack-notify-standby-source | `commands/dev/_slack-notify.md` | 加入第三個 digest source `standby`（M1）：PR-keyed grammar（ci-red/self-pushed E-4、resolver-needs-human、resolver-done、review-posted、review-capped E-2、verdict-change）、7 列 mapping、Standby digest Block Kit 渲染分支、all-clear heartbeat、callers registry 補登（D17 接受重複、不做 cross-source dedup；Slack 未設定為靜默 no-op G1）。既有三 source 不動。 | CLEAR（1 warn，cosmetic） | 0 |
| **W5** dispatcher-inflight-file | `commands/dev/ggx-dispatcher.md` | 純加性 on-disk projection：新 §4.4 在 §4.3 組好 DISPATCH_ROSTER 後寫 `claude-reports/dispatcher/$RUN_TS-$$.inflight.tsv`（`ticket-id\theadRefName\tworktree-path`，B1/D11）；§6.4 報告寫完後 `rm -f`。headRefName 用 `feat/<ticket-id>` 最佳推測（type 段不可預測，建議 consumer 比對 ticket-id 段或 worktree-path）。鎖/claim/spawn/report 不變。 | CLEAR（2 warn，已記錄的合約允許偏差） | 0 |
| **W6** standby-prereq-touches | `commands/dev/ggx-standby.md` | 三項：(a) prerequisite 加 gitignore `.ggx-standby/` 一句；(b) m5 — state.json 加 `"v": 1` schema 版本 + resume load-and-merge-over-defaults 合約（缺 key 補預設、缺 reviewed_shas 變空 map）；(c) m4 — Leg-2 標明 step 1 in-session（純 gh 讀+dedup）、step 2/3 spawn run_in_background。預設行為不變。 | CLEAR（1 warn，未內聯引用 m5，符合本地慣例） | 0 |

git diff --stat：

```
 commands/dev/_slack-notify.md           | 118 ++++++++++++++++++++++++++++++--
 commands/dev/code-review.md             |  47 +++++++++++--
 commands/dev/ggx-dispatcher.md          |  31 ++++++++-
 commands/dev/ggx-pr-resolver.md         |   4 +-
 commands/dev/ggx-standby.md             |  11 ++-
 commands/dev/resolve-conflict.md        |  39 ++++++++++-
 skills/dev/resolve-pr-comments/SKILL.md |  34 +++++++--
 7 files changed, 261 insertions(+), 23 deletions(-)
```

注：`commands/dev/ggx-pr-resolver.md`（4 行變動）為先前已 commit 的 consumer spec（baseline 9e7e98e 帶入），非本批 W1-W6 任何 workstream 編輯之檔案；W1-W6 各自只動其指定 target。

---

## 2. 未解決項

### 2.1 Cross-check BLOCK（**1 項，未解決**）

**`needs-human: tests-failed` exit-reason enum 不對齊（producer/consumer 字串不符）**
- **Producer**：W2 的 callee mode（`resolve-conflict.md` step 3 + report shape `needs_human?: conflict | tests-failed`）在「乾淨 rebase 後測試仍紅」時回 `needs-human: tests-failed`。此為可達狀態（語意衝突 / flaky / pre-existing failure 被 rebase 浮現）。
- **Consumer 1**：`ggx-pr-resolver.md:43` step 8 report enum 只列 `conflict | worktree-dirty | comment-fix-failed-tests | push-failed`，**不含** `tests-failed`（`comment-fix-failed-tests` 是 step 6 comments 階段的失敗，與 rebase 階段不同）。
- **Consumer 2**：`_slack-notify.md:89` grammar 與 :191 mapping 同樣只認那四個 reason，`tests-failed` 命不中任何 token / render 分支，會在 standby digest 被丟棄或渲染未知 reason。
- **狀態**：W2 callee 行為本身正確且自洽（`tests-failed` 與 batch 模式既有詞彙一致），但這是 W2 不可編輯的 caller 檔案（`ggx-pr-resolver.md`）與 `_slack-notify.md` 的缺口。
- **建議解法（二擇一）**：
  - (A) 在 `ggx-pr-resolver.md` step 8 enum + `_slack-notify.md:89` grammar + :191 mapping 各補一個 `tests-failed`（完整 round-trip）；或
  - (B) 讓 callee 把 rebase 後測試失敗折進既有列舉的 reason。
- 此 BLOCK 落在 W2 與 W4 的 target 邊界之間，需一個明確 owner 的後續 edit 才能消解；**unresolved cross blocks 清單回報為空，但 cross-consistency 的 status=BLOCK 仍在**，視為待修。

### 2.2 still-blocked workstreams

無。W1-W6 個別 verify 皆 CLEAR、fixRounds=0。唯一 BLOCK 為跨 spec 一致性問題（見 2.1），非單一 workstream 內部失敗。

---

## 3. Deviations 與 Open Issues 彙整

### Deviations

| WS | Deviation |
|---|---|
| W1 | 採用兩個獨立具名 flag（`--auto`、`--force-with-lease`）而非單一合併模式：clause (d) 明列 force-with-lease flag 為可選機制，且兩者正交（resolver 可在不 rebase 時呼叫 --auto，comments-only PR resolver 不 push、skill 用 plain push）。flag 名與 `ggx-pr-resolver.md` step 6-7 既有詞彙逐字一致。 |
| W1 | 在 Step 5b 內聯引用 M3、相關步驟引用 D14/D16，沿用 consumer spec 內聯引用 owner 決策的方式（原 SKILL.md 不引用決策號，但 flagged 行為需可追溯）。 |
| W2 | flag 命名 `--callee`（含 `--worktree`/`--base` 參數）；caller spec step 5 未硬編 flag 名（僅描述「以 /resolve-conflict 的 mechanics」），故無命名衝突，但若日後 resolver 顯式呼叫須用 `--callee --worktree=<path> --base=<ref>`。 |
| W2 | 為 callee mode 加 `needs-human: tests-failed` terminal（rebase 階段紅測試），鏡像 batch 模式既有 `tests-failed`，以補完 report 形狀避免靜默缺口。**→ 即第 2.1 BLOCK 的根因。** |
| W3 | flag 名 `--post-inline` 採契約建議範例，標準 consumer spec 未硬編不同名。 |
| W4 | 無。 |
| W5 | clause (b)：per-lane headRefName 在 claim 時不可確定預測，依契約 fallback 寫最接近的確定值 `feat/<ticket-id>` 並記錄限制（add-worktree 由 ticket 性質推 `<type>`，dispatcher 設計上不讀分類標籤）；ticket-id 段與 worktree-path 欄為精確值，指示 consumer 改比對該兩者。 |
| W5 | §4.4 加 `: "${RUN_TS:=$(date -u +%Y%m%dT%H%M%SZ)}"`，因 RUN_TS 原僅被 §6 引用而從未顯式賦值；`:=` 冪等，使 inflight 檔與 §6.3/§6.4 報告共用同一 `<RUN_TS>-<PID>` stem。 |
| W6 | m4 新行置於 Leg-2 編號步驟前的粗體 lead-in 段落（符合檔案既有 pattern），而非塞進 step 1 內。 |
| W6 | clause (b)：既有 step-3 bullet 已含 running-flag-reset 句，故將 merge 合約加在其前、reset 句開頭改為「On resume, also unconditionally reset」以維持單一 bullet 語法通順，reset 語意不變。 |

### Open Issues

| WS | Open Issue |
|---|---|
| W2 | `ggx-pr-resolver.md` step 5/7 泛指「/resolve-conflict's mechanics」未具名 `--callee`；未來若使 resolver 顯式呼叫須傳 `--callee --worktree=<path> --base=<ref>`（caller-side wiring，非 W2 target）。 |
| W3 | `ggx-standby.md` Leg-2 step 3 未逐字指名 `--post-inline` flag；routing/severity 語意已對齊（Critical+Improvements inline、Minor digest、Positive drop），如要內聯 flag 名屬另一 workstream 檔案。 |
| W4 | renderer/caller 須產生 standby header-stat 計數（prs_open 及 r/h/p/v 衍生數），由 /ggx-standby 傳入的逐項行聚合而來；grammar 文件化 source 訊號，計數聚合以 prose 層描述（與既有兩 source 一致）。 |
| W5 | 兄弟 workstream 對 `_slack-notify.md` 的變動於工作樹已存在（W4/M1），W5 未動；git diff 範圍確認 W5 僅編輯 ggx-dispatcher.md。 |

---

## 4. Plan §6 清單逐項勾選

| §6 項 | 對應 | 狀態 |
|---|---|---|
| **項 1-2** resolve-pr-comments：`--auto` gate 自動通過、DEFER 抑制（D16）、build-sanity 仍跑、`--force-with-lease` callee push（D14）、M3 build-sanity 不可被 --auto 豁免 | W1 | ✅ 完成（CLEAR） |
| **項 3** resolve-conflict 混合 callee 模式：rebase 顯式 base ref（D12/M5）、非互動 auto-abort `needs-human: conflict`、tests+format+commit、NO push（D14）、B2 worktree primitive 委派 caller | W2 | ✅ 完成（CLEAR）— ⚠ 衍生 2.1 enum BLOCK |
| **項 4** code-review `--post-inline`（M2/D17）：severity 路由、批次 review、hunk 外 fallback、不 SHA-pin、預設不變 | W3 | ✅ 完成（CLEAR） |
| **項 5** _slack-notify 新增 `standby` source（M1）：grammar/mapping/render、D17 接受重複不 dedup、G1 Slack 未設定靜默 | W4 | ✅ 完成（CLEAR） |
| **項（dispatcher 投影）** ggx-dispatcher inflight 檔（B1/D11）：§4.4 寫、§6.4 清、headRefName 最佳推測、newest-mtime glob | W5 | ✅ 完成（CLEAR） |
| **項（standby 前置）** ggx-standby：gitignore 提示、m5 schema `v:1`+resume merge、m4 in-session vs spawned | W6 | ✅ 完成（CLEAR） |

全部 §6 項目皆已實作；唯一橫切缺口為 §6 項 3 衍生、落在 caller spec 的 `tests-failed` enum（見 2.1）。

---

## 5. 建議下一步（T1-T5 驗證序列）

**T0（先做，解 BLOCK）**：消解 2.1 的 `tests-failed` enum 不對齊 — 選擇方案 (A) 在 `ggx-pr-resolver.md` step 8 enum + `_slack-notify.md:89` grammar + :191 mapping 補 `tests-failed`，或方案 (B) 折進既有 reason。此為唯一 hard BLOCK，後續端到端測試前應先處理。

- **T1 — 預設/standalone 不回歸**：對 6 個 target 各跑一次預設模式語意對照（resolve-pr-comments 無 flag、resolve-conflict single+batch、code-review 無 flag 與 /dev:review、_slack-notify 既有三 source、dispatcher 一般 invocation、ggx-standby 既有 wake-cycle），確認 byte-equivalent / 行為不變。重點驗 W5 §4.4 寫入不落在 §4.3 表與 §5.3 spawn 之間（caller-regression warn）。

- **T2 — callee 合約 round-trip**：模擬 /ggx-pr-resolver step 4→5→6→7→8 全鏈，驗 resolve-conflict `--callee` 的三種 exit（成功 commit no-push / `needs-human: conflict` / `tests-failed`）皆能被 resolver step 8 enum 正確接住（依賴 T0 完成）。

- **T3 — standby digest 端到端**：模擬 /ggx-standby Finalize step 1 產生七種 raw-signal 行 → _slack-notify standby source，驗 mapping/render（Needs-your-action 僅 CI-RED/RESOLVER/REVIEW-CAPPED、FYI footer、all-clear heartbeat、Slack 未設定靜默 no-op G1）。

- **T4 — dispatcher↔standby skip-set 對接**：跑 dispatcher 寫 inflight.tsv，驗 ggx-standby Leg-2 step 2 與 ggx-pr-resolver step 5 的 skip-set 比對。特別覆蓋 cross-consistency warn：non-feat chain ticket（如 bug→`fix/CAF-123`）已有 PR 時，consumer 以 full headRefName 比對會漏 match 寫入的 `feat/CAF-123` — 評估是否補 consumer 端 ticket-id 段比對 fallback。

- **T5 — 文件精度收尾（polish，非阻斷）**：修 caller-regression 兩個 warn — resolve-conflict Callee Mode 引用的步驟錨點（3.2c / 3.7 / B3.3 不存在於實際標題）；以及補 §4.4 一行 single-message guardrail 註記。再清掉各 verify 的 cosmetic warn（W1 Failure-modes lease 交叉引用、W1 Step 6 SKIP 行、W3 gh api 旗標、W4 header counts 註記、W6 m5 內聯引用）。
