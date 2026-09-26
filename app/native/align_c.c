/*
 * 全局最小编辑代价对齐内核（Levenshtein + “同分时优先较早原位置”）。
 *
 * 输入：源串 s[0..m-1]（按时间排序的识别字符），目标串 t[0..n-1]（人工修订文本）。
 * 输出：out[j] = 修订字符 t[j] 对齐到的识别字符下标；-1 表示该修订字符为新增。
 * 返回值：0 成功；-1 内存不足；-2 参数非法。
 *
 * 算法（O(m*n) 时间）：
 *   1) 前向两行 DP 求最小编辑代价 E；
 *   2) 反向 DP 求后缀编辑距离 B[i][j] = edit(s[i:], t[j:])（uint16 紧凑存储），
 *      但不再保存整表：把行按块高 H 分块，只在各块底界保存整行检查点
 *      （自底向上两行滚动，落到块底界时复制整行）；
 *   3) 自 (0,0) 起“可行性贪心”游走，已用代价 spent 始终满足 spent + B[i][j] == E：
 *        - 能走“匹配/替换”对角线且仍可达到 E，则走对角线（优先匹配更早的原位置，
 *          重复字符据此结合上下文选择）；
 *        - 否则若“新增”可行则新增（保留当前识别字符，等待后面的匹配）；
 *        - 否则“删除”。
 *      该规则经穷举验证可得到最小编辑代价下、按识别字符原位置字典序最早的匹配方案。
 *   4) 回溯只需要 B 的行；游走进入某块时，用该块底界的检查点行向上重算整块，
 *      块内逐行游走，离开该块即释放当前块缓冲，再重算下一块。每个块至多重算一次，
 *      总时间仍为 O(m*n)。
 *
 * 内存（uint16，要求 m+n < 65535，由应用层上限保证；W = n+1）：
 *   检查点 (m/H+1) 行 + 当前块 H 行，峰值约 (m/H + H + 1) * W * 2 字节。
 *   块高 H ≈ sqrt(m) 按输入规模选取，使检查点与当前块行数合计约 2*sqrt(m)，
 *   即使 m=n=20000 峰值也仅约 11 MB（旧的整表实现约需 800 MB）。
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int edit_cost(uint32_t a, uint32_t b) { return a == b ? 0 : 1; }

/*
 * 用底界检查点行 bottom[0..n]（B[bot][j]）向上重算行块 [top, bot]，
 * 写入 block（共 (bot-top+1) 行 × W 列 uint16）；末行（局部下标 bot-top）
 * 直接复制 bottom，其余按后缀编辑距离递推自下而上计算。
 */
static void fill_block(const uint32_t *s, const uint32_t *t, int m, int n,
                       int W, int top, int bot, const uint16_t *bottom,
                       uint16_t *block) {
    int height = bot - top + 1;
    uint16_t *base = block + (size_t)(height - 1) * W;
    memcpy(base, bottom, sizeof(uint16_t) * (size_t)W);
    for (int li = height - 2; li >= 0; li--) {
        int i = top + li;
        uint16_t *row = block + (size_t)li * W;
        const uint16_t *nxt = row + W;
        row[n] = (uint16_t)(m - i);
        uint32_t sc = s[i];
        for (int j = n - 1; j >= 0; j--) {
            int d = (int)nxt[j + 1] + edit_cost(sc, t[j]);
            int ins = (int)row[j + 1] + 1;
            int del = (int)nxt[j] + 1;
            int best = d;
            if (ins < best) best = ins;
            if (del < best) best = del;
            row[j] = (uint16_t)best;
        }
    }
}

