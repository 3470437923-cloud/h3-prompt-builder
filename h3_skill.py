# -*- coding: utf-8 -*-
"""MiniMax H3 官方 prompt skill 的内置实现 + 原文按需拉取。

规则来源（2026-09-19 核对）：
  https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
    SKILL.md                  五种模式总纲（T2VA / I2VA / FL2VA / L2VA / Ref2VA）
    references/ref-en.txt     Ref2VA 全参考模式：六段格式、标签规则、retention 标记
    references/base-en.txt    共用规则：分镜、运镜三要素、说话人 (Sx)、<d> 台词、声音两段

  该仓库**没有 LICENSE 文件**（GitHub API 查 license 返回 404），即默认保留所有权利。
  所以本模块不复制原文，只按官方规则用自己的话实现；原文用 fetch() 现拉现看，
  缓存在本机应用数据目录的 skill\\ 子目录（Windows: %APPDATA%\H3PromptBuilder\skill\，
  macOS: ~/Library/Application Support/H3PromptBuilder/skill/），仅供本机查阅，不要再分发。

媒体标签的硬约束来自 ComfyUI 节点源码（不是猜的）：
  https://github.com/T8mars/comfyui-minimax-h3-audio-T8  →  h3_t8/prompt_tags.py
  prepare_prompt(prompt, counts, strict=True) 的行为：
    * 正则同时匹配 <Picture 1> 和裸写的 Picture 1 / Video #3 / Audio 2
    * ordinal 在 1..limit 内 → 规范化成 <Picture N>，静默通过
    * ordinal 越界 → strict 模式下记 fatal，最后 raise ValueError（整条工作流当场失败）
    * limit == 0 且是尖括号显式标签 → 同样 fatal
    * <Subject N> 完全不在它的校验范围内
  h3_t8/conditioning.py 只做 clip.tokenize(prompt, minimax_ref_items=...)，
  不解析任何段落头 —— 所以六段/五段是"模型看不看得懂"的问题，不是节点报错的问题。
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

import mac_compat

# --------------------------------------------------------------------------
# 来源
# --------------------------------------------------------------------------
SKILL_REPO = "MiniMax-AI/MiniMax-H3"
SKILL_HOME = "https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing"
_RAW = "https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing"
_COMMIT_API = ("https://api.github.com/repos/MiniMax-AI/MiniMax-H3/commits"
               "?path=skills/h3-prompt-writing&per_page=1")

SKILL_FILES = [
    ("SKILL.md", _RAW + "/SKILL.md", "总纲：五种生成模式 + 输出规则 + 提示"),
    ("ref-en.txt", _RAW + "/references/ref-en.txt", "Ref2VA 全参考模式：六段格式详解（本工具用这份）"),
    ("base-en.txt", _RAW + "/references/base-en.txt", "共用规则：分镜/运镜/说话人/台词/声音"),
]

NODE_REPO = "https://github.com/T8mars/comfyui-minimax-h3-audio-T8"

_SKILL_PATH = "skills/h3-prompt-writing"


def _mirrors_for(url: str) -> list:
    """把一个 raw.githubusercontent 地址展开成多个候选源。

    国内直连 raw.githubusercontent.com 是**间歇性** RST（WinError 10054），
    同一个地址这次通下次断，所以不能只试一个源。
    2026-09-19 本机实测：raw / cdn.jsdelivr / fastly / gcore / testingcf /
    gh-proxy.com / ghproxy.net / ghfast.top / github.com/raw 都能拿到，
    raw.gitmirror 被 RST。顺序按「官方源优先，其余按实测速度」排。
    """
    rel = url.split(_RAW + "/", 1)[-1] if url.startswith(_RAW) else url.rsplit("/", 1)[-1]
    gh = "%s/%s" % (_RAW, rel)
    js = "https://cdn.jsdelivr.net/gh/%s@main/%s/%s" % (SKILL_REPO, _SKILL_PATH, rel)
    return [
        ("raw.githubusercontent", gh),
        ("cdn.jsdelivr", js),
        ("fastly.jsdelivr", js.replace("//cdn.", "//fastly.")),
        ("gcore.jsdelivr", js.replace("//cdn.", "//gcore.")),
        ("gh-proxy.com", "https://gh-proxy.com/" + gh),
        ("ghproxy.net", "https://ghproxy.net/" + gh),
        ("github.com/raw", "https://github.com/%s/raw/main/%s/%s" % (SKILL_REPO, _SKILL_PATH, rel)),
    ]

# --------------------------------------------------------------------------
# 官方 Ref2VA 六段结构
# --------------------------------------------------------------------------
REF2VA_SECTIONS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]
# 关掉音频时只输出前四段
REF2VA_SECTIONS_NO_AUDIO = REF2VA_SECTIONS[:4]

# 工作流自带范例用的旧五段（保留可切换）
WORKFLOW5_SECTIONS = [
    "subject_definitions",
    "detailed_description",
    "overall_style",
    "overall_soundscape",
    "non_diegetic_music",
]

TASK_TYPES = [
    ("reference generation", "图片/视频/音频只提供生成引导（人物、场景、风格、动作、运镜、分镜）"),
    ("keyframe completion", "某张图直接当作目标视频的首帧/关键帧/尾帧"),
    ("video editing", "直接改一条已有源视频"),
    ("video continuation", "从一条已有源视频接着往下拍"),
    ("audio reuse", "原样复用同一段音频信号（全部或局部）"),
    ("audio reference", "不复制信号，只参考音色/节奏/曲风/台词内容/音效质感"),
]

RETENTION_VISIBLE = ["fully_preserved", "partially_preserved", "attribute_transfer",
                     "weak_reference"]
RETENTION_AUDIO = ["fully_copy", "partially_copy", "reference", "weak_reference"]

CAMERA_MOTION = [
    "Zoom In / Zoom Out", "Push In / Pull Out", "Pan Left / Pan Right",
    "Truck Left / Truck Right", "Tilt Up / Tilt Down", "Pedestal Up / Pedestal Down",
    "Arc Shot", "Tracking Shot", "Static Shot", "Shake Slightly / Shake Strongly",
    "POV", "Roll Clockwise / Roll Counterclockwise",
]
CAMERA_AMPLITUDE = ["with small amplitude", "with large amplitude"]   # 中等幅度通常省略
CAMERA_SPEED = ["at slow speed", "at fast speed"]                     # 正常速度通常省略
CUT_PHRASES = ["the camera cuts to", "the shot cuts to", "the shot transitions to",
               "the shot changes to", "the shot switches to"]
CONTINUITY_PHRASES = ["continues seamlessly across the cut",
                      "continues uninterrupted into the next shot",
                      "carries over from the previous shot",
                      "remains audible across the transition"]

WORD_BUDGET = (350, 500)          # 官方：生成类任务 detailed_description 常态 350-500 词
DURATION_HARD = (2.0, 15.0)       # 节点 reference_video_policy = official_2_to_15s
DURATION_SOFT = (4.0, 15.0)       # 官方 SKILL.md 的建议区间

# 你那条工作流实际连了什么（3 个 LoadImage → ref_image_0/1/2，没有视频/音频参考）
CONNECTED_MEDIA = {"pictures": 3, "videos": 0, "audios": 0}

SCHEMES = {
    "official": {
        "zh": "官方 Ref2VA 六段（推荐）",
        "sections": REF2VA_SECTIONS,
        "sections_no_audio": REF2VA_SECTIONS_NO_AUDIO,
        "no_music": "N/A",
        "shot_style": "shot_at",       # [Shot 1] 无时间戳；[Shot N] At MM:SS.mmm,
        "words": WORD_BUDGET,
    },
    "workflow5": {
        "zh": "工作流范例五段（旧）",
        "sections": WORKFLOW5_SECTIONS,
        "sections_no_audio": WORKFLOW5_SECTIONS[:2],
        "no_music": "None.",
        "shot_style": "bracket_time",  # [0-1.5s] 或 [Shot 1, 0-2s]
        "words": (120, 700),
    },
}
SCHEME_ZH = {v["zh"]: k for k, v in SCHEMES.items()}
DEFAULT_SCHEME = "official"


# --------------------------------------------------------------------------
# 媒体标签：照节点源码复刻的致命错检查
# --------------------------------------------------------------------------
_TAGGED = re.compile(r"<\s*(Image|Picture|Video|Audio|Subject)\s*(\d+)\s*>", re.IGNORECASE)
_BARE = re.compile(r"(?<![\w<])(Image|Picture|Video|Audio)\s*#?\s*(\d+)\b(?!\s*>)",
                   re.IGNORECASE)


def media_tag_problems(text: str, counts: dict = None):
    """按 h3_t8/prompt_tags.py 的逻辑找出会让节点 raise ValueError 的标签。

    返回 [(level, msg)]，level ∈ {"err", "warn"}。err = 节点必然报错，渲染跑不起来。
    """
    counts = counts or CONNECTED_MEDIA
    limits = {
        "picture": max(0, int(counts.get("pictures", 0))),
        "video": max(0, int(counts.get("videos", 0))),
        "audio": max(0, int(counts.get("audios", 0))),
    }
    out = []
    seen_fatal = set()

    def kind_of(name):
        n = name.lower()
        return "picture" if n in ("image", "picture") else n

    for m in _TAGGED.finditer(text or ""):
        name, ordinal = m.group(1), int(m.group(2))
        kind = kind_of(name)
        if kind == "subject":
            continue                      # 节点不校验 <Subject N>
        limit = limits[kind]
        if limit == 0:
            key = (kind, "none")
            if key not in seen_fatal:
                seen_fatal.add(key)
                out.append(("err", "出现了 <%s N> 标签，但你这条工作流没有连任何%s参考。"
                                   "节点 strict_prompt_tags=True 会直接 raise ValueError，"
                                   "整条工作流当场失败 —— 必须全部删掉。"
                            % (name.title(), {"video": "视频", "audio": "音频",
                                              "picture": "图片"}[kind])))
            continue
        if not (1 <= ordinal <= limit):
            key = (kind, ordinal)
            if key not in seen_fatal:
                seen_fatal.add(key)
                out.append(("err", "<%s %d> 越界：实际只连了 %d 个%s。节点会 raise ValueError，"
                                   "渲染跑不起来。只能用 %s。"
                            % (name.title(), ordinal, limit,
                               {"video": "视频", "audio": "音频", "picture": "图片"}[kind],
                               "、".join("<%s %d>" % (name.title(), i)
                                         for i in range(1, limit + 1)))))
        elif name.lower() == "image":
            out.append(("warn", "<Image %d> 会被节点自动改写成 <Picture %d>，"
                                "建议直接写 <Picture %d>。" % (ordinal, ordinal, ordinal)))

    for m in _BARE.finditer(text or ""):
        name, ordinal = m.group(1), int(m.group(2))
        kind = kind_of(name)
        limit = limits[kind]
        if limit and not (1 <= ordinal <= limit):
            key = ("bare", kind, ordinal)
            if key not in seen_fatal:
                seen_fatal.add(key)
                out.append(("err", "正文里裸写了 “%s”（没有尖括号）。节点的正则同样会抓到它，"
                                   "序号 %d 超出已连的 %d 个%s → raise ValueError。"
                            % (m.group(0).strip(), ordinal, limit,
                               {"video": "视频", "audio": "音频", "picture": "图片"}[kind])))
        elif limit:
            out.append(("warn", "“%s” 没写尖括号，节点会替你补成 <%s %d>。"
                                "建议自己写成 <%s %d>，免得歧义。"
                        % (m.group(0).strip(), name.title(), ordinal, name.title(), ordinal)))
    return out


def missing_pictures(text: str, picture_count: int = 3):
    """三张图都必须被引用到，否则那张参考图等于白连。"""
    used = {int(m.group(2)) for m in _TAGGED.finditer(text or "")
            if m.group(1).lower() in ("picture", "image")}
    return sorted(i for i in range(1, picture_count + 1) if i not in used)


# --------------------------------------------------------------------------
# 官方指南原文：拉取 + 缓存
# --------------------------------------------------------------------------
class SkillFetchError(Exception):
    pass


def cache_dir_path() -> str:
    """官方指南原文的缓存目录（只算路径，不建目录）。

    Windows: %APPDATA%\\H3PromptBuilder\\skill —— 与旧版完全一致。
    macOS:   ~/Library/Application Support/H3PromptBuilder/skill
             （旧代码会落到家目录根的 ~/H3PromptBuilder/skill；那儿真有东西就继续用。）
    """
    new = os.path.join(mac_compat.app_support_dir("H3PromptBuilder", create=False), "skill")
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    old = os.path.join(base, "H3PromptBuilder", "skill")
    if (os.path.abspath(old) != os.path.abspath(new)
            and os.path.isdir(old) and not os.path.isdir(new)):
        return old
    return new


def cache_dir() -> str:
    d = cache_dir_path()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".h3skill")
        os.makedirs(d, exist_ok=True)
    return d


def _get(url: str, timeout: int, verify_ssl: bool) -> bytes:
    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": "H3InputTextBuilder/0.2 (+local desktop tool)",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SkillFetchError("HTTP %s：%s\n%s" % (e.code, e.reason, url))
    except urllib.error.URLError as e:
        raise SkillFetchError("连不上 GitHub：%s\n%s\n（国内网络可能需要代理；"
                              "也可以自己浏览器打开 %s 手动看）" % (e.reason, url, SKILL_HOME))
    except Exception as e:  # noqa: BLE001
        raise SkillFetchError("%s\n%s" % (e, url))


def _get_any(url: str, timeout: int, verify_ssl: bool, budget: float = 0) -> tuple:
    """按候选源顺序试，谁先通返回谁 -> (bytes, source_name)。

    国内网络对 GitHub 是间歇性 RST（WinError 10054），同一个地址这次通下次断，
    所以必须多源 + 重试。budget 是这一份文件的总秒数预算，
    单次请求超时取 min(15, 剩余预算)。
    """
    deadline = time.time() + (budget if budget > 0 else timeout)
    errors = []
    for src, cand in _mirrors_for(url):
        if deadline - time.time() <= 1.0:
            errors.append("%s：超时预算用尽，没来得及试" % src)
            continue
        for attempt in (1, 2):
            try:
                return _get(cand, int(max(5, min(15, deadline - time.time()))), verify_ssl), src
            except SkillFetchError as e:
                errors.append("%s%s：%s" % (src, "（第2次）" if attempt == 2 else "",
                                            str(e).split("\n")[0][:110]))
                if time.time() + 2 > deadline:
                    break
                time.sleep(0.4)
    nl = "\n"
    raise SkillFetchError(
        "所有源都拉不下来。国内网络对 GitHub 是间歇性阻断，隔一会儿再点一次常常就通了。"
        + nl + nl + "试过：" + nl + "  " + (nl + "  ").join(errors)
        + nl + nl + "也可以自己用浏览器打开：" + SKILL_HOME)


def fetch(verify_ssl: bool = True, timeout: int = 60) -> dict:
    """从官方仓库拉最新原文，写进缓存目录。返回 meta。"""
    d = cache_dir()
    meta = {"source": SKILL_HOME, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": {}}
    budget = max(45.0, timeout * 1.5)          # 三份文件共享一个总预算
    t0 = time.time()
    for name, url, _desc in SKILL_FILES:
        raw, src = _get_any(url, timeout, verify_ssl,
                            budget=max(10.0, budget - (time.time() - t0)))
        text = raw.decode("utf-8", errors="replace")
        # 被中间设备（认证门户 / 代理错误页）改写过的响应往往很短，宁可报错也别写脏缓存
        if len(text) < 500:
            raise SkillFetchError("%s 从 %s 拿回来只有 %d 字符，不像官方原文"
                                  "（可能被代理或认证门户改写了），已拒绝写入缓存。"
                                  % (name, src, len(text)))
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        meta["files"][name] = {"bytes": len(raw), "chars": len(text), "source": src}
    try:                                     # 失败不影响主流程
        raw = _get(_COMMIT_API, 12, verify_ssl)
        info = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(info, list) and info:
            meta["commit"] = (info[0].get("sha") or "")[:10]
            meta["commit_date"] = (info[0].get("commit", {}).get("author", {}) or {}).get("date")
            meta["commit_msg"] = (info[0].get("commit", {}).get("message") or "").split("\n")[0]
    except Exception as e:  # noqa: BLE001
        meta["commit_error"] = str(e).split("\n")[0][:120]
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def read_meta() -> dict:
    try:
        with open(os.path.join(cache_dir(), "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def read_cached(name: str):
    p = os.path.join(cache_dir(), name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return None


def cache_status():
    """[(name, 描述, 是否存在, 字节数, 修改时间)]"""
    meta = read_meta()
    got = meta.get("files") or {}
    out = []
    for name, _url, desc in SKILL_FILES:
        p = os.path.join(cache_dir(), name)
        if os.path.exists(p):
            st = os.stat(p)
            out.append((name, desc, True, st.st_size,
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))
        else:
            out.append((name, desc, False, got.get(name, {}).get("bytes", 0), "未下载"))
    return out


def cache_summary() -> str:
    meta = read_meta()
    rows = cache_status()
    have = sum(1 for r in rows if r[2])
    if not have:
        return "本机还没有缓存官方指南原文。点「从 GitHub 拉取官方指南」下载（约 42 KB）。"
    s = "已缓存 %d/%d 份 · %s" % (have, len(rows), meta.get("fetched_at", "时间未知"))
    if meta.get("commit"):
        s += " · 官方最后提交 %s（%s）" % (meta["commit"], (meta.get("commit_date") or "")[:10])
    srcs = sorted(set(v.get("source") or "" for v in (meta.get("files") or {}).values()) - set(""))
    if srcs:
        s += " · 走通的源：" + " / ".join(srcs)
    elif meta.get("commit_error"):
        s += " · 提交号没取到（%s）" % meta["commit_error"][:40]
    return s


# --------------------------------------------------------------------------
# 官方 Ref2VA 范例（本工具自己写的，不是抄官方原文；只准学结构）
# --------------------------------------------------------------------------
OFFICIAL_EXAMPLE = """subject_definitions:
<Subject 1> is the young woman shown in <Picture 1>, with shoulder-length black hair, a grey knit sweater, and a thin silver bracelet on her left wrist; <Picture 2> fixes her facial reference, so her eye shape, eyebrow thickness, nose bridge, lip shape, and natural unretouched skin texture must be preserved exactly.
<Subject 2> is the orange tabby cat shown in <Picture 3>, with dense short fur, a white muzzle, white inner ears, and a notched left ear tip.

