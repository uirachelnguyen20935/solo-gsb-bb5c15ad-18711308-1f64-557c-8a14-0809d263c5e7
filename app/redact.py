"""敏感词脱敏：精确命中合并、等长 █ 遮蔽与静音毫秒区间生成。

处理流程：
1. 在修订文本中查找所有敏感词的精确出现（同一词的重叠出现也算）；
2. 把重叠或相接（半开区间端点相等）的命中合并为半开字符区间；
3. 命中字符用等长的 █ 遮蔽，得到脱敏文本；
4. 各合并区间取首字 start、末字 end，向两侧扩展 padding_ms 并裁剪到
   录音边界 [0, total_duration]，再合并重叠或相接的毫秒区间，供音频静音。
"""
from __future__ import annotations

from .errors import InvalidTimelineError
from .schemas import (
    MuteInterval,
    RedactRequest,
    RedactResponse,
    RedactSpan,
    RevisedCharTimeline,
)

MASK_CHAR = "█"


def validate_redact(req: RedactRequest) -> None:
    """校验敏感词列表互不相同。

    前置条件：请求已通过 Pydantic 校验（敏感词 1～50 个且每个非空）。
    """
    seen: set[str] = set()
    for term in req.terms:
        if term in seen:
            raise InvalidTimelineError(
                f"敏感词必须互不相同：{term!r} 重复出现")
        seen.add(term)


def _find_merged_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """查找全部精确命中，把重叠或相接的命中合并为半开字符区间（按原文顺序）。"""
    hits: list[tuple[int, int]] = []
    for term in terms:
        pos = text.find(term)
        while pos != -1:
            hits.append((pos, pos + len(term)))
            pos = text.find(term, pos + 1)
    if not hits:
        return []
    hits.sort()
    merged: list[list[int]] = [list(hits[0])]
    for start, end in hits[1:]:
        last = merged[-1]
        if start <= last[1]:  # 重叠或相接
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def build_redaction(text: str, chars: list[RevisedCharTimeline],
                    terms: list[str], padding_ms: int,
                    total_duration: int) -> RedactResponse:
    """生成脱敏文本、合并字符区间与供音频静音的毫秒区间。"""
    merged = _find_merged_spans(text, terms)
    if not merged:
        return RedactResponse(redacted_text=text, spans=[], mute_intervals=[])

    redacted = list(text)
    spans: list[RedactSpan] = []
    for start, end in merged:
        for idx in range(start, end):
            redacted[idx] = MASK_CHAR
        spans.append(RedactSpan(
            start_index=start,
            end_index=end,
            text=text[start:end],
            start=chars[start].start,
            end=chars[end - 1].end,
        ))

    # 时间向两侧扩展 padding、裁剪到录音边界，再合并重叠或相接的区间。
    # 字符区间按原文有序且时间轴单调不回退，扩展后的毫秒区间同样有序。
    mute_intervals: list[MuteInterval] = []
    cur_start = max(0, spans[0].start - padding_ms)
    cur_end = min(total_duration, spans[0].end + padding_ms)
    for span in spans[1:]:
        start = max(0, span.start - padding_ms)
        end = min(total_duration, span.end + padding_ms)
        if start <= cur_end:  # 重叠或相接
            cur_end = max(cur_end, end)
        else:
            mute_intervals.append(MuteInterval(start=cur_start, end=cur_end))
            cur_start, cur_end = start, end
    mute_intervals.append(MuteInterval(start=cur_start, end=cur_end))

    return RedactResponse(
        redacted_text="".join(redacted),
        spans=spans,
        mute_intervals=mute_intervals,
    )
