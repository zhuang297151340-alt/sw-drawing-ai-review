#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sw_qc_parser.py — 工程图 QC 主入口（DXF / PDF 统一入口 + 报告编排）

对应上游 `skills/sw-drawing-qc-pro.md` 的架构：

    sw_qc_parser.py        ← 本文件：主入口 + 核心解析 + Excel 生成
        ├── sw_qc_gb.py       国标库
        ├── sw_qc_features.py 特征识别
        ├── sw_qc_pdf.py      PDF 支持
        ├── sw_qc_rules.py    规则引擎
        └── sw_qc_checks.py   通用检查（免费版清单的可执行化）

用法（与上游文档一致）：
    python sw_qc_parser.py 图纸.dxf
    python sw_qc_parser.py 图纸.dxf --features
    python sw_qc_parser.py 图纸.dxf --features --rules gb_baseline
    python sw_qc_parser.py 图纸.dxf --features --rules automotive --json --excel
    python sw_qc_parser.py 图纸.pdf --pdf --json
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


import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field

ENGINE_VERSION = "Local Rebuild v0.1 (基于 sw-drawing-qc Pro v2.0 架构文档)"

# 中心线/点划线样式，用于识别中心标记
CENTER_LINETYPES = {"CENTER", "CENTER2", "CENTERX2", "DASHDOT", "DASHDOT2", "点划线"}
CENTER_LAYER_HINT = re.compile(r"center|中心|轴线|点划|axis", re.IGNORECASE)

TITLE_FIELD_ALIASES = {
    "图号": ["图号", "代号", "图纸编号", "drawing no", "drawingno", "part no", "dwg"],
    "名称": ["名称", "零件名称", "品名", "name", "title", "part name"],
    "材料": ["材料", "材质", "material", "matl"],
    "比例": ["比例", "scale"],
    "版本": ["版本", "版次", "rev", "revision", "版本号"],
    "设计": ["设计", "design", "设计者"],
    "审核": ["审核", "校对", "check", "review"],
    "批准": ["批准", "approve", "批准人"],
    "重量": ["重量", "weight", "质量"],
    "数量": ["数量", "qty", "qty."],
}


# ==========================================================================
# 数据模型
# ==========================================================================
@dataclass
class DrawingModel:
    path: str = ""
    fmt: str = "dxf"
    acad_version: str = ""
    units: str = ""
    lines: list = field(default_factory=list)
    circles: list = field(default_factory=list)
    arcs: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    dimension_texts: list = field(default_factory=list)
    center_marks: list = field(default_factory=list)
    layers: list = field(default_factory=list)
    hatch_count: int = 0        # 剖面线数量：>0 说明图中存在剖视图
    hatch_boxes: list = field(default_factory=list)   # 剖面线包围盒（几何坐标）
    # 图纸比例系数：实际尺寸 = 几何尺寸 × drawing_scale
    # 来源是尺寸样式的 dimlfac。图纸若没标比例，这是唯一能自动反推的依据。
    drawing_scale: float = 1.0
    drawing_scale_source: str = ""
    title_block: dict = field(default_factory=dict)
    # 每个字段的来源：label(图上印了字段名) / titleblock(图框右下角标准位置)
    # / inferred(全图兜底，弱证据)。只有 inferred 才需要在报告里提示人工确认。
    title_block_source: dict = field(default_factory=dict)
    # 哪些字段名没找到、是靠弱证据推断出来的（必须如实告知用户）
    title_block_inferred: list = field(default_factory=list)
    tech_notes: list = field(default_factory=list)
    all_text: str = ""
    warnings: list = field(default_factory=list)
    # PDF 文字被转成曲线（无任何可提取文字）时置位：此时一切文本类检查都无意义，
    # 必须明确告知用户，而不是报一堆「缺少材料/缺少图号」的假严重问题。
    text_unavailable: bool = False
    # 『确认无误』白名单抑制掉的问题（不计分，但报告里要如实说明，不静默隐藏）
    suppressed_issues: list = field(default_factory=list)
    whitelist_path: str = ""
    whitelist_error: str = ""
    # 零件类型（机加件/钣金件/塑胶件/组合件）：
    # 有几条检查只对特定类型成立（如「粗糙度是否标注」对钣金/塑胶件不适用），
    # 所以类型必须是显式字段，检查函数据此分档。
    part_type: str = ""
    part_type_confidence: float = 0.0
    part_type_evidence: list = field(default_factory=list)
    part_type_info: dict = field(default_factory=dict)
    # AI 语义检查的执行情况（是否跑了、耗时、为什么没跑）—— 报告要如实说明
    ai_meta: dict = field(default_factory=dict)
    # 尺寸检查的豁免情况（阵列 / 技术要求以3D为准 / 塑胶件）——
    # 豁免不是静默跳过，理由必须展示出来，否则用户会以为"没报就是没问题"
    dimension_waivers: dict = field(default_factory=dict)
    _features: list = field(default_factory=list)

    # -- 统计（供规则引擎 count_threshold 使用）-----------------------------
    def stats(self) -> dict:
        ra_count = len(re.findall(r"\bRa\s*[0-9]", self.all_text or "", re.IGNORECASE))
        dim_texts = self.dimension_texts
        return {
            "dimension_count": len(self.dimensions),
            "dimension_text_count": len(dim_texts),
            # 真实可用尺寸数：DIMENSION 实体 + 炸开成文字的尺寸。
            # 很多 SolidWorks/AutoCAD 导出件与 PDF 只有后者，只看实体会误判「无尺寸」。
            "dimension_total": len(self.dimensions) + len(dim_texts),
            "circle_count": len(self.circles),
            "arc_count": len(self.arcs),
            "line_count": len(self.lines),
            "text_count": len(self.texts),
            "feature_count": len(self._features),
            "ra_count": ra_count,
            "tech_note_count": len(self.tech_notes),
            "title_block_fields": len([v for v in self.title_block.values() if str(v).strip()]),
        }


_DIM_TEXT_RE = re.compile(
    r"^(\d+\s*[×xX*]\s*)?"                        # 阵列前缀：4×
    r"(%%c|φ|Φ|ø|Ø|R|M)?\s*"                      # 直径/半径/螺纹符号
    r"\d+(?:\.\d+)?\s*"                           # 公称尺寸
    r"(?:±\s*\d+(?:\.\d+)?|[+\-]\s*\d+(?:\.\d+)?"
    r"|[HhJjKkMmNnPpRrSsGgFf]\d{1,2})?"           # 极限偏差或公差代号
    r"(?:/\s*[A-Za-z]{1,2}\d{1,2})?"              # 配合写法 /g6
    r"$"
)


def _looks_like_dimension_text(s: str) -> bool:
    """判断一段文字是否是一个尺寸标注（含公差写法），用于炸开图纸的兜底识别。"""
    s = (s or "").strip()
    if not s:
        return False
    return bool(_DIM_TEXT_RE.match(s))


