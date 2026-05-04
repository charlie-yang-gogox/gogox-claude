# gogox-claude 新手導覽（繁中）

第一次下載這個 repo 嗎？跟著下面四步走，5 分鐘內就能在 Claude Code 裡用到團隊共享的 skills、commands、agents。

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

## 2. 安裝（挑一個你的角色）

```bash
git clone <repo-url> gogox-claude
cd gogox-claude

# 只裝大家共用的（最小集合）
./install.sh

# 依角色安裝（shared 永遠會包含）
./install.sh pm                 # PM
./install.sh dev                # 工程師
./install.sh design             # 設計師
./install.sh pm dev             # 偶爾寫 PRD 的工程師
./install.sh pm dev design      # 全部都裝
```

裝完終端機會印出這次裝了哪些 `/skill`、`/command`、`agent`，照著挑一個試試看就行。

> 內部用 **symlink**，不是 copy。也就是說 `git pull` 之後檔案就同步更新了，**不用再跑一次 `install.sh`**。除非你要新增/減少角色，才需要重跑。

---

## 3. 馬上試一下

打開任何一個專案，啟動 Claude Code，在對話框輸入：

```
/dev
```

或：

```
/commit
```

如果出現對應的工作流，就代表裝好了。

不確定有什麼可用？輸入 `/` 然後看清單，或者直接問 Claude：「我這邊有哪些 gogox 的 skill？」

---

## 4. 升級 / 移除

```bash
# 升級到最新版（檔案是 symlink，git pull 完就生效）
cd gogox-claude && git pull

# 想換角色，重跑 install 並指定新組合
./install.sh dev design

# 想完全移除？刪掉 ~/.claude/ 裡對應的 symlink 就好
# （symlink 安全可刪，不會影響原檔案）
```

---

## 常見問題

**Q：我自己手動改了 `~/.claude/skills/xxx`，會被覆蓋嗎？**
A：你改的其實是 symlink 指向的 repo 檔案，所以等於改到 repo。如果只是想本地實驗，建議在 repo 裡開分支改，或先 `rm` 掉 symlink 再放自己的版本。

**Q：Project-aware 是什麼意思？**
A：有些 command（例如 `/add-worktree`、`/format`）會依當前 repo 的平台（flutter / android / ios）和產品（ca / da）做不同的事。判斷規則：
1. repo 根目錄有 `.gogox-claude.yaml` → 用它。
2. 否則查 `~/.claude/commands/profiles/repos.yaml` 裡 `basename` 對應的設定。
3. 都沒有 → 報錯，請你補一筆。

**Q：怎麼把我自己的 skill 加進來？**
A：照 [README.md → Adding a skill](./README.md#adding-a-skill) 走。重點：skill 內容必須是英文、放對 category 子目錄、PR 進 `shared/` 需要兩個不同角色 +1。

**Q：哪些是公開、哪些是私人？**
A：repo 本身是內部共享。每次跑 skill 會在 `~/.gogox-claude-usage.jsonl` 留一行紀錄，**完全在你自己的電腦上**，沒有上傳。每季回顧時會請大家自願分享這份檔案。

---

## 下一步

- 看 [ARCHITECTURE.md](./ARCHITECTURE.md) 了解整體設計與 rollout 計畫。
- 在 Slack 找你的 onboarding 同事領 owner 名單（目前 owners: TBD，會在 Week 1 1-on-1 後補上）。
- 用了一陣子之後，幫忙提 PR 把你常用的工作流也共享進來。
