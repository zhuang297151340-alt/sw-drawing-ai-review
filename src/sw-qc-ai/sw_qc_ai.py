# -*- coding: utf-8 -*-
"""
sw_qc_ai.py — AI 语义检查（接 API，只做技术要求语义）

## 定位

本地规则能确定的事（几何、计数、查表）**一律不交给 AI** —— 那些算法又快又准。
AI 只做 **10 条纯文本语义**规则（技术要求与材料/工艺之间的矛盾与缺失）。

## 硬规定（用户 2026-10-13 要求，已写入规则库 AI_POLICY）

| 项 | 规定 |
|---|---|
| 硬超时 | **20 秒**，超时立即放弃并注明 |
| 回复长度 | 每条结论尽量完整（问题+依据+风险+怎么改）；**条数不限** |
| 必须引用原文 | 每条尽量给图上原话（也可引用尺寸/公差）；对不上原文的降级为轻微，不丢信息 |
| 不确定就不报 | 不得用「可能/建议关注」凑数 |
| 输出格式 | 结构化 JSON；解析失败时尽量抢救文本，不整条丢弃 |
| 不做设计 | 只指矛盾/缺失，不给设计建议 |
| 调用次数 | 每次审图**只调 1 次**（不是每条规则一次） |
| 失败降级 | 无密钥/网络失败/超时/非 JSON → 静默降级，**不影响本地结果** |

其中「**原文必须能在图上找到**」是防幻觉的关键：模型编出来的依据过不了校验。

## 配置

优先级：环境变量 > `qc_ai_config.json`。示例：

    {
      "enabled": true,
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "api_key": "sk-...",
      "timeout": 20,
      "max_findings": 3
    }

环境变量：`QC_AI_API_KEY` / `QC_AI_BASE_URL` / `QC_AI_MODEL` / `QC_AI_ENABLED` /
`QC_AI_TIMEOUT`。**没配密钥时本模块完全不动作**，工具行为与以前一致。
"""
from __future__ import annotations

# --- 确保模块能找到同目录的兄弟模块
import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
# -----------------------------------------------------------------

import json
import os
import re
import time
import urllib.error
import urllib.request

CONFIG_FILENAME = "qc_ai_config.json"
DEFAULT_TIMEOUT = 20.0          # 硬超时（用户规定）
DEFAULT_MAX_FINDINGS = 0        # 2026-10-26：0 = 不限条数（用户要求释放）
MAX_CONCLUSION_LEN = 600        # 2026-10-26：放宽（原来 100 字太紧）
MAX_QUOTE_LEN = 200

# AI 负责的规则（只有这些；见 rule_catalog.AI_SEMANTIC_RULES）
ALLOWED_RULES = {
    "TECH-001": "技术要求与材料是否冲突",
    "TECH-002": "技术要求内部是否自相矛盾",
    "TECH-003": "技术要求与单独标注是否冲突",
    "TECH-007": "表面处理是否明确处理范围",
    "TECH-008": "处理层是否影响关键尺寸",
    "TECH-009": "硬度/层深等是否完整",
    "TECH-011": "疑似复制旧图纸残留要求",
    "MAT-003": "材料与热处理要求冲突",
    "MAT-004": "材料与表面处理冲突",
    "MAT-005": "材料与未注公差适用范围冲突",
    # 用户 2026-10-13 追加：表面处理是否合理、有没有耐蚀/生锈风险。
    # 注意：耐蚀**等级**由本地查表判定（CORE-SURF-*）；AI 只判
    # 「在给定材料/工艺/工况下这个处理是否合理」这一类需要综合理解的判断。
    "TECH-013": "表面处理的合理性与耐蚀风险",
    # —— 2026-10-14 补齐：规则目录里标「需接AI模型」但模块原先没覆盖的条目 ——
    # 这 5 条都是**纯文本/语义**判断，不需要几何，AI 能可靠做。
    "DIM-015": "对称关系是否明确",
    "KEY-007": "键槽端部形式/圆角是否说明",
    "ROUGH-005": "是否误加不合理的高粗糙度要求",
    "GEN-006": "默认公差标准与零件/工艺是否匹配",
    "CHAIN-006": "尺寸基准切换是否导致关系不清",
}

