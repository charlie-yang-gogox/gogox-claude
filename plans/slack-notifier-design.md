# Slack Notifier 設計討論

> **狀態（2026-06-05）**：設計討論完成（4 設計視角 + 3 對抗評審 + 綜合）。§8 開放問題已由 Charlie 拍板，決議如下：
>
> | # | 問題 | 決議 |
> |---|---|---|
> | Q1 | Channel 結構 | **單一 `#ggx-pipeline`**，CAF/CET 靠 ticket-id 前綴區分（channel id 待實作時提供） |
> | Q3 | REVIEW 音量 | **digest 行內**，不獨立 ping |
> | Q4 | Liveness pulse | **要**，每日一次，`chat.update` 原地編輯同一則 pinned 訊息 |
> | Q6 | 去重 ledger | **不去重（2026-06-05 翻案定案）**。Charlie 明確表示：卡住的票就是要每輪重複通知，作為持續提醒。→ sidecar / ticket marker 全部取消，§5.2 的 send-on-change-only 規則廢除；digest 每輪重新列出所有 needs-human 票。連帶取消 BLOCKED 的「僅首次進入通知」自癒規則。仍然靜音的只剩：空掃 no-op、中間過程 progress。此決議同時消除了「雲端 sidecar 不跨 session」的未來債——雲端啟用 PR 不再需要重訪 ledger。 |
> | Q2 | 發送身分 | 未拍板 — 預設沿用既有 Slack bot 身分 |
> | Q5 | 雲端啟用 | 未拍板 — 預設照本文分期：v1 本地通知、雲端 graceful no-op，雲端啟用列獨立 PR |

## 1. 核心設計決策

以下決策皆為定案。每條註明採納的理由，以及哪一份 critique 否決了被棄方案。

### 1.1 傳輸機制：本地走 MCP、雲端走 Bash curl，且「雲端是否可送」必須先驗證

- **本地（互動式 / 本地 dispatcher）**：使用 `mcp__claude_ai_Slack__slack_send_message`。這是整個 repo 的既定慣例（所有 skill 都只用 MCP tool，repo 內 grep 不到任何 raw curl / webhook / bot token）。
- **雲端（headless routine）**：唯一可行路徑是 `Bash` curl 到 `chat.postMessage`。已驗證兩個 `routine.json` 的 `allowed_tools` 都是 `[Bash, Read, Write, Edit, Glob, Grep]`、`mcp_connections` 只有 Linear，因此 Slack MCP 在雲端「不存在」。
- **為何不讓 curl 變成本地預設**：Proposal 3 主張「本地也預設走 bot-token curl 以統一行為」，被 MAINTAINABILITY critique 否決——這會引入 repo 從未有過的長效 secret 管理面、第二套傳輸路徑，而本地早已有互動授權的 Slack MCP。curl 只在 MCP 缺席時（即雲端）作為例外。
- **關鍵未決前提**：FAILURE-MODES critique 指出，沒有任何已知機制能把自訂 secret（`GGX_SLACK_BOT_TOKEN`）注入 CCR 雲端 sandbox——`GH_TOKEN` 是 GitHub 整合專屬注入，routine.json 沒有 env block。**因此 v1 先落地「本地通知」，雲端維持 graceful no-op**；雲端送 Slack 列為待驗證的獨立 PR（見 §8 Q5）。在 secret 注入被實證之前，不對外宣稱雲端 Slack 可運作。

### 1.2 Helper 放哪：單一 markdown helper-doc `commands/dev/_slack-notify.md`

完全比照 `_ticket-init.md` 形狀：underscore 前綴（內部專用）、版本化 marker、fail-soft single-WARN、「Callers (current)」footer、「引用勿內聯」規則。command 檔會自動安裝，**零 install.sh 改動**。四份提案在此一致，且符合已驗證的 repo 慣例。

它只暴露**一個概念入口**，呼叫端傳「原始訊號 + ticket-id」，由 helper 內部唯一一張表做 `signal → status → emoji` 映射——**呼叫端永遠不指定 status**。MAINTAINABILITY critique 否決了 Proposal 2「8 種 status × 6 個呼叫點各自判斷」的設計（那會在每次有人改 pipeline 時漂移，正是 repo 禁止的內聯）。映射是 chokepoint，不是每個呼叫端的義務。

### 1.3 Channel topology：單一 `#ggx-pipeline` channel + run-level digest + 扁平貼文（v1 不做 thread-per-ticket、不做 Canvas）

