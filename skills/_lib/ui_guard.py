#!/usr/bin/env python3
# ui_guard.py — PreToolUse hard-block for the /ui-tweak skill.
#
# This is the physical wall that keeps a UI Designer's session from touching
# anything other than pure-visual values. It is invoked by a PreToolUse hook
# registered in ~/.claude/settings.json (see install.sh) on every
# Edit | Write | MultiEdit | Bash call.
#
# Protocol (Claude Code hooks):
#   stdin  : JSON {tool_name, tool_input, cwd, ...}
#   exit 0 : allow the tool call
#   exit 2 : BLOCK the tool call; stderr is shown to Claude as the reason
#   any other non-zero / crash: treated as a non-blocking error by the harness,
#                               so we MUST NOT rely on crashes to block. We
#                               fail CLOSED on every reachable decision path.
#
# Activation is gated on a sentinel file `.dev/ui-designer-mode.json` written by
# the skill at start and removed at exit. No sentinel anywhere up the tree =>
# this guard is a strict no-op, so normal /dev and /port work is never affected.
#
# Single source of truth: the rules below are embedded Python (no YAML/JSON parse
# dependency on the security boundary). Repo-specific extra globs may be supplied
# through the sentinel ("extra_pure_ui_globs" / "extra_forbidden_globs").

import json
import os
import re
import sys

SENTINEL_NAME = "ui-designer-mode.json"
SENTINEL_REL = os.path.join(".dev", SENTINEL_NAME)


# ---------------------------------------------------------------------------
# Result helpers — exit 2 + stderr blocks; exit 0 allows.
# ---------------------------------------------------------------------------
def allow():
    sys.exit(0)