SEVERITY_BY_RULE = {
    "TECH-001": "critical", "TECH-002": "critical", "TECH-003": "major",
    "TECH-007": "major", "TECH-008": "major", "TECH-009": "major",
    "TECH-011": "major", "MAT-003": "major", "MAT-004": "major",
    "MAT-005": "major",
    "TECH-013": "major",
    # 补齐的 5 条：都属「提醒/规范」性质，不构成无法制造 —— 按规则目录的级别走
    "DIM-015": "major", "KEY-007": "minor", "ROUGH-005": "minor",
    "GEN-006": "major", "CHAIN-006": "minor",
}

SYSTEM_PROMPT = """你是机械工程图审核助手。只判断「技术要求」与「材料/工艺」之间的语义问题。

严格遵守：
1. 只从下面给定的图上文字里判断，不得引入图上没有的信息。
2. 只报你自己有把握的矛盾或缺失；没把握就少报或不报，不要用"可能""建议关注"凑数。
3. 每条结论不超过 40 字，必须直接说问题是什么。
4. 每条给 quote：图上**原话片段**（原样复制）；尺寸/几何公差类可引用尺寸或公差值。
5. 只指出问题，不要给设计建议（不要说"建议改用 XX 材料"）。
6. 条数不限：有多少个有依据的问题就报多少条；没问题返回空列表。

只允许这些规则 ID：
TECH-001 技术要求与材料冲突
TECH-002 技术要求内部自相矛盾
TECH-003 技术要求与单独标注冲突
TECH-007 表面处理未明确处理范围
TECH-008 处理层影响关键尺寸
TECH-009 硬度/层深不完整
TECH-011 疑似复制旧图纸残留要求
MAT-003 材料与热处理冲突
MAT-004 材料与表面处理冲突
MAT-005 材料与未注公差适用范围冲突
TECH-013 表面处理的合理性与耐蚀风险（例如：碳钢只写"发黑"未写油封 →
         潮湿环境易锈；铝件写镀锌；焊接件要求镀锌会破坏镀层）
DIM-015 对称关系不明确（对称件未注明"对称"或未说明只标一侧的依据）
KEY-007 键槽端部形式/圆角未说明（图上写了键槽但未说明 A/B/C 型或端部圆角）
ROUGH-005 误加不合理的高粗糙度要求（非功能面/非配合面要求了过高的 Ra，
         无谓推高加工成本。只在图上确实出现了高 Ra 与"非配合"表述并存时才报）
GEN-006 默认公差标准与零件/工艺不匹配（例如铸件/焊接件/钣金件却引用
         机加工用的未注公差标准 GB/T 1804，那套公差对铸锻毛坯面不适用）
CHAIN-006 尺寸基准频繁切换导致关系不清（图上基准字母换来换去、同一方向
         尺寸从多个不同基准出发，导致难以判断尺寸关系）

只输出 JSON，不要任何其它文字：
{"findings":[{"rule_id":"TECH-002","conclusion":"...","quote":"...","suggestion":"..."}]}"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def config_path() -> str:
    """配置文件默认放在包根目录（与 sw-qc-ai 同级），便携、跟着 U 盘走。"""
    return os.path.join(os.path.dirname(_HERE), CONFIG_FILENAME)


def load_config(path: str | None = None) -> dict:
    """环境变量 > 配置文件。读不到就是未启用。"""
    cfg = {"enabled": False, "base_url": "", "model": "", "api_key": "",
           "timeout": DEFAULT_TIMEOUT, "max_findings": DEFAULT_MAX_FINDINGS}
    p = path or config_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                cfg.update({k: v for k, v in (json.load(fh) or {}).items()
                            if k in cfg})
        except Exception:
            pass
    env = os.environ
    if env.get("QC_AI_API_KEY"):
        cfg["api_key"] = env["QC_AI_API_KEY"]
    if env.get("QC_AI_BASE_URL"):
        cfg["base_url"] = env["QC_AI_BASE_URL"]
    if env.get("QC_AI_MODEL"):
        cfg["model"] = env["QC_AI_MODEL"]
    if env.get("QC_AI_ENABLED") is not None:
        cfg["enabled"] = env["QC_AI_ENABLED"].lower() not in ("0", "false", "no", "")
    if env.get("QC_AI_TIMEOUT"):
        try:
            cfg["timeout"] = float(env["QC_AI_TIMEOUT"])
        except ValueError:
            pass
    # 兜底默认模型（用户说"最好接 API"）
    cfg["base_url"] = (cfg["base_url"] or "https://api.deepseek.com/v1").rstrip("/")
    cfg["model"] = cfg["model"] or "deepseek-chat"
    # 有密钥就视为启用（除非显式关掉）
    if cfg["api_key"] and "QC_AI_ENABLED" not in env and not cfg.get("enabled"):
        cfg["enabled"] = True
    return cfg


def availability(cfg: dict | None = None) -> tuple[bool, str]:
    """返回 (是否可用, 原因)。原因会写进报告，让用户知道为什么没有 AI 结论。"""
    c = cfg or load_config()
    if not c.get("enabled"):
        return False, "未启用 AI 语义检查（未配置 qc_ai_config.json 或密钥）"
    if not c.get("api_key"):
        return False, "未配置 API 密钥（qc_ai_config.json 的 api_key 或环境变量 QC_AI_API_KEY）"
    if not c.get("base_url") or not c.get("model"):
        return False, "API 地址或模型名未配置"
    return True, ""


# --------------------------------------------------------------------------
# 送给 AI 的材料（**只发文字，不发图**；控制体积以便快速返回）
# --------------------------------------------------------------------------
def build_brief(model) -> str:
    """
    组装给模型的文字材料。只放与这 10 条规则相关的内容：

        • 技术要求全文
        • 材料 / 表面处理 / 热处理相关行
        • 图面单独标注的粗糙度值与公差代号（供 TECH-003 判断"与技术要求冲突"）
    """
    lines: list[str] = []
    tb = getattr(model, "title_block", {}) or {}
    if tb.get("材料"):
        lines.append(f"【材料】{tb['材料']}")
    if tb.get("名称"):
        lines.append(f"【零件名称】{tb['名称']}")

    notes = [str(n).strip() for n in (getattr(model, "tech_notes", []) or []) if str(n).strip()]
    if notes:
        lines.append("【技术要求】")
        lines.extend(f"- {n}" for n in notes[:40])

    # 图面单独标注（只取与语义冲突相关的）
    hits: list[str] = []
    try:
        from sw_qc_checks import _collect_roughness
        for v, raw, _src in _collect_roughness(model)[:10]:
            if v is not None:
                hits.append(f"Ra {v:g}")
    except Exception:
        pass
    if hits:
        lines.append("【图面单独标注的粗糙度】" + "、".join(sorted(set(hits))))

    codes = sorted({str(d.get("display") or d.get("text") or "").strip()
                    for d in (getattr(model, "dimensions", []) or [])
                    if str(d.get("display") or d.get("text") or "").strip()})
    if codes:
        lines.append("【图面尺寸/公差标注（前 40 条）】" + "；".join(codes[:40]))

    # 「按事实行」——给 AI 补上"图上到底写了没有"这层确定性，
    # 否则它只能靠猜：这些东西**图上没写**时，文字里根本找不到痕迹。
    # 例如 GEN-006 要判断"铸件却引用了机加工未注公差"，
    # 模型既要知道是铸件（上面已有），也要知道图上引用了哪套未注公差标准。
    lines.extend(_fact_lines(model))
    return "\n".join(lines)


def _fact_lines(model) -> list[str]:
    """从图上汇总几条确定性事实，供 AI 判断时引用（不是判断，只是事实）。"""
    facts: list[str] = []
    data = getattr(model, "all_text", "") or ""

    # GEN-006：图上引用了哪套未注公差标准。
    # 图上写法很杂，实测至少有三种：「GB/T 1804」「GB1804」「GB/T1804」。
    # 所以统一归一化：去空格/斜杠，并把 GBT 的 T 也去掉，只留 GB+数字来判。
    flat = re.sub(r"[\s/]+", "", data).upper().replace("GBT", "GB")
    tol_refs = [name for name, pat in (("GB/T 1804", "GB1804"),
                                       ("GB/T 1184", "GB1184"),
                                       ("GB/T 1800", "GB1800"))
                if pat in flat]
    if tol_refs:
        facts.append("【图上引用的未注公差/几何标准】" + "、".join(tol_refs))

    # ROUGH-005：图上出现的粗糙度值
    ras = re.findall(r"Ra\s*([0-9]+(?:\.[0-9]+)?)", data, re.I)
    if ras:
        facts.append("【图上出现的 Ra 值】" + "、".join(sorted(set(ras), key=float)))

    # CHAIN-006：图上出现的基准字母（只认"基准X"这种明确写法，避免把视图字母当基准）
    datums = sorted(set(re.findall(r"基准\s*([A-Z])", data)))
    if datums:
        facts.append("【图上出现的基准字母】" + "、".join(datums))

    # DIM-015：有没有对称声明
    sym = [w for w in ("对称", "对称件", "仅标一侧", "只标一侧", "对中") if w in data]
    facts.append("【对称相关表述】" + ("、".join(sym) if sym else "图上未出现"))

    # KEY-007：键槽端部形式有没有说明
    if "键槽" in data or re.search(r"\b[ABC]\s*型", data):
        forms = sorted(set(re.findall(r"([ABC])\s*型", data)))
        facts.append("【键槽端部形式】"
                     + ("、".join(f"{f} 型" for f in forms) if forms else "图上未说明 A/B/C 型"))
    return facts


# --------------------------------------------------------------------------
# 调用
# --------------------------------------------------------------------------
def _post_chat(cfg: dict, brief: str) -> str:
    """调用 OpenAI 兼容的 chat/completions，返回模型回复的文本。"""
    body = json.dumps({
        "model": cfg["model"],
        "temperature": 0,
        "max_tokens": 2000,           # 取消条数上限后需更大输出预算
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": brief},
        ],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(cfg["base_url"] + "/chat/completions",
                                data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", "Bearer " + cfg["api_key"])
    with urllib.request.urlopen(req, timeout=float(cfg["timeout"])) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict | None:
    """从模型回复里取出 JSON 对象（容忍它前后多说了几句）。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _norm(s: str) -> str:
    """归一化，用于「原文是否真的在图上」的比对（去空白与标点差异）。"""
    return re.sub(r"[\s，。、；：,.;:()（）\[\]【】\-—_/\\]+", "", str(s or ""))


