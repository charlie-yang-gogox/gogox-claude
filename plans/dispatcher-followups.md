# Plan — /ggx-dispatcher 既有 follow-up（B1/B3/M2/M3 + parse.py）

_寫於 2026-06-08。來源：ai-expert review（2026-05-19，`dev-dispatcher-with-work` 分支）的待辦，2026-06-08 對現有 main 重新驗證後，只收**仍開著**的項目。與 Workflow 遷移（見 `plans/dispatcher-workflow-migration.md`）無關，是獨立的 dispatcher/ggx-work 健壯性修補。_

## 已驗證為 FIXED（不再追）

- **B2** — `/dev:start` Step 4 figma-scan 的 `list_comments` 已有 fail-soft（`commands/dev/dev/start.md:142-143` 設 `COMMENTS_FETCH_OK=0` + 空字串續跑；line 166 條件 skip 尊重該旗標）。
- **B1** ✅ FIXED（PR #57，2026-06-08）— `/ggx-work` Step 4.3 auto-mode 貼 `<!-- ggx-work-error -->` 前先 `list_comments`（Jira: `getJiraIssue` 讀 comments）掃 marker，存在則 skip。沿用 `_ticket-init` Step 3 list-then-skip。
- **B3** ✅ FIXED（PR #57，2026-06-08）— `/ggx-work` Step 4.4 由二元改三元：加 "ambiguous-termination"（exit 0 但無 terminal signal、最後一行是 intermediate stage banner）→ 轉 Step 4.3 `reason = pipeline-ambiguous-termination`。含 CAF-370 負例（"Apply complete." / "Verify CLEAR." 非終態）regression guard。

## 仍開（依嚴重度）

### M2 — `/route --non-interactive` Status-line 解析契約鬆（major）
- **檔**：`commands/dev/route.md` Step 1 & 3（約 `:76-80`）；caller 解析在 `ggx-work.md:~232` 用未錨定 grep。
- **問題**：emit `Status: MISSING_TICKET_ID` / `UNKNOWN_LANE` 但沒規定「必須是 stdout 第一行」。caller 用無錨 grep → 若 Status 字串出現在後段輸出會誤判。
- **修法**：route.md 明訂「`Status:` 行必為 stdout 第一個非空白行；caller 以 `grep -m1 '^Status:'` 解析」，並把 `/ggx-work` 的 parse 改成錨定 `^Status:`。

### M3 — workflow-label vs classification-label 衝突未檢（major）
- **檔**：`commands/dev/ggx-dispatcher.md` §2.2（約 `:291-304`，現有 conflict check a/b/c）。
- **問題**：`ready-to-port` + classification ≠ `port` → dispatcher 鎖 `dispatcher-port-in-flight` 但 `/route` 推薦 `/dev:ff`；`/dev:ship` 只移除 `dispatcher-dev-in-flight` → port in-flight label 永久卡住。
- **修法**：加 conflict check (d)：`ready-to-port ∈ labels 且 port ∉ classification → skip + 解釋註解`。**不要**對 `ready-to-dev` 鏡像（它可合法搭 classification=port，即 post-spec-review 狀態）。
- **備註**：若未來做 `ready-to-dispatch` label 合併（見下方 deferred），M3 自動消失。

### parse.py 重複（minor cleanup）
- **檔**：`skills/shared/daily-summary/parse.py`（2276 行）vs `skills/_lib/parse.py`（2280 行，canonical）。
- **問題**：僅 docstring 差異，但 daily-summary 的 SKILL.md 呼叫自己的 stale copy（`~/.claude/skills/daily-summary/parse.py`），monthly-summary 正確呼叫 `_lib`。
- **修法**：刪 daily-summary 的 copy、改呼叫 `_lib`（或 symlink）。統一單一 canonical 路徑。

## Deferred 架構決策（記錄，非本輪）

- **Walker 統一**（`infer_port_stage` + `infer_dev_stage`）：已決議**不合併**（語義不同；façade 是 solution-looking-for-problem）。
- **`ready-to-dispatch` label 合併**：延到現架構 e2e 證實後再啟（3-phase：dual-recognize → switch → cleanup）。會讓 M3 失效。
- **Marker file path constants**：跳過（名稱 3 個月穩定，純預防性抽象）。

## 驗證情境（resume 時）
1. Fresh port → spec-review HITL → resume to dev（兩 sweep）。
2. Ticket 無 classification label → 驗 B1 修好且復原迴圈不累積註解。
3. FF exit-0-non-terminal → 驗 B3 不再空轉。
4. Crash recovery：in-flight label 殘留 + partial ship marker。
5. 直呼 `/route` / `/ggx-work` → 驗 M2 錨定解析。

_來源 memory：`project_dispatcher_pending.md`（已標 3 天舊、需驗證 —— 本檔即驗證後的 committed 版本）。_
