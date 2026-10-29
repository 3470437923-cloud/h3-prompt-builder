# -*- coding: utf-8 -*-
"""长视频（首尾相连）提示词的规则、分段数学与字数预算。

对应工作流：`MiniMaxH3LongVideoInNodeLoopT8Advanced`（In-Node Long Video Loop）。
该节点在**一次排队**里串行生成 N 段，每段以固定窗口渲染、拿上一段末尾若干帧当上下文，
最后流式合成一个完整视频。关键后果：

**同一个 `global_prompt` 会被原封不动发给每一段。**

所以旧写法里那种 `[0-3s] [3-7s] [7-12s]` 的绝对时间码是有害的 ——
每段只有约 5 秒，却都想把整段时间轴演一遍，情绪会反复重来。

本模块的事实来源（都核对过源码/官方文档，不是猜的）：
  - 节点源码 `h3_t8/nodes_long_video_in_node_loop_advanced.py`
    `render_window_frames` default=124 min=124 step=17，`context_frames` options=[5,22,39]
    `segment_prompts_json`：「Optional list/object. Each segment may override prompt, seed and note.」
    `total_duration_seconds` min=0.04 max=None（本地不限时长）
  - 官方 CLI 契约 `MiniMax-AI/cli` → `skill/h3-video/SKILL.md#core-limits`：
    提示词 ≤ 7000 字符；输出时长整数 4–15 秒；参考图 ≤ 9 / 视频 ≤ 3 / 音频 ≤ 3、混合 ≤ 12。
  - 节点源码 `h3_t8/prompt_budget_advanced.py` 自述：7000 是**官方 CLI 提交契约**，
    「is not evidence of a 7000-character hard limit in the open-weight tokenizer or local
    ComfyUI runtime」，且该节点「never truncates or silently drops prompt content」。
"""
import json
import re

# ---------------------------------------------------------------- 常量
FPS = 24
WINDOW_FRAMES = 124                 # render_window_frames 的下限，也是省显存的基线
CONTEXT_OPTIONS = (5, 22, 39)       # context_frames 只有这三档
DEFAULT_CONTEXT = 22
WINDOW_STEP = 17                    # render_window_frames 只能按 17 递增

SUBMISSION_CHAR_LIMIT = 7000        # 官方 CLI 提交上限（不是本地运行时限制）
SUBMISSION_LIMIT_SOURCE = ("https://github.com/MiniMax-AI/cli/blob/main/"
                           "skill/h3-video/SKILL.md#core-limits")
SUBMISSION_LIMIT_NOTE_ZH = ("官方 CLI 提交契约。本地 ComfyUI 不拦这个，节点也从不截断，"
                            "但超过这条线的内容在官方 API 路径上会被判超预算。")
CLI_OUTPUT_DURATION = (4, 15)       # 官方 CLI 只接受整数 4–15 秒
LOCAL_DURATION_MAX_HINT = 600.0     # 本地不限，界面只做个防手滑的上限

MODE_CONTINUOUS = "continuous"      # 一整段连续长镜头（最省事）
MODE_SEGMENTED = "segmented"        # 逐段给词，输出 segment_prompts_json
MODE_ZH = {MODE_CONTINUOUS: "一段连续长镜头（无绝对时间码）",
           MODE_SEGMENTED: "逐段给词（segment_prompts_json）"}
MODE_KEYS = [MODE_CONTINUOUS, MODE_SEGMENTED]
MODE_ZH_LIST = [MODE_ZH[k] for k in MODE_KEYS]
MODE_ZH2KEY = dict(zip(MODE_ZH_LIST, MODE_KEYS))

# 一致性措辞：长视频必须显式声明"整段不变"的东西
CONSISTENCY_CONCEPTS = {
    "identity": ("identity", "same subject", "subject identity", "character identity",
                 "facial", "face"),
    "wardrobe": ("wardrobe", "clothing", "outfit", "costume", "garment"),
    "lighting": ("lighting", "light", "illumination", "color temperature"),
    "camera": ("camera direction", "camera", "screen direction", "shot direction",
               "lens direction"),
    "progression": ("progression", "progressive", "continuous", "gradual", "escalat",
                    "arc", "builds", "evolves"),
}
CONSISTENCY_SENTENCE = (
    "Throughout the entire long take, keep the same subject identity, wardrobe, lighting, "
    "camera direction and screen direction, with a continuous and progressive emotional arc."
)
CONSISTENCY_ZH = ("整段长镜头中保持同一人物、服装、光线、镜头方向与银幕方向，情绪连续递进。")

