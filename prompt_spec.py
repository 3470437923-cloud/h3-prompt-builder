# -*- coding: utf-8 -*-
"""MiniMax H3 (ComfyUI Ref2VA) 三图参考 Input Text —— 格式契约 + 强提示词。

对应工作流：3图参考加速.json
  PrimitiveStringMultiline("Input Text (Prompt)")
      -> MiniMaxH3AudioConditioningT8(task_type=Ref2VA, audio_mode=native,
                                      strict_prompt_tags=True,
                                      ref_images.ref_image_0..2)

本模块只做三件事：
  1) 保存格式契约（官方 Ref2VA 六段 = h3_skill；工作流自带范例五段 = CANONICAL_EXAMPLE）
  2) 提供强约束 SYSTEM_PROMPT
  3) 把 GUI 收集的人工输入组装成 messages
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import h3_skill
import longvideo

# --------------------------------------------------------------------------
# 工作流里的真实范例：只作为“结构示范”，系统提示词里明确禁止抄它的内容
# --------------------------------------------------------------------------
CANONICAL_EXAMPLE = """subject_definitions:

<Subject 1> is the female elf mage shown in <Picture 1>. Preserve exactly her identity from <Picture 1>: long silver-white hair, pointed elf ears, youthful face, white-and-gold mage clothing, body proportions and appearance. She is the ONLY small humanoid fighter in the scene.

<Subject 2> is the giant RX-78-2 Gundam shown in <Picture 2>. Preserve the white, blue, red and yellow mechanical armor, Gundam head, V-fin, shield and giant mechanical proportions. Only the main Gundam from <Picture 2> should appear. Ignore and do not reproduce any other background robots or characters visible in <Picture 2>.

<Subject 3> is the grassy open landscape shown in <Picture 3>. Use <Picture 3> as the battlefield environment, preserving the grassland, distant horizon, mountains and bright outdoor lighting.

detailed_description:

A 10-second cinematic anime battle between <Subject 1> and <Subject 2> in <Subject 3>.

[Shot 1, 0-2s]
Wide establishing shot. <Subject 1>, the small female elf mage, stands on the grassy field facing the enormous <Subject 2>. Preserve the extreme size difference. Wind moves her silver-white hair and the surrounding grass.

[Shot 2, 2-5s]
<Subject 1> raises her arm toward <Subject 2>. Bright magical energy gathers in front of her hand. She suddenly fires a powerful concentrated glowing energy beam directly toward <Subject 2>. The beam travels from <Subject 1> to <Subject 2> across the battlefield.

[Shot 3, 5-7s]
The energy beam strikes near <Subject 2>, producing intense light, sparks, dust and a shockwave. <Subject 2> reacts to the attack while remaining clearly recognizable as the RX-78-2 Gundam.

[Shot 4, 7-10s]
<Subject 2> advances toward <Subject 1> with heavy mechanical movement and begins a counterattack. <Subject 1> quickly dodges sideways. The camera dynamically tracks both characters while maintaining their identities and scale difference.

Only two combatants are present: <Subject 1> from <Picture 1> and <Subject 2> from <Picture 2>. Do not introduce any additional humanoids, robots, soldiers, mecha or background characters.

overall_style:

High-quality Japanese anime action scene, smooth animation, coherent character identity, dynamic cinematic camera movement, dramatic magical beam lighting, strong impact effects, flying grass and dust, consistent environment, epic scale.

overall_soundscape:

Powerful magical energy charging sound, high-energy beam blast, heavy impact, mechanical Gundam movement, wind and battlefield ambience.

non_diegetic_music:

