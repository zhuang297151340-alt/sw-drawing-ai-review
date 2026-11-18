# -*- coding: utf-8 -*-
"""公差框分类回归：物理框数量优先于上下排列，CZ/SZ 不改变框类型。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pdf_audit.render import PageArtifact
from pdf_audit.review import _classify_gtol_groups, _separate_unsubstantiated_issues


PAGE = PageArtifact(1, 100, 100, 0, "", 0, 0, 0, "overview.png", [
    {"crop_id": "P001-T11", "path": "tile.png", "page_bbox": [0, 0, 50, 50]},
], 0)


def frame(entity, group, symbol, typ="", tolerance="0.02"):
    return {"entity_id": entity, "group_id": group, "frame_type": typ,
            "frame_lines": [{"symbol": symbol, "tolerance": tolerance}],
            "evidence": {"page": 1, "crop_id": "P001-T11"}}


def run():
    single = {"gtol_frames": [frame("G1", "A", "⌖", "独立")], "recheck_requests": []}
    _classify_gtol_groups(single, PAGE, True)
    assert single["gtol_frames"][0]["frame_type"] == "独立公差"
    assert single["gtol_frames"][0]["frame_count"] == 1
    assert not single["recheck_requests"]

    separate = {"gtol_frames": [frame("G1", "A", "⌖", "独立"),
                                 frame("G2", "A", "▱", "独立")], "recheck_requests": []}
    _classify_gtol_groups(separate, PAGE, True)
    assert [x["frame_type"] for x in separate["gtol_frames"]] == ["独立公差"] * 2
    assert [x["frame_count"] for x in separate["gtol_frames"]] == [2, 2]
    assert not separate["recheck_requests"]

    combined = {"gtol_frames": [frame("G1", "A", "⌖", "组合"),
                                 frame("G2", "A", "⌖", "组合")], "recheck_requests": []}
    _classify_gtol_groups(combined, PAGE, True)
    assert [x["frame_type"] for x in combined["gtol_frames"]] == ["组合公差"] * 2
    assert combined["gtol_frames"][0]["frame_count"] == 2

    composite_frame = frame("G1", "A", "⌖", "复合")
    composite_frame["frame_lines"].append({"symbol": "", "tolerance": "0.01"})
    composite = {"gtol_frames": [composite_frame], "recheck_requests": []}
    _classify_gtol_groups(composite, PAGE, True)
    assert composite_frame["frame_type"] == "复合公差"
    assert composite_frame["frame_count"] == 1
    assert not composite["recheck_requests"]

    zone = {"gtol_frames": [frame("G1", "A", "⌖", "独立", "0.02CZ")],
            "recheck_requests": []}
    _classify_gtol_groups(zone, PAGE, True)
    assert zone["gtol_frames"][0]["frame_type"] == "独立公差"
    assert zone["gtol_frames"][0]["zone_modifier"] == "CZ"

    wrong = {"gtol_frames": [frame("G1", "A", "⌖", "复合"),
                             frame("G2", "A", "▱", "复合")], "recheck_requests": []}
    _classify_gtol_groups(wrong, PAGE, True)
    assert [x["frame_type"] for x in wrong["gtol_frames"]] == ["独立公差"] * 2
    assert len(wrong["recheck_requests"]) == 2

    merged = frame("G1", "A", "⌖", "组合")
    merged["frame_lines"].append({"symbol": "▱", "tolerance": "0.01"})
    ambiguous = {"gtol_frames": [merged], "recheck_requests": []}
    _classify_gtol_groups(ambiguous, PAGE, True)
    assert merged["frame_type"] == "待复核"
    assert len(ambiguous["recheck_requests"]) == 1

    # 实际 Kimi 返回 frame_lines 为字符串；清楚的单框不能误触发局部复核。
    string_frames = {"gtol_frames": [
        {**frame("G1", "P", "⌖", "独立"),
         "frame_lines": ["POSITION | DIA 0.20 | A | B"], "raw_text": "POSITION | DIA 0.20 | A | B"},
        {**frame("G2", "F", "▱", "独立"),
         "frame_lines": ["FLATNESS | 0.05"], "raw_text": "FLATNESS | 0.05"},
    ], "recheck_requests": []}
    _classify_gtol_groups(string_frames, PAGE, True)
    assert [x["frame_type"] for x in string_frames["gtol_frames"]] == ["独立公差"] * 2
    assert not string_frames["recheck_requests"]

    # 同一公差框的四个单元格也可能被模型误放入 frame_lines 列表。
    cell_frames = {"gtol_frames": [
        {**frame("G1", "P", "⌖", "独立"),
         "frame_lines": ["POSITION", "DIA 0.20", "A", "B"],
         "raw_text": "POSITION | DIA 0.20 | A | B"},
        {**frame("G2", "F", "▱", "独立"),
         "frame_lines": ["FLATNESS", "0.05"], "raw_text": "FLATNESS | 0.05"},
    ], "recheck_requests": []}
    _classify_gtol_groups(cell_frames, PAGE, True)
    assert [x["frame_type"] for x in cell_frames["gtol_frames"]] == ["独立公差"] * 2
    assert all(x["frame_count"] == 1 for x in cell_frames["gtol_frames"])
    assert not cell_frames["recheck_requests"]
    assert cell_frames["gtol_frames"][0]["frame_lines"][0]["datums"] == ["A", "B"]

    quality = {"issues": [
        {"reviewed_object": "尺寸5", "drawing_evidence": "图面原文", "audit_result": "可能有问题",
         "suggestion": "修改", "location": {"page": 1, "crop_id": "P001-T11"}},
        {"reviewed_object": "尺寸6", "drawing_evidence": "图上标注 6±0.1", "audit_result": "上下偏差冲突",
         "suggestion": "核对并统一偏差", "location": {"page": 1, "crop_id": "P001-T11"}},
    ], "manual_review": [], "recheck_requests": []}
    _separate_unsubstantiated_issues(quality, PAGE)
    assert len(quality["issues"]) == 1 and quality["issues"][0]["reviewed_object"] == "尺寸6"
    assert len(quality["manual_review"]) == 1
    assert quality["manual_review"][0]["severity"] == "manual_review"
    print("PASS PDF GD&T classification")


if __name__ == "__main__":
    run()
