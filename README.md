# 采访逐字稿校时 API

将 **ASR 按时间排序的识别字符**（每项含字符、起止毫秒）与 **人工修订后的完整文本**
做全局对齐（最少编辑代价 / Levenshtein），为修订文本生成**逐字时间轴**。

核心能力：

- **全局最少编辑代价对齐**，不是逐字贪心。
- **重复字符结合上下文选择匹配位置**；在同等最小编辑代价下，**优先匹配更早的识别字符原位置**。
- **新增字符**在「相邻已匹配字符」与「录音边界（0 / 总时长）」形成的空档内**均匀分配**时间。
- **删除字符不输出**。
- 输出时间段严格**按顺序**且**处于录音范围内**；每字符带 `matched`（匹配/替换）或 `computed`（推算）标记。
- 输入时间无效（负值、起止倒置、越界、乱序、重叠等）返回 **HTTP 422 + 明确中文错误信息**。
- 支持 `POST /locate-quote`：复用校时输入和逐字时间轴，在修订文本中全局选择互不重叠的近似引语命中。
- 支持 `POST /revision-diff`：复用校时输入与同一对齐结果，输出人工修订相对识别稿的有序改动块（insert/delete/replace）。
- 支持 `POST /captions`：复用校时输入和逐字时间轴，按字符数/时长上限把修订文本切成字幕段。
- 支持 `POST /speaker-turns`：复用校时输入与逐字时间轴，按重叠时长把每个修订字符归属到说话人轮次并合并成块。
- 支持 `POST /retime`：在 `/align` 的逐字时间轴上，按 1～20 个校准点做分段线性重映射，校正整体语速偏差。
- 支持 `POST /entity-links`：复用校时输入与逐字时间轴，按实体别名精确命中与时间桶生成采访对象的共提及关系（边权 = 共同出现的桶数）。
- 支持 `POST /annotation-migrate`：对旧版修订文本与新版修订稿做全局顺序对齐，按相同字符的匹配关系把旧稿批注区间迁移到新稿（complete/partial/orphan），并给出新稿片段的首末毫秒。
- 支持 `POST /redact`：复用校时输入与逐字时间轴，对修订文本做敏感词精确命中脱敏（等长 █ 遮蔽），并给出扩展 padding、裁剪到录音边界后的音频静音毫秒区间。
- 支持 `POST /clip-plan`：复用校时输入与逐字时间轴，把修订稿上选中的 1～20 个非空字符区间映射为源录音裁剪与成片拼接方案（扩展留白、裁剪边界、合并重叠/相接片段，避免声音重复）。
- 支持 `POST /coverage-gaps`：复用校时输入与逐字时间轴，把识别字符的半开毫秒区间视为覆盖，求录音 `[0,total_duration)` 内的覆盖缺口（仅输出时长不少于 `min_gap_ms` 者），并给出两侧最近 matched 修订字符下标与缺口内的连续 computed 字符段，便于听审区分未识别音频与插值文字。
- 支持 `POST /review-clips`：复用校时输入与逐字时间轴，把修订稿中 `source=computed` 或替换纠错（matched 但字符不同）的连续字符合成风险块，按覆盖风险字符数最大化、播放总时长最短、区间序列字典序最小选出至多 `max_clips` 个互不重叠的听审音频片段（含两侧留白与录音边界裁剪）。
- 提供 `/health` 健康检查。
- 对齐内核为 **C（ctypes 调用）**，无编译器时自动回退**纯 Python** 实现，二者算法一致。

---

## 一条命令启动（Docker Compose）

```bash
docker compose up --build -d
```

启动后（首次会构建镜像并在**容器启动时安装 Python 依赖**）：

- API 地址：`http://localhost:8000`
- 健康检查：`GET http://localhost:8000/health`
- 校时接口：`POST http://localhost:8000/align`
- 校准重映射接口：`POST http://localhost:8000/retime`
- 引语定位接口：`POST http://localhost:8000/locate-quote`
- 修订差异接口：`POST http://localhost:8000/revision-diff`
- 字幕切分接口：`POST http://localhost:8000/captions`
- 说话人轮次归属接口：`POST http://localhost:8000/speaker-turns`
- 实体共提及接口：`POST http://localhost:8000/entity-links`
- 批注迁移接口：`POST http://localhost:8000/annotation-migrate`
- 敏感词脱敏接口：`POST http://localhost:8000/redact`
- 成片剪辑规划接口：`POST http://localhost:8000/clip-plan`
- 覆盖缺口审计接口：`POST http://localhost:8000/coverage-gaps`
- 风险听审片段接口：`POST http://localhost:8000/review-clips`
- 交互式文档：`http://localhost:8000/docs`（OpenAPI：`/openapi.json`）

停止：

```bash
docker compose down
```

> 不想用 Compose，可直接：
> `docker build -t transcript-align-api . && docker run --rm -p 8000:8000 transcript-align-api`

---

## 必要配置

均通过**环境变量**配置，`docker-compose.yml` 中已给默认值：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 容器监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `MAX_RECOGNIZED_CHARS` | `20000` | 识别字符数量上限 |
| `MAX_REVISED_LENGTH` | `20000` | 修订文本长度上限 |
| `MAX_QUOTE_LENGTH` | `20000` | `/locate-quote` 引语长度上限 |
| `ALIGN_NATIVE` | `1` | `1` 优先 C 内核；`0` 强制纯 Python 后备内核 |
| `UVICORN_EXTRA_ARGS` | （空） | 透传给 uvicorn 的额外参数 |

宿主机端口映射在 `docker-compose.yml` 的 `ports`（默认 `"8000:8000"`，左侧可改）。

