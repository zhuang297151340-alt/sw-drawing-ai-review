# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from typing import Callable

from .providers import OpenAICompatibleVisionProvider, ProviderError
from .render import PageArtifact


EXTRACT_SYSTEM = r"""
你是机械工程图视觉识别专家。你的任务只是读取图纸事实，不做最终审核。
必须读取给出的整页概览和全部四个高分辨率分区，不得只看标题栏或技术要求。
关键纪律：
1. 不确定就写入 unreadable_or_uncertain，绝不猜测小数点、正负号、直径符号、基准字母。
2. 复合几何公差框必须保持为一个 gtol_frame，frame_lines 按从上到下排列，共用 group_id。
3. 引线箭头明确指向优先于距离；只因靠近不能判定归属。
4. 技术要求按图上的序号逐条拆分，保留原文。
5. 每个对象必须给 evidence.crop_id。crop_id 就是输入图片文件名中的 Pxxx-Txx。
6. 每个尺寸必须先结合引线、尺寸界线、所在视图、相邻孔/轴/槽/台阶和基准体系，自动填写 target_feature；
   同时推断 functional_role（外形包络、装配接口、定位基准、配合/运动、加工工艺、检验/参考、未知）、
   role_basis、role_confidence 和 criticality（high/medium/low）。这是系统工作假设，不要求用户逐项定义。
7. 只有图像本身无法确定尺寸数值或关键归属时才列入不确定项；“尺寸功能需要理解”不能单独成为人工录入理由。
8. 只输出 JSON 对象，不要 Markdown。
"""


AUDIT_SYSTEM = r"""
你是资深机械设计、机械制图、GD&T、制造和质量审核工程师。请审核整份工程图，而不是只做文字纠错。
输入已经由视觉识别器按页结构化。你需要结合标题栏、视图、尺寸、公差、几何公差、基准、粗糙度、孔/螺纹、
材料/热处理/表面处理、技术要求以及装配/BOM信息，识别会影响制造、装配、检测或设计表达的风险。

纪律：
1. 每条问题必须引用输入中真实存在的 entity_id 和 evidence；没有证据不得报确定问题。
2. 不得伪造标准号或条款。没有明确条文时 basis 写“机械制图/制造可执行性原则”。
3. 看不清、关系不唯一、两处识别冲突，放入 manual_review，不要武断判错，也不要当作通过。
4. 复合公差框作为整体审核，不得拆散后改变语义。
5. 建议必须可执行，明确用户应修改什么以及怎样复核。
6. 全面检查标题栏、视图表达、尺寸完整性/一致性、孔螺纹、尺寸公差、GD&T、基准体系、归属、粗糙度、
   材料与工艺、技术要求、可制造性、可检验性；装配图还要检查 BOM/序号球。
7. 只输出 JSON 对象，不要 Markdown。
8. human_confirmed=true 的尺寸、公差、基准及归属是人工确认后的权威事实；必须使用这些字段，
   不得再以“未识别、无被测要素、未指定尺寸”为由制造矛盾。只有确认数据自身确有工程风险时才提出问题。
9. 尺寸的用途/功能应由你结合视图、几何关系、基准、孔轴槽特征和相邻尺寸主动推断；除非关键装配接口存在
   明确冲突且无法从图面消解，否则不得要求用户逐项说明“这个尺寸有什么作用”。
   未人工确认的数据是AI识别事实，按图面证据审核；不可因为缺少人工确认就跳过专业审核。
   critical_verification 中的冲突和 unreadable_or_uncertain 是不确定性证据，请在相关结果里提示工程师复核原图。
10. 输出给老工程师快速决策：合并同类问题，优先制造/装配/检测风险；问题总数原则上不超过20条。
    每条 audit_result、risk、suggestion 各用一句话，禁止长篇复述图纸或泛泛讲标准。
"""