# ==========================================================================
# DXF 解析
# ==========================================================================
def parse_dxf(path: str) -> DrawingModel:
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("缺少 ezdxf：请运行  pip install ezdxf") from exc

    model = DrawingModel(path=path, fmt="dxf")
    doc, recover_msgs = _read_dxf(path, ezdxf)
    model.acad_version = doc.dxfversion
    model.units = str(doc.header.get("$INSUNITS", ""))
    model.layers = [layer.dxf.name for layer in doc.layers]
    model.warnings.extend(recover_msgs)

    msp = doc.modelspace()

    def handle(entity, depth: int = 0, block_name: str = "") -> None:
        if depth > 5:
            return
        etype = entity.dxftype()

        if etype == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            model.lines.append({
                "start": (float(s.x), float(s.y)), "end": (float(e.x), float(e.y)),
                "layer": entity.dxf.layer, "linetype": _linetype(entity),
            })
        elif etype == "CIRCLE":
            c = entity.dxf.center
            model.circles.append({
                "cx": float(c.x), "cy": float(c.y), "r": float(entity.dxf.radius),
                "layer": entity.dxf.layer,
            })
        elif etype == "ARC":
            c = entity.dxf.center
            model.arcs.append({
                "cx": float(c.x), "cy": float(c.y), "r": float(entity.dxf.radius),
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
                "layer": entity.dxf.layer,
            })
        elif etype == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            for i in range(len(pts) - 1):
                model.lines.append({"start": pts[i], "end": pts[i + 1],
                                    "layer": entity.dxf.layer,
                                    "linetype": _linetype(entity)})
            if getattr(entity, "closed", False) and len(pts) > 2:
                model.lines.append({"start": pts[-1], "end": pts[0],
                                    "layer": entity.dxf.layer,
                                    "linetype": _linetype(entity)})
        elif etype == "TEXT":
            p = entity.dxf.insert
            model.texts.append({"text": entity.dxf.text, "x": float(p.x), "y": float(p.y),
                                "layer": entity.dxf.layer, "kind": "TEXT",
                                "block": block_name,
                                "height": float(getattr(entity.dxf, "height", 0) or 0)})
        elif etype == "MTEXT":
            p = entity.dxf.insert
            raw = entity.plain_text() if hasattr(entity, "plain_text") else entity.text
            model.texts.append({"text": raw, "x": float(p.x), "y": float(p.y),
                                "layer": entity.dxf.layer, "kind": "MTEXT",
                                "block": block_name,
                                "height": float(getattr(entity.dxf, "char_height", 0) or 0)})
        elif etype == "DIMENSION":
            model.dimensions.append(_read_dimension(entity))
        elif etype == "HATCH":
            model.hatch_count += 1
            box = _hatch_bbox(entity)
            if box:
                model.hatch_boxes.append(box)
        elif etype == "INSERT":
            try:
                name = str(entity.dxf.name)
                for sub in entity.virtual_entities():
                    handle(sub, depth + 1, block_name=name)
            except Exception as exc:
                model.warnings.append(f"块 {entity.dxf.name} 展开失败：{exc!r}")

    for entity in msp:
        try:
            handle(entity)
        except Exception as exc:
            model.warnings.append(f"{entity.dxftype()} 解析失败：{exc!r}")

    _post_process(model)
    return model


def _read_dxf(path: str, ezdxf_module):
    """
    读取 DXF，优先走 ezdxf 的恢复式读取器，读不动再退回普通读取。

    为什么必须用 recover：SolidWorks / 部分 CAD 导出的 DXF 会把中文写成
    MIF 转义 ``\\M+cxxxx``（c 为代码页，xxxx 为字符的两个字节），例如
    ``\\M+18C8F\\M+1936F`` 其实是「零件」。普通 readfile 不解析这层，
    图纸文字会变成一串 ``\\M+18BA4`` 之类的乱码 —— 标题栏里的「材料/重量」
    就是这样丢掉的。recover 在标签解析阶段就把 MIF 与 ``\\U+xxxx`` 还原了。
    """
    from ezdxf import recover

    msgs: list[str] = []
    try:
        doc, auditor = recover.readfile(path)
        if auditor and auditor.has_errors:
            msgs.append(f"DXF 修复提示：{len(auditor.errors)} 处问题（已自动修复）")
        return doc, msgs
    except Exception as exc:
        msgs.append(f"恢复式读取失败（{exc.__class__.__name__}），改用普通读取")
    return ezdxf_module.readfile(path), msgs


def _hatch_bbox(entity) -> tuple[float, float, float, float] | None:
    """
    取剖面线的包围盒。

    剖面线（HATCH）是**剖视图里被切到的实体区域**，它的范围就等于零件在该
    视图上的实际轮廓范围 —— 这是在没有几何-标注关联的情况下，能拿到的
    最可靠的外形尺寸依据。实测某图剖面线范围 70×48（几何），
    乘 dimlfac 0.5 正好是 35×24，即零件的总长与外径。
    """
    pts: list[tuple[float, float]] = []
    try:
        for path in entity.paths:
            for v in getattr(path, "vertices", []) or []:
                pts.append((float(v[0]), float(v[1])))
            for edge in getattr(path, "edges", []) or []:
                for attr in ("start", "end", "center"):
                    v = getattr(edge, attr, None)
                    if v is not None:
                        pts.append((float(v[0]), float(v[1])))
    except Exception:
        return None
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# 常用图纸比例（几何值 → 实际值的系数）
_COMMON_SCALES = [0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.4, 0.5,
                  1.0, 2.0, 2.5, 4.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0]


