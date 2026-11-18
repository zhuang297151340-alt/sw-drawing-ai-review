# -*- coding: utf-8 -*-
"""
sw_qc_parttype.py — 零件类型识别（机加件 / 钣金件 / 塑胶件 / 组合件）

## 为什么需要它

用户实测反馈（2026-10-13）：

    钣金件和塑胶件一般是不需要标注粗糙度的，一般只有机加件需要标注粗糙度。

也就是说：**对一张钣金件报「未检出粗糙度」就是误报**。而这类误报会直接
消耗工程师的信任（项目一贯口径：宁可漏报也不要误报）。

所以规则必须**按零件类型分档**，而分档的前提是先知道这是什么零件。

## 为什么不直接交给 AI 判

能判，但**不该让 AI 当裁判**：

    • 判错会连锁影响多条规则（粗糙度、去毛刺、热处理…），代价大
    • 慢：多一次模型往返
    • 不可解释：工程师问"你凭什么说这是钣金件"时答不上来

所以本模块用**可解释的启发式**先判（毫秒级），把**依据**一并输出，
界面上再让用户一键改。AI 只在两处出现：
    1) 启发式判不出来时兜底（可选）
    2) 用户主动点"让 AI 再判一次"

## 判定方式

按证据打分（见 PLASTIC_HINTS / SHEET_HINTS / MACHINED_HINTS），
取分最高者；若钣金与机加都很强 → 判「组合件」。
**置信度 < 0.6 时判为「未知」**（沿用本项目特征识别的既有阈值），
此时粗糙度规则按"照常报但注明"处理，不静默放过。
"""
from __future__ import annotations

# --- 确保模块能找到同目录的兄弟模块
import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
# -----------------------------------------------------------------

import math
import re

# 零件类型取值
TYPE_MACHINED = "机加件"
TYPE_SHEET = "钣金件"
TYPE_PLASTIC = "塑胶件"
TYPE_COMBO = "组合件"
TYPE_UNKNOWN = "未知"
ALL_TYPES = [TYPE_MACHINED, TYPE_SHEET, TYPE_PLASTIC, TYPE_COMBO, TYPE_UNKNOWN]

# 置信度阈值：低于它就不下结论（与 sw_qc_features 的 0.6 一致）
CONFIDENCE_MIN = 0.6

# --------------------------------------------------------------------------
# 材料牌号 → 零件类型（**用户 2026-10-13 直接给出的判定表，权威度最高**）
# --------------------------------------------------------------------------
# 用户原话：
#   「如果材质是 ABS, PC, PP 等常用的塑胶模具成型的材质，这个基本是塑胶件；
#     如果材质是 Q235, 冷板, SPCC, SECC, 铝板 5052 等等这些基本是钣金件；
#     当然除了不锈钢 304 201 301 316 等这些，有可能是钣金件也有可能是机加件」
#
# 所以：
#   • 材料是**决定性**信号（+6 分），优先于其它所有启发式
#   • 不锈钢那几种**故意不下结论**（0 分），交给几何信号去分
#
# 为什么不用 AI 判：材料牌号是**干净的结构化字段**，查表比模型又快又准，
# 而且判错时能直接指着"材料=ABS"说明依据。AI 用在这里是浪费。
_PLASTIC_EXACT = {
    "ABS", "PC", "PP", "PE", "HDPE", "LDPE", "LLDPE", "POM", "PA6", "PA66",
    "PA12", "PA46", "PMMA", "PS", "HIPS", "PVC", "PBT", "PET", "PPS", "PPSU",
    "TPU", "TPE", "TPR", "PC+ABS", "PC/ABS", "ASA", "SAN", "EPDM", "NBR",
    "尼龙", "赛钢", "聚甲醛", "聚碳酸酯", "亚克力", "有机玻璃", "电木", "胶木",
    "硅胶", "橡胶", "环氧树脂",
}
_SHEET_EXACT = {
    "Q195", "Q215", "Q235", "Q275", "Q345", "Q355",
    "SPCC", "SPCD", "SPCE", "SECC", "SGCC", "SECE",
    "DC01", "DC03", "DC04", "DC05", "DC06",
    "5052", "1060", "3003", "6061板", "5754", "5083",
    "冷板", "热板", "冷轧板", "热轧板", "镀锌板", "铝板", "钢板", "不锈钢板",
    "板料", "薄板", "中厚板", "花纹板", "电解板",
}
# 不锈钢：可能钣金也可能机加 —— **故意不判**，交给几何信号
_AMBIGUOUS_STEEL = {
    "304", "304L", "316", "316L", "321", "301", "201", "302", "309", "310",
    "1Cr18Ni9", "1Cr18Ni9Ti", "0Cr18Ni9", "0Cr18Ni10Ti",
    "SUS304", "SUS316", "SUS301", "SUS201", "SUS321",
}