def validate_findings(findings: list, brief: str, max_findings: int) -> tuple[list, list]:
    """
    按 AI_POLICY 校验模型输出，返回 (通过的条目, 被丢弃的原因)。

    这是防幻觉的兜底：引用原文在图上找不到的，降级为「轻微」并保留（不丢信息）。

    v0.12+ 口径（核对表第 10 表）：
      · 取消条数上限（不按 max_findings 截断）；
      · 每条必须有**依据 + 风险**，合计 ≤100 字；
      · 冲突类结论要求**两段**引文，只有一个 quote 时按单段处理（不硬拒）。
    """
    ok: list[dict] = []
    dropped: list[str] = []
    hay = _norm(brief)
    for f in findings or []:
        if not isinstance(f, dict):
            dropped.append("条目不是对象")
            continue
        rid = str(f.get("rule_id") or "").strip().upper()
        if rid not in ALLOWED_RULES:
            dropped.append(f"规则ID 不在允许范围：{rid}")
            continue

        # 依据 / 风险：兼容旧字段 conclusion
        basis = str(f.get("basis") or f.get("conclusion") or "").strip()
        risk = str(f.get("risk") or "").strip()
        if not basis and not risk:
            dropped.append(f"{rid} 依据与风险均为空")
            continue
        if not risk:
            dropped.append(f"{rid} 缺「风险」字段（新口径要求写清依据 + 风险）")
            continue
        conclusion = f"{basis} 风险：{risk}" if basis else risk
        if len(conclusion) > MAX_CONCLUSION_LEN:
            conclusion = conclusion[:MAX_CONCLUSION_LEN]

        # 引文：单段或两段（list）都接受，逐段校验都在图上
        raw_quotes = f.get("quotes") or f.get("quote") or ""
        if isinstance(raw_quotes, str):
            quotes = [raw_quotes] if raw_quotes.strip() else []
        else:
            quotes = [str(q).strip() for q in raw_quotes if str(q).strip()]
        quotes = [q[:MAX_QUOTE_LEN] for q in quotes]
        bad = [q for q in quotes if _norm(q) not in hay]
        if bad:
            dropped.append(f"{rid} 引用原文在图上找不到（疑似编造）：{bad[0][:20]}")
            continue

        # 含糊措辞不要（用户规定：不确定就不报）
        if re.search(r"可能|也许|建议关注|疑似需要确认|大概", conclusion) and not quotes:
            dropped.append(f"{rid} 措辞含糊且无原文依据")
            continue

        ok.append({"rule_id": rid, "conclusion": conclusion,
                   "quote": quotes[0] if quotes else "",
                   "quotes": quotes,
                   "suggestion": str(f.get("suggestion") or "").strip()[:60]})
    return ok, dropped