VERIFY_SYSTEM = r"""
你是工程图关键字段复核员。请独立查看高分辨率图像，并核对候选结构中的：尺寸数值、小数点、正负号、
直径/半径/深度/数量符号、几何公差符号与每格内容、实体修饰符、复合框上下顺序、基准字母、箭头归属。
只报告有明确图像证据的冲突；看不清也要列为冲突类型 unreadable。不得做专业风险审核。
只输出 JSON 对象，不要 Markdown。
"""


def _safe_id(prefix: str, page: int, index: int) -> str:
    return "%s-P%03d-%03d" % (prefix, page, index)


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _normalize_page(obj: dict, page: PageArtifact) -> dict:
    out = dict(obj or {})
    out["page"] = page.page
    out.setdefault("sheet_label", "")
    out.setdefault("drawing_type", "unknown")
    out.setdefault("title_block", {})
    out.setdefault("views", [])
    collections = [
        ("dimensions", "DIM"), ("gtol_frames", "GTOL"), ("datums", "DATUM"),
        ("roughness", "ROUGH"), ("holes_threads", "HOLE"),
        ("surface_process", "PROC"), ("technical_requirements", "TECH"),
        ("welds", "WELD"), ("bom_items", "BOM"),
    ]
    for name, prefix in collections:
        rows = _as_list(out.get(name))
        clean = []
        for i, item in enumerate(rows, 1):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("entity_id", _safe_id(prefix, page.page, i))
            ev = row.get("evidence")
            if not isinstance(ev, dict):
                ev = {}
            ev.setdefault("page", page.page)
            ev.setdefault("crop_id", "")
            row["evidence"] = ev
            clean.append(row)
        out[name] = clean
    out["unreadable_or_uncertain"] = _as_list(out.get("unreadable_or_uncertain"))
    out["regions_read"] = _as_list(out.get("regions_read"))
    return out


def extract_page(provider: OpenAICompatibleVisionProvider, page: PageArtifact) -> dict:
    tile_manifest = [
        {"crop_id": t["crop_id"], "page_bbox": t["page_bbox"]}
        for t in page.tiles
    ]
    schema = {
        "page": page.page,
        "sheet_label": "",
        "drawing_type": "part|assembly|weldment|unknown",
        "title_block": {"drawing_no": "", "name": "", "material": "", "revision": "", "scale": "",
                        "unit": "", "projection": "", "sheet": "", "total_sheets": ""},
        "views": [{"entity_id": "VIEW-...", "name": "", "type": "main|section|detail|auxiliary|unknown",
                   "scale": "", "evidence": {"page": page.page, "crop_id": "Pxxx-Txx"}}],
        "dimensions": [{"entity_id": "DIM-...", "kind": "linear|diameter|radius|angle|chamfer|other",
                        "display": "图上完整原文", "nominal": "", "upper": "", "lower": "", "fit": "",
                         "quantity": "", "target_feature": "",
                         "functional_role": "外形包络|装配接口|定位基准|配合/运动|加工工艺|检验/参考|未知",
                         "role_basis": "图面证据与推断理由", "role_confidence": 0.0,
                         "criticality": "high|medium|low",
                         "evidence": {"page": page.page, "crop_id": ""}, "confidence": 0.0}],
        "holes_threads": [],
        "gtol_frames": [{"entity_id": "GTOL-...", "group_id": "GROUP-...",
                         "frame_lines": [{"symbol": "", "tolerance": "", "modifier": "",
                                          "datums": ["A", "B"]}],
                         "target": {"type": "dimension|surface|axis|centerline|unknown", "entity_id": ""},
                         "raw_text": "", "evidence": {"page": page.page, "crop_id": ""}, "confidence": 0.0}],
        "datums": [{"entity_id": "DATUM-...", "letter": "A", "target": "", "target_entity_id": "",
                    "evidence": {"page": page.page, "crop_id": ""}, "confidence": 0.0}],
        "roughness": [], "surface_process": [], "welds": [],
        "technical_requirements": [{"entity_id": "TECH-...", "number": "1", "text": "完整原文",
                                     "evidence": {"page": page.page, "crop_id": ""}}],
        "bom_items": [],
        "regions_read": ["Pxxx-T11", "Pxxx-T12", "Pxxx-T21", "Pxxx-T22"],
        "unreadable_or_uncertain": [{"object": "", "reason": "", "crop_id": ""}],
    }
    user = (
        "这是 PDF 第 %d 页。四个分区清单：%s\n"
        "PDF文字层原文如下。文字层只作辅助，符号、引线和空间关系必须看图：\n---\n%s\n---\n"
        "请完整读取本页并按以下结构返回；允许增加对象字段，但不得删除顶层数组：\n%s"
        % (page.page, json.dumps(tile_manifest, ensure_ascii=False), page.text,
           json.dumps(schema, ensure_ascii=False))
    )
    images = [page.overview] + [t["path"] for t in page.tiles]
    return _normalize_page(provider.call_json(EXTRACT_SYSTEM, user, images), page)


