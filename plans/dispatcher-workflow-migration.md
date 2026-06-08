# `/ggx-dispatcher` 平行 fan-out 遷移至 Workflow tool 設計提案 (R5)

> 對應 `ARCHITECTURE.md` §「Nested-spawn constraint (subagent depth)」R5。
> 行文中文；所有 code / 識別符 / 檔名 / label 維持英文。
> §1 以下是**原始設計提案全文**（2026-06-06）；下方「狀態與 roadmap」是落地後的現況補記。

---

## 狀態與 roadmap（2026-06-08 補記）

設計提案的一部分**已落地**，此檔同時作為設計依據與進度追蹤：

- **Phase A — 已 merge（PR #50）**：`/ggx-dispatcher --workflow` opt-in；dev/port/bug lane 走
  `workflows/ggx-dispatch.workflow.js`；ui-tweak 仍 §5.0 inline。markdown 整合在 §5.2 + §5.3/§6.1/§6.2/§6.4 guards。
- **e2e 發現修正 — 已 merge（PR #51）**：(1) 全 lane PR 查詢改 head-branch（原 `gh pr view <ticket-id>` 在
  branch=`<prefix>/<id>` 時回空 → resume 誤判）；(2) workflow Linear-MCP fallback 一致性（script runFallback +
  §5.2 precondition + allowlist 兩 prefix）。
- **allowlist（user-global `~/.claude/settings.json`）**：git/gh/openspec/`yq`/flutter… +
  `mcp__claude_ai_Linear__*` + `mcp__linear-server__*` + Jira + figma。
- **✅ Phase A 真·驗證 — GREEN（2026-06-08，CAF-371）**：四次 `/ggx-dispatcher` 一個 session。run 1 是 default
  §5.3 路徑（CAF-393 → PR #515）；run 2-4 走 `--workflow --test`。CAF-371 跨 run 3（fresh-port →
  port-paused）→ 你的 `/spec-review` → run 4（fresh-dev → PR #517）跑完整生命週期。**lifecycle correctness 全綠**：
  lock swap / in-flight label / status transition / fallback / empty-batch 全部照 spec，無卡 lock、無漏 label、
  無重複註解。Go-gate 過。
- **驗證逼出的兩個 workflow-script bug（已修，本批 commit）**：
  - **P1** — `meta.description` 用了 `"..." + "..."` 串接，非 pure literal → Workflow tool 直接拒絕啟動。收成單一字串。
  - **P2** — `args` 在此 harness 以 **JSON 字串**抵達（非 live array，§5.2 line 607-608 的文件描述是錯的）；
    原 `Array.isArray(args)` 直接 fall through 到 empty-roster no-op（0 agent、0 mutation，看起來像「沒工作」）。
    加 `JSON.parse` fallback 修好，run 4 無 patch 即過。
- **🟦 P3 澄清（勿再沿用錯誤結論）**：retro 一度宣稱「`--workflow` 讓 verify-agent 變 level-1、修掉 verify 去相關性降級」
  —— **錯**。script 只 spawn **worker**（level-1）；verify-agent 是 worker 在 `/dev:verify` 裡 spawn 的（level-2），
  兩條路徑皆然。`--workflow` **不改變 verify-agent 深度**。會被 script 升到 level-1 的只有 **script 直接 spawn 的東西**
  （= Phase B 的 ui-tweak opus judges）。把 `--workflow` 設預設可以有別的理由（統一路徑、中間狀態不佔 main context），
  但「修好 verify 去相關性」不是其中之一。另：verify-agent 是 **sonnet**，level-2 sonnet 本該 work（見 R2/R3），run 1
  是否真降級存疑（報告未附 `.dev/verify-pass.md` Status）。

**接下來（嚴格依序，gate 不過不前進）：**

1. ~~**Phase A 真·驗證 Go-gate**~~ ✅ **DONE（2026-06-08，見上）**。剩餘掃尾：把 P1/P2 修正 commit + push +
   propagate 到所有 clone（未 propagate 前其他機器的 `--workflow` 是 silent no-op）；修 §5.2 line 607-608 的
   args 文件描述（改成「stringified，script 負責 parse」）。
2. **Phase B — 已實作（opt-in，待 flutter-repo 驗證）**：§7 方案 (a) 已落地 ——
   `workflows/ggx-dispatch.workflow.js` 的 `runUiTweak` 由 script 直接 spawn `ui-verify-agent`(sonnet) +
   `dev-reviewer`(opus) 兩位 judge（level-1）+ prep/finisher；pipeline stage-1 依 `uiTweak` flag 分流；
   `runFallback` 對 ui-tweak 失敗也貼 Linear（script 全程擁有此 lane）。markdown：§5.2 改 Phases A+B、roster
   含 ui-tweak rows、§5.0 標註「default-path only」、step 4/5 改寫。audit.md 頂加 SYNC 註解、ARCHITECTURE R5 更新。
   - **待蓋章的關鍵證明（只能在 flutter repo 實跑）**：opus judge 在 **script-spawned** context 成功 + tier 未降級。
     **風險已被 Phase A 大幅降低** —— Phase A 的 worker 本身就是 `model:"opus"` 且 CAF-371 跑成功,證明 script-spawned
     opus 可行;Phase B 的 judge 只是同一 script 多 spawn 兩隻。剩餘未知 = dual-judge panel 在 script 內的端到端行為
     （prep 停在 audit 前 → 兩 judge parallel → finisher）。**這個 PR 先不 merge,等一張 design bug 票 `--workflow` 實跑綠燈再 merge。**
   - 雙寫風險（script vs audit.md）：已互加 SYNC 註解;契約改版需兩邊同步。
