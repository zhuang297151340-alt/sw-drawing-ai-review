# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone


# 普通尺寸的功能由审核模型结合上下文自动推断，不应因保守的自报置信度让用户逐项录入。
# 几何公差与基准归属影响语义，采用更高阈值并保留冲突复核。
DIMENSION_CONFIRM_THRESHOLD = 0.72
RELATION_CONFIRM_THRESHOLD = 0.78


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _confidence(item: dict) -> float:
    try:
        return max(0.0, min(1.0, float(item.get("confidence", 0.0))))
    except Exception:
        return 0.0


def _frame_lines_text(lines: list) -> str:
    result = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        datums = [str(x) for x in _as_list(line.get("datums")) if str(x).strip()]
        parts = [str(line.get("symbol") or ""), str(line.get("tolerance") or ""),
                 str(line.get("modifier") or "")] + datums[:3]
        result.append(" | ".join(parts).rstrip(" |"))
    return "\n".join(result)


def _parse_frame_lines(text: str) -> list[dict]:
    result = []
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        parts = [x.strip() for x in raw.split("|")]
        while len(parts) < 6:
            parts.append("")
        result.append({
            "symbol": parts[0], "tolerance": parts[1], "modifier": parts[2],
            "datums": [x for x in parts[3:6] if x],
        })
    return result


def build_confirmation_draft(meta: dict, pages: list[dict], verification_conflicts: list[dict],
                             verification_failures: list[dict]) -> dict:
    conflict_ids = set(str(x.get("entity_id") or "") for x in verification_conflicts)
    failed_pages = set(int(x.get("page") or 0) for x in verification_failures)
    rows = []
    dimension_choices = []
    for page in pages:
        page_no = int(page.get("page") or 0)
        for item in _as_list(page.get("dimensions")):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            confidence = _confidence(item)
            status = "系统判定"
            if (confidence < DIMENSION_CONFIRM_THRESHOLD or entity_id in conflict_ids or
                    not str(item.get("display") or "").strip()):
                status = "需确认"
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            row = {
                "row_id": "CONF-" + entity_id, "object_type": "dimension", "entity_id": entity_id,
                "page": page_no, "confidence": confidence, "status": status,
                "kind": str(item.get("kind") or ""), "display": str(item.get("display") or ""),
                "nominal": str(item.get("nominal") or ""), "upper": str(item.get("upper") or ""),
                "lower": str(item.get("lower") or ""), "fit": str(item.get("fit") or ""),
                "quantity": str(item.get("quantity") or ""),
                "target_feature": str(item.get("target_feature") or ""),
                "crop_id": str(evidence.get("crop_id") or ""),
                "confirmed": status != "需确认",
                "confirmation_source": "system" if status != "需确认" else "",
                "manual_added": False, "excluded": False,
            }
            rows.append(row)
            dimension_choices.append({"entity_id": entity_id,
                                      "label": "%s  %s" % (entity_id, row["display"])})

        for item in _as_list(page.get("gtol_frames")):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            confidence = _confidence(item)
            target = item.get("target") if isinstance(item.get("target"), dict) else {}
            status = "系统判定"
            if (confidence < RELATION_CONFIRM_THRESHOLD or entity_id in conflict_ids or page_no in failed_pages or
                    not str(target.get("entity_id") or target.get("description") or "").strip()):
                status = "需确认"
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            rows.append({
                "row_id": "CONF-" + entity_id, "object_type": "gtol", "entity_id": entity_id,
                "page": page_no, "confidence": confidence, "status": status,
                "group_id": str(item.get("group_id") or ""),
                "frame_lines_text": _frame_lines_text(_as_list(item.get("frame_lines"))),
                "raw_text": str(item.get("raw_text") or ""),
                "target_type": str(target.get("type") or "unknown"),
                "target_entity_id": str(target.get("entity_id") or ""),
                "target_feature": str(target.get("description") or target.get("feature") or ""),
                "crop_id": str(evidence.get("crop_id") or ""),
                "confirmed": status != "需确认",
                "confirmation_source": "system" if status != "需确认" else "",
                "manual_added": False, "excluded": False,
            })

        for item in _as_list(page.get("datums")):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            confidence = _confidence(item)
            status = "系统判定"
            if (confidence < RELATION_CONFIRM_THRESHOLD or entity_id in conflict_ids or page_no in failed_pages or
                    not str(item.get("target") or item.get("target_entity_id") or "").strip()):
                status = "需确认"
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            rows.append({
                "row_id": "CONF-" + entity_id, "object_type": "datum", "entity_id": entity_id,
                "page": page_no, "confidence": confidence, "status": status,
                "letter": str(item.get("letter") or ""), "target_feature": str(item.get("target") or ""),
                "target_entity_id": str(item.get("target_entity_id") or ""),
                "crop_id": str(evidence.get("crop_id") or ""),
                "confirmed": status != "需确认",
                "confirmation_source": "system" if status != "需确认" else "",
                "manual_added": False, "excluded": False,
            })
    return {
        "schema_version": "pdf-confirmation-1.2", "source": meta,
        "extraction_pages": pages, "rows": rows, "dimension_choices": dimension_choices,
        "verification_conflicts": verification_conflicts,
        "verification_failures": verification_failures,
    }


