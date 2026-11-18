#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
spec_import_library.py — 把工程师核对过的《规格库核对表》导回代码

## 它在"导出 → 人工核对 → 回导"这条流程里的位置

    export_spec_library.py   导出   代码 → Excel（给审图工程师核对）
         ↓  工程师在 Excel 里改数值、写备注
    spec_import_library.py   回导   Excel → 代码（本脚本）

配套说明见 README「规格库核对流程」。

## 怎么用

    # 只看差异，不改任何东西（默认；发回来先跑这个最稳）
    python spec_import_library.py 核对表.xlsx

    # 确认无误后落盘（生成 qc_spec_overrides.json，工具下次启动即生效）
    python spec_import_library.py 核对表.xlsx --apply

    # 自检：现场导出一份 → 立刻回导 → 必须"零差异"
    python spec_import_library.py --roundtrip

## 为什么落成 JSON 覆盖层，而不是直接改 sw_qc_gb.py 里的数字

回导是「人工核对结果落盘」，不是「程序改程序」。用正则在 Python 源码里替换数字，
表格式一微调就会**静默改错位置**。所以：

    核对值 → qc_spec_overrides.json → sw_qc_gb.py 在 import 时套用到基表上

好处是导出脚本现场读到的值 = 检查真正在用的值（"不是手抄"的承诺继续成立），
而且删掉 JSON 就回到代码基线，改错了能退回来。

## 边界（说清楚，不假装全能）

* 能回导：第 1~7 张数据表（IT / 线性 / 角度 / 倒圆倒角 / 几何公差 / Ra 系列）。
* **不自动回导第 8 表（检查规则）**：那涉及改 rule_catalog.py 里 173 条规则的元组，
  自动改风险高。本脚本会**逐条列出你在第 8 表改了哪些单元格**，交人工确认后改代码。
* 段数/等级数对不上的表**跳过并在报告里点名**，不猜、不截断。
* 表头或列顺序被改过 → 直接报错停下，不冒险错位读取。
"""
from __future__ import annotations

import glob
import json
import os
import sys

# --- 确保能 import 同目录兄弟模块（便携版 Python 隔离模式，见 README）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# -----------------------------------------------------------------

import argparse
import datetime

import sw_qc_gb as gb

OVERRIDE_FILENAME = "qc_spec_overrides.json"

# 每张数据表统一的版式（导出脚本 write_sheet 决定）
_TITLE_ROW = 1
_NOTE_ROW = 2
_HEADER_ROW = 4
_FIRST_DATA_ROW = 5
_MEMO_HEADER = "备注"


# ==========================================================================
# 读表小工具
# ==========================================================================
class SheetError(Exception):
    """表结构不符合约定 —— 必须停下报错，不能猜着往下读。"""


def _load_workbook(path: str):
    from openpyxl import load_workbook
    return load_workbook(path, data_only=True)


def _snapshot_copy(path: str) -> str | None:
    """
    表格正开在 Excel 里时，**独占锁**会让读取失败（BadZipFile / PermissionError）。

    但用户往往就是刚核对完、表格还开着 —— 这时只报「读取失败」等于把最需要
    回导的那一刻挡在门外。所以退一步：用**共享读**把文件复制一份再读。

    说明：只读用户原文件、不改动；读的是复制那一刻的内容，
    所以仍然建议核对完先按 Ctrl+S 保存再回导。
    """
    import shutil
    import tempfile

    # 候选目录：系统临时目录优先，其次项目内的 tests/_scratch_import
    # （受限环境下 %TEMP% 可能不可写，所以要有备选）。
    candidates = []
    try:
        candidates.append(os.path.join(tempfile.gettempdir(), "swqc_import"))
    except Exception:
        pass
    candidates.append(os.path.join(_HERE, "tests", "_scratch_import"))

    for dst_dir in candidates:
        try:
            os.makedirs(dst_dir, exist_ok=True)
        except OSError:
            continue
        dst = os.path.join(dst_dir, "_snapshot.xlsx")
        try:
            with open(path, "rb") as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            return dst
        except OSError:
            continue
    return None


def _drop_snapshot() -> None:
    try:
        import shutil
        shutil.rmtree(os.path.join(_HERE, "tests", "_scratch_import"), ignore_errors=True)
    except Exception:
        pass


def _find_workbook(path: str) -> str:
    """
    path 可以是文件，也可以是目录（目录里找核对表）。

    必须跳过 Excel 的 `~$xxx.xlsx` 锁文件：它不是 zip，
    openpyxl 会抛 BadZipFile；而且它**恰好说明表格正在被打开**。
    """
    if os.path.isfile(path):
        if os.path.basename(path).startswith("~$"):
            raise SheetError(
                f"{path} 是 Excel 的临时锁文件，不是核对表本身。"
                "请选真正的 xlsx 文件。")
        return path
    if not os.path.isdir(path):
        raise SheetError(f"找不到路径：{path}")
    cands = sorted(p for p in glob.glob(os.path.join(path, "*.xlsx"))
                   if not os.path.basename(p).startswith("~$"))
    if not cands:
        raise SheetError(f"目录里没有 xlsx：{path}")
    if len(cands) > 1:
        print(f"  提示：目录里有 {len(cands)} 个 xlsx，用第一个："
              f"{os.path.basename(cands[0])}")
    return cands[0]


def _rows(ws) -> list[tuple]:
    return list(ws.iter_rows(values_only=True))


def _cell(row: tuple, idx: int):
    return row[idx] if idx < len(row) else None


def _headers(ws) -> list[str]:
    return [str(h).strip() if h is not None else "" for h in _rows(ws)[_HEADER_ROW - 1]]


def _check_headers(ws, expected: list[str], where: str) -> None:
    """表头必须与约定一致（顺序也算）—— 不一致就停，避免错位读数。"""
    got = _headers(ws)[:len(expected)]
    if got != expected:
        raise SheetError(
            f"{where} 表头与约定不符，已停止以免错位读取。\n"
            f"      期望：{expected}\n"
            f"      实际：{got}")


def _num_or_none(v):
    """Excel 空格子/空串 → None；数字原样返回；其余尝试转 float。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return s        # 保留原文，由比较逻辑报"无法解析"
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    return v