### 内存与规模

对齐的反向距离表是 O(m·n) 的 `uint16` 紧凑表（m=识别字符数，n=修订字符数），
峰值约为 **(m+1)×(n+1)×2 字节**，请求结束即释放。例如：

- 5,000 × 5,000 ≈ 50 MB
- 12,000 × 12,000 ≈ 288 MB
- 20,000 × 20,000 ≈ 800 MB

Compose 默认 `mem_limit: 2g`。如调大规模，请同步增大内存上限。

---

## API

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok","backend":"native"}
```

### `POST /align`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `recognized` | 数组 | 按时间排序的识别字符；元素为 `{"char","start","end"}` |
| `recognized[].char` | 字符串 | **恰好 1 个字符** |
| `recognized[].start` | 整数 | 起始毫秒，`>=0` |
| `recognized[].end` | 整数 | 结束毫秒，`>= start` |
| `total_duration` | 整数 | 录音总时长（毫秒），非负 |
| `revised_text` | 字符串 | 人工修订后的完整文本 |

示例：

```bash
curl -X POST http://localhost:8000/align \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":400,"end":500},
      {"char":"界","start":600,"end":700}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界"
  }'
```

响应：

```json
{
  "total_duration": 1000,
  "chars": [
    {"char":"你","start":0,"end":100,"source":"matched","matched_index":0,"recognized_char":"你"},
    {"char":"好","start":200,"end":300,"source":"matched","matched_index":1,"recognized_char":"好"},
    {"char":"呀","start":300,"end":400,"source":"computed","matched_index":null,"recognized_char":null},
    {"char":"世","start":400,"end":500,"source":"matched","matched_index":2,"recognized_char":"世"},
    {"char":"界","start":600,"end":700,"source":"matched","matched_index":3,"recognized_char":"界"}
  ],
  "inserted_count": 1,
  "deleted_count": 0,
  "matched_count": 4,
  "backend": "native"
}
```

字段说明：

- `chars`：与 `revised_text` **等长、同序**的逐字时间轴。
- `source`：
  - `matched`：该修订字符匹配/替换到某个识别字符（继承其 `start/end`）。
    人工纠错（替换）时 `char` 与 `recognized_char` 不同，`matched_index` 为识别字符下标。
  - `computed`：新增字符，时间在相邻锚点/录音边界空档内均匀推算。
- 时间保证：`0 ≤ start ≤ end ≤ total_duration`，且相邻字符时间不回退。

### `POST /retime`

在执行与 `/align` **完全相同**的输入校验、全局对齐与逐字校时后，再按人工给出的
**1～20 个校准点**对逐字时间轴做一次**分段线性重映射**（旧时间 → 新时间），用于校正
整体语速偏快/偏慢。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `calibrations` | 数组 | 校准点序列，数量 `1～20` |
| `calibrations[].at_ms` | 整数 | 旧时间轴上的位置（毫秒），严格位于 `(0, total_duration)` 内 |
| `calibrations[].corrected_ms` | 整数 | 校准后的新时间（毫秒），严格位于 `(0, total_duration)` 内 |

校验要求（不满足返回 **422** 中文原因）：

- 校准点数量为 `1～20` 个；
- `at_ms` 列必须**严格递增**，`corrected_ms` 列也必须**严格递增**；
- 每个 `at_ms`、`corrected_ms` 都必须是整数毫秒且严格位于开区间
  `(0, total_duration)` 内（不得等于 `0` 或 `total_duration`）。

重映射规则：

1. 录音首尾固定为 `0 → 0`、`total_duration → total_duration`，与校准点一起构成
   若干分段；
2. 每段内旧时间到新时间为**线性插值**（所有运算均为整数运算，结果**向下取整**）；
3. **校准点必须精确命中**：旧时间等于 `at_ms` 时，新时间恰好等于对应的
   `corrected_ms`（首尾端点同理），不依赖浮点；
4. 每个字的 `start`、`end` **分别**按各自所在段插值。

响应与 `/align` 同构：保留每个字的 `char`、`source`、`matched_index`、
`recognized_char`（文字、来源、匹配下标均不变），只重算 `start/end`；
`total_duration` 与各计数（`inserted_count` 等）不变。由于映射逐段非递减，
结果仍然**时间不回退、不越界**（`0 ≤ start ≤ end ≤ total_duration`）。

示例（把前半段放慢：旧时间 500ms 处实际应为 750ms）：

```bash
curl -X POST http://localhost:8000/retime \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":700,"end":800}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "calibrations": [
      {"at_ms": 500, "corrected_ms": 750}
    ]
  }'
```

响应：

```json
{
  "total_duration": 1000,
  "chars": [
    {"char":"你","start":0,"end":150,"source":"matched","matched_index":0,"recognized_char":"你"},
    {"char":"好","start":300,"end":450,"source":"matched","matched_index":1,"recognized_char":"好"},
    {"char":"呀","start":450,"end":750,"source":"computed","matched_index":null,"recognized_char":null},
    {"char":"世","start":750,"end":800,"source":"matched","matched_index":2,"recognized_char":"世"},
    {"char":"界","start":850,"end":900,"source":"matched","matched_index":3,"recognized_char":"界"}
  ],
  "inserted_count": 1,
  "deleted_count": 0,
  "matched_count": 4,
  "backend": "native"
}
```

注意“世”的 `start=500` 恰好命中校准点，重映射后**精确**为 `750`；录音末尾
（旧时间 `1000`）仍固定为 `1000`。

无效校准点示例（`at_ms` 未严格递增）返回 **422**：

```json
{"detail": "校准点的 at_ms 必须严格递增：第 0 个为 400，第 1 个为 400"}
```

### `POST /locate-quote`

在执行与 `/align` 完全相同的输入校验和逐字校时后，从 `revised_text` 中查找与
`quote` 的 **Levenshtein 编辑距离**（插入、删除、替换）不超过 `max_edits` 的连续非空片段。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `quote` | 字符串 | 待定位引语，必须非空，默认长度不超过 20,000 字符 |
| `max_edits` | 整数 | 最大编辑次数，允许 `0`、`1`、`2` |

示例：

```bash
curl -X POST http://localhost:8000/locate-quote \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":700,"end":800}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "quote": "你好世界",
    "max_edits": 1
  }'