# 绝对时间码：长视频里必须删掉的东西
_TIMECODE_PATTERNS = (
    (re.compile(r"\[\s*\d+(?:\.\d+)?\s*s?\s*[-–~至]\s*\d+(?:\.\d+)?\s*s?\s*\]"), "区间时间码 [0-3s]"),
    (re.compile(r"\bAt\s+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\b"), "At MM:SS.mmm 切换点"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|sec|seconds)\s*[-–~]\s*\d+(?:\.\d+)?\s*(?:s|sec|seconds)\b"),
     "0-3 seconds 这类绝对区间"),
    (re.compile(r"\bfrom\s+\d+(?:\.\d+)?\s*(?:s|sec|seconds)\s+to\s+\d+(?:\.\d+)?\s*(?:s|sec|seconds)\b"),
     "from Xs to Ys 这类绝对区间"),
    (re.compile(r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*[-–~]\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]"),
     "[0:00-0:03] 这类区间"),
)

# ---------------------------------------------------------------- 分段数学


def total_frames(total_seconds: float) -> int:
    return int(round(max(0.0, float(total_seconds)) * FPS))


def segment_plan(total_seconds: float, window: int = WINDOW_FRAMES,
                 context: int = DEFAULT_CONTEXT):
    """把总时长切成分段计划。

    规则（与节点便签里的表一致）：
      第 1 段最多吃满 window 帧；之后每段最多吃 window - context 帧新画面
      （每段仍渲染 window 帧，其中 context 帧是上一段的重叠接缝）。
    返回 [{"index","new_frames","render_frames","overlap"}...]
    """
    total = total_frames(total_seconds)
    if total <= 0:
        return []
    step = max(1, int(window) - int(context))
    plan = []
    remaining = total
    idx = 0
    while remaining > 0:
        cap = int(window) if idx == 0 else step
        take = min(cap, remaining)
        overlap = 0 if idx == 0 else min(int(context), remaining + int(context))
        render = take if idx == 0 else min(int(window), take + int(context))
        plan.append({"index": idx + 1, "new_frames": take, "render_frames": render,
                     "overlap": overlap})
        remaining -= take
        idx += 1
        if idx > 4096:                      # 防呆
            break
    return plan


def plan_summary(total_seconds: float, window: int = WINDOW_FRAMES,
                 context: int = DEFAULT_CONTEXT, steps: int = 8) -> dict:
    plan = segment_plan(total_seconds, window, context)
    new_total = sum(p["new_frames"] for p in plan)
    render_total = sum(p["render_frames"] for p in plan)
    # 经验锚点：单段 124 帧 / 5 步 ≈ 85 秒（工作流便签里的实测量）。线性外推，只当参考。
    per_seg = 85.0 * (max(1, int(steps)) / 5.0)
    lo = per_seg * len(plan) * 0.75 / 60.0
    hi = per_seg * len(plan) * 1.25 / 60.0
    return {
        "segments": len(plan),
        "total_frames": new_total,
        "render_frames": render_total,
        "overhead_pct": (100.0 * (render_total - new_total) / new_total) if new_total else 0.0,
        "plan": plan,
        "seconds": new_total / float(FPS),
        "eta_min_lo": lo,
        "eta_min_hi": hi,
        "per_segment_first": window,
        "per_segment_later": max(1, window - context),
    }


def plan_lines(total_seconds: float, window: int = WINDOW_FRAMES,
               context: int = DEFAULT_CONTEXT, steps: int = 8) -> list:
    """给界面用的一行行说明。"""
    s = plan_summary(total_seconds, window, context, steps)
    if not s["segments"]:
        return ["总时长太短，算不出分段。"]
    out = [
        "总帧 %d @%dfps ≈ %.2fs ｜ 分 %d 段 ｜ 每段渲染 %d 帧（第 1 段 %d，其后 %d）"
        % (s["total_frames"], FPS, s["seconds"], s["segments"], window,
           s["per_segment_first"], s["per_segment_later"]),
        "总渲染量 %d 帧（比直接出一整段多 %.0f%%，换来显存恒定 + 可断点续跑）"
        % (s["render_frames"], s["overhead_pct"]),
        "逐段新增帧：" + ", ".join(str(p["new_frames"]) for p in s["plan"]),
        "参考耗时：约 %.0f–%.0f 分钟（按「单段 124 帧 / 5 步 ≈ 85 秒」线性外推，"
        "实际受显卡、队列和 %d 步设置影响）" % (s["eta_min_lo"], s["eta_min_hi"], steps),
    ]
    if s["segments"] == 1:
        out.append("只有 1 段：已经是单段上限内，用普通三图参考工作流更省事。")
    return out


