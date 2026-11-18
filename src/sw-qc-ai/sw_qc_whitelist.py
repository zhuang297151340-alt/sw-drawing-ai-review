# -*- coding: utf-8 -*-
"""
sw_qc_whitelist.py — 「确认无误」白名单（用户反馈闭环）

## 为什么需要它

这个工具的误报来自一个**列不完的长尾**：

    倒角派生   → 已实现（v0.8）
    螺纹大径/小径 → 待做
    沉孔       → 待做
    退刀槽     → 待做
    阵列分布圆  → 待做
    对称件派生  → 待做
    ……

每加一条规则，都能消掉一批误报，但真实图纸里还有下一批。**穷举是走不通的**。

换一个思路：把「这条我确认过，不是问题」这件事**记住**。
用户点一次，以后同类的不再报 —— 这是唯一能覆盖长尾的办法。

## 两种记住的范围（用户可选）

| 范围 | scope | 含义 | 典型场景 |
|---|---|---|---|
| 这类问题 | `detail_prefix` | 同一条规则 **且** 同一段描述（**数值抹平**） | 「凡是『某个圆漏标』这种提示，我确认过不用报」 |
| 整个规则 | `rule` | 规则相同就够 | 「这版图就是没画签署栏，别再报了」 |

**安全性**：`rule` 级只让用户主动选（界面上是第二个按钮），不作为默认。
因为它的粒度更粗 —— 用户要清楚自己在说「这一整类都别报了」。
谁点的、什么时候点的、当时图上的描述是什么，全部落盘可查（见 `note`）。

## 存储

默认落在项目根目录的 `qc_whitelist.json`（可用环境变量 `QC_WHITELIST` 覆盖）。
落盘是**原子写**（先写 .tmp 再 os.replace），避免写一半断电导致白名单损坏 ——
白名单一旦损坏，用户点过的确认就全丢了。
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


import datetime
import hashlib
import json
import os
import re

WHITELIST_VERSION = 1
DEFAULT_WHITELIST_PATH = os.path.join(_HERE, "qc_whitelist.json")
ENV_WHITELIST_PATH = "QC_WHITELIST"

# 描述前缀取多长（数值抹平后）。太长：描述后半段一有差异就不匹配；
# 太短：不同规则下形状相近的问题会张冠李戴。24 个字足够覆盖
# 「几何 φ# 漏标（疑似漏标），零」这类描述形状，又不会跨规则误伤
# （匹配时还要求 rule_id 相同，双重保险）。
DETAIL_PREFIX_LEN = 24

SCOPE_RULE = "rule"
SCOPE_DETAIL_PREFIX = "detail_prefix"
VALID_SCOPES = (SCOPE_RULE, SCOPE_DETAIL_PREFIX)

_SEP = "\u0001"
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_for_match(text: str) -> str:
    """
    把描述里的**具体数值**抹平，只留下「问题的形状」。

    为什么必须这么做（本项目踩过的坑）：
    描述按用户要求简化成「几何 φ13.2 漏标（疑似漏标），零件上的孔/外圆…」之后，
    **直径跑到了最前面**。若直接拿描述开头当匹配键，φ13.2 与 φ26.4 就是两个
    不同的键 —— 用户点了「这类无误」，换张图照样报，那这个功能等于白做。

    在工程师眼里这两个是同一类问题（都是「某个圆漏标」），所以数值不参与匹配。
    注意：只抹平**数值**。剖视图的 A/B 这类字母保留区分，
    所以「缺少 A-A 剖视图」被确认，不会连带把 B-B 也吞掉。

    这与项目一贯口径一致：用户明确要求「宁可漏报也不要误报」，
    宁可多问一次，也不要让同一个误报反复出现消耗信任。
    """
    return _NUM_RE.sub("#", text or "")


def resolve_path(path: str | None = None) -> str:
    """白名单文件路径：显式参数 > 环境变量 > 默认（项目根目录）。"""
    if path:
        return os.path.abspath(path)
    env = os.environ.get(ENV_WHITELIST_PATH)
    if env:
        return os.path.abspath(env)
    return DEFAULT_WHITELIST_PATH


def _text(value) -> str:
    return str(value or "").strip()


def _as_dict(issue) -> dict:
    """Issue 对象或 dict 都能吃进来。"""
    if isinstance(issue, dict):
        return issue
    if hasattr(issue, "as_dict"):
        return issue.as_dict()
    return dict(getattr(issue, "__dict__", {}) or {})


def detail_prefix(issue, limit: int = DETAIL_PREFIX_LEN) -> str:
    """
    取描述开头（**数值已抹平**）作为匹配键 —— 即「问题的形状」。

    用 detail 而不是 title：同一规则下不同要素的 detail 不一样
    （「缺少 A-A 剖视图」vs「缺少 B-B 剖视图」），而 title 往往相同。
    取开头而不是整段，是为了让「同一条描述、只是数值不同」能归为一类。
    """
    d = _as_dict(issue)
    raw = _text(d.get("detail")) or _text(d.get("title"))
    return normalize_for_match(raw)[:limit]


def fingerprint(issue) -> str:
    """问题指纹（sha1 前 16 位）。规则 + 标题 + 位置，不含 detail —— 详见模块文档。"""
    d = _as_dict(issue)
    parts = [_text(d.get("rule_id")), _text(d.get("title")), _text(d.get("location"))]
    return hashlib.sha1(_SEP.join(parts).encode("utf-8")).hexdigest()[:16]


def scope_key(issue, scope: str) -> str:
    """按作用域算匹配键。"""
    d = _as_dict(issue)
    if scope == SCOPE_RULE:
        return _text(d.get("rule_id"))
    if scope == SCOPE_DETAIL_PREFIX:
        return detail_prefix(issue)
    raise ValueError(f"未知作用域 {scope!r}，可用：{SCOPE_RULE} / {SCOPE_DETAIL_PREFIX}")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class WhitelistStore:
    """白名单的读写与匹配。所有方法都不会因为文件缺失/损坏而抛异常。"""

    def __init__(self, path: str | None = None, autoload: bool = True):
        self.path = resolve_path(path)
        self.entries: list[dict] = []
        self.load_error: str = ""
        if autoload:
            self.load()

    # ---------------------------------------------------------------- 读写
    def load(self) -> list[dict]:
        self.entries = []
        self.load_error = ""
        if not os.path.isfile(self.path):
            return self.entries
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            # 损坏时不能崩：工具照常用，只是白名单失效。
            # 把文件改名留档，避免下次保存时把损坏内容直接覆盖掉（等于毁掉用户点过的记录）。
            self.load_error = f"白名单读取失败（已忽略并备份）：{exc}"
            try:
                os.replace(self.path, self.path + ".broken")
            except Exception:
                pass
            return self.entries

        raw = data.get("whitelist") if isinstance(data, dict) else data
        if isinstance(raw, list):
            for e in raw:
                if isinstance(e, dict) and e.get("scope") in VALID_SCOPES:
                    self.entries.append(e)
        return self.entries

    def save(self) -> str:
        payload = {
            "version": WHITELIST_VERSION,
            "note": "「确认无误」白名单：命中的检查项不再报告。可随时手工删除本文件重置。",
            "whitelist": self.entries,
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)          # 原子替换，避免写一半损坏
        return self.path

    # ---------------------------------------------------------------- 匹配
    def suppression_reason(self, issue) -> str:
        """
        命中则返回原因说明，未命中返回空串。

        `rule` 级优先返回（说明得更具体）。

        匹配键刻意**不含** fingerprint：fingerprint 里带 title（含具体数值），
        拿它一起比会让「数值变了就失效」。这里只看
        「同一条规则 + 同一段描述形状」，这才是用户眼中的「同一类问题」。
        """
        d = _as_dict(issue)
        rule_id = _text(d.get("rule_id"))
        dp = detail_prefix(issue)
        for e in self.entries:
            if e.get("scope") == SCOPE_RULE and e.get("rule_id") == rule_id:
                return f"已确认无误：{rule_id} 整类不再报"
        for e in self.entries:
            if (e.get("scope") == SCOPE_DETAIL_PREFIX and e.get("rule_id") == rule_id
                    and e.get("detail_prefix") == dp):
                return "已确认无误：这类问题不再报"
        return ""

    def is_suppressed(self, issue) -> bool:
        return bool(self.suppression_reason(issue))

    def filter(self, issues) -> tuple[list, list]:
        """返回 (保留的问题, 被抑制的记录)。被抑制的**不丢弃**，供报告如实说明。"""
        kept, dropped = [], []
        for issue in issues:
            reason = self.suppression_reason(issue)
            if reason:
                d = _as_dict(issue)
                dropped.append({
                    "rule_id": _text(d.get("rule_id")),
                    "title": _text(d.get("title")),
                    "location": _text(d.get("location")),
                    "reason": reason,
                })
            else:
                kept.append(issue)
        return kept, dropped

    # ---------------------------------------------------------------- 记录
    def add(self, issue, scope: str, note: str = "") -> dict:
        """把一个判定为「确认无误」的问题写进白名单。"""
        if scope not in VALID_SCOPES:
            raise ValueError(f"未知作用域 {scope!r}，可用：{SCOPE_RULE} / {SCOPE_DETAIL_PREFIX}")
        d = _as_dict(issue)
        entry = {
            "scope": scope,
            "rule_id": _text(d.get("rule_id")),
            "title": _text(d.get("title")),
            "fingerprint": fingerprint(issue),
            "detail_prefix": detail_prefix(issue),
            "detail": _text(d.get("detail")),
            "location": _text(d.get("location")),
            "note": _text(note),
            "added_at": _now_iso(),
        }
        self.entries.append(entry)
        return entry

    def remove(self, index: int) -> dict | None:
        """按序号删掉一条（界面上的「撤销」）。"""
        if 0 <= index < len(self.entries):
            return self.entries.pop(index)
        return None

    def clear(self) -> int:
        n = len(self.entries)
        self.entries = []
        return n

    def summary(self) -> list[dict]:
        """给界面用：当前记住了哪些。"""
        out = []
        for i, e in enumerate(self.entries):
            out.append({
                "index": i,
                "scope": e.get("scope"),
                "scope_label": "整类" if e.get("scope") == SCOPE_RULE else "这一条",
                "rule_id": e.get("rule_id", ""),
                "title": e.get("title", ""),
                "detail_prefix": e.get("detail_prefix", ""),
                "added_at": e.get("added_at", ""),
            })
        return out


def report_suppressed(dropped: list, limit: int = 10) -> str:
    """被抑制问题的说明文字（写进报告，避免「静默隐藏」）。"""
    if not dropped:
        return ""
    lines = [f"另有 {len(dropped)} 条已被『确认无误』白名单抑制（不计分）："]
    for item in dropped[:limit]:
        loc = f"[{item['location']}] " if item.get("location") else ""
        lines.append(f"  · {loc}{item.get('title') or item.get('rule_id')}"
                     f"（{item.get('reason')}）")
    if len(dropped) > limit:
        lines.append(f"  · …还有 {len(dropped) - limit} 条")
    lines.append("  如需恢复报告，删除 qc_whitelist.json 或在该文件里去掉对应条目。")
    return "\n".join(lines)


def create_issue_dict(rule_id: str, title: str, detail: str = "",
                      location: str = "", severity: str = "minor") -> dict:
    """便于测试与外部调用构造一个 issue dict。"""
    return {
        "rule_id": rule_id, "title": title, "detail": detail,
        "location": location, "severity": severity,
    }


__all__ = [
    "WhitelistStore", "fingerprint", "detail_prefix", "scope_key",
    "report_suppressed", "create_issue_dict", "resolve_path",
    "SCOPE_RULE", "SCOPE_DETAIL_PREFIX", "VALID_SCOPES",
    "WHITELIST_VERSION", "DEFAULT_WHITELIST_PATH", "ENV_WHITELIST_PATH",
]