Epic orchestral anime battle music with rising tension and dramatic percussion."""


# --------------------------------------------------------------------------
# 每张参考图的“角色”，决定它在 subject_definitions 里怎么写
# --------------------------------------------------------------------------
ROLES = {
    "character": {
        "zh": "人物 / 角色（主体）",
        "en": "a character or person who acts in the video",
        "rule": (
            "This picture supplies an ACTING SUBJECT. Define <Subject N> as that character. "
            "Preserve identity: face, hair, skin tone and marks, body proportions, clothing "
            "(cut, colour, material), and any distinguishing feature the operator listed. "
            "If the operator says this is the only person in the scene, state it explicitly "
            "with an 'ONLY' sentence."
        ),
    },
    "face_detail": {
        "zh": "面部细节参考",
        "en": "a facial detail reference",
        "rule": (
            "This picture supplies FACE DETAIL only, not a second person. Define <Subject N> as "
            "the facial detail reference and list the exact features to preserve (eye shape, "
            "eyebrow thickness, nose shape, lip shape, moles/scars, skin texture) and add "
            "'without any beautification'. Never let this become an extra character on screen."
        ),
    },
    "object": {
        "zh": "物体 / 道具 / 生物 / 机甲",
        "en": "an object, prop, creature or vehicle",
        "rule": (
            "This picture supplies a NON-HUMAN SUBJECT that appears in the video. Preserve shape, "
            "scale, colours, materials and markings. If the reference image may contain other "
            "items, add: 'Only the main ... from <Picture N> should appear. Ignore and do not "
            "reproduce any other background objects or characters visible in <Picture N>.'"
        ),
    },
    "scene": {
        "zh": "场景 / 环境 / 光线",
        "en": "the setting, background and lighting",
        "rule": (
            "This picture supplies the ENVIRONMENT, not a character. Write 'Use <Picture N> as the "
            "... environment, preserving ...' and list background surfaces, light direction, light "
            "quality (soft/hard), colour temperature, time of day and the framing the operator gave. "
            "Do not describe any person inside this paragraph."
        ),
    },
    "style": {
        "zh": "画风 / 色调 / 质感参考",
        "en": "a visual style reference",
        "rule": (
            "This picture supplies STYLE ONLY. Preserve medium (photoreal / anime / 3D render), "
            "line weight, colour palette, grading, grain and lighting mood. Explicitly state that "
            "its own content, characters and objects must NOT be copied into the video."
        ),
    },
    "custom": {
        "zh": "自定义（在描述里说明）",
        "en": "a custom reference (the operator explains it)",
        "rule": (
            "Follow the operator's own description of what this picture is for. Do not assume it is "
            "a person unless the operator says so."
        ),
    },
}

CAMERA_PRESETS = {
    "完全静止，无任何运镜（推荐）": (
        "Entirely static camera. Same framing, same angle and same background throughout. "
        "No pan, no tilt, no zoom, no push-in, no handheld shake, no cuts."
    ),
    "轻微手持感": (
        "Subtle natural handheld camera. Very small organic drift only, framing and background "
        "stay essentially unchanged, no cuts."
    ),
    "缓慢推近": (
        "One slow continuous push-in, smooth and unhurried, ending on a tighter framing. No cuts."
    ),
    "缓慢拉远": (
        "One slow continuous pull-out, smooth and unhurried, ending on a wider framing. No cuts."
    ),
    "缓慢横移": (
        "One slow continuous lateral tracking move at constant speed. No cuts."
    ),
    "动态跟随": (
        "Dynamic camera tracking the action, smooth motion, keeping every subject clearly framed."
    ),
    "多分镜（有切换）": (
        "Multiple shots with cuts, as described per shot by the operator."
    ),
    "自定义（在需求里写）": "",
}

SEGMENT_STYLES = {
    "time": {
        "zh": "[0-1.5s] —— 单镜头连续动作（推荐）",
        "header": "[0-1.5s]",
    },
    "shot": {
        "zh": "[Shot 1, 0-2s] —— 多分镜",
        "header": "[Shot 1, 0-2s]",
    },
}


# --------------------------------------------------------------------------
# 系统提示词（强约束）
# --------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """ROLE
You are a strict prompt compiler. You convert a human operator's typed notes into ONE block of
"input text" for the ComfyUI MiniMax H3 workflow, task type Ref2VA (3 reference images -> video
with native audio). Your text is pasted verbatim into a PrimitiveStringMultiline node that feeds
MiniMaxH3AudioConditioningT8 with strict_prompt_tags = true. Any structural mistake breaks the
render, so format compliance matters more than creativity.

CRITICAL LIMITATION - YOU ARE BLIND
You have NO vision capability. You will never see the reference images. Every visual fact must come
from the operator's typed description of those images.
- NEVER invent visual facts: no new facial features, hair, eye colour, clothing, colours, props,
  brands, logos, background objects, number of people, or written text on objects.
- If a detail is missing, OMIT it. A shorter, faithful paragraph is correct; a richer, invented one
  is a FAILURE. Do not "improve" the operator's description.
