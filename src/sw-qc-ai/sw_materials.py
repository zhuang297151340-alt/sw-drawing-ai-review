# -*- coding: utf-8 -*-
"""
sw_materials.py — 读取 SolidWorks 自带材质库里的材料名，用于"材料是否认识"的判定。

为什么这样做（用户要求）：
    常用材料不要靠我们手写字典，直接读 SW 安装目录里的 *.sldmat（材质库），
    这样不同行业的材料都能认出来，减少"材料未识别"的误报。

材质库文件是 UTF-16 的 XML，材料名在 <material name="..."> 上。
路径由插件通过 ISldWorks.GetMaterialDatabases() 取出来，放在 .sw.json 的 material_dbs 里。
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

_CACHE_NAME = "sw_materials_cache.json"
_names = None      # list[str]
_lookup = None     # 归一化后的集合字符串（用于快速包含判断）


def _norm(s: str) -> str:
    t = str(s or "").strip().upper()
    t = t.replace("（", "(").replace("）", ")")
    t = re.sub(r"\s+", "", t)
    return t


def _parse_sldmat(path: str):
    """解析一个 .sldmat，返回材料名列表。"""
    out = []
    try:
        tree = ET.parse(path)
    except Exception:
        return out
    for el in tree.getroot().iter():
        tag = el.tag.split("}")[-1]
        name = el.get("name")
        if tag == "material" and name:
            out.append(str(name).strip())
    return out


def _cache_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "SwQcAddin")
    return os.path.join(d, _CACHE_NAME)


def load_names(db_paths):
    """加载所有材质库的材料名（带简单缓存，按文件 mtime 判断是否重读）。"""
    global _names, _lookup
    if _names is not None:
        return _names

    cache_file = _cache_path()
    sig = {}
    for p in db_paths or []:
        try:
            sig[p] = int(os.path.getmtime(p))
        except Exception:
            sig[p] = 0

    # 命中缓存
    try:
        if os.path.isfile(cache_file):
            with open(cache_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("sig") == sig and data.get("names"):
                _names = data["names"]
                _lookup = {_norm(n) for n in _names}
                return _names
    except Exception:
        pass

    names = []
    for p in db_paths or []:
        names.extend(_parse_sldmat(p))
    # 去重保序
    seen = set()
    uniq = []
    for n in names:
        k = _norm(n)
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)
    _names = uniq
    _lookup = seen

    # 写缓存（失败不影响功能）
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump({"sig": sig, "names": uniq}, fh, ensure_ascii=False)
    except Exception:
        pass
    return _names


def _tokens(raw: str):
    """从材料文字里抽"有效记号"：数字牌号、字母数字组合（6061/T6/40Cr/Q235）。"""
    t = str(raw or "").upper()
    toks = re.findall(r"[A-Z]{0,3}\d{2,5}[A-Z0-9\-]*|\d{2,5}", t)
    return [x for x in toks if len(x) >= 2]


def is_known(raw: str, db_paths=None):
    """材料是否能在 SW 材质库里找到。

    返回 (是否认识, 匹配到的材质库名称)。
    判定顺序：整名包含 → 记号匹配（如 5052、6061、Q235）。
    宽松匹配是故意的：这里只用来避免"材料未识别"的误报，不用于判错。
    """
    if not raw:
        return False, ""
    names = load_names(db_paths)
    if not names:
        return False, ""
    n = _norm(raw)
    if not n:
        return False, ""

    # 1) 整名互相包含（材料里写"铝板5052"，库里有"5052 铝合金"→ 走下面记号）
    for name in names:
        nn = _norm(name)
        if nn and (nn in n or (len(n) >= 3 and n in nn)):
            return True, name

    # 2) 记号匹配：45、5052、6061、Q235、40CR、T6…
    toks = _tokens(raw)
    if not toks:
        return False, ""
    for name in names:
        nn = _norm(name)
        for t in toks:
            # 纯数字要求长度≥4（避免 45 撞到 1045 之类的误判）；
            # 带字母的牌号（Q235/40CR/T6）长度≥2 即可
            if t.isdigit():
                # 4 位以上（5052/6061/7075）直接包含即可；
                # 2~3 位（45、40、304）必须按数字边界匹配，避免 45 撞上 1045
                if len(t) >= 4:
                    if t in nn:
                        return True, name
                else:
                    if re.search(r"(?<!\d)" + re.escape(t) + r"(?!\d)", nn):
                        return True, name
            else:
                if t in nn:
                    return True, name
    return False, ""