```

响应：

```json
[
  {
    "start_index": 0,
    "end_index": 5,
    "text": "你好呀世界",
    "edits": 1,
    "start": 0,
    "end": 800
  }
]
```

字段说明：

- `start_index` / `end_index`：命中片段在 `revised_text` 中的字符下标；`end_index` 为半开区间（不含）。
- `text`：修订文本中实际命中的连续文字。
- `edits`：实际插入、删除、替换次数；仅保留 `edits <= max_edits` 的片段。
- `start` / `end`：分别取命中片段首字符时间轴的 `start` 和末字符时间轴的 `end`（毫秒）。
- 同一位置可能同时存在多个近似片段。接口会在所有候选中先选出**互不重叠**集合：
  依次最大化命中数量、最小化 `edits` 总和；仍同分时，选择下标序列字典序最小者，并按原文顺序返回。
- 没有命中时返回 `[]`。

### `POST /revision-diff`

在执行与 `/align` 完全相同的输入校验与全局对齐后，输出**人工修订相对识别稿**
的有序改动块：

- 未匹配的识别字符 → **删除**；
- 无来源的修订字符 → **插入**；
- 匹配但来源字符不同 → **替换**；
- 以**未改动的匹配字符**为边界，把连续改动合并为一块；块内同时出现识别侧与
  修订侧文字（替换或插删混合）时类型为 `replace`。

请求体与 `/align` 完全相同。

示例：

```bash
curl -X POST http://localhost:8000/revision-diff \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"吗","start":0,"end":100},
      {"char":"你","start":200,"end":300},
      {"char":"好","start":400,"end":500},
      {"char":"呢","start":600,"end":700},
      {"char":"玩","start":800,"end":900}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀呢耍"
  }'
```

响应：

```json
[
  {"type":"delete","recognized_start":0,"recognized_end":1,"revised_start":0,"revised_end":0,"recognized_text":"吗","revised_text":"","start":0,"end":100},
  {"type":"insert","recognized_start":3,"recognized_end":3,"revised_start":2,"revised_end":3,"recognized_text":"","revised_text":"呀","start":500,"end":600},
  {"type":"replace","recognized_start":4,"recognized_end":5,"revised_start":4,"revised_end":5,"recognized_text":"玩","revised_text":"耍","start":800,"end":900}
]
```

字段说明：

- 按**原文顺序**返回改动块；无改动时返回 `[]`。
- `recognized_start` / `recognized_end`：块在识别字符序列中的起止下标，
  `end` 为半开区间（不含）；`revised_start` / `revised_end`：块在 `revised_text`
  中的起止字符下标，同样为半开区间。
- `recognized_text` / `revised_text`：块两侧的文字；纯插入时识别侧为空串，
  纯删除时修订侧为空串。
- `type`：`insert`（纯插入）、`delete`（纯删除）、`replace`（替换或插删混合）。
- `start` / `end`：块涉及的原识别字符与修订时间轴字符中最早的 `start`、
  最晚的 `end`（毫秒）。

### `POST /captions`

在执行与 `/align` 完全相同的输入校验和逐字校时后，按 `revised_text` 的**连续字符**
切分字幕段。每段跨度取「首字 `start` 到末字 `end`」，其**字符数**与**跨度毫秒数**
均不得超过上限；某个单字自身跨度超限（不存在任何合法切分）时返回 **422**。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_chars` | 整数 | 每段最大字符数，允许 `1～80` |
| `max_duration_ms` | 整数 | 每段最大时长（毫秒），允许 `500～30000` |

切分目标（依次）：

1. **段数最少**；
2. **断点总分最高**：断点左字为 `。！？；` 加 2000 分，再加上相邻字停顿毫秒值
   （左字 `end` 到右字 `start`，截到 `0～1000`）；
3. 仍同分时，取**断点下标序列字典序最小**者。

示例：

```bash
curl -X POST http://localhost:8000/captions \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"。","start":400,"end":500},
      {"char":"世","start":600,"end":700},
      {"char":"界","start":800,"end":900}
    ],
    "total_duration": 1000,
    "revised_text": "你好。世界",
    "max_chars": 3,
    "max_duration_ms": 30000
  }'
```

响应：

```json
[
  {"start_index": 0, "end_index": 3, "text": "你好。", "start": 0, "end": 500},
  {"start_index": 3, "end_index": 5, "text": "世界", "start": 600, "end": 900}
]
```

字段说明：

- 按**原文顺序**返回每段；`start_index` / `end_index` 为段在 `revised_text` 中的
  起止字符下标，`end_index` 为半开区间（不含）。
- `text` 为该段文字；`start` / `end` 分别取首字符时间轴的 `start` 与末字符的 `end`。
- `revised_text` 为空时返回 `[]`。

### `POST /speaker-turns`