- **單一 channel**：低量（analyzer 2x/天、dev-agent 2x/天），單 channel 才能兌現「從 Slack 一處掌握全狀態」（requirement 3）。過濾靠每行尾端的 `#needs-human` hashtag saved-search，而非拆 channel。
- **不做 thread-per-ticket（v1）**：Proposal 3/4 用「在 Linear/Jira ticket 上寫 `<!-- slack-thread:v1 ts=... -->` marker comment」來跨 stateless 重啟還原 `thread_ts`。MAINTAINABILITY 與 FAILURE-MODES 兩份 critique 同時否決：(a) 把 Slack 傳輸 handle 寫進 tracker 是分層違規，污染人看的 ticket，並讓 `_ticket-lib` 被迫長出 Slack 義務；(b) 雲端讀不到 Slack（`slack_search` 也是 MCP，雲端缺席），永遠 fallback 成新 root → thread-per-ticket 在最重要的無人值守路徑悄悄退化成 post-per-event；(c) Gap-A race（雲端 dev-agent 與本地 dispatcher 重疊）會雙寫 marker、產生雙 thread。
  → **v1 採 run-level digest 模型**：唯一需要的關聯鍵是「這一次 run」，在 process 內解決，不需跨 invocation 還原 ts。
- **不做 Canvas（v1）**：Proposal 4 的 pinned Canvas 被三份 critique 一致否決——它是 MCP-only（雲端無法寫，而雲端正是主要產出者，導致「single pane」在最該即時時反而最舊）、需要付費 Slack 方案、需要多寫者併發 `slack_update_canvas` + 快取 section_id（repo 無任何 shared-mutable-state 先例）。threaded channel + saved-search 已滿足 requirement 3。

### 1.4 通知政策：always-send（terminal-only），no-op fire 全靜音 【2026-06-05 修訂】

- ~~send-on-CHANGE-only~~ → **always-send**：Charlie 拍板「卡住的票就是要每輪重複通知，作為持續提醒」。原評審把 always-send 列為 mute-channel 風險，使用者知情並刻意選擇。digest 每輪重新列出當下所有 needs-human 票（一張卡在 `need-spec-review` 的票每天被重念兩次 = 期望行為）。
- 此修訂的紅利：整個去重機制（sidecar / ticket marker）取消，實作面更簡單，且雲端 stateless 重跑不再是問題。
- no-op / zero-candidate fire 仍**完全靜音**（四份提案一致）。已驗證實際 cadence 是 2x/天（非 brief 假設的 hourly），且多數 fire 是空掃。liveness 由每日 pulse 承擔。

### 1.5 發送來源：只從 parent / orchestrator session，呼叫端 ggx-work 永不碰 channel root

ANNOYANCE critique 指出 Proposal 1 的 `GGX_NOTIFY_SUPPRESS` env 需在每個 spawn 與每個 hook site 正確傳遞/檢查，漏一處就 N 份重複。FAILURE-MODES critique 給出更乾淨的方案並被採納：

- **ggx-work 從不送 channel root**；它只在 run-level rollup 不存在時（即 standalone 或雲端 sequential）用 digest 模型送自己這張票的 terminal 行，且以 marker 去重。
- **dispatcher parent 擁有 batch digest 與 channel-root 的 needs-human 行**，在 §6.2（唯一 race-free 的權威分類點）導出。
- 結果：standalone-ggx-work、cloud-sequential-ggx-work 行為一致，dispatcher 只是在上面疊一個 rollup——**不需 suppress flag、不會漏 site**。三種 ownership regime 收斂成「一種 ggx-work 行為 + 一種 dispatcher 行為」。

---

## 2. 統一 Status Taxonomy

設計原則：**三層 tier**——`needs_human` 一律附 `#needs-human` hashtag（client-reliable 的 load-bearing 過濾鍵；emoji 僅作為一眼可辨的視覺提示，因 Slack emoji 搜尋不可靠）。重用 dispatcher 既有的 🟢/🟡/🔴 主色，細分用文字 token 後綴，**不另開一套 emoji legend**（MAINTAINABILITY critique 否決 Proposal 2 的 legend fork：那會逼著改 dispatcher Step 6.5 的 stdout 表，並讓同一 run 維護兩套 legend）。

訊息固定文法：`<emoji> [TOKEN] <ticket-link> · <lane> — <summary> (next: <action>) #ggx-<token> [#needs-human]`

