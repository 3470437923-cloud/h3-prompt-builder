# -*- coding: utf-8 -*-
"""生成结果的结构校验。

LLM 再听话也会偶尔跑偏，所以这里做一遍本地硬校验：
  * 段落头是否齐全、顺序是否正确（按所选格式方案）
  * 媒体标签是否会让 ComfyUI 节点直接 raise ValueError（照 h3_t8/prompt_tags.py 复刻）
  * <Subject N> / <Picture N> 是否规范、是否一一对应、有没有未定义就使用
  * 时间轴：官方六段用 [Shot N] At MM:SS.mmm；工作流五段用 [0-1.5s] / [Shot 1, 0-2s]
  * 官方六段额外校验 summary 任务前缀、retention_analysis 标记、<d> 台词与 (Sx) 说话人
  * 是否混入了中文（<d> 台词和引号内屏幕文字除外）、markdown、代码围栏
校验结果分成 err / warn / ok 三级，err 会触发“让 LLM 按报告修正”。
"""

from __future__ import annotations

import re
from typing import List, Tuple

import h3_skill
import longvideo

# 工作流五段（旧格式）
SECTIONS_AUDIO_ON = [s + ":" for s in h3_skill.WORKFLOW5_SECTIONS]
SECTIONS_AUDIO_OFF = ["subject_definitions:", "detailed_description:"]