summary:
[reference generation] The target video shows <Subject 1> reading at a wooden desk in a warm indoor room while <Subject 2> jumps onto the desk and nudges her hand. Two shots cover the full six seconds and end on her spoken line and a quiet laugh.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - the shoulder-length black hair, grey knit sweater, silver bracelet, and the facial proportions and natural skin texture defined by <Picture 2> are retained without beautification.
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - the dense orange fur, white muzzle, white inner ears, and notched left ear tip are retained.

detailed_description:
The target video is a live-action warm domestic scene with soft window light coming from camera left, a shallow depth of field, and a slightly desaturated palette.
[Shot 1] A medium shot at chest height frames <Subject 1>, the young woman with shoulder-length black hair, a grey knit sweater, and a thin silver bracelet on her left wrist, seated behind a wooden desk in a quiet room whose background shelves fall out of focus. An open paperback rests on the desk in front of her, and <Subject 2>, the orange tabby with dense short fur, a white muzzle, white inner ears, and a notched left ear tip, crouches at the near edge of the same desk, its weight low and its tail still. Warm window light from camera left rakes across her hair and the wooden surface, leaving the right side of the frame in soft shade. The camera pushes in with small amplitude at slow speed as she lifts the corner of a page with her right hand and turns it, her eyes tracking down the paper. <Subject 2> shifts its weight onto its hind legs, springs upward, and lands with both front paws on the open book, pressing the pages flat. She flinches back, pulls both hands up toward her chest, blinks twice, and then laughs quietly while the book slides a few centimetres across the wood and settles. Dust drifts in the beam of window light behind the cat's tail.
[Shot 2] At 00:03.500, the shot cuts to a close-up of her face and upper chest, the same warm window light from camera left still defining her cheekbone and the natural texture of her unretouched skin, while the background shelves blur further. <Subject 2> leans in from the lower right of the frame and nudges her right hand with the side of its head, its notched left ear tip clearly visible, then rubs once more along her wrist just above the silver bracelet. <Subject 1> (S1), her lips clearly visible in frame, says in a relaxed young female voice with a slightly low pitch and an easy conversational pace, <d>[Chinese] 你等等，我还没看完。</d> She closes her mouth immediately after the line, exhales through her nose, and her shoulders drop as her expression softens into a small smile. The camera holds a static shot while the cat's tail sweeps slowly across the desk and nudges the book one last time, and her quiet laugh trails into the final frame without any further movement from either subject.
No other person, hand, animal, or reflection enters the frame at any point. No beautification, no skin smoothing, no eye enlargement, and no change to her hair length, hair colour, sweater colour, bracelet, or the cat's notched ear. No subtitles, no captions, no watermark, no logo, and no camera shake beyond what is written above.