| status (token) | emoji | needs_human | 觸發來源（subsystem / event） | Jira 適用 | 範例訊息 |
|---|---|---|---|---|---|
| `READY` | ✅ | false | ticket-analyzer 判定 ready-to-port / ready-to-dev（digest 內列計，不單獨 ping） | 是（degraded：`ticket-analysis-ready` 字串標籤映射至此） | `✅ [READY] CAF-1310 · feature — complete + unblocked (next: dispatcher 自動接手) #ggx-ready` |
| `REVIEW` | 🟢 | true | ggx-work / dispatcher outcome=done（draft PR、In Review）。soft needs-human=reviewer | 是 | `🟢 [REVIEW] CET-842 · bug — draft PR open, In Review (next: review PR #842) #ggx-review #needs-human` |
| `SPEC-REVIEW` | 🟡 | true | ggx-work Step 4.4a/4.2 port-paused（need-spec-review HITL） | **n/a（Jira 無 port lane / 無 spec-review gate）** | `🟡 [SPEC-REVIEW] CAF-1260 · port — port 完成，停在 spec-review gate (next: 跑 /spec-review CAF-1260) #ggx-spec-review #needs-human` |
| `NEEDS-REVISION` | 🟠 | true | ticket-analyzer 判定 incomplete（need-revision + 理由 comment） | 是（degraded：`ticket-analysis-need-revision`） | `🟠 [NEEDS-REVISION] CAF-1234 · bug — incomplete: missing repro steps, AC (next: 編輯 ticket，下輪自動重掃) #ggx-needs-revision #needs-human` |
| `BLOCKED` | 🟣 | true | ticket-analyzer 判定 complete-but-blocked（need-dependency）。**自癒**：僅進入時送一次 | 是（degraded：`ticket-analysis-need-dependency`） | `🟣 [BLOCKED] CAF-1240 · port — blocked by CAF-1200 (open) (next: 關閉 blocker，下輪自動解除) #ggx-blocked #needs-human` |
| `CLASSIFY` | ❓ | true | ggx-work /route UNKNOWN_LANE（缺 bug/port/feature 分類） | 是 | `❓ [CLASSIFY] CET-567 · ? — 無 lane 分類 (next: 加上 bug \| port \| feature 標籤) #ggx-classify #needs-human` |
| `CYCLE` | 🔁 | true | ticket-analyzer Step 5.2 依賴環 | n/a（workflow-label 環偵測為 Linear-only） | `🔁 [CYCLE] CAF-1251 ↔ CAF-1252 — 循環依賴，兩者皆排除於順序 (next: 手動打破環) #ggx-cycle #needs-human` |
| `FAILED` | 🔴 | true | ggx-work Step 4.3（所有失敗類別匯流：pipeline-failed / route / loop-cap）+ Step 2 pre-flight hard-stop；dispatcher §6.2 failed/orphan/ambiguous；ticket-analyzer errored（寫入失敗） | 是 | `🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (test stage) (next: 看 claude-reports/CAF-1272/report.md，重跑 /ggx-work) #ggx-failed #needs-human` |
| `BATCH-ABORT` | ⛔ | true | dispatcher Step 4.2 mid-lock MCP 失敗 / partial-lock | 是 | `⛔ [BATCH-ABORT] dispatcher (CAF) — Linear MCP mid-lock 失敗 (next: 手動解鎖 CAF-1280, CAF-1281) #ggx-batch-abort #needs-human` |
| `DIGEST` | 📊 | false | ticket-analyzer Step 10 / dispatcher Step 6.5 / routine Phase 3-4 的 run-level rollup | 是 | 見 §4 |

> Jira parity（FAILURE-MODES critique 要求）：Jira（CET/DET）無 port lane、無 spec-review gate，analyzer 跑 degraded mode（`fields.labels` 字串標籤）。映射表必須把 degraded 字串標籤翻成相同 canonical token；`SPEC-REVIEW` / `CYCLE`（workflow-label）對 Jira 標 **n/a**。Jira 票只會終結於 `READY` / `REVIEW` / `NEEDS-REVISION` / `BLOCKED` / `CLASSIFY` / `FAILED`。

> **requirement 4 的交付物**：一個 saved search `#needs-human` 即列出所有需要人工的票。`READY` / `DIGEST` 不帶該 tag，自然分離。

---

## 3. 各整合點的掛載位置

通則：每個 subsystem **只有一個 run-level 送出點**（MAINTAINABILITY critique 的可維護 chokepoint——一處套 cloud transport override、一處 fail-soft、未來加 ui-tweak 只需在它自己的 rollup 引用 helper）。needs-human 的個別票以「行」內嵌在那一封 digest 裡，**不另發 top-level per-ticket 訊息**（ANNOYANCE critique 否決 Proposal 2 的 digest + per-ticket 雙送：同一秒看到同件事兩次）。

### 3.1 ticket-analyzer（`/ticket-analyze` + `ticket-analyzer-agent`）

- **Hook**：`commands/dev/ticket-analyze.md` Step 10（`Summary: X analyzed, Y skipped, Z errored.` 算出之後）→ 送 **1 封 `DIGEST`**。雲端鏡像：`cloud-routines/ticket-analyzer-agent.routine.json` Phase 3 report block（curl 路徑；§4 'No Slack' scope note 需放寬，列為獨立 PR）。
- **digest 內含**：per-verdict tallies + 每個 needs-human 票一行（`NEEDS-REVISION` / `CYCLE` / errored→`FAILED`），`READY` 僅列計數，`BLOCKED` 僅在「首次進入」時列行。
- **suppress**：zero-candidate no-op fire（全靜音）；per-ticket fetched / lane-derived（不送）；Jira degraded-mode notice（至多 digest 內一行 footnote）。
- **噪音量**：安靜日 **0 則**；活躍日 **1 則 / fire × 2 fire = 2 則/天**。

### 3.2 ggx-work（單票 orchestrator）

