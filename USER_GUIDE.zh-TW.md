# gogox-claude 新手導覽（繁中）

第一次下載這個 repo 嗎？跟著下面步驟走，5 分鐘內就能在 Claude Code 裡用到團隊共享的 skills、commands、agents。

> 英文版說明請看 [README.md](./README.md)。

---

## 1. 你會拿到什麼？

這個 repo 是 gogox 內部共享的 Claude Code 工作流。安裝後會在 `~/.claude/` 出現三類東西：

| 類別 | 安裝位置 | 在 Claude Code 怎麼叫 |
| --- | --- | --- |
| **Skills** | `~/.claude/skills/<name>/` | 在對話中輸入 `/<name>` 觸發 |
| **Commands** | `~/.claude/commands/<name>.md` | 在對話中輸入 `/<name>` 觸發 |
| **Agents** | `~/.claude/agents/<name>.md` | Claude 會依任務自動派工，或你明說「用 xxx-agent」 |

> Skills 和 Commands 都長 `/xxx` 的樣子，使用時不用分。

---

## 2. 安裝 gogox-claude（每台電腦一次）

```bash
git clone <repo-url> gogox-claude
cd gogox-claude
./install.sh
```

一行指令、全部裝好。不用挑角色——repo 裡的 `pm/`、`dev/`、`design/` 資料夾只是內部分類，安裝後全部攤平在 `~/.claude/` 底下。

裝完終端機會印出這次裝了哪些 `/skill`、`/command`、`agent`，挑一個試試看就行。

> 內部用 **symlink**，不是 copy。`git pull` 之後檔案就同步更新了，**不用再跑一次 `install.sh`**（除非有新增最上層的東西想撿起來）。

---

## 3. 設定專案（每個 project 一次）

在你的 project repo 裡，啟動 Claude Code，輸入：

```
/init-project
```

它會用選單引導你選平台、產品、ticket 系統，然後自動產生 `.gogox-claude.yaml`。

**記得把 `.gogox-claude.yaml` commit 進 git** — push 之後，team 裡所有人 pull 就自動有設定。

### `.gogox-claude.yaml` 範例

**固定模式**（單產品 repo，如 gogovan-client-v2-ios）：
```yaml
platform: ios
product: ca
branch_prefix: CET
ticket_system: jira
```

**自動模式**（shared repo，ticket 來自多個產品）：
```yaml
platform: ios
product: ggx-core-ios
branch_prefix: auto
ticket_system: auto
```

| 欄位 | 可用值 | 說明 |
|------|--------|------|
| `platform` | `ios`、`android`、`flutter` | 決定用哪套 test / format / deps 指令 |
| `product` | `ca`、`da`、`ca-revamp`、`da-revamp`、或自訂 | 產品名稱 |
| `branch_prefix` | `CET`、`DET`、`CAF`、`DAF`、或 `auto` | `auto` 會從 branch name 自動偵測 |
| `ticket_system` | `jira`、`linear`、`auto`、或 `none` | `auto` 會從 branch prefix 反推 |

---

## 4. 馬上試一下

打開任何已設定的專案，啟動 Claude Code，在對話框輸入：

```
/commit
```

或：

```
/pull-request --dry-run
```

如果出現對應的工作流，就代表裝好了。

不確定有什麼可用？三個方式都行：