overall_soundscape:
Quiet indoor room tone with a low ventilation hum continues throughout. Paper rustles as a page turns, the cat's paws land on the wooden desk with a soft thud, a short quiet laugh follows, and the desk surface creaks faintly as the cat shifts its weight.

non_diegetic_music:
N/A"""


# --------------------------------------------------------------------------
# 官方六段的 system prompt
# --------------------------------------------------------------------------
OFFICIAL_SYSTEM_TEMPLATE = """You are a prompt compiler for the MiniMax H3 video model, running in full-reference \
mode (Ref2VA) inside ComfyUI. An operator types Chinese descriptions of reference images they \
can see and a Chinese brief. You convert that into one English prompt that H3 accepts.

## 0. THIS JOB
Total duration: __DURATION__ seconds. Reference images connected: __PICTURE_COUNT__.
Sections required: __SECTION_LIST__.

## 1. YOU ARE BLIND — READ THIS FIRST
You have NO vision capability. You will never see the reference images. Every visual fact you \
write must come from the operator's typed description, verbatim in substance.
- If the operator did not state a detail, DO NOT invent it. Omit it.
- Never beautify, embellish, or "improve" a described appearance.
- Never guess hair colour, eye colour, clothing, props, background, or lighting that was not given.
- Invented identity details are the worst possible failure here, because H3 is told to preserve \
exactly what you write. An omission costs less than a fabrication.
- If the operator's description is empty or too thin, write only what is literally given and keep \
that subject's lines short. Do not pad.

