# -*- coding: utf-8 -*-
"""PDF 整图视觉审核：主流程逐页识别并审核，关键疑点再局部复核。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pdf_audit import ENGINE_VERSION
from pdf_audit.confirmation import (apply_confirmed_rows, build_confirmation_draft,
                                    validate_confirmed_payload)
from pdf_audit.providers import OpenAICompatibleVisionProvider, ProviderError
from pdf_audit.render import PdfInputError, inspect_and_render
from pdf_audit.review import (audit_document, collect_all_entity_ids, extract_page,
                              resolve_evidence_paths, verify_critical_fields,
                              analyze_page, recheck_page_questions, _normalize_issue)


def progress(stage: str, message: str, current: int = 0, total: int = 0) -> None:
    print("@@PROGRESS@@" + json.dumps({
        "stage": stage, "message": message, "current": current, "total": total,
    }, ensure_ascii=False), flush=True)


def _load_json(path: str) -> dict:
    if not path or not os.path.isfile(path):
        raise PdfInputError("确认数据不存在：" + str(path or ""))
    with open(path, "r", encoding="utf-8-sig") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise PdfInputError("确认数据格式不正确。")
    return value


def _counts(items: list[dict]) -> dict:
    result = {"critical": 0, "major": 0, "minor": 0, "manual_review": 0}
    for item in items:
        sev = str(item.get("severity") or "")
        if sev in result:
            result[sev] += 1
    return result


def _missing_regions(artifacts, extracted: list[dict], full_mode: bool = False) -> list[str]:
    by_page = {int(p.get("page") or 0): p for p in extracted}
    missing = []
    for artifact in artifacts:
        declared = set(str(x) for x in (by_page.get(artifact.page) or {}).get("regions_read") or [])
        if full_mode and artifact.analysis_overview:
            full_id = "P%03d-FULL" % artifact.page
            if full_id not in declared:
                missing.append(full_id)
            continue
        for tile in artifact.tiles:
            if tile["crop_id"] not in declared:
                missing.append(tile["crop_id"])
    return missing


def _extract_phase(provider, artifacts):
    extracted, failed_pages = [], []
    for index, page in enumerate(artifacts, 1):
        progress("extract", "正在识别第 %d/%d 页的全部标注" % (index, len(artifacts)),
                 index, len(artifacts))
        try:
            extracted.append(extract_page(provider, page))
        except Exception as exc:
            failed_pages.append({"page": page.page, "error": str(exc)})
    if not extracted:
        details = "；".join("第%d页：%s" % (int(x.get("page") or 0), str(x.get("error") or "未知错误"))
                           for x in failed_pages[:3])
        raise ProviderError("全部页面视觉识别失败。" + (" " + details if details else ""))

    progress("verify", "正在核对关键尺寸、几何公差、基准和归属")
    verification_failures, verification_conflicts = [], []
    by_page = {int(p.get("page") or 0): p for p in extracted}
    for artifact in artifacts:
        page_obj = by_page.get(artifact.page)
        if page_obj is None:
            continue
        try:
            verified = verify_critical_fields(provider, artifact, page_obj)
            page_obj["critical_verification"] = verified
            for conflict in verified.get("conflicts") or []:
                row = dict(conflict)
                row["page"] = artifact.page
                verification_conflicts.append(row)
        except Exception as exc:
            verification_failures.append({"page": artifact.page, "error": str(exc)})
    return extracted, failed_pages, verification_conflicts, verification_failures


def _direct_review_phase(provider, artifacts):
    """每页一次视觉识别+审核，仅关键疑点进行一次分区复核。"""
    pages, failed, recheck_failures = [], [], []
    issues, manual, passed, summaries = [], [], [], []
    for index, artifact in enumerate(artifacts, 1):
        progress("review", "正在识别并审核第 %d/%d 页" % (index, len(artifacts)),
                 index, len(artifacts))
        try:
            page = analyze_page(provider, artifact)
        except Exception as exc:
            failed.append({"page": artifact.page, "error": str(exc)})
            continue
        if not collect_all_entity_ids([page]):
            # 模型即使返回了“读完四区”，零对象也不能被当成完整的机械图审核。
            failed.append({"page": artifact.page, "error": "未识别出可核对的图面对象"})
        if page.get("recheck_requests"):
            progress("recheck", "正在复核第 %d 页看不清或冲突的关键项" % artifact.page,
                     index, len(artifacts))
            try:
                page, unresolved = recheck_page_questions(provider, artifact, page)
            except Exception as exc:
                unresolved = page.get("recheck_requests") or []
                recheck_failures.append({"page": artifact.page, "error": str(exc)})
            for question in unresolved:
                entity = str(question.get("entity_id") or "")
                if any(entity in (item.get("entity_ids") or []) for item in page["manual_review"]):
                    continue
                page["manual_review"].append(_normalize_issue({
                    "category": "图面待复核", "reviewed_object": entity or "关键标注",
                    "location": {"page": artifact.page, "crop_id": question.get("crop_id") or ""},
                    "drawing_evidence": str(question.get("reason") or "局部图像仍无法确定"),
                    "audit_result": "关键标注尚未读清，暂不作确定性判错。",
                    "risk": "该处制造或检验要求可能被误解。",
                    "basis": "图面识别不确定", "suggestion": "对照原始 PDF 放大核对该标注。",
                    "verification": "核实尺寸、公差符号和引线归属。", "entity_ids": [entity] if entity else [],
                }, True))
        pages.append(page)
        issues.extend(page["issues"])
        manual.extend(page["manual_review"])
        passed.extend(page["passed_categories"])
        if page["summary"]:
            summaries.append(page["summary"])
    if not pages:
        detail = "；".join("第%d页：%s" % (x["page"], x["error"]) for x in failed[:3])
        raise ProviderError("全部页面视觉审核失败。" + (" " + detail if detail else ""))
    # 同一页同一根因重复出现时只保留一次，跨页同类问题仍分别呈现。
    seen, unique_issues = set(), []
    for item in issues:
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        key = (loc.get("page"), str(item.get("category") or ""),
               str(item.get("reviewed_object") or ""), str(item.get("audit_result") or ""))
        if key not in seen:
            seen.add(key)
            unique_issues.append(item)
    priority = {"critical": 0, "major": 1, "minor": 2}
    unique_issues.sort(key=lambda x: (priority.get(x.get("severity"), 3),
                                      (x.get("location") or {}).get("page") or 0))
    for index, item in enumerate(unique_issues + manual, 1):
        item["issue_id"] = "ISSUE-%03d" % index
    reviewed = {
        "summary": "；".join(summaries[:3]) or "已完成图面逐页审核，详情见问题列表。",
        "reviewed_pages": [p["page"] for p in pages],
        "reviewed_entity_ids": collect_all_entity_ids(pages),
        "issues": unique_issues, "manual_review": manual,
        "passed_categories": list(dict.fromkeys(passed)),
    }
    return pages, failed, recheck_failures, reviewed


def _finalize_audit(report: dict, reviewed: dict, artifacts, extracted: list[dict],
                    failed_pages: list[dict], verification_failures: list[dict],
                    missing_regions: list[str]) -> None:
    report.update(reviewed)
    resolve_evidence_paths(report, artifacts)
    all_ids = collect_all_entity_ids(extracted)
    reviewed_pages = set(int(x) for x in reviewed.get("reviewed_pages") or [] if str(x).isdigit())
    expected_pages = set(range(1, len(artifacts) + 1))
    reviewed_ids = set(str(x) for x in reviewed.get("reviewed_entity_ids") or [])
    missing_ids = [x for x in all_ids if x not in reviewed_ids]
    complete = (not failed_pages and not verification_failures and not missing_regions and
                reviewed_pages == expected_pages and not missing_ids)
    report["coverage"] = {
        "total_pages": len(artifacts), "extracted_pages": len(extracted),
        "reviewed_pages": sorted(reviewed_pages), "total_entities": len(all_ids),
        "reviewed_entities": len(reviewed_ids.intersection(all_ids)),
        "missing_entity_ids": missing_ids, "failed_pages": failed_pages,
        "verification_failures": verification_failures, "missing_regions": missing_regions,
        "complete": complete,
    }
    if missing_ids:
        report["warnings"].append("仍有 %d 个确认对象未被专业审核覆盖。" % len(missing_ids))
    if failed_pages:
        report["warnings"].append("有 %d 页视觉识别失败。" % len(failed_pages))
    if verification_failures:
        report["warnings"].append("有 %d 页关键字段二次复核失败。" % len(verification_failures))
    if missing_regions:
        report["warnings"].append("模型未确认读取 %d 个高分辨率分区。" % len(missing_regions))
    conflicts = sum(len((page.get("critical_verification") or {}).get("conflicts") or [])
                    for page in extracted)
    uncertain = sum(len(page.get("unreadable_or_uncertain") or []) for page in extracted)
    if conflicts:
        report["warnings"].append("关键字段二次识别有 %d 处冲突，请对照原图复核。" % conflicts)
    if uncertain:
        report["warnings"].append("图面有 %d 处看不清或关系不确定，请对照原图复核。" % uncertain)
    report["status"] = "complete" if complete else "incomplete"
    report["counts"] = _counts(list(report.get("issues") or []) +
                                list(report.get("manual_review") or []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PDF 整图视觉审核")
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--expected-pages", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--keep-artifacts", action="store_true")
    ap.add_argument("--mode", choices=("extract", "audit", "full"), default="full")
    ap.add_argument("--confirmed", default="", help="人工确认后的 JSON（mode=audit）")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    pages_dir = os.path.join(args.out, "pages")
    report_path = os.path.join(args.out, "pdf_whole_audit_report.json")
    draft_path = os.path.join(args.out, "pdf_confirmation_draft.json")
    started = time.time()
    render_sec = 0.0
    provider = None
    report = {
        "engine_version": ENGINE_VERSION, "status": "failed", "source": {}, "pages": [],
        "issues": [], "manual_review": [], "summary": "", "coverage": {}, "counts": {},
        "warnings": [], "error": "", "confirmation_path": "", "human_confirmed": False,
    }
    try:
        progress("prepare", "正在检查 PDF 完整性")
        render_started = time.monotonic()
        meta, artifacts = inspect_and_render(args.pdf, pages_dir, args.expected_pages)
        render_sec = time.monotonic() - render_started
        report["source"] = meta
        report["pages"] = [p.public_dict() for p in artifacts]
        progress("render", "PDF 共 %d 页，已完成多尺度渲染" % len(artifacts),
                 len(artifacts), len(artifacts))

        provider = OpenAICompatibleVisionProvider(timeout=args.timeout)
        report["provider"] = provider.description
        if args.mode == "full":
            extracted, failed_pages, verification_failures, reviewed = _direct_review_phase(provider, artifacts)
            missing_regions = _missing_regions(artifacts, extracted, full_mode=True)
            report["extraction"] = {"pages": extracted, "failed_pages": failed_pages}
            _finalize_audit(report, reviewed, artifacts, extracted, failed_pages,
                            verification_failures, missing_regions)
            report["warnings"].insert(0, "AI 对图面作出专业判断；不确定项已单列，工程师可按证据分区复核。")
            progress("report", "正在整理逐页审核报告")
        elif args.mode == "audit":
            draft = _load_json(draft_path)
            confirmed = _load_json(args.confirmed)
            source_hash = str(confirmed.get("source_sha256") or "")
            if not source_hash or source_hash != str(meta.get("sha256") or ""):
                raise PdfInputError("人工确认数据与当前 PDF 不匹配，已停止审核。")
            try:
                validate_confirmed_payload(draft, confirmed)
            except ValueError as exc:
                raise PdfInputError(str(exc))
            extracted = apply_confirmed_rows(draft, confirmed)
            failed_pages = list(draft.get("failed_pages") or [])
            # 二次机器复核失败已由逐行人工确认闭环解决；整页读取失败仍不可放行。
            verification_failures = []
            missing_regions = list(draft.get("missing_regions") or [])
            report["confirmation_path"] = args.confirmed
            report["human_confirmed"] = True
            progress("confirmed", "已载入人工确认数据，正在准备专业审核")
        else:
            extracted, failed_pages, conflicts, verification_failures = _extract_phase(provider, artifacts)
            missing_regions = _missing_regions(artifacts, extracted)
            report["extraction"] = {"pages": extracted, "failed_pages": failed_pages}
            if args.mode == "extract":
                draft = build_confirmation_draft(meta, extracted, conflicts, verification_failures)
                draft["failed_pages"] = failed_pages
                draft["missing_regions"] = missing_regions
                with open(draft_path, "w", encoding="utf-8") as f:
                    json.dump(draft, f, ensure_ascii=False, indent=2)
                report["confirmation_path"] = draft_path
                needs = sum(1 for row in draft.get("rows") or [] if row.get("status") == "需确认")
                report["status"] = "awaiting_confirmation"
                report["summary"] = "系统识别完成：请人工确认尺寸公差、几何公差、基准及归属关系。"
                report["counts"] = {"critical": 0, "major": 0, "minor": 0,
                                    "manual_review": needs}
                report["coverage"] = {
                    "total_pages": len(artifacts), "extracted_pages": len(extracted),
                    "total_entities": len(collect_all_entity_ids(extracted)),
                    "missing_regions": missing_regions, "failed_pages": failed_pages,
                    "verification_failures": verification_failures, "complete": False,
                }
                progress("confirmation", "自动识别完成，等待人工确认")

        if args.mode == "audit":
            progress("review", "正在进行整图专业审核")
            reviewed = audit_document(provider, meta, extracted, [p.overview for p in artifacts])
            _finalize_audit(report, reviewed, artifacts, extracted, failed_pages,
                            verification_failures, missing_regions)
            progress("report", "正在整理结构化审核报告")
        report["elapsed_sec"] = round(time.time() - started, 1)
    except (PdfInputError, ProviderError) as exc:
        report["error"] = str(exc)
        report["summary"] = "PDF 整图审核未完成"
        report["elapsed_sec"] = round(time.time() - started, 1)
    except Exception as exc:
        report["error"] = "程序异常：%s" % exc
        report["debug_error"] = traceback.format_exc(limit=8)
        report["summary"] = "PDF 整图审核异常中止"
        report["elapsed_sec"] = round(time.time() - started, 1)
    finally:
        report["performance"] = {
            "render_sec": round(render_sec, 1),
            "model_calls": getattr(provider, "call_count", 0),
            "http_attempts": getattr(provider, "http_attempts", 0),
            "model_elapsed_sec": round(getattr(provider, "model_elapsed_sec", 0.0), 1),
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("@@REPORT@@" + report_path, flush=True)

    status = report.get("status")
    if status == "awaiting_confirmation":
        return 0
    if status == "complete":
        progress("done", "整图审核完成")
        return 0
    if status == "incomplete":
        progress("incomplete", "已输出审核结果，部分图面或对象未覆盖，请查看覆盖说明")
        return 0
    progress("failed", report.get("error") or "审核失败")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
