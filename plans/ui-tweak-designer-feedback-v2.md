# `/ui-tweak` 改善計劃 v2 — designer feedback 落地

> 行文中文;所有 code / 識別符 / 檔名 / label / flag 維持英文。
> **來源**:designer(2026-06-08)針對 `/ui-tweak` 的 7 條使用 feedback。
> **狀態(2026-06-09)**:設計完成,經 ai-expert 兩輪複審(2nd = 7/10 SHIP_WITH_REVISIONS,必修已回補)。
> **Phase 1(A3 + B)已實作並在真實票 CAF-609 實測**(draft PR #523;A3/B = PR #61 待 merge)。實測跑出
> 7 點 dogfooding 回饋 → **2 個真 bug(F1 macOS `timeout`、F2 `pubspec.lock` 污染)+ 4 個加強**,記於 §7。
> iOS 加速(A1 Pods clone + 共用 DerivedData)折成**一個 Phase 1.5 spike**,在 Phase 1 量測後才做
> (採納 ai-expert 建議:A1 單獨價值低,與 1.5 同源耦合)。
>
> v1 的嘗試是 branch `fix/ui-tweak-designer-feedback`(已 merge 進 main,只做了
> 「flutter resolution 按 platform profile gating」一項);本檔是把 designer 後續完整 7 條
> feedback 系統化的 v2。

---

## 0. 一句話結論

7 條 feedback 裡 **#1 / #2 / #7 是同一個根因**(每張票切新 worktree → 新目錄 → 冷建置 + 每次重 probe
fvm)。**Phase 1 = A3(fvm 解析快取 + 直指 SDK)+ B(預設 staging flavour)+ A1(從 trunk CoW clone
建置快取)**:三者都圍繞「worktree 啟動成本」,改的是同一組檔案(`start.md` / `preview.md` /
`flutter.yaml`)。其餘按 CP 值排序。

> **可攜性鐵則(2026-06-09 釐清)**:**Phase 1 對 `gogox-client-flutter`(app repo)是零 committed 改動。**
> A3 快取只存「相對 token」、展開後的絕對路徑只活在每台機器自己的 `~/.cache` / worktree `.dev/`,不進版控;
> B 的 `--flavor stag` 寫進 gogox-claude 的 `flutter.yaml`(platform 預設,flavour **名稱** machine-agnostic)。
> 所有改動都在 gogox-claude repo 內。設計師 pull app repo 不會吃到任何人的本機路徑,也不會多一個 config 檔。

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

**變更 1 — marker 快取拉到 repo/機器層,但只存「相對 token」(解「每張票重 probe」+ 修 ai-expert 第 4 點)**

`start.md` L80 現在寫 worktree 內,新票一定是空的 → 重 probe。改成寫到跨 worktree 共用、以 repo 為 key
的 per-machine 快取。

> **⚠️ ai-expert 第 4 點(必修):快取不可存 `$WT` 絕對路徑。** fvm 的 SDK 路徑是
> `$WT/.fvm/flutter_sdk/bin/flutter`,**含當前 worktree**。若把整串絕對路徑存進「全機器共用」快取,下一張
> 票的 `$WT` 變了,快取卻指著舊 worktree(可能已刪 / 被切到別版本)。**解法:快取存「種類 token」而非絕對
> 路徑**,每張票用當下的 `$WT` 重新展開。

