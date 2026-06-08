# `/ui-tweak` 改善計劃 v2 — designer feedback 落地

> 行文中文;所有 code / 識別符 / 檔名 / label / flag 維持英文。
> **來源**:designer(2026-06-08)針對 `/ui-tweak` 的 7 條使用 feedback。
> **狀態(2026-06-09)**:設計討論完成,A3 + B 方向已由 Charlie 認可(跨機器可攜性疑慮已釐清,見 §A3)。
> 尚未開工。第一批次 = **A3 + B**(一個 PR)。
>
> v1 的嘗試是 branch `fix/ui-tweak-designer-feedback`(已 merge 進 main,只做了
> 「flutter resolution 按 platform profile gating」一項);本檔是把 designer 後續完整 7 條
> feedback 系統化的 v2。

---

## 0. 一句話結論

7 條 feedback 裡 **#1 / #2 / #7 是同一個根因**(每張票切新 worktree → 新目錄 → 冷建置 + 每次重 probe
fvm)。**第一批次只做 A3(fvm 解析快取 + 直指 SDK)+ B(預設 staging flavour)**:小改、低風險、
每次互動都受惠、改的是同一組檔案(`start.md` / `preview.md` / `.gogox-claude.yaml`)。其餘按 CP 值排序。

---

## 1. Feedback 原文(7 條)

1. iOS simulator startup 慢:換目錄導致首次 ~2 分鐘,每次改動 re-build 再 ~1 分鐘。希望重用 Xcode build
   cache 讓 per-ticket 首次啟動更快,且後續改動能 HMR(hot reload)。
2. designer 通常不會同時做多張票。
3. 預設跑 staging flavour;現在每次都要找該 launch 哪個 flavour。
4. 自動 screenshot / recording 沒有抓到正確的頁面。
5. 希望不只支援 Claude,也支援 cursor / codex。
6. pre-commit 讀 diff 若能用平行 subagent 會更快;現在是序列讀,很慢。
7. repo README 叫人用 fvm 安裝,但每次跑都找不到 flutter command,要花時間找 fvm command。

---

## 2. 根因分群

| Feedback | 群組 | 共同根因 |
|---|---|---|
| #1 啟動慢 / 想要 build cache + HMR | **A 速度** | worktree 換目錄 → DerivedData/`build/` 全冷;preview 每次 kill 重 build 而非 hot reload |
| #2 designer 不做多票 | **A 速度** | R19「每票一個 worktree」的隔離成本對單票場景不划算 |
| #7 每次重找 fvm/flutter | **A 速度** | `flutter-bin` marker 存 worktree 內 → 新票就重 probe;且 `fvm flutter` wrapper 每次呼叫都有 overhead |
| #3 預設 staging flavour | **B 設定** | profile 範本無 flavour,gogox repo 沒在 `.gogox-claude.yaml` 覆寫 → 每次要猜 |
| #4 截圖/錄影抓錯頁 | **C 擷取** | demo 階段「重新偵測 booted 裝置 + 抓當下畫面」,常抓到首頁而非改動頁 |
| #6 pre-commit 讀 diff 序列、慢 | **D 審查** | audit.md 已寫「兩 judge 並行」,但 opus judge 慢 + 可能實際沒真並行 |
| #5 支援 cursor/codex | **E 可攜** | 整個 skill 綁 Claude Code(Agent 子代理、MCP、cards、filesystem markers) |

---

## 3. 各群組 plan

### 群組 A:啟動 / 建置速度(最高優先)

#### A3 — fvm/flutter 解析快取 + 直指 SDK(解 #7)【第一批次】

**目標**:fvm/flutter 解析「每台機器只做一次」而非「每張票一次」,且解析結果直指 SDK 實體檔、不走
`fvm flutter` wrapper。

**先例**:`plans/archive/ggx-pr-resolver-improvements.md` 的 **V2-B** 已驗證同一原則——
「orchestrator 解析一次 `FLUTTER_BIN`,worker 不准重新解析」。A3 把這個原則套到 `/ui-tweak` 的
per-ticket worktree 場景。

**改的檔案**:

| 檔案 | 現況 | 改什麼 |
|---|---|---|
| `commands/design/ui-tweak/start.md`(第 5(a) 步,L57–89) | probe 後寫 `.dev/ui-tweak/flutter-bin`(worktree 內) | 加共用快取讀寫 + 直指 SDK + override |
| `commands/design/ui-tweak/preview.md`(Step 0,L47–71) | marker 不在就 inline 重 probe | 同步先讀共用快取 |
| `commands/design/ui-tweak/demo.md`(Step 1,L33–34) | 讀 worktree 內 `flutter-bin` | 讀法不變(被前面填好,確認相容即可) |
| `commands/dev/profiles/platform/flutter.yaml`(L24–27) | 只講 fvm rewrite | 文件化 `flutter_bin` override key + SDK 直指 |