def segmented_json(plan: list, prompts: list = None, seed_base: int = 123456789) -> str:
    """生成 segment_prompts_json 骨架。节点允许每段覆盖 prompt / seed / note。"""
    rows = []
    for p in plan:
        i = p["index"]
        row = {"note": "第 %d 段" % i}
        if prompts and i - 1 < len(prompts) and str(prompts[i - 1]).strip():
            row["prompt"] = str(prompts[i - 1]).strip()
        else:
            row["prompt"] = ""
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 给 LLM 的规则

LONGVIDEO_RULES_EN = """
=== LONG VIDEO MODE (IN-NODE LOOP / HEAD-TO-TAIL) — READ THIS BEFORE WRITING ===

This prompt is NOT used once. The loop node sends the SAME prompt to EVERY segment
(each segment renders a fixed {window}-frame window, roughly {seg_seconds:.2f} seconds).
Total target duration is {duration:g} seconds, split into {segments} segment(s).

Therefore:

1. NEVER write absolute timestamps anywhere. These are forbidden because every segment
   would try to perform the entire timeline inside its own few seconds:
     FORBIDDEN: [0-3s] [3-7s] [0:00-0:04] "At 00:03.500," "from 2s to 5s"
   Use shot markers WITHOUT any time value: `[Shot 1]`, `[Shot 2]`, `[Shot 3]`.
   `[Shot 1]` still has no timestamp; later shots are `[Shot 2] `, `[Shot 3] ` with
   NOTHING between the marker and the prose. Do not write "At MM:SS.mmm" at all.

2. Write ONE continuous take that stays valid no matter which segment is rendered.
   Describe a sustained, slowly-evolving action rather than a sequence of timed beats.
   Prefer "continuously", "gradually", "slowly", "throughout" over "after 3 seconds".

3. You MUST include this sentence (or a close paraphrase that keeps every concept),
   placed at the end of `detailed_description`:
   "{consistency}"

4. Keep the total output under {char_limit} characters. That is the official MiniMax-H3
   CLI submission ceiling. Cut adjectives before cutting identity facts, camera facts,
   or continuity facts.

5. Still keep the full official six-section structure and keep every section header
   exactly as specified. `detailed_description` should stay around 350-500 words.

6. Describe the emotional arc as a progression across the WHOLE video
   (calm -> building -> resolving), not as per-second instructions.
""".strip()


def build_rules(duration: float, window: int = WINDOW_FRAMES,
                context: int = DEFAULT_CONTEXT) -> str:
    s = plan_summary(duration, window, context)
    return LONGVIDEO_RULES_EN.format(
        window=window,
        seg_seconds=window / float(FPS),
        duration=duration,
        segments=max(1, s["segments"]),
        consistency=CONSISTENCY_SENTENCE,
        char_limit=SUBMISSION_CHAR_LIMIT,
    )


def build_user_lines(duration: float, segment_count: int = 0, mode: str = MODE_CONTINUOUS,
                     window: int = WINDOW_FRAMES, context: int = DEFAULT_CONTEXT) -> list:
    """拼进 user message 的长视频专用几行。"""
    s = plan_summary(duration, window, context)
    lines = [
        "长视频模式（首尾相连循环节点）：",
        "  - 这份提示词会被**原封不动发给每一段**，共 %d 段，每段渲染 %d 帧（≈%.2f 秒）。"
        % (max(1, s["segments"]), window, window / float(FPS)),
        "  - **绝对禁止任何绝对时间码**：不许出现 [0-3s] / [0:00-0:04] / At 00:03.500, 这类写法。",
        "    分镜只写 `[Shot N]` 标记，标记后面**不要**跟任何时间值。",
        "  - 写成一段持续演进的长镜头，用 continuously / gradually / slowly / throughout 这类词。",
        "  - detailed_description 结尾必须补上这句一致性声明（可换措辞但概念不能少）：",
        "      %s" % CONSISTENCY_SENTENCE,
        "  - 全文（含六个段落头）控制在 %d 字符以内（官方 CLI 提交上限）。"
        % SUBMISSION_CHAR_LIMIT,
    ]
    if mode == MODE_SEGMENTED:
        lines += [
            "  - 另外再单独给出一段 `segment_prompts_json`（节点参数名同名），格式为 JSON 数组，",
            "    每个元素形如 {\"prompt\": \"...\", \"note\": \"第 N 段\"}，条数不超过 %d 条。"
            % max(1, s["segments"]),
            "    每段的 prompt 仍是英文六段结构里 detailed_description 的写法，但只描述**该段**的画面。",
        ]
    if segment_count:
        lines.append("  - 分镜数按 %d 个 [Shot N] 写。" % segment_count)
    return lines