def _same(a, b, tol: float = 1e-9) -> bool:
    """数值按容差比；None 与空串视为相等（导出空 = 代码里 None）。"""
    a, b = _num_or_none(a), _num_or_none(b)
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b



def _sheet_compat(wb, names):
    """2026-10-26 兼容：几何公差/形位公差 表名改口径后，旧工作簿仍可导入"""
    for nm in names:
        try:
            if nm in wb.sheetnames:
                return wb[nm]
        except Exception:
            pass
    return None

def _sheet(wb, name: str):
    if name not in wb.sheetnames:
        raise SheetError(f"工作簿里缺工作表「{name}」—— 是不是删过表？")
    return wb[name]


def _data_rows(ws) -> list[tuple]:
    """第 5 行起、去掉整行空行。"""
    out = []
    for r in _rows(ws)[_FIRST_DATA_ROW - 1:]:
        if any(c is not None and str(c).strip() != "" for c in r):
            out.append(r)
    return out


# ==========================================================================
# 各表解析：返回 {键: 值} 或 {等级: [值...]}
# ==========================================================================
def _grade_key(cell) -> str:
    """
    把 Excel 表头里的等级写法归一成代码里的键。

    导出表头带单位与说明，例如 `f（±mm）` / `m（±mm）`；
    代码里的键是裸字母 `f` / `m`。取**第一段字母数字**做键，
    这样表头加不加单位都不影响回导 —— 但只在等级段数与代码一致时才采信。
    """
    import re
    m = re.match(r"\s*([A-Za-z]+\d*)", str(cell or ""))
    return m.group(1) if m else str(cell or "").strip()


