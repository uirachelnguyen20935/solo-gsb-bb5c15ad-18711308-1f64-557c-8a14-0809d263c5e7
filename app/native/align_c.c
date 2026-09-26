/*
 * 全局最小编辑代价对齐内核（Levenshtein + “同分时优先较早原位置”）。
 *
 * 输入：源串 s[0..m-1]（按时间排序的识别字符），目标串 t[0..n-1]（人工修订文本）。
 * 输出：out[j] = 修订字符 t[j] 对齐到的识别字符下标；-1 表示该修订字符为新增。
 * 返回值：0 成功；-1 内存不足；-2 参数非法。
 *
 * 算法（O(m*n) 时间）：
 *   1) 前向两行 DP 求最小编辑代价 E；
 *   2) 反向 DP 求后缀编辑距离 B[i][j] = edit(s[i:], t[j:])，但不保存整表：
 *      按行块（块高 H ≈ √(m+1)）只在块下边界保存检查点行（uint16 紧凑存储）；
 *   3) 自 (0,0) 起“可行性贪心”游走，已用代价 spent 始终满足 spent + B[i][j] == E；
 *      游走进程中 i 单调不减，进入新行块时从该块下边界检查点行重算块内各行
 *      （每个块至多重算一次，总重算工作量不超过一整张表）。游走规则不变：
 *        - 能走“匹配/替换”对角线且仍可达到 E，则走对角线（优先匹配更早的原位置，
 *          重复字符据此结合上下文选择）；
 *        - 否则若“新增”可行则新增（保留当前识别字符，等待后面的匹配）；
 *        - 否则“删除”。
 *      该规则经穷举验证可得到最小编辑代价下、按识别字符原位置字典序最早的匹配方案。
 *
 * 内存：检查点 ⌊m/H⌋ 行 + 当前块 H+1 行 + 少量工作行，峰值 O(√m · n) 而非 O(m·n)。
 * B 使用 uint16（要求 m+n < 65535，由应用层上限保证）；m=n=20000 时峰值约 11 MB
 * （原整表方案约 800 MB）。
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int edit_cost(uint32_t a, uint32_t b) { return a == b ? 0 : 1; }

/* 由下一行 below 反向计算第 i 行：row[j] = edit(s[i:], t[j:])。
 * 调用前需已置 row[n] = m - i；j 从 n-1 到 0 依次填。 */
static void suffix_row(const uint32_t *t, int n, uint32_t sc,
                       const uint16_t *below, uint16_t *row) {
    for (int j = n - 1; j >= 0; j--) {
        int d = (int)below[j + 1] + (sc == t[j] ? 0 : 1);
        int ins = (int)row[j + 1] + 1;
        int del = (int)below[j] + 1;
        int best = d;
        if (ins < best) best = ins;
        if (del < best) best = del;
        row[j] = (uint16_t)best;
    }
}