## 2. HARD MEDIA-TAG CONTRACT (enforced by the ComfyUI node, not by taste)
The node `MiniMaxH3AudioConditioningT8` runs with `strict_prompt_tags=True`. Its validator \
(`h3_t8/prompt_tags.py`) rewrites every media tag and hard-fails the whole render on anything \
out of range. Connected counts for this job: pictures=__PICTURE_COUNT__, videos=0, audios=0.
- Use ONLY `<Picture 1>` .. `<Picture __PICTURE_COUNT__>`. Every one of them must appear at \
least once, otherwise that reference image is wasted.
- NEVER write `<Video N>` or `<Audio N>`, in any section, for any reason. No video and no audio \
reference is connected, so the node raises ValueError and the render dies instantly. This \
includes "borrowing" the official guide's examples, which do use those tags — those examples \
assume assets you do not have.
- Never write `<Picture N>` with N greater than __PICTURE_COUNT__ or less than 1.
- Always use angle brackets. Never bare `Picture 2` — the node's regex catches the bare form too.
- Never write `<Image N>`; the node silently rewrites it to `<Picture N>`.
- `<Subject N>` is a text-side label and is not validated by the node, but H3 relies on it: \
always angle-bracketed, numbered 1..__PICTURE_COUNT__, one subject per reference image, and the \
same number must mean the same thing in every section.