def _check_segment_headers(ws, where: str) -> list[str]:
    """
    线性/角度/倒圆倒角三张表结构相同：第 0 列是尺寸段，
    紧接着 f/m/c/v 四列，最后一列「备注」。

    角度表把第一列写成「短边长度(mm)」（分段依据不同），所以第一个表头
    **只要求含 (mm) 且不为空**，不要求逐字相同 —— 但列序与等级列必须对上，
    那才是会造成错位读取的关键。
    """
    hdr = _headers(ws)
    first = hdr[0] if hdr else ""
    if "(mm)" not in first:
        raise SheetError(f"{where} 第 1 列应是以 (mm) 结尾的尺寸段，实际「{first}」")
    if _MEMO_HEADER not in hdr:
        raise SheetError(f"{where} 找不到「备注」列")
    return [_grade_key(h) for h in hdr[1:hdr.index(_MEMO_HEADER)]]


def parse_linear_like(wb, sheet_name: str, grades: list[str], where: str,
                      value_cols: list) -> tuple[dict, list]:
    """
    线性/角度/倒圆倒角三张表结构相同：
        第 0 列=尺寸段，第 1..4 列=f/m/c/v，最后一列=备注
    返回（{等级: [值]}, 差异列表）。
    """
    ws = _sheet(wb, sheet_name)
    file_grades = _check_segment_headers(ws, where)
    if file_grades != grades:
        raise SheetError(
            f"{where} 的等级列与代码不一致，已停止以免错位读取。\n"
            f"      表：{file_grades}\n      代码：{grades}")
    data = _data_rows(ws)
    if not data:
        raise SheetError(f"{where} 没有数据行")
    if len(data) > len(value_cols):
        # 多出来的行：可能是核对人补的行 —— 明确报出，不静默吞掉
        extra = len(data) - len(value_cols)
        print(f"  ⚠ {where} 多出 {extra} 行数据，超出代码中段数，已忽略（请人工确认）")
        data = data[:len(value_cols)]

    key = _table_key(where)
    baseline = gb.SPEC_BASELINE[key]

    parsed = {g: [] for g in grades}
    diffs: list = []
    for i, row in enumerate(data):
        seg = _cell(row, 0)
        for gi, g in enumerate(grades):
            v = _num_or_none(_cell(row, 1 + gi))
            parsed[g].append(v)
            base = baseline[g][i]
            if not _same(v, base):
                diffs.append((where, f"{g} @ {seg}", base, v))
        memo = _cell(row, 1 + len(grades))
        if memo is not None and str(memo).strip():
            diffs.append((where, f"备注 @ {seg}", "", str(memo).strip()))
    if len(parsed[grades[0]]) != len(value_cols):
        raise SheetError(f"{where} 段数为 {len(parsed[grades[0]])}，"
                         f"代码里是 {len(value_cols)}，对不上，已停止")
    return parsed, diffs


def _table_key(where: str) -> str:
    if "线性" in where:
        return "linear"
    if "角度" in where:
        return "angle"
    return "radius"


def parse_it(wb) -> tuple[dict, list]:
    ws = _sheet(wb, "1_IT公差_GB1800.1")
    hdr = _headers(ws)
    if hdr[0] != "尺寸段(mm)":
        raise SheetError(f"IT 表第 1 列表头应为「尺寸段(mm)」，实际「{hdr[0]}」")
    # 等级列 = 第 2 列到「备注」列之前
    try:
        memo_idx = hdr.index(_MEMO_HEADER)
    except ValueError:
        raise SheetError("IT 表找不到「备注」列")
    grades = []
    for h in hdr[1:memo_idx]:
        # 表头写成 "IT7 ★"（★ = 优先数系）；归一化后必须是 IT 等级
        key = _grade_key(h)
        if not key.upper().startswith("IT"):
            raise SheetError(f"IT 表出现了非 IT 等级列：「{h}」")
        grades.append(key.upper())
    if grades != list(gb.GB_T_1800_IT):
        raise SheetError(
            f"IT 表等级列与代码不一致：\n      表：{grades}\n"
            f"      代码：{list(gb.GB_T_1800_IT)}")

    data = _data_rows(ws)
    if len(data) != len(gb._IT_BOUNDS):
        raise SheetError(f"IT 表有 {len(data)} 个尺寸段，代码里是 "
                         f"{len(gb._IT_BOUNDS)} 个，对不上，已停止")

    parsed = {g: [] for g in grades}
    diffs: list = []
    baseline = gb.SPEC_BASELINE["it"]
    for i, row in enumerate(data):
        seg = _cell(row, 0)
        for gi, g in enumerate(grades):
            v = _num_or_none(_cell(row, 1 + gi))
            parsed[g].append(v)
            base = baseline[g][i]
            if not _same(v, base):
                diffs.append(("IT公差", f"{g} @ {seg}", base, v))
        memo = _cell(row, memo_idx)
        if memo is not None and str(memo).strip():
            diffs.append(("IT公差", f"备注 @ {seg}", "", str(memo).strip()))
    return parsed, diffs