3. **Phase C（B 綠燈後）**：`--workflow` 翻成預設，留 `--no-workflow` 逃生門一個 release；刪 §5.0/§6.1/§6.2 逐張重算。
   驗 resume（同 session `resumeFromRunId` + 跨 session label 重撿）。

**待決**：Linear 連線策略長期是否固定 linear-server（現兩者皆允許）；chained-command prefix-match e2e 實證。
**不會改善（誠實）**：token 成本同今天；R1 重活仍 inline 在 worker。

---

## 0. 一句話結論（先講重點）

把 `/ggx-dispatcher` 的「人類互動 + Linear 寫入 + lock」留在 markdown 主 session，把
「§5.3 fan-out + §6.1 wait + §6.2 fallback + §6.4 彙總」整段搬進一支 **Workflow script**，
透過 `args` 傳入 §4.3 的 `DISPATCH_ROSTER`。**核心洞見**：Workflow script 自己 spawn 的 agent
全部是 *level-1*（script→agent 之間不受 nested-spawn 限制），所以 §5.0 為了讓 opus judge 能 spawn
而存在的「inline 例外」自然消失 —— ui-tweak 的兩位 judge 可以由 script 直接平行 spawn，
回到 §5.3 的「全 lane 統一 fan-out」初衷。

---

## 1. 現況 vs 目標架構（兩張 ASCII 圖）

### 1.1 現況（manual N×Agent，含 §5.0 inline 例外）

```
main session (/ggx-dispatcher markdown, bypassPermissions)
  │
  ├─ §2  sweep Q1–Q4 (Linear MCP)  ┐
  ├─ §2.1 dedup + ui-tweak flag    │  互動 / MCP / 檔案 I/O
  ├─ §3  anti-dup (gh + ls-remote) │  全部在主 session
  ├─ §4  race-lock + §4.1 init     ┘
  │
  ├─ §4.3 print DISPATCH_ROSTER table  ─┐  同一個 assistant message
  ├─ §5.3 spawn N × Agent(general-purpose, opus, bg, bypassPerms)
  │         │   │   │ ...                │  ── 這是要遷移的部分
  │       (CAF-1)(CAF-2)(CAF-3)          │
  │         worker = /ggx-work --auto    │
  │           └─ /route → /dev:ff …       │
  │                └─ R1 inline 重活      │
  │                                       │
  ├─ §5.0 INLINE ui-tweak lane (序列，在主 session) ←── 例外：
  │         /ggx-work CAF-X --auto inline      因為 nested opus judge 在
  │           └─ /ui-tweak:ff → audit          spawned worker 內會失敗
  │                └─ spawn ui-verify(sonnet)+dev-reviewer(OPUS) ✔ 主 session 可
  │
  ├─ §6.1 wait（背景完成通知 → joined++）─┘
  ├─ §6.2 per-ticket 權威 outcome 推導（get_issue + walker + gh pr）
  ├─ §6.4 彙總表 + §6.3 報告複製
  └─ §6.5 release lock + Slack digest
```

痛點：
- §5.0 是整個 fan-out 唯一的 lane-specific 分支，只為了「opus judge 不能在 level-2 spawn」。
- §6.1 是一個事件驅動的等待迴圈 + 手動 `joined` 計數器，靠 background notification 收斂。
- §6.4 表格資料來自 §6.2 在主 session 逐張 ticket 的 `get_issue`/walker/`gh pr` 推導，
  跟 agent 文字輸出的解析（cosmetic `[ggx-work-result]`）刻意分離。

### 1.2 目標（Hybrid：markdown 互動層 + Workflow script 編排層）

```
main session (/ggx-dispatcher markdown)         ── 保留：互動 / Linear MCP / lock
  │
  ├─ §2/§2.1/§3  sweep · dedup · anti-dup        (不變)
  ├─ §4/§4.1     race-lock + _ticket-init        (不變)
  ├─ §4.3        build DISPATCH_ROSTER (JSON)     ←── 改：序列化為 JSON array
  ├─ 持久化 {runId, scriptPath, roster} 到
  │            claude-reports/dispatcher/<TS>-<PID>.run.json   ←── 新增（resume 用）
  │
  └─ Workflow({ scriptPath, args: roster })   ───────────────────┐ 背景執行
                                                                  │
   ┌──────────────── ggx-dispatch.workflow.js (level-0 script) ──┘
   │  ※ script 無 FS / 無 MCP / 無 shell；Date.now/Math.random 會 throw
   │
   │  pipeline(roster,
   │    stageWork:   item → agent("/ggx-work <ID> --auto", {schema:WORK_SCHEMA})  ── level-1
   │    stageUi:     若 uiTweak → 由 script 直接 spawn 三隻 level-1 agent：
   │                   apply/preview agent → ui-verify-agent(sonnet) ┐ parallel barrier
   │                                        → dev-reviewer(opus)     ┘ ←── §5.0 消失點
   │                   → finisher agent (commit/PR)
   │    stageFallback: item.outcome=='failed' → agent(Linear 寫入)   ── §6.2 即時 fallback
   │  )
   │  最後 phase：彙總所有 structured result → 回傳 summary object
   └──────────────────────────────────────────────────────────────
                                                                  │
  main session ← 完成通知 + summary object                         ┘
  ├─ §6.4 render 彙總表（吃 summary，不再逐張 get_issue）
  ├─ §6.5 release lock + 一次 Slack digest agent（在 script 內或回到 markdown）
```

---

## 2. Hybrid 切分表

切分原則：**任何需要 AskUserQuestion、需要 dispatcher lock 檔案、或需要「sweep 當下的
Linear/gh 即時狀態判斷」的步驟留在 markdown；純編排 + 把 I/O 委派給 agent 的步驟進 script。**
根因是 Workflow script 本體無 FS / 無 MCP / 無 shell，且 `Date.now()`/`Math.random()`/
無參數 `new Date()` 會 throw（resume 決定性）。