def check_tech_semantics(model, cfg: dict | None = None) -> tuple[list, dict]:
    """
    跑一次 AI 语义检查。返回 (issues, meta)。

    issues 用 `AI-<规则ID>` 作 rule_id（一眼能看出是 AI 判的，且白名单照常适用）。
    meta 记录：是否执行、耗时、丢弃原因 —— 报告里要如实说明。
    """
    from sw_qc_rules import Issue

    c = cfg or load_config()
    meta = {"enabled": False, "available": False, "elapsed_ms": 0,
            "reason": "", "dropped": [], "raw_count": 0, "ai": True}
    can, why = availability(c)
    meta["enabled"] = bool(c.get("enabled"))
    if not can:
        meta["reason"] = why
        return [], meta

    brief = build_brief(model)
    if not brief.strip():
        meta["reason"] = "图上没有可判断的文字（无技术要求、无材料）"
        return [], meta

    t0 = time.time()
    try:
        text = _post_chat(c, brief)
    except urllib.error.URLError as exc:
        meta["reason"] = f"网络失败（{exc.reason if hasattr(exc, 'reason') else exc}）"
        meta["elapsed_ms"] = round((time.time() - t0) * 1000)
        return [], meta
    except (TimeoutError, OSError) as exc:
        # socket 超时也会落到这里；硬超时按用户规定直接放弃
        meta["reason"] = f"超时或被中断（硬超时 {c['timeout']:g} 秒）：{exc}"
        meta["elapsed_ms"] = round((time.time() - t0) * 1000)
        return [], meta
    except Exception as exc:
        meta["reason"] = f"调用失败：{exc}"
        meta["elapsed_ms"] = round((time.time() - t0) * 1000)
        return [], meta
    meta["elapsed_ms"] = round((time.time() - t0) * 1000)

    data = _extract_json(text)
    if not isinstance(data, dict):
        meta["reason"] = "模型回复不是合法 JSON，整条丢弃"
        return [], meta

    raw = data.get("findings") or []
    meta["raw_count"] = len(raw)
    findings, dropped = validate_findings(raw, brief, 0)   # 0 = 不设条数上限
    meta["dropped"] = dropped

    issues = []
    for f in findings:
        title = ALLOWED_RULES[f["rule_id"]]
        detail = f["conclusion"]
        if f.get("quote"):
            detail += f"（图上原文：『{f['quote']}』）"
        issues.append(Issue(
            rule_id="AI-" + f["rule_id"],
            category="技术要求",
            severity=SEVERITY_BY_RULE.get(f["rule_id"], "major"),
            title=f"{title}（AI 判断）",
            detail=detail,
            suggestion=f.get("suggestion") or "请人工核对技术要求与材料/表面处理的一致性",
            standard="—",
            evidence=f.get("quote", ""),
        ))
    return issues, meta