**變更 1 — marker 快取拉到 repo/機器層**(解「每張票重 probe」)

`start.md` L80 現在寫 worktree 內,新票一定是空的 → 重 probe。改成寫到跨 worktree 共用、以 repo 為 key
的 per-machine 快取,worktree 內保留副本(下游讀法不變):

```bash
COMMON=$(git rev-parse --git-common-dir)                 # 同一 repo 所有 worktree 都指向主 .git,跨票穩定
REPO_KEY=$(cd "$(dirname "$COMMON")" && basename "$PWD")
CACHE_DIR="$HOME/.cache/ui-tweak/$REPO_KEY"
mkdir -p "$CACHE_DIR"

# 1) 先試共用快取 —— 命中且仍有效 → 0 次 probe(用 -x / command -v,毫秒級,不再跑慢的 --version)
if [ -z "$FLUTTER_BIN" ] && [ -f "$CACHE_DIR/flutter-bin" ]; then
  CANDIDATE=$(cat "$CACHE_DIR/flutter-bin")
  if { case "$CANDIDATE" in /*) [ -x "${CANDIDATE%% *}" ];; *) command -v "${CANDIDATE%% *}" >/dev/null 2>&1;; esac; }; then
    FLUTTER_BIN="$CANDIDATE"
  fi
fi

# 2) 仍空 → 才走原本 probe 區塊（第一次 / 快取失效時）

# 3) 解析成功後同時寫兩處
printf '%s\n' "$FLUTTER_BIN" > "$CACHE_DIR/flutter-bin"     # 共用,給後續所有票
printf '%s\n' "$FLUTTER_BIN" > .dev/ui-tweak/flutter-bin    # worktree 本地,下游讀法不變
```

`preview.md` Step 0 的 inline fallback 套同一套「先讀 `$CACHE_DIR`」。

**變更 2 — pinned repo 直指 SDK 實體 binary**(解 wrapper overhead)

`fvm flutter` 每次都先啟動 fvm 解析 pinned 版本再轉呼;改成優先解析 fvm 建的 symlink 實體檔:

```bash
SDK_FLUTTER="$WT/.fvm/flutter_sdk/bin/flutter"     # fvm use/install 後存在的實體檔(repo 相對位置)
if [ "$PINNED" = 1 ]; then
  if [ -x "$SDK_FLUTTER" ] && probe "$SDK_FLUTTER"; then
    FLUTTER_BIN="$SDK_FLUTTER"                       # ← 直指,免 wrapper
  elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then
    FLUTTER_BIN="$FVM_BIN flutter"                   # symlink 未 materialize → 退回 wrapper
  fi
fi
```

**變更 3 — `.gogox-claude.yaml` 明確 `flutter_bin` override**(完全跳過 probe)

```yaml
# <repo>/.gogox-claude.yaml
flutter_bin: .fvm/flutter_sdk/bin/flutter      # 只准相對路徑
```

`start.md` 在 probe 前最先讀;**偵測到開頭是 `/` 的絕對路徑 → 忽略 + 印 warning**(見下方可攜性)。

**跨機器可攜性(Charlie → commit → Arthur,已釐清)**

三層儲存的「共用 vs per-machine」屬性不同,解析出的絕對路徑**絕不進版控**:

| 儲存層 | 是否 commit / 共用 | 能放絕對路徑 |
|---|---|---|
| `.gogox-claude.yaml` 的 `flutter_bin` | 會 commit,全隊共用 | ❌ 只准相對路徑(start.md 擋絕對) |
| `~/.cache/ui-tweak/<repo>/flutter-bin` | 在 `$HOME`,每台機器各一份 | ✅ |
| worktree 內 `.dev/ui-tweak/flutter-bin` | gitignored(`dev/verify.md:110-116` 加 `.dev/` 進 .gitignore + evict)+ ui-tweak commit 是 coverage-scoped(`ff.md:289` 只 commit UI 檔) | ✅ |