- **不碰 channel root**（§1.5）。在 run-level rollup 不存在時（standalone 直接呼叫、或雲端 sequential）才以 digest 模型送自己這張票的 terminal 行。
- **Hook（terminal 分支，皆讀權威狀態，絕不 parse cosmetic `[ggx-work-result]` 行）**：
  - Step 4.1 done → `REVIEW`（帶 PR URL）
  - Step 4.4a / 4.2 port-paused → `SPEC-REVIEW`
  - Step 4.3 failed / unknown-lane → `FAILED` / `CLASSIFY`
  - Step 2 pre-flight hard-stop → `FAILED`
- ~~去重 marker~~ **【廢除】**：terminal 行直接送，重跑/resume 重發同一狀態是刻意的提醒行為。
- **suppress**：Step 2.5 started、Step 4.4 pipeline-launch 等 progress（v1 扁平模型下不送；不污染 channel）。
- **在 dispatcher 之下時**：完全靜音（parent 的 §6.2 擁有報告）。因 ggx-work 不碰 root，自然滿足，無需 suppress env。
- **噪音量**：standalone 互動執行時，**每票至多 1 則 terminal**；在 dispatcher 下 **0 則**（併入 dispatcher digest）。

### 3.3 ggx-dispatcher（本地 batch）+ ggx-dev-agent（雲端）

- **只從 parent session 送，永不從 fan-out 的 N 個 background agent 送**（已驗證 dispatcher 把所有分類留在 parent，正為此）。
- **Hook**：
  - dispatcher Step 6.5（6-column 表已建好、race-free）→ 送 **1 封 `DIGEST`**（batch summary），needs-human 行排在表頂。
  - dispatcher Step 4.2 batch-abort → `BATCH-ABORT`（即使從未 spawn 也要浮現；可在 spawn 訊息之前的 turn 送）。
  - 雲端 ggx-dev-agent Phase 4 → curl digest（涵蓋 no-op fire 判斷與 per-ticket outcome）。
- **嚴禁**在 Step 4.3 dispatch table 與 N 個 Agent spawn 之間插入任何 send。FAILURE-MODES critique 證明：MCP send 是 tool call，會強制 turn 邊界，破壞「table + N spawn 必須同一訊息」的硬約束——所謂「fire-and-forget」在此 harness 不存在。batch-start ping 因此**取消**（Step 6.5 digest 已涵蓋）。
- **權威性**：所有 status 鍵自 §6.2 derived outcome + Flags，**絕不**用 cosmetic `[joined/N]` 或 `[ggx-work-result]` 行。
- **suppress**：所有 skip（PR-exists / branch-exists / duplicate / concurrent-lock）→ 只進 digest 的 `skipped: N` 計數，不發行。
- **噪音量**：每 batch **1 則 digest**（+ 罕見的 batch-abort）。

### 3.4 全域噪音數學（已對齊實際 2x/天 cadence，非 hourly）

- 雲端：analyzer 14 fire/週 + dev-agent 14 fire/週 = **28 fire/週**；其中多數為靜音 no-op。
- 預估：安靜週趨近 **0**；活躍日 **2–6 則**；卡住的票因 send-on-change-only **不重念**。

---

## 4. 訊息範例

**(A) ticket-analyzer sweep digest（Step 10）**

```
📊 [DIGEST] ticket-analyzer · CAF team — 6 analyzed (3 ready, 2 need-revision, 1 blocked, 0 errored)
Best start: CAF-1310
🟠 [NEEDS-REVISION] CAF-1234 · bug — missing repro steps, AC (next: 編輯 ticket) #ggx-needs-revision #needs-human
🟠 [NEEDS-REVISION] CAF-1239 · feature — missing Figma, scope (next: 編輯 ticket) #ggx-needs-revision #needs-human
🟣 [BLOCKED] CAF-1240 · port — blocked by CAF-1200 (open) (next: 關閉 blocker) #ggx-blocked #needs-human
ready: CAF-1310, CAF-1312, CAF-1315 · skipped: 0
#ggx-digest
```

**(B) ggx-work HITL stop（standalone，Step 4.4a port-paused）**

```
🟡 [SPEC-REVIEW] CAF-1260 · port — port 完成，停在 spec-review gate；feat/CAF-1260 已推送，無 PR
(next: 跑 /spec-review CAF-1260；PRD 已貼於 ticket)
#ggx-spec-review #needs-human
```

**(C) ggx-work failure（standalone，Step 4.3）**

```
🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (test stage, iter 2)；停在 In Progress，無 PR
(next: 看 claude-reports/CAF-1272/report.md，修正後重跑 /ggx-work CAF-1272)
#ggx-failed #needs-human
```

**(D) dispatcher batch summary（Step 6.5）**

```
📊 [DIGEST] ggx-dispatcher · CAF team — 5 processed (2 done, 1 spec-review, 2 failed) · skipped 1 (PR-exists)
🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (next: 重跑 /ggx-work CAF-1272) #ggx-failed #needs-human
🔴 [FAILED] CAF-1280 · bug — outcome-derivation-ambiguous (next: 手動核對 labels vs PR) #ggx-failed #needs-human
🟡 [SPEC-REVIEW] CAF-1260 · port — 停在 spec-review gate (next: 跑 /spec-review CAF-1260) #ggx-spec-review #needs-human
🟢 [REVIEW] CAF-1310 · feature — draft PR #501 open, In Review #ggx-review #needs-human
🟢 [REVIEW] CET-842 · bug — draft PR #842 open, In Review #ggx-review #needs-human
#ggx-digest
```