- You MAY add non-visual craft language: framing and shot size, lens and depth of field, camera
  motion, lighting continuity, pacing, motion choreography between states the operator described,
  and identity/continuity lock phrasing. These are direction, not new facts about the images.
- When the operator's notes are in Chinese, translate their meaning faithfully. Do not add or drop
  facts in translation.

OUTPUT CONTRACT (hard requirements)
Output ONLY the input text block itself. No preamble, no "Here is...", no explanation, no markdown
code fences, no quotation marks around the whole thing, no trailing commentary, no Chinese.
Sections, in this exact order, each header on its own line, spelled exactly as shown (lowercase,
ending with a colon), with one blank line after each header and between every paragraph:
{SECTION_LIST}
Never add, rename, reorder or translate section headers. Never add titles, bullet lists, numbering
or markdown of any kind.

TAG DISCIPLINE (strict_prompt_tags is ON)
- <Picture 1>, <Picture 2>, <Picture 3> are the three reference images, in that fixed order. Never
  mention <Picture 4> or any higher number.
- Subjects map one-to-one onto pictures: <Subject 1> comes from <Picture 1>, <Subject 2> from
  <Picture 2>, <Subject 3> from <Picture 3>. Never swap them.
- Write the angle brackets exactly: <Subject 1>. Never "Subject1", "[Subject 1]", "subject 1",
  "<subject 1>", and never replace a tag with a character's proper name.
- Every subject you define must be used in detailed_description, and every <Subject N> tag used
  there must have been defined. Never use an undefined tag.
- Inside detailed_description refer to people, objects and the environment through <Subject N>
  tags. You may add one short appositive per subject, e.g. "<Subject 1>, the young man,".
- If a subject is the environment, refer to it as the location, e.g. "in <Subject 3>".

SUBJECT DEFINITION RULES
Each of the three paragraphs inside subject_definitions:
- Starts with "<Subject N> is the ... shown in <Picture N>." using the operator's short English
  label for that picture.
