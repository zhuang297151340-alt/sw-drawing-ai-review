# -*- coding: utf-8 -*-
"""
sw_qc_features.py — 机械特征识别

实现上游 `skills/sw-drawing-qc-pro.md` 中描述的「特征识别」模块：
从 DXF 实体几何中推断「这段几何是什么」，而不是只看标注文字。

自动检测的 8 种机械特征（与文档一致）：
    轴承安装孔 / 轴承安装轴肩 / 键槽 / 螺纹孔 / 倒角 / 圆角 / 孔组(阵列) / 退刀槽

设计原则
    纯启发式几何推理，每个结论都带 `confidence` 与 `evidence`，
    让审核者能看懂「为什么判定为轴承孔」，而不是无条件相信一个黑盒结论。
    置信度低于阈值的候选会保留在结果里但标记为弱证据，由规则引擎决定是否报出。
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
from dataclasses import dataclass, field


@dataclass
class Feature:
    """一个识别到的机械特征。"""
    type: str                       # 特征类型名，如 "轴承安装孔"
    confidence: float               # 0.0 ~ 1.0
    location: tuple[float, float]   # 图纸坐标 (x, y)
    params: dict = field(default_factory=dict)   # 直径/半径/宽度等
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "confidence": round(self.confidence, 2),
            "location": [round(self.location[0], 3), round(self.location[1], 3)],
            "params": {k: (round(v, 4) if isinstance(v, float) else v)
                       for k, v in self.params.items()},
            "evidence": self.evidence,
        }


# --------------------------------------------------------------------------
# 几何辅助
# --------------------------------------------------------------------------
def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_concentric(c1, c2, tol: float) -> bool:
    return _dist(c1, c2) <= tol


def _angle_of(p1, p2) -> float:
    """线段角度，返回 0~180 度。"""
    ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180.0
    return ang


# --------------------------------------------------------------------------
# 各类特征检测
# --------------------------------------------------------------------------
def detect_concentric_groups(circles: list[dict], centers_marks: list[dict],
                             tol: float = 0.5) -> list[Feature]:
    """
    同心圆组：同一圆心上 2 个以上的圆。

    ⚠ 命名与参数刻意保持「几何描述」，不做零件类型判断。
      第一版把这个特征叫「轴承安装孔」，并按 2×r_max 编出一个直径报出去
      （实测：一张试剂瓶接头上报出「轴承安装孔 φ48.0」，而图纸上根本没有 φ48）。
      「2 个同心圆」在任何回转体零件上都成立，据此断定是轴承件是错的；
      而把一个几何计算值写成 φ48.0，等于凭空造了一个图纸上没有的尺寸。

      所以现在：
        • 类型名只说几何事实「同心圆组」
        • params 只给半径列表，不给「孔径」这种暗示图纸标注的量
        • 是否轴承件，只在图纸文字真的出现「轴承」时才另行推定
    """
    features: list[Feature] = []
    used: set[int] = set()

    for i, c1 in enumerate(circles):
        if i in used:
            continue
        group = [c1]
        for j, c2 in enumerate(circles):
            if j <= i or j in used:
                continue
            if _is_concentric((c1["cx"], c1["cy"]), (c2["cx"], c2["cy"]), tol):
                group.append(c2)
        if len(group) < 2:
            continue

        radii = sorted(g["r"] for g in group)
        conf = 0.45
        ev = [f"发现 {len(group)} 个同心圆，半径 {', '.join(f'R{r:g}' for r in radii)}"]

        has_mark = any(_is_concentric((c1["cx"], c1["cy"]), (m["cx"], m["cy"]), tol)
                       for m in centers_marks)
        if has_mark:
            conf += 0.20
            ev.append("存在中心标记/中心线，符合回转体特征")
        if len(radii) >= 3:
            conf += 0.15
            ev.append("三级以上同心结构")
        conf = min(conf, 0.90)

        for j, c in enumerate(circles):
            if any(c is g for g in group):
                used.add(j)

        features.append(Feature(
            type="同心圆组",
            confidence=conf,
            location=(c1["cx"], c1["cy"]),
            params={"ring_count": len(group),
                    "radii": [round(r, 3) for r in radii],
                    "min_radius": radii[0], "max_radius": radii[-1]},
            evidence=ev,
        ))

    return features


# 兼容旧名（外部若引用过）
detect_bearing_seats = detect_concentric_groups


def detect_keyways(lines: list[dict], arcs: list[dict],
                   tol: float = 0.15) -> list[Feature]:
    """
    键槽：一对「等长且投影重叠」的平行线 + 端部半圆。

    判据（硬性门槛，缺一不可）
      • 平行（角度差 < 1°）且间距 2~50mm
      • 两线长度相近（短/长 ≥ 0.7）—— 键槽两侧边等长
      • 沿长度方向投影重叠率 ≥ 0.7 —— 排除「尺寸界线与轮廓线」这类错位平行线
      • 端部存在半径 ≈ 槽宽/2 的半圆弧（跨度 120°~240°），且弧心到两侧线端
        距离 ≈ 槽宽/2

    为什么把圆弧设为必要条件：
      第一版只用「平行线对 + 长宽比」，在一张普通传动轴图上误报了 25 处键槽
      （轴的上/下轮廓线本身就是一对平行线）。按 GB/T 1095，键槽/滑槽端部一律
      为半圆，所以「无端弧」应直接判否，而不是给一个低置信度结论。
    """
    features: list[Feature] = []
    n = len(lines)

    for i in range(n):
        for j in range(i + 1, n):
            l1, l2 = lines[i], lines[j]
            a1 = _angle_of(l1["start"], l1["end"])
            a2 = _angle_of(l2["start"], l2["end"])
            dang = abs(a1 - a2)
            if min(dang, 180 - dang) > 1.0:
                continue

            len1 = _dist(l1["start"], l1["end"])
            len2 = _dist(l2["start"], l2["end"])
            if len1 < 2.0 or len2 < 2.0:
                continue

            # 两线等长性
            ratio_len = min(len1, len2) / max(len1, len2)
            if ratio_len < 0.7:
                continue

            # 垂直间距
            x1, y1 = l1["start"]
            x2, y2 = l1["end"]
            px, py = l2["start"]
            denom = math.hypot(x2 - x1, y2 - y1)
            if denom < 1e-9:
                continue
            width = abs((x2 - x1) * (y1 - py) - (x1 - px) * (y2 - y1)) / denom
            if not (2.0 <= width <= 50.0):
                continue

            # 沿长度方向的投影重叠率
            overlap = _projection_overlap(l1, l2)
            if overlap < 0.7:
                continue

            length = max(len1, len2)
            ratio = length / width
            if not (1.2 <= ratio <= 8.0):
                continue

            # 端部半圆（必要条件）
            half = width / 2.0
            r_tol = max(0.10 * half, 0.5)
            end_pts = [l1["start"], l1["end"], l2["start"], l2["end"]]
            arc_hit = None
            for arc in arcs:
                if abs(arc["r"] - half) > r_tol:
                    continue
                span = (arc.get("end_angle", 0.0) - arc.get("start_angle", 0.0)) % 360.0
                if not (120.0 <= span <= 240.0):
                    continue
                ac = (arc["cx"], arc["cy"])
                near = [p for p in end_pts if abs(_dist(ac, p) - half) <= r_tol]
                if len(near) >= 2:
                    arc_hit = arc
                    break
            if arc_hit is None:
                continue

            conf = 0.45 + 0.35 + 0.20          # 平行对 + 半圆端 + 长宽比合理
            conf = min(conf, 0.95)
            ev = [
                f"发现等长平行线对，间距 {width:.2f}mm（槽宽），长度 {length:.2f}mm",
                f"沿长度方向投影重叠率 {overlap:.0%}，两线长度比 {ratio_len:.2f}",
                f"端部存在 R{arc_hit['r']:.2f} 半圆（跨度 {span:.0f}°），与槽宽匹配",
                f"长宽比 {ratio:.1f}，属键槽典型范围",
            ]

            cx = (l1["start"][0] + l1["end"][0] + l2["start"][0] + l2["end"][0]) / 4
            cy = (l1["start"][1] + l1["end"][1] + l2["start"][1] + l2["end"][1]) / 4
            features.append(Feature(
                type="键槽",
                confidence=conf,
                location=(cx, cy),
                params={"width": width, "length": length,
                        "length_width_ratio": ratio,
                        "end_arc_radius": arc_hit["r"]},
                evidence=ev,
            ))

    return _dedupe(features, radius=5.0)


def _projection_overlap(l1: dict, l2: dict) -> float:
    """两条（近似）平行线在公共方向上的投影重叠率，0~1。"""
    import math as _m
    dx, dy = l1["end"][0] - l1["start"][0], l1["end"][1] - l1["start"][1]
    norm = _m.hypot(dx, dy)
    if norm < 1e-9:
        return 0.0
    ux, uy = dx / norm, dy / norm

    def proj(p):
        return p[0] * ux + p[1] * uy

    a1, a2 = sorted((proj(l1["start"]), proj(l1["end"])))
    b1, b2 = sorted((proj(l2["start"]), proj(l2["end"])))
    lo, hi = max(a1, b1), min(a2, b2)
    if hi <= lo:
        return 0.0
    shorter = min(a2 - a1, b2 - b1)
    return (hi - lo) / shorter if shorter > 1e-9 else 0.0


def detect_threads(circles: list[dict], texts: list[dict],
                   search_radius: float = 25.0) -> list[Feature]:
    """
    螺纹孔：圆附近存在 M/G/Rc/Rp/Tr 等螺纹标注。
    同时识别「细实线小圆 + 粗实线大圆」的螺纹画法（两同心圆，直径比 ≈ 0.85）。
    """
    features: list[Feature] = []
    for c in circles:
        near = [t for t in texts
                if _dist((c["cx"], c["cy"]), (t["x"], t["y"])) <= search_radius]
        label = None
        for t in near:
            s = (t.get("text") or "").strip()
            if s.upper().startswith(("M", "G", "RC", "RP", "TR", "BSP", "NPT")):
                label = s
                break
        if not label:
            continue

        conf = 0.85
        ev = [f"圆附近检出螺纹标注『{label}』"]
        # 螺纹画法：存在直径比 0.8~0.9 的同心小圆
        for c2 in circles:
            if c2 is c:
                continue
            if _is_concentric((c["cx"], c["cy"]), (c2["cx"], c2["cy"]), 0.5):
                ratio = min(c["r"], c2["r"]) / max(c["r"], c2["r"])
                if 0.75 <= ratio <= 0.92:
                    conf = min(conf + 0.10, 1.0)
                    ev.append(f"存在同心细实线圆（直径比 {ratio:.2f}），符合螺纹画法")
                    break

        features.append(Feature(
            type="螺纹孔",
            confidence=conf,
            location=(c["cx"], c["cy"]),
            params={"diameter": 2 * c["r"], "thread_label": label},
            evidence=ev,
        ))
    return features


def detect_hole_patterns(circles: list[dict], tol: float = 0.05) -> list[Feature]:
    """
    孔组(阵列)：3 个以上等径圆，沿直线均布或沿圆周均布。
    """
    features: list[Feature] = []
    groups: list[list[dict]] = []
    for c in circles:
        placed = False
        for g in groups:
            if abs(g[0]["r"] - c["r"]) <= max(tol * c["r"], 0.05):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    for g in groups:
        if len(g) < 3:
            continue
        pts = [(c["cx"], c["cy"]) for c in g]

        # 模式 A：沿一条直线均布
        line_ok, line_detail = _collinear_even(pts)
        if line_ok:
            eq = sorted(
                (_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)))
            features.append(Feature(
                type="孔组",
                confidence=0.85,
                location=pts[0],
                params={"count": len(g), "hole_diameter": 2 * g[0]["r"],
                        "pattern": "linear", "spacing": eq[0]},
                evidence=[f"{len(g)} 个等径圆沿直线均布，间距约 {eq[0]:.2f}mm",
                          line_detail],
            ))
            continue

        # 模式 B：沿圆周均布
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        radii = [_dist(p, (cx, cy)) for p in pts]
        if max(radii) - min(radii) <= max(0.5, 0.02 * max(radii)):
            angles = sorted(math.degrees(math.atan2(p[1] - cy, p[0] - cx)) % 360
                            for p in pts)
            gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
            gaps.append(360 - angles[-1] + angles[0])
            spread = max(gaps) - min(gaps)
            conf = 0.85 if spread <= 1.0 else 0.70
            features.append(Feature(
                type="孔组",
                confidence=conf,
                location=(cx, cy),
                params={"count": len(g), "hole_diameter": 2 * g[0]["r"],
                        "pattern": "circular",
                        "bolt_circle_diameter": 2 * (sum(radii) / len(radii))},
                evidence=[f"{len(g)} 个等径圆沿 φ{2 * (sum(radii) / len(radii)):.2f} 圆周分布",
                          f"角度间隔最大偏差 {spread:.2f}°"],
            ))
    return features


def _collinear_even(pts: list[tuple[float, float]]) -> tuple[bool, str]:
    """判断点是否近似共线且间距均匀。"""
    if len(pts) < 3:
        return False, ""
    # 以最远两点定直线
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = _dist(pts[i], pts[j])
            if best is None or d > best[0]:
                best = (d, pts[i], pts[j])
    if not best or best[0] < 1e-9:
        return False, ""
    _, p1, p2 = best
    max_dev = 0.0
    for p in pts:
        d = abs((p2[0] - p1[0]) * (p1[1] - p[1]) - (p1[0] - p[0]) * (p2[1] - p1[1])) / best[0]
        max_dev = max(max_dev, d)
    if max_dev > 0.5:
        return False, ""
    # 间距均匀性：把中间点投影到直线上
    ux, uy = (p2[0] - p1[0]) / best[0], (p2[1] - p1[1]) / best[0]
    ts = sorted(( (p[0] - p1[0]) * ux + (p[1] - p1[1]) * uy for p in pts))
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    positive = [g for g in gaps if g > 1e-6]
    if not positive:
        return False, ""
    if max(positive) - min(positive) > max(0.5, 0.02 * max(positive)):
        return False, ""
    return True, f"点共线且间距均匀（最大偏差 {max_dev:.3f}mm）"


def detect_chamfers(lines: list[dict], circles: list[dict],
                    texts: list[dict]) -> list[Feature]:
    """
    倒角：短直线 + 与轮廓约 45° 夹角；或有 C 值标注。
    """
    features: list[Feature] = []
    for l in lines:
        length = _dist(l["start"], l["end"])
        if not (0.5 <= length <= 20.0):
            continue
        ang = _angle_of(l["start"], l["end"])
        dev45 = min(abs(ang - 45.0), abs(ang - 135.0))
        if dev45 > 3.0:
            continue

        conf = 0.65
        ev = [f"发现 {length:.2f}mm 短线，与坐标轴约 45°（偏差 {dev45:.1f}°）"]

        # 邻近 C 标注
        mid = ((l["start"][0] + l["end"][0]) / 2, (l["start"][1] + l["end"][1]) / 2)
        for t in texts:
            s = (t.get("text") or "").strip().upper()
            if s.startswith("C") and _dist(mid, (t["x"], t["y"])) <= 15.0:
                conf = 0.85
                ev.append(f"邻近检出标注『{s}』")
                break

        if conf < 0.60:
            continue
        features.append(Feature(
            type="倒角",
            confidence=conf,
            location=mid,
            params={"length": length, "angle": ang},
            evidence=ev,
        ))
    return _dedupe(features, radius=3.0)


def detect_fillets(arcs: list[dict], lines: list[dict]) -> list[Feature]:
    """
    圆角：小半径圆弧，且两端与直线相连（相切过渡）。
    """
    features: list[Feature] = []
    for arc in arcs:
        r = arc["r"]
        if not (0.2 <= r <= 20.0):
            continue

        # 圆弧端点
        a0 = math.radians(arc.get("start_angle", 0.0))
        a1 = math.radians(arc.get("end_angle", 0.0))
        p0 = (arc["cx"] + r * math.cos(a0), arc["cy"] + r * math.sin(a0))
        p1 = (arc["cx"] + r * math.cos(a1), arc["cy"] + r * math.sin(a1))

        touching = 0
        for p in (p0, p1):
            if any(_dist(p, l["start"]) <= 0.5 or _dist(p, l["end"]) <= 0.5
                   for l in lines):
                touching += 1

        conf = 0.65 if touching >= 1 else 0.40
        if conf < 0.60:
            continue
        features.append(Feature(
            type="圆角",
            confidence=conf,
            location=(arc["cx"], arc["cy"]),
            params={"radius": r},
            evidence=[f"R{r:.2f} 小圆弧，{touching}/2 个端点与直线相接"],
        ))
    return _dedupe(features, radius=2.0)


def detect_relief_grooves(circles: list[dict], arcs: list[dict],
                          lines: list[dict]) -> list[Feature]:
    """
    退刀槽：轴侧小圆弧/小圆与相邻短线组合（弱证据，置信度较低）。
    """
    features: list[Feature] = []
    for arc in arcs:
        r = arc["r"]
        if not (0.3 <= r <= 4.0):
            continue
        loc = (arc["cx"], arc["cy"])
        short_lines = [l for l in lines
                       if _dist(l["start"], loc) <= 8.0 or _dist(l["end"], loc) <= 8.0]
        if len(short_lines) >= 2:
            features.append(Feature(
                type="退刀槽",
                confidence=0.50,
                location=loc,
                params={"radius": r},
                evidence=[f"R{r:.2f} 小圆弧，附近 {len(short_lines)} 条短线段，疑似槽形"],
            ))
    for c in circles:
        r = c["r"]
        if not (0.3 <= r <= 4.0):
            continue
        loc = (c["cx"], c["cy"])
        short_lines = [l for l in lines
                       if _dist(l["start"], loc) <= 8.0 or _dist(l["end"], loc) <= 8.0]
        if len(short_lines) >= 2:
            features.append(Feature(
                type="退刀槽",
                confidence=0.50,
                location=loc,
                params={"radius": r},
                evidence=[f"R{r:.2f} 小圆，附近 {len(short_lines)} 条短线段，疑似槽形"],
            ))
    return _dedupe(features, radius=3.0)


def detect_shaft_shoulders(circles: list[dict], lines: list[dict],
                           tol: float = 0.5) -> list[Feature]:
    """
    台阶轴肩：同心圆组 + 附近的轴向定位面（竖直线）。
    同样只做几何描述，不推定零件类型。
    """
    features: list[Feature] = []
    for f in detect_concentric_groups(circles, [], tol=tol):
        loc = f.location
        vertical = [l for l in lines
                    if abs(_angle_of(l["start"], l["end"]) - 90.0) <= 2.0]
        near = [l for l in vertical
                if _dist(l["start"], loc) <= f.params["max_radius"] * 1.5]
        if near:
            conf = min(f.confidence - 0.10, 0.85)
            if conf < 0.60:
                continue
            features.append(Feature(
                type="台阶轴肩",
                confidence=conf,
                location=loc,
                params=dict(f.params),
                evidence=f.evidence + [f"附近存在 {len(near)} 条轴向定位面线"],
            ))
    return features


def _dedupe(features: list[Feature], radius: float = 3.0) -> list[Feature]:
    """同一位置同一类型的特征只保留置信度最高的一个。"""
    out: list[Feature] = []
    for f in sorted(features, key=lambda x: -x.confidence):
        if any(g.type == f.type and _dist(g.location, f.location) <= radius
               for g in out):
            continue
        out.append(f)
    return out


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
ALL_DETECTORS = [
    "轴承安装孔", "轴承安装轴肩", "键槽", "螺纹孔",
    "倒角", "圆角", "孔组", "退刀槽",
]


def extract_features(model) -> list[Feature]:
    """
    从 DrawingModel 提取全部特征。

    model 需提供 .circles / .arcs / .lines / .texts 属性（见 sw_qc_parser.DrawingModel）。
    """
    circles = model.circles
    arcs = model.arcs
    lines = model.lines
    texts = model.texts
    center_marks = getattr(model, "center_marks", [])

    found: list[Feature] = []
    found += detect_concentric_groups(circles, center_marks)
    found += detect_shaft_shoulders(circles, lines)
    keyways = detect_keyways(lines, arcs)
    found += keyways
    found += detect_threads(circles, texts)
    found += detect_hole_patterns(circles)
    found += detect_chamfers(lines, circles, texts)
    fillets = detect_fillets(arcs, lines)
    # 退刀槽判据本身很弱（几何上无法与普通小圆弧可靠区分，最高只有 0.50 置信度）。
    # 实测在一张真实图上它把 R0.5/R1 圆角全报了一遍。低于 0.6 的猜测不该作为
    # 检查结论出现 —— 宁可漏报，也不要用噪声淹没真正的严重问题。
    grooves = [g for g in detect_relief_grooves(circles, arcs, lines)
               if g.confidence >= 0.6]

    # 跨特征仲裁：键槽的端部半圆不应被同时报成「圆角」或「退刀槽」。
    # 这是启发式识别的典型冲突点——同一段几何满足多种特征的判据，
    # 必须按「解释力更强的特征优先」消解，否则报告会出现自相矛盾的条目。
    for kw in keyways:
        r = kw.params.get("end_arc_radius")
        if not r:
            continue
        cand = (kw.location[0], kw.location[1] + kw.params["length"] / 2)
        fillets = [f for f in fillets
                   if not (f.type == "圆角" and abs(f.params.get("radius", 0) - r) < 0.1
                           and f.confidence <= kw.confidence)]
        grooves = [g for g in grooves
                   if not (abs(g.params.get("radius", 0) - r) < 0.1
                           and _dist(g.location, cand) <= kw.params["length"])]
    found += fillets
    found += grooves

    # 仲裁二：退刀槽是弱证据特征（置信度仅 0.50），而圆角判据更强。
    # 实测在一张真实零件图上，退刀槽把每一个 R0.5/R1 圆角都报了一遍，
    # 产生 16 条误报，直接淹没真正的问题。同一位置同一半径时，圆角优先。
    fillet_keys = {(round(f.location[0], 1), round(f.location[1], 1),
                    round(f.params.get("radius", 0), 1)) for f in fillets}
    found = [f for f in found
             if not (f.type == "退刀槽"
                     and (round(f.location[0], 1), round(f.location[1], 1),
                          round(f.params.get("radius", 0), 1)) in fillet_keys)]

    # 跨特征仲裁完成后，再决定要不要「推定为轴承件」。
    #
    # 只有在图纸文字真的出现「轴承」时才允许这么叫。没有文字证据时一律保持
    # 中性的几何描述 —— 否则每一张回转体零件图都会被判成轴承件。
    # 用户实测反馈：一张「试剂瓶接头」被报成轴承件，完全错误。
    all_text = getattr(model, "all_text", "") or ""
    if "轴承" in all_text:
        for f in found:
            if f.type == "同心圆组":
                f.type = "轴承安装孔（推定）"
                f.evidence.append("图纸文字出现『轴承』，据此推定（非零件类型判定）")
            elif f.type == "台阶轴肩":
                f.type = "轴承安装轴肩（推定）"
                f.evidence.append("图纸文字出现『轴承』，据此推定")

    # 全局去重（不同类型允许共存）
    return sorted(found, key=lambda f: (-f.confidence, f.type))