---

## 5. 防噪音機制

1. ~~send-on-CHANGE-only~~ **【2026-06-05 廢除，Charlie 拍板】**：原設計只在狀態翻轉時通知；Charlie 明確要求卡住的票**每輪重複通知**作為持續提醒。digest 每輪重新列出所有 needs-human 票（need-revision / blocked / spec-review…），這是刻意行為、不是 bug。評審原本標記的 mute-channel 風險由使用者自行承擔——若日後覺得吵，重新引入此規則即可（digest 模型不需改架構）。
2. ~~去重 ledger~~ **【廢除】**：不需要任何 sidecar / ticket marker。digest 是 run-level 重建（每輪從 tracker 現況重新導出），天然冪等於「當下狀態的快照」，重跑/resume 重發同一快照正是期望行為。
3. **no-op 全靜音**：zero-candidate / empty sweep 不送、不發 heartbeat（per-fire）。
4. ~~need-dependency 自癒（僅首次通知）~~ **【廢除，隨不去重決議】**：blocked 票每輪 digest 重新列出，直到 blocker 關閉翻成 ready。
5. **run-level batching**：一次 sweep / batch 收斂成 **1 封 digest**；needs-human 以行內嵌，**不**另發 per-ticket top-level（避免雙送）。
6. **單一 surface per item per run**：每件 actionable 物只出現在一處（digest 行），不再 digest + 個別行並列。
7. **`#needs-human` saved-search**：把 requirement 4 變成一個固定搜尋；emoji 僅視覺輔助。
8. **rate-limit 防護**：每 batch 上限 = 1 digest（多行單訊息），避免 chat.postMessage ~1 msg/sec/channel 觸發 429；遇 429 尊重 Retry-After 做一次有界 sleep 後丟棄，**絕不** retry-storm。
9. **--dry-run 全靜音**（已驗證 dispatcher --dry-run 全程唯讀；ticket-analyze 有 --dry-run）。

---

## 6. 故障隔離

1. **fail-silent 合約（逐字照搬 `_ticket-init`）**：每次 send 包成 `... || echo 'WARN: /_slack-notify: send failed for <id> — continuing.' >&2`。Slack 失敗**永不**改 exit code、**永不**阻塞 pipeline、**永不**中止 batch。只有 pipeline 自身的初始 ticket read 才 hard-stop。
2. **default-OFF kill switch**：`GGX_SLACK_ENABLE` 未設 / false 時 helper 立即 no-op return。安裝 helper 在設定前是**零風險 no-op**。單一 enable 旗標 + 單一 channel id 為唯一 config（MAINTAINABILITY critique 否決 Proposal 3 的四旋鈕 config sprawl）。
3. **headless cloud 處理**：
   - Slack MCP 在雲端**證明缺席**；helper 偵測到 Slack tool / token 不存在即 silent no-op（與 Jira missing-fields 同模式）。
   - 雲端唯一路徑是 Bash curl，但 secret 注入機制**未經實證**（§1.1）。v1 落地本地路徑、雲端 graceful no-op；兩個 routine.json 的 re-permission + scope-note 放寬列為**獨立、明確標註**的 PR，待 secret 注入驗證後才動（不綁進 v1 的 invasive edit）。
4. **併發**：
   - 只從 parent session 送（§1.5），dispatcher 的 N 個 fan-out background agent 永不送 → 不交錯、不重複。
   - 嚴禁在 dispatch-table 與 N spawn 之間插 send（會破壞單訊息 spawn 約束）。
   - Gap-A race（雲端 dev-agent 與本地 dispatcher 重疊抓同票）：因 v1 採 run-level digest（無 per-ticket thread root），不會雙開 thread；去重 ledger 進一步保證同狀態不重念。
5. **權威性**：所有分類鍵自權威狀態（analyzer label / dispatcher §6.2 outcome+Flags / ggx-work Step 4.x 分支），**絕不** parse cosmetic 行。

---

## 7. 被否決的方案

