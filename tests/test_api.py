"""端到端 API 与时间轴语义测试（native 与 python 两种内核均会覆盖）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.alignment import align_chars  # noqa: E402
from app.main import app  # noqa: E402
from app.quote import find_quote_hits  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def rc(items):
    return [{"char": c, "start": a, "end": b} for c, a, b in items]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 对齐内核：重复字符结合上下文选择匹配位置，同分优先较早原位置
# ---------------------------------------------------------------------------
def test_duplicate_chars_prefer_earlier_source():
    # aaa -> aa：保留前两个（较早原位置）
    assert align_chars("aaa", "aa") == [0, 1]
    # aa -> aaa：新增字符落在末尾
    assert align_chars("aa", "aaa") == [0, 1, -1]


def test_substitution_is_anchored():
    # 替换纠错优先锚定到识别字符时间槽，而不是全部走删除+新增
    mapping = align_chars("识别错误文字", "识别正确文字")
    assert mapping[2] == 2  # “正” 替换 “错”，锚定下标 2
    assert mapping[3] == 3  # “确” 替换 “误”


def test_insertion_and_deletion():
    mapping = align_chars("你好吗世界", "你好世界")
    assert mapping == [0, 1, 3, 4]  # “吗” 被删除


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["backend"] in ("native", "python")


# ---------------------------------------------------------------------------
# 基本对齐与新增字符空档均分
# ---------------------------------------------------------------------------
def test_basic_align_with_interpolation(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    chars = body["chars"]
    assert [c["char"] for c in chars] == list("你好呀世界")
    assert [c["source"] for c in chars] == [
        "matched", "matched", "computed", "matched", "matched"]

    ni = chars[2]
    # “呀”位于“好”结束(300)与“世”开始(400)之间
    assert ni["start"] == 300 and ni["end"] == 400
    # matched 锚点时间原样保留
    assert (chars[0]["start"], chars[0]["end"]) == (0, 100)
    assert body["inserted_count"] == 1
    assert body["deleted_count"] == 0
    assert body["matched_count"] == 4


def test_multiple_insertions_evenly_split(client):
    # 锚点 a 与 c 之间没有任何识别字符，x、y 为两个纯新增字符，空档 [100,600]
    payload = {
        "recognized": rc([("a", 0, 100), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "computed" and y["source"] == "computed"
    # [100,600] 分两段：100..350, 350..600
    assert (x["start"], x["end"]) == (100, 350)
    assert (y["start"], y["end"]) == (350, 600)


def test_substitution_tie_anchors_into_gap(client):
    # a b c -> a x y c：x 与 b 替换代价相同且优先锚定，y 落在 b 与 c 的空档
    payload = {
        "recognized": rc([("a", 0, 100), ("b", 200, 300), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "matched" and x["recognized_char"] == "b"
    assert (x["start"], x["end"]) == (200, 300)
    assert y["source"] == "computed"
    assert (y["start"], y["end"]) == (300, 600)


def test_leading_and_trailing_insertions_use_recording_bounds(client):
    payload = {
        "recognized": rc([("b", 400, 500)]),
        "total_duration": 1000,
        "revised_text": "abc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    a, b, c = r.json()["chars"]
    # 前缀“a”在录音起点 0 到 b 开始 400 之间
    assert (a["start"], a["end"]) == (0, 400)
    # 后缀“c”在 b 结束 500 到录音总时长 1000 之间
    assert (c["start"], c["end"]) == (500, 1000)
    assert b["source"] == "matched"


def test_deleted_chars_not_output(client):
    payload = {
        "recognized": rc([("a", 0, 100), ("x", 200, 300), ("b", 400, 500)]),
        "total_duration": 600,
        "revised_text": "ab",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    assert [c["char"] for c in body["chars"]] == ["a", "b"]
    assert body["deleted_count"] == 1


def test_substitution_marked_matched_with_recognized_char(client):
    payload = {
        "recognized": rc([("错", 0, 100)]),
        "total_duration": 200,
        "revised_text": "对",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    ch = body["chars"][0]
    assert ch["char"] == "对"
    assert ch["source"] == "matched"
    assert ch["recognized_char"] == "错"
    assert (ch["start"], ch["end"]) == (0, 100)


def test_all_inserted_spans_whole_recording(client):
    payload = {"recognized": [], "total_duration": 300, "revised_text": "三"}
    r = client.post("/align", json=payload)
    ch = r.json()["chars"][0]
    assert ch["source"] == "computed"
    assert (ch["start"], ch["end"]) == (0, 300)


def test_empty_revised_text(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["chars"] == []
    assert body["deleted_count"] == 1


def test_timestamps_ordered_and_in_range(client):
    payload = {
        "recognized": rc([("a", 0, 10), ("b", 20, 30), ("c", 40, 50)]),
        "total_duration": 100,
        "revised_text": "xaxbxcx",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    prev_end = 0
    for c in r.json()["chars"]:
        assert 0 <= c["start"] <= c["end"] <= 100
        assert c["start"] >= prev_end
        prev_end = c["end"]


def test_zero_duration(client):
    payload = {"recognized": rc([("a", 0, 0)]), "total_duration": 0,
               "revised_text": "a"}
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    ch = r.json()["chars"][0]
    assert (ch["start"], ch["end"]) == (0, 0)


def test_duplicate_resolved_by_context_to_earlier_source(client):
    # 识别串里出现两个“对”。修订文本在“错”前补“对”、“错”后保留“对”：
    # 第一个“对”必须结合上下文匹配到更早的识别位置 0，而不是误配位置 2。
    payload = {
        "recognized": rc([
            ("对", 0, 100), ("错", 200, 300), ("对", 400, 500),
        ]),
        "total_duration": 600,
        "revised_text": "对错对",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1, 2]


def test_duplicate_identical_target_keeps_earlier_slots(client):
    # aaa（三个相同识别字） -> aa：删除多余重复字符时，保留最早的两个时间槽
    payload = {
        "recognized": rc([("a", 0, 10), ("a", 20, 30), ("a", 40, 50)]),
        "total_duration": 60,
        "revised_text": "aa",
    }
    r = client.post("/align", json=payload)
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1]
    assert (chars[0]["start"], chars[1]["start"]) == (0, 20)
    assert r.json()["deleted_count"] == 1


# ---------------------------------------------------------------------------
# 输入校验：时间无效时给出明确错误
# ---------------------------------------------------------------------------
def _post(client, payload):
    return client.post("/align", json=payload)


def test_negative_total_duration(client):
    r = _post(client, {"recognized": [], "total_duration": -1,
                       "revised_text": "a"})
    assert r.status_code == 422
    assert "非负" in r.json()["detail"]


def test_end_before_start(client):
    r = _post(client, {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_out_of_recording_range(client):
    r = _post(client, {
        "recognized": rc([("a", 100, 600)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "超出录音总时长" in r.json()["detail"]


def test_not_sorted(client):
    r = _post(client, {
        "recognized": rc([("b", 300, 400), ("a", 0, 100)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "排序" in r.json()["detail"]


def test_overlapping_intervals(client):
    r = _post(client, {
        "recognized": rc([("a", 0, 200), ("b", 100, 300)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "重叠" in r.json()["detail"]


def test_negative_timestamp(client):
    r = _post(client, {
        "recognized": rc([("a", -5, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "负数" in r.json()["detail"]


def test_missing_field(client):
    r = client.post("/align", json={"recognized": [], "total_duration": 100})
    assert r.status_code == 422
    assert "revised_text" in r.json()["detail"]


def test_wrong_type_timestamp(client):
    r = client.post("/align", json={
        "recognized": [{"char": "a", "start": "0", "end": 100}],
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_multi_char_recognized_item(client):
    r = _post(client, {
        "recognized": [{"char": "ab", "start": 0, "end": 100}],
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "1 个字符" in r.json()["detail"]


def test_float_duration_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 1.5,
                       "revised_text": "a"})
    assert r.status_code == 422


def test_extra_field_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 5,
                       "revised_text": "a", "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_zero_duration_multiple_inserts(client):
    r = _post(client, {"recognized": [], "total_duration": 0,
                       "revised_text": "甲乙"})
    assert r.status_code == 200
    chars = r.json()["chars"]
    assert [(c["start"], c["end"]) for c in chars] == [(0, 0), (0, 0)]


# ---------------------------------------------------------------------------
# 引语近似定位
# ---------------------------------------------------------------------------
def test_quote_finder_exact_repeats():
    assert find_quote_hits("abc", "xabcxabc", 1) == [
        (1, 4, 0), (5, 8, 0)]


def test_quote_finder_uses_insertion():
    assert find_quote_hits("你好世界", "你好呀世界", 1) == [(0, 5, 1)]


def test_quote_finder_overlapping_choice_prefers_more_hits():
    # 长度 2 的引语与任一单字符距离均为 1，因此四个互不重叠单字符优先于两个较长命中。
    assert find_quote_hits("ab", "aabb", 1) == [
        (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1)]


def test_quote_finder_tie_uses_lower_edit_cost():
    # 同一文本内较短片段可消耗 1 次删除命中；零编辑完整片段优先。
    assert find_quote_hits("abc", "abc", 1) == [(0, 3, 0)]


def test_locate_quote_returns_timeline_ranges(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 500, 600),
            ("界", 700, 800),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
        "quote": "你好世界",
        "max_edits": 1,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "start_index": 0,
        "end_index": 5,
        "text": "你好呀世界",
        "edits": 1,
        "start": 0,
        "end": 800,
    }]


def test_locate_quote_empty_array_when_no_hit(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "a",
        "quote": "zzz",
        "max_edits": 2,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200
    assert r.json() == []


def test_locate_quote_reuses_alignment_validation(client):
    payload = {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500,
        "revised_text": "a",
        "quote": "a",
        "max_edits": 0,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_locate_quote_empty_quote_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "", "max_edits": 0})
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_locate_quote_max_edits_range(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 3})
    assert r.status_code == 422
    assert "0～2" in r.json()["detail"]


def test_locate_quote_extra_field_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_locate_quote_too_long_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_QUOTE_LENGTH", 2)
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "abc",
        "quote": "abc", "max_edits": 0})
    assert r.status_code == 422
    assert "引语长度" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 字幕切分
# ---------------------------------------------------------------------------
def _captions(client, items, revised, max_chars=80, max_duration_ms=30000,
              total=None):
    payload = {
        "recognized": rc(items),
        "total_duration": total if total is not None else 40000,
        "revised_text": revised,
        "max_chars": max_chars,
        "max_duration_ms": max_duration_ms,
    }
    return client.post("/captions", json=payload)


def test_captions_empty_text_returns_empty_array(client):
    r = _captions(client, [], "", total=0)
    assert r.status_code == 200
    assert r.json() == []


def test_captions_prefer_break_after_sentence_punctuation(client):
    # 两种两段切分：[你好][。世界] 与 [你好。][世界]；断点左字为“。”加 2000 分
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("。", 400, 500),
        ("世", 600, 700), ("界", 800, 900),
    ], "你好。世界", max_chars=3, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "你好。",
         "start": 0, "end": 500},
        {"start_index": 3, "end_index": 5, "text": "世界",
         "start": 600, "end": 900},
    ]


def test_captions_punctuation_bonus_applies_to_all_listed_marks(client):
    for mark in "。！？；":
        r = _captions(client, [
            ("甲", 0, 100), (mark, 200, 300), ("乙", 400, 500),
        ], f"甲{mark}乙", max_chars=2, total=600)
        assert r.status_code == 200, r.text
        segs = r.json()
        assert [s["text"] for s in segs] == [f"甲{mark}", "乙"]


def test_captions_maximize_pause_score_without_punctuation(client):
    # 候选断点停顿分别为 100 / 300 / 100 毫秒，选停顿最大者
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 600, 700), ("d", 800, 900),
    ], "abcd", max_chars=3, total=1000)
    assert [s["text"] for s in r.json()] == ["ab", "cd"]


def test_captions_pause_clamped_then_lexicographic(client):
    # 断点 1 与断点 3 的停顿分别为 1900、3200 毫秒，均截到 1000 而同分，
    # 取断点下标序列字典序最小者
    r = _captions(client, [
        ("a", 0, 100), ("b", 2000, 2100), ("c", 2200, 2300),
        ("d", 5500, 5600),
    ], "abcd", max_chars=3, total=6000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bcd"]
    assert (segs[0]["start_index"], segs[0]["end_index"]) == (0, 1)
    assert (segs[1]["start_index"], segs[1]["end_index"]) == (1, 4)


def test_captions_fewest_segments_beats_breakpoint_score(client):
    # 一段总分 0 也优于两段（断点在“。”后可得 2000+ 分）
    r = _captions(client, [
        ("a", 0, 100), ("。", 200, 300), ("b", 400, 500),
    ], "a。b", max_chars=3, total=600)
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "a。b",
         "start": 0, "end": 500},
    ]


def test_captions_respect_max_chars(client):
    # 停顿全部相同，同分时取字典序最小断点序列：前段尽量短
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
        ("d", 600, 700), ("e", 800, 900),
    ], "abcde", max_chars=2, total=1000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bc", "de"]
    assert all(s["end_index"] - s["start_index"] <= 2 for s in segs)


def test_captions_respect_max_duration(client):
    # 每段跨度不得超过 1000 毫秒：abc 相连 1400 毫秒超限，只能 ab / cd
    r = _captions(client, [
        ("a", 0, 400), ("b", 500, 900), ("c", 1000, 1400),
        ("d", 1500, 1900),
    ], "abcd", max_duration_ms=1000, total=2000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["ab", "cd"]
    assert (segs[0]["start"], segs[0]["end"]) == (0, 900)
    assert (segs[1]["start"], segs[1]["end"]) == (1000, 1900)


def test_captions_single_char_span_exceeds_limit(client):
    r = _captions(client, [("啊", 0, 600)], "啊",
                  max_duration_ms=500, total=1000)
    assert r.status_code == 422
    assert "超过单段时长上限" in r.json()["detail"]


def test_captions_uses_computed_timeline_for_inserted_chars(client):
    # 新增字符“呀”在空档 [300,400] 内推算，切分以其推算时间为准
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500),
    ], "你好呀世", max_chars=2, total=600)
    segs = r.json()
    assert [s["text"] for s in segs] == ["你好", "呀世"]
    assert (segs[1]["start"], segs[1]["end"]) == (300, 500)


def test_captions_reuses_alignment_validation(client):
    r = _captions(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_captions_max_chars_range(client):
    for bad in (0, 81):
        r = _captions(client, [], "", max_chars=bad, total=0)
        assert r.status_code == 422
        assert "1～80" in r.json()["detail"]


def test_captions_max_duration_range(client):
    for bad in (499, 30001):
        r = _captions(client, [], "", max_duration_ms=bad, total=0)
        assert r.status_code == 422
        assert "500～30000" in r.json()["detail"]


def test_captions_extra_field_rejected(client):
    r = client.post("/captions", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "max_chars": 10, "max_duration_ms": 1000, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 修订差异
# ---------------------------------------------------------------------------
def _revision_diff(client, items, revised, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
    }
    return client.post("/revision-diff", json=payload)


def test_revision_diff_no_changes_returns_empty_array(client):
    r = _revision_diff(client, [("你", 0, 100), ("好", 200, 300)], "你好",
                       total=400)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_revision_diff_insert_block_uses_computed_timeline(client):
    # “呀”为新增字符，在“好”结束(300)与“世”开始(400)之间推算
    r = _revision_diff(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
    ], "你好呀世界")
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "insert",
        "recognized_start": 2,
        "recognized_end": 2,
        "revised_start": 2,
        "revised_end": 3,
        "recognized_text": "",
        "revised_text": "呀",
        "start": 300,
        "end": 400,
    }]


def test_revision_diff_delete_block(client):
    r = _revision_diff(client, [
        ("你", 0, 100), ("吗", 200, 300), ("好", 400, 500),
    ], "你好", total=600)
    assert r.json() == [{
        "type": "delete",
        "recognized_start": 1,
        "recognized_end": 2,
        "revised_start": 1,
        "revised_end": 1,
        "recognized_text": "吗",
        "revised_text": "",
        "start": 200,
        "end": 300,
    }]


def test_revision_diff_substitution_is_replace(client):
    r = _revision_diff(client, [("错", 0, 100)], "对", total=200)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 0,
        "recognized_end": 1,
        "revised_start": 0,
        "revised_end": 1,
        "recognized_text": "错",
        "revised_text": "对",
        "start": 0,
        "end": 100,
    }]


def test_revision_diff_mixed_delete_and_insert_is_replace(client):
    # “bc”被删、“x”新增，落在同一空档（无未改动匹配字符分隔）→ 合并为 replace
    r = _revision_diff(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
    ], "ax", total=600)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 1,
        "recognized_end": 3,
        "revised_start": 1,
        "revised_end": 2,
        "recognized_text": "bc",
        "revised_text": "x",
        "start": 200,
        "end": 500,
    }]


def test_revision_diff_ordered_blocks_of_all_types(client):
    # 依次：删除“吗”、插入“呀”（推算 [500,600]）、替换“玩→耍”
    r = _revision_diff(client, [
        ("吗", 0, 100), ("你", 200, 300), ("好", 400, 500),
        ("呢", 600, 700), ("玩", 800, 900),
    ], "你好呀呢耍")
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"type": "delete", "recognized_start": 0, "recognized_end": 1,
         "revised_start": 0, "revised_end": 0,
         "recognized_text": "吗", "revised_text": "", "start": 0, "end": 100},
        {"type": "insert", "recognized_start": 3, "recognized_end": 3,
         "revised_start": 2, "revised_end": 3,
         "recognized_text": "", "revised_text": "呀",
         "start": 500, "end": 600},
        {"type": "replace", "recognized_start": 4, "recognized_end": 5,
         "revised_start": 4, "revised_end": 5,
         "recognized_text": "玩", "revised_text": "耍",
         "start": 800, "end": 900},
    ]


def test_revision_diff_block_time_spans_both_sides(client):
    # 替换“x→y”占 [200,300]，新增“z”推算到 [300,900]，
    # 块时间取两侧最早起点 200、最晚终点 900
    r = _revision_diff(client, [
        ("a", 0, 100), ("x", 200, 300), ("b", 900, 1000),
    ], "ayzb", total=1000)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 1,
        "recognized_end": 2,
        "revised_start": 1,
        "revised_end": 3,
        "recognized_text": "x",
        "revised_text": "yz",
        "start": 200,
        "end": 900,
    }]


def test_revision_diff_empty_revised_text_is_single_delete(client):
    r = _revision_diff(client, [("a", 0, 100), ("b", 200, 300)], "", total=400)
    assert r.json() == [{
        "type": "delete",
        "recognized_start": 0,
        "recognized_end": 2,
        "revised_start": 0,
        "revised_end": 0,
        "recognized_text": "ab",
        "revised_text": "",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_empty_recognized_is_single_insert(client):
    r = _revision_diff(client, [], "甲乙", total=300)
    assert r.json() == [{
        "type": "insert",
        "recognized_start": 0,
        "recognized_end": 0,
        "revised_start": 0,
        "revised_end": 2,
        "recognized_text": "",
        "revised_text": "甲乙",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_reuses_alignment_validation(client):
    r = _revision_diff(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_revision_diff_extra_field_rejected(client):
    r = client.post("/revision-diff", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 校准重映射 POST /retime
# ---------------------------------------------------------------------------
def _retime(client, items, revised, calib, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "calibrations": calib,
    }
    return client.post("/retime", json=payload)


def _pt(a, b):
    return {"at_ms": a, "corrected_ms": b}


def test_retime_piecewise_remap_with_inserted_char(client):
    # 旧轴：你[0,100] 好[200,300]，呀 推算 [300,500]，世[500,600] 界[700,800]
    # 校准点 500 -> 750：前段 [0,500] -> [0,750]（×1.5），
    # 后段 [500,1000] -> [750,1000]（×0.5）。
    r = _retime(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 500, 600), ("界", 700, 800),
    ], "你好呀世界", [_pt(500, 750)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_duration"] == 1000
    assert body["inserted_count"] == 1
    assert body["deleted_count"] == 0
    assert body["matched_count"] == 4
    assert body["backend"] in ("native", "python")
    assert [(c["char"], c["source"], c["matched_index"]) for c in body["chars"]] == [
        ("你", "matched", 0), ("好", "matched", 1), ("呀", "computed", None),
        ("世", "matched", 2), ("界", "matched", 3)]
    assert [(c["start"], c["end"]) for c in body["chars"]] == [
        (0, 150), (300, 450), (450, 750), (750, 800), (850, 900)]


def test_retime_floor_interpolation(client):
    # 段 [0,100] -> [0,30]：t=50 -> 15，t=100 精确命中 30
    r = _retime(client, [("a", 0, 50), ("b", 50, 100)], "ab", [_pt(100, 30)])
    assert r.status_code == 200, r.text
    assert [(c["start"], c["end"]) for c in r.json()["chars"]] == [
        (0, 15), (15, 30)]


def test_retime_calibration_points_hit_exactly_across_segments(client):
    # 多个校准点；识别字符正好落在校准位置上时必须精确命中，不得有取整误差。
    # 结 0,200,400,700,1000 -> 新 0,500,700,800,1000（两列均严格递增）。
    r = _retime(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500), ("d", 700, 900),
    ], "abcd", [_pt(200, 500), _pt(400, 700), _pt(700, 800)])
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    # a.end=100 在段 [0,200]->[0,500]：500*100//200 = 250
    assert (chars[0]["start"], chars[0]["end"]) == (0, 250)
    # b.start 旧 200 命中校准点 -> 500；b.end=300 在 [200,400]->[500,700]：600
    assert (chars[1]["start"], chars[1]["end"]) == (500, 600)
    # c.start 旧 400 命中校准点 -> 700；c.end=500 在 [400,700]->[700,800]：733
    assert (chars[2]["start"], chars[2]["end"]) == (700, 733)
    # d.start 旧 700 命中校准点 -> 800；d.end=900 在 [700,1000]->[800,1000]：933
    assert (chars[3]["start"], chars[3]["end"]) == (800, 933)


def test_retime_endpoints_fixed_zero_and_total(client):
    r = _retime(client, [
        ("a", 0, 100), ("b", 200, 400),
    ], "ab", [_pt(200, 300)], total=500)
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    # 旧 0 固定映射 0
    assert chars[0]["start"] == 0
    # b.end=400 位于末段 [200,500] -> [300,500]：300 + 200*200//300 = 433
    assert chars[1]["end"] == 433
    # 追加一个旧时间恰为 total_duration 的字，新时间必须仍是 total
    r2 = _retime(client, [
        ("a", 0, 100), ("b", 200, 500),
    ], "ab", [_pt(200, 300)], total=500)
    assert r2.status_code == 200, r2.text
    assert r2.json()["chars"][1]["end"] == 500


def test_retime_no_regression_and_in_range(client):
    # 结 0,25,45,100 -> 新 0,20,70,100：前段压缩、中段拉伸、末段压缩
    r = _retime(client, [
        ("a", 0, 10), ("b", 20, 30), ("c", 40, 50),
    ], "xaxbxcx", [_pt(25, 20), _pt(45, 70)], total=100)
    assert r.status_code == 200, r.text
    prev_end = 0
    for c in r.json()["chars"]:
        assert 0 <= c["start"] <= c["end"] <= 100
        assert c["start"] >= prev_end
        prev_end = c["end"]


def test_retime_empty_revised_text(client):
    r = _retime(client, [("a", 0, 100)], "", [_pt(50, 80)], total=200)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chars"] == []
    assert body["deleted_count"] == 1


def test_retime_preserves_recognized_char_on_substitution(client):
    r = _retime(client, [("错", 0, 100)], "对", [_pt(50, 80)], total=200)
    assert r.status_code == 200, r.text
    ch = r.json()["chars"][0]
    assert ch["char"] == "对" and ch["recognized_char"] == "错"
    assert ch["source"] == "matched" and ch["matched_index"] == 0
    # end=100 在段 [50,200]->[80,200]：80 + 120*50//150 = 120
    assert (ch["start"], ch["end"]) == (0, 120)


def test_retime_missing_calibrations_rejected(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a"})
    assert r.status_code == 422
    assert "calibrations" in r.json()["detail"]


def test_retime_empty_calibrations_rejected(client):
    r = _retime(client, [], "a", [])
    assert r.status_code == 422
    assert "1～20" in r.json()["detail"]


def test_retime_too_many_calibrations_rejected(client):
    calib = [_pt(i, i) for i in range(1, 22)]
    r = _retime(client, [], "a", calib, total=1000)
    assert r.status_code == 422
    assert "1～20" in r.json()["detail"]


def test_retime_twenty_calibrations_accepted(client):
    # 恰好 20 个：at_ms=5,10,...,100；corrected_ms=10,20,...,200，均严格递增
    calib = [_pt(5 * i, 10 * i) for i in range(1, 21)]
    r = _retime(client, [], "a", calib, total=1000)
    assert r.status_code == 200, r.text
    ch = r.json()["chars"][0]
    # 全新增字符“a”：旧轴 [0,1000]，起点 0 -> 0；终点 1000 -> 1000
    assert (ch["start"], ch["end"]) == (0, 1000)


def test_retime_at_ms_not_strictly_increasing(client):
    r = _retime(client, [], "a", [_pt(400, 100), _pt(400, 900)])
    assert r.status_code == 422
    assert "at_ms" in r.json()["detail"]
    assert "严格递增" in r.json()["detail"]


def test_retime_corrected_ms_not_strictly_increasing(client):
    r = _retime(client, [], "a", [_pt(100, 500), _pt(400, 500)])
    assert r.status_code == 422
    assert "corrected_ms" in r.json()["detail"]
    assert "严格递增" in r.json()["detail"]


def test_retime_at_ms_out_of_open_interval(client):
    for bad_at in (0, 1000, -1, 1001):
        r = _retime(client, [], "a", [_pt(bad_at, 500)])
        assert r.status_code == 422, (bad_at, r.text)
        assert "(0, 1000)" in r.json()["detail"]


def test_retime_corrected_ms_out_of_open_interval(client):
    for bad_new in (0, 1000):
        r = _retime(client, [], "a", [_pt(500, bad_new)])
        assert r.status_code == 422
        assert "(0, 1000)" in r.json()["detail"]


def test_retime_zero_duration_has_no_interior_points(client):
    # total_duration=0 时开区间 (0,0) 为空，任何校准点都非法
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 0, "revised_text": "甲",
        "calibrations": [_pt(0, 0)]})
    assert r.status_code == 422
    assert "(0, 0)" in r.json()["detail"]


def test_retime_reuses_alignment_validation(client):
    r = _retime(client, [("a", 300, 100)], "a", [_pt(200, 400)], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_retime_extra_field_rejected(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "calibrations": [_pt(50, 60)], "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_retime_calibration_point_wrong_type(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "calibrations": [{"at_ms": "50", "corrected_ms": 60}]})
    assert r.status_code == 422
    assert "整数毫秒" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 说话人轮次归属 POST /speaker-turns
# ---------------------------------------------------------------------------
def _speaker_turns(client, items, revised, turns, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "speaker_turns": turns,
    }
    return client.post("/speaker-turns", json=payload)


def _turn(speaker, start, end):
    return {"speaker_id": speaker, "start": start, "end": end}


def test_speaker_turns_assign_by_max_overlap_and_merge(client):
    # 你[0,100] 好[200,300] 呀(computed)[300,400] 世[500,600] 界[700,800]
    # 轮次 A[0,250)、B[250,1000)：好与 A 重叠 50、与 B 重叠 50 → 并列取较早 A
    r = _speaker_turns(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 500, 600), ("界", 700, 800),
    ], "你好呀世界", [_turn("A", 0, 250), _turn("B", 250, 1000)])
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 2,
         "text": "你好", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 2, "end_index": 5,
         "text": "呀世界", "start": 300, "end": 800},
    ]


def test_speaker_turns_boundary_goes_to_right_turn(client):
    # 字符正好覆盖到边界 300：a[0,300) 与左右两轮次各重叠 300，并列取较早；
    # 而零时长字符落在边界 300 上时归右侧轮次。
    r = _speaker_turns(client, [
        ("a", 0, 300), ("b", 300, 300), ("c", 300, 600),
    ], "abc", [_turn("A", 0, 300), _turn("B", 300, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 1, "end_index": 3,
         "text": "bc", "start": 300, "end": 600},
    ]


def test_speaker_turns_no_overlap_is_null_block(client):
    # 轮次只覆盖 [0,200)；好 [200,300) 与之端点相接无重叠，后续字符也无重叠
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
    ], "abc", [_turn("A", 0, 200)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 100},
        {"speaker_id": None, "start_index": 1, "end_index": 3,
         "text": "bc", "start": 200, "end": 500},
    ]


def test_speaker_turns_zero_duration_char_inside_turn(client):
    # 零时长字符“。”[300,300] 位于轮次内部
    r = _speaker_turns(client, [
        ("你", 0, 100), ("好", 200, 300), ("。", 300, 300), ("呀", 400, 500),
    ], "你好。呀", [_turn("A", 0, 400), _turn("B", 400, 1000)])
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 3,
         "text": "你好。", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 3, "end_index": 4,
         "text": "呀", "start": 400, "end": 500},
    ]


def test_speaker_turns_zero_duration_char_in_gap_is_null(client):
    # 零时长字符在两轮次的间隙 (400,500) 内 → null，与相邻同归属字符合并
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 450, 450), ("c", 600, 700),
    ], "abc", [_turn("A", 0, 400), _turn("B", 500, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 100},
        {"speaker_id": None, "start_index": 1, "end_index": 2,
         "text": "b", "start": 450, "end": 450},
        {"speaker_id": "B", "start_index": 2, "end_index": 3,
         "text": "c", "start": 600, "end": 700},
    ]


def test_speaker_turns_choose_larger_overlap_not_earlier(client):
    # 字符 [100,500) 与 A 重叠 100、与 B 重叠 300 → 归 B（非并列不取较早）
    r = _speaker_turns(client, [("x", 100, 500)], "x",
                       [_turn("A", 0, 200), _turn("B", 200, 1000)],
                       total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": "B", "start_index": 0, "end_index": 1,
        "text": "x", "start": 100, "end": 500}]


def test_speaker_turns_same_speaker_adjacent_turns_merge(client):
    # 同一 speaker_id 的两个相接轮次，相邻归属字符合并为一块
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300),
    ], "ab",
        [_turn("A", 0, 200), _turn("A", 200, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": "A", "start_index": 0, "end_index": 2,
        "text": "ab", "start": 0, "end": 300}]


def test_speaker_turns_empty_turns_all_null(client):
    r = _speaker_turns(client, [("a", 0, 100)], "a", [], total=200)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": None, "start_index": 0, "end_index": 1,
        "text": "a", "start": 0, "end": 100}]


def test_speaker_turns_empty_revised_text(client):
    r = _speaker_turns(client, [("a", 0, 100)], "", [_turn("A", 0, 200)],
                       total=200)
    assert r.status_code == 200
    assert r.json() == []


def test_speaker_turns_zero_duration_turns_touching_allowed(client):
    # 零时长轮次 [200,200) 与前后轮次端点相接，互不重叠 → 合法
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300),
    ], "ab",
        [_turn("A", 0, 200), _turn("C", 200, 200), _turn("B", 200, 1000)],
        total=1000)
    assert r.status_code == 200, r.text
    assert [blk["speaker_id"] for blk in r.json()] == ["A", "B"]


def test_speaker_turns_reuses_alignment_validation(client):
    r = _speaker_turns(client, [("a", 300, 100)], "a",
                       [_turn("A", 0, 500)], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_speaker_turns_negative_timestamp_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", -1, 100)])
    assert r.status_code == 422
    assert "说话人轮次" in r.json()["detail"]
    assert "负数" in r.json()["detail"]


def test_speaker_turns_end_before_start_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", 300, 100)])
    assert r.status_code == 422
    assert "说话人轮次" in r.json()["detail"]
    assert "不能早于起始时间" in r.json()["detail"]


def test_speaker_turns_out_of_recording_range_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", 0, 1001)],
                       total=1000)
    assert r.status_code == 422
    assert "超出录音总时长" in r.json()["detail"]


def test_speaker_turns_not_sorted_rejected(client):
    r = _speaker_turns(client, [], "a",
                       [_turn("B", 500, 600), _turn("A", 0, 100)])
    assert r.status_code == 422
    assert "按起始时间排序" in r.json()["detail"]


def test_speaker_turns_overlap_rejected(client):
    r = _speaker_turns(client, [], "a",
                       [_turn("A", 0, 300), _turn("B", 200, 500)])
    assert r.status_code == 422
    assert "不得重叠" in r.json()["detail"]


def test_speaker_turns_touching_intervals_allowed(client):
    # [0,200) 与 [200,500) 端点相接（左闭右开）不算重叠
    r = _speaker_turns(client, [("a", 0, 0)], "a",
                       [_turn("A", 0, 200), _turn("B", 200, 500)], total=500)
    assert r.status_code == 200, r.text
    # 零时长字符时点 0 位于 A 轮次起点，归 A（边界归右侧规则下 A 即右侧）
    assert r.json() == [{
        "speaker_id": "A", "start_index": 0, "end_index": 1,
        "text": "a", "start": 0, "end": 0}]


def test_speaker_turns_empty_speaker_id_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "", "start": 0, "end": 50}]})
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_speaker_turns_missing_field_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a"})
    assert r.status_code == 422
    assert "speaker_turns" in r.json()["detail"]


def test_speaker_turns_extra_field_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "A", "start": 0, "end": 50,
                           "bogus": 1}]})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_speaker_turns_wrong_type_timestamp(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "A", "start": "0", "end": 50}]})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 实体共提及 POST /entity-links
# ---------------------------------------------------------------------------
def _entity_links(client, items, revised, entities, bucket_ms=1000, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "entities": entities,
        "bucket_ms": bucket_ms,
    }
    return client.post("/entity-links", json=payload)


def _ent(eid, *aliases):
    return {"entity_id": eid, "aliases": list(aliases)}


def test_entity_links_basic_hits_and_edge(client):
    # 张三 [0,2) 首字 start=0、李四 [3,5) 首字 start=300，均归入桶 0
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200), ("和", 200, 300),
        ("李", 300, 400), ("四", 400, 500),
    ], "张三和李四", [_ent("张三", "张三"), _ent("李四", "李四")], total=600)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "hits": [
            {"entity_id": "张三", "start_index": 0, "end_index": 2,
             "start": 0, "end": 200},
            {"entity_id": "李四", "start_index": 3, "end_index": 5,
             "start": 300, "end": 500},
        ],
        "edges": [
            {"source": "张三", "target": "李四", "weight": 1, "buckets": [0]},
        ],
    }


def test_entity_links_bucket_by_first_char_start(client):
    # 李四首字 start=1500 → 桶 1，与桶 0 的张三不共现 → 空边列表
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200),
        ("李", 1500, 1600), ("四", 1600, 1700),
    ], "张三李四", [_ent("张三", "张三"), _ent("李四", "李四")], total=2000)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [h["entity_id"] for h in body["hits"]] == ["张三", "李四"]
    assert body["edges"] == []


def test_entity_links_same_bucket_entity_counted_once(client):
    # 张三在桶 0 内命中两次，只计一次 → 边权为 1 而非 2
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200), ("张", 300, 400), ("三", 400, 500),
        ("李", 600, 700), ("四", 700, 800),
    ], "张三张三李四", [_ent("张三", "张三"), _ent("李四", "李四")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(h["start_index"], h["end_index"]) for h in body["hits"]] == [
        (0, 2), (2, 4), (4, 6)]
    assert body["edges"] == [
        {"source": "张三", "target": "李四", "weight": 1, "buckets": [0]}]


def test_entity_links_weight_counts_shared_buckets(client):
    # 两实体在桶 0 与桶 2 各共现一次 → 边权 2，桶下标升序
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200), ("李", 300, 400), ("四", 400, 500),
        ("张", 2000, 2100), ("三", 2100, 2200),
        ("李", 2300, 2400), ("四", 2400, 2500),
    ], "张三李四张三李四", [_ent("张三", "张三"), _ent("李四", "李四")],
        total=3000)
    assert r.status_code == 200, r.text
    assert r.json()["edges"] == [
        {"source": "张三", "target": "李四", "weight": 2, "buckets": [0, 2]}]


def test_entity_links_edges_sorted_by_entity_id(client):
    # 实体按 C、A、B 顺序给出，边仍按实体 ID 排序输出
    r = _entity_links(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300),
    ], "abc", [_ent("C", "c"), _ent("A", "a"), _ent("B", "b")], total=400)
    assert r.status_code == 200, r.text
    assert r.json()["edges"] == [
        {"source": "A", "target": "B", "weight": 1, "buckets": [0]},
        {"source": "A", "target": "C", "weight": 1, "buckets": [0]},
        {"source": "B", "target": "C", "weight": 1, "buckets": [0]},
    ]


def test_entity_links_prefers_max_coverage_over_fewer_hits(client):
    # {ab, c} 覆盖 3 字 2 次命中，优于任一单命中（覆盖 2 字）
    r = _entity_links(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300),
    ], "abc", [_ent("A", "ab"), _ent("B", "bc", "c")], total=400)
    assert r.status_code == 200, r.text
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("A", 0, 2), ("B", 2, 3)]


def test_entity_links_equal_coverage_prefers_fewer_hits(client):
    # 单命中 abc 与双命中 ab+c 覆盖同为 3 字，取命中数更少者
    r = _entity_links(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300),
    ], "abc", [_ent("A", "abc"), _ent("B", "ab", "c")], total=400)
    assert r.status_code == 200, r.text
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("A", 0, 3)]


def test_entity_links_tie_prefers_earlier_start(client):
    # ab 与 bc 重叠且覆盖、命中数相同，取起点更小者
    r = _entity_links(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300),
    ], "abc", [_ent("A", "ab"), _ent("B", "bc")], total=400)
    assert r.status_code == 200, r.text
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("A", 0, 2)]


def test_entity_links_tie_prefers_lexicographically_smaller_sequence(client):
    # (0,3,X)+(3,4,W) 与 (0,2,Y)+(2,4,Z) 覆盖与命中数均相同，
    # 按（起点、终点、实体 ID）序列字典序取后者（同起点时终点更小）
    r = _entity_links(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300), ("d", 300, 400),
    ], "abcd", [
        _ent("X", "abc"), _ent("Y", "ab"), _ent("Z", "cd"), _ent("W", "d"),
    ], total=500)
    assert r.status_code == 200, r.text
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("Y", 0, 2), ("Z", 2, 4)]
    assert r.json()["edges"] == [
        {"source": "Y", "target": "Z", "weight": 1, "buckets": [0]}]


def test_entity_links_alias_can_match_overlapping_occurrences(client):
    # 别名“aa”在“aaa”中有两个重叠候选，最终选互不重叠的一个
    r = _entity_links(client, [
        ("a", 0, 100), ("a", 100, 200), ("a", 200, 300),
    ], "aaa", [_ent("A", "aa"), _ent("B", "b")], total=400)
    assert r.status_code == 200, r.text
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("A", 0, 2)]


def test_entity_links_uses_computed_timeline_for_inserted_chars(client):
    # 新增字符“和”在空档 [200,900] 内推算，命中时间以其推算时间为准
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200),
        ("李", 900, 1000), ("四", 1000, 1100),
    ], "张三和李四", [_ent("张三", "张三和"), _ent("李四", "李四")],
        total=1100)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(h["start"], h["end"]) for h in body["hits"]] == [
        (0, 900), (900, 1100)]
    # 张三首字 start=0 → 桶 0；李四首字 start=900 → 桶 0（900//1000）
    assert body["edges"] == [
        {"source": "张三", "target": "李四", "weight": 1, "buckets": [0]}]


def test_entity_links_multiple_aliases_per_entity(client):
    # 同一实体的多个别名均可命中，归桶时按实体去重
    r = _entity_links(client, [
        ("张", 0, 100), ("三", 100, 200), ("李", 300, 400), ("四", 400, 500),
    ], "张三李四", [_ent("E1", "张三", "张"), _ent("E2", "李四")], total=600)
    assert r.status_code == 200, r.text
    # “张三”(0,2) 与“张”(0,1) 起点相同，覆盖更大者胜
    assert [(h["entity_id"], h["start_index"], h["end_index"])
            for h in r.json()["hits"]] == [("E1", 0, 2), ("E2", 2, 4)]


def test_entity_links_empty_revised_text(client):
    r = _entity_links(client, [], "", [_ent("A", "x"), _ent("B", "y")],
                      total=0)
    assert r.status_code == 200
    assert r.json() == {"hits": [], "edges": []}


def test_entity_links_no_alias_match_returns_empty(client):
    r = _entity_links(client, [("你", 0, 100)], "你",
                      [_ent("A", "甲"), _ent("B", "乙")], total=200)
    assert r.status_code == 200
    assert r.json() == {"hits": [], "edges": []}


def test_entity_links_bucket_boundaries(client):
    # 首字 start 恰为 bucket_ms 整数倍时归入该倍数的桶
    r = _entity_links(client, [
        ("a", 1000, 1100), ("b", 2000, 2100),
    ], "ab", [_ent("A", "a"), _ent("B", "b")], total=3000)
    assert r.status_code == 200, r.text
    assert r.json()["edges"] == []  # 桶 1 与桶 2，无共现


def test_entity_links_reuses_alignment_validation(client):
    r = _entity_links(client, [("a", 300, 100)], "a",
                      [_ent("A", "a"), _ent("B", "b")], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_entity_links_entity_count_range(client):
    one = _entity_links(client, [], "", [_ent("A", "x")], total=0)
    assert one.status_code == 422
    assert "2～20" in one.json()["detail"]
    many = _entity_links(client, [], "",
                         [_ent(f"E{i}", f"a{i}") for i in range(21)], total=0)
    assert many.status_code == 422
    assert "2～20" in many.json()["detail"]
    ok = _entity_links(client, [], "",
                       [_ent(f"E{i}", f"a{i}") for i in range(20)], total=0)
    assert ok.status_code == 200, ok.text


def test_entity_links_alias_count_range(client):
    zero = _entity_links(client, [], "",
                         [{"entity_id": "A", "aliases": []}, _ent("B", "y")],
                         total=0)
    assert zero.status_code == 422
    assert "1～5" in zero.json()["detail"]
    six = _entity_links(client, [], "",
                        [_ent("A", "a", "b", "c", "d", "e", "f"),
                         _ent("B", "y")], total=0)
    assert six.status_code == 422
    assert "1～5" in six.json()["detail"]


def test_entity_links_empty_alias_rejected(client):
    r = _entity_links(client, [], "",
                      [_ent("A", ""), _ent("B", "y")], total=0)
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_entity_links_empty_entity_id_rejected(client):
    r = _entity_links(client, [], "",
                      [_ent("", "x"), _ent("B", "y")], total=0)
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_entity_links_duplicate_entity_id_rejected(client):
    r = _entity_links(client, [], "",
                      [_ent("A", "x"), _ent("A", "y")], total=0)
    assert r.status_code == 422
    assert "唯一" in r.json()["detail"]


def test_entity_links_duplicate_alias_across_entities_rejected(client):
    r = _entity_links(client, [], "",
                      [_ent("A", "x"), _ent("B", "x")], total=0)
    assert r.status_code == 422
    assert "不重复" in r.json()["detail"]


def test_entity_links_duplicate_alias_within_entity_rejected(client):
    r = _entity_links(client, [], "",
                      [_ent("A", "x", "x"), _ent("B", "y")], total=0)
    assert r.status_code == 422
    assert "不重复" in r.json()["detail"]


def test_entity_links_bucket_ms_range(client):
    for bad in (999, 60001):
        r = _entity_links(client, [], "", [_ent("A", "x"), _ent("B", "y")],
                          bucket_ms=bad, total=0)
        assert r.status_code == 422, (bad, r.text)
        assert "1000～60000" in r.json()["detail"]
    for good in (1000, 60000):
        r = _entity_links(client, [], "", [_ent("A", "x"), _ent("B", "y")],
                          bucket_ms=good, total=0)
        assert r.status_code == 200, (good, r.text)


def test_entity_links_bucket_ms_wrong_type(client):
    r = client.post("/entity-links", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "entities": [_ent("A", "x"), _ent("B", "y")], "bucket_ms": "1000"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_entity_links_missing_entities_rejected(client):
    r = client.post("/entity-links", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "bucket_ms": 1000})
    assert r.status_code == 422
    assert "entities" in r.json()["detail"]


def test_entity_links_extra_field_rejected(client):
    r = client.post("/entity-links", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "entities": [_ent("A", "x"), _ent("B", "y")],
        "bucket_ms": 1000, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_entity_links_entity_extra_field_rejected(client):
    r = client.post("/entity-links", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "entities": [{"entity_id": "A", "aliases": ["x"], "bogus": 1},
                     _ent("B", "y")],
        "bucket_ms": 1000})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 批注迁移 POST /annotation-migrate
# ---------------------------------------------------------------------------
def _ann(aid, s, e):
    return {"annotation_id": aid, "start_index": s, "end_index": e}


def _annotate(client, items, revised, old_text, annotations, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "old_text": old_text,
        "annotations": annotations,
    }
    return client.post("/annotation-migrate", json=payload)


def test_annotation_migrate_complete_with_inner_insertion(client):
    # 旧稿“好世”在新稿中变为“好呀世”：内部新增字符“呀”一并纳入新区间
    r = _annotate(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
    ], "你好呀世界", "你好世界", [_ann("a1", 1, 3), _ann("a2", 3, 4)])
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"annotation_id": "a1", "status": "complete",
         "old_start_index": 1, "old_end_index": 3,
         "new_start_index": 1, "new_end_index": 4,
         "start": 200, "end": 500},
        {"annotation_id": "a2", "status": "complete",
         "old_start_index": 3, "old_end_index": 4,
         "new_start_index": 4, "new_end_index": 5,
         "start": 600, "end": 700},
    ]


def test_annotation_migrate_partial_when_some_chars_deleted(client):
    # 新稿删去“世”：批注“好世”只有“好”存活 → partial
    r = _annotate(client, [
        ("你", 0, 100), ("好", 200, 300), ("界", 600, 700),
    ], "你好界", "你好世界", [_ann("a1", 1, 3)])
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "annotation_id": "a1", "status": "partial",
        "old_start_index": 1, "old_end_index": 3,
        "new_start_index": 1, "new_end_index": 2,
        "start": 200, "end": 300,
    }]


def test_annotation_migrate_orphan_when_no_char_survives(client):
    # 新稿整体删去“世界”：批注无任何存活字符 → orphan，新区间与时间为 null
    r = _annotate(client, [
        ("你", 0, 100), ("好", 200, 300),
    ], "你好", "你好世界", [_ann("a1", 2, 4)], total=400)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "annotation_id": "a1", "status": "orphan",
        "old_start_index": 2, "old_end_index": 4,
        "new_start_index": None, "new_end_index": None,
        "start": None, "end": None,
    }]


def test_annotation_migrate_substituted_char_does_not_survive(client):
    # 第一个“错”被纠错为“措”：替换不算存活；第二个“错”原样保留 → partial
    r = _annotate(client, [
        ("对", 0, 100), ("错", 200, 300), ("错", 400, 500),
    ], "对措错", "对错错", [_ann("a1", 1, 3)], total=600)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "annotation_id": "a1", "status": "partial",
        "old_start_index": 1, "old_end_index": 3,
        "new_start_index": 2, "new_end_index": 3,
        "start": 400, "end": 500,
    }]


def test_annotation_migrate_full_substitution_is_orphan(client):
    # 唯一字符被替换纠错：无相同字符存活 → orphan
    r = _annotate(client, [("错", 0, 100)], "措", "错", [_ann("a1", 0, 1)],
                  total=200)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "annotation_id": "a1", "status": "orphan",
        "old_start_index": 0, "old_end_index": 1,
        "new_start_index": None, "new_end_index": None,
        "start": None, "end": None,
    }]


def test_annotation_migrate_empty_new_text_all_orphan(client):
    r = _annotate(client, [("a", 0, 100)], "", "aa", [_ann("x", 0, 2)],
                  total=200)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "annotation_id": "x", "status": "orphan",
        "old_start_index": 0, "old_end_index": 2,
        "new_start_index": None, "new_end_index": None,
        "start": None, "end": None,
    }]


def test_annotation_migrate_preserves_input_order(client):
    r = _annotate(client, [
        ("你", 0, 100), ("好", 200, 300),
    ], "你好", "你好", [_ann("b", 1, 2), _ann("a", 0, 1)], total=400)
    assert r.status_code == 200, r.text
    assert [x["annotation_id"] for x in r.json()] == ["b", "a"]


def test_annotation_migrate_duplicate_id_rejected(client):
    r = _annotate(client, [("你", 0, 100)], "你", "你",
                  [_ann("a", 0, 1), _ann("a", 0, 1)], total=200)
    assert r.status_code == 422
    assert "唯一" in r.json()["detail"]


def test_annotation_migrate_out_of_old_text_rejected(client):
    r = _annotate(client, [("你", 0, 100)], "你", "你好", [_ann("a", 0, 3)],
                  total=200)
    assert r.status_code == 422
    assert "超出旧版修订文本" in r.json()["detail"]


def test_annotation_migrate_negative_index_rejected(client):
    r = _annotate(client, [("你", 0, 100)], "你", "你好", [_ann("a", -1, 1)],
                  total=200)
    assert r.status_code == 422
    assert "超出旧版修订文本" in r.json()["detail"]


def test_annotation_migrate_empty_interval_rejected(client):
    for s, e in ((1, 1), (2, 1)):
        r = _annotate(client, [("你", 0, 100)], "你", "你好", [_ann("a", s, e)],
                      total=200)
        assert r.status_code == 422, (s, e, r.text)
        assert "非空" in r.json()["detail"]


def test_annotation_migrate_annotation_count_range(client):
    r0 = _annotate(client, [], "", "", [], total=0)
    assert r0.status_code == 422
    assert "1～100" in r0.json()["detail"]
    many = [_ann(f"a{i}", 0, 1) for i in range(101)]
    r101 = _annotate(client, [], "", "x", many, total=0)
    assert r101.status_code == 422
    assert "1～100" in r101.json()["detail"]
    ok = _annotate(client, [], "", "x",
                   [_ann(f"a{i}", 0, 1) for i in range(100)], total=0)
    assert ok.status_code == 200, ok.text


def test_annotation_migrate_empty_id_rejected(client):
    r = _annotate(client, [("你", 0, 100)], "你", "你", [_ann("", 0, 1)],
                  total=200)
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_annotation_migrate_reuses_alignment_validation(client):
    r = _annotate(client, [("a", 300, 100)], "a", "a", [_ann("a", 0, 1)],
                  total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_annotation_migrate_old_text_too_long_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_REVISED_LENGTH", 2)
    r = client.post("/annotation-migrate", json={
        "recognized": [], "total_duration": 0, "revised_text": "ab",
        "old_text": "abc", "annotations": [_ann("a", 0, 1)]})
    assert r.status_code == 422
    assert "旧版修订文本长度" in r.json()["detail"]


def test_annotation_migrate_missing_fields_rejected(client):
    r = client.post("/annotation-migrate", json={
        "recognized": [], "total_duration": 0, "revised_text": ""})
    assert r.status_code == 422
    assert "old_text" in r.json()["detail"]
    assert "annotations" in r.json()["detail"]


def test_annotation_migrate_extra_field_rejected(client):
    r = client.post("/annotation-migrate", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "old_text": "a",
        "annotations": [_ann("a", 0, 1)],
        "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_annotation_migrate_annotation_extra_field_rejected(client):
    r = client.post("/annotation-migrate", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "old_text": "a",
        "annotations": [{"annotation_id": "a", "start_index": 0,
                         "end_index": 1, "bogus": 1}]})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_annotation_migrate_index_wrong_type(client):
    r = client.post("/annotation-migrate", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "old_text": "a",
        "annotations": [{"annotation_id": "a", "start_index": "0",
                         "end_index": 1}]})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 敏感词脱敏 POST /redact
# ---------------------------------------------------------------------------
def _redact(client, items, revised, terms, padding_ms=0, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "terms": terms,
        "padding_ms": padding_ms,
    }
    return client.post("/redact", json=payload)


def test_redact_basic_masking_and_mute_intervals(client):
    r = _redact(client, [
        ("张", 0, 100), ("三", 100, 200), ("说", 200, 300),
        ("李", 300, 400), ("四", 400, 500), ("来", 500, 600),
        ("了", 600, 700),
    ], "张三说李四来了", ["张三", "李四"], padding_ms=30)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "redacted_text": "██说██来了",
        "spans": [
            {"start_index": 0, "end_index": 2, "text": "张三",
             "start": 0, "end": 200},
            {"start_index": 3, "end_index": 5, "text": "李四",
             "start": 300, "end": 500},
        ],
        # [0,200] 扩 30 → [0,230]（起点裁剪到 0）；[300,500] 扩 30 → [270,530]
        "mute_intervals": [
            {"start": 0, "end": 230},
            {"start": 270, "end": 530},
        ],
    }


def test_redact_overlapping_occurrences_merge(client):
    # “aa”在“aaa”中的两个重叠命中 (0,2)、(1,3) 合并为 (0,3)
    r = _redact(client, [
        ("a", 0, 100), ("a", 100, 200), ("a", 200, 300),
    ], "aaa", ["aa"], total=400)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redacted_text"] == "███"
    assert body["spans"] == [
        {"start_index": 0, "end_index": 3, "text": "aaa",
         "start": 0, "end": 300}]
    assert body["mute_intervals"] == [{"start": 0, "end": 300}]


def test_redact_touching_hits_merge_into_one_span(client):
    # “ab”(0,2) 与 “cd”(2,4) 端点相接 → 合并为 (0,4)
    r = _redact(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300), ("d", 300, 400),
    ], "abcd", ["ab", "cd"], total=500)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redacted_text"] == "████"
    assert body["spans"] == [
        {"start_index": 0, "end_index": 4, "text": "abcd",
         "start": 0, "end": 400}]


def test_redact_mask_length_equals_original(client):
    r = _redact(client, [
        ("机", 0, 100), ("密", 100, 200), ("词", 200, 300),
    ], "机密词", ["机密词"], total=400)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["redacted_text"]) == len("机密词")
    assert set(body["redacted_text"]) == {"█"}


def test_redact_no_hit_returns_original_and_empty(client):
    r = _redact(client, [("你", 0, 100), ("好", 200, 300)], "你好",
                ["机密"], padding_ms=100, total=400)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "redacted_text": "你好", "spans": [], "mute_intervals": []}


def test_redact_empty_revised_text(client):
    r = _redact(client, [("a", 0, 100)], "", ["a"], total=200)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "redacted_text": "", "spans": [], "mute_intervals": []}


def test_redact_padding_clamped_to_recording_bounds(client):
    # 命中区间 [100,300] 扩 1000：两侧分别裁剪到 0 与 total_duration
    r = _redact(client, [
        ("你", 0, 100), ("机", 100, 200), ("密", 200, 300),
    ], "你机密", ["机密"], padding_ms=1000, total=300)
    assert r.status_code == 200, r.text
    assert r.json()["mute_intervals"] == [{"start": 0, "end": 300}]


def test_redact_mute_intervals_merge_when_padding_bridges_gap(client):
    # [0,200] 扩 100 → [0,300]；[300,500] 扩 100 → [200,500]，重叠合并
    r = _redact(client, [
        ("张", 0, 100), ("三", 100, 200), ("说", 200, 300),
        ("李", 300, 400), ("四", 400, 500),
    ], "张三说李四", ["张三", "李四"], padding_ms=100, total=500)
    assert r.status_code == 200, r.text
    assert r.json()["mute_intervals"] == [{"start": 0, "end": 500}]


def test_redact_touching_mute_intervals_merge(client):
    # [0,200] 扩 50 → [0,250]；[300,500] 扩 50 → [250,500]，端点相接也合并
    r = _redact(client, [
        ("张", 0, 100), ("三", 100, 200), ("说", 200, 300),
        ("李", 300, 400), ("四", 400, 500),
    ], "张三说李四", ["张三", "李四"], padding_ms=50, total=500)
    assert r.status_code == 200, r.text
    assert r.json()["mute_intervals"] == [{"start": 0, "end": 500}]


def test_redact_uses_computed_timeline_for_inserted_chars(client):
    # 新增字符“要”在空档 [100,900] 内推算，区间时间以其推算时间为准
    r = _redact(client, [("机", 0, 100), ("密", 900, 1000)], "机要密",
                ["机要密"], padding_ms=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redacted_text"] == "███"
    assert body["spans"] == [
        {"start_index": 0, "end_index": 3, "text": "机要密",
         "start": 0, "end": 1000}]
    assert body["mute_intervals"] == [{"start": 0, "end": 1000}]


def test_redact_zero_padding_keeps_original_times(client):
    r = _redact(client, [
        ("机", 100, 200), ("密", 300, 400),
    ], "机密", ["机密"], padding_ms=0, total=500)
    assert r.status_code == 200, r.text
    assert r.json()["mute_intervals"] == [{"start": 100, "end": 400}]


def test_redact_reuses_alignment_validation(client):
    r = _redact(client, [("a", 300, 100)], "a", ["a"], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_redact_duplicate_terms_rejected(client):
    r = _redact(client, [], "", ["机密", "秘密", "机密"], total=0)
    assert r.status_code == 422
    assert "互不相同" in r.json()["detail"]


def test_redact_empty_term_rejected(client):
    r = _redact(client, [], "", ["机密", ""], total=0)
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_redact_terms_count_range(client):
    r0 = _redact(client, [], "", [], total=0)
    assert r0.status_code == 422
    assert "1～50" in r0.json()["detail"]
    r51 = _redact(client, [], "", [f"词{i}" for i in range(51)], total=0)
    assert r51.status_code == 422
    assert "1～50" in r51.json()["detail"]
    ok = _redact(client, [], "", [f"词{i}" for i in range(50)], total=0)
    assert ok.status_code == 200, ok.text


def test_redact_padding_ms_range(client):
    for bad in (-1, 1001):
        r = _redact(client, [], "", ["机密"], padding_ms=bad, total=0)
        assert r.status_code == 422, (bad, r.text)
        assert "0～1000" in r.json()["detail"]
    for good in (0, 1000):
        r = _redact(client, [], "", ["机密"], padding_ms=good, total=0)
        assert r.status_code == 200, (good, r.text)


def test_redact_padding_ms_wrong_type(client):
    r = _redact(client, [], "", ["机密"], padding_ms="50", total=0)
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_redact_term_wrong_type(client):
    r = _redact(client, [], "", ["机密", 1], total=0)
    assert r.status_code == 422
    assert "字符串" in r.json()["detail"]


def test_redact_missing_fields_rejected(client):
    r = client.post("/redact", json={
        "recognized": [], "total_duration": 0, "revised_text": ""})
    assert r.status_code == 422
    assert "terms" in r.json()["detail"]
    assert "padding_ms" in r.json()["detail"]


def test_redact_extra_field_rejected(client):
    r = client.post("/redact", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "terms": ["机密"], "padding_ms": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 成片剪辑规划 POST /clip-plan
# ---------------------------------------------------------------------------
def _clip(client, items, revised, selections, padding_ms=0, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "selections": [
            {"start_index": s, "end_index": e} for s, e in selections],
        "padding_ms": padding_ms,
    }
    return client.post("/clip-plan", json=payload)


def test_clip_plan_two_separate_segments_with_padding(client):
    # 选 “你好”[0,2) 与 “世界”[3,5)，扩 30ms，间隔不合并；成片连续拼接
    r = _clip(client, [
        ("你", 0, 100), ("好", 100, 200), ("呀", 300, 400),
        ("世", 500, 600), ("界", 600, 700),
    ], "你好呀世界", [(0, 2), (3, 5)], padding_ms=30, total=800)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "segments": [
            {"segment_index": 0, "source_start": 0, "source_end": 230,
             "output_start": 0, "output_end": 230, "duration_ms": 230},
            {"segment_index": 1, "source_start": 470, "source_end": 730,
             "output_start": 230, "output_end": 490, "duration_ms": 260},
        ],
        "total_duration": 490,
        "selections": [
            {"index": 0, "start_index": 0, "end_index": 2, "text": "你好",
             "segment_index": 0, "output_start": 0, "output_end": 200},
            {"index": 1, "start_index": 3, "end_index": 5, "text": "世界",
             "segment_index": 1, "output_start": 260, "output_end": 460},
        ],
    }


def test_clip_plan_padding_bridges_gap_merges_segments(client):
    # 源区间 [0,200] 扩 50 → [0,250]；[300,500] 扩 50 → [250,550]，
    # 端点相接 → 合并成一段，声音不重复
    r = _clip(client, [
        ("张", 0, 100), ("三", 100, 200), ("说", 200, 300),
        ("李", 300, 400), ("四", 400, 500),
    ], "张三说李四", [(0, 2), (3, 5)], padding_ms=50, total=600)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == [
        {"segment_index": 0, "source_start": 0, "source_end": 550,
         "output_start": 0, "output_end": 550, "duration_ms": 550}]
    assert body["total_duration"] == 550
    assert [s["segment_index"] for s in body["selections"]] == [0, 0]
    sel0, sel1 = body["selections"]
    assert (sel0["output_start"], sel0["output_end"]) == (0, 200)
    # “李四”原时间 [300,500] 随合并片段整体平移为 [300,500]（片段源起点也是 0）
    assert (sel1["output_start"], sel1["output_end"]) == (300, 500)


def test_clip_plan_touching_char_selections_share_segment(client):
    # 端点相接的两个字符区间（“ab” 与 “cd”）天然在同一合并片段内
    r = _clip(client, [
        ("a", 0, 100), ("b", 100, 200), ("c", 200, 300), ("d", 300, 400),
    ], "abcd", [(0, 2), (2, 4)], padding_ms=0, total=500)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == [
        {"segment_index": 0, "source_start": 0, "source_end": 400,
         "output_start": 0, "output_end": 400, "duration_ms": 400}]
    assert body["total_duration"] == 400
    assert body["selections"][1] == {
        "index": 1, "start_index": 2, "end_index": 4, "text": "cd",
        "segment_index": 0, "output_start": 200, "output_end": 400}


def test_clip_plan_padding_clamped_to_recording_bounds(client):
    r = _clip(client, [
        ("你", 0, 100), ("机", 100, 200), ("密", 200, 300),
    ], "你机密", [(1, 3)], padding_ms=2000, total=300)
    assert r.status_code == 200, r.text
    assert r.json()["segments"] == [
        {"segment_index": 0, "source_start": 0, "source_end": 300,
         "output_start": 0, "output_end": 300, "duration_ms": 300}]


def test_clip_plan_output_timeline_concatenates_with_offset(client):
    # 两段：[100,200] 与 [500,600]，padding=0；第二段成片时间整体后移 100ms
    r = _clip(client, [
        ("a", 100, 200), ("b", 300, 400), ("c", 500, 600),
    ], "abc", [(0, 1), (2, 3)], padding_ms=0, total=700)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_duration"] == 200
    assert body["segments"][1] == {
        "segment_index": 1, "source_start": 500, "source_end": 600,
        "output_start": 100, "output_end": 200, "duration_ms": 100}
    assert body["selections"][1] == {
        "index": 1, "start_index": 2, "end_index": 3, "text": "c",
        "segment_index": 1, "output_start": 100, "output_end": 200}


def test_clip_plan_single_selection(client):
    r = _clip(client, [("你", 0, 100), ("好", 200, 300)], "你好", [(0, 2)],
              padding_ms=10, total=400)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == [
        {"segment_index": 0, "source_start": 0, "source_end": 310,
         "output_start": 0, "output_end": 310, "duration_ms": 310}]
    assert body["total_duration"] == 310
    assert len(body["selections"]) == 1


def test_clip_plan_uses_computed_timeline_for_inserted_chars(client):
    # “要”为新增字符，在空档 [100,900] 内推算；选择含该字符时以推算时间为准
    r = _clip(client, [("机", 0, 100), ("密", 900, 1000)], "机要密",
              [(0, 3)], padding_ms=0, total=1000)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"][0]["source_start"] == 0
    assert body["segments"][0]["source_end"] == 1000
    assert body["selections"][0]["text"] == "机要密"


def test_clip_plan_reuses_alignment_validation(client):
    r = _clip(client, [("a", 300, 100)], "a", [(0, 1)], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_clip_plan_negative_index_rejected(client):
    r = _clip(client, [("a", 0, 100)], "a", [(-1, 1)], total=200)
    assert r.status_code == 422
    assert "负数" in r.json()["detail"]


def test_clip_plan_empty_interval_rejected(client):
    r = _clip(client, [("a", 0, 100)], "a", [(1, 1)], total=200)
    assert r.status_code == 422
    assert "非空半开区间" in r.json()["detail"]


def test_clip_plan_out_of_text_rejected(client):
    r = _clip(client, [("a", 0, 100)], "a", [(0, 2)], total=200)
    assert r.status_code == 422
    assert "超出修订稿长度" in r.json()["detail"]


def test_clip_plan_not_ordered_rejected(client):
    r = _clip(client, [("a", 0, 100), ("b", 100, 200), ("c", 200, 300)],
              "abc", [(2, 3), (0, 1)], total=400)
    assert r.status_code == 422
    assert "顺序" in r.json()["detail"]


def test_clip_plan_overlapping_intervals_rejected(client):
    r = _clip(client, [("a", 0, 100), ("b", 100, 200), ("c", 200, 300)],
              "abc", [(0, 2), (1, 3)], total=400)
    assert r.status_code == 422
    assert "不得重叠" in r.json()["detail"]


def test_clip_plan_zero_duration_mapped_selection_rejected(client):
    # 两个锚点字符同一时间槽，中间新增字符推算为零时长，选中该新增字符 → 422
    r = _clip(client, [("a", 100, 100), ("b", 100, 100)], "axb",
              [(1, 2)], total=300)
    assert r.status_code == 422
    assert "零时长" in r.json()["detail"]


def test_clip_plan_selection_touching_zero_duration_boundary(client):
    # 新增字符 x 在零空档 [100,100] 内推算为零时长，但与前后正时长字符一起
    # 选中时整体区间为 [0,100] 正时长，合法
    r = _clip(client, [("a", 0, 100), ("b", 100, 100)], "axb",
              [(0, 3)], total=300)
    assert r.status_code == 200, r.text
    seg = r.json()["segments"][0]
    assert (seg["source_start"], seg["source_end"]) == (0, 100)
    assert seg["duration_ms"] == 100


def test_clip_plan_padding_ms_range(client):
    base = lambda pad: _clip(client, [("a", 0, 100)], "a", [(0, 1)],
                             padding_ms=pad, total=200)
    for bad in (-1, 2001):
        r = base(bad)
        assert r.status_code == 422, (bad, r.text)
        assert "0～2000" in r.json()["detail"]
    for good in (0, 2000):
        r = base(good)
        assert r.status_code == 200, (good, r.text)


def test_clip_plan_padding_ms_wrong_type(client):
    r = client.post("/clip-plan", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a",
        "selections": [{"start_index": 0, "end_index": 1}],
        "padding_ms": "50"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_clip_plan_selections_count_range(client):
    r0 = client.post("/clip-plan", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a", "selections": [], "padding_ms": 0})
    assert r0.status_code == 422
    assert "1～20" in r0.json()["detail"]

    text = "a" * 20
    items = [("a", i * 10, i * 10 + 10) for i in range(20)]
    r21 = client.post("/clip-plan", json={
        "recognized": rc(items), "total_duration": 300,
        "revised_text": text,
        "selections": [{"start_index": i, "end_index": i + 1}
                       for i in range(21)], "padding_ms": 0})
    assert r21.status_code == 422
    assert "1～20" in r21.json()["detail"]

    r20 = client.post("/clip-plan", json={
        "recognized": rc(items), "total_duration": 300,
        "revised_text": text,
        "selections": [{"start_index": i, "end_index": i + 1}
                       for i in range(20)], "padding_ms": 0})
    assert r20.status_code == 200, r20.text


def test_clip_plan_index_wrong_type(client):
    r = client.post("/clip-plan", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a",
        "selections": [{"start_index": "0", "end_index": 1}],
        "padding_ms": 0})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_clip_plan_missing_fields_rejected(client):
    r = client.post("/clip-plan", json={
        "recognized": [], "total_duration": 0, "revised_text": ""})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "selections" in detail
    assert "padding_ms" in detail


def test_clip_plan_extra_field_rejected(client):
    r = client.post("/clip-plan", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a",
        "selections": [{"start_index": 0, "end_index": 1}],
        "padding_ms": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_clip_plan_selection_extra_field_rejected(client):
    r = client.post("/clip-plan", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a",
        "selections": [{"start_index": 0, "end_index": 1, "bogus": 1}],
        "padding_ms": 0})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 录音覆盖缺口审计 POST /coverage-gaps
# ---------------------------------------------------------------------------
def _gaps(client, items, revised, min_gap_ms=1, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "min_gap_ms": min_gap_ms,
    }
    return client.post("/coverage-gaps", json=payload)


def test_coverage_gaps_leading_middle_trailing(client):
    # 覆盖 [100,300) 与 [600,800)：首、中、尾三个空洞都保留
    r = _gaps(client, [
        ("你", 100, 200), ("好", 200, 300), ("世", 600, 700), ("界", 700, 800),
    ], "你好呀世界", min_gap_ms=100, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 0, "end": 100, "left_matched_index": None,
         "right_matched_index": 0, "computed_spans": []},
        {"start": 300, "end": 600, "left_matched_index": 1,
         "right_matched_index": 3,
         "computed_spans": [{"start_index": 2, "end_index": 3}]},
        {"start": 800, "end": 1000, "left_matched_index": 4,
         "right_matched_index": None, "computed_spans": []},
    ]


def test_coverage_gaps_min_gap_ms_filters_short_gaps(client):
    # 缺口 [100,200) 长 100、[300,1000) 长 700；min_gap_ms=200 只保留后者
    r = _gaps(client, [("a", 0, 100), ("b", 200, 300)], "ab",
              min_gap_ms=200, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 300, "end": 1000, "left_matched_index": 1,
         "right_matched_index": None, "computed_spans": []}]
    # 时长恰好等于 min_gap_ms 的缺口保留（不少于）
    r = _gaps(client, [("a", 0, 100), ("b", 200, 300)], "ab",
              min_gap_ms=100, total=1000)
    assert [g["start"] for g in r.json()] == [100, 300]


def test_coverage_gaps_zero_duration_recognized_does_not_cover(client):
    # 零时长识别字符不覆盖：相接处不产生缺口，单独出现也不覆盖
    r = _gaps(client, [("a", 0, 100), ("z", 100, 100), ("b", 100, 200)],
              "ab", total=300)
    assert [(g["start"], g["end"]) for g in r.json()] == [(200, 300)]
    r = _gaps(client, [("z", 500, 500)], "", total=1000)
    assert r.json() == [
        {"start": 0, "end": 1000, "left_matched_index": None,
         "right_matched_index": None, "computed_spans": []}]


def test_coverage_gaps_touching_intervals_leave_no_gap(client):
    r = _gaps(client, [("a", 0, 100), ("b", 100, 200)], "ab", total=200)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_coverage_gaps_zero_total_duration(client):
    r = _gaps(client, [], "", total=0)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_coverage_gaps_empty_recognized_single_gap(client):
    # 无识别字符：整段录音是一个缺口，所有修订字符都是 computed
    r = _gaps(client, [], "甲乙", min_gap_ms=1, total=1000)
    assert r.json() == [
        {"start": 0, "end": 1000, "left_matched_index": None,
         "right_matched_index": None,
         "computed_spans": [{"start_index": 0, "end_index": 2}]}]


def test_coverage_gaps_deleted_recognized_still_covers(client):
    # 被删除的识别字符仍覆盖其时间槽；两侧 matched 跳过它
    r = _gaps(client, [("你", 0, 100), ("吗", 200, 300), ("世", 400, 500)],
              "你世", total=600)
    assert r.json() == [
        {"start": 100, "end": 200, "left_matched_index": 0,
         "right_matched_index": 1, "computed_spans": []},
        {"start": 300, "end": 400, "left_matched_index": 0,
         "right_matched_index": 1, "computed_spans": []},
        {"start": 500, "end": 600, "left_matched_index": 1,
         "right_matched_index": None, "computed_spans": []},
    ]


def test_coverage_gaps_substitution_counts_as_matched(client):
    # 替换纠错的字符仍是 matched，可作为缺口两侧边界
    r = _gaps(client, [("你", 0, 100), ("吗", 300, 400)], "你好", total=500)
    assert r.json() == [
        {"start": 100, "end": 300, "left_matched_index": 0,
         "right_matched_index": 1, "computed_spans": []},
        {"start": 400, "end": 500, "left_matched_index": 1,
         "right_matched_index": None, "computed_spans": []},
    ]


def test_coverage_gaps_substituted_recognized_still_covers(client):
    # 替换纠错的识别字符仍覆盖其时间槽，并作为两侧缺口的 matched 边界
    r = _gaps(client, [("a", 0, 100), ("d", 200, 300), ("b", 400, 500)],
              "axb", total=500)
    assert r.json() == [
        {"start": 100, "end": 200, "left_matched_index": 0,
         "right_matched_index": 1, "computed_spans": []},
        {"start": 300, "end": 400, "left_matched_index": 1,
         "right_matched_index": 2, "computed_spans": []},
    ]


def test_coverage_gaps_multiple_computed_runs_in_one_gap(client):
    # 缺口内的 computed 字符被 matched 字符（零时长识别字符所锚定）截断成多段
    r = _gaps(client, [("a", 0, 100), ("z", 250, 250), ("b", 400, 500)],
              "axzyb", total=500)
    assert r.json() == [
        {"start": 100, "end": 400, "left_matched_index": 0,
         "right_matched_index": 4,
         "computed_spans": [{"start_index": 1, "end_index": 2},
                            {"start_index": 3, "end_index": 4}]}]


def test_coverage_gaps_reuses_alignment_validation(client):
    r = _gaps(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_coverage_gaps_min_gap_ms_must_be_positive(client):
    for bad in (0, -5):
        r = _gaps(client, [("a", 0, 100)], "a", min_gap_ms=bad, total=200)
        assert r.status_code == 422, (bad, r.text)
        assert "正整数" in r.json()["detail"]
    r = _gaps(client, [("a", 0, 100)], "a", min_gap_ms=1, total=200)
    assert r.status_code == 200, r.text


def test_coverage_gaps_min_gap_ms_wrong_type(client):
    r = _gaps(client, [("a", 0, 100)], "a", min_gap_ms="100", total=200)
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_coverage_gaps_missing_fields_rejected(client):
    r = client.post("/coverage-gaps", json={
        "recognized": [], "total_duration": 0, "revised_text": ""})
    assert r.status_code == 422
    assert "min_gap_ms" in r.json()["detail"]


def test_coverage_gaps_extra_field_rejected(client):
    r = client.post("/coverage-gaps", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "min_gap_ms": 1, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 风险听审片段 POST /review-clips
# ---------------------------------------------------------------------------
def _review(client, items, revised, max_clips=5, padding_ms=0, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "max_clips": max_clips,
        "padding_ms": padding_ms,
    }
    return client.post("/review-clips", json=payload)


def test_review_clips_substitution_and_computed_blocks(client):
    # “道”替换识别字“到”（matched 但字符不同），“呀”为新增 computed；
    # 二者被安全字符隔开，各成一个风险块
    r = _review(client, [
        ("张", 0, 100), ("三", 100, 200), ("到", 200, 300),
        ("李", 300, 400), ("四", 400, 500),
    ], "张三道李四呀", padding_ms=0, total=600)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 200, "end": 300, "start_index": 2, "end_index": 3,
         "risk_count": 1},
        # “呀”在“四”end=500 与录音终点 600 的空档内推算
        {"start": 500, "end": 600, "start_index": 5, "end_index": 6,
         "risk_count": 1},
    ]


def test_review_clips_adjacent_risk_chars_merge_into_one_block(client):
    # 替换字“道”与相邻新增字“呀”（无安全字符隔开）合并为一个风险块，
    # 区间取首字 start 到末字 end（“呀”在“到”end=200 与“世”start=400 空档推算）
    r = _review(client, [
        ("张", 0, 100), ("到", 100, 200), ("世", 400, 500),
    ], "张道呀世", padding_ms=20, total=600)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 80, "end": 420, "start_index": 1, "end_index": 3,
         "risk_count": 2},
    ]


def test_review_clips_padding_clamped_to_recording_bounds(client):
    # 纯新增字符在录音首/尾空档推算，扩展留白后裁剪到 [0,total_duration]
    r = _review(client, [
        ("a", 200, 300), ("b", 800, 900),
    ], "axb", padding_ms=200, total=1000)
    assert r.status_code == 200, r.text
    # “x”在 [300,800] 空档推算为 [300,800]；扩 200 → [100,1000]
    assert r.json() == [
        {"start": 100, "end": 1000, "start_index": 1, "end_index": 2,
         "risk_count": 1}]

    # 新增字符落在录音起点空档：裁剪 start
    r = _review(client, [("吗", 400, 500)], "呀吗", padding_ms=200, total=1000)
    assert r.status_code == 200, r.text
    # “呀”推算区间 [0,400]，扩 200 裁剪到起点 → [0,600]
    assert r.json() == [
        {"start": 0, "end": 600, "start_index": 0, "end_index": 1,
         "risk_count": 1}]

    # 替换字符贴录音终点：裁剪 end
    r = _review(client, [("吗", 900, 1000)], "呀", padding_ms=200, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 700, "end": 1000, "start_index": 0, "end_index": 1,
         "risk_count": 1}]


def test_review_clips_no_risk_returns_empty(client):
    # 完全匹配（含无任何修订）时返回空数组
    r = _review(client, [("你", 0, 100), ("好", 100, 200)], "你好", total=300)
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = _review(client, [], "", total=0, max_clips=1, padding_ms=0)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_review_clips_max_clips_limits_selection(client):
    # 三个互不重叠、等权（各 1 个风险字符）的风险块，max_clips=2 取总时长最短的两块
    r = _review(client, [
        ("x", 0, 100), ("s", 100, 200), ("y", 400, 600),
        ("t", 600, 700), ("z", 900, 900),
    ], "asbtc", max_clips=2, padding_ms=0, total=1000)
    assert r.status_code == 200, r.text
    # 修订稿 “asbtc”：a(sub)[0,100]、s 安全[100,200]、b(sub)[400,600]、
    # t 安全[600,700]、c(sub 零时长)[900,900]
    # c 零时长最短必选；a（100ms）比 b（200ms）更短 → 选 a、c
    assert r.json() == [
        {"start": 0, "end": 100, "start_index": 0, "end_index": 1,
         "risk_count": 1},
        {"start": 900, "end": 900, "start_index": 4, "end_index": 5,
         "risk_count": 1},
    ]


def test_review_clips_maximize_risk_chars_over_count(client):
    # 左右两个单字符风险块与中间一个三字符连续替换风险块，经留白扩展后两两重叠；
    # 选左右两块合计 2 个风险字符，单独选中间块为 3 个 → 只选中间块
    r = _review(client, [
        ("u", 0, 100), ("z", 100, 200),
        ("q", 200, 400), ("w", 400, 600), ("p", 600, 800),
        ("v", 800, 900), ("k", 900, 1000),
    ], "bz123vk", max_clips=2, padding_ms=150, total=1000)
    assert r.status_code == 200, r.text
    # 风险块：b(sub 下标0)[0,100]；下标 2/3/4 的 “1/2/3” 连续替换 q/w/p，
    # 合并为中间块 [200,800] 扩 150 得 [50,950]，risk_count=3；
    # c(sub 下标6)[900,1000]。中间块与左右扩展区间都重叠。
    body = r.json()
    assert len(body) == 1
    assert body[0] == {"start": 50, "end": 950,
                       "start_index": 2, "end_index": 5, "risk_count": 3}


def test_review_clips_tie_shorter_total_duration(client):
    # 等权两块（各 1 风险字符）经留白重叠：左块扩展后更短 → 选左块
    r = _review(client, [
        ("x", 0, 100), ("s", 200, 300), ("y", 400, 500),
    ], "asb", max_clips=1, padding_ms=200, total=800)
    assert r.status_code == 200, r.text
    # a[0,100] 扩 200 裁剪 → [0,300]（300ms）；b[400,500] → [200,700]（500ms）
    assert r.json() == [
        {"start": 0, "end": 300, "start_index": 0, "end_index": 1,
         "risk_count": 1}]


def test_review_clips_tie_lexicographic_intervals(client):
    # 两块等权、扩展后等时长但互相重叠：取起点更早（区间序列字典序更小）的左块
    r = _review(client, [
        ("m", 0, 100), ("x", 100, 300), ("z", 300, 700),
        ("y", 700, 900), ("n", 900, 1000),
    ], "mazbn", max_clips=1, padding_ms=300, total=1000)
    assert r.status_code == 200, r.text
    # a[100,300] 扩 300 → [0,600]；b[700,900] → [400,1000]；均 600ms
    assert r.json() == [
        {"start": 0, "end": 600, "start_index": 1, "end_index": 2,
         "risk_count": 1}]


def test_review_clips_touching_intervals_are_compatible(client):
    # 两个风险块音频区间端点相接（半开区间）不算重叠，可同时选中
    r = _review(client, [
        ("x", 0, 100), ("s", 100, 100), ("y", 100, 200),
    ], "asb", max_clips=2, padding_ms=0, total=300)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start": 0, "end": 100, "start_index": 0, "end_index": 1,
         "risk_count": 1},
        {"start": 100, "end": 200, "start_index": 2, "end_index": 3,
         "risk_count": 1},
    ]


def test_review_clips_results_sorted_by_time(client):
    # max_clips 足够大时按原文（时间）顺序返回全部风险块（三块被安全字符隔开）
    r = _review(client, [
        ("x", 0, 100), ("s", 100, 200), ("y", 200, 300),
        ("t", 300, 400), ("z", 400, 500),
    ], "asbtc", max_clips=20, padding_ms=0, total=600)
    assert r.status_code == 200, r.text
    assert [clip["start"] for clip in r.json()] == [0, 200, 400]
    assert [clip["risk_count"] for clip in r.json()] == [1, 1, 1]


def test_review_clips_zero_duration_block_allowed(client):
    # 零时长风险块（替换锚定到零时长识别字符）合法，且时长最短优先
    r = _review(client, [
        ("x", 0, 100), ("s", 100, 200), ("y", 200, 200),
    ], "asb", max_clips=1, padding_ms=0, total=300)
    assert r.status_code == 200, r.text
    # a(sub)[0,100]（100ms）与 b(sub 零时长)[200,200] 两块等权，
    # 零时长块播放时长更短 → 选 b
    assert r.json() == [
        {"start": 200, "end": 200, "start_index": 2, "end_index": 3,
         "risk_count": 1}]


def test_review_clips_reuses_alignment_validation(client):
    r = _review(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_review_clips_max_clips_range(client):
    body = {
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a", "padding_ms": 0}
    for bad in (0, 21):
        r = client.post("/review-clips", json={**body, "max_clips": bad})
        assert r.status_code == 422, (bad, r.text)
        assert "1～20" in r.json()["detail"]
    for good in (1, 20):
        r = client.post("/review-clips", json={**body, "max_clips": good})
        assert r.status_code == 200, (good, r.text)


def test_review_clips_max_clips_wrong_type(client):
    r = client.post("/review-clips", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a", "max_clips": "5", "padding_ms": 0})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_review_clips_padding_ms_range(client):
    body = {
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a", "max_clips": 1}
    for bad in (-1, 2001):
        r = client.post("/review-clips", json={**body, "padding_ms": bad})
        assert r.status_code == 422, (bad, r.text)
        assert "0～2000" in r.json()["detail"]
    for good in (0, 2000):
        r = client.post("/review-clips", json={**body, "padding_ms": good})
        assert r.status_code == 200, (good, r.text)


def test_review_clips_padding_ms_wrong_type(client):
    r = client.post("/review-clips", json={
        "recognized": rc([("a", 0, 100)]), "total_duration": 200,
        "revised_text": "a", "max_clips": 1, "padding_ms": "50"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_review_clips_missing_fields_rejected(client):
    r = client.post("/review-clips", json={
        "recognized": [], "total_duration": 0, "revised_text": ""})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "max_clips" in detail
    assert "padding_ms" in detail


def test_review_clips_extra_field_rejected(client):
    r = client.post("/review-clips", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "max_clips": 1, "padding_ms": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]