def parse_geom(wb) -> tuple[dict, list]:
    ws = _sheet_compat(wb, ["3_几何公差_GB1184", "3_形位公差_GB1184"])
    _check_headers(ws, ["公差项目", "等级", "尺寸段(mm)", "尺寸上限(mm)",
                        "未注公差值(mm)", "代码中已核对", _MEMO_HEADER], "几何公差")
    parsed: dict[str, dict[str, list]] = {}
    diffs: list = []
    for row in _data_rows(ws):
        kind = _cell(row, 0)
        grade = _cell(row, 1)
        val = _num_or_none(_cell(row, 4))
        if kind is None or grade is None:
            continue
        kind, grade = str(kind).strip(), str(grade).strip().upper()
        table = gb.GB_T_1184.get(kind)
        if table is None or grade not in ("H", "K", "L"):
            diffs.append(("几何公差", f"{kind}/{grade}", "", "代码中无此项，已跳过"))
            continue
        parsed.setdefault(kind, {}).setdefault(grade, []).append(val)

    for kind, grades in parsed.items():
        for grade, values in grades.items():
            base = gb.SPEC_BASELINE["geom"][kind][grade]
            if len(values) != len(base):
                raise SheetError(f"几何公差 {kind}/{grade} 段数为 {len(values)}，"
                                 f"代码里是 {len(base)}，对不上，已停止")
            for i, v in enumerate(values):
                if not _same(v, base[i]):
                    diffs.append(("几何公差", f"{kind}/{grade} 第{i + 1}段",
                                  base[i], v))
    return parsed, diffs


def parse_ra_series(wb) -> tuple[list, list]:
    ws = _sheet(wb, "4_粗糙度Ra系列_GB131")
    baseline = gb.SPEC_BASELINE["ra_series"]
    values, diffs = [], []
    for row in _data_rows(ws):
        v = _num_or_none(_cell(row, 0))
        if v is None:
            continue
        values.append(v)
        if len(values) <= len(baseline):
            base = baseline[len(values) - 1]
            if not _same(v, base):
                diffs.append(("Ra系列", f"第 {len(values)} 个", base, v))
        else:
            diffs.append(("Ra系列", f"新增第 {len(values)} 个", "", v))
        memo = _cell(row, 1)
        if memo is not None and str(memo).strip():
            diffs.append(("Ra系列", f"备注 @ {v}", "", str(memo).strip()))
    if len(values) < len(baseline):
        diffs.append(("Ra系列", "条目变少", len(baseline), len(values)))
    return values, diffs


