"""成片剪辑规划：把修订稿上的选中字符区间映射为源录音裁剪与成片拼接方案。

处理流程：
1. 校验 1～20 个选择区间为修订稿上的非空半开区间，按原文顺序排列且互不重叠
   （端点相接合法）；
2. 每个选择区间映射到逐字时间轴：首字 start、末字 end；映射后零时长的选择
   （非法空选择）返回 422；
3. 各源录音区间向两侧各扩展 padding_ms，裁剪到录音边界 [0, total_duration]；
4. 把重叠或相接（半开区间端点相等）的源录音区间合并为成片片段，避免同一段
   声音在成片中重复出现；
5. 片段按源录音时间顺序从 0 开始连续拼接得到成片时间轴，并标明每个选择区间
   所属的合并片段及其在成片中的起止时间。
"""
from __future__ import annotations

from .errors import InvalidTimelineError
from .schemas import (
    ClipPlanRequest,
    ClipPlanResponse,
    ClipSegment,
    ClipSelectionResult,
    RevisedCharTimeline,
)


def validate_clip_selections(req: ClipPlanRequest) -> None:
    """校验选择区间：非空半开、落在修订稿内、按顺序排列且互不重叠。

    前置条件：请求已通过 Pydantic 校验（选择区间 1～20 个、下标为整数）。
    """
    text_len = len(req.revised_text)
    for idx, sel in enumerate(req.selections):
        if sel.start_index < 0 or sel.end_index < 0:
            raise InvalidTimelineError(
                f"第 {idx} 个选择区间的字符下标不能为负数："
                f"start_index={sel.start_index}, end_index={sel.end_index}")
        if sel.end_index <= sel.start_index:
            raise InvalidTimelineError(
                f"第 {idx} 个选择区间必须是非空半开区间 "
                f"(start_index < end_index)，当前为 "
                f"[{sel.start_index}, {sel.end_index})")
        if sel.start_index >= text_len or sel.end_index > text_len:
            raise InvalidTimelineError(
                f"第 {idx} 个选择区间 [{sel.start_index}, {sel.end_index}) "
                f"超出修订稿长度 {text_len}")

    for idx in range(1, len(req.selections)):
        prev = req.selections[idx - 1]
        cur = req.selections[idx]
        if cur.start_index < prev.start_index:
            raise InvalidTimelineError(
                f"选择区间必须按修订稿顺序（起始下标）排列：第 {idx - 1} 个 "
                f"start_index={prev.start_index}，第 {idx} 个 "
                f"start_index={cur.start_index}")
        # 半开区间：前一个 end_index 等于后一个 start_index（端点相接）不算重叠。
        if cur.start_index < prev.end_index:
            raise InvalidTimelineError(
                f"选择区间不得重叠：第 {idx - 1} 个 "
                f"[{prev.start_index},{prev.end_index}) 与第 {idx} 个 "
                f"[{cur.start_index},{cur.end_index}) 重叠")


def build_clip_plan(text: str, chars: list[RevisedCharTimeline],
                    req: ClipPlanRequest) -> ClipPlanResponse:
    """把选择区间映射到逐字时间轴，扩展留白、合并并生成成片拼接方案。"""
    total = req.total_duration
    padding = req.padding_ms

    # 选择区间在修订稿上有序、互不重叠，逐字时间轴单调不回退，因此映射出的
    # 源录音区间同样按时间非递减排列。
    raw: list[tuple[int, int]] = []
    for idx, sel in enumerate(req.selections):
        src_start = chars[sel.start_index].start
        src_end = chars[sel.end_index - 1].end
        if src_end <= src_start:
            raise InvalidTimelineError(
                f"第 {idx} 个选择区间 [{sel.start_index}, {sel.end_index}) "
                f"（{text[sel.start_index:sel.end_index]!r}）映射到逐字时间轴后"
                f"为零时长区间 [{src_start}, {src_end}]，无法构成成片片段")
        # 向两侧扩展留白并裁剪到录音边界。
        raw.append((
            max(0, src_start - padding),
            min(total, src_end + padding),
        ))

    # 合并重叠或相接（半开端点相等）的源录音区间。
    merged: list[list[int]] = [[raw[0][0], raw[0][1]]]
    owner_of_sel: list[int] = [0]
    for start, end in raw[1:]:
        last = merged[-1]
        if start <= last[1]:  # 重叠或相接 → 并入当前片段
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
        owner_of_sel.append(len(merged) - 1)

    # 成片时间轴：片段按顺序从 0 开始无缝拼接。
    segments: list[ClipSegment] = []
    output_cursor = 0
    segment_output_start: list[int] = []
    segment_source_start: list[int] = []
    for seg_idx, (start, end) in enumerate(merged):
        segment_output_start.append(output_cursor)
        segment_source_start.append(start)
        segments.append(ClipSegment(
            segment_index=seg_idx,
            source_start=start,
            source_end=end,
            output_start=output_cursor,
            output_end=output_cursor + (end - start),
            duration_ms=end - start,
        ))
        output_cursor += end - start

    # 每个选择内容随所属片段整体平移到成片时间轴。
    selection_results: list[ClipSelectionResult] = []
    for idx, sel in enumerate(req.selections):
        seg_idx = owner_of_sel[idx]
        shift = (segment_output_start[seg_idx]
                 - segment_source_start[seg_idx])
        selection_results.append(ClipSelectionResult(
            index=idx,
            start_index=sel.start_index,
            end_index=sel.end_index,
            text=text[sel.start_index:sel.end_index],
            segment_index=seg_idx,
            output_start=chars[sel.start_index].start + shift,
            output_end=chars[sel.end_index - 1].end + shift,
        ))

    return ClipPlanResponse(
        segments=segments,
        total_duration=output_cursor,
        selections=selection_results,
    )