在执行与 `/align` 完全相同的输入校验和逐字校时后，按一组**按时间排序、互不重叠
的说话人轮次**把每个修订字符归属到说话人，并把**相邻且归属相同**的字符合并成块。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `speaker_turns` | 数组 | 说话人轮次序列；元素为 `{"speaker_id","start","end"}`，允许为空数组 |
| `speaker_turns[].speaker_id` | 字符串 | **非空**说话人标识 |
| `speaker_turns[].start` | 整数 | 轮次起始毫秒（含该时点） |
| `speaker_turns[].end` | 整数 | 轮次结束毫秒（**不含**该时点），`end >= start` |

轮次为毫秒时间轴上的**左闭右开区间** `[start, end)`，必须：

- 起止时间非负、`end >= start`（允许零时长轮次）、不超出 `total_duration`；
- 按 `start` 非递减排序；
- 互不重叠——**端点相接合法**（上一轮次 `end` 等于下一轮次 `start` 不算重叠）；
- 任一不满足均返回 **422** 中文原因。

字符归属规则：

1. **正时长字符** `[start, end)`：与各轮次取左闭右开交集，归属**重叠毫秒数最大**
   的轮次；重叠时长并列时取**较早**的轮次；与所有轮次都不重叠则不归属（`null`）。
2. **零时长字符**（`start == end`）：按**所在时点**归属——时点位于某轮次区间内部
   归该轮次；恰好落在两轮次相接边界时归**右侧**轮次；落在间隙则不归属。
3. 把**相邻且 `speaker_id` 相同**的字符合并为块（相邻轮次共用同一 `speaker_id`
   时也会合并；同为未归属 `null` 的相邻字符同样合并为未归属块）。

每块返回：`speaker_id`（未归属为 `null`）、块在修订文本中的**半开下标**
`start_index` / `end_index`、块的修订文本 `text`、首尾毫秒 `start` / `end`
（首字符时间轴的 `start`、末字符时间轴的 `end`）。

示例：

```bash
curl -X POST http://localhost:8000/speaker-turns \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":700,"end":800}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "speaker_turns": [
      {"speaker_id":"主持人","start":0,"end":250},
      {"speaker_id":"嘉宾","start":250,"end":1000}
    ]
  }'
```

响应：

```json
[
  {"speaker_id":"主持人","start_index":0,"end_index":2,"text":"你好","start":0,"end":300},
  {"speaker_id":"嘉宾","start_index":2,"end_index":5,"text":"呀世界","start":300,"end":800}
]
```

说明：新增字符“呀”的推算区间为 `[300,400)`，整体落在嘉宾轮次；“好”的区间
`[200,300)` 与两轮次各重叠 50 毫秒，并列时取较早的主持人；块时间取块内首字符的
`start` 与末字符的 `end`，因此“你好”块为 `[0,300]`（跨轮次边界不影响合并）。

### `POST /entity-links`

在执行与 `/align` 完全相同的输入校验和逐字校时后，对 `revised_text` **逐字精确
匹配**各实体的别名，从全部候选中选出**互不重叠**的命中集合，再按命中首字时间
归入时间桶，生成采访对象（实体）之间的**共提及关系**。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `entities` | 数组 | 采访实体列表，数量 `2～20` |
| `entities[].entity_id` | 字符串 | **非空**实体标识，全局唯一 |
| `entities[].aliases` | 字符串数组 | 实体别名，`1～5` 个；每个**非空**且**全局不重复** |
| `bucket_ms` | 整数 | 时间桶宽度（毫秒），允许 `1000～60000` |

命中选择规则（依次）：

1. **覆盖字符数最多**（命中区间长度之和最大）；
2. **命中数最少**；
3. 仍同分时，取按**（起点、终点、实体 ID）**排序的命中序列**字典序最小**者。

归桶与边权：

- 每个命中按**首字 `start` 整除 `bucket_ms`** 归入时间桶；同一桶内同一实体
  只计一次；
- 每对实体**共同出现的桶数**即为边权；只返回边权非零的边，边按实体 ID 排序，
  边内桶下标升序；没有任何共现时 `edges` 为空列表。

示例：

```bash
curl -X POST http://localhost:8000/entity-links \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"张","start":0,"end":100},
      {"char":"三","start":100,"end":200},
      {"char":"和","start":200,"end":300},
      {"char":"李","start":300,"end":400},
      {"char":"四","start":400,"end":500},
      {"char":"谈","start":1200,"end":1300},
      {"char":"张","start":1300,"end":1400},
      {"char":"三","start":1400,"end":1500},
      {"char":"与","start":1500,"end":1600},
      {"char":"李","start":1600,"end":1700},
      {"char":"四","start":1700,"end":1800}
    ],
    "total_duration": 2000,
    "revised_text": "张三和李四谈张三与李四",
    "entities": [
      {"entity_id": "张三", "aliases": ["张三"]},
      {"entity_id": "李四", "aliases": ["李四"]}
    ],
    "bucket_ms": 1000
  }'
```

响应：

```json
{
  "hits": [
    {"entity_id": "张三", "start_index": 0, "end_index": 2, "start": 0, "end": 200},
    {"entity_id": "李四", "start_index": 3, "end_index": 5, "start": 300, "end": 500},
    {"entity_id": "张三", "start_index": 6, "end_index": 8, "start": 1300, "end": 1500},
    {"entity_id": "李四", "start_index": 9, "end_index": 11, "start": 1600, "end": 1800}
  ],
  "edges": [
    {"source": "张三", "target": "李四", "weight": 2, "buckets": [0, 1]}
  ]
}
```

字段说明：