# 兜底的正则（材料字段里带后缀/前缀，如 "Q235B"、"SPCC-SD"、"铝板5052"）
_PLASTIC_RE = re.compile(
    r"(?<![A-Z])(ABS|PC|PP|PE|HDPE|LDPE|POM|PA6|PA66|PMMA|PS|PVC|PBT|PET|PPS|"
    r"TPU|TPE|PC\+ABS)(?![A-Z])|尼龙|赛钢|聚甲醛|聚碳酸酯|亚克力", re.IGNORECASE)
_SHEET_RE = re.compile(
    r"冷板|热板|冷轧板|热轧板|镀锌板|铝板|花纹板|电解板|板料|薄板|"
    r"SPCC|SPCD|SPCE|SECC|SGCC|DC0\d|Q2\d{2}|Q3\d{2}|5052|1060|3003|5754|5083",
    re.IGNORECASE)


def classify_by_material(mat: str) -> tuple[str, str, float]:
    """
    只按材料牌号判定，返回 (零件类型, 依据, 分数)。判不出返回 ("", "", 0)。

    注意顺序：**先查钣金再查塑胶**。因为 "SPCC" 里含 "PC" 子串，
    顺序反了会把镀锌板判成塑胶件（这种低级错误一旦发生，工程师就不会再信工具）。
    """
    s = (mat or "").strip()
    if not s:
        return "", "", 0.0
    up = re.sub(r"[\s\-_/]+", "", s).upper()

    # ① 精确 / 前缀匹配（最常见：材料字段就是干净的牌号）
    for key in _SHEET_EXACT:
        k = key.upper()
        if up == k or up.startswith(k) or k in up:
            return (TYPE_SHEET, f"材料「{s}」属钣金常用板材（{key}）", 6.0)
    for key in _PLASTIC_EXACT:
        k = key.upper()
        if up == k or up.startswith(k) or k in up:
            return (TYPE_PLASTIC, f"材料「{s}」是常用注塑材料（{key}）", 6.0)

    # ② 不锈钢：故意不判，交给几何
    for key in _AMBIGUOUS_STEEL:
        k = key.upper()
        if up == k or up.startswith(k):
            return ("", f"材料「{s}」是不锈钢，钣金/机加都可能 —— 交给几何信号判断", 0.0)

    # ③ 带前后缀的写法
    if _SHEET_RE.search(s):
        return (TYPE_SHEET, f"材料「{s}」含钣金板材特征", 6.0)
    if _PLASTIC_RE.search(s):
        return (TYPE_PLASTIC, f"材料「{s}」含注塑材料特征", 6.0)
    return "", "", 0.0


# 旧的塑料正则保留（供别处引用），已由 classify_by_material 承担主判定
PLASTIC_MATERIAL_RE = _PLASTIC_RE

# 中文塑料/工艺词
PLASTIC_HINTS = [
    (re.compile(r"塑胶|塑料|胶件|尼龙|聚甲醛|聚碳酸酯|亚克力|电木"), 3.0, "文字含塑料类词"),
    (re.compile(r"拔模|脱模|蚀纹|火花纹|晒纹|模具成型"), 2.0, "含模具/拔模要求"),
]