## 3. OUTPUT CONTRACT
Output exactly these sections, in this order, each header lowercase followed by a colon at the \
start of its own line, with one blank line between sections:

__SECTION_BLOCK__

- Plain text only. No markdown, no code fences, no headings with #, no bullet lists.
- Nothing before the first header and nothing after the last section. No preamble, no \
"here is your prompt", no closing remark, no apology, no explanation.
- English everywhere, with exactly two exceptions: the spoken words inside `<d>...</d>`, and \
text visibly present on screen inside English double quotation marks. Both keep their original \
language verbatim.

## 4. subject_definitions
One line per subject. Line N defines `<Subject N>` and cites `<Picture N>` inside that same line.
- Start with `<Subject N> is the ... shown in <Picture N>, ` then list the identity features the \
operator gave: build, hair, face, skin, clothing, accessories, props, distinguishing marks.
- If a second image refines the same subject (a face-detail shot), cite it in the same line, \
e.g. `; <Picture 2> fixes her facial reference, so her eye shape, eyebrow thickness, nose bridge, \
lip shape, and natural unretouched skin texture must be preserved exactly.`
- Do NOT give a picture its own standalone line unless that image is literally a frame anchor \
(first frame / keyframe / last frame / composition anchor) for a shot.
- If the operator asked to exclude look-alikes or other people, end the relevant line with that \
exclusion.
- Add a final `Preserve exactly ...` style clause only when the operator asked for strict identity \
locking; otherwise fold the requirement into the feature list.