- `hits`：按**原文顺序**返回的互不重叠命中。`start_index` / `end_index` 为命中
  在 `revised_text` 中的半开字符区间（`end_index` 不含）；`start` / `end` 分别
  取命中首字符时间轴的 `start` 与末字符时间轴的 `end`（毫秒）。
- `edges`：共提及边。`source` / `target` 为一对实体 ID（按实体 ID 排序，
  较小者在先）；`weight` 为两实体共同出现的时间桶数量；`buckets` 为共同出现的
  桶下标（升序）。没有共现时返回 `[]`。

### `POST /annotation-migrate`

在执行与 `/align` 完全相同的输入校验和逐字校时后，对**旧版修订文本** `old_text`
与**新版修订稿** `revised_text` 做**全局顺序对齐**（同一对齐内核），按
**相同字符**的匹配关系把旧稿上的批注区间迁移到新稿：

- 旧稿字符在新稿中仍有**相同字符**的匹配即视为**存活**；被删除或被**替换纠错**
  （对齐锚定但字符不同）的旧字符都不算存活；
- 每条批注的新区间取**仍存活字符在新稿中的最小覆盖区间**——从首个存活字符的
  新位置到最后一个存活字符的新位置，**区间内部的新增字符一并纳入**；
- 全部存活 → `complete`；部分存活 → `partial`；无存活 → `orphan`
  （`orphan` 的新区间与时间均为 `null`）。

请求体包含 `/align` 的全部字段（`revised_text` 为新版修订稿），另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `old_text` | 字符串 | 旧版修订文本（批注区间所在文本），默认长度不超过 20,000 字符 |
| `annotations` | 数组 | 批注列表，数量 `1～100` |
| `annotations[].annotation_id` | 字符串 | **非空**且**唯一**的批注标识 |
| `annotations[].start_index` | 整数 | 批注在旧稿中的起始字符下标（从 0 开始，半开区间） |
| `annotations[].end_index` | 整数 | 批注在旧稿中的结束字符下标（不含该位置），须大于 `start_index` |

校验要求（不满足返回 **422** 中文原因）：批注数量 `1～100`；批注 ID 非空且
互不重复；区间为旧稿上的**非空半开区间**
（`0 <= start_index < end_index <= len(old_text)`，空区间或越界均返回 422）。

示例（新稿把旧稿的“吗”修订为“呀”，其余字不变）：

```bash
curl -X POST http://localhost:8000/annotation-migrate \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":400,"end":500},
      {"char":"界","start":600,"end":700}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "old_text": "你好吗世界",
    "annotations": [
      {"annotation_id": "a1", "start_index": 1, "end_index": 3},
      {"annotation_id": "a2", "start_index": 3, "end_index": 5},
      {"annotation_id": "a3", "start_index": 2, "end_index": 3}
    ]
  }'
```

响应：

```json
[
  {"annotation_id":"a1","status":"partial","old_start_index":1,"old_end_index":3,"new_start_index":1,"new_end_index":2,"start":200,"end":300},
  {"annotation_id":"a2","status":"complete","old_start_index":3,"old_end_index":5,"new_start_index":3,"new_end_index":5,"start":400,"end":700},
  {"annotation_id":"a3","status":"orphan","old_start_index":2,"old_end_index":3,"new_start_index":null,"new_end_index":null,"start":null,"end":null}
]
```

字段说明：

- 按**批注输入顺序**返回每条的迁移结果。
- `status`：`complete`（全部存活）/ `partial`（部分存活）/ `orphan`（无存活）。
- `old_start_index` / `old_end_index`：批注在旧稿中的原区间（半开）。
- `new_start_index` / `new_end_index`：迁移后在新稿中的区间（半开）；
  `orphan` 时为 `null`。若存活字符之间夹着新稿的新增字符，新增字符一并
  纳入新区间（如旧稿“好世”在新稿“你好呀世界”中迁移为 `[1,4)` 即“好呀世”）。
- `start` / `end`：新稿片段首字符时间轴的 `start`、末字符时间轴的 `end`
  （毫秒）；`orphan` 时为 `null`。
- 示例中“吗”被替换为“呀”（替换纠错不算存活），因此 a1 只有“好”存活
  （`partial`，新区间 `[1,2)`），a3 无任何存活字符（`orphan`）。

### `POST /redact`

在执行与 `/align` 完全相同的输入校验和逐字校时后，对 `revised_text` **逐字精确
查找**所有敏感词的出现（同一词的重叠出现也算），把**重叠或相接**的命中先合并为
半开字符区间，用**等长的 `█`** 遮蔽这些字符，并给出供音频静音的毫秒区间。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `terms` | 字符串数组 | 敏感词列表，数量 `1～50`；每个**非空**且**互不相同** |
| `padding_ms` | 整数 | 静音区间向两侧扩展的毫秒数，允许 `0～1000` |

处理规则：

1. 在修订文本中查找每个敏感词的**所有精确出现**；
2. 重叠或相接（半开区间端点相等）的命中**合并为一个字符区间**；
3. 命中字符用等长的 `█` 遮蔽，得到 `redacted_text`；
4. 每个合并区间取**首字 `start`、末字 `end`**，向两侧各扩展 `padding_ms`、
   **裁剪到录音边界** `[0, total_duration]`，再把**重叠或相接**的毫秒区间合并，
   得到供音频静音的 `mute_intervals`；
5. 无命中时返回原文与空区间（`spans`、`mute_intervals` 均为空列表）。

示例：