def verify_critical_fields(provider: OpenAICompatibleVisionProvider, page: PageArtifact,
                           extracted_page: dict) -> dict:
    critical = {
        "page": page.page,
        "dimensions": extracted_page.get("dimensions") or [],
        "holes_threads": extracted_page.get("holes_threads") or [],
        "gtol_frames": extracted_page.get("gtol_frames") or [],
        "datums": extracted_page.get("datums") or [],
    }
    schema = {
        "page": page.page,
        "confirmed_entity_ids": [],
        "conflicts": [{
            "entity_id": "", "field": "", "first_read": "", "second_read": "",
            "type": "value_conflict|symbol_conflict|relation_conflict|unreadable",
            "reason": "", "crop_id": "Pxxx-Txx"
        }],
    }
    user = ("请独立核对第 %d 页关键字段。候选结构：\n%s\n返回结构：\n%s"
            % (page.page, json.dumps(critical, ensure_ascii=False),
               json.dumps(schema, ensure_ascii=False)))
    images = [page.overview] + [t["path"] for t in page.tiles]
    obj = provider.call_json(VERIFY_SYSTEM, user, images)
    return {
        "page": page.page,
        "confirmed_entity_ids": _as_list(obj.get("confirmed_entity_ids")),
        "conflicts": [x for x in _as_list(obj.get("conflicts")) if isinstance(x, dict)],
    }


def _compact_document(meta: dict, pages: list[dict]) -> dict:
    return {
        "source": meta,
        "pages": pages,
    }


def _normalize_issue(item: dict, manual: bool = False) -> dict:
    row = dict(item or {})
    row.setdefault("issue_id", "")
    row["severity"] = "manual_review" if manual else str(row.get("severity") or "major").lower()
    if row["severity"] not in ("critical", "major", "minor", "manual_review"):
        row["severity"] = "major"
    for key in ("category", "reviewed_object", "drawing_evidence", "audit_result", "risk",
                "basis", "suggestion", "verification"):
        row[key] = str(row.get(key) or "")
    row.setdefault("location", {})
    row.setdefault("entity_ids", [])
    try:
        row["confidence"] = max(0.0, min(1.0, float(row.get("confidence", 0.0))))
    except Exception:
        row["confidence"] = 0.0
    return row