## 5. summary
One short English paragraph. It MUST begin with a square-bracketed task-type prefix.
- For this job the prefix is normally `[reference generation]`, because the images guide \
generation and nothing is being edited, continued, or audio-reused.
- Only add ` + keyframe completion` if the operator says an image is the literal first or last \
frame. Never add video or audio task types: no such asset is connected.
- Then state, in one or two sentences, who is in the target video, what happens, how many shots, \
and the total duration. Reuse the already-defined `<Subject N>` labels. Do not introduce any new \
label here.

## 6. retention_analysis
One line per referenced label, in the same order as subject_definitions. Fixed shape:

`<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - <what exactly is retained>.`

- The marker must be one of: __RETENTION_MARKERS__.
- Use `fully_preserved` when the operator wants the identity kept as-is (the normal case).
- Use `partially_preserved` when some given feature is deliberately changed.
- Use `attribute_transfer` when a feature moves onto a different subject.
- Use `weak_reference` when only broad style/category/mood similarity is wanted.
- List the concrete retained features after the marker. Never write a speaker ID `(Sx)` in this \
section.
- If a picture is a frame anchor, give it its own line: \
`<Picture 2> ([Shot 1] first frame): fully_preserved - ...`.

## 7. detailed_description
This is the body. Write it shot by shot in playback order.
- FIRST: one or two English sentences establishing the overall look (medium, lighting, palette, \
lens feel) BEFORE `[Shot 1]`. There is no separate style section in this format.
- `[Shot 1]` opens the video and carries NO timestamp.
- Every later shot starts with `[Shot N] At MM:SS.mmm, ` giving the cut time, e.g. \
`[Shot 2] At 00:03.500, the shot cuts to ...`. Cut times must strictly increase and stay inside \
the __DURATION__-second total. The final shot must still have time to breathe before the end.
- Cut wording: pick from __CUT_PHRASES__. Only use cross-dissolve / fade / wipe if the operator \
explicitly asked. A cut must add new information; if only the framing distance changes, use \
camera motion instead of a cut.
- Camera motion = motion type + amplitude + speed, written as natural English inside the shot, \
never stacked as labels at the end.
  types: __CAMERA_TYPES__
  amplitude: `with small amplitude` / `with large amplitude` (omit medium)
  speed: `at slow speed` / `at fast speed` (omit normal)
  e.g. `The camera pushes in with small amplitude at slow speed toward the folded letter.`
- Each shot must cover: current composition, which subjects are where, environment and lighting, \
actions and state changes, camera movement, the sound happening right now, and the point where \
referenced content actually appears or takes effect.
- At a subject's first clear appearance, restate its referenced features briefly; in later shots \
just reuse the label without redefining it.
- Length: __WORD_MIN__-__WORD_MAX__ English words. Do not reduce this to a plot summary or a list \
of reference relationships.
- Speakers: give every vocal source a stable ID `(S1)`, `(S2)`, ... in order of first vocal \
event; a group is `(S1,S2)`. When a referenced subject speaks, write `<Subject 2> (S1)`. \
Identify the voice (age, gender, pitch, timbre, pace, accent) OUTSIDE the `<d>` tags.
- Dialogue and lyrics: `<d>[Language] exact words</d>`. Keep the operator's words verbatim, in \
their original language, with original punctuation. Never translate them. Write `[unclear]` for \
anything unintelligible instead of guessing.
- Voice-over: use the exact phrase `says in an off-screen voiceover` and state right after that \
the on-screen character's lips remain closed.
- One line crossing a cut: put `<scenetrans>` at both connecting points and state that the audio \
continues, e.g. `carries over from the previous shot`. Speech cut off by the end of the video: \
use `<cutoff>`.
- Text visible on screen (signs, banners, subtitles, neon): put it in English double quotation \
marks, verbatim, e.g. `a red neon sign reading "营业中"`.
- LAST: one short paragraph of exclusions and prohibitions — what must not appear, what must not \
change, no beautification, no extra people, no subtitles, no watermark, and anything else the \
operator forbade.