| 段落 | 留 markdown / 進 script | 為什麼 |
|---|---|---|
| §0 Resolve profile（讀 registry yaml、team_key、default_branch） | **markdown** | 需讀檔 + `git`/`gh` shell；且決定要不要 abort，是互動前置。 |
| §1 Pre-flight（lockfile、worktree guard、branch/clean、gh auth） | **markdown** | dispatcher lock 檔案 + `git stash` 殘留處理 + abort 訊息，全是 shell + 主 session 專屬。 |
| §2 sweep Q1–Q4（`list_issues`） | **markdown** | Linear MCP 呼叫；script 內無 MCP（agent 內才有，但 sweep 是「決定要不要繼續」的互動前置，不適合丟給背景 agent）。 |
| §2.0/§2.1 post-filter · dedup · ui-tweak flag | **markdown** | 純資料轉換，但緊接 sweep 結果且影響「是否 STOP cleanly」，留在主 session 較直觀。 |
| §2.2 conflict checks（drop + comment + label 移除） | **markdown** | 需 `save_issue` + `save_comment`（Linear 寫入）+ 立即決定 drop。 |
| §3 anti-dup（`gh pr list` / `git ls-remote`） | **markdown** | shell + lane-aware drop/comment；lock 前的最後 gate。 |
| §3.5 port config pre-check | **markdown** | 讀 `.claude/port-settings.json` + abort。 |
| §4 / §4.1 race-lock + label swap + `_ticket-init` | **markdown** | **最關鍵留下理由**：lock 必須在 spawn *前* 完成（Guardrail：第二次 invocation 不能看到 race-pickable ticket）；且 `dispatcher-*-in-flight` 是 dispatcher 專屬寫入。這些是 Linear MCP 寫入 + 順序性，且要能在失敗時 §4.2 rollback。 |
| §4.2 mid-batch lock 失敗 rollback | **markdown** | 發生在 spawn 之前；script 還沒啟動。 |
| §4.3 build `DISPATCH_ROSTER` | **markdown 建立 → 進 script 當 `args`** | roster 在 markdown 算出（worktree path = `realpath ../<ID>`、url 來自 §2 cache、ui-tweak flag），序列化成 JSON array 傳給 script。 |
| **§5.0 inline ui-tweak lane** | **進 script（消失為一個 stage 分支）** | script-spawned agent 是 level-1，opus judge 可直接由 script spawn → 不再需要 inline 例外。見 §4。 |
| §5.1/§5.3 fan-out（N×Agent） | **進 script（`pipeline`）** | 這是遷移主體。 |
| §6.1 wait loop + joined 計數 | **進 script（`pipeline`/`parallel` 自帶 barrier）** | script 的 promise 收斂取代手動 joined 計數器。 |
| §6.2 per-ticket fallback（Linear comment + label flip） | **進 script（per-item `.then` stage）** | 改成「失敗 ticket 的 fallback 立即跑」而非批次結尾跑（見 §5）。**權威 outcome 來自 agent 回傳的 structured schema**，不再逐張 `get_issue` 重算。 |
| §6.3 報告複製（`cp -R` worktree reports） | **markdown（script 回來後）** | 純 shell 檔案搬運，主 session 做。 |
| §6.4 彙總表 render | **markdown（吃 script 回傳 summary）** | 終端輸出；資料已由 script 結構化回傳。 |
| §6.5 release lock + Slack digest | **markdown（lock）+ script 或 markdown（Slack）** | lock 檔案必須主 session 釋放；Slack 可由 script 最後一個 agent 發，或回 markdown 發（建議後者，見風險表）。 |

---

## 3. 完整可讀的範例 Workflow script（centerpiece）

> 檔名建議 `~/.claude/workflows/ggx-dispatch.workflow.js`（personal）或 `.claude/workflows/`（shared）。
> 這份 script 忠實對應 §§ 語義：`WORK_SCHEMA` 取代 §6.1 的 `[ggx-work-result]` 文字解析；
> `pipeline` 的 per-item barrier 取代 §6.1 wait loop；ui-tweak stage 由 script 直接 spawn
> 兩位 judge（§5.0 消失點）；fallback stage 即時跑（§6.2）。