- **Proposal 4 — pinned Slack Canvas 作為 single pane**：被三份 critique 一致否決。MCP-only（雲端無法寫，導致最該即時時最舊）、需付費 Slack 方案、多寫者併發 `slack_update_canvas` + 快取 section_id 是 repo 從無的 shared-mutable-state。
- **Proposal 3/4 — thread_ts 存成 ticket 上的 marker comment（thread-per-ticket）**：被 MAINTAINABILITY + FAILURE-MODES 否決。分層違規污染 ticket、雲端讀不到 Slack 必 fallback 成 post-per-event、Gap-A race 雙 thread。v1 改用 run-level digest。
- **Proposal 2/3/4 — terminal 事件無條件 `always-send`**：被 ANNOYANCE + FAILURE-MODES 否決。卡住的票每天被重念兩次，是 mute-channel 路徑。改 send-on-change-only。
- **Proposal 2 — digest + per-ticket 雙送 + 8-emoji legend fork**：被 ANNOYANCE + MAINTAINABILITY 否決。同件事一秒內出現兩次；fork legend 逼改 dispatcher stdout 表並維護兩套 legend。改單 surface + 重用既有 🟢/🟡/🔴 + 文字 token 細分。
- **Proposal 3 — 本地預設走 bot-token curl**：被 MAINTAINABILITY 否決。引入 repo 從無的長效 secret 面與第二傳輸路徑；本地已有 Slack MCP。curl 僅雲端例外。
- **Proposal 1 — 完全無 liveness 信號**：被 FAILURE-MODES（minor）質疑。total silence 讓「routine 死了」與「安靜但健康」無法區分，違反 requirement 3。緩解見 §8 Q4（一條每日 / chat.update in-place 的低調 liveness 行）。
- **batch-start ping（Proposal 1/4）**：被 FAILURE-MODES 否決。MCP send 會強制 turn 邊界、破壞單訊息 spawn 約束；「fire-and-forget」在此 harness 不存在。取消。
- **`GGX_NOTIFY_SUPPRESS` env 穿透 spawn（Proposal 1）**：被 FAILURE-MODES 以更乾淨方案取代——「ggx-work 永不碰 channel root」消除 suppress flag 與漏 site 風險。

---

## 8. 待你決定的開放問題

1. **目標 channel**：確認單一 `#ggx-pipeline`（推薦）涵蓋 CAF（Linear）與 CET（Jira），以 ticket-id 前綴區分 team？還是要 per-team（`#ggx-pipeline-caf` / `-cet`）？並請提供 channel id。
2. **發送身分**：訊息以你既有的 Slack bot 身分張貼即可？helper 應吃設定的 channel，不寫死。
3. **`REVIEW`（draft PR / In Review）的音量**：v1 設計把它放進 digest 行（帶 `#needs-human`，因需 reviewer），**不**單獨 broadcast。確認可接受？（若你希望每個 done 都更醒目地獨立 ping，請說。）
4. **liveness pulse**：是否要一條**每日一次**（非 per-fire）的「pipeline alive, last sweep HH:MM, nothing actionable」低調行（建議用 chat.update 編輯同一則 pinned 訊息，雲端 curl 可達）？還是接受 no-op fire 完全靜音、liveness 交給 routine log？（推薦：要，成本約 7 則/週。）
5. **雲端 Slack 啟用**：是否允許開一個**獨立 PR**去 (a) 實證 CCR sandbox 能否注入自訂 secret env（`GGX_SLACK_BOT_TOKEN`），(b) 放寬 `ticket-analyzer-agent` 的「No Slack」scope note，(c) 在兩個 routine.json 加 curl 送出？在實證前 v1 雲端維持 Slack-silent，只本地 dispatcher / analyze 通知——確認此分期可接受？
6. **去重 ledger marker**：接受在 ticket 上寫 `<!-- ggx-slack:v1 status=... sent=... -->` 這個**通知狀態** marker comment（非 thread_ts、不污染為傳輸 handle，僅記是否已通知）？還是偏好完全不寫 tracker、改用本地 sidecar JSONL（但雲端 sidecar 不跨 session）？（推薦：ticket marker，因唯一跨 stateless 重跑 + Linear/Jira 皆可。）

---

## 9. 實作計畫（v1 本地）【2026-06-05 已實作 ✅ — 依 §9.4 簡化範圍落地：`_slack-notify.md` 新增、`ticket-analyze.md` Step 10.1、`ggx-dispatcher.md` §4.2/§6.5/Guardrail。Gate 邏輯（G1/G2a/G2/G3）已以 bash 實測，全路徑 exit 0】

> **§9.1 位置修訂（2026-06-05，Charlie：「應該要放在這個 repo 裡面」）**：設定檔從 `~/.claude/ggx-slack.json` 改為 **`commands/dev/profiles/ggx-slack.json`（repo 內，與 org.yaml 同層同模式）**——repo 既有慣例本來就是 profiles 放 repo、由 `install.sh` **symlink** 到 `~/.claude/commands/profiles/` 固定路徑供任何 cwd 讀取（symlink = repo 內編輯即時生效）。原 home-dir 設計反而偏離慣例。但 token 仍不可 commit：真檔已加入 `.gitignore`（commit 會洩漏 token + 把個人設定強加給所有安裝者，破壞 opt-in 防呆），committed 的是 `ggx-slack.json.example` 範本（`.example` 結尾不被 install symlink）。fresh clone 無真檔 → symlink 不存在 → G1 靜默 no-op，防呆語義不變。helper 讀固定部署路徑 `$HOME/.claude/commands/profiles/ggx-slack.json`。骨架已建（enabled:false）並已手動建 symlink；Charlie 填 channel_id + bot_token 後翻 true 即生效。