def audit_document(provider: OpenAICompatibleVisionProvider, meta: dict, pages: list[dict],
                   overview_images: list[str]) -> dict:
    doc = _compact_document(meta, pages)
    expected_pages = [p.get("page") for p in pages]
    output_schema = {
        "summary": "一句话总体评价",
        "reviewed_pages": expected_pages,
        "reviewed_entity_ids": ["所有实际审核过的对象ID"],
        "issues": [{
            "issue_id": "ISSUE-001", "severity": "critical|major|minor", "category": "",
            "reviewed_object": "", "location": {"page": 1, "crop_id": "P001-T11"},
            "drawing_evidence": "图上真实原文/数值", "audit_result": "", "risk": "",
            "basis": "", "suggestion": "", "verification": "", "confidence": 0.0,
            "entity_ids": []
        }],
        "manual_review": [],
        "passed_categories": [],
    }
    user = (
        "请审核下面这份完整工程图结构化事实。reviewed_pages 必须覆盖 %s；"
        "reviewed_entity_ids 必须列出实际审核的对象。\n输入事实：\n%s\n输出结构：\n%s"
        % (expected_pages, json.dumps(doc, ensure_ascii=False), json.dumps(output_schema, ensure_ascii=False))
    )
    obj = provider.call_json(AUDIT_SYSTEM, user, overview_images[:8])
    issues = [_normalize_issue(x, False) for x in _as_list(obj.get("issues")) if isinstance(x, dict)]
    manual = [_normalize_issue(x, True) for x in _as_list(obj.get("manual_review")) if isinstance(x, dict)]
    for i, item in enumerate(issues + manual, 1):
        if not item.get("issue_id"):
            item["issue_id"] = "ISSUE-%03d" % i
    return {
        "summary": str(obj.get("summary") or ""),
        "reviewed_pages": _as_list(obj.get("reviewed_pages")),
        "reviewed_entity_ids": _as_list(obj.get("reviewed_entity_ids")),
        "issues": issues,
        "manual_review": manual,
        "passed_categories": _as_list(obj.get("passed_categories")),
    }


# 主流程：一次视觉调用同时读取本页事实并完成专业审核。旧的提取/人工确认
# 接口保留，仅供已有报告和兼容性测试使用。
PAGE_REVIEW_SYSTEM = r"""
你是一位有制造、装配和检验经验的机械设计审核工程师。请同时读取本页图纸并审核，
必须查看给出的全部图像。矢量 PDF 可能只提供高分辨率整页图与文字层；扫描件提供整页概览和分区。
按引线、尺寸界线和视图确定特征，不能靠邻近位置猜归属。
先建立事实，再判断风险：标题栏、技术要求、每个尺寸及其公差、孔/螺纹、基准、
几何公差框（包括独立、组合多单段、复合和 CZ/SZ 修饰）、粗糙度、材料工艺均要检查。
分类规则：只有一框是独立单框；多个完整框且符号不同是独立公差，框数为实际框数；
多个完整框且符号相同是组合公差，每框独立验证；只有一个几何符号、同一框内分上下层且
下层无独立符号才是复合公差。CZ/SZ 是公差带修饰，单独记录，不改变框类型。
每个 gtol_frames 对象只对应一个有物理边界的完整框；同一标注组的不同框共享 group_id。
每个框应有独立 entity_id、完整 frame_lines 和证据分区。公差框类型应跟具体几何公差走，
不得挂在无关的普通尺寸上。
尺寸的用途结合图面自行推断；不要求工程师逐项定义。无方框的普通未注公差尺寸
不能称为理论正确尺寸；未单独标公差时，还要看标题栏/技术要求是否规定一般公差。
不要把不同表面的粗糙度因数值相同就判为重复；不要以零件属性缺少版本否定图纸标题栏版本。
孔螺纹标注应保留数量、制式、规格、沉头/沉孔和贯穿关系，不能只取第一个直径。
公差框必须按框的物理边界和符号数量分类，不能只凭上下排列分类。

输出是给老工程师快速作决定的：只报有明确图面证据的具体风险；同一根因合并。
每条写清“哪处、图上是什么、会影响什么、怎么改、如何复核”，每项一两短句。
严重=可能直接影响安全/关键装配/关键功能；一般=制造或检验有实质影响；轻微=表达优化。
看不清或关键关系冲突时写入 manual_review 和 recheck_requests，绝不猜数值或伪造标准条款。
没有足够证据时不要把一般性建议写成图纸错误。不得为了凑问题数量而制造问题，
也不得为控制篇幅漏掉不同的实际风险。输出结构里的示例字符串不能照抄成图面事实。
只输出 JSON。
"""


RECHECK_SYSTEM = r"""
你是机械图纸关键项局部复核员。只根据给定高分辨率分区核对列出的关键疑点。
不要重新审核整张图。每项返回能否读清、准确的原文和必要的修正结论。
几何公差疑点须确认物理框边界、每框几何符号及是否为同一框内上下层，不能靠上下位置猜。
看不清就保持未解决，不得推测。若确有工程风险，给出具体证据、影响、修改和复核办法。
只输出 JSON。
"""