# ---------------------------------------------------------------- 校验


def check(text: str, duration: float = 30.0, char_limit: int = SUBMISSION_CHAR_LIMIT) -> list:
    """长视频专有的检查项。返回 [(level, message)]，level 为 err / warn / ok。"""
    out = []
    t = text or ""

    hits = []
    for pat, label in _TIMECODE_PATTERNS:
        for m in pat.finditer(t):
            hits.append("%s → %r" % (label, m.group(0)))
    if hits:
        out.append(("err", "长视频里出现了绝对时间码（每段都会想演完整条时间轴）："
                           + "；".join(hits[:4]) + ("……" if len(hits) > 4 else "")))
    else:
        out.append(("ok", "没有任何绝对时间码"))

    low = t.lower()
    matched = [k for k, words in CONSISTENCY_CONCEPTS.items()
               if any(w in low for w in words)]
    missing = [k for k in CONSISTENCY_CONCEPTS if k not in matched]
    # 只零散提到一两个词不算一致性声明（正文里几乎必然会出现 camera / light），
    # 所以要求覆盖面：5 个概念齐全才算 ok，3-4 个算 warn，≤2 个直接 err。
    if len(matched) <= 2:
        out.append(("err", "缺一致性声明：要显式说明整段保持同一人物/服装/光线/镜头方向 + 情绪递进"
                           "（现在只覆盖 %d/5：%s）"
                    % (len(matched), "、".join(matched) or "无")))
    elif missing:
        out.append(("warn", "一致性声明可能不全（覆盖 %d/5），没提到：%s"
                    % (len(matched), "、".join(missing))))
    else:
        out.append(("ok", "一致性声明覆盖了人物/服装/光线/镜头/情绪递进（5/5）"))

    n = len(t)
    if n > char_limit:
        out.append(("warn", "全文 %d 字符，超出官方 CLI 提交上限 %d 字符（本地能跑，"
                            "官方 API 路径会判超预算）" % (n, char_limit)))
    else:
        out.append(("ok", "全文 %d 字符，在官方 %d 字符预算内（余 %d）"
                    % (n, char_limit, char_limit - n)))

    s = plan_summary(duration)
    if s["segments"] > 1:
        shots = len(re.findall(r"\[\s*Shot\s+\d+\s*\]", t, re.I))
        if shots == 0:
            out.append(("warn", "没写任何 [Shot N] 标记；%d 段的长视频建议至少标出镜头结构"
                        % s["segments"]))
        else:
            out.append(("ok", "有 %d 个 [Shot N] 标记（%d 段长视频）" % (shots, s["segments"])))
    return out


def budget_status(text: str, char_limit: int = SUBMISSION_CHAR_LIMIT) -> dict:
    n = len(text or "")
    return {"chars": n, "limit": char_limit, "left": char_limit - n,
            "pct": min(100.0, 100.0 * n / char_limit) if char_limit else 0.0,
            "over": max(0, n - char_limit)}


def rules_zh() -> str:
    """给界面『长视频规则』页看的说明。"""
    return """长视频（首尾相连）提示词 —— 两条比格式更重要的铁律

一、删掉绝对时间码
    循环节点把 global_prompt 原封不动发给每一段。每段只渲染 %d 帧（≈%.2f 秒），
    而旧写法里的 [0-3s] [3-7s] [7-12s] 会让每一段都想把这十几秒演一遍 → 情绪反复重来。
    改用不含时间值的 [Shot 1] [Shot 2] 标记；需要逐段给词就用节点参数
    segment_prompts_json（每段可覆盖 prompt / seed / note）。

二、通篇写一致性措辞
    补一句"整段保持同一人物、服装、光线、镜头方向与银幕方向，情绪连续递进"。
    %s

三、字数预算
    官方 CLI 契约：提示词 ≤ %d 字符（%s）。
    这是**官方 API 提交线**，不是本地 ComfyUI 的限制 ——
    节点源码自述「never truncates or silently drops prompt content」，
    prompt_budget 节点只提示不拦截。但超了在官方路径上会被判超预算。

四、分段数学（本地循环节点）
    第 1 段吃满 %d 帧；之后每段新增 %d 帧（每段仍渲染 %d 帧，其中 %d 帧是重叠接缝）。
    每段显存恒定，代价是总渲染量多约 20%%，换来可断点续跑、时长不设上限。
    改了模型 / LoRA / 提示词 / 参考图 / 分辨率 / 时长 / 采样参数，必须换一个新的 chain_id，
    否则会被判「合同不匹配」直接报错。

五、其它仍然照旧的硬要求
    六段结构、英文正文、<Picture N> / <Subject N> 标签、<d>[语言] 台词、
    strict_prompt_tags=True 的标签致命性 —— 这些和短的三图参考工作流**完全一样**。
""" % (WINDOW_FRAMES, WINDOW_FRAMES / float(FPS), CONSISTENCY_SENTENCE,
       SUBMISSION_CHAR_LIMIT, SUBMISSION_LIMIT_SOURCE, WINDOW_FRAMES,
       WINDOW_FRAMES - DEFAULT_CONTEXT, WINDOW_FRAMES, DEFAULT_CONTEXT)