```bash
COMMON=$(git rev-parse --git-common-dir)                 # 同一 repo 所有 worktree 都指向主 .git,跨票穩定
TRUNK=$(dirname "$COMMON")
REPO_KEY=$(basename "$TRUNK")                            # ⚠️ ai-expert 第 3 點(撞名)先忽略 — 見下方備註
CACHE_DIR="$HOME/.cache/ui-tweak/$REPO_KEY"
mkdir -p "$CACHE_DIR"

CACHE_FMT=v1   # 格式哨兵:expand 規則一改就讓舊快取自動失效(ai-expert pro tip),免每台機器手動清
# 快取存的是「種類 token」,絕不是 $WT 絕對路徑:
#   sdk-rel|<relpath>   → 每票展開成 $WT/<relpath>(fvm 直指 SDK,worktree 相對)
#   fvm-abs|<fvm-path>  → <fvm-path> flutter(fvm binary 位置 machine-stable,存絕對 OK;非 $WT-相對)
#   bare                → flutter
# ⚠️ ai-expert 第 1 點(必修):用 fvm-abs 存「絕對 fvm 路徑」,不可用會跑 `command -v fvm` 的 fvm-wrapper——
#    designer 機器 fvm 在 ~/.pub-cache/bin(不在 agent shell PATH),wrapper 展開會空 → 快取每票失效 → #7 沒解。
#    欄位用 `|` 分隔(非空白):路徑含空白(/Users/x/My Projects/…)時 ${x%% *} 會截斷 → 永久 cache miss(第 1(b) 點)。
expand_token() {  # "v1<TAB><body>" → 當前 worktree 可用的 FLUTTER_BIN(輸出 "<head>[ flutter]")
  [ "${1%%	*}" = "$CACHE_FMT" ] || return       # 格式哨兵不符 → 視為 miss
  body=${1#*	}
  case "$body" in
    "sdk-rel|"*) printf '%s'        "$WT/${body#sdk-rel|}";;   # head = 整串(無尾 " flutter")
    "fvm-abs|"*) printf '%s flutter' "${body#fvm-abs|}";;      # head = fvm 絕對路徑
    "bare")      printf 'flutter';;
  esac
}

# 1) 先試共用快取 —— 展開後驗證(毫秒級,不跑慢的 --version)→ 命中即 0 次 probe
if [ -z "$FLUTTER_BIN" ] && [ -f "$CACHE_DIR/flutter-kind" ]; then
  CAND=$(expand_token "$(cat "$CACHE_DIR/flutter-kind")")
  HEAD=${CAND% flutter}                           # 去掉結尾 " flutter" 取執行檔頭;含空白路徑安全(不用 %% *)
  if [ -n "$CAND" ] && { case "$HEAD" in /*) [ -x "$HEAD" ];; *) command -v "$HEAD" >/dev/null 2>&1;; esac; }; then
    FLUTTER_BIN="$CAND"
  fi
fi

# 2) 仍空 → 走 probe 區塊(變更 2),probe 出 KIND(sdk-rel|… / fvm-abs|… / bare)

# 3) ⚠️ ai-expert 第 2 點(必修):probe 後仍空 → FAIL,不可把空 KIND 寫進共用快取
[ -z "$FLUTTER_BIN" ] && { echo "FAIL: no working flutter found (tried fvm + bare)." >&2; exit 1; }
printf '%s\t%s\n' "$CACHE_FMT" "$KIND" > "$CACHE_DIR/flutter-kind"   # 共用,帶格式哨兵,跨票安全
printf '%s\n' "$FLUTTER_BIN" > .dev/ui-tweak/flutter-bin             # 展開後的值,僅本 worktree 用
```

`preview.md` Step 0 的 inline fallback 套同一套「先讀 `$CACHE_DIR/flutter-kind` → 展開」。

> **ai-expert 第 3 點(REPO_KEY 撞名)— 本輪先忽略(Charlie 決定 2026-06-09)**:`basename($TRUNK)` 理論
> 上兩個同名 repo 會共用一份快取。實務上 gogox 不會有兩個同名 flutter repo,風險低 → 暫不加 path hash。
> 若日後出現同名衝突,fix 很小(`REPO_KEY=$(basename "$TRUNK")-$(echo "$TRUNK"|shasum|cut -c1-8)`)。**已記錄為已知債。**

**變更 2 — pinned repo 直指 SDK 實體 binary**(解 wrapper overhead)

`fvm flutter` 每次都先啟動 fvm 解析 pinned 版本再轉呼;改成優先解析 fvm 建的 symlink 實體檔。
**probe 時同時決定 `KIND` token**(供變更 1 的快取使用,跨票安全):

```bash
SDK_REL=".fvm/flutter_sdk/bin/flutter"             # repo 相對位置(fvm use/install 後存在)
# FVM_BIN 沿用現有 probe 的探測(start.md:67-68 / preview.md:56:含 ~/.pub-cache/bin/fvm,fvm 常不在 PATH)
if [ "$PINNED" = 1 ]; then
  if [ -x "$WT/$SDK_REL" ] && probe "$WT/$SDK_REL"; then
    FLUTTER_BIN="$WT/$SDK_REL"; KIND="sdk-rel|$SDK_REL"     # 直指,免 wrapper;快取存「相對」token
  elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then
    FLUTTER_BIN="$FVM_BIN flutter"; KIND="fvm-abs|$FVM_BIN" # 退回 wrapper;快取存「fvm 絕對路徑」(machine-stable,非 $WT-相對)
  fi
elif probe flutter; then
  FLUTTER_BIN="flutter"; KIND="bare"
fi
```

