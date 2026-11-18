# -*- coding: utf-8 -*-
"""
sw_qc_report.py — 报告生成（Markdown / JSON / Excel）

Markdown 格式严格对齐 `skills/sw-drawing-qc.md` 第八节定义的结构，
使本工具的输出可以直接被免费版 Skill 的报告规范消费、也可直接交付签审。
"""
from __future__ import annotations

# --- 确保模块能找到同目录的兄弟模块
# 便携版 / 嵌入式 Python 带 ._pth 文件时会进入「隔离模式」，
# **不会**自动把脚本所在目录加进 sys.path，于是 import sw_qc_xxx 会失败。
# 这几行让所有入口在任何 Python 环境下都能自洽运行（放在 __future__ 之后）。
import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
# -----------------------------------------------------------------


import datetime
import json
import os

from sw_qc_parser import ENGINE_VERSION
from sw_qc_rules import SEVERITY_LABEL

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
SEVERITY_TAG = {"critical": "S", "major": "M", "minor": "N"}
SEVERITY_COLOR = {"critical": "FFC7CE", "major": "FFEB9C", "minor": "C6EFCE"}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _counts(issues) -> dict:
    c = {"critical": 0, "major": 0, "minor": 0}
    for i in issues:
        c[i.severity] = c.get(i.severity, 0) + 1
    return c


def _sorted(issues):
    return sorted(issues, key=lambda x: (SEVERITY_ORDER.get(x.severity, 9), x.category, x.rule_id))


# ==========================================================================
# JSON
# ==========================================================================
def build_payload(model, features, issues, scores, stats, preset) -> dict:
    # 问题条目带上稳定序号 —— 网页上的「确认无误」按钮靠它指回具体哪一条，
    # 否则前端只能靠数组下标，一旦排序变化就会记错对象。
    sorted_issues = _sorted(issues)
    checks_with_index = []
    for idx, i in enumerate(sorted_issues):
        row = i.as_dict()
        row["index"] = idx
        checks_with_index.append(row)

    suppressed = list(getattr(model, "suppressed_issues", []) or [])

    return {
        "engine_version": ENGINE_VERSION,
        "generated_at": _now(),
        "info": {
            "file": os.path.basename(model.path),
            "format": model.fmt,
            "acad_version": model.acad_version,
            "units": model.units,
            "layers": model.layers,
        },
        "title_block": model.title_block,
        # 每个字段的来源：label / titleblock / inferred。
        # 只有 inferred（离开标准位置、靠弱证据猜的）才需要提示人工确认。
        "title_block_source": dict(getattr(model, "title_block_source", {}) or {}),
        "title_block_inferred": list(getattr(model, "title_block_inferred", []) or []),
        "tech_notes": model.tech_notes,
        # 零件类型：有几条检查只对特定类型成立（如粗糙度对钣金/塑胶件不适用），
        # 所以把判定结果**与依据**一起交出去 —— 用户要能看出该不该接受，并可覆盖。
        "part_type": {
            "type": getattr(model, "part_type", "") or "",
            "confidence": getattr(model, "part_type_confidence", 0.0),
            "evidence": list(getattr(model, "part_type_evidence", []) or []),
            "requested": (getattr(model, "part_type_info", {}) or {}).get("requested", "auto"),
            "overridden": (getattr(model, "part_type_info", {}) or {}).get("overridden", False),
            "scores": (getattr(model, "part_type_info", {}) or {}).get("scores", {}),
        },
        "stats": stats,
        # AI 语义检查：是否跑了、耗时多久、为什么没跑 —— 必须如实交代，
        # 否则用户会以为"没报就是没问题"，而实际可能是 AI 根本没跑。
        "ai": dict(getattr(model, "ai_meta", {}) or {}),
        # 尺寸检查豁免：图上"没标"不等于"漏标"。豁免了哪些检查、凭什么 ——
        # 同样必须展示，不能静默跳过（否则报告的可信度会被怀疑）。
        "dimension_waivers": dict(getattr(model, "dimension_waivers", {}) or {}),
        "dim_data": {
            "count": len(model.dimensions),
            "dimensions": model.dimensions,
        },
        "geometry": {
            "circles": model.circles,
            "arcs": model.arcs,
            "line_count": len(model.lines),
            "center_marks": len(model.center_marks),
        },
        "features": [f.as_dict() for f in features],
        "checks": [i.as_dict() for i in sorted_issues],
        # 网页用它渲染问题表并支持「确认无误」；checks 保持不变以兼容旧调用方
        "checks_with_index": checks_with_index,
        "summary": {
            "total": len(issues),
            **_counts(issues),
        },
        # 被『确认无误』抑制掉的条目：不计分，但要能看到，不做静默隐藏
        "suppressed": {
            "count": len(suppressed),
            "items": suppressed,
            "whitelist_path": str(getattr(model, "whitelist_path", "") or ""),
            "error": str(getattr(model, "whitelist_error", "") or ""),
        },
        "score": scores,
        "rule_preset": preset,
        "tolerance_reference": _tolerance_reference(model),
        "geometry_dimension_map": _geometry_dimension_map(model),
        "drawing_scale": {
            "value": getattr(model, "drawing_scale", 1.0),
            "source": getattr(model, "drawing_scale_source", ""),
        },
        "overall_extents": _overall_extents(model),
        "warnings": model.warnings,
    }


