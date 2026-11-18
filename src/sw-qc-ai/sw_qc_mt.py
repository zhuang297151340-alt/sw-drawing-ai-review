# -*- coding: utf-8 -*-
"""
sw_qc_mt.py — 塑料模塑件公差判定辅助（GB/T 47067-2026）

现行标准：GB/T 47067-2026《塑料模塑件 公差和验收条件》
          2026-01-28 发布，2026-08-01 实施；修改采用 ISO 20457:2018（MOD）
          公差体系：公差等级 TG1~TG9（标准表1 与 ISO 标准公差 IT 对应）

本文件内容：
  1) TG1~TG9 ↔ IT 换算（标准表1）
  2) 技术要求里「GB/T 47067-2026-TGx」一般公差声明的识别
  3) 机加工塑料(CNC) 的常规/较高精度参考数据（用户提供的工程经验）
  4) 注塑可达公差的按尺寸分档经验值（待标准表2 数值录入后替换）
"""
import re

# ===================== 1) 公差等级 TG ↔ IT（标准表1） =====================
# 公称尺寸 1~6 mm      : TG1=IT8 … TG9=IT16
# 公称尺寸 >6~120 mm   : TG1=IT9 … TG9=IT17
# 公称尺寸 >120~1000mm : TG1 不适用, TG2=IT11 … TG9=IT18
TG_SIZES = [(6, 8), (120, 9), (1000, 11)]


def tg_to_it(size_mm, grade):
    """返回该公称尺寸下 TG 等级对应的 IT 号；不适用时返回 None。"""
    try:
        size = abs(float(size_mm or 0))
        g = int(grade)
    except Exception:
        return None
    if not (1 <= g <= 9):
        return None
    for limit, base in TG_SIZES:
        if size <= limit:
            if base == 11:                 # >120~1000mm：TG2=IT11
                if g == 1:
                    return None
                return 11 + (g - 2)
            return base + (g - 1)
    return None


def detect_general_tolerance(notes):
    """识别「一般公差：GB/T 47067-2026-TG6」这类声明。返回 dict 或 None。"""
    text = str(notes or "")
    m = re.search(r"47067[\s\-—/]*\d{4}[\s\-—/]*TG\s*(\d)", text)
    if not m:
        m = re.search(r"47067[\s\S]{0,10}?TG\s*(\d)", text)
    if not m:
        return None
    g = int(m.group(1))
    if not (1 <= g <= 9):
        return None
    return {"standard": "GB/T 47067-2026", "grade": g,
            "text": "GB/T 47067-2026-TG%d" % g}


# ===================== 1b) 表2：W 列（不受模具活动部分影响）=====================
# GB/T 47067-2026 表2（单位 mm，数值为 ±极限偏差）
TG2_W_SIZES = ['1~3', '>3~6', '>6~10', '>10~18', '>18~30', '>30~50', '>50~80', '>80~120', '>120~180', '>180~250', '>250~315', '>315~400', '>400~500', '>500~630', '>630~800', '>800~1000']
TG2_W_UPPER = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500, 630, 800, 1000]
TG2_W_TABLE = {1: [0.007, 0.012, 0.018, 0.022, 0.026, 0.031, 0.037, 0.044, None, None, None, None, None, None, None, None], 2: [0.013, 0.019, 0.029, 0.035, 0.042, 0.05, 0.06, 0.09, 0.13, 0.15, 0.16, 0.18, 0.2, None, None, None], 3: [0.02, 0.03, 0.05, 0.06, 0.07, 0.08, 0.1, 0.15, 0.2, 0.23, 0.26, 0.29, 0.32, 0.35, 0.4, 0.45], 4: [0.03, 0.05, 0.08, 0.09, 0.11, 0.13, 0.15, 0.23, 0.32, 0.35, 0.41, 0.45, 0.49, 0.55, 0.63, 0.7], 5: [0.05, 0.08, 0.11, 0.14, 0.17, 0.2, 0.23, 0.36, 0.5, 0.58, 0.65, 0.7, 0.78, 0.88, 1.0, 1.15], 6: [0.07, 0.12, 0.18, 0.22, 0.26, 0.31, 0.37, 0.57, 0.8, 0.93, 1.05, 1.15, 1.25, 1.4, 1.6, 1.8], 7: [0.13, 0.2, 0.29, 0.35, 0.42, 0.5, 0.6, 0.9, 1.25, 1.45, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8], 8: [0.2, 0.3, 0.45, 0.55, 0.65, 0.8, 0.95, 1.4, 2.0, 2.3, 2.6, 2.85, 3.15, 3.5, 4.0, 4.5], 9: [0.48, 0.75, 0.9, 1.05, 1.25, 1.5, 2.25, 3.15, 3.6, 4.05, 4.45, 4.9, 5.4, 6.2, 7.1, 8.5]}