> **為何 `fvm-abs|$FVM_BIN` 而非 `fvm-wrapper`(ai-expert 第 1 點)**:designer 機器的 fvm 在
> `~/.pub-cache/bin`、不在 agent shell 的 PATH。若快取只記「用 fvm wrapper」、展開時再 `command -v fvm` →
> 找不到 → 展開成空 → 每票 cache miss、重 probe → #7 等於沒解。`$FVM_BIN` 是探測時已確定的絕對路徑、
> 跨票穩定(fvm binary 不隨 worktree 變),存它才能讓 designer 機器真的命中快取。

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
| worktree 內 `.dev/ui-tweak/flutter-bin` | gitignored(`commands/dev/dev/verify.md:110-117` 加 `.dev/` 進 .gitignore + evict)+ ui-tweak commit 是 coverage-scoped(`ff.md:289` 只 commit UI 檔) | ✅ |

兩道防線:(1) auto-resolve 的絕對路徑只落 per-machine,兩條路都到不了 commit;(2) 唯一 commit 的
override 強制相對路徑。`.fvm/flutter_sdk/bin/flutter` 是 repo 相對位置,每台機器各自解析到自己的 SDK。
→ Charlie 的 `/Users/charlie/...` 不會傳給 Arthur,Arthur 在自己機器 probe 出 `/Users/arthur/...` 存自己
的 `~/.cache`。比現況更安全(現況把 probe 結果直接寫 worktree `.dev/`)。

**可選強化**:把 `.fvmrc` 內容 hash 併進 `REPO_KEY`,SDK 版本一換就自動失效重 probe。

**工作量**:小-中。**風險**:低(三變更都保留現有 probe / wrapper fallback,任一步失敗退回今天行為)。

---

#### B — 預設 staging flavour(解 #3)【Phase 1】

**根因**:`flutter.yaml` 範本是無 flavour 的 `flutter run -d {device} --debug`(L28);設計上要 repo
覆寫,但 gogox-client / gogox-driver-flutter 沒設 → 每次要找 flavour。

**改法(直接寫進 platform 預設 `flutter.yaml`,Q1=同 flavour 拍板 2026-06-09)**:

Charlie 確認兩個 app 的 staging flavour 同名(`stag`),所以**直接 hardcode 在 platform 預設**——
這是 resolver **今天就會讀**的位置,**零 resolver 改動**,ai-expert 第 1 點(registry resolve 不到)
整個消失:

```yaml
# commands/dev/profiles/platform/flutter.yaml(本 repo 內)
# NOTE: `--flavor stag` 是 gogox 兩個 app 的預設;非 gogox flutter repo 若無 stag flavour 會 build 失敗,
#       須在 <repo>/.gogox-claude.yaml 覆寫 ui_preview_cmd/ui_build_cmd(precedence:repo override > platform 預設)。
ui_build_cmd:   flutter build apk --debug --flavor stag
ui_preview_cmd: flutter run -d {device} --debug --flavor stag
```

> **blast-radius(ai-expert 第 2 點,非阻擋)**:這是 platform 預設,套用所有 flutter repo。今天只有 gogox 兩個
> app,且 `preview.md:40` precedence 讓 `<repo>/.gogox-claude.yaml` 覆寫贏過預設 → 任何新 repo 加 override
> 即可。已在 yaml 加註解標示為 gogox-specific 預設。風險:中(未來非 gogox flutter repo 需記得覆寫),非極小。

**為何不放 registry / app repo**:
- 放 registry per-repo yaml → 會踩 ai-expert 第 1 點(resolver 用 `basename($WT)`=票號去找 →
  找不到 → 靜默失效),還要補 resolver。**Q1 同 flavour 後不需要走這條。**
- 放 app repo `.gogox-claude.yaml` → 碰 app repo,且 designer 多一個檔。

`flutter.yaml` 是 platform 預設 → 影響所有 flutter repo,但**任何 repo 仍可用 `<repo>/.gogox-claude.yaml`
覆寫**(precedence 不變),所以日後若有 repo 用別的 flavour,override 即可。Phase 1 對 app repo 仍是**零改動**。

**(選用,非 Phase 1)** 自動偵測 productFlavors / schemes 預設 staging——收益低、複雜度高,寫死即解,
延後。

**工作量**:極小(2 行 yaml,無程式改動)。**風險**:低。

