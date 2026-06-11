# /ggx-standby + /ggx-pr-resolver — 開工前審查報告 (PRE-CONSTRUCTION REVIEW)

審查對象:
- `plans/ggx-standby.md` (計畫，狀態 APPROVED FOR CONSTRUCTION)
- `commands/dev/ggx-standby.md` (loop 編排器 spec)
- `commands/dev/ggx-pr-resolver.md` (per-PR worker spec)

硬約束:決策日誌 D1–D10 與 edge-case 修正 E-1..E-8 已由 owner 拍板，本審查不重新辯論;範圍僅限「開工準備度」——spec 與既有 command/skill 檔案的現實是否吻合、哪些地方規格不足、§6 清單漏掉哪些整合工作、施工者會被什麼絆住。

---

## 1. 總評 — 可否開工

**結論:架構方向正確,但尚未達到「可直接開工」的狀態。先解決 3 個 blocker 與 1 組 owner 拍板問題,再進場。**

整體設計(dynamic `/loop` 雙腿架構、背景 spawn 重活、no-lock-probe、E-1..E-8 邊界處理)在審查中經得起對抗式驗證——沒有任何 blocker/major 是在挑戰已拍板的 D/E 決策,而是集中在「決策如何落地」的規格缺口。`/ggx-pr-resolver` 這支新 worker 是風險集中區:它宣稱「組合 `/resolve-conflict` single-mode + `/resolve-pr-comments`」,但實際上需要一個兩種既有模式都不提供的 hybrid(single 的 no-push/no-interactive 一半、batch 的 worktree-scoped/auto-abort/per-strategy 一半)。E-3 skip-set 的關鍵依賴(dispatcher 的 claimed set)在它最需要的整個 `chain.running` 視窗內都不可讀。這三點是必須在動手前釘死的硬阻塞。

majors 大多是「§6 清單一行字底下藏著一整塊未規格化的整合工作」(Slack 第三 digest source、inline-PR-comment 全新能力、build-sanity abort 無回報狀態、reused-worktree 失效、release-branch base、Linear pre-flight 不存在)。這些不會讓架構崩,但會讓施工者在實作時撞牆並自行發明行為,導致兩個施工者做出不同的東西。

minor/info 多為文字對齊與防禦性建議,可在施工中順手處理。

建議:owner 先回覆下方 §4 的開放問題(尤其 Q1 cadence 機制、Q4 skip-set key、Q5 strategy pin、Q6 HOLD authority),施工者據此修補 §6 清單後即可開工。

---

## 2. 🔴 開工前必須解決 (BLOCKERS)

### B1 — E-3 skip-set 不可建構:dispatcher 的 claimed set 全程不可讀

**合流自 lens:** `integration-gaps:1` + `F1-skipset-timing-blocker`(兩個獨立 lens 指向同一缺陷,信心高)。

**問題:** §6 item 5 假設「dispatcher 已經印出 dispatch plan → 確保它落進 standby 讀的 report file」即可。但:
- DISPATCH_ROSTER(claimed ticket+branch+worktree 集合)明確「只存在 session state、**不寫 roster.tsv**」(`ggx-dispatcher.md:448`)。
- 唯一含 claimed set 的磁碟檔 `claude-reports/dispatcher/<RUN_TS>-<PID>.md` 在 §6.4 (`ggx-dispatcher.md:755`) 才寫,而 §6.4 在 §6.1 join barrier 完成後才執行(`ggx-dispatcher.md:597`)。barrier 會 block 數十分鐘。
- §4.3 的 dispatch table 雖早印,但印到的是 spawned dispatcher subagent 的 stdout,不是 standby session(dispatcher 是 `run_in_background`,`ggx-standby.md:35`)。

因此在**整個 `chain.running` 視窗**——正是 `ggx-standby.md:87` 需要 skip-set 避免 Leg-2 resolver 走進 `/dev:ff` 還在寫的 worktree 的時候——磁碟上沒有可讀的 claimed set。等 report 出現,`chain.running` 已翻 false,skip-set 失去意義。

