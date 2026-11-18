# -*- coding: utf-8 -*-
"""
sw_qc_checks.py — 通用检查逻辑（免费版 Skill 检查清单的可执行化）

把 `skills/sw-drawing-qc.md` 第三节~第七节的检查清单，从「给 LLM 看的文字」
变成「可以确定执行的代码」。这是整个工具最核心的价值转换：

    上游免费版  = 一份清单，每次都要 LLM 重新读一遍、结论不稳定
    本模块      = 同样的清单，固化成可复现、可回归测试、可批量跑的判定

每个 check_* 函数返回 list[Issue]。
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


import math
import re

from sw_qc_rules import Issue, SEVERITY_CRITICAL, SEVERITY_MAJOR, SEVERITY_MINOR

RA_RE = re.compile(r"\bRa\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
# 配合公差代号必须「紧跟在一个数字之后」，否则 M10 这类螺纹代号会被误判为公差
TOL_CODE_RE = re.compile(r"(?<=\d)([A-Za-z]{1,2}\d{1,2})(?![0-9])")
GDT_SYMBOLS = ["⌀", "⌖", "∥", "⊥", "⌭", "⌒", "◎", "∠", "⌰", "⌯", "⌓", "⌔"]


def _num(text: str) -> float | None:
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", text or "")
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------
# 一、标题栏检查（对应免费版 6.1）
# --------------------------------------------------------------------------
# 图框/标题栏类检查的开关。
#
# 决定依据（用户 2026-10-13）：《AI机械工程图审核规则库 V1》开篇声明
# 「不包含图框完整性」，用户据此要求**关闭图框类检查，材料保留**
# （规则库有 MAT-001「是否注明材料」，属内容检查，不在排除范围）。
#
# 保留「名称」的原因：用户先明确要求把「零件名称缺失」的级别定为严重，
# 后又要求关闭图框类 —— 两条指示有张力。按「更具体的指示优先」，
# 名称保留并定为严重。若日后确认要关，把下面的 True 改成 False 即可。
#
# 关闭的是**报告输出**，解析与识别逻辑一律不变（标题栏字段照常读出来，
# 报告里仍会显示，只是不再作为"问题"报出）。
TITLE_BLOCK_CHECKS = {
    "材料": True,     # 保留（规则库 MAT-001）
    "名称": True,     # 保留（用户要求级别=严重）
    "图号": False,    # 关闭（属图框完整性）
    "比例": False,    # 关闭（属图框完整性；工具仍会在报告里显示自动反推结果）
    "张数": False,    # 关闭（属图框完整性）
    "签署栏": False,  # 关闭（属图框完整性）
}


def check_title_block(model) -> list[Issue]:
    issues: list[Issue] = []
    tb = model.title_block or {}

    # 图框（标题栏）位于右下角是制图标准的固定要求，与幅面无关。
    # 读到了就是读到了（不挂"推断"之类的免责标签）；读不到才算问题。
    # 但"报不报"由 TITLE_BLOCK_CHECKS 决定 —— 见上方说明。
    if TITLE_BLOCK_CHECKS.get("比例", True) and not str(tb.get("比例", "")).strip():
        scale = float(getattr(model, "drawing_scale", 1.0) or 1.0)
        src = getattr(model, "drawing_scale_source", "") or ""
        extra = ""
        if abs(scale - 1.0) > 1e-6:
            extra = (f"。工具已从尺寸标注自动反推：**几何尺寸 × {scale:g} = 实际尺寸**"
                     f"（例：图上量得 ⌀48，实际为 ⌀24）"
                     + (f"。依据：{src}" if src else ""))
        issues.append(Issue(
            rule_id="CORE-TB-比例", category="标题栏", severity=SEVERITY_MAJOR,
            title="标题栏缺少『比例』",
            detail="绘图比例应标注在标题栏，否则看图的人无法判断零件实际大小" + extra,
            suggestion=f"在标题栏补填比例（本图按反推结果应填对应比例）",
            standard="GB/T 10609.1", location="图框右下角"))

    required = {
        "材料": ("critical", "材料未标注，无法确定毛坯与工艺",
                 "在标题栏材料栏补充材料牌号及标准号，如 45 GB/T 699-2015"),
        "图号": ("critical", "缺少图号，图纸无法受控归档",
                 "在标题栏补填图号（或零件代号）"),
        # 零件名称缺失 = 严重（用户明确要求，原为 major）
        "名称": ("critical", "缺少零件名称，图纸无法识别与归档",
                 "在标题栏补填零件名称"),
    }
    for fld, (sev, msg, sug) in required.items():
        if not TITLE_BLOCK_CHECKS.get(fld, True):
            continue
        if not str(tb.get(fld, "")).strip():
            issues.append(Issue(
                rule_id=f"CORE-TB-{fld}", category="标题栏", severity=sev,
                title=f"标题栏缺少『{fld}』", detail=msg, suggestion=sug,
                standard="GB/T 10609.1", location="图框右下角"))
    return issues


# --------------------------------------------------------------------------
# 尺寸检查的豁免机制（用户 2026-10-14 提出）
# --------------------------------------------------------------------------
# 用户的实测反馈：
#   「零件的特征是阵列出来的，比如方框阵列用来减重，有些用来散热的翅片，
#     往往很多工程师不会标注详细尺寸，因为这些特征尺寸不是很重要」
#   「塑胶件中有些尺寸没法标注，因为有拔模角度或曲面特征……
#     塑胶件尺寸的完整性需要适当放松，甚至不做检测对象，轻微都算不上，
#     一般会在技术要求中说明『未注尺寸参考3D模型』」
#
# 核心认识：**图上"没标"不等于"漏标"**。至少四类"不标"是正当的：
#   ① 阵列特征（标一处 + 注明数量/均布即可）
#   ② 塑胶件的拔模/曲面尺寸（二维尺寸根本表达不了）
#   ③ 技术要求声明"未注尺寸参考3D模型"（以数模为准）
#   ④ 非功能面/工艺基准
#
# 所以检查必须先看"有没有正当理由不标"，再决定报不报。
# 这是"宁可漏报也不要误报"的具体落地 —— 硬要求每个特征都标注，
# 换来的是一堆工程师一眼就知道是废话的误报，信任就没了。
_3D_TOKEN_RE = re.compile(r"3D|三维|数模|电子模型|STP|STEP", re.IGNORECASE)
# 「以…为准」类的授权词。注意不能只列死词：图纸写法有
# 「未注尺寸参考3D模型」「未注尺寸以3D模型为准」「尺寸参照三维模型」「未注尺寸按数模」…
# 所以采用「授权词与 3D 词相距很近」的判据（见 has_3d_model_reference），
# 避免把「按图纸加工，3D打印样件」这种误判成"以 3D 为准"。
_3D_AUTH_RE = re.compile(r"为准|参考|参照|依据|详见|见|按|以|依")


def has_3d_model_reference(model) -> tuple[bool, str]:
    """
    技术要求是否声明「未注尺寸参考 3D 模型 / 以数模为准」。

    判据：同一行里「3D/三维/数模」与「为准/参考/参照/依据/按/以」**相距不超过 8 个字符**。
    加上距离限制，可以避免把"按图纸加工，3D打印样件"这类句子误判成豁免声明。
    """
    lines = [str(t.get("text") or "") for t in (getattr(model, "texts", []) or [])]
    lines += [str(n) for n in (getattr(model, "tech_notes", []) or [])]
    for s in lines:
        for m3 in _3D_TOKEN_RE.finditer(s):
            for ma in _3D_AUTH_RE.finditer(s):
                if abs(ma.start() - m3.start()) <= 8:
                    return True, s.strip()[:60]
    return False, ""


def _regular_linear(centers: list[tuple[float, float]], tol_ratio: float = 0.12) -> bool:
    """一组中心点是否沿一条直线等间距排列。"""
    if len(centers) < 3:
        return False
    pts = sorted(centers)
    # 先看是否共线（用最小二乘后残差判断，简化：比较 y 的离散度）
    ys = [p[1] for p in pts]
    xs = [p[0] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    if (max(ys) - min(ys)) > tol_ratio * span and (max(xs) - min(xs)) > tol_ratio * span:
        return False                      # 两个方向都散开 → 不是一排
    # 等间距：相邻间距的中位数与最大偏差
    if max(xs) - min(xs) >= max(ys) - min(ys):
        axis_vals = xs
    else:
        axis_vals = ys
    gaps = [b - a for a, b in zip(axis_vals, axis_vals[1:]) if b - a > 1e-6]
    if len(gaps) < 2:
        return False
    gaps_sorted = sorted(gaps)
    med = gaps_sorted[len(gaps_sorted) // 2]
    return med > 0 and all(abs(g - med) <= 0.25 * med for g in gaps_sorted)


def detect_circle_arrays(model) -> list[dict]:
    """
    识别圆形要素阵列（同一半径、≥3 个、等间距排列）。

    返回 [{"radius": r, "count": n, "members": [(x, y), ...]}]。

    用途：**阵列里只要有一处的尺寸被标注（或写了 n×φd），
    其余几处就不该再报"漏标"** —— 否则一张有 6 个均布孔的图会报 5 条误报。
    """
    from collections import defaultdict
    groups: dict[float, list[tuple[float, float]]] = defaultdict(list)
    for c in (getattr(model, "circles", []) or []):
        r = float(c.get("r") or 0)
        if r < 0.5:                        # 中心标记之类的小圆跳过
            continue
        groups[round(r, 2)].append((float(c["cx"]), float(c["cy"])))
    out = []
    for r, pts in groups.items():
        if len(pts) >= 3 and _regular_linear(pts):
            out.append({"radius": r, "count": len(pts), "members": pts})
    return out


def dimension_waivers(model) -> dict:
    """
    汇总"这张图的尺寸检查该放多宽"，返回：

        {"waive_all": bool, "reason": str,
         "array_sizes": [直径…], "part_type": str, "notes": [说明…]}

    waive_all=True 表示**整图的尺寸完整性检查豁免**（仍会显示理由，不静默跳过）。
    """
    info = {"waive_all": False, "reason": "", "array_sizes": [],
            "part_type": str(getattr(model, "part_type", "") or ""), "notes": []}

    # ① 技术要求声明以 3D 为准
    has3d, snippet = has_3d_model_reference(model)
    if has3d:
        info["waive_all"] = True
        info["reason"] = f"技术要求声明以 3D 模型为准（『{snippet}』），未注尺寸不作为漏标"
        info["notes"].append(info["reason"])

    # ② 塑胶件：拔模角与曲面尺寸无法用二维尺寸完全表达
    if info["part_type"] == "塑胶件":
        info["waive_all"] = True
        r = ("塑胶件的拔模角/曲面尺寸无法完全用二维尺寸表达，"
             "尺寸完整性不作为检查对象（一般只标孔、外形轮廓、壁厚等关键尺寸）")
        info["reason"] = (info["reason"] + "；" + r) if info["reason"] else r
        info["notes"].append(r)

    # ③ 阵列：记录"已有标注"的阵列尺寸，供圆形漏标检查放宽
    arrays = detect_circle_arrays(model)
    if arrays:
        dim_vals = [float(d.get("value") or 0) for d in (getattr(model, "dimensions", []) or [])
                    if d.get("value")]
        for a in arrays:
            dia = 2 * a["radius"]
            tol = max(0.05, 0.01 * dia)
            if any(abs(dia - v) <= tol for v in dim_vals):
                info["array_sizes"].append(round(dia, 2))
        if info["array_sizes"]:
            info["notes"].append(
                "识别到圆形阵列（直径 "
                + "、".join(f"φ{d:g}" for d in info["array_sizes"])
                + "）：阵列中已有一处标注，其余按阵列处理、不再逐个要求标注")
    return info


# --------------------------------------------------------------------------
# 二、尺寸漏标检查（对应免费版 3.1）
# --------------------------------------------------------------------------
# 倒角/圆角数值提取
_CHAMFER_IN_TEXT_RE = re.compile(r"C\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_FILLET_IN_TEXT_RE = re.compile(r"R\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_NOTE_CHAMFER_RE = re.compile(r"未注(?:倒角|圆角)\s*[CR]\s*(\d+(?:\.\d+)?)")


def chamfer_fillet_values(model) -> list[float]:
    """
    收集图上所有倒角/圆角数值（C1、C 1.5、2×1、未注倒角 C1、SW 倒角标注块…）。

    用途：判断某个圆是不是**派生尺寸**——
    例如 ⌀11.2 内孔带 C1 倒角，倒角后的外圆就是 11.2 + 2×1 = 13.2。
    这个 ⌀13.2 由「孔径 + 倒角」完全确定，**按制图规范不需要单独标注**，
    报成"漏标"就是误报。

    实测：用户明确指出 ⌀13.2 正是 ⌀11.2 + C1 的倒角外圆。
    """
    vals: set[float] = set()
    block_texts: dict[str, list[str]] = {}

    for t in getattr(model, "texts", []) or []:
        s = (t.get("text") or "").strip()
        if not s:
            continue
        b = str(t.get("block") or "")
        if b:
            block_texts.setdefault(b, []).append(s)
        for m in _CHAMFER_IN_TEXT_RE.finditer(s):
            vals.add(round(float(m.group(1)), 3))
        for m in _NOTE_CHAMFER_RE.finditer(s):
            vals.add(round(float(m.group(1)), 3))
        # 阵列倒角写法 2×1（两处 C1）
        m = re.match(r"^\s*\d+\s*[×xX]\s*(\d+(?:\.\d+)?)\s*$", s)
        if m:
            vals.add(round(float(m.group(1)), 3))

    # SolidWorks 的倒角标注是独立块，文字被拆成 'C' 与 '1' 两段，
    # 单独看每段都读不出数值，必须按块拼起来。
    for b, parts in block_texts.items():
        if "CHAMFER" in b.upper() or "FILLET" in b.upper():
            joined = "".join(parts)
            for m in re.finditer(r"(\d+(?:\.\d+)?)", joined):
                v = float(m.group(1))
                if 0 < v <= 50:
                    vals.add(round(v, 3))
    return sorted(v for v in vals if 0 < v <= 50)


def explain_circle_by_chamfer(real_d: float, dim_values: list[float],
                              chamfers: list[float],
                              tol_ratio: float = 0.01) -> str | None:
    """
    判断一个圆能否由「某个已标注尺寸 ± 2×倒角」解释出来（即派生尺寸）。
    能解释则返回说明文字，否则返回 None。
    """
    for d in dim_values:
        for c in chamfers:
            for candidate, sign in ((d + 2 * c, "+"), (abs(d - 2 * c), "−")):
                tol = max(0.1, tol_ratio * max(real_d, d))
                if abs(real_d - candidate) <= tol:
                    return f"{d:g} {sign} 2×C{c:g} = {candidate:g}（倒角派生）"
    return None


def geometry_dimension_map(model) -> tuple[list[dict], float]:
    """
    几何要素 ↔ 尺寸标注 对照表。

    这是「尺寸完整性」第一次做到要素级 —— 虽然还只是**圆/直径**这一类，
    但已经是实质进步。桥梁是 dimlfac：
        实际尺寸 = 几何尺寸 × dimlfac
    实测某图 dimlfac=0.5（模型按 2:1 画，标注值 = 几何值 × 0.5），
    不换算就直接比会全军覆没。

    返回 (对照行列表, 使用的 dimlfac)
    """
    if not model.dimensions:
        return [], 1.0

    # 比例系数优先用解析阶段**从数据反推**出来的 drawing_scale，
    # 而不是尺寸样式的 dimlfac —— ezdxf 自带的 EZDXF 样式 dimlfac=100，
    # 照抄会把 ⌀30 算成 ⌀3000。只有在模型没提供时才退回 dimlfac。
    scale = float(getattr(model, "drawing_scale", 0) or 0)
    if scale <= 0:
        factors = [float(d.get("dimlfac") or 1.0) for d in model.dimensions]
        scale = max(set(factors), key=factors.count) if factors else 1.0

    values = [(d.get("value"), _dim_display(d)) for d in model.dimensions
              if d.get("value")]
    dim_values = [float(v) for v, _disp in values]
    chamfers = chamfer_fillet_values(model)

    rows: list[dict] = []
    for c in model.circles:
        r = float(c["r"])
        if r < 0.5:                      # 中心标记之类的小圆跳过
            continue
        real_d = 2.0 * r * scale
        hit = None
        for v, disp in values:
            tol = max(0.05, 0.004 * real_d)
            if abs(real_d - v) <= tol:
                hit = disp
                break
        derived = ""
        if not hit:
            # 直接找不到标注时，先看它是不是倒角/圆角派生出来的尺寸
            derived = explain_circle_by_chamfer(real_d, dim_values, chamfers) or ""
        rows.append({
            "x": round(c["cx"], 1), "y": round(c["cy"], 1),
            "geom_diameter": round(2.0 * r, 2),
            "real_diameter": round(real_d, 2),
            "matched": hit or "",
            "ok": bool(hit) or bool(derived),
            "derived": derived,
        })

    # 注意：这里**只做「几何 → 标注」一个方向**。
    # 反方向（有直径标注但找不到圆）不再报 —— 用户实测反馈：
    # ⌀4.2、⌀17 这类标注本来就可能指向剖视图里的要素，几何里没有对应圆
    # 完全正常，报了就是误报。
    return rows, scale


def check_dimension_completeness(model) -> list[Issue]:
    """
    尺寸完整性（要素级，v1）：把「圆」和「直径标注」对起来，报出对不上的。

    诚实说明能力边界：
      • 只覆盖圆形要素（孔/轴/回转面），不覆盖长度、角度、位置尺寸
      • 依赖 dimlfac 换算，若图纸有多个不同比例视图，换算可能不准
      • 对不上的原因可能是「真的漏标」，也可能是「该要素在别的视图里标注」
        或「当前视图比例不同」—— 所以只给 🟢轻微 并说明需人工确认
    """
    rows, scale = geometry_dimension_map(model)
    if not rows:
        return []

    # 尺寸完整性的豁免（见本文件顶部"尺寸检查的豁免机制"）：
    #   • 技术要求声明以 3D 模型为准 → 整图豁免
    #   • 塑胶件（拔模/曲面无法用二维尺寸表达）→ 整图豁免
    #   • 圆形阵列里已有直径标注 → 该直径的成员不再逐个要求标注
    # 豁免不是静默跳过：理由会记在 model.dimension_waivers 里，报告要如实说明。
    waivers = dimension_waivers(model)
    model.dimension_waivers = waivers

    issues: list[Issue] = []
    missing = [r for r in rows
               if not r["ok"] and not r.get("orphan_dim") and not r.get("derived")]

    if waivers["waive_all"]:
        return issues                       # 整图豁免，理由由报告展示

    # 阵列成员：直径已作为阵列标注过一次的，不算漏标
    array_sizes = list(waivers.get("array_sizes") or [])
    if array_sizes:
        def _in_array(r) -> bool:
            d = float(r.get("real_diameter") or 0)
            return any(abs(d - a) <= max(0.05, 0.01 * a) for a in array_sizes)
        missing = [r for r in missing if not _in_array(r)]

    if not missing:
        return issues

    # 几何要素找不到对应标注 = 漏标，按用户要求报**严重**。
    # 用户实测：删掉 ⌀24 标注后，本工具必须报严重错误。
    #
    # 描述口径按用户明确要求「简单明了」：
    #   ✗ 不写坐标（工程师不需要知道漏标在哪，只需要知道漏了）
    #   ✗ 不写换算比例（那是内部实现，读者不关心）
    #   ✓ 写成「几何 φ24 漏标（疑似漏标），零件上的孔/外圆未标注直径将无法加工」
    #
    # 为什么标「疑似」：本工具**不做视图分割**（已知限制），无法排除
    # 「该要素在另一个视图里已经标注了」。既然不能确定，就必须写明疑似 ——
    # 这是项目定下的诚实口径：宁可说疑似，也不要武断结论。
    items = "、".join(f"φ{r['real_diameter']:g}" for r in missing[:8])
    if len(missing) > 8:
        items += "…"
    issues.append(Issue(
        rule_id="CORE-DIM-GEOM", category="尺寸", severity=SEVERITY_CRITICAL,
        title=(f"几何 {items} 漏标（疑似）" if len(missing) == 1
               else f"{len(missing)} 处直径漏标（疑似）：{items}"),
        detail=(f"几何 {items} 漏标（疑似漏标），"
                f"零件上的孔/外圆未标注直径将无法加工"),
        suggestion="为每个圆补标直径（同一圆周阵列可用 n×φd 形式）",
        standard="GB/T 4458.4", location="几何-标注对照"))
    return issues


def overall_extents(model) -> list[dict]:
    """
    推算零件的**外形轮廓尺寸**，返回 [{"name","value","source"}]。

    依据来自两处可靠几何：
      1) **剖面线（HATCH）范围** —— 剖视图里被切到的实体区域，它的包围盒
         就等于零件在该视图上的实际轮廓范围（长 × 高/直径）
      2) **最大外圆** —— 端视图里最大的圆就是零件外径

    实测某图：剖面线范围 70×48（几何）× dimlfac 0.5 = 35×24，
    正是零件的总长与外径 —— 而这两个尺寸标注被人为删掉后，
    本工具此前完全查不出来。
    """
    out: list[dict] = []
    scale = getattr(model, "drawing_scale", 1.0) or 1.0

    boxes = list(getattr(model, "hatch_boxes", []) or [])
    if boxes:
        # 相邻剖面线（同一剖视图的上下两半）合并
        x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
        out.append({"name": "总长", "value": round((x1 - x0) * scale, 2),
                    "source": "剖面线范围"})
        out.append({"name": "总高", "value": round((y1 - y0) * scale, 2),
                    "source": "剖面线范围"})

    # 最大外圆 → 外径
    if model.circles:
        rmax = max(float(c["r"]) for c in model.circles)
        if rmax > 0.5:
            out.append({"name": "外径", "value": round(2.0 * rmax * scale, 2),
                        "source": "最大外圆"})
    return out


def check_overall_dimensions(model) -> list[Issue]:
    """
    外形轮廓尺寸检查：推算出的总长/总高/外径，必须有对应的标注。

    这是用户明确要求的能力：「整个零件外形尺寸你没有检查出来，
    尤其是外形轮廓尺寸未匹配到的，需报严重错误」。

    实测验证：把某图上的 `35`（总长）、`24`（外径）、`10.5` 删掉后，
    本检查能全部报出；原图（尺寸齐全）则一条不报。
    """
    extents = overall_extents(model)
    if not extents:
        return []

    # 整图豁免（技术要求以 3D 为准 / 塑胶件）→ 外形轮廓也不作为漏标
    if (getattr(model, "dimension_waivers", None) or {}).get("waive_all"):
        return []

    values = [float(d["value"]) for d in model.dimensions if d.get("value")]
    if not values:
        return []

    missing = []
    for e in extents:
        v = e["value"]
        tol = max(0.15, 0.01 * v)          # 外形尺寸允许 1% 或 0.15mm 误差
        if not any(abs(v - dv) <= tol for dv in values):
            missing.append(e)

    if not missing:
        return []

    # 外形轮廓尺寸是**明确**的：推算依据是剖面线包围盒与最大外圆，
    # 全图找不到对应标注就是真漏了，所以这一条**不加「疑似」**
    # （用户明确区分：圆形要素可能有别的视图兜底，要标疑似；外形轮廓不用）。
    # 推算依据挪到 evidence，正文只留「哪个尺寸漏了」。
    items = "、".join(f"{e['name']} {e['value']:g}" for e in missing)
    return [Issue(
        rule_id="CORE-OVERALL", category="尺寸", severity=SEVERITY_CRITICAL,
        title=f"零件外形轮廓尺寸未标注：{'、'.join(e['name'] for e in missing)}",
        detail=f"{items} 未标注。外形轮廓尺寸是零件最基本的尺寸，"
               f"缺失将无法确定毛坯与加工余量，也无法核对零件大小",
        suggestion="补标零件总长/总高/外径等外形轮廓尺寸",
        standard="GB/T 4458.4", location="外形轮廓",
        evidence="；".join(f"{e['name']} {e['value']:g} ← {e['source']}"
                          for e in missing))]


def check_title_block_completeness(model) -> list[Issue]:
    """
    图框填写完整性：图纸张数、签署栏。

    实测某图标题栏里「共 张 第 张」是空的（没有任何数字），
    设计/校对/审核/工艺/标准化/质量/批准 七栏也全部空白。
    这类"表格填了没有"属于图纸完整性，之前完全没查。
    """
    issues: list[Issue] = []

    # 张数：找到含「共…张…第…张」的文字，看有没有数字
    # 按 TITLE_BLOCK_CHECKS 关闭（属图框完整性，用户 2026-10-13 决定）
    if TITLE_BLOCK_CHECKS.get("张数", True):
        for t in model.texts:
            s = (t.get("text") or "").strip()
            if "共" in s and "张" in s and "第" in s and not re.search(r"\d", s):
                issues.append(Issue(
                    rule_id="CORE-TB-SHEETS", category="标题栏", severity=SEVERITY_MINOR,
                    title="图纸张数未填写（共 张 / 第 张）",
                    detail=f"标题栏中的『{s}』只有栏目名、没有数字",
                    suggestion="填写『共 N 张 第 N 张』",
                    standard="GB/T 10609.1", location="图框右下角"))
                break

    # 签署栏：标签存在但没有任何签署内容
    # 按 TITLE_BLOCK_CHECKS 关闭（属图框完整性，用户 2026-10-13 决定）
    if TITLE_BLOCK_CHECKS.get("签署栏", True):
        from sw_qc_parser import _pair_value
        sign_fields = ["设计", "校对", "审核", "工艺", "标准化", "质量", "批准"]
        present_labels = [t for t in model.texts
                          if (t.get("text") or "").strip() in sign_fields]
        if present_labels:
            signed = 0
            for t in present_labels:
                v = _pair_value(model.texts, t,
                                {(round(x["x"], 2), round(x["y"], 2))
                                 for x in present_labels})
                if v and re.search(r"[\u4e00-\u9fa5A-Za-z]", v):
                    signed += 1
            if signed == 0:
                issues.append(Issue(
                    rule_id="CORE-TB-SIGN", category="标题栏", severity=SEVERITY_MINOR,
                    title="签署栏全部空白（设计/校对/审核/工艺/标准化/质量/批准）",
                    detail=f"标题栏有 {len(present_labels)} 个签署栏位，但没有任何签署内容；"
                           f"若图纸尚未发布可忽略",
                    suggestion="发布/归档前完成签署",
                    standard="GB/T 10609.1", location="图框右下角"))
    return issues


def check_dimension_coverage(model) -> list[Issue]:
    issues: list[Issue] = []

    # 整图豁免（技术要求以 3D 为准 / 塑胶件）：尺寸完整性不作为检查对象。
    # 用户明确说塑胶件「甚至不做检测对象，轻微都算不上，可以忽略」。
    if (getattr(model, "dimension_waivers", None) or {}).get("waive_all"):
        return issues

    circles = [c for c in model.circles if c["r"] > 0.5
               and not _is_center_mark(c)]
    dia_dims = [d for d in model.dimensions if d.get("dim_type") in ("diameter", "radius")]
    linear_dims = [d for d in model.dimensions if d.get("dim_type") in ("linear", "aligned")]
    text_dims = getattr(model, "dimension_texts", [])

    if circles and not dia_dims and not text_dims:
        issues.append(Issue(
            rule_id="CORE-DIM-CIRCLE", category="尺寸", severity=SEVERITY_CRITICAL,
            title=f"检出 {len(circles)} 个圆但无直径/半径标注",
            detail=f"圆心示例：{[(round(c['cx'], 1), round(c['cy'], 1)) for c in circles[:3]]}",
            suggestion="为每个圆补 φ 或 R 标注；阵列孔可用『n×φd』简化标注",
            standard="GB/T 4458.4", location="全图"))

    if not linear_dims and not dia_dims and not text_dims:
        issues.append(Issue(
            rule_id="CORE-DIM-NONE", category="尺寸", severity=SEVERITY_CRITICAL,
            title="图纸未检出任何尺寸标注",
            detail="DIMENSION 实体与尺寸文字均未找到，零件无法按图制造",
            suggestion="补齐全部定形尺寸与定位尺寸", standard="GB/T 4458.4"))
    elif not model.dimensions and text_dims:
        # 炸开图纸：尺寸只以文字存在，能查到内容但无法与几何自动关联
        sample = "、".join((t.get("text") or "").strip() for t in text_dims[:5])
        issues.append(Issue(
            rule_id="CORE-DIM-TEXTONLY", category="尺寸", severity=SEVERITY_MINOR,
            title=f"尺寸以文字形式存在（{len(text_dims)} 处），无结构化标注实体",
            detail=f"示例：{sample}。可检查文字内容，但无法自动核对「每个几何要素"
                   f"是否都有尺寸」——漏标检查能力受限",
            suggestion="需要完整漏标检查时，请提供含 DIMENSION 实体的 DXF 原图",
            standard="GB/T 4458.4", location="全图"))

    if len(circles) > len(dia_dims) and dia_dims:
        issues.append(Issue(
            rule_id="CORE-DIM-CIRCLE-PART", category="尺寸", severity=SEVERITY_MAJOR,
            title=f"圆数量({len(circles)})多于直径标注数({len(dia_dims)})",
            detail="存在未被直径标注覆盖的圆（含同心圆时可能为正常情况，需人工确认）",
            suggestion="核对是否所有圆都有对应标注，阵列孔用 n×φd 形式",
            standard="GB/T 4458.4", location="全图"))

    # 倒角/圆角漏标（免费版 3.1 常见漏标项）
    chamfer_features = [f for f in getattr(model, "_features", []) if f.type == "倒角"]
    rounded = [f for f in getattr(model, "_features", []) if f.type == "圆角"]
    text_upper = (model.all_text or "").upper()
    if (chamfer_features or rounded) and not re.search(r"\b[CR]\s*\d", text_upper):
        issues.append(Issue(
            # ★级别：一般（用户 2026-10-13 批注，原为 🟢轻微）
        rule_id="CORE-DIM-CR", category="尺寸", severity=SEVERITY_MAJOR,
            title=f"检出 {len(chamfer_features)} 处倒角、{len(rounded)} 处圆角，未见 C/R 标注",
            detail="若技术要求已有『未注圆角/倒角』条款可忽略",
            suggestion="关键倒角/圆角必须单独标注，其余用技术要求统一覆盖",
            standard="GB/T 4458.4"))

    return issues


def _is_center_mark(circle: dict) -> bool:
    """中心标记画法：很小的圆或位于标注层。"""
    return circle["r"] < 0.6


# --------------------------------------------------------------------------
# 三、公差检查（对应免费版第四节）
# --------------------------------------------------------------------------
def check_tolerances(model) -> list[Issue]:
    issues: list[Issue] = []
    dims = model.dimensions

    if dims:
        without_tol = [d for d in dims if not _dim_has_tolerance(d)]
        if len(without_tol) == len(dims):
            # DXF 常把尺寸炸开成「几何 + 文字」，此时公差写在 TEXT 上而不是
            # DIMENSION 实体里。先看文字型尺寸里有没有公差代号，避免误报。
            text_tol = [t for t in getattr(model, "dimension_texts", [])
                        if _text_has_tolerance(t.get("text", ""))]
            if text_tol:
                sample = "、".join(t["text"].strip() for t in text_tol[:4])
                issues.append(Issue(
                    rule_id="CORE-TOL-TEXTONLY", category="公差",
                    severity=SEVERITY_MINOR,
                    title=f"尺寸公差以文字形式标注（{len(text_tol)} 处），未落在标注实体上",
                    detail=f"示例：{sample}。公差信息完整，但结构化数据不可靠，"
                           f"无法与具体几何自动关联",
                    suggestion="在 CAD 中把公差写入尺寸属性或标注覆盖，而非散落文本",
                    standard="GB/T 4458.4"))
            else:
                issues.append(Issue(
                    rule_id="CORE-TOL-ALL", category="公差", severity=SEVERITY_MAJOR,
                    title=f"全部 {len(dims)} 个尺寸均未标注公差",
                    detail="若全部由 GB/T 1804 一般公差覆盖可接受，但配合尺寸必须有单独公差",
                    suggestion="对轴承配合、键槽、销孔等配合尺寸补单独公差",
                    standard="GB/T 1804 / GB/T 1801"))

    # 几何公差完全缺失（符号形式 + 中文术语形式都算）
    gdt_terms = GDT_SYMBOLS + ["同轴度", "圆跳动", "全跳动", "圆柱度", "平面度",
                              "垂直度", "平行度", "位置度", "对称度", "圆度",
                              "直线度", "倾斜度", "端面跳动", "轮廓度"]
    has_gdt = any(term in (t.get("text") or "") for t in model.texts
                  for term in gdt_terms)
    if model.circles and not has_gdt:
        issues.append(Issue(
            # ★级别：一般（用户 2026-10-13 批注，原为 🟢轻微）
            rule_id="CORE-GDT", category="公差", severity=SEVERITY_MAJOR,
            title="未检出几何公差（GD&T）标注",
            detail="图上没有几何公差框格，也没有引用未注几何公差标准",
            suggestion="按配合要求补几何公差框格，或引用 GB/T 1184-K 未注几何公差",
            standard="GB/T 1182 / GB/T 1184"))

    # 公差数值写法规范（免费版 4.3）
    for t in model.texts:
        s = (t.get("text") or "")
        if re.search(r"[^0-9]\.\d", s) and not re.search(r"0\.\d", s):
            issues.append(Issue(
                rule_id="CORE-TOL-FMT", category="公差", severity=SEVERITY_MINOR,
                title="公差值写法不规范（缺少前导 0）",
                detail=f"文本『{s.strip()[:40]}』中出现 .02 形式",
                suggestion="按标准写作 φ0.02 而非 φ.02", standard="GB/T 1182"))
            break

    return issues


TOL_CODE_SEARCH_RE = re.compile(
    r"(?<=\d)[A-Za-z]{1,2}\d{1,2}(?![0-9])")      # φ30H7 / 40h6 / 8N9


def _text_has_tolerance(text: str) -> bool:
    """文本中是否含公差信息：公差带代号、极限偏差、± 或配合写法。"""
    t = text or ""
    if not t:
        return False
    if "±" in t:
        return True
    # 极限偏差：+0.02 / -0.01（前面不能紧邻数字，避免把 699-2015 当成偏差）
    if re.search(r"(?<![\d.])[+\-]\s*\d+(?:\.\d+)?", t):
        return True
    if TOL_CODE_SEARCH_RE.search(t):
        return True
    if "/" in t and re.search(r"\d", t):        # φ30H7/g6 配合写法
        return True
    return False


def _dim_has_tolerance(dim: dict) -> bool:
    """判断一个尺寸是否带公差（显示文字里的代号/偏差，或上下偏差字段）。"""
    text = _dim_display(dim)
    if text and _text_has_tolerance(text):
        return True
    try:
        if float(dim.get("upper_dev") or 0) != 0 or float(dim.get("lower_dev") or 0) != 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


# --------------------------------------------------------------------------
# 四、表面粗糙度检查（对应免费版第五节）
# --------------------------------------------------------------------------
# 统注关键词：出现它 + 附近一个 Ra 优先数值，就说明"整体已规定粗糙度"。
#
# 用户实测反馈（2026-10-13）：「如果图纸上有标识说明 如『其余 粗糙度多少』，
# 或者『全部 粗糙度多少』，这个就已经说明了有标注粗糙度，没有漏标。」
# 所以只认「其余」不够，必须把「全部 / 所有 / 未注 / 全图 / 整体」一并认下来 ——
# 否则这些图纸会被误报「未检出任何表面粗糙度」。
_BLANKET_RA_RE = re.compile(r"其余|全部|所有|未注|全图|整体|一般表面")
# 统注说明一般很短（「其余」「全部」「未注粗糙度」）。限长可避免把一整段技术要求里
# 恰好出现的「全部」当成统注，从而误吞附近的尺寸数字（6.3、1.6 既是 Ra 优先数、
# 也是常见尺寸值，不加限制会互相污染）。
_BLANKET_MAX_LEN = 14


def _collect_roughness(model) -> list[tuple[float, str, str]]:
    """
    收集图上所有表面粗糙度信息，返回 [(Ra值, 原文, 来源说明)]。

    为什么要分四条路（少认一条就会误报"未检出粗糙度"）：
      • 中文图纸常见写「Ra 0.8」
      • SolidWorks 的表面粗糙度符号是独立块（SW_SFSYMBOL_n），数字写在块里，
        旁边没有 "Ra" 两个字 —— 只按 Ra 关键字找必然漏判
      • 统注：「其余 1.6」「全部 3.2」「未注粗糙度 6.3」—— 有一条就说明整体规定了
      • 只有符号没有数字（如「其余▽」）—— 也算已规定，值记 None

    实测：一张图上明明标了 0.8 和「其余 1.6」，工具却报「未检出任何粗糙度」，
    就是因为只认字面 "Ra"。
    """
    from sw_qc_gb import GB_T_131_RA_SERIES

    found: list[tuple[float, str, str]] = []
    for t in model.texts:
        s = (t.get("text") or "").strip()
        if not s:
            continue
        block = str(t.get("block") or "")

        for m in RA_RE.finditer(s):
            found.append((float(m.group(1)), s, "Ra 标注"))

        # 表面粗糙度符号块（SolidWorks: SW_SFSYMBOL_n）
        if re.search(r"SFSYMBOL|SURFACE|FINISH|ROUGH|粗糙", block, re.IGNORECASE):
            hit = False
            for m in re.finditer(r"\d+(?:\.\d+)?", s):
                v = float(m.group(0))
                if v in GB_T_131_RA_SERIES:
                    found.append((v, s, f"粗糙度符号（{block}）"))
                    hit = True
            if not hit:
                # 只有符号、数字读不出：仍然说明"图上规定了粗糙度"，不能报漏标
                found.append((None, s or f"符号块 {block}",
                              f"粗糙度符号（{block}，未读到数值）"))

    # 统注：其余 / 全部 / 所有 / 未注 / 全图 / 整体
    for t in model.texts:
        s = (t.get("text") or "").strip()
        if not s or len(s) > _BLANKET_MAX_LEN or not _BLANKET_RA_RE.search(s):
            continue

        # ① 数值就在同一段文字里（「其余Ra3.2」「全部 3.2」）
        got = False
        for m in re.finditer(r"\d+(?:\.\d+)?", s):
            v = float(m.group(0))
            if v in GB_T_131_RA_SERIES:
                found.append((v, s, "整体统注（同段文字）"))
                got = True
                break
        if got:
            continue

        # ② 数值在附近（SolidWorks 常把数值放进符号块，与「其余」是两个实体）
        for other in model.texts:
            txt = (other.get("text") or "").strip()
            if not re.fullmatch(r"\d+(?:\.\d+)?", txt):
                continue
            v = float(txt)
            if v not in GB_T_131_RA_SERIES:
                continue
            same_block = str(other.get("block") or "") == str(t.get("block") or "")
            near = math.hypot(other["x"] - t["x"], other["y"] - t["y"]) <= 12.0
            if same_block or near:
                found.append((v, f"{s} {v:g}", "整体统注（邻近数值）"))
                break
    return found


def check_roughness(model) -> list[Issue]:
    issues: list[Issue] = []
    ra_hits = _collect_roughness(model)

    # 零件类型决定「要不要标粗糙度」（用户实测反馈 2026-10-13）：
    #   机加件 / 组合件 → 需要；钣金件 / 塑胶件 → 不需要（报出来就是误报）
    # 类型由 sw_qc_parttype.detect_part_type 判定，也可被用户手动覆盖。
    ptype = str(getattr(model, "part_type", "") or "")
    pconf = float(getattr(model, "part_type_confidence", 0.0) or 0.0)
    try:
        from sw_qc_parttype import needs_roughness
        need_ra, why_ra = needs_roughness(ptype, pconf)
    except Exception:
        need_ra, why_ra = True, ""

    if not ra_hits and (model.circles or model.lines) and need_ra:
        detail = ("未找到 Ra 标注、表面粗糙度符号块，"
                  "也没有『其余/全部 N』之类的整体统注")
        if ptype:
            detail += f"。零件类型判定为「{ptype}」（置信度 {pconf:.0%}）：{why_ra}"
        issues.append(Issue(
            rule_id="CORE-RA-MISSING", category="粗糙度", severity=SEVERITY_MAJOR,
            title="未检出任何表面粗糙度标注",
            detail=detail,
            suggestion="按配合性质标注 Ra：轴承面 0.4-0.8，一般配合面 1.6-3.2，"
                       "非配合面 6.3-12.5。整图统一要求可用『其余 Ra N』或『全部 Ra N』统注",
            standard="GB/T 131"))

    # Ra 值是否为 GB/T 131 优先数
    from sw_qc_gb import GB_T_131_RA_SERIES
    for value, raw, _src in ra_hits:
        if value is None:
            continue                    # 只有符号没有数值：算已规定，不判优先数
        if value not in GB_T_131_RA_SERIES:
            issues.append(Issue(
                # ★级别：一般（用户 2026-10-13 两次确认：最初批注"严重"，
                # 随后明确改为"一般" —— 非优先数 Ra 只是不便，不是制造硬伤）
                rule_id="CORE-RA-SERIES", category="粗糙度", severity=SEVERITY_MAJOR,
                title=f"Ra {value:g} 不是 GB/T 131 优先数系列值",
                detail=f"来源文本『{raw[:40]}』",
                suggestion="改用优先数系列：" + "、".join(
                    str(v) for v in GB_T_131_RA_SERIES if 0.1 <= v <= 25),
                standard="GB/T 131"))
            break

    # 公差-粗糙度匹配（免费版第五节）
    if ra_hits:
        ra_near = ra_hits[0][0]
        for dim in model.dimensions:
            text = _dim_display(dim)
            m = TOL_CODE_RE.search(text)
            if not m:
                continue
            from sw_qc_gb import tolerance_grade_consistency
            rng = tolerance_grade_consistency(m.group(1))
            if rng and not (rng[0] * 0.5 <= ra_near <= rng[1] * 2):
                issues.append(Issue(
                    # ★级别：一般（用户 2026-10-13 确认，原为 🟢轻微）
                    rule_id="CORE-RA-TOL", category="粗糙度", severity=SEVERITY_MAJOR,
                    title=f"公差 {m.group(1)} 与 Ra {ra_near:g} 可能不匹配",
                    detail=f"该公差等级经验区间为 Ra {rng[0]}~{rng[1]}",
                    suggestion="公差越紧则粗糙度要求越高，请核对",
                    standard="GB/T 131"))
                break
    return issues


# --------------------------------------------------------------------------
# 表面处理耐蚀性检查（用户 2026-10-13 提出）
# --------------------------------------------------------------------------
# 用户原话：「技术要求中一般会写表面处理，这个需要分析表面处理是否合理，
#   会不会有不耐腐蚀、不耐生锈的风险……比如我写个表面发黑，
#   这是不是要做提醒，发黑引起容易生锈的风险」
#
# 判据来自规格库 SURFACE_TREATMENT_CORROSION（工程经验值，可修改）：
#   • 耐蚀性"弱"的处理（发黑/磷化/镀锡/导电氧化）**必须配套防锈措施**
#     —— 发黑件标准做法是"发黑后浸油"，单独发黑在潮湿环境会锈
#   • 碳钢/合金钢/铸铁件**完全没规定表面处理** → 必然锈蚀
#
# 这一条**不需要 AI**：耐蚀等级是查表就能定的。AI 只在"给定使用环境下是否合理"
# 上有增量价值，而图纸通常不写使用环境，AI 也缺输入 —— 所以先做确定的。
def _treatments_in_text(text: str) -> list[tuple[str, dict]]:
    """
    找出文中出现的表面处理，**长词优先、避免子串重复**。

    例："阳极氧化" 命中后，不该再因为含 "氧化" 而记一条"发黑类氧化"。
    """
    from sw_qc_gb import SURFACE_TREATMENT_CORROSION
    spans: list[tuple[int, int, str, dict]] = []
    low = (text or "").lower()
    for key, info in SURFACE_TREATMENT_CORROSION.items():
        k = key.lower()
        start = 0
        while True:
            i = low.find(k, start)
            if i < 0:
                break
            spans.append((i, i + len(k), key, info))
            start = i + len(k)
    # 去掉被更长匹配完全包含的那些
    out: list[tuple[str, dict]] = []
    for s, e, key, info in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if any(o_s <= s and e <= o_e and (o_e - o_s) > (e - s)
               for o_s, o_e, _k, _i in spans):
            continue
        if key not in [k for k, _ in out]:
            out.append((key, info))
    return out


def check_surface_treatment(model) -> list[Issue]:
    """表面处理耐蚀性检查。返回 list[Issue]。"""
    from sw_qc_gb import CORROSION_PRONE_MATERIALS

    issues: list[Issue] = []
    tb = getattr(model, "title_block", {}) or {}
    material = str(tb.get("材料", "") or "").strip()
    text = "\n".join([(getattr(model, "all_text", "") or "")]
                     + [str(n) for n in (getattr(model, "tech_notes", []) or [])]
                     + [material])

    found = _treatments_in_text(text)

    # ① 该处理有"必须配套的措施"（requires 非空），图上却没写 → 提醒
    #
    # 注意**不能只看"弱"等级**：阳极氧化（中）必须封闭、镀铬（中）必须有铜镍底层，
    # 漏写这些一样会导致实际耐蚀不达标。判据是 requires 是否为空，不是等级高低。
    # 等级只决定提醒的响度：弱 → 一般；中 → 轻微。
    #
    # 「防锈/油封/浸油/涂油/封闭/封孔/涂装/喷漆」这类通用措施词算已配套 ——
    # 宁可放过，也不要因为措辞不同而误报。
    GENERIC_MEASURE = ("防锈", "油封", "浸油", "涂油", "封闭", "封孔",
                       "涂装", "喷漆", "喷塑", "电泳")
    for key, info in found:
        requires = list(info.get("requires") or [])
        if not requires:
            continue                    # 没有"必须配套"要求的处理，不查
        if any(r in text for r in requires) or any(g in text for g in GENERIC_MEASURE):
            continue
        need = "、".join(requires)
        weak = info.get("level") == "弱"
        issues.append(Issue(
            rule_id="CORE-SURF-WEAK", category="技术要求",
            severity=SEVERITY_MAJOR if weak else SEVERITY_MINOR,
            title=f"表面处理『{key}』需要配套措施，图上未写",
            detail=f"『{key}』{info.get('note', '')}。"
                   f"图上未见 {need} 之类的配套说明 —— "
                   + ("耐蚀性本就有限，缺配套措施在潮湿或户外环境会锈蚀"
                      if weak else "缺了这一步实际耐蚀会不达标"),
            suggestion=f"补充配套要求（{need}），"
                       + ("或改用耐蚀更好的处理（镀锌/达克罗/喷塑等）" if weak else ""),
            standard="—", location="技术要求",
            evidence=f"检出的表面处理：{key}（耐蚀性 {info.get('level')}）"))

    # ② 易锈材料却完全没规定表面处理
    if not found and material and CORROSION_PRONE_MATERIALS.match(material):
        issues.append(Issue(
            rule_id="CORE-SURF-MISSING", category="技术要求", severity=SEVERITY_MAJOR,
            title=f"材料『{material}』未规定表面处理，存在锈蚀风险",
            detail=f"{material} 属碳钢/合金钢类，不做表面处理在常规环境下会生锈；"
                   f"图中未见任何表面处理要求",
            suggestion="补充表面处理要求（镀锌/发黑后浸油/喷塑/达克罗等），"
                       "或注明使用环境与防锈油封要求",
            standard="—", location="技术要求"))
    return issues


# --------------------------------------------------------------------------
# 五、技术要求完整性（对应免费版 6.2）
# --------------------------------------------------------------------------
TECH_NOTE_PATTERNS = {
    "未注尺寸公差": (["GB/T 1804", "GB1804", "GB 1804", "未注尺寸公差",
                "未注公差", "一般公差"], SEVERITY_MAJOR,
                "增加『未注尺寸公差按 GB/T 1804-m』"),
    "未注几何公差": (["GB/T 1184", "GB1184", "GB 1184", "未注几何公差"],
                SEVERITY_MAJOR, "增加『未注几何公差按 GB/T 1184-K』"),
    "未注圆角/倒角": (["未注圆角", "未注倒角", "R0", "圆角", "倒角"], SEVERITY_MINOR,
                 "增加『未注圆角 R2，未注倒角 C1』"),
    "去毛刺": (["去毛刺", "毛刺", "倒棱"], SEVERITY_MINOR, "增加『去除毛刺』"),
    "棱边倒钝": (["倒钝", "棱边"], SEVERITY_MINOR, "增加『棱边倒钝』"),
}


def check_tech_notes(model) -> list[Issue]:
    issues: list[Issue] = []
    from sw_qc_rules import text_matches
    for name, (patterns, sev, sug) in TECH_NOTE_PATTERNS.items():
        # 用归一化匹配：图上是「未注尺寸公差按GB1804-m级执行」，
        # 模式里写 "GB/T 1804" 也要能命中，否则会误报"未引用一般公差标准"
        if not text_matches(model.all_text or "", patterns):
            issues.append(Issue(
                rule_id=f"CORE-NOTE-{name}", category="技术要求", severity=sev,
                title=f"技术要求缺少『{name}』条款", detail="按常规范例应包含该条款",
                suggestion=sug, standard="GB/T 4458"))
    return issues


# --------------------------------------------------------------------------
# 六、封闭尺寸链 / 冗余尺寸（对应免费版 3.2、3.3）
# --------------------------------------------------------------------------
def _dim_value(dim: dict) -> float:
    """
    取尺寸的「图上标称值」。

    必须优先用 `value`（由图上显示文字解析），而不是 `measurement`
    （原始几何距离，会被 DIMLFAC 之类的比例因子放大/缩小）。
    实测踩坑：图上标 14、几何距离 28，用 measurement 会报出图纸上根本没有的 28。
    """
    v = dim.get("value")
    if v is None:
        v = dim.get("measurement")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _dim_display(dim: dict) -> str:
    """尺寸在图上显示的文字（含公差写法）。"""
    return str(dim.get("display") or dim.get("text") or "")


def check_dimension_chain(model, tol: float = 0.05) -> list[Issue]:
    """
    简化实现：找同一方向上「两个尺寸之和 ≈ 第三个尺寸」的三元组，
    即典型的链式尺寸与总尺寸同时标注（过约束）。
    """
    issues: list[Issue] = []
    linear = [d for d in model.dimensions
              if d.get("dim_type") in ("linear", "aligned")]
    if len(linear) < 3:
        return issues

    # 只有「同一处、同一方向」的几个尺寸才谈得上构成封闭链。
    # 不限定空间范围的话，14(左视图长度) + 3(某处⌀3) ≈ 17(另一处⌀17) 这种
    # 纯属数字凑巧的组合会被报成封闭链 —— 实测就误报过一次。
    span_limit = 60.0

    for i in range(len(linear)):
        for j in range(i + 1, len(linear)):
            a, b = _dim_value(linear[i]), _dim_value(linear[j])
            if a <= 0 or b <= 0:
                continue
            for k in range(len(linear)):
                if k in (i, j):
                    continue
                c = _dim_value(linear[k])
                if c <= 0 or abs((a + b) - c) > tol:
                    continue
                locs = [linear[i].get("location") or (0, 0),
                        linear[j].get("location") or (0, 0),
                        linear[k].get("location") or (0, 0)]
                xs = [p[0] for p in locs]
                ys = [p[1] for p in locs]
                if (max(xs) - min(xs)) > span_limit or (max(ys) - min(ys)) > span_limit:
                    continue        # 相距太远，不可能是一条尺寸链
                # 真正的尺寸链，几个尺寸通常排在同一行（Y 相近）；
                # 没有这个特征就只是数字凑巧相等，不能报。
                row_shared = (max(ys) - min(ys)) <= 3.0
                col_shared = (max(xs) - min(xs)) <= 3.0
                if not (row_shared or col_shared):
                    continue
                issues.append(Issue(
                    # ★级别：严重（用户 2026-10-13 批注，原为 🟢轻微）——
        # 封闭尺寸链是过约束，会出现"到底按哪个尺寸加工"的歧义。
        rule_id="CORE-CHAIN", category="尺寸", severity=SEVERITY_CRITICAL,
                    title="疑似封闭尺寸链（过约束）",
                    detail=f"尺寸 {a:g} + {b:g} ≈ {c:g}，且三处标注集中，"
                           f"可能同时标注形成封闭链",
                    suggestion="核对是否过约束；将其中非功能性的一个改为参考尺寸（加括号）",
                    standard="GB/T 4458.4"))
                return issues     # 报一次即可，避免组合爆炸

    # 冗余尺寸：同一测量值且标注位置几乎重合，才算重复标注。
    # 注意：不同位置出现相同的尺寸值（如两段轴都是 40）是完全正常的，
    # 第一版只比较数值，会把正常图纸误判为「重复标注」。
    groups: dict[float, list[dict]] = {}
    for d in linear:
        m = round(_dim_value(d), 3)
        if m > 0:
            groups.setdefault(m, []).append(d)
    dupes = {}
    for m, items in groups.items():
        if len(items) < 2:
            continue
        close = 0
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                la = items[a].get("location") or (0, 0)
                lb = items[b].get("location") or (0, 0)
                if math.hypot(la[0] - lb[0], la[1] - lb[1]) <= 5.0:
                    close += 1
        if close:
            dupes[m] = len(items)
    if dupes:
        items_txt = "、".join(f"{k:g}(×{v})" for k, v in list(dupes.items())[:5])
        issues.append(Issue(
            # ★级别：一般（用户 2026-10-13 批注，原为 🟢轻微）
        rule_id="CORE-DUP", category="尺寸", severity=SEVERITY_MAJOR,
            title=f"存在重复标注的尺寸值：{items_txt}",
            detail="同一测量值标注在同一位置附近，属重复标注或对称件未用对称符号",
            suggestion="对称零件标注对称符号；重复尺寸删除并注明『同左』",
            standard="GB/T 4458.4"))
    return issues


# --------------------------------------------------------------------------
# 七、视图质量（对应免费版第七节）
# --------------------------------------------------------------------------
def check_views(model) -> list[Issue]:
    issues: list[Issue] = []
    scales = set(re.findall(r"\b([0-9]+:[0-9]+)\b", model.all_text or ""))
    if not scales and not str((model.title_block or {}).get("比例", "")).strip():
        issues.append(Issue(
            rule_id="CORE-VIEW-SCALE", category="视图", severity=SEVERITY_MINOR,
            title="未检出视图比例标注",
            detail="标题栏与视图中都没有形如 1:1 的比例",
            suggestion="在标题栏标注绘图比例；若有剖视图或局部放大图，"
                       "在相应视图上方单独标注其比例",
            standard="GB/T 4458.1"))

    if len(scales) > 1 and not re.search(r"放大|局部|详图|剖视", model.all_text or ""):
        issues.append(Issue(
            rule_id="CORE-VIEW-SCALE-MULTI", category="视图", severity=SEVERITY_MINOR,
            title=f"检出多个比例 {sorted(scales)}，但未见视图标注说明",
            detail="多比例图纸应说明哪个视图使用哪个比例",
            suggestion="在各视图上方标注其比例", standard="GB/T 4458.1"))
    return issues


# --------------------------------------------------------------------------
# 八、盲孔深度（免费版 3.1 常见漏标项）
# --------------------------------------------------------------------------
def check_blind_holes(model) -> list[Issue]:
    """检测有圆但缺少深度标注的情况（用 ↧ 或 深度 关键字近似判断）。"""
    haystack = model.all_text or ""
    holes = [c for c in model.circles if 1.0 <= c["r"] <= 40.0]
    if not holes:
        return []
    if re.search(r"[↧⇩]|深度|深\s*\d|DEPTH", haystack, re.IGNORECASE):
        return []
    return [Issue(
        # ★级别：严重（用户 2026-10-13 批注，原为 🟢轻微）——
        # 盲孔不标深度就无法加工，属会造成理解错误的硬伤。
        rule_id="CORE-HOLE-DEPTH", category="尺寸", severity=SEVERITY_CRITICAL,
        title=f"检出 {len(holes)} 个圆孔特征，未见深度标注",
        detail="若均为通孔可忽略；盲孔必须标注深度",
        suggestion="盲孔标注 φd ↧深度，或注明『全部通孔』",
        standard="GB/T 4458.4")]


def find_cutting_planes(model) -> dict[str, tuple]:
    """
    识别剖切符号：同一个大写字母成对出现在一个视图的两侧。

    GB/T 4458.6 规定剖切符号由「剖切位置线 + 投影方向箭头 + 字母」组成，
    同一处剖切的两端用同一个字母。所以判据是：
      • 单个大写字母（不含 I、O、Z，标准要求避开）
      • 恰好出现两次
      • 两点在同一水平线或同一竖直线上（|Δy| 或 |Δx| 很小）
      • 分居某点两侧（说明是跨过视图的一条剖切线）

    返回 {字母: ((x1,y1), (x2,y2))}
    """
    from sw_qc_gb import SECTION_LETTERS

    singles: dict[str, list[tuple[float, float]]] = {}
    for t in model.texts:
        s = (t.get("text") or "").strip()
        if len(s) == 1 and s in SECTION_LETTERS:
            singles.setdefault(s, []).append((t["x"], t["y"]))

    result: dict[str, tuple] = {}
    for letter, pts in singles.items():
        if len(pts) < 2:
            continue
        # 同一个字母可能出现多次（例如有个 'A' 是基准符号，另外两个才是剖切符号），
        # 所以不能要求"恰好两个"，而要在所有点里找出最合理的一对：
        # 同一行或同一列、且间距最大的那一对。
        best: tuple | None = None
        best_span = 0.0
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[j]
                same_row = abs(y1 - y2) <= 3.0 and abs(x1 - x2) > 1.0
                same_col = abs(x1 - x2) <= 3.0 and abs(y1 - y2) > 1.0
                if not (same_row or same_col):
                    continue
                span = max(abs(x1 - x2), abs(y1 - y2))
                if span > best_span:
                    best_span = span
                    best = (pts[i], pts[j])
        if best:
            result[letter] = best
    return result


def find_section_view_labels(model) -> set[str]:
    """找出图上的剖视图名称（如 A-A、B—B、A-A 剖面）。"""
    labels: set[str] = set()
    for t in model.texts:
        s = (t.get("text") or "")
        for m in re.finditer(r"([A-Z])\s*[-—－–]\s*([A-Z])", s):
            if m.group(1) == m.group(2):
                labels.add(m.group(1))
    return labels


def check_section_views(model) -> list[Issue]:
    """
    检查「有剖切符号，却没有对应的剖视图名称」。

    实测：一张图上 A、B 两个剖切符号都画了（成对分布在视图左右/上下），
    但全图找不到 A-A / B-B 任何标记 —— 而图里确实有剖面线，说明剖视图存在
    却没标注名称。这是真缺陷，之前完全没查。

    GB/T 4458.6：剖视图必须用与剖切符号相同的字母标注，且注在视图上方。
    """
    issues: list[Issue] = []
    planes = find_cutting_planes(model)
    if not planes:
        return issues

    labels = find_section_view_labels(model)
    has_hatch = getattr(model, "hatch_count", 0) > 0

    for letter in sorted(planes):
        if letter in labels:
            continue
        detail = (f"图中存在剖切符号 {letter}（起点 "
                  f"({planes[letter][0][0]:.0f},{planes[letter][0][1]:.0f})、"
                  f"({planes[letter][1][0]:.0f},{planes[letter][1][1]:.0f})），"
                  f"但全图找不到标注为『{letter}-{letter}』的剖视图名称")
        if has_hatch:
            detail += "。图中有剖面线，剖视图可能已画出但未标注名称"
        else:
            detail += "。也未检出剖面线，对应剖视图可能缺失"
        issues.append(Issue(
            rule_id="CORE-SECTION", category="视图", severity=SEVERITY_MAJOR,
            title=f"缺少 {letter}-{letter} 剖视图（或未标注名称）",
            detail=detail,
            suggestion=f"在对应剖视图上方标注『{letter}-{letter}』；"
                       f"若该剖视图确实未画，请补画或删掉 {letter} 剖切符号",
            standard="GB/T 4458.6", location=f"视图 {letter}"))
    return issues


def _dim_deviations(dim: dict) -> tuple[float, float]:
    """
    取尺寸的上下偏差（mm）。优先用结构化字段（含 DIMSTYLE 里的 dimtp/dimtm），
    没有就退回从显示文字解析：±0.1 / +0.05/-0.02 / +0.050。
    """
    up = dim.get("upper_dev") or 0.0
    lo = dim.get("lower_dev") or 0.0
    try:
        up, lo = float(up), float(lo)
    except (TypeError, ValueError):
        up = lo = 0.0
    if up or lo:
        return up, lo

    s = _dim_display(dim)
    m = re.search(r"±\s*(\d+(?:\.\d+)?)", s)
    if m:
        v = float(m.group(1))
        return v, -v
    m = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*/\s*[-−]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), -float(m.group(2))
    m = re.search(r"(?<![\d.])\+\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), 0.0
    return 0.0, 0.0


FIT_CODE_RE = re.compile(r"(?<=\d)\s*([A-Za-z]{1,2})(\d{1,2})(?![0-9])")


# --------------------------------------------------------------------------
# 公差「过严」判定（工艺可达性）
# --------------------------------------------------------------------------
# 设计口径（用户实测反馈后明确要求，2026-10-13）：
#
#   IT 等级只在图上写了**配合代号**（H7/g6…）时才是「设计者的明确规定」——
#   那时核对等级是否合理才有意义。
#
#   对于设计者自己填的**数值公差**（+0.050/0、±0.1），IT 等级没有约束力。
#   原先报「紧公差 0.050mm 不对应任何标准公差等级」，用户实测反馈：
#   「这个公差对于机加件来讲很容易做到，对应公差等级有什么用？」
#   —— 说得对。那等于在说图纸有错，其实没有，是误导。
#
#   唯一能挂上动作的是：**这个精度要求需要什么工艺、成本高不高**。
#   所以这里只把 IT 值当「难度刻度」，而且**只在过严时才出声**：
#
#     公差带 ≤ IT6 → 需磨削/研磨（成本显著上升）        ← 提醒
#     公差带 ≤ IT8 → 需精加工（精车/精铣/铰）           ← 提醒
#     更松         → 常规机加工可达                      ← 不提醒
#
#   阈值是**在尺寸段内比较**（IT6/IT8 的微米值随公称尺寸变化），
#   不做全局一刀切。用户选择的口径：紧于 IT9 才提（即 IT8 及更紧）。
def machining_hint(nominal: float, band_um: float) -> dict:
    """
    把公差带宽度翻译成「加工难度」。

    返回 {"grade": "≈IT9", "level": "ok|precision|grinding", "note": "…"}。
    level == "ok" 时 note 是「常规机加工可达」，调用方据此决定要不要提醒。
    """
    from sw_qc_gb import PREFERRED_GRADES, it_grade_of, it_tolerance

    out = {"grade": "—", "level": "ok", "note": ""}
    if nominal <= 0 or band_um <= 0:
        return out

    exact = it_grade_of(nominal, band_um)
    if exact:
        out["grade"] = exact
    else:
        # 不是标准等级也给一个「最接近的」，它在这里的作用是难度刻度，不是判定
        nearest, gap = None, None
        for g in PREFERRED_GRADES:
            v = it_tolerance(nominal, g)
            if not v:
                continue
            d = abs(v - band_um)
            if gap is None or d < gap:
                nearest, gap = g, d
        if nearest:
            out["grade"] = f"≈{nearest}"

    it6 = it_tolerance(nominal, "IT6")
    it8 = it_tolerance(nominal, "IT8")
    if it8 and band_um <= it8:
        if it6 and band_um <= it6:
            out["level"] = "grinding"
            out["note"] = "该公差需磨削或研磨才能稳定达到，加工成本明显上升"
        else:
            out["level"] = "precision"
            out["note"] = "该公差需精加工（精车/精铣/铰孔）"
    else:
        out["note"] = "常规机加工可达"
    return out


def tolerance_reference(model) -> list[dict]:
    """
    标准公差对照表：给图上每个带公差的尺寸附上参考信息。

    这不是"问题"，是**参考资料** —— 所以不放进问题清单，而是单独列表，
    避免把有用的信息变成噪声。

    两类尺寸分开处理（用户实测反馈后定的口径）：
      • **配合代号**（H7/g6）：查 GB/T 1800.1 的标准偏差值 —— 这是设计者
        的明确规定，核对它有意义
      • **数值公差**（+0.050/0）：不评判"对不对应标准等级"（那是误导），
        只给「需要什么工艺」——见 machining_hint()
    """
    from sw_qc_gb import fit_interval, it_tolerance

    rows: list[dict] = []
    for dim in model.dimensions:
        display = _dim_display(dim)
        nominal = _dim_value(dim)
        if not display or nominal <= 0:
            continue

        code_m = FIT_CODE_RE.search(display)
        if code_m:
            code = code_m.group(1) + code_m.group(2)
            iv = fit_interval(nominal, code)
            row = {"dim": display, "nominal": nominal, "type": "配合代号",
                   "code": code, "standard": "", "grade": "", "note": ""}
            if iv is None:
                it = it_tolerance(nominal, code_m.group(2))
                if it is not None:
                    row["standard"] = f"IT{code_m.group(2)} = {it / 1000:.3f} mm"
                    row["note"] = "该字母的基本偏差需查表，此处只给公差值"
            else:
                lo, hi = iv
                row["standard"] = f"{lo / 1000:+.3f} / {hi / 1000:+.3f} mm"
                row["grade"] = f"IT{code_m.group(2)}"
                row["note"] = "标准偏差（下/上）"
            rows.append(row)
            continue

        up, lo = _dim_deviations(dim)
        if not up and not lo:
            continue
        total_um = (up - lo) * 1000.0
        # 数值公差：**不再**评判「对不对应标准等级」（那是误导，见 machining_hint）。
        # 这里只给「这个精度需要什么工艺」——而且松公差照样说「常规机加工可达」，
        # 让工程师一眼确认这个要求不苛刻。
        hint = machining_hint(nominal, total_um)
        rows.append({
            "dim": display, "nominal": nominal, "type": "数值公差",
            "code": f"{up:+.3f}/{lo:+.3f}",
            "standard": f"公差带 {total_um:.0f} μm",
            "grade": hint["grade"],
            "note": hint["note"] or "—",
            "tight": hint["level"] != "ok",
        })
    return rows


def check_standard_tolerance(model) -> list[Issue]:
    """
    公差检查。**只报两类**：

      1. 配合代号里的公差等级不是常用优先数系（如 H3、h14）—— 这是硬性不一致
      2. **公差过严**：需要磨削/研磨或精加工才能稳定达到 —— 这是提醒，不是错误

    「数值公差不对应标准等级」**已删除**：设计者可以用任意公差值，
    说它「不对应标准等级」等于在说图纸有错，是误导（用户实测反馈）。
    数值公差现在只在**过严**时出声，判据见 machining_hint()。
    """
    from sw_qc_gb import PREFERRED_GRADES, it_tolerance

    issues: list[Issue] = []
    for dim in model.dimensions:
        display = _dim_display(dim)
        nominal = _dim_value(dim)
        if not display or nominal <= 0:
            continue

        code_m = FIT_CODE_RE.search(display)
        if code_m:
            grade = f"IT{int(code_m.group(2))}"
            if grade not in PREFERRED_GRADES:
                it = it_tolerance(nominal, code_m.group(2))
                issues.append(Issue(
                    rule_id="CORE-IT-GRADE", category="公差", severity=SEVERITY_MINOR,
                    title=f"公差等级 {grade} 不是常用优先等级",
                    detail=f"尺寸 {display}（{nominal:g}mm）标注了 "
                           f"{code_m.group(1)}{code_m.group(2)}；"
                           + (f"{grade} 的标准公差为 {it}μm。" if it else "")
                           + f"常用优先等级：" + "、".join(PREFERRED_GRADES[:7]),
                    suggestion="核对是否应为常用等级（IT5~IT11）",
                    standard="GB/T 1800.1"))
            continue

        up, lo = _dim_deviations(dim)
        if not up and not lo:
            continue
        total_um = (up - lo) * 1000.0
        hint = machining_hint(nominal, total_um)
        if hint["level"] == "ok":
            continue        # 常规机加工可达 → 不提醒（用户明确要求只在过严时出声）

        # 严重程度：磨削级会改变加工路线（可能需要磨床/外协），比精加工更值得注意，
        # 所以给「一般」；精加工给「轻微」。两者都**不是错误**，正文里必须说清楚。
        grinding = hint["level"] == "grinding"
        issues.append(Issue(
            rule_id="CORE-TOL-TIGHT", category="公差",
            severity=(SEVERITY_MAJOR if grinding else SEVERITY_MINOR),
            title=(f"公差 {total_um / 1000:.3f}mm 偏紧（{nominal:g}mm 处，"
                   f"{hint['grade']}）"),
            detail=(f"该尺寸公差带 {total_um:.0f}μm，相当于 {hint['grade']}；"
                    f"{hint['note']}。"
                    f"**这不是错误** —— 配合面本来就需要高精度；"
                    f"本提醒只是请确认精度要求与实际需要相符"),
            suggestion=("确认该精度是否必要；若为配合面则属正常，"
                        "否则可放宽到常用等级以降低加工成本"),
            standard="GB/T 1800.1", location=f"{nominal:g}mm 尺寸"))
    return issues


ALL_CORE_CHECKS = [
    check_title_block,
    check_title_block_completeness,
    check_dimension_coverage,
    check_dimension_completeness,
    check_overall_dimensions,
    check_tolerances,
    check_standard_tolerance,
    check_section_views,
    check_roughness,
    check_tech_notes,
    check_surface_treatment,      # 表面处理耐蚀性（用户 2026-10-13 提出）
    check_dimension_chain,
    check_views,
    # check_blind_holes 暂时停用：
    # 「圆孔有没有标深度」需要先把尺寸与具体几何关联起来才判得准。
    # 现在只靠「全图有没有 ↧/深度 字样」判断，实测在一张盲孔标注齐全的图上
    # 误报过。宁可少报，也不要给出错的结论 —— 等尺寸-要素关联做好再启用。
]


def run_core_checks(model) -> list[Issue]:
    model_issues: list[Issue] = []
    # 先算一次豁免（阵列/3D模型/塑胶件），后面的检查统一读它 ——
    # 避免每个检查各算一遍，也保证报告里展示的理由与检查用的理由一致。
    try:
        model.dimension_waivers = dimension_waivers(model)
    except Exception:
        model.dimension_waivers = {"waive_all": False, "reason": "", "notes": [],
                                   "array_sizes": [], "part_type": ""}
    for fn in ALL_CORE_CHECKS:
        try:
            model_issues.extend(fn(model))
        except Exception as exc:                      # 单个检查失败不影响整体
            model_issues.append(Issue(
                rule_id=f"CORE-ERR-{fn.__name__}", category="系统",
                severity=SEVERITY_MINOR, title=f"检查项 {fn.__name__} 执行失败",
                detail=repr(exc), suggestion="请提交 issue"))
    return model_issues