兩道防線:(1) auto-resolve 的絕對路徑只落 per-machine,兩條路都到不了 commit;(2) 唯一 commit 的
override 強制相對路徑。`.fvm/flutter_sdk/bin/flutter` 是 repo 相對位置,每台機器各自解析到自己的 SDK。
→ Charlie 的 `/Users/charlie/...` 不會傳給 Arthur,Arthur 在自己機器 probe 出 `/Users/arthur/...` 存自己
的 `~/.cache`。比現況更安全(現況把 probe 結果直接寫 worktree `.dev/`)。

**可選強化**:把 `.fvmrc` 內容 hash 併進 `REPO_KEY`,SDK 版本一換就自動失效重 probe。

**工作量**:小-中。**風險**:低(三變更都保留現有 probe / wrapper fallback,任一步失敗退回今天行為)。

---

#### B — 預設 staging flavour(解 #3)【第一批次,與 A3 同 PR】

**根因**:`flutter.yaml` 範本是無 flavour 的 `flutter run -d {device} --debug`(L28);設計上要 repo
覆寫,但 gogox-client / gogox-driver-flutter 沒設 → 每次要找 flavour。

**改法**:

1. 在 gogox flutter repo 的 `.gogox-claude.yaml` 寫死:
   ```yaml
   ui_preview_cmd: flutter run -d {device} --debug --flavor stag
   ui_build_cmd:   flutter build apk --debug --flavor stag   # 對應
   ```
   (最小、最直接的解)
2. 強化:`start` / `preview` 自動偵測可用 flavour(Android `productFlavors` / iOS schemes),有 staging 類
   就預設它,避免「猜」。把「預設 staging」寫成正式 contract。

**與 A3 為何同一 PR**:兩者都改 `start.md` / `preview.md` 的「resolve build tooling once」區塊 + 同一個
`.gogox-claude.yaml`;A3 定指令前綴(哪個 flutter binary),B 定指令參數(`--flavor stag`)。一起測一次涵蓋
兩者。

**工作量**:小。**風險**:低。

---

#### A1 — 跨 worktree 共用建置快取(解 #1 #2)【第二批次】

**根因**:`/add-worktree` 建 `../<ticket-id>`,新路徑使 Flutter `build/`、`.dart_tool/`、iOS `ios/Pods`、
`ios/.symlinks`、Xcode DerivedData(以路徑 hash 為 key)全冷 → 首次 ~2 分鐘。

**改法**(`start.md` 第 5 步,worktree 建立後):從共用快取目錄 symlink/硬連結重物
(`ios/Pods`、`ios/.symlinks`、`.dart_tool/`、`build/`,或設 Xcode `-derivedDataPath` 指共用路徑);
`pod install` 只在 `Podfile.lock` 變動時跑。

**對 #2 的回應**:**保留 worktree**(隔離 + 乾淨 PR branch;`ff.md` 已記錄 in-place 會 orphan 編輯 +
重問 C-WT 兩個 bug),只把冷建置成本拿掉——隔離留著、慢的修掉。

**工作量**:中。**風險**:共用快取在不同 branch 間可能髒;需以 repo+flavour 為 key + Podfile 變動就重裝。

---

#### A2 — preview 常駐 session + hot reload(解 #1 後半 HMR)【第三批次,大改】

**根因**:`preview.md` Step 2 把 `flutter run` 背景化後結束;下一輪 apply 後又是全新 `flutter run`
(整包重 build + 重裝),而非對既有 session 按 `r`/`R`。

**改法**:orchestrator 維持常駐 `flutter run` 程序(named pipe / 背景 task 控 stdin),每次 apply 後送
`r`/`R`。把「每次改動 ~1 分鐘」降到秒級。

**工作量**:大(改 preview「背景後結束」模型 + ff walker 要管程序生命週期)。**風險**:程序殘留;hot
reload 對資源/原生改動無效需 fallback 重啟。**不與 A3 綁**。

---

### 群組 B:見上(已併入第一批次)

---

### 群組 C:截圖 / 錄影抓對頁(解 #4)

**根因**:`demo.md` 在 commit 後「重新偵測 booted 裝置 + 抓當下畫面」。問題:(a) 重新偵測可能抓到另一台
booted sim;(b) 時間差後 app 可能回首頁;(c) HARD BOUNDARY 禁止 agent 導航。

**改法(不破壞 no-navigate 原則)**:

1. **記住 preview 用的裝置 id**(`preview.md` 寫 `.dev/ui-tweak/preview-device`),demo 只擷取那台,抓不到
   就 fail-silent。
2. **擷取時機提前到「designer 在 C1 按 looks-good 的當下」**(畫面正是剛核准那頁),而非 commit 後重抓。
   被動擷取、不增加等待。