**緩解但不可依賴的旁證:** resolver 自己的 ownership guard(`ggx-pr-resolver.md:27`)會跳過帶 `dispatcher-*-in-flight` label 的 ticket,label 在 §4.1 lock 即早寫。但 `ggx-standby.md:87` **明文禁止依賴它**(「`/dev:ship` 可能在 sweep 中途移除 label,resolver 會走進 `/dev:ff` 還在寫的 worktree」)——這正是 E-3 存在的理由。所以 label 路徑無法滿足 E-3。

**建議修法(§6 item 5 改寫為 dispatcher spec 變更):** 在 dispatcher §4.1 lock 之後、§5.3 spawn 之前,新增一個**早期持久化寫入**到確定性路徑(如 `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv`,內含 `<ticket>\t<headRefName>\t<worktree-path>`),並把 `ggx-standby.md:87` 指向該檔。skip-set 必須以 **branch name (headRefName)** 為 key(見 B3)。

---

### B2 — resolver step 4 用了錯的 worktree primitive(`/add-worktree`),且 step 5 single-mode 在無人值守下會卡死

**合流自 lens:** `pr-resolver-correctness:add-worktree-wrong-primitive` + `pr-resolver-correctness:single-mode-is-interactive-not-autonomous` + `integration-gaps:5/6` + `RC-4/RC-5` + `failure-modes:2`(六個 lens 收斂,最高信心)。這是兩個技術上不同但同源的缺陷——resolver 借用了 `/resolve-conflict` 的錯誤一半。

**B2a — worktree primitive 錯誤(`ggx-pr-resolver.md:37`):** step 4 說「`enter ../<TICKET-ID>; create via /add-worktree if missing`」。`/add-worktree` 預設 `git worktree add -b <BRANCH> <path> origin/trunk`(`add-worktree.md:73`,基於最新 trunk),其唯一的 remote-tracking 路徑(`:75`)還卡在**互動式提問**(`:63` "ask the user — track the remote branch, or abort?"),無人值守的 `--batch` 答不了。它也用衍生的 `<type>/<ticket-id>`(`:52`)而非 PR 的真實 headRefName,且**完全無 fork-PR 路徑**。正確 primitive 是 `resolve-conflict.md:264-272` 的 batch B3 邏輯:list worktrees → 以 `refs/heads/<headRefName>` 比對重用 → 否則 raw `git worktree add <path> <headRefName>`,fork 走 `worktree add --detach` + `gh pr checkout`(`:271`),並有 dirty guard(`:272`)。

**B2b — single-mode 是 HITL,但 step 5 期待 batch 的非互動 give-up(`ggx-pr-resolver.md:38-39`):** step 5 說「run the `/resolve-conflict` single-mode procedure ... NO push yet」並斷言「Conflict cannot be auto-resolved → STOP, report `needs-human: conflict`」。但 single mode 是互動路徑:`resolve-conflict.md:98,111`(if in doubt, ask the user via AskUserQuestion)、`:169`(never `--abort` without asking)、`:170`(>5 rounds → pause and ask)。非互動 auto-abort 只存在於 **batch mode**(`:278-281`)。Leg-2 在 `run_in_background` 中跑 `/ggx-pr-resolver --batch --auto`(`ggx-standby.md:87`),衝突時 AskUserQuestion 無人回答 → background subagent 卡死,違反「wake cycle 恆在 ~1-2 分鐘內完成」(`ggx-standby.md:35`)。

resolver 真正需要的是一個**兩種模式都不提供的 hybrid**:worktree-scoped + 非互動 auto-abort + **不 push**(push 延到 step 7)。

**建議修法(新增 §6 resolve-conflict 變更項——目前 §6 完全沒有 resolve-conflict 項):** 二擇一——
(a) 為 `/resolve-conflict` 新增「worktree single, no-push, non-interactive auto-abort」變體;或
(b) 在 `/ggx-pr-resolver` 內 inline 重寫 rebase/resolve loop,直接採用 batch 的 worktree-selection(B3)+ 非互動 abort 語意 + step 7 自己 push。
同時 step 4 必須改用 raw `git worktree add <path> <headRefName>` reuse-or-create,並補 fork 路徑與非互動 dirty guard。

---

### B3 — skip-set key 不一致:resolver 以 PR# 為 key,standby 以 ticket/branch 為 key;pre-PR ticket 根本沒有 PR#

**合流自 lens:** `pr-resolver-correctness:skip-set-key-mismatch`(與 B1 互補:B1 是「何時可讀」,B3 是「以什麼為 key」)。