def apply_confirmed_rows(draft: dict, confirmed: dict) -> list[dict]:
    pages = copy.deepcopy(_as_list(draft.get("extraction_pages")))
    entities = {}
    page_by_no = {}
    for page in pages:
        page_by_no[int(page.get("page") or 0)] = page
        for name in ("dimensions", "gtol_frames", "datums"):
            for item in _as_list(page.get(name)):
                if isinstance(item, dict) and item.get("entity_id"):
                    entities[(name, str(item["entity_id"]))] = (page, item)
    for row in _as_list(confirmed.get("rows")):
        if not isinstance(row, dict):
            continue
        object_type = str(row.get("object_type") or "")
        name = {"dimension": "dimensions", "gtol": "gtol_frames", "datum": "datums"}.get(object_type, "")
        entity_id = str(row.get("entity_id") or "")
        found = entities.get((name, entity_id))
        if row.get("excluded") is True:
            if found:
                page, item = found
                page[name] = [x for x in _as_list(page.get(name)) if x is not item]
            continue
        if found:
            page, item = found
        else:
            page_no = int(row.get("page") or 0)
            page = page_by_no.get(page_no)
            if page is None or not row.get("manual_added") or not name:
                continue
            item = {"entity_id": entity_id, "evidence": {"page": page_no, "crop_id": ""}}
            page.setdefault(name, []).append(item)
            entities[(name, entity_id)] = (page, item)
        if name == "dimensions":
            for key in ("kind", "display", "nominal", "upper", "lower", "fit", "quantity", "target_feature"):
                item[key] = str(row.get(key) or "")
        elif name == "gtol_frames":
            item["group_id"] = str(row.get("group_id") or "")
            item["raw_text"] = str(row.get("raw_text") or "")
            parsed = _parse_frame_lines(str(row.get("frame_lines_text") or ""))
            if parsed:
                item["frame_lines"] = parsed
            item["target"] = {
                "type": str(row.get("target_type") or "unknown"),
                "entity_id": str(row.get("target_entity_id") or ""),
                "description": str(row.get("target_feature") or ""),
            }
        elif name == "datums":
            item["letter"] = str(row.get("letter") or "")
            item["target"] = str(row.get("target_feature") or "")
            item["target_entity_id"] = str(row.get("target_entity_id") or "")
        source = str(row.get("confirmation_source") or
                     ("human" if row.get("manual_added") else "system"))
        item["human_confirmed"] = source == "human"
        item["confirmation_source"] = source
        item["human_added"] = bool(row.get("manual_added"))
    return pages


