# -*- coding: utf-8 -*-
"""
inspect_dxf.py — DXF 结构诊断工具

用来回答「为什么工具没认出来这张图的某个信息」这类问题。
它会打印图纸的图层、文字样式（含 SHX/TrueType 字体引用）、实体统计、
全部文字及其坐标，并按「图框区域」单独列出标题栏候选文字。

用法：
    python inspect_dxf.py 图纸.dxf
    python inspect_dxf.py 图纸.dxf --texts        # 打印全部文字
    python inspect_dxf.py 图纸.dxf --region       # 只打印右下角图框区域
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DXF 结构诊断")
    ap.add_argument("drawing")
    ap.add_argument("--texts", action="store_true", help="打印全部文字")
    ap.add_argument("--region", action="store_true", help="只打印图框区域文字")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)

    import ezdxf
    doc = ezdxf.readfile(args.drawing)
    msp = doc.modelspace()

    print("=" * 70)
    print(f"  文件：{os.path.basename(args.drawing)}")
    print("=" * 70)
    print(f"  DXF 版本 : {doc.dxfversion}")
    print(f"  编码     : {doc.encoding}")
    print(f"  单位     : {doc.header.get('$INSUNITS', '(未设置)')}")
    print(f"  图层数   : {len(doc.layers)}")

    counts = Counter(e.dxftype() for e in msp)
    print(f"  实体统计 : {dict(counts)}")

    # ---------------- 文字样式与字体 ----------------
    print("\n--- 文字样式（决定中文能不能显示）---")
    for style in doc.styles:
        name = style.dxf.name
        font = getattr(style.dxf, "font", "") or ""
        bigfont = getattr(style.dxf, "bigfont", "") or ""
        print(f"    {name:20s} font={font!r:30s} bigfont={bigfont!r}")

    # ---------------- 文字实体 ----------------
    texts = []
    for e in msp:
        t = e.dxftype()
        if t == "TEXT":
            texts.append({
                "kind": "TEXT", "text": e.dxf.text,
                "x": float(e.dxf.insert.x), "y": float(e.dxf.insert.y),
                "h": float(getattr(e.dxf, "height", 0) or 0),
                "style": getattr(e.dxf, "style", ""),
                "layer": e.dxf.layer,
            })
        elif t == "MTEXT":
            raw = e.plain_text() if hasattr(e, "plain_text") else e.text
            texts.append({
                "kind": "MTEXT", "text": raw,
                "x": float(e.dxf.insert.x), "y": float(e.dxf.insert.y),
                "h": float(getattr(e.dxf, "char_height", 0) or 0),
                "style": getattr(e.dxf, "style", ""),
                "layer": e.dxf.layer,
            })
        elif t == "DIMENSION":
            try:
                dp = e.dxf.defpoint
                texts.append({
                    "kind": "DIM-TEXT", "text": str(e.dxf.text or "<>"),
                    "x": float(dp.x), "y": float(dp.y), "h": 0.0,
                    "style": "", "layer": e.dxf.layer,
                })
            except Exception:
                pass

    # 图形范围
    if texts:
        xs = [t["x"] for t in texts]
        ys = [t["y"] for t in texts]
        print(f"\n  文字范围 : X {min(xs):.1f} ~ {max(xs):.1f}   "
              f"Y {min(ys):.1f} ~ {max(ys):.1f}")

    print(f"\n--- 文字实体（{len(texts)} 条）---")
    show = texts
    if args.region:
        xs = [t["x"] for t in texts]
        ys = [t["y"] for t in texts]
        if xs and ys:
            x0 = min(xs) + (max(xs) - min(xs)) * 0.55
            y0 = min(ys) + (max(ys) - min(ys)) * 0.60
            show = [t for t in texts if t["x"] >= x0 and t["y"] >= y0]
            print(f"  （筛选 右下角区域 X>={x0:.1f} Y>={y0:.1f}）")

    if args.texts or args.region:
        for t in show[:args.limit]:
            print(f"    [{t['kind']:8s}] ({t['x']:9.2f},{t['y']:9.2f}) "
                  f"h={t['h']:5.2f} style={t['style']:12s} "
                  f"layer={t['layer']:15s} {t['text']!r}")
    else:
        # 默认只打印疑似标题栏/技术要求相关
        keys = ("材料", "比例", "图号", "名称", "代号", "版本", "技术要求",
                "材质", "重量", "数量", "设计", "审核", "批准", "制图")
        hits = [t for t in texts if any(k in (t["text"] or "") for k in keys)]
        for t in hits[:args.limit]:
            print(f"    [{t['kind']:8s}] ({t['x']:9.2f},{t['y']:9.2f}) "
                  f"style={t['style']:12s} {t['text']!r}")
        print(f"\n  （共 {len(hits)} 条疑似标题栏/技术要求文字；用 --texts 看全部）")

    # ---------------- 当前解析器结果 ----------------
    print("\n--- 当前解析器识别到的标题栏 ---")
    from sw_qc_parser import parse_dxf
    model = parse_dxf(args.drawing)
    for k, v in (model.title_block or {}).items():
        print(f"    {k:6s} = {v!r}")
    print(f"    技术要求条目：{len(model.tech_notes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