> **B+ (Phase 1 實測後新增,見 §7.3 #1)**:目前 flavor 寫死無 runtime 驗證 → 無 `stag` 的 repo 會吃
> Xcode/gradle 天書 error。後續加強:repo 在 `.gogox-claude.yaml` 宣告 `flavor:` + `start` 偵測 flavor +
> `preview` 偵測不到時 graceful fallback。詳見 §7.3。

---

#### A1 — 從 trunk CoW clone 依賴(解 #1 #2)【折入 Phase 1.5 spike;Phase 1 量測後】

> **batching 更新(2026-06-09,採納 ai-expert 建議)**:A1 不再是獨立 PR-3。理由:A1 單獨只省
> `pod install`(~20-60s),iOS 真正的省時(~60-90s 編譯)在 Phase 1.5;且 A1「不 clone `build/`」的決定本就
> 與 1.5 的 DerivedData 重用同源耦合。故 **A1 + Phase 1.5 合成一個「iOS 加速 spike」**,在 Phase 1(A3+B)
> 量測後一起做,避免先 land 一個低價值、APFS-條件、可能被 1.5 機制取代的 flag 路徑。下文 A1 設計仍有效,
> 只是落地時機改為與 1.5 同批。

**根因**:`/add-worktree` 建 `../<ticket-id>`,新路徑使 iOS `ios/Pods`、`ios/.symlinks` 全冷 → 首次
`pod install` 慢。

**Topology**:

```
~/Projects/gogox-client-flutter     ← 主 checkout,trunk(設計師日常待的地方,Pods 已裝)
~/Projects/CAF-1234                 ← worktree(從 trunk 開,/ui-tweak 一張票一個)
```

**改法(copy-from-trunk via APFS CoW,只 clone「flavour-無關的依賴」)**(`start.md` 第 5 步,
`/add-worktree` 回來後):

> **⚠️ ai-expert 第 2 點(必修):絕不 clone `build/` 與 `.dart_tool/`。** 這兩個目錄記著 trunk 上次建置
> 的 **flavour 與 build mode**,而 lock 檔跨 flavour 相同 → `cmp` 偵測不到差異。情境:trunk 上次跑 `prod`,
> worktree 要跑 `stag`,lock 一致 → 不重建 → 設計師看到的是 **prod 的舊畫面**(且能編譯、不報錯)→ 直接
> 違反 ui-tweak「只改外觀、不換成另一個 app」的鐵則。**只 clone CocoaPods 依賴**(flavour-無關、純套件解析,
> 安全);編譯成果的重用交給 Phase 1.5 的共用 DerivedData(flavour-aware,自我隔離)。

```bash
TRUNK=$(dirname "$(git rev-parse --git-common-dir)")   # 主 checkout 路徑
[ "${UI_TWEAK_COW:-0}" = 1 ] || exit 0                 # feature-flag,預設關;量測後再開
for d in ios/Pods ios/.symlinks; do                    # ← 只有依賴;NO build/ NO .dart_tool/
  [ -e "$TRUNK/$d" ] || continue
  cp -c -R "$TRUNK/$d" "$(dirname "$d")/" 2>/dev/null || true   # cp -c = clonefile(CoW);非 APFS → 失敗即略過
done
cp -c "$TRUNK/ios/Podfile.lock" ios/ 2>/dev/null || true
cmp -s ios/Podfile.lock "$TRUNK/ios/Podfile.lock" || (cd ios && pod install)   # lock 不符才重裝
```

**為何 copy 勝過 symlink**:symlink 會讓 worktree 的 build **寫回 trunk** 的 `Pods`,破壞 worktree 隔離;
copy 各自一份、寫入分流 → 隔離保住、無跨票 stale。APFS CoW 讓「各自一份」成本趨近於零。

**Phase 1 範圍(已拍板,2026-06-09)**:只 CoW clone `ios/Pods` + `ios/.symlinks` + `Podfile.lock`;
`build/` / `.dart_tool/` **不 clone**(讓它各自重建,正確性優先)。feature-flag `UI_TWEAK_COW`,預設關,
**最後 land**(避免 A1 的不確定性連累 A3/B 信心,ai-expert 第 6 點)。

**誠實預期(ai-expert Q3)**:單獨 A1 對 iOS 只省下 `pod install`(~20-60s),**省不到 native 編譯
(~60-90s)** —— 那塊要靠 Phase 1.5。所以 A1 自己不是 iOS 2 分鐘的主力,真正主力是下面的 Phase 1.5。

**對 #2 的回應**:**保留 worktree**(隔離 + 乾淨 PR branch;`ff.md` 已記錄 in-place 會 orphan 編輯 +
重問 C-WT 兩個 bug),只把冷建置成本拿掉——隔離留著、慢的修掉。

**前提 / fallback**:cache 與 worktree 須同一 APFS volume 才有 CoW(`cp -c`);偵測到非 APFS / 跨 volume
→ 退回不 clone(維持現況冷建),不可硬塞。

**工作量**:中。**風險**:低(各自一份、不 clone build、無 stale;lock 不一致就重裝)。

---

#### Phase 1.5 — iOS 加速 spike(A1 Pods clone + 共用 DerivedData,#1 的真正主力)

> 這個 spike **打包 A1(上節的 Pods CoW clone)+ 共用 DerivedData**,因為兩者同源:A1 提供 flavour-無關的
> 依賴、DerivedData 提供 flavour-aware 的編譯重用,合起來才是完整的 iOS 提速。Phase 1 量測後啟動。