3. `--auto` / direct-ship 本來就無人導航 → demo 結構上不可達(現況已是),維持;#4 純互動路徑問題。
4. (待拍板)若要保證錄到改動頁,唯一方法是放寬 boundary 允許 designer 指定 route 的單次 deep-link 啟動
   (opt-in)。預設**不建議**。

**工作量**:中。**風險**:擷取時機提前需確認不卡 designer。

---

### 群組 D:審查並行加速(解 #6)

**現況**:`audit.md:61` 已寫「Always run BOTH judges in parallel (one message, two Agent calls)」。
designer 仍覺序列,可能:(a) 實際沒真並行;(b) opus `dev-reviewer` 本身慢;(c) format + 結構 pre-pass 在
judge 前序列跑。

**改法**:

1. **驗證並強制真並行**:確保 orchestrator(含 `workflows/ggx-dispatch.workflow.js` 的 `runUiTweak`)真在
   單一 message 發兩個 Agent call。
2. **diff 只算一次**:預先算好 `git diff` + changed-files,把 diff 文字 inline 餵兩個 judge,避免各自重跑
   git / 重讀檔。
3. **大 diff 才 per-file fan-out**:UI tweak diff 通常很小,fan-out 收益有限;真正瓶頸是 opus latency。
   可讓確定性結構 pre-pass(快)先短路明顯 logic 改動,省 opus 呼叫。

**工作量**:小-中。**風險**:低(不動 both-must-be-CLEAR 契約)。

---

### 群組 E:支援 cursor / codex(解 #5)【最大工程,roadmap】

**根因**:skill 綁 Claude Code——Agent 子代理(dual-judge)、MCP、互動 cards、filesystem markers。

**分階段(建議先做 MVP)**:

- **MVP**:抽出「編輯契約」成 tool-agnostic 的 rules 檔(`AGENTS.md` / `.cursorrules`)——只允許
  visual/layout/structure、禁碰 logic/build config——Cursor/Codex 載入即遵守核心約束。
- **第二步**:把 logic-audit 抽成獨立 CLI(`git diff` → 結構 pre-pass + 單次 LLM 呼叫,provider 可換),
  任何編輯器可呼叫,不依賴 Claude Agent tool。
- **完整移植**(cards/markers/orchestrator)成本最高,**建議延後**。

**工作量**:大。**建議**:列 roadmap,先交 rules 檔 MVP。

---

## 4. 優先順序(依 CP 值)

1. **A3 + B**(第一批次,一個 PR)— 小改、每次受惠、低風險。**← 已認可,先做**
2. **A1**(跨 worktree 共用建置快取)— 中改,解 #1 #2 核心痛點
3. **D**(審查真並行 + diff 算一次)— 小-中改
4. **C**(記住 preview 裝置 + 擷取時機提前)— 中改
5. **A2**(常駐 flutter run + hot reload)— 大改,第二波
6. **E**(cursor/codex)— roadmap,先交 rules 檔 MVP

---

## 5. 待拍板決策

| # | 決策 | 建議 | 狀態 |
|---|---|---|---|
| 1 | #2 worktree 留或拿掉 | **保留**,靠 A1 快取消除慢的部分 | ✅ Charlie 認可方向 |
| 2 | A3 committed override 對絕對路徑:擋掉 vs 放行+警告 | **擋掉**(`/` 開頭忽略),對共用 skill 較安全 | ✅ 已釐清 |
| 3 | #4 是否開 opt-in deep-link 導航 | 預設**不開**,靠記裝置 + 提前擷取 | ⏳ 待 C 階段再定 |
| 4 | #5 先 MVP rules 檔 vs 完整移植 | 先 **MVP rules 檔** | ⏳ 待 E 階段再定 |

---

## 6. 第一批次開工步驟(A3 + B)

1. 開 branch `fix/ui-tweak-fvm-cache-flavour`。
2. 改 `commands/design/ui-tweak/start.md`(A3 三變更)。
3. 改 `commands/design/ui-tweak/preview.md`(A3 共用快取讀取同步)。
4. 改 `commands/dev/profiles/platform/flutter.yaml`(文件化 `flutter_bin` + flavored cmd)。
5. 在 gogox flutter repo 的 `.gogox-claude.yaml` 補 `flutter_bin`(相對)+ flavored `ui_preview_cmd` /
   `ui_build_cmd`(B)。
6. 一起測 → 一個 draft PR。
</content>
</invoke>
