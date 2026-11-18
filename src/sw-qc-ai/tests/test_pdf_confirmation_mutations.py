# -*- coding: utf-8 -*-
"""人工确认协议：补录、排除、关系校验和失败闭锁。"""
from __future__ import annotations

import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pdf_audit.confirmation import (apply_confirmed_rows, build_confirmation_draft,
                                    make_confirmed_payload, validate_confirmed_payload)


def _draft() -> dict:
    pages = [{
        "page": 1,
        "dimensions": [
            {"entity_id": "DIM-1", "kind": "diameter", "display": "⌀5", "nominal": "5",
             "target_feature": "孔", "confidence": 0.9},
            {"entity_id": "DIM-FALSE", "kind": "linear", "display": "7", "nominal": "7",
             "target_feature": "误识别线", "confidence": 0.4},
        ],
        "gtol_frames": [{"entity_id": "GTOL-1", "group_id": "G1",
                         "frame_lines": [{"symbol": "位置度", "tolerance": "⌀0.05",
                                          "modifier": "M", "datums": ["A"]}],
                         "target": {"type": "dimension", "entity_id": "DIM-1"},
                         "confidence": 0.9}],
        "datums": [{"entity_id": "DATUM-1", "letter": "A", "target": "底面",
                    "target_entity_id": "", "confidence": 0.9}],
    }]
    return build_confirmation_draft({"sha256": "TEST", "page_count": 1}, pages, [], [])


def main() -> None:
    draft = _draft()
    rows = copy.deepcopy(draft["rows"])
    for row in rows:
        row["confirmed"] = True
        row["status"] = "人工确认"
        if row["entity_id"] == "DIM-FALSE":
            row["excluded"] = True
            row["status"] = "人工排除"
    rows.append({
        "row_id": "CONF-MANUAL-DIM-1", "object_type": "dimension",
        "entity_id": "MANUAL-DIM-1", "page": 1, "confidence": 1.0,
        "status": "人工新增并确认", "kind": "linear", "display": "12±0.1",
        "nominal": "12", "upper": "+0.1", "lower": "-0.1", "fit": "",
        "quantity": "1", "target_feature": "总宽", "confirmed": True,
        "manual_added": True, "excluded": False,
    })
    payload = make_confirmed_payload(draft, rows)
    validate_confirmed_payload(draft, payload)
    pages = apply_confirmed_rows(draft, payload)
    ids = {x["entity_id"] for x in pages[0]["dimensions"]}
    assert "DIM-FALSE" not in ids
    assert "MANUAL-DIM-1" in ids
    assert next(x for x in pages[0]["dimensions"] if x["entity_id"] == "MANUAL-DIM-1")["human_confirmed"]

    missing = copy.deepcopy(payload)
    missing["rows"] = [x for x in missing["rows"] if x["entity_id"] != "DIM-1"]
    try:
        validate_confirmed_payload(draft, missing)
        raise AssertionError("静默丢失原识别行没有被阻断")
    except ValueError as exc:
        assert "遗漏" in str(exc)

    unconfirmed = copy.deepcopy(payload)
    unconfirmed["rows"][0]["confirmed"] = False
    try:
        validate_confirmed_payload(draft, unconfirmed)
        raise AssertionError("未确认行没有被阻断")
    except ValueError as exc:
        assert "未经人工确认" in str(exc)

    missing_datum = copy.deepcopy(payload)
    for row in missing_datum["rows"]:
        if row["object_type"] == "datum":
            row["excluded"] = True
    try:
        validate_confirmed_payload(draft, missing_datum)
        raise AssertionError("不存在基准引用没有被阻断")
    except ValueError as exc:
        assert "基准 A" in str(exc)

    print("PASS confirmation add-exclude-relations-failclosed")


if __name__ == "__main__":
    main()