def _overall_extents(model) -> list:
    """推算出零件的外形轮廓尺寸（参考资料）。"""
    try:
        from sw_qc_checks import overall_extents
        return overall_extents(model)
    except Exception:
        return []


def _geometry_dimension_map(model) -> dict:
    """几何要素 ↔ 尺寸标注 对照表（参考资料，用于核对尺寸完整性）。"""
    try:
        from sw_qc_checks import geometry_dimension_map
        rows, scale = geometry_dimension_map(model)
        return {"scale": scale, "rows": rows}
    except Exception:
        return {"scale": 1.0, "rows": []}


def _tolerance_reference(model) -> list:
    """标准公差对照（参考资料，不是问题）。"""
    try:
        from sw_qc_checks import tolerance_reference
        return tolerance_reference(model)
    except Exception:
        return []


def write_json(path, model, features, issues, scores, stats, preset) -> str:
    payload = build_payload(model, features, issues, scores, stats, preset)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    return path


# ==========================================================================
# Markdown（对齐免费版第八节格式）
# ==========================================================================
def write_markdown(path, model, features, issues, scores, stats, preset) -> str:
    tb = model.title_block or {}
    inferred = set(getattr(model, "title_block_inferred", []) or [])
    counts = _counts(issues)
    lines: list[str] = []

    def field(label: str, value) -> str:
        """标题栏字段：没有字段名、靠推断得来的必须标出来。"""
        text = str(value or "").strip()
        if not text:
            return "（未检出）"
        return text + ("　⚠️按图框位置推断，请人工确认" if label in inferred else "")

    lines.append("# 工程图 QC 检查报告")
    lines.append("")
    lines.append("## 基本信息")
    lines.append("")
    lines.append(f"- **图纸名称**：{field('名称', tb.get('名称')) or os.path.basename(model.path)}")
    lines.append(f"- **图纸文件**：`{os.path.basename(model.path)}`")
    lines.append(f"- **图号**：{field('图号', tb.get('图号'))}")
    lines.append(f"- **图纸类型**：{'零件图（推定）' if len(model.dimensions) else '未知'}")
    lines.append(f"- **材料**：{field('材料', tb.get('材料'))}")
    lines.append(f"- **比例**：{field('比例', tb.get('比例'))}")
    if str(tb.get("重量", "")).strip():
        lines.append(f"- **重量**：{tb.get('重量')}")
    # 图纸比例（自动反推）
    sc_info = getattr(model, "drawing_scale", 1.0)
    if sc_info and abs(float(sc_info) - 1.0) > 1e-6:
        lines.append(f"- **图纸比例（自动反推）**：几何尺寸 × {float(sc_info):g} = 实际尺寸"
                     f"　（依据：{getattr(model, 'drawing_scale_source', '')}）")
    lines.append(f"- **视图数量**：依实体统计，共 {stats['line_count']} 条线、"
                 f"{stats['circle_count']} 个圆、{stats['arc_count']} 段弧")
    lines.append(f"- **检查引擎**：{ENGINE_VERSION}")
    lines.append(f"- **规则预设**：`{preset}`")
    lines.append(f"- **检查日期**：{_now()}")
    lines.append("")

    lines.append("## 问题汇总")
    lines.append("")
    lines.append("| 严重程度 | 数量 |")
    lines.append("|----------|------|")
    lines.append(f"| 🔴 严重（影响制造） | {counts['critical']} |")
    lines.append(f"| 🟡 一般（建议修改） | {counts['major']} |")
    lines.append(f"| 🟢 轻微（优化建议） | {counts['minor']} |")
    lines.append(f"| **合计** | **{len(issues)}** |")
    lines.append("")

    # 『确认无误』白名单抑制掉的条目：不计分，但必须如实列出 ——
    # 否则用户看到分数变好了却不知道少报了哪几条，这就是静默隐藏。
    _suppressed = list(getattr(model, "suppressed_issues", []) or [])
    if _suppressed:
        from sw_qc_whitelist import report_suppressed
        lines.append("## 已『确认无误』抑制（不计分）")
        lines.append("")
        lines.append("```text")
        lines.append(report_suppressed(_suppressed))
        lines.append("```")
        lines.append("")
    _wl_err = str(getattr(model, "whitelist_error", "") or "")
    if _wl_err:
        lines.append(f"> ⚠️ {_wl_err}")
        lines.append("")

    idx = {"critical": 0, "major": 0, "minor": 0}
    for sev in ("critical", "major", "minor"):
        group = [i for i in _sorted(issues) if i.severity == sev]
        lines.append(f"## {SEVERITY_LABEL[sev]}")
        lines.append("")
        if not group:
            lines.append("_无_")
            lines.append("")
            continue
        lines.append("| 编号 | 类别 | 位置 | 问题描述 | 判定依据 | 建议 | 标准 |")
        lines.append("|------|------|------|----------|----------|------|------|")
        for i in group:
            idx[sev] += 1
            code = f"{SEVERITY_TAG[sev]}{idx[sev]}"
            detail = (i.detail or i.title).replace("|", "／").replace("\n", " ")
            ev = (i.evidence or "—").replace("|", "／").replace("\n", " ")
            sug = (i.suggestion or "").replace("|", "／").replace("\n", " ")
            lines.append(f"| {code} | {i.category} | {i.location or '—'} | "
                         f"{detail} | {ev} | {sug} | {i.standard or '—'} |")
        lines.append("")

    # 特征识别详情**不再写进报告**（用户实测反馈：对审图工程师没有意义）。
    # 特征仍然照常参与检查（GB-BEARING / GB-KEYWAY / GB-PATTERN / GB-THREAD
    # 这些规则依赖它），只是不再占据报告篇幅 —— 报告只回答
    # 「这张图有没有问题、问题在哪、怎么改」。
    # 需要特征原始数据的场景（脚本/CI）请读 JSON 里的 features 字段。

    # 几何要素 ↔ 尺寸标注 对照
    #
    # 只列**没匹配上的**（未匹配 + 派生尺寸）。用户实测反馈：
    # 「已经匹配到尺寸，你就不用显示，整个报告当然要简单明了」。
    # 全部匹配时整段不出现 —— 没有表就是「圆形要素都标全了」最好的表达。
    gmap = _geometry_dimension_map(model)
    pending = [r for r in gmap["rows"] if not r.get("matched")]
    if pending:
        lines.append("## 尺寸-几何对照（仅列未匹配项）")
        lines.append("")
        lines.append("> 用于核对圆形要素的尺寸完整性。已经匹配到标注的圆不在下表里。"
                     "「未匹配」可能是真漏标，也可能是该要素在其它视图标注。")
        lines.append("")
        lines.append("| 位置 | 几何直径 | 实际直径 | 匹配到的标注 | 状态 |")
        lines.append("|------|----------|----------|--------------|------|")
        for r in pending:
            g = f"{r['geom_diameter']:g}" if r.get("geom_diameter") is not None else "—"
            if r.get("derived"):
                state = f"🔗 派生尺寸（{r['derived']}）"
            elif r.get("orphan_dim"):
                state = "⚠️ 标注无对应几何"
            else:
                state = "🔴 未匹配（疑似漏标）"
            lines.append(f"| ({r['x']}, {r['y']}) | {g} | {r['real_diameter']:g} | "
                         f"{r['matched'] or '—'} | {state} |")
        lines.append("")

    # 标准公差对照（参考资料，不计入问题）
    try:
        from sw_qc_checks import tolerance_reference
        refs = tolerance_reference(model)
    except Exception:
        refs = []
    if refs:
        lines.append("## 标准公差对照（GB/T 1800.1）")
        lines.append("")
        lines.append("> **参考资料**，不是问题。两类尺寸分开看：")
        lines.append("> **配合代号**（H7/g6…）查标准偏差值核对——那是设计者的明确规定；")
        lines.append("> **数值公差**（+0.050/0）**不评判**「对不对应标准等级」，")
        lines.append("> 只说明这个精度**需要什么工艺**。「≈等级」一列只是难度刻度，"
                     "不是合格判定。")
        lines.append("")
        lines.append("| 尺寸标注 | 公称尺寸 | 类型 | 代号/偏差 | 标准值 | ≈等级（难度刻度） | 工艺可达性 |")
        lines.append("|----------|----------|------|-----------|--------|----------|------|")
        for r in refs:
            lines.append(f"| {r['dim']} | {r['nominal']:g} | {r['type']} | "
                         f"{r['code']} | {r['standard'] or '—'} | "
                         f"{r['grade'] or '—'} | {r['note']} |")
        lines.append("")

    # 完整性评分（维度 / 扣分明细）**不再写进报告**。
    #
    # 原因（用户实测反馈 + 本工具的自证）：那个分数**给不出客观结论**。
    # 「尺寸标注完整性」只覆盖圆形要素与外形轮廓，长度、角度、位置尺寸
    # 根本没查（已知限制），可它照样显示 10/10 —— 一张长度尺寸漏标的图
    # 也能拿满分。把这种分数摆在工程师面前，就是"瞎说"。
    #
    # 与其报一个无法追溯的数字，不如只给能站得住的东西：问题清单。
    # 分数仍然保留在 JSON 的 score 字段里，供脚本/CI 做趋势统计用，
    # 但不再作为交付给工程师的结论。

    lines.append("## 优先修改建议")
    lines.append("")
    top = [i for i in _sorted(issues) if i.suggestion][:3]
    if top:
        for n, i in enumerate(top, 1):
            lines.append(f"{n}. **{i.title}** — {i.suggestion}"
                         + (f"（依据 {i.standard}）" if i.standard else ""))
    else:
        lines.append("_未发现需要修改的问题。_")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 限制声明")
    lines.append("")
    lines.append("- 本检查基于图纸中可识别的标注和文本，无法推算隐式几何关系")
    lines.append("- 无法验证材料选择的合理性（需人工判断）")
    lines.append("- 检查结果仅供参考，最终审核仍需由工程师确认")
    lines.append("")

    if model.warnings:
        lines.append("## 解析警告")
        lines.append("")
        for w in model.warnings[:20]:
            lines.append(f"- {w}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


# ==========================================================================
# Excel
# ==========================================================================
def write_excel(path, model, features, issues, scores, stats, preset) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True)

    def style_header(ws, ncols: int) -> None:
        for c in range(1, ncols + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"

    # --- Sheet 1: QC 汇总 ---
    ws = wb.active
    ws.title = "QC汇总"
    ws.append(["工程图 QC 检查报告"])
    ws["A1"].font = Font(bold=True, size=14)
    meta = [
        ["文件", os.path.basename(model.path)],
        ["检查时间", _now()],
        ["引擎版本", ENGINE_VERSION],
        ["规则预设", preset],
        ["图号", (model.title_block or {}).get("图号", "")],
        ["名称", (model.title_block or {}).get("名称", "")],
        ["材料", (model.title_block or {}).get("材料", "")],
        ["比例", (model.title_block or {}).get("比例", "")],
        ["严重问题数", _counts(issues)["critical"]],
        ["一般问题数", _counts(issues)["major"]],
        ["轻微问题数", _counts(issues)["minor"]],
    ]
    for k, v in meta:
        ws.append([k, v])
    ws.append([])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 42

    row0 = ws.max_row + 2
    ws.cell(row=row0, column=1, value="问题明细").font = Font(bold=True, size=12)
    cols = ["编号", "严重程度", "类别", "规则ID", "位置", "问题描述", "判定依据", "建议", "标准"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=ws.max_row, column=c)
        cell.fill = header_fill
        cell.font = header_font

    idx = {"critical": 0, "major": 0, "minor": 0}
    for i in _sorted(issues):
        idx[i.severity] += 1
        ws.append([f"{SEVERITY_TAG[i.severity]}{idx[i.severity]}",
                   {"critical": "严重", "major": "一般", "minor": "轻微"}[i.severity],
                   i.category, i.rule_id, i.location or "", i.detail or i.title,
                   i.evidence or "", i.suggestion or "", i.standard or ""])
        fill = PatternFill("solid", fgColor=SEVERITY_COLOR[i.severity])
        for c in range(1, len(cols) + 1):
            ws.cell(row=ws.max_row, column=c).fill = fill
    for col, width in zip("CDEFGHI", (10, 12, 16, 16, 40, 46, 40, 16)):
        ws.column_dimensions[col].width = width

    # --- Sheet 2: 尺寸统计 ---
    ws2 = wb.create_sheet("尺寸统计")
    ws2.append(["#", "类型", "测量值", "文本覆盖", "上偏差", "下偏差", "图层", "X", "Y"])
    style_header(ws2, 9)
    for n, d in enumerate(model.dimensions, 1):
        loc = d.get("location") or (0, 0)
        ws2.append([n, d.get("dim_type"), d.get("measurement"), d.get("text"),
                    d.get("upper_dev"), d.get("lower_dev"), d.get("layer"),
                    round(loc[0], 2), round(loc[1], 2)])
    for col, width in zip("ABCDEFGHI", (5, 12, 12, 18, 10, 10, 16, 10, 10)):
        ws2.column_dimensions[col].width = width

    # Sheet 3「特征识别」已移除：工程师版本不需要它（用户实测反馈）。
    # 需要特征原始数据请读 JSON 的 features 字段。

    # --- Sheet 3: 标题栏 ---
    ws4 = wb.create_sheet("标题栏")
    ws4.append(["字段", "识别值"])
    style_header(ws4, 2)
    for k, v in (model.title_block or {}).items():
        ws4.append([k, v])
    ws4.append([])
    ws4.append(["技术要求条目", ""])
    for n in model.tech_notes:
        ws4.append(["", n])
    ws4.column_dimensions["A"].width = 22
    ws4.column_dimensions["B"].width = 80

    wb.save(path)
    return path