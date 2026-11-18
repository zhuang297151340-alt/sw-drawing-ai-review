# -*- coding: utf-8 -*-
"""
sw_qc_render.py — 图纸预览渲染（DXF → PNG）

给报告页顶部提供图纸预览。要点有三个，都是踩坑踩出来的：

1) **中文必须能显示。** 图纸文字样式引用的是「长仿宋体」「仿宋_GB」这类 SHX 字体，
   系统里通常没有对应 TTF。ezdxf 找不到时会回退到内置默认字体，中文会变成方框或空白。
   这里显式检查并换成一个确定含中日韩字形的字体（宋体 / 微软雅黑 / Arial Unicode MS）。

2) **MIF 转义要先还原。** 见 sw_qc_parser._read_dxf：SolidWorks 导出的图纸把中文写成
   ``\\M+1xxxx``，不解码就会在预览里显示成一串 ``\\M+18BA4``。这里直接复用同一套读取逻辑，
   保证「预览看到的」和「检查依据的」是同一份文字。

3) **尺寸要可控。** 按 DPI 渲染，并限制最大像素边长，避免大装配图生成几十 MB 的图。
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


import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 候选中文字体（按优先级）。这些是 Windows 自带、确定含 CJK 字形的字体。
CJK_FONT_CANDIDATES = [
    "msyh.ttc",        # 微软雅黑
    "simsun.ttc",      # 宋体
    "simhei.ttf",      # 黑体
    "simkai.ttf",      # 楷体
    "ARIALUNI.TTF",    # Arial Unicode MS
]

FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]


def find_cjk_font() -> str | None:
    """找一个确实存在的中文字体文件名。"""
    for name in CJK_FONT_CANDIDATES:
        for d in FONT_DIRS:
            if d and os.path.isfile(os.path.join(d, name)):
                return name
    return None


def prepare_doc_for_render(doc) -> list[str]:
    """
    让图纸「渲染得出来中文」。返回处理说明，便于在报告里如实告知做了什么。

    做两件事：
      • 把每个文字样式的字体换成系统里真实存在的中文字体（保留原本能用的）
      • 清掉 bigfont —— bigfont 是 SHX 大字体，TTF 渲染路径用不上
    """
    notes: list[str] = []
    font = find_cjk_font()

    changed = []
    for style in doc.styles:
        name = style.dxf.name
        try:
            current = (style.dxf.font or "").strip()
        except Exception:
            current = ""
        # SHX 字体名（.shx 或无扩展名的中文名）在 TTF 渲染路径下都不可用
        needs_replace = (not current) or current.lower().endswith(".shx") \
            or not current.lower().endswith((".ttf", ".ttc", ".otf"))
        if needs_replace and font:
            style.dxf.font = font
            changed.append(f"{name}:{current or '(空)'}→{font}")
        try:
            if (style.dxf.bigfont or "").strip():
                style.dxf.bigfont = ""
                changed.append(f"{name}:bigfont 已清空")
        except Exception:
            pass

    if changed:
        notes.append("字体替换：" + "；".join(changed[:8])
                     + ("…" if len(changed) > 8 else ""))
    if not font:
        notes.append("未找到中文字体，预览中的中文可能显示为方框")
    return notes


def render_dxf_to_png(path: str, out_path: str | None = None, *,
                      dpi: int = 150, max_px: int = 4000,
                      background: str = "white") -> dict:
    """
    把 DXF 的模型空间渲染成 PNG。

    返回 dict：{png: bytes|None, out_path, width, height, notes, error}
    """
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing.config import (BackgroundPolicy, ColorPolicy,
                                             Configuration)
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    from sw_qc_parser import _read_dxf

    result = {"png": None, "out_path": None, "width": 0, "height": 0,
              "notes": [], "error": None}

    doc, msgs = _read_dxf(path, ezdxf)
    result["notes"].extend(msgs)
    result["notes"].extend(prepare_doc_for_render(doc))

    msp = doc.modelspace()
    if len(msp) == 0:
        # 模型空间为空时退回到第一个有内容的布局
        for lay in doc.layouts:
            if len(lay) > 0:
                msp = lay
                result["notes"].append(f"模型空间为空，改为渲染布局 {lay.name}")
                break
    if len(msp) == 0:
        result["error"] = "图纸中没有任何可渲染的实体"
        return result

    # 审图预览要的是「像打印出来的图纸」：白底黑线。
    # 不指定的话会跟随 DXF 的模型空间配色（通常是黑底彩色线），
    # 在网页里既费墨又看不清，也不符合签审习惯。
    if background == "white":
        config = Configuration(background_policy=BackgroundPolicy.WHITE,
                               color_policy=ColorPolicy.BLACK)
    else:
        config = Configuration()

    backend = PyMuPdfBackend()
    ctx = RenderContext(doc)
    frontend = Frontend(ctx, backend, config=config)
    frontend.draw_layout(msp, finalize=True)

    # 先按目标 DPI 渲染，若像素过大则降 DPI 重来，避免超大图
    page = layout.Page(0, 0)
    used_dpi = dpi
    png = b""
    for _ in range(4):
        png = backend.get_pixmap_bytes(page, fmt="png", dpi=used_dpi, alpha=False)
        if not png:
            break
        w, h = _png_size(png)
        if max(w, h) <= max_px:
            result.update(width=w, height=h)
            break
        used_dpi = int(used_dpi * max_px / max(w, h))
        if used_dpi < 20:
            used_dpi = 20
            break
    if not png:
        result["error"] = "渲染失败（未生成图像数据，可能缺少 PyMuPDF 或 Pillow）"
        return result

    w, h = _png_size(png)
    result["png"] = png
    result["width"], result["height"] = w, h

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(png)
        result["out_path"] = out_path

    result["notes"].append(f"预览渲染 {w}×{h}px @ {used_dpi}dpi")
    return result


def _png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 字节头读出宽高（不依赖 Pillow）。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
    except Exception:
        pass
    return 0, 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DXF 图纸预览渲染")
    ap.add_argument("drawing")
    ap.add_argument("--out", default=None, help="输出 PNG 路径")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--max-px", type=int, default=4000)
    args = ap.parse_args(argv)

    out = args.out or os.path.splitext(args.drawing)[0] + ".preview.png"
    r = render_dxf_to_png(args.drawing, out, dpi=args.dpi, max_px=args.max_px)
    if r["error"]:
        print("渲染失败：", r["error"])
        return 1
    for n in r["notes"]:
        print("  ·", n)
    print(f"  已输出：{r['out_path']}  ({r['width']}×{r['height']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())