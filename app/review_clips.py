"""听审片段选取：把风险字符聚成风险块，再全局选取互不重叠的音频片段。

风险字符：修订时间轴中 source=computed（人工新增），或 source=matched 但
修订字符与识别字符不同（替换纠错）的字符；其余完全匹配字符不是风险字符。

处理流程：
1. 沿修订文本顺序，把连续的风险字符合并为风险块，记录半开字符下标；
2. 每块音频区间取首字 start、末字 end，向两侧各扩展 padding_ms，并裁剪到
   录音边界 [0, total_duration]；
3. 从全部风险块中选择至多 max_clips 个互不重叠（半开区间端点相接合法）的
   片段，依次最大化覆盖风险字符总数、最小化播放总时长，再同分取按起点排序
   的区间序列字典序最小者；
4. 按时间顺序返回各片段的音频毫秒区间、风险块半开下标与风险字符数。
"""
from __future__ import annotations

from bisect import bisect_right

from .schemas import ReviewClip, RevisedCharTimeline


def _is_risk(ch: RevisedCharTimeline) -> bool:
    """computed 新增字符，或 matched 但修订字符与识别字符不同（替换纠错）。"""
    if ch.source == "computed":
        return True
    return ch.source == "matched" and ch.recognized_char != ch.char


def _risk_blocks(chars: list[RevisedCharTimeline]) -> list[tuple[int, int]]:
    """把连续风险字符合并为半开下标区间（按原文顺序）。"""
    blocks: list[tuple[int, int]] = []
    run_start: int | None = None
    for j, ch in enumerate(chars):
        if _is_risk(ch):
            if run_start is None:
                run_start = j
        elif run_start is not None:
            blocks.append((run_start, j))
            run_start = None
    if run_start is not None:
        blocks.append((run_start, len(chars)))
    return blocks


def build_review_clips(chars: list[RevisedCharTimeline],
                       total_duration: int,
                       max_clips: int,
                       padding_ms: int) -> list[ReviewClip]:
    """全局选取覆盖风险字符最多、总时长最短、区间序列字典序最小的听审片段。"""
    raw_blocks = _risk_blocks(chars)
    if not raw_blocks:
        return []

    # 每块：风险块半开下标、扩展留白并裁剪后的音频区间、风险字符数（权重）。
    # 逐字时间轴单调不回退，故风险块的音频起止均随原文顺序非递减。
    blocks: list[tuple[int, int, int, int, int]] = []
    for block_start, block_end in raw_blocks:
        audio_start = max(0, chars[block_start].start - padding_ms)
        audio_end = min(total_duration, chars[block_end - 1].end + padding_ms)
        blocks.append((
            block_start, block_end, audio_start, audio_end,
            block_end - block_start,
        ))

    n = len(blocks)
    starts = [b[2] for b in blocks]
    ends = [b[3] for b in blocks]

    # 反向带数量上限的加权区间调度 DP。dp[k][i]：从第 i 个块开始、至多选 k 个
    # 时的最优解，键为 (风险字符总数, -播放总时长, 区间序列字典序)：
    # 风险字符越多越优；同分时总时长越短（-duration 越大）越优；仍同分时
    # 按起点排序的 (start, end) 区间序列字典序越小越优。取 i 时的区间序列为
    # ((start, end),) + 取后缀最优的区间序列，嵌套元组比较恰好是字典序。
    limit = min(max_clips, n)
    cov = [[0] * (n + 1) for _ in range(limit + 1)]
    dur = [[0] * (n + 1) for _ in range(limit + 1)]
    keys: list[list[tuple | None]] = [
        [None] * (n + 1) for _ in range(limit + 1)]
    take = [[False] * (n + 1) for _ in range(limit + 1)]

    for k in range(1, limit + 1):
        for i in range(n - 1, -1, -1):
            # 跳过第 i 块：沿用后缀 k 个名额的最优解。
            skip_cov = cov[k][i + 1]
            skip_dur = dur[k][i + 1]
            skip_key = keys[k][i + 1]

            # 选取第 i 块：与所有 start >= 本块 end 的后续块（端点相接合法）组合。
            j = bisect_right(starts, ends[i] - 1, lo=i + 1)
            tail = keys[k - 1][j]
            take_key: tuple = ((starts[i], ends[i]),) + (
                tail if tail is not None else ())
            take_cov = blocks[i][4] + cov[k - 1][j]
            take_dur = (ends[i] - starts[i]) + dur[k - 1][j]

            # 覆盖风险字符更多优先；同分时播放总时长更短优先；仍同分时区间序列
            # 字典序更小优先（嵌套元组逐位比较即按 (start, end) 的序列字典序）。
            # 三者完全相同（不同块扩展后得到完全相同的区间）时取当前块（更早者）。
            better = (
                take_cov > skip_cov
                or (take_cov == skip_cov and take_dur < skip_dur)
                or (take_cov == skip_cov and take_dur == skip_dur
                    and (skip_key is None or take_key <= skip_key))
            )
            if better:
                cov[k][i] = take_cov
                dur[k][i] = take_dur
                keys[k][i] = take_key
                take[k][i] = True
            else:
                cov[k][i] = skip_cov
                dur[k][i] = skip_dur
                keys[k][i] = skip_key

    # 从 (limit, 0) 回放：take 表示该位置取块并跳到兼容前缀，否则顺移一位。
    chosen: list[int] = []
    remaining, pos = limit, 0
    while remaining > 0 and pos < n:
        if take[remaining][pos]:
            chosen.append(pos)
            remaining -= 1
            pos = bisect_right(starts, ends[pos] - 1, lo=pos + 1)
        else:
            pos += 1

    return [
        ReviewClip(
            start=blocks[i][2],
            end=blocks[i][3],
            start_index=blocks[i][0],
            end_index=blocks[i][1],
            risk_count=blocks[i][4],
        )
        for i in chosen
    ]