**問題:** `ggx-pr-resolver.md:48` 說 skip-set 是「PR numbers」。但 `ggx-standby.md:87` 的 E-3 skip-set 來自 dispatcher 的 claimed set,那是 ticket+branch+worktree roster(`DISPATCH_ROSTER: <ticket>\t<lane>\t<worktree>\t<url>`,**無 PR#**)。對 fresh ready-to-dev ticket,chain 正在 pipeline 中,**PR 還沒開**(PR 在 `/dev:ship` 才開)。所以 standby 最需要排除的東西(`/dev:ff` 正在寫的 worktree)根本沒有 PR# 可放進 PR-keyed skip-set。反過來 resolver 以 `gh pr list` 列舉(headRefName),也不以 ticket 為 key。

**建議修法:** 把 skip-set 定義為 **BRANCH NAME (headRefName)** 集合——這是 dispatcher roster(branch,B1 早寫入)與 resolver `gh pr list` 輸出(headRefName)唯一共同的 identifier。resolver step 3a/4 改以 branch set 比對,而非 PR#。改 `ggx-pr-resolver.md:48` 與 §6 item 5「Dispatcher claimed-set exposure」(目前寫「tickets/branches」但 roster 無 branch 欄位——需一併在 roster 暴露 headRefName)。注:plan 與 standby 兩份 artifact 的 E-3 本就寫「branches」,本修法與其一致。

> **B1+B3 為同一條施工線:** 在 dispatcher 早寫一個以 headRefName 為 key 的 inflight 檔,standby 與 resolver 都讀它。

---

## 3. 🟡 施工清單需補充/修正的項目 (MAJORS → 對應 §6)

每項標註對 plan §6 清單是「新增項」或「修正既有項」。

### M1 — Slack 需要設計**第三個 digest source**,不是「加一列」 → §6 修正(`_slack-notify` 項)
**合流:** `integration-gaps:2` + `SN-1` + `SN-2`。
`_slack-notify` 不是單表加 source 欄,每個 source 有自己的 Input schema、mapping rows、Block Kit render branch(`_slack-notify.md:28-29,36-63,132-142,161-195`)。standby payload(red CI / resolver reports / needs-human PRs / analyzer verdict CHANGES,`ggx-standby.md:99`)是 **PR-keyed**,既有兩個 source 全是 ticket-id keyed,完全不符。「加一列」實為:新 Shapes 條目 + 全新 PR-keyed Input grammar + 新 mapping rows + 新 render branch + caller registry 第 4 條(`_slack-notify.md:261-271`)。施工者必須端到端設計。**緩解:** `.ggx-standby/digest.md` 持久 fallback 不受影響,且 Slack 未設定時為 silent no-op,所以此項不 gate 功能,僅 gate Slack 表面。
**§6 動作:** 把「add a standby row」改寫為「設計 standby digest source(Shapes/Inputs(PR-keyed)/mapping/render/caller registry)」並列出五個子任務。

### M2 — inline-PR-comment 是全新能力,不是 code-review「addition」 → §6 修正 (item a)
**合流:** `integration-gaps:3` + `CR-5`。
code-review 今天**零** line-anchored 能力,唯一 posting 是整段 issue comment(`code-review.md:74-77,194-199`)。inline review 需:解析 `filename:line` → 映射到 diff position/line+side → 批次成一個 `event=COMMENT` review → 處理 hunk 外行(API 會拒)。且 `--auto` 今天**明文禁止任何 comment posting**(`code-review.md:61,79` "no PR-comment side-effect",因 `/dev:review` 消費 report),standby 用 `--auto` 卻要 inline post → 需新 flag/signal 開特例**而不可回歸 `/dev:review`**。
**§6 動作:** item (a) 展開機制(gh api reviews payload 細節 + `--auto` 例外條款),並澄清 standby 走 inline 路徑(`code-review.md:52` inline 執行,故 agent 檔不在執行路徑,僅其 Step-6 輸出格式被 inline)。

### M3 — `resolve-pr-comments` build-sanity gate 是第二個 `--auto` 不繞過的 abort,resolver 無回報狀態 → §6 新增 (`ggx-pr-resolver` 項)
**合流:** `pr-resolver-correctness:build-sanity-abort-no-report-state` + `integration-gaps:4` + `F1`。
5b 在 FIX 後 check-test 失敗時 abort **整個 skill**:不 reply、不 resolve、不 push、tree 留 dirty(`SKILL.md:135-147`)。`--auto` 只繞過 strategy-table prompt(§6 item),**不繞過**這個 real-build-failure abort。resolver step 6 只說「gate still runs」,step 8 report enum(`ggx-pr-resolver.md:42`)**無 build-sanity-failed 狀態**。結果:step 5 已做 rebase commit、5b abort 前留下未 commit 的 FIX edits、step 7 不知該 push rebase-only commit(靜默丟掉失敗的 fix)還是不 push、且無 report 狀態可上報。
**§6 動作:** 定義明確 outcome:`needs-human: comment-fix-failed-tests`,規定已套用的 rebase commit 之處置(push rebase commit only 或都不 push),保留 dirty worktree 供檢視。

### M4 — reused stale worktree 被盲目重用,E-3 skip-set 蓋不到上一輪殘留 → §6 新增 (`ggx-pr-resolver` 項)
**lens:** `failure-modes:2`。
dispatcher §6 cleanup 是人工 `/remove-worktree`(`resolve-conflict.md:314`),故 `../CAF-123` 常跨 run 殘留。resolver 重用它(`resolve-conflict.md:265`),但 single-mode 只 fetch base(`resolve-conflict.md:78/84`),**不 fetch/reset PR head**。重用的 worktree 其 `origin/<headRef>` ref 停在舊值 → step 7 `--force-with-lease` 以**舊 lease** 比對 → 期間若有 head 上的新 commit(人或前一輪 resolver/review push),lease **通過並 clobber**(force-with-lease 只防它 fet 過的變更,head 從沒 fetch)。靜默資料遺失。skip-set 蓋不到此情形,因 `chain.running==false`(前一輪已完成)。
**§6 動作:** resolver 重用 worktree 前須 (i) `git fetch origin <headRef>` 並 verify/reset 到 `origin/<headRef>` 再 rebase,使 lease 對最新 remote;(ii) 套用**非互動** dirty guard(batch 語意,回 `worktree-dirty`/`needs-human`),不沿用 single-mode 的 HITL `/check-clean`(會卡死無人值守)。

### M5 — release-branch PR 會被 rebase 到錯誤 base → §6 新增 (resolve-conflict 項)
**合流:** `failure-modes:12`(+ 與 `integration-gaps:7`/`RC-3` strategy 議題相鄰)。
step 5 invoke single-mode,single-mode **hardcode base = origin/trunk**(`resolve-conflict.md:40`);只有 batch 設 per-PR baseRef(`:41`)。resolver step 1 已 fetch baseRef(`ggx-pr-resolver.md:26`)卻沒往下傳,single mode 也無 arg 接收。target release branch 的 PR 會被 rebase 到 trunk → PR corruption(且 step 7 force-with-lease 推送壞 history)。**佐證團隊已知此 hazard:** plan 已為 code-review leg 加了「diff against baseRef not trunk」修正(§6 item b),卻漏了 resolver 的 rebase leg。
**§6 動作:** 擴充 `/resolve-conflict` single mode 接受 explicit base,或 resolver invoke 前 pin baseRef。(此項與 Q5 strategy pin 連動——若 pin REBASE-only 仍需傳 base。)

### M6 — judge verdict (ACT/HANDLED/HOLD) 沒傳進 `resolve-pr-comments`,skill 可自行 auto-resolve 一個 judge 標 HOLD 的 thread → §6 新增 / 需 Q6 拍板
**合流:** `pr-resolver-correctness:judge-verdict-not-passed-to-skill`(對應 Q6/Q12)。
step 3b judge 把每個 thread 分 ACT/HANDLED/HOLD,step 34 說 HOLD「triggers no work」。但 step 6 invoke skill **不傳 verdict**,skill 跑自己的 FIX/REPLY/STALE/DEFER classifier 並 auto-resolve STALE(`SKILL.md:179-190`),`--auto` 下無 HITL 攔截。HOLD 的定義包含「code no longer exists」(`ggx-pr-resolver.md:33`),正好 overlap skill 的 STALE trigger(`SKILL.md:93,98`)→ judge 標 HOLD 的 thread 被無人值守 auto-resolve,正是 HOLD 要防的。**緩解:** 危險的 human-only HOLD(wontfix/設計辯論)map 到 skill 的 REPLY/DEFER(留 open,不 auto-resolve),只有與 STALE overlap 的 HOLD 被 auto-resolve(通常無害),故 major 而非 blocker。
**§6 動作:** 二擇一(見 Q6):傳 ACT allowlist / HOLD blocklist 進 skill(新 skill input),或宣告 skill classifier 為權威、HOLD 僅 advisory(並修正 step 33「triggers no work」措辭)。

### M7 — standby step 1 宣稱重用 dispatcher 的「Linear auth」pre-flight,但該檢查不存在 → §6 新增 (standby 項)
**lens:** `F4-linear-auth-preflight-major`(+ `integration-gaps:14` 的 Linear 半部)。
`ggx-standby.md:42` 說 step 1 跑 dispatcher pre-flight「main worktree, default branch, clean tree, **gh + Linear auth**」。dispatcher Step 1(`ggx-dispatcher.md:139-219`)只有 lockfile/worktree/branch/clean/prune/gh auth(`:212`)/open-PR count——**無 Linear MCP probe**(Linear 只是 Prerequisite 標題 `:11`,prefix lazy 解析 `:106`)。施工者去 dispatcher 找 Linear pre-flight 會撲空。且 dispatcher Step 1 的 lockfile 步驟(`:141-155`)standby 明文不可碰(`ggx-standby.md:36`)→「run dispatcher pre-flight」只能是 SUBSET(skip lockfile),此邊界未說明。**緩解:** standby 自己的 Prerequisite 已宣告需 Linear MCP,任何 Linear 不通會在第一個 Leg-1 cycle 顯現(被「leg failure never ends loop」接住),為延遲偵測而非靜默死亡,故 major。
**§6 動作:** (1) 修正 `:42` 不實的「Linear auth」措辭,在 standby step 1 加自己的 Linear MCP reachability probe(一次 `list_teams`);(2) 列出 standby 重用的 pre-flight 確切 subset,明確排除 lockfile。

> **註(failure-modes:3 / state.json 寫入機制):** 此項被驗證為 **minor**(見 §6 附錄 m13),非 major——因 maps 由 live PR# keyed、量小,且 RECONCILE/restart 自癒。仍建議在 §6 加「deterministic state-write contract」一行。

---

## 4. ❓ 需要 owner 拍板的開放問題

> 已去重;只保留真正需要 owner 決策、無法從檔案推導者。每題附具體選項。

**Q1 — Cadence 機制歸屬。** spec 同時用 no-interval `/loop` 啟動 **且** 規定具體 ScheduleWakeup 時長(1800s heartbeat / ~3600s quiet / 240-270s short-poll)。哪個機制權威?
  - (A) harness `ScheduleWakeup`:standby 每次計算並設定下次 wakeup,則「絕不選 ~300s」等規則可強制執行。
  - (B) `/loop` 自我 pacing:model 自行決定何時重跑,則上述秒數規則只是建議。
  *影響 §5 cadence 規則是否可實作如寫。*

**Q2 — 8h+ session 的 context 成長界線。** 要具體 auto-restart/auto-compact trigger(N 次 wake 或 token 門檻),還是維持「覺得重就手動重啟」的 operator-discretion?(對應 `loop-architecture:7`)
  - (A) 具體 trigger(可建構);(B) 手動(現狀,「feels heavy」不可建構但為刻意 UX)。

**Q3 — skip-set 的 canonical key。** 確認採 **branch name (headRefName)**(B3 建議),接受 pre-PR chain ticket 在 PR 存在前即以 branch 排除?spec 現寫「PR numbers」無法覆蓋 pre-PR worktree,需 owner 裁定 key。

**Q4 — resolver strategy pin。** ggx-pr-resolver 永遠 rebase(使 step 7 無條件 `--force-with-lease` 正確、符合「fixes on top of latest base」前提),還是支援 per-PR `--merge` 並 mirror batch 的 per-strategy push?(對應 `integration-gaps:7`/`RC-3`)D3 寫「real rebase + push」似暗示 rebase-always——請確認以便 step 5 pin。
  - (A) REBASE-only(最簡,本審查推薦);(B) 雙策略 + per-strategy push(較彈性,規格面更大)。

**Q5 — HOLD authority(對應 M6)。** judge 標 HOLD 但 skill classifier 會 auto-resolve 為 STALE 時,誰贏?
  - (A) 傳 judge HOLD set 進 skill 當 blocklist(新 skill input);(B) 宣告 skill classifier 權威、HOLD 僅 advisory(接受 `--auto` 下 HOLD thread 可被 auto-resolve,無 human gate)。

**Q6 — single-push 歸屬。** `resolve-pr-comments` 不 push(resolver 永遠擁有唯一一次 push),還是 skill 自己 `--force-with-lease` push 而 resolver 抑制自己的?兩者都達成 one CI run——挑一個讓 §6 skill edit 實作。

**Q7 — local-suite 成本(對應 `pr-resolver-correctness:double-format-checktest`,info)。** 既 rebase 又有 comment-fix 的 PR 會跑 format+check-test 兩次(rebase stage 一次、5b 一次)。接受此 deliberate tradeoff,還是 resolver 在已知 comments stage 會重測時跳過 rebase-stage 測試?(注:5b check-test 為 incremental、僅涵蓋 FIX 檔,worst case 是 Flutter full suite + incremental。)

**Q8 — Slack digest 重複(對應 `integration-gaps:15`/`TA-9`)。** Leg-1 背景 chain 內 `ticket-analyze` Step 10.1 與 `ggx-dispatcher` §6.5 會各自發 built-in digest(verdict-state based,刻意 re-announce stuck tickets),與 standby Finalize 的 change-only digest 衝突。
  - (A) 在 standby chain 中**抑制** analyzer/dispatcher 的 built-in digest(只留 standby change-only);(B) 保留 per-run digest,standby Finalize **drop** analyzer/dispatcher verdicts。
  *純通知噪音偏好,只有 owner 能定。*

**Q9 — code-review SHA-pinning vs live-HEAD(對應 `integration-gaps:9`)。** E-7「只 review settled SHA」目前無機制,code-review remote mode review 的是 call-time live HEAD(`gh pr diff`)。
  - (A) 接受 best-effort live-HEAD(reviewed_shas 變近似,race 可能 review 略舊/略新 HEAD,自癒);(B) 硬性精確 pin,為 code-review + delegated agent 加 `--sha`/`--base..--head` arg。

**Q10 — 自家 code-review inline 發現的 auto-fix 信任門檻。** self-authored「critical」發現是否該比 human review comment 用更嚴 judge 門檻(或 human gate)才讓 resolver 在 `--auto` 下 auto-fix?現狀「err toward ACT」+ cap-at-cycle-2 表示至少一個 machine-generated critical fix 會在任何人看到前被 auto-commit+push。D6 settled「同一機制流經」但未 settle 信心門檻——風險容忍度,只有 owner 能定。

**Q11 — Leg-2 範圍。** health poll 用 `--author @me`(現狀)還是 match dispatcher full roster?`@me` 會靜默排除 recovered/手開的 PR,而 plan 目標宣稱「keep EVERY open PR rebased」。範圍決策(standby 照顧誰的 PR)。

**Q12 — `--until` 時仍有 background agent 在跑。** drain(拒絕停直到 agents 完成)還是 hard-stop(接受 completion notification 遺失,靠隔日 reset + dispatcher Q2/Q4 label recovery,可能重 review reviewed_shas 沒寫成的 SHA)?clean shutdown vs prompt shutdown 偏好。

**Q13 — `--auto` 下的 DEFER reply(對應 `failure-modes:10`)。** skill 會 post 公開「will file ticket after merge」但無人在 loop 中真的 file。
  - (A) loop 抑制 DEFER reply;(B) 保留,接受 unkept-promise comment / 可能 re-DEFER spam(注:substance-based judge 會在下輪重分類,部分緩解 spam)。

---

## 5. 🟢 已驗證符合 spec 的依賴 (GROUNDING CONFIRMED)

- `resolve-pr-comments` SKILL:self-author 處理與 locked header 與 E-1/D7 相容。
- `resolve-conflict`:single mode **不 push**——確認,符合 step 5;worktree PATH 慣例 `../<TICKET-ID>` 兩檔一致。
- `code-review`(→ `git-branch-code-reviewer`):remote mode 可對任意 PR/branch 而不進其 worktree——符合 Leg-2 假設;batch dedup 為 comment-timestamp based,會與 standby per-PR inline posting model 衝突(已記於 M2)。
- `ticket-analyze`:`--non-interactive` 存在且跳過兩個 default-mode AskUserQuestion gate;分析 comment 在 label write 前 post;Step 1.5.5 跳過 `ready-to-*` 與 `dispatcher-*-in-flight`;Step 8.2 為 pre-write re-check;label write 移除其他 analyzer labels(但 hand-added label 僅在 batch fetch 後加入才被覆寫);comments append-only,need-revision/need-dependency 每輪重分析;`--team:<KEY>` 接受(batch-mode only)。
- `ggx-dispatcher` 整合假設:`dispatcher-*-in-flight` label 經 Q2/Q4 在下輪 self-recover(符合 `--until` 假設);§6.1 join barrier block caller 數十分鐘、lock 為 internal 600s TTL、report 路徑 `<RUN_TS>-<PID>.md`、pre-flight worktree/branch/clean/gh 可重用——standby 的 background-spawn + no-lock-probe 架構動機正確。
- install/config:`install.sh` 已涵蓋兩個新 command 檔(無需改);`resolve-pr-comments` 變更經 install.sh 自動流通(skill 為 symlink 目錄);config path 用正確 deployed 路徑(滿足 memory 警告);Slack-unconfigured 為 silent no-op(G1,屬實);prototype 副本與 repo 副本 byte-identical(「copied verbatim」屬實)。
- `.ggx-standby/` 在本 repo 不被 gitignore 是**正確**的——command 在 target repo 跑且 runtime self-ensure target repo 的 gitignore(`ggx-standby.md:43`、plan §6 `:131-132`)。

---

## 6. minor 附錄

每項一行;施工中順手處理,不 gate 開工。

- **m1 (loop-architecture:1 — major,已併入 B1 周邊):** RECONCILE 的 liveness probe 應以 **TaskList 為唯一權威**;`ggx-standby.md:66` 砍掉「/ its report file」(report file 在 join barrier 前不存在,會誤判 live chain 為 dead → double-spawn)。並在每個 spawn site 把 agent handle/id 存進 state.json 以利 TaskList correlation。*(此項實為 major,因 double-spawn 風險;列此處因與 B1 同源,施工時一併處理。)*
- **m2 (loop-architecture:3):** 補一句構造性語句:「wake 串行處理、recurring prompt 不可重入、state.json 在 Finalize 原子重寫」——釐清 mid-cycle/並發 completion 語意。
- **m3 (loop-architecture:5):** 為 RECONCILE gap 門檻定具體公式(如 `gap > 2× min(chain.next_due, health.next_due)`)並命名 anchor;`next_due` 已持久化可用。
- **m4 (loop-architecture:7):** 明列 Leg-2 step 3 哪些子步驟 in-session vs spawned,並給具體 restart trigger(wake-count 或 token 門檻),取代「feels heavy」。
- **m5 (loop-architecture:9):** state.json 加 schema version + load-and-merge-over-defaults 的 resume 契約(「file 存在但缺 key」處理)。
- **m6 (pr-resolver-correctness:ticket-id-derivation):** step 4 補非-ticket branch fallback(grep `[A-Z]+-[0-9]+`,無則 sanitized branch name)與 fork 路徑,mirror `resolve-conflict.md:266,271`。*(與 B2a 同檔,一併修。)*
- **m7 (pr-resolver-correctness:lease-rejection):** 定義 lease-rejection 後的 local 復原契約(reset worktree 到 `origin/<headRef>` 或標 needs-human),避免下輪 re-rebase 疊在被棄的 local commit 上;注意公開 reply 可能 cite 未 push 的 SHA。
- **m8 (pr-resolver-correctness:another-resolver-guard):** step 2「caller tracks this」在 standby 內標註「N/A — single batch at a time」;修 `ggx-pr-resolver.md:48` 過時的「event-triggered resolver」措辭(`ggx-standby.md:82` 已無此路徑);宣告 standalone+standby 並發 resolver 不支援。
- **m9 (integration-gaps:7 / RC-3):** step 5 措辭從「merge/rebase」pin 為「rebase onto latest base」;step 7 砍掉不實的「same semantics as resolve-conflict --batch」(batch merge path 是 plain push,非 force-with-lease)。*(連動 Q4。)*
- **m10 (integration-gaps:11 / CR-2):** code-review 輸出實為四桶(Critical/Improvements/Minor/Positive),spec 用的「major」「nit」不存在。明列映射:Critical+Improvements→inline、Minor→digest、Positive→drop;修 `ggx-standby.md:93` 與 plan `:122` 的詞彙。(注:convergence cap 為 SHA-chain based,不依賴此 routing。)
- **m11 (integration-gaps:12):** 定義 dispatcher report 檔的 discovery 契約(glob 最新 `claude-reports/dispatcher/*.md` by mtime,或 dispatcher 寫 stable「latest」pointer)——standby 無管道得知 background subagent 的 RUN_TS/PID,lock 又禁讀。
- **m12 (integration-gaps:17):** 把 `skipped: judged-clean` 與 `held[]` 折進 step-8 report enum(或聲明 step 8 為兩個 exit shape 的 union),使 standby settled-SHA 偵測有定義欄位可讀。
- **m13 (failure-modes:3):** §6 加 deterministic state-write contract(Bash heredoc/jq 寫 temp file + mv,或 per-key jq upsert),避免 LLM 每輪重印整個 JSON 而靜默丟 key。
- **m14 (failure-modes:6):** 加 secondary-rate-limit 偵測(HTTP 403 + Retry-After)→ 比照 DEGRADED(skip evictions、拉長 next_due),取代 per-PR 靜默 skip;否則持續 throttle 下 Leg-2 變 no-op 且與「全 clean」無法區分。(注:Leg-2 poll `--author @me`,N 通常 3-10,REST cap 不易觸及;secondary-abuse limiter 是唯一現實 trigger。)
- **m15 (failure-modes:10):** build-sanity abort 列為 terminal `needs-human: build`(併入 M3);DEFER reply 處置見 Q13;single-mode rebase 前加非互動 dirty 預檢(否則撞 `/check-clean` HITL 卡死)。
- **m16 (failure-modes:11 — info):** 文件註明 `self_pushed` 單-SHA tag 在並發 human push 下為 best-effort(只影響通知 framing,E-4 從不吞 red;E-2 cap 對 human commit 為 fail-safe reset)。
- **m17 (integration-gaps:14 subset / integration-gaps:18 — info):** 修 `ggx-standby.md:5` front-matter「Three legs」→「Two legs」(body 只有 Leg 1/Leg 2)。
- **m18 (integration-gaps:19 — minor):** T3 把 DEGRADED 觸發從「revoke network」改寫為 gh-scoped 注入(unset GH_TOKEN / 指向壞 host / 強制非零 gh exit;或 stub 前一輪 non-empty 再強制 empty),涵蓋兩個 trigger branch。
- **m19 (integration-gaps:10 / CR-4 — info):** §6 item (b) 措辭釐清:standby 走 REMOTE path,`gh pr diff` 已 server-side 對 baseRef,LOCAL 才 hardcode trunk;若改 explicit `git diff <baseRef>...<head>`(SHA-pin 需要)才須在 `gh pr view --json` 加 `baseRefName`(`git-branch-code-reviewer.md:38` 目前漏)。
- **m20 (F5-team-flag — info):** 建議 standby step 1 fail-fast 驗證 `--team`(dispatcher 對 auto-prefix repo 缺 --team 會 STOP、concrete-prefix mismatch 也 STOP)。注:現有 registry 中唯一 auto-prefix repo 非 Linear,故「auto-prefix 無限 no-op」情境今天不可達;僅 concrete-prefix mismatch typo 半部成立,WARN + report path 會帶 STOP 訊息,非全靜默。
- **m21 (integration-gaps:20 — info):** resolver step 6「combined commit」措辭澄清為「one push, may be two commits」,並在「Why one command」註明 double local suite 為刻意 tradeoff(連動 Q7)。
- **m22 (SN-8 — info):** 可在本 repo `.gitignore` 加一行註解,說明 gogox-claude 刻意不 ignore `.ggx-standby`(command 從不寫 state 進本 repo)。

---

*報告結束。blocker 全部為「決策已拍板、實作機制缺口」類型,非架構翻案。建議 owner 先答 §4(尤其 Q1/Q3/Q4/Q5),施工者據此補 §6 後開工。*