def tg_tolerance(size_mm, grade):
    """按公称尺寸查表2 W 列的 ±偏差（mm）；不适用返回 None。"""
    try:
        size = abs(float(size_mm or 0))
        g = int(grade)
    except Exception:
        return None
    if g not in TG2_W_TABLE:
        return None
    if size < 1 or size > 1000:
        return None
    for upper, val in zip(TG2_W_UPPER, TG2_W_TABLE[g]):
        if size <= upper:
            return val
    return None


# ===================== 1c) 表9：位置度（W 列）=====================
# GB/T 47067-2026 表9，单位 mm，值为圆柱形公差带直径 Ø
TG9_POS_SIZES = ['1~3', '>3~6', '>6~10', '>10~18', '>18~30', '>30~50', '>50~80', '>80~120', '>120~180', '>180~250', '>250~315', '>315~400', '>400~500', '>500~630', '>630~800', '>800~1000']
TG9_POS_UPPER = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500, 630, 800, 1000]
TG9_POS_TABLE = {1: [0.02, 0.034, 0.05, 0.062, 0.073, 0.087, 0.104, 0.123, None, None, None, None, None, None, None, None], 2: [0.036, 0.053, 0.081, 0.098, 0.118, 0.14, 0.168, 0.252, 0.364, 0.42, 0.448, 0.504, 0.56, None, None, None], 3: [0.056, 0.084, 0.14, 0.168, 0.196, 0.224, 0.28, 0.42, 0.56, 0.644, 0.728, 0.812, 0.896, 0.98, 1.12, 1.26], 4: [0.084, 0.14, 0.224, 0.252, 0.308, 0.364, 0.42, 0.664, 0.869, 0.98, 1.15, 1.26, 1.37, 1.54, 1.76, 1.96], 5: [0.14, 0.224, 0.308, 0.392, 0.476, 0.56, 0.644, 1.01, 1.4, 1.62, 1.82, 1.96, 2.18, 2.46, 2.8, 3.22], 6: [0.196, 0.336, 0.504, 0.616, 0.728, 0.868, 1.04, 1.6, 2.24, 2.6, 2.94, 3.22, 3.5, 3.92, 4.48, 5.04], 7: [0.364, 0.56, 0.812, 0.98, 1.18, 1.4, 1.68, 2.52, 3.5, 4.06, 4.48, 5.04, 5.6, 6.16, 7.0, 7.84], 8: [0.56, 0.84, 1.26, 1.54, 1.82, 2.24, 2.66, 3.92, 5.6, 6.44, 7.28, 7.98, 8.82, 9.8, 11.2, 12.6], 9: [1.34, 2.1, 2.52, 2.94, 3.5, 4.2, 6.3, 8.82, 10.08, 11.34, 12.46, 13.72, 15.12, 17.36, 19.88, 23.8]}


def tg_position(size_mm, grade):
    """查表9 W 列位置度（Ø mm）；不适用返回 None。"""
    try:
        size = abs(float(size_mm or 0))
        g = int(grade)
    except Exception:
        return None
    if g not in TG9_POS_TABLE:
        return None
    if size < 1 or size > 1000:
        return None
    for upper, val in zip(TG9_POS_UPPER, TG9_POS_TABLE[g]):
        if size <= upper:
            return val
    return None