def analyze_page(provider: OpenAICompatibleVisionProvider, page: PageArtifact) -> dict:
    manifest = [{"crop_id": t["crop_id"], "page_bbox": t["page_bbox"]} for t in page.tiles]
    full_image = bool(page.analysis_overview)
    full_id = "P%03d-FULL" % page.page
    schema = {
        "page": page.page, "summary": "本页一句话结论", "drawing_type": "part|assembly|unknown",
        "title_block": {"drawing_no": "", "name": "", "material": "", "revision": "", "scale": ""},
        "views": [],
        "dimensions": [{"entity_id": "DIM-P001-001", "display": "图面原文", "nominal": "",
                        "upper": "", "lower": "", "target_feature": "", "functional_role": "",
                        "role_basis": "", "confidence": 0.0,
                        "evidence": {"page": page.page, "crop_id": "Pxxx-Txx"}}],
        "gtol_frames": [{"entity_id": "GTOL-P001-001", "group_id": "同组编号",
                         "frame_type": "独立|组合|复合", "frame_count": 1,
                         "zone_modifier": "CZ|SZ|空", "frame_lines": [], "target": {}, "raw_text": "",
                         "evidence": {"page": page.page, "crop_id": "Pxxx-Txx"}}],
        "datums": [], "roughness": [], "holes_threads": [], "surface_process": [],
        "technical_requirements": [], "welds": [], "bom_items": [],
        "regions_read": [full_id] if full_image else [t["crop_id"] for t in page.tiles],
        "unreadable_or_uncertain": [],
        "issues": [{"severity": "critical|major|minor", "category": "", "reviewed_object": "",
                    "location": {"page": page.page, "crop_id": "Pxxx-Txx"},
                    "drawing_evidence": "图面原文", "audit_result": "明确结论", "risk": "实际影响",
                    "basis": "图面/规则依据", "suggestion": "可执行改法", "verification": "复核办法",
                    "confidence": 0.0, "entity_ids": []}],
        "manual_review": [], "passed_categories": [],
        "recheck_requests": [{"entity_id": "", "crop_id": "Pxxx-Txx", "reason": "关键字段看不清或互相冲突"}],
    }
    user = (
        "这是 PDF 第 %d 页。首轮视觉输入：%s。可用于局部复核的分区位置：%s\n"
        "文字层仅作辅助，符号和引线以图像为准：\n%s\n"
        "请逐项读取并审核本页，按下面的 JSON 结构返回。每个已读对象给稳定 entity_id 和证据分区；"
        "没有问题时 issues 留空。只有关键项看不清或相互冲突才请求局部复核。\n%s"
        % (page.page, "240 DPI 整页图+PDF文字层" if full_image else "整页概览+四个分区图",
           json.dumps(manifest, ensure_ascii=False), page.text[:30000],
           json.dumps(schema, ensure_ascii=False))
    )
    images = ([page.analysis_overview] if full_image else
              [page.overview] + [t.get("analysis_path") or t["path"] for t in page.tiles])
    obj = provider.call_json(PAGE_REVIEW_SYSTEM, user, images,
                             reasoning_effort="low")
    out = _normalize_page(obj, page)
    out["analysis_mode"] = "vector_full_page" if full_image else "overview_and_tiles"
    supplied_regions = {full_id} if full_image else {t["crop_id"] for t in page.tiles}
    out["regions_read"] = [str(x) for x in out["regions_read"] if str(x) in supplied_regions]
    out["issues"] = [_normalize_issue(x) for x in _as_list(obj.get("issues")) if isinstance(x, dict)]
    out["manual_review"] = [_normalize_issue(x, True) for x in _as_list(obj.get("manual_review"))
                            if isinstance(x, dict)]
    evidence_by_id = {}
    for collection in ("views", "dimensions", "gtol_frames", "datums", "roughness",
                       "holes_threads", "surface_process", "technical_requirements", "welds", "bom_items"):
        for fact in out.get(collection) or []:
            evidence_by_id[str(fact.get("entity_id") or "")] = (fact.get("evidence") or {}).get("crop_id")
    for issue in out["issues"] + out["manual_review"]:
        loc = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        loc["page"] = page.page
        if not loc.get("crop_id"):
            for entity in issue.get("entity_ids") or []:
                if evidence_by_id.get(str(entity)):
                    loc["crop_id"] = evidence_by_id[str(entity)]
                    break
        issue["location"] = loc
    out["passed_categories"] = [str(x) for x in _as_list(obj.get("passed_categories")) if x]
    out["recheck_requests"] = [x for x in _as_list(obj.get("recheck_requests")) if isinstance(x, dict)]
    _classify_gtol_groups(out, page, request_recheck=True)
    _separate_unsubstantiated_issues(out, page)
    out["summary"] = str(obj.get("summary") or "")
    return out