def parse_surface_remarks(wb) -> tuple[dict, list]:
    """
    4b_表面处理耐蚀性表的「备注」列 —— 工程师核对时补写的话。

    为什么单独处理：用户第一份核对表**只改了这一列**（QPQ 那条写了批注），
    而早期版本的回导只认第 1~7 表，于是报「没有任何改动」——
    **用户的核对结果被静默忽略**。这正是回导最不该犯的错，所以补上并加测试。

    只收「备注」列：等级/配套措施属于判据，要动就该改代码（避免静默改判据）。
    """
    ws = _sheet(wb, "4b_表面处理耐蚀性")
    hdr = _headers(ws)
    if "表面处理" not in hdr:
        raise SheetError(f"4b 表找不到「表面处理」列：{hdr}")
    try:
        memo_idx = (hdr.index("备注（请在此填写）") if "备注（请在此填写）" in hdr
                    else hdr.index(_MEMO_HEADER))
    except ValueError:
        raise SheetError(f"4b 表找不到备注列：{hdr}")

    known = set(gb.SURFACE_TREATMENT_CORROSION)
    # 大小写不敏感地映射回代码里的键：代码里写的是 "qpq"，
    # 而人在表格里自然写成 "QPQ" —— 不认大小写差异，否则批注会被当成"表中无此处理"丢掉。
    lower_map = {k.lower(): k for k in known}
    base = gb.SPEC_BASELINE.get("surface_remarks", {})
    parsed: dict[str, str] = {}
    diffs: list = []
    for row in _data_rows(ws):
        name = _cell(row, 0)
        if name is None:
            continue
        raw_name = str(name).strip()
        memo = _cell(row, memo_idx)
        memo = "" if memo is None else str(memo).strip()
        key = raw_name if raw_name in known else lower_map.get(raw_name.lower())
        if key is None:
            if memo:
                diffs.append(("表面处理", f"{raw_name} 备注", "",
                              f"{memo}（表中无此处理，已跳过）"))
            continue
        if memo:
            parsed[key] = memo
        old = base.get(key, "")
        if memo != old:
            diffs.append(("表面处理", f"{key} 备注", old, memo or "（清空）"))
    return parsed, diffs


def report_other_sheets(wb) -> list:
    """
    兜底：把所有**还没被专门解析**的表里的「备注」列都列出来。

    为什么要这个兜底：工程师可能在任何一张表上写批注。如果只处理已知的几张，
    他在别处的核对结果就会被**静默忽略** —— 实测第一次回导就是这么漏掉的
    （只在 4b 写了一句话，工具报"没有任何改动"）。

    这里只**报告**、不自动落盘：这些表的数值改动需要人工改代码，
    但至少批注不会消失。
    """
    handled = {"1_IT公差_GB1800.1", "2_线性一般公差_GB1804", "2_角度一般公差_GB1804",
               "2_倒圆倒角_GB1804", "3_几何公差_GB1184", "4_粗糙度Ra系列_GB131",
               "4b_表面处理耐蚀性", "8_检查规则预设", "填表说明"}
    diffs = []
    for ws in wb.worksheets:
        if ws.title in handled:
            continue
        hdr = _headers(ws)
        # 只认**留给人填写**的列（「备注」「请在此填写」），
        # **必须排除「工具提示」这类程序自己写的列** ——
        # 否则工具写的提示会被当成"工程师批注"报出来（实测被误报 54 处）。
        memo_cols = [i for i, h in enumerate(hdr)
                     if ("备注" in h or "请在此填写" in h) and "工具" not in h]
        if not memo_cols:
            continue
        for row in _data_rows(ws):
            for i in memo_cols:
                v = _cell(row, i)
                if v is not None and str(v).strip():
                    diffs.append((ws.title, f"{_cell(row, 0)} / {hdr[i]}",
                                  "", str(v).strip()))
    return diffs


def parse_rules_sheet(wb) -> list:
    """
    第 8 表只**报告**、不自动回导。

    用户真正会动的三列：严重级别、默认启用、备注（请在此填写）。
    这里把它们与 rule_catalog 的现行值比出来，交人工确认后改代码 ——
    自动改 173 条规则的元组风险太高，不值得。
    """
    from rule_catalog import rows as catalog_rows
    import rule_catalog as rc

    ws = _sheet(wb, "8_检查规则预设")
    hdr = _headers(ws)
    try:
        id_idx = hdr.index("规则ID")
        sev_idx = hdr.index("严重级别")
        on_idx = hdr.index("默认启用")
        memo_idx = hdr.index("备注（请在此填写）")
    except ValueError as exc:
        raise SheetError(f"第 8 表表头缺少必要列：{exc}")

    current = {}
    for entry in catalog_rows():
        d = dict(zip(rc.DISPLAY_ORDER, entry))
        current[str(d.get("id", ""))] = d

    diffs = []
    for row in _data_rows(ws):
        rid = _cell(row, id_idx)
        if rid is None:
            continue
        rid = str(rid).strip()
        cur = current.get(rid)
        if cur is None:
            diffs.append(("检查规则", f"{rid} 规则ID", "", "代码中不存在，已跳过"))
            continue
        for label, idx, key in (("严重级别", sev_idx, "sev"),
                                ("默认启用", on_idx, "on")):
            v = _cell(row, idx)
            v = "" if v is None else str(v).strip()
            base = str(cur.get(key, "")).strip()
            if v != base:
                diffs.append(("检查规则", f"{rid} {label}", base, v))
        memo = _cell(row, memo_idx)
        if memo is not None and str(memo).strip():
            diffs.append(("检查规则", f"{rid} 备注", "", str(memo).strip()))
    return diffs