# ===================== 4) 由公差反查 TG 等级 + 加工难度 =====================
TG_DIFFICULTY = {
    1: "高精度要求，核实材料、结构及批量能力",
    2: "较高精度，关注收缩和尺寸稳定性",
    3: "根据具体材料及特征判断",
    4: "常规审核，不代表所有结构均容易制造",
    5: "常规审核，不代表所有结构均容易制造",
    6: "常规审核，不代表所有结构均容易制造",
    7: "常规审核，不代表所有结构均容易制造",
    8: "常规审核，不代表所有结构均容易制造",
    9: "常规审核，不代表所有结构均容易制造",
}


def tg_grade_of(size_mm, tol_mm):
    """由"公称尺寸 + 单边公差(mm)"反查最接近的 TG 等级。

    返回 (tag, grade)：
      tag = "TG3" 表示正好落在 TG3；"≈TG3" 表示最接近；
            "严于TG1" 表示比 TG1 还紧（超出标准 9 个等级范围）。
    """
    try:
        size = abs(float(size_mm or 0))
        tol = abs(float(tol_mm or 0))
    except Exception:
        return ("", None)
    if tol <= 0 or size < 1 or size > 1000:
        return ("", None)
    best = None
    for g in range(1, 10):
        v = tg_tolerance(size, g)
        if v is None:
            continue
        diff = abs(v - tol) / v
        if best is None or diff < best[0]:
            best = (diff, g, v)
    if best is None:
        return ("", None)
    diff, g, v = best
    # 就近归档：误差 ≤20% 直接报该等级；否则标 ≈（更紧/更松都按最接近的等级报）
    if diff <= 0.30:
        return (f"TG{g}", g)
    return (f"≈TG{g}", g)


# ===================== 2) 机加工塑料（CNC）参考数据 =====================
# 材料 → (常规公差 ±mm, 较高精度 ±mm, 特点/风险提示)
# 来源：用户提供的机加工经验数据（CNC 车削/铣削，基本尺寸 10~100mm，常温检测）
CNC_PLASTIC_GRADES = [
    ("ABS", 0.10, 0.05, "加工难度低"),
    ("POM", 0.075, 0.03, "尺寸稳定性好，适合精密件"),
    ("PTFE", 0.15, 0.05, "材料软易变形；±0.05 不宜作常规要求"),
    ("铁氟龙", 0.15, 0.05, "材料软易变形；±0.05 不宜作常规要求"),
    ("PEEK", 0.075, 0.03, "稳定；专门工艺已有 ±0.02 案例"),
    ("PMMA", 0.075, 0.04, "亚克力，可抛光到更高"),
    ("亚克力", 0.075, 0.04, "可抛光到更高"),
    ("PA66", 0.15, 0.05, "刚性高但吸湿，注意尺寸稳定性"),
    ("PA6", 0.15, 0.05, "吸湿性强，尺寸易变化"),
    ("尼龙", 0.15, 0.05, "吸湿性强，尺寸易变化"),
    ("PC", 0.075, 0.04, "韧性好易内应力，注意开裂/热变形"),
    ("PVC", 0.075, 0.04, "硬质 PVC，注意热软化/崩边"),
    ("PET", 0.075, 0.025, "PET-P 刚性好、吸湿低，适合精密"),
    ("PPS", 0.075, 0.025, "刚性好、吸湿低，注意脆性与崩边"),
    ("HDPE", 0.15, 0.075, "软、热膨胀大，易夹持变形"),
]


def cnc_grade(material):
    t = str(material or "").upper().replace(" ", "")
    for key, normal, high, tip in CNC_PLASTIC_GRADES:
        if key.upper() in t:
            return (normal, high, tip)
    return None


def cnc_limits(material):
    """(可达最紧公差带, 常规公差带)，单位 mm；不认识返回 None。"""
    g = cnc_grade(material)
    return (g[1] * 2, g[0] * 2) if g else None


def is_cnc_plastic(material):
    return cnc_grade(material) is not None