- 輸入 `/` 看 Claude Code 即時清單
- 在 Notion 看完整索引（含每個 skill 的 detail 頁）：[claude-gogox / Claude Skills](https://www.notion.so/gogox/claude-gogox-357f54d1149880c98674f8b1218ee1f1)。可以用 `Category` 篩選 dev / shared / design / pm / agent
- 直接問 Claude：「我這邊有哪些 gogox 的 skill？」

---

## 5. Port — 把 feature 從另一個 codebase 搬過來

如果你 repo 是 port target（譬如 flutter app 要從 android app 搬功能），用 `/port:*` 系列。

### 一條指令跑完

```
/port:ff --ticket:CAF-212
```

從 Linear ticket 開始，自動：建 worktree → 讀 origin codebase → 產 PM/設計 notes → 合成 OpenSpec artifact（proposal / design / tasks / spec） → 跑 lint check → 互動式 review → push feature branch → 回貼 Linear summary。

跑完之後 cwd 會在 worktree 裡，下一步通常是 `/opsx:apply` 開始實作。

### 三種模式

| 模式 | 用途 |
|---|---|
| `/port:ff --ticket:X` | **預設 HITL** — 每個 stage 仍會 AskUserQuestion 等你確認（Locate gate / 修訂 / review gate） |
| `/port:ff --ticket:X --auto` | **無人值守** — 給 dispatcher 半夜跑用，所有 gate 套 G1-G9 auto-decision rules，locate 信心過低或 agent 重複失敗才 abort |
| `/port:ff --ticket:X --simple` | **輕量探勘** — 不建 worktree、不開 spec，只跑一次 origin codebase 分析，把結果寫進 Linear comment。適合 PM 給的 ticket 太薄、想先看可行性 |

### 進階：拆開跑（atomic stage）

`/port:ff` 是 wrapper。實際拆成 6 個獨立 command，可以單跑：

```
/port:start --ticket:X    # 建 worktree、scaffold
/port:explore             # dev-consult-agent 讀 origin codebase
/port:plan                # pm-agent + designer-agent 平行跑
/port:synth               # 合成 artifact + /spec-lint
/port:revise              # HITL 處理 lint findings
/port:ship                # commit + push + 回貼 Linear
```

中段 stage 會自動從 cwd worktree 名稱推 ticket-id，不用每次傳 `--ticket:`。

### 設定（每台電腦一次）

Port 需要知道 origin codebase 在你電腦的哪裡。在 **port target repo** 的根目錄建 `.gogox-claude.local.yaml`（**加進 .gitignore**，是 per-machine 設定）：

```yaml
origin_project_path: ~/Projects/work_project/gogovan-client-v2-android
```

支援 `~` 和 `$ENV_VAR`。第一次 `/port:start` 會驗路徑、缺就互動式問你補。

### `/spec-lint` 也可以單獨用

任何 OpenSpec change（不限 port 產生的）都能用 `/spec-lint --change <name>` 檢查：
- 缺 spec scenario 對應 FR、artifact 引用了 notes 沒寫的 ID（synthesis 幻覺）、forbidden marker（TBD / TODO）等 9 種純 grep 檢查
- 修完一輪後可以跑 `/spec-lint --after-edit --stale "old-term"` 找散落各處的舊用詞

詳細設計：[`plans/port-centralization.md`](./plans/port-centralization.md)。

---

## 6. 升級 / 移除

```bash
# 升級到最新版（檔案是 symlink，git pull 完就生效）
cd gogox-claude && git pull

# 想完全移除？刪掉 ~/.claude/ 裡對應的 symlink 就好
# （symlink 安全可刪，不會影響原檔案）
```

---

## 常見問題

**Q：我自己手動改了 `~/.claude/skills/xxx`，會被覆蓋嗎？**
A：你改的其實是 symlink 指向的 repo 檔案，所以等於改到 repo。如果只是想本地實驗，建議在 repo 裡開分支改，或先 `rm` 掉 symlink 再放自己的版本。

**Q：Project-aware 是什麼意思？**
A：有些 command（例如 `/add-worktree`、`/format`、`/pull-request`）會依當前 repo 的設定做不同的事。判斷規則：
1. repo 根目錄有 `.gogox-claude.yaml` → 用它（source of truth）。
2. 否則查 `~/.claude/commands/profiles/registry/{repo-name}.yaml`（fallback）。
3. 都沒有 → 報錯，請跑 `/init-project`。

**Q：第一次在新 repo 使用怎麼設定？**
A：在該 repo 裡跑 `/init-project`。它會用選單引導你選平台、產品、ticket 系統，然後產生 `.gogox-claude.yaml`。**commit 進 git 後，所有人 pull 就有**——一人設定，全 team 受益。同時會自動推一份 registry 到 gogox-claude 作為 fallback。

**Q：Shared repo 的 ticket 來自不同產品怎麼辦？**
A：用 `auto` 模式。設定 `branch_prefix: auto` 和 `ticket_system: auto`，指令會從 branch name 提取 ticket prefix（如 `CET`），再查 `org.yaml` 反推 ticket 系統。例如 `feat/CET-1234` → Jira，`feat/CAF-567` → Linear。

**Q：Jira 和 Linear 都支援嗎？**
A：對。`/pull-request` 會根據 `ticket_system` 自動去 Jira 或 Linear 抓 ticket 標題、產生連結、回貼 implementation notes。native app（ca / da）用 Jira，Flutter revamp（ca-revamp / da-revamp）用 Linear。shared repo 用 `auto` 模式兩邊都支援。

**Q：怎麼把我自己的 skill 加進來？**
A：照 [README.md → Adding a skill](./README.md#adding-a-skill) 走。重點：skill 內容必須是英文、放對 category 子目錄、PR 進 `shared/` 需要兩個不同角色 +1。

**Q：哪些是公開、哪些是私人？**
A：repo 本身是內部共享。每次跑 skill 會在 `~/.gogox-claude-usage.jsonl` 留一行紀錄，**完全在你自己的電腦上**，沒有上傳。每季回顧時會請大家自願分享這份檔案。

---

## 下一步

- 看 [ARCHITECTURE.md](./ARCHITECTURE.md) 了解整體設計與 rollout 計畫。
- 在 Slack 找你的 onboarding 同事領 owner 名單（目前 owners: TBD，會在 Week 1 1-on-1 後補上）。
- 用了一陣子之後，幫忙提 PR 把你常用的工作流也共享進來。