def _infer_drawing_scale(model: DrawingModel) -> tuple[float, str]:
    """
    从「几何测量值 → 图上显示值」反推图纸比例系数（实际 = 几何 × 系数）。

    只采信**显示文字是纯数字**的尺寸，比值取中位数（抗个别异常值）。
    若反推值接近某个常用比例就吸附过去，否则原样返回。
    """
    pairs: list[tuple[float, float]] = []
    for d in model.dimensions:
        if not d.get("value_from_display"):
            continue
        m = d.get("measurement")
        v = d.get("value")
        try:
            m, v = float(m), float(v)
        except (TypeError, ValueError):
            continue
        if abs(m) > 1e-6 and v > 0:
            pairs.append((m, v))

    if not pairs:
        return 1.0, "无可用尺寸（按 1:1 处理）"

    ratios = sorted(v / m for m, v in pairs)
    median = ratios[len(ratios) // 2]

    for nice in _COMMON_SCALES:
        if abs(median - nice) <= max(0.02 * nice, 0.005):
            median = nice
            break

    # 与尺寸样式的 dimlfac 交叉印证
    factors = [float(d.get("dimlfac") or 1.0) for d in model.dimensions]
    agree = ""
    if factors:
        mode = max(set(factors), key=factors.count)
        agree = ("，与尺寸样式 dimlfac 一致" if abs(mode - median) <= 0.02 * abs(median)
                 else f"，与尺寸样式 dimlfac={mode:g} 不一致（以实测比值为准）")
    return median, f"由 {len(pairs)} 个尺寸的几何值/标注值比值反推{agree}"


def _linetype(entity) -> str:
    try:
        return str(entity.dxf.linetype or "")
    except Exception:
        return ""


def _dimension_geometry_text(entity) -> str:
    """
    取尺寸「图上真正印出来的文字」。

    DIMENSION 实体自身常常只有一个 `<>`（表示用测量值），真正显示的数字在它的
    几何块（`dxf.geometry` 指向的匿名块，如 `_D`、`_D_1`）里的 TEXT/MTEXT 上。
    这是唯一可靠的显示值来源：直角坐标距离会被 DIMLFAC 之类的比例因子缩放，
    直接拿 get_measurement() 会得到**图上根本不存在的数字**。

    实测：一张图上 `get_measurement()` 返回 28，而图上印的是 14（比例因子 0.5），
    于是工具报出一条「尺寸 28」——用户翻遍图纸都找不到 28。必须以显示文字为准。
    """
    name = ""
    try:
        name = str(entity.dxf.geometry or "")
    except Exception:
        return ""
    if not name:
        return ""
    try:
        block = entity.doc.blocks.get(name)
    except Exception:
        return ""
    parts: list[str] = []
    for sub in block:
        t = sub.dxftype()
        if t == "TEXT":
            parts.append(sub.dxf.text)
        elif t == "MTEXT":
            parts.append(sub.plain_text() if hasattr(sub, "plain_text") else sub.text)
    return "".join(p for p in parts if p).strip()


_NUM_IN_TEXT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

# 螺纹/管螺纹标注不是线性尺寸，不能把里面的数字当成标称值。
# 例如 "11/16-24 UNEF" —— 第一版解析出 11，于是和 ⌀3、14 凑出 3+11≈14，
# 报了一条根本不存在的「封闭尺寸链」。
_THREAD_CALLOUT_RE = re.compile(
    r"\d+\s*/\s*\d+"                                   # 英制分数 11/16、管螺纹 1/2
    r"|UNEF|UNC|UNF|UNS|NPT|BSPT?|Tr\s*\d"            # 螺纹种类代号
    r"|^(?:M|G)\s*\d+(?:\.\d+)?$"                      # 单独出现的 M10 / G1
    r"|^(?:Rc|Rp)\s*\d+",                              # 管螺纹 Rc3/4
    re.IGNORECASE)
# 注意不要把「R0.5」这种圆角半径当成螺纹：R 后跟小数是半径，不是管螺纹。


# 阵列标注「n×φd」：数值是后面的 d，不是前面的个数。
# 第一版按「第一个数字」解析，把 4×φ8 读成 4，于是 4 个螺栓孔全部被判成漏标。
_ARRAY_DIA_RE = re.compile(
    r"\d+\s*[×xX*]\s*(?:%%c|φ|Φ|ø|Ø)\s*(\d+(?:\.\d+)?)")
# 「n×C1」「n×R2」：倒角/圆角数量在前，尺寸在后
_ARRAY_CR_RE = re.compile(
    r"\d+\s*[×xX*]\s*[CR]\s*(\d+(?:\.\d+)?)")


def _parse_display_value(text: str) -> float | None:
    """
    从显示文字里解析标称数值：φ24 → 24，12.7±0.1 → 12.7，%%c3 → 3，
    4×φ8 → 8（阵列标注取直径，不取个数）。
    螺纹标注返回 None（它不是长度尺寸）。
    """
    if not text:
        return None
    s = text.replace("%%c", "φ").replace("%%C", "φ")
    if _THREAD_CALLOUT_RE.search(s):
        return None
    for pattern in (_ARRAY_DIA_RE, _ARRAY_CR_RE):
        m = pattern.search(s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    s = s.replace("φ", "").replace("Φ", "").replace("Ø", "").replace("ø", "")
    m = _NUM_IN_TEXT_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _read_dimension(entity) -> dict:
    """把一个 DIMENSION 实体读成统一的 dict。"""
    dtype = int(getattr(entity.dxf, "dimtype", 0)) & 7
    kind = {0: "linear", 1: "aligned", 2: "angular", 3: "diameter",
            4: "radius", 5: "angular", 6: "ordinate"}.get(dtype, "unknown")

    measurement = None
    try:
        measurement = float(entity.get_measurement())
        if isinstance(measurement, (tuple, list)):
            measurement = float(measurement[0])
    except Exception:
        pass

    override = ""
    try:
        override = str(entity.dxf.text or "")
    except Exception:
        pass

    geometry_text = _dimension_geometry_text(entity)

    # 尺寸样式（DIMSTYLE）先读，因为下面的 value 计算就要用 dimlfac。
    #
    # 这里踩过一个很隐蔽的坑：dimlfac 的赋值原本写在这个函数的**后面**，
    # 而 value 计算在「显示文字解析不出数字」时要乘 dimlfac —— 于是抛
    # UnboundLocalError。异常被模型空间遍历的兜底 except 接住，只留下一条
    # 「DIMENSION 解析失败」警告，**这个尺寸被整条丢掉**。
    # 实测那张真实图纸上丢掉的正是螺纹标注 `11/16-24 UNEF`（文字读不出数字，
    # 必须走兜底分支），16 个尺寸只进来 15 个 —— 螺纹大径的标注在检查里就消失了。
    # 所以这段必须在 value 计算之前。已加回归测试锁住。
    #
    # DXF 里公差通常不写在 DIMENSION 实体上，而是由尺寸样式的 dimtp/dimtm 决定。
    # 实测某图 φ11.2 的 +0.05/0 就来自 SLDDIMSTYLE2。
    # 同一批样式的 dimlfac 也很关键：这张图 dimlfac=0.5，意味着
    # 「几何距离 × 0.5 = 图上标注值」，是尺寸-几何对照的唯一桥梁。
    dimlfac = 1.0
    upper = lower = 0.0
    try:
        upper = float(entity.dxf.get("dimtp", 0) or 0)
        lower = float(entity.dxf.get("dimtm", 0) or 0)
    except Exception:
        pass
    try:
        st = entity.doc.dimstyles.get(entity.dxf.dimstyle)
        if st is not None:
            if not upper and not lower:
                upper = float(st.dxf.get("dimtp", 0) or 0)
                lower = float(st.dxf.get("dimtm", 0) or 0)
            dimlfac = float(st.dxf.get("dimlfac", 1) or 1) or 1.0
    except Exception:
        pass

    # 显示文字优先级：
    #   1) 几何块里的文字 —— 这就是图上真正印出来的内容，最可信
    #   2) 实体文本覆盖，但必须不含 `<>`（`<>` 是「用测量值」的占位符；
    #      `%%c<>` 这种是「直径符号 + 自动测量值」，不能当成最终文字）
    #   3) 都拿不到则留空，由 value 退回测量值
    display = ""
    if geometry_text:
        display = geometry_text
    elif override and "<>" not in override:
        display = override
    display = display.replace("%%c", "φ").replace("%%C", "φ").strip()

    # 图上实际标称的值：能解析显示文字就用它，否则用「测量值 × dimlfac」。
    # 注意必须乘 dimlfac：螺纹标注（11/16-24 UNEF）的显示文字解析不出数字，
    # 这时测量值 34.8 是几何值，乘 0.5 才是实际 17.4；直接用 34.8 会污染后续比对。
    value = _parse_display_value(display)
    value_from_display = value is not None
    if value is None and measurement is not None:
        value = measurement * dimlfac

    # AutoCAD 约定：dimtm 存的是下偏差的绝对值，下偏差 = −dimtm
    if lower > 0:
        lower = -lower

    try:
        dp = entity.dxf.defpoint
        location = (float(dp.x), float(dp.y))
    except Exception:
        location = (0.0, 0.0)

    return {
        "dim_type": kind,
        "measurement": measurement,      # 几何测量值（受比例因子影响，仅供几何核对）
        "value": value,                  # 图上标称值 —— 检查逻辑一律用这个
        "display": display,              # 图上印出来的原文
        "text": display,
        "text_override": override,
        "upper_dev": upper,
        "lower_dev": lower,
        "layer": getattr(entity.dxf, "layer", ""),
        "location": location,
        "dimlfac": dimlfac,
        "value_from_display": value_from_display,
    }


def _post_process(model: DrawingModel) -> None:
    """文本汇总、中心标记识别、标题栏与技术要求提取。"""
    # 图纸比例自动反推。
    #
    # 不能直接信尺寸样式的 dimlfac：ezdxf 自带的 EZDXF 样式 dimlfac=100
    # （为 cm→mm 准备的），照抄会把 ⌀30 算成 ⌀3000 —— 实测踩到过。
    #
    # 可靠做法是**从数据本身反推**：对每个能读出数字的尺寸，
    # 比较「几何测量值」与「图上印出来的值」，取比值的中位数。
    #   实测用户图纸：几何 28 → 图上 14，比值 0.5 ⇒ 实际尺寸 = 几何 × 0.5
    # 再用 dimlfac 作交叉印证。
    scale, source = _infer_drawing_scale(model)
    model.drawing_scale = scale
    model.drawing_scale_source = source
    # all_text 代表「图上印出来的全部文字」，因此除了 TEXT/MTEXT，
    # 还必须包含尺寸标注显示的文字 —— 否则 12.7±0.1 这类公差、
    # ⌀11.2+0.05 这类配合，在文本检查里全都看不见。
    parts = [t["text"] for t in model.texts if t.get("text")]
    for d in model.dimensions:
        if d.get("display"):
            parts.append(d["display"])
    model.all_text = "\n".join(parts)
    # 文字型尺寸：很多 SolidWorks / AutoCAD 导出的 DXF 会把尺寸炸开成
    # 「几何线 + 文字」，此时 DIMENSION 实体为 0。这类文字是检查公差与
    # 尺寸完整性的唯一依据，必须单独收集。
    model.dimension_texts = [t for t in model.texts
                             if _looks_like_dimension_text(t.get("text", ""))]

    # 中心标记：中心线图层上的小圆，或点划线
    for c in model.circles:
        layer = str(c.get("layer", ""))
        if CENTER_LAYER_HINT.search(layer) or c["r"] < 0.6:
            model.center_marks.append(c)
    for ln in model.lines:
        if str(ln.get("linetype", "")).upper() in CENTER_LINETYPES or \
                CENTER_LAYER_HINT.search(str(ln.get("layer", ""))):
            model.center_marks.append({
                "cx": (ln["start"][0] + ln["end"][0]) / 2,
                "cy": (ln["start"][1] + ln["end"][1]) / 2,
                "r": 0.0, "layer": ln.get("layer", ""),
            })

    _extract_title_block(model)
    _extract_tech_notes(model)


def _extract_title_block(model: DrawingModel) -> None:
    """
    标题栏提取（两步，缺一不可）。

    第一步：标签配对。
        在真实图纸里，字段名与值往往在**不同单元格**：常见两种排布
        「标签在左、值在右」和「标签在上、值在下」。所以先按几何位置配对，
        并且明确规定：**标签本身不能当作值**、中间隔着别的标签就不能跨越。
        第一版只做「向右取最近文本」，结果把 重量→比例、设计→标准化、
        批准→共 张 第 张 全配错了 —— 就是没做这两条约束。

    第二步：无标签字段的推断（本图正是这种情况）。
        用户这张 SolidWorks 图纸的标题栏里**根本没有「材料」两个字**
        （已对全图 91 个 MIF 转义逐字解码确认：材、料 二字从未出现），
        2A12 直接写在一个无标签的格子里。对这类图，只能靠
        「图框区域 + 牌号/编号/名称的书写规律」推断，并且**必须如实标注是推断值**，
        不能假装是读到的字段。
    """
    texts = [t for t in model.texts if (t.get("text") or "").strip()]
    tb: dict[str, str] = {}
    source: dict[str, str] = {}
    inferred: set[str] = set()

    # ---------- 第一步：标签配对 ----------
    labels: list[tuple[dict, str, str]] = []      # (文字, 字段, 同行残留值)
    for t in texts:
        raw = t["text"].strip()
        field, rest = _match_title_label(raw)
        if field:
            labels.append((t, field, rest))
            if rest and field not in tb:
                tb[field] = rest                 # 『材料 45』这种同行写法
                source[field] = "label"

    label_positions = {(round(t["x"], 2), round(t["y"], 2)) for t, _, _ in labels}

    for t, field, _rest in labels:
        if field in tb:
            continue
        value = _pair_value(texts, t, label_positions)
        if value:
            tb[field] = value
            source[field] = "label"

    # ---------- 第二步：图框右下角区域识别 ----------
    # 这里是制图标准规定的固定位置，在标准位置上按书写规律识别出来的结果
    # 属于**正常识别**，不是猜测，因此不标「推断」。
    # 用户明确要求：图框右下角本来就该写名称/材料/图号，读到就是读到了；
    # 只有图里根本没有这些文字时，才应该报严重问题。
    region = _title_block_region(model)
    if region:
        region_texts = [t for t in texts
                        if region[0] <= t["x"] <= region[2]
                        and region[1] <= t["y"] <= region[3]]
        for field, value in _infer_title_fields(region_texts, tb).items():
            if not str(tb.get(field, "")).strip() and value:
                tb[field] = value
                source[field] = "titleblock"

    # ---------- 第三步：全图兜底（弱证据，才需要标「推断」）----------
    if not str(tb.get("材料", "")).strip():
        value = _scan_material_anywhere(texts, region)
        if value:
            tb["材料"] = value
            source["材料"] = "inferred"
            inferred.add("材料")

    for field in TITLE_FIELD_ALIASES:
        tb.setdefault(field, "")

    model.title_block = tb
    model.title_block_source = source
    model.title_block_inferred = sorted(inferred)


def _scan_material_anywhere(texts: list[dict], region) -> str:
    """
    全图兜底找材料牌号 —— 只有图框右下角找不到时才走这条路。
    因为离开了标题栏这个固定位置，证据就弱了，结果会被标成「推断」。
    要求：全图只有一个候选才敢认，多个候选直接放弃（不猜）。
    """
    candidates = []
    for t in texts:
        s = (t.get("text") or "").strip()
        if not s or len(s) > 24:
            continue
        if region and (region[0] <= t["x"] <= region[2]
                       and region[1] <= t["y"] <= region[3]):
            continue                     # 标题栏里的已经在第二步处理过
        if _match_title_label(s)[0] or s in TITLE_LABEL_WORDS:
            continue
        if is_material_grade(s):
            candidates.append(s)
    return candidates[0] if len(candidates) == 1 else ""


def _match_title_label(raw: str) -> tuple[str | None, str]:
    """
    判断一段文字是不是标题栏标签。返回 (字段名, 同行残留值)。
    要求整串就是标签（或标签开头紧跟值），避免「零件代号」被「代号」误命中。
    """
    s = raw.strip()
    low = s.lower()
    for field, aliases in TITLE_FIELD_ALIASES.items():
        for alias in aliases:
            a = alias.lower()
            if low == a:
                return field, ""
            if low.startswith(a):
                rest = s[len(alias):].strip(" :：\t")
                # 残留值不能又是另一个标签（否则说明只是共享前缀）
                if rest and len(rest) <= 40 and not _match_title_label(rest)[0]:
                    return field, rest
    return None, ""


TITLE_LABEL_WORDS = {
    "设计", "校对", "审核", "工艺", "标准化", "批准", "质量", "描图", "描校", "制图",
    "重量", "比例", "材料", "材质", "名称", "图号", "代号", "零件代号", "图样代号",
    "阶段标记", "标记", "处数", "分区", "更改文件号", "签名", "日期", "底图总号",
    "旧底图总号", "数量", "版本", "版次", "技术要求", "其余", "借(通)用件登记",
    "当前图纸", "图纸总和", "图纸比例", "件号", "序号", "备注", "共", "第", "张",
    "产品型号", "图样名称", "单位", "归档", "密级",
}


def _pair_value(texts: list[dict], label: dict,
                label_positions: set[tuple[float, float]],
                max_gap: float = 30.0) -> str:
    """
    为一个标签找它的值：先看同一行右侧，再看同一列下方。

    两条硬约束（第一版就栽在这里）：
      • 候选值本身是标签 → 拒绝（否则 重量 会配到 比例）
      • 中间隔着别的标签 → 拒绝（否则 批准 会跨格配到 共 张 第 张）
    """
    lx, ly = label["x"], label["y"]
    lh = float(label.get("height") or 3.5)
    row_tol = max(lh * 0.8, 2.0)
    col_tol = max(lh * 1.6, 6.0)

    def is_label(t: dict) -> bool:
        s = (t.get("text") or "").strip()
        return bool(_match_title_label(s)[0]) or s in TITLE_LABEL_WORDS

    def blocked(t: dict) -> bool:
        """标签与候选之间是否横着另一个标签。"""
        tx, ty = t["x"], t["y"]
        for ox, oy in label_positions:
            if abs(oy - ly) > row_tol:
                continue
            if lx < ox < tx:
                return True
        return False

    # 同一行，向右
    row = [t for t in texts
           if t is not label and abs(t["y"] - ly) <= row_tol
           and 0 < t["x"] - lx <= max_gap
           and not is_label(t) and not blocked(t)]
    row.sort(key=lambda t: t["x"] - lx)

    # 同一列，向下（中文标题栏常见「标签在上一格、值在下一格」）
    col = [t for t in texts
           if t is not label and abs(t["x"] - lx) <= col_tol
           and 0 < ly - t["y"] <= max_gap
           and not is_label(t)]
    col.sort(key=lambda t: ly - t["y"])

    if row:
        return (row[0].get("text") or "").strip()
    if col:
        return (col[0].get("text") or "").strip()
    return ""


def _title_block_region(model: DrawingModel):
    """
    估计标题栏区域：图幅的右下角。

    这是**制图标准规定的固定位置**，与图纸幅面无关：
    GB/T 10609.1 的标题栏一律放在图框右下角，标准幅面下约占
    图幅宽度的 60%、高度的 27%（A4 横放 297×210 对应标题栏 180×56）。
    A0~A4 比例基本一致，所以按比例取右下角，比按"图幅四分之一"之类的
    固定比例稳得多，也不受零件画在哪个位置影响。

    返回 (x0, y0, x1, y1)。
    """
    xs: list[float] = []
    ys: list[float] = []
    for ln in model.lines:
        xs += [ln["start"][0], ln["end"][0]]
        ys += [ln["start"][1], ln["end"][1]]
    for c in model.circles:
        xs += [c["cx"] - c["r"], c["cx"] + c["r"]]
        ys += [c["cy"] - c["r"], c["cy"] + c["r"]]
    for a in model.arcs:
        xs += [a["cx"] - a["r"], a["cx"] + a["r"]]
        ys += [a["cy"] - a["r"], a["cy"] + a["r"]]
    for t in model.texts:
        xs.append(t["x"])
        ys.append(t["y"])
    if len(xs) < 4 or len(ys) < 4:
        return None
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    w, h = xmax - xmin, ymax - ymin
    if w < 1e-6 or h < 1e-6:
        return None

    # 右下角：宽取 62%，高取 30%（含图框线的边界余量）
    tb_w = min(0.62 * w, w)
    tb_h = min(0.30 * h, h)
    return (xmax - tb_w, ymin, xmax, ymin + tb_h)


# 常见材料牌号（GB 钢 / 铝 / 不锈钢 / 铸件 / 铜 / 塑料）
MATERIAL_PATTERNS = [
    r"^[0-9]{2}#?$",                       # 45 / 20 / 45#
    r"^Q[0-9]{3}[A-Z]?$",                  # Q235 / Q345B
    r"^[0-9]{2}(Cr|Mn|Si|Ni|Mo)[A-Za-z0-9]*$",   # 40Cr / 20CrMnTi / 65Mn
    r"^T[0-9]{1,2}[A-Z]?$",                # T8A
    r"^GCr15$",
    r"^[0-9]A[0-9]{2}$",                   # 2A12 / 5A06 / 7A04
    r"^(LY|LF|LD|ZL)[0-9]{2,3}$",          # LY12 / LF21 / ZL104
    r"^[0-9]{4}(-T[0-9]+)?$",              # 6061 / 7075 / 2024
    r"^[0-9]{3}L?$",                       # 304 / 316 / 316L
    r"^[0-9]{1,2}Cr[0-9]{2}Ni[0-9]{1,2}(Ti)?$",   # 1Cr18Ni9Ti
    r"^0Cr[0-9]{2}Ni[0-9]{1,2}$",
    r"^HT[0-9]{3}$", r"^QT[0-9]{3}-?[0-9]{0,2}$", r"^ZG[0-9]{3}-[0-9]{3}$",
    r"^H[0-9]{2}$", r"^T[0-9]$", r"^Q[A-Za-z]?[0-9.]+-[0-9.]+$",   # H62 / T2 / QSn6.5-0.1
    r"^(ABS|POM|PA6|PA66|PC|PEEK|PTFE|PVC|PMMA|PP|PE|PBT|PET)$",
]
_MATERIAL_RES = [re.compile(p) for p in MATERIAL_PATTERNS]
# 图号样式：以字母开头、含字母数字、允许 . - / _
_DRAWING_NO_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._\-/]{4,}$")
# 公司名/单位名，不是零件名称
_COMPANY_HINT = re.compile(r"公司|有限|责任|集团|厂$|研究所|检测|技术有限|大学|学院")
_LABEL_LIKE = re.compile(r"[:：。;；]$")


def is_material_grade(text: str) -> bool:
    """判断一段文字是否像材料牌号。"""
    s = (text or "").strip()
    if not s or len(s) > 24:
        return False
    return any(r.match(s) for r in _MATERIAL_RES)


def _infer_title_fields(region_texts: list[dict], tb: dict) -> dict:
    """
    在没有字段名的标题栏里，按书写规律推断 材料 / 图号 / 名称。
    只在标签法失败时用作兜底，调用方会把结果标记为「推断」。
    """
    out: dict[str, str] = {}
    cands = []
    for t in region_texts:
        s = (t.get("text") or "").strip()
        if not s or _match_title_label(s)[0] or s in TITLE_LABEL_WORDS:
            continue
        cands.append((s, t))

    # 材料：唯一的牌号候选才敢认，多个候选就放弃（避免猜错）
    mats = [s for s, _t in cands if is_material_grade(s)]
    if len(mats) == 1:
        out["材料"] = mats[0]

    # 图号：以字母开头的编码
    for s, _t in cands:
        if _DRAWING_NO_RE.match(s) and not is_material_grade(s):
            out["图号"] = s
            break

    # 名称：中文短语、排除公司名、取字号最大的那个
    name_cands = []
    for s, t in cands:
        if not re.search(r"[\u4e00-\u9fa5]", s):
            continue
        if _COMPANY_HINT.search(s) or _LABEL_LIKE.search(s):
            continue
        if len(s) < 2 or len(s) > 24:
            continue
        name_cands.append((float(t.get("height") or 0), s))
    if name_cands:
        name_cands.sort(reverse=True)
        out["名称"] = name_cands[0][1]
    return out


def _extract_tech_notes(model: DrawingModel) -> None:
    """提取『技术要求』区块及其后若干行文本。"""
    notes: list[str] = []
    lines = [t for t in model.texts if (t.get("text") or "").strip()]
    for i, t in enumerate(lines):
        s = (t.get("text") or "").strip()
        if "技术要求" in s or "技术条件" in s or re.match(r"^技术\s*要求", s):
            rest = re.sub(r"^.*?(技术要求|技术条件)", "", s).strip(" :：")
            if rest:
                notes.append(rest)
            # 取同一列附近、下方的文本
            for other in lines[i + 1: i + 25]:
                if abs(other["x"] - t["x"]) <= 200 and other["y"] < t["y"]:
                    notes.append((other.get("text") or "").strip())
    if not notes:
        # 退路：含常见技术要求关键词的 MTEXT
        for t in model.texts:
            s = (t.get("text") or "")
            if any(k in s for k in ("GB/T 1804", "GB/T 1184", "去毛刺", "未注公差")):
                notes.append(s.strip())
    model.tech_notes = [n for n in notes if n]


# ==========================================================================
# 评分（免费版报告格式的 50 分制）
# ==========================================================================
SCORE_DIMENSIONS = ["尺寸标注完整性", "公差标注规范性", "表面粗糙度标注",
                    "标题栏及技术要求", "视图规范性"]

_CATEGORY_TO_DIMENSION = {
    "尺寸": "尺寸标注完整性",
    "公差": "公差标注规范性",
    "粗糙度": "表面粗糙度标注",
    "标题栏": "标题栏及技术要求",
    "技术要求": "标题栏及技术要求",
    "视图": "视图规范性",
    "特征": "公差标注规范性",
    "系统": "视图规范性",
}

_SEVERITY_PENALTY = {"critical": 3.0, "major": 1.5, "minor": 0.5}

# 评分口径说明（必须写进报告，否则这个分数无法追溯）
SCORE_RUBRIC = (
    "评分口径：5 个维度各 10 分，满分 50 分。"
    "每发现一条问题按严重程度扣分：🔴严重 −3.0、🟡一般 −1.5、🟢轻微 −0.5，"
    "单维度最低 0 分。"
    "⚠️ 该 50 分制沿用上游 sw-drawing-qc 免费版 Skill 第八节的报告格式，"
    "是**相对质量分**，不是任何国标规定的指标；扣分权重可在 sw_qc_parser.py "
    "的 _SEVERITY_PENALTY 中按贵司习惯调整。"
    "另外：问题数少不等于图纸没问题 —— 本工具目前无法做尺寸-几何关联，"
    "要素级漏标查不出来，分数只反映**已检出的问题**。"
)


def score(model, issues) -> dict:
    scores = {d: 10.0 for d in SCORE_DIMENSIONS}
    breakdown = []
    for issue in issues:
        if issue.rule_id.startswith("CORE-ERR"):
            continue
        dim = _CATEGORY_TO_DIMENSION.get(issue.category, "视图规范性")
        penalty = _SEVERITY_PENALTY.get(issue.severity, 0.5)
        scores[dim] -= penalty
        breakdown.append({
            "dimension": dim,
            "rule_id": issue.rule_id,
            "title": issue.title,
            "severity": issue.severity,
            "deduct": penalty,
        })
    scores = {k: max(0.0, round(v, 1)) for k, v in scores.items()}
    scores["总分"] = round(sum(scores.values()), 1)
    # 扣分明细：让每一分都能追溯到具体问题
    scores["_breakdown"] = breakdown
    scores["_rubric"] = SCORE_RUBRIC
    return scores


# ==========================================================================
# 主流程
# ==========================================================================
def analyze(path: str, use_features: bool = False, preset: str = "gb_baseline",
            extra_rules: list[str] | None = None, is_pdf: bool = False,
            whitelist_path: str | None = None, part_type: str = "auto",
            use_ai: bool | None = None):
    """
    跑完整流程，返回 (model, features, issues, scores, stats)。

    whitelist_path：『确认无误』白名单文件路径（默认项目根目录 qc_whitelist.json）。
    命中白名单的问题**不计分、不出现在问题表**，但会如实记在
    model.suppressed_issues 里并写进报告 —— 不做静默隐藏。
    """
    from sw_qc_checks import run_core_checks
    from sw_qc_rules import RuleEngine
    from sw_qc_whitelist import WhitelistStore

    if is_pdf or path.lower().endswith(".pdf"):
        from sw_qc_pdf import parse_pdf
        model = parse_pdf(path)
    else:
        model = parse_dxf(path)

    # PDF 文字被转成曲线时，文本类检查全部失效。此时要立刻停下来说明原因，
    # 否则会输出「缺少材料、缺少图号、无尺寸标注」等一整页假严重问题 ——
    # 用户实测反馈正是被这种假结论激怒的。
    if getattr(model, "text_unavailable", False):
        from sw_qc_rules import Issue, SEVERITY_MAJOR
        issue = Issue(
            rule_id="CORE-PDF-NOTEXT", category="系统", severity=SEVERITY_MAJOR,
            title="该 PDF 不含可提取文字（文字已被转成曲线），无法检查",
            detail="页面里只有矢量线条、没有任何文字对象。"
                   "很多「打印成 PDF」或某些导出设置会把文字转成曲线，"
                   "这种情况下材料、图号、尺寸、公差、粗糙度全都读不到。",
            suggestion="改用 DXF 原图，或用「文字可选中」的方式重新导出 PDF"
                       "（在 PDF 里能框选到文字即为可提取）",
            standard="")
        return model, [], [issue], score(model, [issue]), model.stats()

    features = []
    if use_features:
        from sw_qc_features import extract_features
        features = extract_features(model)
        model._features = features
    else:
        model._features = []

    # 零件类型识别（机加件/钣金件/塑胶件/组合件）。
    #
    # 为什么必须先做这一步：有几条检查**只对特定零件类型成立**。
    # 用户实测反馈（2026-10-13）：「钣金件和塑胶件一般是不需要标注粗糙度的，
    # 一般只有机加件需要标注粗糙度」—— 对钣金件报「未检出粗糙度」就是误报。
    #
    # 用可解释的启发式先判（毫秒级）并把依据记下来；part_type 参数可让用户
    # 在界面上直接覆盖（自动判断不可能 100% 准，用户说了算）。
    _detect_part_type(model, part_type)

    issues = run_core_checks(model)

    # 统计量：粗糙度不能只按字面 "Ra" 数（SolidWorks 的粗糙度写在符号块里），
    # 复用与检查逻辑同一套识别，保证「规则引擎看到的」和「检查看到的」一致。
    stats = model.stats()
    try:
        from sw_qc_checks import _collect_roughness
        stats["roughness_count"] = len(_collect_roughness(model))
    except Exception:
        stats["roughness_count"] = 0

    engine = RuleEngine(preset=preset, extra_files=extra_rules)
    rule_issues = engine.evaluate(model, features, stats)

    # 通用检查（免费版清单）与规则预设（Pro 版规则引擎）覆盖范围有重叠，
    # 例如「材料未标注」两边都会报。若不处理，同一问题会出现两次，
    # 报告可信度直接下降。这里统一消解：预设规则更具体（带标准号与建议），
    # 因此重叠时保留预设规则，丢弃通用检查的对应条目。
    issues = _drop_subsumed_core_issues(issues, rule_issues, engine)
    issues.extend(rule_issues)

    # 去重：同一 rule_id + 同一位置只保留一条
    seen = set()
    unique = []
    for i in issues:
        key = (i.rule_id, i.location, i.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(i)
    issues = unique

    # AI 语义检查（可选，只在配置了 API 密钥时动作）。
    #
    # 严格按项目规定（见 rule_catalog.AI_POLICY / sw_qc_ai.py）：
    #   • 只做 10 条纯文本语义规则，不做几何、不查表
    #   • 硬超时 20 秒，超时/失败一律静默降级，**不影响上面的本地结果**
    #   • 每次审图只调 1 次模型；结论 ≤3 条、每条 ≤40 字、必须引用图上原文
    # 放在白名单过滤之前：用户点过「确认无误」的 AI 结论，下次同样不再报。
    ai_meta = {"enabled": False, "available": False, "reason": "未启用"}
    if use_ai is not False:
        try:
            from sw_qc_ai import check_tech_semantics
            ai_issues, ai_meta = check_tech_semantics(model)
            issues.extend(ai_issues)
        except Exception as exc:
            ai_meta = {"enabled": True, "available": False,
                       "reason": f"AI 模块异常：{exc}", "elapsed_ms": 0}
    model.ai_meta = ai_meta

    # 『确认无误』白名单：用户点过「确认无误」的问题不再报、不再扣分。
    #
    # 位置很关键 —— 必须在 score() **之前**过滤。误报不只是烦人：
    # 每条严重问题扣 3 分、单维度最低 0 分，4 条误报就能把一个维度打到 0，
    # 把一张好图纸判成差图纸。所以「不计分」才是这个功能的重点。
    #
    # 但**不做静默隐藏**：被抑制的条目完整记在 model.suppressed_issues，
    # 报告里如实说明有几条被抑制、依据是什么（见 report_suppressed）。
    whitelist = WhitelistStore(whitelist_path)
    issues, suppressed = whitelist.filter(issues)
    model.suppressed_issues = suppressed
    model.whitelist_path = whitelist.path
    model.whitelist_error = whitelist.load_error

    return model, features, issues, score(model, issues), stats


def _detect_part_type(model, requested: str = "auto") -> dict:
    """
    设定 model.part_type 等字段。

    requested = "auto" 时用启发式判断；否则用用户指定的类型（用户说了算 ——
    自动判断不可能 100% 准，尤其是钣金+机加的组合件）。
    判定依据一并记下来，报告里会显示，用户能看出该不该接受。
    """
    info = {"requested": requested, "type": "", "confidence": 0.0,
            "evidence": [], "scores": {}, "auto": False, "overridden": False}
    try:
        from sw_qc_parttype import detect_part_type, ALL_TYPES
    except Exception as exc:
        model.part_type = ""
        model.part_type_confidence = 0.0
        model.part_type_evidence = [f"零件类型识别模块不可用：{exc}"]
        model.part_type_info = info
        return info

    auto = detect_part_type(model)
    info.update({k: auto.get(k) for k in ("type", "confidence", "evidence", "scores")})
    info["auto"] = True

    if requested and requested != "auto" and requested in ALL_TYPES:
        info["type"] = requested
        info["overridden"] = True
        info["evidence"] = list(info["evidence"]) + [
            f"用户手动指定为「{requested}」，覆盖自动判断结果（自动判断为「{auto['type']}」）"]

    model.part_type = info["type"]
    model.part_type_confidence = float(info.get("confidence") or 0.0)
    model.part_type_evidence = list(info.get("evidence") or [])
    model.part_type_info = info
    return info


def _drop_subsumed_core_issues(core_issues, rule_issues, engine):
    """
    去掉被当前规则预设覆盖的通用检查条目。

    两类重叠：
      1. 标题栏字段 —— 由 field_check 规则负责时，不再重复报 CORE-TB-*
      2. 固定配对 —— 如 CORE-RA-MISSING 与 GB-RA 描述同一件事
    """
    active_ids = {r.id for r in engine.rules if r.enabled}
    covered_fields = {r.params.get("field") for r in engine.rules
                      if r.enabled and r.mode == "field_check"
                      and r.params.get("field")}

    # 通用检查条目 → 当其存在时即可取代它的预设规则 id
    equiv = {
        "CORE-DIM-NONE": {"GB-DIM", "ISO-DIM"},
        "CORE-GDT": {"GB-GDT", "ISO-GDT"},
        "CORE-NOTE-未注尺寸公差": {"GB-TOLTXT", "ISO-TOL"},
        "CORE-NOTE-未注几何公差": {"GB-GDT", "ISO-GDT"},
        "CORE-NOTE-未注圆角/倒角": {"GB-CHAMFER"},
        "CORE-RA-MISSING": {"GB-RA", "ISO-RA"},
        "CORE-NOTE-去毛刺": {"GB-DEBURR"},
        "CORE-NOTE-棱边倒钝": {"GB-DEBURR"},
    }

    out = []
    for issue in core_issues:
        if issue.rule_id.startswith("CORE-TB-"):
            field = issue.rule_id[len("CORE-TB-"):]
            if field in covered_fields:
                continue
        if equiv.get(issue.rule_id, set()) & active_ids:
            continue
        out.append(issue)

    # 「标题栏缺比例」和「未检出视图比例」是同一个根因，只报一次，
    # 否则用户会看到两条说同一件事的问题。
    if any(i.rule_id == "CORE-TB-比例" for i in out):
        out = [i for i in out if i.rule_id != "CORE-VIEW-SCALE"]
    return out


def _print_console(model, features, issues, scores, stats, use_features: bool) -> None:
    name = os.path.basename(model.path)
    bar = "=" * 60
    print(bar)
    print(f"  Engineering Drawing QC Inspector — {ENGINE_VERSION}")
    print(f"  {name}")
    print(bar)
    print(f"  Dimensions: {stats['dimension_count']}"
          f"   (文字型尺寸约 {stats['dimension_text_count']})")
    print(f"  Circles/Arcs: {stats['circle_count']} / {stats['arc_count']}")
    print(f"  Texts: {stats['text_count']}   Layers: {len(model.layers)}")
    if use_features:
        print(f"  Features detected: {len(features)}")
    print()

    counts = {"critical": 0, "major": 0, "minor": 0}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    print(f"  Issues: {len(issues)}"
          f"   🔴 {counts['critical']}  🟡 {counts['major']}  🟢 {counts['minor']}")
    print()

    marks = {"critical": "!!", "major": " !", "minor": "  "}
    for i in sorted(issues, key=lambda x: {"critical": 0, "major": 1, "minor": 2}[x.severity]):
        loc = f"[{i.location}] " if i.location else ""
        print(f"  {marks.get(i.severity, '  ')} {loc}{i.title}")

    if use_features and features:
        print()
        print("  特征识别：")
        for f in features:
            print(f"    - {f.type} ({f.confidence:.0%}) "
                  f"@({f.location[0]:.1f}, {f.location[1]:.1f}) {f.params}")

    print()
    print(f"  完整性评分: {scores['总分']} / 50")
    for d in SCORE_DIMENSIONS:
        print(f"    {d}: {scores[d]} / 10")

    if model.warnings:
        print()
        print(f"  解析警告 {len(model.warnings)} 条（前 3 条）：")
        for w in model.warnings[:3]:
            print(f"    - {w}")


def make_stdout_safe() -> None:
    """
    让控制台输出在非 UTF-8 环境下也能工作。

    Windows 中文系统的控制台默认是 GBK。当输出被重定向到文件时，Python 会退回
    本地编码，此时打印 🔴/🟡/🟢 会直接抛 UnicodeEncodeError 让整个检查崩掉
    （实测：`python sw_qc_parser.py 图纸.dxf > log.txt` 必崩）。

    这里保留原有编码（保证中文正常），只把「编不出来的字符」降级为替代符，
    而不是让程序崩溃。若外部已设置 PYTHONIOENCODING，则完全尊重外部设置。
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sw_qc_parser.py",
        description="工程图 QC 检查器 — DXF/PDF 结构化解析 + 特征识别 + 国标规则",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python sw_qc_parser.py 图纸.dxf --features --rules gb_baseline\n"
               "  python sw_qc_parser.py 图纸.dxf --features --rules automotive --json --excel\n"
               "  python sw_qc_parser.py 图纸.pdf --pdf --json\n")
    p.add_argument("drawing", nargs="?", help="DXF 或 PDF 图纸路径")
    p.add_argument("--features", action="store_true", help="启用机械特征识别")
    p.add_argument("--rules", default="gb_baseline",
                   help="规则预设（gb_baseline/automotive/mold/sheet_metal/welding/hydraulic/gear）")
    p.add_argument("--rules-file", action="append", default=[],
                   help="附加自定义 YAML 规则文件（可多次指定）")
    p.add_argument("--pdf", action="store_true", help="按 PDF 解析")
    p.add_argument("--part-type", default="auto",
                   choices=["auto", "机加件", "钣金件", "塑胶件", "组合件", "未知"],
                   help="零件类型（默认 auto=自动判断）。钣金件/塑胶件不检查粗糙度漏标")
    p.add_argument("--ai", dest="use_ai", action="store_true", default=None,
                   help="强制启用 AI 语义检查（需先配置 qc_ai_config.json）")
    p.add_argument("--no-ai", dest="use_ai", action="store_false",
                   help="本次不做 AI 语义检查")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告")
    p.add_argument("--excel", action="store_true", help="输出 Excel 报告")
    p.add_argument("--markdown", action="store_true", help="输出 Markdown 报告")
    p.add_argument("--preview", action="store_true",
                   help="同时渲染图纸预览 PNG（白底黑线，含中文字体处理）")
    p.add_argument("--preview-dpi", type=int, default=150, help="预览渲染 DPI（默认 150）")
    p.add_argument("--out", default=None, help="输出目录（默认与图纸同目录）")
    p.add_argument("--list-presets", action="store_true", help="列出所有规则预设后退出")
    p.add_argument("--quiet", action="store_true", help="不打印控制台摘要")
    return p


def main(argv: list[str] | None = None) -> int:
    make_stdout_safe()
    args = build_parser().parse_args(argv)

    if args.list_presets:
        from sw_qc_rules import list_presets
        print("可用规则预设：")
        for name in list_presets():
            print("  -", name)
        return 0

    if not args.drawing:
        build_parser().print_help()
        return 2

    if not os.path.isfile(args.drawing):
        print(f"错误：找不到文件 {args.drawing}", file=sys.stderr)
        return 2

    model, features, issues, scores, stats = analyze(
        args.drawing, use_features=args.features, preset=args.rules,
        extra_rules=args.rules_file,
        is_pdf=args.pdf or args.drawing.lower().endswith(".pdf"),
        part_type=args.part_type, use_ai=args.use_ai)

    if not args.quiet:
        _print_console(model, features, issues, scores, stats, args.features)

    outdir = args.out or os.path.dirname(os.path.abspath(args.drawing))
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.drawing))[0]

    wrote: list[str] = []
    write_errors: list[str] = []
    if args.json or args.excel or args.markdown:
        from sw_qc_report import write_excel, write_json, write_markdown

        plan = []
        if args.json:
            plan.append(("JSON", "qc_report.json", write_json))
        if args.markdown:
            plan.append(("Markdown", "qc_report.md", write_markdown))
        if args.excel:
            plan.append(("Excel", "qc_report.xlsx", write_excel))

        wrote, write_errors, outdir = _write_reports(
            plan, outdir, base, model, features, issues, scores, stats, args.rules)

        if not args.quiet:
            print()
            for w in wrote:
                print(f"  已写出报告: {w}")
            for e in write_errors:
                print(f"  [错误] {e}")

    if args.preview and model.fmt == "dxf":
        from sw_qc_render import render_dxf_to_png
        png_path = os.path.join(outdir, f"{base}.preview.png")
        try:
            r = render_dxf_to_png(args.drawing, png_path, dpi=args.preview_dpi)
            if not args.quiet:
                if r["error"]:
                    print(f"  预览渲染失败: {r['error']}")
                else:
                    print(f"  已写出预览: {r['out_path']}  ({r['width']}×{r['height']})")
        except OSError as exc:
            write_errors.append(f"预览写入失败：{png_path}（{exc.strerror or exc}）")
            print(f"  [错误] {write_errors[-1]}")

    # 退出码：0 正常 / 1 有严重问题 / 3 报告写不出去（便于脚本区分）
    if any(i.severity == "critical" for i in issues):
        return 1
    if write_errors:
        return 3
    return 0


def _fallback_dirs() -> list[str]:
    """图纸目录不可写时的候选落盘位置（按优先级）。"""
    home = os.path.expanduser("~")
    import tempfile
    return [
        os.path.join(home, "Documents", "图纸QC报告"),
        os.path.join(tempfile.gettempdir(), "图纸QC报告"),
    ]


def _write_reports(plan, outdir, base, model, features, issues, scores, stats,
                   preset):
    """
    写报告；首选图纸所在目录，写不进去就自动换到备用目录。

    为什么要自动换：真实环境里图纸常放在只读盘、受保护目录或网络盘上，
    此时直接报「Permission denied」对用户毫无帮助。改存到「文档\\图纸QC报告」
    并明确告知，比让流程失败更有用。
    """
    def attempt(directory: str):
        results, errors = [], []
        for label, suffix, fn in plan:
            target = os.path.join(directory, f"{base}.{suffix}")
            try:
                results.append(fn(target, model, features, issues, scores, stats,
                                  preset))
            except OSError as exc:
                errors.append((label, target, exc))
        return results, errors

    wrote, errors = attempt(outdir)
    if not errors:
        return wrote, [], outdir

    for candidate in _fallback_dirs():
        try:
            os.makedirs(candidate, exist_ok=True)
        except OSError:
            continue
        wrote2, errors2 = attempt(candidate)
        if wrote2:
            reason = errors[0][2].strerror or errors[0][2]
            print(f"  [提示] 图纸所在目录不可写（{reason}），"
                  f"报告已改存到：{candidate}")
            remaining = [f"{l} 报告写入失败：{t}（{e.strerror or e}）"
                         for l, t, e in errors2]
            return wrote2, remaining, candidate
        errors = errors2

    return [], [f"{l} 报告写入失败：{t}（{e.strerror or e}）"
                for l, t, e in errors], outdir


if __name__ == "__main__":
    raise SystemExit(main())