```javascript
// ggx-dispatch.workflow.js
// 對應 /ggx-dispatcher §5.3 fan-out + §6.1 wait + §6.2 fallback + §6.4 aggregation。
// args = DISPATCH_ROSTER：JSON array of
//   { ticketId, lane, worktreePath, url, uiTweak: boolean }
// 由 markdown 主 session 在 §4.3 鎖定完成後序列化傳入。

export const meta = {
  name: "ggx-dispatch",
  description:
    "Fan out one /ggx-work --auto agent per locked Linear ticket; " +
    "design-bug tickets run apply→dual-judge(sonnet+opus)→finish as " +
    "script-spawned level-1 agents (dissolves dispatcher §5.0 inline lane). " +
    "Per-ticket failure fallback writes Linear immediately, not at batch end.",
  // phases 純為 literal —— /workflows 進度視圖用，無執行語義。
  phases: [
    { title: "work",     detail: "Drive each ticket through /ggx-work --auto" },
    { title: "ui-judge", detail: "ui-tweak lane: dual-judge panel + finisher" },
    { title: "fallback", detail: "Per-ticket Linear writes on failure" },
    { title: "aggregate", detail: "Collect structured outcomes into summary" },
  ],
};

// ── 結構化每張 ticket 的結果 —— 取代 §6.1 的 [ggx-work-result] 文字解析 ──
// schema 透過 forced StructuredOutput 回傳已驗證的 object。
const WORK_SCHEMA = {
  type: "object",
  required: ["ticketId", "outcome"],
  additionalProperties: false,
  properties: {
    ticketId: { type: "string" },
    // 對齊 §6.2 權威分類詞彙
    outcome:  { type: "string", enum: ["done", "port-paused", "failed"] },
    prUrl:    { type: ["string", "null"] },
    // walker 階段（infer_*_stage 的輸出），給 §6.4 stage_reached 欄位
    stage:    { type: ["string", "null"] },
    error:    { type: ["string", "null"] },
  },
};

// ui-tweak judge 的精簡 verdict schema（CLEAR/BLOCKED + 一行原因）
const JUDGE_SCHEMA = {
  type: "object",
  required: ["status"],
  additionalProperties: false,
  properties: {
    status: { type: "string", enum: ["CLEAR", "BLOCKED"] },
    reason: { type: ["string", "null"] },
  },
};

// ── 共用：把一張 roster row 跑完 /ggx-work --auto（dev/port/bug lane）──
// agentType 用 general-purpose-equivalent；model:'opus' 對應 §5.3 的 opus 理由
// （R1 重活仍 inline 在這個 level-1 worker 內 —— 遷移不改變這點）。
async function runWork(item) {
  log(`[work] ${item.ticketId} lane=${item.lane}`);
  const result = await agent(
    [
      `Execute: /ggx-work ${item.ticketId} --auto`,
      ``,
      `/ggx-work is a single-ticket orchestrator that drives this ticket`,
      `through every pipeline it needs by repeatedly calling /route and`,
      `executing the recommended ff. Drive it to a terminal condition; do`,
      `NOT stop on intermediate stage messages.`,
      ``,
      `Terminal conditions (any ONE ends the run):`,
      `  (a) "Ticket <id>: done."            -> outcome "done"`,
      `  (b) "port complete, paused for ..."  -> outcome "port-paused"`,
      `  (c) non-zero abort                   -> outcome "failed"`,
      ``,
      `Return the structured object: ticketId, outcome, prUrl (if a PR was`,
      `opened, else null), stage (the final infer_*_stage value), error`,
      `(one-line reason when outcome=failed, else null).`,
    ].join("\n"),
    {
      label: `work:${item.ticketId}`,
      phase: "work",
      agentType: "general-purpose",
      model: "opus",          // R1 重活 inline 在此 worker，需 opus 等級推理
      schema: WORK_SCHEMA,    // 強制結構化回傳 —— 取代文字解析
      // isolation: 省略 —— ff 管線自己在 ../<ID> 開 worktree（沿用 §5.3 規則）
    },
  );
  return result; // 已被 WORK_SCHEMA 驗證的 object
}

// ── ui-tweak lane：apply/preview → 雙 judge → finisher（§5.0 消失點）──
// 關鍵：這三段都是 SCRIPT 直接 spawn 的 level-1 agent，所以 opus judge 可正常 spawn。
// 代價（誠實面對）：script 在這裡「重新實作了 /ui-tweak:ff walker 的一部分階段序」，
// 因為原本由 walker（infer_ui_stage）擁有的 apply→preview→audit→commit→pr 順序，
// 在 script 內被攤平成顯式步驟。我們把「會動 logic 的審查」忠實對齊 audit.md：
// 兩位 judge 都跑、tier 不可降級、必須 unanimous CLEAR。
async function runUiTweak(item) {
  log(`[ui] ${item.ticketId} apply+preview`);

  // 階段 1：apply（產生 UI-only diff）+ preview（build-only compile gate，
  // 對應 R20 direct-ship：--auto 不進裝置預覽）。仍走 /ui-tweak:apply 與
  // /ui-tweak:preview 的真實 stage，只是由 script 起一隻 agent 執行到 audit 前停。
  const prep = await agent(
    [
      `In the worktree ${item.worktreePath} for ticket ${item.ticketId}:`,
      `Run /ui-tweak:start then /ui-tweak:apply then /ui-tweak:preview`,
      `(direct-ship build-only gate, --auto semantics). STOP before`,
      `/ui-tweak:audit. Leave .dev/ui-tweak/base_ref and build-pass in place.`,
      `Return { ok: boolean, baseRef: string, error: string|null }.`,
    ].join("\n"),
    {
      label: `ui-prep:${item.ticketId}`, phase: "ui-judge",
      agentType: "general-purpose", model: "opus",
      schema: {
        type: "object", required: ["ok"], additionalProperties: false,
        properties: { ok: { type: "boolean" },
          baseRef: { type: ["string", "null"] },
          error: { type: ["string", "null"] } },
      },
    },
  );
  if (!prep.ok) {
    return { ticketId: item.ticketId, outcome: "failed",
             prUrl: null, stage: "ui:apply", error: prep.error };
  }

  // 階段 2：decorrelated dual-judge —— BOTH 必須 CLEAR（audit.md Step 2/3）。
  // 兩隻平行 spawn（parallel barrier）；tier 鎖死：sonnet vs opus，禁止降級。
  log(`[ui] ${item.ticketId} dual-judge (sonnet + opus)`);
  const judgePrompt = (lens) =>
    [
      `Audit the final cumulative diff (git diff ${prep.baseRef}) in`,
      `${item.worktreePath} for ticket ${item.ticketId}, ${lens} lens.`,
      `Read .dev/ui-tweak/figma-context.md and assert every WILL-EDIT row`,
      `is covered (a miss => BLOCKED). Return { status, reason }.`,
    ].join("\n");

  const [uiVerify, devReview] = await parallel([
    () => agent(judgePrompt("UI-only / visual"), {
      label: `ui-verify:${item.ticketId}`, phase: "ui-judge",
      agentType: "ui-verify-agent", model: "sonnet", schema: JUDGE_SCHEMA,
    }),
    () => agent(judgePrompt("behavior / logic, with structural pre-pass"), {
      label: `dev-review:${item.ticketId}`, phase: "ui-judge",
      agentType: "dev-reviewer", model: "opus", schema: JUDGE_SCHEMA,
    }),
  ]);

  // parallel 的失敗 thunk 回傳 null —— 對齊 audit.md「missing file / agent error => BLOCKED」
  const blocked =
    !uiVerify || uiVerify.status !== "CLEAR" ||
    !devReview || devReview.status !== "CLEAR";

  if (blocked) {
    const who = (!uiVerify || uiVerify.status !== "CLEAR") ? "ui-verify" : "dev-reviewer";
    const reason = ((!uiVerify || uiVerify.status !== "CLEAR")
      ? uiVerify?.reason : devReview?.reason) || "judge error";
    // --auto 是 loud-fail（audit.md §--auto）：不進 repair loop，revert 由 finisher 不執行，
    // 直接判 failed，dispatcher-dev-in-flight 留作 human-resume 訊號。
    log(`[ui] ${item.ticketId} BLOCKED by ${who}: ${reason}`);
    return { ticketId: item.ticketId, outcome: "failed",
             prUrl: null, stage: "ui:audit",
             error: `UI-TWEAK BLOCKED (${who}): ${reason}` };
  }

  // 階段 3：finisher —— commit → PR（audit CLEAR 後 walker 的 commit/pr/review）。
  log(`[ui] ${item.ticketId} CLEAR -> commit + PR`);
  const ship = await agent(
    [
      `In ${item.worktreePath} for ${item.ticketId}: audit is CLEAR`,
      `(.dev/ui-verify-pass.md Status: CLEAR). Run the remaining`,
      `/ui-tweak:ff stages: commit -> pr -> review (--auto). Open a draft PR.`,
      `Return { prUrl: string|null, stage: string, error: string|null }.`,
    ].join("\n"),
    {
      label: `ui-ship:${item.ticketId}`, phase: "ui-judge",
      agentType: "general-purpose", model: "opus",
      schema: {
        type: "object", required: ["stage"], additionalProperties: false,
        properties: { prUrl: { type: ["string", "null"] },
          stage: { type: "string" }, error: { type: ["string", "null"] } },
      },
    },
  );
  return {
    ticketId: item.ticketId,
    outcome: ship.error ? "failed" : "done",   // ui-tweak 無 port-paused
    prUrl: ship.prUrl, stage: ship.stage || "ui:review", error: ship.error,
  };
}

// ── per-ticket fallback stage（§6.2）：失敗 ticket「立即」寫 Linear，不等批次結尾 ──
// 由一隻 agent 做 Linear 寫入（script 本體無 MCP；agent 透過 ToolSearch 取得 session MCP）。
async function runFallback(res) {
  if (res.outcome !== "failed") return res; // done / port-paused 由 ship/ff 自身已寫好
  log(`[fallback] ${res.ticketId} -> post Linear failure comment (keep in-flight label)`);
  await agent(
    [
      `Ticket ${res.ticketId} failed in the dispatcher batch.`,
      `Via the Linear MCP (find it with ToolSearch): if no`,
      `<!-- ggx-work-error --> comment exists yet, post one summarizing:`,
      `"${(res.error || "pipeline failed").slice(0, 200)}".`,
      `DO NOT remove dispatcher-dev-in-flight / dispatcher-port-in-flight —`,
      `that label is the resume signal for the next sweep (§6.2 "any failed").`,
    ].join("\n"),
    { label: `fallback:${res.ticketId}`, phase: "fallback",
      agentType: "general-purpose", model: "sonnet" },
  );
  return res;
}

// ── 主編排：pipeline 做 per-item 串接（work -> [ui 分流] -> fallback），無批次 barrier ──
// 一張失敗的 ticket 其 fallback 立刻接在它後面跑（§6.2 即時性需求），
// 而非所有 work 跑完才統一 fallback。
const roster = args; // JSON array，由 markdown §4.3 傳入

const results = await pipeline(
  roster,
  // stage 1：依 uiTweak flag 分流。dev/port/bug → runWork；design bug → runUiTweak。
  (item) => (item.uiTweak ? runUiTweak(item) : runWork(item)),
  // stage 2：per-item fallback —— 立即性靠 pipeline 的 per-item 串接保證。
  (res) => runFallback(res),
);

// ── 最終彙總（§6.4 餵料）——回傳給呼叫 session 的 summary object ──
phase("aggregate");
const counts = results.reduce(
  (a, r) => ((a[r.outcome] = (a[r.outcome] || 0) + 1), a),
  { done: 0, "port-paused": 0, failed: 0 },
);
log(`[aggregate] done=${counts.done} port-paused=${counts["port-paused"]} failed=${counts.failed}`);

// script 的最終回傳值 = 呼叫 session 收到的結果；§6.4 直接用它 render 表格，
// 不必再逐張 get_issue（§6.2 的權威推導已被 WORK_SCHEMA + judge 結果取代）。
return { counts, rows: results };
```