int align_chars(const uint32_t *s, int m, const uint32_t *t, int n, int32_t *out) {
    if (m < 0 || n < 0) return -2;
    if (m > 65534 || n > 65534) return -2;

    int W = n + 1;

    /* ---- 边界：一侧为空时无需距离表 ---- */
    if (m == 0) {
        for (int j = 0; j < n; j++) out[j] = -1;
        return 0;
    }
    if (n == 0) {
        return 0;
    }

    /* ---- 1) 前向两行 DP 求 E ---- */
    int *prev = (int *)malloc(sizeof(int) * (size_t)W);
    int *cur = (int *)malloc(sizeof(int) * (size_t)W);
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

    /* ---- 2) 反向检查点：块高 H≈sqrt(m)，只保存各块底界整行 ---- */
    int H = 1;
    while ((long long)(H + 1) * (H + 1) <= (long long)m) H++;

    int nblocks = (m + H - 1) / H;          /* 块数（自上而下），>= 1 */
    size_t cp_rows = (size_t)nblocks + 1;   /* 检查点槽：0..nblocks，槽 k 存行 k*H */

    uint16_t *chk = (uint16_t *)malloc(cp_rows * W * sizeof(uint16_t));
    uint16_t *rp = (uint16_t *)malloc((size_t)W * sizeof(uint16_t));
    uint16_t *rn = (uint16_t *)malloc((size_t)W * sizeof(uint16_t));
    if (!chk || !rp || !rn) { free(chk); free(rp); free(rn); return -1; }

    /* 自底向上两行滚动：rn 保存 B[i+1]，rp 计算 B[i]；行 m 落入槽 nblocks。 */
    for (int j = 0; j <= n; j++) rn[j] = (uint16_t)(n - j);
    memcpy(chk + (size_t)nblocks * W, rn, sizeof(uint16_t) * (size_t)W);
    for (int i = m - 1; i >= 0; i--) {
        rp[n] = (uint16_t)(m - i);
        uint32_t sc = s[i];
        for (int j = n - 1; j >= 0; j--) {
            int d = (int)rn[j + 1] + edit_cost(sc, t[j]);
            int ins = (int)rp[j + 1] + 1;
            int del = (int)rn[j] + 1;
            int best = d;
            if (ins < best) best = ins;
            if (del < best) best = del;
            rp[j] = (uint16_t)best;
        }
        if (i % H == 0) {
            int slot = i / H;               /* 行 m 已单独保存，此处 i<m，slot<nblocks */
            memcpy(chk + (size_t)slot * W, rp, sizeof(uint16_t) * (size_t)W);
        }
        uint16_t *tmp = rp; rp = rn; rn = tmp;
    }
    free(rp);
    free(rn);

    /* ---- 3) 可行性贪心游走：按需重算当前行块，用完即释放 ---- */
    for (int j = 0; j < n; j++) out[j] = -1;

    int k = 0;                              /* 当前块号，块内行 [k*H, (k+1)*H)∩[0,m] */
    int top = 0;
    int bot = H < m ? H : m;
    size_t block_cap = (size_t)(H + 1) * W; /* 最后一块可能不足 H 行，按 H+1 预留即可 */
    uint16_t *block = (uint16_t *)malloc(block_cap * sizeof(uint16_t));
    if (!block) { free(chk); return -1; }
    fill_block(s, t, m, n, W, top, bot, chk + (size_t)(k + 1) * W, block);

    int i = 0, j = 0, spent = 0;
    while (i < m || j < n) {
        int diag_ok = 0, cd = 1;
        if (i < m && j < n) {
            cd = edit_cost(s[i], t[j]);
            diag_ok = (spent + cd +
                (int)block[(size_t)(i - top + 1) * W + (j + 1)] == E);
        }
        if (diag_ok) {
            out[j] = i;
            spent += cd;
            i++; j++;
        } else if (j < n &&
                   spent + 1 +
                   (int)block[(size_t)(i - top) * W + (j + 1)] == E) {
            spent++; j++;                 /* 新增：保留识别字符等待后续匹配 */
        } else {
            /* 不变式保证此删除必然可行 */
            spent++; i++;                 /* 删除该识别字符 */
        }

        /* 走出当前块底界：重算下一块（B[bot] 已在检查点槽 k+1 中）。 */
        if (i >= bot && bot < m) {
            k++;
            top = bot;
            bot = bot + H < m ? bot + H : m;
            fill_block(s, t, m, n, W, top, bot,
                       chk + (size_t)(k + 1) * W, block);
        }
    }

    free(block);
    free(chk);
    return 0;
}