## 8. overall_soundscape
1-4 English sentences in one continuous paragraph: ambience, physical action sounds, and \
non-verbal human sounds across the whole video (wind, rain, traffic, footsteps, fabric, impacts, \
breathing, laughter, panting). Do NOT repeat dialogue, lyrics, or diegetic music here — those \
live in detailed_description. Write `N/A` only if the operator explicitly demands total silence.

## 9. non_diegetic_music
1-3 English sentences describing music only the audience can hear: instrumentation, tempo, \
rhythm, dynamic development. No abstract mood words and no explanation of what the score is for. \
Music that characters can hear (a radio, a phone, someone singing on screen) is diegetic and \
belongs in detailed_description instead. Write `__NO_MUSIC__` when there is no audience-only \
music.

## 10. SELF-CHECK BEFORE YOU ANSWER
1. Six headers? Correct spelling, correct order, lowercase, colon, blank line between?
2. Any `<Video N>` or `<Audio N>` anywhere? If yes, DELETE — the node will hard-fail the render.
3. Every `<Picture N>` within 1..__PICTURE_COUNT__ and angle-bracketed? Each one used at least once?
4. Every `<Subject N>` angle-bracketed, never bare `Subject 1`?
5. `[Shot 1]` without a timestamp, later shots with strictly increasing `At MM:SS.mmm` inside \
__DURATION__ s?
6. retention_analysis marker spelled exactly as one of the four allowed values? No `(Sx)` there?
7. All dialogue inside `<d>[Language] ...</d>` with the operator's exact words?
8. Any Chinese outside `<d>` and outside quoted on-screen text? If yes, rewrite it in English.
9. detailed_description between __WORD_MIN__ and __WORD_MAX__ words?
10. Nothing but the sections themselves in your answer?

## 11. WORKED EXAMPLE — STRUCTURE ONLY
This example was written by this tool to show the exact shape of a __PICTURE_COUNT__-image, \
no-audio-reference Ref2VA prompt. Learn the structure, the label discipline, the shot timing \
notation and the section rhythm. NEVER copy its content: it describes a woman, a cat and a desk \
that have nothing to do with the operator's job.

__EXAMPLE__
"""


def build_official_system_prompt(duration: float, include_audio: bool = True,
                                 extra_rules: str = "", picture_count: int = 3) -> str:
    scheme = SCHEMES["official"]
    sections = scheme["sections"] if include_audio else scheme["sections_no_audio"]
    block = "\n".join("%d. `%s:`" % (i + 1, s) for i, s in enumerate(sections))
    text = OFFICIAL_SYSTEM_TEMPLATE
    for token, value in (
        ("__DURATION__", ("%g" % duration)),
        ("__PICTURE_COUNT__", str(picture_count)),
        ("__SECTION_LIST__", ", ".join("`%s`" % s for s in sections)),
        ("__SECTION_BLOCK__", block),
        ("__RETENTION_MARKERS__", " / ".join("`%s`" % m for m in RETENTION_VISIBLE)),
        ("__CUT_PHRASES__", " / ".join("`%s`" % c for c in CUT_PHRASES)),
        ("__CAMERA_TYPES__", "; ".join(CAMERA_MOTION)),
        ("__WORD_MIN__", str(WORD_BUDGET[0])),
        ("__WORD_MAX__", str(WORD_BUDGET[1])),
        ("__NO_MUSIC__", scheme["no_music"]),
        ("__EXAMPLE__", OFFICIAL_EXAMPLE),
    ):
        text = text.replace(token, value)
    if not include_audio:
        text = text.replace("## 8. overall_soundscape", "## 8. (音频段已关闭) overall_soundscape")
        text = text.replace("## 9. non_diegetic_music", "## 9. (音频段已关闭) non_diegetic_music")
        text = ("The operator disabled the audio sections: output ONLY sections 1-4 "
                "(subject_definitions, summary, retention_analysis, detailed_description) and "
                "stop there.\n\n" + text)
    if extra_rules and extra_rules.strip():
        text += ("\n## 12. OPERATOR'S EXTRA HARD RULES (highest priority, override anything "
                 "above on conflict)\n" + extra_rules.strip() + "\n")
    return text


# --------------------------------------------------------------------------
# 给界面看的中文说明（不是发给 LLM 的）
# --------------------------------------------------------------------------
RULES_ZH = """官方 Ref2VA 六段（本工具默认）
  1. subject_definitions:    每行一个 <Subject N>，在同一行里引用 <Picture N>
  2. summary:                一段话，必须以 [reference generation] 这类任务前缀开头
  3. retention_analysis:     每个标签一行，标记 fully_preserved / partially_preserved /
                             attribute_transfer / weak_reference
  4. detailed_description:   先一两句定调，再 [Shot 1]（不带时间戳）、
                             [Shot 2] At 00:03.500, ... 350-500 词
  5. overall_soundscape:     只写画面内声音，1-4 句
  6. non_diegetic_music:     只写观众能听到的配乐，没有就写 N/A

台词与说话人
  <d>[Chinese] 你等等，我还没看完。</d>     原话原语言，一个字都不许改
  <Subject 2> (S1) says, <d>...</d>        说话人编号 (S1)(S2)，同一个人全程同号
  says in an off-screen voiceover          旁白固定说法，且要写明"嘴唇保持闭合"
  <scenetrans> / <cutoff>                  台词跨镜 / 被视频结尾截断

