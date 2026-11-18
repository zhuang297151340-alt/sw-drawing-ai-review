#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
export_spec_library.py — 把国标规格库导出成 Excel，供人工核对与补充

## 为什么要有这个脚本

`sw_qc_gb.py` 里有些表标了 `verified=False`（数值未对照标准正本复核），
另有几张表**根本没有核对标记**。这些数值直接决定检查结论，必须让人过一遍。

导出成 Excel 是为了让**审图工程师**（而不是程序员）能核对：

    python export_spec_library.py                    # 输出到包根目录
    python export_spec_library.py --out 我的核对表.xlsx

**关键点：数据是从 sw_qc_gb.py / rules/*.yaml 现场读出来的，不是手抄的。**
所以表里的值 = 工具真正在用的值，不可能对不上。

## 表格结构（导入时要遵守）

每张数据表统一布局：

    第 1 行  表名 + 标准号
    第 2 行  数据来源与核对状态（合并单元格）
    第 3 行  空行
    第 4 行  表头
    第 5 行起 数据

每张表最后一列都是「备注」，留给核对人写依据或疑问。
核对人**直接改数值单元格**即可；表头与列顺序不要改，否则回导会错位。

## 颜色

    绿色  = 代码里标记为已核对（verified=True）
    黄色  = **未核对**，或代码里没标核对状态 —— 优先复核这些
"""
from __future__ import annotations

# --- 确保模块能找到同目录的兄弟模块（便携版 Python 的隔离模式，见 README）
import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
# -----------------------------------------------------------------

import argparse
import datetime
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import sw_qc_gb as gb


def _make_stdout_safe() -> None:
    """
    让控制台输出**永不因编码崩溃**。

    实测踩到：说明里写了 `⚠` / `φ` 这类非 GBK 字符，在中文 Windows 的
    GBK 控制台（或输出被重定向到文件）时，`print` 直接抛
    UnicodeEncodeError: 'gbk' codec can't encode character '\\u26a0'，
    整个导出脚本以退出码 1 收场 —— **一个人都没导出来**。

    处理办法：能改成 utf-8 就改（Windows 终端/管道都支持）；
    改不了就退到 errors='replace'，最差也就是把图标降级成 `?`，
    但绝不中断导出。这是本项目一贯口径：「即使控制台是 GBK、输出被重定向，
    也只是把图标降级，不会崩溃」（见 README 命令行用法一节）。
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


_make_stdout_safe()

BRAND = "1F4E79"
HDR_FILL = PatternFill("solid", fgColor=BRAND)
HDR_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14, color=BRAND)
NOTE_FONT = Font(italic=True, size=10, color="6B7A8D")
NOTE_FILL = PatternFill("solid", fgColor="F8FAFC")
SEC_FONT = Font(bold=True, size=12, color=BRAND)
OK_FILL = PatternFill("solid", fgColor="E2EFDA")      # 已核对
CHECK_FILL = PatternFill("solid", fgColor="FFF2CC")   # 待核对
_THIN = Side(style="thin", color="D9E2EC")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# 尺寸分段文本（与代码里的 bounds 一一对应，左开右闭）
def _seg_text(bounds: list, i: int, unit: str = "mm") -> str:
    lo = 0 if i == 0 else bounds[i - 1]
    hi = bounds[i]
    if i == 0:
        return f"≤{hi:g}"
    return f">{lo:g}~{hi:g}"


def _num(v):
    """None → 空串，便于人工一眼看出「该段无此等级」。"""
    return "" if v is None else v


# ==========================================================================
# 通用写表
# ==========================================================================
def write_sheet(wb, name, title, note, headers, rows,
                widths=None, row_style=None, note_fill=None, impl_from=None):
    """
    row_style: 可调用，接收 (行索引, 行数据) 返回 PatternFill 或 None。
    impl_from: 从第几列起属于"程序用"列（1 基）。给了就把这些列的表头涂成灰色，
               与"给人审的"列区分开 —— 避免技术列干扰审图人。
    """
    ws = wb.create_sheet(name)
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT

    ws["A2"] = note
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(len(headers), 2))
    for c in range(1, max(len(headers), 2) + 1):
        ws.cell(row=2, column=c).fill = note_fill or NOTE_FILL
    ws.row_dimensions[2].height = 46

    IMPL_FILL = PatternFill("solid", fgColor="6B7A8D")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.fill = IMPL_FILL if (impl_from and c >= impl_from) else HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[4].height = 30

    for i, row in enumerate(rows, start=5):
        fill = row_style(i - 5, row) if row_style else None
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = BORDER
            if c > 1:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            if fill:
                cell.fill = fill

    ws.freeze_panes = "A5"
    for idx, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    return ws


# ==========================================================================
# v0.12 补入的 4 组标准表（螺纹 / 键 / 挡圈 / 基本偏差）
# ==========================================================================
# 这几张表的每个数据块都带**置信度**（见 sw_qc_gb.py 里的注释）：
#   高 = 两套独立来源逐格一致
#   中 = 单一来源，或教材表排版错位已按 ISO 公式修正
#   低 = 推导值（非标准表值）
# 导出时按置信度上色，并在第 2 行说明里写清楚 —— 审核人一眼就知道
# 哪些数值可以直接用、哪些必须先对照标准正本。

_CONF_FILL = {
    "high": OK_FILL,        # 绿：可直接用
    "medium": CHECK_FILL,   # 黄：需复核
    "pending": PatternFill("solid", fgColor="FCE4D6"),   # 橙：已停用
    "low": PatternFill("solid", fgColor="F8CBAD"),       # 深橙：推导值
}
_CONF_LABEL = {"high": "高（两源逐格一致）", "medium": "中（需复核）",
               "pending": "待核对（已停用，不参与检查）", "low": "低（推导值）"}


def sheet_threads(wb):
    """
    11_螺纹螺距_GB193：THD-002「细牙必须标螺距、粗牙可省略」的判据表。

    两个块：
      ① 公称直径 → 粗牙螺距（**不在此表内的直径没有粗牙**）
      ② 公称直径 → 细牙螺距系列
    另有「无粗牙直径」一节：这些规格只写 Mxx 不写螺距就是真漏标。
    """
    rows = []
    fine = gb.M_FINE_PITCHES
    for d in sorted(fine, key=float):
        rows.append([_num(d), _num(gb.M_COARSE_PITCH.get(d)),        # 无粗牙则留空
                     "、".join(f"{p:g}" for p in fine[d]),
                     "⚠ 无粗牙，必须标螺距" if d in gb.M_NO_COARSE_DIAMETERS else "",
                     ""])
    rows.append([])
    rows.append(["以下直径**没有粗牙规格**", "", "", "", ""])
    for d in sorted(gb.M_NO_COARSE_DIAMETERS, key=float):
        rows.append([_num(d), "（无）", "、".join(f"{p:g}" for p in fine.get(d, [])),
                     "只写 Mxx 不写螺距即为漏标", ""])
    return write_sheet(
        wb, "11_螺纹螺距_GB193",
        "米制普通螺纹 直径与螺距系列（判「螺纹是否漏标螺距」用）",
        "数据来源：sw_qc_gb.M_COARSE_PITCH / M_FINE_PITCHES　·　标准：GB/T 193-2003　·　"
        "核对状态：**部分逐条核过，未全部对照标准正本**。"
        "来源为公开机械设计手册两个页面交叉验证，并用 d₂ = d − 0.6495P 做尺寸自洽校验；"
        "两页有 5 处不一致（如 1-3 页把 M33 的粗牙 3.5 错印到 M32 行），已按尺寸校验补正。"
        "**用法**：图上只写 Mxx 时，若 xx 在「无粗牙」清单里 → 必须标螺距；"
        "写了螺距则要求它落在该直径的细牙系列内。"
        "（注：「工具提示」列是程序写的，供你核对用；请只在最后一列写你的意见。）",
        ["公称直径(mm)", "粗牙螺距(mm)", "细牙螺距系列(mm)", "工具提示",
         "备注（请在此填写）"],
        rows, widths=[14, 14, 30, 30, 30],
        row_style=lambda i, row: (CHECK_FILL if row and str(row[0]) == "以下直径**没有粗牙规格**"
                                  else None))


def sheet_keys(wb):
    """
    12_平键键槽_GB1095：KEY-005「键槽宽度是否与轴径对应」/
    KEY-006「键槽宽公差是否按用途取 N9/JS9/P9」的判据表。
    """
    rows = []
    for upper, b, h, t1, t2 in gb.KEY_BY_SHAFT_DIAMETER:
        rows.append([f"≤{upper:g}", _num(b), _num(h), _num(t1), _num(t2), ""])
    rows.append([])
    rows.append(["键宽公差带（按连接类型）", "", "", "", "", ""])
    for fit, pair in gb.KEYWAY_TOL_BANDS.items():
        rows.append([fit, pair["轴槽"], pair["毂槽"],
                     gb.KEYWAY_FIT_BY_TYPE.get(fit, ""), "", ""])
    rows.append([])
    rows.append(["键宽 b 的极限偏差（mm）", "", "", "", "", ""])
    rows.append(["公差带", "上偏差", "下偏差", "适用键宽 b", "", ""])
    for band, table in gb.KEYWAY_WIDTH_LIMITS.items():
        if not table:
            rows.append([band, "（未取到）", "（未取到）",
                         "来源网页该列为图片，取不到文本 —— 不猜", "", ""])
            continue
        for b in sorted(table):
            lim = table[b]
            rows.append([band, _num(lim[0]), _num(lim[1]), _num(b), "", ""])
    return write_sheet(
        wb, "12_平键键槽_GB1095",
        "平键键槽剖面尺寸与键宽公差（判「键槽宽度/公差是否合规」用）",
        "数据来源：sw_qc_gb.KEY_BY_SHAFT_DIAMETER / KEYWAY_TOL_BANDS / "
        "KEYWAY_WIDTH_LIMITS　·　标准：GB/T 1095-2003、GB/T 1096-2003　·　"
        "核对状态：**键宽/键高/槽深已逐行自洽核过**（26 个轴径段）。"
        "⚠ **已知缺口**：来源网页上「轴 H9」「轴 N9」两列的**具体偏差数字是 GIF 图片**，"
        "取不到文本，故未收录（不猜）。"
        "但 KEY-006 判的是**公差带代号**（N9/JS9/P9，表头纯文本已确证），不受影响。",
        ["轴径 d(mm)", "键宽 b", "键高 h", "轴槽深 t1", "毂槽深 t2",
         "备注（请在此填写）"],
        rows, widths=[16, 10, 10, 12, 12, 30],
        row_style=lambda i, row: (CHECK_FILL if row and str(row[0]).startswith(
            ("键宽公差带", "键宽 b 的极限偏差", "公差带")) else None))


def sheet_rings(wb):
    """
    13_5c_挡圈_GB894_GB893：轴用/孔用弹性挡圈槽尺寸（判 RING-003/004/007 用）

    ⚠ 2026-10-25 用户反馈：10-24 版把「厚度 S、厚度偏差、沟槽直径偏差、最小边距 n」
      这几列误删了。现从 2026-10-23 审核版**原样恢复**，并与后来的
      「标准版本 / 核对状态」两列合并为 11 列。
      数据源：sw_qc_rings.py（轴用 56 档 3~100mm、孔用 55 档 8~100mm）。
    """
    import sw_qc_rings as RG

    # 2026-10-25：列名恢复成 2026-10-23 版原文（用户核对发现我改过名）
    SHAFT_HDR = ["轴径 d1（mm）", "挡圈内径 d3", "厚度 S", "厚度偏差",
                 "沟槽直径 d2", "沟槽直径偏差", "沟槽宽度 m", "沟槽最小边距 n",
                 "标准版本", "核对状态", "备注（请在此填写）"]
    HOLE_HDR = ["孔径 d1（mm）", "挡圈外径 d3", "厚度 S", "厚度偏差",
                "沟槽直径 d2", "沟槽直径偏差", "沟槽宽度 m", "沟槽最小边距 n",
                "标准版本", "核对状态", "备注（请在此填写）"]
    rows = []

    rows.append(["【轴用挡圈 GB/T 894.1-2017（A 型）】"] + [""] * 10)
    rows.append(list(SHAFT_HDR[:-1]) + [""])   # 备注列留空，避免回导误当批注
    for r in RG.SHAFT_RING_ROWS:
        rows.append(list(r[:8]) + ["GB/T 894.1-2017", "用户已确认 2017 版", ""])

    rows.append([])
    rows.append(["【孔用挡圈 GB/T 893.1-2017（A 型）】"] + [""] * 10)
    rows.append(list(HOLE_HDR[:-1]) + [""])
    for r in RG.HOLE_RING_ROWS:
        rows.append(list(r[:8]) + ["GB/T 893.1-2017",
                                   "⚠ 厚度按 2017 版核对，槽宽 m 待复核", ""])

    return write_sheet(
        wb, "13_5c_挡圈_GB894_GB893",
        "轴用/孔用弹性挡圈槽尺寸（判「挡圈槽径与槽宽是否合规」用）",
        "数据来源：sw_qc_rings.py（从 2026-10-23 审核版原样恢复）　·　"
        "轴用 GB/T 894.1-2017（用户已确认）；孔用 GB/T 893.1-2017（厚度已按新版核对，"
        "**槽宽 m 待你复核**，因为现行 2017 已改动部分厚度，槽宽可能随之变化）。"
        "列含：挡圈厚度 S 与厚度偏差、沟槽直径 d2 与直径偏差、槽宽 m、最小边距 n —— "
        "这几列是 2026-10-24 版被误删后由用户要求找回的。"
        "轴用覆盖 d1=3~100（56 档），孔用覆盖 d1=8~100（55 档）。",
        SHAFT_HDR, rows,
        widths=[14, 12, 10, 12, 12, 13, 12, 14, 18, 30, 26],
        row_style=lambda i, row: (CHECK_FILL if row and str(row[0]).startswith(
            ("【轴用", "【孔用")) else None))


def sheet_deviations(wb):
    """
    14_基本偏差_GB1800.2：FIT-004「标注偏差是否等于该公差带标准偏差」/
    FIT-005「孔轴能否形成目标配合」的判据表。

    每个代号带置信度与是否启用（孔 K/M/N/P/R/S 已停用，见代码注释）。
    """
    ranges = ["≤3", ">3~6", ">6~10", ">10~18", ">18~30", ">30~50", ">50~80",
              ">80~120", ">120~180", ">180~250", ">250~315", ">315~400", ">400~500"]
    # 注意：程序自己写的列一律叫「工具提示」，**不要叫「备注」** ——
    # 回导会把「备注」列当人工批注读，叫备注就会被误报成"你改了 54 处"。
    header = ["代号 / 分类", "给的是", "置信度"] + ranges[:7] + ["工具提示", ""]

    rows = [["【轴】基本偏差（μm）", "", "", "", "", "", "", "", "", "", ""],
            header]
    for code, entry in gb.SHAFT_FUNDAMENTAL_DEVIATION.items():
        vals = entry["values"][:7]
        rows.append([code, entry["kind"], _CONF_LABEL.get(entry["confidence"], ""),
                     *[_num(v) if v is not None else "（无）" for v in vals],
                     "两版一致，可放心用" if entry["confidence"] == "high"
                     else "含推导值或仅单源 → 引用前复核", ""])
    rows.append([])
    rows.append(["【孔】基本偏差（μm）", "", "", "", "", "", "", "", "", "", ""])
    rows.append(header)
    for code, entry in gb.HOLE_FUNDAMENTAL_DEVIATION.items():
        vals = entry["values"][:7]
        note = ""
        if entry.get("verified") is False:
            note = "⚠ 等级归属存疑（P 列 D=25 给 −22，教材例题 25P7 是 −14，Δ 换算 −20，三者不相容）→ **已停用**"
        elif entry["confidence"] == "high":
            note = "与轴 f~h 镜像，两源一致"
        rows.append([code, entry["kind"], _CONF_LABEL.get(entry["confidence"], ""),
                     *[_num(v) if v is not None else "（无）" for v in vals],
                     note, ""])
    rows.append([])
    rows.append(["旧版 GB/T 1800.1-1999 轴 k6/m6/n6 极限偏差 (es, ei) μm", "", "",
                 "", "", "", "", "", "", "", ""])
    for code, table in gb.LEGACY_1999_SHAFT_LIMITS.items():
        rows.append([code, "", "旧版", *[f"{es:+d}/{ei:+d}" for es, ei in table[:7]],
                     "用于识别按旧标准标注的图纸 → 报「偏差不符」前先查这里", ""])
    return write_sheet(
        wb, "1b_基本偏差_GB1800.2",
        "极限偏差的基本偏差（判「标注偏差/配合性质」用）　单位：μm",
        "数据来源：sw_qc_gb.SHAFT_FUNDAMENTAL_DEVIATION / HOLE_FUNDAMENTAL_DEVIATION / "
        "LEGACY_1999_SHAFT_LIMITS　·　标准：GB/T 1800.1/1800.2-2020　·　"
        "**版本口径：以 2020 现行为准**（用户 2026-10-14 拍板）。"
        "k/m/n/r/s 在 1999 与 2020 两版之间普遍不同（m6 在 400~500mm 段差 17µm），"
        "故另存旧版 k6/m6/n6 供识别旧标准图纸 —— 报「偏差不符」前必须先查旧版，"
        "命中就改口径为提示而非错误。"
        "实测：φ30k6 现行 2020 版 = +0.014/+0.001；1999 版 = +0.002/−0.002。"
        "⚠ **孔 K/M/N/P/R/S 已停用**（等级归属存疑，见备注列）；"
        "轴 s>50mm 与 >500mm 段为「中/低」置信，请优先复核。",
        ["代号 / 分类", "偏差类型", "置信度"] + ranges[:7] + ["工具提示",
                                                              "备注（请在此填写）"],
        rows, widths=[30, 10, 16] + [10] * 7 + [40, 30])




# ==========================================================================
# 1c_极限偏差速查（按配合性质分组，轴与孔都有）
# ==========================================================================
def sheet_deviation_quick(wb):
    """
    极限偏差速查：直接给出常用配合代号的**上/下偏差**（mm），拿图纸一比就知道对不对。

    用户要求（沿袭）：
      「轴的公差值 r/s 可以不考虑，优先 g」「孔优先考虑 H」
      「孔和轴过渡配合 公差值 也可以加进去」
    所以按配合性质分组：间隙 → 过渡 → 过盈，过渡段孔轴两侧都收。
    """
    # (代号, 配合性质, 说明)  大写=孔，小写=轴
    SPECS = [
        ("H7", "间隙", "孔（基准孔，最常用）"),
        ("g6", "间隙", "轴（H7/g6 滑动配合）"),
        ("h6", "间隙", "轴（H7/h6 定位配合）"),
        ("f7", "间隙", "轴"),
        ("f8", "间隙", "轴"),
        ("e8", "间隙", "轴"),
        ("d9", "间隙", "轴"),
        ("G7", "间隙", "孔（基轴制）"),
        ("F8", "间隙", "孔（基轴制）"),
        ("H8", "间隙", "孔"),
        ("H9", "间隙", "孔"),
        ("H11", "间隙", "孔（粗配合）"),
        ("h7", "间隙", "轴"),
        ("h9", "间隙", "轴"),
        ("h11", "间隙", "轴"),
        ("js6", "过渡", "轴（对称 ±IT/2）"),
        ("js7", "过渡", "轴（对称 ±IT/2）"),
        ("JS7", "过渡", "孔（对称 ±IT/2）"),
        ("JS8", "过渡", "孔（对称 ±IT/2）"),
        # 2026-10-25：恢复 K7/M7/N7/P7 四列（用户核对发现丢失）。
        # 之前停用是因为 Δ 换算算错；现已按 GB/T 1800.2 修正（Δ=IT(n)−IT(n−1)），
        # 实测 φ25：K7=+6/−15、M7=0/−21、N7=−7/−28、P7=−14/−35，与标准一致。
        ("K7", "过渡", "孔（Δ 已修正）"),
        ("M7", "过渡", "孔（Δ 已修正）"),
        ("N7", "过渡", "孔（Δ 已修正）"),
        ("P7", "过盈", "孔（Δ 已修正）"),
        ("k6", "过渡", "轴（H7/k6 最常用过渡）"),
        ("k7", "过渡", "轴"),
        ("m6", "过渡", "轴"),
        ("m7", "过渡", "轴"),
        ("n6", "过渡", "轴（H7/n6，较紧）"),
        ("n7", "过渡", "轴"),
        ("p6", "过盈", "轴（H7/p6 压入）"),
        ("r6", "过盈", "轴（你说可以不考虑）"),
        ("s6", "过盈", "轴（你说可以不考虑）"),
    ]
    codes = [s[0] for s in SPECS]
    bounds = [f"≤{gb._IT_BOUNDS[0]}"] + \
             [f">{gb._IT_BOUNDS[i - 1]}~{gb._IT_BOUNDS[i]}"
              for i in range(1, len(gb._IT_BOUNDS))]

    def fmt(v_um):
        """μm → mm。js/JS 的半微米值要保留 4 位（φ30 js6 = ±0.0065）。"""
        if abs(v_um) < 0.5:
            return "0"
        whole = abs(v_um - round(v_um)) < 1e-9
        return f"{v_um / 1000.0:+.{3 if whole else 4}f}"

    ws = wb.create_sheet("1c_极限偏差速查")
    ws["A1"] = "常用配合代号的极限偏差（mm）—— 按配合性质分组，轴与孔都有"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("数据来源：工作表 1（IT 值）× 工作表 1b（基本偏差），按 GB/T 1800.2 换算法则算出"
                "　·　核对状态：**待你核对**。格式「上偏差 / 下偏差」。"
                "用法：图纸写 φ30g6，查 >18~30 段 g6 一格，应得 -0.007 / -0.020，不一致就是图上错了。"
                "**大写=孔，小写=轴**。分三段：间隙 → 过渡 → 过盈。"
                "**2026-10-25 恢复 K7/M7/N7/P7**：之前因 Δ 换算错误被停用，"
                "现按 GB/T 1800.2 修正（Δ=IT(n)−IT(n−1)，K/M/N 等级≤IT8、P~ZC 等级≤IT7 时加 Δ），"
                "φ25 实测 K7=+6/−15、M7=0/−21、N7=−7/−28、P7=−14/−35，与标准一致。"
                "R/S 仍停用（等级归属存疑）。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:H2")
    for c in range(1, 9):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 62

    hdr_row = 4
    for c, h in enumerate(["尺寸段 (mm)"] + codes, 1):
        cell = ws.cell(row=hdr_row, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for c, (code, kind, _n) in enumerate([("", "", "")] + SPECS, 1):
        cell = ws.cell(row=hdr_row + 1, column=c, value=kind)
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center")
        cell.font = Font(size=9, bold=True,
                         color={"间隙": "067647", "过渡": "B54708",
                                "过盈": "C01048"}.get(kind, "667085"))
        if kind:
            cell.fill = {"间隙": OK_FILL, "过渡": CHECK_FILL,
                         "过盈": PatternFill("solid", fgColor="FEE4E2")}[kind]
    for c, (code, _k, note) in enumerate([("", "", "")] + SPECS, 1):
        cell = ws.cell(row=hdr_row + 2, column=c, value=note)
        cell.border = BORDER
        cell.font = Font(size=8, color="667085")
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.row_dimensions[hdr_row + 2].height = 26

    r = hdr_row + 3
    for i, seg in enumerate(bounds):
        nominal = gb._IT_BOUNDS[i]
        cell = ws.cell(row=r, column=1, value=seg)
        cell.border = BORDER
        for j, code in enumerate(codes, 2):
            iv = gb.limit_deviation(code, nominal)
            c = ws.cell(row=r, column=j,
                        value="—" if iv is None else f"{fmt(iv[1])} / {fmt(iv[0])}")
            c.border = BORDER
            c.alignment = Alignment(horizontal="center")
        r += 1

    ws.column_dimensions["A"].width = 14
    for c in range(2, len(codes) + 2):
        ws.column_dimensions[get_column_letter(c)].width = 15

    r += 1
    for line in (
        "注 1：本表是**算出来的**（IT 值 × 基本偏差），能自查，但不能替代对照标准正本核对 1 表与 1b 表。",
        "注 2：孔 K7/M7/N7/P7 等因等级归属存疑已停用 → 显示「—」，工具不猜。",
        "注 3：过渡段的判定还取决于具体等级组合，本表按基本偏差字母组给出最常见对应关系。",
        "注 4：r6/s6 放最后 —— 你说可以不考虑，需要时再看。",
    ):
        ws.cell(row=r, column=1, value=line).font = Font(size=9, color="475467")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
    return ws


# ==========================================================================
# 3b_几何公差数系_GB1184B（附录 B 的 B1~B5，一个表内分 5 块）
# ==========================================================================
def sheet_form_series(wb):
    """
    GB/T 1184-1996 附录 B 几何公差数系表（B1~B5）。

    为什么放一个工作表分 5 块：四张表主参数/尺寸段/等级范围都不同，硬合并会大量空格且极易看错行；
    放同一页便于对照，又不会让工作簿多出四张表。
    """
    ws = wb.create_sheet("3b_几何公差数系_GB1184B")
    ws["A1"] = "GB/T 1184-1996 附录 B　几何公差数系表（单位 μm）"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("数据来源：用户提供的 GB/T 1184-1996 标准原文截图（表 B1~B5），逐格转录；"
                "已用「结构自洽 + B3/B1 倍数关系 + 抽查原图格子」校验，但**仍请抽查关键格**。"
                "★这里的等级是「**几何公差等级 0~12**」，**不是 IT 等级**（IT 属 GB/T 1800.1）。"
                "B2 从 0 级起，B1/B3/B4 从 1 级起 —— 标准原本如此。"
                "用途：判断图上**注出**的几何公差值是否落在数系上"
                "（与「未注几何公差」是两张不同表，见 3_几何公差_GB1184）。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:N2")
    for c in range(1, 15):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 78

    r = 4
    grades_all = gb.GB_T_1184_GRADES_0_12
    for key in ("B1", "B2", "B3", "B4"):
        t = gb.GB_T_1184_SERIES[key]
        ws.cell(row=r, column=1,
                value=f"表 {key}　{t['name']}　（主参数：{t['main_param']}）").font = SEC_FONT
        r += 1
        for c, h in enumerate(["尺寸段 (mm)"] + [f"{g} 级" for g in grades_all], 1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")
        r += 1
        grades = t["grades"]
        for upper, vals in t["segments"]:
            cell = ws.cell(row=r, column=1, value=f"≤{upper}")
            cell.border = BORDER
            for gi, g in enumerate(grades_all):
                v = vals[grades.index(g)] if g in grades else "—"
                c = ws.cell(row=r, column=2 + gi, value=v)
                c.border = BORDER
                c.alignment = Alignment(horizontal="center")
                if g not in grades:
                    c.fill = PatternFill("solid", fgColor="F2F4F7")
            r += 1
        r += 1

    ws.cell(row=r, column=1, value="表 B5　位置度数系（μm）").font = SEC_FONT
    r += 1
    for c, v in enumerate(gb.GB_T_1184_B5_POSITION_SERIES, 1):
        cell = ws.cell(row=r, column=c, value=v)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center")
    r += 1
    for c, v in enumerate(gb.GB_T_1184_B5_POSITION_SERIES, 1):
        cell = ws.cell(row=r, column=c, value=f"{v}×10ⁿ")
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center")
    r += 1
    ws.cell(row=r, column=1, value="注：n 为正整数。").font = Font(size=9, color="475467")
    r += 2

    for line in (
        "注 1：本表是**图纸注出**几何公差值的判定依据；«未注»几何公差请查 3_几何公差_GB1184。",
        "注 2：B3（平行/垂直/倾斜）比 B1（直线/平面）粗，实测倍数 1.8~2.7，**不是**严格 2 倍。",
        "注 3：B4 前两段（≤1、>1~3）在 1~8 级取值相同，9~12 级不同 —— 标准原文如此。",
        "注 4：灰底「—」= 该表无此等级（B1/B3/B4 无 0 级）。",
        "注 5：工程上常有图纸不按数系取值（如 0.01、0.05）；工具目前只提供「是否在数系内」判断，尚未据此报警。",
    ):
        ws.cell(row=r, column=1, value=line).font = Font(size=9, color="475467")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        r += 1

    ws.column_dimensions["A"].width = 14
    for c in range(2, 15):
        ws.column_dimensions[get_column_letter(c)].width = 8
    return ws

# ==========================================================================
# 各张表
# ==========================================================================
def sheet_instructions(wb, unverified, sheet_index):
    ws = wb.create_sheet("填表说明")
    ws["A1"] = "工程图 QC 工具 — 国标规格库核对表"
    ws["A1"].font = Font(bold=True, size=16, color=BRAND)
    ws["A2"] = (f"导出时间：{datetime.datetime.now():%Y-%m-%d %H:%M}　·　"
                f"数据来源：sw-qc-ai\\sw_qc_gb.py 与 rules\\*.yaml（自动导出，非手抄）")
    ws["A2"].font = NOTE_FONT

    r = 4

    def section(text):
        nonlocal r
        ws.cell(row=r, column=1, value=text).font = SEC_FONT
        r += 1

    def item(k, v):
        nonlocal r
        a = ws.cell(row=r, column=1, value=k)
        a.font = Font(bold=True, size=10)
        a.alignment = Alignment(vertical="top")
        b = ws.cell(row=r, column=2, value=v)
        b.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = max(16, 15 * (1 + len(str(v)) // 46))
        r += 1

    section("一、这份表怎么用")
    item("这是什么",
         "工程图 QC 工具里「国标规格库」的完整导出。这些数值直接决定检查结论，"
         "请你对照标准正本核对，并把贵司企标/常用值补进来。")
    item("怎么改",
         "直接在数值单元格里改成正确值。**表头和列顺序请不要改**，"
         "否则发回来重新导入时会错位。")
    item("想加内容",
         "整行新增完全可以（保持列数与顺序一致）。若某个等级/分段标准里还有值，"
         "也可在最右侧加一列，并在表头写清等级名。")
    item("备注列",
         "每张表最后一列都留了「备注」。写依据（如「按 GB/T 1804-2000 表 1」）"
         "或疑问都行 —— 有依据的数值才敢用于生产。")
    item("颜色含义",
         "绿色 = 代码里标记为已核对；黄色 = 代码里标记为未核对或未标核对状态。"
         "**黄色请优先复核。**")
    item("改完怎么办",
         "把这一份 xlsx 发回即可。我会按同样格式重新导入代码，"
         "并跑一遍 60 项回归测试确认没有改坏其它检查。")
    r += 1

    section("二、请优先复核的表（共 %d 张）" % len(unverified))
    hdr = ["表名", "标准", "代码中的核对状态", "为什么重要"]
    for c, h in enumerate(hdr, 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    for name, std, why in unverified:
        ws.cell(row=r, column=1, value=name).border = BORDER
        ws.cell(row=r, column=2, value=std).border = BORDER
        c3 = ws.cell(row=r, column=3, value="未核对")
        c3.fill = CHECK_FILL
        c3.border = BORDER
        c3.alignment = Alignment(horizontal="center")
        c4 = ws.cell(row=r, column=4, value=why)
        c4.border = BORDER
        c4.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = max(16, 15 * (1 + len(why) // 40))
        r += 1
    r += 1

    section("三、按你反馈新增的检查行为（2026-10-13）")
    item("① 统注也算已标注粗糙度",
         "图上写「其余 Ra3.2」「全部 1.6」「所有表面 6.3」「未注粗糙度」"
         "（数值在同段文字或邻近符号块里）都算**已规定粗糙度**，不再报「未检出」。"
         "只有符号、读不出数值也算已规定。")
    item("② 按零件类型分档：钣金件/塑胶件不查粗糙度",
         "你说的「钣金件和塑胶件一般不需要标注粗糙度，只有机加件需要」已写进工具："
         "新增 sw_qc_parttype.py，自动判定 机加件/钣金件/塑胶件/组合件，"
         "并在网页上显示**判定依据**、可一键改。"
         "判为钣金/塑胶件时不再报「粗糙度漏标」；组合件照常报（机加部分需要）。")
    item("③ 置信度不够就不下结论",
         "零件类型置信度 < 0.6 时判为「未知」，此时粗糙度检查**照常执行**"
         "（宁可多报也不静默放过），并在报告里注明类型未判定。")
    r += 1

    section("四、第 8 表：检查规则是怎么整合的（2026-10-13 确定）")
    item("整合目的",
         "把工具原有规则与《AI机械工程图审核规则库 V1》（117 条）整合成一份总目录，"
         "**以工具原有规则为准**，规则库用于补充。整合结果维护在 rule_catalog.py 里。")
    item("① 只走 DXF 几何路线",
         "规则库中要求「看图」的（视觉判断/图像 OCR）**暂缓**，但保留在表里不丢 —— "
         "「实现方式」列会标「需看图（暂缓）」。")
    item("② 语义理解要做",
         "需要理解语义的规则（技术要求自相矛盾、材料与热处理冲突等）**保留**，"
         "标为「需接AI模型」，等接入模型后启用。")
    item("③ OCR / 联网数据库 不纳入",
         "由你明确排除。相关规则在表里标为「不纳入」并注明原因。")
    item("④ 企业规则模板不纳入",
         "规则库里的「企业规则模板」不并入，企业标准另行处理。")
    item("⑤ 图框类检查关闭，材料保留",
         "规则库声明「不含图框完整性」，据此**关闭** 图号/比例/张数/签署栏；"
         "**保留材料**（规则库有 MAT-001）。零件名称按你的要求保留并定为「严重」——"
         "这一条与「关闭图框类」有张力，已在行内标注待你确认。")
    item("⑥ 严重级别：只有严重/一般/轻微",
         "原规则库的「错误 / 提醒 / 建议」这套叫法**已取消**，全表统一用"
         "**严重 / 一般 / 轻微**（对应报告里的 🔴/🟡/🟢）。"
         "具体级别由你逐条批注确定（见 rule_catalog.SEVERITY_POLICY），"
         "第一批 9 项已按你在冲突对照图上的红字标定。")
    item("⑦ FIT-009 与 CORE-TOL-TIGHT 合并",
         "两者是同一件事（公差宽窄），已合并为一条；严重级按工具、默认启用沿用工具。")
    item("怎么复核这一表",
         "重点看三列：「严重级别」是否符合你的判断、「默认启用」是否需要调整、"
         "「整合处理说明」里标注的待确认项。直接在单元格里改即可。")
    r += 1

    # ── 待确认清单（用户 2026-10-14 要求：把需要他核对的标出来，一个个过）──
    from rule_catalog import CONFIRM_TAGS, OPEN_QUESTIONS, confirmation_summary
    section("五、需要你确认的项 —— 第 8 表最后一列「待你确认」")
    item("怎么用",
         "第 8 表最后一列标了要你拍板的项。**在这一列做筛选**，一条条过即可；"
         "确认完把「备注」列写好发回，我按最终口径落进代码。")
    need_count = sum(confirmation_summary().values())
    item("规模", f"162 条规则里，共 {need_count} 处标记需要你过目"
         "（同一条可能有多项标记）。")
    r += 1
    _hdr = ["标记", "含义", "怎么处理"]
    for c, h in enumerate(_hdr, 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    _sm = confirmation_summary()
    for tag, name, how in CONFIRM_TAGS:
        ws.cell(row=r, column=1, value=f"{tag} {name}").border = BORDER
        ws.cell(row=r, column=2, value=f"{_sm.get(tag, 0)} 条").border = BORDER
        c3 = ws.cell(row=r, column=3, value=how)
        c3.border = BORDER
        c3.alignment = Alignment(wrap_text=True, vertical="center")
        r += 1
    r += 1
    item("⚠ 待裁决的 7 项（最需要你先看）",
         "这 7 项是有明确选择题的；其余标记只是「请你复核」：")
    for rid, q in OPEN_QUESTIONS.items():
        a = ws.cell(row=r, column=1, value=rid)
        a.font = Font(bold=True, size=10)
        a.alignment = Alignment(vertical="top")
        b = ws.cell(row=r, column=2, value=q)
        b.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.row_dimensions[r].height = max(16, 15 * (1 + len(q) // 48))
        r += 1
    r += 1

    section("六、本次导出包含 %d 张数据表" % len(sheet_index))
    hdr2 = ["序号", "工作表名", "内容", "核对状态"]
    for c, h in enumerate(hdr2, 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    for no, name, desc, status in sheet_index:
        ws.cell(row=r, column=1, value=no).border = BORDER
        ws.cell(row=r, column=2, value=name).border = BORDER
        ws.cell(row=r, column=3, value=desc).border = BORDER
        c4 = ws.cell(row=r, column=4, value=status)
        c4.border = BORDER
        c4.fill = OK_FILL if status == "已核对" else CHECK_FILL
        c4.alignment = Alignment(horizontal="center")
        r += 1

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 42
    ws.column_dimensions["D"].width = 40
    return ws


def sheet_it(wb):
    bounds = gb._IT_BOUNDS
    grades = list(gb.GB_T_1800_IT)
    headers = ["尺寸段(mm)"] + [
        f"{g} ★" if g in gb.PREFERRED_GRADES else g for g in grades] + ["备注"]
    rows = []
    for i in range(len(bounds)):
        rows.append([_seg_text(bounds, i)] + [gb.GB_T_1800_IT[g][i] for g in grades] + [""])
    return write_sheet(
        wb, "1_IT公差_GB1800.1",
        "GB/T 1800.1-2020（等同 ISO 286-1）标准公差数值 IT　单位：μm",
        "数据来源：sw_qc_gb.GB_T_1800_IT　·　核对状态：**代码中未标注核对标记**，建议对照标准正本复核。"
        "★ = 代码视为「常用优先等级」。这张表同时用于两处：① 给出配合代号的标准偏差（如 φ30H7 → +0.021/0）；"
        "② 判断图上自填的数值公差属于什么加工难度（v0.10 的「公差过严提醒」）。",
        headers, rows,
        widths=[14] + [9] * len(grades) + [30],
        row_style=lambda i, row: CHECK_FILL)


def sheet_linear(wb):
    bounds = gb._LINEAR_BOUNDS
    grades = ["f", "m", "c", "v"]
    headers = ["尺寸段(mm)"] + [f"{g}（±mm）" for g in grades] + ["备注"]
    rows = []
    for i in range(len(bounds)):
        rows.append([_seg_text(bounds, i)] +
                    [_num(gb.GB_T_1804_LINEAR[g]["values"][i]) for g in grades] + [""])
    ok = all(gb.GB_T_1804_LINEAR[g]["verified"] for g in grades)
    return write_sheet(
        wb, "2_线性一般公差_GB1804",
        "GB/T 1804-2000 一般公差：线性尺寸的极限偏差（表中为 ± 值，单位 mm）",
        f"数据来源：sw_qc_gb.GB_T_1804_LINEAR　·　核对状态：{'已核对' if ok else '未核对'}。"
        "用于：图上没单独标公差的线性尺寸，按此表给出默认公差（技术要求声明等级，如 GB1804-m）。",
        headers, rows,
        widths=[14, 13, 13, 13, 13, 30],
        row_style=lambda i, row: OK_FILL if ok else CHECK_FILL)


def sheet_angle(wb):
    bounds = gb._ANGLE_BOUNDS
    grades = ["f", "m", "c", "v"]
    headers = ["短边长度(mm)"] + [f"{g}（′）" for g in grades] + ["备注"]
    rows = []
    for i in range(len(bounds) + 1):        # 最后一档是「>400」
        txt = _seg_text(bounds, i) if i < len(bounds) else f">{bounds[-1]:g}"
        rows.append([txt] +
                    [_num(gb.GB_T_1804_ANGLE[g]["values"][i]) for g in grades] + [""])
    ok = all(gb.GB_T_1804_ANGLE[g]["verified"] for g in grades)
    return write_sheet(
        wb, "2_角度一般公差_GB1804",
        "GB/T 1804-2000 一般公差：角度尺寸的极限偏差　单位：分（′），1° = 60′",
        f"数据来源：sw_qc_gb.GB_T_1804_ANGLE　·　核对状态：{'已核对' if ok else '未核对'}。"
        "分段依据是「角度短边长度」，不是角度大小 —— 这是最容易看错的地方，请留意。",
        headers, rows,
        widths=[16, 12, 12, 12, 12, 30],
        row_style=lambda i, row: OK_FILL if ok else CHECK_FILL)


def sheet_radius(wb):
    bounds = gb._RADIUS_BOUNDS
    grades = ["f", "m", "c", "v"]
    headers = ["尺寸段(mm)"] + [f"{g}（±mm）" for g in grades] + ["备注"]
    rows = []
    for i in range(len(bounds)):
        rows.append([_seg_text(bounds, i)] +
                    [_num(gb.GB_T_1804_RADIUS[g]["values"][i]) for g in grades] + [""])
    ok = all(gb.GB_T_1804_RADIUS[g]["verified"] for g in grades)
    return write_sheet(
        wb, "2_倒圆倒角_GB1804",
        "GB/T 1804-2000 一般公差：倒圆半径与倒角高度　单位：mm",
        f"数据来源：sw_qc_gb.GB_T_1804_RADIUS　·　核对状态：{'已核对' if ok else '**未核对（代码里明确标了 verified=False）**'}。"
        "这张表用于「未注倒角/圆角」类判断（例如图上写『未注倒角 C1』时核对数值是否合理），请对照标准正本复核。",
        headers, rows,
        widths=[14, 13, 13, 13, 13, 30],
        row_style=lambda i, row: OK_FILL if ok else CHECK_FILL)


def sheet_geom(wb):
    headers = ["公差项目", "等级", "尺寸段(mm)", "尺寸上限(mm)",
               "未注公差值(mm)", "代码中已核对", "备注"]
    rows = []
    styles = []
    for kind, table in gb.GB_T_1184.items():
        ok = bool(table.get("verified"))
        bounds = table["bounds"]
        for grade in ("H", "K", "L"):
            vals = table.get(grade) or []
            for i, upper in enumerate(bounds):
                rows.append([kind, grade, _seg_text(bounds, i), upper,
                             vals[i] if i < len(vals) else "", "是" if ok else "否", ""])
                styles.append(OK_FILL if ok else CHECK_FILL)
    ws = write_sheet(
        wb, "3_几何公差_GB1184",
        "GB/T 1184-1996 几何公差未注公差值　单位：mm",
        "数据来源：sw_qc_gb.GB_T_1184　·　核对状态：直线度/平面度/垂直度=已核对；"
        "**倾斜度/同轴度/对称度/圆跳动=代码里明确标了 verified=False（未核对）**。"
        "注意：这几项的尺寸分段比其他项粗（只有 4 段，从 >100 起），请一并核对分段本身对不对。",
        headers, rows,
        widths=[12, 8, 16, 14, 16, 14, 30],
        row_style=lambda i, row: styles[i])
    return ws


def sheet_ra_series(wb):
    rows = [[v, ""] for v in gb.GB_T_131_RA_SERIES]
    return write_sheet(
        wb, "4_粗糙度Ra系列_GB131",
        "GB/T 131-2006 表面粗糙度 Ra 优先数系列　单位：μm",
        "数据来源：sw_qc_gb.GB_T_131_RA_SERIES　·　核对状态：代码中未标注核对标记，建议复核。"
        "用于：把图上写的 Ra 值吸附到优先数系列，并检查是否用了非优先值。"
        "若贵司标准里还有更细的档（如 0.008、0.0063），请补在下面。",
        ["Ra(μm)", "备注"], rows, widths=[14, 40])


def sheet_ra_reco(wb):
    rows = []
    for k, (lo, hi) in gb.RA_RECOMMENDATION.items():
        rows.append([k, _num(lo), _num(hi), ""])
    return write_sheet(
        wb, "4_粗糙度推荐区间",
        "特征/部位 → 推荐 Ra 区间　单位：μm",
        "数据来源：sw_qc_gb.RA_RECOMMENDATION　·　核对状态：**工程经验值，非标准强制值**。"
        "这类值各厂习惯不同，请按贵司实际加工能力修改。下限/上限留空表示「不标注或不加工」。",
        ["部位/特征", "Ra 下限(μm)", "Ra 上限(μm)", "备注"], rows,
        widths=[18, 14, 14, 40],
        row_style=lambda i, row: CHECK_FILL)


def sheet_feature_reco(wb):
    rows = []
    for feat, rec in gb.FEATURE_RECOMMENDATION.items():
        ra = rec.get("ra")
        ra_txt = f"{ra[0]}~{ra[1]}" if ra and ra[0] is not None else ""
        rows.append([feat, rec.get("size_tol") or "",
                     "、".join(rec.get("gdt") or []), ra_txt,
                     rec.get("note", ""), ""])
    return write_sheet(
        wb, "5_特征推荐标注",
        "机械特征 → 推荐尺寸公差 / 几何公差 / 粗糙度",
        "数据来源：sw_qc_gb.FEATURE_RECOMMENDATION　·　核对状态：**工程推荐值，可修改**。"
        "带「（推定）」的特征只在图纸文字出现「轴承」等字样时才启用 —— 工具刻意不主动推断零件类型。",
        ["特征", "推荐尺寸公差", "推荐几何公差", "推荐 Ra 区间", "说明", "备注"],
        rows, widths=[18, 14, 16, 14, 46, 24],
        row_style=lambda i, row: CHECK_FILL)


def sheet_fit(wb):
    rows = []
    for k, v in gb.FIT_RECOMMENDATION.items():
        rows.append(["配合部位推荐公差带", k, v, "", ""])
    for letter, (mode, val) in gb.FIT_DIRECTION.items():
        side = "孔" if letter.isupper() else "轴"
        desc = ("下偏差 0，上偏差 +IT" if mode == "lower"
                else "上偏差 0，下偏差 −IT")
        rows.append(["基本偏差方向", f"{letter}（{side}）", desc, val, ""])
    rows.append(["基本偏差方向", "JS / js", "对称偏差 ±IT/2", "", ""])
    rows.append(["说明", "其它字母（f/g/k/m/n/p/r/s…）",
                 "方向需查基本偏差表，工具目前只给公差值不判方向", "", ""])
    return write_sheet(
        wb, "6_配合推荐",
        "配合部位 → 推荐公差带　以及　基本偏差方向",
        "数据来源：sw_qc_gb.FIT_RECOMMENDATION / FIT_DIRECTION / fit_interval()　·　核对状态：建议复核。"
        "「基本偏差方向」决定了工具能不能把 H7 换算成具体偏差值（+0.021/0）；"
        "缺哪个字母就会退化成「只给公差值」。若贵司常用 f/g/k/m/n/p，建议补全。",
        ["类别", "键/代号", "推荐值/方向", "附加值", "备注"], rows,
        widths=[20, 26, 40, 10, 24],
        row_style=lambda i, row: CHECK_FILL if row[0] != "说明" else None)


def sheet_grade_ra(wb):
    """公差等级 → 经验 Ra 区间（直接调用函数取值，避免和代码脱节）。"""
    probes = ["IT5", "IT6", "IT7", "IT8", "IT9", "IT10", "IT11", "IT12", "IT13", "IT14",
              "H5", "H6", "H7", "H8", "H9", "H11", "h5", "h6", "h7", "h8", "h9", "h11",
              "N9", "P9", "f7", "g6"]
    rows = []
    for p in probes:
        got = gb.tolerance_grade_consistency(p)
        rows.append([p, got[0] if got else "", got[1] if got else "", ""])
    return write_sheet(
        wb, "6_公差等级与Ra匹配",
        "公差等级 → 经验 Ra 区间（用于「公差-粗糙度是否匹配」检查）",
        "数据来源：sw_qc_gb.tolerance_grade_consistency()（本表逐条调用该函数生成，与代码一致）"
        "　·　核对状态：**经验映射，非标准强制**。含义是「这个精度的尺寸，表面通常要做到多光」。",
        ["公差等级写法", "经验 Ra 下限(μm)", "经验 Ra 上限(μm)", "备注"], rows,
        widths=[18, 18, 18, 36],
        row_style=lambda i, row: CHECK_FILL)


def sheet_misc(wb):
    rows = []
    for s in gb.GB_T_4458_SHEET_SIZES:
        rows.append(["图样幅面", s, "", ""])
    for s in gb.GB_T_4458_SCALES:
        rows.append(["常用比例", s, "", ""])
    for field, aliases in gb.TITLE_BLOCK_REQUIRED_FIELDS.items():
        rows.append(["标题栏必备字段", field, "、".join(aliases), ""])
    for p in gb.THREAD_PREFIXES:
        rows.append(["螺纹代号前缀", p, "", ""])
    rows.append(["剖切符号可用字母", "".join(gb.SECTION_LETTERS),
                 "按标准不使用 I、O、Z（易与数字 1、0、2 混淆）", ""])
    return write_sheet(
        wb, "7_图样幅面比例等_GB4458",
        "图样幅面 / 常用比例 / 标题栏必备字段 / 螺纹代号 / 剖切符号字母",
        "数据来源：sw_qc_gb.GB_T_4458_* / TITLE_BLOCK_REQUIRED_FIELDS / THREAD_PREFIXES / SECTION_LETTERS"
        "　·　核对状态：建议复核（尤其「常用比例」和「标题栏必备字段」是否覆盖贵司模板）。",
        ["类别", "值", "说明/别名", "备注"], rows,
        widths=[20, 46, 46, 24],
        row_style=lambda i, row: CHECK_FILL)


def sheet_surface_treatment(wb):
    """
    表面处理耐蚀性表（用户 2026-10-13 提出）。

    用户原话：「技术要求中一般会写表面处理……会不会有不耐腐蚀、不耐生锈的风险……
    比如我写个表面发黑，这是不是要做提醒，发黑引起容易生锈的风险」

    这是**工程经验值**（不是某个国标的规定），所以必须让工程师核对并按本厂实际修改。
    """
    rows = []
    order = {"弱": 0, "中": 1, "强": 2}
    remarks = getattr(gb, "SURFACE_TREATMENT_REMARKS", {}) or {}
    for name, info in sorted(gb.SURFACE_TREATMENT_CORROSION.items(),
                             key=lambda kv: (order.get(kv[1].get("level"), 9), kv[0])):
        rows.append([
            name,
            info.get("level", ""),
            "、".join(info.get("requires") or []) or "—",
            info.get("note", ""),
            # 「备注」列回显工程师核对时写的话 —— 回导后这里就能看到，
            # 一眼确认工具确实收下了他的批注。
            remarks.get(name, ""),
        ])
    return write_sheet(
        wb, "4b_表面处理耐蚀性",
        "表面处理 → 耐蚀性等级与必须配套的措施（工程经验值）",
        "数据来源：sw_qc_gb.SURFACE_TREATMENT_CORROSION　·　核对状态：**工程经验值，非国标规定**，"
        "请按贵司实际工艺能力核对与补充。用法：① 本地查表判断「该处理耐蚀够不够、"
        "配套措施写没写」（规则 CORE-SURF-WEAK）；② 给 AI 提供事实基础，避免模型凭印象乱说。"
        "「弱」等级的处理若图上没写配套措施，工具会提醒 —— 例如只写『发黑』没写『浸油』。",
        ["表面处理", "耐蚀性等级", "必须配套的措施", "说明", "备注（请在此填写）"],
        rows, widths=[16, 12, 26, 62, 30],
        row_style=lambda i, row: (CHECK_FILL if row[1] == "弱"
                                  else (OK_FILL if row[1] == "强" else None)))


def sheet_ai_policy(wb):
    """
    第 10 表：AI 检查规范。

    用户 2026-10-13 要求：「AI 语义硬超时 20 秒，但必须控制 AI 回复内容，
    简单，明了，这个也要写进规则库里面」—— 所以规范本身也是规则库的一部分，
    实现见 sw_qc_ai.py，两边必须一致。
    """
    from rule_catalog import AI_POLICY, AI_SEMANTIC_RULES

    ws = wb.create_sheet("10_AI检查规范")
    ws["A1"] = "AI 语义检查规范（硬规定）"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("数据来源：rule_catalog.AI_POLICY / AI_SEMANTIC_RULES　·　实现：sw_qc_ai.py。"
                "这些是**硬规定**不是建议；改这里必须同步改实现，否则报告里的行为会对不上。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:C2")
    for c in range(1, 4):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 32

    r = 4
    ws.cell(row=r, column=1, value="一、硬规定").font = SEC_FONT
    r += 1
    for c, h in enumerate(["规定项", "内容", "为什么"], 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    for item, rule, why in AI_POLICY:
        ws.cell(row=r, column=1, value=item).border = BORDER
        ws.cell(row=r, column=1).font = Font(bold=True, size=10)
        c2 = ws.cell(row=r, column=2, value=rule)
        c2.border = BORDER
        c2.alignment = Alignment(wrap_text=True, vertical="center")
        c3 = ws.cell(row=r, column=3, value=why)
        c3.border = BORDER
        c3.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = max(16, 15 * (1 + max(len(str(rule)) // 42,
                                                            len(str(why)) // 34)))
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="二、AI 负责的 %d 条规则（**只有这些**）"
            % len(AI_SEMANTIC_RULES)).font = SEC_FONT
    r += 1
    for c, h in enumerate(["规则ID", "检查项", "说明"], 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    for rid, item, desc in AI_SEMANTIC_RULES:
        ws.cell(row=r, column=1, value=rid).border = BORDER
        ws.cell(row=r, column=2, value=item).border = BORDER
        c3 = ws.cell(row=r, column=3, value=desc)
        c3.border = BORDER
        c3.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = max(16, 15 * (1 + len(str(desc)) // 60))
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="三、怎么启用").font = SEC_FONT
    r += 1
    for line in [
        "在包根目录（与 sw-qc-ai 同级）新建 qc_ai_config.json：",
        '{ "enabled": true, "base_url": "https://api.deepseek.com/v1",',
        '  "model": "deepseek-chat", "api_key": "sk-你的密钥", "timeout": 20 }',
        "也可以用环境变量：QC_AI_API_KEY / QC_AI_BASE_URL / QC_AI_MODEL / QC_AI_TIMEOUT",
        "没配密钥时本模块完全不动作 —— 工具行为与以前一致，不会报错。",
        "命令行可用 --ai 强制启用、--no-ai 本次不做。",
    ]:
        ws.cell(row=r, column=1, value=line).font = Font(size=10, color="1F2933")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        r += 1

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 62
    ws.column_dimensions["C"].width = 46
    return ws


def sheet_rules(wb):
    """
    第 8 表：**整合后的检查规则总目录**。

    用户 2026-10-13 要求：把工具原有规则与《AI机械工程图审核规则库 V1》整合，
    以工具原有规则为准。整合结果在 rule_catalog.py 里维护，这里只负责导出 ——
    保证「核对表里看到的」与「以后生成 YAML 用的」是同一份数据。
    """
    from rule_catalog import (COLUMNS, _WIDTH_BY_KEY, DISPLAY_ORDER,
                              REVIEWER_COLUMNS, rows as catalog_rows, stats)
    _HDR = {c[0]: c[1] for c in COLUMNS}
    _HDR["cat"] = "类别(审图结果)"      # cat 只用于显示，不在 COLUMNS 存储列里
    headers = [_HDR[k] for k in DISPLAY_ORDER]
    widths = [_WIDTH_BY_KEY[k] for k in DISPLAY_ORDER]
    data = catalog_rows()
    st = stats()
    src = st["按来源"]
    on_idx = DISPLAY_ORDER.index("on")
    note = (
        f"数据来源：rule_catalog.py（工具原有规则 ×《AI机械工程图审核规则库 V1》整合）"
        f"　·　共 {st['总条数']} 条　·　"
        f"规则库V1 {src.get('规则库V1', 0)} 条 / 工具原有 {src.get('工具原有', 0)} 条 / "
        f"两边合并 {src.get('合并', 0)} 条。"
        f"**前 {REVIEWER_COLUMNS} 列（深蓝表头）是给你审的**；"
        "后面 2 列（灰表头：来源、工具现有规则）是程序追溯用，审图时不用管。"
        "严重级别只有 严重/一般/轻微 三级。"
        "黄色行 = 默认启用为「否」（暂缓 / 待开发 / 需接模型 / 已按要求关闭）。"
        "「备注（请在此填写）」是留给你的空白列；「规则ID」请不要改（它是引用键）。"
    )

    def style(i, row):
        return CHECK_FILL if row[on_idx] == "否" else None

    return write_sheet(wb, "8_检查规则预设",
                       "检查规则总目录（工具原有规则 × 审图规则库 V1 整合结果）",
                       note, headers, data, widths=widths, row_style=style,
                       impl_from=REVIEWER_COLUMNS + 1)


def sheet_rule_ids(wb):
    """
    第 9 表：规则 ID 说明 —— 解释编号体系怎么划分、干什么用。

    用户问过「这些规则ID是什么意思、怎么划分的」，所以把说明**做进表里**，
    而不是只留在对话里。条数由 rule_catalog 现场统计，不会与实际规则脱节。
    """
    from rule_catalog import ID_PREFIX_DOC, id_prefix_counts
    counts = id_prefix_counts()

    ws = wb.create_sheet("9_规则ID说明")
    ws["A1"] = "规则 ID 说明 —— 编号怎么划分、干什么用"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("规则 ID 是每条规则的唯一编号，作用类似零件的「图号」。"
                "本表的条数由 rule_catalog.py 现场统计，与实际规则一致。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:D2")
    for c in range(1, 5):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 30

    r = 4

    def sec(text):
        nonlocal r
        ws.cell(row=r, column=1, value=text).font = SEC_FONT
        r += 1

    def bullets(items):
        nonlocal r
        for it in items:
            ws.cell(row=r, column=1, value="•").alignment = \
                Alignment(horizontal="center", vertical="top")
            v = ws.cell(row=r, column=2, value=it)
            v.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
            ws.row_dimensions[r].height = max(16, 15 * (1 + len(it) // 55))
            r += 1

    def table(headers, rows, center_cols=(1, 3)):
        nonlocal r
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")
        r += 1
        for row in rows:
            for c, v in enumerate(row, 1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border = BORDER
                cell.alignment = Alignment(
                    vertical="center", wrap_text=(c == len(headers)),
                    horizontal="center" if c in center_cols else "left")
            r += 1

    sec("一、规则 ID 是干什么用的")
    bullets([
        "唯一编号，作用类似零件的「图号」—— 用来精确指代某一条规则。",
        "报告里会印出来（如 CORE-DIM-GEOM），一看就知道是哪条规则报的。",
        "网页上的「这类无误 / 整类无误」按它记忆：你点过一次的，以后同类不再报。",
        "代码与 rules/*.yaml 靠它引用规则；出问题时能精确定位到文件与函数。",
        "改名字、改文案都不影响 ID —— 历史记录与白名单因此能长期稳定。",
    ])
    r += 1

    sec("二、编号怎么划分：模块英文缩写 + 三位序号（例：DIM-007）")
    bullets([
        "DIM-007 = 「尺寸完整性（dimension）」模块的第 7 条。",
        "序号只是顺次分配，**不代表优先级** —— 优先级看「优先级」列（P1~P4）。",
        "下表 14 个前缀来自你那份《审图规则库 V1》，本工具沿用，未做改动。",
    ])
    lib_rows = []
    for pre, (mod, desc) in ID_PREFIX_DOC.items():
        if pre not in counts or mod.startswith("（"):
            continue        # 工具原有的前缀放到第三节
        lib_rows.append([pre, mod, len(counts[pre]), desc])
    lib_rows.sort(key=lambda x: -x[2])
    table(["前缀", "中文模块", "条数", "含义（英文全称 / 覆盖内容）"], lib_rows)
    r += 1

    sec("三、为什么还有另一套 ID（CORE / GB / AUTO / GEAR / …）")
    bullets([
        "这些是**工具原有规则**。整合时按「以工具为主」保留了原名，"
        "所以总目录里同时存在两套编号 —— 这是刻意的，便于追溯来源。",
        "CORE- 写在代码里（sw_qc_checks.py）；GB- 写在 rules/*.yaml；"
        "AUTO-/GEAR-/HYD-/MOLD-/SM-/WELD- 是行业预设。",
        "看 ID 就能知道：这条规则来自哪边、该去哪个文件改。",
    ])
    tool_rows = []
    for pre, (mod, desc) in ID_PREFIX_DOC.items():
        if pre not in counts:
            continue
        if not (mod.startswith("（") or pre in ("AUTO", "GEAR", "HYD", "MOLD", "SM", "WELD")):
            continue
        tool_rows.append([pre, mod, len(counts[pre]), desc])
    tool_rows.sort(key=lambda x: -x[2])
    table(["前缀", "归属", "条数", "说明"], tool_rows)
    r += 1

    sec("四、两点提醒")
    bullets([
        "「规则ID」这一列**请不要改**：白名单里已记的条目、代码引用都靠它对应，"
        "改了会失联。确实要改编号请告诉我，我会连代码与白名单一起改。",
        "想按模块筛选或统计，直接用「一级模块」列做筛选即可 —— "
        "前缀与一级模块是一一对应的（工具原有那几条除外）。",
    ])

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 62
    return ws


# ==========================================================================
def sheet_tg2(wb):
    """GB/T 47067-2026 表2：尺寸允许极限（W 列）"""
    import sw_qc_mt as M
    headers = ["公差等级"] + M.TG2_W_SIZES
    rows = []
    for g in range(1, 10):
        rows.append([f"TG{g}"] + ["—" if v is None else f"±{v:g}"
                                  for v in M.TG2_W_TABLE[g]])
    write_sheet(wb, "19_塑料件尺寸公差表2", "GB/T 47067-2026 表2 模塑件的允许极限尺寸（W 列）",
                "单位为 mm，数值为 ±极限偏差；W=不受模具活动部分影响；TG9 不分 W/NW",
                headers, rows, widths=[10] + [9] * len(M.TG2_W_SIZES))


def sheet_tg9pos(wb):
    """GB/T 47067-2026 表9：位置度（W 列）"""
    import sw_qc_mt as M
    headers = ["公差等级"] + M.TG9_POS_SIZES
    rows = []
    for g in range(1, 10):
        rows.append([f"TG{g}"] + ["—" if v is None else f"Ø{v:g}"
                                  for v in M.TG9_POS_TABLE[g]])
    write_sheet(wb, "20_塑料件位置度表9", "GB/T 47067-2026 表9 模塑件公差的位置度公差（W 列）",
                "单位为 mm，数值为圆柱形公差带直径 Ø；W=不受模具活动部分影响",
                headers, rows, widths=[10] + [9] * len(M.TG9_POS_SIZES))


# ==========================================================================
# 21_工艺能力_特征精度（2026-10-25 新增）
# ==========================================================================
def sheet_process_capability(wb):
    """
    工艺能力表：按「零件类别 + 常规工艺 + 特征类型 + 尺寸范围」给可达公差。

    为什么要这张表（用户 2026-10-25）：
      旧做法只看「公差 → IT 等级」一张整档表，丢掉了两个关键维度：
        ① 尺寸范围（+/-0.05 在 phi5 和 500mm 上含义完全不同）
        ② 特征类型（激光切割的外形/孔位/折弯高度精度各不相同）
      所以改成特征级可达公差，图上实际公差与它比对后分 4 档下结论。

    ⚠ 全部是**工程经验值**，请按贵司设备实际能力修改（尤其激光切割机、折弯机精度）。
    """
    import sw_qc_capability as CAP

    KIND_LABEL = {"mm": "±mm", "IT": "IT 等级", "deg": "度(°)",
                  "Ra": "Ra(μm)", "none": "不检"}
    rows = []
    for i, (cls, proc, feat, lo, hi, tol, kind, note) in enumerate(
            CAP.PROCESS_CAPABILITY, 1):
        if kind == "IT":
            tol_txt = "IT%d" % int(tol)
        elif kind == "deg":
            tol_txt = "±%g°" % tol
        elif kind == "Ra":
            tol_txt = "Ra%g" % tol
        elif kind == "none":
            tol_txt = "—（不检）"
        else:
            tol_txt = "±%g" % tol
        seg = ("≤%g" % hi) if lo == 0 and hi < 9999 else (
            "%g~%g" % (lo, hi) if hi < 9999 else ">%g" % lo)
        rows.append([i, cls, proc, feat, lo if lo else "", 
                     hi if hi < 9999 else "以上", tol_txt,
                     KIND_LABEL.get(kind, kind), note, ""])

    return write_sheet(
        wb, "21_工艺能力_特征精度",
        "工艺能力表：特征类型 + 尺寸范围 → 常规可达公差（机加 / 钣金 / 塑料）",
        "数据来源：sw_qc_capability.PROCESS_CAPABILITY　·　核对状态：**工程经验值，请按贵司设备实际能力修改**。"
        "用法：对每个尺寸，取「零件类别 + 特征类型 + 尺寸落在哪个范围」这一行，"
        "得到常规可达公差 T_std；再与图上实际公差 T_act 比对（判定档位见第 22 页）。"
        "公差类型说明：±mm = 对称公差半宽；IT 等级 = 按 GB/T 1800.1 查该尺寸段公差值；"
        "度 = 角度公差；Ra = 粗糙度；不检 = 该项不作为能力判定依据（如板厚取规格料）。"
        "★ 你可以直接改「常规可达公差」列的数值，也可以整行增删（保持列顺序不变）。",
        ["序号", "零件类别", "常规工艺", "特征类型",
         "尺寸下限(mm)", "尺寸上限(mm)", "常规可达公差", "公差类型",
         "说明 / 依据", "备注（请在此填写）"],
        rows,
        widths=[6, 10, 14, 18, 13, 13, 14, 11, 34, 30],
        row_style=lambda i, row: (CHECK_FILL if row[1] == "钣金"
                                  else (OK_FILL if row[1] == "机加" else None)))


# ==========================================================================
# 22_能力判定阈值（4 档）
# ==========================================================================
def sheet_capability_levels(wb):
    """
    能力判定阈值：图上实际公差 T_act 与可达公差 T_std 的比值 → 4 档结论。
    """
    import sw_qc_capability as CAP

    ws = wb.create_sheet("22_能力判定阈值")
    ws["A1"] = "加工能力判定阈值 —— 图上公差 vs 常规可达公差"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("数据来源：sw_qc_capability.CAPABILITY_LEVELS　·　"
                "核对状态：**判定比例是我按工程经验定的，请你确认是否合适**。"
                "T_std = 第 21 页查到的「常规可达公差」；T_act = 图上标注的公差半宽"
                "（对称 ±0.05 → T_act=0.05；+0.02/0 → T_act=0.01）。"
                "「需精密工艺 / 需二次加工」只作提示，不判不合格。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:F2")
    for c in range(1, 7):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 62

    r = 4
    ws.cell(row=r, column=1, value="一、判定档位").font = SEC_FONT
    r += 1
    for c, h in enumerate(["档次", "判定条件", "阈值(相对 T_std)",
                           "界面文案模板", "报告级别", "备注（请在此填写）"], 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    for (name, cond, ratio, tpl, sev) in CAP.CAPABILITY_LEVELS:
        for c, v in enumerate([name, cond, ("%.3f" % ratio) if ratio else "0",
                               tpl, sev, ""], 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=(c in (2, 4)))
        if name == "常规可满足":
            ws.cell(row=r, column=1).fill = OK_FILL
        elif name == "难保证":
            ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor="FEE4E2")
        ws.row_dimensions[r].height = 30
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="二、判定示例（按第 21 页数据自动算）").font = SEC_FONT
    r += 1
    for c, h in enumerate(["零件类别", "特征", "尺寸", "图上公差",
                           "可达 T_std", "判定结果"], 1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    r += 1
    examples = [
        ("钣金", "外形长/宽", 300, 0.05),
        ("钣金", "外形长/宽", 300, 0.15),
        ("钣金", "孔到边/孔到孔", 50, 0.10),
        ("钣金", "折弯高度/边长", 50, 0.30),
        ("机加", "轴径/外圆", 20, 0.021),
        ("机加", "孔距/中心距", 100, 0.05),
        ("机加", "平面度/平行度", 100, 0.02),
        ("塑料", "外形/孔径", 50, 0.20),
    ]
    for cls, feat, size, tact in examples:
        cap = CAP.find_capability(cls, feat, size)
        if not cap:
            continue
        proc, tol, kind, note, _feat, _lo, _hi = cap
        if kind == "IT":
            tstd_txt = "IT%d" % int(tol)
            verdict = "（IT 等级需按 GB/T 1800.1 换算后判定）"
        elif kind == "mm":
            tstd_txt = "±%g" % tol
            if tact >= tol:
                verdict = "常规可满足"
            elif tact >= tol / 3:
                verdict = "需精密工艺"
            elif tact >= tol / 5:
                verdict = "需二次加工"
            else:
                verdict = "难保证"
        else:
            tstd_txt = "%g(%s)" % (tol, kind)
            verdict = "（按该类型判定）"
        for c, v in enumerate([cls, feat, size, "±%g" % tact, tstd_txt, verdict], 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="三、与旧口径的关系").font = SEC_FONT
    r += 1
    for line in [
        CAP.LEGACY_NOTE,
        "旧口径表格位置：第 1 页（IT 公差数值）与第 4 页（Ra 优先数系列）；"
        "这三张表仍保留，用于「公差等级是否过严」等辅助判断。",
        "本表核对完并确认后，我会把判定主逻辑切到「特征级可达公差」，"
        "IT 等级只作为辅助显示（例如「≈IT9」）。",
    ]:
        cell = ws.cell(row=r, column=1, value=line)
        cell.font = Font(size=9, color="475467")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.row_dimensions[r].height = 30
        r += 1

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 52
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 30
    return ws


# ==========================================================================
# 1d_基本偏差原表（矩阵视图）：行=尺寸段，列=偏差代号
# 2026-10-25 补回：2026-10-23 版的「矩阵布局」基本偏差表曾被并掉，用户要求恢复
# ==========================================================================
def sheet_fundamental_matrix(wb):
    """
    基本偏差原表（按标准原表的矩阵排布：行=尺寸段，列=偏差代号）。

    与 1b 的区别：
      · 1b = 明细视图（行=代号，带置信度/工具提示，便于逐条核对）
      · 本表 = 矩阵视图（行=尺寸段，与原标准表格排布一致，便于横向对比）
    两者数据同源（sw_qc_gb.SHAFT/HOLE_FUNDAMENTAL_DEVIATION）。
    """
    bounds = gb._IT_BOUNDS
    seg_names = [f"≤{bounds[0]}"] + \
                [f">{bounds[i-1]}~{bounds[i]}" for i in range(1, len(bounds))]

    def block(ws, r, title, table, order):
        ws.cell(row=r, column=1, value=title).font = SEC_FONT
        r += 1
        for c, h in enumerate(["尺寸段 (mm)"] + order, 1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")
        r += 1
        for i, seg in enumerate(seg_names):
            cell = ws.cell(row=r, column=1, value=seg)
            cell.border = BORDER
            for j, code in enumerate(order, 2):
                vals = (table.get(code) or {}).get("values") or []
                v = vals[i] if i < len(vals) else None
                c = ws.cell(row=r, column=j, value="—" if v is None else v)
                c.border = BORDER
                c.alignment = Alignment(horizontal="center")
            r += 1
        return r

    ws = wb.create_sheet("1d_基本偏差原表_矩阵视图")
    ws["A1"] = "基本偏差原表（矩阵视图：行=尺寸段，列=偏差代号）　单位：μm"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("数据来源：sw_qc_gb.SHAFT_FUNDAMENTAL_DEVIATION / HOLE_FUNDAMENTAL_DEVIATION"
                "　·　核对状态：与 1b 同源，**待核对**。"
                "本表是标准原表的矩阵排布（2026-10-23 版里的布局），10-24 版曾被并掉，"
                "按用户要求补回。数值含义：轴给 es（上偏差）或 ei（下偏差），"
                "孔给 ES/EI；js/JS 为对称 ±IT/2。"
                "⚠ 同一代号不同公差等级时，孔 K/M/N（≤IT8）与 P~ZC（≤IT7）需加 Δ 修正，"
                "本表是**表值本身**（未加 Δ）；加 Δ 后的限额见 1c_极限偏差速查。")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("A2:N2")
    for c in range(1, 15):
        ws.cell(row=2, column=c).fill = NOTE_FILL
    ws.row_dimensions[2].height = 78

    r = 4
    shaft_order = ["g", "h", "f", "k", "m", "n", "p", "e", "d", "c", "r", "s"]
    r = block(ws, r, "块一：轴的基本偏差（μm）", gb.SHAFT_FUNDAMENTAL_DEVIATION, shaft_order)
    ws.cell(row=r, column=1, value="js / JS = ±IT/2（对称偏差）").font = Font(size=9, color="475467")
    r += 2
    hole_order = ["G", "H", "F", "K", "M", "N", "P", "E", "D", "C", "R", "S"]
    r = block(ws, r, "块二：孔的基本偏差（μm）", gb.HOLE_FUNDAMENTAL_DEVIATION, hole_order)
    r += 1
    for line in (
        "注 1：孔 K/M/N 在等级 ≤IT8、P~ZC 在等级 ≤IT7 时要加 Δ（Δ=IT(n)−IT(n−1)）；",
        "      加 Δ 后的极限偏差见 1c 表（如 φ25：K7=+6/−15、P7=−14/−35）。",
        "注 2：R/S 的等级归属存疑，仅作参考，暂不用于判错。",
        "注 3：本表与 1b 明细表是同一批数据的两种排布，核对时选一张即可。",
    ):
        ws.cell(row=r, column=1, value=line).font = Font(size=9, color="475467")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
        r += 1

    ws.column_dimensions["A"].width = 15
    for c in range(2, 14):
        ws.column_dimensions[get_column_letter(c)].width = 9
    return ws


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导出工程图 QC 工具的国标规格库为 Excel")
    ap.add_argument("--out", default=None, help="输出 xlsx 路径（默认：包根目录）")
    args = ap.parse_args(argv)

    out = args.out or os.path.join(
        os.path.dirname(_HERE),
        f"规格库核对表_{datetime.datetime.now():%Y%m%d}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)

    # 待核对清单（用于说明页）
    unverified = []
    if not all(gb.GB_T_1804_RADIUS[g]["verified"] for g in gb.GB_T_1804_RADIUS):
        unverified.append(("2_倒圆倒角_GB1804", "GB/T 1804-2000",
                           "代码里明确标 verified=False —— 未对照标准正本"))
    for kind in ("倾斜度", "同轴度", "对称度", "圆跳动"):
        if not gb.is_verified(kind):
            unverified.append(("3_几何公差_GB1184", f"GB/T 1184-1996（{kind}）",
                               "代码里明确标 verified=False —— 未对照标准正本"))
    unverified.append(("1_IT公差_GB1800.1", "GB/T 1800.1-2020",
                       "代码里**没有**核对标记，但 v0.10 的「公差过严提醒」直接依赖它"))
    unverified.append(("4_粗糙度Ra系列_GB131", "GB/T 131-2006",
                       "代码里没有核对标记；用于判断图上 Ra 是否为优先数"))
    # v0.12 补入的 4 组表：都来自公开手册/教材，做过跨来源交叉验证，
    # 但**未全部对照国标正本**——按项目口径一律进「待复核」清单。
    unverified.append(("11_螺纹螺距_GB193", "GB/T 193-2003",
                       "两页交叉验证 + 尺寸自洽校验；有 5 处按尺寸判定补正，建议复核"))
    unverified.append(("12_平键键槽_GB1095", "GB/T 1095/1096-2003",
                       "键宽/键高/槽深已自洽核过；轴 H9/N9 偏差在来源页是图片，未收录"))
    unverified.append(("13_5c_挡圈_GB894_GB893", "GB/T 894.1-2017 / 893.1-1986",
                       "⚠ 孔用表是 1986 版，现行 2017 已改动厚度 —— 按 2017 版标注需另行核对"))
    unverified.append(("1b_基本偏差_GB1800.2", "GB/T 1800.1/1800.2-2020",
                       "⚠ 孔 K/M/N/P/R/S 等级归属存疑已停用；轴 s>50mm 与 >500mm 为中/低置信"))
    unverified.append(("21_工艺能力_特征精度", "工艺经验值（非国标）",
                       "机加/钣金/塑料的可达公差全凭工程经验，请按贵司设备实际能力核定"))
    unverified.append(("22_能力判定阈值", "无标准（工具口径）",
                       "4 档判定的比例阈值（T_std、T_std/3、T_std/5）请你确认是否合适"))

    sheet_index = [
        (1, "1_IT公差_GB1800.1", "标准公差数值 IT（μm），9 个等级 × 13 个尺寸段", "待核对"),
        (1.1, "1b_基本偏差_GB1800.2", "基本偏差（μm），轴+孔", "待核对"),
        (1.15, "1d_基本偏差原表_矩阵视图",
         "基本偏差原表矩阵排布（行=尺寸段，列=代号）；2026-10-25 按用户要求补回", "待核对"),
        (1.2, "1c_极限偏差速查", "常用配合代号的上/下偏差（mm），按间隙/过渡/过盈分组", "待核对"),
        (2, "2_线性一般公差_GB1804", "线性尺寸未注公差 f/m/c/v（±mm）", "已核对"),
        (3, "2_角度一般公差_GB1804", "角度尺寸未注公差（′，按短边长度分段）", "已核对"),
        (4, "2_倒圆倒角_GB1804", "倒圆半径与倒角高度未注公差（mm）", "待核对"),
        (5, "3_几何公差_GB1184", "几何公差未注值，7 项 × H/K/L（mm）", "部分待核对"),
        (5.1, "3b_几何公差数系_GB1184B",
         "附录B 数系表 B1~B5：注出几何公差值是否在标准数系上", "待抽查"),
        (6, "4_粗糙度Ra系列_GB131", "Ra 优先数系列（μm）", "待核对"),
        (7, "4_粗糙度推荐区间", "部位 → 推荐 Ra 区间（经验值）", "经验值"),
        (8, "4b_表面处理耐蚀性", "表面处理 → 耐蚀等级与必须配套的措施", "工程经验值"),
        (9, "5_特征推荐标注", "特征 → 推荐公差/几何公差/Ra", "经验值"),
        (10, "6_配合推荐", "配合部位 → 推荐公差带；基本偏差方向", "建议复核"),
        (11, "6_公差等级与Ra匹配", "公差等级 → 经验 Ra 区间", "经验值"),
        (12, "7_图样幅面比例等_GB4458", "幅面/比例/标题栏字段/螺纹代号/剖切字母", "建议复核"),
        (13, "8_检查规则预设",
         "整合后的检查规则总目录：工具原有规则 × 审图规则库 V1（含 117 条）",
         "本次整合"),
        (14, "9_规则ID说明",
         "规则 ID 怎么划分、干什么用；哪些列是给你审的、哪些是给程序用的",
         "说明"),
        (15, "10_AI检查规范",
         "AI 语义检查的硬规定（超时/长度/引用原文/降级），以及它负责的 16 条规则",
         "本次新增"),
        # —— v0.12 补入的 4 组标准表（缺表规则的判据来源）——
        (16, "11_螺纹螺距_GB193",
         "螺纹直径→粗牙/细牙螺距（判「螺纹漏标螺距」用）", "待核对"),
        (17, "12_平键键槽_GB1095",
         "轴径→键宽/键高/槽深、键槽宽公差带（判键槽合规用）", "待核对"),
        (18, "13_5c_挡圈_GB894_GB893",
         "轴用/孔用挡圈槽径与槽宽（判挡圈槽合规用）", "待核对（含版本差异）"),
        (19, "1b_基本偏差_GB1800.2",
         "极限偏差的基本偏差，含旧版对照（判偏差/配合用）", "待核对（孔部分已停用）"),
        (20, "19_塑料件尺寸公差表2",
         "GB/T 47067-2026 表2：TG1~TG9 × 16 尺寸段允许极限（W 列）", "待核对"),
        (21, "20_塑料件位置度表9",
         "GB/T 47067-2026 表9：位置度公差（W 列）", "待核对"),
        # —— 2026-10-25：工艺能力表（用户要求按 特征类型+尺寸范围 判定）——
        (22, "21_工艺能力_特征精度",
         "机加/钣金/塑料：特征类型 + 尺寸范围 → 常规可达公差", "请审核（经验值）"),
        (23, "22_能力判定阈值",
         "图上公差 vs 可达公差 的 4 档判定规则与示例", "请审核"),
    ]

    sheet_instructions(wb, unverified, sheet_index)
    sheet_it(wb)
    sheet_deviations(wb)      # 1b：基本偏差（IT 表之后最常对照）
    sheet_fundamental_matrix(wb)   # 1d：基本偏差矩阵视图（2026-10-25 补回）
    sheet_deviation_quick(wb)  # 1c：极限偏差速查
    sheet_linear(wb)
    sheet_angle(wb)
    sheet_radius(wb)
    sheet_geom(wb)
    sheet_form_series(wb)      # 3b：几何公差数系 B1~B5
    sheet_ra_series(wb)
    sheet_ra_reco(wb)
    sheet_surface_treatment(wb)   # 4b：与粗糙度同属"表面"主题，放一起
    sheet_feature_reco(wb)
    sheet_fit(wb)
    sheet_grade_ra(wb)
    sheet_misc(wb)
    sheet_rules(wb)
    sheet_rule_ids(wb)
    sheet_ai_policy(wb)
    # v0.12：缺表规则补上判据表后新增的 4 张
    sheet_threads(wb)
    sheet_keys(wb)
    sheet_rings(wb)
    # GB/T 47067-2026 两张表（2026-10-23）
    sheet_tg2(wb)
    sheet_tg9pos(wb)
    # 2026-10-25：工艺能力表（机加/钣金/塑料 特征级）
    sheet_process_capability(wb)
    sheet_capability_levels(wb)

    wb.save(out)

    print("=" * 66)
    print("  国标规格库已导出")
    print("=" * 66)
    print(f"  输出：{out}")
    print(f"  工作表 {len(wb.sheetnames)} 张：")
    for n in wb.sheetnames:
        rows = wb[n].max_row
        print(f"    - {n}（约 {max(0, rows - 4)} 行数据）")
    print()
    print(f"  需优先复核：{len(unverified)} 处")
    for name, std, why in unverified:
        print(f"    · {std}：{why}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