### 3.1 這份 script 如何對應各 §

- **§6.1 wait loop 消失**：`pipeline()` 的回傳 `await` 自帶 barrier；`joined/N` 計數器
  由 `/workflows` 進度視圖（phase × agent count）取代。
- **§6.1 `[ggx-work-result]` 文字解析消失**：`WORK_SCHEMA` 用 forced StructuredOutput
  回傳已驗證 object，不再 `grep -oE '^\[ggx-work-result\]'`。
- **§5.0 inline 例外消失**：`runUiTweak` 內兩位 judge 是 script-spawned level-1 agent，
  opus judge 正常 spawn —— 不需要回主 session inline。
- **§6.2 即時 fallback**：`runFallback` 是 pipeline 第二個 per-item stage，失敗 ticket
  的 Linear 寫入緊接其 work 結果之後，不等全批。
- **§6.4 彙總**：`return { counts, rows }` 把表格資料結構化交回 markdown。

---

## 4. ui-tweak lane 兩個方案 + 推薦

### 方案 (a)（**推薦**）：worker 跑 apply/preview 後 RETURN，script 自己 spawn 兩位 judge + finisher
即上面 `runUiTweak` 的寫法。

- **優點**：徹底消除 §5.0 inline 例外；ui-tweak 與其他 lane 一樣是 script 編排的
  level-1 agent；opus judge 因 level-1 而能 spawn；judge tier-decorrelation
  （sonnet vs opus）完整保留，對齊 `audit.md` Step 2/3「BOTH must be CLEAR」。