# ==========================================================================
# 主流程
# ==========================================================================
def collect(path: str, quiet: bool = False):
    """解析整份核对表，返回 (覆盖数据, 差异列表, 说明行)。"""
    from sw_qc_gb import (GB_T_1800_IT, GB_T_1804_ANGLE, GB_T_1804_LINEAR,
                          GB_T_1804_RADIUS, GB_T_131_RA_SERIES, _IT_BOUNDS,
                          _ANGLE_BOUNDS, _LINEAR_BOUNDS, _RADIUS_BOUNDS)

    try:
        wb = _load_workbook(path)
    except Exception as exc:
        # 多半是 Excel 正开着（独占锁）。退一步用共享读复制一份再读。
        snap = _snapshot_copy(path)
        if snap is None:
            raise
        print(f"  ⚠ 直接读取失败（{exc.__class__.__name__}），"
              "已改用共享读的快照副本解析。")
        print("     提示：为保证读到的是最新内容，请先按 Ctrl+S 保存。")
        wb = _load_workbook(snap)

    overrides: dict = {}
    diffs: list = []
    notes: list[str] = []

    it, d = parse_it(wb)
    overrides["it"] = it
    diffs += d

    linear, d = parse_linear_like(wb, "2_线性一般公差_GB1804",
                                 list(GB_T_1804_LINEAR), "线性一般公差",
                                 GB_T_1804_LINEAR["f"]["values"])
    overrides["linear"] = linear
    diffs += d

    angle, d = parse_linear_like(wb, "2_角度一般公差_GB1804",
                                list(GB_T_1804_ANGLE), "角度一般公差",
                                GB_T_1804_ANGLE["f"]["values"])
    overrides["angle"] = angle
    diffs += d

    radius, d = parse_linear_like(wb, "2_倒圆倒角_GB1804",
                                 list(GB_T_1804_RADIUS), "倒圆倒角",
                                 GB_T_1804_RADIUS["f"]["values"])
    overrides["radius"] = radius
    diffs += d

    geom, d = parse_geom(wb)
    overrides["geom"] = geom
    diffs += d

    ra, d = parse_ra_series(wb)
    overrides["ra_series"] = ra
    diffs += d

    remarks, d = parse_surface_remarks(wb)
    if remarks:
        overrides["surface_remarks"] = remarks
    diffs += d

    diffs += parse_rules_sheet(wb)
    diffs += report_other_sheets(wb)

    # 只保留"真的改过"的表：没动的项不进覆盖层，
    # 这样将来代码更新了默认值，不会被一份旧覆盖文件拽回旧值。
    return _prune_unchanged(overrides), diffs, notes


def _current_value(table: str, key: str, idx: int):
    """
    取**代码基线**的现行值（不是"当前生效值"）。

    踩过的坑：这里若返回"套用覆盖后的值"，就会出现一个很坏的行为 ——
    第一次 --apply 落盘后，第二次运行发现"和当前生效值一样"，
    于是把覆盖项判为"没改过"而丢掉，等于**第二次运行静默撤销了第一次**。
    所以比较的基准永远是 sw_qc_gb.py 里的原值，覆盖层只是叠加在它上面。

    另注意两张表的**形状不同**：
      • IT 表    GB_T_1800_IT["IT5"]      → 直接是 [值...]
      • 其余三张  GB_T_1804_LINEAR["f"]   → {"values": [...], "verified": bool}
    统一在这里拆开，调用方不用管。
    """
    from sw_qc_gb import SPEC_BASELINE
    entry = SPEC_BASELINE["it" if table == "it" else table][key]
    return entry[idx]