```bash
curl -X POST http://localhost:8000/redact \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"张","start":0,"end":100},
      {"char":"三","start":100,"end":200},
      {"char":"说","start":200,"end":300},
      {"char":"李","start":300,"end":400},
      {"char":"四","start":400,"end":500},
      {"char":"来","start":500,"end":600},
      {"char":"了","start":600,"end":700}
    ],
    "total_duration": 1000,
    "revised_text": "张三说李四来了",
    "terms": ["张三", "李四"],
    "padding_ms": 30
  }'
```

响应：

```json
{
  "redacted_text": "██说██来了",
  "spans": [
    {"start_index": 0, "end_index": 2, "text": "张三", "start": 0, "end": 200},
    {"start_index": 3, "end_index": 5, "text": "李四", "start": 300, "end": 500}
  ],
  "mute_intervals": [
    {"start": 0, "end": 230},
    {"start": 270, "end": 530}
  ]
}
```

字段说明：

- `redacted_text`：脱敏后的修订文本，命中字符替换为等长的 `█`（长度不变）。
- `spans`：按**原文顺序**返回的合并字符区间。`start_index` / `end_index` 为
  半开字符下标（`end_index` 不含）；`text` 为该区间原文；`start` / `end` 分别
  取区间首字符时间轴的 `start` 与末字符时间轴的 `end`（毫秒）。
- `mute_intervals`：供音频静音的毫秒区间，按时间顺序、互不重叠也不相接。
  上例中“张三”的 `[0,200]` 扩 30ms 并裁剪到录音起点得 `[0,230]`，“李四”的
  `[300,500]` 扩 30ms 得 `[270,530]`；若 padding 加大使两者重叠或相接，
  会进一步合并为一个区间。

### `POST /clip-plan`

在执行与 `/align` 完全相同的输入校验和逐字校时后，把人工在修订稿上选中的
**1～20 个非空半开字符区间**映射到逐字时间轴，规划出源录音的裁剪方案与成片
（剪辑后时间轴）的拼接方案：

1. 每个选择区间取**首字 `start`、末字 `end`** 映射为源录音毫秒区间；
2. 各区间向**两侧各扩展** `padding_ms` 的片段留白，并**裁剪到录音边界**
   `[0, total_duration]`；
3. 把**重叠或相接**（半开区间端点相等）的源录音区间**合并为一个成片片段**，
   保证同一段声音不会在成片中重复出现；
4. 各片段按源录音时间顺序从 `0` 开始**无缝拼接**，得到成片时间轴与成片总时长；
5. 标明每个选择区间**所属的合并片段**及其在**成片中的起止时间**。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `selections` | 数组 | 选择区间列表，数量 `1～20` |
| `selections[].start_index` | 整数 | 选择区间在修订稿中的起始字符下标（从 0 开始，半开区间） |
| `selections[].end_index` | 整数 | 选择区间的结束字符下标（不含），须大于 `start_index` |
| `padding_ms` | 整数 | 片段留白毫秒数，每个源录音区间向两侧各扩展，允许 `0～2000` |

校验要求（不满足返回 **422** 中文原因）：

- 选择区间数量为 `1～20` 个；
- 每个区间是修订稿上的**非空半开区间**
  （`0 <= start_index < end_index <= len(revised_text)`），为空或越界均 422；
- 区间必须**按修订稿顺序排列**（`start_index` 非递减）且**互不重叠**——
  **端点相接合法**（前一个 `end_index` 等于后一个 `start_index`）；
- 任一选择映射到逐字时间轴后为**零时长区间**（首字 `start` 等于末字 `end`）
  时返回 422；
- `padding_ms` 必须是 `0～2000` 的整数。

示例（选中“你好”与“世界”两段，各扩 30ms 留白；两段源录音不重叠，
分别成为成片片段并从 0 开始连续拼接）：

```bash
curl -X POST http://localhost:8000/clip-plan \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":100,"end":200},
      {"char":"呀","start":300,"end":400},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":600,"end":700}
    ],
    "total_duration": 800,
    "revised_text": "你好呀世界",
    "selections": [
      {"start_index": 0, "end_index": 2},
      {"start_index": 3, "end_index": 5}
    ],
    "padding_ms": 30
  }'
```

响应：

```json
{
  "segments": [
    {"segment_index": 0, "source_start": 0, "source_end": 230,
     "output_start": 0, "output_end": 230, "duration_ms": 230},
    {"segment_index": 1, "source_start": 470, "source_end": 730,
     "output_start": 230, "output_end": 490, "duration_ms": 260}
  ],
  "total_duration": 490,
  "selections": [
    {"index": 0, "start_index": 0, "end_index": 2, "text": "你好",
     "segment_index": 0, "output_start": 0, "output_end": 200},
    {"index": 1, "start_index": 3, "end_index": 5, "text": "世界",
     "segment_index": 1, "output_start": 260, "output_end": 460}
  ]
}
```

字段说明：

- `segments`：合并后的成片片段，按源录音时间升序。`source_start` /
  `source_end` 为该片段在**源录音**中的半开毫秒区间（已扩展留白并裁剪到
  `[0, total_duration]`）；`output_start` / `output_end` 为其在**成片**中的
  半开毫秒区间（片段从 0 开始无缝拼接）；`duration_ms` 为片段时长
  （等于 `source_end - source_start`）。
- `total_duration`：**成片总时长**（毫秒），等于各片段时长之和。
- `selections`：按**输入顺序**返回每个选中字符区间的成片归属。
  `start_index` / `end_index` 为修订稿上的半开字符区间，`text` 为该区间原文；
  `segment_index` 为所属合并片段的序号；`output_start` / `output_end` 为选择
  内容随所属片段**整体平移**后在成片中的起止毫秒。