def block(reason):
    sys.stderr.write(
        "BLOCKED by /ui-tweak guard: " + reason + "\n"
        "This session is in UI-Designer mode — only pure-visual value changes "
        "(dp/sp sizes, colors, padding, radius, alpha, elevation, font size/weight) "
        "to UI files are permitted. Logic, structure, build config, and source "
        "rewrites are not.\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Per-platform path rules. Precedence: FORBIDDEN > PURE_UI > MIXED > deny.
#   PURE_UI : whole file is UI; value-only diff still enforced for safety.
#   MIXED   : UI + logic share the file; allowed ONLY if value-only diff passes.
# Globs support ** (cross-directory) and * (within a segment).
# ---------------------------------------------------------------------------
RULES = {
    "android": {
        "pure_ui": [
            "**/res/values*/dimens*.xml",
            "**/res/values*/colors*.xml",
            "**/res/values*/styles*.xml",
            "**/res/values*/themes*.xml",
            "**/res/values*/attrs*.xml",
            "**/res/layout*/**",
            "**/res/drawable*/**",
            "**/res/color*/**",
            "**/res/font*/**",
            "**/res/mipmap*/**",
        ],
        "mixed": ["**/*.kt", "**/*.java"],
        "forbidden": [
            "**/build.gradle*", "**/settings.gradle*", "**/gradle.properties",
            "**/gradle/**", "**/*.pro", "**/proguard*/**", "**/AndroidManifest.xml",
            "**/*.properties", "**/*.cfg", "**/*.json", "**/*.yaml", "**/*.yml",
            "**/res/values*/strings*.xml", "**/res/raw/**", "**/res/xml/**",
            # logic by package / by name
            "**/di/**", "**/data/**", "**/domain/**", "**/network/**",
            "**/repository/**", "**/*ViewModel*.kt", "**/*Repository*.kt",
            "**/*UseCase*.kt", "**/*Interactor*.kt", "**/*Presenter*.kt",
            "**/*Manager*.kt", "**/*Service*.kt", "**/*Api*.kt", "**/*Client*.kt",
            "**/*Dao*.kt", "**/*Entity*.kt", "**/*Dto*.kt", "**/*Mapper*.kt",
            "**/*Module*.kt", "**/*Navigator*.kt", "**/*Navigation*.kt",
            "**/*Factory*.kt", "**/*Provider*.kt",
            "**/test/**", "**/androidTest/**", "**/*Test*.kt", "**/*Test*.java",
        ],
    },
    "ios": {
        "pure_ui": ["**/*.storyboard", "**/*.xib", "**/*.xcassets/**"],
        "mixed": ["**/*.swift"],
        "forbidden": [
            "**/*.pbxproj", "**/*.plist", "**/Podfile*", "**/*.entitlements",
            "**/Package.swift", "**/Package.resolved", "**/*.xcconfig",
            "**/*.json", "**/*.yaml", "**/*.yml",
            "**/*ViewModel*.swift", "**/*Service*.swift", "**/*Repository*.swift",
            "**/*Manager*.swift", "**/*Interactor*.swift", "**/*Presenter*.swift",
            "**/*Router*.swift", "**/*Coordinator*.swift", "**/*Network*.swift",
            "**/*Api*.swift", "**/*Client*.swift", "**/*UseCase*.swift",
            "**/*Store*.swift", "**/*Reducer*.swift",
            "**/*Tests*.swift", "**/*Test*.swift", "**/*Tests/**",
        ],
    },
    "flutter": {
        # Flutter UI lives in widgets (MIXED). Pure-UI is limited to design
        # token / theme files, which are the cleanest place to land changes.
        "pure_ui": [
            "**/theme/**", "**/themes/**", "**/*_theme.dart", "**/*_themes.dart",
            "**/tokens/**", "**/*_tokens.dart", "**/design_system/**",
            "**/styles/**", "**/*_styles.dart", "**/*_style.dart",
            "**/*_colors.dart", "**/*_dimens.dart", "**/*_spacing.dart",
        ],
        "mixed": ["**/*.dart"],
        "forbidden": [
            "**/pubspec.yaml", "**/pubspec.lock", "**/*.g.dart",
            "**/*.freezed.dart", "**/*.config.dart", "**/*.gr.dart",
            "**/*.json", "**/*.yaml", "**/*.yml",
            "**/data/**", "**/domain/**", "**/bloc/**", "**/cubit/**",
            "**/*_bloc.dart", "**/*_cubit.dart", "**/*_provider.dart",
            "**/*_controller.dart", "**/*_repository.dart", "**/*_service.dart",
            "**/*_model.dart", "**/*_state.dart", "**/*_event.dart",
            "**/*_api.dart", "**/*_client.dart", "**/*_usecase.dart",
            "**/*_mapper.dart", "**/*_datasource.dart",
            "**/test/**", "**/*_test.dart",
        ],
    },
}


# ---------------------------------------------------------------------------
# Glob -> regex (supports ** across separators, * within a segment).
# ---------------------------------------------------------------------------
def glob_to_regex(glob):
    out = ["(?:^|/)"] if not glob.startswith("**") else [""]
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and glob[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == ".":
            out.append(r"\.")
        else:
            out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def matches_any(rel_path, globs):
    for g in globs:
        if glob_to_regex(g).search(rel_path):
            return True
    return False


def classify_path(rel_path, platform, extra_pure, extra_forbidden):
    rules = RULES.get(platform)
    if rules is None:
        return "UNKNOWN_PLATFORM"
    if matches_any(rel_path, extra_forbidden) or matches_any(rel_path, rules["forbidden"]):
        return "FORBIDDEN"
    if matches_any(rel_path, extra_pure) or matches_any(rel_path, rules["pure_ui"]):
        return "PURE_UI"
    if matches_any(rel_path, rules["mixed"]):
        return "MIXED"
    return "OTHER"


# ---------------------------------------------------------------------------
# Tokenizer — whitespace-insensitive. Emits (kind, text) tuples.
# ---------------------------------------------------------------------------
# A trailing float/long suffix (16f, 0.5F, 10L) is only consumed when NOT
# followed by another letter, so `16dp` -> num `16` + ident `dp` (the `d` of
# `dp` is not eaten). The suffix lives INSIDE the num group so re's lastgroup
# resolves to "num" rather than to an empty trailing optional group.
TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<hexcolor>\#[0-9a-fA-F]{3,8})
    | (?P<num>(?:0[xX][0-9a-fA-F]+|\d+\.\d+|\.\d+|\d+)(?:[fFdDlL](?![A-Za-z]))?)
    | (?P<ident>[A-Za-z_@?\$][A-Za-z0-9_\-]*)
    | (?P<punct>.)
    """,
    re.VERBOSE,
)

UI_UNITS = {"dp", "sp", "px", "pt", "dip", "em"}
# Identifiers that, when they immediately precede a value, mark that value visual.
VISUAL_KEYWORDS = {
    # spacing / sizing (Android, common)
    "padding", "paddingstart", "paddingend", "paddingtop", "paddingbottom",
    "paddinghorizontal", "paddingvertical", "paddingleft", "paddingright",
    "margin", "marginstart", "marginend", "margintop", "marginbottom",
    "width", "height", "size", "minwidth", "minheight", "maxwidth", "maxheight",
    "fontsize", "textsize", "linespacing", "lineheight", "letterspacing",
    "cornerradius", "radius", "elevation", "alpha", "opacity", "shadowradius",
    "strokewidth", "borderwidth", "thickness", "spacing", "gap", "fontweight",
    "weight", "color", "background", "backgroundcolor", "textcolor", "tint",
    "tintcolor", "bordercolor", "strokecolor", "shadowcolor", "foregroundcolor",
    # Flutter / SwiftUI use bare numbers for sizing — these are the governing
    # constructor / parameter names that mark a number as a visual dimension.
    "all", "symmetric", "only", "fromltrb", "fromltwh", "circular", "elliptical",
    "top", "bottom", "left", "right", "start", "end", "horizontal", "vertical",
    "dx", "dy", "blurradius", "spreadradius", "kerning", "tracking", "frame",
    "offset", "scale", "rotationangle", "linewidth",
    # NOTE: minLength/maxLength are intentionally OMITTED. On Android,
    # android:maxLength/android:minLength are SEMANTIC text-length limits (logic),
    # not visual dimensions; treating them as visual would let a logic value
    # through. The SwiftUI Spacer(minLength:) spacing case is sacrificed to keep
    # the boundary fail-closed (a blocked legit edit beats an allowed logic edit).
    "idealwidth", "idealheight", "fontweight", "lineheightmultiple", "borderradius",
    # SwiftUI EdgeInsets positional/labeled edges (top/bottom already present).
    "leading", "trailing",
    # color components — SwiftUI Color(red:green:blue:), storyboard <color .../>,
    # HSB/grayscale. Color-only names with no plausible logic-number alias.
    "red", "green", "blue", "hue", "saturation", "brightness", "white",
}
# ConstraintLayout attribute names tokenize whole (e.g.
# `layout_constraintWidth_percent`, `layout_constraintHorizontal_bias`) so an
# exact-set membership test misses them. These suffixes mark a pure-visual
# fractional value (a bias 0..1 or a width/height percent) and have no logic
# alias.
VISUAL_KEYWORD_SUFFIXES = ("_percent", "_bias")
# High-collision keywords: these double as ubiquitous logic property names
# (a frame index, a stack `top`, a byte `offset`, a model `weight`, a `size`
# count). They may only GOVERN a value when used as a CALL HEAD (`keyword(`) or a
# labeled/attribute key (`keyword=` / `keyword:`) that is NOT a property read on
# a receiver (`recv.keyword = N`). This blocks the accessor-assignment leak
# (`video.frame = 60`, `stack.top = 9`) while keeping every visual idiom
# (Modifier.size(48), EdgeInsets.only(top: 8), .frame(width: 48)) governing.
HIGH_COLLISION_KEYWORDS = {
    "size", "width", "height", "weight", "top", "bottom", "left", "right",
    "start", "end", "offset", "scale", "frame", "color",
}
# A high-collision keyword used as a labeled arg (`fn(width: N)`) or a receiver
# call-head (`recv.size(N)`) only names a logic value unless it is GROUNDED in a
# recognized visual construct. Two grounding sets, applied fail-closed:
#  * VISUAL_CALL_HEADS — the enclosing call head a labeled high-collision arg must
#    sit inside (`.frame(width: N)`, `EdgeInsets.only(top: N)`, `padding(start: N)`,
#    `RoundedCornerShape(...)`). An arbitrary call (`evict(size=N)`, `fetch(end=N)`,
#    `resize(width=N)`, `copy(weight=N)`) is NOT here, so its high-collision arg
#    blocks.
#  * VISUAL_RECEIVERS — the receiver a high-collision CALL HEAD must hang off
#    (`Modifier.size(48)`). A logic receiver (`range.end(9)`, `stack.top(5)`,
#    `table.width(7)`) is NOT here, so it blocks.
# These are the only constructs the SKILL's visual-edit matrix relies on; anything
# unrecognized stays BLOCKED (a blocked legit visual edit beats an allowed logic
# edit). Compared case-folded.
VISUAL_CALL_HEADS = {
    # spacing / inset constructors and edge selectors
    "padding", "paddingvalues", "edgeinsets", "insets", "only", "all",
    "symmetric", "fromltrb", "fromltwh",
    # sizing / layout modifiers
    "size", "width", "height", "frame", "offset", "scale", "requiredsize",
    "defaultminsize", "sizein", "widthin", "heightin", "aspectratio",
    # shape / corner / border constructors
    "roundedcornershape", "cutcornershape", "cornersize", "circular",
    "elliptical", "border", "borderstroke", "stroke",
    # color constructors
    "color", "rgb", "argb", "hsv", "hsl",
    # text / font sizing
    "system", "textstyle", "font",
}
VISUAL_RECEIVERS = {"modifier"}


def is_visual_keyword(ident):
    low = ident.lower()
    if low in VISUAL_KEYWORDS:
        return True
    if any(low.endswith(s) for s in VISUAL_KEYWORD_SUFFIXES):
        return True
    # Android layout attributes carry a `layout_` prefix (android:layout_weight,
    # android:layout_marginTop). Strip it and re-test against the keyword set so
    # `layout_weight`->`weight`, `layout_marginTop`->`margintop` resolve.
    if low.startswith("layout_") and low[len("layout_"):] in VISUAL_KEYWORDS:
        return True
    return False


# Identifier-valued visual swaps allowed in code (e.g. FontWeight.W500 -> W600).
FONT_WEIGHT_RE = re.compile(r"^[wW][1-9]00$")

# PURE_UI files whose numeric content is inherently a dimension/color, so a
# bare (unit-less, un-governed) number is safe there. Other PURE_UI files
# (styles*/themes*/layout, generic token .dart) require a unit or visual keyword.
DIMENSIONAL_PURE_UI_RE = re.compile(
    r"(?:/(?:dimens|colors)[^/]*\.xml"
    r"|_(?:dimens|colors|spacing)\.dart)$", re.I)

# Android resource elements that, even inside a dimens*/colors*.xml file, carry
# a SEMANTIC (non-dimension/non-color) value — a grid column count, maxLines, a
# boolean. A bare number governed by one of these must NOT ride the dimensional
# autopass; it needs the same keyword/unit governance as a non-dimensional file.
SEMANTIC_RES_ELEMENTS = {"integer", "integer-array", "bool", "array",
                         "string-array"}
# Generic `<item ... type="...">` form (used by dimens*/colors*.xml too) may
# declare ANY resource type. Only these declared types carry a dimension/color
# value; every other type (integer, bool, id, ...) is SEMANTIC and must NOT ride
# the dimensional autopass.
VISUAL_ITEM_TYPES = {"dimen", "color", "fraction"}


def semantic_res_element_governs(toks, idx):
    """In a dimensional PURE_UI fragment, return True iff the nearest enclosing
    XML element opening tag to the LEFT of this value is a SEMANTIC resource
    element (<integer>/<bool>/...). The fragment per the SKILL travels
    keyword-anchored, so the tag ident sits in the same old_string. Detection is
    purely positional: find the most recent `<` `ident` pair walking left.

    The generic `<item name="..." type="...">value</item>` form (which dimens*/
    colors*.xml also accept) can declare ANY resource type. Such an item is
    SEMANTIC unless its declared `type` is a dimension/color/fraction OR (when no
    type attr is present) its `name` key's last segment is a visual keyword.
    Fail-closed: an `<item>` with no parseable type AND a non-visual name blocks."""
    j = idx - 1
    while j >= 1:
        k, t = toks[j]
        if k == "ident" and toks[j - 1] == ("punct", "<"):
            name = t.lower()
            if name == "item":
                return _item_value_is_semantic(toks, j)
            return name in SEMANTIC_RES_ELEMENTS
        j -= 1
    return False


def _item_value_is_semantic(toks, item_idx):
    """Given the index of the `item` ident of an opening `<item ...>` tag, parse
    its `type="..."` and `name="..."` attributes and decide whether the element's
    value is SEMANTIC (non-dimension/color). Returns True to SUPPRESS the
    dimensional autopass. Visual iff type in {dimen,color,fraction}, or (when no
    type attr) the name's last :/.-segment is a visual keyword. Anything else
    (including an unparseable/missing type with a non-visual name) is semantic."""
    type_val = None
    name_val = None
    j = item_idx + 1
    n = len(toks)
    # Scan attributes within this opening tag, stopping at the closing `>`.
    while j < n:
        k, t = toks[j]
        if k == "punct" and t == ">":
            break
        if k == "ident" and t.lower() in ("type", "name"):
            if (j + 2 < n and toks[j + 1] == ("punct", "=")
                    and toks[j + 2][0] == "str"):
                val = toks[j + 2][1][1:-1]
                if t.lower() == "type":
                    type_val = val.lower()
                else:
                    name_val = val
                j += 3
                continue
        j += 1
    if type_val is not None:
        return type_val not in VISUAL_ITEM_TYPES
    if name_val is not None:
        key = re.split(r"[:.]", name_val)[-1]
        return not is_visual_keyword(key)
    # No type and no name => cannot prove visual; fail closed (semantic).
    return True


# String-literal value shapes that count as visual (used in XML / token files).
DIM_STR_RE = re.compile(r"^-?\d+(\.\d+)?(dp|sp|px|pt|dip|sdp|ssp|%)?$", re.I)
COLOR_STR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
REF_STR_RE = re.compile(r"^[@?](dimen|color|style|fraction|integer)/", re.I)


def lex(text):
    toks = []
    for m in TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind == "ws":
            continue
        toks.append((kind, m.group()))
    return toks


def is_visual_string(inner):
    return bool(DIM_STR_RE.match(inner) or COLOR_STR_RE.match(inner) or REF_STR_RE.match(inner))


def item_name_keyword_governs(toks, idx):
    """Android styles/themes idiom: `<item name="android:alpha">0.5</item>`.
    The governing key lives in the `name="..."` attribute STRING, and the value
    is the element text node — so nearest_keyword (which walks structural tokens)
    can't reach it past the `>`. Here: if the value at idx sits directly after a
    `>` that closes an opening `<item ... name="KEY" ...>` tag, extract KEY's last
    `:`/`.`-separated segment and test it against the visual-keyword set.
    Fail-closed: a `name` whose key is not visual (e.g. android:maxLength) blocks.
    """
    if not (idx >= 1 and toks[idx - 1] == ("punct", ">")):
        return False
    # Walk left from the `>` to the opening `<item`, capturing a name="..." value.
    j = idx - 2
    name_val = None
    while j >= 1:
        k, t = toks[j]
        if k == "punct" and t == "<":
            # reached the start of a tag; require it to be <item
            nxt = toks[j + 1] if j + 1 < len(toks) else None
            if nxt and nxt[0] == "ident" and nxt[1].lower() == "item":
                break
            return False
        if k == "ident" and t.lower() == "name":
            # name = "KEY"
            if (j + 2 < len(toks) and toks[j + 1] == ("punct", "=")
                    and toks[j + 2][0] == "str"):
                name_val = toks[j + 2][1][1:-1]
        j -= 1
    if name_val is None:
        return False
    key = re.split(r"[:.]", name_val)[-1]
    return is_visual_keyword(key)


def _enclosing_call_head_is_visual(toks, key_idx):
    """For a labeled high-collision arg at `key_idx` (`fn(... width: N ...)`),
    walk left across the current paren group to the call head immediately before
    the enclosing `(` and return True iff that head is a recognized visual call
    head (VISUAL_CALL_HEADS). Fail-closed: an unbalanced/unfound group, or any
    enclosing head not on the visual list (`evict`, `fetch`, `resize`, `copy`,
    `slice`, `query`, `buffer`, `scrollToPage`, ...), returns False so the
    high-collision arg does NOT govern the value. Balanced inner `()` groups
    (e.g. a nested call argument) are skipped so we reach the TRUE enclosing
    paren rather than stopping at an inner one."""
    depth = 0
    j = key_idx - 1
    while j >= 0:
        k, t = toks[j]
        if k == "punct" and t == ")":
            depth += 1
        elif k == "punct" and t == "(":
            if depth == 0:
                # token immediately before this opening paren is the call head
                h = toks[j - 1] if j - 1 >= 0 else None
                if h and h[0] == "ident" and h[1].lower() in VISUAL_CALL_HEADS:
                    return True
                return False
            depth -= 1
        j -= 1
    return False


def _receiver_is_visual(toks, head_idx):
    """For a high-collision keyword used as a CALL HEAD at `head_idx`
    (`recv.size(N)` / `.size(N)`), return True iff it hangs off a recognized
    visual receiver (VISUAL_RECEIVERS, e.g. `Modifier`). A bare call with no
    receiver (`size(N)` with no preceding `.`) or a logic receiver (`range.end`,
    `stack.top`, `table.width`) returns False so it does NOT govern. Fail-closed."""
    left = toks[head_idx - 1] if head_idx - 1 >= 0 else None
    if left != ("punct", "."):
        return False
    recv = toks[head_idx - 2] if head_idx - 2 >= 0 else None
    return bool(recv and recv[0] == "ident" and recv[1].lower() in VISUAL_RECEIVERS)


def nearest_keyword(toks, idx):
    """Walk left a few structural tokens to find a visual keyword that governs
    this value (e.g. `fontSize = 16` or `padding(16)`).

    Positional-argument constructors (`EdgeInsets.fromLTRB(8, 16, 8, 16)`,
    `Offset(dx, dy)`, `Color(r, g, b)`) are handled by skipping over any
    preceding `<value> ,` pair — a numeric/color literal followed by a comma is
    an earlier positional slot, so the chain may continue past it to reach the
    governing call keyword. Every numeric slot in a visual constructor's paren
    group thus resolves to the same head keyword. A non-visual call (e.g.
    `substring(a, b)`) still blocks: its head ident is not a visual keyword, so
    the walk reaches it and returns False.
    """
    seen = 0
    j = idx - 1
    while j >= 0 and seen < 12:
        k, t = toks[j]
        if k == "ident":
            if is_visual_keyword(t):
                # High-collision keywords (size/width/top/frame/...) also name
                # logic properties, so they may govern a value ONLY when grounded
                # in a recognized visual construct — never on the keyword shape
                # alone (which let pure-logic numbers ride through, e.g.
                # `evict(size = N)`, `fetch(end = N)`, `range.end(N)`):
                #  * CALL HEAD (`keyword(`): require a visual RECEIVER, so
                #    `Modifier.size(48)` governs but `range.end(9)`/`stack.top(5)`/
                #    `table.width(7)` and bare `buffer(...)` do NOT.
                #  * LABELED/attr key (`keyword=`/`keyword:`), not a receiver
                #    property read (`recv.keyword = N`): require the ENCLOSING call
                #    head to be visual, so `.frame(width: N)`/`only(top: N)`/
                #    `padding(start: N)` govern but `evict(size=N)`/`resize(width=N)`/
                #    `copy(weight=N)` do NOT.
                if t.lower() in HIGH_COLLISION_KEYWORDS:
                    right = toks[j + 1] if j + 1 < len(toks) else None
                    is_call_head = right == ("punct", "(")
                    is_labeled = right in (("punct", "="), ("punct", ":"))
                    left = toks[j - 1] if j - 1 >= 0 else None
                    accessor_read = left == ("punct", ".")
                    if is_call_head:
                        if not _receiver_is_visual(toks, j):
                            return False
                    elif is_labeled and not accessor_read:
                        if not _enclosing_call_head_is_visual(toks, j):
                            return False
                    else:
                        return False
                return True
            # an unrelated identifier between keyword and value => stop
            return False
        if k == "punct" and t in "=(:":
            j -= 1
            continue
        if k == "punct" and t == ".":
            j -= 1
            continue
        # step over a leading unary minus so `padding(-4)` keeps the keyword chain
        if k == "punct" and t == "-":
            j -= 1
            continue
        # Positional-arg skip: a `,` immediately preceded by a value literal
        # (optionally a unary-minus-prefixed number) is an earlier positional
        # slot — step over the whole `[-] <value> ,` group and keep walking left
        # toward the governing call keyword. Only literal value tokens may be
        # skipped, so an identifier arg (a logic expression) still breaks the
        # chain at the ident check above.
        if k == "punct" and t == ",":
            p = j - 1
            if p >= 0 and toks[p][0] in ("num", "str", "hexcolor"):
                p -= 1
                if p >= 0 and toks[p] == ("punct", "-"):
                    p -= 1
                j = p
                seen += 1
                continue
            # a comma not preceded by a bare value literal => not a positional
            # value list we can vouch for; break the chain.
            return False
        # any other token (string, number, other punct) breaks the chain.
        # CRITICAL: fail closed. The ONLY tokens that may continue the leftward
        # walk are the whitelisted `= ( :`, `.`, the unary `-`, and the positional
        # `,` group handled above. Anything else — arithmetic/comparison operators
        # (> < >= % + * /), other punctuation, string/number/hexcolor literals —
        # must END the walk with False, never fall through. Falling through let a
        # logic number (a loop bound, an index, a factor) bind to a far-left visual
        # keyword across an operator, slipping a pure-logic edit past the gate.
        return False


def classify_token(toks, idx, file_kind, dimensional_pure_ui=False):
    """Return True if token at idx is an allowed *visual literal* value.

    `dimensional_pure_ui` is True only for PURE_UI files whose entire numeric
    content is inherently a dimension/color (e.g. res/values/dimens*.xml,
    colors*.xml, Flutter *_dimens/_spacing token files). For other PURE_UI files
    (styles/themes/layout/generic token .dart) a bare number is NOT auto-visual —
    it must still carry a UI unit or be governed by a visual keyword — so that a
    semantic value like android:maxLength or screenOrientation cannot slip
    through just because the file as a whole is UI-classified.
    """
    kind, text = toks[idx]

    if kind == "hexcolor":
        return True

    if kind == "str":
        inner = text[1:-1]
        # A unit-tagged dimension ("16dp"), a color ("#RRGGBB"), or a dimen/color
        # reference is always visual. A UNIT-LESS numeric string ("10") is treated
        # as visual only in an inherently-dimensional PURE_UI file — otherwise it
        # may be a semantic value (a layout maxLength/maxLines/an index) and must
        # NOT auto-pass just because the enclosing file is UI-classified.
        if COLOR_STR_RE.match(inner) or REF_STR_RE.match(inner):
            return True
        if DIM_STR_RE.match(inner):
            has_unit = bool(re.search(r"(dp|sp|px|pt|dip|sdp|ssp|%)$", inner, re.I))
            # A unit-less numeric string ("0.5", "1") is visual when a visual
            # keyword governs it (the XML attribute ident, e.g. `alpha`,
            # `layout_weight`, sits to the left) — the same governing-keyword
            # test the num branch uses. This stays fail-closed: an UN-governed
            # bare-number string in a non-dimensional file still blocks, so a
            # semantic `maxLength="10"` (key not a visual keyword) keeps blocking.
            auto = dimensional_pure_ui and not semantic_res_element_governs(toks, idx)
            return (has_unit or auto or nearest_keyword(toks, idx)
                    or item_name_keyword_governs(toks, idx))
        return False

    if kind == "num":
        low = text.lower()
        # 0x____ hex => color only in an explicit Color(...) context.
        if low.startswith("0x"):
            return nearest_keyword(toks, idx)
        # number with a trailing UI unit:  16.dp / 16 . sp / 16dp
        nxt = toks[idx + 1] if idx + 1 < len(toks) else None
        nxt2 = toks[idx + 2] if idx + 2 < len(toks) else None
        if nxt and nxt[0] == "punct" and nxt[1] == "." and nxt2 and nxt2[0] == "ident" and nxt2[1].lower() in UI_UNITS:
            return True
        if nxt and nxt[0] == "ident" and nxt[1].lower() in UI_UNITS:
            return True
        # parenthesized unary-minus dimension: `( - NUM ) . unit`  => `(-4).dp`
        # the num is preceded by `(` `-` and followed by `)` `.` <unit>. Only a
        # numeric token can sit here, so this stays numeric-only.
        prev = toks[idx - 1] if idx - 1 >= 0 else None
        prev2 = toks[idx - 2] if idx - 2 >= 0 else None
        nxt3 = toks[idx + 3] if idx + 3 < len(toks) else None
        if (prev and prev == ("punct", "-") and prev2 and prev2 == ("punct", "(")
                and nxt and nxt == ("punct", ")")
                and nxt2 and nxt2 == ("punct", ".")
                and nxt3 and nxt3[0] == "ident" and nxt3[1].lower() in UI_UNITS):
            return True
        # number governed by a visual keyword (fontSize = 16, padding(8), alpha=0.5)
        if nearest_keyword(toks, idx):
            return True
        # styles/themes text-node idiom: <item name="android:alpha">0.5</item>
        if item_name_keyword_governs(toks, idx):
            return True
        # In an inherently-dimensional PURE_UI file (dimens*/colors*/token files)
        # a bare number is a dimension => safe. In OTHER PURE_UI files (styles/
        # themes/layout) a bare number must be unit-tagged or keyword-governed
        # (handled above) — a context-free integer there can be a semantic value
        # (maxLength, screenOrientation, an index) and must NOT auto-pass.
        # Exception: even inside a dimens*/colors*.xml, a number governed by a
        # SEMANTIC resource element (<integer>/<bool>/<*-array>) is a count/flag,
        # not a dp/color — it must NOT ride the autopass (it already failed the
        # keyword test above, so falling through to block is correct).
        if (file_kind == "PURE_UI" and dimensional_pure_ui
                and not semantic_res_element_governs(toks, idx)):
            return True
        return False

    if kind == "ident":
        # font-weight swap (Compose FontWeight.W500) governed by a weight keyword
        if FONT_WEIGHT_RE.match(text) and nearest_keyword(toks, idx):
            return True
        # Android resource reference in XML TEXT-node form: `@dimen/small`,
        # `@color/red`, `?attr/x`. The tokenizer splits this into <prefix-ident>
        # `/` <name-ident>. Only in PURE_UI XML, treat the trailing NAME segment
        # as a visual-reference slot whose value may change, while the resource
        # TYPE prefix (@dimen/@color/?attr) stays verbatim (so @dimen/x->@color/y
        # still blocks — the prefix token itself differs and is not a slot).
        if file_kind == "PURE_UI":
            prev = toks[idx - 1] if idx - 1 >= 0 else None
            prev2 = toks[idx - 2] if idx - 2 >= 0 else None
            if (prev == ("punct", "/") and prev2 and prev2[0] == "ident"
                    and re.match(r"^[@?](dimen|color|attr)$", prev2[1], re.I)):
                return True
        return False

    return False


def skeleton(toks, file_kind, dimensional_pure_ui=False):
    """Replace allowed visual-literal tokens with a placeholder slot, keep all
    structural tokens verbatim. Two edits are value-only-equivalent iff their
    skeletons are byte-identical."""
    out = []
    for i, (kind, text) in enumerate(toks):
        if classify_token(toks, i, file_kind, dimensional_pure_ui):
            out.append("\x00V\x00")
        else:
            out.append(text)
    return out


def value_only_ok(old, new, file_kind, dimensional_pure_ui=False):
    """True iff old->new changes ONLY visual-literal values: same structural
    skeleton, same number of value slots, in the same order."""
    so = skeleton(lex(old), file_kind, dimensional_pure_ui)
    sn = skeleton(lex(new), file_kind, dimensional_pure_ui)
    return so == sn


def _norm_lines(s):
    """Whitespace-normalized, non-empty lines (indentation/reflow ignored)."""
    out = []
    for ln in s.splitlines():
        t = " ".join(ln.split())
        if t:
            out.append(t)
    return out


def reorder_ok(old, new):
    """True iff old->new is a pure SECTION REORDER: the exact same set of
    (whitespace-normalized) lines, only their ORDER changed. This is the SAFE
    definition of a structural reorder — because the line MULTISET is identical,
    no line was added, removed, or modified, so:
      - no new identifier / handler / binding / control-flow / logic is introduced;
      - an id/onClick/attribute cannot be swapped BETWEEN two elements (that would
        change a line's content, breaking the multiset);
      - only whole intact lines move, which is exactly "move this section above
        that one".
    What it deliberately does NOT cover (correctly blocked, route to /dev): reorders
    that must also rewrite a reference (e.g. ConstraintLayout `toBottomOf="@id/x"`),
    single-line layouts, or any edit that changes a line's text. Malformed results
    (e.g. a moved opening tag without its close) still parse here but are caught by
    the build gate. Gated behind the sentinel's allow_reorder flag and the path gate
    (UI-eligible files only)."""
    lo = _norm_lines(old)
    ln = _norm_lines(new)
    if len(lo) < 2 or not ln:
        return False  # nothing to reorder
    return sorted(lo) == sorted(ln)


# ---------------------------------------------------------------------------
# Sentinel discovery
# ---------------------------------------------------------------------------
def find_sentinel(start):
    d = os.path.abspath(start or ".")
    while True:
        cand = os.path.join(d, SENTINEL_REL)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load_sentinel(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        # Sentinel present but unreadable -> fail closed (mode is armed).
        block("UI-Designer-mode sentinel is present but unreadable; refusing all "
              "edits until it is fixed or the skill exits cleanly.")
    if not data.get("armed", False):
        return None
    return data


# ---------------------------------------------------------------------------
# Bash policy while ARMED: block everything. (Architectural decision — see plan.)
#
# Statically gating an arbitrary shell command is unwinnable: build tools
# (gradle / xcodebuild / fastlane / pod) are themselves arbitrary-code-execution
# engines, and there is an endless supply of command-runners (env, xargs,
# interpreters, init scripts, substitution forms). Rather than maintain a
# perpetually-leaking allowlist, the skill confines the ARMED window to pure
# editing: the model uses Edit (value-gated) plus the non-Bash Read/Grep/Glob
# tools, and NOTHING runs through the shell. Build, deploy, screenshot, git, and
# the revert all happen AFTER the skill disarms the sentinel. So while armed we
# block ALL Bash — zero shell attack surface, nothing to keep current.
# ---------------------------------------------------------------------------
def check_bash(cmd):
    block("Bash is disabled while UI-Designer mode is armed. The editing window "
          "is pure-editing only: use the Edit tool for visual value changes and "
          "Read/Grep/Glob to inspect code. Build, deploy, screenshot, git, and "
          "revert all run AFTER the skill disarms the sentinel (flip \"armed\" to "
          "false via an Edit to .dev/ui-designer-mode.json — the one write the "
          "guard permits while armed). This keeps the shell out of the protected "
          "window entirely.")


# ---------------------------------------------------------------------------
# Sentinel disarm carve-out (narrowed)
# ---------------------------------------------------------------------------
# The skill must be able to flip its OWN sentinel to "armed": false before it
# reverts (the guard blocks `git checkout` while armed). But an UNCONDITIONAL
# allow on the sentinel is a model-controlled kill switch: write {"armed":false}
# and the whole guard becomes a no-op, after which any file is editable. So we
# permit a sentinel write ONLY when it is a pure disarm — armed flips to false
# and every other security-relevant field (repo_root, platform, the glob lists)
# is byte-for-byte identical to the currently-armed sentinel. A write that
# relaxes the rules, repoints repo_root, or keeps armed:true is rejected.
# "policy" is locked (R13): otherwise a disarm-then-re-arm could relax strict→open
# (or whitelist a forbidden path via extra_*_globs) mid-run. The disarm carve-out
# only permits flipping "armed" while every locked field stays byte-identical.
_SENTINEL_LOCKED_FIELDS = ("repo_root", "platform", "policy", "allow_reorder",
                           "extra_pure_ui_globs", "extra_forbidden_globs")


def _is_safe_sentinel_disarm(tool_name, tool_input, abs_fp, current):
    # Reconstruct the FULL proposed content of the sentinel after this tool call.
    if tool_name == "Write":
        proposed_text = tool_input.get("content", "")
    elif tool_name in ("Edit", "MultiEdit"):
        try:
            with open(abs_fp, "r") as f:
                proposed_text = f.read()
        except Exception:
            return False
        edits = (tool_input.get("edits")
                 if tool_name == "MultiEdit"
                 else [{"old_string": tool_input.get("old_string", ""),
                        "new_string": tool_input.get("new_string", "")}])
        for e in edits or []:
            old = e.get("old_string", "")
            new = e.get("new_string", "")
            if old and old not in proposed_text:
                return False
            proposed_text = proposed_text.replace(old, new, 1) if old else new
    else:
        return False
    try:
        proposed = json.loads(proposed_text)
    except Exception:
        return False
    if not isinstance(proposed, dict):
        return False
    # Must be a DISARM: armed explicitly false.
    if proposed.get("armed", True) is not False:
        return False
    # Every locked field must be unchanged from the currently-armed sentinel.
    for k in _SENTINEL_LOCKED_FIELDS:
        if proposed.get(k) != current.get(k):
            return False
    return True


# ---------------------------------------------------------------------------
# Edit / Write / MultiEdit gate
# ---------------------------------------------------------------------------
def gate_file_edit(tool_name, tool_input, sentinel):
    platform = sentinel.get("platform", "")
    # repo_root is the containment boundary. The SKILL always writes a correct
    # repo_root; an absent/empty/null one signals a corrupt or hand-rolled
    # sentinel, so we fail CLOSED rather than guess a root from the sentinel's
    # location (guessing risked an off-by-one that admitted sibling directories).
    repo_root = sentinel.get("repo_root")
    if not repo_root:
        block("sentinel has no repo_root; cannot confine edits to a single repo.")
    extra_pure = sentinel.get("extra_pure_ui_globs", []) or []
    extra_forbidden = sentinel.get("extra_forbidden_globs", []) or []
    # Structural reorder mode: when the skill sets allow_reorder=true (because the
    # requirement is a section reorder), an edit may also be a pure line-multiset
    # permutation (see reorder_ok). Only consulted in strict policy.
    allow_reorder = bool(sentinel.get("allow_reorder", False))
    # Content policy within UI-eligible files:
    #   "open"   (default) — ANY edit to a UI-eligible file is allowed at the hook
    #             level. The hook's hard guarantee is file-level containment only
    #             (you still cannot touch a forbidden/logic file, below). "Do not
    #             change logic" inside a UI file is enforced downstream, best-effort,
    #             by ui-verify-agent (an LLM that reads the diff) plus the build gate.
    #   "strict" — the value-only (+ optional reorder) token gate; a hard guarantee
    #             that nothing but visual values / pure reorders change, at the cost
    #             of blocking some legitimate structural UI edits.
    policy = sentinel.get("policy", "open")
    if policy not in ("open", "strict"):
        block("sentinel policy %r is unknown; expected \"open\" or \"strict\"." % policy)

    fp = tool_input.get("file_path") or tool_input.get("path") or ""
    if not fp:
        block("edit with no file_path.")
    # Editing THROUGH a symlink lets a UI-named link inherit a non-UI target's
    # class (and the resolved target may be anywhere). A UI session never needs
    # to edit through a link, so reject before any classification.
    if os.path.islink(fp):
        block("edit target is a symlink (%s); UI-Designer mode does not edit "
              "through links." % fp)
    # Resolve symlinks so classification keys off the REAL on-disk path, never a
    # link's own (potentially UI-shaped) name.
    abs_fp = os.path.realpath(fp)

    # Containment: the edit target's realpath MUST live inside repo_root. A path
    # outside the repo yields a `../`-prefixed rel that still matches **/*.kt etc.
    # Fail closed on any out-of-tree target so the blast radius is this repo only.
    if repo_root:
        real_root = os.path.realpath(repo_root)
        try:
            if os.path.commonpath([abs_fp, real_root]) != real_root:
                block("edit target %s is outside the UI-Designer repo root %s."
                      % (abs_fp, real_root))
        except ValueError:
            # Different drives / un-relatable paths -> not contained.
            block("edit target %s cannot be confined to the repo root." % abs_fp)

    # Sentinel disarm carve-out — see _is_safe_sentinel_disarm. Narrowed so it
    # ONLY permits a write that flips armed without relaxing any other rule; the
    # model can no longer use it as a kill switch to edit anything afterwards.
    if os.path.basename(abs_fp) == SENTINEL_NAME and \
       os.path.basename(os.path.dirname(abs_fp)) == ".dev":
        if _is_safe_sentinel_disarm(tool_name, tool_input, abs_fp, sentinel):
            allow()
        block("the only permitted write to the UI-Designer sentinel is one that "
              "sets \"armed\": false while keeping repo_root/platform/globs "
              "identical; rewriting it to relax rules or change targets is not "
              "allowed.")
    try:
        rel = os.path.relpath(abs_fp, repo_root) if repo_root else abs_fp
    except Exception:
        rel = abs_fp
    rel = rel.replace(os.sep, "/")

    kind = classify_path(rel, platform, extra_pure, extra_forbidden)
    if kind == "UNKNOWN_PLATFORM":
        block("sentinel platform %r has no ruleset; cannot prove this edit is UI-only." % platform)
    if kind in ("FORBIDDEN", "OTHER"):
        block("%s is not a UI-eligible file (%s). Only resource/UI files and "
              "UI-package code may be touched." % (rel, kind))

    # R3: a UI tweak NEVER needs to CREATE a new source file. Reject new-file
    # Write for ALL policies, BEFORE the open short-circuit below. (This check
    # used to live only in the strict branch — under the open short-circuit — so
    # open policy could author a brand-new .kt/.dart/.swift file full of logic in
    # a UI-eligible path. Hoisted here so no policy can create files.)
    if tool_name == "Write" and not os.path.isfile(abs_fp):
        block("Write would create a new file (%s). UI-Designer mode only tweaks "
              "values in existing UI files; it does not add new files." % rel)

    # OPEN policy: file-level containment is the hard guarantee; the file is
    # UI-eligible (proven above) and not forbidden, so permit any edit here —
    # including creating a new UI-eligible file. Whether the change touches LOGIC
    # *inside* this UI file is judged downstream by ui-verify-agent + the build gate
    # (best-effort, by design — see SKILL "The guarantee"). Bash stays fully blocked
    # while armed regardless of policy (check_bash), so edits still cannot be smuggled
    # through the shell.
    if policy == "open":
        allow()

    # ---- strict policy below: value-only (+ optional reorder) hard gate ----
    # Only inherently-dimensional PURE_UI files let a bare (unit-less, un-governed)
    # number count as a visual value. In all other PURE_UI files (styles/themes/
    # layout, generic token .dart) a context-free integer must NOT auto-pass, so
    # a semantic value (maxLength, screenOrientation, an index) cannot slip through.
    dimensional_pure_ui = bool(kind == "PURE_UI" and DIMENSIONAL_PURE_UI_RE.search(rel))

    # Build the list of (old, new) pairs to validate.
    pairs = []
    if tool_name == "Edit":
        pairs.append((tool_input.get("old_string", ""), tool_input.get("new_string", "")))
    elif tool_name == "MultiEdit":
        for e in tool_input.get("edits", []):
            pairs.append((e.get("old_string", ""), e.get("new_string", "")))
    elif tool_name == "Write":
        content = tool_input.get("content", "")
        if not os.path.isfile(abs_fp):
            block("Write would create a new file (%s). UI-Designer mode only tweaks "
                  "values in existing UI files; it does not add new files." % rel)
        try:
            with open(abs_fp, "r") as f:
                old_content = f.read()
        except Exception:
            block("cannot read existing file to validate a full-file Write; use Edit instead.")
        pairs.append((old_content, content))
    else:
        # A gated file-mutating tool whose fields we do not recognize (e.g.
        # NotebookEdit's new_source schema) must NEVER fall through to allow() —
        # an unparsed mutation is an un-validated mutation. Fail closed.
        block("tool %r is not a supported value-only editor in UI-Designer mode; "
              "use Edit/Write/MultiEdit so the guard can validate the change." % tool_name)

    for old, new in pairs:
        if old == new:
            continue
        if value_only_ok(old, new, kind, dimensional_pure_ui):
            continue
        # Structural reorder: only when explicitly enabled for this run, and only
        # as a pure line-multiset permutation (no token added/removed/changed).
        if allow_reorder and reorder_ok(old, new):
            continue
        if allow_reorder:
            block("this edit to %s is neither a visual-value change nor a pure "
                  "section reorder. Reorder mode permits only moving whole intact "
                  "lines (the same lines, reordered) — it does not allow adding, "
                  "removing, or modifying a line (which could change a reference, "
                  "handler, or logic). Reorders that must rewrite a reference (e.g. "
                  "a ConstraintLayout `@id/...`) need the /dev pipeline." % rel)
        block("this edit to %s changes more than visual values. Only numeric "
              "sizes (with dp/sp), colors, and dimen/color references governed "
              "by a visual property may change — identifiers, function names, "
              "control flow, structure, and attribute keys must stay identical."
              % rel)
    allow()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Can't parse the hook payload — don't wedge the session on unrelated
        # tool calls; allow. (The path/value gates only run once we KNOW we are
        # armed AND parsing succeeds.)
        allow()
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or os.getcwd()

    # Only these tools are gated. Anything else: allow.
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"):
        allow()
        return

    sentinel_path = find_sentinel(cwd)
    if not sentinel_path:
        # Try the edited file's own directory for Bash-less file tools.
        fp = tool_input.get("file_path")
        if fp:
            sentinel_path = find_sentinel(os.path.dirname(os.path.abspath(fp)))
    if not sentinel_path:
        allow()  # not in UI-Designer mode -> no-op
        return

    sentinel = load_sentinel(sentinel_path)
    if not sentinel:
        allow()
        return
    sentinel["_path"] = sentinel_path

    if tool_name == "Bash":
        check_bash(tool_input.get("command", "") or "")
    elif tool_name == "NotebookEdit":
        # NotebookEdit's schema (new_source / cell_id) is not the Edit schema and
        # can full-file rewrite a cell with arbitrary logic. There are no notebooks
        # in these repos, so block it outright rather than risk a mis-parse.
        block("NotebookEdit is not permitted in UI-Designer mode; make value-only "
              "changes with the Edit tool so the guard can validate them.")
    else:
        gate_file_edit(tool_name, tool_input, sentinel)


if __name__ == "__main__":
    main()
