# -*- coding: utf-8 -*-
"""
sw_qc_pdf.py — PDF 支持（PyMuPDF 文本 + 矢量提取）

适用场景：甲方只给 PDF、旧图纸扫描后 OCR。

能力边界（重要）
    PDF 是「打印结果」而不是「设计数据」：
      • 文字位置精确，尺寸/公差/粗糙度文本可可靠提取 —— 这是主要价值
      • 矢量线条可提取为线段，可做数量与长度统计
      • 圆弧/圆在 PDF 中是贝塞尔曲线，无法还原为圆心半径 —— 因此
        特征识别（轴承孔/键槽/孔组）在 PDF 输入下基本不可用
    所以 PDF 通道定位为「文本规范检查」，几何类检查以 DXF 为准。
    报告会在 warnings 中显式说明这一点，避免给出虚假结论。
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


import os
import re

from sw_qc_parser import DrawingModel, _extract_tech_notes, _extract_title_block


def parse_pdf(path: str) -> DrawingModel:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf          # 旧版本兼容
        except ImportError as exc:          # pragma: no cover
            raise SystemExit("缺少 PyMuPDF：请运行  pip install pymupdf") from exc

    model = DrawingModel(path=path, fmt="pdf")
    doc = pymupdf.open(path)
    model.acad_version = f"PDF {doc.metadata.get('format', '')}".strip()
    model.warnings.append(
        "PDF 输入：仅做文本类检查（尺寸/公差/粗糙度/标题栏/技术要求）；"
        "圆弧与圆无法从 PDF 还原为几何，几何类检查与特征识别结果不可用。")

    curve_count = 0
    vector_items = 0
    for page_index, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height

        # --- 文本 ---
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    model.texts.append({
                        "text": text,
                        "x": float(x0),
                        "y": float(h - y1),      # 转成「自下而上」坐标系，与 DXF 一致
                        "bbox": [float(v) for v in span["bbox"]],
                        "layer": f"page{page_index}",
                        "kind": "PDFSPAN",
                        "height": float(span.get("size", 0.0)),
                        "page": page_index,
                        "in_title_area": bool(x0 > 0.55 * w and y1 > 0.70 * h),
                    })

        # --- 矢量线条 ---
        try:
            drawings = page.get_drawings()
        except Exception as exc:
            model.warnings.append(f"第 {page_index + 1} 页矢量提取失败：{exc!r}")
            drawings = []
        for d in drawings:
            for item in d.get("items", []):
                vector_items += 1
                if item[0] == "l":                      # 直线
                    p1, p2 = item[1], item[2]
                    model.lines.append({
                        "start": (float(p1.x), float(h - p1.y)),
                        "end": (float(p2.x), float(h - p2.y)),
                        "layer": f"page{page_index}",
                        "linetype": "",
                    })
                elif item[0] == "re":                   # 矩形 → 拆成 4 条线
                    r = item[1]
                    pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                    pts = [(float(x), float(h - y)) for x, y in pts]
                    for i in range(4):
                        model.lines.append({
                            "start": pts[i], "end": pts[(i + 1) % 4],
                            "layer": f"page{page_index}", "linetype": "",
                        })
                elif item[0] == "c":                    # 三次贝塞尔（圆/圆弧/样条）
                    curve_count += 1

    if curve_count:
        model.warnings.append(
            f"跳过 {curve_count} 条贝塞尔曲线（PDF 中的圆/圆弧/样条），未做几何还原")

    model.all_text = "\n".join(t["text"] for t in model.texts if t.get("text"))
    from sw_qc_parser import _looks_like_dimension_text
    model.dimension_texts = [t for t in model.texts
                             if _looks_like_dimension_text(t.get("text", ""))]

    # 判断「文字是否被转成了曲线」：页面里矢量图形很多，却一个字都提不出来。
    # 这种情况下所有文本类检查都无意义，必须显式标记，由上层中止并说明原因。
    if not model.texts and vector_items > 30:
        model.text_unavailable = True
        model.warnings.insert(0, (
            f"该 PDF 没有可提取文字（矢量图形 {vector_items} 个，文字 0 个）："
            f"文字很可能已被转换为曲线。"
            f"材料、图号、尺寸、公差、粗糙度均无法读取，请改用 DXF 原图"
            f"或导出「文字可选中」的 PDF。"))
    _extract_title_block(model)
    _extract_tech_notes(model)
    model.warnings = _dedupe_keep_order(model.warnings)
    return model


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out