- 上例中“你好”映射为 `[0,200]`，扩 30ms 并裁剪到录音起点得片段
  `[0,230]`；“世界”映射为 `[500,700]`，扩 30ms 得 `[470,730]`，成为第二个
  片段，成片区间为 `[230,490]`，故“世界”在成片中为 `[260,460]`。

留白使片段相接时会合并为一段。例如下例两段各扩 50ms：“张三”的 `[0,200]`
扩展为 `[0,250]`，“李四”的 `[300,500]` 扩展为 `[250,550]`，二者端点相接，
合并成同一个源录音片段 `[0,550]`（成片总时长 550ms，声音不重复），两个选择
的 `segment_index` 均为 0。

### `POST /coverage-gaps`

在执行与 `/align` 完全相同的输入校验和逐字校时后，把**原始识别字符**的半开
毫秒区间 `[start, end)` 视为已覆盖（**零时长字符不覆盖**任何时间），在录音
`[0, total_duration)` 内求覆盖的互补，得到按时间升序的**覆盖缺口**
（录音**首尾的空洞同样保留**），仅输出**时长不少于 `min_gap_ms`** 的缺口，
便于听审区分**未识别音频**与**插值文字**。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `min_gap_ms` | 整数 | 最小缺口时长（毫秒），**正整数**；仅输出时长不少于该值的缺口 |

每个缺口返回：

- `start` / `end`：缺口的起止毫秒（半开区间，`end` 不含）。
- `left_matched_index` / `right_matched_index`：缺口两侧**最近的 matched
  修订字符**在修订文本中的下标（按 `matched_index` 查找；被删除的识别字符
  不算，该侧没有 matched 修订字符时为 `null`）。
- `computed_spans`：时间与缺口相交（`start < gap.end` 且 `end > gap.start`）
  的**连续 computed 修订字符**半开下标段 `[start_index, end_index)`，按原文
  顺序；没有则为空列表。matched 字符不会出现在段内，并会截断相邻段。

示例（首尾空洞与插值文字）：

```bash
curl -X POST http://localhost:8000/coverage-gaps \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":100,"end":200},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":600,"end":700},
      {"char":"界","start":700,"end":800}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "min_gap_ms": 100
  }'
```

响应：

```json
[
  {"start": 0, "end": 100, "left_matched_index": null, "right_matched_index": 0,
   "computed_spans": []},
  {"start": 300, "end": 600, "left_matched_index": 1, "right_matched_index": 3,
   "computed_spans": [{"start_index": 2, "end_index": 3}]},
  {"start": 800, "end": 1000, "left_matched_index": 4, "right_matched_index": null,
   "computed_spans": []}
]
```

说明：识别字符覆盖 `[100,300)` 与 `[600,800)`，互补缺口为 `[0,100)`、
`[300,600)`、`[800,1000)`（时长均不少于 100ms）。新增字符“呀”的推算区间为
`[300,600]`，恰好落在第二个缺口内，故该缺口带 `computed_spans` `[2,3)`；
首尾缺口外侧没有 matched 修订字符，对应侧为 `null`。

### `POST /review-clips`

在执行与 `/align` 完全相同的输入校验和逐字校时后，从修订稿中找出需要人工
**听审复核**的字符：`source=computed`（人工新增，时间为推算）或
`source=matched` 但修订字符与识别字符不同（**替换纠错**）；其余完全匹配的
字符不是风险字符。处理流程：

1. 沿修订文本顺序，把**连续的风险字符**合并成一个**风险块**（安全字符会切断
   风险块）；
2. 每个风险块的音频区间取**首字 `start` 到末字 `end`**，向**两侧各扩展**
   `padding_ms`，并**裁剪到录音边界** `[0, total_duration]`（允许零时长片段）；
3. 从全部风险块中选择**至多 `max_clips` 个互不重叠**的片段（半开区间
   **端点相接合法**），目标依次为：
   1. **覆盖的风险字符总数最大**（一个片段恰好覆盖其风险块内的风险字符）；
   2. 同分则**播放总时长最短**（各片段 `end - start` 之和最小）；
   3. 仍同分则取**按起点排序的区间序列字典序最小**的方案（逐位比较
      `(start, end)`）。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_clips` | 整数 | 最多选择的互不重叠片段数，允许 `1～20` |
| `padding_ms` | 整数 | 每个风险块音频区间向两侧扩展的毫秒数，允许 `0～2000` |

示例（“呀”“耍”为新增 computed、“嘛”替换识别字“吗”，各成风险块；扩 50ms、
最多选 3 段，三段互不重叠故全部返回）：

```bash
curl -X POST http://localhost:8000/review-clips \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":600,"end":700},
      {"char":"再","start":800,"end":900},
      {"char":"见","start":900,"end":1000},
      {"char":"吗","start":1000,"end":1100}
    ],
    "total_duration": 1200,
    "revised_text": "你好呀世界耍再见嘛",
    "max_clips": 3,
    "padding_ms": 50
  }'