- **誠實面對的耦合代價**：`/ui-tweak:ff` 的 walker（`infer_ui_stage`）原本擁有
  apply→preview→audit→commit→pr→review 的完整階段序與 marker 驅動 resume。
  方案 (a) 等於把「audit 這一刀」從 walker 裡抽出來、由 script 顯式編排
  （prep agent 停在 audit 前 → script spawn judges → finisher agent 接 commit/pr）。
  這是**部分重新實作 walker**：若 `audit.md` 的 judge 契約（讀 figma-context、
  WILL-EDIT 覆蓋斷言、loud-fail 語義）日後改版，script 與 `audit.md` 會有雙寫風險。
  緩解：script 的 judge prompt 明確引用 `audit.md` 的契約字句，並在 audit.md 加一行
  SYNC 註解指向本 script；或更保守 —— prep agent 一路跑到 audit *完成*、把 judge
  spawn 仍留在 worker 內（但這就退回 nested-opus 問題，不可行）。

### 方案 (b)：維持 §5.0 現狀（ui-tweak 仍 inline 在 markdown）
- script 只接管 dev/port/bug lane 的 fan-out；roster 中 `uiTweak:true` 的列
  *不*放進 `args`，由 markdown 在 `Workflow(...)` 之後沿用今天的 §5.0 inline 序列跑。
- **優點**：零耦合風險，`audit.md` walker 維持單一擁有者。
- **缺點**：§5.0 例外仍在，遷移沒解決 R5 觸發點之一（design-bug volume 成長仍是瓶頸）；
  且 markdown 同時要「啟動背景 workflow」又「inline 跑 ui-tweak」，主 session context
  仍被 ui-tweak 重活佔用（§5.0 現有缺點原樣保留）。

**推薦 (a)**，但**分階段落地**（見 §7 Phase B），先讓 dev/port lane 上 script、
ui-tweak 維持 (b) 一個 release，待 (a) 的 judge 重編排在 dummy ticket 驗證 unanimous-CLEAR
與 BLOCKED loud-fail 行為一致後，才切 (a)。

---

## 5. §4.2 mid-batch crash recovery → resumeFromRunId 映射

### 5.1 兩種「中途失敗」要分清楚
- **§4.2 是 lock 階段失敗**（spawn *之前*）：發生在 markdown，script 還沒啟動 →
  與 Workflow 無關，維持現狀（rollback fresh-lane label + batch-abort Slack + STOP）。
- **本節要解的是 spawn *之後* 的 crash**：script 跑到一半、session 中斷。

### 5.2 resume 機制
Workflow 的 crash-recovery 故事 = `Workflow({ scriptPath, resumeFromRunId })`：
未變動的 `(prompt, opts)` 前綴回傳 cached 結果，第一個變動的 `agent()` 起往後重跑。
因為 `runWork` 的 prompt 由 `item.ticketId` 決定、roster 不變、且 script 內無
`Date.now()`/`Math.random()`（會 throw），所以同一 roster + 同一 script 的 resume
具決定性：**已完成的 ticket agent 直接回 cached structured result，未完成的接著跑。**

**重要限制（誠實標註）**：官方文件明言 *resume 只在同一個 Claude Code session 內有效*；
「If you exit Claude Code while a workflow is running, the next session starts the workflow
fresh.」這與 §4.2 期望的「跨 session 用 in-flight label 重撿」**不完全等價**。

### 5.3 markdown 要持久化什麼、放哪
新增一個 run 檔（不放進現有 `.lock`，避免污染 lock 語義）：

```
claude-reports/dispatcher/<RUN_TS>-<PID>.run.json
  { runId, scriptPath, roster, startedAt }   ← roster 是傳給 script 的同一份 args
```

- 同 session resume：markdown 讀此檔 → `Workflow({ scriptPath, resumeFromRunId: runId })`。
- 跨 session（exit 後）：Workflow 無法 resume → **退回今天的 label-based recovery**：
  下次 `/ggx-dispatcher` sweep 時 Q2/Q4 仍靠 `dispatcher-*-in-flight` 撿回未完成 ticket，
  重新 lock → 重新起一支全新 workflow run。**結論：in-flight label 仍是跨 session 的
  權威 resume 訊號；resumeFromRunId 只是同 session 的快取最佳化，不取代 label 機制。**
- 放 `claude-reports/dispatcher/` 而非 `.lock`：與 §6.3/§6.4 報告同目錄，post-mortem 易找；
  且 §6.5 release lock 時可一併清掉（或保留作 audit）。

---

## 6. Permissions（開放風險 + 緩解）

今天 §5.3 spawn 用 `mode:"bypassPermissions"`，且 Guardrail 明令「Never use
`mode != "bypassPermissions"`」（互動 permission prompt 會卡死整批背景 agent）。

