"""批注迁移：按旧新修订文本的全局顺序对齐，把旧稿批注区间迁移到新稿。

规则：
- 对旧版修订文本与新版修订稿做全局顺序对齐（与校时共用一个对齐内核）；
- 仅以「相同字符」的匹配关系迁移：被删除或被替换纠错（对齐锚定但字符
  不同）的旧字符都不算存活；
- 每条批注的新区间取仍存活字符在新稿中的最小覆盖区间（首个存活字符的
  新位置到最后一个存活字符的新位置），区间内部的新增字符一并纳入；
- 全部存活 → complete；部分存活 → partial；无存活 → orphan
  （orphan 的新区间与时间为 null）。
"""
from __future__ import annotations

from .alignment import align_chars
from .errors import InvalidTimelineError
from .schemas import (
    Annotation,
    AnnotationMigrateRequest,
    AnnotationMigration,
    RevisedCharTimeline,
)


def validate_annotations(req: AnnotationMigrateRequest) -> None:
    """校验批注：ID 唯一、区间为旧稿上的非空半开区间。

    前置条件：请求已通过 Pydantic 校验（批注 1～100 条、ID 非空、下标为整数）。
    """
    seen: set[str] = set()
    old_len = len(req.old_text)
    for idx, ann in enumerate(req.annotations):
        if ann.annotation_id in seen:
            raise InvalidTimelineError(
                f"批注 ID 必须唯一：{ann.annotation_id!r} 重复出现")
        seen.add(ann.annotation_id)
        if ann.start_index >= ann.end_index:
            raise InvalidTimelineError(
                f"第 {idx} 条批注（ID {ann.annotation_id!r}）必须为非空半开区间："
                f"start_index={ann.start_index} 必须小于 "
                f"end_index={ann.end_index}")
        if ann.start_index < 0 or ann.end_index > old_len:
            raise InvalidTimelineError(
                f"第 {idx} 条批注（ID {ann.annotation_id!r}）的区间 "
                f"[{ann.start_index}, {ann.end_index}) 超出旧版修订文本范围"
                f"（长度 {old_len}）")


def build_annotation_migrations(
        old_text: str,
        new_text: str,
        chars: list[RevisedCharTimeline],
        annotations: list[Annotation]) -> list[AnnotationMigration]:
    """按批注输入顺序返回每条的迁移结果。"""
    # 旧稿 -> 新稿全局对齐：new_to_old[j] 为新稿第 j 字对应的旧稿下标（-1 为新增）。
    new_to_old = align_chars(old_text, new_text)
    old_to_new = [-1] * len(old_text)
    for j, i in enumerate(new_to_old):
        if i >= 0:
            old_to_new[i] = j

    results: list[AnnotationMigration] = []
    for ann in annotations:
        # 仅「相同字符」的匹配算存活；替换纠错锚定（字符不同）的不算。
        positions = [
            old_to_new[i]
            for i in range(ann.start_index, ann.end_index)
            if old_to_new[i] >= 0 and new_text[old_to_new[i]] == old_text[i]
        ]
        if not positions:
            results.append(AnnotationMigration(
                annotation_id=ann.annotation_id,
                status="orphan",
                old_start_index=ann.start_index,
                old_end_index=ann.end_index,
                new_start_index=None,
                new_end_index=None,
                start=None,
                end=None,
            ))
            continue

        # 仍存活字符在新稿中的最小覆盖区间；内部新增字符一并纳入。
        new_start = min(positions)
        new_end = max(positions) + 1
        total = ann.end_index - ann.start_index
        results.append(AnnotationMigration(
            annotation_id=ann.annotation_id,
            status="complete" if len(positions) == total else "partial",
            old_start_index=ann.start_index,
            old_end_index=ann.end_index,
            new_start_index=new_start,
            new_end_index=new_end,
            start=chars[new_start].start,
            end=chars[new_end - 1].end,
        ))
    return results