```

响应：

```json
[
  {"start": 250, "end": 550, "start_index": 2, "end_index": 3, "risk_count": 1},
  {"start": 650, "end": 850, "start_index": 5, "end_index": 6, "risk_count": 1},
  {"start": 950, "end": 1150, "start_index": 8, "end_index": 9, "risk_count": 1}
]
```

字段说明：

- 按**时间顺序**（即修订稿顺序）返回选中的片段；没有任何风险字符时返回 `[]`。
- `start` / `end`：片段在录音中的**半开毫秒区间**（`end` 不含），已扩展留白并
  裁剪到 `[0, total_duration]`。
- `start_index` / `end_index`：对应风险块在 `revised_text` 中的**半开字符下标**
  （`end_index` 不含）；`risk_count` 为该风险块（即该片段覆盖）的风险字符数。
- 上例“呀”的推算区间为 `[300,500]`，扩 50ms 得 `[250,550]`；“耍”的推算区间为
  `[700,800]`，扩 50ms 得 `[650,850]`；“嘛”替换“吗”，区间 `[1000,1100]`
  扩 50ms 得 `[950,1150]`。
- 若 `max_clips` 较小或留白使片段互相重叠，接口只返回按上述目标选出的子集。
  例如把 `max_clips` 改为 `2`，由于三段互不重叠且各覆盖 1 个风险字符，会放弃
  时长最长（300ms）的第一段，返回“耍”“嘛”两段（各 200ms）；若把
  `padding_ms` 增大到使两个风险块的音频区间重叠，则二者不能同时入选
  （端点恰好相接仍可同时入选）。

### 输入校验（HTTP 422）

例如 `end < start`：

```json
{"detail": "第 0 项字符 '你' 的结束时间 100 不能早于起始时间 300"}
```

覆盖：负时间、结束早于起始、超出录音总时长、未按起始时间排序、时间区间重叠、
识别字符多于/少于 1 个、字段缺失或类型错误、超出规模上限、`quote` 为空或超长、
`max_edits` 不在 0～2、`max_chars` 不在 1～80、`max_duration_ms` 不在 500～30000、
单字跨度超过 `max_duration_ms`，以及 `/retime` 校准点数量不在 1～20、
`at_ms`/`corrected_ms` 未严格递增或落在开区间 `(0, total_duration)` 之外、
`/speaker-turns` 的 `speaker_id` 为空或轮次为负时间、起止倒置、越界、
未按起始时间排序或区间重叠、`/entity-links` 的实体数量不在 2～20、
实体 ID 为空或重复、别名数量不在 1～5、别名为空或全局重复、
`bucket_ms` 不在 1000～60000、`/annotation-migrate` 的批注数量不在 1～100、
批注 ID 为空或重复、区间为空或越出旧版修订文本，以及 `/redact` 的敏感词数量
不在 1～50、敏感词为空或重复、`padding_ms` 不在 0～1000，以及 `/clip-plan`
的选择区间数量不在 1～20、区间为空/为负/越出修订稿、未按修订稿顺序排列或
互相重叠、映射后为零时长区间、`padding_ms` 不在 0～2000，以及
`/coverage-gaps` 的 `min_gap_ms` 缺失、非整数或不为正整数，以及
`/review-clips` 的 `max_clips` 不在 1～20、`padding_ms` 不在 0～2000 等。

---

## 对齐与时间分配规则（说明）

1. 以识别字符序列为源串、修订文本为目标串，求最小编辑距离。
2. 在所有最小编辑代价方案中，选择「按识别字符原位置字典序最早」的匹配：
   从前往后扫描识别字符，**只要把当前字符匹配出去仍能达到全局最优就优先匹配**；
   否则优先新增（保留该识别字符等待后续匹配），最后才删除。
   这保证重复字符结合上下文落到**更早**的识别位置。
3. 匹配/替换字符直接采用识别字符的 `start/end`。
4. 连续的新增字符视为一段，取其左右边界：
   - 左侧有锚点用左锚点的 `end`，否则用录音起点 `0`；
   - 右侧有锚点用右锚点的 `start`，否则用录音终点 `total_duration`；
   在该整数区间内做均匀切分（端点固定、地板法取整，保证有序且不越界）。
5. 被删除的识别字符不出现在结果中。

---

## 本地开发（不用 Docker）

需要 Python 3.11+ 与（可选）`gcc`：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# 可选：预编译 C 内核（不编译会自动即时编译，再不行则回退纯 Python）
gcc -O3 -fPIC -shared -o app/native/libalign.so app/native/align_c.c

uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pip install pytest httpx
pytest -q
# 用纯 Python 后备内核跑同一套测试：
ALIGN_NATIVE=0 pytest -q
```

## 目录结构

```
app/
  main.py            FastAPI 应用、路由与中文错误处理
  schemas.py         请求/响应模型
  timeline.py        输入校验、对齐调度、新增字符空档均分
  quote.py           近似引语片段枚举与全局互不重叠最优选择
  revision.py        修订差异：以未改动匹配字符为边界合并有序改动块
  retime.py          校准点校验与分段线性重映射（旧时间 → 新时间）
  captions.py        字幕切分（段数最少 → 断点总分最高 → 字典序最小）
  speaker.py         说话人轮次归属（按重叠时长最大 → 较早轮次）与相邻同归属合并
  entity_links.py    实体共提及（别名精确命中最优选择、时间桶归集与共现边权）
  annotation.py      批注迁移（旧新文本全局对齐、存活字符最小覆盖区间与状态判定）
  redact.py          敏感词脱敏（精确命中合并、等长 █ 遮蔽与静音毫秒区间生成）
  clip_plan.py       成片剪辑规划（字符区间映射、留白扩展/裁剪、源录音区间合并与成片拼接）
  coverage.py        录音覆盖缺口审计（识别覆盖互补、两侧 matched 定位与缺口内 computed 段归集）
  review_clips.py    风险听审片段（风险字符聚块、留白扩展/裁剪、带数量上限的加权区间调度）
  alignment.py       内核加载（C → 即时编译 → 纯 Python）
  align_py.py        纯 Python 后备内核
  native/align_c.c   C 对齐内核源码
entrypoint.sh        容器启动：安装依赖 + 编译内核 + 启动 uvicorn
Dockerfile
docker-compose.yml
requirements.txt
tests/test_api.py
```
