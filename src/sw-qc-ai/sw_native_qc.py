# -*- coding: utf-8 -*-
"""
sw_native_qc.py — 原生审图判定层（读 SW 插件直读出的 .sw.json）

## 判定原则（来自实际使用反馈，逐条落实）

1. **看图纸，不是看 3D 属性**
   3D 自定义属性只用于：窗口顶部显示审核对象 + 作为"应有值"基准。
   判定查的是：这些值**是否真的出现在工程图的标题栏文字里**；没出现就报。
2. **材料只查"有没有漏"**
   45 / 45# / 45钢 / 5052 / 铝板5052 / 5052铝板 / 6061-T6 都算同一材料，
   不因写法/状态代号不同而报错。识别不出类别只给"未归类"提示，不算错误。
3. **材料 vs 工艺矛盾**（真正有价值）
   铝件写"淬火/调质/发黑"、钢件写"阳极氧化" → 报矛盾。
4. **公差口径**
   技术要求声明 GB/T 1804 → 未注公差允许，不报；
   没声明才提示"未引用一般公差标准"。几何公差同理（GB/T 1184）。
5. **诚实性**
   读取失败只报一条"读取失败"；几何类检查没做就写明没做，不假装。
6. **AI**
   配置了 Key 才调用；窗口先弹出（--header-only）时不调用；
   调用失败会写清原因，绝不假装 AI 做过。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sw_qc_gb  # IT 公差表 / 一般公差表
import sw_materials  # SW 自带材质库（材料识别）
import sw_qc_mt     # 注塑件公差（现行 GB/T 47067-2026，旧 MT 表仅参考）

SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}
SEV_TEXT = {"critical": "严重", "major": "一般", "minor": "轻微"}

# ---------------------------------------------------------------- 材料类别
# 不维护"完整牌号表"：只认类别 + 归一化，写法不同不报错（用户要求保持灵活）。
CLASS_KEYWORDS = [
    ("不锈钢", ["不锈钢", "SUS", "1CR18NI9", "304", "316", "2CR13", "3CR13", "4CR13",
              "301", "302", "17-7PH", "631", "1CR17NI7"]),
    ("铝合金", ["铝", "AL", "6061", "6063", "7075", "5052", "6082", "2A12", "2A11",
              "5A06", "LY12", "ADC12", "A356"]),
    ("铜合金", ["铜", "H62", "H59", "H65", "T2", "T3", "QSN", "QAL", "QBE2", "铍铜", "黄铜", "紫铜", "青铜"]),
    ("铸铁", ["HT200", "HT250", "QT450", "QT600", "球墨", "灰铁", "铸铁"]),
    ("铸钢", ["ZG45", "ZG270", "铸钢"]),
    ("工程塑料", ["POM", "ABS", "PA66", "PA6", "尼龙", "PEEK", "PTFE", "聚甲醛", "塑料",
              "PC", "PMMA", "亚克力", "聚碳酸酯", "PPS", "PI", "聚酰亚胺",
              "PSU", "PPSU", "PEI", "PET", "PBT", "PVC", "TPU", "AS",
              "CA", "EP", "环氧", "铁氟龙", "氟塑料", "PE", "PP", "PS", "HDPE", "PET-P", "PETP"]),
    ("橡胶", ["NBR", "丁腈", "硅胶", "SILICONE", "EPDM", "氟橡胶", "FKM", "橡胶"]),
    ("钛合金", ["TC4", "TA2", "TA15", "TI6AL4V", "钛合金", "TITANIUM"]),
    ("镁合金", ["AZ31", "AZ91", "AM60", "镁合金", "MAGNESIUM"]),
    ("锌合金", ["ZAMAK", "锌合金", "ZINC"]),
    ("弹簧钢", ["65MN", "60SI2MN", "50CRVA", "弹簧钢"]),
    ("轴承钢", ["GCr15", "GCr15SI MN", "轴承钢"]),
    ("工具钢", ["CR12MOV", "SKD11", "SKH51", "DC53", "工具钢", "模具钢"]),
    ("粉末冶金", ["粉末冶金", "PM ", "烧结"]),
    ("玻璃/陶瓷", ["玻璃", "陶瓷", "AL2O3", "ZRO2", "石英"]),
    ("钢", ["Q195", "Q215", "Q235", "Q275", "Q345", "Q355", "20CR", "40CR", "CR40",
            "42CRMO", "20CRMNTI", "65MN", "38CRMOAL", "SW-MATERIAL", "碳钢", "合金钢"]),
]

# 工艺 → 适用材料类别（用于"材料与技术要求矛盾"）
PROCESS_RULES = [
    ("钢的热处理(淬火/调质/渗碳等)",
     r"淬火|调质|渗碳|渗氮|氮化|高频|发黑|发蓝|磷化|正火|退火|HRC\s*\d",
     ["钢", "铸铁", "铸钢"]),
    ("阳极氧化/硬质氧化",
     r"阳极氧化|硬质氧化|阿洛丁|ALODINE",
     ["铝合金"]),
    ("电镀锌/镀锌",
     r"电镀锌|镀锌|镀白锌|镀彩锌",
     ["钢"]),
    ("电镀镍/化学镀镍",
     r"化学镀镍|电镀镍|镀镍",
     ["钢", "铜合金", "铝合金"]),
]

FIELD_ALIASES = {
    "图号": ["图样代号", "图号", "代号", "零件号", "零件代号", "drawingno", "partnumber", "partno"],
    "名称": ["图样名称", "名称", "零件名称", "零件名", "description", "name", "title"],
    "材料": ["材料", "材质", "材料牌号", "material"],
    "类别": ["类别", "类型", "type", "category"],
    "规格": ["规格", "型号", "spec", "specification"],
    "版本": ["版本", "版次", "版本号", "revision", "rev", "version"],
    "单重": ["单重", "重量", "质量", "weight", "mass"],
    "厂家": ["厂家", "供应商", "制造商", "vendor", "supplier"],
    "数量": ["数量", "qty", "quantity"],
    "单位": ["单位", "unit"],
    "备注": ["备注", "remark", "note", "notes"],
    "设计": ["设计", "design"],
    "审核": ["审核", "校对", "check"],
    "标准化": ["标准化", "standardized"],
    "工艺": ["工艺", "process"],
    "批准": ["批准", "approved"],
}

# ---------------------------------------------------------------- 表面处理
# 图纸上"写了表面处理"的判定关键词
SURFACE_KEYWORDS = re.compile(
    r"镀|发黑|发蓝|磷化|钝化|喷漆|喷塑|喷涂|电泳|阳极氧化|硬质氧化|达克罗|"
    r"防锈|涂油|涂覆|阿洛丁|alodine|锌镍|电镀|氧化处理",
    re.I)

# 材料类别 → 是否需要必须写表面处理
#   惰性/自带防腐：不需强制（不锈钢、铝、铜、塑料）
#   易锈/易氧化：必须写（碳钢、合金钢、铸铁、铸钢）
SURFACE_OPTIONAL_KINDS = {"不锈钢", "铝合金", "铜合金", "工程塑料", "橡胶",
                          "钛合金", "玻璃/陶瓷"}
SURFACE_REQUIRED_KINDS = {"钢", "铸铁", "铸钢", "弹簧钢", "轴承钢", "工具钢",
                          "镁合金", "锌合金", "粉末冶金"}
# 说明：镁合金/锌合金易氧化、粉末冶金易锈，按"必须写表面处理"处理；
#       钛合金耐蚀、橡胶/塑料/玻璃陶瓷惰性 → 不要求写。

# ---------------------------------------------------------------- 材料用途适宜性
# 零件功能关键词 → 推荐材料（用于"这个材料适不适合做这个零件"的提示）
# 只做**提示**，不下硬结论：设计者可能有意选材（如成本、耐蚀优先）。
FUNCTION_MATERIAL_RULES = [
    (r"弹片|弹簧片|簧片|弹性元件|膜片|卡簧|挡圈|波形垫|碟簧",
     ["301", "302", "17-7PH", "631", "65MN", "60SI2MN", "QBE2", "铍铜", "钛合金", "TC4"],
     "弹性元件需要较高的弹性极限与疲劳强度；304 屈服强度低、弹性差，"
     "做弹片容易塑性变形（不回弹）。常用：301/302 冷轧不锈钢、65Mn、60Si2Mn、17-7PH、铍铜 QBe2。",
     None, []),
    (r"齿轮|齿条", ["45", "40CR", "42CRMO", "20CR", "20CRMNTI", "40CRNIMO", "QT600"],
     "齿轮通常需调质/渗碳淬火，常用 45、40Cr、42CrMo、20CrMnTi 等；"
     "铝制齿轮（6061/7075）需硬质阳极氧化提高耐磨性。",
     r"硬质氧化|硬质阳极氧化|阳极氧化", ["6061", "7075", "2A12", "铝合金", "铝板"]),
    (r"轴|销轴|传动轴", ["45", "40CR", "42CRMO", "20CR", "40CRNIMO", "38CRMOAL",
                     "304", "316", "SUS", "6061", "7075", "2A12", "铝合金",
                     "钛合金", "TC4"],
     "轴类常用 45、40Cr、42CrMo 等调质钢；耐蚀/轻载/低速工况也常用 304、316 不锈钢或 "
     "6061/7075 铝合金（铝轴通常配硬质氧化）。", None, []),
    (r"导轨|滑块|耐磨", ["GCr15", "CR12MOV", "SKD11", "40CR", "38CRMOAL"],
     "耐磨件常用 GCr15、Cr12MoV、SKD11 或渗氮钢。", None, []),
]

GENERAL_TOL_PAT = re.compile(r"1804")
GDT_STD_PAT = re.compile(r"(1184|未注几何公差|几何公差.{0,6}未注)")


# ---------------------------------------------------------------- 工具函数
def _n(s):
    """归一化：去空白、全角括号、大小写统一。"""
    t = str(s or "").strip()
    t = t.replace("（", "(").replace("）", ")").replace("＃", "#")
    return re.sub(r"\s+", "", t).lower()


def _norm_upper(s):
    t = str(s or "").strip().upper()
    t = t.replace("＃", "#").replace("－", "-").replace("—", "-").replace("_", "")
    return re.sub(r"\s+", "", t)


def material_kind(raw):
    """材料类别；识别不出返回 ''（不判错）。

    先查关键词，再用**牌号规则**兜底（材质库里不一定有 45/Q235/SUS304 这些写法）：
      45 / 45# / 45钢        → 钢（优质碳素钢）
      Q235 / Q345            → 钢
      304 / 316 / SUS304     → 不锈钢
      5052 / 6061 / 7075     → 铝合金
    """
    t = _norm_upper(raw)
    if not t:
        return ""
    for kind, keys in CLASS_KEYWORDS:
        for k in keys:
            kk = _norm_upper(k)
            if not kk:
                continue
            # 1~2 字符关键词（T2/AL/PC）按独立记号匹配，避免 HT200 命中 T2
            if len(kk) <= 2:
                if re.search(r"(?<![A-Z0-9])" + re.escape(kk) + r"(?![A-Z0-9])", t):
                    return kind
            elif kk in t:
                return kind

    import re as _re
    core = _re.sub(r"[^0-9A-Z#]", "", t)
    # 两位数字：10~70 → 优质碳素钢
    m = _re.fullmatch(r"(\d{2})#?", core)
    if m and 10 <= int(m.group(1)) <= 70:
        return "钢"
    # Q235 / Q345
    if _re.fullmatch(r"Q\d{3}[A-Z]?", core):
        return "钢"
    # SUS304 / 304 / 316L
    if _re.fullmatch(r"(SUS)?(304|316|321|310S?)L?", core):
        return "不锈钢"
    # 5052 / 6061 / 7075 / 6082（四位、5~7 打头）
    if _re.fullmatch(r"\d{4}", core) and core[0] in "567":
        return "铝合金"
    return ""


def material_core(raw):
    """材料主牌号（用于"图上是否出现"的宽松比对）：45#→45、铝板5052→5052、6061-T6→6061。"""
    t = _norm_upper(raw)
    t = re.sub(r"(板材|板料|板|棒料|棒|管材|管|带材|带|卷|料|圆钢|方钢|锻件|铸件|型材)$", "", t)
    t = re.sub(r"^(铝|钢|铜|不锈钢)", "", t)
    t = re.sub(r"-(T\d|H\d|O|2B|2H|F|H112|T651)$", "", t)
    t = t.replace("#", "").replace("号钢", "").replace("钢", "")
    return t


_THREAD_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(M\s?\d+(?:\.\d+)?(?:\s?[x×]\s?\d+(?:\.\d+)?)?"
    r"|G\s?\d+(?:/\d+)?"
    r"|NPT\s?\d+(?:/\d+)?|NPTF\s?\d+(?:/\d+)?|BSPT?\s?\d+(?:/\d+)?"
    r"|Rc\s?\d+(?:/\d+)?|Rp\s?\d+(?:/\d+)?"
    r"|Tr\s?\d+(?:[x×]\d+(?:\.\d+)?)?"
    r"|UNC\s?\d+(?:-\d+)?|UNF\s?\d+(?:-\d+)?|UNEF\s?\d+(?:-\d+)?"
    r"|#?\d+/\d+-\d+\s?(?:UNC|UNF|UNEF)|#?\d+-\d+\s?(?:UNC|UNF|UNEF))",
    re.I)

_THREAD_STD = [
    (r"^M", "公制螺纹" ),
    (r"^G|^BSP|^Rc|^Rp|^R\d", "管螺纹(55°)"),
    (r"^NPT|^NPTF", "美制管螺纹(60°)"),
    (r"^Tr", "梯形螺纹"),
    (r"^UNC|^UNF|^UNEF|^#?\d+/\d+-|^#\d+-|^\d+-\d+", "美制统一螺纹"),
]


THREAD_SPECS = {
    # code: (标准, 公称大径mm, 内螺纹基本小径mm, 牙距mm)
    # 公制粗牙 GB/T 193 / ISO 724
    "M3": ("GB/T 193 公制粗牙", 3.0, 2.459, 0.5),
    "M4": ("GB/T 193 公制粗牙", 4.0, 3.242, 0.7),
    "M5": ("GB/T 193 公制粗牙", 5.0, 4.134, 0.8),
    "M6": ("GB/T 193 公制粗牙", 6.0, 4.917, 1.0),
    "M8": ("GB/T 193 公制粗牙", 8.0, 6.647, 1.25),
    "M10": ("GB/T 193 公制粗牙", 10.0, 8.376, 1.5),
    "M12": ("GB/T 193 公制粗牙", 12.0, 10.106, 1.75),
    "M16": ("GB/T 193 公制粗牙", 16.0, 13.835, 2.0),
    "M20": ("GB/T 193 公制粗牙", 20.0, 17.294, 2.5),
    "M24": ("GB/T 193 公制粗牙", 24.0, 20.752, 3.0),
    # 美制统一细牙 UNF（用户提供表）
    "#6-40UNF": ("ASME B1.1 UNF", 3.51, 2.82, 0.635),
    "#8-36UNF": ("ASME B1.1 UNF", 4.17, 3.40, 0.706),
    "#10-32UNF": ("ASME B1.1 UNF", 4.83, 3.97, 0.794),
    "1/4-28UNF": ("ASME B1.1 UNF", 6.35, 5.37, 0.907),
    "5/16-24UNF": ("ASME B1.1 UNF", 7.94, 6.79, 1.058),
    "3/8-24UNF": ("ASME B1.1 UNF", 9.53, 8.38, 1.058),
    "7/16-20UNF": ("ASME B1.1 UNF", 11.11, 9.92, 1.270),
    "1/2-20UNF": ("ASME B1.1 UNF", 12.70, 11.33, 1.270),
    "9/16-18UNF": ("ASME B1.1 UNF", 14.29, 12.93, 1.411),
    "5/8-18UNF": ("ASME B1.1 UNF", 15.88, 14.35, 1.411),
    # 美制统一粗牙 UNC
    "1/4-20UNC": ("ASME B1.1 UNC", 6.35, 5.10, 1.270),
    "5/16-18UNC": ("ASME B1.1 UNC", 7.94, 6.73, 1.411),
    "3/8-16UNC": ("ASME B1.1 UNC", 9.53, 8.24, 1.588),
    "7/16-14UNC": ("ASME B1.1 UNC", 11.11, 9.74, 1.814),
    "1/2-13UNC": ("ASME B1.1 UNC", 12.70, 11.31, 1.954),
    "9/16-12UNC": ("ASME B1.1 UNC", 14.29, 12.83, 2.117),
    "5/8-11UNC": ("ASME B1.1 UNC", 15.88, 14.34, 2.309),
    "3/4-10UNC": ("ASME B1.1 UNC", 19.05, 17.32, 2.540),
    "7/8-9UNC": ("ASME B1.1 UNC", 22.23, 20.34, 2.822),
    "1-8UNC": ("ASME B1.1 UNC", 25.40, 23.32, 3.175),
    # 55° 管螺纹 G（ISO 228，非密封）
    "G1/8": ("ISO 228 管螺纹(55°)", 9.728, 8.566, 0.907),
    "G1/4": ("ISO 228 管螺纹(55°)", 13.157, 11.445, 1.337),
    "G3/8": ("ISO 228 管螺纹(55°)", 16.662, 14.950, 1.337),
    "G1/2": ("ISO 228 管螺纹(55°)", 20.955, 18.631, 1.814),
    "G5/8": ("ISO 228 管螺纹(55°)", 22.911, 20.587, 1.814),
    "G3/4": ("ISO 228 管螺纹(55°)", 26.441, 24.117, 1.814),
    "G7/8": ("ISO 228 管螺纹(55°)", 30.201, 27.877, 1.814),
    "G1": ("ISO 228 管螺纹(55°)", 33.249, 30.291, 2.309),
    "G1-1/8": ("ISO 228 管螺纹(55°)", 37.897, 34.939, 2.309),
    "G1-1/4": ("ISO 228 管螺纹(55°)", 41.910, 38.952, 2.309),
    # 55° 密封管螺纹 Rc/Rp（GB/T 7306 / ISO 7，基面尺寸）
    "Rc1/8": ("GB/T 7306 密封管螺纹(55°)", 9.230, 8.566, 0.907),
    "Rc1/4": ("GB/T 7306 密封管螺纹(55°)", 13.157, 11.445, 1.337),
    "Rc3/8": ("GB/T 7306 密封管螺纹(55°)", 16.662, 14.950, 1.337),
    "Rc1/2": ("GB/T 7306 密封管螺纹(55°)", 20.955, 18.631, 1.814),
    "Rc3/4": ("GB/T 7306 密封管螺纹(55°)", 26.441, 24.117, 1.814),
    "Rc1": ("GB/T 7306 密封管螺纹(55°)", 33.249, 30.291, 2.309),
    "Rp1/8": ("GB/T 7306 密封管螺纹(55°)", 9.728, 8.566, 0.907),
    "Rp1/4": ("GB/T 7306 密封管螺纹(55°)", 13.157, 11.445, 1.337),
    "Rp1/2": ("GB/T 7306 密封管螺纹(55°)", 20.955, 18.631, 1.814),
    "Rp3/4": ("GB/T 7306 密封管螺纹(55°)", 26.441, 24.117, 1.814),
    # 60° 锥管螺纹 NPT（ANSI B1.20.1，基面基本尺寸）
    "NPT1/8": ("ANSI B1.20.1 NPT", 10.287, 8.736, 0.941),
    "NPT1/4": ("ANSI B1.20.1 NPT", 13.716, 11.844, 1.411),
    "NPT3/8": ("ANSI B1.20.1 NPT", 17.145, 15.240, 1.411),
    "NPT1/2": ("ANSI B1.20.1 NPT", 21.336, 18.980, 1.814),
    "NPT3/4": ("ANSI B1.20.1 NPT", 26.670, 24.299, 1.814),
    "NPT1": ("ANSI B1.20.1 NPT", 33.401, 30.611, 2.209),
    "NPT1-1/4": ("ANSI B1.20.1 NPT", 42.164, 38.988, 2.209),
    "NPT1-1/2": ("ANSI B1.20.1 NPT", 48.260, 44.958, 2.209),
    "NPT2": ("ANSI B1.20.1 NPT", 60.325, 56.607, 2.209),
    "NPT1/16": ("ANSI B1.20.1 NPT", 7.938, 6.401, 0.941),
    # 梯形螺纹 Tr（GB/T 5796，基本尺寸）
    "Tr8x1.5": ("GB/T 5796 梯形螺纹", 8.0, 6.2, 1.5),
    "Tr10x2": ("GB/T 5796 梯形螺纹", 10.0, 7.5, 2.0),
    "Tr12x2": ("GB/T 5796 梯形螺纹", 12.0, 9.5, 2.0),
    "Tr14x2": ("GB/T 5796 梯形螺纹", 14.0, 11.5, 2.0),
    "Tr16x4": ("GB/T 5796 梯形螺纹", 16.0, 11.5, 4.0),
    "Tr20x4": ("GB/T 5796 梯形螺纹", 20.0, 15.5, 4.0),
    "Tr24x5": ("GB/T 5796 梯形螺纹", 24.0, 18.5, 5.0),
    "Tr28x5": ("GB/T 5796 梯形螺纹", 28.0, 22.5, 5.0),
    "Tr32x6": ("GB/T 5796 梯形螺纹", 32.0, 25.5, 6.0),
    "Tr40x7": ("GB/T 5796 梯形螺纹", 40.0, 32.5, 7.0),
}




# 公制粗牙螺距（GB/T 193 / ISO 261，M2–M50）
METRIC_COARSE = [
    (2, 0.4), (2.5, 0.45), (3, 0.5), (4, 0.7), (5, 0.8), (6, 1.0),
    (8, 1.25), (10, 1.5), (12, 1.75), (14, 2.0), (16, 2.0), (18, 2.5),
    (20, 2.5), (22, 2.5), (24, 3.0), (27, 3.0), (30, 3.5), (33, 3.5),
    (36, 4.0), (39, 4.0), (42, 4.5), (45, 4.5), (48, 5.0), (50, 3.0),
]
# 公制细牙常用螺距
METRIC_FINE = [
    (2, 0.25), (2.5, 0.35), (3, 0.35), (4, 0.5), (5, 0.5), (6, 0.75),
    (8, 0.75), (8, 1.0), (10, 0.75), (10, 1.0), (10, 1.25),
    (12, 1.0), (12, 1.25), (12, 1.5), (14, 1.0), (14, 1.5),
    (16, 1.0), (16, 1.5), (18, 1.0), (18, 1.5),
    (20, 1.0), (20, 1.5), (20, 2.0), (22, 1.5), (22, 2.0),
    (24, 1.5), (24, 2.0), (27, 1.5), (27, 2.0), (27, 3.0),
    (30, 1.5), (30, 2.0), (30, 3.0), (33, 1.5), (33, 2.0), (33, 3.5),
    (36, 1.5), (36, 2.0), (36, 3.0), (39, 2.0), (39, 3.0), (39, 4.0),
    (42, 2.0), (42, 3.0), (42, 4.5), (45, 2.0), (45, 3.0), (45, 4.5),
    (48, 2.0), (48, 3.0), (48, 5.0), (50, 2.0), (50, 3.0),
]


def _metric_minor(d, pitch):
    """内螺纹基本小径 D1 = d - 1.082532P（GB/T 196）。"""
    return round(float(d) - 1.082532 * float(pitch), 3)


for _d, _p in METRIC_COARSE:
    _key = "M%g" % _d
    THREAD_SPECS.setdefault(_key, ("GB/T 193 公制粗牙", float(_d), _metric_minor(_d, _p), float(_p)))
    _fine_key = "M%gx%g" % (_d, _p)
    THREAD_SPECS.setdefault(_fine_key, ("GB/T 193 公制粗牙", float(_d), _metric_minor(_d, _p), float(_p)))
for _d, _p in METRIC_FINE:
    _coarse = dict(METRIC_COARSE).get(_d)
    if _coarse is not None and abs(float(_coarse) - float(_p)) < 1e-9:
        continue  # 与粗牙同螺距不重复登记
    _key = "M%gx%g" % (_d, _p)
    THREAD_SPECS.setdefault(_key, ("GB/T 193 公制细牙", float(_d), _metric_minor(_d, _p), float(_p)))

def _spec_key(code):
    """把图纸写法归一成表键（大小写/前缀/UNF 的 # 号统一）。"""
    c = re.sub(r"\s+", "", str(code or ""))
    c = c.replace("×", "x").replace("X", "x").replace("－", "-")
    up = c.upper()
    m = re.match(r"^M(\d+)x(\d+(?:\.\d+)?)$", up, re.I)
    if m:
        return "M" + m.group(1) + "x" + ("%g" % float(m.group(2)))
    m = re.match(r"^G(\d+(?:-\d+/\d+|/\d+)?)$", up, re.I)
    if m:
        return "G" + m.group(1)
    # 统一螺纹：#6-40UNF / 6-40UNF / 1/4-20UNC
    m = re.match(r"^#?(\d+(?:/\d+)?)-(\d+)(UNC|UNF|UNEF)$", up)
    if m:
        size = m.group(1)
        if "/" in size:
            return size + "-" + m.group(2) + m.group(3)
        return "#" + size + "-" + m.group(2) + m.group(3)
    # 其它（Rc/Rp/Tr/NPT）保持原大小写：统一转成首字母大写形式
    m = re.match(r"^(RC|RP|TR|NPT|NPTF|BSP|BSPT)(.+)$", up)
    if m:
        return m.group(1) + m.group(2)
    return c


_THREAD_SPECS_UP = {str(k).upper(): v for k, v in THREAD_SPECS.items()}


def thread_spec(code):
    """螺纹规格查询：返回 dict(标准/公称大径/小径/牙距/粗细牙) 或 None。"""
    key = _spec_key(code)
    hit = THREAD_SPECS.get(key) or _THREAD_SPECS_UP.get(str(key).upper())
    if hit:
        _coarse = None
        try:
            _m = re.match(r"^M(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", str(key).upper(), re.I)
            if _m:
                _base = THREAD_SPECS.get("M%g" % float(_m.group(1)))
                if _base:
                    _coarse = abs(float(_m.group(2)) - float(_base[3])) < 1e-6
        except Exception:
            _coarse = None
        return {"标准": hit[0], "公称大径": hit[1], "小径": hit[2], "牙距": hit[3],
                "粗牙": _coarse}
    # M8x1.25 == M8 默认粗牙：按粗牙表返回，并标记“粗牙”
    m = re.match(r"^M(\d+)x(\d+(?:\.\d+)?)$", str(key).upper(), re.I)
    if m:
        base = THREAD_SPECS.get("M" + m.group(1)) or _THREAD_SPECS_UP.get("M" + m.group(1))
        if base:
            pitch = float(m.group(2))
            is_coarse = abs(pitch - float(base[3])) < 1e-6
            if not is_coarse:
                return None          # 细牙数值需查细牙表，不拿粗牙冒充
            return {"标准": base[0], "公称大径": base[1], "小径": base[2],
                    "牙距": pitch, "粗牙": True}
    return None


def dim_display(d):
    """返回 (显示内容, 附加备注)。覆盖：螺纹/直径/计数前缀/配合代号/深度/贯穿/均布/包容要求。"""
    if not isinstance(d, dict):
        return "", ""
    ta = str(d.get("text_all") or "")
    tp = str(d.get("text_parts") or "")
    txt = str(d.get("text") or "")
    hc = str(d.get("hole_callout") or "")
    blob = re.sub(r"<[^>]+>", " ", ta + " " + txt + " " + hc)
    prefix = ""
    m0 = re.match(r"^\|*\s*(\d+)\s*(?:-\s*|[xX×]\s*)", ta)
    if m0:
        prefix = m0.group(1) + "-"
    # 统一螺纹写法 6-40UNF 的“6-”不是数量前缀
    try:
        if re.search(r"#?\d+-\d+\s?(?:UNC|UNF|UNEF)", ta, re.I):
            prefix = ""
    except Exception:
        pass
    notes = []
    # 0) 倒角/圆角/方形/球面/参考尺寸（必须先于螺纹判断，避免 R2 被当管螺纹）
    try:
        _plain = re.sub(r"<[^>]+>", " ", ta + " " + txt).replace("|", " ")
        _plain = re.sub(r"\s+", " ", _plain).strip()
        mref = re.match(r"^\(\s*(\d+(?:\.\d+)?)\s*\)$", _plain)
        if mref:
            return mref.group(1), "参考尺寸（仅参考，不检验）"
        mch = re.match(r"^C\s?(\d+(?:\.\d+)?)$", _plain, re.I)
        if mch:
            return "C" + mch.group(1), "倒角"
        mrf = re.match(r"^R\s?(\d+(?:\.\d+)?)$", _plain, re.I)
        if mrf:
            return "R" + mrf.group(1), "圆角"
        msq = re.search(r"□\s*(\d+(?:\.\d+)?)", _plain)
        if msq:
            return "□" + msq.group(1), "正方形"
        msp = re.search(r"S\s*(?:<MOD-DIAM>|[⌀φØ])\s*(\d+(?:\.\d+)?)", ta + " " + txt, re.I)
        if msp:
            return "S⌀" + msp.group(1), "球面"
    except Exception:
        pass
    # 1) 螺纹（优先）
    tm = _THREAD_RE.search(blob)
    if not tm and hc:
        tm = _THREAD_RE.search(hc)
    if tm:
        code = re.sub(r"\s+", "", tm.group(1))
        if prefix and code.startswith(prefix):
            code = code[len(prefix):]
        content = prefix + code
        std = ""
        for pat, name in _THREAD_STD:
            if re.match(pat, code, re.I):
                std = name
                break
        if std:
            notes.append(std)
        try:
            _sp = thread_spec(code)
            if re.match(r"^M\d", code, re.I):
                if _sp and _sp.get("粗牙") is not None:
                    notes.append("粗牙" if _sp["粗牙"] else "细牙")
                elif re.search(r"[x×]\d", code):
                    notes.append("细牙")
                else:
                    notes.append("粗牙（默认）")
            if _sp:
                notes.append("公称大径%g / 小径%g / 牙距%g（%s）"
                             % (_sp["公称大径"], _sp["小径"], _sp["牙距"], _sp["标准"]))
            else:
                notes.append("规格未收录（请人工确认牙距/大径/小径）")
        except Exception:
            pass
        md = re.search(r"<HOLE-DEPTH>\s*([0-9.]+)", ta)
        if not md:
            md = re.search(r"深\s*([0-9]+(?:\.[0-9]+)?)", blob)
        if md:
            notes.append("螺纹深" + md.group(1))
        if "贯穿" in ta or "贯穿" in txt:
            notes.append("贯穿")
        if "均布" in blob:
            notes.append("均布")
        if "<MOD-EP>" in ta:
            content += " Ⓔ"
            notes.append("包容要求 Ⓔ")
        return content, "；".join(notes)
    # 1.5) 槽标注（如 3X5）
    if "<MOD-DIAM>" not in ta and not tm:
        _slot = re.search(r"(\d+(?:\.\d+)?)\s*[Xx×]\s*(\d+(?:\.\d+)?)", blob)
        if _slot:
            _w, _l = _slot.group(1), _slot.group(2)
            return ("槽 " + _w + "×" + _l), ("槽宽" + _w + "；槽长" + _l)

    # 2) 普通尺寸
    try:
        base = fmt_dim(d)
    except Exception:
        base = ""
    if not base:
        try:
            base = "%g" % abs(float(d.get("value_mm")))
        except Exception:
            base = ""
    base = re.sub(r"<[^>]+>", "", str(base)).strip()
    if _dimension_has_diameter(d) and not base.startswith(("⌀", "φ", "Ø")):
        base = "⌀" + base
    elif ("⌀" in ta or "φ" in ta or "Ø" in ta) and not base.startswith(("⌀", "φ", "Ø")):
        base = "⌀" + base
    content = (prefix + base).replace("φ", "⌀").replace("Ø", "⌀")
    if "<MOD-EP>" in ta:
        content += " Ⓔ"
        notes.append("包容要求 Ⓔ")
    # 配合代号（H8/h7/P7…）
    fm = re.search(r"(?<![A-Za-z0-9])([HhKkGgFfJjNnPpSsTtUu]\d{1,2}[A-Za-z]?)(?![A-Za-z0-9])", blob)
    if fm:
        content += " " + fm.group(1)
        notes.append("配合 " + fm.group(1))
    md = re.search(r"<HOLE-DEPTH>\s*([0-9.]+)", ta)
    if not md:
        md = re.search(r"深\s*([0-9]+(?:\.[0-9]+)?)", blob)
    if md:
        notes.append("深度" + md.group(1))
    # 孔标注主/次尺寸判定（2026-10-29 修正）：
    #   “N x ⌀d” = 主特征（孔组，如 4×⌀3.2 通孔）；
    #   ⌀6.6-90° / SINK / CBORE = 沉头/倒角参数 → 只进备注；
    #   不能把沉头直径当主尺寸。
    try:
        _dias = re.findall(r"<MOD-DIAM>\s*([0-9.]+)", ta)
        if len(_dias) >= 2:
            _sub = re.search(r"(\d+)\s*[xX×]\s*<MOD-DIAM>\s*([0-9.]+)", ta)
            _extras = []
            if _sub:
                # 孔组为主特征：内容改成 N-⌀主直径
                _n_sub, _d_sub = _sub.group(1), _sub.group(2)
                content = _n_sub + "-⌀" + _d_sub
                if "<HOLE-SINK>" in ta:
                    _extras.append("沉头⌀" + _dias[0] + ("-90°" if "90" in ta else ""))
                _extras.append("孔组 " + _n_sub + "×⌀" + _d_sub)
            else:
                if "<HOLE-SINK>" in ta and _dias:
                    _extras.append("沉头⌀" + _dias[0])
            if "贯穿" in blob or "贯通" in blob:
                _extras.append("贯穿")
            if "均布" in blob:
                _extras.append("均布")
            if _extras:
                notes.append("；".join(dict.fromkeys(_extras)))
            # 原始文本留档，便于人工核对（不算问题）
            notes.append("原文：" + re.sub(r"\s+", " ", ta.replace("|", " ")).strip())
    except Exception:
        pass
    if "贯穿" in blob or "贯通" in blob:
        notes.append("贯穿")
    if "均布" in blob:
        notes.append("均布")
    try:
        if "<HOLE-CBORE>" in ta:
            _dias2 = re.findall(r"<MOD-DIAM>\s*([0-9.]+)", ta)
            if _dias2:
                notes.append("沉台孔⌀" + _dias2[0])
            _dp = re.search(r"<HOLE-DEPTH>\s*([0-9.]+)", ta)
            if _dp:
                notes.append("沉台孔深" + _dp.group(1))
    except Exception:
        pass
    return content, "；".join(notes)


def fmt_dim(d):
    txt = str(d.get("text") or "").strip()
    if txt:
        return txt
    try:
        v = float(d.get("value_mm") or 0)
        return f"{v:g}"
    except Exception:
        return ""


def _dimension_has_diameter(d):
    """Diameter is exposed inconsistently by SW; inspect every authoritative text part."""
    if not isinstance(d, dict):
        return False
    blob = " ".join(str(d.get(k) or "") for k in
                    ("text_all", "text_parts", "text", "hole_callout"))
    up = blob.upper()
    return ("<MOD-DIAM>" in up or "⌀" in blob or "φ" in blob or "Ø" in blob or
            str(d.get("is_diameter") or "").lower() in ("1", "true", "yes"))


def tol_missing(d):
    """是否"没标公差"。
    SW 会把没有公差的尺寸返回成 tol_value="0,0"（上/下偏差都是 0），
    这种也按"未注公差"处理，否则会误进"尺寸公差"页签。
    """
    t = str(d.get("tol_type", "") or "").strip()
    v = str(d.get("tol_value", "") or "").strip()
    if v:
        nums = []
        for it in re.split(r"[/,;\\s]+", v):
            try:
                nums.append(abs(float(it)))
            except Exception:
                pass
        if nums and any(x > 0 for x in nums):
            return False
    return t in ("", "0", "None", "-1")


# ---------------------------------------------------------------------------
# 审图结果三大类（用户 2026-10-23 要求：图纸信息 / 尺寸 / 技术要求）
# ---------------------------------------------------------------------------
# C# DLL 的问题表格只有 级别|问题|建议 三列，没有"类别"列，
# 所以分类要在 Python 侧带出去：category 字段给程序用，title 前缀给人看。
# 用户 2026-10-23 反馈：不接受「前缀式」分类 → 默认关闭。
# 真正的三分类需要改 C# DLL 的表格渲染（按 category 插分段标题行）。
SHOW_CATEGORY_PREFIX = False

_CAT_RULES = [
    # 技术要求：技术要求语义、粗糙度、工艺、一般公差声明
    ("技术要求", ("TECH-", "GB-TECH", "GB-1804", "GB-1184", "GB1804", "GB1184",
                  "NOTES", "RA-", "CORE-RA", "PROCESS", "GB-TOLTXT", "GB-GDT",
                  "GB47067-GEN", "GB47067-OBSOLETE", "ROUGH-", "WELD-",
                  "MOLD-", "SM-", "AUTO-", "BASE-", "GEN-",
                  # 2026-10-24：表面处理 / 材料与工艺冲突 都属于技术要求范畴（用户明确）
                  "SURFACE-", "CORE-SURF", "MAT-PROCESS")),
    # 尺寸：尺寸/公差/几何/孔/键/挡圈/螺纹/塑料件
    ("尺寸", ("DIM-", "OUTLINE-", "TOL-", "GTOL-", "GDNT-", "CHAIN-", "HOLE-",
              "THD-", "THREAD-", "KEY-", "RING-", "FIT-", "GEAR-", "HYD-",
              "PLASTIC-", "ENVELOPE-", "CORE-DIM", "CORE-TOL", "CORE-HOLE",
              "CORE-CHAIN", "CORE-OVERALL", "CORE-TIGHT", "SIZE-")),
    # 图纸信息：图框/标题栏/材料/视图/表面处理要求
    ("图纸信息", ("DRAWING-", "VIEW-", "GB-MAT", "CORE-MAT", "MAT-", "MATERIAL-",
                  "GB-NAME", "GB-DWGNO", "GB-REV", "TITLE", "TB-")),
]


def category_of(rule_id):
    """按 rule_id 前缀归档到三大类；认不出的一律归「图纸信息」。"""
    rid = str(rule_id or "").upper()
    for cat, prefixes in _CAT_RULES:
        for p in prefixes:
            if rid.startswith(p.upper()):
                return cat
    return "图纸信息"


def issue(rule_id, severity, title, detail="", suggestion="", standard="", location=""):
    return {
        "rule_id": rule_id, "category": category_of(rule_id), "severity": severity,
        "title": title, "detail": detail or title, "suggestion": suggestion,
        "standard": standard, "location": location, "evidence": "",
    }


# ---------------------------------------------------------------- 表头
def normalize_material(raw, alt=""):
    """
    归一化 SolidWorks 给的材料串，只留**牌号**。

    2026-10-23 实测：SW 会把「材质库名」拼进材料字段，真实数据是
        props['材料'] = '结构设计常用材料材质|SPCC'
        props['材质'] = 'SPCC'
    以前直接拿前者去图框文字里比对，永远找不到 → 误报「图框材料栏未填材料」。
    这里统一拆成 SPCC；有干净的「材质」字段时优先用它。
    """
    t = str(raw or "").strip()
    a = str(alt or "").strip()
    looks_like_lib = ("|" in t) or re.search(r"材质\s*[\[\(【]", t) is not None
    if a and looks_like_lib:
        return a
    if "|" in t:
        t = t.split("|")[-1].strip()
    m = re.search(r"材质\s*[\[\(【]([^\]\)】]+)[\]\)】]", t)
    if m:
        t = m.group(1).strip()
    return t


def _is_calc_dim(v):
    """
    是不是**计算/展开尺寸**：小数位 ≥3（如展开长 2356.1945）。

    钣金展开图经常带这种尺寸；拿它去判"轮廓长"必然误报。
    取 3 位是因为工程图标注一般 1~2 位小数。
    """
    try:
        txt = f"{float(v):.6f}".rstrip("0").rstrip(".")
    except Exception:
        return False
    return ("." in txt) and len(txt.split(".")[1]) >= 3


_EXPAND_VIEW_RE = re.compile(r"展开|平展|平板|FLAT", re.IGNORECASE)


def outline_from_views(view_boxes, sheet_names=None):
    """
    按 X / Y 方向在**所有视图**里取最大轮廓（用户 2026-10-23 要求）。

    排除两类视图：
      · 图纸格式视图（名字等于图纸名、或尺寸明显是整张图纸）
      · 展开图 / 平板型式（它不是零件外形）

    返回 {"长": mm, "宽": mm}；算不出来返回 {}（调用方按"不知道"处理，不猜）。
    """
    sn = set(str(x) for x in (sheet_names or []))
    longs, shorts = [], []
    for vb in (view_boxes or []):
        try:
            name = str(vb.get("view") or "")
            w = float(vb.get("w_mm") or 0)
            h = float(vb.get("h_mm") or 0)
        except Exception:
            continue
        if not name or name in sn:
            continue
        if _EXPAND_VIEW_RE.search(name):
            continue
        if w <= 0 or h <= 0:
            continue
        if max(w, h) > 1000:          # 整张图纸量级，不是零件
            continue
        longs.append(max(w, h))
        shorts.append(min(w, h))
    if not longs:
        return {}
    return {"长": round(max(longs), 1), "宽": round(max(shorts), 1)}


# ---------------------------------------------------------------------------
# 板厚标注核查（2026-10-24）
# 用户确认的方案：四层都查不到才报「未标注」；图上写的与 3D 不符报「不一致」。
# ---------------------------------------------------------------------------
_THICK_PATTERNS = [
    r"板厚\s*[:：=]?\s*(\d+(?:\.\d+)?)",
    r"料厚\s*[:：=]?\s*(\d+(?:\.\d+)?)",
    r"厚度\s*[:：=]?\s*(\d+(?:\.\d+)?)",
    r"(?<![A-Za-z0-9])t\s*[:：=]\s*(\d+(?:\.\d+)?)",     # t=1.5 / t:1.5
    r"(?<![A-Za-z0-9])t(\d+(?:\.\d+)?)(?!\d)",            # t1.5
    r"[δΔ]\s*[:：=]?\s*(\d+(?:\.\d+)?)",                  # δ=1.5
    r"(\d+(?:\.\d+)?)\s*mm\s*厚",
    r"厚\s*(\d+(?:\.\d+)?)\s*mm",                         # 厚1.5mm
]


def _thick_in_text(text):
    """从一段文字里抽板厚声明。返回 (值, 片段) 或 (None, "")。"""
    t = str(text or "")
    if not t:
        return None, ""
    for p in _THICK_PATTERNS:
        for m in re.finditer(p, t):
            try:
                v = float(m.group(1))
            except Exception:
                continue
            # 板厚合理区间：0.2~20mm（再大就不是钣金板厚了，宁可不认）
            if 0.2 <= v <= 20.0:
                return v, t.strip()[:48]
    return None, ""


def all_declared_thickness(thickness_mm, dims=None, tech_text="", header_text=""):
    """
    收集**所有**层级里出现的板厚声明（不只取第一个），用于反向核查。
    返回 [(layer, value, snippet), ...]，按 尺寸→技术要求→标题栏 顺序。
    """
    th = float(thickness_mm or 0)
    out = []
    if th <= 0:
        return out
    # ① 尺寸：凡是 ≈ 板厚的尺寸都算一条
    tol = max(0.02, th * 0.0005)
    for d in (dims or []):
        try:
            v = abs(float(d.get("value_mm")))
        except Exception:
            continue
        if abs(v - th) <= tol:
            out.append(("尺寸", v, "尺寸 %g" % v))
            break
    # ② 技术要求（可能有多条声明，全收）
    t = str(tech_text or "")
    for p in _THICK_PATTERNS:
        for m in re.finditer(p, t):
            try:
                v = float(m.group(1))
            except Exception:
                continue
            if 0.2 <= v <= 20.0:
                out.append(("技术要求", v, t.strip()[:48]))
    # ③ 标题栏 / 材料栏
    h = str(header_text or "")
    for p in _THICK_PATTERNS:
        for m in re.finditer(p, h):
            try:
                v = float(m.group(1))
            except Exception:
                continue
            if 0.2 <= v <= 20.0:
                out.append(("标题栏", v, h.strip()[:48]))
    return out


def declared_thickness(thickness_mm, dims=None, tech_text="", header_text=""):
    """
    四层检查「图上有没有标板厚」，返回 dict：
        {"found": bool, "layer": 层名, "value": 值, "snippet": 片段}
    层次：① 尺寸 ② 技术要求 ③ 标题栏/材料 ④ 其他注释（由调用方并入 tech_text）
    """
    decls = all_declared_thickness(thickness_mm, dims, tech_text, header_text)
    if not decls:
        return {"found": False, "layer": "", "value": 0.0, "snippet": ""}
    layer, value, snippet = decls[0]
    return {"found": True, "layer": layer, "value": value, "snippet": snippet}


# ---------------------------------------------------------------------------
# 表面处理耐蚀性（2026-10-24 接入原生路径）
# 判据来自 sw_qc_gb.SURFACE_TREATMENT_CORROSION（工程经验值，可改）
# ---------------------------------------------------------------------------
_GENERIC_CORR_MEASURE = ("防锈", "油封", "浸油", "涂油", "封闭", "封孔",
                         "涂装", "喷漆", "喷塑", "电泳", "钝化")


def surface_treatments_in_text(text):
    """找出文中出现的表面处理（长词优先，避免子串重复）。返回 [(名称, 信息dict)]"""
    try:
        from sw_qc_gb import SURFACE_TREATMENT_CORROSION as TBL
    except Exception:
        return []
    low = (text or "").lower()
    spans = []
    for key, info in TBL.items():
        k = key.lower()
        start = 0
        while True:
            i = low.find(k, start)
            if i < 0:
                break
            spans.append((i, i + len(k), key, info))
            start = i + len(k)
    out = []
    for st, en, key, info in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if any(o_s <= st and en <= o_e and (o_e - o_s) > (en - st)
               for o_s, o_e, _k, _i in spans):
            continue
        if key not in [k for k, _ in out]:
            out.append((key, info))
    return out


def surface_capability(notes_text):
    """
    评估表面处理的防腐能力。
    返回 dict：{found, names[], level, note, missing[]} 
    """
    found = surface_treatments_in_text(notes_text)
    if not found:
        return {"found": False, "names": [], "level": "", "note": "", "missing": []}

    # 整体等级以**主处理**（第一个出现的，通常是主镀层）为准；
    # 不能因为"彩锌+钝化"里钝化是强，就把整体说成 500h —— 钝化只是锌层后处理。
    items = [{"name": k,
              "level": str(i.get("level") or ""),
              "note": str(i.get("note") or "")} for k, i in found]
    best = items[0]["level"] if items else ""
    notes = [it["note"] for it in items if it["note"]]
    missing = []
    for _k, i in found:
        req = list(i.get("requires") or [])
        if not req:
            continue
        if not any(r in (notes_text or "") for r in req) and \
           not any(g in (notes_text or "") for g in _GENERIC_CORR_MEASURE):
            missing.append(_k)
    return {"found": True, "names": [k for k, _ in found], "items": items,
            "level": best, "note": "；".join(notes)[:200], "missing": missing}


# ---------------------------------------------------------------------------
# 几何公差 → 尺寸 关联（2026-10-24）
#   用户在 φ5 上加了「直线度 0.01 Ⓜ」，要能在尺寸列表和公差计算框格里体现。
#   关联方式：同视图 + 挂接点最近（≤60mm）。取不到坐标则放弃关联，不硬猜。
# ---------------------------------------------------------------------------
# 符号名/描述 → 符号字符（GetSymName/GetSymDesc 兜底用）
GTOL_NAME_MAP = [
    ("straight", "—"), ("直线", "—"),
    ("flat", "▱"), ("平面", "▱"),
    ("round", "○"), ("circularity", "○"), ("圆度", "○"),
    ("cyl", "⌭"), ("圆柱", "⌭"),
    ("lineprof", "⌒"), ("线轮廓", "⌒"),
    ("surfprof", "⌓"), ("面轮廓", "⌓"),
    ("angular", "∠"), ("angle", "∠"), ("倾斜", "∠"),
    ("perp", "⊥"), ("垂直", "⊥"),
    ("parallel", "∥"), ("平行", "∥"),
    ("position", "⌖"), ("位置度", "⌖"),
    ("conc", "◎"), ("同轴", "◎"),
    ("symmetry", "⌯"), ("对称", "⌯"),
    ("totalrunout", "⌰"), ("全跳动", "⌰"),
    ("circrunout", "↗"), ("runout", "↗"), ("圆跳动", "↗"), ("跳动", "↗"),
]


def _sym_from_names(gt):
    """从 symnames / text 里按关键字找符号字符"""
    blob = " ".join(str(x) for x in (gt.get("symnames") or []))
    blob += " " + str(gt.get("text") or "")
    low = blob.lower()
    for kw, ch in GTOL_NAME_MAP:
        if kw.lower() in low:
            return ch
    return ""


# ===========================================================================
# SW2016 实测：GetFrameSymbols3 返回字符串标记
#   <IGTOL-STRAIGHT> 直线度 / <MOD-MMC> Ⓜ / <MOD-DIAM> φ / <CL-M> …
# ===========================================================================
GTOL_TOKEN_MAP = {
    "<IGTOL-STRAIGHT>": "—", "<IGTOL-FLAT>": "▱", "<IGTOL-ROUND>": "○",
    "<IGTOL-CYL>": "⌭", "<IGTOL-CYLINDRICITY>": "⌭",
    "<IGTOL-LINEPROF>": "⌒", "<IGTOL-SURFPROF>": "⌓", "<IGTOL-SPROF>": "⌓", "<IGTOL-PROF>": "⌓",
    "<IGTOL-ANG>": "∠", "<IGTOL-ANGULARITY>": "∠",
    "<IGTOL-PERP>": "⊥", "<IGTOL-PARALLEL>": "∥",
    "<IGTOL-POSITION>": "⌖", "<IGTOL-POSI>": "⌖", "<IGTOL-POS>": "⌖", "<IGTOL-CONC>": "◎",
    "<IGTOL-SYMMETRY>": "⌯",
    # SW 内部短名：SRUN=圆跳动, TRUN=全跳动（实测 <IGTOL-SRUN>0.02<CL-M>B-C）
    "<IGTOL-SRUN>": "↗", "<IGTOL-TRUN>": "⌰",
    "<IGTOL-CIRCRUNOUT>": "↗", "<IGTOL-TOTALRUNOUT>": "⌰",
    # 兼容无尖括号写法
    "IGTOL-STRAIGHT": "—", "IGTOL-FLAT": "▱", "IGTOL-ROUND": "○",
    "IGTOL-CYL": "⌭", "IGTOL-LINEPROF": "⌒", "IGTOL-SURFPROF": "⌓", "IGTOL-SPROF": "⌓", "IGTOL-PROF": "⌓",
    "IGTOL-ANG": "∠", "IGTOL-PERP": "⊥", "IGTOL-PARALLEL": "∥",
    "IGTOL-POSITION": "⌖", "IGTOL-POSI": "⌖", "IGTOL-POS": "⌖", "IGTOL-CONC": "◎", "IGTOL-SYMMETRY": "⌯",
    "IGTOL-SRUN": "↗", "IGTOL-TRUN": "⌰",
    "IGTOL-CIRCRUNOUT": "↗", "IGTOL-TOTALRUNOUT": "⌰",
}

# 修正符号 token
MOD_TOKEN_MAP = {
    # 2026-10-25 实测：SW2016 把"包容原则"返回成 <MOD-EP>（Envelope Principle），
    # 不是 <MOD-ENVELOPE> —— 之前漏了这条，所以 Ⓔ 一直读不到。
    "<MOD-EP>": "Ⓔ", "<MOD-ENVELOPE>": "Ⓔ", "MOD-EP": "Ⓔ", "MOD-ENVELOPE": "Ⓔ",
    "<MOD-MMC>": "Ⓜ", "<MOD-LMC>": "Ⓛ", "<CL-M>": "Ⓜ", "<CL-L>": "Ⓛ",
    "<MOD-FREE>": "Ⓕ", "<MOD-TANGENT>": "Ⓣ", "<MOD-PROJECTED>": "Ⓟ",
    "MOD-MMC": "Ⓜ", "MOD-LMC": "Ⓛ",
}


def _sym_from_tokens(blob):
    """从 token 串里找几何公差符号"""
    up = blob.upper()
    for tok, ch in GTOL_TOKEN_MAP.items():
        if tok.upper() in up:
            return ch
    return ""


def _mod_from_tokens(blob):
    """从 token 串里找修正符号（Ⓜ/Ⓛ/Ⓔ…）"""
    up = blob.upper()
    for tok, ch in MOD_TOKEN_MAP.items():
        if tok.upper() in up:
            return ch
    return ""


GTOL_CODE_MAP = {
    "12": "", "13": "⌯", "14": "—", "15": "▱", "16": "○", "17": "⌭",
    "18": "⌒", "19": "⌓", "20": "∠", "21": "⊥", "22": "∥", "23": "⌖",
    "24": "◎", "25": "↗", "26": "⌰", "27": "↗", "28": "⌰",
}
_MOD_CODE_MAP = {"1": "Ⓜ", "2": "Ⓛ", "3": "Ⓢ"}


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _zone_suffix_from_text(text):
    """读取公差值后的 CZ/SZ 修饰；它是公差带属性，不是公差框类型。"""
    upper = str(text or "").upper()
    has_cz = bool(re.search(r"(?<![A-Z])CZ(?![A-Z])", upper))
    has_sz = bool(re.search(r"(?<![A-Z])SZ(?![A-Z])", upper))
    if has_cz and has_sz:
        return "冲突"
    if has_cz:
        return "CZ"
    if has_sz:
        return "SZ"
    return ""


def _raw_frame_sym(frame):
    """返回本层实际符号格；复合下层即使已有有效继承符号，仍必须返回空。"""
    if isinstance(frame, dict) and "raw_sym" in frame:
        return str(frame.get("raw_sym") or "").strip()
    return str((frame or {}).get("sym") or "").strip() if isinstance(frame, dict) else ""


def parse_gtol(gt):
    """
    解析一条几何公差为 {sym, val, mod, dat1, dat2, dat3, label, x, y, view}

    框格数据来自 C#：frames[i] = "<symbols>|<values>"
       symbols 是 GetFrameSymbols3 的数组串：GCS, TolDia1, TolMC1, TolDia2, TolMC2, DatMC1..
       values 是 GetFrameValues：公差1, 公差2, 基准1, 基准2, 基准3
    兼容：符号数组为空时退回用 text 里的符号字符。
    """
    if not isinstance(gt, dict):
        return None
    frames = gt.get("frames") or []
    if frames:
        first = str(frames[0])
        if "|" in first:
            syms, vals = first.split("|", 1)
        else:
            syms, vals = first, ""
    else:
        # 部分 SW 版本能读到显示文本，却拿不到 GetFrameSymbols3/GetFrameValues。
        # 旧逻辑在这里直接返回 None，导致“API 已读取、报告里完全消失”。
        raw_text = str(gt.get("text") or "").strip()
        if not raw_text:
            return None
        syms = raw_text
        plain = re.sub(r"<[^>]*>", "", raw_text).strip()
        value_match = re.search(r"[-+]?[0-9]+(?:\.[0-9]+)?", plain)
        value = value_match.group(0) if value_match else ""
        datums = []
        if value_match:
            tail = plain[value_match.end():].strip(" |,;/-")
            for token in re.split(r"[\s|,;/-]+", tail):
                token = token.strip().upper()
                if re.fullmatch(r"[A-Z]", token):
                    datums.append(token)
        vals = ",".join([value, ""] + datums[:3] + [""] * max(0, 3 - len(datums[:3])))
    # CZ/SZ 可能以 SW token 形式存在，必须在清除 <...> 之前提取。
    zone_type = _zone_suffix_from_text(str(syms) + "|" + str(vals))
    codes = [c.strip() for c in syms.replace("，", ",").split(",") if c.strip()]
    # 保留空位：GetFrameValues 是 [公差1, 公差2, 基准1, 基准2, 基准3]，位置有意义
    vlist = [v.strip() for v in vals.replace("，", ",").split(",")]

    # ---- 符号 ----
    sym = ""
    dia = False
    # 2026-10-25 修：多框格注解的 text 含**所有**框格 token，若混进来会导致
    # 每个 frame 都识别成第一条的符号（实测：3 框格全变直线度）。
    # 符号只从本 frame 的 syms 里取；text 仅作兜底。
    _blob = syms or str(gt.get("text") or "")
    # ① 优先：SW2016 的字符串标记（实测）
    sym = _sym_from_tokens(_blob)
    if "<MOD-DIAM>" in _blob.upper():
        dia = True
    # 2026-10-26 修：框格符号串里没有 <MOD-DIAM>、但注解 text 里有（实测 转轴：
    # <IGTOL-STRAIGHT><MOD-DIAM>0.05<CL-M>）→ 直径 φ 会丢。
    # 多框格注解不能这么退（text 含所有框格），只有单框格才退回。
    if not dia:
        try:
            if len(gt.get("frames") or []) <= 1 and "<MOD-DIAM>" in str(gt.get("text") or "").upper():
                dia = True
        except Exception:
            pass
    # 兜底：值里本来就有 φ
    if not dia and any("φ" in str(v) for v in vlist):
        dia = True
    # ② 退回：数字符号码
    if not sym and codes:
        _c0 = str(codes[0]).strip()
        if _c0 and _c0 not in ("0", "-1"):
            sym = GTOL_CODE_MAP.get(_c0, "")
        if len(codes) >= 2 and str(codes[1]) in ("1", "-1"):
            dia = True
    if not sym:
        # 兜底 1：符号名/描述（GetSymName / GetSymDesc）
        sym = _sym_from_names(gt)
    if not sym:
        # 兜底 2：直接在文本里找符号字符
        txt = str(gt.get("text") or "")
        for ch in ("⌖", "◎", "∥", "⊥", "∠", "⌯", "↗", "⌰", "⌭", "⌒",
                   "⌓", "▱", "○", "—"):
            if ch in txt:
                sym = ch
                break
    # ---- 修正符号 ----
    mod = ""
    # ① 优先：字符串标记 <MOD-MMC> / <MOD-LMC> / <MOD-ENVELOPE>
    mod = _mod_from_tokens(_blob)
    # ② 退回：数字码（第 3 位）
    if not mod and len(codes) >= 3:
        mod = _MOD_CODE_MAP.get(str(codes[2]), "")
    if not mod:
        for v in vlist:
            if not v:
                continue
            u = v.upper()
            if u in ("M", "Ⓜ"):
                mod = "Ⓜ"
                break
            if u in ("L", "Ⓛ"):
                mod = "Ⓛ"
                break
    # ---- 值 / 基准 ----
    _clean = lambda x: re.sub(r"<[^>]*>", "", str(x or "")).strip()
    vlist = [_clean(v) for v in vlist]
    val = vlist[0] if vlist else ""
    if "φ" not in val and dia:
        val = "φ" + val
    if zone_type in ("CZ", "SZ") and not _zone_suffix_from_text(val):
        val = (val + " " + zone_type).strip()
    dat1 = vlist[2] if len(vlist) >= 3 else ""
    dat2 = vlist[3] if len(vlist) >= 4 else ""
    dat3 = vlist[4] if len(vlist) >= 5 else ""
    if not sym and not val:
        return None
    name = {"—": "直线度", "▱": "平面度", "○": "圆度", "⌭": "圆柱度",
            "⌒": "线轮廓度", "⌓": "面轮廓度", "∠": "倾斜度", "⊥": "垂直度",
            "∥": "平行度", "⌖": "位置度", "◎": "同轴度", "⌯": "对称度",
            "↗": "圆跳动", "⌰": "全跳动"}.get(sym, "几何公差")
    label = name + (" " + val if val else "") + ((" " + mod) if mod else "")
    return {"sym": sym, "val": val, "mod": mod, "dat1": dat1, "dat2": dat2, "dat3": dat3,
            "zone_type": zone_type,
            "label": label, "x": _f(gt.get("x")), "y": _f(gt.get("y")),
            "view": str(gt.get("view") or "")}


# 几何公差 → 是否计入"径向配合作用边界"（2026-10-25 用户要求：系统自动判，不让用户勾选）
#   计入：形状类（直线度/圆度/圆柱度）+ 位置类（同轴/位置/跳动）
#   不计入：端面方向类（平面度/垂直度/平行度/倾斜度）、轮廓度、对称度
GTOL_RADIAL_RULE = {
    "—": (True, "形状：影响径向"),
    "○": (True, "形状：影响径向"),
    "⌭": (True, "形状：影响径向"),
    "◎": (True, "位置：影响径向"),
    "⌖": (True, "位置：影响径向"),
    "↗": (True, "位置：影响径向"),
    "⌰": (True, "位置：影响径向"),
    "▱": (False, "端面形状：不影响径向"),
    "∥": (False, "方向：按端面处理，不计"),
    "⊥": (False, "方向：按端面处理，不计"),
    "∠": (False, "方向：按端面处理，不计"),
    "⌒": (False, "轮廓度：不计"),
    "⌓": (False, "轮廓度：不计"),
    "⌯": (False, "对称度：不计"),
}
SHAPE_SYMS = ("—", "○", "⌭")


def _tol_um_to_mm(x):
    try:
        return float(x) * 1000.0
    except Exception:
        return None


def gtol_value_mm(val_text):
    """从 "0.02" / "φ0.01" 里取数(mm)。"""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(val_text or ""))
    return float(m.group(1)) if m else None


def _tol_mm_vals(dim):
    """把 tol_value（米）转成 mm 列表。"""
    raw = str((dim or {}).get("tol_value") or "")
    out = []
    for it in raw.replace("，", ",").replace(";", ",").split(","):
        it = it.strip()
        if not it:
            continue
        try:
            out.append(float(it) * 1000.0)
        except Exception:
            pass
    return out


# GB/T 1182 公差带包含关系：父级 → 可涵盖的子级
GTOL_CONTAINS = {
    "⌭": ("○", "—"),                  # 圆柱度 涵盖 圆度、直线度
    "▱": ("—",),                       # 平面度 涵盖 直线度（同一平面）
    "⌰": ("↗", "⌭", "◎", "○"),        # 全跳动 涵盖 圆跳动/圆柱度/同轴度/圆度
    "↗": ("○",),                       # 圆跳动 涵盖 圆度
    "⌖": ("◎", "⌯"),                  # 位置度 涵盖 同轴度/对称度（基准相同）
}


def resolve_gtols(gtols, is_env):
    """
    规则引擎：决定每条几何公差是否计入最大作用边界，并给出原因。
    返回 (list, sigma_t_mm)
    """
    # 先按物理标注重建框格。复合框下层没有独立符号格，但在计算和 AI 事实中
    # 必须继承上层有效符号；同时保留 raw_sym，避免刷新后被误判为“同符号组合框”。
    grouped = {}
    source = [g for g in (gtols or []) if isinstance(g, dict)]
    for source_index, source_g in enumerate(source):
        identity = str(source_g.get("group_id") or source_g.get("_source_group") or
                       source_g.get("gtol_id") or source_g.get("raw_id") or
                       ("ITEM-%d" % source_index))
        grouped.setdefault(identity, []).append((source_index, source_g))
    effective_symbols = {}
    frame_facts = {}
    for identity, members in grouped.items():
        frames = [x[1] for x in members]
        ftype, zone_type, frame_reason = classify_tolerance_frames(frames)
        inherited = next((_raw_frame_sym(x) for x in frames if _raw_frame_sym(x)), "")
        for source_index, source_g in members:
            raw_symbol = _raw_frame_sym(source_g)
            effective_symbols[id(source_g)] = inherited if ftype == "复合" and not raw_symbol else raw_symbol
            frame_facts[id(source_g)] = (ftype, zone_type, frame_reason)

    items = []
    for g_index, g in enumerate(gtols or []):
        if not isinstance(g, dict):
            continue
        raw_sym = _raw_frame_sym(g)
        sym = effective_symbols.get(id(g), raw_sym)
        inc, reason = GTOL_RADIAL_RULE.get(sym, (False, "未知类型：默认不计"))
        item = dict(g)
        ftype, zone_type, frame_reason = frame_facts.get(id(g), ("独立", "", "独立公差（单框）：框数=1"))
        item.update({"sym": sym, "raw_sym": raw_sym, "effective_sym": sym,
                     "frame_type": ftype, "zone_type": zone_type, "frame_reason": frame_reason,
                     "val": g.get("val") or "", "dat1": g.get("dat1") or "",
                     "dat2": g.get("dat2") or "", "dat3": g.get("dat3") or "", "mod": g.get("mod") or "",
                     "value_mm": gtol_value_mm(g.get("val")),
                     "include": bool(inc), "reason": reason, "effective": bool(inc)})
        # 涵盖/取严只能发生在同一公差框组/同一物理注解内。不同物理标注即使
        # 符号和值相同，也可能控制不同区段，不能相互吞掉。
        item["_resolve_scope"] = (str(g.get("group_id") or g.get("_source_group") or
                                      g.get("gtol_id") or g.get("raw_id") or "") or
                                  ("ITEM-%d" % g_index))
        items.append(item)

    # ① 尺寸带 Ⓔ：形状类不叠加
    if is_env:
        for it in items:
            if it["sym"] in SHAPE_SYMS and it["include"]:
                it["include"] = False
                it["reason"] = "尺寸带 Ⓔ：形状误差已被尺寸带约束，不叠加"

    # ② 包含关系：父级存在 → 子级被涵盖（不叠加）
    for it in items:
        if not it["include"]:
            continue
        for parent_sym, children in GTOL_CONTAINS.items():
            if it["sym"] in children:
                # 同组里是否有该父级且也计入
                for p in items:
                    if p is it or not p["include"] or p["sym"] != parent_sym:
                        continue
                    if p.get("_resolve_scope") != it.get("_resolve_scope"):
                        continue
                    # 基准要能对上（父级没标基准则不做限制）
                    if any(p.get(k) and it.get(k) and p.get(k) != it.get(k)
                           for k in ("dat1", "dat2", "dat3")):
                        continue
                    it["include"] = False
                    it["reason"] = "被 " + parent_sym + "（" + {
                        "⌭": "圆柱度", "▱": "平面度", "⌰": "全跳动",
                        "↗": "圆跳动", "⌖": "位置度"}.get(parent_sym, "父级") + "）涵盖，不叠加"
                    break
            if not it["include"]:
                break

    # ③ 同一项目 + 同基准多值 → 取最严（最小），其余标取严
    seen = {}
    for it in items:
        if not it["include"] or not it["value_mm"]:
            continue
        key = (it.get("_resolve_scope"), it["sym"], it["dat1"], it["dat2"], it["dat3"])
        if key not in seen:
            seen[key] = it
            continue
        keep = seen[key]
        if it["value_mm"] < keep["value_mm"]:
            keep["include"] = False
            keep["reason"] = "同一项目取严：被更小值 %g 取代" % it["value_mm"]
            it["reason"] = "同一项目取严（保留较小值）"
            seen[key] = it
        else:
            it["include"] = False
            it["reason"] = "同一项目取严：未超过已取严值 %g" % keep["value_mm"]

    sigma = 0.0
    for it in items:
        if it["include"] and it["value_mm"]:
            sigma += it["value_mm"]
    return items, round(sigma, 4)


def build_gtol_summary(dim, is_env):
    """
    返回 (list_of_gtols, sigma_t_mm, note_text)
    每条 gtol: {sym,val,dat1,dat2,dat3,mod,include,reason}
    """
    raw = dim.get("gtols")
    if not raw and dim.get("gtol"):
        raw = [dim.get("gtol")]
    if not raw:
        return [], 0.0, ""
    out, sigma = resolve_gtols(raw, is_env)
    if not out:
        return [], 0.0, ""
    heads = []
    for g in out:
        _sym = g.get("sym") or ""
        _val = str(g.get("val") or "")
        _dia = "⌀ " if (g.get("dia") or g.get("diameter") or "⌀" in _sym or "φ" in str(g.get("raw") or "")) else ""
        s1 = ((_sym + " ") if _sym else "") + (_dia + _val).strip()
        if g.get("mod"):
            s1 += g["mod"]
        if g["dat1"]:
            s1 += " |" + str(g["dat1"])
        if g["dat2"]:
            s1 += " |" + str(g["dat2"])
        if g["dat3"]:
            s1 += " |" + str(g["dat3"])
        heads.append(s1)
    if len(out) == 1:
        note = "几何公差：" + heads[0]
    else:
        inc_n = sum(1 for g in out if g["include"])
        # 多条时备注只写汇总（明细在「公差计算」面板逐条列出，避免表格被截断）
        note = "几何 %d 条（计入 %d，Σt=%g）" % (len(out), inc_n, round(sigma, 4))
    return out, round(sigma, 4), note


def parse_gtol_all(gt):
    """
    一个几何公差注解可能含**多个框格**（独立多框、组合多单段或复合框）。
    frames = ["<IGTOL-STRAIGHT>,,,,,|0.03,,,,",
              "<IGTOL-CYL>,,,,,|0.01,,,,",
              "<IGTOL-PERP>,,,,,|0.02,,A,,"]
    → 返回这 N 个框格各自的解析结果（共用同一坐标/视图）。
    """
    if not isinstance(gt, dict):
        return []
    frames = gt.get("frames") or []
    if not frames:
        one = parse_gtol(gt)
        return [one] if one else []
    out = []
    for f in frames:
        g2 = dict(gt)
        g2["frames"] = [f]
        it = parse_gtol(g2)
        if it:
            out.append(it)
    return out


def _gtol_link_key(item):
    """与 C# QcRunner.GtolKey 保持一致：符号|值|基准1|基准2|基准3|修正符。"""
    return "|".join([
        str(item.get("sym") or ""), str(item.get("val") or ""),
        str(item.get("dat1") or ""), str(item.get("dat2") or ""),
        str(item.get("dat3") or ""),
        str(item.get("mod") or ""),
    ])


def _gtol_link_lookup(mapping, item):
    """读取新六段签名；兼容升级前没有基准3的五段历史记忆。"""
    key = _gtol_link_key(item)
    if key in mapping:
        return True, mapping.get(key)
    if not str(item.get("dat3") or ""):
        old = "|".join([str(item.get("sym") or ""), str(item.get("val") or ""),
                        str(item.get("dat1") or ""), str(item.get("dat2") or ""),
                        str(item.get("mod") or "")])
        if old in mapping:
            return True, mapping.get(old)
    return False, None


def load_gtol_link_map(dwg_no):
    """读人工归属记忆：{公差签名: 尺寸内容}。路径来自环境变量 QC_GTOL_LINK_MAP。"""
    path = os.environ.get("QC_GTOL_LINK_MAP", "").strip()
    if not path or not dwg_no or not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8-sig"))
    except Exception:
        return {}
    node = (data or {}).get(dwg_no) or {}
    if not isinstance(node, dict):
        return {}
    out = {}
    for _k, _v in node.items():
        _sv = str(_v or "")
        out[_k] = re.sub(r"（人工指定）+", "", _sv).strip()
        if str(_k).endswith("#near"):
            _letter = str(_k)[:-5]
            out[_letter + "#src"] = "人工指定" if "人工指定" in _sv else "关联"
    return out


def _dim_link_content(d):
    """与报告/界面一致的尺寸内容（含 ⌀ 前缀），用于匹配人工归属记忆。"""
    try:
        base = fmt_dim(d)
    except Exception:
        base = ''
    if not base:
        try:
            base = '%g' % abs(float(d.get('value_mm')))
        except Exception:
            base = ''
    base = re.sub(r'<[^>]+>', '', str(base)).strip()
    try:
        if _dimension_has_diameter(d) and not base.startswith(('⌀', 'φ', 'Ø')):
            base = '⌀' + base
    except Exception:
        pass
    return base


def _apply_memory_override(dims, dwg_no, unlinked=None):
    """人工归属记忆优先于自动关联：把已挂在 A 尺寸的公差按记忆改挂到 B。"""
    mapping = load_gtol_link_map(dwg_no)
    if not mapping:
        return 0
    by_content = {}
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        try:
            _content = _dim_link_content(d)
            if _content in by_content:
                by_content[_content] = None       # 同名尺寸不唯一：宁可待确认，不能错挂
            else:
                by_content[_content] = d
        except Exception:
            pass
    # 同一签名在图纸上出现多次 → 是图上多条独立公差，不能被单条记忆合并
    _sig_cnt = {}
    for _d in (dims or []):
        if not isinstance(_d, dict):
            continue
        for _g in (_d.get("gtols") or []):
            if isinstance(_g, dict):
                _k = _gtol_link_key(_g)
                _sig_cnt[_k] = _sig_cnt.get(_k, 0) + 1
    moved = 0
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        gs = d.get("gtols")
        if not isinstance(gs, list):
            continue
        keep = []
        for g in gs:
            key = _gtol_link_key(g)
            if _sig_cnt.get(key, 0) > 1:
                keep.append(g)
                continue
            _found, tgt_txt = _gtol_link_lookup(mapping, g)
            if _found:
                if not tgt_txt:          # 记忆为不指定 → 转回未归属，不能静默丢弃
                    if isinstance(unlinked, list):
                        unlinked.append(g)
                    moved += 1
                    continue
                tgt = by_content.get(str(tgt_txt).strip())
                if tgt is not None and tgt is not d:
                    tgt.setdefault("gtols", []).append(g)
                    moved += 1
                    continue
            keep.append(g)
        d["gtols"] = keep
    return moved


def apply_saved_gtol_links(dims, unlinked, dwg_no):
    """把记忆里的人工归属挂回尺寸。返回 (仍未被关联的列表, 生效条数)。"""
    if not unlinked:
        return unlinked, 0
    mapping = load_gtol_link_map(dwg_no)
    if not mapping:
        return unlinked, 0
    # 尺寸内容 → 原始尺寸（用 fmt_dim，与报告/界面同一口径）
    by_content = {}
    for d in dims:
        if not isinstance(d, dict):
            continue
        try:
            txt = _dim_link_content(d)
        except Exception:
            txt = ""
        if txt:
            _key = str(txt).strip()
            if _key in by_content:
                by_content[_key] = None           # 同名尺寸不唯一时禁止猜测
            else:
                by_content[_key] = d
    keep, applied = [], 0
    for u in unlinked:
        if not isinstance(u, dict):
            continue
        _found, target_txt = _gtol_link_lookup(mapping, u)
        if not _found:
            keep.append(u)
            continue
        if not target_txt:            # 记忆为“不指定”：仍保留在未归属表
            keep.append(u)
            applied += 1
            continue
        d = by_content.get(str(target_txt).strip())
        if d is None:
            # 目标内容带 ⌀ / 前缀时，按数值匹配原始尺寸
            try:
                _nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(target_txt))
                if _nums:
                    _tv = float(_nums[-1])
                    _numeric_matches = []
                    for _dd in dims:
                        if not isinstance(_dd, dict):
                            continue
                        try:
                            if abs(abs(float(_dd.get("value_mm") or 0)) - _tv) <= 0.01:
                                _numeric_matches.append(_dd)
                        except Exception:
                            continue
                    if len(_numeric_matches) == 1:
                        d = _numeric_matches[0]
            except Exception:
                pass
        if d is None:
            keep.append(u)
            continue
        lst = d.get("gtols")
        if not isinstance(lst, list):
            lst = []
            d["gtols"] = lst
        item = dict(u)       # 保留复合框组、顺序、人工被测要素等主数据
        item["reason"] = "人工指定归属（已记忆）"
        item["source"] = "manual"
        lst.append(item)
        d.setdefault("gtol", item)
        applied += 1
    return keep, applied


def link_gtols_to_dims(dims, gtols, max_dist=120.0):
    """按用户口径关联：[几何公差] 的引线箭头指向哪个尺寸就挂哪个；没有箭头时挂最近的尺寸线。

    · 优先用引线箭头坐标 (ax, ay)——这才是“指向”
    · 没有箭头 → 用注解位置 (x, y) 找最近的尺寸标注
    · 同视图约束；距离超过 max_dist 视为未关联（交给人工指定）
    """
    if not gtols:
        return 0, []
    linked = 0
    unlinked = []
    groups = []
    for gt_index, gt in enumerate(gtols):
        if not isinstance(gt, dict):
            continue
        items = parse_gtol_all(gt)
        if not items:
            # 解析失败也必须可见，禁止“读到了却消失”。
            _raw_gtol = str(gt.get("text") or "").strip()
            unlinked.append({"sym": "?", "val": _raw_gtol, "dat1": "", "dat2": "", "dat3": "",
                             "mod": "", "label": "未解析几何公差 " + _raw_gtol,
                             "view": str(gt.get("view") or ""), "parse_error": True,
                             "_source_group": "GFRAME-" + str(gt_index + 1),
                             "_frame_order": 1, "_frame_count": 1})
            continue
        g0 = items[0]
        source_group = "GFRAME-" + str(gt_index + 1)
        for frame_index, parsed_item in enumerate(items):
            parsed_item["_source_group"] = source_group
            parsed_item["_frame_order"] = frame_index + 1
            parsed_item["_frame_count"] = len(items)
        # 形状公差通常独立，但带直径公差带/MMC/LMC 时控制的是尺寸要素；
        # 即使 SW 没有返回引线端点，也允许在近距离内挂到对应尺寸。
        _FORM = ("▱", "—", "○", "⌭")
        try:
            shape_only = all((str(x.get("sym") or "") in _FORM) and
                             not any(str(x.get(k) or "").strip() for k in ("dat1", "dat2", "dat3"))
                             for x in items)
            feature_of_size = any(
                any(mark in str(x.get("val") or "") for mark in ("φ", "⌀", "Ø")) or
                str(x.get("mod") or "").strip() in ("Ⓜ", "Ⓛ")
                for x in items)
        except Exception:
            shape_only = False
            feature_of_size = False
        ax = _f(gt.get("ax"))
        ay = _f(gt.get("ay"))
        gx0 = _f(g0.get("x"))
        gy0 = _f(g0.get("y"))
        # 只有 x/y 同时非零才算有效位置（SW 有时只读到一个坐标）
        if abs(ax) < 1e-9 or abs(ay) < 1e-9:
            ax = ay = 0.0
        if abs(gx0) < 1e-9 or abs(gy0) < 1e-9:
            gx0 = gy0 = 0.0
        use_arrow = (abs(ax) > 1e-9 and abs(ay) > 1e-9)
        tx, ty = (ax, ay) if use_arrow else (gx0, gy0)
        if abs(tx) < 1e-9 and abs(ty) < 1e-9:
            # 位置读取失败（SW 有时只给一个坐标）→ 交给人工指定，不能静默丢
            for it0 in items:
                unlinked.append({"sym": it0.get("sym") or "", "val": it0.get("val") or "",
                                 "dat1": it0.get("dat1") or "", "dat2": it0.get("dat2") or "",
                                 "dat3": it0.get("dat3") or "",
                                 "mod": it0.get("mod") or "", "label": it0.get("label") or "",
                                 "view": it0.get("view") or "",
                                 "_source_group": it0.get("_source_group") or "",
                                 "_frame_order": it0.get("_frame_order") or 1,
                                 "_frame_count": it0.get("_frame_count") or 1})
            continue
        groups.append({"items": items, "x": tx, "y": ty, "view": g0.get("view"),
                       "arrow": use_arrow, "shape_only": shape_only,
                       "feature_of_size": feature_of_size})
    if not groups:
        return 0, unlinked
    _dim_views = set(str(d.get("view") or "") for d in dims if isinstance(d, dict))
    for g in groups:
        _gview = str(g.get("view") or "")
        _view_known = _gview in _dim_views
        cands = []
        for d in dims:
            if not isinstance(d, dict):
                continue
            if _view_known and str(d.get("view") or "") != _gview:
                continue
            dx, dy = _f(d.get("x")), _f(d.get("y"))
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue
            dist = ((dx - g["x"]) ** 2 + (dy - g["y"]) ** 2) ** 0.5
            cands.append((dist, d))
        cands.sort(key=lambda t: t[0])
        best, best_d = (cands[0][1], cands[0][0]) if cands else (None, 1e18)
        second_d = cands[1][0] if len(cands) > 1 else 1e18
        link_limit = max_dist
        if not g.get("arrow") and g.get("shape_only"):
            # 无箭头的普通形状公差只在几乎重合时自动归属；直径公差带或
            # MMC/LMC 明确属于尺寸要素，可使用稍大的安全距离。
            link_limit = 12.0 if g.get("feature_of_size") else 2.0
        if best is not None and best_d <= link_limit:
            lst = best.get("gtols")
            if not isinstance(lst, list):
                lst = []
                best["gtols"] = lst
            for it0 in g["items"]:
                item = {k: it0.get(k) for k in ("sym", "val", "mod", "dat1", "dat2", "dat3", "label")}
                item["_x"] = round(g["x"], 1)
                item["_y"] = round(g["y"], 1)
                item["_source_group"] = it0.get("_source_group") or ""
                item["_frame_order"] = it0.get("_frame_order") or 1
                item["_frame_count"] = it0.get("_frame_count") or 1
                # 最近两个候选几乎等距（<5mm）→ 关联不确定，提示人工确认
                if second_d < 1e17 and (second_d - best_d) < 5.0:
                    item["uncertain"] = True
                lst.append(item)
                best.setdefault("gtol", item)
                linked += 1
            try:
                best["_gtol_arrow"] = bool(g.get("arrow"))
            except Exception:
                pass
        else:
            for it1 in g["items"]:
                unlinked.append({"sym": it1.get("sym") or "", "val": it1.get("val") or "",
                                 "dat1": it1.get("dat1") or "", "dat2": it1.get("dat2") or "",
                                 "dat3": it1.get("dat3") or "",
                                 "mod": it1.get("mod") or "", "label": it1.get("label") or "",
                                 "view": it1.get("view") or "",
                                 "_source_group": it1.get("_source_group") or "",
                                 "_frame_order": it1.get("_frame_order") or 1,
                                 "_frame_count": it1.get("_frame_count") or 1})
    return linked, unlinked



def fill_mod_gtol(dim_rows, dims):
    """
    2026-10-24：analyze_dims 有多个分支，部分分支复制行时会丢掉 modifier/gtol。
    这里按尺寸值反查原始 dim，缺什么补什么（只补不算错）。
    """
    if not dim_rows or not dims:
        return dim_rows
    by_val = {}
    by_id = {}
    for d in dims:
        if not isinstance(d, dict):
            continue
        try:
            v = round(abs(float(d.get("value_mm"))), 3)
        except Exception:
            continue
        by_val.setdefault(v, d)
        if d.get("_dim_id"):
            by_id[str(d.get("_dim_id"))] = d
    for r in dim_rows:
        d = by_id.get(str(r.get("_dim_id") or ""))
        try:
            raw = str(r.get("content") or "").replace("φ", "").replace("Φ", "")
            raw = raw.replace("⌀", "").replace("Ø", "").replace("Ф", "").replace("±", "").strip()
            v = round(abs(float(raw)), 3)
        except Exception:
            v = None
        if not d:
            d = by_val.get(v)
        if not d:
            try:
                _rc = _norm_content(r.get("content"))
                for _d2 in dims:
                    if not isinstance(_d2, dict):
                        continue
                    if _norm_content(dim_display(_d2)[0]) == _rc:
                        d = _d2
                        break
            except Exception:
                d = None
        if not d:
            continue
        if not r.get("modifier"):
            try:
                m = modifier_of_dim(d)
                if m:
                    r["modifier"] = m
            except Exception:
                pass
        try:
            _gs = d.get("gtols")
            if _gs:
                _calculated = r.get("gtols")
                if isinstance(_calculated, list) and _calculated:
                    # Keep include/reason/value_mm calculated by resolve_gtols.
                    # Merge only source identity/group metadata from the raw
                    # dimension; replacing the list made the UI say "not count"
                    # while sigma_t had actually counted the tolerance.
                    _used = set()
                    for _ci, _cg in enumerate(_calculated):
                        if not isinstance(_cg, dict):
                            continue
                        _match_i = None
                        for _ri, _rg in enumerate(_gs):
                            if _ri in _used or not isinstance(_rg, dict):
                                continue
                            if _sig_g(_cg) == _sig_g(_rg):
                                _match_i = _ri
                                break
                        if _match_i is None and _ci < len(_gs):
                            _match_i = _ci
                        if _match_i is None or _match_i >= len(_gs):
                            continue
                        _used.add(_match_i)
                        _rg = _gs[_match_i]
                        if not isinstance(_rg, dict):
                            continue
                        for _mk in ("_x", "_y", "_source_group", "_frame_order", "_frame_count",
                                    "raw_id", "gtol_id", "group_id", "frame_order",
                                    "measured_feature", "label", "view"):
                            if _mk in _rg:
                                _cg[_mk] = _rg.get(_mk)
                else:
                    r["gtols"] = [dict(x) for x in _gs]
        except Exception:
            pass
        if not r.get("gtols") and d.get("gtol"):
            r["gtols"] = [dict(d["gtol"])]
        if not r.get("gtol") and d.get("gtol"):
            r["gtol"] = d["gtol"]
            # 备注里也要体现，不然列表上看不到这个尺寸带了几何公差
            if "几何公差" not in str(r.get("note") or ""):
                _lbl = str(d["gtol"].get("label") or "")
                if _lbl:
                    r["note"] = ((r.get("note") or "") + "；几何公差：" + _lbl).strip("；")
    return dim_rows


def _norm_content(t):
    """尺寸内容归一：去 Ⓔ/空白/φ，便于“人工记忆”匹配。"""
    x = str(t or "")
    for ch in ("Ⓔ", "Ⓜ", "Ⓕ", " ", "\u3000"):
        x = x.replace(ch, "")
    return x.replace("φ", "⌀").replace("Ø", "⌀").strip()


def _sig_g(g):
    return (str(g.get("sym") or ""), str(g.get("val") or ""),
            str(g.get("dat1") or ""), str(g.get("dat2") or ""),
            str(g.get("dat3") or ""), str(g.get("mod") or ""))


def _mark_basic_by_gtol(dim_rows):
    """只保留图纸明确给出的基本尺寸，禁止按附近几何公差自行推断。

    基本尺寸必须来自 SW 的 swTolBASIC/方框尺寸。普通尺寸即使没有 ± 公差，
    或附近存在位置度、轮廓度，也仍然是“无独立尺寸公差”，不能改成基本尺寸。
    """
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        tol = str(r.get("tol") or "").strip()
        if tol == "基本尺寸":
            r["it"] = "—"
            r["capability"] = "基本尺寸（图纸方框尺寸），由几何公差控制"
    return dim_rows


def _strip_orphan_gtol_notes(dim_rows):
    """行里已经没有 gtols，但备注还写着“几何公差：…”→ 去掉，避免重复/矛盾。"""
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        if r.get("gtols"):
            continue
        note = str(r.get("note") or "")
        if "几何公差" not in note:
            continue
        parts = [x for x in note.split("；") if x and "几何公差" not in x]
        r["note"] = "；".join(parts).strip("；")
    return dim_rows


def _dedupe_gtols_global(dim_rows, dwg_no=""):
    """同一条几何公差只能挂在一个尺寸上（人工记忆目标优先，其次第一条出现）。"""
    mapping = load_gtol_link_map(dwg_no) if dwg_no else {}
    want = {}
    for k, v in (mapping or {}).items():
        parts = str(k).split("|")
        if len(parts) >= 5 and v:
            dat3, mod = (parts[4], parts[5]) if len(parts) >= 6 else ("", parts[4])
            sig = (parts[0], parts[1].replace("φ", "").replace("⌀", "").strip(),
                   parts[2], parts[3], dat3, mod)
            want[sig] = _norm_content(v)

    # 收集：sig -> [(row, g)]
    holders = {}
    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        gs = r.get("gtols")
        if not isinstance(gs, list):
            continue
        for g in gs:
            if not isinstance(g, dict):
                continue
            sig = (str(g.get("sym") or ""), str(g.get("val") or "").replace("φ", "").replace("⌀", "").strip(),
                   str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                   str(g.get("dat3") or ""), str(g.get("mod") or ""))
            try:
                _pos = (round(float(g.get("_x") or g.get("x") or 0), 1),
                        round(float(g.get("_y") or g.get("y") or 0), 1))
            except Exception:
                _pos = (0.0, 0.0)
            holders.setdefault(sig + (_pos,), []).append((r, g))

    keep = {}     # id(row) -> list[g]
    # 同一基础签名出现多个位置 → 是图纸上的多条独立公差，不能合并/套用单条记忆
    _base_count = {}
    for _sg in holders:
        _base_count[_sg[:6]] = _base_count.get(_sg[:6], 0) + 1
    for sig, lst in holders.items():
        target = want.get(sig[:5])
        if target is None:
            # 旧记忆可能没记符号（如 "|0.02|A||"）→ 按 值+基准 匹配
            target = want.get(("",) + tuple(sig[1:5]))
        if _base_count.get(sig[:5], 0) > 1:
            target = None
        winner = None
        if target:
            for r, g in lst:
                if _norm_content(r.get("content")) == target:
                    winner = (r, g)
                    break
        if winner is None:
            winner = lst[0]
        wr = winner[0]
        keep.setdefault(id(wr), []).append(winner[1])

    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        gs = keep.get(id(r), [])
        r["gtols"] = gs
        r["gtol"] = gs[0] if gs else None
        if gs:
            lbl = str(gs[0].get("label") or "")
            if lbl and "几何公差" not in str(r.get("note") or ""):
                r["note"] = ((r.get("note") or "") + "；几何公差：" + lbl).strip("；")
    return sum(len(v) for v in holders.values()) - sum(len(v) for v in keep.values())



def _dedupe_row_gtols(dim_rows):
    """同一行的几何公差去重（符号|值|基准1|基准2|基准3|修正符）。"""
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        gs = r.get("gtols")
        if not isinstance(gs, list):
            continue
        seen, out = set(), []
        for g in gs:
            if not isinstance(g, dict):
                continue
            key = (str(g.get("sym") or ""), str(g.get("val") or ""),
                   str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                   str(g.get("dat3") or ""),
                   str(g.get("mod") or ""),
                   round(float(g.get("_x") or 0), 1), round(float(g.get("_y") or 0), 1))
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
        r["gtols"] = out
        if out:
            r["gtol"] = out[0]
            lbl = str(out[0].get("label") or "")
            if lbl and "几何公差" not in str(r.get("note") or ""):
                r["note"] = ((r.get("note") or "") + "；几何公差：" + lbl).strip("；")
    return dim_rows


def _apply_memory_to_rows(dim_rows, dwg_no):
    """把人工归属记忆直接应用到尺寸分析行（保证界面/AI 一致）。"""
    mapping = load_gtol_link_map(dwg_no)
    if not mapping or not dim_rows:
        return 0
    norm_map = {}
    for k, v in mapping.items():
        parts = str(k).split("|")
        if len(parts) >= 5:
            dat3, mod = (parts[4], parts[5]) if len(parts) >= 6 else ("", parts[4])
            kk = "|".join([parts[0], parts[1].replace("φ", "").replace("⌀", "").strip(),
                           parts[2], parts[3], dat3, mod])
            norm_map[kk] = str(v or "")
        else:
            norm_map[str(k)] = str(v or "")
    by_content = {}
    _sig_count = {}
    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        by_content.setdefault(_norm_content(r.get("content")), r)
        for _g0 in (r.get("gtols") or []):
            if isinstance(_g0, dict):
                _sk = (str(_g0.get("sym") or ""), str(_g0.get("val") or ""),
                       str(_g0.get("dat1") or ""), str(_g0.get("dat2") or ""),
                       str(_g0.get("dat3") or ""), str(_g0.get("mod") or ""))
                _sig_count[_sk] = _sig_count.get(_sk, 0) + 1
    moved = 0
    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        for kname in ("gtol", "gtols"):
            cur = r.get(kname)
            if not cur:
                continue
            items = cur if isinstance(cur, list) else [cur]
            keep = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                nkey = "|".join([str(it.get("sym") or ""),
                                 str(it.get("val") or "").replace("φ", "").replace("⌀", "").strip(),
                                 str(it.get("dat1") or ""), str(it.get("dat2") or ""),
                                 str(it.get("dat3") or ""),
                                 str(it.get("mod") or "")])
                tgt = norm_map.get(nkey)
                if tgt is None:
                    keep.append(it)
                    continue
                tr = by_content.get(_norm_content(tgt))
                _sig_here = (str(it.get("sym") or ""), str(it.get("val") or ""),
                             str(it.get("dat1") or ""), str(it.get("dat2") or ""),
                             str(it.get("dat3") or ""), str(it.get("mod") or ""))
                if _sig_count.get(_sig_here, 0) > 1:
                    keep.append(it)          # 同签名出现在多行 → 图纸上有多条，不合并
                    continue
                if tr is None:
                    # 人工指定了一个表中还没有的尺寸（如 ▱0.015 → ⌀42）→ 补一行再挂
                    tr = {"kind": "特征尺寸", "content": str(tgt),
                          "nominal_mm": 0, "tol": "未注", "it": "未注",
                          "capability": "按 GB/T 1804-m 一般公差",
                          "note": "人工指定归属：" + str(tgt),
                          "view": str(r.get("view") or ""), "modifier": ""}
                    dim_rows.append(tr)
                    by_content[_norm_content(tgt)] = tr
                if tr is r:
                    keep.append(it)
                    continue
                lst = tr.get("gtols")
                if not isinstance(lst, list):
                    lst = []
                lst.append(it)
                tr["gtols"] = lst
                tr.setdefault("gtol", it)
                moved += 1
            if kname == "gtols":
                r["gtols"] = keep
            else:
                r["gtol"] = keep[0] if keep else None
    # 同步 gtol 与 gtols 的单/多一致性
    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        if r.get("gtols") and not r.get("gtol"):
            r["gtol"] = r["gtols"][0]
    return moved


def dedupe_surface_issues(checks):
    """
    2026-10-24：表面处理/材料工艺冲突会被"本地查表 + AI 语义"各报一次。
    只做**精确去重**：本地规则标题里的处理名（如「阳极氧化」）在 AI 结论里也出现时，
    才丢掉本地那条；AI 没提到的本地信息一律保留（宁可重复也不要漏信息）。
    """
    if not checks:
        return checks
    ai_txt = ""
    for c in checks:
        if str(c.get("rule_id") or "").startswith("AI"):
            ai_txt += str(c.get("title") or "") + " " + str(c.get("detail") or "") + " "
    if not ai_txt:
        return checks

    def _names(txt):
        """取标题里「…」包起来的关键词（本地规则标题都是这个格式）"""
        out, buf, on = [], "", False
        for ch in str(txt or ""):
            if ch == "「":
                on, buf = True, ""
            elif ch == "」" and on:
                on = False
                if buf.strip():
                    out.append(buf.strip())
            elif on:
                buf += ch
        return out

    out = []
    for c in checks:
        rid = str(c.get("rule_id") or "")
        if rid in ("SURFACE-WEAK", "SURFACE-REQ"):
            names = _names(c.get("title"))
            # 处理名（至少 2 字）在 AI 结论里出现 → 视为重复
            if any(len(nm) >= 2 and nm in ai_txt for nm in names):
                continue
        if rid.startswith("MAT-PROCESS"):
            names = _names(c.get("title"))
            hit = [nm for nm in names if len(nm) >= 2 and nm in ai_txt]
            # 冲突项里出现的处理名（如 阳极氧化/硬质氧化）需按 / 拆开再比
            if not hit:
                for nm in names:
                    for part in str(nm).replace("／", "/").split("/"):
                        part = part.strip()
                        if len(part) >= 2 and part in ai_txt:
                            hit.append(part)
            if hit:
                continue
        out.append(c)
    return out




def _outline_tol():
    try:
        return max(1e-6, float(os.environ.get("QC_OUTLINE_TOL", "0.01")))
    except Exception:
        return 0.01


def _outline_fmt(v):
    return ("%.3f" % float(v)).rstrip("0").rstrip(".")


def outline_match_pairs(part_box, dims, thickness_mm=0.0, is_sheet_metal=False):
    """包络 X/Y/Z → 命中的图纸尺寸内容。

    返回 (pairs, missing, blocked)：
      pairs   = [(axis, ext_value, dim_content), ...]
      missing = [(axis, ext_value), ...]     # 未匹配且**至少有一个方向匹配**时才报
      blocked = True 表示一个方向都没匹配（多半是读取/展开图，不报漏标）
    """
    try:
        box = [float(x) for x in (part_box or [])[:6]]
        if len(box) < 6:
            return [], [], True
        xmin, ymin, zmin, xmax, ymax, zmax = box
    except Exception:
        return [], [], True
    ext = [xmax - xmin, ymax - ymin, zmax - zmin]
    if max(ext) <= 0:
        return [], [], True
    tol = _outline_tol()

    # 候选：图纸尺寸（值 + 内容）；R 尺寸按 2R 折算；直径标记优先
    cands = []
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        try:
            v = abs(float(d.get("value_mm") or 0))
        except Exception:
            continue
        if v <= 0:
            continue
        try:
            content = dim_display(d)[0]
        except Exception:
            content = ""
        ta = str(d.get("text_all") or "") + str(d.get("text") or "")
        is_dia = ("<MOD-DIAM>" in ta) or ("⌀" in content) or ("φ" in content) or ("Ø" in content)
        cands.append((v, content, is_dia))
        # 半径尺寸：R21 → 包络 42（仅当不是直径）
        if not is_dia and re.search(r"(?<![A-Za-z])R\s*[0-9]", ta):
            cands.append((v * 2.0, content, is_dia))

    def hit(v):
        best = None
        for cv, content, is_dia in cands:
            if abs(cv - v) <= tol:
                if is_dia:
                    return content
                if best is None:
                    best = content
        return best

    axes = ("X", "Y", "Z")
    pairs, missing = [], []
    matched = 0
    for i, ax in enumerate(axes):
        v = ext[i]
        if v < 0.05:
            continue
        c = hit(v)
        if c:
            matched += 1
            pairs.append((ax, v, c))
        else:
            missing.append((ax, v))
    if matched == 0:
        return pairs, [], True
    return pairs, missing, False




def _ensure_outline_rows(dim_rows, pairs, dims):
    """包络命中的尺寸若被过滤掉，补一行（保证列表里有该尺寸并带轮廓备注）。"""
    if not pairs or not isinstance(dim_rows, list):
        return dim_rows
    shown = {_norm_content(r.get("content")) for r in dim_rows if isinstance(r, dict)}
    for ax, v, content in pairs:
        key = _norm_content(content)
        if not key or key in shown:
            continue
        d = None
        for dd in (dims or []):
            if not isinstance(dd, dict):
                continue
            try:
                if _norm_content(dim_display(dd)[0]) == key:
                    d = dd
                    break
            except Exception:
                continue
        if d is None:
            continue
        try:
            row = {
                "kind": "特征尺寸",
                "content": content,
                "nominal_mm": round(abs(float(d.get("value_mm") or 0)), 4),
                "tol": fmt_tol_display(d),
                "it": "未注",
                "capability": "按 GB/T 1804-m 一般公差",
                "note": "",
                "view": str(d.get("view") or ""),
                "modifier": "",
            }
        except Exception:
            continue
        # 插到粗糙度之前，保持尺寸在前
        at = len(dim_rows)
        for i, r in enumerate(dim_rows):
            if isinstance(r, dict) and str(r.get("kind") or "").startswith("粗糙度"):
                at = i
                break
        dim_rows.insert(at, row)
        shown.add(key)
    return dim_rows

def _annotate_outline_notes(dim_rows, pairs):
    """把 轮廓-X=42 追加到命中尺寸行的备注（已存在则不重复加）。"""
    if not pairs:
        return dim_rows
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        rc = _norm_content(r.get("content"))
        if not rc:
            continue
        add = []
        for ax, v, content in pairs:
            if _norm_content(content) != rc:
                continue
            seg = "轮廓-%s=%s" % (ax, _outline_fmt(v))
            note = str(r.get("note") or "")
            if seg not in note and seg not in "；".join(add):
                add.append(seg)
        if add:
            note = str(r.get("note") or "")
            r["note"] = ((note + "；") if note else "") + "；".join(add)
    return dim_rows

def box_outline_rows(part_box, thickness_mm=0.0, is_sheet_metal=False,
                     dims=None, part_cat="", tech_text="", header_text=""):
    """
    用 3D 模型包围盒生成「轮廓-X/Y/Z」「板厚」行。

    用户 2026-10-24 要求：
      · 轮廓尺寸必须 **100% 准确、不能误判** → 唯一可信来源是 3D 模型包围盒；
      · 钣金件的板厚要准确识别（取钣金特征厚度，不从尺寸猜）；
      · 机加件 / 塑胶件不判定板厚。

    防误判规则：
      · 只有拿到 6 个包围盒数值才生成，否则返回空（判定层退回视图推断）；
      · 缺尺寸提示只在「至少一个方向能在图上尺寸里对上」时才报，
        避免钣金件按展开图标注时被误判成"漏标"。

    返回 (rows, issues)；rows 是 dim_analysis 同结构；issues 是 (code, sev, title, desc, sug, std)。
    """
    try:
        box = [float(x) for x in (part_box or [])[:6]]
        if len(box) < 6:
            return [], []
        xmin, ymin, zmin, xmax, ymax, zmax = box
    except Exception:
        return [], []
    ext = [xmax - xmin, ymax - ymin, zmax - zmin]
    if max(ext) <= 0:
        return [], []

    vals = []
    for d in dims or []:
        try:
            vals.append(abs(float(d.get("value_mm"))))
        except Exception:
            pass

    def hit(v):
        """图上尺寸里有没有跟 v 对得上的（容差 max(0.02mm, 0.05%)）"""
        tol = max(0.02, abs(v) * 0.0005)
        for dv in vals:
            if abs(dv - v) <= tol:
                return dv
        return None

    def fmt(v):
        return ("%.3f" % v).rstrip("0").rstrip(".")

    rows = []
    issues = []
    axes = ("X", "Y", "Z")
    is_sm = bool(is_sheet_metal) or ("钣金" in str(part_cat) or "板金" in str(part_cat))

    # ---- 板厚（仅钣金件，取钣金特征厚度）----
    th = float(thickness_mm or 0)
    thick_axis = -1
    if is_sm and th > 0:
        best = min(range(3), key=lambda i: abs(ext[i] - th))
        if abs(ext[best] - th) <= max(0.05, th * 0.05):
            thick_axis = best
        _decls = all_declared_thickness(th, dims, tech_text, header_text)
        _tol2 = max(0.02, th * 0.005)
        # 反向核查：**每一处**声明都跟 3D 比，矛盾的都报出来
        _mismatch = []
        for (_layer, _dv, _sn) in _decls:
            if abs(float(_dv) - th) > _tol2:
                _mismatch.append((_layer, float(_dv), _sn))
        if _mismatch:
            _layers = "、".join(sorted(set(m[0] for m in _mismatch)))
            _vals = "、".join("%s mm（%s）" % (fmt(m[1]), m[0]) for m in _mismatch)
            issues.append((
                "THICK-MISMATCH", "critical",
                "板厚不一致：图上 %s，3D 模型 %s mm" % (_vals, fmt(th)),
                "图纸里写的板厚（%s）与 3D 钣金特征的实际厚度 %s mm 不符，"
                "按图下料会做错。" % (_vals, fmt(th)),
                "确认以哪个为准（图还是模型），两边统一后再下发。",
                "GB/T 4458"))
        _decl = {"found": bool(_decls),
                 "layer": _decls[0][0] if _decls else "",
                 "value": _decls[0][1] if _decls else 0}
        if _decl["found"]:
            rows.append({
                "kind": "板厚", "content": fmt(th), "nominal_mm": 0,
                "tol": "未注", "it": "未注",
                "capability": "板厚按材料规格料选取（常规钣金可满足）",
                "note": "来源：3D模型钣金特征 · 图上已标注（%s：%s）"
                        % (_decl["layer"], fmt(_dv)),
                "view": "",
            })
        else:
            rows.append({
                "kind": "板厚", "content": fmt(th), "nominal_mm": 0,
                "tol": "未注", "it": "未注",
                "capability": "板厚按材料规格料选取（常规钣金可满足）",
                "note": "来源：3D模型钣金特征 · 图上未见板厚标注"
                        "（尺寸/技术要求/标题栏/注释 四处均未找到）",
                "view": "",
            })
            if vals or tech_text or header_text:
                issues.append((
                    "OUTLINE-THICK-3D", "major",
                    "钣金件板厚未标注（3D 模型测得 %s mm）" % fmt(th),
                    "3D 模型的钣金特征厚度是 %s mm，但四处都找不到板厚标注："
                    "尺寸里没有、技术要求没有、标题栏/材料栏没有、注释也没有。"
                    % fmt(th),
                    "在图上补标板厚（如 t%s），或在技术要求写明「板厚 %s」。"
                    % (fmt(th), fmt(th)),
                    "GB/T 4458"))

    # ---- 三个方向的轮廓 ----
    matched = 0
    ext_ok = []
    for i, ax in enumerate(axes):
        v = ext[i]
        if v < 0.05:
            continue
        # 注意：XYZ 是**三个方向的最大投影尺寸**，与「板厚」是两回事 ——
        # 即使钣金件 Z 向数值恰好等于板厚，也要单独列出（用户 2026-10-24 明确）。
        m = hit(v)
        if m is not None:
            matched += 1
        ext_ok.append((i, ax, v, m))
        rows.append({
            "kind": "轮廓-" + ax, "content": fmt(v), "nominal_mm": 0,
            "tol": "未注", "it": "未注",
            "capability": "外廓尺寸，常规加工可满足",
            "note": ("来源：3D模型包围盒（%s向）· 图上已标注 %s" % (ax, fmt(m))) if m else
                    ("来源：3D模型包围盒（%s向）· 图上未见对应尺寸" % ax),
            "view": "",
            "modifier": "",
        })

    # ---- 缺尺寸提示（只在图纸确实在标外形时报）----
    if matched >= 1:
        for (i, ax, v, m) in ext_ok:
            if m is not None:
                continue
            # 该方向数值≈板厚时，板厚那条已经报过，别重复
            if i == thick_axis:
                continue
            issues.append((
                "OUTLINE-BOX-MISS", "minor",
                "轮廓尺寸（%s 向）未标注：3D 模型 %s" % (ax, fmt(v)),
                "3D 模型的 %s 向外廓为 %s mm，图上的主尺寸里没有对应值；"
                "另有方向能对上，说明本图是按外形标注的。" % (ax, fmt(v)),
                "核对是否漏标该方向外形尺寸。", "GB/T 4458"))

    return rows, issues


def collect_header(data):
    """抽表头：属性用于取值，工程图标题栏用于确认图上是否已经标注。"""
    # 零件 3D 属性优先；图纸属性只补缺失字段（用户要求：自定义优先，缺了再用别的来源）
    part_props = data.get("props_part") or {}
    draw_props = data.get("props") or {}
    merged = {}
    for k, v in draw_props.items():
        if v is not None and str(v).strip():
            merged[str(k).strip()] = str(v).strip()
    for k, v in part_props.items():
        if v is not None and str(v).strip():
            merged[str(k).strip()] = str(v).strip()

    out = {}
    for field, aliases in FIELD_ALIASES.items():
        val = ""
        for a in aliases:
            for k, v in merged.items():
                if k.strip().lower() == a.lower() or k.strip() == a:
                    val = v
                    break
            if val:
                break
        if not val and field in merged:
            val = merged[field]
        if val:
            out[field] = val
    # 模型配置没有“版本”并不等于工程图缺版本。某些旧模板的版本格只有
    # 单独的 A/B/C，没有“版本：”前缀；按标题栏坐标，把与图号同排且最近
    # 的单字符识别为工程图版本。这样审核的是图纸，而不是只审核模型属性。
    if not out.get("版本"):
        _draw_rev = _drawing_revision_from_title_block(data, out)
        if _draw_rev:
            out["版本"] = _draw_rev
            out["_version_source"] = "工程图标题栏"
    # 【2026-10-23】材料归一化：'库名|牌号' → '牌号'（优先用干净的「材质」字段）
    if out.get("材料"):
        out["材料"] = normalize_material(out["材料"], merged.get("材质", ""))
    out["_all"] = merged
    # 人工指定类别（插件「检查设置」里选过 → 优先于 AI/图面推断）
    try:
        _forced = os.environ.get("QC_FORCE_CATEGORY", "").strip()
        if _forced and not _forced.startswith("自动"):
            out["类别"] = _forced
            out["_forced_category"] = _forced
    except Exception:
        pass
    return out


def _drawing_revision_from_title_block(data, header=None):
    """识别旧模板中没有字段名前缀的版本格（如单独的 A）。

    只使用模板层(owner=2)文字，并要求候选值与图号在同一行/列且距离最近，
    避免把基准字母、设计者缩写或普通注记误当版本。
    """
    tb = [x for x in (data.get("tb_notes") or []) if isinstance(x, dict)]
    if not tb:
        return ""
    dwg = str((header or {}).get("图号") or "").strip()
    refs = []
    for t in tb:
        txt = str(t.get("text") or "").strip()
        if int(t.get("owner") if t.get("owner") is not None else -1) != 2:
            continue
        if ((dwg and _n(txt) == _n(dwg)) or
                re.fullmatch(r"[A-Z]{2,4}[0-9]{6,}[0-9A-Z.\-]*", txt, re.I)):
            refs.append(t)
    if not refs:
        return ""
    best = None
    for t in tb:
        try:
            if int(t.get("owner") if t.get("owner") is not None else -1) != 2:
                continue
            val = str(t.get("text") or "").strip().upper()
            if not re.fullmatch(r"[A-Z][0-9]?|[0-9]{1,2}", val):
                continue
            x, y = float(t.get("x") or 0), float(t.get("y") or 0)
            sheet = str(t.get("sheet") or "")
            for ref in refs:
                if sheet and str(ref.get("sheet") or "") and sheet != str(ref.get("sheet") or ""):
                    continue
                rx, ry = float(ref.get("x") or 0), float(ref.get("y") or 0)
                dx, dy = abs(x - rx), abs(y - ry)
                if not (dy <= 6.0 or dx <= 6.0):
                    continue
                distance = (dx * dx + dy * dy) ** 0.5
                if distance < 5.0 or distance > 80.0:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, val)
        except Exception:
            continue
    return best[1] if best else ""


# ---------------------------------------------------------------- 判定
def title_block_text(data):
    """图框（标题栏）区域文字：优先用注记坐标筛出右下角，没坐标就退回全部文字。"""
    notes_text = "\n".join(str(x) for x in (data.get("notes") or []))
    _tb = data.get("tb_notes") or []
    if not _tb:
        return notes_text
    _pw, _ph = _paper_size(data)
    xs = [float(t.get("x") or 0) for t in _tb]
    ys = [float(t.get("y") or 0) for t in _tb]
    # 标题栏区域：仍用注记坐标范围判定（换纸张后不会把标题栏缩没）
    W = max(xs) if xs else 0.0
    H = max(ys) if ys else 0.0
    _Wf = _pw if _pw > 0 else W      # 图框过滤用纸张尺寸
    _Hf = _ph if _ph > 0 else H
    keep = []
    for t in _tb:
        try:
            x = float(t.get("x") or 0)
            y = float(t.get("y") or 0)
            if not _inside_frame(x, y, _Wf, _Hf):
                continue
            owner = int(t.get("owner") if t.get("owner") is not None else -1)
            in_region = (W > 0 and H > 0 and x >= W * 0.45 and y <= H * 0.55)
            # 模板层(owner=2)就是标题栏单元格；其余注记按右下角区域取
            if owner == 2 or in_region:
                keep.append(str(t.get("text") or ""))
        except Exception:
            pass
    txt = "\n".join(keep)
    return txt or notes_text


# 图框（GB 留装订边）：左 25mm，其余 10mm
FRAME_MARGIN_L = 25.0
FRAME_MARGIN = 10.0


def _paper_size(data):
    """纸张尺寸(mm)：优先 sheets 元数据，其次图属 SWFormatSize。"""
    W = H = 0.0
    for sh in (data.get("sheets") or []):
        try:
            W = max(W, float(sh.get("w_mm") or 0))
            H = max(H, float(sh.get("h_mm") or 0))
        except Exception:
            pass
    if W <= 0 or H <= 0:
        txt = str((data.get("props") or {}).get("SWFormatSize") or "")
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*\*\s*(\d+(?:\.\d+)?)", txt)
        if m:
            W, H = float(m.group(1)), float(m.group(2))
    return W, H


def _inside_frame(x, y, W, H):
    """图框内判定；纸张横/竖放都接受（图属里的幅面可能与实际方向不一致）。"""
    if W <= 0 or H <= 0:
        return True

    def _in(w, h):
        return (FRAME_MARGIN_L - 1.0 <= x <= w - FRAME_MARGIN + 1.0 and
                FRAME_MARGIN - 1.0 <= y <= h - FRAME_MARGIN + 1.0)

    return _in(W, H) or _in(H, W)


def requirement_notes(data):
    """判定用技术要求：剔除「图框外」与「模板层」注记。返回 (notes, 剔除数)。

    没有坐标数据（旧报告）时退回 data["notes"]，保持兼容。
    """
    raw = [str(n).strip() for n in (data.get("notes") or []) if str(n).strip()]
    meta = data.get("notes_meta") or []
    if not meta:
        return raw, 0
    W, H = _paper_size(data)
    keep, dropped = [], 0
    seen = set()
    for t in meta:
        try:
            txt = str(t.get("text") or "").strip()
            if not txt or txt in seen:
                continue
            owner = int(t.get("owner") if t.get("owner") is not None else -1)
            x = float(t.get("x") or 0)
            y = float(t.get("y") or 0)
            # 模板层（swAnnotationOwner_DrawingTemplate=2）不参与判定
            if owner == 2:
                dropped += 1
                continue
            if not _inside_frame(x, y, W, H):
                dropped += 1
                continue
            seen.add(txt)
            keep.append(txt)
        except Exception:
            continue
    if not keep:            # 全被过滤掉 → 退回原始（避免误伤）
        return raw, 0
    return keep, dropped


def native_checks(data, header):
    out = []
    _welds = data.get("welds") or []
    _notes_all = "\n".join(str(x) for x in (data.get("notes") or []))
    _has_weld_word = any(w in _notes_all for w in ("焊", "焊缝", "焊接"))
    if _welds and not _has_weld_word:
        out.insert(0, issue("WELD-SYMBOL", "minor",
                          "图上有 %d 处焊接符号，但技术要求未写焊接要求" % len(_welds),
                          "图面标注了焊接符号，技术要求里没有对应焊缝质量/焊后处理/打磨要求。",
                          "在技术要求补充：焊缝形式与尺寸、焊后去应力/打磨/表面处理要求。",
                          "GB/T 324"))
    elif (not _welds) and _has_weld_word:
        out.insert(0, issue("WELD-SYMBOL", "minor",
                          "技术要求提到焊接，但图上未检出焊接符号",
                          "文字有焊接要求，但未读到焊接符号（可能用文字说明代替，或符号未被识别）。",
                          "确认是否需要用焊接符号明确焊缝位置/形式；若已用文字说明可忽略。",
                          "GB/T 324"))
    tb_text = title_block_text(data)
    notes, _nfr_dropped = requirement_notes(data)
    if _nfr_dropped:
        probe_note = "图框过滤：剔除 %d 条（图框外/模板层）" % _nfr_dropped
        try:
            out_probe = data.setdefault("_frame_dropped", [])
            out_probe.append(probe_note) if isinstance(out_probe, list) else None
        except Exception:
            pass
    # 2026-10-26：多套技术要求 → 只认最完整的一套（图外/模板残留不进 AI、不进规则）
    _dropped_notes = []
    try:
        notes, _dropped_notes, _note_pick_msg = _pick_authoritative_notes(notes)
    except Exception:
        _note_pick_msg = ""
    notes_text = "\n".join(notes)
    dims = data.get("dimensions") or []
    counts = data.get("counts") or {}
    roughness = data.get("roughness") or []
    gtol = int(counts.get("gtol", 0) or 0)

    has_1804 = bool(GENERAL_TOL_PAT.search(notes_text)) or bool(
        sw_qc_mt.detect_general_tolerance(notes_text))
    has_gdt_std = bool(GDT_STD_PAT.search(notes_text))

    # ---------- 1) 标题栏信息：图上的文字里必须能查到 3D 属性里的值 ----------
    def on_drawing(val):
        if not val:
            return True
        # 图框(标题栏) 或 全图注记 任一命中即可（避免"通过项说已标注、这里说未填"）
        if _n(val) in _n(tb_text):
            return True
        if _n(val) in _n(notes_text):
            return True
        try:
            for t in (data.get("tb_notes") or []):
                if _n(val) in _n(str(t.get("text") or "")):
                    return True
        except Exception:
            pass
        return False

    # 若图框里出现了另一个图号样式（如 CJC...），先记下来，后面只报不一致，不再报未填
    _rev_mismatch = None
    try:
        if header.get("图号"):
            # 只认"零件图号样式"：字母前缀 + 至少 6 位数字（如 CJC130602000.0049）
            _cands = re.findall(r"(?<![A-Za-z0-9])([A-Z]{2,4}[0-9]{6,}[0-9A-Z.\-]*)", tb_text)
            _bad_prefix = ("GB", "GBT", "JB", "ISO", "DIN", "EN", "ASTM", "ANSI", "QC")
            _others = []
            for c in _cands:
                cu = c.upper()
                if any(cu.startswith(b) for b in _bad_prefix):
                    continue
                # 排除标准号：GB1804 / GB/T 1804 之类
                if re.search(r"(GB|JB|ISO|DIN)\s*/?\s*T?\s*" + re.escape(c[:4]), notes_text):
                    continue
                if _n(c) != _n(header["图号"]):
                    _others.append(c)
            if _others:
                _rev_mismatch = _others[0]
    except Exception:
        _rev_mismatch = None

    # 用户要求：漏材料 / 名称 / 图号 / 版本 一律「严重」
    for field, sev in (("材料", "critical"), ("名称", "critical"),
                       ("图号", "critical"), ("版本", "critical")):
        val = header.get(field, "")
        if not val:
            continue
        if field == "图号" and _rev_mismatch:
            continue   # 图号不一致已在下面单独报，避免重复
        core = material_core(val) if field == "材料" else ""
        if not (on_drawing(val) or (core and on_drawing(core))):
            out.append(issue(
                "DRAWING-" + field, sev,
                f"图框{field}栏未填{field}「{val}」",
                f"3D 属性里有{field}「{val}」，但图框{field}栏里没写上 —— 该栏没填或没链接属性。",
                f"在图框{field}栏补「{val}」，或把该栏链接到模型属性。",
                "GB/T 4458"))

    # 图号"不一致"：图纸上能认出另一个图号样式 → 精确报不一致
    try:
        if header.get("图号"):
            cands = re.findall(r"(?<![A-Za-z0-9])([A-Z]{2,4}[0-9]{6,}[0-9A-Z.\-]*)", tb_text)
            _bad = ("GB", "GBT", "JB", "ISO", "DIN", "EN", "ASTM", "ANSI", "QC")
            others = [c for c in cands
                      if not any(c.upper().startswith(b) for b in _bad)
                      and _n(c) != _n(header["图号"])]
            if others:
                out.append(issue(
                    "DRAWING-图号-不一致", "critical",
                    f"图框图号栏「{others[0]}」与 3D 属性「{header['图号']}」不一致",
                    "图框图号栏填的图号与模型属性里的图样代号不同，容易发错图/装配错件。",
                    "确认哪一个是正确的，统一后更新图框与 3D 属性。",
                    "GB/T 4458"))
    except Exception:
        pass

    # 版本缺失兜底：3D 属性没有版本字段时，图纸上必须能看到版本
    if not header.get("版本"):
        has_rev_txt = bool(re.search(r"(版本|版次)\s*[:：]?\s*[A-Z0-9]", tb_text))
        if not has_rev_txt:
            out.append(issue(
                "DRAWING-REV-EMPTY", "critical", "图框版本栏未填版本",
                "3D 属性里没有版本字段，图框版本栏里也没有版本信息。",
                "在图框版本栏填写版本（A/B/C…），并在 3D 属性里补「版本」字段。",
                "GB/T 4458"))

    # ---------- 2) 材料：只查有没有漏 ----------
    mat = header.get("材料", "") or (data.get("props") or {}).get("材料", "")
    kind = material_kind(mat)
    if not mat:
        out.append(issue("GB-MAT", "critical", "图框材料栏缺材料",
                         "图框材料栏与 3D 属性里都没读到材料。",
                         "在标题栏材料栏补：如 45、Q235、5052、6061 等。", "GB/T 4458"))
    elif mat and not kind:
        # 类别关键词没命中时，再查 SW 自带材质库（用户要求：以 SW 材质库为准，别手写字典）
        known, matched = False, ""
        try:
            known, matched = sw_materials.is_known(mat, data.get("material_dbs"))
        except Exception:
            pass
        if not known:
            out.append(issue("CORE-MAT-KIND", "minor",
                             f"材料「{mat}」在 SW 材质库里没找到",
                             "不影响「有没有漏材料」的判断，仅作提示。",
                             "若常用，把该材料加入 SW 材质库，或告知作者扩充关键词。", "—"))

    # 塑料件：一般公差是否按 GB/T 47067-2026 声明
    try:
        _pc0 = _part_class(header)
        _decl = sw_qc_mt.detect_general_tolerance(notes_text)
        if _pc0 == "plastic" and not _decl and not has_1804:
            out.append(issue(
                "GB47067-GEN", "minor",
                "塑料件未声明一般公差等级（GB/T 47067-2026）",
                "塑料模塑件一般公差应在标题栏/技术要求标注「GB/T 47067-2026-TGx」；"
                "尺寸按表2、位置度按表9、面轮廓度按表10 取值。",
                "在图框一般公差栏标注等级（如 TG6）；旧 MT 等级已随 GB/T 14486-2008 废止。",
                "GB/T 47067-2026 5.3"))
    except Exception:
        pass

    # 引用了已废止的旧标准（GB/T 14486 系列）
    if re.search(r"14486", notes_text):
        out.append(issue(
            "GB47067-OBSOLETE", "major",
            "技术要求引用了已废止的 GB/T 14486（塑料模塑件公差）",
            "GB/T 14486-2008 已废止，现行标准为 GB/T 47067-2026《塑料模塑件 公差和验收条件》"
            "（2026-08-01 实施，修改采用 ISO 20457:2018），公差等级改为 TG1~TG9。",
            "把技术要求中的旧标准/MT 等级改为「GB/T 47067-2026-TGx」并在图框一般公差栏标注。",
            "GB/T 47067-2026"))

    # ---------- 2b) 图框必要条件：名称 / 图号（3D 属性与图纸都必须有）----------
    if not header.get("名称"):
        out.append(issue(
            "GB-NAME", "critical", "图框名称栏缺零件名称",
            "3D 属性里没有名称字段，图框名称栏里也没读到名称。",
            "在图框名称栏填写零件名称，并在 3D 属性补「图样名称」。",
            "GB/T 4458"))
    if not header.get("图号"):
        out.append(issue(
            "GB-DWGNO", "critical", "图框图号栏缺图号（代号）",
            "3D 属性里没有图样代号，图框图号栏里也没读到图号。",
            "在图框图号栏填写图号，并在 3D 属性补「图样代号」。",
            "GB/T 4458"))

    # 类别判定不出来：不硬猜工艺，交人工/AI
    if _part_class(header) == "unknown":
        out.append(issue(
            "CORE-CAT-UNKNOWN", "minor", "未识别零件类别（钣金/机加/注塑）",
            "图框与 3D 属性里没有「类别」，名称/材料也判断不出成型方式，"
            "因此加工能力只能给中性结论。",
            "在图框或 3D 属性补「类别」（钣金件/机加件/注塑件），"
            "工具才能按对应工艺给出准确的加工能力判断。",
            "—"))

    # ---------- 3) 材料 vs 工艺 矛盾 ----------
    if mat and kind:
        for pname, pat, kinds in PROCESS_RULES:
            if re.search(pat, notes_text, flags=re.I) and kind not in kinds:
                out.append(issue(
                    "MAT-PROCESS-CONFLICT", "major",
                    f"材料与技术要求可能矛盾：材料「{mat}」（{kind}），但要求「{pname}」",
                    f"「{pname}」通常用于 {'/'.join(kinds)}，当前材料类别是 {kind}。",
                    "核对：要么材料写错，要么工艺要求不适合该材料（如铝件写淬火、钢件写阳极氧化）。",
                    "—"))
                break

    # ---------- 3c) 表面处理：按材料是否"自带防腐"分层判断 ----------
    if mat and kind:
        has_surface = bool(SURFACE_KEYWORDS.search(notes_text))

    # 2026-10-24：表面处理不是"写了就算过" —— 要评估防腐能力并给出结论
    _surf = surface_capability(notes_text)
    if _surf["found"]:
        _names = "、".join(_surf["names"])
        if _surf["missing"]:
            out.append(issue(
                "SURFACE-WEAK", "minor",
                f"表面处理「{_names}」耐蚀性弱，但未写配套防护措施",
                f"「{'、'.join(_surf['missing'])}」单独使用时耐蚀性不足。"
                + (("查表说明：" + _surf["note"]) if _surf["note"] else ""),
                "补写配套措施（如发黑后浸油、磷化后涂装、氧化后封闭），"
                "或改用耐蚀等级更高的处理（达克罗/喷塑/电泳）。",
                "GB/T 11379"))
    elif kind in SURFACE_REQUIRED_KINDS and has_surface:
        # 写了表面处理但认不出具体类型 —— 说明里有，交给 AI/人工判断
        pass

        if kind in SURFACE_REQUIRED_KINDS and not has_surface:
            out.append(issue(
                "SURFACE-REQ", "major",
                f"材料「{mat}」属易锈/易氧化材料，但技术要求未注表面处理",
                f"{kind} 类材料（碳钢/合金钢/铸铁等）在加工、储运中会锈蚀，"
                "应按需规定表面处理。",
                "在技术要求补表面处理，如：镀锌/达克罗/发黑/喷漆/防锈油等（按使用环境选）。",
                "—"))
        elif kind in SURFACE_OPTIONAL_KINDS and not has_surface:
            # 惰性材料：不强制，记入"通过项"说明（不报错）
            pass

    # ---------- 3d) 材料用途适宜性（弹片/齿轮/轴…） ----------
    if mat:
        fn_text = str(header.get("名称", "") or "") + " " + notes_text
        for rule in FUNCTION_MATERIAL_RULES:
            pat, good, tip = rule[0], rule[1], rule[2]
            cond_re = rule[3] if len(rule) > 3 else None
            cond_good = rule[4] if len(rule) > 4 else []
            if not re.search(pat, fn_text):
                continue
            up = _norm_upper(mat)
            hit = any(_norm_upper(g) in up for g in good)
            # 条件允许：如"铝齿轮 + 硬质氧化"→ 认为合理
            if not hit and cond_re and cond_good and re.search(cond_re, notes_text):
                hit = any(_norm_upper(g) in up for g in cond_good)
                if hit:
                    break
            if not hit:
                # 铝制齿轮且没写硬质氧化：给"建议表面强化"的专门提示
                if re.search(r"齿轮", pat) and any(
                        x in up for x in ("6061", "7075", "2A12", "铝")):
                    out.append(issue(
                        "MATERIAL-USE", "major",
                        f"铝制齿轮未注明表面强化：材料「{mat}」",
                        "6061/7075 可以做齿轮（轻载、低噪音），但耐磨性差，"
                        "通常要**硬质阳极氧化**提高表面硬度/耐磨。",
                        "在技术要求补：硬质阳极氧化（膜厚按需，如 25~50μm）；"
                        "或确认载荷很小、可接受无强化。",
                        "—"))
                else:
                    out.append(issue(
                        "MATERIAL-USE", "major",
                        f"材料用途存疑：材料「{mat}」用于「{pat.split('|')[0]}」类零件",
                        tip,
                        "确认选材是否满足弹性/疲劳/耐磨要求；若仅为耐蚀且变形量小可接受，"
                        "否则建议改用推荐材料。",
                        "—"))
            break

    # ---------- 3b) 钣金件：板厚 / 轮廓尺寸 是否标了 ----------
    part_cat2 = str(header.get("类别", "") or "")
    is_sm = ("钣金" in part_cat2 or "板金" in part_cat2)
    if is_sm and dims and not data.get("part_box"):
        vals = []
        for d in dims:
            try:
                vals.append(abs(float(d.get("value_mm"))))
            except Exception:
                pass
        if vals:
            thin = [v for v in vals if v <= 3.0]
            if not thin:
                out.append(issue(
                    "OUTLINE-THICK", "major",
                    "钣金件未检出板厚尺寸",
                    f"本图尺寸最小值 {min(vals):g} mm，没有 ≤3mm 的板厚尺寸。",
                    "钣金图应标注板厚（如 t1.5、δ=2）；若板厚在技术要求里写，请补到尺寸或说明。",
                    "GB/T 4458"))
            # 【2026-10-23 修】先剔除计算/展开尺寸（如展开长 2356.1945），
            # 再用视图包围盒验证轮廓是否真的不齐 —— 避免误报。
            vals_real = [v for v in vals if not _is_calc_dim(v)] or vals
            _ov = outline_from_views(data.get("view_boxes"), data.get("sheet_names"))
            _outline_ok = False
            if _ov:
                _L = float(_ov.get("长") or 0)
                # 尺寸里能找到接近"视图最大轮廓"的值 → 轮廓是标全的
                # 容差给 8%：视图包围盒通常比零件略大（含中心线/标注外沿）
                if _L > 0 and any(abs(v - _L) <= max(2.0, _L * 0.08) for v in vals_real):
                    _outline_ok = True
            big = sorted(vals_real, reverse=True)[:2]
            if (not _outline_ok
                    and len(vals_real) >= 2 and len(big) == 2
                    and big[0] - big[1] > big[0] * 0.5):
                out.append(issue(
                    "OUTLINE-SIZE", "minor",
                    "轮廓尺寸疑似不全（只有一个大尺寸）",
                    f"最大尺寸 {big[0]:g}，第二 {big[1]:g}，差距较大，可能缺长/宽中的一个。",
                    "核对是否漏标外形长、宽（必要时加高）尺寸。",
                    "GB/T 4458"))
            elif len(vals) < 3:
                out.append(issue(
                    "OUTLINE-SIZE", "minor",
                    "尺寸数量偏少，可能缺轮廓尺寸",
                    f"整张图只读到 {len(vals)} 个尺寸，难以覆盖外形长/宽/板厚。",
                    "核对是否漏标外形尺寸与板厚。",
                    "GB/T 4458"))

    # ---------- 4) 尺寸 / 公差 ----------
    if not dims:
        out.append(issue("CORE-DIM-NONE", "critical", "未检出任何尺寸标注",
                         "图中没有读到尺寸对象，零件无法按图加工。",
                         "检查是否漏标全部尺寸。", "GB/T 4458.4"))
    else:
        missing = [d for d in dims if tol_missing(d)]
        if missing and not has_1804:
            names = "、".join(fmt_dim(d) for d in missing[:8])
            out.append(issue(
                "CORE-TOL-PART", "major",
                f"{len(missing)}/{len(dims)} 个尺寸未标注公差，且未声明一般公差标准",
                f"未标公差的尺寸：{names}" + ("…" if len(missing) > 8 else ""),
                "在技术要求补「未注尺寸公差按 GB/T 1804-m 执行」，或给配合尺寸单独标公差。",
                "GB/T 1804"))

    # ---------- 5) 粗糙度 / 几何公差 ----------
    # 钣金件一般不标粗糙度（最多标"禁止切削加工"符号），未标不算错 —— 用户明确要求。
    part_cat = str(header.get("类别", "") or "")
    mat_txt = str(header.get("材料", "") or "")
    name_txt = str(header.get("名称", "") or "")
    # 钣金件识别：类别/材料/名称 + 是否存在薄板尺寸（类别写"外购件"也可能就是钣金）
    _thin_vals = []
    for d in dims:
        try:
            v = abs(float(d.get("value_mm")))
            if 0 < v <= 3.0:
                _thin_vals.append(v)
        except Exception:
            pass
    # 2026-10-26：塑料/橡胶件绝不判钣金（之前按名字里"盖板"误判成钣金 → 误报钣金规则）
    _is_plastic = bool(re.search(r"(ABS|PC|POM|PA\d*|PP|PE|PMMA|PS|PVC|PBT|TPU|硅胶|橡胶|塑胶|塑料)",
                                 (mat_txt + " " + part_cat)))
    is_sheet_metal = (
        "钣金" in part_cat or "板金" in part_cat
        or ("板" in mat_txt and "钣材" not in mat_txt)   # 材料写"304板/铝板"这类
        or bool(re.search(r"(弹片|支架|连接片|安装板|盖板|挡板|挂钩|卡扣|折弯件)", name_txt))
        or (bool(_thin_vals) and bool(re.search(r"(板|片|弹片|支架)", name_txt)))
    )
    if _is_plastic and "钣金" not in part_cat:
        is_sheet_metal = False
    if is_sheet_metal and not roughness and int(counts.get("roughness", 0) or 0) == 0:
        out.append(issue(
            "SM-RA-SIGN", "minor",
            "钣金件建议标注「禁止表面加工」粗糙度符号",
            "钣金件一般不要求 Ra；按惯例建议在图纸右上角标注粗糙度符号"
            "（「禁止表面加工」符号，或「其余」统一要求）。",
            "在图框右上角加 GB/T 131 的粗糙度符号：禁止切削加工 或 其余 Ra3.2。",
            "GB/T 131"))
    if not roughness and int(counts.get("roughness", 0) or 0) == 0 and not is_sheet_metal:
        out.append(issue("GB-RA", "major", "未检出表面粗糙度标注",
                         "图上没有找到粗糙度符号。",
                         "标注加工面 Ra，或用技术要求「其余 Ra3.2」统一规定。", "GB/T 131"))
    if gtol == 0 and not has_gdt_std and not is_sheet_metal:
        out.append(issue("GB-GDT", "minor", "未检出几何公差，且未引用未注几何公差标准",
                         "图上没有几何公差框格，技术要求也没写未注几何公差按哪一标准。",
                         "补：未注几何公差按 GB/T 1184-K 执行，或对配合面补几何公差。",
                         "GB/T 1184"))

    # ---------- 6) 技术要求 ----------
    if not notes:
        out.append(issue("GB-NOTES", "major", "未读到技术要求",
                         "工程图里没有读到注释/技术要求文本。",
                         "补：未注公差、未注几何公差、未注倒角、去毛刺、表面处理等。",
                         "GB/T 4458"))
    elif not has_1804:
        out.append(issue("GB-TOLTXT", "major", "未引用一般公差标准",
                         "技术要求里没有找到 GB/T 1804（未注尺寸公差）的引用。",
                         "在技术要求补：未注线性尺寸公差按 GB/T 1804-m 执行。",
                         "GB/T 1804"))

    # ---------- 8) 读取告警 ----------
    warns = data.get("warnings") or []
    if warns:
        out.append(issue("CORE-SW-READ-WARN", "minor",
                         f"原生读取有 {len(warns)} 项字段未取到",
                         "以下字段 API 没读到，相关检查可能不完整：" +
                         "；".join(str(w)[:60] for w in warns[:5]),
                         "把该图纸的 .sw.json 发回作者，按实际字段修正读取逻辑。", "—"))
    return out


# ---------------------------------------------------------------- 公差等级分析
# 常规加工能力（经验口径，用于"这个公差普通加工能不能做"的提示）
# 加工能力反馈：按零件/材料类别给结论（简单明了，不说做不到的工艺）
# 类别不明时：只给中性结论，不指定具体工艺（不出现车铣磨研）
# 2026-10-24：按「上限升序」写，查表用 grade <= limit
IT_CAPABILITY_NEUTRAL = [
    (7, "需精加工才可满足"),
    (9, "常规加工可满足（数控）"),
    (18, "常规加工可满足"),
]
GTOL_CAPABILITY_NEUTRAL = [
    (0.2, "常规加工可满足"),
    (0.1, "需精加工才可满足"),
    (0.0, "需较高精度加工（待确认）"),
]

IT_CAPABILITY_BY_CLASS = {
    # 一般金属机加件
    # 金属机加：按 GB/T 1800.1 的 IT 等级 ↔ 常规加工能力（经验档）
    # 2026-10-25 用户定稿口径：
    #   IT6~IT7 = **可满足（精密加工）**（精车/精铣或磨削，不是"难以满足"）
    #   IT5 = 需磨削；IT≤4 = 研磨/超精；IT8~IT10 = 半精加工可满足；≥IT13 粗加工
    "metal": [
        (4, "研磨/珩磨/超精加工（专用工艺）"),
        (5, "需磨削（精磨可达）"),
        (7, "可满足（精密加工：精车/精铣或磨削）"),
        (10, "可满足（半精加工：常规车/铣）"),
        (12, "常规车/铣可满足"),
        (18, "粗加工即可（车/铣/钻）"),
    ],
    # 钣金件：用户 2026-10-25 —— IT≤7 难以满足；IT8 需警惕；IT9 及更粗 常规可满足
    "sheet": [
        (7, "常规钣金难以满足（需精冲或二次机加工）"),
        (8, "需警惕：精密冲压/CNC 折弯"),
        (18, "常规可满足（激光/冲压/折弯）"),
    ],
    # 塑料件：注塑/CNC
    "plastic": [
        (9, "需 CNC 加工"),
        (11, "注塑 + 关键位 CNC 修整"),
        (13, "注塑可满足（常规模具）"),
        (18, "注塑可满足（宽松）"),
    ],
}

GTOL_CAPABILITY_BY_CLASS = {
    "metal": [
        (0.2, "常规加工可满足"),
        (0.1, "精铣/精车可满足"),
        (0.05, "需磨削或精镗"),
        (0.02, "需磨削 + 高精度检测"),
        (0.0, "高要求，需专用工艺/三坐标"),
    ],
    "sheet": [
        (0.2, "钣金可满足"),
        (0.1, "需 CNC/精加工"),
        (0.0, "难保证"),
    ],
    "plastic": [
        (0.2, "注塑可满足"),
        (0.1, "需 CNC 加工"),
        (0.0, "难以保证"),
    ],
}


def _part_class(header):
    """零件/材料类别：sheet(钣金) / plastic(塑料) / metal(其余)

    类别字段经常没填，所以再看零件名称/材料：含板/片/支架/弹片/盖板…或材料写板→钣金。
    """
    header = header or {}
    cat = str(header.get("类别", "") or "")
    mat = str(header.get("材料", "") or "")
    name = str(header.get("名称", "") or "")
    if "钣金" in cat or "板金" in cat:
        return "sheet"
    if "注塑" in cat:
        return "plastic"
    if "机加" in cat:
        kind0 = material_kind(mat)
        return "plastic" if (kind0 == "工程塑料" or "塑料" in mat) else "metal"
    # 类别缺失 → 用名称/材料推断钣金
    if re.search(r"(板|片|弹片|支架|连接片|盖板|挡板|折弯|挂钩|卡扣)\$", name) or        re.search(r"(板|片|支架|弹片|连接片|盖板|挡板|折弯)", name):
        if material_kind(mat) not in ("工程塑料",):
            return "sheet"
    if "板" in mat and material_kind(mat) != "工程塑料":
        return "sheet"
    kind = material_kind(mat)
    if kind == "工程塑料" or "塑料" in mat or "ABS" in _norm_upper(mat) or "POM" in _norm_upper(mat):
        return "plastic"
    # 材料类别认得出来 → 按金属机加（钢/不锈钢/铝/铜/铸铁等）
    if kind in ("钢", "不锈钢", "铝合金", "铜合金", "铸铁", "铸钢", "钛合金",
                "镁合金", "锌合金", "弹簧钢", "轴承钢", "工具钢", "粉末冶金", "橡胶"):
        return "metal"
    # 三级都判不出来：不猜，交给人工/AI
    return "unknown"


def _capability_by_grade(grade_num, part_class="metal"):
    if part_class == "unknown":
        table = IT_CAPABILITY_NEUTRAL
    else:
        table = IT_CAPABILITY_BY_CLASS.get(part_class) or IT_CAPABILITY_BY_CLASS["metal"]
    # 2026-10-24：表是「上限升序」，取第一个 grade <= limit 的档
    for limit, text in table:
        if grade_num <= limit:
            return text
    return table[-1][1] if table else "常规难满足"


def _parse_tol_width_um(dim):
    """把一条尺寸的公差换算成公差带宽度（µm）。返回 (width_um, note)。"""
    t = str(dim.get("tol_type", "") or "").strip()
    raw = dim.get("tol_value", "")
    vals = []
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = re.split(r"[/,;\s]+", str(raw or ""))
    for it in items:
        try:
            vals.append(float(str(it).strip()))
        except Exception:
            pass
    if not vals:
        return None, ""

    def _to_um(diff):
        """SW 返回的公差是**米**；若数值明显像毫米（>3mm 公差不合理）则按毫米处理。"""
        um = diff * 1e6
        if 0.02 <= um <= 3000:
            return um
        um2 = diff * 1000.0
        if 0.02 <= um2 <= 3000:
            return um2
        return um

    # 对称公差 ±v：SW 常返回 [v, 0] 或 [v, -v]，宽度都按 2×|v|
    if t == "4" or (len(vals) >= 2 and abs(vals[0] + vals[1]) < 1e-12
                    and abs(vals[0]) > 0):
        v = max(abs(x) for x in vals)
        return _to_um(2 * v), "对称公差"
    if len(vals) >= 2:
        # 2026-10-25 用户：备注里不要"上/下偏差"（上下偏差已有专门列，重复且无用）
        return _to_um(abs(vals[0] - vals[1])), ""
    v = abs(vals[0])
    if t in ("2", "3"):   # 双向/极限
        return _to_um(v), "单侧公差"
    return _to_um(v), ""


# 包容要求（ISO 8015 / GB/T 4249）：ⓔ / (E) / 包容
# 2026-10-24 收紧：原来的 E\b 会误匹配任何以 E 结尾的英文词
import re as _re_env
ENVELOPE_PAT = _re_env.compile(r"Ⓔ|ⓔ|\(E\)|包容要求|包容原则|envelope", _re_env.I)

# 修正符号识别（用户 2026-10-24：图上有 Ⓔ 要自动填进「修正」分段框）
_MOD_PATTERNS = (
    (_re_env.compile(r"Ⓔ|ⓔ|\(E\)|包容要求|包容原则"), "Ⓔ"),
    (_re_env.compile(r"Ⓜ|ⓜ|\(M\)"), "Ⓜ"),
    (_re_env.compile(r"Ⓛ|ⓛ|\(L\)"), "Ⓛ"),
    (_re_env.compile(r"Ⓕ|ⓕ|\(F\)"), "Ⓕ"),
    (_re_env.compile(r"Ⓣ|ⓣ|\(T\)"), "Ⓣ"),
    (_re_env.compile(r"Ⓟ|ⓟ|\(P\)"), "Ⓟ"),
)


# 深度：SW2016 用 <HOLE-DEPTH>7 表示（如 ⌀5.1 +0.1/0 ▽7）
_DEPTH_PAT = re.compile(
    r"<HOLE-DEPTH>\s*([0-9]+(?:\.[0-9]+)?)"
    r"|<DIM-DEPTH>\s*([0-9]+(?:\.[0-9]+)?)"
    r"|<DEPTH>\s*([0-9]+(?:\.[0-9]+)?)"
    r"|▽\s*([0-9]+(?:\.[0-9]+)?)"
    r"|深度\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)")


def depth_of_dim(dim):
    """从尺寸对象里读深度(mm)。没有深度返回 None。"""
    if not isinstance(dim, dict):
        return None
    txt = " ".join(str(dim.get(k) or "") for k in ("text_all", "text", "tol_value"))
    m = _DEPTH_PAT.search(txt)
    if not m:
        return None
    for g in m.groups():
        if g:
            try:
                return float(g)
            except Exception:
                return None
    return None


def modifier_of_dim(dim):
    """从尺寸对象里找修正符号（Ⓔ/Ⓜ/Ⓛ…）。找不到返回空串。"""
    if not isinstance(dim, dict):
        return ""
    txt = " ".join(str(dim.get(k) or "") for k in
                   ("text_all", "text", "tol_value", "tol_type"))
    # 2026-10-25：text_all 里是 <MOD-EP> 这类 token → 先映射成 Ⓔ 再匹配
    tok = _mod_from_tokens(txt)
    if tok:
        return tok
    for pat, sym in _MOD_PATTERNS:
        if pat.search(txt):
            return sym
    return ""


def envelope_note(dim):
    """
    带包容要求的尺寸：只回「修正符号」本身。
    用户 2026-10-24：备注里不要写 MMS/LMS 的"孔…/轴…"猜测 —— 图纸不会告诉我们
    这是轴还是孔，这种信息既无关又可能误导。
    """
    return "包容要求 Ⓔ"


def fmt_tol_display(dim):
    """把 SW 的公差（米）显示成毫米可读串，如 0/+0.010、±0.005。"""
    try:
        if int(dim.get("tol_type") or 0) == 1:
            return "基本尺寸"      # 方框尺寸（swTolBASIC），不带 ±
    except Exception:
        pass
    raw = dim.get("tol_value", "")
    t = str(dim.get("tol_type", "") or "")
    nums = []
    for it in str(raw or "").replace("，", ",").split(","):
        it = it.strip()
        if not it:
            continue
        try:
            nums.append(float(it))
        except Exception:
            pass
    if not nums:
        return "无" if dim.get("tol_value") in (None, "") else str(raw)
    for i, v in enumerate(nums):
        if abs(v) < 1e-9:
            nums[i] = 0.0
    mm = [v * 1000.0 for v in nums]
    if t == "4" or (len(mm) >= 2 and abs(mm[0] + mm[1]) < 1e-9):
        v = max(abs(x) for x in mm)
        return "±%g" % v
    if len(mm) >= 2:
        # 【2026-10-23 修】用户明确要求：显示 **上偏差 / 下偏差**（正公差在前、负公差在后）。
        # 原写法按 SW 存储顺序输出，实测 444 的 -0.1/0 被显示成 "-0.1 / +0"，顺序反了。
        up, low = max(mm), min(mm)
        return "%+g / %+g" % (up, low)
    v = mm[0]
    if t == "4":
        return "±%g" % abs(v)
    return "%+g" % v


def _thread_label(hole):
    """从 HoleWzd 特征生成螺纹代号，兼容公制与 ANSI Inch。"""
    if not isinstance(hole, dict):
        return ""
    fs = str(hole.get("fastener_size") or "").strip()
    if fs and re.match(r"^M\d", fs, re.I):
        return fs.upper()
    # SolidWorks 的 ANSI Inch HoleWzd 常只返回“1/4-28”，不带 UNF。
    # 优先用模型给出的系列；没有系列时，只在规则表中唯一命中时补全。
    if fs:
        compact = re.sub(r"\s+", "", fs).upper()
        if re.fullmatch(r"(?:#\d+|\d+/\d+|\d+)-\d+(?:UNC|UNF|UNEF)", compact):
            return compact
        if re.fullmatch(r"(?:#\d+|\d+/\d+|\d+)-\d+", compact):
            std_txt = " ".join(str(hole.get(k) or "") for k in
                               ("standard", "thread_series", "fastener_type")).upper()
            for series in ("UNEF", "UNF", "UNC"):
                if series in std_txt and thread_spec(compact + series):
                    return compact + series
            hits = [compact + series for series in ("UNC", "UNF", "UNEF")
                    if thread_spec(compact + series)]
            if len(hits) == 1:
                return hits[0]
    try:
        td = float(hole.get("thread_dia") or 0)
    except Exception:
        td = 0.0
    if td > 0:
        return "M%g" % td
    ft = str(hole.get("fastener_type") or "").strip()
    if ft and re.search(r"M\d", ft, re.I):
        m = re.search(r"M\d+(?:\.\d+)?", ft, re.I)
        return m.group(0).upper()
    return ""


def _apply_part_holes(dim_rows, part_holes):
    """零件孔特征只作备注，不覆盖尺寸内容（2026-10-27 确认口径）。

    仅当「螺纹孔且螺纹直径与尺寸孔径接近(±0.75)」才允许换成 M 代号；
    螺钉间隙/沉孔/销孔/直径数据为 0 → 只在备注提示“零件模型：…”。
    """
    hits = []
    if not part_holes:
        return hits
    for hole in part_holes:
        if not isinstance(hole, dict):
            continue
        lab = _thread_label(hole)
        ftype = str(hole.get("fastener_type") or "")
        try:
            hd = float(hole.get("thread_dia") or hole.get("hole_dia") or 0)
        except Exception:
            hd = 0.0
        try:
            hdep = float(hole.get("thread_depth") or 0)
        except Exception:
            hdep = 0.0
        is_thread = ("螺纹" in ftype) and ("间隙" not in ftype) and hd > 0
        # Model-side non-thread HoleWzd metadata is not drawing evidence.
        # With no reliable diameter the old fallback attached it to the first
        # dimension arbitrarily (for example M3/ISO7047 -> diameter 5).
        if not is_thread:
            continue
        best, best_score = None, 1e9
        for r in (dim_rows or []):
            if not isinstance(r, dict):
                continue
            try:
                val = float(r.get("nominal_mm") or 0)
            except Exception:
                val = 0.0
            if val <= 0:
                continue
            dscore = abs(hd - val) if hd > 0 else 9.0
            if hd > 0 and dscore > 0.75:
                continue
            score = dscore + (0.0 if "均布" in str(r.get("note") or "") else 0.3)
            if score < best_score:
                best, best_score = r, score
        if best is None:
            continue
        note = str(best.get("note") or "")
        tag = "零件模型：" + (lab or "孔特征") + ("/" + ftype + "孔" if ftype and not ftype.endswith("孔") else ("/" + ftype if ftype else "")) + "（仅提示）"
        if is_thread and best_score <= 0.75:
            mpre = re.match(r"^(\d+)-", str(best.get("content") or ""))
            prefix = mpre.group(1) + "-" if mpre else ""
            best["content"] = prefix + lab
            extra = "螺纹 " + lab
            if hdep > 0:
                extra += "，螺纹深%g" % hdep
            note = ((note + "；") if note else "") + extra
            hits.append({"尺寸": best["content"], "螺纹": lab, "视图": best.get("view") or ""})
        best["note"] = note.strip("；")
    return hits


def _refill_content_from_dims(dim_rows, dims):
    """行内容统一取 dim_display，并把其备注（均布/深度/槽宽等）合并进来。"""
    if not dim_rows or not dims:
        return dim_rows
    by_val = {}
    by_id = {}
    for d in dims:
        if not isinstance(d, dict):
            continue
        try:
            v = round(abs(float(d.get("value_mm") or 0)), 3)
        except Exception:
            continue
        by_val.setdefault(v, d)
        if d.get("_dim_id"):
            by_id[str(d.get("_dim_id"))] = d
    for r in dim_rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("kind") or "").startswith(("轮廓", "板厚", "粗糙度")):
            continue
        try:
            val = round(abs(float(r.get("nominal_mm") or 0)), 3)
        except Exception:
            val = 0.0
        if val <= 0:
            nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(r.get("content") or ""))
            if nums:
                try:
                    val = round(abs(float(nums[-1])), 3)
                except Exception:
                    val = 0.0
        d = by_id.get(str(r.get("_dim_id") or ""))
        if not d:
            d = by_val.get(val)
        if not d:
            continue
        # 理论正确尺寸（SW swTolBASIC=1）：不带 ± 公差是正常，不能显示成 ±0 / 无法解析
        try:
            if int(d.get("tol_type") or 0) == 1:
                r["tol"] = "基本尺寸"
                r["it"] = "—"
                r["capability"] = "基本尺寸，由几何公差控制"
                _nt = str(r.get("note") or "")
                for _bad in ("公差值无法解析", "±0", "对称公差；", "对称公差"):
                    _nt = _nt.replace(_bad, "")
                r["note"] = re.sub(r"；\s*；+", "；", _nt).strip("；")
        except Exception:
            pass
        # 同一尺寸上重复的同一几何公差 → 去重
        try:
            _gs = r.get("gtols")
            if isinstance(_gs, list) and len(_gs) > 1:
                _seen, _out = set(), []
                for _g in _gs:
                    if not isinstance(_g, dict):
                        continue
                    _k = (str(_g.get("sym") or ""), str(_g.get("val") or ""),
                          str(_g.get("dat1") or ""), str(_g.get("dat2") or ""),
                          str(_g.get("dat3") or ""), str(_g.get("mod") or ""))
                    if _k in _seen:
                        continue
                    _seen.add(_k)
                    _out.append(_g)
                r["gtols"] = _out
                if _out:
                    _lbl = str(_out[0].get("label") or "")
                    _nt2 = re.sub(r"几何\s*[0-9]+\s*条（[^）]*）[；;]?", "", str(r.get("note") or "")).strip("；")
                    if _lbl and "几何公差" not in _nt2:
                        _nt2 = ((_nt2 + "；") if _nt2 else "") + "几何公差：" + _lbl
                    r["note"] = _nt2
        except Exception:
            pass
        try:
            c, n = dim_display(d)
            if c:
                r["content"] = c
            if n:
                old = str(r.get("note") or "")
                if n not in old:
                    r["note"] = ((old + "；") if old else "") + n
        except Exception:
            pass
    return dim_rows



def _append_missing_dim_rows(dim_rows, dims):
    """把分析行里缺失、但能识别出内容的 dim 补成「特征尺寸」行（如槽 3×5）。"""
    if not dims:
        return dim_rows
    seen = set()
    for r in dim_rows or []:
        try:
            seen.add(round(abs(float(r.get("nominal_mm") or 0)), 3))
        except Exception:
            pass
        nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(r.get("content") or ""))
        if nums:
            try:
                seen.add(round(abs(float(nums[-1])), 3))
            except Exception:
                pass
    for d in dims:
        if not isinstance(d, dict):
            continue
        try:
            v = round(abs(float(d.get("value_mm") or 0)), 3)
        except Exception:
            continue
        if v <= 0 or v in seen:
            continue
        try:
            c, n = dim_display(d)
        except Exception:
            continue
        if not c:
            continue
        _mh = re.match(r"^\s*\d+\s*[-×xX]\s*[⌀φØΦ]\s*([0-9]+(?:\.[0-9]+)?)", c)
        if _mh:
            v = round(abs(float(_mh.group(1))), 3)
        # 普通线性尺寸即使没有直径/深度等附加说明，也必须进入唯一尺寸表。
        # 例如图上的“19”无独立公差、无几何公差时，应显示“19 / 无”，
        # 不能因为内容只是纯数字而被过滤掉。
        row = {"kind": "特征尺寸", "content": c, "nominal_mm": v,
               "tol": "无", "it": "未注", "capability": "按 GB/T 1804-m 一般公差",
               "note": n or "", "view": str(d.get("view") or ""), "modifier": "",
               "_dim_id": d.get("_dim_id") or ""}
        dim_rows.append(row)
        seen.add(v)
    return dim_rows



def _clean_dim_rows(rows):
    """统一精简尺寸行备注：去 3D 包围盒来源、去“已标注公差（取自视图…）”、合并重复深度、补 Ⓔ。"""
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        try:
            note = str(r.get("note") or "")
            note = re.sub(r"来源：3D模型包围盒（[XYZ]向）[·•]?\s*", "", note)
            note = re.sub(r"（取自视图「[^」]*」）", "", note)
            note = note.replace("已标注公差", "")
            note = re.sub(r"深度\s*([0-9.]+)\s*；\s*深度\s*▽\s*\1", r"深度\1", note)
            if "深度" in note:
                note = re.sub(r"；?\s*深度\s*▽\s*[0-9.]+", "", note)
            note = re.sub(r"；\s*；+", "；", note)
            _seen_n = []
            for _seg in [x.strip() for x in re.split(r"；", note) if x.strip()]:
                if _seg not in _seen_n:
                    _seen_n.append(_seg)
            note = "；".join(_seen_n)
            note = re.sub(r"^[；;、\s]+|[；;、\s]+$", "", note)
            r["note"] = note
            content = str(r.get("content") or "")
            if "包容要求" in (str(r.get("note") or "") + str(r.get("modifier") or "")) and "Ⓔ" not in content:
                r["content"] = content + " Ⓔ"
        except Exception:
            continue
    return rows


def analyze_dims(dims, notes_text, header=None, view_boxes=None, sheet_names=None):
    """逐个尺寸分析：公差带 → IT 等级 → 常规加工能否满足。"""
    rows = []
    has_1804 = bool(GENERAL_TOL_PAT.search(notes_text)) or bool(
        sw_qc_mt.detect_general_tolerance(notes_text))
    for d in dims:
        val = d.get("value_mm")
        try:
            nominal = abs(float(val))
        except Exception:
            nominal = 0.0
        width_um, note = _parse_tol_width_um(d)
        _mod = modifier_of_dim(d)
        _gtol = d.get("gtol") if isinstance(d, dict) else None
        row = {
            "kind": "尺寸",
            "content": fmt_dim(d) or (f"{nominal:g}" if nominal else ""),
            "nominal_mm": round(nominal, 4),
            "tol": fmt_tol_display(d),
            "it": "", "capability": "", "note": note,
            "view": (str(d.get("sheet") or "") + " / " + str(d.get("view") or "")).strip(" /")
                    if d.get("sheet") else d.get("view", ""),
            "sheet": d.get("sheet") or "",
            "modifier": _mod,
            "_dim_id": d.get("_dim_id") or "",
        }
        # 统一内容识别：螺纹/直径/计数/配合/深度/Ⓔ
        try:
            _disp, _dnote = dim_display(d)
            if _disp:
                row["content"] = _disp
                # 孔标注的 SW value_mm 常给沉头/沉孔直径；当显示主特征为 N-⌀d 时，
                # nominal_mm 必须跟随主孔径 d，避免后续公差/标准匹配仍误用沉头直径。
                _mh = re.match(r"^\s*\d+\s*[-×xX]\s*[⌀φØΦ]\s*([0-9]+(?:\.[0-9]+)?)", _disp)
                if _mh:
                    row["nominal_mm"] = round(float(_mh.group(1)), 4)
            if _dnote:
                row["note"] = "；".join(x for x in (_dnote, row.get("note")) if x)
        except Exception:
            pass
        # 2026-10-25：直径符号（text_all 里的 <MOD-DIAM>）显示到尺寸特征列，如 ⌀5.1
        try:
            _ta = str(d.get("text_all") or "")
            _cur = str(row.get("content") or "")
            _pre = ""
            _mpre = re.match(r"^\|*(\d+)\s*-", _ta)
            if _mpre and not _cur.startswith(_mpre.group(1) + "-"):
                _pre = _mpre.group(1) + "-"
            _cur2 = str(row.get("content") or "")
            if not re.match(r"^(\d+-)?[⌀φØ]", _cur2):
                if "<MOD-DIAM>" in _ta:
                    row["content"] = _pre + "⌀" + _cur2
                elif _pre:
                    row["content"] = _pre + _cur2
            if "<MOD-EP>" in _ta and "Ⓔ" not in str(row.get("content") or ""):
                row["content"] = str(row.get("content") or "") + " Ⓔ"
            _extra = []
            _md = re.search(r"<HOLE-DEPTH>\s*([0-9.]+)", _ta)
            if _md:
                _extra.append("深度" + _md.group(1))
            if "均布" in _ta:
                _extra.append("均布")
            if _extra:
                row["note"] = "；".join(_extra + ([str(row.get("note"))] if row.get("note") else []))
        except Exception:
            pass
        # 2026-10-25 用户要求：读取深度信息（⌀5.1 +0.1/0 ▽7 这种）
        try:
            _dep = depth_of_dim(d)
            if _dep:
                row["depth_mm"] = _dep
                row["note"] = ((row.get("note") or "") + "；深度 ▽" + ("%g" % _dep)).strip("；")
        except Exception:
            pass
        # 包容要求（ⓔ）：备注里算 MMS/LMS
        try:
            _env_txt = (str(d.get("text_all") or "") + " " +
                        str(d.get("text") or "") + " " +
                        str(d.get("tol_value") or "") + " " +
                        str(d.get("tol_type") or ""))
            if _mod or (ENVELOPE_PAT and ENVELOPE_PAT.search(_env_txt)):
                _sym = _mod or "Ⓔ"
                _nm = {"Ⓔ": "包容要求", "Ⓜ": "最大实体要求", "Ⓛ": "最小实体要求",
                       "Ⓕ": "自由状态", "Ⓣ": "相切平面", "Ⓟ": "延伸公差带"}.get(_sym, "")
                _en = "修正符号 " + _sym + ("（" + _nm + "）" if _nm else "")
                row["modifier"] = _sym
                row["note"] = ((row.get("note") or "") + "；" + _en).strip("；")
                # 能力列只谈加工能力，不掺公差原则（用户要求）
        except Exception:
            pass
        # 2026-10-25：一个尺寸可挂多条几何公差（多行框格）
        #   · 系统按 GTOL_RADIAL_RULE 自动判"是否计入作用边界"（不让用户勾选）
        #   · 尺寸带 Ⓔ 时形状类不叠加
        #   · 备注只写一条紧凑汇总，避免多条拼接错乱
        try:
            _gs, _sigma, _gnote = build_gtol_summary(d, row.get("modifier") == "Ⓔ")
            if _gs:
                row["gtols"] = _gs
                row["sigma_t"] = _sigma
                row["gtol"] = {k: _gs[0].get(k) for k in ("sym", "val", "mod", "dat1", "dat2", "dat3")}
                # 直径尺寸的"最大实体"：轴 = 上偏差，孔 = 下偏差
                try:
                    _vals = _tol_mm_vals(d)
                    if _vals:
                        _up, _low = max(_vals), min(_vals)
                        _mms_s = nominal + _up
                        _mms_h = nominal + _low
                        _boundary_mod = str(row.get("modifier") or "")
                        if not _boundary_mod:
                            for _gg in _gs:
                                if _gg.get("include") and str(_gg.get("mod") or "") in ("Ⓜ", "Ⓛ"):
                                    _boundary_mod = str(_gg.get("mod") or "")
                                    break
                        # Tool rule confirmed by user: compound tolerances are
                        # first reduced by containment/tightest rules, then the
                        # effective sigma tightens the shaft/hole boundary.
                        _boundary_sigma = 0.0 if _boundary_mod == "Ⓔ" else _sigma
                        row["mms_shaft"] = round(_mms_s, 4)
                        row["mms_hole"] = round(_mms_h, 4)
                        row["mmvc_shaft"] = round(_mms_s + _boundary_sigma, 4)
                        row["mmvc_hole"] = round(_mms_h - _boundary_sigma, 4)
                        row["boundary_sigma_t"] = round(_boundary_sigma, 4)
                except Exception:
                    pass
                row["note"] = ((row.get("note") or "") + "；" + _gnote).strip("；")
        except Exception:
            if _gtol:
                row["gtol"] = _gtol
                row["note"] = ((row.get("note") or "") + "；几何公差：" +
                               str(_gtol.get("label") or "")).strip("；")
        if width_um and nominal > 0:
            grade = None
            try:
                grade = sw_qc_gb.it_grade_of(nominal, width_um)
            except Exception:
                grade = None
            if not grade and nominal > 0:
                # 不在标准值上 → 就近取最近的 IT 等级（标 ≈，不硬判错）
                best = None
                for gn in range(5, 15):
                    try:
                        tv = sw_qc_gb.it_tolerance(nominal, "IT%d" % gn)
                    except Exception:
                        tv = None
                    if not tv:
                        continue
                    diff = abs(tv - width_um) / float(tv)
                    if best is None or diff < best[0]:
                        best = (diff, gn)
                if best:
                    grade = "≈IT%d" % best[1]
            _pc = _part_class(header)
            if grade:
                row["it"] = grade
                try:
                    gnum = int(re.sub(r"[^0-9]", "", str(grade)) or "9")
                except Exception:
                    gnum = 9
            else:
                row["it"] = "?"
                gnum = None

            if _pc == "plastic":
                # 用户 2026-10-25：注塑件不判加工能力（注塑机都能满足），
                # 只反馈"这个尺寸需要哪个等级"。用 GB/T 47067 的 TG 分级：
                #   width_um 是公差带宽度 → 单边公差 mm = width_um/2000
                _tag, _tg = "", None
                try:
                    _tag, _tg = sw_qc_mt.tg_grade_of(
                        nominal, (width_um / 2000.0) if width_um else 0.0)
                except Exception:
                    _tag, _tg = "", None
                if _tg:
                    _it = None
                    try:
                        _it = sw_qc_mt.tg_to_it(nominal, _tg)
                    except Exception:
                        pass
                    row["capability"] = ("注塑件不判加工能力；该尺寸需 %s%s · 精密注塑可满足"
                                         % (_tag or ("TG%d" % _tg),
                                            ("（≈IT%d）" % _it) if _it else ""))
                else:
                    row["capability"] = ("注塑件不判加工能力；该尺寸 %s · 精密注塑可满足"
                                         % (row["it"] if row["it"] != "?" else "公差为自行给定"))
            elif gnum is not None:
                row["capability"] = _capability_by_grade(gnum, _pc)
            else:
                row["capability"] = "公差值不属于标准 IT 等级（自行给定）"
        else:
            if tol_missing(d):
                row["it"] = "未注"
                row["capability"] = ("按 GB/T 1804-m 一般公差，常规加工可满足"
                                     if has_1804 else "未注且未声明 GB/T 1804")
            else:
                row["it"] = "?"
                row["capability"] = "公差值无法解析"

        # 塑料件：能力判断统一按 MT 等级建议 + 公差阈值（覆盖 IT 分档结论）
        if _part_class(header) == "plastic" and not tol_missing(d):
            _mode = sw_qc_mt.process_mode(
                str(header.get("材料", "") or ""),
                str(header.get("类别", "") or ""), notes_text)
            if _mode == "injection":
                # 用户 2026-10-25 定稿：注塑件**不判加工能力**（注塑机都能满足），
                # 只反馈"这个尺寸需要哪个等级"；精密注塑可满足行业要求。
                _tag, _g = sw_qc_mt.tg_grade_of(nominal, (width_um or 0) / 2000.0)
                if _tag:
                    row["it"] = _tag
                    _it = None
                    try:
                        _it = sw_qc_mt.tg_to_it(nominal, _g)
                    except Exception:
                        pass
                    row["capability"] = (
                        "注塑件不判加工能力；该尺寸需 %s%s（GB/T 47067-2026）· 精密注塑可满足"
                        % (_tag, ("（≈IT%d）" % _it) if _it else ""))
                else:
                    row["capability"] = ("注塑件不判加工能力；该尺寸公差超出 "
                                         "GB/T 47067-2026 常用分级 · 请与模具厂确认")
            else:
                row["capability"] = sw_qc_mt.plastic_capability(
                    width_um, str(header.get("材料", "") or ""), nominal,
                    str(header.get("类别", "") or ""), notes_text)
            # 机加工塑料件：若图纸声明 TG 等级，附上表2 该尺寸段的允许偏差
            try:
                _gt2 = sw_qc_mt.detect_general_tolerance(notes_text)
                if _gt2 and _mode != "injection":
                    _tv = sw_qc_mt.tg_tolerance(nominal, _gt2["grade"])
                    if _tv:
                        row["capability"] += (f"；按 TG{_gt2['grade']} 该尺寸允许 ±{_tv:g}mm")
            except Exception:
                pass

        # 特征尺寸：带公差的进这个页签；**带几何公差的也一定要进**（否则几何公差会凭空消失）
        if not tol_missing(d) or (isinstance(d.get("gtols"), list) and d["gtols"]):
            row["kind"] = "特征尺寸"
            rows.append(row)

    # ------------------------------------------------------------------
    # ---------- 轮廓：按视图分组识别（不用包围盒：会含图框/标注/展开图）----------
    def _row(kind, content, tol_note, it, cap, note):
        return {"kind": kind, "content": content, "nominal_mm": 0,
                "tol": tol_note, "it": it, "capability": cap, "note": note, "view": ""}

    def _toleranced(v):
        for r in rows:
            try:
                if abs(float(r.get("nominal_mm") or 0) - v) < 1e-3:
                    return r
            except Exception:
                pass
        return None

    def _outline_row(kind, v):
        base = _toleranced(v)
        if base is not None:
            return _row(kind, f"{v:g}", base["tol"], base["it"],
                        base["capability"], "已标注公差")
        if has_1804:
            return _row(kind, f"{v:g}", "无", "未注",
                        "按 GB/T 1804-m 一般公差，常规加工可满足", "已标注，未注公差")
        return _row(kind, f"{v:g}", "无", "未注",
                    "未注公差且未声明 GB/T 1804", "已标注，缺一般公差声明")

    def _looks_calc(v):
        txt = f"{v:.6f}".rstrip("0").rstrip(".")
        return ("." in txt) and len(txt.split(".")[1]) >= 3 and abs(v - round(v, 1)) > 1e-6

    # 按视图分组；排除图纸格式视图（名字与图纸名相同）
    sheet_names = set(str(x) for x in (sheet_names or []))
    part_cat = str(header.get("类别", "") or "")
    is_sm = ("钣金" in part_cat or "板金" in part_cat)
    raw_by_view = {}
    for d in dims:
        try:
            v = abs(float(d.get("value_mm")))
        except Exception:
            continue
        if v <= 0:
            continue
        vn = str(d.get("view") or "")
        if vn in sheet_names:
            continue
        raw_by_view.setdefault(vn, []).append(v)

    all_raw = sorted({v for vs in raw_by_view.values() for v in vs}, reverse=True)
    # 离群值：降序找"断崖"（a[i] > 3*a[i+1]），断崖以上视为展开/计算尺寸
    limit = float("inf")
    for i in range(len(all_raw) - 1):
        if all_raw[i] > all_raw[i + 1] * 3:
            limit = all_raw[i + 1]
            break

    def _keep(v):
        return v <= limit

    by_view = {vn: [v for v in vs if _keep(v)] for vn, vs in raw_by_view.items()}
    by_view = {vn: vs for vn, vs in by_view.items() if vs}

    all_vals = sorted({round(v, 1) for vs in by_view.values() for v in vs}, reverse=True)
    thin = [v for v in all_vals if v <= 3.0]
    outline_rows = []
    if thin and is_sm:
        outline_rows.append(_outline_row("板厚", min(thin)))
    elif is_sm:
        # 钣金件必须有板厚；机加件/塑料件不需要「板厚」这一项
        outline_rows.append(_row("板厚", "未检出", "—", "缺失",
                                 "需补标板厚（如 t1.5 / δ=2）", "未找到 ≤3mm 的尺寸"))

    # 主视图 = 含最大有效尺寸的视图（长宽通常在主视图）
    main_view, main_vals = "", []
    for vn, vs in by_view.items():
        if not vs:
            continue
        if not main_vals or max(vs) > max(main_vals):
            main_view, main_vals = vn, vs

    if len(set(round(v, 1) for v in main_vals)) >= 2:
        picks = sorted({round(v, 1) for v in main_vals}, reverse=True)[:2]
        for lab, v in zip(("长", "宽"), picks):
            r = _outline_row("轮廓-" + lab, v)
            r["note"] = (r.get("note", "") or "") + f"（取自视图「{main_view}」）"
            outline_rows.append(r)
        # 钣金件是平板件：轮廓 = 长 × 宽 + 板厚，不再报"高/候选"（避免噪音）
        # 机加件才需要高向尺寸
        if not is_sm:
            short_side = picks[1] if len(picks) > 1 else picks[0]
            thick = min(thin) if thin else 0
            cand = [v for vn, vs in by_view.items() if vn != main_view
                    for v in vs if thick * 2 < v < short_side]
            if cand:
                h = max(cand)
                r = _outline_row("轮廓-高", h)
                r["note"] = (r.get("note", "") or "") + "（按尺寸推断，请确认）"
                outline_rows.append(r)
            else:
                outline_rows.append(_row("轮廓-高", "未可靠识别", "—", "待确认",
                                         "请人工确认高度方向尺寸",
                                         "无法自动区分高向尺寸，需人工核对"))
    else:
        for lab in ("长", "宽", "高"):
            outline_rows.append(_row("轮廓-" + lab, "未可靠识别", "—", "待确认",
                                     "请人工确认外形" + lab, "尺寸太少"))

    # 去重：轮廓/板厚里已出现的值，不再重复显示为「特征尺寸」
    used = set()
    for r in outline_rows:
        try:
            used.add(round(float(r.get("content")), 3))
        except Exception:
            pass
    # 去重前：把带「包容要求」等特殊备注的特征尺寸信息并到轮廓行
    for r in rows:
        if r.get("kind") != "特征尺寸":
            continue
        if not r.get("note") or ("包容" not in r["note"]):
            continue
        try:
            v = round(float(r.get("nominal_mm") or 0), 3)
        except Exception:
            continue
        for o in outline_rows:
            try:
                if abs(float(o.get("content")) - v) < 1e-6:
                    o["note"] = ((o.get("note") or "") + "；" + r["note"]).strip("；")
                    break
            except Exception:
                pass
    # 2026-10-25 用户要求：**带几何公差的尺寸绝不能因去重而消失**
    rows = [r for r in rows
            if r.get("kind") != "特征尺寸"
            or r.get("gtols")
            or round(float(r.get("nominal_mm") or 0), 3) not in used]

    return outline_rows + rows
    # ---------- 尺寸推断（几何不可用时） ----------
    def _looks_calc(v):
        txt = f"{v:.6f}".rstrip("0").rstrip(".")
        return ("." in txt) and len(txt.split(".")[1]) >= 3

    clean = [v for v in uniq if not _looks_calc(v)]
    # 离群大值（展开长度常是外形的好几倍）→ 去掉最大的
    if len(clean) >= 3 and clean[0] > clean[1] * 4:
        clean = clean[1:]
    for i, lab in enumerate(labels):
        if i < len(clean):
            r = _outline_row("轮廓-" + lab, clean[i])
            r["note"] = (r.get("note", "") or "") + "；按尺寸推断，可能含展开/计算尺寸"
            outline_rows.append(r)
        elif i < (2 if is_sm else 3):
            outline_rows.append(_row("轮廓-" + lab, "未检出", "—", "缺失",
                                     "需补标外形" + lab + "尺寸", "尺寸数量不足"))

    # 去重：轮廓/板厚里已出现的值，不再重复显示为「特征尺寸」
    used = set()
    for r in outline_rows:
        try:
            used.add(round(float(r.get("content")), 3))
        except Exception:
            pass
    # 去重前：把带「包容要求」等特殊备注的特征尺寸信息并到轮廓行
    for r in rows:
        if r.get("kind") != "特征尺寸":
            continue
        if not r.get("note") or ("包容" not in r["note"]):
            continue
        try:
            v = round(float(r.get("nominal_mm") or 0), 3)
        except Exception:
            continue
        for o in outline_rows:
            try:
                if abs(float(o.get("content")) - v) < 1e-6:
                    o["note"] = ((o.get("note") or "") + "；" + r["note"]).strip("；")
                    break
            except Exception:
                pass
    rows = [r for r in rows
            if r.get("kind") != "特征尺寸"
            or round(float(r.get("nominal_mm") or 0), 3) not in used]

    return outline_rows + rows

    # 没有几何数据 → 用尺寸推断；先排除"展开/计算尺寸"（小数≥3 位，如 785.3982）
    def _looks_calc(v):
        txt = f"{v:.6f}".rstrip("0").rstrip(".")
        return ("." in txt) and len(txt.split(".")[1]) >= 3

    uniq = [v for v in uniq if not _looks_calc(v)]
    picks = uniq[:3]
    for i, lab in enumerate(labels):
        if i < len(picks):
            outline_rows.append(_outline_row("轮廓-" + lab, picks[i]))
        elif i < (2 if is_sm else 3):
            outline_rows.append(_row("轮廓-" + lab, "未检出", "—", "缺失",
                                     "需补标外形" + lab + "尺寸",
                                     "尺寸数量不足，可能漏标外形" + lab))

    # 轮廓/板厚排前面，特征尺寸排后面
    # 去重：轮廓/板厚里已出现的值，不再重复显示为「特征尺寸」
    used = set()
    for r in outline_rows:
        try:
            used.add(round(float(r.get("content")), 3))
        except Exception:
            pass
    # 去重前：把带「包容要求」等特殊备注的特征尺寸信息并到轮廓行
    for r in rows:
        if r.get("kind") != "特征尺寸":
            continue
        if not r.get("note") or ("包容" not in r["note"]):
            continue
        try:
            v = round(float(r.get("nominal_mm") or 0), 3)
        except Exception:
            continue
        for o in outline_rows:
            try:
                if abs(float(o.get("content")) - v) < 1e-6:
                    o["note"] = ((o.get("note") or "") + "；" + r["note"]).strip("；")
                    break
            except Exception:
                pass
    rows = [r for r in rows
            if r.get("kind") != "特征尺寸"
            or round(float(r.get("nominal_mm") or 0), 3) not in used]

    return outline_rows + rows


def parse_ra(text):
    """从粗糙度文字里取 Ra 数值（"1.6其余"/"Ra0.8"/"3.2"→ 1.6/0.8/3.2）。"""
    import re as _re
    m = _re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


# 按"工艺经济精度"给 Ra 能力（来源：机械制造工艺通用数据）
#   粗车 IT12~13 Ra12.5~25｜半精车 IT10~11 Ra3.2~6.3｜精车 IT7~8 Ra0.8~1.6
#   精铣 Ra0.8~1.6｜磨削 IT6~7 Ra0.2~0.8｜研磨 Ra0.008~0.1
# 结论：Ra0.8 用精车/精铣即可，不是"必须磨削"；Ra0.4 及以下才需要磨削/研磨。
RA_CAPABILITY_BY_CLASS = {
    # 用户 2026-10-24：Ra3.2 属**常规车/铣（普通 CNC）**可达，不算精加工
    "metal": [
        (12.5, "粗加工即可（粗车/粗铣/钻）"),
        (6.3, "常规车/铣可满足（半精加工）"),
        (3.2, "常规车/铣可满足（普通 CNC）"),
        (1.6, "精车/精铣可满足"),
        (0.8, "精车可稳定达到 Ra0.8（精铣亦可，选好刀具与进给）"),
        (0.4, "需磨削（磨削可稳定到 Ra0.4~0.8）"),
        (0.2, "需精磨/研磨"),
        (0.0, "需研磨/抛光（镜面级）"),
    ],
    "sheet": [
        (6.3, "激光/冲压切割面常规"),
        (3.2, "冲压/激光常规（局部可 CNC 修整）"),
        (1.6, "需二次加工（精冲/CNC 等）"),
        (0.0, "难保证"),
    ],
    # 注塑件：靠模具表面（抛光/高光模具）
    "plastic_injection": [
        (3.2, "常规模具可满足"),
        (1.6, "常规模具可满足（需控制模具表面）"),
        (0.8, "需高光/抛光模具"),
        (0.0, "需镜面模具（高光注塑）"),
    ],
    # 机加工塑料：CNC 切削+抛光
    "plastic_cnc": [
        (3.2, "CNC 加工常规面"),
        (1.6, "CNC 精加工可满足（Ra1.6）"),
        (0.8, "CNC 精加工可达 Ra0.8（锋利刀具+高转速）"),
        (0.4, "需抛光处理"),
        (0.0, "需镜面抛光"),
    ],
    "unknown": [
        (6.3, "常规加工可满足"),
        (3.2, "常规加工可满足（CNC）"),
        (1.6, "需精加工"),
        (0.8, "精加工可达（要求稳定时可磨削/抛光）"),
        (0.0, "需磨削/研磨/抛光（待确认）"),
    ],
}


def ra_capability(ra, part_class="unknown", mode=""):
    if part_class == "plastic":
        part_class = "plastic_cnc" if mode == "cnc" else "plastic_injection"
    table = RA_CAPABILITY_BY_CLASS.get(part_class) or RA_CAPABILITY_BY_CLASS["unknown"]
    for limit, text in table:
        if ra >= limit:
            return text
    return table[-1][1]


def analyze_roughness(rough_list, header):
    """返回 (行列表, 问题列表)。"""
    rows, issues = [], []
    pc = _part_class(header)
    mode = ""
    if pc == "plastic":
        try:
            mode = sw_qc_mt.process_mode(str(header.get("材料", "") or ""),
                                         str(header.get("类别", "") or ""),
                                         "\n".join(str(x) for x in (header.get("_notes_all") or [])))
        except Exception:
            mode = ""
    if not rough_list:
        return rows, issues
    seen = []
    for t in _dedupe_roughness(rough_list):
        ra = parse_ra(t)
        if ra is None:
            continue
        cap = ra_capability(ra, pc, mode)
        note = ("图上统一要求（其余）" if "其余" in str(t) else
                "图上统一要求（全部）" if ("全部" in str(t) or "所有" in str(t)) else
                "图上标注")
        rows.append({"kind": "粗糙度", "content": f"Ra{ra:g}", "nominal_mm": 0,
                     "tol": "—", "it": "—", "capability": cap,
                     "note": note, "view": ""})
        # 超出该类工艺的常规能力 → 提示实现方式
        if pc == "sheet" and ra < 1.6:
            issues.append(("ROUGH-STRICT", "major",
                           f"表面粗糙度 Ra{ra:g} 超出钣金常规能力",
                           "钣金（激光/冲压/折弯）一般 Ra3.2 以上；更细需精冲/CNC/精整，"
                           "或说明该面另行加工。",
                           "确认该粗糙度对应的面是否机加工面；若是钣金面，建议放宽或注明加工方式。",
                           "GB/T 1031"))
        elif pc == "plastic" and ((mode == "cnc" and ra < 0.4) or (mode != "cnc" and ra < 0.8)):
            issues.append(("ROUGH-STRICT", "major",
                           f"表面粗糙度 Ra{ra:g} 超出塑料件常规能力",
                           "注塑面靠模具表面（Ra0.8 需抛光模具）；CNC 塑料面常规 Ra1.6，"
                           "Ra0.4 以下需抛光。",
                           "确认是否必要；否则放宽，或注明需抛光处理。",
                           "GB/T 1031"))
        elif pc == "metal" and ra < 0.4:
            issues.append(("ROUGH-STRICT", "minor",
                           f"表面粗糙度 Ra{ra:g} 需磨削/研磨",
                           "精车/精铣一般可达 Ra0.8~1.6；Ra0.4 及更细需磨削、研磨或抛光。",
                           "确认是否必须；必要时注明磨削/研磨工艺与检验方法。",
                           "GB/T 1031"))
        seen.append(ra)
    return rows, issues


def _dedupe_roughness(items):
    """按 Ra 值和适用范围去重多图纸/多视图重复返回的粗糙度。"""
    out, seen = [], set()
    for x in (items or []):
        if isinstance(x, dict):
            text = str(x.get("要求") or x.get("原文") or "").strip()
        else:
            text = str(x or "").strip()
        if not text:
            continue
        ra = parse_ra(text)
        scope = ("其余" if "其余" in text else
                 "全部" if ("全部" in text or "所有" in text) else "局部")
        key = ((round(float(ra), 6), scope) if ra is not None else
               (re.sub(r"\s+", "", text).upper(), scope))
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out

# GB/T 1184 几何公差 H 级（按被测长度分段，平面度参考值）
GBT1184_H = [
    (10, 0.02),
    (30, 0.05),
    (100, 0.10),
    (300, 0.20),
    (1000, 0.30),
    (100000, 0.50),
]


def gbt1184_h_value(size_mm):
    for limit, val in GBT1184_H:
        if size_mm <= limit:
            return val
    return GBT1184_H[-1][1]


# 几何公差项目 → 中文名（SW 文本里的 <IGTOL-XXX> 代号）
GTOL_CN = {
    "STRG": "直线度", "FLAT": "平面度", "CIRC": "圆度", "CYL": "圆柱度",
    "PROF": "轮廓度", "SPROF": "面轮廓度", "LINPROF": "线轮廓度", "SURFPROF": "面轮廓度",
    "ANG": "倾斜度", "PERP": "垂直度", "PERPEND": "垂直度", "PARA": "平行度", "PAR": "平行度", "PARAL": "平行度",
    "POS": "位置度", "POSI": "位置度", "POSIT": "位置度", "CONC": "同轴度", "CONCENT": "同轴度",
    "SYMM": "对称度", "SYMMETRY": "对称度", "CIRRUN": "圆跳动", "TOTRUN": "全跳动",
    # SW 完整 token 名（实测 GetFrameSymbols3 可能给全名）
    "ROUND": "圆度", "LINEPROF": "线轮廓度", "SURFPROF": "面轮廓度",
    "PARALLEL": "平行度", "PARALLELISM": "平行度", "ANGULARITY": "倾斜度",
    "CIRCRUNOUT": "圆跳动", "TOTALRUNOUT": "全跳动", "STRAIGHTNESS": "直线度",
    "FLATNESS": "平面度", "CIRCULARITY": "圆度", "CYLINDRICITY": "圆柱度",
    "PERPENDICULARITY": "垂直度", "POSITION": "位置度", "CONCENTRICITY": "同轴度",
    # SW 内部短名（实测 2016 返回 <IGTOL-SRUN> / <IGTOL-TRUN>）
    "SRUN": "圆跳动", "TRUN": "全跳动", "RUNOUT": "圆跳动",
    "STRAIGHT": "直线度", "FLATNESS": "平面度", "CIRCULARITY": "圆度",
    "CYLINDRICITY": "圆柱度", "POSITION": "位置度", "CONCENTRICITY": "同轴度",
    "PERPENDICULARITY": "垂直度", "PARALLELISM": "平行度", "ANGULARITY": "倾斜度",
}
# 几何公差符号（GB/T 1182）
GTOL_SYMBOL = {
    "STRG": "—", "FLAT": "▱", "CIRC": "○", "CYL": "⌭",
    "PROF": "⌓", "SPROF": "⌓", "LINPROF": "⌒", "SURFPROF": "⌓",
    "ANG": "∠", "PERP": "⊥", "PERPEND": "⊥", "PARA": "∥", "PAR": "∥", "PARAL": "∥",
    "POS": "⌖", "POSI": "⌖", "POSIT": "⌖", "CONC": "◎", "CONCENT": "◎",
    "SYMM": "⌯", "CIRRUN": "↗", "TOTRUN": "⌰",
    "SRUN": "↗", "TRUN": "⌰", "RUNOUT": "↗",
}

# 几何公差修正符号（GB/T 1182 / ISO 1101）
GTOL_MODIFIERS = [
    ("\u24C2", ["Ⓜ", "MMC", "<IGTOL-MMC>", "(M)", "圈M", "最大实体"], "Ⓜ", "最大实体要求(MMC)"),
    ("\u24C1", ["Ⓛ", "LMC", "<IGTOL-LMC>", "(L)", "最小实体"], "Ⓛ", "最小实体要求(LMC)"),
    ("CZ", ["CZ", "<IGTOL-CZ>", "组合公差带", "公共公差带"], "CZ", "组合公差带(CZ)"),
    ("SZ", ["SZ", "<IGTOL-SZ>", "独立公差带"], "SZ", "独立公差带(SZ)"),
    ("\u24BB", ["Ⓕ", "FREESTATE", "(F)", "自由状态"], "Ⓕ", "自由状态(F)"),
    ("\u24C9", ["Ⓣ", "TANGENT", "(T)", "相切"], "Ⓣ", "相切要素(T)"),
    ("\u24C5", ["Ⓟ", "PROJECTED", "(P)", "延伸公差带"], "Ⓟ", "延伸公差带(P)"),
]


def gtol_modifiers(text):
    """返回命中的修正符号 [(code, 符号, 中文含义)]。"""
    t = str(text or "")
    out = []
    for code, pats, sym, cn in GTOL_MODIFIERS:
        if any(p in t for p in pats):
            out.append((code, sym, cn))
    return out


def dim_limits_mm(dim):
    """从尺寸记录算上下限（mm）：返回 (lower, upper) 或 None。"""
    import re as _re
    try:
        nominal = abs(float(dim.get("value_mm")))
    except Exception:
        return None
    raw = str(dim.get("tol_value") or "")
    t = str(dim.get("tol_type") or "")
    nums = []
    for it in _re.split(r"[/,;\s]+", raw):
        it = it.strip()
        if not it:
            continue
        try:
            nums.append(float(it))
        except Exception:
            pass
    if not nums:
        return (nominal, nominal)
    # SW 公差单位是米
    mm = [v * 1000.0 for v in nums]
    if t == "4" and len(mm) == 1:
        return (nominal - abs(mm[0]), nominal + abs(mm[0]))
    if len(mm) >= 2:
        up, low = mm[0], mm[1]
        return (nominal + min(up, low), nominal + max(up, low))
    v = mm[0]
    if t in ("2", "3"):
        return (nominal + min(0.0, v), nominal + max(0.0, v))
    return (nominal - abs(v), nominal + abs(v))


def mmc_lmc_note(mods, text, dims, view):
    """给带 Ⓜ/Ⓛ 的几何公差写备注：算最大/最小实体尺寸。"""
    codes = [m[0] for m in mods]
    need = ("\u24C2" in codes) or ("\u24C1" in codes)
    if not need:
        return ""
    # 找关联尺寸：同视图、带 Ø 的尺寸；找不到就取同视图最大尺寸
    import re as _re
    cands = []
    for d in dims or []:
        vn = str(d.get("view") or "")
        if view and vn and vn != view:
            continue
        try:
            val = abs(float(d.get("value_mm")))
        except Exception:
            continue
        txt = str(d.get("text") or "")
        cands.append((("Ø" in txt or "φ" in txt or "%%c" in txt.lower()), val, d))
    if not cands:
        return "带 Ⓜ/Ⓛ 修正符号，但**未找到关联尺寸**，无法计算最大/最小实体尺寸（需人工核算）"
    dia = [c for c in cands if c[0]]
    pick = max(dia, key=lambda c: c[1]) if dia else max(cands, key=lambda c: c[1])
    dim = pick[2]
    lim = dim_limits_mm(dim)
    if not lim:
        return "带 Ⓜ/Ⓛ 修正符号，关联尺寸公差无法解析（需人工核算）"
    lo, hi = lim
    txt = f"关联尺寸 {dim.get('value_mm'):g}"
    tv = str(dim.get("tol_value") or "")
    if tv:
        import re as _re2
        nums2 = []
        for it2 in _re2.split(r"[/,;\s]+", tv):
            it2 = it2.strip()
            if not it2:
                continue
            try:
                nums2.append(float(it2) * 1000.0)
            except Exception:
                pass
        if nums2:
            if len(nums2) >= 2:
                lo2, hi2 = min(nums2), max(nums2)
                txt += f"（公差 {lo2:g}~{hi2:g}mm）"
            else:
                txt += f"（公差 ±{abs(nums2[0]):g}mm）"
        else:
            txt += f"（公差 {tv}）"
    parts = [f"尺寸范围 {lo:g}~{hi:g}mm"]
    if "\u24C2" in codes:
        parts.append(f"MMS（最大实体尺寸）：孔 Ø{lo:g} / 轴 Ø{hi:g}")
    if "\u24C1" in codes:
        parts.append(f"LMS（最小实体尺寸）：孔 Ø{hi:g} / 轴 Ø{lo:g}")
    return txt + "；" + "；".join(parts)


# 必须带 φ（直径符号）的项目
GTOL_NEED_DIA = {"同轴度", "位置度"}
# φ 不应出现的项目（出现则提示确认）
GTOL_NO_DIA = {"直线度", "平面度", "圆度", "圆柱度", "平行度", "垂直度",
               "倾斜度", "对称度", "轮廓度", "线轮廓度", "面轮廓度",
               "圆跳动", "全跳动"}


def gtol_cn_name(text):
    import re as _re
    m = _re.search(r"<IGTOL[-_]([A-Z]+)>", str(text or ""))
    if not m:
        return ""
    return GTOL_CN.get(m.group(1).upper(), "")


def gtol_has_dia(text):
    t = str(text or "")
    return ("Ø" in t) or ("φ" in t) or ("Φ" in t) or ("%%c" in t.lower()) or \
        ("<IGTOL-DIA>" in t) or ("<IGTOL-DIAM>" in t) or ("<MOD-DIAM>" in t)


def gtol_value_and_datum(text):
    """从 '0.02A' / 'Ø0.02|A' 之类里取数值与基准字母（自动忽略 <MOD-*> 等标签与修正词）。"""
    import re as _re
    t = str(text or "")
    t = _re.sub(r"<[^>]*>", " ", t)          # 去掉全部 <IGTOL-*> / <MOD-*> 标签
    for _p in ("Ⓜ", "Ⓛ", "Ⓔ", "Ⓕ", "Ⓣ", "Ⓟ", "CZ", "SZ", "MMC", "LMC", "SIM",
               "FREESTATE", "TANGENT", "PROJECTED", "均布"):
        t = t.replace(_p, " ")
    t = t.replace("Ø", " ").replace("φ", " ").replace("Φ", " ")
    nums = _re.findall(r"\d+(?:\.\d+)?", t)
    val = nums[0] if nums else ""
    # 基准：取数值后紧跟的连续大写串（如 BA → B | A；BM → B | M）
    m2 = _re.search(r"\d+(?:\.\d+)?\s*([A-Z]{1,4})\b", t)
    letters = list(m2.group(1)) if m2 else []
    datum = " | ".join(dict.fromkeys(letters))
    return val, datum


def gtol_symbol(text):
    import re as _re
    m = _re.search(r"<IGTOL[-_]([A-Z]+)>", str(text or ""))
    if not m:
        return ""
    return GTOL_SYMBOL.get(m.group(1).upper(), ""), re


def _gtol_datum_from_frames(frames):
    """从 frames 字符串（如 '0.1CZ,,B,A,'）里取基准字母，供 format 使用。"""
    if not frames:
        return ""
    try:
        fr = frames[0] if isinstance(frames, (list, tuple)) else str(frames)
        parts = str(fr).split("|")
        if len(parts) < 2:
            return ""
        fields = parts[-1].split(",")
        letters = [x.strip() for x in fields[2:4] if x and x.strip()]
        return " | ".join(dict.fromkeys(letters))
    except Exception:
        return ""


def format_gtol_content(text, frames=None):
    """形位公差显示：符号 + ⌀ + 值 + 修正 + |基准（如 ⌖ ⌀0.1CZ |B |A）。"""
    if not str(text or "").strip() and frames:
        try:
            text = str(frames[0] if isinstance(frames, (list, tuple)) else frames)
        except Exception:
            pass
    name = gtol_cn_name(text)
    val, datum = gtol_value_and_datum(text)
    _fd = _gtol_datum_from_frames(frames)
    if _fd:
        datum = _fd
    dia = "⌀ " if gtol_has_dia(text) else ""
    sym = _sym_from_tokens(text) or ""
    if not sym and not name:
        return str(text or "").strip()
    mods = gtol_modifiers(text)
    modtxt = ""
    for m in mods:
        modtxt += m[1]
    body = ((sym + " ") if sym else "") + (dia + val + modtxt).strip()
    _ds = [x.strip() for x in datum.split("|") if x.strip()] if datum else []
    for _d in _ds:
        body += " |" + _d
    return body



def analyze_gtols(gtols, header=None, part_size=0.0, issues=None, dims=None):
    """几何公差：取框内数值，判断常规加工能否满足。"""
    rows = []
    if issues is None:
        issues = []
    for g in (gtols or []):
        if isinstance(g, str):
            g = {"text": g}
        text = str(g.get("text") or "").strip()
        _cn = gtol_cn_name(text)
        _dia = gtol_has_dia(text)
        # φ（直径）符号审核
        if _cn:
            if _cn in GTOL_NEED_DIA and not _dia:
                issues.append(("GTOL-DIA-MISSING", "critical",
                               f"{_cn}公差缺 φ（直径符号）",
                               f"「{text}」是{_cn}，公差带为圆柱形，公差值前必须带 φ（如 φ0.02）；"
                               "缺 φ 会让加工/检验理解成平面公差带，属于标注错误。",
                               "把公差值改为 φ + 数值（如 φ0.02）；同轴度/位置度这类都要带 φ。",
                               "GB/T 1182"))
            elif _cn in GTOL_NO_DIA and _dia:
                # 垂直度/平行度/倾斜度带 φ：被测要素是“轴线”时是正确写法（GB/T 1182），
                # 只有当被测要素是平面/轮廓时才算标注错误 → 给提示由人工确认，不判严重。
                issues.append(("GTOL-DIA-EXTRA", "minor",
                               f"{_cn}公差带了 φ（直径符号）",
                               f"「{text}」是{_cn}：φ 表示圆柱形公差带，"
                               "被测要素为轴线时这种写法是正确的（GB/T 1182 / ISO 1101）；"
                               "若被测要素是平面/轮廓，则 φ 不适用。",
                               "确认被测要素：轴线→保留 φ；平面/轮廓→去掉 φ 并改为平面公差带。",
                               "GB/T 1182"))
        _mods = gtol_modifiers(text)
        frames = g.get("frames") or []
        vals = []
        syms = []
        for f in frames:
            fs = str(f)
            if "|" in fs:
                sym, val = fs.split("|", 1)
                syms.append(sym.strip())
                for m in re.findall(r"\d+(?:\.\d+)?", val):
                    try:
                        vals.append(float(m))
                    except Exception:
                        pass
        for m in re.findall(r"\d+(?:\.\d+)?", text):
            try:
                vals.append(float(m))
            except Exception:
                pass
        v = min(vals) if vals else None
        cap = ""
        if v is not None:
            _pc = _part_class(header)
            # 塑料件 + 位置度：按 GB/T 47067-2026 表9 判定
            try:
                if _pc == "plastic" and re.search(r"位置度|⌖", str(text or "")):
                    _gtx = sw_qc_mt.detect_general_tolerance(
                        "\n".join(str(x) for x in (header.get("_notes_all") or [])))
                    if _gtx:
                        _apos = sw_qc_mt.tg_position(part_size, _gtx["grade"])
                        if _apos:
                            ok = v <= _apos
                            cap = (f"按 TG{_gtx['grade']} 表9 位置度允许 Ø{_apos:g}mm，"
                                   f"实际 {v:g}mm → {'满足' if ok else '超差'}")
                            rows.append({"kind": "位置度", "content": format_gtol_content(text) or f"{v:g}",
                                         "nominal_mm": 0, "tol": f"{v:g} mm",
                                         "it": "", "capability": cap,
                                         "note": "按 GB/T 47067-2026 表9", "view": ""})
                            continue
            except Exception:
                pass
            _size = abs(float(part_size or 0)) or 100.0
            _h = gbt1184_h_value(_size)
            if v <= _h:
                cap = (f"达 GB/T 1184 H 级（被测长度≈{_size:g}mm，H级={_h:g}mm）"
                       f"，需重点审核/与厂家确认")
                if _pc == "sheet":
                    cap += "；钣金需精密折弯/检具保证"
                elif _pc == "plastic":
                    cap += "；塑料件受成型收缩影响，需模具/工装保证"
            else:
                cap = f"松于 H 级（H级={_h:g}mm），常规加工可满足"
        _content = format_gtol_content(text, g.get("frames")) or " ".join(s for s in syms if s)
        for _m in _mods:
            if _m[1] not in _content:
                _content += " " + _m[1]
        _note_parts = [gtol_cn_name(text)] if gtol_cn_name(text) else []
        for _m in _mods:
            _note_parts.append(_m[2])
        _mmc = mmc_lmc_note(_mods, text, dims, g.get("view", ""))
        if _mmc:
            _note_parts.append(_mmc)
        rows.append({
            "kind": "几何公差",
            "content": _content,
            "nominal_mm": 0,
            "tol": (f"{v:g} mm" if v is not None else ""),
            "it": "", "capability": cap or "未取到数值，无法判断",
            "note": "；".join(_note_parts), "view": g.get("view", ""),
        })
    return rows


# ---------------------------------------------------------------- AI
AI_SYSTEM = (
    "你是机械工程图审核工程师。请依据输入中已经提供的材料、技术要求、尺寸公差、几何公差、基准关系、"
    "本地规则事实和审核标准体系，识别明确问题及有工程依据的风险。\n"
    "\n"
    "职责：\n"
    "1. 审核技术要求是否合理、完整、无矛盾且可制造、可检验。\n"
    "2. 审核材料、工艺、热处理、表面处理与技术要求是否匹配。\n"
    "3. 审核尺寸公差、几何公差、基准和技术要求之间是否存在明显矛盾或组合风险。\n"
    "4. 标准数值、IT等级和规格表计算以输入中的本地规则结果为准，不重新猜测或查表。\n"
    "5. 公差列写“基本尺寸”的尺寸（方框尺寸），表示图上不带 ± 公差、由几何公差（如轮廓度/位置度）控制，"
    "不要判为缺公差或“±0 不规范”。\n"
    "6. 孔标注中“N x ⌀d”是主特征（孔组）；沉头（⌵/CSINK）、沉孔（⌴/CBORE）、深度、倒角、均布"
    "只是修饰参数，不要当成主尺寸，也不要判成“尺寸错误”。\n"
    "7. 螺纹代号 M8（粗牙）/ M8x1.25（细牙）/ G、NPT、Rc、Rp（管螺纹）/ Tr（梯形）/ UNC、UNF（美制）"
    "原样理解；牙距写在代号后（如 x1.25），不要自行补默认牙距数值。\n"
    "8. 圆角 R2、倒角 C1、正方形 □20、球面 S⌀20、参考尺寸 (30) 按标准含义理解；"
    "参考尺寸不检验，不得据此报“缺公差”。\n"
    "9. 图纸上出现无法识别的符号或写法时，标记 review_required，不要猜其含义或凭经验补全。\n"
    "10. 尺寸若与轮廓度/位置度/倾斜度等几何公差关联，且本身没有 ± 公差，按“基本尺寸”理解；"
    "可能图上没有方框，但同样不带 ±、由几何公差定位，不要判缺公差。\n"
    "11. 输入“待审核公差清单”的“备注”是程序查表/读取的确定结果（螺纹公称大径、小径、牙距、沉台孔、深度等），"
    "以此为准，不要重算；M8 为粗牙、M8x1.25 为粗牙（默认牙距）、M8x1.0 为细牙；"
    "不得自行给未写牙距的螺纹补数值。\n"
    "\n"
    "输出原则：\n"
    "1. 只输出需要修改或需要人工确认的项目；通过项不逐条输出。\n"
    "2. 不要求每个技术要求都生成结论；但必须结合材料/工艺/表面处理/技术要求，把【待审核公差清单】里的尺寸公差、几何公差、粗糙度逐条看一遍，"
    "发现“公差过严/漏标/与工艺冲突/与基准矛盾/归属不合理”的，必须写 findings。\n"
    "3. TECH-*/DIM-*/GDT-* 只是在 JSON 中定位证据的内部编号；面向用户的 title、risk、suggestion "
    "必须写成‘尺寸3.5上的面轮廓度’这类可读名称，不得单独显示内部编号。\n"
    "4. 不得虚构输入中不存在的材料、用途、环境、载荷、配合对象或几何关系。\n"
    "5. 可以根据已提供事实作工程推断，但证据不足时必须使用 review_required，不得强判为错误。\n"
    "6. 每项必须说明结果、风险、判定依据、工程推理、修改建议和验证方法，语言简洁直接。\n"
    "7. 必须逐项审核 required_review_ids 中的全部项目，并在 reviewed_ids 原样回传每个 ID，不能漏项、重复或增加不存在的 ID。\n"
    "8. input_checksum 必须原样回传；不得省略任何已发现的问题，也不得用概括性意见替代具体项目。\n"
    "9. 不得虚构标准条款号、功能用途或推荐数值。输入不足以确定改成多少时，明确写‘需设计确认’，并给出确认方法。\n"
    "10. 只返回一个合法 JSON 对象，不输出 Markdown、解释过程或客套话。\n"
    "\n"
    "输入编号：技术要求为 TECH-1/TECH-2…；尺寸公差为 DIM-1/DIM-2…；几何公差为 GDT-1/GDT-2…；"
    "标题栏事实为 TITLE-MATERIAL / TITLE-NAME / TITLE-VERSION / TITLE-CATEGORY；"
    "本地规则事实为 LOCAL-<规则ID>-<序号>。整体性问题 location 用 OVERALL。\n"
    "\n"
    "【唯一输出格式】\n"
    "{\n"
    " \"input_checksum\": \"原样回传输入中的校验值\",\n"
    " \"reviewed_ids\": [\"TECH-1\", \"DIM-1\", \"GDT-1\"],\n"
    " \"summary\": \"≤80字总述\",\n"
    " \"findings\": [\n"
    "  {\"category\": \"技术要求|尺寸/几何公差|材料/工艺\",\n"
    "   \"location\": \"TECH-1 / DIM-2 / GDT-1 / OVERALL\",\n"
    "   \"status\": \"problem|review_required\",\n"
    "   \"title\": \"≤40字的问题标题\",\n"
    "   \"risk_level\": \"critical|major|minor|review\",\n"
    "   \"risk\": \"≤120字：后果或工程风险\",\n"
    "   \"standard_basis\": \"≤80字：输入中可核对的标准或工程依据，不得编造条款号\",\n"
    "   \"reasoning\": \"≤120字：事实到结论的专业推理\",\n"
    "   \"evidence_refs\": [\"TECH-1\"],\n"
    "   \"suggestion\": \"≤120字：具体可执行的改法\",\n"
    "   \"verification_method\": \"≤120字：修改后如何复核或检验\",\n"
    "   \"confidence\": 0.9}\n"
    " ],\n"
    " \"omitted_count\": 0\n"
    "}\n"
    "\n"
    "规则：所有已发现的问题和待确认项都必须输出，omitted_count 必须为0。信息不足时用 status=review_required + risk_level=review，"
    "不得把信息不足判成 critical/major。\n"
)
AI_TECH_RULE_ID = "AI-TECH-ITEM"
AI_TECH_CATEGORY = "技术要求"
AI_TECH_SEVERITIES = ("critical", "major", "minor")
MAX_AI_DETAIL_LEN = 500
MAX_AI_SUGGESTION_LEN = 300
MAX_AI_TECH_TITLE_LEN = 40
PROVIDER_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
}

AI_VAGUE_RE = re.compile(r"可能|也许|建议关注|疑似需要确认|大概")
# 技术条目自带的编号前缀（保留原编号时避免出现「1. 1. xxx」）
_NOTE_NO_PREFIX_RE = re.compile(
    r"^\s*(?:[（(]\s*\d{1,2}\s*[）)]|\d{1,2}\s*[、.．]|"
    r"[一二三四五六七八九十]{1,2}\s*[、.．])\s*(?=[\u4e00-\u9fa5A-Za-z])")
# 一行是否是「1. xxx / (1) xxx」这种编号条目
_NOTE_NO_LINE_RE = re.compile(
    r"^\s*(?:\d{1,2}\s*[.、．]|[（(]\s*\d{1,2}\s*[）)])\s*\S")
# 技术要求里可能混进来的小标题（不是条目）
_NOTE_HEADINGS = {"技术要求", "技术条件", "技术要求：", "技术条件：",
                  "技术要求:", "技术条件:", "技术要求（逐条）"}


def _split_note_number(text):
    """去掉技术条目自带的编号前缀，返回正文（都去不掉就原样返回）。"""
    t = str(text or "").strip()
    return _NOTE_NO_PREFIX_RE.sub("", t).strip() or t


def _split_tech_note_lines(note):
    """把一条 note 拆成「逐条」文本：有 ≥2 个编号行才按行拆，否则整条算一条。

    实测 SW 注释有时一条 note 里塞了整段「1. … 2. … 3. …」，直接整段编号
    会变成「1. 1. … 2. …」，AI 对不上号；这里先按行拆开，编号才稳定。
    """
    raw = str(note or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    numbered = [ln for ln in lines if _NOTE_NO_LINE_RE.match(ln)]
    if len(numbered) < 2:
        return [raw]
    out = []
    for ln in lines:
        if ln in _NOTE_HEADINGS:
            continue
        if _NOTE_NO_LINE_RE.match(ln):
            out.append(ln)
        elif out:
            out[-1] = out[-1] + " " + ln       # 续行并入上一条
        else:
            out.append(ln)
    return out


def _tech_items(notes):
    """技术要求整理成 [(no, text), ...]：no 按原文顺序从 1 开始，稳定不变。"""
    merged = []
    for n in notes or []:
        merged.extend(_split_tech_note_lines(n))
    cleaned = []
    for t in merged:
        body = _split_note_number(t).strip()
        if not body or body in _NOTE_HEADINGS:
            continue                            # 纯标题/空条不算技术要求条目
        cleaned.append(body)
    return [(i, body) for i, body in enumerate(cleaned, 1)]


def _reason_brief(reason, limit=MAX_AI_TECH_TITLE_LEN):
    """从 AI 给的原因里取短摘要（问题标题用，≤limit 字）。"""
    t = re.sub(r"\s+", " ", str(reason or "")).strip()
    return t if len(t) <= limit else t[:limit]


def _cited_item_text(reason, item_text):
    """reason 里有没有引用该条原文（引用了才算有依据，用于含糊措辞的二次净化）。"""
    r = _n(reason)
    t = _n(item_text)
    if not r or not t:
        return False
    if len(t) <= 40 and t in r:
        return True
    for i in range(0, max(1, len(t) - 8) + 1, 4):
        seg = t[i:i + 8]
        if len(seg) == 8 and seg in r:
            return True
    return False


def _coerce_item_no(v):
    """把 AI 给的条目编号转成 int；转不了返回 None。"""
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, float):
            return int(v) if v.is_integer() else None
        m = re.fullmatch(r"第?\s*(\d{1,3})\s*条?", str(v or "").strip())
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _level_to_sev(level):
    """中文问题等级 → severity（info 表示无需修改）"""
    t = str(level or "").strip()
    if not t or t in ("—", "-", "无", "通过"):
        return "info"
    if "重点" in t or "严重" in t:
        return "critical"
    if "建议修改" in t or "修 改" in t or "一般" in t:
        return "major"
    if "轻微" in t or "建议优化" in t or "优化" in t:
        return "minor"
    if t.lower() in ("critical", "major", "minor"):
        return t.lower()
    return "minor"


def _land_tech_items(raw_items, tech_items):
    """把 AI 的逐条判定（items）落成 (issues, passed, dropped)。

    · 问题 → AI-TECH-ITEM issue（category 固定「技术要求」）
    · 通过 → passed 文案「技术要求第N条：通过（AI）」
    · no 越界/重复、verdict 非法、问题没给原因、含糊措辞且无引用 → 丢弃（宁可少报）
    """
    tech_items = list(tech_items or [])
    if not tech_items:
        return [], [], len(list(raw_items or []))
    total = len(tech_items)
    src = {no: txt for no, txt in tech_items}
    out, passed, dropped = [], [], 0
    seen = set()
    for pos, it in enumerate(raw_items or []):
        if not isinstance(it, dict):
            dropped += 1
            continue
        no = _coerce_item_no(it.get("no"))
        if no is None or not (1 <= no <= total):
            no = pos + 1                       # 没给/给错编号时按顺序对位
        if not (1 <= no <= total):
            dropped += 1
            continue
        if no in seen:
            dropped += 1
            continue
        seen.add(no)
        verdict = str(it.get("verdict") or "").strip()
        clause = str(it.get("clause") or "").strip()
        conclusion = str(it.get("conclusion") or "").strip()
        level_cn = str(it.get("level") or "").strip()
        reason = re.sub(r"\s+", " ", (str(it.get("reason") or conclusion or "")).strip())
        if not verdict:
            verdict = "问题" if (conclusion or reason) else "通过"
        # 放宽：不再因“可能/建议”等措辞丢弃（用户要求少限制）
        if verdict == "通过":
            # 用户新口径：通过的条目不输出（不产生通过项）
            pass
        elif verdict == "问题":
            if not reason:
                dropped += 1                   # 没原因无法说明问题 → 宁可少报
                continue
            sev = _level_to_sev(level_cn) if level_cn else str(it.get("severity") or "").strip().lower()
            if sev not in AI_TECH_SEVERITIES:
                sev = "major"                  # 非法值降为「一般」
            sugg = str(it.get("suggestion") or "").strip()[:MAX_AI_SUGGESTION_LEN]
            # 用户要求：每条「问题」必须给修改建议；缺失或过于含糊 → 自动补成可执行建议
            _vague_sugg = (not sugg) or len(sugg) < 6 or bool(AI_VAGUE_RE.search(sugg)) \
                or sugg in ("请修改", "建议修改", "需要修改", "请确认", "待确认", "修改")
            if _vague_sugg:
                sugg = ("请核对技术要求第" + str(no) +
                        "条：补充明确的工艺/公差/表面处理要求，或改为可执行、可检验的表述。")
            _brief = clause or reason
            title = ("技术要求第" + str(no) + "条："
                     + _reason_brief(_brief or conclusion, MAX_AI_TECH_TITLE_LEN))
            iss = issue(AI_TECH_RULE_ID, sev, title,
                        reason[:MAX_AI_DETAIL_LEN], sugg, "AI 逐条判定")
            iss["category"] = AI_TECH_CATEGORY
            out.append(iss)
        else:
            dropped += 1                       # verdict 非法 → 丢弃（宁可少报）
    return out, passed, dropped



def _ai_notes(notes, header):
    """只把真正的技术要求条目交给 AI。

    图纸 notes 里混着标题栏文字（材料值、图号、名称、版本、单重等），
    之前整段发过去，AI 会误报"技术要求内容混编"。这里按标题栏值过滤掉。
    """
    vals = []
    for k, v in (header or {}).items():
        if k.startswith("_"):
            continue
        v = str(v or "").strip()
        if len(v) >= 2:
            vals.append(_n(v))
    out = []
    for n in notes:
        t = str(n or "").strip()
        if not t:
            continue
        nt = _n(t)
        # 与标题栏值相同/包含 → 认为是标题栏碎片，不是技术要求
        if any(nt == v or (len(v) >= 3 and v in nt and len(nt) <= len(v) + 6) for v in vals):
            continue
        # 纯数字/短字母（如 0.01、C、A、2-C4 这类标题栏或标注碎片）
        if re.fullmatch(r"[0-9.]+", nt):
            continue
        if len(nt) <= 2 and re.fullmatch(r"[a-z0-9\-]+", nt):
            continue
        # 2026-10-26：标题栏固定标签（不是技术要求，别喂给 AI 稀释注意力）
        _TB_LABELS = {
            "审核", "设计", "标记", "处数", "分区", "更改文件号", "签名", "年月日", "年 月 日",
            "批准", "共张", "第张", "共 张", "第 张", "版本", "比例", "投影法", "投 影 法",
            "日期", "重量", "材料", "名称", "图号", "图样名称", "图样代号", "阶段标记", "比例尺",
        }
        if nt in _TB_LABELS or nt.replace(" ", "") in {x.replace(" ", "") for x in _TB_LABELS}:
            continue
        if len(nt) <= 6 and re.fullmatch(r"[\u4e00-\u9fa5\s]+", nt) and "、" not in nt:
            # 2~6 字纯中文且不含标点：标题栏标签的典型特征（"技术要求"本身除外）
            if nt not in ("技术要求",):
                continue
        # 2026-10-26：纯符号/日期/比例/模板占位符（如 1:5、2026/10/26、<VLLABEL>/<VLSCALEV>）不是技术要求
        if not re.search(r"[\u4e00-\u9fa5]", nt):
            continue
        out.append(t)
    return out


def _strip_json_fence(t):
    t = str(t or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"```\s*$", "", t)
    return t.strip()


def _balance_json(t):
    """尽量把截断的 JSON 补成可解析：补齐括号、去掉尾逗号。"""
    t = re.sub(r",\s*([}\]])", r"\1", t)          # 尾逗号
    stack = []
    in_str = False
    esc = False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
    out = t
    if in_str:
        out += '"'
    # 去掉末尾不完整的 "key": "value" 片段（以逗号或冒号结尾）
    out = re.sub(r",\s*$", "", out)
    out = re.sub(r'"[^"]*"\s*:\s*$', "", out)
    out = re.sub(r",\s*$", "", out)
    for closer in reversed(stack):
        out = out.rstrip()
        if out.endswith(","):
            out = out[:-1]
        out += closer
    return out


def _salvage_items(t):
    """从损坏/截断文本里尽量捞出完整的花括号对象。"""
    items = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    frag = t[start:i + 1]
                    try:
                        obj = json.loads(frag)
                        if isinstance(obj, dict):
                            items.append(obj)
                    except Exception:
                        pass
                    start = -1
    return items


def _parse_ai_json(content):
    """返回 (parsed, err)。err 为空表示成功；截断时 parsed 尽量可用。"""
    t = _strip_json_fence(content)
    if not t:
        return None, "AI 返回空内容"
    try:
        return json.loads(t), ""
    except Exception:
        pass
    # 去掉前后多余文字，只取最外层 {...} / [...]
    m = re.search(r"[\{\[]", t)
    if m:
        body = t[m.start():]
        for cand in (body, _balance_json(body)):
            try:
                return json.loads(cand), ""
            except Exception:
                continue
    # 仍失败：捞完整对象，能捞到几个算几个
    salv = _salvage_items(t)
    if salv:
        return salv, "AI 输出不完整（已按完整条目尽量恢复）"
    return None, "AI 返回不是合法 JSON（可能被截断）"


def audit_standard_block():
    """按「检查设置」里的标准体系，告诉 AI 按哪套标准审（GB / ISO / ASME）。"""
    std = (os.environ.get("QC_STANDARD", "") or "").strip().upper()
    if std.startswith("ASME") or std.startswith("ANSI"):
        return {
            "体系": "ASME Y14.5（美标）",
            "主要依据": ["ASME Y14.5 尺寸与公差标注", "ASME Y14.41 数字化产品定义",
                        "ASME B1.1 螺纹、B4.1 配合", "ASME Y14.36 表面纹理"],
            "公差原则": "包容原则（尺寸公差约束形状，默认包容要求 ⓔ）",
            "冲突时": "以图纸技术要求里明确引用的标准为准；未引用时按 ASME 审",
            "注意": "美标不用 GB/T 1804 那类「未注公差」叫法，未注公差按图纸标注的 ASME 未注公差表或通用公差块。",
        }
    if "ISO" in std:
        return {
            "体系": "ISO（国际标准）",
            "主要依据": ["ISO 2768 一般公差", "ISO 1101 几何公差", "ISO 8015 独立原则",
                        "ISO 286 极限与配合", "ISO 1302 表面纹理", "ISO 20457 塑料模塑件公差"],
            "公差原则": "独立原则（ISO 8015）；若图上标 ⓔ 才按包容要求",
            "冲突时": "以图纸技术要求里明确引用的标准为准；未引用时按 ISO 审",
            "注意": "ISO 2768-m/f/c 与 GB/T 1804 数值基本对应，但引用名要写成 ISO 2768。",
        }
    return {
        "体系": "GB/T（中国国标，默认）",
        "主要依据": ["GB/T 1800.1 极限与配合/公差等级", "GB/T 1804 一般公差（未注线性/角度）",
                    "GB/T 1184 未注几何公差", "GB/T 1182 几何公差标注", "GB/T 4249 / ISO 8015 独立原则",
                    "GB/T 131 / GB/T 1031 表面粗糙度", "GB/T 47067-2026 塑料模塑件公差（TG1~TG9）"],
        "公差原则": "独立原则（GB/T 4249 / ISO 8015）；若图上标 ⓔ 才按包容要求",
        "冲突时": "以图纸技术要求里明确引用的标准为准；未引用时按 GB/T 审",
        "注意": "图中若引用 ISO/ASME 标准号，就按那个标准的具体条款判断，不要硬套 GB。",
    }


def _ai_roughness_block(roughness, capabilities=None):
    """粗糙度输入：用图纸**原始标注文字**（保留“其余”等中文），并附解析与加工能力。"""
    raw = []
    caps = {}
    for c in (capabilities or []):
        try:
            if isinstance(c, dict):
                key = str(c.get("要求") or "")
                caps[key] = str(c.get("常规能否满足") or "")
        except Exception:
            pass
    if isinstance(roughness, (list, tuple)):
        for x in _dedupe_roughness(roughness):
            if isinstance(x, dict):
                t = str(x.get("要求") or x.get("原文") or "")
            else:
                t = str(x)
            if t.strip():
                raw.append(t.strip())
    elif roughness:
        raw = [str(roughness).strip()]

    parsed = []
    for t in raw:
        m = re.search(r"(\d+(?:\.\d+)?)", t)
        ra = float(m.group(1)) if m else None
        item = {"原文": t}
        if ra is not None:
            item["Ra"] = ra
            # 能力匹配（按 Ra 值找）
            for k, v in caps.items():
                if str(ra) in k or ("Ra%s" % ("%g" % ra)) in k:
                    if v:
                        item["常规能否满足"] = v
                    break
        if "其余" in t:
            item["适用"] = "其余（图上未单独标注的表面，属于有效技术要求）"
        elif "全部" in t or "所有" in t:
            item["适用"] = "全部表面"
        else:
            item["适用"] = "图上单独标注的表面"
        parsed.append(item)

    notes = []
    if any("其余" in t for t in raw):
        notes.append("「Ra3.2（其余）」表示未注表面的默认粗糙度，必须参与审核，不能当成没有粗糙度要求")
    if any("Ra" in t or re.match(r"^\d", t) for t in raw):
        pass
    return {
        "标注": raw,
        "解析": parsed,
        "说明": "；".join(notes) if notes else "按图纸标注审核",
    }


def _gtol_human_name(sym):
    return {"—": "直线度", "▱": "平面度", "○": "圆度", "⌭": "圆柱度",
            "⌒": "线轮廓度", "⌓": "面轮廓度", "∠": "倾斜度", "⊥": "垂直度",
            "∥": "平行度", "⌖": "位置度", "◎": "同轴度", "⌯": "对称度",
            "↗": "圆跳动", "⌰": "全跳动"}.get(str(sym or ""), "几何公差")




def _infer_feature_kind(content):
    """由挂接尺寸内容推断被测要素：带 ⌀/φ → 轴心；纯数值 → 平面；空 → 未判定。"""
    c = str(content or "").strip()
    if not c:
        return "未判定"
    if ("⌀" in c) or ("φ" in c) or ("Ø" in c):
        return "轴心"
    if re.search(r"[0-9]", c):
        return "平面"
    return "未判定"


def _phi_judgement(sym, val, content, datum_types=None):
    """φ 使用是否与要素类型匹配。
    返回 (被测要素, 公差带, φ使用, 结论)
    结论：匹配 / 可疑（平面用圆柱带，建议确认） / 常见（轴线无φ也可成立） / 未判定
    """
    sym = str(sym or "")
    val = str(val or "")
    feat = _infer_feature_kind(content)
    has_phi = ("φ" in val) or ("⌀" in val) or ("Ø" in val)
    zone = "圆柱" if has_phi else "两平行平面"
    if sym not in ("⊥", "∥", "∠", "⌖", "◎", "⌯"):
        return feat, zone, ("带φ" if has_phi else "不带φ"), "不适用"
    if feat == "未判定":
        return feat, zone, ("带φ" if has_phi else "不带φ"), "未判定（不要据此判错）"
    if feat == "平面" and has_phi:
        return feat, zone, "带φ", "可疑：平面要素通常不用圆柱公差带，建议人工确认"
    if feat == "轴心" and not has_phi:
        return feat, zone, "不带φ", "常见：轴线用两平行平面公差带也可成立，不要判错"
    return feat, zone, ("带φ" if has_phi else "不带φ"), "匹配"


def _ai_frame_facts(g):
    """把插件判定的框格事实完整交给 AI，禁止 AI 再凭显示文本猜类别。"""
    ftype = str(g.get("frame_type") or "待确认")
    labels = {"独立": "独立公差", "组合": "组合公差框格（多单段）",
              "复合": "复合公差框格", "待确认": "待确认"}
    structure = g.get("frame_structure")
    if not isinstance(structure, list) or not structure:
        structure = [{
            "层序": int(g.get("frame_order") or g.get("_frame_order") or 1),
            "本层符号": _raw_frame_sym(g),
            "有效符号": str(g.get("effective_sym") or g.get("sym") or ""),
            "符号来源": str(g.get("symbol_source") or "本层"),
            "公差值": str(g.get("val") or ""),
            "材料状态修饰": str(g.get("mod") or ""),
            "基准": [str(g.get(k) or "") for k in ("dat1", "dat2", "dat3")
                     if str(g.get(k) or "")],
            "公差带修饰": str(g.get("zone_type") or ""),
        }]
    zone = str(g.get("zone_type") or "")
    zone_text = {"CZ": "CZ（组合公差带）", "SZ": "SZ（独立公差带）",
                 "冲突": "CZ/SZ冲突（待人工确认）"}.get(zone, "无")
    return {
        "公差框类别": labels.get(ftype, ftype),
        "公差框类别代码": ftype,
        "类别判定依据": str(g.get("frame_reason") or ""),
        "公差框组ID": str(g.get("group_id") or g.get("_source_group") or ""),
        "框内顺序": int(g.get("frame_order") or g.get("_frame_order") or 1),
        "框格总数": int(g.get("frame_count") or len(structure) or 1),
        "本层原始符号": _raw_frame_sym(g),
        "符号来源": str(g.get("symbol_source") or "本层"),
        "同组完整结构": structure,
        "公差带修饰": zone_text,
        "CZ_SZ说明": "CZ/SZ仅是公差带修饰，不是公差框类别",
    }


def _ai_tolerance_checklist_from_analysis(rows):
    """只从最终“尺寸/公差”表生成 AI 清单；该表是尺寸相关窗口的唯一数据源。"""
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        if str(row.get("kind") or "").startswith("粗糙度"):
            out.append({
                "序号": len(out) + 1, "类型": "粗糙度", "要求": content,
                "常规能否满足": str(row.get("capability") or ""),
                "备注": str(row.get("note") or "")[:80],
                "显示名称": "粗糙度" + content,
            })
            continue
        tol = str(row.get("tol") or "").strip()
        if tol and tol not in ("无", "未注", "基本尺寸", "—", "-"):
            out.append({
                "序号": len(out) + 1, "类型": "尺寸公差", "尺寸": content,
                "值_mm": row.get("nominal_mm"), "公差": tol,
                "IT": str(row.get("it") or ""), "备注": str(row.get("note") or "")[:80],
                "视图": str(row.get("view") or ""),
                "显示名称": "尺寸%s（公差%s）" % (content, tol),
            })
        for g in (row.get("gtols") or []):
            if not isinstance(g, dict):
                continue
            sym = str(g.get("effective_sym") or g.get("sym") or "")
            val = str(g.get("val") or "")
            dat = " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                        str(g.get("dat3") or "")) if x)
            label = "%s %s%s" % (_gtol_human_name(sym), sym, val)
            if dat:
                label += " |" + dat.replace(" ", " |")
            _src = "人工指定" if (str(row.get("is_manual") or "") == "1"
                                  or str(g.get("source") or "") == "manual"
                                  or "人工" in str(g.get("reason") or "")) else "图纸关联"
            _feat, _zone, _phiuse, _phiok = _phi_judgement(sym, val, content)
            _explicit_feat = str(g.get("measured_feature") or "").strip()
            if _explicit_feat and _explicit_feat not in ("未指定", "未判定", "（未指定）"):
                _feat = _explicit_feat
            if sym in ("▱", "—", "○", "⌭"):
                # 直线度可以控制轴线，也可以控制表面素线；人工指定永远优先。
                if not _explicit_feat or _explicit_feat in ("未指定", "未判定", "（未指定）"):
                    _feat = ("轴心" if sym == "—" and
                             any(mark in val for mark in ("φ", "⌀", "Ø")) and
                             _infer_feature_kind(content) == "轴心" else "表面")
                if sym == "—" and _feat == "轴心" and any(mark in val for mark in ("φ", "⌀", "Ø")):
                    _zone, _phiuse, _phiok = "圆柱", "带φ", "匹配"
                else:
                    _zone, _phiuse, _phiok = "不适用（形状公差）", "-", "不适用"
            _common = {
                "几何公差记录ID": str(g.get("gtol_id") or g.get("raw_id") or ""),
                "被测要素": _feat,
                "指定尺寸": content,
                "归属来源": _src,
                "作用边界状态": ("计入" if g.get("include") is True else
                                   "被涵盖（不重复计入）" if "涵盖" in str(g.get("reason") or "") else
                                   "不计" if g.get("include") is False else "未判定"),
                "作用边界原因": str(g.get("reason") or ""),
            }
            _common.update(_ai_frame_facts(g))
            # 形状公差（平面度/直线度/圆度/圆柱度）只控制形状，不控制位置：
            # 不写成“挂在某尺寸上”，避免 AI 误判它和尺寸公差/轮廓度矛盾
            _form_syms = ("▱", "—", "○", "⌭")
            if sym in _form_syms:
                _item = {
                    "序号": len(out) + 1, "类型": "几何公差", "符号": sym, "值": val,
                    "修正符号": str(g.get("mod") or ""), "基准": dat,
                    "挂到尺寸": content, "公差带形状": _zone, "φ使用": _phiuse, "φ结论": _phiok,
                    "视图": str(row.get("view") or ""),
                    "说明": "形状公差只控制被测要素的形状；指定尺寸表示归属对象，不表示它控制尺寸大小；无基准时不需要基准",
                    "显示名称": "%s %s%s（归属尺寸%s）" % (
                        _gtol_human_name(sym), sym, val, content),
                }
                _item.update(_common)
                # 上面的格式保留可读性，同时明确指出人工归属尺寸。
                _item["显示名称"] = "%s %s%s（归属尺寸%s）" % (
                    _gtol_human_name(sym), sym, val, content)
                out.append(_item)
                continue
            _item = {
                "序号": len(out) + 1, "类型": "几何公差", "符号": sym, "值": val,
                "修正符号": str(g.get("mod") or ""), "基准": dat,
                "挂到尺寸": content, "公差带形状": _zone, "φ使用": _phiuse, "φ结论": _phiok,
                "视图": str(row.get("view") or ""),
                "显示名称": "尺寸%s上的%s" % (content, label),
            }
            _item.update(_common)
            out.append(_item)
    return out


def _ai_unlinked_checklist(items):
    """统一文档里尚未归属的几何公差也必须进入 AI 输入，不能静默丢失。"""
    out = []
    for g in (items or []):
        if not isinstance(g, dict):
            continue
        sym = str(g.get("effective_sym") or g.get("sym") or "")
        val = str(g.get("val") or "")
        feat = str(g.get("measured_feature") or "").strip() or "未判定"
        dat = " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                    str(g.get("dat3") or "")) if x)
        item = {
            "序号": len(out) + 1, "类型": "几何公差", "符号": sym, "值": val,
            "修正符号": str(g.get("mod") or ""), "基准": dat,
            "挂到尺寸": "", "指定尺寸": "（未指定）", "归属来源": "未归属",
            "被测要素": feat, "几何公差记录ID": str(g.get("gtol_id") or g.get("raw_id") or ""),
            "作用边界状态": "计入" if g.get("include") is True else "不计",
            "作用边界原因": str(g.get("reason") or ""),
            "说明": "三表中尚未指定归属，只有这种记录才允许 AI 提示归属待确认",
            "显示名称": "%s %s%s（未指定归属）" % (_gtol_human_name(sym), sym, val),
        }
        item.update(_ai_frame_facts(g))
        out.append(item)
    return out


def _ai_tolerance_checklist(dims, unlinked=None):
    """统一编号的待审核公差清单：尺寸公差 + 几何公差（供 AI 逐条 1:1 审核）。"""
    # 降级路径也先补齐框格事实，不能因为缺少尺寸分析表就让 AI 自行猜类别。
    _apply_frame_types(dims, unlinked)
    out = []
    _special = []
    for _d in (dims or []):
        if not isinstance(_d, dict):
            continue
        try:
            if not tol_missing(_d):
                continue
            _c0, _n0 = dim_display(_d)
            if not _c0:
                continue
            _ta0 = str(_d.get("text_all") or "")
            _is_special = bool(
                _THREAD_RE.search(_c0)
                or _THREAD_RE.search(_ta0)
                or "<HOLE-SINK>" in _ta0
                or "<HOLE-CBORE>" in _ta0
                or _c0.startswith(("R", "C", "□", "S⌀", "槽"))
                or int(_d.get("tol_type") or 0) == 1
                or "参考尺寸" in (_n0 or ""))
            if _is_special:
                _special.append((_d, _c0, _n0))
        except Exception:
            continue
    for _d, _c0, _n0 in _special[:40]:
        out.append({
            "序号": len(out) + 1,
            "类型": "特征/符号",
            "尺寸": _c0,
            "值_mm": _d.get("value_mm"),
            "公差": fmt_tol_display(_d),
            "备注": (_n0 or "")[:80],
            "视图": str(_d.get("view") or ""),
        })
    for d in (dims or []):
        if not isinstance(d, dict) or tol_missing(d):
            continue
        content = str(d.get("text") or "").strip()
        if not content:
            try:
                content = fmt_dim(d) or ""
            except Exception:
                content = ""
        try:
            _c0, _n0 = dim_display(d)
            if _c0:
                content = _c0
        except Exception:
            pass
        _ta0 = str(d.get("text_all") or "")
        _mpre0 = re.match(r"^\|*(\d+)\s*-", _ta0)
        if _mpre0 and not content.startswith(_mpre0.group(1) + "-"):
            content = _mpre0.group(1) + "-" + content
        if "<MOD-DIAM>" in _ta0 and ("⌀" not in content) and not content.startswith(("φ", "Ø")):
            content = "⌀" + content
        if "<MOD-EP>" in _ta0 and "Ⓔ" not in content:
            content += " Ⓔ"
        _note_disp = ""
        try:
            _note_disp = dim_display(d)[1] or ""
        except Exception:
            _note_disp = ""
        out.append({
            "序号": len(out) + 1,
            "类型": "尺寸公差",
            "尺寸": content,
            "值_mm": d.get("value_mm"),
            "公差": fmt_tol_display(d),
            "备注": _note_disp[:80],
            "几何公差": str(d.get("_gtol_note") or ""),
            "修正符号": ("Ⓔ 包容要求" if "<MOD-EP>" in str(d.get("text_all") or "")
                        else ("Ⓜ 最大实体" if "<MOD-MMC>" in str(d.get("text_all") or "")
                              else ("Ⓛ 最小实体" if "<MOD-LMC>" in str(d.get("text_all") or "") else ""))),
            "视图": str(d.get("view") or ""),
        })
    for u in (unlinked or []):
        if not isinstance(u, dict):
            continue
        item = {
            "序号": len(out) + 1,
            "类型": "几何公差",
            "符号": str(u.get("effective_sym") or u.get("sym") or ""),
            "值": str(u.get("val") or ""),
            "基准": " ".join(x for x in (str(u.get("dat1") or ""), str(u.get("dat2") or ""),
                                          str(u.get("dat3") or "")) if x),
            "挂到尺寸": "（未关联）",
            "指定尺寸": "（未指定）", "归属来源": "未归属",
            "被测要素": str(u.get("measured_feature") or "未判定"),
            "几何公差记录ID": str(u.get("gtol_id") or u.get("raw_id") or ""),
            "作用边界状态": "计入" if u.get("include") is True else "不计",
            "作用边界原因": str(u.get("reason") or ""),
            "视图": str(u.get("view") or ""),
        }
        item.update(_ai_frame_facts(u))
        out.append(item)
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        for g in (d.get("gtols") or []):
            if not isinstance(g, dict):
                continue
            _target = (dim_display(d)[0] or str(d.get("text") or d.get("value_mm") or ""))
            item = {
                "序号": len(out) + 1,
                "类型": "几何公差",
                "符号": str(g.get("effective_sym") or g.get("sym") or ""),
                "值": str(g.get("val") or ""),
                "基准": " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                              str(g.get("dat3") or "")) if x),
                "挂到尺寸": _target, "指定尺寸": _target,
                "归属来源": "人工指定" if "人工" in str(g.get("reason") or "") else "图纸关联",
                "被测要素": str(g.get("measured_feature") or _infer_feature_kind(_target) or "未判定"),
                "几何公差记录ID": str(g.get("gtol_id") or g.get("raw_id") or ""),
                "作用边界状态": "计入" if g.get("include") is True else "不计",
                "作用边界原因": str(g.get("reason") or ""),
                "视图": str(d.get("view") or ""),
            }
            item.update(_ai_frame_facts(g))
            out.append(item)
    return out


def _ai_gtol_items(dims, unlinked=None):
    """给 AI 的几何公差清单：带「挂到尺寸」（指定对象）+ 归属方式 + 未关联项。"""
    def _datum(g):
        return " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                     str(g.get("dat3") or "")) if x)

    def _mode(g):
        r = str(g.get("reason") or "")
        return "人工指定" if "人工" in r else "自动关联"

    out = []
    for d in dims or []:
        if not isinstance(d, dict):
            continue
        for g in (d.get("gtols") or []):
            if not isinstance(g, dict):
                continue
            out.append({
                "序号": len(out) + 1,
                "符号": str(g.get("sym") or ""),
                "值": str(g.get("val") or ""),
                "修正符号": str(g.get("mod") or ""),
                "基准": _datum(g),
                "挂到尺寸": (dim_display(d)[0] or str(d.get("text") or d.get("value_mm") or "")),
                "视图": str(d.get("view") or ""),
                "归属方式": _mode(g),
            })
    for u in (unlinked or []):
        if not isinstance(u, dict):
            continue
        out.append({
            "序号": len(out) + 1,
            "符号": str(u.get("sym") or ""),
            "值": str(u.get("val") or ""),
            "修正符号": str(u.get("mod") or ""),
            "基准": _datum(u),
            "挂到尺寸": "（未关联）",
            "视图": str(u.get("view") or ""),
            "归属方式": "未关联",
        })
    return out


def _ai_dim_rows_from_analysis(rows, limit=20):
    """用尺寸/公差分析结果（含 IT、几何公差、备注）生成给 AI 的尺寸明细。"""
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("kind") or "").startswith("粗糙度"):
            continue
        item = {"尺寸": str(r.get("content") or "")}
        item["公差"] = str(r.get("tol") or "")
        if r.get("it"):
            item["IT"] = str(r.get("it"))
        gs = []
        for g in (r.get("gtols") or []):
            if not isinstance(g, dict):
                continue
            t = str(g.get("sym") or "") + str(g.get("val") or "")
            if g.get("mod"):
                t += str(g.get("mod"))
            dat = " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                        str(g.get("dat3") or "")) if x)
            if dat:
                t += " | " + dat
            gs.append(t)
        if gs:
            item["几何公差"] = "；".join(gs)
        if r.get("note"):
            item["备注"] = str(r.get("note"))
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _ai_dim_rows(dims, limit=20):
    """给 AI 的尺寸明细：尺寸/公差/几何公差（含基准与归属方式）/备注。"""
    out = []
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        try:
            content = str(d.get("text") or "").strip()
            if not content:
                content = fmt_dim(d) or ""
            item = {"尺寸": content}
            try:
                item["公差"] = fmt_tol_display(d)
            except Exception:
                item["公差"] = ""
            gs = []
            for g in (d.get("gtols") or []):
                if not isinstance(g, dict):
                    continue
                t = str(g.get("sym") or "") + str(g.get("val") or "")
                if g.get("mod"):
                    t += str(g.get("mod"))
                dat = " ".join(x for x in (str(g.get("dat1") or ""), str(g.get("dat2") or ""),
                                            str(g.get("dat3") or "")) if x)
                if dat:
                    t += " | " + dat
                mode = "人工指定" if "人工" in str(g.get("reason") or "") else "自动关联"
                gs.append(t + "（" + mode + "）")
            if gs:
                item["几何公差"] = "；".join(gs)
            note = str(d.get("note") or "").strip()
            if note:
                item["备注"] = note
            out.append(item)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _load_manual_rows():
    """界面「右键新增一行」的手工行（JSON 列表），仅本会话。"""
    raw = os.environ.get("QC_MANUAL_ROWS", "").strip()
    if not raw:
        return []
    try:
        lst = json.loads(raw)
    except Exception:
        return []
    out = []
    for m in (lst or []):
        if not isinstance(m, dict):
            continue
        c = str(m.get("content") or "").strip()
        if not c:
            continue
        out.append(m)
    return out


def _load_document_snapshot():
    """读取 C# 统一会话文档。存在时它是三表唯一真源。"""
    raw = os.environ.get("QC_DOCUMENT_SNAPSHOT", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _document_snapshot_rows(dwg_no):
    data = _load_document_snapshot()
    if not data:
        return []
    want = _norm_content(dwg_no)
    got = _norm_content(data.get("dwg_no"))
    if want and got and want != got:
        return []
    out = []
    for source in (data.get("rows") or []):
        if not isinstance(source, dict):
            continue
        row = dict(source)
        row["gtols"] = [dict(g) for g in (source.get("gtols") or []) if isinstance(g, dict)]
        row["gtol"] = row["gtols"][0] if row["gtols"] else None
        out.append(row)
    return out


def _document_snapshot_unlinked():
    data = _load_document_snapshot()
    return [dict(g) for g in (data.get("unlinked_gtols") or []) if isinstance(g, dict)]


def _document_snapshot_datums():
    data = _load_document_snapshot()
    return [dict(d) for d in (data.get("datums") or []) if isinstance(d, dict)]


def _manual_row_dict(m):
    """界面尺寸表快照/手工行 → dim_analysis 行。"""
    c = str(m.get("content") or "").strip()
    kind = str(m.get("kind") or "尺寸特征").strip() or "尺寸特征"
    tol = str(m.get("tol") or "未注").strip() or "未注"
    it = str(m.get("it") or "").strip()
    note = str(m.get("note") or "").strip()
    nominal = 0.0
    try:
        nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", c)
        if nums:
            nominal = float(nums[-1])
    except Exception:
        nominal = 0.0
    gtols = []
    for raw in re.split(r"[；;]+", str(m.get("gtol") or "")):
        raw = raw.strip()
        if not raw:
            continue
        fields = re.split(r"\s*\|\s*", raw)
        main = fields[0].strip()
        mm = re.match(r"^\s*(?P<sym>.*?)(?P<val>[φ⌀]?[0-9]+(?:\.[0-9]+)?)\s*$", main)
        if not mm:
            continue
        sym = str(mm.group("sym") or "").strip()
        val = str(mm.group("val") or "").strip().replace("⌀", "φ")
        mod = ""
        for token in ("Ⓜ", "Ⓛ", "Ⓢ", "Ⓔ"):
            if token in sym:
                mod, sym = token, sym.replace(token, "").strip()
                break
        dat1 = fields[1].strip() if len(fields) > 1 else ""
        dat2 = fields[2].strip() if len(fields) > 2 else ""
        dat3 = fields[3].strip() if len(fields) > 3 else ""
        try:
            value_mm = float(re.sub(r"[^0-9.]", "", val) or 0)
        except Exception:
            value_mm = 0.0
        label = _gtol_human_name(sym) + (" " + val if val else "")
        gtols.append({"sym": sym, "val": val, "mod": mod, "dat1": dat1, "dat2": dat2, "dat3": dat3,
                      "value_mm": value_mm, "label": label,
                      "reason": "尺寸/公差表人工确认", "include": False})
    row = {
        "kind": kind, "content": c, "nominal_mm": round(nominal, 4),
        "tol": tol, "it": (it if it and it != "自动" else "—"),
        "capability": str(m.get("capability") or "手工新增（本会话有效）"),
        "note": note, "view": "尺寸/公差表", "modifier": "", "gtols": gtols,
        "gtol": (gtols[0] if gtols else None),
        "is_manual": "1" if str(m.get("is_manual") or "0") == "1" else "0",
    }
    return row


def _table_snapshot_rows(dwg_no):
    """读取与当前图号一致的完整尺寸表快照；图号不匹配时绝不串图。"""
    want = _norm_content(dwg_no)
    rows = []
    for m in _load_manual_rows():
        if str(m.get("source") or "") != "table":
            continue
        got = _norm_content(m.get("dwg_no") or "")
        if want and got and got != want:
            continue
        rows.append(_manual_row_dict(m))
    return rows




def _migrate_note_text(note):
    """清理历史备注：几何 N 条 / 手工新增 / 几何公差：… / 裸公差文本；保留基准指向、轮廓、孔组等。"""
    note = str(note or "")
    _sym_re = re.compile(r"^[—▱○⌭⌒⌓∠⊥∥⌖◎⌯↗⌰]\s*")
    segs = []
    for x in note.split("；"):
        x = x.strip()
        if not x:
            continue
        if x.startswith("几何 ") or x.startswith("几何公差"):
            continue
        if x == "手工新增" or x.startswith("手工新增；"):
            continue
        if _sym_re.match(x):
            continue
        if x in ("无基准",):
            continue
        segs.append(x)
    return "；".join(segs)


def _normalize_rows(dim_rows):
    """快照/最终表归一化：字段 trim、几何公差去重、备注迁移、粗糙度行去重。"""
    _seen_rough = set()
    _kept = []
    for r in (dim_rows or []):
        if isinstance(r, dict) and str(r.get("kind") or "").startswith("粗糙度"):
            _k = (str(r.get("kind") or ""), str(r.get("content") or ""), str(r.get("note") or ""))
            if _k in _seen_rough:
                continue
            _seen_rough.add(_k)
        _kept.append(r)
    dim_rows = _kept
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        _tol_txt = str(r.get("tol") or "").strip()
        if (_tol_txt in ("", "未注", "—", "-", "上 0 下 0", "上 +0 下 -0") or
                re.fullmatch(r"[±+\-]?0+(?:\.0+)?(?:\s*/\s*[±+\-]?0+(?:\.0+)?)?", _tol_txt)):
            r["tol"] = "无"
        r["note"] = _migrate_note_text(r.get("note"))
        gs = r.get("gtols")
        if isinstance(gs, list):
            out, seen = [], set()
            for g in gs:
                if not isinstance(g, dict):
                    continue
                g = dict(g)
                for k in ("sym", "val", "mod", "dat1", "dat2", "dat3", "label"):
                    if k in g:
                        g[k] = str(g.get(k) or "").strip()
                for k in ("dat1", "dat2", "dat3"):
                    g[k] = str(g.get(k) or "").upper()
                # 统一文档用稳定 ID 区分同内容的两条公差；旧输入才退回内容签名。
                stable_id = str(g.get("gtol_id") or g.get("raw_id") or "").strip()
                key = (("id", stable_id) if stable_id else
                       ("sig", g.get("sym"), g.get("val"), g.get("mod"),
                        g.get("dat1"), g.get("dat2"), g.get("dat3")))
                if key in seen:
                    continue
                seen.add(key)
                out.append(g)
            r["gtols"] = out
            r["gtol"] = out[0] if out else None
    return dim_rows


def _diag_dump(payload):
    try:
        d = os.environ.get("QC_DIAG_DIR", "").strip() or os.path.join(
            os.environ.get("TEMP") or "/tmp", "SwQcAddinDiag")
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, "diag_check_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        files = sorted([os.path.join(d, x) for x in os.listdir(d) if x.startswith("diag_check_") and x.endswith(".json")])
        for old in files[:-5]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass





_SYM_CN = {
    "⌖": "位置度", "◎": "同轴度", "⌓": "面轮廓度", "⌒": "线轮廓度",
    "▱": "平面度", "—": "直线度", "○": "圆度", "⌭": "圆柱度",
    "⊥": "垂直度", "∥": "平行度", "∠": "倾斜度", "⌯": "对称度",
    "↗": "圆跳动", "⌰": "全跳动",
}


def _frame_group_key(g):
    """只使用真实身份分组；绝不能用“符号+数值”把两个独立标注合并。"""
    return str(g.get("_source_group") or g.get("group_id") or g.get("raw_id") or "").strip()


def classify_tolerance_frames(frames):
    """按用户确认的标准区分框格类型，并独立读取 CZ/SZ 公差带修饰。

    frames: [dict(sym, val, dat1, dat2, mod, _frame_order, _frame_count)]
    返回 (frame_type, zone_type, reason)
    """
    fs = sorted([f for f in (frames or []) if isinstance(f, dict)],
                key=lambda f: int(f.get("frame_order") or f.get("_frame_order") or 1))
    n = len(fs)
    if n == 0:
        return "", "", ""
    syms = [_raw_frame_sym(f) for f in fs]
    has = [s for s in syms if s]
    suffixes = []
    for f in fs:
        z = str(f.get("zone_type") or "").strip().upper()
        if z not in ("CZ", "SZ", "冲突"):
            txt = " ".join(str(f.get(k) or "") for k in
                           ("val", "mod", "dat1", "dat2", "dat3", "raw", "text"))
            z = _zone_suffix_from_text(txt)
        if z and z not in suffixes:
            suffixes.append(z)
    if "冲突" in suffixes or ("CZ" in suffixes and "SZ" in suffixes):
        suffix = "冲突"
    else:
        suffix = suffixes[0] if suffixes else ""

    if n == 1:
        return "独立", suffix, "独立公差（单框）：框数=1"
    # 复合框必须是“只有一个符号格 + 至少两层”，且符号位于第一层；
    # 不能把部分识别丢失的多框数据冒充复合框。
    if len(has) == 1 and syms[0]:
        return "复合", suffix, "复合公差框格：仅一个几何符号，框数≥2，下层无独立符号格"
    if len(has) == n:
        if len(set(has)) == 1:
            return "组合", suffix, "组合公差框格（多单段）：多个完整框，几何符号全部相同"
        return "独立", suffix, "独立公差（多框不同项目）：每框有独立几何符号，且符号不全相同"
    return "待确认", suffix, "框格结构不完整：符号格数量既不等于1，也不等于框数"


def _frame_type_note(ftype, reason, zone):
    labels = {
        "独立": "独立公差",
        "组合": "组合公差",
        "复合": "复合公差",
        "待确认": "公差框待确认",
    }
    # 尺寸表备注只给人看简明结论；理由、框数、同组结构保留在每条 gtol 的
    # frame_reason/frame_count/frame_structure 中，完整交给 AI，不能堆进备注。
    note = labels.get(ftype, ftype or "公差框待确认")
    if zone == "CZ":
        note += "；CZ组合公差带"
    elif zone == "SZ":
        note += "；SZ独立公差带"
    elif zone == "冲突":
        note += "；CZ/SZ公差带冲突"
    return note


def _frame_structure(fs, ftype, zone):
    ordered = sorted(fs, key=lambda f: int(f.get("frame_order") or f.get("_frame_order") or 1))
    inherited = next((_raw_frame_sym(f) for f in ordered if _raw_frame_sym(f)), "")
    structure = []
    for index, f in enumerate(ordered, 1):
        raw_sym = _raw_frame_sym(f)
        effective = inherited if ftype == "复合" and not raw_sym else raw_sym
        f["effective_sym"] = effective
        f["symbol_source"] = ("继承上层（复合公差）" if effective and not raw_sym else
                              "本层" if raw_sym else "未识别")
        f["frame_count"] = len(ordered)
        structure.append({
            "层序": int(f.get("frame_order") or f.get("_frame_order") or index),
            "本层符号": raw_sym,
            "有效符号": effective,
            "符号来源": f["symbol_source"],
            "公差值": str(f.get("val") or ""),
            "材料状态修饰": str(f.get("mod") or ""),
            "基准": [str(f.get(k) or "") for k in ("dat1", "dat2", "dat3")
                     if str(f.get(k) or "")],
            "公差带修饰": str(f.get("zone_type") or zone or ""),
        })
    return structure


def _strip_old_frame_notes(note):
    """刷新时移除旧分类结论，避免错误分类永久残留。"""
    parts = [p.strip() for p in re.split(r"[；;]", str(note or "")) if p.strip()]
    system_labels = {"独立公差", "组合公差", "复合公差", "公差框待确认",
                     "CZ组合公差带", "SZ独立公差带", "CZ/SZ公差带冲突"}
    return "；".join(p for p in parts if not (
        p.startswith("公差框类型：") or p.startswith("公差带：") or
        p.startswith("公差带修饰：") or p in system_labels))


def _apply_frame_types(rows, unlinked=None):
    groups = {}
    order = []
    row_members = {}
    unique_no = 0
    for row_index, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        for gtol_index, g in enumerate(r.get("gtols") or []):
            if not isinstance(g, dict):
                continue
            unique_no += 1
            k = _frame_group_key(g) or "ATTACHED-%d-%d-%d" % (row_index, gtol_index, unique_no)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(g)
            row_members.setdefault(id(r), []).append(g)
    for item_index, u in enumerate(unlinked or []):
        if not isinstance(u, dict):
            continue
        unique_no += 1
        k = _frame_group_key(u) or "UNLINKED-%d-%d" % (item_index, unique_no)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(u)

    for k in order:
        fs = groups[k]
        ftype, zone, reason = classify_tolerance_frames(fs)
        note = _frame_type_note(ftype, reason, zone)
        structure = _frame_structure(fs, ftype, zone)
        for f in fs:
            f["frame_type"] = ftype
            f["zone_type"] = zone
            f["frame_reason"] = reason
            f["frame_type_note"] = note
            f["frame_structure"] = structure
    # 尺寸/公差表是唯一主数据源：每次刷新都用本次结构化结果替换旧分类备注。
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        notes = []
        for g in (r.get("gtols") or []):
            nt = str(g.get("frame_type_note") or "")
            if nt and nt not in notes:
                notes.append(nt)
        base = _strip_old_frame_notes(r.get("note"))
        add = "；".join(notes)
        r["note"] = (base + "；" + add) if base and add else (base or add)
    return rows

def _tag_gtols_meta(rows, unlinked=None):
    """给最终几何公差写入 raw_id / group_id / frame_order / measured_feature。
    物理标注分组：同一尺寸行内、位置(_x,_y)相同(±1mm)的多个框格视为同组；组号不代表复合。
    """
    n = 0
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        gs = r.get("gtols") or []
        if not gs:
            continue
        content = str(r.get("content") or "")
        feat = "轴心" if ("⌀" in content or "φ" in content or "Ø" in content) else "平面"
        if str(r.get("kind") or "").startswith("粗糙度"):
            feat = "表面"
        groups = {}
        for g in gs:
            if not isinstance(g, dict):
                continue
            existing_group = str(g.get("group_id") or "").strip()
            source_group = str(g.get("_source_group") or "").strip()
            if existing_group:
                key = ("existing", existing_group)
            elif source_group:
                key = ("source", source_group)
            else:
                try:
                    key = ("position", round(float(g.get("_x") or 0), 0), round(float(g.get("_y") or 0), 0))
                except Exception:
                    key = ("position", 0.0, 0.0)
            groups.setdefault(key, []).append(g)
        for group_key, lst in groups.items():
            lst.sort(key=lambda x: int(x.get("frame_order") or x.get("_frame_order") or 1))
            frame_count = max([int(x.get("_frame_count") or 1) for x in lst] or [1])
            gid = ""
            if group_key[0] == "existing":
                gid = str(group_key[1])
            elif frame_count > 1 or len(lst) > 1:
                if group_key[0] == "source":
                    gid = str(group_key[1])
                else:
                    # deterministic fallback; Python hash() is deliberately randomized per process
                    token = (content + "|" + "|".join(str(x) for x in group_key[1:])).encode("utf-8")
                    gid = "GROUP-" + hashlib.sha1(token).hexdigest()[:10].upper()
            for i, g in enumerate(lst):
                n += 1
                g["raw_id"] = "GDT-" + str(n)
                g["group_id"] = gid
                g["frame_order"] = int(g.get("frame_order") or g.get("_frame_order") or (i + 1))
                # 复合公差下层没有独立符号格，应继承上层符号用于被测要素判定；
                # 原始 sym 仍保留为空，供框格类型重建，不能覆盖。
                _sym_f = str(g.get("effective_sym") or g.get("sym") or "")
                _explicit_feature = str(g.get("measured_feature") or "").strip()
                if _explicit_feature and _explicit_feature not in ("未指定", "未判定", "（未指定）"):
                    # 人工确认值是主数据，后续重算不能覆盖。
                    g["measured_feature"] = _explicit_feature
                else:
                    _val_f = str(g.get("val") or "")
                    _mod_f = str(g.get("mod") or "").strip()
                    _straight_axis = (_sym_f == "—" and feat == "轴心" and
                                      (any(mark in _val_f for mark in ("φ", "⌀", "Ø")) or
                                       _mod_f in ("Ⓜ", "Ⓛ")))
                    if _straight_axis:
                        # 直径公差带/MMC/LMC 的直线度控制尺寸要素的导出中线（界面称轴心）。
                        g["measured_feature"] = "轴心"
                    elif _sym_f in ("⌓", "⌒", "▱", "—", "○", "⌭"):
                        g["measured_feature"] = "表面"
                    else:
                        g["measured_feature"] = feat
    for u in (unlinked or []):
        if not isinstance(u, dict):
            continue
        n += 1
        if not u.get("raw_id"):
            u["raw_id"] = "GDT-" + str(n)
        _existing_gid = str(u.get("group_id") or "").strip()
        _source_gid = str(u.get("_source_group") or "").strip()
        _frame_count = int(u.get("_frame_count") or 1)
        u["group_id"] = _existing_gid or (_source_gid if _frame_count > 1 else "")
        u["frame_order"] = int(u.get("frame_order") or u.get("_frame_order") or 1)
        if not str(u.get("measured_feature") or "").strip():
            u["measured_feature"] = ""
    return rows

def _prepare_analysis_rows(dim_rows, dims, dwg_no):
    """生成唯一的最终尺寸表；人工编辑快照存在时以快照为准。"""
    document_rows = _document_snapshot_rows(dwg_no)
    if document_rows:
        return _normalize_rows(document_rows)
    rows = fill_mod_gtol(dim_rows, dims)
    try:
        _apply_memory_to_rows(rows, dwg_no)
    except Exception:
        pass
    try:
        _dedupe_row_gtols(rows)
    except Exception:
        pass
    try:
        _dedupe_gtols_global(rows, dwg_no)
    except Exception:
        pass
    try:
        _strip_orphan_gtol_notes(rows)
    except Exception:
        pass
    try:
        _mark_basic_by_gtol(rows)
    except Exception:
        pass
    try:
        _sync_gtol_notes(rows, _load_datum_map(dwg_no))
    except Exception:
        pass

    snapshot = _table_snapshot_rows(dwg_no)
    if snapshot:
        # 表格是唯一真源：保留用户看到并确认过的顺序、尺寸、公差、几何公差与备注。
        rows = _normalize_rows(snapshot)
    else:
        # 兼容旧版只传“右键新增行”的数据。
        for m in _load_manual_rows():
            if str(m.get("source") or "") != "table":
                rows.append(_manual_row_dict(m))
    return _normalize_rows(rows)


def _load_excluded_rows():
    """读界面“右键删除”的排除行（仅本次插件会话；JSON: [kind|content|tol, ...]）。"""
    raw = os.environ.get("QC_DIM_EXCLUDE", "").strip()
    if not raw:
        return [], set()
    try:
        lst = json.loads(raw)
    except Exception:
        return [], set()
    fps, contents = [], set()
    for it in (lst or []):
        t = str(it or "")
        if not t:
            continue
        fps.append(t)
        parts = t.split("|")
        if len(parts) >= 2 and parts[1]:
            contents.add(_norm_content(parts[1]))
    return fps, contents


def _feature_counts(dims):
    """统计螺纹与直径：明细 + 数量（含 2-/4- 前缀的孔数）。"""
    threads, diams = [], []
    for d in (dims or []):
        if not isinstance(d, dict):
            continue
        try:
            content, _note = dim_display(d)
        except Exception:
            continue
        if not content:
            continue
        ta = str(d.get("text_all") or "")
        mpre = re.match(r"^(\d+)-", content)
        qty = int(mpre.group(1)) if mpre else 1
        body = re.sub(r"^\\d+-", "", content).strip()
        if re.match(r"^(M|G|NPT|NPTF|BSP|BSPT|Rc|Rp|R|Tr|UNC|UNF|UNEF)", body, re.I):
            threads.append({"规格": body, "数量": qty, "视图": str(d.get("view") or "")})
        elif content.startswith(("⌀", "φ", "Ø")) or "<MOD-DIAM>" in ta:
            diams.append({"尺寸": body, "数量": qty, "视图": str(d.get("view") or "")})
    return {
        "螺纹明细": threads,
        "螺纹处数": len(threads),
        "螺纹总数": sum(t["数量"] for t in threads),
        "直径明细": diams,
        "直径尺寸数": len(diams),
        "直径总数": sum(x["数量"] for x in diams),
    }


_SHAPE_SYMS = ("—", "▱", "○", "⌭", "⌒", "⌓")


_SHEET_MATERIALS = (
    "SPCC", "SPCD", "SPCE", "SECC", "SGCC", "SGHC", "DC01", "DC03", "DC04", "DC05",
    "ST12", "ST13", "ST14", "Q195", "Q235", "Q345", "Q355",
    "SUS304", "SUS301", "SUS316", "SUS201", "SUS430", "304", "316", "201", "430",
    "5052", "6061", "6063", "1060", "3003",
    "冷板", "热轧", "热轧板", "冷轧板", "镀锌板", "镀锌", "铝板", "不锈钢板", "钢板",
    "酸洗板", "彩涂板", "马口铁", "电解板",
)
_SHEET_HINTS = ("钣金", "板金", "冲压", "折弯", "激光切割", "数控冲", "焊接件")


def _is_sheet_material(mat):
    """材料是否属于钣金板料（SPCC/SECC/SGCC/镀锌/冷热轧/不锈钢板/铝板…）"""
    raw = str(mat or "")
    m = raw.upper()
    if not m:
        return False
    # 明确的板料牌号
    for k in ("SPCC", "SPCD", "SPCE", "SECC", "SGCC", "SGHC", "DC01", "DC03",
              "DC04", "DC05", "ST12", "ST13", "ST14", "Q195", "Q235", "Q345", "Q355",
              "SUS304", "SUS301", "SUS316", "SUS201", "SUS430"):
        if k in m:
            return True
    # 板料中文名
    for k in ("冷板", "热轧", "热轧板", "冷轧板", "镀锌板", "镀锌", "铝板",
              "不锈钢板", "钢板", "酸洗板", "彩涂板", "马口铁", "电解板"):
        if k in raw:
            return True
    # 常见板材牌号（304/5052 直接算；6061 等需带“板”）
    if re.search(r"(?<![0-9A-Z])(304|316|201|430|5052)(?![0-9A-Z])", m):
        return True
    if "板" in raw and re.search(r"6061|6063|1060|3003|2A12|7075|H62|H65|T2|T3", m):
        return True
    return False



_PLASTIC_RE = re.compile(
    r"(?<![A-Z])(POM|ABS|PC|PMMA|PEEK|PA\d*|PP|PE|PVC|TPU|TPE|PPS|PSU|PEI)(?![A-Z])"
    r"|聚甲醛|尼龙|塑料|聚碳|亚克力|聚丙烯|聚乙烯", re.I)


def _ai_process_block(header, notes):
    """工艺类别判定（材料 + 引用标准 + 类别）。

    规则：
      1) 材料是塑料 且 引用 GB/T 47067-2026 / GB/T 14486 → 注塑件
      2) 引用注塑标准但材料不是塑料 → 不判注塑，按后续规则，并提示“引用可能错误”
      3) 材料属于钣金板料（SPCC/SECC/镀锌/冷热轧/不锈钢板/铝板…）→ 钣金件
      4) 引用 GB/T 15055 或钣金类用语 → 钣金件
      5) 引用 GB/T 1804 / GB/T 1184 → 机加件
      6) 类别 注塑/钣金/机加 → 对应
      7) 默认机加件
    """
    cat = str(header.get("类别") or "").strip()
    mat = str(header.get("材料") or "").strip()
    txt = "\n".join(str(x) for x in (notes or ""))
    reasons = ["标题栏类别=%s" % (cat or "未填"), "材料=%s" % (mat or "未填")]

    def _has(*keys):
        return any(k in txt for k in keys)

    inj_std = _has("47067", "14486", "GB/T 14486", "GBT 14486")
    mach = _has("1804", "1184", "GB/T 1804", "GB/T 1184")
    sheet_std = _has("15055")
    plastic_mat = bool(_PLASTIC_RE.search(mat))
    sheet_mat = _is_sheet_material(mat)
    sheet_cat = ("钣金" in cat or "板金" in cat or "冲压" in cat or "折弯" in cat)
    sheet_hint = any(h in (txt + cat) for h in _SHEET_HINTS)
    conflict = ""

    if inj_std and plastic_mat:
        proc = "注塑件"
        reasons.append("材料为塑料 + 技术要求引用注塑类标准（GB/T 47067-2026 / GB/T 14486）→ 按注塑工艺审")
        reasons.append("按注塑件审：需考虑脱模斜度、壁厚、缩水率、模塑公差（GB/T 14486）")
    else:
        if inj_std and not plastic_mat:
            conflict = ("技术要求引用注塑类标准（GB/T 47067/14486），但材料「%s」不是塑料，"
                        "引用可能有误，请核实" % (mat or "未填"))
            reasons.append(conflict)
        if sheet_mat:
            proc = "钣金件"
            reasons.append("材料「%s」属于钣金板料 → 按钣金工艺审" % mat)
            if mach:
                reasons.append("技术要求虽引用 GB/T 1804/1184，但板料件仍按钣金工艺")
        elif sheet_std or sheet_cat or sheet_hint:
            proc = "钣金件"
            if sheet_std:
                reasons.append("技术要求引用 GB/T 15055（钣金）→ 按钣金工艺审")
            if sheet_cat:
                reasons.append("标题栏类别为钣金/冲压件")
            if (not sheet_std) and (not sheet_cat) and sheet_hint:
                reasons.append("技术要求出现钣金工艺用语（冲压/折弯/激光切割等）→ 按钣金工艺审")
        elif mach:
            proc = "机加件"
            reasons.append("技术要求引用 GB/T 1804/1184（机加通用标准）→ 按机加工艺审")
        elif plastic_mat and ("塑料" in cat or "注塑" in cat):
            proc = "注塑件"
            reasons.append("材料为塑料 + 标题栏类别为塑料/注塑件（未引用注塑标准）")
        elif "机加" in cat:
            proc = "机加件"
            reasons.append("标题栏类别为机加件")
        else:
            proc = "机加件"
            reasons.append("未读到工艺标准/板料特征，默认按机加件")

    if proc != "注塑件" and plastic_mat:
        reasons.append("材料是塑料但按%s工艺加工：**不要**按注塑件要求（脱模斜度/缩水/模塑公差）审" % proc)
    res = {"工艺类别": proc, "判定依据": reasons,
           "说明": "以材料 + 引用标准 + 类别联合判定为准，禁止仅凭材料是塑料就判定为注塑件"}
    if conflict:
        res["需提示 AI"] = conflict
    return res


def _ai_shape_block(data, header, dims, dim_summary, dim_rows=None):
    """给 AI 的零件形状概况（3D 包围盒 + 视图 + 主要尺寸）。"""
    info = {}
    try:
        box = data.get("part_box") or []
        if isinstance(box, list) and len(box) >= 6:
            x = abs(float(box[3]) - float(box[0]))
            y = abs(float(box[4]) - float(box[1]))
            z = abs(float(box[5]) - float(box[2]))
            info["外形尺寸_mm"] = "%.2f × %.2f × %.2f（长×宽×高，来自 3D 包围盒）" % (x, y, z)
    except Exception:
        pass
    try:
        info["是否钣金件"] = bool(data.get("is_sheet_metal"))
        th = data.get("thickness_mm") or 0
        if th:
            info["板厚_mm"] = round(float(th), 3)
    except Exception:
        pass
    views = []
    try:
        for d in (dims or []):
            v = str(d.get("view") or "").strip()
            if v and v not in views:
                views.append(v)
    except Exception:
        pass
    if views:
        info["视图（含标注）"] = views
    try:
        info["主要尺寸"] = _ai_dim_rows(dims, 20)
    except Exception:
        if dim_summary:
            info["主要尺寸"] = dim_summary[:10]
    if header.get("名称"):
        info["零件名称"] = header.get("名称")
    return info




def _datum_target_text(dm, letter, rows=None):
    """基准指向文字：基准 A → ⌀42 平面（人工指定）；没指向 → 待指定。"""
    letter = str(letter or "").strip()
    if not letter:
        return ""
    raw = str((dm or {}).get(letter + "#near") or "").strip()
    manual = str((dm or {}).get(letter + "#src") or "") == "人工指定" or "人工指定" in raw
    if not raw:
        return "基准 %s → 待指定（请到「基准设置」指定）" % letter
    val = re.sub(r"（人工指定）+", "", raw).strip()
    # 目标如果是纯数值，用表中对应尺寸的内容（如 42 → ⌀42）
    try:
        if rows and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", val):
            _tv = float(val)
            _cand = []
            for _r in rows:
                if not isinstance(_r, dict):
                    continue
                try:
                    _digits = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(_r.get("content") or ""))
                    _rv = float(_r.get("nominal_mm") or 0)
                    if not _digits:
                        continue
                    if abs(float(_digits[-1]) - _tv) <= 1e-6 or abs(abs(_rv) - _tv) <= 1e-6:
                        _c = str(_r.get("content") or "")
                        if _c:
                            _cand.append(_c)
                except Exception:
                    continue
            # 优先取带直径符号的尺寸（42 → ⌀42），避免取到“轮廓 42”
            _dia = [c for c in _cand if c.startswith(("⌀", "φ", "Ø"))]
            if _dia:
                val = _dia[0]
            elif _cand:
                val = _cand[0]
    except Exception:
        pass
    if not re.search(r"平面|表面|底面|端面|轴|孔|中心", val):
        val = val + " 平面"
    src = "（人工指定）" if manual else ""
    return "基准 %s → %s%s" % (letter, val, src)


def _gtol_line_text(g, dm=None, with_datum_target=True, rows=None):
    """一条几何公差的界面文本：⌓ 0.02 |A（基准 A → ⌀42 平面（人工指定））"""
    if not isinstance(g, dict):
        return ""
    sym = str(g.get("sym") or "")
    val = str(g.get("val") or "")
    t = (sym + " " if sym else "") + val
    mod = str(g.get("mod") or "")
    if mod:
        t += mod
    dats = [str(g.get("dat1") or "").strip(), str(g.get("dat2") or "").strip(),
            str(g.get("dat3") or "").strip()]
    for d in dats:
        if d:
            t += " |" + d
    if with_datum_target:
        tg = [_datum_target_text(dm or {}, d, rows=rows) for d in dats if d]
        t += "（" + "；".join(tg) + "）" if tg else "（无基准）"
    return t


def _sync_gtol_notes(dim_rows, dm=None):
    """把每行的“几何公差：…”备注重写为统一模板（符号+值+基准+基准指向）。"""
    for r in (dim_rows or []):
        if not isinstance(r, dict):
            continue
        gs = r.get("gtols")
        if not isinstance(gs, list) or not gs:
            continue
        parts = [_gtol_line_text(g, dm, rows=dim_rows) for g in gs if isinstance(g, dict)]
        parts = [p for p in parts if p]
        if not parts:
            continue
        # 备注不再重复几何公差本身（几何公差列已有）；只保留“基准指向”
        note = str(r.get("note") or "")
        _drop = ("对称公差", "公差值无法解析", "±0")
        note = note.split("几何公差：")[0].strip("；")
        _sym_re = re.compile(r"^[—▱○⌭⌒⌓∠⊥∥⌖◎⌯↗⌰]\s*")
        segs = []
        for x in note.split("；"):
            x = x.strip()
            if not x:
                continue
            if any(d in x for d in _drop):
                continue
            if x.startswith("几何 "):          # 旧的“几何 N 条…”计数
                continue
            if x.startswith("基准 "):          # 旧的基准指向段，重建
                continue
            if _sym_re.match(x):               # 裸几何公差文本
                continue
            segs.append(x)
        _dats = []
        for g in gs:
            if not isinstance(g, dict):
                continue
            for d in (str(g.get("dat1") or "").strip(), str(g.get("dat2") or "").strip(),
                      str(g.get("dat3") or "").strip()):
                if d and d not in _dats:
                    _dats.append(d)
        try:
            for d in _dats:
                segs.append(_datum_target_text(dm or {}, d, rows=dim_rows))
        except Exception:
            pass
        r["note"] = "；".join([x for x in segs if x])
    return dim_rows

def _load_datum_map(dwg_no):
    """读人工指定的基准要素类型：{图号: {A: 平面, B: 轴心}}"""
    path = os.environ.get("QC_DATUM_MAP", "").strip()
    if not path or not dwg_no or not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8-sig"))
    except Exception:
        return {}
    node = (data or {}).get(dwg_no) or {}
    if not isinstance(node, dict):
        return {}
    out = {}
    for _k, _v in node.items():
        _sv = str(_v or "")
        out[_k] = re.sub(r"（人工指定）+", "", _sv).strip()
        if str(_k).endswith("#near"):
            out[str(_k)[:-5] + "#src"] = "人工指定"
    return out


def _ai_datum_block(data, dims, unlinked, roughness):
    """给 AI 的基准信息：基准字母、所在视图、被哪些几何公差引用、附近尺寸、形状公差/粗糙度是否定义。"""
    dt = data.get("datums") or []
    # 基准符号（读到的）
    pos = {}
    for d in dt:
        try:
            letter = str(d.get("letter") or "").strip()
            if not letter:
                continue
            pos.setdefault(letter, d)
        except Exception:
            continue
    # 几何公差里引用到的基准字母
    used = {}
    all_g = []
    for d in (dims or []):
        for g in (d.get("gtols") or []):
            if isinstance(g, dict):
                all_g.append((d, g))
    for u in (unlinked or []):
        if isinstance(u, dict):
            all_g.append((None, u))
    for d, g in all_g:
        for key in ("dat1", "dat2", "dat3"):
            raw = str(g.get(key) or "")
            for letter in re.split(r"[\s\-/,]+", raw):
                letter = letter.strip()
                if letter and letter.isalpha() and len(letter) <= 2:
                    used.setdefault(letter.upper(), []).append(g)

    dwg_no = ""
    try:
        dwg_no = str((data.get("props_part") or {}).get("图样代号")
                     or (data.get("props") or {}).get("图样代号") or "").strip()
    except Exception:
        dwg_no = ""
    _dmap = _load_datum_map(dwg_no) if dwg_no else {}
    out = []
    for letter in sorted(set(list(pos.keys()) + list(used.keys()))):
        item = {"基准": letter}
        p = pos.get(letter)
        item["图上基准符号"] = "已标注" if p else "未读到（可能未标注）"
        if p:
            try:
                item["所在视图"] = str(p.get("view") or "")
            except Exception:
                pass
        refs = used.get(letter, [])
        item["被引用次数"] = len(refs)
        if refs:
            item["引用它的几何公差"] = [
                (str(g.get("sym") or "")) + (str(g.get("val") or "")) +
                "".join(" | " + str(g.get(k) or "") for k in ("dat1", "dat2", "dat3") if g.get(k))
                for g in refs]
        # 基准指向要素（优先用 leader 箭头点）
        try:
            tx = float(p.get("target_x") or 0) if p else 0.0
            ty = float(p.get("target_y") or 0) if p else 0.0
            if p and (abs(tx) > 1e-9 or abs(ty) > 1e-9):
                item["指向坐标"] = "(%.1f, %.1f)" % (tx, ty)
                bestd, bestv = 1e18, None
                pv = str(p.get("view") or "")
                for d in (dims or []):
                    if str(d.get("view") or "") != pv:
                        continue
                    dx = float(d.get("x") or 0) - tx
                    dy = float(d.get("y") or 0) - ty
                    dd = (dx * dx + dy * dy) ** 0.5
                    if dd < bestd:
                        bestd, bestv = dd, d
                if bestv is not None and bestd <= 60:
                    ta = str(bestv.get("text_all") or "")
                    is_dia = ("<MOD-DIAM>" in ta) or str(bestv.get("text_all") or "").startswith("⌀")
                    lab = str(bestv.get("text") or bestv.get("value_mm") or "")
                    item["指向要素（推断）"] = (("轴线/中心" if is_dia else "平面/轮廓")
                                          + "（对应 " + ("⌀" if is_dia else "") + lab + "）")
                    item["置信度"] = "推断（距离 %.1fmm）" % bestd
            else:
                item["指向要素（推断）"] = "未读到指向（基准符号可能没有 leader），需人工确认"
        except Exception:
            pass
        # 附近尺寸（推断基准要素所在面）
        if p and dims:
            try:
                px, py = float(p.get("x") or 0), float(p.get("y") or 0)
                pv = str(p.get("view") or "")
                best, bd = None, 1e18
                for d in dims:
                    if str(d.get("view") or "") != pv:
                        continue
                    dx = float(d.get("x") or 0) - px
                    dy = float(d.get("y") or 0) - py
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < bd:
                        best, bd = d, dist
                if best is not None:
                    item["附近尺寸（推断基准面）"] = "%s（距离 %.1f mm）" % (
                        str(best.get("text") or best.get("value_mm") or ""), bd)
            except Exception:
                pass
        # 该基准面是否有形状公差
        shape_gs = []
        for d, g in all_g:
            if str(g.get("sym") or "") in _SHAPE_SYMS:
                shape_gs.append((str(g.get("sym") or "") + str(g.get("val") or "")))
        item["图上形状公差标注"] = shape_gs if shape_gs else "未见（平面度/圆度/圆柱度等）"
        # 粗糙度
        ras = [str(x) for x in (roughness or [])]
        item["图上粗糙度标注"] = ras if ras else "未见"
        if _dmap.get(letter):
            item["人工指定"] = str(_dmap[letter])
            item["指向要素"] = "%s（人工指定）" % str(_dmap[letter])
        _near_manual = _dmap.get(str(letter) + "#near")
        if _near_manual:
            item["关联尺寸（人工）"] = str(_near_manual)
            item["附近尺寸（推断基准面）"] = str(_near_manual) + "（人工指定）"
            item["指向要素"] = (str(item.get("人工指定") or "") + "，对应 " + str(_near_manual)).strip("，")
            item["指认方式"] = "人工指定"
        item["备注"] = "粗糙度/形状公差无法自动对应到该基准面，需人工按图确认"
        out.append(item)
    return out


def _ai_timeouts():
    """返回 (空闲超时秒, 总等待秒)。空闲超时=1分钟无任何响应就中止。"""
    try:
        idle = float(os.environ.get("QC_AI_TIMEOUT", "60") or 60)
    except Exception:
        idle = 60.0
    try:
        total = float(os.environ.get("QC_AI_MAX_WAIT", "180") or 180)
    except Exception:
        total = 180.0
    if idle <= 0:
        idle = 60.0
    if total < idle:
        total = max(idle, 180.0)
    return idle, total


AI_GTOL_RULE_ID = "AI-GTOL-ITEM"
AI_GTOL_CATEGORY = "尺寸/几何公差"


def _merge_geom_items(items):
    """结论完全相同的公差项合并为一条（特征并列），避免同一句话重复多行。"""
    merged = []
    for it in items:
        key = (str(it.get("_match") or ""), str(it.get("suggestion") or ""), str(it.get("severity") or ""))
        hit = None
        for m in merged:
            if m["_key"] == key:
                hit = m
                break
        if hit is not None:
            hit["_features"].append(it.get("_feature") or "")
            hit["_tol"].append(it.get("_tolerance") or "")
        else:
            it = dict(it)
            it["_key"] = key
            it["_features"] = [it.get("_feature") or ""]
            it["_tol"] = [it.get("_tolerance") or ""]
            merged.append(it)
    return merged


def _land_geom_items(raw_items, gtol_items):
    """几何公差逐项：每项都输出（风险/合理性/矛盾），严重级空=提示。"""
    gtol_items = list(gtol_items or [])
    total = len(gtol_items)
    out, dropped = [], 0
    seen = set()
    for pos, it in enumerate(raw_items or []):
        if not isinstance(it, dict):
            dropped += 1
            continue
        no = _coerce_item_no(it.get("no"))
        if no is None or not (1 <= no <= total):
            no = pos + 1
        if not (1 <= no <= total) or no in seen:
            dropped += 1
            continue
        seen.add(no)
        g = gtol_items[no - 1]
        label = ((str(g.get("符号") or "") + str(g.get("值") or "")) +
                 ((" | " + str(g.get("基准"))) if g.get("基准") else ""))
        target = str(g.get("挂到尺寸") or "")
        feature = str(it.get("feature") or "").strip()
        tolerance = str(it.get("tolerance") or "").strip()
        grade = str(it.get("grade") or "").strip()
        matchv = str(it.get("match") or "").strip()
        risk = re.sub(r"\s+", " ", str(it.get("risk") or matchv or "")).strip()
        reasonable = re.sub(r"\s+", " ", str(it.get("reasonable") or "")).strip()
        conflict = re.sub(r"\s+", " ", str(it.get("conflict") or "")).strip()
        sev = _level_to_sev(it.get("level")) if it.get("level") else str(it.get("severity") or "").strip().lower()
        if sev not in AI_TECH_SEVERITIES:
            sev = "info"          # 无问题 → 提示级（计入列表但不计入问题数）
        sugg = str(it.get("suggestion") or "").strip()[:MAX_AI_SUGGESTION_LEN]
        brief = risk or reasonable or conflict or "已核对"
        detail_parts = []
        if risk:
            detail_parts.append("风险：" + risk)
        if reasonable:
            detail_parts.append("合理性：" + reasonable)
        if conflict:
            detail_parts.append("与技术要求/尺寸/材料的一致性：" + conflict)
        _head = " ".join(x for x in (feature, tolerance, ("(" + grade + ")") if grade else "") if x) or ("几何公差 " + label)
        _tmp = {"_feature": _head, "_tolerance": tolerance, "_match": (matchv or risk),
                "_tail": _reason_brief(brief, MAX_AI_TECH_TITLE_LEN)}
        iss = issue("AI-GEOM-ITEM", sev,
                    _head + "：" + _reason_brief(brief, MAX_AI_TECH_TITLE_LEN),
                    "\n".join(detail_parts)[:MAX_AI_DETAIL_LEN], sugg, "AI 逐项判定")
        iss["category"] = "尺寸/几何公差"
        iss["_no"] = no
        for _k2, _v2 in _tmp.items():
            iss[_k2] = _v2
        out.append(iss)
    # 结论相同的合并成一条（特征并列）
    try:
        _merged = _merge_geom_items(out)
        out = _merged
        for m in out:
            feats = "；".join(x for x in m.pop("_features", []) if x)
            m.pop("_tol", None)
            m.pop("_key", None)
            if len(feats) > 1:
                _tail = str(m.get("_tail") or "")
                m["title"] = "尺寸/几何公差 " + feats + ("：" + _tail if _tail else "")
    except Exception:
        pass
    return out, dropped


def _land_gtol_items(raw_items, gtol_items):
    """几何公差部分：问题 → AI-GTOL-ITEM；通过 → passed。返回 (issues, passed, dropped)。"""
    gtol_items = list(gtol_items or [])
    if not gtol_items:
        return [], [], len(list(raw_items or []))
    total = len(gtol_items)
    out, passed, dropped = [], [], 0
    seen = set()
    for pos, it in enumerate(raw_items or []):
        if not isinstance(it, dict):
            dropped += 1
            continue
        no = _coerce_item_no(it.get("no"))
        if no is None or not (1 <= no <= total):
            no = pos + 1
        if not (1 <= no <= total) or no in seen:
            dropped += 1
            continue
        seen.add(no)
        g = gtol_items[no - 1]
        label = ((str(g.get("符号") or "") + str(g.get("值") or "")) +
                 ((" | " + str(g.get("基准"))) if g.get("基准") else ""))
        target = str(g.get("挂到尺寸") or "")
        verdict = str(it.get("verdict") or "").strip()
        reason = re.sub(r"\s+", " ", str(it.get("reason") or "")).strip()
        if verdict == "通过":
            passed.append("尺寸/几何公差 " + label + "（挂" + target + "）：通过（AI）")
        elif verdict == "问题":
            if not reason:
                dropped += 1
                continue
            sev = str(it.get("severity") or "").strip().lower()
            if sev not in AI_TECH_SEVERITIES:
                sev = "major"
            sugg = str(it.get("suggestion") or "").strip()[:MAX_AI_SUGGESTION_LEN]
            if (not sugg) or len(sugg) < 6 or AI_VAGUE_RE.search(sugg):
                sugg = "请核对该几何公差（" + label + "，挂" + target + "）的基准/公差值与尺寸精度是否匹配，必要时补标基准或调整公差。"
            iss = issue(AI_GTOL_RULE_ID, sev,
                        "几何公差 " + label + "：" + _reason_brief(reason, MAX_AI_TECH_TITLE_LEN),
                        reason[:MAX_AI_DETAIL_LEN], sugg, "AI 逐条判定")
            iss["category"] = AI_GTOL_CATEGORY
            out.append(iss)
        else:
            dropped += 1
    return out, passed, dropped


_AI_LAST_META = {}
AI_NEW_CATEGORIES = ("技术要求", "尺寸/几何公差", "材料/工艺")
AI_NEW_STATUS = ("problem", "review_required")
AI_NEW_RISK = ("critical", "major", "minor", "review")


def _cut(sv, n):
    t = str(sv or "").strip()
    return t if len(t) <= n else t[:n]


def _ai_low_limit():
    try:
        v = int(os.environ.get("QC_AI_MAX_LOW_PRIORITY_FINDINGS", "10") or 10)
    except Exception:
        v = 10
    return max(5, min(30, v))


def _ref_text(ref, valid):
    """把引用 ID 转成给人看的短文本。"""
    d = valid.get(str(ref))
    if not d:
        return ""
    if d.get("text"):
        return d["text"]
    if d.get("label"):
        return d["label"]
    return ""


def _humanize_internal_refs(text, valid):
    """内部证据编号可以保留在 JSON 中，但用户可见文案必须换成尺寸/要素名称。"""
    def repl(m):
        return _ref_text(m.group(0), valid) or "图纸对应项目"
    return re.sub(r"(?:TECH|DIM|GDT)-\d+", repl, str(text or ""))


def _datum_chain_from_item(item):
    raw = str((item or {}).get("基准") or "").strip().upper()
    return [x for x in re.split(r"[\s|,;/]+", raw) if x]


def _enrich_ai_checklist(checklist, baseline_settings):
    """将几何公差引用的基准链展开为可独立复核的明确事实，避免只给 AI 一个字母。"""
    datum_map = {}
    for d in (baseline_settings or []):
        if not isinstance(d, dict):
            continue
        letter = str(d.get("基准") or d.get("letter") or "").strip().upper()
        if letter:
            datum_map[letter] = dict(d)
    out = []
    for source in (checklist or []):
        item = dict(source) if isinstance(source, dict) else source
        if not isinstance(item, dict) or str(item.get("类型") or "") != "几何公差":
            out.append(item)
            continue
        chain = _datum_chain_from_item(item)
        expanded, missing = [], []
        for letter in chain:
            d = datum_map.get(letter)
            if d is None:
                missing.append(letter)
                expanded.append({"基准": letter, "要素类型": "未定义", "指向尺寸": "",
                                 "来源": "三表中无对应基准记录", "状态": "缺失"})
            else:
                feature = str(d.get("要素类型") or d.get("feature_type") or "未指定")
                target = str(d.get("指向尺寸") or d.get("size_text") or "")
                expanded.append({"基准": letter, "要素类型": feature, "指向尺寸": target,
                                 "来源": str(d.get("来源") or d.get("source") or ""),
                                 "所在视图": str(d.get("所在视图") or d.get("view") or ""),
                                 "状态": "完整" if feature not in ("", "未指定") else "待确认"})
        item["基准链"] = chain
        item["基准定义展开"] = expanded
        missing_fields = []
        if not str(item.get("符号") or "").strip():
            missing_fields.append("几何公差符号")
        if not str(item.get("值") or "").strip():
            missing_fields.append("几何公差值")
        if str(item.get("被测要素") or "").strip() in ("", "未判定", "未指定", "（未指定）"):
            missing_fields.append("被测要素")
        if str(item.get("归属来源") or "") != "未归属" and not str(item.get("指定尺寸") or "").strip():
            missing_fields.append("指定尺寸")
        if missing:
            missing_fields.append("基准定义:" + ",".join(missing))
        item["数据完整性状态"] = "完整" if not missing_fields else "待确认（" + "；".join(missing_fields) + "）"
        out.append(item)
    return out


def _validate_ai_input(checklist, expected_ids):
    """校验发送给 AI 的结构本身；结构错乱时宁可停止 AI，也不让模型审核错对象。"""
    errors = []
    ids = [str(x.get("id") or "") for x in (checklist or []) if isinstance(x, dict)]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        errors.append("公差清单 ID 缺失或重复")
    if len(expected_ids) != len(set(expected_ids)):
        errors.append("待审核 ID 重复")
    groups = {}
    for item in (checklist or []):
        if not isinstance(item, dict) or str(item.get("类型") or "") != "几何公差":
            continue
        iid = str(item.get("id") or "GDT-?")
        for key in ("符号", "值", "被测要素", "指定尺寸", "归属来源", "作用边界状态",
                    "公差框类别", "类别判定依据", "公差框组ID", "框内顺序", "框格总数",
                    "同组完整结构", "公差带修饰", "CZ_SZ说明"):
            if key not in item:
                errors.append(iid + " 缺字段 " + key)
        chain = item.get("基准链")
        expanded = item.get("基准定义展开")
        if not isinstance(chain, list) or not isinstance(expanded, list) or len(chain) != len(expanded):
            errors.append(iid + " 基准链展开不完整")
        structure = item.get("同组完整结构")
        if not isinstance(structure, list) or not structure:
            errors.append(iid + " 同组完整结构为空")
        try:
            total = int(item.get("框格总数") or 0)
        except Exception:
            total = 0
        if total <= 0 or (isinstance(structure, list) and len(structure) != total):
            errors.append(iid + " 框格总数与同组完整结构不一致")
        gid = str(item.get("公差框组ID") or "").strip()
        if gid:
            try:
                order = int(item.get("框内顺序") or 0)
            except Exception:
                order = 0
            groups.setdefault(gid, []).append((order, str(item.get("公差框类别") or ""),
                                               total, structure))
        elif total != 1:
            errors.append(iid + " 多框记录缺少公差框组ID")
    for gid, members in groups.items():
        orders = [x[0] for x in members]
        seq = sorted(orders)
        if len(seq) != len(set(seq)) or seq != list(range(1, len(seq) + 1)):
            errors.append("公差框组 %s 的框内顺序不连续或重复：%s" % (gid, seq))
        categories = set(x[1] for x in members)
        totals = set(x[2] for x in members)
        if len(categories) != 1 or len(totals) != 1 or (totals and next(iter(totals)) != len(members)):
            errors.append("公差框组 %s 的类别或框数不一致" % gid)
        structures = [json.dumps(x[3], ensure_ascii=False, sort_keys=True) for x in members]
        if len(set(structures)) != 1:
            errors.append("公差框组 %s 的同组完整结构不一致" % gid)
    return errors


def _validate_ai_response_coverage(parsed, checksum, expected_ids):
    """AI 必须证明它审核了完整输入；漏项、串包、截断一律判失败。"""
    if not isinstance(parsed, dict):
        return "AI 返回结构不是对象"
    if str(parsed.get("input_checksum") or "") != str(checksum or ""):
        return "AI 回传的输入校验值不一致，结果可能串包或基于不完整数据"
    reviewed = parsed.get("reviewed_ids")
    if not isinstance(reviewed, list):
        return "AI 未返回逐项审核清单 reviewed_ids"
    reviewed = [str(x or "").strip() for x in reviewed]
    if len(reviewed) != len(set(reviewed)):
        return "AI 的 reviewed_ids 存在重复项"
    want = list(expected_ids or [])
    missing = [x for x in want if x not in reviewed]
    extra = [x for x in reviewed if x not in set(want)]
    if missing or extra:
        return "AI 审核覆盖不完整（漏审：%s；无效：%s）" % (
            ",".join(missing[:12]) or "无", ",".join(extra[:12]) or "无")
    try:
        if int(parsed.get("omitted_count") or 0) != 0:
            return "AI 明确省略了审核发现，结果不完整"
    except Exception:
        return "AI 返回的 omitted_count 无效"
    if not isinstance(parsed.get("findings"), list):
        return "AI 未返回 findings 数组"
    return ""


def _norm_finding(raw, valid):
    """单条 finding 归一化与校验；返回 (finding_dict, truncated_flag) 或 (None, False)。"""
    if not isinstance(raw, dict):
        return None, False
    truncated = False

    def cut(k, n):
        nonlocal truncated
        v = raw.get(k)
        t = _cut(v, n)
        if v is not None and len(str(v)) > n:
            truncated = True
        return t

    category = str(raw.get("category") or "").strip()
    if category not in AI_NEW_CATEGORIES:
        # 接受“尺寸公差”“几何公差”等近似写法
        if "尺寸" in category or "公差" in category or "基准" in category:
            category = "尺寸/几何公差"
        elif "材料" in category or "工艺" in category or "表面" in category:
            category = "材料/工艺"
        else:
            category = "技术要求"
    status = str(raw.get("status") or "").strip()
    if status not in AI_NEW_STATUS:
        status = "problem"
    risk = str(raw.get("risk_level") or "").strip().lower()
    if risk not in AI_NEW_RISK:
        # 非法等级 → 按 review 处理，绝不自动升级为 major
        risk = "review"
        status = "review_required"
    location = str(raw.get("location") or "").strip()
    if location and location != "OVERALL" and location not in valid:
        location = "OVERALL"
    refs = raw.get("evidence_refs")
    refs = refs if isinstance(refs, list) else []
    good_refs = []
    bad_refs = 0
    for r in refs[:3]:
        r = str(r or "").strip()
        if r in valid:
            good_refs.append(r)
        elif r:
            bad_refs += 1
    # 位置本身也作为一条依据
    if location and location != "OVERALL" and location not in good_refs:
        good_refs = [location] + good_refs
    # 防止 AI 把“内容相同但挂到不同尺寸”的独立几何公差误判成重复标注。
    _dup_text = str(raw.get("title") or "") + " " + str(raw.get("risk") or "")
    _grefs = [valid.get(r) for r in good_refs if str(r).startswith("GDT-")]
    _grefs = [x for x in _grefs if isinstance(x, dict)]
    _targets = {str(x.get("target") or "").strip() for x in _grefs if str(x.get("target") or "").strip()}
    if "重复" in _dup_text and len(_grefs) >= 2 and len(_targets) >= 2:
        return None, False
    if not good_refs:
        # 全部依据无效（或没有依据）→ 降级为待人工确认
        status = "review_required"
        risk = "review"
    try:
        conf = float(raw.get("confidence"))
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    title = _humanize_internal_refs(cut("title", 40), valid) or "AI 提示"
    if status == "review_required" and not title.startswith("待人工确认"):
        title = "待人工确认：" + title
        title = title[:40]
    risk_text = _humanize_internal_refs(cut("risk", 120), valid)
    basis = _humanize_internal_refs(cut("standard_basis", 80), valid)
    reasoning = _humanize_internal_refs(cut("reasoning", 120), valid)
    if basis:
        risk_text = (risk_text + "　判定依据：" + basis).strip()
    if reasoning:
        risk_text = (risk_text + "　工程分析：" + reasoning).strip()
    suggestion = _humanize_internal_refs(cut("suggestion", 120), valid)
    verify = _humanize_internal_refs(cut("verification_method", 120), valid)
    if verify:
        suggestion = (suggestion + "；复核：" + verify).strip("；")
    f = {
        "category": category, "location": location or "OVERALL",
        "status": status, "risk_level": risk, "title": title,
        "risk": risk_text, "evidence_refs": good_refs,
        "suggestion": suggestion, "confidence": conf,
        "invalid_refs": bad_refs,
    }
    return f, truncated


def _land_findings(parsed, valid):
    """新格式落地：findings → 现有 Issue（AI-TECH-ITEM / AI-GEOM-ITEM）。

    返回 (issues, summary, meta)。meta: truncated_count / omitted_count / low_limited。
    """
    if not isinstance(parsed, dict):
        return [], "", {}
    summary = _cut(parsed.get("summary"), 80)
    raw = parsed.get("findings")
    raw = raw if isinstance(raw, list) else []
    truncated_n = 0
    norm = []
    for it in raw:
        f, tr = _norm_finding(it, valid)
        if f is None:
            continue
        if tr:
            truncated_n += 1
        norm.append(f)
    # 去重：类别 + 位置 + 标题
    seen, dedup = set(), []
    for f in norm:
        _ti = re.sub(r"\s+", "", f["title"])
        key = (f["category"], f["location"], _ti, f["risk_level"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(f)
    # 完整性优先：不再静默截掉 minor/review；上游覆盖校验也要求 omitted_count=0。
    highs = [f for f in dedup if f["risk_level"] in ("critical", "major")]
    lows = [f for f in dedup if f["risk_level"] not in ("critical", "major")]
    lows.sort(key=lambda x: (-x["confidence"], 0 if x["risk_level"] == "minor" else 1))
    kept_lows = lows
    omitted = 0
    try:
        omitted += max(0, int(parsed.get("omitted_count") or 0))
    except Exception:
        pass
    final = highs + kept_lows
    out = []
    for f in final:
        sev = "info"
        if f["status"] == "review_required" or f["risk_level"] == "review":
            sev = "info"
        elif f["risk_level"] == "critical":
            sev = "critical"
        elif f["risk_level"] == "major":
            sev = "major"
        elif f["risk_level"] == "minor":
            sev = "minor"
        rule = "AI-GEOM-ITEM" if f["category"] == "尺寸/几何公差" else "AI-TECH-ITEM"
        # 依据文本
        refs_txt = "；".join(x for x in (_ref_text(r, valid) for r in f["evidence_refs"]) if x)
        detail = f["risk"]
        if refs_txt:
            detail = (detail + "　依据：" + refs_txt).strip()
        elif f["invalid_refs"]:
            detail = (detail + "　（AI依据未能与图纸数据对应，请人工确认）").strip()
        iss = issue(rule, sev, f["title"], detail, f["suggestion"], "AI语义审核")
        iss["category"] = "尺寸/几何公差" if rule == "AI-GEOM-ITEM" else "技术要求"
        iss["location"] = f["location"]
        iss["evidence"] = refs_txt
        iss["ai_risk_level"] = f["risk_level"]
        iss["ai_confidence"] = round(f["confidence"], 2)
        out.append(iss)
    if not out and not summary:
        summary = "AI审查：未发现有明确依据的语义问题。"
    meta = {"truncated_count": truncated_n, "omitted_count": omitted,
            "low_limited": 0}
    return out, summary, meta


def _is_new_findings(parsed):
    if not isinstance(parsed, dict):
        return False
    fs = parsed.get("findings")
    if not isinstance(fs, list):
        return False
    if "reviewed_ids" in parsed or "input_checksum" in parsed:
        return True
    for it in fs:
        if isinstance(it, dict) and ("status" in it or "risk_level" in it or "evidence_refs" in it):
            return True
    return False


def ai_check(header, notes, dims, dim_summary=None, roughness=None, unlinked_gtols=None, extra=None, roughness_raw=None, local_findings=None):
    """返回 (issues, err, elapsed_sec, summary, passed_items)。

    passed_items：AI 逐条判定为「通过」的技术要求条目（已格式化的 passed 文案），
    由 build_report 直接并入报告「通过项」。
    err: '' 成功 / 'disabled' 未配置 / 'skipped' 本阶段跳过 / 其它=失败原因。
    """
    if os.environ.get("QC_NO_AI") == "1":
        return [], "skipped", 0, "", []
    key = os.environ.get("QC_AI_KEY", "").strip()
    if not key:
        return [], "disabled", 0, "", []

    provider = (os.environ.get("QC_AI_PROVIDER", "") or "").strip().lower()
    # 2026-10-24 兜底：插件没传 provider/model 时，按 base 里的域名反推，
    # 否则会拿 gpt-4o-mini 去请求 DeepSeek → HTTP 400（表现为"AI 没介入"）。
    env_base = (os.environ.get("QC_AI_BASE", "") or "").strip()
    if not provider and env_base:
        bl = env_base.lower()
        for _k in ("deepseek", "qwen", "dashscope", "zhipu", "moonshot", "openai"):
            if _k in bl:
                provider = _k
                break
    dbase, dmodel = PROVIDER_DEFAULTS.get(provider, ("https://api.openai.com/v1", "gpt-4o-mini"))
    base = (env_base or dbase).rstrip("/")
    model = (os.environ.get("QC_AI_MODEL", "") or "").strip() or dmodel
    timeout = float(os.environ.get("QC_AI_TIMEOUT", "90") or 90)

    import urllib.request

    import time as _time
    _t0 = _time.time()
    _hdr = {k: v for k, v in header.items()
            if not k.startswith("_") and k not in ("单重", "重量", "质量")}
    # 2026-10-26：工艺类别（用户可在窗口里改，通过环境变量传进来；否则自动推断）
    _mat = ""
    try:
        _mat = str(header.get("材料") or header.get("Material") or "").strip()
    except Exception:
        _mat = ""
    _proc = (os.environ.get("QC_PROCESS", "") or "").strip()
    if not _proc:
        try:
            _mode = process_mode(_mat, "", "\n".join(str(x) for x in (notes or [])))
            _proc = "注塑" if _mode == "injection" else "机加"
        except Exception:
            _nt2 = chr(10).join(str(x) for x in (notes or []))
            if ("1804" in _nt2) or ("1184" in _nt2) or ("CNC" in _nt2.upper()) or ("车" in _nt2) or ("铣" in _nt2):
                _proc = "机加"
            elif ("注塑" in _nt2) or ("47067" in _nt2) or ("模塑" in _nt2):
                _proc = "注塑"
            elif ("15055" in _nt2) or ("钣金" in _nt2) or ("折弯" in _nt2):
                _proc = "钣金"
            else:
                _proc = "机加"
    # 技术要求逐条、全文提交。禁止为节省 token 静默截断，否则 AI 无法证明完整覆盖。
    _tech_items_list = []
    for _no, _t in _tech_items(notes):
        _tech_items_list.append((_no, _t))
    _tech_ids = [{"id": "TECH-" + str(_no), "条号": _no, "内容": _t}
                 for _no, _t in _tech_items_list]

    # 2026-10-26：标题栏「类别」改写成工艺类别（AI 会锚定这个字段 → 必须写清以此为准）
    try:
        _orig_cat = str(_hdr.pop("类别", "") or "")   # 2026-10-26：类别删除，不让 AI 依赖
        pass   # 类别已删除，不再注入
    except Exception:
        pass

    # 审查规则（写进输入，避免改 system prompt 影响别的调用）
    _rules = {
        "角色": "资深机械设计/工艺/标准化审核工程师，精通 GB/ISO/ASME；审核：技术要求、尺寸公差、几何公差、粗糙度、基准及它们的相互影响",
        "审查范围（必须逐项结合）": [
            "清单里的每一条尺寸公差：结合技术要求、材料、工艺、表面处理判断是否合理（如阳极氧化/电镀/喷涂会改变尺寸，公差是否需预留余量；±0.01 这类严公差是否超出该工艺能力）",
            "清单里的每一条几何公差：结合基准体系、挂到尺寸、配合对象、加工工艺判断是否合理/完整/矛盾（基准是否明确、归属是否正确、是否需要最大实体）",
            "形状公差（平面度/直线度/圆度/圆柱度）只控制形状、不控制位置：无基准时不要要求基准，不要拿它和尺寸公差/轮廓度比大小、不要判“比轮廓度更严”",
            "垂直度/平行度/位置度/同轴度前的 φ：带φ=圆柱公差带，不带φ=两平行平面公差带；轴线不带φ也能成立，平面带φ才可疑。φ的有无不单独判错，只能结合‘被测要素/公差带’给 review，不得给 critical/major",
            "清单里的每条粗糙度：是否与材料/工艺/配合面/技术要求匹配（太粗/太细/漏标）",
            "技术要求之间、技术要求与材料/工艺/表面处理之间是否矛盾或缺项",
            "尺寸公差与几何公差之间是否打架（IT 等级 vs 形位公差、是否需要基准/包容要求）",
            "装配/加工风险（最大作用边界、过盈/间隙、薄壁变形、工艺不可达）",
            "尺寸/公差表是唯一主数据源；其中被测要素、指定尺寸、公差框类别、公差框组ID、框内顺序、同组完整结构、CZ/SZ、基准设置和作用边界状态均为插件已识别事实，不得自行改写或重新推断",
            "公差框类别以输入字段为准：框数=1为独立公差；多框且每框有独立符号、符号不全相同为独立公差（多框不同项目）；多框且每框有独立符号、符号全部相同为组合公差框格（多单段）；只有一个几何符号且框数≥2、下层无独立符号格才是复合公差框格",
            "同一公差框组ID只表示来自同一物理标注，不等于复合框；必须结合公差框类别和同组完整结构理解。独立多框要分别审核但不得误报为组合/复合或重复；组合多单段要分别验证并同时满足；复合框才按上下层整体理解",
            "CZ/SZ只是公差带修饰，必须与公差框类别分开：CZ=组合公差带、SZ=独立公差带；严禁因为出现CZ就判为复合公差框格",
            "按公差框组ID、框内顺序和同组完整结构逐项核对有效符号、公差带形状、数值、材料状态修正、被测要素、指定尺寸和完整基准链；复合框下层的有效符号来自上层继承，不得报符号缺失",
            "核对基准链的主/次/第三基准顺序，并使用基准定义展开中的要素类型、指向尺寸、来源和状态；引用了未定义基准时只能报待确认，不得自行补定义",
            "检查尺寸公差、最大/最小实体要求、包容要求、几何公差加严、表面处理余量、加工可达性和检验方案之间的组合风险",
            "把确定错误与信息不足分开：只有输入事实能直接证明的问题才用 problem；缺功能、装配或检验上下文时用 review_required",
        ],
        "专业判定流程（每个项目内部必须完成）": [
            "先复述并核对输入事实，再识别适用标准体系和控制对象",
            "分析该要求对功能、装配、制造和检验的影响，区分明确违规、工程风险和资料不足",
            "形成结论时引用对应 TECH/DIM/GDT 证据；跨项目结论必须同时引用相关 ID",
            "修改建议必须指出改哪个对象、改什么内容、预期解决什么风险，并给出修改后的复核或检验方法",
            "没有计算依据时不得建议具体公差数值；可建议设计人员依据配合、工艺能力或测量不确定度重新分配并确认",
        ],
        "风险等级口径": {
            "critical": "已有事实表明可能导致安全、关键功能失效或无法装配/制造，且影响重大",
            "major": "很可能影响功能、装配、批量制造或可检验性，需要设计修改",
            "minor": "主要是标准化、表达清晰度或低影响优化，但仍有明确修改依据",
            "review": "输入不足或需要功能/装配/检验上下文才能定论，必须人工确认",
        },
        "输出要求": [
            "只输出需要修改或需要人工确认的项目；通过项不逐条输出",
            "但不得因为“不要求每条”就跳过公差：清单里的尺寸公差/几何公差/粗糙度都要在内部检查一遍，有问题的写 findings",
            "尺寸表中的‘无’表示没有单独标注尺寸公差，不等于漏标；若技术要求已声明一般公差标准，不得报告‘该尺寸公差未标注’。‘基本尺寸’由几何公差控制，也不得按缺尺寸公差报告",
            "category 只能是：技术要求 / 尺寸/几何公差 / 材料/工艺",
            "location 只能引用清单里的 id（TECH-n / DIM-n / GDT-n）或 OVERALL；title/risk/suggestion 必须写可读名称，不得只写编号",
            "每条 finding 必须：title≤40字、risk≤120字、standard_basis≤80字、reasoning≤120字、suggestion≤120字、verification_method≤120字、risk_level=critical|major|minor|review、confidence=0~1、evidence_refs 写引用的 id",
            "不确定但值得问的用 status=review_required；完全没依据的不要输出",
            "只要清单的被测要素不是‘未判定/未指定’，就禁止报告‘被测要素未指定’；只有归属来源=未归属时才可报告归属未指定",
            "summary≤80字，先给整体结论；如果问题多，summary 要点出最严重的 1~2 项",
            "reviewed_ids 必须逐字覆盖 required_review_ids 的每一项；即使通过且不生成 finding，也必须列入 reviewed_ids",
            "input_checksum 必须原样回传，omitted_count 必须为0",
        ],
        "示例（尺寸公差要结合工艺）": [
            "「尺寸10 ±0.01、材料6061、表面阳极氧化」→ 氧化层会改变尺寸，±0.01 可能没预留氧化余量；或 ±0.01 对普通阳极氧化过严 → 报 minor/major，建议注明氧化前/后尺寸或放宽",
            "「尺寸5 基本尺寸 + 面轮廓度0.02」→ 不要报缺公差；应检查轮廓度是否给了基准、基准是否明确",
            "「两个几何公差同值但分别挂3.5和5」→ 不是重复，不要报重复；要看归属/基准是否正确",
        ],
        "注意": "先自己判定工艺类型（机加/钣金/注塑/其他），再按该工艺规则审；不要套用与判定结果不符的标准。",
    }

    # ---- 输入引用 ID（稳定、可校验）----
    _analysis_for_ai = (extra or {}).get("dim_rows") or []
    if _analysis_for_ai:
        _checklist = _ai_tolerance_checklist_from_analysis(_analysis_for_ai)
        _checklist.extend(_ai_unlinked_checklist(unlinked_gtols))
    else:
        _checklist = _ai_tolerance_checklist(dims, unlinked_gtols)
    _baseline_for_ai = (extra or {}).get("baseline_settings") or []
    _checklist = _enrich_ai_checklist(_checklist, _baseline_for_ai)
    _d_i = _g_i = 0
    _cl_ids = []
    for _it in _checklist:
        _row = dict(_it)
        if str(_row.get("类型")) == "几何公差":
            _g_i += 1
            _row["id"] = "GDT-" + str(_g_i)
        else:
            _d_i += 1
            _row["id"] = "DIM-" + str(_d_i)
        if str(_row.get("类型") or "") == "几何公差" and not str(_row.get("几何公差记录ID") or "").strip():
            _row["几何公差记录ID"] = _row["id"] + "（本次审核稳定编号）"
        _cl_ids.append(_row)
    _title_facts = []
    for _k, _id in (("名称", "TITLE-NAME"), ("材料", "TITLE-MATERIAL"),
                    ("版本", "TITLE-VERSION"), ("类别", "TITLE-CATEGORY")):
        _v = str(_hdr.get(_k) or "").strip()
        if _v:
            _title_facts.append({"id": _id, "字段": _k, "值": _v})
    _local_block = []
    _n_loc = 0
    for _lf in (local_findings or []):
        if not isinstance(_lf, dict):
            continue
        _n_loc += 1
        _rid = str(_lf.get("rule_id") or "LOCAL")
        _local_block.append({
            "id": "LOCAL-" + _rid + "-" + str(_n_loc),
            "category": str(_lf.get("category") or ""),
            "severity": str(_lf.get("severity") or ""),
            "title": str(_lf.get("title") or "")[:80],
            "fact": str(_lf.get("detail") or _lf.get("title") or "")[:120]})
    _valid = {}
    for _t in _tech_ids:
        _valid[_t["id"]] = {"text": _t["内容"][:120]}
    for _c in _cl_ids:
        _label = str(_c.get("显示名称") or "").strip()
        if not _label:
            _label = (str(_c.get("尺寸") or _c.get("挂到尺寸") or "") + " " +
                      str(_c.get("公差") or _c.get("值") or "")).strip()
        _valid[_c["id"]] = {"label": _label,
                              "kind": str(_c.get("类型") or ""),
                              "target": str(_c.get("挂到尺寸") or _c.get("尺寸") or "")}
    for _tf in _title_facts:
        _valid[_tf["id"]] = {"text": _tf["字段"] + "=" + _tf["值"]}
    for _lb in _local_block:
        _valid[_lb["id"]] = {"text": _lb["title"]}

    _expected_review_ids = [x["id"] for x in _tech_ids] + [x["id"] for x in _cl_ids]
    _input_errors = _validate_ai_input(_cl_ids, _expected_review_ids)
    if _input_errors:
        return [], "AI 输入结构校验失败：" + "；".join(_input_errors[:10]), round(_time.time() - _t0, 1), "", []
    _review_input = {
        "标题栏事实": _title_facts,
        "技术要求": _tech_ids,
        "公差清单": _cl_ids,
        "基准设置": _baseline_for_ai,
        "本地规则": _local_block,
    }
    _input_checksum = hashlib.sha256(json.dumps(
        _review_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM},
            {"role": "user", "content": json.dumps({
                "审核标准体系": audit_standard_block(),
                "审核规则与范围（必须遵守）": _rules,
                "input_checksum（必须原样回传）": _input_checksum,
                "required_review_ids（必须全部逐项审核并原样回传）": _expected_review_ids,
                "标题栏事实": _title_facts,
                "技术要求（逐条）": _tech_ids,
                "待审核公差清单": _cl_ids,
                "本地规则摘要": _local_block,
                "表面粗糙度": _ai_roughness_block(roughness_raw if roughness_raw else roughness, roughness),
                "基准设置信息": _baseline_for_ai,
                "本地工艺初判（仅供核对，最终以你的判定为准）": _ai_process_block(header, notes),
                "焊接符号": [{"序号": i + 1,
                              "文字": str(w.get("text") or ""),
                              "视图": str(w.get("view") or "")}
                             for i, w in enumerate((extra or {}).get("welds") or [])],
            }, ensure_ascii=False)},
        ],
        "thinking": {"type": "disabled"},   # 关掉推理：避免 token 全耗在 reasoning_content 上\n        "stream": True,            # 流式：有数据就继续；1 分钟无响应则中止
        "max_tokens": int(os.environ.get("QC_AI_MAX_TOKENS", "12000") or 12000),  # 全量 findings + reviewed_ids 不得被截断
        "temperature": 0,          # 固定输出，避免每次结论不同
        "top_p": 1,
    }
    # 自测用：QC_AI_DUMP_PAYLOAD=<路径> → 把完整请求体落盘
    try:
        _dp = os.environ.get("QC_AI_DUMP_PAYLOAD", "").strip()
        if _dp:
            open(_dp, "w", encoding="utf-8").write(
                json.dumps(payload, ensure_ascii=False, indent=1))
    except Exception:
        pass

    # 2026-10-26 自测用：QC_AI_DUMP=1 → 把请求体写到 %TEMP%\SwQcAddin\ai_request.json
    if os.environ.get("QC_AI_DUMP") == "1":
        try:
            _tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
            _dump = os.path.join(_tmp, "SwQcAddin", "ai_request.json")
            try:
                os.makedirs(os.path.dirname(_dump), exist_ok=True)
            except Exception:
                pass
            with open(_dump, "w", encoding="utf-8") as _f:
                _f.write(json.dumps(payload, ensure_ascii=False, indent=1))
            sys.stderr.write("[QC_AI_DUMP] " + _dump + chr(10))
        except Exception:
            pass

    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST")
    _idle, _total = _ai_timeouts()
    try:
        _t_start = _time.time()
        content_parts = []
        reasoning_parts = []
        _body_obj = None
        with urllib.request.urlopen(req, timeout=_idle) as resp:
            _ctype = str(resp.headers.get("Content-Type") or "")
            if "text/event-stream" in _ctype:
                _dbg = os.environ.get("QC_AI_STREAM_DEBUG", "").strip()
                _dbgf = open(_dbg, "w", encoding="utf-8") if _dbg else None
                while True:
                    if _time.time() - _t_start > _total:
                        raise TimeoutError("AI 总等待超过 %.0f 秒" % _total)
                    try:
                        raw_line = resp.readline()
                    except Exception as _e:
                        if content_parts:      # 已收到部分 → 当作超时截断
                            raise TimeoutError("AI 输出中断（%.0f 秒无新数据）" % _idle)
                        raise
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", "replace").strip()
                    if _dbgf is not None:
                        _dbgf.write(line + chr(10)); _dbgf.flush()
                    if not line.startswith("data:"):
                        continue
                    data_txt = line[5:].strip()
                    if data_txt == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_txt)
                    except Exception:
                        continue
                    try:
                        delta = obj["choices"][0].get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            content_parts.append(piece)
                        rp = delta.get("reasoning_content") or ""
                        if rp:
                            reasoning_parts.append(rp)
                    except Exception:
                        continue
                if _dbgf is not None:
                    _dbgf.close()
                content = "".join(content_parts).strip()
                if not content and reasoning_parts:
                    # 推理模型可能把正文也塞进 reasoning_content（或正文为空）
                    content = "".join(reasoning_parts).strip()
            else:
                body = json.loads(resp.read().decode("utf-8", "replace"))
                _body_obj = body
                content = (body["choices"][0]["message"]["content"] or "").strip()
        if _body_obj is None:
            body = {"choices": [{"finish_reason": ""}]}
        else:
            body = _body_obj
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
        try:
            _dr = os.environ.get("QC_AI_DUMP_RESPONSE", "").strip()
            if _dr:
                open(_dr, "w", encoding="utf-8").write(content)
        except Exception:
            pass
        parsed, _parse_err = _parse_ai_json(content)
        _finish = ""
        try:
            _finish = str(body["choices"][0].get("finish_reason") or "")
        except Exception:
            _finish = ""
        if parsed is None:
            _why = _parse_err or "AI 返回不是合法 JSON"
            if _finish == "length":
                _why += "（模型输出达到长度上限被截断，可减少技术要求条数或加大模型输出上限）"
            return [], _why, round(_time.time() - _t0, 1), "", []
        if _parse_err and _finish == "length":
            _parse_err += "（模型输出达到长度上限被截断）"
        _coverage_error = _validate_ai_response_coverage(
            parsed, _input_checksum, _expected_review_ids)
        if _coverage_error:
            return [], "AI 结果完整性校验失败：" + _coverage_error, round(_time.time() - _t0, 1), "", []
        # 严格格式：校验值 + 全量 reviewed_ids + findings；未覆盖全部输入时上面已拒绝。
        if _is_new_findings(parsed):
            _new_issues, _new_summary, _new_meta = _land_findings(parsed, _valid)
            _AI_LAST_META.update(_new_meta or {})
            return _new_issues, "", round(_time.time() - _t0, 1), _new_summary, []

        summary = ""
        items = []
        # 旧格式 {"items":[{no,verdict,...}]} / tech_items / geom_items 兼容（仅解析层）
        # 没有 items 时退回旧格式 findings（保持原逻辑，不炸）。
        raw_items = None
        _tech_raw = []
        _geom_raw = []
        if isinstance(parsed, dict):
            summary = str(parsed.get("summary") or "")[:400]
            if isinstance(parsed.get("tech_items"), list):
                _tech_raw = parsed.get("tech_items")
            if isinstance(parsed.get("geom_items"), list):
                _geom_raw = parsed.get("geom_items")
            try:
                if parsed.get("passed_note"):
                    summary = (summary + "｜" + str(parsed.get("passed_note"))).strip("｜")
            except Exception:
                pass
            if _tech_raw or _geom_raw:
                raw_items = []
            else:
                # 兼容旧格式
                raw_items = parsed.get("items")
                if isinstance(raw_items, list):
                    items = []
                else:
                    raw_items = None
                    items = parsed.get("findings") or []
        elif isinstance(parsed, list):
            items = parsed

        # 审核原文（技术要求）用于「依据」校验，防幻觉
        # 2026-10-26：依据校验范围要包含尺寸/几何公差，否则关于公差的结论会被误杀
        _audit_parts = [str(x) for x in (notes or [])]
        try:
            for _d in (dims or []):
                _audit_parts.append(str(_d.get("text") or ""))
                _audit_parts.append(str(_d.get("value_mm") or ""))
                _audit_parts.append(fmt_tol_display(_d))
                for _g in (_d.get("gtols") or []):
                    _audit_parts.append(str(_g.get("sym") or "") + str(_g.get("val") or ""))
                    _audit_parts.extend(str(_g.get(k) or "") for k in ("dat1", "dat2", "dat3"))
        except Exception:
            pass
        _audit_src = "\n".join(_audit_parts)

        def _quote_ok(q):
            if not q:
                return True                      # 没给依据 → 放行但不加分
            q = str(q).strip()
            if len(q) < 3:
                return True
            return q in _audit_src

        out, dropped = [], 0
        passed_items = []
        if _tech_raw or _geom_raw:
            # 新格式：技术要求只列问题；几何公差逐项（风险/合理性/矛盾）
            _gtol_list = _ai_tolerance_checklist(dims, unlinked_gtols)
            out, _passed_unused, dropped = _land_tech_items(_tech_raw, _tech_items_list)
            passed_items = []          # 2026-10-29：不再逐条输出“通过”
            _g_iss, _g_drop = _land_geom_items(_geom_raw, _gtol_list)
            out.extend(_g_iss)
            dropped += _g_drop
        else:
            _it_list = [x for x in (items or raw_items or []) if isinstance(x, dict)]
            if any(("kind" in x) or ("verdict" in x) for x in _it_list):
                _tech_raw2 = [x for x in _it_list if str(x.get("kind") or "") != "几何公差"]
                _geom_raw2 = [x for x in _it_list if str(x.get("kind") or "") == "几何公差"]
                _gtol_list2 = _ai_tolerance_checklist(dims, unlinked_gtols)
                _o2, _passed_unused2, dropped = _land_tech_items(_tech_raw2, _tech_items_list)
                passed_items = []
                _g2, _gd2 = _land_geom_items(_geom_raw2, _gtol_list2)
                _o2.extend(_g2)
                dropped += _gd2
                out = _o2
            else:
                for it in _it_list:
                    if not isinstance(it, dict):
                        continue
                    sev = str(it.get("severity", "minor"))
                    if sev not in SEV_ORDER:
                        sev = "minor"
                    q = str(it.get("quote") or "")
                    if not _quote_ok(q):
                        dropped += 1
                        continue
                    # 2026-10-26：没引用原文的结论，最高只能算"轻微"（宁漏勿假）
                    if not q.strip() and sev in ("critical", "major"):
                        sev = "minor"
                    detail = str(it.get("detail", ""))[:MAX_AI_DETAIL_LEN]   # 内容要完整
                    suggestion_txt = str(it.get("suggestion", ""))[:MAX_AI_SUGGESTION_LEN]
                    if q:
                        detail = "依据：「" + str(q)[:60] + "」　" + detail
                    # 2026-10-29 需求书 §9：旧格式也要转成新语义（按类别落 AI-TECH / AI-GEOM）
                    _cat = str(it.get("category") or "").strip()
                    _is_geo = ("尺寸" in _cat) or ("公差" in _cat) or ("基准" in _cat)
                    _rule = "AI-GEOM-ITEM" if _is_geo else "AI-TECH-ITEM"
                    _ai_issue = issue(_rule, sev,
                                      str(it.get("title", ""))[:60],
                                      detail,
                                      suggestion_txt,
                                      "AI语义审核")
                    _ai_issue["category"] = "尺寸/几何公差" if _is_geo else "技术要求"
                    out.append(_ai_issue)
        if dropped:
            summary = (summary + "（已丢弃 " + str(dropped) + " 条依据不实的结论）").strip()
        _AI_LAST_SUMMARY = summary
        return out, "", round(_time.time() - _t0, 1), summary, passed_items
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        hint = {401: "（密钥无效或过期）", 403: "（无权限：可能地区限制或密钥权限）",
                404: "（模型名或地址不对）", 429: "（额度用尽或请求过快）"}.get(exc.code, "")
        return ([], f"AI 调用失败：HTTP {exc.code} {exc.reason}{hint} {detail}",
                round(_time.time() - _t0, 1), "", [])
    except urllib.error.URLError as exc:
        _reason = str(getattr(exc, "reason", exc))
        if "timed out" in _reason.lower() or "timeout" in _reason.lower():
            return ([], "AI 检测未完成：%.0f 秒无响应，已中止 AI（其余检查结果正常）"
                    % _ai_timeouts()[0], round(_time.time() - _t0, 1), "", [])
        return ([], "AI 调用失败：网络不可达（" + _reason +
                "）。若用 OpenAI/ChatGPT，请在「代理」填本机代理后重试。",
                round(_time.time() - _t0, 1), "", [])
    except Exception as exc:
        return [], f"AI 调用失败：{exc!r}", round(_time.time() - _t0, 1), "", []


# ---------------------------------------------------------------- 报告
def _pick_authoritative_notes(notes):
    """
    2026-10-26：一张图读到多套技术要求时，只保留最完整的那套作为判定依据。
    规则：含「技术要求」的块里，编号条目数最多者胜；平手取文本更长者。
    返回 (保留列表, 被排除列表, 说明)
    """
    items = [str(x).strip() for x in (notes or []) if str(x).strip()]
    # 去重（同一文本重复读多次只算一份）
    seen = set()
    uniq = []
    for t in items:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    blocks = [t for t in uniq if "技术要求" in t]
    if len(blocks) < 2:
        return uniq, [], ""
    def score(t):
        numbered = len(re.findall(r"(?m)^\s*\d+[.、]", t)) + len(re.findall(r"\d+[.、]\s*\S", t))
        return (numbered, len(t))
    best = max(blocks, key=score)
    drop = [t for t in blocks if t != best]
    kept = [t for t in uniq if t not in drop]
    note = ("图纸读到 " + str(len(blocks)) + " 套技术要求，仅采用最完整的一套（"
            + str(score(best)[0]) + " 条编号），其余 " + str(len(drop))
            + " 套疑似模板/旧版残留，已排除、不参与判定")
    return kept, drop, note


# ---------------------------------------------------------------- 通过项去重
# 规则（用户要求：窗口里同一信息只出现一次，且归到唯一类别）：
#   1) 文本完全相同的通过项只留一条
#   2) 同一主题只留信息量最大的一条（表面处理：保留含防腐能力/工艺描述的那条）
#   3) 每条通过项的归属类别在 C# 侧按关键词唯一确定（粗糙度→尺寸 等）
_PASSED_TOPICS = [
    ("表面处理", ("表面处理",)),          # 表面处理相关：合并成一条
    ("材料", ("标题栏材料", "材料「")),   # 材料栏只留「标题栏材料…」
]


def _norm_passed(t):
    return re.sub(r"\s+", "", str(t or ""))


def _dedupe_passed(items):
    out, seen = [], set()
    # 先按主题合并
    merged = []
    surface = [x for x in items if "表面处理" in str(x)]
    if len(surface) > 1:
        # 优先保留带工艺细节的那条（盐雾/钝化/阳极/镀/氧化/喷涂），再按长度
        def _score(x):
            t = str(x)
            k = sum(1 for w in ("盐雾", "钝化", "阳极", "镀", "氧化", "喷涂", "喷塑", "磷化") if w in t)
            return (k, len(t))
        best = max(surface, key=_score)
        surface_set = set(surface)
        merged = [x for x in items if x not in surface_set]
        merged.append(best)
    else:
        merged = list(items)
    for it in merged:
        k = _norm_passed(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _passed_consistency(items):
    """一致性自检：同一主题出现多次、或同类信息跨类别重复 → 返回提示。"""
    problems = []
    try:
        seen = {}
        for it in items:
            k = _norm_passed(it)
            if k in seen:
                problems.append("通过项重复：" + str(it)[:40])
            seen[k] = True
        n_surface = sum(1 for x in items if "表面处理" in str(x))
        if n_surface > 1:
            problems.append("表面处理信息重复出现 %d 次" % n_surface)
    except Exception:
        pass
    return problems


def build_report(data, preset):
    header = collect_header(data)
    notes, _nfr_dropped = requirement_notes(data)
    # 2026-10-26：多套技术要求 → 只认最完整的一套（图外/模板残留不进 AI、不进规则）
    _note_pick_msg = ""
    try:
        notes, _dropped_notes, _note_pick_msg = _pick_authoritative_notes(notes)
    except Exception:
        pass
    dims = data.get("dimensions") or []
    # Same nominal value can occur in several views (for example a plain 5 and
    # a diameter 5).  Value-only joins used to merge them and overwrite the
    # diameter symbol/geometry ownership.  Carry a source identity end-to-end.
    for _dim_index, _raw_dim in enumerate(dims):
        if isinstance(_raw_dim, dict) and not _raw_dim.get("_dim_id"):
            _raw_dim["_dim_id"] = "DIMRAW-" + str(_dim_index + 1)
    roughness = _dedupe_roughness(data.get("roughness") or [])
    counts = data.get("counts") or {}
    warns = data.get("warnings") or []
    file_name = data.get("file") or "未命名.SLDDRW"

    # 2026-10-24：先把几何公差挂到对应尺寸上，再分析尺寸
    if _note_pick_msg:
        try:
            warns = list(warns or [])
            warns.append(_note_pick_msg)
        except Exception:
            pass

    unlinked_gtols = []
    try:
        _l, unlinked_gtols = link_gtols_to_dims(dims, data.get("gtols") or [])
    except Exception as _link_exc:
        # 关联层异常时原始公差仍须进入未归属表，不能以空列表伪装成功。
        unlinked_gtols = []
        for _raw_g in (data.get("gtols") or []):
            _parsed = parse_gtol_all(_raw_g) if isinstance(_raw_g, dict) else []
            if _parsed:
                unlinked_gtols.extend(_parsed)
            else:
                _raw_text = str((_raw_g or {}).get("text") or "") if isinstance(_raw_g, dict) else str(_raw_g)
                unlinked_gtols.append({"sym": "?", "val": _raw_text, "label": "未解析几何公差 " + _raw_text,
                                       "parse_error": True})
        warns = list(warns or [])
        warns.append("几何公差自动归属失败，已全部保留为未归属：%s" % str(_link_exc)[:120])
    # 人工指定过的归属（按图号记忆）→ 挂回去，再交给后续判定与 AI
    try:
        _dwg0 = ""
        try:
            _dwg0 = str((collect_header(data) or {}).get("图号") or "")
        except Exception:
            _dwg0 = ""
        unlinked_gtols, _applied_links = apply_saved_gtol_links(dims, unlinked_gtols, _dwg0)
        try:
            _moved = _apply_memory_override(dims, _dwg0, unlinked_gtols)
            if _moved:
                warns = list(warns or [])
                warns.append("按人工归属记忆调整了 %d 条几何公差的归属" % _moved)
        except Exception:
            pass
        if _applied_links:
            warns = list(warns or [])
            warns.append("按人工归属记忆挂回 %d 条几何公差（来源：历史人工指定）" % _applied_links)
    except Exception:
        pass
    dim_rows = analyze_dims(dims, "\n".join(notes), header,
                            data.get("view_boxes"), data.get("sheet_names"))
    # 需求 2026-10-29：轮廓 X/Y/Z 永远不进入列表（视图推断的也去掉；带公差的转为普通尺寸）
    try:
        _rest0 = []
        for _r0 in dim_rows:
            if str(_r0.get("kind") or "").startswith("轮廓"):
                _tol0 = str(_r0.get("tol") or "")
                if _tol0 and _tol0 not in ("无", "未注", "—"):
                    _r0 = dict(_r0); _r0["kind"] = "尺寸特征"; _rest0.append(_r0)
                continue
            _rest0.append(_r0)
        dim_rows = _rest0
    except Exception:
        pass

    # 2026-10-24：拿到 3D 模型包围盒时，轮廓/板厚一律以它为准（100% 准确）
    # 拿不到就保持上面视图推断的结果，并且不额外声称任何事。
    _box_issues = []
    try:
        _box = data.get("part_box")
        if _box and len(_box) >= 6:
            # 需求 2026-10-29：轮廓 X/Y/Z 不再进列表，只用于“漏标检查 + 备注标注”
            _rest = []
            for r in dim_rows:
                _k = str(r.get("kind", ""))
                if not _k.startswith("轮廓"):
                    _rest.append(r)
                    continue
                # 带公差的轮廓尺寸（如 444 0/-0.1）是有意义的尺寸，保留为普通尺寸
                _tol = str(r.get("tol", "") or "")
                if _tol and _tol not in ("无", "未注"):
                    r = dict(r)
                    r["kind"] = "尺寸特征"
                    _rest.append(r)
            dim_rows = _rest
            _outline_pairs, _outline_missing, _outline_blocked = outline_match_pairs(
                _box, dims, data.get("thickness_mm") or 0,
                bool(data.get("is_sheet_metal")))
            if not _outline_blocked:
                for _ax, _v, _c in _outline_pairs:
                    _box_issues.append((
                        "OUTLINE-MATCH", "minor", "外形尺寸已对上：%s 向 %s" % (_ax, _outline_fmt(_v)),
                        "包络 %s 与图纸尺寸 %s 对应。" % (_outline_fmt(_v), _c),
                        "", "GB/T 4458"))
                # 去掉占位匹配提示（只保留漏标问题）
                _box_issues = [x for x in _box_issues if x[0] != "OUTLINE-MATCH"]
                for _ax, _v in _outline_missing:
                    _box_issues.append((
                        "OUTLINE-MISS", "major",
                        "整体外形尺寸可能漏标：%s 向 %s" % (_ax, _outline_fmt(_v)),
                        "3D 模型包络 %s 向为 %s mm，但在图纸已标注尺寸中找不到对应值"
                        "（按 %s mm 容差匹配，直径按同值匹配、半径按 2R 折算）。" %
                        (_ax, _outline_fmt(_v), _outline_fmt(_outline_tol())),
                        "在图上补标该方向外形尺寸，或确认由哪个尺寸间接确定后注明。",
                        "GB/T 4458"))
    except Exception:
        _box_issues = []
    try:
        dim_rows = _refill_content_from_dims(dim_rows, dims)
    except Exception:
        pass
    try:
        dim_rows = _append_missing_dim_rows(dim_rows, dims)
    except Exception:
        pass
    try:
        dim_rows = _clean_dim_rows(dim_rows)
    except Exception:
        pass
    # 被关联、但最终没有出现在尺寸公差表里的几何公差 → 仍要交给用户确认。
    # 必须等 dim_rows 完成构造/清洗后再检查；旧位置在赋值前访问它，异常被吞后整段失效。
    try:
        for _d2 in dims:
            _gl = _d2.get("gtols")
            if not isinstance(_gl, list):
                continue
            _dim_content = _norm_content(dim_display(_d2)[0])
            _shown = bool(_dim_content) and any(
                _norm_content(_r.get("content")) == _dim_content
                for _r in (dim_rows or []) if isinstance(_r, dict))
            if _shown:
                continue
            for _g in _gl:
                unlinked_gtols.append({"sym": _g.get("sym") or "", "val": _g.get("val") or "",
                                       "dat1": _g.get("dat1") or "", "dat2": _g.get("dat2") or "",
                                       "dat3": _g.get("dat3") or "",
                                       "mod": _g.get("mod") or "", "label": _g.get("label") or "",
                                       "view": str(_d2.get("view") or "")})
    except Exception:
        pass
    # 2026-10-29：被关联但表中没有对应行的公差 → 补行或进未关联（不能丢）
    try:
        _dm2 = _load_datum_map(((data.get("props_part") or {}).get("图样代号")
                                or (data.get("props") or {}).get("图样代号") or ""))
        _datum_near = ""
        for _k2, _v2 in (_dm2 or {}).items():
            if str(_k2).endswith("#near") and _v2:
                _datum_near = str(_v2)
                break
        _rows_by_content = {_norm_content(r.get("content")): r
                            for r in (dim_rows or []) if isinstance(r, dict)}
        for _d2 in dims:
            _gl = _d2.get("gtols")
            if not isinstance(_gl, list) or not _gl:
                continue
            _content2, _note2 = dim_display(_d2)
            _key2 = _norm_content(_content2)
            _row2 = _rows_by_content.get(_key2)
            if _row2 is None and _datum_near:
                # 基准有人工关联尺寸（如 A→⌀42）：补一行，把公差挂到它
                _row2 = {"kind": "特征尺寸", "content": _content2,
                         "nominal_mm": 0, "tol": "未注", "it": "未注",
                         "capability": "按 GB/T 1804-m 一般公差",
                         "note": _note2 or "", "view": str(_d2.get("view") or ""),
                         "modifier": ""}
                dim_rows.append(_row2)
                _rows_by_content[_key2] = _row2
            if _row2 is None:
                for _g in _gl:
                    unlinked_gtols.append({"sym": _g.get("sym") or "", "val": _g.get("val") or "",
                                           "dat1": _g.get("dat1") or "", "dat2": _g.get("dat2") or "",
                                           "dat3": _g.get("dat3") or "",
                                           "mod": _g.get("mod") or "", "label": _g.get("label") or "",
                                           "view": str(_d2.get("view") or "")})
                continue
            _lst2 = _row2.get("gtols")
            if not isinstance(_lst2, list):
                _lst2 = []
            for _g in _gl:
                if not any((str(_x.get("sym")) == str(_g.get("sym"))
                            and str(_x.get("val")) == str(_g.get("val"))) for _x in _lst2 if isinstance(_x, dict)):
                    _lst2.append(_g)
            _row2["gtols"] = _lst2
            try:
                _sigs = {(str(_g.get("sym") or ""), str(_g.get("val") or ""),
                          str(_g.get("dat1") or ""), str(_g.get("dat2") or ""),
                          str(_g.get("dat3") or "")) for _g in _gl}
                unlinked_gtols[:] = [u for u in unlinked_gtols
                                     if (str(u.get("sym") or ""), str(u.get("val") or ""),
                                         str(u.get("dat1") or ""), str(u.get("dat2") or ""),
                                         str(u.get("dat3") or "")) not in _sigs]
            except Exception:
                pass
            if _lst2:
                _row2["gtol"] = _lst2[0]
                _lbl2 = str(_lst2[0].get("label") or "")
                if _lbl2 and "几何公差" not in str(_row2.get("note") or ""):
                    _row2["note"] = ((_row2.get("note") or "") + "；几何公差：" + _lbl2).strip("；")
    except Exception:
        pass

    _thread_hits = []
    try:
        _thread_hits = _apply_part_holes(dim_rows, data.get("part_holes") or [])
        if _thread_hits:
            warns = list(warns or [])
            warns.append("已从零件模型补齐螺纹：%d 处" % len(_thread_hits))
    except Exception:
        pass
    _gtol_issues = []
    gtol_rows = analyze_gtols(data.get("gtols") or [], header,
                  max([abs(float(d.get("value_mm") or 0)) for d in dims] or [0]),
                  _gtol_issues, dims)
    rough_rows, rough_issues = analyze_roughness(
        [str(x) for x in (data.get("roughness") or [])], header)
    _rough_issue_objs = [issue(code, sev, title, desc, sug, std)
                         for code, sev, title, desc, sug, std in rough_issues]

    # 先生成最终尺寸表，再启动 AI。后续所有尺寸/公差审核只能读取这份表，
    # 禁止 AI 再从原始 dims 建立另一套相互矛盾的数据。
    _dwg_for_rows = ((data.get("props_part") or {}).get("图样代号")
                     or (data.get("props") or {}).get("图样代号") or "")
    _analysis_rows = _prepare_analysis_rows(dim_rows, dims, _dwg_for_rows)
    _doc_snapshot = _load_document_snapshot()
    _snap_mode = bool(_doc_snapshot or (_load_manual_rows() or []))
    if _doc_snapshot:
        unlinked_gtols = _document_snapshot_unlinked()
    # 注：尺寸/公差表仍以快照为唯一真源；图纸侧规则结果只作为“审核事实/本地摘要”提供给 AI
    # 快照会覆盖引擎备注；这里在快照之后按最新基准记忆重写“基准指向”段落
    if not _doc_snapshot:
        try:
            _sync_gtol_notes(_analysis_rows, _load_datum_map(_dwg_for_rows))
        except Exception:
            pass
    if not _snap_mode:
        try:
            _ensure_outline_rows(_analysis_rows, locals().get("_outline_pairs") or [], dims)
        except Exception:
            pass
        try:
            _annotate_outline_notes(_analysis_rows, locals().get("_outline_pairs") or [])
        except Exception:
            pass

    # ID、复合框分组、框内顺序、被测要素必须在 AI 输入生成前写入。
    # 旧位置在 AI 调用之后，导致界面有值而 AI 收到“未判定/无组号”。
    try:
        _apply_frame_types(_analysis_rows, unlinked_gtols)
        _tag_gtols_meta(_analysis_rows, unlinked_gtols)
    except Exception as _tag_exc:
        warns = list(warns or [])
        warns.append("几何公差元数据整理失败：%s" % str(_tag_exc)[:120])

    read_failed = (int(counts.get("views", 0) or 0) == 0 and
                   int(counts.get("dimensions", 0) or 0) == 0 and
                   len(notes) == 0 and len(warns) > 0)
    if read_failed:
        checks = [issue("CORE-SW-READ-FAIL", "critical", "原生读取失败，本次无法审图",
                        "SW API 读取图纸数据时出错，未拿到视图/尺寸/注释。错误信息：" +
                        "；".join(str(w)[:120] for w in warns[:4]),
                        "把本次生成的 .sw.json 发给作者，按真实 API 返回修正读取逻辑。", "—")]
        ai_issues, ai_err, ai_elapsed, ai_summary = [], "skipped", 0, ""
        ai_passed = []
    else:
        checks = native_checks(data, header)
        checks.extend([issue(c, sev, t, d, sg, std)
                       for c, sev, t, d, sg, std in _box_issues])
        checks.extend(_rough_issue_objs)
        checks.extend([issue(c, sev, t, d, sg, std)
                       for c, sev, t, d, sg, std in _gtol_issues])
        _tol_dims = [d for d in dims if not tol_missing(d)]
        # 2026-10-26：摘要给全（之前只给板厚/轮廓 → 这张图是空的，白占字段）
        _summary = []
        for r in _analysis_rows:
            _seg = f"{r.get('kind')} {r.get('content')} 公差{r.get('tol')} IT{r.get('it')}"
            if r.get("note"):
                _seg += " 备注:" + str(r.get("note"))[:40]
            _summary.append(_seg)
        _rough_payload = [{"要求": r.get("content"), "常规能否满足": r.get("capability")}
                          for r in rough_rows]
        _baseline_settings = []
        try:
            _dm_ai = _load_datum_map(_dwg_for_rows)
            _datum_ai_raw = _ai_datum_block(data, dims, unlinked_gtols, data.get("roughness") or [])
            _auto_by_letter = {}
            for _x in (_datum_ai_raw or []):
                _lb = str(_x.get("基准") or "").strip()
                if _lb:
                    _auto_by_letter[_lb] = _x
            _letters = []
            for _x in (_datum_ai_raw or []):
                _lb = str(_x.get("基准") or "").strip()
                if _lb and _lb not in _letters:
                    _letters.append(_lb)
            # 人工记忆里有、但图纸没读到的基准也补上
            for _k in (_dm_ai or {}):
                if str(_k).endswith(("#near", "#src")):
                    continue
                _lb = str(_k)
                if _lb and _lb not in _letters:
                    _letters.append(_lb)
            for _lb in _letters:
                _auto = _auto_by_letter.get(_lb) or {}
                _kind_manual = str((_dm_ai or {}).get(_lb) or "")
                _near_manual = str((_dm_ai or {}).get(_lb + "#near") or "")
                _src_manual = str((_dm_ai or {}).get(_lb + "#src") or "")
                _is_manual = bool(_kind_manual or _near_manual or _src_manual == "人工指定")
                _near = _near_manual or str(_auto.get("附近尺寸（推断基准面）") or "")
                _baseline_settings.append({
                    "基准": _lb,
                    "要素类型": _kind_manual or "（图纸未区分，需人工确认）",
                    "指向尺寸": _near,
                    "来源": "人工指定" if _is_manual else "图纸",
                    "所在视图": str(_auto.get("所在视图") or ""),
                    "被引用": _auto.get("引用它的几何公差") or [],
                })
            # 统一会话文档中的基准表是最终真源；人工确认后必须覆盖旧的磁盘记忆/自动推断。
            _doc_datums = _document_snapshot_datums()
            if _doc_datums:
                _baseline_settings = []
                for _d in _doc_datums:
                    _baseline_settings.append({
                        "基准": str(_d.get("letter") or ""),
                        "要素类型": str(_d.get("feature_type") or "未指定"),
                        "指向尺寸": str(_d.get("size_text") or ""),
                        "来源": "人工指定" if str(_d.get("source") or "") == "manual" else "图纸",
                        "所在视图": str(_d.get("view") or ""),
                        "坐标": str(_d.get("coord") or ""),
                    })
        except Exception:
            _baseline_settings = []
        _ai_rows = list(_analysis_rows or [])
        try:
            _have_rough = {_norm_content(x.get("content")) for x in _ai_rows
                           if str(x.get("kind") or "").startswith("粗糙度")}
            for _rr in (rough_rows or []):
                if _norm_content(_rr.get("content")) not in _have_rough:
                    _ai_rows.append(_rr)
        except Exception:
            pass
        _ai_extra = {
            "baseline_settings": _baseline_settings,
            "shape": _ai_shape_block(data, header, dims, _summary),
            "datums": _baseline_settings,
            "dim_rows": _ai_rows,
        "part_holes": data.get("part_holes") or [],
        "thread_hits": _thread_hits,
            "welds": data.get("welds") or [],
            "part_holes": data.get("part_holes") or [],
        }
        ai_issues, ai_err, ai_elapsed, ai_summary, ai_passed = ai_check(
            header, _ai_notes(notes, header), dims, _summary, _rough_payload,
            unlinked_gtols, _ai_extra, data.get("roughness") or [], checks)
        checks.extend(ai_issues)
        checks = dedupe_surface_issues(checks)

    # 尺寸公差等级过严 → 提示（不是错，是工艺提醒）
    for row in _analysis_rows:
        it = str(row.get("it") or "")
        m = re.match(r"IT(\d+)", it)
        if m and int(m.group(1)) <= 5:
            checks.append(issue(
                "TOL-STRICT", "minor",
                f"尺寸 {row['content']} 公差对应 {it}，属高精度",
                f"公差带按 {it} 折算（{row['tol']}），常规加工难以稳定保证。",
                "确认是否确需该等级；否则放宽到 IT7-IT9 更经济（" + row["capability"] + "）。",
                "GB/T 1800.1"))
    for row in gtol_rows:
        if row.get("tol"):
            try:
                v = float(str(row["tol"]).replace("mm", "").strip())
            except Exception:
                v = None
            if v is not None and v < 0.01:
                checks.append(issue(
                    "GTOL-STRICT", "minor",
                    f"几何公差 {row['content']} = {row['tol']}，要求很高",
                    "该数值低于常规加工稳定能力（0.01mm）。",
                    "确认是否必须；否则适当放宽，或明确标注需磨削/三坐标验证。",
                    "GB/T 1184"))

    checks.sort(key=lambda x: SEV_ORDER.get(x["severity"], 9))
    summary = {"critical": 0, "major": 0, "minor": 0}
    for c in checks:
        summary[c["severity"]] = summary.get(c["severity"], 0) + 1

    warnings = list(warns)
    if not read_failed:
        warnings.append("原生模式不含几何数据：圆形要素漏标、外形轮廓、尺寸-几何对照未检查。")
    if ai_err == "disabled":
        warnings.append("AI 语义检查未启用（未配置 API Key）。")
    elif ai_err == "skipped":
        warnings.append("AI 语义检查本次跳过（仅读取表头阶段）。")
    elif ai_err:
        warnings.append("AI 语义检查未完成：" + ai_err + "（报告不含 AI 结论）。")

    has_1804 = (bool(GENERAL_TOL_PAT.search("\n".join(notes)))
                or bool(sw_qc_mt.detect_general_tolerance("\n".join(notes))))
    gtol = int(counts.get("gtol", 0) or 0)
    _pc = str(header.get("类别", "") or "")
    is_sheet_metal = ("钣金" in _pc or "板金" in _pc)
    # ---------- 通过项：让"没有问题"也看得见，而不是空表格 ----------
    passed = []
    _sheets = data.get("sheets") or []
    if len(_sheets) > 1:
        _names = "、".join(str(x.get("name") or "") for x in _sheets[:6])
        passed.append("多图纸：共 %d 张（%s），已合并读取" % (len(_sheets), _names))
    # 通过项口径必须与「图框未填」检查一致：图框文字 ∪ 全图注记 ∪ 标题栏注记
    _draw_text_parts = ["\n".join(notes)]
    try:
        _draw_text_parts.append(title_block_text(data))
    except Exception:
        pass
    try:
        _draw_text_parts.append("\n".join(
            str(t.get("text") or "") for t in (data.get("tb_notes") or [])))
    except Exception:
        pass
    _draw_text = "\n".join(_draw_text_parts)
    for field in ("材料", "名称", "图号", "版本"):
        val = header.get(field, "")
        if val and (_n(val) in _n(_draw_text) or
                    (material_core(val) and _n(material_core(val)) in _n(_draw_text))):
            passed.append(f"标题栏{field}「{val}」已在图上标注")
    try:
        _fc = _feature_counts(dims)
        if _fc["螺纹处数"] or _fc["直径尺寸数"]:
            _p = []
            if _fc["螺纹处数"]:
                _p.append("螺纹 %d 处（共 %d 个）" % (_fc["螺纹处数"], _fc["螺纹总数"]))
            if _fc["直径尺寸数"]:
                _p.append("直径尺寸 %d 个（共 %d 处）" % (_fc["直径尺寸数"], _fc["直径总数"]))
            passed.append("图上" + "、".join(_p))
    except Exception:
        pass
    if dims:
        miss = [d for d in dims if tol_missing(d)]
        if miss and has_1804:
            passed.append(f"尺寸 {len(dims)} 个，其中 {len(miss)} 个未注公差，"
                          f"已声明一般公差标准（允许）")
        elif miss:
            passed.append(f"尺寸 {len(dims)} 个，其中 {len(miss)} 个未注公差")
        else:
            passed.append(f"尺寸 {len(dims)} 个，均已标注公差")
    if header.get("_forced_category"):
        passed.append("工艺类别：人工指定「%s」（优先于 AI 判定）" % header.get("_forced_category"))
    if is_sheet_metal:
        passed.append("钣金件：按规则豁免“未标粗糙度/未注几何公差”检查")
    # 【2026-10-23 修】通过项文案必须按零件类别说对应标准，
    # 不能给钣金件也挂「GB/T 47067-2026（塑料模塑件）」——那是注塑件标准。
    _gt = None
    try:
        _gt = sw_qc_mt.detect_general_tolerance(notes_text)
    except Exception:
        _gt = None
    _notes_all = "\n".join(notes)
    _has1184 = bool(re.search(r"GB\s*/?\s*T?\s*1184", _notes_all, re.IGNORECASE))
    _kind_txt = material_kind(header.get("材料", ""))
    _cat_txt = str(header.get("类别", "") or "")
    _is_plastic2 = ("塑料" in _cat_txt or "注塑" in _cat_txt
                    or "塑胶" in _cat_txt or _kind_txt in ("塑料", "塑胶"))
    if has_1804:
        # 2026-10-26 修：图面写的是 GB/T 1804 就如实说 1804，不能因为材料是塑料就写成 47067
        _dim_std = "GB/T 1804"
        _msg = "技术要求已声明一般尺寸公差标准 " + _dim_std
        if _has1184:
            _msg += "、一般几何公差标准 GB/T 1184"
        passed.append(_msg)
    if _gt:
        _idx = sw_qc_mt.tg_to_it(50.0, _gt["grade"])
        passed.append(f"技术要求按 {_gt['text']} 声明一般公差（公差等级 TG{_gt['grade']}）")
        if _idx:
            passed.append(f"TG{_gt['grade']} 对应 IT{_idx}（以 6~120mm 段为例）")
    _k = material_kind(header.get("材料", ""))
    _sv = bool(SURFACE_KEYWORDS.search("\n".join(notes)))
    # 2026-10-24：把表面处理的防腐能力显式写进「通过项」，让人看到评估过程
    try:
        _surf2 = surface_capability("\n".join([str(x) for x in notes]))
        if _surf2["found"]:
            _lvl_txt = {"强": "强（盐雾 ≥500h 或等效）",
                        "中": "中（盐雾约 72~200h）",
                        "弱": "弱（须配套防锈措施）"}.get(_surf2["level"], _surf2["level"])
            _parts = []
            for _it in _surf2.get("items") or []:
                _t = _it["name"] + "（" + (_it["level"] or "—") + "）"
                if _it["note"]:
                    _t += "：" + _it["note"].split("；")[0]
                _parts.append(_t)
            _msg = ("表面处理防腐能力：" + _lvl_txt +
                    ("｜" + "；".join(_parts) if _parts else ""))
            passed.append(_msg)
    except Exception:
        pass

    if _k in SURFACE_OPTIONAL_KINDS and not _sv:
        passed.append("材料「" + str(header.get("材料", "")) + "」（" + _k +
                      "）自带防腐/氧化膜，技术要求未写表面处理属正常")
    elif _k in SURFACE_REQUIRED_KINDS and _sv:
        passed.append("材料「" + str(header.get("材料", "")) + "」（" + _k + "）已注明表面处理")
    # 2026-10-24：AI 审查摘要写进通过项 —— 0 结论时用户也能看到 AI 审了什么
    try:
        if ai_summary:
            passed.append("AI 审查：" + str(ai_summary))
        elif ai_issues:
            passed.append("AI 审查：发现问题 " + str(len(ai_issues)) + " 条")
    except Exception:
        pass
    # 2026-10-26：AI 对技术要求逐条判定为「通过」的，也放进通过项（看得见）
    try:
        if ai_passed:
            passed.extend(ai_passed)
    except Exception:
        pass
    if gtol > 0:
        passed.append(f"图上有 {gtol} 处几何公差")
    # 几何公差自动关联不确定 → 提示用户核对（不放到"通过项"，避免误导）
    try:
        _gtol_uncertain = 0
        for _d0 in dims:
            for _g0 in (_d0.get("gtols") or []):
                if isinstance(_g0, dict) and _g0.get("uncertain"):
                    _gtol_uncertain += 1
        if _gtol_uncertain > 0:
            checks.append(issue(
                "GTOL-LINK-UNCERTAIN", "minor",
                "有 %d 条几何公差的自动关联不确定" % _gtol_uncertain,
                "这些几何公差按位置就近挂到了尺寸上，但最近两个候选尺寸距离接近，可能挂错。",
                "打开「尺寸/公差」页 → 点右上角「指定归属」，逐条核对并改成正确尺寸（可多次指定）。",
                "GB/T 1182"))
    except Exception:
        pass
    # 粗糙度：审图结果里放【尺寸】栏（用户要求：不要放图纸信息/技术要求栏）；
    # 尺寸栏同时逐条列出 Ra（尺寸/公差页）
    if roughness:
        passed.append(f"图上有 {len(roughness)} 处粗糙度标注")
    # 用户规则（2026-10-26）：窗口内同一信息只能出现一次 → 去重 + 唯一归属
    try:
        passed = _dedupe_passed(passed)
        _pdup = _passed_consistency(passed)
        if _pdup:
            try:
                warnings = list(warnings or [])
                warnings.extend(_pdup)
            except Exception:
                pass
    except Exception:
        pass

    header_out = {k: v for k, v in header.items() if not k.startswith("_")}
    has_1804 = (bool(GENERAL_TOL_PAT.search("\n".join(notes)))
                or bool(sw_qc_mt.detect_general_tolerance("\n".join(notes))))
    missing_tol = [d for d in dims if tol_missing(d)]

    # 【2026-10-23】给问题标题加【类别】前缀，让"三分类"在现有 DLL 里可见。
    # 只改给人看的 title，rule_id / category 保持原样（不影响程序判定与白名单）。
    if SHOW_CATEGORY_PREFIX:
        for _it in (checks or []):
            try:
                _cat = _it.get("category") or category_of(_it.get("rule_id"))
                _t = str(_it.get("title") or "")
                if _cat and not _t.startswith("【"):
                    _it["title"] = f"【{_cat}】{_t}"
            except Exception:
                pass

    _ex_fps, _ex_contents = _load_excluded_rows()
    if _ex_contents:
        try:
            dims = [d for d in dims if _norm_content(dim_display(d)[0]) not in _ex_contents]
        except Exception:
            pass
    # _analysis_rows 已在 AI 调用前生成，禁止在这里重新从原始 dims 覆盖用户确认的表格。
    try:
        if _ex_fps:
            _fpset = set(x.replace("—", "-") for x in _ex_fps)
            _kept = []
            for _r in _analysis_rows:
                if not isinstance(_r, dict):
                    _kept.append(_r)
                    continue
                _fp = "|".join([str(_r.get("kind") or ""), str(_r.get("content") or ""),
                                str(_r.get("tol") or _r.get("tol_display") or "")])
                _fp = _fp.replace("—", "-")
                if _fp in _fpset:
                    continue
                _kept.append(_r)
            _analysis_rows = _kept
    except Exception:
        pass

    try:
        _diag_dump({
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dwg": _dwg_for_rows,
            "snapshot_mode": bool(_snap_mode),
            "snapshot_input_checksum": str(abs(hash(json.dumps(_load_manual_rows(), ensure_ascii=False, sort_keys=True))) % 1000000007),
            "snapshot_rows": [{"row_id": r.get("row_id"), "kind": r.get("kind"),
                               "content": r.get("content"), "tol": r.get("tol"),
                               "gtols": [{k: g.get(k) for k in (
                                   "gtol_id", "raw_id", "group_id", "frame_order", "sym", "val",
                                   "dat1", "dat2", "dat3", "mod", "measured_feature", "target_row_id",
                                   "include", "reason", "source")}
                                   for g in (r.get("gtols") or []) if isinstance(g, dict)],
                               "note": r.get("note")} for r in _analysis_rows],
            "snapshot_datums": _document_snapshot_datums(),
            "snapshot_unlinked": _document_snapshot_unlinked(),
            "checks_count": len(checks),
            "unlinked_count": len(unlinked_gtols),
        })
    except Exception:
        pass
    return {
        "engine_version": "SW-native v0.4",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "info": {"file": file_name, "format": "slddrw", "path": data.get("path", ""),
                 "sheet_count": data.get("sheet_count", 0)},
        "header": header_out,
        "tech_notes": notes,
        "drawing_scale": data.get("scale") or {},
        "stats": {"dimension_count": len([r for r in _analysis_rows
                                           if not str(r.get("kind") or "").startswith("粗糙度")]),
                  "untoleranced_count": len([r for r in _analysis_rows
                                             if not str(r.get("kind") or "").startswith(("轮廓", "板厚", "粗糙度"))
                                             and str(r.get("tol") or "") in ("未注", "")]),
                  "roughness_count": len(roughness),
                  "tech_note_count": len(notes),
                  "gtol_count": int(counts.get("gtol", 0) or 0)},
        "counts": counts,
        "sheets": data.get("sheets") or [],
        "probe": data.get("probe") or [],
        "dim_data": {"count": len(dims), "dimensions": dims},
        "roughness": roughness,
        "checks": checks,
        "summary": summary,
        "warnings": warnings,
        "rule_preset": preset,
        "part_category": str(header.get("类别", "") or ""),
        "datums": _ai_datum_block(data, dims, unlinked_gtols, data.get("roughness") or []),
        "part_holes": data.get("part_holes") or [],
        "thread_hits": _thread_hits,
        "is_sheet_metal": bool(
            ("钣金" in str(header.get("类别", "") or "")) or
            ("板金" in str(header.get("类别", "") or "")) or
            (re.search(r"(弹片|支架|连接片|安装板|盖板|挡板|挂钩|卡扣|折弯件)",
                       str(header.get("名称", "") or ""))
             and not re.search(r"(ABS|PC|POM|PA\d*|PP|PE|PMMA|PS|PVC|硅胶|橡胶|塑胶|塑料)",
                               str(header.get("材料", "") or "") + str(header.get("类别", "") or "")))),
        "notes_has_1804": has_1804,
        # 2026-10-25 修：enabled 表示"AI 真的跑过"，不是"AI 报了问题"。
        # 原来是 bool(ai_issues) —— AI 执行成功但没发现问题时误报成"未启用"。
        "ai": {"enabled": bool(ai_err == "") and bool(os.environ.get("QC_AI_KEY", "").strip())
               and ai_err not in ("disabled", "skipped"),
               "configured": ai_err not in ("disabled", "skipped"),
               "error": "" if ai_err in ("", "disabled", "skipped") else ai_err,
               "skipped": ai_err == "skipped", "count": len(ai_issues),
               "elapsed_sec": ai_elapsed, "summary": ai_summary,
               "truncated_count": int(_AI_LAST_META.get("truncated_count", 0) or 0),
               "omitted_count": int(_AI_LAST_META.get("omitted_count", 0) or 0)},
        "dim_analysis": (_analysis_rows if _snap_mode else (_analysis_rows + rough_rows)),
        "passed": passed,
        "gtol_analysis": gtol_rows,
        "unlinked_gtols": unlinked_gtols,
        "read_failed": read_failed,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="原生审图（读 .sw.json）")
    ap.add_argument("input", help="插件生成的 .sw.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rules", default="gb_baseline")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--header-only", action="store_true", help="只读表头，先弹窗")
    ap.add_argument("--ai-test", action="store_true", help="测试 AI 接口")
    args = ap.parse_args(argv)

    if args.ai_test:
        issues, err, _, _sum, _passed = ai_check({"材料": "45"}, ["测试：未注尺寸公差按GB/T 1804-m级执行。"], [])
        if err == "disabled":
            print("AI_TEST_FAIL 未配置 Key")
        elif err == "skipped":
            print("AI_TEST_FAIL 已设置跳过")
        elif err:
            print("AI_TEST_FAIL " + err)
        else:
            print("AI_TEST_OK 返回 %d 条" % len(issues))
        return 0

    with open(args.input, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)

    if args.header_only:
        os.environ["QC_NO_AI"] = "1"

    out_dir = args.out or os.path.dirname(os.path.abspath(args.input))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    report = build_report(data, args.rules)
    if args.header_only:
        report["checks"] = []
        report["summary"] = {"critical": 0, "major": 0, "minor": 0}
        report["header_only"] = True
        report["warnings"] = ["尚未检查：点窗口右下角「检查」开始审图。"]

    stem = os.path.basename(args.input)
    stem = stem[:-len(".sw.json")] if stem.lower().endswith(".sw.json") else os.path.splitext(stem)[0]
    json_path = os.path.join(out_dir, stem + ".qc_report.json")

    file_ok, file_err = True, ""
    try:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        file_ok, file_err, json_path = False, repr(exc), ""

    s = report["summary"]
    print("原生审图完成：%s" % report["info"]["file"])
    print("  严重 %d / 一般 %d / 轻微 %d" % (s.get("critical", 0), s.get("major", 0), s.get("minor", 0)))
    if file_ok:
        print("  报告：%s" % json_path)
    else:
        print("  报告文件写入失败（%s），改为标准输出回传" % file_err)
    print("<<<QC_REPORT_BEGIN>>>")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    print("<<<QC_REPORT_END>>>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