TAG_SUBJECT = re.compile(r"<\s*Subject\s*(\d+)\s*>", re.IGNORECASE)
TAG_PICTURE = re.compile(r"<\s*(?:Picture|Image)\s*(\d+)\s*>", re.IGNORECASE)
BAD_SUBJECT = re.compile(r"(?<!<)\bSubject\s*\d\b(?!>)")
# 五段方案的分段时间头
SEG_TIME = re.compile(
    r"^\s*\[\s*(?:(Shot)\s*(\d+)\s*,\s*)?(-?\d+(?:\.\d+)?)\s*[-~–]\s*(-?\d+(?:\.\d+)?)\s*s\s*\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# 官方方案的分镜头：[Shot 1] 无时间戳；[Shot N] At MM:SS.mmm,
SHOT_HEAD = re.compile(
    r"^\s*\[Shot\s*(\d+)\]\s*(?:At\s*(\d{1,2}):(\d{2}(?:\.\d{1,3}))?\s*,)?",
    re.IGNORECASE | re.MULTILINE,
)
SHOT_INLINE = re.compile(r"\[Shot\s*(\d+)\]", re.IGNORECASE)
D_BLOCK = re.compile(r"<d>\s*(?:\[([^\]]{1,40})\])?\s*(.*?)\s*</d>", re.IGNORECASE | re.DOTALL)
D_OPEN = re.compile(r"<d>", re.IGNORECASE)
D_CLOSE = re.compile(r"</d>", re.IGNORECASE)
SPEAKER = re.compile(r"\(\s*S\d+(?:\s*,\s*S\d+)*\s*\)")
SPEAKER_ID = re.compile(r"\(\s*(S\d+)(?:\s*,\s*(S\d+))*\s*\)")
RETENTION_LINE = re.compile(
    r"^\s*(<(?:Subject|Picture|Video|Audio)\s*\d+\s*>)\s*(\([^)]*\))?\s*:\s*([A-Za-z_]+)\s*-",
    re.MULTILINE,
)
SUMMARY_PREFIX = re.compile(r"^\s*\[\s*([a-z][a-z +]*?)\s*\]", re.IGNORECASE)
TASK_TYPE_WORDS = {t for t, _zh in h3_skill.TASK_TYPES}
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
FENCE = re.compile(r"^\s*(```|~~~)")


def clean_output(raw: str) -> str:
    """把 LLM 常见的包装剥掉：代码围栏、开场白、结尾解释。"""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip()

    # 去掉 ```xxx ... ``` 围栏
    if "```" in text:
        parts = re.split(r"```[a-zA-Z0-9_+-]*\s*\n?", text)
        if len(parts) >= 3:
            # 取围栏内部最长的一块
            text = max(parts[1:-1], key=len).strip()
        elif len(parts) == 2:
            text = (parts[0] if parts[1].strip() == "" else parts[1]).strip()

    # 从第一个 subject_definitions: 开始截断（干掉开场白）
    idx = text.lower().find("subject_definitions:")
    if idx > 0:
        text = text[idx:]

    # 只削尾巴上的中文寒暄（“希望对你有帮助”之类），正文中间的中文交给 check() 报错，
    # 不在这里悄悄删行 —— 悄悄删会让校验看不到真实问题。
    # 含 <d> 台词的行绝不删：官方格式允许台词是中文。
    lines = text.split("\n")
    while lines:
        tail = lines[-1].strip()
        if tail == "" or (CJK.search(tail) and not QUOTED.search(tail)
                          and "<d>" not in tail.lower() and "</d>" not in tail.lower()):
            lines.pop()
            continue
        break
    text = "\n".join(lines).strip()

    # 收尾多余的围栏符号
    text = re.sub(r"\n*```+\s*$", "", text).strip()
    return text


def split_sections(body: str, names: List[str]) -> dict:
    """按段落头切分，返回 {name: 该段正文}。names 不带冒号。"""
    heads = []
    for n in names:
        m = re.search(r"^\s*" + re.escape(n) + r"\s*:\s*$", body, re.IGNORECASE | re.MULTILINE)
        if not m:
            m = re.search(re.escape(n) + r"\s*:", body, re.IGNORECASE)
        if m:
            heads.append((m.start(), m.end(), n))
    heads.sort()
    out = {}
    for i, (_s, e, n) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(body)
        out[n] = body[e:end].strip()
    return out


def check(text: str, duration: float, include_audio: bool = True,
          segment_style: str = "time", scheme: str = "official",
          counts: dict = None, long_video: bool = False) -> List[Tuple[str, str]]:
    """返回 [(level, message), ...]，level ∈ {'err','warn','ok'}。"""
    out: List[Tuple[str, str]] = []
    body = text or ""
    scheme = scheme if scheme in h3_skill.SCHEMES else "official"
    counts = counts or h3_skill.CONNECTED_MEDIA
    pic_limit = max(0, int(counts.get("pictures", 3)))

    if not body.strip():
        return [("err", "输出为空")]

    # ---- 节点级致命错：媒体标签（照 h3_t8/prompt_tags.py 复刻）----
    for lv, msg in h3_skill.media_tag_problems(body, counts):
        out.append((lv, "节点标签：" + msg))

    # ---- 段落头 ----
    if scheme == "official":
        names = (h3_skill.REF2VA_SECTIONS if include_audio
                 else h3_skill.REF2VA_SECTIONS_NO_AUDIO)
    else:
        names = ([s[:-1] for s in SECTIONS_AUDIO_ON] if include_audio
                 else [s[:-1] for s in SECTIONS_AUDIO_OFF])
    sections = split_sections(body, list(names))

    for n in names:
        if n not in sections:
            out.append(("err", "缺少段落头：%s:" % n))
    found = [n for n in names if n in sections]
    if len(found) == len(names):
        order_ok = True
        pos = []
        for n in names:
            m = re.search(r"^\s*" + re.escape(n) + r"\s*:\s*$", body, re.IGNORECASE | re.MULTILINE)
            pos.append(m.start() if m else -1)
        order_ok = pos == sorted(pos) and -1 not in pos
        if order_ok:
            out.append(("ok", "段落头齐全且顺序正确（%d 段）" % len(names)))
        else:
            out.append(("err", "段落顺序不对，应为：%s" % " → ".join(n + ":" for n in names)))

    # 关音频时不许再出现音频段
    if not include_audio:
        banned = (["overall_soundscape", "non_diegetic_music"] if scheme == "official"
                  else ["overall_style", "overall_soundscape", "non_diegetic_music"])
        for extra in banned:
            if re.search(r"^\s*" + extra + r"\s*:", body, re.IGNORECASE | re.MULTILINE):
                out.append(("err", "已关闭音频段，但仍出现了 %s:" % extra))

    # 契约外的段落头
    known = set(h3_skill.REF2VA_SECTIONS) | set(h3_skill.WORKFLOW5_SECTIONS)
    for m in re.finditer(r"^\s*([a-z_]{3,})\s*:\s*$", body, re.IGNORECASE | re.MULTILINE):
        head = m.group(1).lower()
        if head not in known:
            out.append(("warn", "出现了契约外的段落头：%s:" % head))
        elif head not in set(names):
            out.append(("err", "%s: 不属于当前方案（%s），这个方案只输出：%s"
                        % (head, h3_skill.SCHEMES[scheme]["zh"], " / ".join(names))))

    sd = sections.get("subject_definitions", "")
    dd = sections.get("detailed_description", "")

    # ---- Subject / Picture 标签 ----
    subs = [int(x) for x in TAG_SUBJECT.findall(body)]
    pics = [int(x) for x in TAG_PICTURE.findall(body)]
    if not subs:
        out.append(("err", "全文没有任何 <Subject N> 标签"))
    if BAD_SUBJECT.search(body):
        out.append(("err", "存在没写尖括号的 Subject N。节点不校验它，但 H3 靠 <Subject N> 认人，"
                           "裸写会让身份锁失效 —— 全部补上尖括号"))

    sd_subs = sorted(set(int(x) for x in TAG_SUBJECT.findall(sd)))
    sd_pics = sorted(set(int(x) for x in TAG_PICTURE.findall(sd)))
    dd_subs = sorted(set(int(x) for x in TAG_SUBJECT.findall(dd)))
    # 官方格式允许 N:M —— 一个 Subject 可以由多张图共同定义（人物图 + 面部细节图），
    # 一张图也可以贡献多个 Subject。所以只硬要求"每张连上的图都被引用到"。
    for n in range(1, pic_limit + 1):
        if sd and n not in sd_pics:
            out.append(("err", "subject_definitions 里没有引用 <Picture %d>（那张参考图等于白连）" % n))
    if sd_subs and sd_subs != list(range(1, len(sd_subs) + 1)):
        out.append(("err", "<Subject N> 编号必须从 1 连续编下来，实际是 %s" % sd_subs))
    if len(sd_subs) > pic_limit:
        out.append(("warn", "定义了 %d 个 Subject 但只连了 %d 张图 —— 如果一张图里有多个人物那没问题，"
                            "否则是编多了" % (len(sd_subs), pic_limit)))
    if sd and sd_pics and sd_subs:
        out.append(("ok", "subject_definitions 引用了 %d 张图、定义了 %d 个 Subject"
                    % (len(sd_pics), len(sd_subs))))
    miss = h3_skill.missing_pictures(body, pic_limit)
    if miss:
        out.append(("err", "全文没有引用到 <Picture %s>，这些参考图不会被绑定"
                    % "、".join(str(i) for i in miss)))
    for n in dd_subs:
        if n not in sd_subs:
            out.append(("err", "detailed_description 用了未定义的 <Subject %d>" % n))
    for n in sd_subs:
        if n not in dd_subs:
            out.append(("warn", "<Subject %d> 定义了但 detailed_description 里没用到" % n))

    # ---- 时间轴 / 分镜 ----
    if scheme == "official":
        out.extend(_check_official_timeline(dd, duration, body, sections, long_video))
        out.extend(_check_official_sections(sections, body, duration, include_audio))
    else:
        out.extend(_check_legacy_timeline(body, duration, segment_style))

    # ---- 语言 / markdown 污染 ----
    no_d = D_BLOCK.sub(lambda m: " ", body)          # <d> 里的台词允许原语言
    no_quote = QUOTED.sub("", no_d)                   # 引号内是屏幕文字，也允许原语言
    cjk_hits = CJK.findall(no_quote)
    if cjk_hits:
        sample = "".join(dict.fromkeys(cjk_hits))[:20]
        where = "（<d> 台词和引号内屏幕文字除外）" if scheme == "official" else "（引号内除外）"
        out.append(("err", "出现了中文字符%s：%s… 必须全英文" % (where, sample)))
    if FENCE.search(body):
        out.append(("err", "残留 markdown 代码围栏 ```"))
    if re.search(r"^\s*[-*•]\s+", body, re.MULTILINE):
        out.append(("warn", "出现了列表符号 - / * ，契约要求纯文本段落"))
    if re.search(r"^\s*#{1,6}\s+", body, re.MULTILINE):
        out.append(("warn", "出现了 markdown 标题 #"))

    # ---- 体量 ----
    lo, hi = h3_skill.SCHEMES[scheme]["words"]
    scope = dd if (scheme == "official" and dd) else body
    words = len(re.findall(r"[A-Za-z][A-Za-z'-]*", scope))
    label = "detailed_description" if (scheme == "official" and dd) else "全文"
    if scheme == "official" and words < 150:
        out.append(("err", "%s 只有约 %d 词，官方要求 %d-%d 词，太薄会直接掉细节"
                    % (label, words, lo, hi)))
    elif words < lo:
        out.append(("warn", "%s 约 %d 词，低于官方常态 %d-%d 词" % (label, words, lo, hi)))
    elif words > hi + 200:
        out.append(("warn", "%s 约 %d 词，明显超出 %d-%d 词，H3 可能被稀释" % (label, words, lo, hi)))
    else:
        out.append(("ok", "%s 约 %d 词，处于推荐区间" % (label, words)))

    # ---- 官方 CLI 提交字数预算（两种方案、两种页面都适用）----
    budget = longvideo.budget_status(body)
    if budget["over"]:
        out.append(("warn", "全文 %d 字符，超出官方 CLI 提交上限 %d 字符 %d 个字符（%s）"
                    % (budget["chars"], budget["limit"], budget["over"],
                       longvideo.SUBMISSION_LIMIT_SOURCE)))
    else:
        out.append(("ok", "全文 %d 字符，在官方 %d 字符预算内（余 %d）"
                    % (budget["chars"], budget["limit"], budget["left"])))

    # ---- 长视频专有：禁绝对时间码 + 要一致性声明 ----
    if long_video:
        for lv, msg in longvideo.check(body, duration):
            out.append((lv, msg))

    if not any(lv == "err" for lv, _ in out):
        out.insert(0, ("ok", "结构校验通过，可以直接粘进 ComfyUI 的 Input Text 节点"))
    return out


# --------------------------------------------------------------------------
# 官方六段：分镜时间
# --------------------------------------------------------------------------
def _check_official_timeline(dd: str, duration: float, body: str,
                             sections: dict, long_video: bool = False) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not dd:
        return out
    heads = list(SHOT_HEAD.finditer(dd))
    if not heads:
        out.append(("err", "分镜：detailed_description 里没有任何 [Shot N] 头"))
        return out

    nums = [int(m.group(1)) for m in heads]
    if nums != list(range(1, len(nums) + 1)):
        out.append(("err", "分镜：编号必须是 1,2,3… 连续，实际是 %s" % nums))

    # Shot 1 不许带时间戳
    if heads[0].group(2) is not None:
        out.append(("err", "分镜：[Shot 1] 是开场镜头，官方格式不带时间戳"))

    cuts = []
    for m in heads[1:]:
        if m.group(2) is None:
            if not long_video:
                out.append(("err", "分镜：[Shot %s] 缺少 “At MM:SS.mmm,” 切换时间" % m.group(1)))
        else:
            if long_video:
                out.append(("err", "分镜：[Shot %s] 带了时间戳；长视频里同一份 prompt 会发给每一段，"
                                   "绝对时间码必须去掉" % m.group(1)))
            else:
                cuts.append((int(m.group(1)), int(m.group(2)) * 60 + float(m.group(3))))
    for i in range(1, len(cuts)):
        if cuts[i][1] <= cuts[i - 1][1]:
            out.append(("err", "分镜：[Shot %d] 的切换时间 %s 没有严格递增（上一个 Shot %d 是 %s）"
                        % (cuts[i][0], _mmss(cuts[i][1]), cuts[i - 1][0], _mmss(cuts[i - 1][1]))))
    if cuts:
        last = cuts[-1][1]
        if last >= duration:
            out.append(("err", "分镜：最后一个切换点 %s 已经到/超过总时长 %ss"
                        % (_mmss(last), _n(duration))))
        elif duration - last < 0.8:
            out.append(("warn", "分镜：最后一个镜头只剩 %.2fs，可能来不及演完"
                        % (duration - last)))
        if last > duration - 0.2:
            out.append(("warn", "分镜：切换点贴着总时长 %ss，确认一下" % _n(duration)))
        out.append(("ok", "分镜：%d 个，切换点 %s，总时长 %ss"
                    % (len(heads), "、".join(_mmss(c) for _n_, c in cuts) or "无（单镜头）",
                       _n(duration))))

    # 五段方案那种 [0-1.5s] 头不该出现在官方方案里
    if SEG_TIME.search(dd):
        if long_video:
            out.append(("err", "分镜：出现了 [0-1.5s] 这种绝对时间码；长视频里只用不含时间的 "
                               "[Shot N]（每段都会重演整条时间轴）"))
        else:
            out.append(("err", "分镜：出现了 [0-1.5s] 这种旧格式时间头，官方六段只用 "
                               "[Shot N] At MM:SS.mmm"))

    if long_video and heads:
        out.append(("ok", "分镜：%d 个 [Shot N] 标记，全部不含时间码（长视频正确写法）"
                    % len(heads)))

    # 官方要求 [Shot 1] 之前先有一两句定调
    first = heads[0].start()
    lead = dd[:first].strip()
    if len(lead.split()) < 6:
        out.append(("warn", "分镜：[Shot 1] 之前应该先用一两句英文定调（介质/光线/色调/镜头感），"
                            "官方六段没有单独的 overall_style 段"))

    # retention_analysis 里引用的 [Shot N] 必须真实存在
    ra = sections.get("retention_analysis", "")
    valid = set(nums)
    for m in SHOT_INLINE.finditer(ra):
        if int(m.group(1)) not in valid:
            out.append(("err", "retention_analysis 引用了不存在的 [Shot %s]（实际只有 %s）"
                        % (m.group(1), sorted(valid))))
    return out


def _mmss(sec: float) -> str:
    return "%02d:%06.3f" % (int(sec // 60), sec - int(sec // 60) * 60)


# --------------------------------------------------------------------------
# 官方六段：summary / retention_analysis / 台词 / 声音两段
# --------------------------------------------------------------------------
def _check_official_sections(sections: dict, body: str, duration: float,
                             include_audio: bool) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []

    # ---- summary ----
    sm = sections.get("summary", "")
    if sm:
        m = SUMMARY_PREFIX.match(sm)
        if not m:
            out.append(("err", "summary：必须以方括号任务前缀开头，例如 [reference generation]"))
        else:
            parts = [p.strip() for p in m.group(1).split("+")]
            bad = [p for p in parts if p not in TASK_TYPE_WORDS]
            if bad:
                out.append(("err", "summary：任务类型 %s 不在官方允许列表里（%s）"
                            % ("、".join(bad), " / ".join(sorted(TASK_TYPE_WORDS)))))
            if len(parts) != len(set(parts)):
                out.append(("warn", "summary：任务类型重复了，官方要求不重复"))
            for forbidden in ("video editing", "video continuation", "audio reuse",
                              "audio reference"):
                if forbidden in parts:
                    out.append(("err", "summary：写了 “%s”，但你没连视频/音频参考，"
                                       "节点会因为没有对应资产而报错" % forbidden))
            if not bad:
                out.append(("ok", "summary：任务前缀 [%s] 合法" % m.group(1).strip()))
        if re.search(r"<\s*(Picture|Video|Audio)\s*\d+\s*>", sm, re.IGNORECASE):
            out.append(("warn", "summary：官方要求这一段只复用已定义的 <Subject N>，"
                                "不要在这里引入媒体标签"))
        if len(sm.split()) > 120:
            out.append(("warn", "summary：官方要求“一段短话”，现在有 %d 词" % len(sm.split())))

    # ---- retention_analysis ----
    ra = sections.get("retention_analysis", "")
    if ra:
        rows = list(RETENTION_LINE.finditer(ra))
        if not rows:
            out.append(("err", "retention_analysis：没有一行符合 "
                               "<Subject N> (appears in [Shot 1]): marker - 说明 的格式"))
        else:
            markers = [r.group(3) for r in rows]
            allowed = set(h3_skill.RETENTION_VISIBLE) | set(h3_skill.RETENTION_AUDIO)
            for mk in markers:
                if mk not in allowed:
                    out.append(("err", "retention_analysis：标记 “%s” 不在官方固定值里（%s）"
                                % (mk, " / ".join(h3_skill.RETENTION_VISIBLE))))
            labels = [r.group(1) for r in rows]
            sd_labels = re.findall(r"<Subject\s*(\d+)\s*>", sections.get("subject_definitions", ""),
                                   re.IGNORECASE)
            defined = {"<Subject %s>" % n for n in sd_labels}
            for lab in labels:
                norm = re.sub(r"\s+", " ", lab.strip())
                if norm in defined:
                    defined.discard(norm)
            for left in sorted(defined):
                out.append(("warn", "retention_analysis：漏了 %s（官方要求每个标签一行）" % left))
            if SPEAKER.search(ra):
                out.append(("err", "retention_analysis：官方明确规定这一段不写说话人编号 (Sx)"))
            if not any(lv == "err" for lv, m in out if "retention" in m):
                out.append(("ok", "retention_analysis：%d 行，标记合法" % len(rows)))

    # ---- 台词与说话人 ----
    dd = sections.get("detailed_description", "")
    if dd:
        opens, closes = len(D_OPEN.findall(dd)), len(D_CLOSE.findall(dd))
        if opens != closes:
            out.append(("err", "台词：<d> 和 </d> 数量不一致（%d vs %d）" % (opens, closes)))
        blocks = list(D_BLOCK.finditer(dd))
        if blocks:
            for b in blocks:
                if not b.group(1):
                    out.append(("err", "台词：<d> 里缺语言标签，官方格式是 "
                                       "<d>[Chinese] 原话</d> / <d>[English] ...</d>"))
                    break
            speakers = set()
            for m in SPEAKER_ID.finditer(dd):
                for g in m.groups():
                    if g:
                        speakers.add(g)
            if not speakers:
                out.append(("err", "台词：有 <d> 对白但没有任何 (S1)/(S2) 说话人编号"))
            else:
                expect = {"S%d" % i for i in range(1, len(speakers) + 1)}
                if speakers != expect:
                    out.append(("warn", "台词：说话人编号 %s 不是从 S1 连续编下来的（应为 %s）"
                                % (sorted(speakers), sorted(expect))))
                else:
                    out.append(("ok", "台词：%d 段对白，说话人 %s" % (len(blocks), sorted(speakers))))
        elif CJK.search(dd):
            out.append(("err", "台词：detailed_description 里有中文但没包在 <d>[语言] ...</d> 里"))
        if re.search(r"\boff-screen voiceover\b", dd, re.IGNORECASE):
            if not re.search(r"lips\s+(remain|stay)", dd, re.IGNORECASE):
                out.append(("warn", "旁白：用了 off-screen voiceover，官方要求紧接着写明"
                                    "“嘴唇保持闭合”"))

    # ---- 声音两段 ----
    if include_audio:
        os_ = sections.get("overall_soundscape", "")
        nm = sections.get("non_diegetic_music", "")
        if os_ and D_OPEN.search(os_):
            out.append(("err", "overall_soundscape：不许重复台词，对白只写在 detailed_description"))
        if nm:
            low = nm.strip().lower()
            if low not in ("n/a", "na", "none", "none.") and len(nm.split()) < 4:
                out.append(("warn", "non_diegetic_music：太短，官方要求写清配器/速度/力度变化，"
                                    "或者干脆写 N/A"))
            if low in ("none", "none."):
                out.append(("warn", "non_diegetic_music：官方六段用 N/A 表示没有配乐（None. 是"
                                    "五段方案的写法）"))
    return out


# --------------------------------------------------------------------------
# 工作流五段：分段时间
# --------------------------------------------------------------------------
def _check_legacy_timeline(body: str, duration: float,
                           segment_style: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    segs = list(SEG_TIME.finditer(body))
    if not segs:
        out.append(("err", "没有找到任何时间轴分段（形如 [0-1.5s] 或 [Shot 1, 0-2s]）"))
        return out
    tol = 0.051
    times = [(float(m.group(3)), float(m.group(4)), bool(m.group(1)), m.group(2)) for m in segs]
    problems = []
    if abs(times[0][0]) > tol:
        problems.append("第一段不是从 0 开始（是 %s）" % _n(times[0][0]))
    for i in range(1, len(times)):
        if abs(times[i][0] - times[i - 1][1]) > tol:
            problems.append("第 %d 段与第 %d 段之间不连续（%s → %s）"
                            % (i, i + 1, _n(times[i - 1][1]), _n(times[i][0])))
    for i, t in enumerate(times, 1):
        if t[1] <= t[0]:
            problems.append("第 %d 段结束时间不大于开始时间" % i)
    if abs(times[-1][1] - duration) > tol:
        problems.append("时间轴收在 %ss，但总时长是 %ss" % (_n(times[-1][1]), _n(duration)))
    has_shot = [t for t in times if t[2]]
    if segment_style == "time" and has_shot:
        problems.append("你选的是 [0-1.5s] 样式，但输出里写了 Shot")
    if segment_style == "shot":
        if len(has_shot) != len(times):
            problems.append("你选的是 [Shot N, a-bs] 样式，但有分段没写 Shot")
        else:
            nums = [int(t[3]) for t in times]
            if nums != list(range(1, len(nums) + 1)):
                problems.append("Shot 编号不连续：%s" % nums)
    for p in problems:
        out.append(("err", "时间轴：" + p))
    if not problems:
        out.append(("ok", "时间轴：0 → %ss，共 %d 段，连续无缺口" % (_n(duration), len(times))))
    return out


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else ("%.2f" % x).rstrip("0").rstrip(".")


def problems_only(report: List[Tuple[str, str]]) -> List[str]:
    return [m for lv, m in report if lv == "err"]


def has_errors(report: List[Tuple[str, str]]) -> bool:
    return any(lv == "err" for lv, _ in report)