int align_chars(const uint32_t *s, int m, const uint32_t *t, int n, int32_t *out) {
    if (m < 0 || n < 0) return -2;
    if (m > 65534 || n > 65534) return -2;

    int W = n + 1;
    size_t w = (size_t)W;

    /* ---- 1) 前向两行 DP 求 E ---- */
    int *prev = (int *)malloc(sizeof(int) * w);
    int *cur = (int *)malloc(sizeof(int) * w);
    if (!prev || !cur) { free(prev); free(cur); return -1; }
    for (int j = 0; j <= n; j++) prev[j] = j;
    for (int i = 1; i <= m; i++) {
        cur[0] = i;
        for (int j = 1; j <= n; j++) {
            int d = prev[j - 1] + edit_cost(s[i - 1], t[j - 1]);
            int ins = cur[j - 1] + 1;
            int del = prev[j] + 1;
            int best = d;
            if (ins < best) best = ins;
            if (del < best) best = del;
            cur[j] = best;
        }
        int *tmp = prev; prev = cur; cur = tmp;
    }
    int E = prev[n];
    free(prev);
    free(cur);

    /* ---- 2) 反向 DP，仅保存块下边界检查点行 ---- */
    /* 块高 H = ceil(√(m+1))：检查点行数 ⌊m/H⌋ 与块内行数 H 同为 O(√m)，
     * 二者合计内存约为整表的 2/√m，避免大输入时重新接近整表规模。 */
    int H = 1;
    while ((int64_t)H * (int64_t)H < (int64_t)(m + 1)) H++;

    int num_ckpt = m / H;  /* 检查点行：第 H, 2H, ..., num_ckpt*H 行 */

    uint16_t *ckpt = (uint16_t *)malloc(((size_t)num_ckpt * w + 1) * sizeof(uint16_t));
    uint16_t *blk = (uint16_t *)malloc(((size_t)H + 1) * w * sizeof(uint16_t));
    uint16_t *row_below = (uint16_t *)malloc(w * sizeof(uint16_t));
    uint16_t *row_cur = (uint16_t *)malloc(w * sizeof(uint16_t));
    if (!ckpt || !blk || !row_below || !row_cur) {
        free(ckpt); free(blk); free(row_below); free(row_cur);
        return -1;
    }

    for (int j = 0; j <= n; j++) row_below[j] = (uint16_t)(n - j);  /* 第 m 行 */
    if (m > 0 && m % H == 0)
        memcpy(ckpt + (size_t)(m / H - 1) * w, row_below, w * sizeof(uint16_t));
    for (int i = m - 1; i >= 0; i--) {
        row_cur[n] = (uint16_t)(m - i);
        suffix_row(t, n, s[i], row_below, row_cur);
        if (i > 0 && i % H == 0)
            memcpy(ckpt + (size_t)(i / H - 1) * w, row_cur, w * sizeof(uint16_t));
        uint16_t *tmp = row_below; row_below = row_cur; row_cur = tmp;
    }
    free(row_below);
    free(row_cur);

    /* ---- 3) 可行性贪心游走（进入新行块时自重算该块） ---- */
    int blk_top = -1;  /* blk 当前覆盖的行块起点；-1 表示尚未加载任何块 */
    for (int j = 0; j < n; j++) out[j] = -1;
    int i = 0, j = 0, spent = 0;
    while (i < m || j < n) {
        int diag_ok = 0, cd = 1;
        const uint16_t *row_i = NULL;
        if (j < n) {
            /* 确保行 i 与 i+1 所在块已载入 blk（i 单调不减，每块至多重算一次） */
            int top = (i / H) * H;
            if (top != blk_top) {
                int last = top + H;      /* 块下边界行：需可用的最大行号 */
                if (last > m) last = m;  /* 末块以第 m 行（基例行）为底 */
                /* blk 覆盖行 [top, last]，行 r 位于 blk[(r - top) * W] */
                uint16_t *below = blk + (size_t)(last - top) * w;
                if (last == m) {
                    for (int jj = 0; jj <= n; jj++)
                        below[jj] = (uint16_t)(n - jj);
                } else {
                    /* last 为 H 的整数倍且不超过 m：取自检查点 */
                    memcpy(below, ckpt + (size_t)(last / H - 1) * w,
                           w * sizeof(uint16_t));
                }
                for (int r = last - 1; r >= top; r--) {
                    uint16_t *row_r = blk + (size_t)(r - top) * w;
                    row_r[n] = (uint16_t)(m - r);
                    suffix_row(t, n, s[r], below, row_r);
                    below = row_r;
                }
                blk_top = top;
            }
            row_i = blk + (size_t)(i - blk_top) * w;
            if (i < m) {
                const uint16_t *row_ni = blk + (size_t)(i + 1 - blk_top) * w;
                cd = edit_cost(s[i], t[j]);
                diag_ok = (spent + cd + (int)row_ni[j + 1] == E);
            }
        }
        if (diag_ok) {
            out[j] = i;
            spent += cd;
            i++; j++;
        } else if (j < n && spent + 1 + (int)row_i[j + 1] == E) {
            spent++; j++;                 /* 新增：保留识别字符等待后续匹配 */
        } else {
            /* 不变式保证此删除必然可行 */
            spent++; i++;                 /* 删除该识别字符 */
        }
    }

    free(ckpt);
    free(blk);
    return 0;
}