def _separate_unsubstantiated_issues(analyzed: dict, page: PageArtifact) -> None:
    """缺少可定位证据的 AI 结论不能冒充已确认的图纸错误。"""
    valid_crops = {t["crop_id"] for t in page.tiles}
    disputed_frames = {str(x.get("entity_id") or "") for x in analyzed.get("recheck_requests") or []
                       if "公差框" in str(x.get("reason") or "")}
    supported = []
    for issue in analyzed.get("issues") or []:
        loc = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        evidence = str(issue.get("drawing_evidence") or "").strip()
        result = str(issue.get("audit_result") or "").strip()
        suggestion = str(issue.get("suggestion") or "").strip()
        weak = (not issue.get("reviewed_object") or not result or not suggestion or
                not evidence or evidence in ("图面原文", "图上真实原文/数值", "图面证据") or
                loc.get("crop_id") not in valid_crops)
        classification_disputed = (any(str(x) in disputed_frames for x in issue.get("entity_ids") or [])
                                   and re.search(r"独立|组合|复合|框数|公差框类型", result) is not None)
        if not weak and not classification_disputed:
            supported.append(issue)
            continue
        issue["severity"] = "manual_review"
        if not issue.get("reviewed_object"):
            issue["reviewed_object"] = "未定位的疑似风险"
        if not evidence:
            issue["drawing_evidence"] = "模型未提供可核对的图面原文"
        issue["audit_result"] = ("公差框类别与物理框结构存在冲突，待局部复核。" if classification_disputed
                                 else "AI 结论缺少可定位图面证据或可执行改法，暂不作为确定问题。")
        issue["suggestion"] = "对照原图核对该标注后再决定是否修改。"
        analyzed["manual_review"].append(issue)
    analyzed["issues"] = supported


