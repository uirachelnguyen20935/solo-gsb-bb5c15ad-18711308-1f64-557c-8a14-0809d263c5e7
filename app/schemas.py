"""请求/响应数据模型。"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints


class RecognizedChar(BaseModel):
    """单个识别字符及其起止时间（毫秒）。"""

    model_config = ConfigDict(extra="forbid")

    char: str = Field(..., description="识别字符，必须且只能包含 1 个字符")
    start: StrictInt = Field(..., description="起始时间（毫秒）")
    end: StrictInt = Field(..., description="结束时间（毫秒）")


class AlignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recognized: list[RecognizedChar] = Field(
        ..., description="按时间排序的识别字符序列"
    )
    total_duration: StrictInt = Field(
        ..., alias="total_duration",
        description="录音总时长（毫秒），须为非负整数",
    )
    revised_text: str = Field(..., description="人工修订后的完整文本")


class CalibrationPoint(BaseModel):
    """单个校准点：旧时间 at_ms 在校准后应为 corrected_ms。"""

    model_config = ConfigDict(extra="forbid")

    at_ms: StrictInt = Field(
        ..., description="旧时间轴上的位置（毫秒），须严格位于 (0, total_duration) 内"
    )
    corrected_ms: StrictInt = Field(
        ..., description="校准后的新时间（毫秒），须严格位于 (0, total_duration) 内"
    )


class RetimeRequest(AlignRequest):
    """校时输入与 1～20 个分段线性校准点。"""

    calibrations: list[CalibrationPoint] = Field(
        ..., min_length=1, max_length=20,
        description="校准点序列；at_ms 与 corrected_ms 均须严格递增",
    )


class LocateQuoteRequest(AlignRequest):
    """校时输入与引语定位参数。"""

    quote: str = Field(..., min_length=1, description="待查找的非空引语")
    max_edits: StrictInt = Field(
        ..., ge=0, le=2,
        description="允许的插入、删除、替换最大总次数（0～2）",
    )


class CaptionsRequest(AlignRequest):
    """校时输入与字幕切分参数。"""

    max_chars: StrictInt = Field(
        ..., ge=1, le=80,
        description="每段字幕最大字符数（1～80）",
    )
    max_duration_ms: StrictInt = Field(
        ..., ge=500, le=30000,
        description="每段字幕最大时长（毫秒，500～30000）",
    )


class SpeakerTurn(BaseModel):
    """单个说话人轮次：毫秒时间轴上的左闭右开区间 [start, end)。"""

    model_config = ConfigDict(extra="forbid")

    speaker_id: str = Field(..., min_length=1, description="非空说话人标识")
    start: StrictInt = Field(..., description="轮次起始时间（毫秒，含该时点）")
    end: StrictInt = Field(..., description="轮次结束时间（毫秒，不含该时点）")


class SpeakerTurnsRequest(AlignRequest):
    """校时输入与按时间排序、互不重叠的说话人轮次。"""

    speaker_turns: list[SpeakerTurn] = Field(
        ...,
        description="按起始时间排序、互不重叠（可端点相接）的说话人轮次序列",
    )


class EntitySpec(BaseModel):
    """单个采访实体：全局唯一 ID 与 1～5 个非空别名。"""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(..., min_length=1, description="全局唯一的实体标识")
    aliases: list[Annotated[str, StringConstraints(min_length=1)]] = Field(
        ..., min_length=1, max_length=5,
        description="实体别名列表（1～5 个），每个非空且全局不重复",
    )


class EntityLinksRequest(AlignRequest):
    """校时输入与实体共提及参数。"""

    entities: list[EntitySpec] = Field(
        ..., min_length=2, max_length=20,
        description="采访实体列表（2～20 个）；实体 ID 唯一、别名全局不重复",
    )
    bucket_ms: StrictInt = Field(
        ..., ge=1000, le=60000,
        description="时间桶宽度（毫秒，1000～60000）",
    )


class Annotation(BaseModel):
    """单条批注：唯一 ID 与旧版修订文本上的非空半开字符区间。"""

    model_config = ConfigDict(extra="forbid")

    annotation_id: str = Field(..., min_length=1, description="唯一的批注标识")
    start_index: StrictInt = Field(
        ..., description="批注在旧版修订文本中的起始字符下标（从 0 开始，半开区间）"
    )
    end_index: StrictInt = Field(
        ..., description="批注在旧版修订文本中的结束字符下标（不含该位置）"
    )


class AnnotationMigrateRequest(AlignRequest):
    """校时输入（revised_text 为新版修订稿）与旧稿批注迁移参数。"""

    old_text: str = Field(
        ..., description="旧版修订文本（批注区间所在文本）"
    )
    annotations: list[Annotation] = Field(
        ..., min_length=1, max_length=100,
        description="批注列表（1～100 条）；ID 唯一，区间为旧稿上的非空半开区间",
    )


class RedactRequest(AlignRequest):
    """校时输入与敏感词脱敏参数。"""

    terms: list[Annotated[str, StringConstraints(min_length=1)]] = Field(
        ..., min_length=1, max_length=50,
        description="敏感词列表（1～50 个），每个非空且互不相同",
    )
    padding_ms: StrictInt = Field(
        ..., ge=0, le=1000,
        description="静音区间向两侧扩展的毫秒数（0～1000）",
    )


class ClipSelection(BaseModel):
    """单个成片选择：修订稿上的非空半开字符区间。"""

    model_config = ConfigDict(extra="forbid")

    start_index: StrictInt = Field(
        ..., description="选择区间在修订稿中的起始字符下标（从 0 开始，半开区间）"
    )
    end_index: StrictInt = Field(
        ..., description="选择区间在修订稿中的结束字符下标（不含该位置），须大于 start_index"
    )


class ClipPlanRequest(AlignRequest):
    """校时输入与成片选择参数：1～20 个有序、互不重叠的非空字符区间与留白。"""

    selections: list[ClipSelection] = Field(
        ..., min_length=1, max_length=20,
        description="按修订稿顺序排列、互不重叠（可端点相接）的非空半开字符区间（1～20 个）",
    )
    padding_ms: StrictInt = Field(
        ..., ge=0, le=2000,
        description="每个源录音区间向两侧扩展的片段留白毫秒数（0～2000），扩展后裁剪到录音边界",
    )


class CoverageGapsRequest(AlignRequest):
    """校时输入与覆盖缺口审计参数。"""

    min_gap_ms: StrictInt = Field(
        ..., ge=1,
        description="最小缺口时长（毫秒）：仅输出时长不少于该值的缺口，须为正整数",
    )


class ReviewClipsRequest(AlignRequest):
    """校时输入与听审片段参数：片段数量上限与两侧留白。"""

    max_clips: StrictInt = Field(
        ..., ge=1, le=20,
        description="最多选择的互不重叠候选片段数（1～20）",
    )
    padding_ms: StrictInt = Field(
        ..., ge=0, le=2000,
        description="每个风险块音频区间向两侧扩展的毫秒数（0～2000），扩展后裁剪到录音边界",
    )


class RevisedCharTimeline(BaseModel):
    char: str = Field(..., description="修订文本中的字符")
    start: int = Field(..., description="起始时间（毫秒）")
    end: int = Field(..., description="结束时间（毫秒）")
    source: str = Field(
        ...,
        description="matched=直接匹配到识别字符；computed=按空档均匀推算的新增字符",
    )
    matched_index: int | None = Field(
        None, description="source=matched 时对应的识别字符下标（从 0 开始）"
    )
    recognized_char: str | None = Field(
        None, description="source=matched 时对应的识别字符（替换纠错时与 char 不同）"
    )


class AlignResponse(BaseModel):
    total_duration: int
    chars: list[RevisedCharTimeline]
    inserted_count: int = Field(..., description="新增（推算）字符数")
    deleted_count: int = Field(..., description="被删除、未输出的识别字符数")
    matched_count: int = Field(..., description="匹配/替换到识别字符的修订字符数")
    backend: str = Field(..., description="对齐内核：native(C) 或 python")


class QuoteHit(BaseModel):
    start_index: int = Field(..., description="命中片段在修订文本中的起始字符下标（从 0 开始，半开区间）")
    end_index: int = Field(..., description="命中片段在修订文本中的结束字符下标（不含该位置）")
    text: str = Field(..., description="修订文本中实际命中的文字")
    edits: int = Field(..., description="实际插入、删除、替换总次数")
    start: int = Field(..., description="起始时间（毫秒），取首字符时间轴的 start")
    end: int = Field(..., description="结束时间（毫秒），取末字符时间轴的 end")


class CaptionSegment(BaseModel):
    start_index: int = Field(..., description="段首字符在修订文本中的下标（从 0 开始）")
    end_index: int = Field(..., description="段末字符下标（半开区间，不含该位置）")
    text: str = Field(..., description="该段字幕文字")
    start: int = Field(..., description="段起始毫秒，取首字符时间轴的 start")
    end: int = Field(..., description="段结束毫秒，取末字符时间轴的 end")


class RevisionBlock(BaseModel):
    type: str = Field(
        ..., description="insert=纯插入；delete=纯删除；replace=替换或插删混合")
    recognized_start: int = Field(
        ..., description="改动在识别字符序列中的起始下标（从 0 开始，半开区间）")
    recognized_end: int = Field(
        ..., description="改动在识别字符序列中的结束下标（不含该位置）")
    revised_start: int = Field(
        ..., description="改动在修订文本中的起始字符下标（从 0 开始，半开区间）")
    revised_end: int = Field(
        ..., description="改动在修订文本中的结束字符下标（不含该位置）")
    recognized_text: str = Field(
        ..., description="识别稿一侧被改动的文字（纯插入时为空串）")
    revised_text: str = Field(
        ..., description="修订稿一侧改动后的文字（纯删除时为空串）")
    start: int = Field(
        ..., description="块起始毫秒：涉及识别字符与修订时间轴字符的最早 start")
    end: int = Field(
        ..., description="块结束毫秒：涉及识别字符与修订时间轴字符的最晚 end")


class HealthResponse(BaseModel):
    status: str
    backend: str


class SpeakerBlock(BaseModel):
    speaker_id: str | None = Field(
        ..., description="归属说话人标识；未归属到任何轮次时为 null")
    start_index: int = Field(
        ..., description="块首字符在修订文本中的下标（从 0 开始）")
    end_index: int = Field(
        ..., description="块末字符下标（半开区间，不含该位置）")
    text: str = Field(..., description="该块对应的修订文本连续文字")
    start: int = Field(..., description="块起始毫秒，取首字符时间轴的 start")
    end: int = Field(..., description="块结束毫秒，取末字符时间轴的 end")


class EntityLinkHit(BaseModel):
    entity_id: str = Field(..., description="命中的实体 ID")
    start_index: int = Field(
        ..., description="命中片段在修订文本中的起始字符下标（从 0 开始，半开区间）")
    end_index: int = Field(
        ..., description="命中片段在修订文本中的结束字符下标（不含该位置）")
    start: int = Field(..., description="起始时间（毫秒），取首字符时间轴的 start")
    end: int = Field(..., description="结束时间（毫秒），取末字符时间轴的 end")


class EntityLinkEdge(BaseModel):
    source: str = Field(..., description="边一端实体 ID（按实体 ID 排序的较小者）")
    target: str = Field(..., description="边另一端实体 ID（按实体 ID 排序的较大者）")
    weight: int = Field(..., description="两个实体共同出现的时间桶数量")
    buckets: list[int] = Field(
        ..., description="两个实体共同出现的时间桶下标，按升序排列")


class EntityLinksResponse(BaseModel):
    hits: list[EntityLinkHit] = Field(
        ..., description="互不重叠的实体别名命中，按原文顺序返回")
    edges: list[EntityLinkEdge] = Field(
        ..., description="共提及边（边权非零），按实体 ID 排序；无共现为空列表")


class AnnotationMigration(BaseModel):
    annotation_id: str = Field(..., description="批注 ID")
    status: str = Field(
        ..., description="complete=全部存活；partial=部分存活；orphan=无存活")
    old_start_index: int = Field(
        ..., description="批注在旧版修订文本中的起始字符下标（从 0 开始，半开区间）")
    old_end_index: int = Field(
        ..., description="批注在旧版修订文本中的结束字符下标（不含该位置）")
    new_start_index: int | None = Field(
        ..., description="迁移后在新稿中的起始字符下标；orphan 时为 null")
    new_end_index: int | None = Field(
        ..., description="迁移后在新稿中的结束字符下标（半开区间，不含）；orphan 时为 null")
    start: int | None = Field(
        ..., description="新稿片段首字符时间轴的 start（毫秒）；orphan 时为 null")
    end: int | None = Field(
        ..., description="新稿片段末字符时间轴的 end（毫秒）；orphan 时为 null")


class RedactSpan(BaseModel):
    start_index: int = Field(
        ..., description="合并区间在修订文本中的起始字符下标（从 0 开始，半开区间）")
    end_index: int = Field(
        ..., description="合并区间在修订文本中的结束字符下标（不含该位置）")
    text: str = Field(..., description="该合并区间在修订文本中的原文")
    start: int = Field(..., description="起始时间（毫秒），取首字符时间轴的 start")
    end: int = Field(..., description="结束时间（毫秒），取末字符时间轴的 end")


class MuteInterval(BaseModel):
    start: int = Field(..., description="静音起始毫秒（含），已裁剪到录音边界")
    end: int = Field(..., description="静音结束毫秒（不含），已裁剪到录音边界")


class RedactResponse(BaseModel):
    redacted_text: str = Field(
        ..., description="脱敏后的修订文本（命中字符替换为等长的 █）")
    spans: list[RedactSpan] = Field(
        ..., description="重叠或相接命中合并后的敏感字符区间，按原文顺序；无命中为空列表")
    mute_intervals: list[MuteInterval] = Field(
        ..., description="供音频静音的毫秒区间：各区间扩展 padding、裁剪到录音边界后再合并；无命中为空列表")


class ClipSegment(BaseModel):
    segment_index: int = Field(
        ..., description="成片片段序号（从 0 开始，按源录音时间升序）")
    source_start: int = Field(
        ..., description="该片段在源录音中的起始毫秒（含），已扩展留白并裁剪到录音边界")
    source_end: int = Field(
        ..., description="该片段在源录音中的结束毫秒（不含），已扩展留白并裁剪到录音边界")
    output_start: int = Field(
        ..., description="该片段在成片中的起始毫秒（含，从 0 起连续拼接）")
    output_end: int = Field(
        ..., description="该片段在成片中的结束毫秒（不含）")
    duration_ms: int = Field(
        ..., description="该片段成片时长（毫秒），等于 source_end - source_start")


class ClipSelectionResult(BaseModel):
    index: int = Field(
        ..., description="选择区间的输入序号（从 0 开始，按输入顺序返回）")
    start_index: int = Field(
        ..., description="选择区间在修订稿中的起始字符下标（从 0 开始，半开区间）")
    end_index: int = Field(
        ..., description="选择区间在修订稿中的结束字符下标（不含该位置）")
    text: str = Field(..., description="该选择区间对应的修订稿原文")
    segment_index: int = Field(
        ..., description="所属合并成片片段的序号（从 0 开始）")
    output_start: int = Field(
        ..., description="该选择内容在成片中的起始毫秒（含，随所属片段整体平移）")
    output_end: int = Field(
        ..., description="该选择内容在成片中的结束毫秒（不含）")


class ClipPlanResponse(BaseModel):
    segments: list[ClipSegment] = Field(
        ..., description="合并后的成片片段：源录音区间重叠或相接时合并，按源录音时间升序")
    total_duration: int = Field(
        ..., description="成片总时长（毫秒），等于各片段时长之和")
    selections: list[ClipSelectionResult] = Field(
        ..., description="每个选中字符区间的成片归属与时间，按输入顺序返回")


class ComputedSpan(BaseModel):
    """一段连续 computed 修订字符在修订文本中的半开下标区间。"""

    start_index: int = Field(
        ..., description="段首字符在修订文本中的下标（从 0 开始，半开区间）")
    end_index: int = Field(
        ..., description="段末字符下标（不含该位置）")


class CoverageGap(BaseModel):
    """单个录音覆盖缺口：起止毫秒、两侧最近 matched 修订字符与内部 computed 段。"""

    start: int = Field(..., description="缺口起始毫秒（含）")
    end: int = Field(..., description="缺口结束毫秒（不含）")
    left_matched_index: int | None = Field(
        ..., description="缺口左侧最近的 matched 修订字符在修订文本中的下标"
        "（从 0 开始）；左侧没有 matched 修订字符时为 null")
    right_matched_index: int | None = Field(
        ..., description="缺口右侧最近的 matched 修订字符在修订文本中的下标"
        "（从 0 开始）；右侧没有 matched 修订字符时为 null")
    computed_spans: list[ComputedSpan] = Field(
        ..., description="时间与缺口相交的连续 computed 修订字符半开下标段，"
        "按原文顺序；没有则为空列表")


class ReviewClip(BaseModel):
    """单个听审片段：音频毫秒区间与覆盖的连续风险字符半开下标。"""

    start: int = Field(..., description="片段音频起始毫秒（含），已扩展留白并裁剪到录音边界")
    end: int = Field(..., description="片段音频结束毫秒（不含），已扩展留白并裁剪到录音边界")
    start_index: int = Field(
        ..., description="风险块首字符在修订文本中的下标（从 0 开始，半开区间）")
    end_index: int = Field(
        ..., description="风险块末字符下标（不含该位置）")
    risk_count: int = Field(..., description="该片段覆盖的风险字符数")