运镜三要素（写在句子里，不要堆标签）
  类型：Zoom In/Out、Push In/Pull Out、Pan Left/Right、Truck Left/Right、Tilt Up/Down、
        Pedestal Up/Down、Arc Shot、Tracking Shot、Static Shot、Shake Slightly/Strongly、
        POV、Roll Clockwise/Counterclockwise
  幅度：with small amplitude / with large amplitude（中等省略）
  速度：at slow speed / at fast speed（正常省略）

节点会直接报错的写法（strict_prompt_tags=True）
  ✗ <Video 1> / <Audio 1>     你没连视频和音频参考 → raise ValueError，渲染当场死
  ✗ <Picture 4>               只连了 3 张图，序号越界 → 同上
  ✗ 裸写 Picture 4            节点正则一样抓，越界一样死
  △ <Image 1>                 会被自动改写成 <Picture 1>，建议直接写 <Picture 1>
  △ <Picture 0>               会被自动改成 <Picture 1> 并给警告
  ✓ <Subject N>               节点完全不校验，但模型靠它认人，必须带尖括号

时长
  节点策略 official_2_to_15s → 2~15 秒硬区间；官方 skill 建议 4~15 秒。"""


def selftest():
    """python h3_skill.py 直接跑：校验本模块自己的规则没写歪。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sp = build_official_system_prompt(6.0, True, "不要出现第三个人。", 3)
    assert "__DURATION__" not in sp and "__PICTURE_COUNT__" not in sp, "占位符没替换干净"
    assert "__EXAMPLE__" not in sp and "__SECTION_BLOCK__" not in sp
    for s in REF2VA_SECTIONS:
        assert s in sp, "system prompt 里缺段落 %s" % s
    assert "6 seconds" in sp
    assert "不要出现第三个人。" in sp
    assert "<Video N>" in sp and "raises ValueError" in sp
    print("system prompt: %d 字符 / %d 行" % (len(sp), sp.count("\n") + 1))

    # 自己的范例必须能通过自己的媒体标签检查
    bad = media_tag_problems(OFFICIAL_EXAMPLE)
    assert not [m for lv, m in bad if lv == "err"], bad
    assert missing_pictures(OFFICIAL_EXAMPLE, 3) == [], missing_pictures(OFFICIAL_EXAMPLE, 3)
    for s in REF2VA_SECTIONS:
        assert (s + ":") in OFFICIAL_EXAMPLE, "范例缺段落 %s" % s
    assert OFFICIAL_EXAMPLE.index("subject_definitions:") < OFFICIAL_EXAMPLE.index("summary:") \
        < OFFICIAL_EXAMPLE.index("retention_analysis:") \
        < OFFICIAL_EXAMPLE.index("detailed_description:") \
        < OFFICIAL_EXAMPLE.index("overall_soundscape:") \
        < OFFICIAL_EXAMPLE.index("non_diegetic_music:")
    assert "[Shot 2] At 00:03.500," in OFFICIAL_EXAMPLE
    assert "<d>[Chinese] 你等等，我还没看完。</d>" in OFFICIAL_EXAMPLE
    words = len(re.findall(r"[A-Za-z][A-Za-z'-]*", OFFICIAL_EXAMPLE.split(
        "detailed_description:")[1].split("overall_soundscape:")[0]))
    assert WORD_BUDGET[0] <= words <= WORD_BUDGET[1] + 120, \
        "范例 detailed_description %d 词，不在官方 %d-%d 区间内，模型会学坏" % (
            words, *WORD_BUDGET)
    print("范例 detailed_description 词数：%d（官方常态 %d-%d）" % (words, *WORD_BUDGET))

    # 节点致命错必须被抓到
    cases = [
        ("<Audio 1> is the voice reference.", "err"),
        ("<Video 1> is the source video.", "err"),
        ("the shot ends on <Picture 4>", "err"),
        ("as shown in Picture 5 the woman turns", "err"),
        ("<Image 2> fixes her face", "warn"),
        ("Picture 2 fixes her face", "warn"),
        ("<Subject 1> is the woman in <Picture 1>.", None),
    ]
    for text, want in cases:
        got = media_tag_problems(text, {"pictures": 3, "videos": 0, "audios": 0})
        levels = {lv for lv, _ in got}
        if want is None:
            assert not got, (text, got)
        else:
            assert want in levels, (text, got)
        print("  %-46s -> %s" % (text[:44], sorted(levels) or "clean"))

    # 连了音频/视频参考时，同样的标签就不该报错
    ok = media_tag_problems("<Video 1> is the source. <Audio 1> is reused.",
                            {"pictures": 0, "videos": 1, "audios": 1})
    assert not [m for lv, m in ok if lv == "err"], ok

    print("缓存目录：", cache_dir())
    print(cache_summary())
    print("== h3_skill PASS ==")


if __name__ == "__main__":
    selftest()