**Workflow `agent()` 沒有文件化的 `mode` 參數** —— 這是真實風險。但官方 workflows 文件
給了明確語義：

> 「The subagents the workflow spawns **always run in `acceptEdits` mode** and inherit
> your **tool allowlist**, regardless of your session's mode. File edits are auto-approved.
> Shell commands, web fetches, and MCP tools that aren't in your allowlist can still
> prompt you mid-run.」
> 且「No mid-run user input」—— 背景 run 中沒人能回答 prompt。

含義：
- file edit 自動批准（acceptEdits）→ `/dev:apply` 的程式碼寫入不會卡。
- **但 shell / MCP / WebFetch 若不在 allowlist 會 mid-run prompt**，而 workflow 又
  「no mid-run user input」→ 等於**靜默卡住**（最壞情況）。這正是今天 bypassPermissions
  在規避的。`/ggx-work` 內大量 `git`/`gh`/Linear MCP 呼叫，全部命中此風險。

**緩解（兩個，建議都做）**：
1. **預先 allowlist**：用 `/fewer-permission-prompts` pattern，掃描歷史 transcript 把
   `/ggx-work`/ff 管線實際用到的 `git`、`gh pr *`、`mcp__claude_ai_Linear__*` 等加進
   `.claude/settings.json` 的 permission allowlist。這是 workflow agent 唯一能繞過
   mid-run prompt 的官方途徑。
2. **以 bypassPermissions 跑 dispatcher session 本身**：官方表格指出在
   「Bypass permissions / `claude -p` / Agent SDK」模式下，workflow 啟動**不提示**、
   且 `-p`/SDK「tool calls follow your configured permission rules without interactive
   confirmation」。亦即在 headless / bypass session 啟動 workflow，最接近今天
   bypassPermissions 的行為。**注意**：互動 CLI session 的 bypass 只影響「啟動提示」，
   不改 workflow agent 一律 acceptEdits + allowlist 的事實 → 所以緩解 1（allowlist）
   仍是必要的，緩解 2 只解決啟動提示與 headless 場景。

**Go 前置條件**：在 dummy ticket e2e 中確認 allowlist 覆蓋到 ff 管線所有 shell/MCP 呼叫，
否則背景 workflow 會在第一個未授權呼叫靜默卡死 —— 這是遷移最大的單點風險。

---

## 7. 風險表（含緩解）

| 風險 | 說明 | 緩解 |
|---|---|---|
| **Permissions 靜默卡死** | workflow agent 一律 acceptEdits + 繼承 allowlist，未授權 shell/MCP/WebFetch 會 mid-run prompt 但無人可答 → 卡死整支 run。 | (1) `/fewer-permission-prompts` 預建 allowlist 覆蓋 ff 全部 `git`/`gh`/Linear MCP 呼叫；(2) 以 bypass / `-p` session 啟動；e2e 必驗。 |
| **MCP via ToolSearch** | script 本體無 MCP；agent 要靠 ToolSearch 才拿得到 session MCP（Linear/gh）。fallback agent 的 Linear 寫入依賴它。 | prompt 明確指示 agent「find the Linear MCP with ToolSearch」；e2e 驗證 fallback agent 真的能 `save_comment`。 |
| **worktree 衝突** | ff 管線在 `../<ID>` 自建 worktree；script `agent()` 的 `isolation:'worktree'` 會再包一層 → 衝突 checkout。 | **省略** `isolation`（對齊今天 §5.3「omit isolation」Guardrail）。roster 已帶 `worktreePath`，agent 直接 cd 進去。 |
| **context（script vs agent）** | 好處：中間結果留在 script 變數，主 session context 只收 summary。風險：方案 (a) 把 raw diff 留在 judge agent context（與今天相同，未惡化）。 | judge agent read-once；prep/finisher 各自獨立 agent，重活不疊在同一 context。 |
| **resume 限制** | resumeFromRunId 只在同 session 有效；exit 後重啟一律 fresh。 | 跨 session 仍靠 `dispatcher-*-in-flight` label（§5.3）；run.json 只作同 session 最佳化。 |
| **ui-tweak walker 雙寫** | 方案 (a) 把 audit 階段序從 `infer_ui_stage` walker 抽到 script，audit.md 契約改版會雙寫。 | script judge prompt 引用 audit.md 契約字句 + 互加 SYNC 註解；Phase B 才切 (a)。 |
| **concurrency cap** | workflow 上限 min(16, cores-2)；今天 `--max-parallel` 預設 10、硬上限 20。 | roster 超過 cap 時 pipeline 自動排隊；把 `--max-parallel` 上限對齊 workflow cap（≤16），避免使用者預期落差。 |
| **1000 agents lifetime** | 每張 ui-tweak ticket 起 prep+2judge+finisher=4 agent；大批量會逼近 lifetime cap。 | 單次 run 遠低於 1000；但巨批量分多次 sweep（dispatcher 本來就 cap N）。 |
| **Slack digest 時序** | §6.5 digest 若放 script 末段 agent，主 session 需等 workflow 完成才知道；放 markdown 則需等 summary 回來。 | **建議放 markdown**：workflow 完成通知 → markdown 收 summary → release lock → 一次 `/_slack-notify digest`。保留 §4.2 batch-abort 仍在 markdown（spawn 前）。 |

---

## 8. 遷移三階段計畫（各含 e2e 驗證 / 回滾）

### Phase A — script 並存，`--workflow` flag opt-in（只接管 dev/port/bug lane）
- markdown 新增 `--workflow` flag：設定時，§5.3 不 spawn N×Agent，改 build JSON roster
  （**排除 uiTweak 列**）→ 寫 run.json → `Workflow({scriptPath, args})`；ui-tweak 列仍走
  §5.0 inline（方案 (b)）。未設 flag → 完全走今天路徑。