def _prune_unchanged(overrides: dict) -> dict:
    """
    丢掉与代码基线完全相同的项：覆盖层里只保留"人工改动过"的值。

    为什么重要：如果整表都写进覆盖层，将来代码更新了默认值，
    这份旧覆盖文件会把值拽回去 —— 覆盖范围越小越安全。
    """
    out: dict = {}
    for table in ("it", "linear", "angle", "radius"):
        block = overrides.get(table)
        if not block:
            continue
        kept = {}
        for key, values in block.items():
            changed = []
            for i, v in enumerate(values):
                base = _current_value(table, key, i)
                if not _same(v, base):
                    changed.append(i)
            if changed:
                kept[key] = {"values": values, "changed_indexes": changed}
        if kept:
            out[table] = kept

    geom = overrides.get("geom")
    if geom:
        kept = {}
        for kind, grades in geom.items():
            for grade, values in grades.items():
                base = gb.SPEC_BASELINE["geom"][kind][grade]
                if any(not _same(v, b) for v, b in zip(values, base)):
                    kept.setdefault(kind, {})[grade] = values
        if kept:
            out["geom"] = kept

    ra = overrides.get("ra_series")
    if ra is not None:
        base_ra = gb.SPEC_BASELINE["ra_series"]
        if len(ra) != len(base_ra) or any(not _same(v, b) for v, b in zip(ra, base_ra)):
            out["ra_series"] = ra

    # 表面处理批注：只保留与基线不同的（清空的项不进覆盖层）
    remarks = overrides.get("surface_remarks")
    if remarks:
        base_rm = gb.SPEC_BASELINE.get("surface_remarks", {})
        kept = {k: v for k, v in remarks.items() if base_rm.get(k, "") != v}
        if kept:
            out["surface_remarks"] = kept
    return out


def _slim(overrides: dict) -> dict:
    """落盘前把 changed_indexes 这种中间信息去掉，只留纯数据。"""
    out = {}
    for table in ("it", "linear", "angle", "radius"):
        if table in overrides:
            out[table] = {k: v["values"] for k, v in overrides[table].items()}
    for key in ("geom", "ra_series", "surface_remarks"):
        if key in overrides:
            out[key] = overrides[key]
    return out