__all__ = ["load_config", "availability", "check_tech_semantics", "build_brief",
           "validate_findings", "ALLOWED_RULES", "ALLOWED_RULES", "DEFAULT_TIMEOUT",
           "MAX_CONCLUSION_LEN", "DEFAULT_MAX_FINDINGS", "CONFIG_FILENAME",
           "config_path"]

# ==========================================================================
# v0.12+ 覆盖：按核对表新的 16 条 AI 规则重建清单与提示词
# ==========================================================================
# 规则 ID 从旧的 TECH-xxx 换成了 A/B/C/D 系列（见核对表第 10 表），
# 这里从 spec_20261018.AI_RULE_ROWS 读取，保证代码与核对表**同一份来源**。
# 严重级别：核对表用 [P1]/[P2] 标优先级，按 P2→minor、其余→major 落级别
# （核对表未单独给严重级别，这是当前口径；若要改，改这里即可）。
try:
    from spec_20261018 import AI_RULE_ROWS as _AI_ROWS

    _NEW_ALLOWED = {}
    _NEW_SEV = {}
    for _rid, _item, _desc in _AI_ROWS:
        _rid = str(_rid).strip()
        _title = str(_item).split("[")[0].strip()
        _NEW_ALLOWED[_rid] = _title
        _NEW_SEV[_rid] = "minor" if "[P2]" in str(_item) else "major"

    ALLOWED_RULES.clear()
    ALLOWED_RULES.update(_NEW_ALLOWED)
    SEVERITY_BY_RULE.clear()
    SEVERITY_BY_RULE.update(_NEW_SEV)

    _RULE_LINES = "\n".join(f"{rid} {title}" for rid, title in _NEW_ALLOWED.items())
    SYSTEM_PROMPT = f"""你是机械工程图审核助手。只判断「技术要求 / 材料 / 工艺」之间的语义问题。

严格遵守：
1. 只从下面给定的图上文字与事实里判断，不得引入图上没有的信息。
2. 只报你自己有把握的矛盾或缺失；没把握就少报或不报，不要用"可能""建议关注"凑数。
3. **不设条数上限**，但每一条必须写清楚**依据 + 风险**，不得用一句话泛泛带过；
   每条回复（依据+风险）合计不超过 100 字。
4. 依据必须引用图上**原话片段**（原样复制，不得改写），否则该条会被程序丢弃；
   冲突类结论要给**两段**引文（A 说法 vs B 说法）。
5. 只指出问题，不给设计建议（不要说"建议改用 XX 材料"）。
6. 没有把握就返回空列表。

只允许这些规则 ID：
{_RULE_LINES}

只输出 JSON，不要任何其它文字：
{{"findings":[{{"rule_id":"A03","basis":"...","risk":"...","quote":"..."}}]}}"""
except Exception:                                          # 缺数据则沿用旧清单
    pass