- script 只含 `runWork` + `runFallback` + aggregate（先不含 `runUiTweak`）。
- **e2e recipe**：建 2-3 張 dummy ticket（1 feature `ready-to-dev`、1 port `ready-to-port`、
  1 故意會失敗的 bug）。`/ggx-dispatcher --test --workflow --max-parallel:3`。
  驗證：(i) 三隻 work agent 平行起、(ii) feature → done + draft PR、(iii) port → port-paused
  + `need-spec-review`、(iv) 失敗 bug → runFallback 立即貼 `ggx-work-error` 且保留 in-flight、
  (v) summary counts 正確、(vi) §6.4 表格與今天非-workflow 路徑逐欄一致。
- **rollback**：拿掉 `--workflow` flag —— 零 code 改動即回今天行為。

### Phase B — ui-tweak lane 改走 script judges（切方案 (a)）
- script 加入 `runUiTweak`；markdown 把 uiTweak 列也放進 roster（不再 inline）。
- **e2e recipe**：建 1 張 `design bug` dummy（純 UI 改，預期 CLEAR）+ 1 張會動 logic 的
  `design bug`（預期 BLOCKED）。`/ggx-dispatcher --test --workflow`。驗證：(i) CLEAR 張
  →兩 judge 都 CLEAR→commit+draft PR→done、(ii) BLOCKED 張→loud-fail→failed→revert→
  in-flight 保留、(iii) opus judge（dev-reviewer）確實在 script-spawned context 成功 spawn
  （這是消除 §5.0 的關鍵證明）、(iv) tier 未被降級（log 顯示 sonnet+opus）。
- **rollback**：roster 改回排除 uiTweak 列 → ui-tweak 退回 §5.0 inline（Phase A 行為）。

### Phase C — 預設翻轉 + 移除 §5.0/§6.1
- `--workflow` 成預設；保留 `--no-workflow` 逃生門一個 release。
- markdown 刪除 §5.0 inline lane、§6.1 wait loop + joined 計數、§6.2 逐張 get_issue 重算
  （改吃 script summary）。§4 lock / §6.5 lock release / Slack 留 markdown。
- **e2e recipe**：混合 dummy（feature + port + design-bug-clear + design-bug-blocked + 失敗 bug），
  不帶任何 flag。全跑一遍驗證與 Phase A/B 行為一致；再做一次「中途 Ctrl-C → 同 session
  re-invoke」驗證 resumeFromRunId 快取已完成 ticket；再「exit 後重開」驗證 fresh-start +
  Q2/Q4 label 重撿。
- **rollback**：`--no-workflow` 一個 release 內仍可回退；之後移除。

---

## 9. Go / No-Go 建議（綁 R5 觸發條件）

R5 觸發條件：**(1) 官方 docs 改變 nesting 限制、(2) sonnet nesting 真的壞掉、
(3) inline design-bug / figma / align lane 大到成為瓶頸。**

| 觸發 | 現況 | 建議 |
|---|---|---|
| batch > 10 / design-bug volume 成長 | 今天 §5.0 design bug 在主 session 序列跑，與背景批次搶 context；volume 上升即瓶頸（觸發 3）。 | **GO（Phase A 先行，Phase B 解此痛）**：script 把 design bug 變平行 level-1，瓶頸消失。 |
| sonnet nesting 壞掉 | R2 的 `/dev:figma`/`/dev:align`/`/dev:verify`/`/port:plan` 仍靠 level-2 sonnet。 | **GO 且升級為高優先**：遷到 script 後這些可改由 script spawn（level-1），sonnet 不再 nest。本提案先處理 fan-out 層；R2 stage 的 level-1 化是後續工作。 |
| 官方放寬 nesting | 尚未發生。 | 不影響本案決定（本案不依賴 nesting 放寬，反而靠 script-spawn 繞過）。 |

**整體建議：GO，但分階段、保守。**
- 立刻可做 **Phase A**（低風險、`--workflow` opt-in、零回滾成本），先拿到「script 變數
  持中間結果、主 session context 乾淨、`/workflows` 進度視圖」的好處。
- **Phase B（切 ui-tweak 方案 a）是本遷移的真正價值點**（消除 §5.0），但需先在 dummy
  ticket 證明 opus judge 在 script-spawned context 可靠 spawn + tier 未降級 + BLOCKED loud-fail
  行為一致，才切。
- **No-Go 的唯一硬條件**：若 Phase A e2e 顯示 permission allowlist 無法覆蓋 ff 管線全部
  shell/MCP 呼叫、背景 workflow 會靜默卡死（§6 風險），則暫緩到該風險有解（擴充 allowlist
  或以 `-p`/SDK headless 啟動驗證通過）為止。

### 什麼**不會**改善（誠實）
- **token 成本不變**：agent 數量大致相同（dev/port lane 一張一隻；ui-tweak 反而從
  「1 worker 內含 judge」變「prep+2judge+finisher=4 隻」，略增）。Workflow 不省 token。
- **重活仍 inline 在 worker（R1 不變）**：`/dev:apply --auto`→`/opsx:apply`、
  `/code-review`、`/port:explore`/`/port:synth` 仍在 level-1 worker 內 inline，
  因為 worker 仍是「會自己做重活的 agent」，不是純編排。遷移只改「誰 spawn worker」，
  不改 worker 內部。
- **Slack digest 仍是結尾一次 agent 呼叫**（建議留 markdown 發），數量不變。
- **R2/R3 的 level-2 sonnet spawn 不在本案範圍**：本案只搬 fan-out 層；ff 管線內部
  stage 的 spawn-shape 維持現狀（後續可另案 level-1 化）。

---

*文件結束。本文件僅為設計提案，未修改 repo 任何檔案。*