- Then an identity-lock sentence starting with "Preserve ..." (or "Use <Picture N> as the ...
  environment, preserving ..." for a scene picture) that lists ONLY operator-provided features, in
  the same order the operator gave them, as a comma-separated series ending with a full stop.
- Then, only when the operator asked for it, one exclusivity sentence ("She is the ONLY person in
  the scene." / "Only the main ... from <Picture N> should appear.").
- Follow the declared ROLE of each picture (given in the operator input) and never mix roles: do not
  describe the room inside a character paragraph, do not describe the face inside a scene paragraph.
- Keep each paragraph to 2-4 sentences. Dense and specific, not flowery.

DETAILED DESCRIPTION RULES
- First line after the header: one summary sentence that states the total duration in seconds, the
  shot type, and which subjects are involved, e.g. "A calm, harmless 6-second clip of <Subject 1>
  eating a small dessert, single continuous static shot, no camera movement, no cuts."
- Then the timeline segments. Timeline maths is mandatory:
  * The first segment starts at 0. Segments are contiguous: each one starts exactly where the
    previous one ended. No gaps, no overlaps, no repeated time ranges.
  * The last segment ends EXACTLY at the operator's total duration. Never exceed it, never stop
    short.
  * Segment header style must match the operator's choice, character for character:
      time style -> [0-1.5s]
      shot style -> [Shot 1, 0-2s]   (numbered from 1, in order, no gaps)
    Never mix the two styles in one output.
  * Segment length: about 1.5-3 s each unless the operator fixed the count. More segments for a
    longer duration; a 6 s clip usually needs 3-5 segments.
  * Each segment body is 2-5 sentences of concrete, filmable behaviour: what moves, how fast, where
    the gaze goes, what the hands do, breathing, expression change, and what deliberately stays
    unchanged. Write visible behaviour, never internal feelings ("she feels sad" is wrong; "her eyes
    glisten and a single tear rolls down her cheek" is right).
  * Every segment must keep the identity of each subject locked to its reference picture.
- CAMERA: apply exactly the operator's camera instruction. If the camera is static and there are no
  cuts, do not mention any camera move anywhere, and keep framing and background identical in every
  segment. If the operator chose the "time" segment style, treat the whole clip as ONE continuous
  shot and never write the word "Shot".
- LOOP: if the operator asked for a seamless loop, the final segment must return every subject to
  its starting pose, height, framing and expression, and must end with a sentence stating that the
  final frame matches the first frame so the clip loops without a visible jump.
- EXCLUSIVITY PARAGRAPH: after the last segment, add one short paragraph (2-4 sentences) that locks
  the cast and states the operator's prohibitions as plain "Do not ..." / "No ..." sentences. Always
  include identity consistency. Include every prohibition the operator listed. Do not invent
  prohibitions the operator never mentioned.

AUDIO SECTION RULES
- overall_style: 1-3 sentences on the visual look only: medium (photorealistic footage / anime /
  3D render), lighting, lens and depth of field, colour grade, motion smoothness, identity
  consistency. Reuse the operator's words where given; if none were given, keep it minimal and
  consistent with the described scene.
- overall_soundscape: DIEGETIC sound only - what a microphone on set would pick up, derived strictly
  from what is visibly happening (room tone, cloth rustle, utensils, chewing, footsteps, wind,
  mechanical servos, impacts). Add dialogue handling: if the operator supplied lines, describe voice
  quality, language, delivery and state that lip sync matches the words. If the operator asked for
  silence, write exactly: "Silent. No dialogue, no sound effects, no ambience."
- non_diegetic_music: music only, 1-2 sentences (genre, instrumentation, tempo, emotional arc). If
  the operator asked for no music, write exactly: "None."
- Never describe sound inside detailed_description; sound lives in the two audio sections only.

DIALOGUE RULES (only when the operator supplies lines)
- Put each line inside the segment where it is spoken, as: <Subject N> says quietly, "..."
- Keep the quoted words in the language the operator wrote them in. Everything else stays English.
- Describe the mouth and jaw movement so lip sync is plausible. Never add lines the operator did
  not write.

LENGTH AND LANGUAGE
- The whole output is English, except quoted dialogue. No Chinese characters anywhere else.
- Target 250-500 words. Match the granularity of the canonical example: dense, concrete, filmable.
- Plain text. One blank line between paragraphs and sections. No markdown, no emoji, no bullets.

SELF-CHECK - run silently, then output only the final block
1. Section headers: exact spelling, exact order, exact count.
2. Exactly three subjects, Subject 1/2/3 bound to Picture 1/2/3, tags spelled with angle brackets.
3. Timeline: starts at 0, contiguous, ends exactly at {DURATION} s, one consistent header style.
4. No visual fact that the operator did not give you.
5. No Chinese outside quoted dialogue. No fences, no preamble, no commentary.
6. Camera, loop, exclusivity and audio instructions all honoured.

CANONICAL EXAMPLE - STRUCTURE ONLY
This example shows the required structure, tag discipline, timeline maths and level of detail.
NEVER copy its subject matter, characters, props, actions, style or sentences. Your content must
come only from the operator's notes below.
<<<EXAMPLE-START>>>
{CANONICAL_EXAMPLE}
<<<EXAMPLE-END>>>"""


def build_system_prompt(duration: float, include_audio: bool, extra_rules: str = "",
                        scheme: str = "official", long_video: bool = False,
                        lv_window: int = 124, lv_context: int = 22) -> str:
    """组装最终 system prompt。extra_rules 是用户在“格式规则”页追加的自定义硬规则。

    scheme="official"  → MiniMax 官方 skill 的 Ref2VA 六段（h3_skill 模块实现）
    scheme="workflow5" → 工作流自带范例的五段（旧格式，留作可切换）

    long_video=True 时，把长视频（首尾相连循环节点）的规则拼在官方规则之后 ——
    循环节点会把同一份 prompt 原封不动发给每一段，所以绝对时间码必须禁掉。
    """
    if long_video:
        lv = longvideo.build_rules(duration, lv_window, lv_context)
        extra_rules = (lv + "\n\n" + extra_rules) if extra_rules.strip() else lv
    if scheme != "official":
        return _build_workflow5_system_prompt(duration, include_audio, extra_rules)
    return h3_skill.build_official_system_prompt(duration, include_audio, extra_rules,
                                                 picture_count=3)


def _build_workflow5_system_prompt(duration: float, include_audio: bool,
                                   extra_rules: str = "") -> str:
    if include_audio:
        sections = (
            "  1. subject_definitions:\n"
            "  2. detailed_description:\n"
            "  3. overall_style:\n"
            "  4. overall_soundscape:\n"
            "  5. non_diegetic_music:"
        )
    else:
        sections = (
            "  1. subject_definitions:\n"
            "  2. detailed_description:\n"
            "(Audio is disabled for this job: output ONLY these two sections and stop. "
            "Do not write overall_style, overall_soundscape or non_diegetic_music.)"
        )

    text = (
        SYSTEM_PROMPT_TEMPLATE
        .replace("{SECTION_LIST}", sections)
        .replace("{DURATION}", _fmt_num(duration))
        .replace("{CANONICAL_EXAMPLE}", CANONICAL_EXAMPLE)
    )
    if extra_rules.strip():
        text += (
            "\n\nADDITIONAL HARD RULES FROM THE OPERATOR (these override nothing above unless they "
            "are stricter; obey them exactly)\n" + extra_rules.strip()
        )
    return text


# --------------------------------------------------------------------------
# GUI -> LLM 的数据结构
# --------------------------------------------------------------------------
@dataclass
class Picture:
    role: str = "character"          # ROLES 的 key
    short_name: str = ""             # 英文短称，如 "the young man"
    description: str = ""            # 人工敲进去的图片内容描述


@dataclass
class OperatorInput:
    pictures: List[Picture] = field(default_factory=lambda: [Picture(), Picture(), Picture()])
    brief: str = ""                  # 视频需求（可中文）
    duration: float = 6.0
    segment_style: str = "time"      # time / shot
    segment_count: int = 0           # 0 = 自动
    camera: str = "完全静止，无任何运镜（推荐）"
    camera_custom: str = ""
    loop: bool = True
    include_audio: bool = True
    sound_brief: str = ""            # 声音/台词/音乐要求
    dialogue: str = ""
    prohibitions: str = ""           # 额外禁止项 / 硬性要求
    primary_audio_subject: int = 0   # 对应 prompt_primary_audio_ordinal
    extra_rules: str = ""            # 追加的自定义硬规则
    format_scheme: str = "official"  # official = 官方 Ref2VA 六段；workflow5 = 工作流五段范例
    long_video: bool = False         # 长视频（首尾相连循环节点）：禁绝对时间码 + 要一致性声明
    lv_mode: str = "continuous"      # continuous = 一段连续长镜头；segmented = 逐段给词
    lv_window: int = 124             # 节点 render_window_frames
    lv_context: int = 22             # 节点 context_frames
    page: str = "gen"                # 哪个页面产生的（gen / lv），决定结果写回哪个输出框


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else ("%.2f" % x).rstrip("0").rstrip(".")


def build_user_message(inp: OperatorInput) -> str:
    """把人工输入拼成 user message。所有图片信息都来自人工敲字，LLM 看不到图。"""
    lines: List[str] = []
    lines.append("OPERATOR INPUT (the reference images are NOT visible to you; the text below is")
    lines.append("the only source of visual truth)")
    lines.append("")
    lines.append("=== REFERENCE IMAGES, IN WORKFLOW ORDER ===")
    for i, pic in enumerate(inp.pictures[:3], start=1):
        role = ROLES.get(pic.role, ROLES["custom"])
        lines.append("")
        lines.append("[Picture %d]  == the tag <Picture %d> in your output   |   role: %s"
                     % (i, i, role["en"]))
        lines.append("  role handling rule: %s" % role["rule"])
        if pic.short_name.strip():
            lines.append("  short English label to use: %s" % pic.short_name.strip())
        else:
            lines.append(
                "  short English label to use: (not given - derive a 2-5 word label strictly from "
                "the description below, e.g. \"the young man\")"
            )
        desc = pic.description.strip() or "(the operator left this EMPTY)"
        lines.append("  operator's description of this image:")
        for d in desc.splitlines() or [""]:
            lines.append("      " + d)
        if not pic.description.strip():
            lines.append(
                "      -> Because this description is empty, define <Subject %d> with the minimum "
                "faithful wording only: state what the picture is for and preserve its appearance "
                "as shown, without inventing a single feature." % i
            )

    lines.append("")
    lines.append("=== VIDEO BRIEF (operator's own words, may be Chinese) ===")
    lines.append(inp.brief.strip() or "(the operator gave no free-form brief)")

    lines.append("")
    lines.append("=== HARD PARAMETERS ===")
    if inp.long_video:
        s = longvideo.plan_summary(inp.duration, inp.lv_window, inp.lv_context)
        lines.append("- total duration: %s seconds, rendered as %d chained segment(s) of at most "
                     "%d frames each (~%.2f s per segment)"
                     % (_fmt_num(inp.duration), max(1, s["segments"]), inp.lv_window,
                        inp.lv_window / float(longvideo.FPS)))
        lines.extend(longvideo.build_user_lines(inp.duration, inp.segment_count, inp.lv_mode,
                                                inp.lv_window, inp.lv_context))
    else:
        lines.append("- total duration: %s seconds (the timeline MUST end exactly here)"
                     % _fmt_num(inp.duration))

    cam = CAMERA_PRESETS.get(inp.camera, "")
    # 预设文本里写了 "no cuts" / "No cuts" 的就是单镜头（大小写不一致，统一小写比）
    no_cuts = "no cuts" in cam.lower()
    official = (inp.format_scheme or "official") == "official"

    if official:
        shots = int(inp.segment_count or 0)
        if shots <= 0:
            shots = 1 if no_cuts else 0
        if inp.long_video:
            if shots == 1:
                lines.append("- shot structure: exactly ONE shot for the whole video. Write "
                             "`[Shot 1]` and put no cut anywhere.")
            elif shots > 1:
                lines.append("- shot structure: exactly %d shots. Write `[Shot 1]`, `[Shot 2]`, … "
                             "with NO time value after any marker." % shots)
            else:
                lines.append("- shot structure: `[Shot 1]` … `[Shot N]` markers only, each with NO "
                             "time value. Use 2-4 shots if the action needs structure.")
            lines.append("- LONG VIDEO: absolute timestamps are FORBIDDEN in this mode. Every "
                         "segment receives this same prompt, so `[0-3s]` / `At 00:03.500,` style "
                         "ranges would make each ~5-second segment replay the entire timeline.")
        elif shots == 1:
            lines.append("- shot structure: exactly ONE shot. Write `[Shot 1]` with NO timestamp "
                         "and put no cut anywhere in the video.")
        elif shots > 1:
            lines.append("- shot structure: exactly %d shots. `[Shot 1]` carries NO timestamp; "
                         "every later shot starts with `[Shot N] At MM:SS.mmm, ` and the cut times "
                         "strictly increase and stay inside the %s-second total."
                         % (shots, _fmt_num(inp.duration)))
        else:
            lines.append("- shot structure: your choice, 2-3 shots recommended. `[Shot 1]` carries "
                         "NO timestamp; every later shot starts with `[Shot N] At MM:SS.mmm, ` and "
                         "the cut times strictly increase inside the %s-second total."
                         % _fmt_num(inp.duration))
        lines.append("- sections to output: %s"
                     % (" / ".join(h3_skill.REF2VA_SECTIONS) if inp.include_audio
                        else " / ".join(h3_skill.REF2VA_SECTIONS_NO_AUDIO)
                        + "  (audio sections disabled - stop after detailed_description)"))
        lines.append("- camera wording: use the official motion vocabulary - motion type + "
                     "amplitude + speed, written as natural English inside the shot.")
        lines.append("- dialogue: put the operator's exact words inside `<d>[Language] ...</d>` "
                     "and give every vocal source a stable `(S1)` / `(S2)` id; when a referenced "
                     "subject speaks, write `<Subject N> (Sx)`.")
        lines.append("- FORBIDDEN TAGS: never write <Video N> or <Audio N> anywhere. No video and "
                     "no audio reference is connected to this workflow, so the ComfyUI node raises "
                     "ValueError and the render dies. Only <Picture 1>..<Picture 3> and "
                     "<Subject 1>..<Subject 3> are allowed.")
    else:
        style = SEGMENT_STYLES.get(inp.segment_style, SEGMENT_STYLES["time"])
        lines.append("- segment header style: %s  (use this style for EVERY segment)"
                     % style["header"])
        if inp.segment_count and inp.segment_count > 0:
            lines.append("- number of segments: exactly %d" % inp.segment_count)
        else:
            lines.append("- number of segments: your choice, about 1.5-3 s each")
        lines.append("- sections to output: %s"
                     % (" / ".join(h3_skill.WORKFLOW5_SECTIONS) if inp.include_audio
                        else "subject_definitions / detailed_description  "
                             "(audio sections disabled - stop after detailed_description)"))

    if cam:
        lines.append("- camera: %s" % cam)
    else:
        lines.append("- camera: as described in the brief below")
    if inp.camera_custom.strip():
        lines.append("- camera (operator's extra wording): %s" % inp.camera_custom.strip())

    # 单镜头无剪辑：官方方案看镜头预设有没有 No cuts；五段方案还要求分段样式是 time
    continuous = no_cuts if official else (
        inp.segment_style == "time"
        and inp.camera not in ("多分镜（有切换）", "自定义（在需求里写）")
    )
    lines.append("- single continuous shot with no cuts: %s"
                 % ("YES - one unbroken take, identical framing throughout"
                    if continuous else "follow the camera instruction above"))
    lines.append("- seamless loop (final frame matches first frame): %s"
                 % ("YES" if inp.loop else "NO"))
    if not official:
        lines.append("- audio sections (overall_style / overall_soundscape / non_diegetic_music): %s"
                     % ("INCLUDE all three" if inp.include_audio
                        else "OMIT all three, stop after detailed_description"))
    if inp.include_audio and inp.primary_audio_subject is not None:
        lines.append("- primary speaking/sound subject for the workflow's audio ordinal: <Subject %d>"
                     % (int(inp.primary_audio_subject) + 1))

    if inp.dialogue.strip():
        lines.append("")
        lines.append("=== DIALOGUE (use these exact lines, in this language, inside the right segment) ===")
        lines.append(inp.dialogue.strip())

    if inp.sound_brief.strip():
        lines.append("")
        lines.append("=== SOUND / MUSIC REQUIREMENTS ===")
        lines.append(inp.sound_brief.strip())

    if inp.prohibitions.strip():
        lines.append("")
        lines.append("=== EXTRA PROHIBITIONS / HARD REQUIREMENTS (put them in the exclusivity paragraph) ===")
        lines.append(inp.prohibitions.strip())

    lines.append("")
    lines.append("=== YOUR TASK ===")
    lines.append("Write the input text block now. Obey the OUTPUT CONTRACT exactly. Remember:")
    lines.append("you cannot see the images, so every visual fact must come from the descriptions")
    lines.append("above. Output the block and nothing else.")
    return "\n".join(lines)


def build_messages(inp: OperatorInput) -> list:
    return [
        {"role": "system", "content": build_system_prompt(
            inp.duration, inp.include_audio, inp.extra_rules,
            scheme=getattr(inp, "format_scheme", "official"),
            long_video=bool(getattr(inp, "long_video", False)),
            lv_window=int(getattr(inp, "lv_window", 124)),
            lv_context=int(getattr(inp, "lv_context", 22)))},
        {"role": "user", "content": build_user_message(inp)},
    ]


def build_fix_messages(inp: OperatorInput, bad_output: str, problems: List[str]) -> list:
    """把校验报告丢回去，让 LLM 只修结构问题，不改内容。"""
    msgs = build_messages(inp)
    msgs.append({"role": "assistant", "content": bad_output})
    msgs.append({
        "role": "user",
        "content": (
            "Your previous block FAILED the automated format check of the ComfyUI MiniMax H3 "
            "workflow. Fix it and output the corrected block only.\n\n"
            "VIOLATIONS TO FIX:\n"
            + "\n".join("- " + p for p in problems)
            + "\n\nRULES FOR THIS REWRITE:\n"
            "- Keep all the visual content and wording you already had, unless a violation is about "
            "that exact wording.\n"
            "- Do not invent new visual facts to fill a gap.\n"
            "- Never introduce a <Video N> or <Audio N> tag while fixing: no video or audio "
            "reference is connected, and the ComfyUI node hard-fails the render on those.\n"
            "- Re-run the SELF-CHECK list, then output ONLY the corrected input text block: no "
            "preamble, no markdown fences, no explanations, no Chinese outside <d>...</d> dialogue "
            "and quoted on-screen text."
        ),
    })
    return msgs


# --------------------------------------------------------------------------
# 演示数据（“载入示例”按钮用）：来自用户提供的吃甜品范例
# --------------------------------------------------------------------------
def demo_input() -> OperatorInput:
    return OperatorInput(
        pictures=[
            Picture(
                role="character",
                short_name="the young man",
                description=(
                    "年轻男性，短而凌乱的黑发，额前有不齐的长刘海；深棕色眼睛；自然浓眉；"
                    "肤色偏暖白，右脸颊有一颗小痣；薄唇；穿纯白色圆领棉质T恤。"
                    "画面里只有他一个人，没有其他人物。"
                ),
            ),
            Picture(
                role="face_detail",
                short_name="the facial detail reference",
                description=(
                    "面部细节参考图。需要严格保留：眼型、眉毛粗细、鼻型、唇型、脸颊上的小痣、"
                    "自然皮肤质感。不要任何美颜/磨皮。"
                ),
            ),
            Picture(
                role="scene",
                short_name="the setting and lighting reference",
                description=(
                    "场景与光线参考。纯浅米色墙面背景；正面柔和均匀的室内光；中性色温；"
                    "自然手持自拍式构图，人物半身入镜。"
                ),
            ),
        ],
        brief=(
            "一段平静无害的视频：他坐在桌前吃一小份甜点，时长 6 秒，单镜头连续拍摄，"
            "完全静止的机位，没有剪辑。他面向镜头坐在一张朴素的桌子前，取景到胸部以上，"
            "表情放松中性。桌上正对画面下沿有一个小白盘子，里面放着一块草莓奶油蛋糕。"
            "动作流程：先低头看盘子 → 慢慢抬右手拿小叉子切一小口 → 送进嘴里闭唇咀嚼，"
            "眼睛微眯露出一点点满足 → 咽下去，嘴角浮起很淡的笑，手把叉子放回盘子方向，"
            "姿态回到开头的位置和表情。全程自然真实的实拍质感。"
        ),
        duration=6.0,
        segment_style="shot",
        segment_count=0,               # 0 = 自动；镜头预设写了 No cuts → 官方方案自动收成 1 个分镜
        camera="完全静止，无任何运镜（推荐）",
        loop=True,
        include_audio=True,
        sound_brief=(
            "安静的室内环境音；叉子碰瓷盘的轻微声音；很轻的咀嚼声（闭嘴、不外放）；"
            "不要背景音乐，non_diegetic_music 按所选方案写 N/A（五段方案写 None.）。"
        ),
        dialogue="",
        prohibitions=(
            "不要出现第二个人；不要站起来；不要转身；不要走动；不要换场景；"
            "不要任何文字/字幕/水印；不要运镜和剪辑；不要美颜。"
        ),
        primary_audio_subject=0,
        extra_rules="",
        format_scheme="official",
    )


def demo_input_longvideo() -> OperatorInput:
    """长视频（首尾相连）页的示例：30 秒连续长镜头，无绝对时间码。"""
    base = demo_input()
    base.brief = (
        "长视频示例：30 秒连续长镜头，单镜头不剪辑，机位完全静止。"
        "他坐在桌前慢慢吃完那一小块草莓奶油蛋糕，全程动作连续、节奏舒缓："
        "从平静地看着盘子开始，到慢慢吃完，最后安静地坐着，情绪从平静逐渐转为轻微的满足。"
        "不要在中途切换场景或改变光线，不要有时间码式的分段动作。"
    )
    base.duration = 30.0
    base.segment_style = "shot"
    base.segment_count = 0
    base.camera = "完全静止，无任何运镜（推荐）"
    base.sound_brief = (
        "安静室内环境音持续贯穿；叉子碰瓷盘的轻微声音；很轻的闭嘴咀嚼声；"
        "不要背景音乐（non_diegetic_music 写 N/A）。"
    )
    base.prohibitions = (
        "不要出现第二个人；不要站起来；不要走动；不要换场景；不要换衣服；"
        "不要改变光线方向；不要运镜和剪辑；不要任何文字/字幕/水印；"
        "不要出现任何绝对时间码（[0-3s] / At 00:03.500, 之类）。"
    )
    base.long_video = True
    base.lv_mode = longvideo.MODE_CONTINUOUS
    base.lv_window = longvideo.WINDOW_FRAMES
    base.lv_context = longvideo.DEFAULT_CONTEXT
    base.page = "lv"
    return base
