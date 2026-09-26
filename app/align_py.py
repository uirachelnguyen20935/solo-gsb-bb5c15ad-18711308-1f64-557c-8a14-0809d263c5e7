"""纯 Python 全局最小编辑代价对齐（与 C 内核算法、同分规则完全一致）。

步骤：
  1) 前向两行 DP 求最小编辑代价 E；
  2) 反向 DP 求后缀编辑距离 B，但不保存整表：按行块（块高 H ≈ √(m+1)）
     只在块下边界保存检查点行（array('H') 即 uint16 紧凑存储）；
  3) 自 (0,0) 可行性贪心游走：能匹配/替换则匹配（优先较早原位置），
     否则尝试新增（保留识别字符），最后删除。游走进程中行号单调不减，
     进入新行块时从该块下边界检查点行重算块内各行（每块至多重算一次）。

内存峰值为 O(√m · n)（检查点 ⌊m/H⌋ 行 + 当前块 H+1 行），而非整表 O(m·n)。
当共享库不可用（如无编译器的精简镜像）时作为后备实现。
"""
from __future__ import annotations

from array import array

_UINT16_MAX = 65535


def _block_height(m: int) -> int:
    """ceil(√(m+1))：检查点行数与块内行数同为 O(√m)。"""
    h = 1
    while h * h < m + 1:
        h += 1
    return h


def _suffix_row(target: str, n: int, sc: str, below, row) -> None:
    """由下一行 below 反向计算一行：row[j] = edit(s[i:], t[j:])。

    调用前需已置 row[n]；j 从 n-1 到 0 依次填。
    """
    for j in range(n - 1, -1, -1):
        d = below[j + 1] + (0 if sc == target[j] else 1)
        ins = row[j + 1] + 1
        dele = below[j] + 1
        best = d
        if ins < best:
            best = ins
        if dele < best:
            best = dele
        row[j] = best


def align_chars(source: str, target: str) -> list[int]:
    """返回 target 每个字符对齐到的 source 下标；-1 表示新增字符。"""
    m = len(source)
    n = len(target)
    width = n + 1

    if m + n > _UINT16_MAX - 1:  # 由上层限额提前拦截，这里兜底
        raise ValueError("输入过长，超出 Python 后备内核容量")

    # ---- 1) 前向两行 DP ----
    prev = list(range(width))
    cur = [0] * width
    for i in range(1, m + 1):
        cur[0] = i
        sc = source[i - 1]
        for j in range(1, width):
            d = prev[j - 1] + (0 if sc == target[j - 1] else 1)
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            best = d
            if ins < best:
                best = ins
            if dele < best:
                best = dele
            cur[j] = best
        prev, cur = cur, prev
    optimal = prev[n]

    if m == 0:
        return [-1] * n

    # ---- 2) 反向 DP，仅保存块下边界检查点行（第 H, 2H, ... 行）----
    height = _block_height(m)
    checkpoints: list[array | None] = [None] * (m // height)

    below = array('H', (n - j for j in range(width)))  # 第 m 行
    if m % height == 0:
        checkpoints[m // height - 1] = array('H', below)
    row = array('H', [0]) * width
    for i in range(m - 1, -1, -1):
        row[n] = m - i
        _suffix_row(target, n, source[i], below, row)
        if i > 0 and i % height == 0:
            checkpoints[i // height - 1] = array('H', row)
        below, row = row, below

    # ---- 3) 可行性贪心游走（进入新行块时自重算该块）----
    out = [-1] * n
    i = j = 0
    spent = 0
    blk_top = -1        # 当前块起点行；-1 表示尚未加载任何块
    blk_rows: list[array] = []  # blk_rows[r - blk_top] = 第 r 行
    while i < m or j < n:
        if j < n:
            top = (i // height) * height
            if top != blk_top:
                last = min(top + height, m)  # 块下边界行（末块为基例行 m）
                if last == m:
                    below = array('H', (n - jj for jj in range(width)))
                else:
                    below = checkpoints[last // height - 1]
                blk_rows = [None] * (last - top + 1)
                blk_rows[last - top] = below
                for r in range(last - 1, top - 1, -1):
                    row = array('H', [0]) * width
                    row[n] = m - r
                    _suffix_row(target, n, source[r], below, row)
                    blk_rows[r - top] = row
                    below = row
                blk_top = top
            row_i = blk_rows[i - blk_top]
            if i < m:
                cd = 0 if source[i] == target[j] else 1
                diag_ok = (spent + cd
                           + int(blk_rows[i + 1 - blk_top][j + 1]) == optimal)
            else:
                cd = 1
                diag_ok = False
        else:
            row_i = None
            cd = 1
            diag_ok = False
        if diag_ok:
            out[j] = i
            spent += cd
            i += 1
            j += 1
        elif j < n and spent + 1 + int(row_i[j + 1]) == optimal:
            spent += 1
            j += 1               # 新增
        else:
            spent += 1
            i += 1               # 删除
    return out
