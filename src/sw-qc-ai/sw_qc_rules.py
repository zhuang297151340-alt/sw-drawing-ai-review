# -*- coding: utf-8 -*-
"""
sw_qc_rules.py — 规则引擎

实现上游 `skills/sw-drawing-qc-pro.md` 中描述的「规则引擎」模块：
    • 7 大行业预设 + iso/gb 基线
    • 5 种规则匹配模式：always / field_check / feature_check /
      count_threshold / text_contains
    • 企业自定义规则通过 YAML 文件扩展（rules/ 目录）

与上游文档的差异（有意为之）
    上游只描述了「7 大预设」，未公开规则文件格式。本实现把规则全部外置成
    YAML，使工艺/标准部门可以不改代码就维护规则——这是把工具从 demo 变成
    可落地产品的关键一步。
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


import os
import re
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

import sw_qc_gb as gb

# --------------------------------------------------------------------------
# 螺纹规格解析（THD-002：细牙必须标螺距、粗牙可省略）
# --------------------------------------------------------------------------
# 图上螺纹写法实测很杂，至少要认这几种：
#     M10            M10×1.25       M10x1.25      M10X1.25
#     M10-6H         M10×1.25-6H    M 10 × 1.25   M10×1,25
# 粗牙「M10」不带螺距是**合法的**，所以不能一见 M 就报 —— 必须查标准表。
_THREAD_SPEC_RE = re.compile(
    r"\bM\s*(\d+(?:\.\d+)?)"                 # 公称直径
    r"(?:\s*[×xX*]\s*(\d+(?:[.,]\d+)?))?"    # 可选螺距
)


def iter_thread_specs(text: str) -> list[tuple[str, float, float | None]]:
    """
    从文本里解析出所有米制普通螺纹规格。

    返回 [(代号, 公称直径, 螺距或 None)]，例如：
        "M10"        -> [("M", 10.0, None)]
        "2-M10×1.25" -> [("M", 10.0, 1.25)]

    只认 `M + 数字` 形式；G/Rc/NPT 等其它螺纹体系本函数不管（另有规则覆盖）。
    """
    out: list[tuple[str, float, float | None]] = []
    for m in _THREAD_SPEC_RE.finditer(text or ""):
        try:
            dia = float(m.group(1))
        except (TypeError, ValueError):
            continue
        pitch = None
        if m.group(2):
            try:
                pitch = float(m.group(2).replace(",", "."))
            except ValueError:
                pitch = None
        out.append(("M", dia, pitch))
    return out

# 标准号在真实图纸里的写法五花八门：GB/T 1804、GB1804、GB/T1804、GB 1804、
# GB1804-m …… 直接子串匹配必然漏判（实测：图上是「未注尺寸公差按GB1804-m级执行」，
# 规则里写 "GB/T 1804"，结果报出「未引用 GB/T 1804」的假问题）。
# 统一把空白、斜杠、连字符、点、下划线去掉再比，就能全部命中。
_NORM_STRIP_RE = re.compile(r"[\s/\\\-\._、，,]+")


def normalize_ref(text: str) -> str:
    """
    把标准号/条款文本归一化，用于宽松匹配。

    除了去掉空白与分隔符，还要把 "GB/T" 视同 "GB"：
    图纸上普遍写成「GB1804-m」，而标准全称是「GB/T 1804」，
    只去分隔符会得到 GBT1804，仍然匹配不上 GB1804。
    """
    s = (text or "").upper()
    s = s.replace("GB/T", "GB").replace("GBT", "GB")
    return _NORM_STRIP_RE.sub("", s)


def text_matches(haystack: str, patterns: list[str]) -> bool:
    """原文包含 或 归一化后包含，任一命中即算匹配。"""
    raw = (haystack or "").lower()
    norm = normalize_ref(haystack)
    for p in patterns or []:
        p = str(p)
        if p.lower() in raw:
            return True
        np = normalize_ref(p)
        if np and np in norm:
            return True
    return False

# 规则文件目录（与本文件同级）
RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")

SEVERITY_CRITICAL = "critical"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"

SEVERITY_LABEL = {
    SEVERITY_CRITICAL: "🔴 严重（影响制造）",
    SEVERITY_MAJOR: "🟡 一般（建议修改）",
    SEVERITY_MINOR: "🟢 轻微（优化建议）",
}


@dataclass
class Issue:
    """一条检查发现。"""
    rule_id: str
    category: str
    severity: str
    title: str
    detail: str = ""
    suggestion: str = ""
    standard: str = ""
    location: str = ""
    evidence: str = ""      # 支撑该结论的原始证据（几何/文本），便于人工复核

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "standard": self.standard,
            "location": self.location,
            "evidence": self.evidence,
        }


@dataclass
class Rule:
    id: str
    name: str
    category: str = "通用"
    severity: str = SEVERITY_MAJOR
    mode: str = "always"
    params: dict = field(default_factory=dict)
    message: str = ""
    suggestion: str = ""
    standard: str = ""
    enabled: bool = True


# --------------------------------------------------------------------------
# 内置兜底预设（当 rules/ 目录缺失时仍可运行）
# --------------------------------------------------------------------------
BUILTIN_PRESETS: dict[str, list[dict]] = {
    "gb_baseline": [
        {"id": "GB-MAT", "name": "材料未标注", "category": "标题栏",
         "severity": "critical", "mode": "field_check", "params": {"field": "材料"},
         "message": "标题栏未检出材料字段",
         "suggestion": "补充材料牌号及标准号，如 45 GB/T 699-2015",
         "standard": "GB/T 4458"},
        {"id": "GB-DIM", "name": "无尺寸标注", "category": "尺寸",
         "severity": "critical", "mode": "count_threshold",
         "params": {"metric": "dimension_total", "min": 1},
         "message": "图纸中未检出任何尺寸标注，零件无法制造",
         "suggestion": "补齐定形尺寸与定位尺寸", "standard": "GB/T 4458.4"},
        {"id": "GB-TOLTXT", "name": "未引用一般公差标准", "category": "技术要求",
         "severity": "major", "mode": "text_contains",
         "params": {"patterns": ["GB/T 1804", "GB1804", "GB 1804", "未注尺寸公差",
                                 "未注公差", "一般公差"]},
         "message": "技术要求中未引用 GB/T 1804 未注尺寸公差",
         "suggestion": "增加『未注尺寸公差按 GB/T 1804-m』", "standard": "GB/T 1804"},
        {"id": "GB-GDT", "name": "未引用未注几何公差标准", "category": "技术要求",
         "severity": "major", "mode": "text_contains",
         "params": {"patterns": ["GB/T 1184", "GB1184", "GB 1184", "未注几何公差"]},
         "message": "技术要求中未引用 GB/T 1184 未注几何公差",
         "suggestion": "增加『未注几何公差按 GB/T 1184-K』", "standard": "GB/T 1184"},
        {"id": "GB-RA", "name": "未规定表面粗糙度", "category": "粗糙度",
         "severity": "major", "mode": "count_threshold",
         "params": {"metric": "roughness_count", "min": 1},
         "message": "图纸中未检出任何表面粗糙度标注（含符号块与「其余 N」统注）",
         "suggestion": "标注各加工面 Ra 值，或用「其余 N」统注",
         "standard": "GB/T 131"},
        {"id": "GB-DEBURR", "name": "未要求去毛刺/倒钝", "category": "技术要求",
         "severity": "minor", "mode": "text_contains",
         "params": {"patterns": ["去毛刺", "棱边", "倒钝", "毛刺"]},
         "message": "技术要求缺少去毛刺或棱边倒钝要求",
         "suggestion": "增加『去除毛刺，棱边倒钝』"},
        {"id": "GB-NOTES", "name": "缺少技术要求条目", "category": "技术要求",
         "severity": "major", "mode": "count_threshold",
         "params": {"metric": "tech_note_count", "min": 1},
         "message": "未检出技术要求文字块",
         "suggestion": "补充技术要求（一般公差/热处理/表面处理/去毛刺）",
         "standard": "GB/T 4458"},
        {"id": "GB-CONC", "name": "同心结构未见配合公差", "category": "公差",
         "severity": "minor", "mode": "feature_check",
         "params": {"feature": "同心圆组",
                    "require_text": ["H7", "H8", "H6", "h6", "h7", "g6", "f7", "k6",
                                     "m6", "n6", "JS6", "P7", "±", "+0", "-0"]},
         "message": "检出同心圆组（{n} 个同心圆，{radii}），但全图未见配合公差代号或极限偏差",
         "suggestion": "按配合性质对相关直径标注公差（如 φ…H7），或注明由 GB/T 1804 一般公差覆盖",
         "standard": "GB/T 1801"},
        {"id": "GB-BEARING", "name": "轴承配合孔缺少公差", "category": "公差",
         "severity": "critical", "mode": "feature_check",
         "params": {"feature": "轴承安装孔（推定）",
                    "require_text": ["H7", "H6", "K6", "JS6", "M6", "N6", "P6"]},
         "message": "图纸文字提到轴承，且检出同心圆组（{n} 个，{radii}），但未标注 H7 等轴承配合公差",
         "suggestion": "对相应孔径标注 H7，并按需补圆柱度/同轴度与 Ra 0.4-0.8",
         "standard": "GB/T 1801"},
        {"id": "GB-KEYWAY", "name": "键槽缺少公差", "category": "公差",
         "severity": "major", "mode": "feature_check",
         "params": {"feature": "键槽", "require_text": ["N9", "JS9", "P9", "对称度"]},
         "message": "检出键槽，但未标注宽度公差 N9 或对称度",
         "suggestion": "键槽宽标注 N9，并注对称度（如 0.02）", "standard": "GB/T 1095"},
        {"id": "GB-THREAD", "name": "螺纹标注不完整", "category": "尺寸",
         "severity": "major", "mode": "feature_check",
         "params": {"feature": "螺纹孔", "require_text": ["M", "G", "Rc", "Rp", "Tr"]},
         "message": "检出螺纹孔，但未标注完整螺纹规格",
         "suggestion": "标注 Mxx 规格与螺纹深度，如 M10-6H ↧20", "standard": "GB/T 4459.1"},
        {"id": "GB-PATTERN", "name": "孔组缺少位置度", "category": "公差",
         "severity": "major", "mode": "feature_check",
         "params": {"feature": "孔组", "require_text": ["位置度", "POS", "⌖"]},
         "message": "检出孔组（阵列孔），但未标注位置度或分布圆尺寸",
         "suggestion": "标注分布圆直径、数量与位置度", "standard": "GB/T 1182"},
    ]
}


def _load_yaml_document(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("缺少 PyYAML，无法加载 YAML 规则：%s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_yaml_rules(path: str) -> list[dict]:
    data = _load_yaml_document(path)
    rules = data.get("rules", [])
    return [r for r in rules if isinstance(r, dict)]


def _find_preset_file(preset: str) -> str | None:
    if os.path.isdir(RULES_DIR):
        for ext in (".yaml", ".yml"):
            candidate = os.path.join(RULES_DIR, preset + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


def _resolve_rules(preset: str, _seen: tuple[str, ...] = ()) -> list[dict]:
    """
    解析预设规则，支持 `extends: <preset>` 继承。
    父预设规则在前，子预设规则在后；同 id 时子规则覆盖父规则。
    """
    if preset in _seen:
        raise KeyError(f"规则预设继承出现循环：{' -> '.join(_seen + (preset,))}")

    raw: list[dict] = []
    parent = None
    path = _find_preset_file(preset)
    if path:
        doc = _load_yaml_document(path)
        parent = doc.get("extends")
        raw = [r for r in (doc.get("rules") or []) if isinstance(r, dict)]
    else:
        raw = list(BUILTIN_PRESETS.get(preset, []) or [])
        if preset not in BUILTIN_PRESETS:
            return None  # 未知预设

    if parent:
        inherited = _resolve_rules(parent, _seen + (preset,))
        if inherited is None:
            raise KeyError(f"预设『{preset}』继承的父预设『{parent}』不存在")
        by_id = {r.get("id"): r for r in inherited}
        for r in raw:
            by_id[r.get("id")] = r          # 子规则覆盖同 id 父规则
        # 保持父规则顺序，子规则中新增的追加在后面
        ordered, added = [], set()
        for r in inherited:
            rid = r.get("id")
            ordered.append(by_id[rid])
            added.add(rid)
        for r in raw:
            if r.get("id") not in added:
                ordered.append(r)
                added.add(r.get("id"))
        return ordered
    return raw


def list_presets() -> list[str]:
    """列出可用预设：磁盘 YAML 优先，其次内置。"""
    names = set(BUILTIN_PRESETS)
    if os.path.isdir(RULES_DIR):
        for fn in os.listdir(RULES_DIR):
            if fn.endswith((".yaml", ".yml")):
                names.add(os.path.splitext(fn)[0])
    return sorted(names)


def load_rules(preset: str) -> list[Rule]:
    """加载预设规则；先找 rules/<preset>.yaml，再回退到内置，支持 extends 继承。"""
    raw = _resolve_rules(preset)
    if raw is None:
        raise KeyError(
            f"未知规则预设『{preset}』，可用：{', '.join(list_presets())}")
    return [
        Rule(
            id=r.get("id", "?"),
            name=r.get("name", r.get("id", "?")),
            category=r.get("category", "通用"),
            severity=r.get("severity", SEVERITY_MAJOR),
            mode=r.get("mode", "always"),
            params=r.get("params", {}) or {},
            message=r.get("message", ""),
            suggestion=r.get("suggestion", ""),
            standard=r.get("standard", ""),
            enabled=r.get("enabled", True),
        )
        for r in raw
    ]


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
class RuleEngine:
    def __init__(self, preset: str = "gb_baseline", extra_files: list[str] | None = None):
        self.preset = preset
        self.rules = load_rules(preset)
        for path in extra_files or []:
            for r in _load_yaml_rules(path):
                self.rules.append(Rule(
                    id=r.get("id", "?"), name=r.get("name", "?"),
                    category=r.get("category", "自定义"),
                    severity=r.get("severity", SEVERITY_MAJOR),
                    mode=r.get("mode", "always"), params=r.get("params", {}) or {},
                    message=r.get("message", ""), suggestion=r.get("suggestion", ""),
                    standard=r.get("standard", ""),
                ))

    # -- 各模式求值 ---------------------------------------------------------
    def evaluate(self, model, features: list, stats: dict) -> list[Issue]:
        issues: list[Issue] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            hit = self._eval_rule(rule, model, features, stats)
            issues.extend(hit)
        return issues

    def _eval_rule(self, rule: Rule, model, features, stats) -> list[Issue]:
        mode = rule.mode
        if mode == "always":
            return [self._issue(rule, rule.message or rule.name)]

        if mode == "field_check":
            fld = rule.params.get("field", "")
            value = (model.title_block or {}).get(fld, "")
            if not str(value).strip():
                return [self._issue(rule, rule.message or f"{fld}未填写")]
            return []

        if mode == "count_threshold":
            metric = rule.params.get("metric", "")
            actual = stats.get(metric, 0)
            lo, hi = rule.params.get("min"), rule.params.get("max")
            bad = (lo is not None and actual < lo) or (hi is not None and actual > hi)
            if bad:
                return [self._issue(
                    rule, f"{rule.message or rule.name}（实测 {metric}={actual}）")]
            return []

        if mode == "text_contains":
            patterns = [str(p) for p in rule.params.get("patterns", [])]
            haystack = (model.all_text or "") + "\n" + "\n".join(
                str(v) for v in (model.title_block or {}).values())
            if not text_matches(haystack, patterns):
                return [self._issue(rule, rule.message or rule.name)]
            return []

        if mode == "feature_check":
            want = rule.params.get("feature", "")
            require = [str(t) for t in rule.params.get("require_text", [])]
            hits = [f for f in features if f.type == want]
            if not hits:
                return []
            # require_text 用归一化匹配，容忍 GB1804 / GB/T 1804 这类写法差异
            if require and text_matches(model.all_text or "", require):
                return []          # 图纸里已有对应标注，合规
            out = []
            for f in hits:
                msg = rule.message or f"{want} 缺少必要标注"
                fmt = dict(f.params or {})
                fmt["n"] = fmt.get("ring_count", len(hits))
                fmt["radii"] = "、".join(
                    f"R{r:g}" for r in (fmt.get("radii") or [])[:6]) or "—"
                fmt.setdefault("conf", f"{f.confidence:.0%}")
                # 只允许用几何事实做占位符，不提供任何"编出来的直径"
                try:
                    msg = msg.format(**fmt)
                except (KeyError, IndexError, ValueError):
                    pass
                out.append(self._issue(
                    rule, msg,
                    evidence="；".join(f.evidence),
                    location=f"({f.location[0]:.1f}, {f.location[1]:.1f})"))
            return out

        if mode == "thread_pitch":
            return self._eval_thread_pitch(rule, model)

        return []

    @staticmethod
    def _eval_thread_pitch(rule: Rule, model) -> list[Issue]:
        """
        按 GB/T 193 查表判断「螺纹规格是否必须写出螺距」。

        为什么需要单独一种模式：这判据既不是「文中是否含某关键词」（text_contains），
        也不是「某特征是否缺标注」（feature_check），而是
        **把图上每个螺纹规格解析出来，逐个查标准表**：
          M10        → 有粗牙，可省略螺距   → 不报
          M32        → 该直径无粗牙，必须写 → 报
          M10×9.9    → 螺距不在标准系列内   → 报

        只用 DXF 文本即可判定，不需要几何。
        """
        text = model.all_text or ""
        seen: set[tuple] = set()
        out: list[Issue] = []
        for kind, dia, pitch in iter_thread_specs(text):
            key = (kind, dia, pitch)
            if key in seen:
                continue
            seen.add(key)
            must, why = gb.thread_pitch_required(dia, pitch)
            if not must:
                continue
            label = f"{kind}{dia:g}" + (f"×{pitch:g}" if pitch is not None else "")
            out.append(RuleEngine._issue(
                rule,
                f"{label}：{why}" if why else (rule.message or rule.name),
                evidence=label))
        return out

    @staticmethod
    def _issue(rule: Rule, message: str, evidence: str = "", location: str = "") -> Issue:
        return Issue(
            rule_id=rule.id, category=rule.category, severity=rule.severity,
            title=rule.name, detail=message,
            suggestion=rule.suggestion, standard=rule.standard,
            location=location, evidence=evidence,
        )