def _classify_gtol_groups(analyzed: dict, page: PageArtifact,
                          request_recheck: bool = False) -> None:
    """根据已识别的物理框和符号校正分类；结构不足时标明待复核。"""
    names = ("POSITION", "FLATNESS", "STRAIGHTNESS", "CIRCULARITY", "CYLINDRICITY",
             "PROFILE", "SPROF", "LINEPROF", "SURFPROF", "PARALLELISM", "PERPENDICULARITY",
             "ANGULARITY", "RUNOUT", "TOTALRUNOUT", "SYMMETRY", "CONCENTRICITY",
             "位置度", "平面度", "直线度", "圆度", "圆柱度", "轮廓度", "线轮廓度", "面轮廓度",
             "平行度", "垂直度", "倾斜度", "跳动", "全跳动", "对称度", "同轴度")

    def line_symbol(line) -> str:
        if isinstance(line, dict):
            value = str(line.get("symbol") or "").strip()
        elif isinstance(line, str):
            value = line.split("|", 1)[0].strip()
        else:
            return ""
        if not value or value in ("—", "-", "无"):
            return ""
        if re.match(r"^(?:[+\-±⌀φØ]?[\d.]|DIA\b|MM\b|CZ\b|SZ\b)", value, re.I):
            return ""
        glyphs = ("⌖", "▱", "⌓", "⌒", "⊥", "∥", "○", "◎", "↗", "⌯")
        for glyph in glyphs:
            if value.startswith(glyph):
                return glyph
        first = value.split()[0].upper()
        return first if first in names else ""

    def normalize_lines(frame: dict) -> list:
        rows = _as_list(frame.get("frame_lines"))
        if not rows or not all(isinstance(x, str) for x in rows):
            return rows
        raw = str(frame.get("raw_text") or "")
        joined = "|".join(x.strip() for x in rows)
        compact = lambda x: re.sub(r"\s+", "", x).replace("│", "|")
        # 模型有时把“符号|公差值|基准”四个单元格当成四条 frame_lines。
        # 仅当它们与无换行原文完全对应时，才合并为一个单段框。
        cells = (len(rows) == 1 and "|" in rows[0]) or (
            len(rows) > 1 and raw and "\n" not in raw and compact(joined) == compact(raw))
        if cells:
            parts = [x.strip() for x in (rows[0].split("|") if len(rows) == 1 else rows)]
            return [{"symbol": line_symbol(parts[0]), "tolerance": parts[1] if len(parts) > 1 else "",
                     "datums": parts[2:]}]
        normalized = []
        for row in rows:
            parts = [x.strip() for x in row.split("|")]
            normalized.append({"symbol": line_symbol(parts[0]),
                               "tolerance": parts[1] if len(parts) > 1 else "",
                               "datums": parts[2:]})
        return normalized

    groups = {}
    for index, frame in enumerate(analyzed.get("gtol_frames") or []):
        frame["frame_lines"] = normalize_lines(frame)
        group_id = str(frame.get("group_id") or frame.get("entity_id") or "frame-%d" % index)
        groups.setdefault(group_id, []).append(frame)
    valid_crops = {t["crop_id"] for t in page.tiles}
    for frames in groups.values():
        symbols = []
        ambiguous = False
        for frame in frames:
            present = [x for x in (line_symbol(line) for line in _as_list(frame.get("frame_lines"))) if x]
            if not present:
                ambiguous = True
            symbols.append(present)
        if len(frames) == 1:
            if len(symbols[0]) == 1 and len(_as_list(frames[0].get("frame_lines"))) > 1:
                kind = "复合公差"
            elif len(symbols[0]) == 1 and len(_as_list(frames[0].get("frame_lines"))) == 1:
                kind = "独立公差"
            else:
                kind = ""
                ambiguous = True
        elif not ambiguous and all(len(x) == 1 for x in symbols):
            kind = "组合公差" if len({x[0] for x in symbols}) == 1 else "独立公差"
        else:
            kind = ""
            ambiguous = True
        for frame in frames:
            previous = str(frame.get("frame_type") or "")
            if previous.startswith("独立"):
                previous = "独立公差"
            elif previous.startswith("组合"):
                previous = "组合公差"
            elif previous.startswith("复合"):
                previous = "复合公差"
            frame["frame_count"] = len(frames)
            raw = str(frame.get("raw_text") or "") + " " + " ".join(
                str(x.get("tolerance") or "") if isinstance(x, dict) else str(x)
                for x in _as_list(frame.get("frame_lines")))
            modifier = re.search(r"(?:^|[^A-Z])(CZ|SZ)(?=$|[^A-Z])", raw.upper())
            if modifier:
                frame["zone_modifier"] = modifier.group(1)
            elif frame.get("zone_modifier") not in ("CZ", "SZ"):
                frame["zone_modifier"] = ""
            if kind:
                frame["frame_type"] = kind
            else:
                frame["frame_type"] = "待复核"
            if not request_recheck or (kind and previous in ("", kind)):
                continue
            crop_id = str((frame.get("evidence") or {}).get("crop_id") or "")
            entity_id = str(frame.get("entity_id") or "")
            if crop_id not in valid_crops or not entity_id:
                continue
            reason = ("几何公差框物理边界或符号数未读清" if ambiguous else
                      "几何公差框分类与已识别的物理框/符号结构不一致")
            requests = analyzed.setdefault("recheck_requests", [])
            if not any(str(x.get("entity_id") or "") == entity_id for x in requests):
                requests.append({"entity_id": entity_id, "crop_id": crop_id, "reason": reason})