def cnc_plastic_capability(tol_width_um, material="", nominal_mm=0.0):
    """机加工塑料：按该材料常规/较高精度两档判定（比公差带宽度）。"""
    if tol_width_um is None:
        return "公差值无法解析"
    w = tol_width_um / 1000.0
    g = cnc_grade(material)
    if not g:
        return "（该材料机加工数据未收录，需与厂家确认）"
    normal, high, tip = g
    if w >= normal * 2:
        cap = "CNC 加工可满足"
    elif w >= high * 2:
        cap = "CNC 加工可满足（常规精度）"
    elif w >= high * 2 * 0.6:
        cap = "需较高精度加工（建议与厂家确认）"
    else:
        cap = "超出常见 CNC 水平（需与厂家确认）"
    return "%s（%s：常规 ±%g / 较高 ±%gmm；%s）" % (cap, material, normal, high, tip)


# ===================== 3) 注塑（经验值，待表2数值替换） =====================
def injection_allowance_mm(nominal_mm):
    """注塑常用可达公差（按尺寸分档的经验值）。"""
    size = abs(float(nominal_mm or 0)) or 10.0
    return max(0.03, 0.02 * (size ** (1.0 / 3.0)))


def _strip_paren(text):
    """
    材料/类别里允许带括号说明（用户 2026-10-25）：
      POM（黑色）、SUS304（不锈钢）、45钢（调质）、6061-T6（阳极氧化）
    这些括号是颜色/状态备注，不算错；分类时先剥掉再判断。
    """
    t = str(text or "")
    t2 = re.sub(r"[（(][^）)]*[）)]", "", t).strip()
    return t2 or t


def process_mode(material, category="", notes=""):
    """
    判塑料件的加工工艺：'injection' 注塑 / 'cnc' 机加工。

    2026-10-25 用户口径（两条一起）：
      ① 材料/类别允许带括号说明（POM（黑色）），分类前剥掉括号；
      ② 技术要求引 GB/T 1804 / 1184（金属件标准）→ 说明该件按**机加工艺**做，
         按 cnc 判；不要要求脱模斜度、模具纹理、GB/T 47067 塑件公差。
    判定优先级：
      1) 技术要求出现 注塑/模塑/注射成型/GB/T 47067 → injection
      2) 技术要求出现 CNC/数控/车削/铣削/加工中心/1804 → cnc
      3) 类别含 塑胶/塑料/注塑 → injection；含 机加 → cnc
      4) 都没有才按材料表推测
    """
    material = _strip_paren(material)
    category = _strip_paren(category)
    t = str(notes or "")
    if re.search(r"注塑|模塑|注射成型|注塑成型|47067", t):
        return "injection"
    if re.search(r"CNC|数控|车削|铣削|加工中心|机加工|1804", t, re.I):
        return "cnc"
    cat = str(category or "")
    if re.search(r"注塑|模塑|塑胶|塑料", cat):
        return "injection"
    if "机加" in cat:
        return "cnc"
    return "cnc" if is_cnc_plastic(material) else "injection"


def plastic_capability(tol_width_um, material="", nominal_mm=0.0,
                       category="", notes=""):
    """塑料件：按工艺分流判断能否满足公差。"""
    mode = process_mode(material, category, notes)
    if mode == "cnc":
        return cnc_plastic_capability(tol_width_um, material, nominal_mm)

    # 2026-10-25 用户定稿：注塑件不判「能不能做到」（注塑机都能满足），
    # 只反馈"这个尺寸需要哪个等级"，并说明精密注塑可满足行业要求。
    if tol_width_um is None:
        return "注塑件不判加工能力；该尺寸公差未标注，等级待确认"
    try:
        # 公差带宽度(µm) → 单边公差(mm)：±0.1 → 200µm → 0.1mm
        _tag, _tg = tg_grade_of(nominal_mm, tol_width_um / 2000.0)
    except Exception:
        _tag, _tg = "", None
    if _tg:
        _it = None
        try:
            _it = tg_to_it(nominal_mm, _tg)
        except Exception:
            pass
        return ("注塑件不判加工能力；该尺寸需 %s%s（GB/T 47067-2026）· 精密注塑可满足"
                % (_tag or ("TG%d" % _tg), ("（≈IT%d）" % _it) if _it else ""))
    return ("注塑件不判加工能力；该尺寸公差超出 GB/T 47067-2026 常用分级 · "
            "请与模具厂确认")