新增需求（Charlie）：(a) **防呆** — repo 是多人共用安裝，沒有 Slack bot 需求的 user 必須完全無感；(b) **Slack bot 設定檔要有留存位置**。

### 9.1 設定檔 + 傳輸層修訂 ✅（2026-06-05 Charlie 已確認：bot token + curl、明文 chmod 600）

**位置**：`~/.claude/ggx-slack.json` — per-user home 目錄、repo 之外。

- 為什麼不放 repo：repo 經 `install.sh` 多人共用，任何進 repo 的設定都變成所有人的預設，違反防呆需求；且 channel/token 是個人的。先例：`~/.claude/monthly-summary-config.json`（monthly-summary skill）。
- home 目錄天然解決 worktree 問題（ggx-work 的 worktree 會被清除，home 不受影響）。

**Schema v1**（最小化，防 config sprawl）：

```json
{
  "version": 1,
  "enabled": true,
  "channel_id": "C0XXXXXXXXX",
  "bot_token": "xoxb-...",
  "liveness_message_ts": ""
}
```

檔案 `chmod 600`。`liveness_message_ts` 保留給 v1.1 每日 pulse（chat.update 需要）。

**⚠️ 傳輸層修訂（推翻 §1.1 的「本地走 MCP」）**：Charlie 明確說「我已經有一個 slack bot，要和他整合」且「bot 設定檔要留存」——**Slack MCP 發出的訊息不是他的 bot 身分**（是 claude.ai Slack 整合的授權身分）。因此：

- **修訂**：本地與雲端統一走 `Bash curl https://slack.com/api/chat.postMessage`，以 `bot_token` 認證 → 訊息以**他既有的 bot** 身分發出，傳輸路徑單一（未來雲端 PR 不需第二套）。
- §1.1 原 MAINTAINABILITY 否決理由（「repo 從無 secret 管理面」）被使用者需求覆蓋；secret 不進 repo（只在 `~/.claude/` chmod 600，與 `~/.netrc`、`gh hosts.yml` 同級）。更高安全需求可日後改 macOS Keychain，v1 不做。
- 紅利：curl 不是 MCP tool call → dispatcher「table 與 spawn 之間禁 send」的 turn-boundary 顧慮自動消失（但仍維持只在 §6.5 送的設計，理由是 digest 模型本身）。

### 9.2 防呆 gate chain（helper Step 0，依序短路，全部 exit 0）

| Gate | 條件 | 行為 |
|---|---|---|
| G1 | `~/.claude/ggx-slack.json` 不存在 | **完全靜音 no-op**。stdout 一行 audit `slack-notify: disabled (no config)`，無 WARN。→ 其他 user 的預設體驗 |
| G2 | `enabled != true` | 同 G1（`disabled by config`）|
| G3 | JSON parse 失敗 / `channel_id` 空 / `bot_token` 空 | 單行 WARN（user 開了但設錯，需要知道），no-op |
| G4 | curl 失敗 / 非 2xx / `ok:false` | 單行 WARN（含 Slack error code），no-op。429 → 尊重 Retry-After 一次，再失敗即丟棄，絕不 retry-storm |

防呆語義：G1/G2 用 audit line 而非零輸出——debug「為什麼沒通知」時有跡可循，但不是 WARN、不會嚇到無需求的 user。任何 gate 都不影響 pipeline exit code。

### 9.3 Helper 介面（`commands/dev/_slack-notify.md`，仿 `_ticket-init.md`）【2026-06-05 簡化】

兩種呼叫 shape，呼叫端永遠傳**原始訊號**、不指定 status（映射是 helper 內唯一一張表）：

- `/_slack-notify digest <source>` + per-ticket signal 行
  source ∈ `ticket-analyzer`（行：`ready` / `need-revision reasons=<..>` / `need-dependency blockers=<..>` / `cycle ids=<..>` / `errored`）| `ggx-dispatcher`（行：§6.2 權威 outcome + Flags：`done flags=In-Review pr=<url>` / `port-paused flags=need-spec-review` / `failed flags=in-flight-residue stage=<s>`）
- `/_slack-notify batch-abort detail=<...>` — dispatcher §4.2 專用（batch 層級事件、無單一 ticket-id）

章節結構：frontmatter / Inputs / Config + 防呆 gates（§9.2）/ 映射表（signal → §2 taxonomy token/emoji/#needs-human/next-action 模板）/ 訊息文法（§2）/ Send（curl + fail-soft）/ Audit line / Failure handling 表 / Callers (3 sites, 2 files) / Guardrails。

Guardrails 必含：**no-dedup is deliberate（不要好心加回去）**、永不阻塞 pipeline、config 永不進 repo、`--dry-run` 路徑不可達 send、絕不在 dispatcher 表格與 spawn 之間插 send。

### 9.4 範圍簡化決議（2026-06-05，Charlie）：digest-only，ggx-work 完全不動

Charlie：「我通常是用 dispatcher 進行 batch 工作，是不是只要在 ggx-dispatcher 印出就好？而且可以只印在執行完最後吐出的 table？」+「也可以新增 ticket analyzer 的訊息」。定案：