def recheck_page_questions(provider: OpenAICompatibleVisionProvider, page: PageArtifact,
                           analyzed: dict) -> tuple[dict, list[dict]]:
    tile_map = {t["crop_id"]: t["path"] for t in page.tiles}
    requests = []
    for row in analyzed.get("recheck_requests") or []:
        crop = str(row.get("crop_id") or "")
        entity = str(row.get("entity_id") or "")
        if crop in tile_map and entity and not any(x["entity_id"] == entity for x in requests):
            requests.append({"entity_id": entity, "crop_id": crop,
                             "reason": str(row.get("reason") or "")})
    if not requests:
        return analyzed, []
    # 一页只做一次局部复核；疑点多时同批提交所需分区，避免逐项串行调用。
    crops = list(dict.fromkeys(x["crop_id"] for x in requests))
    user = ("第 %d 页关键疑点：%s\n只返回 JSON：{\"results\":[{\"entity_id\":\"\","
            "\"resolved\":true,\"observation\":\"准确原文\",\"fact_updates\":{},"
            "\"finding\":null}]}。finding 若有风险，字段与 issues 相同；无风险为 null。"
            % (page.page, json.dumps(requests, ensure_ascii=False)))
    obj = provider.call_json(RECHECK_SYSTEM, user, [tile_map[x] for x in crops],
                             reasoning_effort="high")
    results = [x for x in _as_list(obj.get("results")) if isinstance(x, dict)]
    by_id = {str(x.get("entity_id") or ""): x for x in results}
    unresolved = []
    for request in requests:
        entity = request["entity_id"]
        result = by_id.get(entity) or {}
        if result.get("resolved") is not True:
            unresolved.append(request)
            continue
        analyzed["manual_review"] = [x for x in analyzed["manual_review"]
                                     if entity not in (x.get("entity_ids") or [])]
        analyzed["unreadable_or_uncertain"] = [x for x in analyzed["unreadable_or_uncertain"]
            if not isinstance(x, dict) or str(x.get("entity_id") or "") != entity]
        updates = result.get("fact_updates") if isinstance(result.get("fact_updates"), dict) else {}
        for collection in ("dimensions", "gtol_frames", "datums", "holes_threads"):
            for fact in analyzed.get(collection) or []:
                if fact.get("entity_id") == entity:
                    for key in ("display", "nominal", "upper", "lower", "target_feature",
                                "raw_text", "frame_lines", "target", "frame_type", "frame_count",
                                "zone_modifier", "group_id"):
                        if key in updates:
                            fact[key] = updates[key]
        finding = result.get("finding")
        if isinstance(finding, dict) and finding.get("audit_result"):
            issue = _normalize_issue(finding)
            issue["entity_ids"] = [entity]
            issue["location"] = {"page": page.page, "crop_id": request["crop_id"]}
            analyzed["issues"].append(issue)
    _classify_gtol_groups(analyzed, page)
    return analyzed, unresolved


def collect_all_entity_ids(pages: list[dict]) -> list[str]:
    result = []
    names = ("views", "dimensions", "gtol_frames", "datums", "roughness", "holes_threads",
             "surface_process", "technical_requirements", "welds", "bom_items")
    for page in pages:
        for name in names:
            for item in _as_list(page.get(name)):
                if isinstance(item, dict) and item.get("entity_id"):
                    result.append(str(item["entity_id"]))
    return result


def resolve_evidence_paths(report: dict, page_artifacts: list[PageArtifact]) -> None:
    crop_paths = {}
    overview_paths = {}
    for page in page_artifacts:
        overview_paths[page.page] = page.overview
        for tile in page.tiles:
            crop_paths[tile["crop_id"]] = tile["path"]
    for item in list(report.get("issues") or []) + list(report.get("manual_review") or []):
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        crop_id = str(location.get("crop_id") or "")
        page_no = location.get("page")
        item["evidence_image"] = crop_paths.get(crop_id) or overview_paths.get(page_no) or ""

