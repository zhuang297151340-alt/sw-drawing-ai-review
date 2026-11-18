# -*- coding: utf-8 -*-
"""用本地假 OpenAI 视觉接口测试：识别 → 人工确认 → 专业审核完整 CLI 链路。"""
from __future__ import annotations

import json
import copy
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pdf_audit.confirmation import make_confirmed_payload
from pdf_whole_audit import main


class Handler(BaseHTTPRequestHandler):
    fixture = None
    calls = []
    image_counts = []
    force_recheck = False

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        system = str(payload["messages"][0]["content"])
        self.calls.append(system)
        user = payload["messages"][1]["content"]
        self.image_counts.append(sum(1 for x in user if isinstance(x, dict) and x.get("type") == "image_url"))
        text = "\n".join(str(x.get("text") or "") for x in user if isinstance(x, dict) and x.get("type") == "text")
        if "关键项局部复核员" in system:
            obj = {"results": [{"entity_id": "DIM-1", "resolved": True,
                                "observation": "⌀5", "fact_updates": {"display": "⌀5"},
                                "finding": None}]}
        elif "任务只是读取图纸事实" in system or "同时读取本页图纸并审核" in system:
            m = re.search(r"这是 PDF 第 (\d+) 页", text)
            page = int(m.group(1)) if m else 1
            p = "P%03d" % page
            obj = copy.deepcopy(self.fixture) if self.fixture else {
                "page": page, "drawing_type": "part", "title_block": {"drawing_no": "TEST"},
                "views": [{"entity_id": "VIEW-%d" % page, "name": "主视图",
                           "evidence": {"page": page, "crop_id": p + "-T11"}}],
                "dimensions": [{"entity_id": "DIM-%d" % page, "kind": "diameter", "display": "⌀5",
                                "nominal": "5", "upper": "", "lower": "", "target_feature": "孔",
                                "confidence": 0.7, "evidence": {"page": page, "crop_id": p + "-T11"}}],
                "gtol_frames": [{"entity_id": "GTOL-%d" % page, "group_id": "G1",
                                 "frame_lines": [{"symbol": "位置度", "tolerance": "⌀0.05",
                                                  "modifier": "M", "datums": ["A"]}],
                                 "target": {"type": "dimension", "entity_id": "DIM-%d" % page},
                                 "confidence": 0.8, "evidence": {"page": page, "crop_id": p + "-T11"}}],
                "datums": [{"entity_id": "DATUM-%d" % page, "letter": "A", "target": "底面",
                            "target_entity_id": "DIM-%d" % page, "confidence": 0.8,
                            "evidence": {"page": page, "crop_id": p + "-T21"}}],
                "roughness": [], "holes_threads": [], "surface_process": [], "technical_requirements": [],
                "welds": [], "bom_items": [],
                "regions_read": [p + "-T11", p + "-T12", p + "-T21", p + "-T22"],
                "unreadable_or_uncertain": [],
            }
            obj["page"] = page
            if "同时读取本页图纸并审核" in system:
                if "240 DPI 整页图" in text:
                    obj["regions_read"] = [p + "-FULL"]
                obj.update({"summary": "已审尺寸与几何公差", "issues": [{
                    "severity": "major", "category": "尺寸公差", "reviewed_object": "⌀5 孔",
                    "location": {"page": page, "crop_id": p + "-T11"},
                    "drawing_evidence": "图面标注 ⌀5", "audit_result": "配合孔未明确公差",
                    "risk": "装配间隙可能失控", "basis": "图面尺寸与用途",
                    "suggestion": "按配合要求补充孔径公差", "verification": "核对配合件尺寸链",
                    "entity_ids": ["DIM-1"], "confidence": 0.8}], "manual_review": [],
                            "passed_categories": ["尺寸公差", "几何公差"],
                            "recheck_requests": []})
                if self.force_recheck:
                    obj["recheck_requests"] = [{"entity_id": "DIM-1", "crop_id": "P001-T11",
                                                "reason": "直径符号需局部核对"}]
                    obj["manual_review"] = [{"reviewed_object": "⌀5", "entity_ids": ["DIM-1"],
                                              "audit_result": "直径符号待核对"}]
        elif "关键字段复核员" in system:
            ids = sorted(set(re.findall(r'"entity_id"\s*:\s*"([^"]+)"', text)))
            obj = {"page": 1, "confirmed_entity_ids": ids, "conflicts": []}
        else:
            ids = sorted(set(re.findall(r'"entity_id"\s*:\s*"([^"]+)"', text)))
            pages = sorted(set(int(x) for x in re.findall(r'"page"\s*:\s*(\d+)', text)))
            obj = {"summary": "两阶段自测通过", "reviewed_pages": pages,
                   "reviewed_entity_ids": ids, "issues": [], "manual_review": [],
                   "passed_categories": ["两阶段协议"]}
        body = json.dumps({"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def realistic_fixture(pdf_path: str) -> dict:
    p = "P001"
    common = {
        "page": 1, "drawing_type": "part", "views": [], "gtol_frames": [], "datums": [],
        "welds": [], "bom_items": [],
        "regions_read": [p + "-T11", p + "-T12", p + "-T21", p + "-T22"],
        "unreadable_or_uncertain": [],
    }
    if os.path.basename(pdf_path) == "上侧横梁.pdf":
        common.update({
            "title_block": {"drawing_no": "BDY-005-013-01", "name": "上侧横梁",
                            "material": "SPCC", "scale": "1:5", "version": "A"},
            "views": [
                {"entity_id": "VIEW-P001-001", "name": "主视图", "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "VIEW-P001-002", "name": "端视图", "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "VIEW-P001-003", "name": "侧视图", "evidence": {"page": 1, "crop_id": p + "-T21"}},
            ],
            "dimensions": [
                {"entity_id": "DIM-P001-001", "kind": "linear", "display": "300 +0/-0.1",
                 "nominal": "300", "upper": "0", "lower": "-0.1", "target_feature": "零件总长",
                 "confidence": 0.97, "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-002", "kind": "linear", "display": "40", "nominal": "40",
                 "target_feature": "端面总高", "confidence": 0.96,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-003", "kind": "linear", "display": "18", "nominal": "18",
                 "target_feature": "端面总宽", "confidence": 0.96,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-004", "kind": "linear", "display": "11", "nominal": "11",
                 "target_feature": "端面内宽", "confidence": 0.94,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-005", "kind": "slot", "display": "4.5 × 9.5",
                 "target_feature": "中部长圆孔", "confidence": 0.92,
                 "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-006", "kind": "hole", "display": "2-⌀4.5 × 6.5 对称加工",
                 "nominal": "4.5", "quantity": "2", "target_feature": "端板两孔",
                 "confidence": 0.82, "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-007", "kind": "linear", "display": "2", "nominal": "2",
                 "target_feature": "两端折边宽", "confidence": 0.9,
                 "evidence": {"page": 1, "crop_id": p + "-T21"}},
                {"entity_id": "DIM-P001-008", "kind": "linear", "display": "3", "nominal": "3",
                 "target_feature": "侧视高度", "confidence": 0.9,
                 "evidence": {"page": 1, "crop_id": p + "-T21"}},
            ],
            "roughness": [], "holes_threads": [], "surface_process": [
                {"entity_id": "PROC-P001-001", "text": "表面电镀彩锌，钝化，封闭处理",
                 "evidence": {"page": 1, "crop_id": p + "-T21"}}
            ],
            "technical_requirements": [
                {"entity_id": "TECH-P001-001", "text": "未注线性、角度尺寸公差按 GB/T 1804 m级",
                 "evidence": {"page": 1, "crop_id": p + "-T21"}},
                {"entity_id": "TECH-P001-002", "text": "未注形状和位置公差按 GB/T 1184 K级",
                 "evidence": {"page": 1, "crop_id": p + "-T21"}},
            ],
        })
        return common
    if os.path.basename(pdf_path) == "导向滑板.pdf":
        common.update({
            "title_block": {"drawing_no": "BDY-005-022-01", "name": "导向滑板",
                            "material": "POM（黑色）", "scale": "1:2", "version": "A"},
            "views": [
                {"entity_id": "VIEW-P001-001", "name": "主视图", "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "VIEW-P001-002", "name": "A-A剖视图", "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "VIEW-P001-003", "name": "B-B剖视图", "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "VIEW-P001-004", "name": "C-C剖视图", "evidence": {"page": 1, "crop_id": p + "-T21"}},
            ],
            "dimensions": [
                {"entity_id": "DIM-P001-001", "kind": "linear", "display": "147.4", "nominal": "147.4",
                 "target_feature": "零件总长", "confidence": 0.98,
                 "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-002", "kind": "linear", "display": "48.5", "nominal": "48.5",
                 "target_feature": "零件总宽", "confidence": 0.97,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-003", "kind": "linear", "display": "3", "nominal": "3",
                 "target_feature": "底板厚度", "confidence": 0.96,
                 "evidence": {"page": 1, "crop_id": p + "-T21"}},
                {"entity_id": "DIM-P001-004", "kind": "hole", "display": "6 × ⌀3.4 贯穿，沉孔 ⌀6.3 × 90°",
                 "nominal": "3.4", "quantity": "6", "target_feature": "六个安装孔",
                 "confidence": 0.96, "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-005", "kind": "diameter", "display": "⌀7.5，6处",
                 "nominal": "7.5", "quantity": "6", "target_feature": "六处沉孔外径",
                 "confidence": 0.82, "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-006", "kind": "radius", "display": "4-R3", "nominal": "3",
                 "quantity": "4", "target_feature": "四角圆角", "confidence": 0.98,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
                {"entity_id": "DIM-P001-007", "kind": "linear", "display": "129", "nominal": "129",
                 "target_feature": "端部孔中心跨距", "confidence": 0.95,
                 "evidence": {"page": 1, "crop_id": p + "-T11"}},
                {"entity_id": "DIM-P001-008", "kind": "linear", "display": "34", "nominal": "34",
                 "target_feature": "上下孔中心距", "confidence": 0.95,
                 "evidence": {"page": 1, "crop_id": p + "-T12"}},
            ],
            "roughness": [{"entity_id": "ROUGH-P001-001", "value": "Ra3.2", "scope": "全部",
                           "evidence": {"page": 1, "crop_id": p + "-T12"}}],
            "holes_threads": [], "surface_process": [],
            "technical_requirements": [
                {"entity_id": "TECH-P001-001", "text": "未注线性、角度尺寸公差按 GB/T 1804 m级",
                 "evidence": {"page": 1, "crop_id": p + "-T22"}},
                {"entity_id": "TECH-P001-002", "text": "未注形状和位置公差按 GB/T 1184 K级",
                 "evidence": {"page": 1, "crop_id": p + "-T22"}},
            ],
        })
        return common
    raise ValueError("没有该 PDF 的高拟真模拟数据：" + pdf_path)


def run(pdf_path: str, fixture: dict | None = None) -> None:
    Handler.fixture = fixture
    Handler.calls = []
    Handler.image_counts = []
    Handler.force_recheck = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    old = {k: os.environ.get(k) for k in ("PDF_AI_PROVIDER", "PDF_AI_BASE", "PDF_AI_MODEL", "PDF_AI_KEY")}
    try:
        os.environ["PDF_AI_PROVIDER"] = "custom"
        os.environ["PDF_AI_BASE"] = "http://127.0.0.1:%d/v1" % server.server_port
        os.environ["PDF_AI_MODEL"] = "offline-vision"
        os.environ["PDF_AI_KEY"] = "test"
        with tempfile.TemporaryDirectory(prefix="swqc_pdf_two_phase_") as tmp:
            assert main([pdf_path, "--out", tmp, "--mode", "extract", "--keep-artifacts"]) == 0
            report_path = os.path.join(tmp, "pdf_whole_audit_report.json")
            report = json.load(open(report_path, encoding="utf-8"))
            assert report["status"] == "awaiting_confirmation"
            draft = json.load(open(report["confirmation_path"], encoding="utf-8"))
            assert any(x["status"] == "需确认" for x in draft["rows"])
            for row in draft["rows"]:
                row["confirmed"] = True
                row["status"] = "人工确认"
            confirmed = make_confirmed_payload(draft, draft["rows"])
            confirmed_path = os.path.join(tmp, "pdf_confirmation_confirmed.json")
            with open(confirmed_path, "w", encoding="utf-8") as f:
                json.dump(confirmed, f, ensure_ascii=False)
            assert main([pdf_path, "--out", tmp, "--mode", "audit", "--confirmed", confirmed_path,
                         "--keep-artifacts"]) == 0
            final = json.load(open(report_path, encoding="utf-8"))
            assert final["status"] == "complete", final
            assert final["human_confirmed"] is True
            # 新主流程：无需确认文件，即使默认样例的尺寸置信度只有 0.7 也要出审核报告。
            direct_dir = os.path.join(tmp, "direct")
            before = len(Handler.calls)
            assert main([pdf_path, "--out", direct_dir, "--mode", "full", "--keep-artifacts"]) == 0
            direct = json.load(open(os.path.join(direct_dir, "pdf_whole_audit_report.json"), encoding="utf-8"))
            assert direct["status"] == "complete", direct
            assert direct["human_confirmed"] is False
            assert direct["confirmation_path"] == ""
            assert direct["coverage"]["complete"] is True
            assert len(Handler.calls) - before == 1, "普通单页图纸应只调用视觉模型一次"
            if (direct.get("pages") or [{}])[0].get("analysis_overview"):
                assert Handler.image_counts[-1] == 1, "带文字层的矢量图首轮只提交高分辨率整页图"
            assert direct["performance"]["model_calls"] == 1
            assert direct["performance"]["http_attempts"] == 1
            assert direct["coverage"]["total_entities"] > 0
            assert direct["counts"]["major"] == 1
            assert direct["issues"][0]["suggestion"] == "按配合要求补充孔径公差"
            assert direct["issues"][0]["evidence_image"].endswith("P001-T11.png")
            Handler.force_recheck = True
            before = len(Handler.calls)
            recheck_dir = os.path.join(tmp, "recheck")
            assert main([pdf_path, "--out", recheck_dir, "--mode", "full", "--keep-artifacts"]) == 0
            checked = json.load(open(os.path.join(recheck_dir, "pdf_whole_audit_report.json"), encoding="utf-8"))
            assert len(Handler.calls) - before == 2, "关键疑点应只增加一次局部分区复核"
            assert checked["performance"]["model_calls"] == 2
            assert checked["counts"]["manual_review"] == 0
            print("PASS two-phase", os.path.basename(pdf_path))
    finally:
        server.shutdown(); server.server_close()
        for key, value in old.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("需要测试 PDF 路径")
    use_realistic = "--realistic" in sys.argv[2:]
    run(sys.argv[1], realistic_fixture(sys.argv[1]) if use_realistic else None)
