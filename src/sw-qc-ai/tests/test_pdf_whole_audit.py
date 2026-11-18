# -*- coding: utf-8 -*-
"""PDF整图通道离线协议测试：不调用收费模型，只验证真实PDF渲染和结构化链路。"""
from __future__ import annotations

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pdf_audit.render import PdfInputError, inspect_and_render
from pdf_audit.confirmation import (apply_confirmed_rows, build_confirmation_draft,
                                    make_confirmed_payload, validate_confirmed_payload)
from pdf_audit.review import (audit_document, collect_all_entity_ids, extract_page,
                              resolve_evidence_paths, verify_critical_fields)


class FakeProvider:
    description = "offline/fake-vision"

    def call_json(self, system, user_text, images=None):
        if "任务只是读取图纸事实" in system:
            m = re.search(r"这是 PDF 第 (\d+) 页", user_text)
            page = int(m.group(1)) if m else 1
            crop = "P%03d-T11" % page
            return {
                "page": page,
                "drawing_type": "part",
                "title_block": {"drawing_no": "SELFTEST", "name": "离线协议测试"},
                "views": [{"entity_id": "VIEW-%d" % page, "name": "主视图",
                           "evidence": {"page": page, "crop_id": crop}}],
                "dimensions": [{"entity_id": "DIM-%d" % page, "kind": "linear", "display": "100",
                                "target_feature": "总长",
                                "evidence": {"page": page, "crop_id": crop}, "confidence": 1.0}],
                "gtol_frames": [], "datums": [], "roughness": [], "holes_threads": [],
                "surface_process": [], "technical_requirements": [], "welds": [], "bom_items": [],
                "regions_read": ["P%03d-T11" % page, "P%03d-T12" % page,
                                 "P%03d-T21" % page, "P%03d-T22" % page],
                "unreadable_or_uncertain": [],
            }
        if "关键字段复核员" in system:
            ids = sorted(set(re.findall(r'"entity_id"\s*:\s*"([^"]+)"', user_text)))
            m = re.search(r"第 (\d+) 页", user_text)
            return {"page": int(m.group(1)) if m else 1,
                    "confirmed_entity_ids": ids, "conflicts": []}
        ids = sorted(set(re.findall(r'"entity_id"\s*:\s*"([^"]+)"', user_text)))
        pages = sorted(set(int(x) for x in re.findall(r'"page"\s*:\s*(\d+)', user_text)))
        return {
            "summary": "离线结构化链路通过",
            "reviewed_pages": pages,
            "reviewed_entity_ids": ids,
            "issues": [{
                "issue_id": "ISSUE-001", "severity": "minor", "category": "自测",
                "reviewed_object": ids[0] if ids else "VIEW-1",
                "location": {"page": pages[0] if pages else 1,
                             "crop_id": "P%03d-T11" % (pages[0] if pages else 1)},
                "drawing_evidence": "离线测试证据", "audit_result": "协议正常",
                "risk": "无", "basis": "测试", "suggestion": "无", "verification": "自动",
                "confidence": 1.0, "entity_ids": ids[:1],
            }],
            "manual_review": [], "passed_categories": ["离线协议"],
        }


def run_one(pdf_path: str) -> None:
    with tempfile.TemporaryDirectory(prefix="swqc_pdf_test_") as tmp:
        meta, artifacts = inspect_and_render(pdf_path, os.path.join(tmp, "pages"))
        assert meta["page_count"] >= 1
        assert len(artifacts) == meta["page_count"]
        assert all(len(p.tiles) == 4 for p in artifacts)
        assert all(os.path.isfile(p.overview) for p in artifacts)
        provider = FakeProvider()
        pages = [extract_page(provider, page) for page in artifacts]
        for page_obj, artifact in zip(pages, artifacts):
            page_obj["critical_verification"] = verify_critical_fields(provider, artifact, page_obj)
            assert not page_obj["critical_verification"]["conflicts"]
        draft = build_confirmation_draft(meta, pages, [], [])
        assert draft["rows"] and draft["dimension_choices"]
        for row in draft["rows"]:
            row["confirmed"] = True
            row["confirmation_source"] = "human"
            if row["object_type"] == "dimension":
                row["display"] = "100±0.1"
                row["upper"] = "+0.1"
                row["lower"] = "-0.1"
        confirmed = make_confirmed_payload(draft, draft["rows"])
        validate_confirmed_payload(draft, confirmed)
        rejected = dict(confirmed)
        rejected["rows"] = [dict(x) for x in confirmed["rows"]]
        rejected["rows"][0]["confirmed"] = False
        try:
            validate_confirmed_payload(draft, rejected)
            raise AssertionError("未确认行必须阻断专业审核")
        except ValueError:
            pass
        pages = apply_confirmed_rows(draft, confirmed)
        assert pages[0]["dimensions"][0]["display"] == "100±0.1"
        assert pages[0]["dimensions"][0]["human_confirmed"] is True
        ids = collect_all_entity_ids(pages)
        assert ids
        reviewed = audit_document(provider, meta, pages, [p.overview for p in artifacts])
        resolve_evidence_paths(reviewed, artifacts)
        assert set(reviewed["reviewed_pages"]) == set(range(1, meta["page_count"] + 1))
        assert set(ids).issubset(set(reviewed["reviewed_entity_ids"]))
        assert reviewed["issues"] and os.path.isfile(reviewed["issues"][0]["evidence_image"])


def main(argv=None) -> int:
    paths = list(argv or sys.argv[1:])
    if not paths:
        print("用法：python test_pdf_whole_audit.py <测试图1.pdf> [测试图2.pdf]")
        return 2
    for path in paths:
        run_one(path)
        print("PASS", os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