> ai-expert Q3 + Pro tip:**這才是砍掉 iOS ~60-90 秒編譯的唯一槓桿**;A1 只解 `pod install`。和 A1
> 第 2 點綁在一起設計——A1 不 clone `build/`(避免 flavour stale),編譯重用改由 DerivedData 提供
> (DerivedData 按 build configuration 分目錄,**flavour-aware、自我隔離**,所以安全)。

**目標**:讓不同 worktree 共用同一份已編譯的 Pods `.o` / module cache,新票首次只重編改動的少數檔
(~10-20s),而非整包冷編(~60-90s)。

**機制(已知有實作卡點 — 老實列,需 spike 決定)**:
DerivedData 預設在 `~/Library/Developer/Xcode/DerivedData/<proj>-<pathhash>/`,以 **workspace 路徑 hash**
為 key → 新 worktree 路徑不同 → 視為全新 → 冷編。要共用必須 pin 一個「不隨 worktree 路徑變」的位置:

| 候選 | 做法 | 卡點 / 副作用 |
|---|---|---|
| (a) `xcodebuild -derivedDataPath <固定>` | 每次 build 指定共用路徑 | **`flutter run` 不開放這個 flag** → 要繞開 flutter run,複雜 |
| (b) 全域 `defaults write com.apple.dt.Xcode IDECustomDerivedDataLocation` | 設機器全域 DerivedData 位置 | 影響**整台機器所有 Xcode build**,且 Xcode 仍按 `<proj>-<pathhash>` 分子目錄 → **未必達成共用**,需驗證 |
| (c) CoW clone trunk 的 DerivedData 到新 worktree 的 hash 目錄 | 算出 worktree 的 pathhash,`cp -c` trunk 的 DerivedData 過去 | 要逆推 Xcode 的 hash 演算法(已知但脆弱) |
| (d) `FLUTTER_XCODE_*` env 透傳(ai-expert 補) | `flutter run` 會把 `FLUTTER_XCODE_<SETTING>` 當 xcodebuild build setting 傳入 → 用 `FLUTTER_XCODE_SYMROOT` / 固定 `CONFIGURATION_BUILD_DIR` pin 輸出,不離開 flutter run | 比 (a)/(c) 穩;需驗證能否真的共用編譯快取 |
| (e) 穩定 worktree 路徑(ai-expert 補) | symlink `~/.ui-tweak/active` → 當前 worktree,透過 symlink build → pathhash 跨票不變 | 一次只能一張票(符合 #2 單票假設);需確認 flutter/Xcode 跟 symlink 的互動 |

**結論:Phase 1.5 = 一個 time-boxed spike**,先驗證哪個機制在 `flutter run` 下真的拿得到編譯重用、副作用
可接受;**決定機制前不寫進 production**。**優先試 (d) `FLUTTER_XCODE_*` 與 (e) 穩定路徑 symlink**(不離開
flutter run、比逆推 hash 穩);(a) 被 flutter run 擋、(b) 仍按 pathhash 分目錄 → 兩者多半無效。

**決策 gate**:先 land Phase 1(A3+B,可選 A1),**實測 iOS 首次啟動從 2 分鐘降到多少**;若剩餘編譯時間仍痛 →
才投資 Phase 1.5 spike。不在量測前先做最複雜的這塊。

**工作量**:spike 小,落地視機制而定(中-大)。**風險**:(a)/(c) 脆弱、(b) 全域副作用 → 故先 spike。

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

> **Phase 1 實測補充(見 §7.3)**:#2b iOS 無 `idb`/`simctl` 輸入橋 → 連 capture-only 都只能截當下、
> 無法導航(文件需補環境需求 + HARD BOUNDARY 註);#2c cascade 多台在線不分平台 → 優先選 ticket/參考圖
> 平台或詢問;#3b 細微顏色單張看不出 → demo 改 before/after 並排。三點都併入本群組設計。

---

### 群組 D:審查並行加速(解 #6)

**現況**:`audit.md:61` 已寫「Always run BOTH judges in parallel (one message, two Agent calls)」。
designer 仍覺序列,可能:(a) 實際沒真並行;(b) opus `dev-reviewer` 本身慢;(c) format + 結構 pre-pass 在
judge 前序列跑。

**改法**:

1. **驗證並強制真並行**:確保 orchestrator(含 `workflows/dispatch-fanout.workflow.js` 的 `runUiTweak`)真在
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

## 4. 優先順序與 batching(已採納 ai-expert:拆 PR + A1 折入 1.5)

| 順序 | 項目 | PR | 風險 | 備註 |
|---|---|---|---|---|
| 1 | **A3**(快取 token + 直指 SDK) | PR-1,先 land | 低、自包含 | 快取存相對 token(修第 4 點);REPO_KEY 撞名先忽略(第 3 點) |
| 2 | **B**(`flutter.yaml` 加 `--flavor stag`) | PR-2 | 極低 | Q1 同 flavour → 寫 platform 預設,零 resolver 改動(第 1 點消失) |
| **1.1** | **dogfooding hotfix**(F1 macOS `timeout` + F2 `pubspec.lock` 污染) | 待開,優先於後續加強 | 低 | CAF-609 實測的真 bug;改 `preview.md` Step 1 + `apply.md` Step 5(§7.2) |
| 3 | **Phase 1.5 = iOS 加速 spike**(A1 Pods clone + 共用 DerivedData) | spike,Phase 1 量測後 | 中 | A1 折入此處(單獨價值低);A1 部分用 `UI_TWEAK_COW` flag、不 clone `build/`;DerivedData 機制待 spike |
| 4 | **D**(審查真並行 + diff 算一次) | — | 低 | |
| 5 | **C**(記裝置 + 擷取時機 + #2b/#2c/#3b,§7.3) | — | 中 | iOS idb 文件、cascade 平台優先、before/after 並排 |
| 5+ | **B+**(flavor 偵測 + repo 宣告 `flavor:`,§7.3 #1) | — | 中 | 無 `stag` 的 repo 不再吃天書 error |
| 6 | **A2**(常駐 flutter run + HMR) | — | 大 | 第二波 |
| 7 | **E**(cursor/codex) | — | 大 | roadmap,先 rules 檔 MVP |

---

## 5. 決策記錄

| # | 決策 | 結論 | 狀態 |
|---|---|---|---|
| 1 | #2 worktree 留或拿掉 | **保留**,靠 A1/Phase 1.5 消除慢的部分 | ✅ Charlie 認可 |
| 2 | A3 override 對絕對路徑 | **擋掉**(`/` 開頭忽略) | ✅ |
| 3 | B flavour 放哪 | **`flutter.yaml` platform 預設**(Q1=同 flavour,零 resolver 改動) | ✅ 2026-06-09 |
| 4 | A1 link 機制 | **copy-from-trunk(`cp -c` CoW)** | ✅ 2026-06-09 |
| 5 | A1 clone 範圍 | **只 clone `ios/Pods`/`ios/.symlinks`/`Podfile.lock`**;不 clone `build/`/`.dart_tool/`(ai-expert 第 2 點) | ✅ 2026-06-09 |
| 6 | A3 快取格式 | **存 token**(`sdk-rel\|<rel>`/`fvm-abs\|<fvm絕對>`/`bare`)+ `v1` 格式哨兵,不存 `$WT` 絕對路徑(ai-expert 第 4+1 點) | ✅ 2026-06-09 |
| 7 | REPO_KEY 撞名(ai-expert 第 3 點) | **先忽略**,記為已知債(gogox 無同名 flutter repo) | ✅ 2026-06-09 |
| 8 | iOS 編譯重用 | **Phase 1.5 共用 DerivedData**,先 spike 驗證機制 + 量測 Phase 1 後再做 | ✅ 2026-06-09 |
| 9 | batching | Phase 1 = **PR-1 A3 → PR-2 B**;**A1 折入 Phase 1.5「iOS 加速 spike」**(ai-expert 建議:A1 單獨價值低、與 1.5 同源),Phase 1 量測後做 | ✅ 2026-06-09 |
| 10 | #4 opt-in deep-link 導航 | 預設**不開** | ⏳ 待 C |
| 11 | C 取捨:抓對頁 vs 零等待 | (未定) | ⏳ 待 C |
| 12 | #5 MVP rules vs 完整移植 | 先 **MVP rules 檔** | ⏳ 待 E |
| 13 | F1 macOS `timeout` 修法 | bounded-wait 不依賴 `timeout`(純 `flutter devices` 輪詢 + 計數 / `gtimeout` / 背景+kill),文件明寫禁用 `timeout` | ⏳ 待修(§7.2) |
| 14 | F2 `pubspec.lock` 污染 | `apply` 記 base_ref 前還原環境 setup 噪音(`git checkout -- pubspec.lock`) | ⏳ 待修(§7.2) |
| 15 | #1 flavor 偵測(B+) | repo 宣告 `flavor:` + `start` 偵測 + `preview` graceful fallback | ⏳ 待設計(§7.3) |
| 16 | #2b iOS 導航 | iOS 無 `idb` → capture-only 只截當下;文件補環境需求 + HARD BOUNDARY 註 | ⏳ 待 C |
| 17 | #2c cascade 平台優先 | 多台在線優先選 ticket/參考圖平台或詢問 | ⏳ 待 C |
| 18 | #3b before/after | demo 並排 before/after 取代單張截圖 | ⏳ 待 C |

---

## 6. Phase 1 開工步驟(2 個 PR,全部在 gogox-claude repo 內)

> **app repo(`gogox-client-flutter`)零 committed 改動。**

**PR-1 — A3(先 land,低風險)**
1. 改 `commands/design/ui-tweak/start.md`:`~/.cache/ui-tweak/<REPO_KEY>/flutter-kind` 共用快取存
   **token + `v1` 格式哨兵**(`sdk-rel|<rel>` / `fvm-abs|<fvm絕對>` / `bare`)+ 直指
   `$WT/.fvm/flutter_sdk/bin/flutter` + 選用相對 `flutter_bin` override(擋絕對路徑)。
   **保留現有 empty-`FLUTTER_BIN` FAIL guard 於寫快取之前**(ai-expert 第 2 點)。
2. 改 `commands/design/ui-tweak/preview.md` Step 0:同套「讀 token(驗格式哨兵)→ 展開 `$WT` → 驗 `-x`」fallback。

**PR-2 — B(極小)**
3. 改 `commands/dev/profiles/platform/flutter.yaml`:`ui_preview_cmd` / `ui_build_cmd` 加 `--flavor stag`
   (兩個 app 同 flavour)+ gogox-specific 註解。無程式改動。

**量測 gate** → 跑真實 ticket,量 iOS 首次啟動從 ~2 分鐘降到多少 → 決定是否啟動下一步。

**Phase 1.5 — iOS 加速 spike(A1 + 共用 DerivedData,量測後才做)**
4. A1:`UI_TWEAK_COW=1` 時 `cp -c` CoW clone trunk 的 `ios/Pods` / `ios/.symlinks` / `Podfile.lock`
   (**不含 `build/` / `.dart_tool/`**);非 APFS 略過;`Podfile.lock` 不符才 `pod install`。
5. DerivedData:time-boxed spike 驗證機制(優先試 (d) `FLUTTER_XCODE_*` / (e) 穩定路徑 symlink),
   確認 `flutter run` 下真能共用編譯快取且副作用可接受後,才與 A1 同批落地。

---

## 7. Phase 1 dogfooding 回饋(CAF-609,2026-06-09)

> Phase 1(A3 + B)已在真實票 **CAF-609**(運輸選車頁灰底)跑完整 `/ui-tweak` 流程:draft PR #523、
> ticket In Review、截圖/錄影已回貼 Linear。以下是該次跑出來的 7 點回饋,依「真 bug / 加強 / 已知」
> 分類並標落點。**本節是這批回饋的權威記錄**;群組 B/C 只反向連結到這裡,不重複內文。

### 7.1 分類總表

| # | 回饋 | 性質 | 落點 |
|---|---|---|---|
| **F1** | `timeout` 在 macOS 不存在 → device 偵測靜默失敗 | 🔴 **真 bug**(這次害 iOS 假裝沒 device) | `preview.md` Step 1 — bounded-wait 從散文改成 macOS-safe 明確機制 |
| **F2** | `pubspec.lock` 污染 audit diff(`/add-worktree` pub get 在 base_ref 之前髒了 tree) | 🔴 **真 bug** | `apply.md` Step 5 — 記 base_ref 前還原環境 setup 噪音 |
| #1 | flavor 寫死無 runtime 驗證;無 `stag` 的 repo 吃天書 build error | 🟡 加強(B+) | 群組 B — repo 宣告 `flavor:` + 偵測/graceful fallback |
| #2b | iOS 連 capture-only 都只能截當下(無 idb 無法導航);Android 可 adb | 🟡 文件補強 | 群組 C + preview/demo HARD BOUNDARY 補註 |
| #2c | 多 device cascade 不分平台,挑第一個 already-running 就用 | 🟡 加強 | 群組 C / `preview.md` Step 1(a) — 平台優先序 |
| #3b | 細微顏色(#F7F8F8 vs 白)單張截圖看不出,要 before/after 並排 | 🟡 加強 | 群組 C(demo 擷取) |
| #2d | iOS build 慢(pod install + Xcode build ~2.5min) | ⚪ 已知 | = Phase 1.5(待量測 gate 決定) |

### 7.2 兩個真 bug(已驗證缺陷,優先於後續加強修)

**F1 — macOS 無 `timeout`,device 偵測靜默失敗** 🔴
- **現象**:跑的 agent 把 `preview.md` Step 1 的 device 等待寫成 `timeout 90 flutter devices --machine`;
  macOS 不內建 `timeout` → 前幾次 poll 全回空、誤判「沒有 device」,其實 iPhone 17 Pro 早已 booted。
- **根因**:`preview.md` Step 1(a)/(b) 只用**散文**寫「grace poll ~10s」「bounded wait ~60s」,沒指定
  **怎麼**等;agent 自然抓 `timeout`,踩到 macOS 缺指令。bug 不在檔案的字面字串,而在「指定了等多久、
  沒指定用什麼等」這個缺口。
- **修法**:把 bounded-wait 改成不依賴 `timeout` 的明確機制 —— 純 `flutter devices --machine` 輪詢 +
  迴圈計數器(或偵測 `command -v gtimeout`、或背景啟動 + `kill`),並在文件**明寫「macOS 無 `timeout`,
  禁用」**。
- **檔案**:`commands/design/ui-tweak/preview.md` Step 1。

**F2 — `pubspec.lock` 污染 audit diff** 🔴
- **現象**:`/add-worktree` 跑 `flutter pub get` 改了 `pubspec.lock`;`apply.md` Step 5 在那之後才記
  `base_ref = HEAD`(指 pub get 之前的 commit)→ `git diff base_ref` 含 `pubspec.lock` → 進 audit 的
  frozen set,需手動 `git checkout -- pubspec.lock` 才讓 audit 只看到那一個 UI 檔。
- **根因**:`apply.md:162` 記 base_ref 時,working tree 已被環境 setup(pub get)弄髒,base_ref 卻指
  更早的 HEAD。`preview.md` Step 3 的 F3 quarantine 只還原 audit-set **之外**的檔,但 `pubspec.lock`
  已落在 base→working diff 裡 → 已被算進 audit-set → 救不到,必須在 **base_ref 源頭**修。
- **修法**:`apply.md` Step 5 記 base_ref 前,先還原環境 setup 噪音(flutter platform block 內
  `git checkout -- pubspec.lock 2>/dev/null || true`),讓 frozen set 從一開始就只含 designer 的 UI 編輯;
  不靠下游手動 checkout。
- **檔案**:`commands/design/ui-tweak/apply.md` Step 5(flutter platform only)。

### 7.3 加強(折入既有群組)

- **#1 flavor 偵測(B+)** → 群組 B:flavor 寫死在 platform 預設、**無 runtime 驗證**;Android 看
  `android/app/build.gradle` 的 `productFlavors`、iOS 看 `ios/.../xcschemes/` 的 scheme 是否有 `stag`——
  兩邊都沒檢查,直接賭它存在(CAF-609 剛好有 `stag` scheme 才過,純屬幸運)。Android flavor(gradle
  productFlavor)與 iOS flavor(Xcode scheme)是**兩套東西**,只是都叫 `stag` 才沒爆。改進:(i) 讓 repo
  在 `.gogox-claude.yaml` 宣告 `flavor:`(現只有 `product:`),platform 預設只當 fallback;(ii) `start`
  解析環境時順手**偵測 flavor**(像 flutter-bin 那樣 probe + cache),`preview` 前偵測不到目標 flavor →
  graceful no-flavor build 或給看得懂的訊息,而非讓 build 自爆。
- **#2b iOS 無輸入橋** → 群組 C + HARD BOUNDARY 補註:iOS sim 這台機器無 `idb`、`xcrun simctl` 不支援
  tap/輸入 → agent 連 capture-only 都只能截**當下**畫面、無法導航;Android 可 `adb shell input tap/text`
  導到目標頁。文件補:iOS 自動導航/demo 需在環境需求列 `idb`,否則 iOS 一律「人導、agent 截」。這也正好
  說明 demo 設計成 capture-only 的原因,但現況文件沒講明「iOS 連 capture-only 都受限於無輸入橋」。
- **#2c device cascade 不分平台** → 群組 C / `preview.md` Step 1(a):Android emulator 與 iOS sim 同時開時,
  cascade 取「第一個 already-running device」,不問也不分平台(CAF-609 是手動選了 Android)。改進:多台在線時
  優先選與 ticket / 參考圖一致的平台,或直接問。
- **#3b 細微顏色難驗收** → 群組 C(demo 擷取):#F7F8F8 vs 純白在單張截圖幾乎看不出差別,"show me on a
  phone" 對這種改動說服力低 → **before/after 並排**比單張有用得多。

### 7.4 已知(不另開項)

- **#2d iOS build 慢(pod install + Xcode build ~2.5min)**:pre-warm 只解 sim 開機、解不了 build 時間,
  對「show me」互動流程是偏長等待 → 正是 **Phase 1.5**(A1 Pods clone + 共用 DerivedData)的標的,待量測
  gate 數字決定是否啟動。