- **v1 通知點只有三個**：dispatcher §6.5 digest、dispatcher §4.2 batch-abort、ticket-analyze Step 10 digest。
- **ggx-work 的 7 處 terminal hook 全部取消**：standalone `/ggx-work` 是互動式（輸出就在眼前，Slack 通知多餘）；dispatcher 之下的結果由 §6.5 table 涵蓋。
- **連帶取消 `--dispatched` flag 與 5 處範例字串同步**——ggx-work 不通知，「偵測自己在 dispatcher 之下」的問題整個消失。（歷史備註：候選方案「讀 `dispatcher-*-in-flight` label」已被證偽——`/dev:ship` 成功時即移除該 label，而 Step 4.1 Terminal 在 ship 之後才到達；未來若要恢復 per-ticket 通知，用 CLI flag，勿用 label。）
- §4.2 batch-abort 保留的理由：它是唯一**到不了 §6.5** 的異常出口——batch 中途死掉時「最後的 table」永遠不會印，沒有它 Slack 上就零紀錄。
- v1 實際出現的 token：`DIGEST`、`REVIEW` / `SPEC-REVIEW` / `FAILED`（dispatcher 行）、`NEEDS-REVISION` / `BLOCKED` / `CYCLE` / `FAILED`（analyzer 行，`READY` 僅計數）、`BATCH-ABORT`。`CLASSIFY` 在 dispatcher 視角併入 `FAILED`（reason 文字仍可見）；§2 完整 taxonomy 保留供未來擴點。

### 9.5 各檔精確 edit 形狀（3 檔）

**(1) `commands/dev/_slack-notify.md`** — 新增，§9.3 結構。

**(2) `commands/dev/ticket-analyze.md`** — Step 10（L402-413）結尾加「Slack digest (best-effort)」小節：
- gate：`--dry-run`（Step 7 即轉印報告、不進寫入迴圈）或 `analyzed + errored == 0`（空掃/全 skipped）→ 跳過，一行 audit。
- 否則：從 Step 9 報告資料組 header counts + per-ticket signal 行（needs-human 行排頂、ready 僅計數、best-start 一行）→ `/_slack-notify digest ticket-analyzer`（1 封）。

**(3) `commands/dev/ggx-dispatcher.md`** — 2 處 + 1 條 guardrail：
- §6.5（L748-770）：印完 `Counts/Report` 之後、`STOP.` 之前 → 從 §6.4 in-memory rows（§6.2 權威 outcome + Flags + pr）組 digest 行 → `/_slack-notify digest ggx-dispatcher`（1 封）。`--dry-run` 在 §4.0 gate 即停、不達 §6.5 → 天然靜音。
- §4.2（L367-378）：第 2 點 `PARTIAL LOCK` 之後、`STOP — release lock` 之前插入 → `/_slack-notify batch-abort detail=<failed-ticket + 未解鎖清單>`（best-effort）。
- Guardrails 清單末尾加一條：Slack notify 僅存在於 §4.2 / §6.5 兩點；絕不在 §4.3 表格與 §5.3 spawn 之間插入任何 send。

**不動**：`commands/dev/ggx-work.md`、`cloud-routines/*`（獨立 PR）、`install.sh`（command 檔自動安裝）、`_ticket-lib.md`。

### 9.6 v1.1 後續（不綁進 v1）

- **每日 liveness pulse**：機制 = 本地 cron（或 schedule routine）每日呼叫 helper 的 pulse mode，`chat.update` 編輯 `liveness_message_ts` 指向的 pinned 訊息（ts 存設定檔）。獨立小 PR。
- **雲端啟用**：實證 CCR secret 注入 → 兩個 routine.json + scope note 放寬。獨立 PR（§8 Q5）。

### 9.7 驗證計畫（實作後依序）

1. **防呆**：無 config 跑 `/ticket-analyze <id>` → 零 Slack 訊息、audit line 出現、exit code 不變。
2. `enabled:false` → 同上。
3. 真 channel 設定後手動 `/_slack-notify terminal CAF-XXX done pr=<url>` → 驗證 bot 身分、格式、hashtags。
4. 故意填錯 token → 單行 WARN、pipeline 不受影響。
5. 小規模 `/ticket-analyze` batch → 恰好 1 封 digest、needs-human 行在頂。
6. `/ggx-dispatcher --dry-run` → 0 訊息。
7. standalone `/ggx-work` → 1 條 terminal 行；模擬 `--dispatched` → 0 條。

### 9.8 動工前待 Charlie 提供／確認

1. **傳輸層修訂確認**（§9.1）：本地也改走你的 bot token curl（訊息=你的 bot 身分）。若你其實接受訊息以 Slack MCP 授權身分（非 bot）發出，可回到原 §1.1 設計、設定檔就不存 token。
2. `#ggx-pipeline` 的 **channel id**（bot 需先被邀進 channel）。
3. bot token 放 `~/.claude/ggx-slack.json`（chmod 600，明文）可接受？或要求 Keychain（v1 成本較高）。