def selftest() -> int:
    ok = 0
    bad = []

    def ck(cond, msg):
        nonlocal ok
        if cond:
            ok += 1
            print("   OK   %s" % msg)
        else:
            bad.append(msg)
            print("   FAIL %s" % msg)

    # 便签里的三行表必须能精确复现
    for secs, segs, frames, new in ((12.25, 3, 294, [124, 102, 68]),
                                    (30.0, 7, 720, [124, 102, 102, 102, 102, 102, 86]),
                                    (60.0, 14, 1440, [124] + [102] * 12 + [92])):
        s = plan_summary(secs)
        ck(s["segments"] == segs, "%gs → %d 段（便签说 %d）" % (secs, s["segments"], segs))
        ck(s["total_frames"] == frames, "%gs → %d 总帧（便签说 %d）"
           % (secs, s["total_frames"], frames))
        ck([p["new_frames"] for p in s["plan"]] == new,
           "%gs 逐段新增帧 %s" % (secs, new))
    ck(plan_summary(124 / 24.0)["segments"] == 1, "刚好一屏 → 1 段")

    # 时间码必须被抓出来
    bad_text = ("subject_definitions:\n<Subject 1> ... <Picture 1>\n\ndetailed_description:\n"
                "The camera slowly pushes in. [0-4s] she looks at the camera. "
                "[4-8s] she turns her head. [Shot 2] At 00:09.500, she looks back.")
    rep = check(bad_text, 30.0)
    joined = " ".join(m for l, m in rep if l == "err")
    ck("时间码" in joined, "抓出绝对时间码：%s" % joined[:80])
    ck(any(l == "err" and "一致性" in m for l, m in rep), "抓出缺一致性声明")
    ck(any(l == "warn" and "超" in m for l, m in rep) is False,
       "短文本不该报超字数（%d 字符）" % len(bad_text))

    long_text = bad_text + "\n" + "x" * 7100
    ck(any(l == "warn" and "超出官方 CLI 提交上限" in m for l, m in check(long_text, 30.0)),
       "超 7000 字符报 warn")

    good = ("detailed_description:\n[Shot 1] The camera holds. Throughout the entire long "
            "take, keep the same subject identity, wardrobe, lighting, camera direction and "
            "screen direction, with a continuous and progressive emotional arc.")
    g = check(good, 30.0)
    ck(not [m for l, m in g if l == "err"], "干净文本无 err：%s" % [m for l, m in g if l == "err"])
    ck(any(l == "ok" for l, m in g), "干净文本给 ok")

    b = budget_status("abc")
    ck(b["chars"] == 3 and b["left"] == 6997, "字数预算计数正确")
    ck(budget_status("x" * 7001)["over"] == 1, "超限计数正确")

    rules = build_rules(30.0)
    ck(len(rules) > 400, "system 规则块够长（%d 字符）" % len(rules))
    ck("absolute timestamps" in rules.lower(), "规则块明确禁止绝对时间码")
    ck("7000" in rules, "system 规则块带上 7000 上限")
    ck("7" in rules, "30 秒 → 规则里写明段数")
    ck(CONSISTENCY_SENTENCE[:40] in rules, "规则块给出必须照抄的一致性句子")
    lines = build_user_lines(30.0, mode=MODE_SEGMENTED, segment_count=4)
    ck(any("segment_prompts_json" in x for x in lines), "逐段模式的 user 行")
    ck(any("分镜数按 4" in x for x in lines), "分镜数进 user 行")
    rz = rules_zh()
    ck("7000" in rz and "chain_id" in rz and "绝对时间码" in rz,
       "中文规则说明含预算/断点续跑/时间码三条（%d 字符）" % len(rz))

    j = segmented_json(plan_summary(12.25)["plan"], ["a", "", "c"])
    ck(j.count("note") == 3 and '"prompt": "a"' in j, "segment_prompts_json 骨架")

    print()
    if bad:
        print("== longvideo FAIL: %d 项 ==" % len(bad))
        return 1
    print("== longvideo PASS（%d 项）==" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(selftest())
