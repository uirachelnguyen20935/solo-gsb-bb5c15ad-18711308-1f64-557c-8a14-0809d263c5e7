"""录音覆盖缺口审计：求识别字符覆盖的互补区间并关联修订稿字符。

处理流程：
1. 把每个正时长识别字符的半开毫秒区间 [start, end) 视为已覆盖
   （零时长字符不覆盖任何时间）；
2. 在录音 [0, total_duration) 内求覆盖的互补，得到按时间升序的缺口
   （录音首尾的空洞同样保留）；
3. 仅保留时长不少于 min_gap_ms 的缺口；
4. 每个缺口给出两侧最近的 matched 修订字符下标（按 matched_index 查找，
   缺侧为 null），以及时间与缺口相交的所有连续 computed 修订字符
   半开下标段，便于听审区分未识别音频与插值文字。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from .schemas import (
    ComputedSpan,
    CoverageGap,
    CoverageGapsRequest,
    RevisedCharTimeline,
)


def _raw_gaps(req: CoverageGapsRequest) -> list[tuple[int, int]]:
    """识别字符覆盖在 [0, total_duration) 内的互补缺口（按时间升序）。

    识别字符已校验为按起始时间排序且互不重叠；零时长字符不覆盖任何时间，
    录音首尾的空洞同样保留。
    """
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for item in req.recognized:
        if item.end <= item.start:
            continue  # 零时长字符不覆盖
        if item.start > cursor:
            gaps.append((cursor, item.start))
        cursor = item.end
    if cursor < req.total_duration:
        gaps.append((cursor, req.total_duration))
    return gaps


def build_coverage_gaps(
        req: CoverageGapsRequest,
        chars: list[RevisedCharTimeline]) -> list[CoverageGap]:
    """生成覆盖缺口审计结果：缺口起止、两侧 matched 下标与内部 computed 段。"""
    recognized = req.recognized
    rec_starts = [it.start for it in recognized]  # 已校验排序：非递减
    rec_ends = [it.end for it in recognized]      # 互不重叠 ⇒ 同样非递减

    # matched 修订字符的 (修订下标, matched_index)；matched_index 随修订下标递增。
    matched_positions = [j for j, ch in enumerate(chars)
                         if ch.matched_index is not None]
    matched_src = [chars[j].matched_index for j in matched_positions]

    # 逐字时间轴单调不回退：start 与 end 均非递减，可用二分定位相交字符。
    char_starts = [ch.start for ch in chars]
    char_ends = [ch.end for ch in chars]

    gaps: list[CoverageGap] = []
    for g_start, g_end in _raw_gaps(req):
        if g_end - g_start < req.min_gap_ms:
            continue

        # 左侧最近 matched：识别区间 end <= g_start 的最大识别下标，再按
        # matched_index 找到对应修订字符；右侧同理（start >= g_end）。
        # 被删除的识别字符没有 matched 修订字符，自然被跳过。
        left_rec = bisect_right(rec_ends, g_start) - 1
        pos = bisect_right(matched_src, left_rec) - 1
        left_index = matched_positions[pos] if pos >= 0 else None

        right_rec = bisect_left(rec_starts, g_end)
        pos = bisect_left(matched_src, right_rec)
        right_index = (matched_positions[pos]
                       if pos < len(matched_positions) else None)

        # 与缺口相交的字符下标范围：end > g_start 且 start < g_end。
        lo = bisect_right(char_ends, g_start)
        hi = bisect_left(char_starts, g_end)
        spans: list[ComputedSpan] = []
        run_start: int | None = None
        for j in range(lo, hi):
            if chars[j].source == "computed":
                if run_start is None:
                    run_start = j
            elif run_start is not None:
                spans.append(ComputedSpan(start_index=run_start, end_index=j))
                run_start = None
        if run_start is not None:
            spans.append(ComputedSpan(start_index=run_start, end_index=hi))

        gaps.append(CoverageGap(
            start=g_start,
            end=g_end,
            left_matched_index=left_index,
            right_matched_index=right_index,
            computed_spans=spans,
        ))
    return gaps