# --------------------------------------------------------------------------
# 钣金：折弯/展开/板厚
# --------------------------------------------------------------------------
SHEET_HINTS = [
    (re.compile(r"折弯|展开|落料|冲压|钣金|激光切割|数冲|折弯线|板料|剪板"), 3.0, "含折弯/展开/钣金工艺词"),
    (re.compile(r"焊接|焊缝|坡口"), 1.0, "含焊接要求（钣金焊接件常见）"),
]
# 板厚写法：t=2 / δ2 / 板厚2 / 厚 2 / 2mm厚 / 厚度2
THICKNESS_RES = [
    re.compile(r"(?<![A-Za-z0-9])t\s*=\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"[δΔ]\s*(\d+(?:\.\d+)?)"),
    re.compile(r"板厚\s*[:：]?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"厚度\s*[:：]?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"厚\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*厚"),
]

# --------------------------------------------------------------------------
# 机加：粗糙度 / 配合公差 / 螺纹 / 回转体 / 剖面线
# --------------------------------------------------------------------------
MACHINED_HINTS = [
    (re.compile(r"Ra\s*\d|粗糙度|▽|表面粗糙"), 3.0, "含表面粗糙度标注（机加特征）"),
    (re.compile(r"(?<=\d)\s*([A-Za-z]{1,2})(\d{1,2})(?![0-9])"), 2.5, "含配合公差代号（H7/g6 等）"),
    (re.compile(r"\bM\d{1,3}(?:[×x]\d+(?:\.\d+)?)?|G\d/?\d?|Rc\d|Rp\d|Tr\d"), 2.0, "含螺纹标注"),
    (re.compile(r"车|铣|磨|钻|镗|铰|攻丝|调质|淬火|HRC|HB\d"), 1.5, "含机加工序/热处理要求"),
    (re.compile(r"去毛刺|倒钝|棱边"), 0.5, "含去毛刺要求"),
]

# 阈值：某类得分达到它才敢下结论
SCORE_MIN = 4.0


def _texts(model) -> list[str]:
    return [str(t.get("text") or "").strip() for t in (getattr(model, "texts", []) or [])]


def _all_text(model) -> str:
    parts = _texts(model)
    parts += [str(t.get("text") or "") for t in
              (getattr(model, "dimension_texts", []) or [])]
    parts += [str(v) for v in (getattr(model, "title_block", {}) or {}).values()]
    return "\n".join(parts)


def _material(model) -> str:
    return str((getattr(model, "title_block", {}) or {}).get("材料", "") or "").strip()


def _has_concentric(model) -> bool:
    """是否存在同心圆组（回转体的强烈信号）。"""
    circles = [c for c in (getattr(model, "circles", []) or []) if c.get("r", 0) > 0.5]
    centers: dict[tuple, list] = {}
    for c in circles:
        key = (round(float(c["cx"]), 2), round(float(c["cy"]), 2))
        centers.setdefault(key, []).append(float(c["r"]))
    return any(len(set(radii)) >= 2 for radii in centers.values())


def _blanket_roughness(model) -> bool:
    """是否已有粗糙度标注（含统注）。复用检查模块的同一套识别，避免两处口径不一致。"""
    try:
        from sw_qc_checks import _collect_roughness
        return bool(_collect_roughness(model))
    except Exception:
        return bool(re.search(r"Ra\s*\d|粗糙度|▽", _all_text(model)))


def detect_part_type(model) -> dict:
    """
    识别零件类型。返回：

        {"type": "钣金件", "confidence": 0.82, "evidence": [...],
         "scores": {"机加件": 3.0, "钣金件": 6.0, ...}, "auto": True}

    evidence 是**给人看的依据**（"含折弯/展开/钣金工艺词"、"材料=ABS"…），
    工程师据此判断该不该接受这个结论，并在界面上改掉。
    """
    text = _all_text(model)
    mat = _material(model)
    scores = {TYPE_MACHINED: 0.0, TYPE_SHEET: 0.0, TYPE_PLASTIC: 0.0}
    evidence: list[str] = []

    def add(kind: str, pts: float, why: str) -> None:
        scores[kind] += pts
        evidence.append(f"{why}（+{pts:g} → {kind}）")

    # —— 材料牌号：**决定性信号**（用户给出的判定表，优先于其它一切启发式）——
    mat_kind, mat_why, mat_pts = classify_by_material(mat)
    if mat_kind and mat_pts:
        add(mat_kind, mat_pts, mat_why)
    elif mat_why:
        evidence.append(mat_why)          # 不锈钢这类"故意不判"的也记下来，便于解释
    if mat and not mat_kind:
        try:
            from sw_qc_parser import is_material_grade
            if is_material_grade(mat) and not _PLASTIC_RE.search(mat):
                add(TYPE_MACHINED, 1.5, f"材料「{mat}」是金属牌号（非板材牌号）")
        except Exception:
            pass

    # —— 塑料/模具词 ——
    for rx, pts, why in PLASTIC_HINTS:
        if rx.search(text):
            add(TYPE_PLASTIC, pts, why)

    # —— 钣金工艺词 ——
    for rx, pts, why in SHEET_HINTS:
        if rx.search(text):
            add(TYPE_SHEET, pts, why)

    # —— 板厚标注 ——
    thick_hits = []
    for rx in THICKNESS_RES:
        for m in rx.finditer(text):
            try:
                v = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0.2 <= v <= 25:              # 常见板厚范围
                thick_hits.append(v)
    if thick_hits:
        add(TYPE_SHEET, 2.5, f"检出板厚写法（如 {thick_hits[0]:g}mm）")

    # —— 机加信号 ——
    for rx, pts, why in MACHINED_HINTS:
        if rx.search(text):
            add(TYPE_MACHINED, pts, why)
    if _blanket_roughness(model):
        # 有粗糙度标注 = 机加信号（钣金/塑胶件一般不需要它）。
        # 权重刻意给小：钣金件偶尔也会写粗糙度，不能因此把类型掀翻。
        # 方向上是"安全的" —— 多判成机加件只会让粗糙度检查照常执行（更保守）。
        add(TYPE_MACHINED, 1.0, "已有粗糙度标注")
    if _has_concentric(model):
        add(TYPE_MACHINED, 2.0, "存在同心圆组（回转体）")
    if int(getattr(model, "hatch_count", 0) or 0) > 0:
        add(TYPE_MACHINED, 1.0, "存在剖面线（剖视图）")

    # —— 判定 ——
    m, s, p = scores[TYPE_MACHINED], scores[TYPE_SHEET], scores[TYPE_PLASTIC]
    best_kind = max(scores, key=lambda k: scores[k])
    best = scores[best_kind]
    total = m + s + p

    # 置信度必须同时反映两件事：
    #   ① 领先幅度（只有一类强时高；两类接近时低）
    #   ② 是否达到判定门槛（分数不够时不能因为"没有对手"就报 100%）
    # 之前的算法只看 ①，导致"得分 2 分、判为未知"却显示 100% —— 自相矛盾。
    margin = 0.0 if (best <= 0 or total <= 0) else max(0.0, (best - (total - best)) / best)
    strength = min(1.0, best / SCORE_MIN)
    # 启发式判断不该把话说满，上限 0.95
    conf = round(min(0.95, margin * strength), 2)

    # 钣金 + 机加信号同时达到门槛 → 组合件。
    # 不加"两者分数要接近"的条件：真实组合件常常机加工序更多（机加分明显领先），
    # 但它确实是组合件。判成组合件在粗糙度检查上等同于机加件（照常报），
    # 所以放宽是安全方向。
    if s >= SCORE_MIN and m >= SCORE_MIN:
        return {"type": TYPE_COMBO,
                "confidence": round(min(0.95, (s + m) / (total or 1)), 2),
                "evidence": evidence + [
                    f"钣金信号 {s:g} 分、机加信号 {m:g} 分都达到门槛 → 判为组合件"],
                "scores": scores, "auto": True}

    if best < SCORE_MIN or conf < CONFIDENCE_MIN:
        return {"type": TYPE_UNKNOWN,
                "confidence": conf,
                "evidence": evidence + [
                    f"最强信号 {best_kind} 仅 {best:g} 分（门槛 {SCORE_MIN:g}）"
                    f"，不足以判定零件类型"],
                "scores": scores, "auto": True}

    return {"type": best_kind, "confidence": conf,
            "evidence": evidence, "scores": scores, "auto": True}


# 需要标注表面粗糙度的零件类型
NEEDS_ROUGHNESS = {TYPE_MACHINED, TYPE_COMBO}


def needs_roughness(part_type: str, confidence: float = 1.0) -> tuple[bool, str]:
    """
    该零件类型是否需要标注表面粗糙度。返回 (是否需要, 理由)。

    依据用户口径（2026-10-13）：
        「钣金件和塑胶件一般是不需要标注粗糙度的，一般只有机加件需要标注粗糙度」
    """
    if part_type == TYPE_MACHINED:
        return True, "机加件需要标注表面粗糙度"
    if part_type == TYPE_COMBO:
        return True, "组合件中的机加部分需要标注表面粗糙度"
    if part_type == TYPE_SHEET:
        return False, "钣金件一般不需要标注表面粗糙度（以板材原始表面为准）"
    if part_type == TYPE_PLASTIC:
        return False, "塑胶件一般不需要标注表面粗糙度（模具面另行规定蚀纹/火花纹）"
    return True, "零件类型未判定，按需要标注处理（若为钣金/塑胶件可忽略此项）"


__all__ = ["detect_part_type", "needs_roughness", "ALL_TYPES", "CONFIDENCE_MIN",
           "TYPE_MACHINED", "TYPE_SHEET", "TYPE_PLASTIC", "TYPE_COMBO", "TYPE_UNKNOWN",
           "NEEDS_ROUGHNESS", "PLASTIC_MATERIAL_RE"]