def validate_confirmed_payload(draft: dict, confirmed: dict) -> None:
    """Fail closed unless the human-confirmed rows exactly match this extraction draft."""
    draft_sha = str((draft.get("source") or {}).get("sha256") or "")
    confirmed_sha = str(confirmed.get("source_sha256") or "")
    if not draft_sha or confirmed_sha != draft_sha:
        raise ValueError("人工确认数据与当前 PDF 草稿不匹配。")

    draft_rows = [x for x in _as_list(draft.get("rows")) if isinstance(x, dict)]
    confirmed_rows = [x for x in _as_list(confirmed.get("rows")) if isinstance(x, dict)]
    def key(row: dict) -> tuple[str, str, int]:
        return (str(row.get("object_type") or ""), str(row.get("entity_id") or ""),
                int(row.get("page") or 0))

    draft_keys = [key(x) for x in draft_rows]
    confirmed_keys = [key(x) for x in confirmed_rows]
    if len(set(draft_keys)) != len(draft_keys):
        raise ValueError("系统识别草稿包含重复实体编号，已停止审核。")
    if len(set(confirmed_keys)) != len(confirmed_keys):
        raise ValueError("人工确认数据包含重复实体编号，已停止审核。")
    # 原识别行必须全部保留（有效或人工排除），防止静默丢行；仅允许显式人工新增。
    if not set(draft_keys).issubset(set(confirmed_keys)):
        raise ValueError("原识别对象有遗漏；请逐项确认或明确标记为误识别。")
    draft_key_set = set(draft_keys)
    for row in confirmed_rows:
        if key(row) not in draft_key_set and row.get("manual_added") is not True:
            raise ValueError("识别集合之外的对象必须标记为人工新增。")
    if any(x.get("confirmed") is not True for x in confirmed_rows):
        raise ValueError("仍有尺寸、公差、基准或归属关系未经人工确认。")

    dimension_ids = {str(x.get("entity_id") or "") for x in confirmed_rows
                     if x.get("object_type") == "dimension" and x.get("excluded") is not True}
    datum_letters = {str(x.get("letter") or "").strip() for x in confirmed_rows
                     if x.get("object_type") == "datum" and x.get("excluded") is not True}
    for row in confirmed_rows:
        if row.get("excluded") is True:
            continue
        object_type = str(row.get("object_type") or "")
        entity_id = str(row.get("entity_id") or "")
        if int(row.get("page") or 0) <= 0:
            raise ValueError("对象 %s 的页码无效。" % entity_id)
        if object_type == "dimension":
            if not str(row.get("display") or "").strip():
                raise ValueError("尺寸 %s 的完整标注不能为空。" % entity_id)
        elif object_type == "gtol":
            frame_text = str(row.get("frame_lines_text") or "").strip()
            parsed = _parse_frame_lines(frame_text)
            if not parsed or any(not x.get("symbol") or not x.get("tolerance") for x in parsed):
                raise ValueError("几何公差 %s 的公差框格式不完整。" % entity_id)
            referenced_datums = {str(x).strip() for line in parsed for x in _as_list(line.get("datums"))}
            missing_datums = sorted(x for x in referenced_datums if x and x not in datum_letters)
            if missing_datums:
                raise ValueError("几何公差 %s 引用的基准 %s 不存在或已排除。" %
                                 (entity_id, ",".join(missing_datums)))
            target_type = str(row.get("target_type") or "unknown")
            target_entity_id = str(row.get("target_entity_id") or "")
            target_feature = str(row.get("target_feature") or "").strip()
            if target_type == "dimension":
                if target_entity_id not in dimension_ids:
                    raise ValueError("几何公差 %s 挂接的尺寸不存在。" % entity_id)
            elif not target_feature:
                raise ValueError("几何公差 %s 的被测要素尚未确认。" % entity_id)
        elif object_type == "datum":
            if not str(row.get("letter") or "").strip():
                raise ValueError("基准 %s 的字母不能为空。" % entity_id)
            target_entity_id = str(row.get("target_entity_id") or "")
            target_feature = str(row.get("target_feature") or "").strip()
            if not target_feature and not target_entity_id:
                raise ValueError("基准 %s 的指向要素尚未确认。" % entity_id)
            if target_entity_id and target_entity_id not in dimension_ids:
                raise ValueError("基准 %s 关联的尺寸不存在。" % entity_id)


def make_confirmed_payload(draft: dict, rows: list[dict]) -> dict:
    return {
        "schema_version": str(draft.get("schema_version") or "pdf-confirmation-1.2"),
        "source_sha256": str((draft.get("source") or {}).get("sha256") or ""),
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
    }