def report(diffs: list, limit: int = 60) -> None:
    if not diffs:
        print("  ✅ 未发现差异：表里的值与代码现行值完全一致。")
        return
    print(f"  发现 {len(diffs)} 处差异")
    for i, (sheet, where, old, new) in enumerate(diffs[:limit], 1):
        if old == "":
            print(f"    {i:>3}. [{sheet}] {where}：新增/批注 → {new}")
        else:
            print(f"    {i:>3}. [{sheet}] {where}：{old}  →  {new}")
    if len(diffs) > limit:
        print(f"    …… 其余 {len(diffs) - limit} 处省略")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="把《规格库核对表》导回代码（生成 qc_spec_overrides.json）")
    ap.add_argument("workbook", nargs="?",
                    help="核对表 xlsx（或放它的目录）。默认：上一级目录里最新的 核对表*.xlsx")
    ap.add_argument("--apply", action="store_true",
                    help="落盘生成 qc_spec_overrides.json（默认只比较、不写文件）")
    ap.add_argument("--out", default=None, help="覆盖文件输出路径（默认与 sw_qc_gb.py 同目录）")
    ap.add_argument("--roundtrip", action="store_true",
                    help="自检：现场导出一份 → 立刻回导 → 必须零差异")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("  规格库回导 —— 把核对表里的值套回工具")
    print("=" * 66)

    if args.roundtrip:
        return _roundtrip()

    path = args.workbook or os.path.dirname(_HERE)
    try:
        path = _find_workbook(path)
    except SheetError as exc:
        print(f"  ❌ {exc}")
        return 2

    print(f"  核对表：{os.path.basename(path)}")
    if gb.SPEC_OVERRIDE_NOTE:
        print(f"  当前生效：{gb.SPEC_OVERRIDE_NOTE}")
    print()

    try:
        overrides, diffs, _ = collect(path)
    except SheetError as exc:
        print(f"  ❌ 表结构不符合约定，已停止（没有改任何东西）：\n     {exc}")
        return 2
    except Exception as exc:                       # Excel 被占用等
        print(f"  ❌ 读取失败：{exc!r}")
        print("     若表格正开在 Excel 里，先保存并关闭再跑；"
              "或复制一份到别处再读。")
        return 2
    finally:
        _drop_snapshot()

    slim = _slim(overrides)
    report(diffs)

    if not slim:
        print()
        print("  → 没有任何需要落盘的改动。")
        if not args.apply:
            print("    （要生成文件可加 --apply，但当前确实没有改动。）")
        return 0

    print()
    print(f"  将落盘 {len(slim)} 张表的覆盖值：")
    for k, v in slim.items():
        n = len(v) if isinstance(v, (list, dict)) else 0
        print(f"    - {k}：{n} 项")

    if not args.apply:
        print()
        print("  （这是预演，没有写任何文件。确认无误后加 --apply 落盘。）")
        return 0

    out = args.out or os.path.join(_HERE, OVERRIDE_FILENAME)
    payload = dict(slim)
    payload["meta"] = {
        "导回时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "来源表格": os.path.basename(path),
        "说明": "由 spec_import_library.py 生成；删掉本文件即回到 sw_qc_gb.py 基线值。",
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print()
    print(f"  ✅ 已写入：{out}")
    print("     工具下次启动即生效。建议接着跑 tests/test_qc.py 确认没有改坏。")
    print("=" * 66)
    return 0


def _roundtrip() -> int:
    """
    自检：导出 → 回导，必须"零差异"。

    这是这条流程唯一能自动验证的部分。若表结构或解析逻辑被改坏，
    差异会立刻冒出来 —— 而不是等到工程师的核对表发回来才发现。

    ⚠ 这里**必须比对纯代码基线**：如果已经套用了覆盖层，
    导出出来的就是"核对后的值"，再回导自然"零差异"——那是假通过。
    """
    import subprocess

    print("  自检：现场导出一份 → 立刻回导（按代码基线比对）")
    # 输出必须放在**项目内**的 tests/_scratch，不能用系统临时目录：
    # 受限环境下 %TEMP% 不可写，子进程会以 PermissionError 失败，
    # 自检就会变成"导出失败"这种与逻辑无关的假警报（实测踩到）。
    scratch = os.path.join(_HERE, "tests", "_scratch")
    os.makedirs(scratch, exist_ok=True)
    target = os.path.join(scratch, "roundtrip.xlsx")
    try:
        env = dict(os.environ, QC_SPEC_NO_OVERRIDE="1")
        # 子进程也要用同一个 Python，并把临时目录指到项目内 ——
        # openpyxl 自身也会用到 tempfile，不能让默认值把它带到 %TEMP%。
        env.setdefault("TEMP", scratch)
        env.setdefault("TMP", scratch)
        # 捕获输出：失败时能把真实原因带出来，而不是只报"导出失败"。
        proc = subprocess.run(
            [sys.executable, os.path.join(_HERE, "export_spec_library.py"),
             "--out", target],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=env)
        if proc.returncode != 0 or not os.path.isfile(target):
            print(f"  ❌ 导出失败（退出码 {proc.returncode}）")
            tail = (proc.stdout or "").strip().splitlines()[-8:]
            for ln in tail:
                print(f"     {ln}")
            return 2
        overrides, diffs, _ = collect(target)
        if diffs:
            print(f"  ❌ 回导发现 {len(diffs)} 处差异 —— 导出/回导不自洽：")
            for sheet, where, old, new in diffs[:20]:
                print(f"      [{sheet}] {where}：{old} → {new}")
            return 1
        print("  ✅ 零差异：导出与回导自洽，表结构一致。")
        return 0
    except SheetError as exc:
        print(f"  ❌ 结构问题：{exc}")
        return 1
    finally:
        try:
            if os.path.isfile(target):
                os.remove(target)
            if os.path.isdir(scratch) and not os.listdir(scratch):
                os.rmdir(scratch)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
