# -*- coding: utf-8 -*-
"""
PDF 视觉审核 v0.2（2026-10-25）

v0.1 问题：让 AI 自由发挥 → 多轮结论一致率 0~50%，不可用于审图。
v0.2 改法：
  ① **结构化输入**：先确定性抽取（标题栏/技术要求条款/尺寸/粗糙度），AI 看到的是结构化事实，
     不是一团文字 → 减少猜测。
  ② **固定检查清单**：内置 14 项检查（源自规则库），AI 只能给 ok/warn/bad/na + 依据，
     输出条数、类别、严重度都由本地映射决定 → 多轮结果稳定、可对比。
  ③ 依据（evidence）必须引用图上原文；缺失就判 na，不许编。
  ④ 视觉模型可用时同时给图像；纯文字模型自动降级。

调用（插件或命令行）：
    python pdf_vision_audit.py <图纸.pdf> [--out 目录] [--text-only] [--keep-images]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

ENGINE_VERSION = "PDF-vision v0.2"

# ===========================================================================
# 固定检查清单（源自规则库；AI 只能判 ok/warn/bad/na）
#   sev: bad 时的严重度；warn_sev: warn 时的严重度
# ===========================================================================
CHECKS = [
    dict(id="C01", cat="技术要求", name="一般公差声明", mode="local",
         ask="技术要求是否声明了未注线性/角度尺寸公差（GB/T 1804 或等效）？写了=ok；没写=bad。",
         sev="major", warn_sev="minor"),
    dict(id="C02", cat="技术要求", name="几何公差声明", mode="local",
         ask="技术要求是否声明了未注几何公差（GB/T 1184 或等效）？写了=ok；没写=warn（可选）。",
         sev="major", warn_sev="minor"),
    dict(id="C03", cat="技术要求", name="倒角/圆角规定", mode="local",
         ask="技术要求是否规定未注倒角/圆角？规定了=ok；没规定=warn。",
         sev="minor", warn_sev="minor"),
    dict(id="C04", cat="技术要求", name="去毛刺要求", mode="local",
         ask="技术要求是否有去毛刺/去飞边/锐角倒钝要求？有=ok；无=warn。",
         sev="minor", warn_sev="minor"),
    dict(id="C05", cat="技术要求", name="技术要求内部矛盾", mode="ai",
         ask="多条技术要求之间、或技术要求与标题栏/尺寸标注之间是否**明显矛盾**（螺母规格不一致、"
             "材料与表面处理冲突、公差等级冲突）？有=bad（必须指出哪两条矛盾）；无=ok。",
         sev="major", warn_sev="minor"),
    dict(id="C06", cat="技术要求", name="表面处理与材料匹配", mode="ai",
         ask="若图纸写了表面处理：工艺与材料是否明显不兼容（钢件阳极氧化、不锈钢镀锌、塑料件电镀等）？"
             "不兼容=bad；兼容或未写=ok。写了工艺名称就算合格，不要要求镀层厚度/盐雾小时数。",
         sev="major", warn_sev="minor"),
    dict(id="C07", cat="尺寸", name="孔位定位完整性", mode="ai",
         ask="孔（含螺纹孔/沉孔）的定位尺寸是否齐全：孔距 + 孔到边（或明确对称基准）？"
             "缺一项=warn；完全没有定位=bad；无孔=na。",
         sev="major", warn_sev="major"),
    dict(id="C08", cat="尺寸", name="沉孔/沉头深度", mode="ai",
         ask="图中若有沉孔/沉头/锪平（如 Ø6.3×90°、⌴Ø7.5），是否给出了深度或可唯一确定的完整规格？"
             "缺深度且不可推算=bad；完整=ok；没有此类特征=na。",
         sev="major", warn_sev="minor"),
    dict(id="C09", cat="尺寸", name="尺寸链闭合", mode="ai",
         ask="同一方向的一组尺寸是否**明显**不闭合或矛盾（总和超出总长、同一特征两个不同值）？"
             "明显不闭合=bad（给出两个具体数字作证）；看不出来=na。不要凭感觉报。",
         sev="major", warn_sev="minor"),
    dict(id="C10", cat="尺寸", name="标注精度与公差等级", mode="local",
         ask="尺寸标注的小数位数是否明显细于未注公差等级（如 m 级该段 ±0.4，却标 147.4 这种 0.1 精度）？"
             "明显不匹配=warn；一致=ok。",
         sev="minor", warn_sev="minor"),
    dict(id="C11", cat="尺寸", name="槽/阵列数量标注", mode="local",
         ask="图上重复特征（槽/齿/孔阵列）是否标注了数量（如 12×）？只标间距未标数量=warn；已标或无重复特征=ok。",
         sev="minor", warn_sev="minor"),
    dict(id="C12", cat="图纸信息", name="标题栏完整", mode="local",
         ask="标题栏是否有 图号、名称、材料、版本、比例？缺任一项=warn；齐全=ok。"
             "（材料栏括号里带颜色如 POM（黑色）是合规写法，不要报。）",
         sev="minor", warn_sev="minor"),
    dict(id="C13", cat="图纸信息", name="投影法与张数", mode="local",
         ask="是否标注投影法（第一角/第三角）与张数（第X张 共X张）？都有=ok；缺=warn。",
         sev="minor", warn_sev="minor"),
    dict(id="C14", cat="图纸信息", name="签字/日期", mode="local",
         ask="设计/审核/批准栏是否有签名与日期？全空=warn（若为设计稿可忽略，轻报）；有=ok。",
         sev="minor", warn_sev="minor"),
]

# 只有语义判断交给 AI（规则能判的本地判死，保证多轮稳定）
AI_CHECK_IDS = [c["id"] for c in CHECKS if c.get("mode") == "ai"]

# 证据闸门（2026-10-25）：AI 报 warn/bad 时，evidence 必须含对应关键词/数字，
# 否则视为"没拿出依据"→ 丢弃（宁漏不误，也让多轮更稳）
EVIDENCE_GATE = {
    "C05": re.compile(r"\d|M\d|GB|要求|条"),
    "C06": re.compile(r"电镀|镀|氧化|阳极|钝化|喷涂|喷粉|发黑|磷化|喷砂|拉丝|彩锌|处理|"
                      r"SUS|SPCC|SECC|ABS|POM|PC|PP|PA|45|6061|304|316|Q235"),
    "C07": re.compile(r"孔|Ø|⌀|φ|沉头|沉孔|M\d|螺纹"),
    "C08": re.compile(r"沉头|沉孔|锪|Ø|⌀|φ|深度"),
    "C09": re.compile(r"\d+\s*[+\-]?\s*\d*"),
}

# 说明闸门：C07/C08 必须明确指出"缺什么"，否则视为猜测 → 丢弃
NOTE_GATE = {
    "C07": re.compile(r"缺|未标|未注|没有|无定位|不全|无法确定|不完整"),
    "C08": re.compile(r"缺|未标|未注|没有|无法|不可|不完整"),
}

# GB/T 1804-m 一般公差（线性尺寸）单位 mm：段上限 → ±公差
_GB1804_M = [(0.5, 0.05), (3, 0.1), (6, 0.1), (30, 0.2), (120, 0.3),
             (400, 0.5), (1000, 0.8), (2000, 1.2), (4000, 2.0)]

SYSTEM_PROMPT = """你是一名资深机械制图审核工程师。图纸中**规则能判的项目已由本地规则判完**，
你只负责 **5 项语义判断**：C05 技术要求内部矛盾、C06 表面处理与材料匹配、C07 孔位定位完整性、
C08 沉孔/沉头深度、C09 尺寸链闭合。

【输出格式（严格 JSON，不要多余文字）】
{
  "summary": "一句话总体结论",
  "drawing": {"图号":"","名称":"","材料":"","版本":"","比例":"","投影法":""},
  "results": {
    "C05": {"result":"ok|warn|bad|na", "evidence":"图上原文/数值", "note":"一句话说明"},
    "C06": {...}, "C07": {...}, "C08": {...}, "C09": {...}
  }
}

【判定纪律】
· result 只能是 ok / warn / bad / na。
· evidence 必须是图上**真实存在**的文字或数值；找不到依据就判 na，绝不许编造。
· na 用于"该图没有这类特征"或"信息不足"。
· 不要评论排版、字体、错别字、设计意图；不要要求补镀层厚度/盐雾指标/材料状态。
· **材料是塑料（POM/ABS/PC 等）而技术要求引用 GB/T 1804 或 1184 时，表示该件按机加工艺制造，
  不要因此报任何问题，也不要要求脱模斜度、成型收缩、模具纹理或 GB/T 47067。**
· 已注公差比一般公差紧不算错。
· C09 只报**明显**矛盾（给出两个具体数字作证），看不出就 na——宁可 na 不要猜。
"""


# ===========================================================================
# 结构化抽取（确定性，不依赖 AI）
# ===========================================================================
def extract_structure(texts):
    blob = "\n".join(texts)
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]

    drawing = {}
    m = re.search(r"\b([A-Z]{2,4}-\d{2,4}-\d{2,4}(?:-\d{2})?)\b", blob)
    if m:
        drawing["图号"] = m.group(1)

    # 材料：材料栏里常常只写牌号、没有"材料"二字 → 用牌号白名单兜底。
    # 注意：正文尺寸里也可能出现 "45" 之类数字，所以**取最后一处命中**（标题栏在文字流末尾）。
    MATERIAL_RE = re.compile(
        r"(SUS\s?\d{3}|SPCC|SECC|SGCC|SPHC|Q235[A-Z]?|Q345[A-Z]?|45#|45钢|40Cr|20CrMnTi"
        r"|6061(?:-T\d)?|7075(?:-T\d)?|5052|AL\s?\d{4}|H62|H65|TU1"
        r"|POM(?:（[^）]{1,8}）|\([^)]{1,8}\))?|ABS(?:（[^）]{1,8}）)?|PC(?:（[^）]{1,8}）)?"
        r"|PA\s?\d{1,2}(?:[A-Z]?)|PBT|PPS|PET|PEI|PP(?:（[^）]{1,8}）)?|PMMA|亚克力|尼龙|铁氟龙|PTFE"
        r"|不锈钢|冷轧板|热轧板|镀锌板|铝合金|黄铜|青铜|紫铜|钛合金|弹簧钢|轴承钢|工具钢"
        r"|SKD\s?\d{1,2}|H13|Cr12MoV|304|316L|316|201)", re.I)
    m = re.search(r"(?:材料|材质)\s*[:：]?\s*([^\s\n，,]{1,20})", blob)
    if m:
        drawing["材料"] = m.group(1)
    else:
        # 取所有命中后**打分选最优**：带字母（6061-T6/SUS304）优先于纯数字（201/304），
        # 且靠近图号（标题栏）的优先 —— 避免把正文里的 "201" 当材料。
        hits = [(mm.start(), mm.group(1)) for mm in MATERIAL_RE.finditer(blob)]
        if hits:
            id_pos = blob.find(drawing.get("图号") or "")

            def _score(h):
                pos, val = h
                s = 0.0
                if not re.fullmatch(r"\d{2,4}", val):
                    s += 10.0
                if re.search(r"[A-Za-z]", val):
                    s += 3.0
                if id_pos >= 0:
                    s -= abs(pos - id_pos) / 2000.0
                return s

            drawing["材料"] = max(hits, key=_score)[1]

    # 版本/比例/投影法：标题栏里标签与数值常分两行/多行，按 token 邻近找
    if "版本" in lines:
        vi = max(i for i, ln in enumerate(lines) if ln == "版本")
        for ln in lines[vi + 1: vi + 9]:
            if re.fullmatch(r"[A-Z]", ln):
                drawing["版本"] = ln
                break
    if not drawing.get("版本"):
        # 兜底：在比例值（1:x）后面紧跟的单个大写字母，通常就是版本
        m = re.search(r"1\s*[:：]\s*\d+(?:\.\d+)?\s*(?:\n\s*)([A-Z])(?:\s*\n|$)", blob)
        if m:
            drawing["版本"] = m.group(1)
    m = re.search(r"比\s*例\s*[\s\S]{0,10}?(\d+\s*[:：]\s*\d+(?:\.\d+)?)", blob)
    if m:
        drawing["比例"] = m.group(1).replace(" ", "")

    # 名称：标题栏里紧跟图号的那段中文（排除字段名/材料牌号），比"最后一个中文串"可靠
    LABELS = {"技术要求", "审核", "设计", "批准", "标记", "处数", "分区", "更改文件号",
              "签名", "年月日", "年 月 日", "版本", "比例", "投影法", "材料", "日期",
              "第张", "共张", "对称加工"}
    id_idx = [i for i, ln in enumerate(lines) if ln == drawing.get("图号")]
    if id_idx:
        last = id_idx[-1]
        win = lines[max(0, last - 8): last + 10]
        cands = [ln for ln in win
                 if re.fullmatch(r"[\u4e00-\u9fa5]{2,12}", ln) and ln not in LABELS]
        if cands:
            drawing["名称"] = max(cands, key=len)
    if not drawing.get("名称"):
        cand = [ln for ln in lines
                if re.fullmatch(r"[\u4e00-\u9fa5]{2,12}", ln) and ln not in LABELS]
        if cand:
            drawing["名称"] = max(cand, key=len)
    # 张数：图上"第  张 共  张"数字常被拆到别的文字块 → 只认"第…张/共…张"结构
    m = re.search(r"共\s*\d*\s*张[\s\S]{0,20}?第\s*\d*\s*张", blob)
    if not m:
        m = re.search(r"第\s*\d*\s*张[\s\S]{0,20}?共\s*\d*\s*张", blob)
    if m:
        drawing["张数"] = "有"
    # 投影法：图上写作"投 影 法"
    if re.search(r"投\s*影\s*法|第一角|第三角", blob):
        drawing["投影法"] = drawing.get("投影法") or "有"

    notes = []
    ti = None
    for i, ln in enumerate(lines):
        if "技术要求" in ln:
            ti = i
            break
    if ti is not None:
        for ln in lines[ti + 1:]:
            if re.match(r"^[A-Z0-9]{2,}-", ln):
                continue
            notes.append(ln)
            if len(notes) > 30:
                break
    notes = [n for n in notes if len(n) > 3]

    rough = sorted(set(re.findall(r"Ra\s*([0-9.]+)", blob)
                       + re.findall(r"(?<![\d.])([0-9]\.[0-9])\s*(?:全部|所有|其余)", blob)))

    dims = []
    tol_pat = re.compile(r"(±\s*\d+(?:\.\d+)?|[+＋]\s*\d+(?:\.\d+)?\s*[/／]\s*[-−]?\s*\d+(?:\.\d+)?"
                         r"|\d+(?:\.\d+)?\s*[/／]\s*[-−]\s*\d+(?:\.\d+)?)")
    for ln in lines:
        if any(k in ln for k in ("技术要求", "GB/T")):
            continue
        nums = re.findall(r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)(?![\d.])", ln)
        tols = tol_pat.findall(ln)
        for v in nums:
            try:
                fv = float(v)
            except ValueError:
                continue
            if not (0.1 <= fv <= 10000):
                continue
            dims.append({"text": v, "value_mm": fv,
                         "tol": tols[0] if tols else "", "source": ln[:60]})
    seen, uniq = set(), []
    for d in dims:
        k = (d["text"], d["tol"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(d)

    return {"drawing": drawing, "notes": notes, "roughness": rough,
            "dimensions": uniq[:80], "raw_text": blob}


def build_user_content(struct, imgs, text_only):
    facts = {
        "标题栏": struct["drawing"],
        "技术要求": struct["notes"][:20],
        "粗糙度": struct["roughness"][:10],
        "尺寸标注(数值,公差)": [{"值": d["text"], "公差": d["tol"]} for d in struct["dimensions"][:60]],
    }
    txt = ("【结构化事实（由 PDF 矢量文字确定性抽取，可作为依据）】\n"
           + json.dumps(facts, ensure_ascii=False, indent=1)
           + "\n\n【图纸原文（供你核对依据）】\n" + struct["raw_text"][:12000]
           + "\n\n你只需对 C05~C09 这 5 项做语义判断（其余项已由本地规则判完），只输出 JSON。")
    content = [{"type": "text", "text": txt}]
    if not text_only:
        for p in imgs[:8]:
            content.append({"type": "text", "text": "【图纸图像】" + os.path.basename(p)})
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _b64(p),
                                          "detail": "high"}})
    return content


# ===========================================================================
# 图像准备
# ===========================================================================
def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _bbox(blocks):
    return (min(b[0] for b in blocks), min(b[1] for b in blocks),
            max(b[2] for b in blocks), max(b[3] for b in blocks))


def prepare_page(page, workdir, page_no):
    W, H = page.rect.width, page.rect.height
    text = page.get_text("text")
    imgs = []

    p = os.path.join(workdir, f"p{page_no}_full.png")
    page.get_pixmap(dpi=200).save(p)
    imgs.append(p)

    blocks = [b for b in page.get_text("blocks") if (b[4] or "").strip()]
    tech = [b for b in blocks if "技术要求" in (b[4] or "")]
    if tech:
        x0, y0, x1, y1 = _bbox(tech)
        clip = pymupdf.Rect(max(0, x0 - 10), max(0, y0 - 10),
                            min(W, x1 + W * 0.12), min(H, y1 + H * 0.25))
        p = os.path.join(workdir, f"p{page_no}_tech.png")
        page.get_pixmap(dpi=400, clip=clip).save(p)
        imgs.append(p)

    KEYW = ("图号", "材料", "版本", "比例", "投影法", "共", "第", "审核", "设计", "批准")
    title = [b for b in blocks if sum(1 for k in KEYW if k in (b[4] or "")) >= 2]
    if title:
        x0, y0, x1, y1 = _bbox(title)
        clip = pymupdf.Rect(max(0, x0 - 20), max(0, y0 - 20),
                            min(W, x1 + 40), min(H, y1 + 40))
        p = os.path.join(workdir, f"p{page_no}_title.png")
        page.get_pixmap(dpi=400, clip=clip).save(p)
        imgs.append(p)

    clip = pymupdf.Rect(W * 0.02, H * 0.02, W * 0.75, H * 0.72)
    p = os.path.join(workdir, f"p{page_no}_views.png")
    page.get_pixmap(dpi=300, clip=clip).save(p)
    imgs.append(p)
    return text, imgs


# ===========================================================================
# AI 调用
# ===========================================================================
def _endpoint():
    provider = (os.environ.get("QC_AI_PROVIDER", "") or "").strip().lower()
    base = (os.environ.get("QC_AI_BASE", "") or "").strip()
    if not base:
        base = {"deepseek": "https://api.deepseek.com/v1",
                "chatgpt": "https://api.openai.com/v1",
                "openai": "https://api.openai.com/v1",
                "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "zhipu": "https://open.bigmodel.cn/api/paas/v4",
                "moonshot": "https://api.moonshot.cn/v1",
                }.get(provider, "https://api.openai.com/v1")
    model = (os.environ.get("QC_AI_MODEL", "") or "").strip()
    if not model:
        model = {"deepseek": "deepseek-chat", "qwen": "qwen-vl-max",
                 "zhipu": "glm-4v-plus"}.get(provider, "gpt-4.1")
    return base.rstrip("/"), model


def _no_vision(base, model):
    b = (base or "").lower()
    return ("deepseek" in b) or ("text-" in (model or "").lower()) or ("moonshot" in b)


def call_ai(struct, imgs, timeout=180, force_text_only=False):
    key = (os.environ.get("QC_AI_KEY", "") or "").strip()
    if not key:
        return None, "未配置 AI Key"
    base, model = _endpoint()
    text_only = force_text_only or _no_vision(base, model)
    content = build_user_content(struct, imgs, text_only)

    payload = {"model": model, "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": content}]}
    try:
        payload["response_format"] = {"type": "json_object"}
    except Exception:
        pass

    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        txt = (body["choices"][0]["message"]["content"] or "").strip()
        txt = re.sub(r"^```[a-zA-Z]*\n?", "", txt)
        txt = re.sub(r"\n?```$", "", txt).strip()
        return json.loads(txt), ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        hint = {401: "（Key 无效）", 403: "（无权限/地区限制）",
                404: "（模型名或地址错）", 429: "（额度/频率）"}.get(e.code, "")
        return None, f"HTTP {e.code} {e.reason}{hint} {detail}"
    except urllib.error.URLError as e:
        return None, f"网络不可达（{e.reason}）；OpenAI 需代理"
    except Exception as e:
        return None, f"调用失败：{e!r}"


def call_ai_vote(struct, imgs, rounds=2, timeout=180, force_text_only=False):
    """
    多轮投票（2026-10-25）：AI 语义判断有随机性，**只要两轮不一致就不报**（宁漏不误）。
    返回 (merged_ai_obj, errs, detail)
    """
    rounds = max(1, min(3, int(rounds or 1)))
    objs, errs = [], []
    for _ in range(rounds):
        o, e = call_ai(struct, imgs, timeout=timeout, force_text_only=force_text_only)
        if o:
            objs.append(o)
        else:
            errs.append(e)
    if not objs:
        return None, errs, {"rounds": rounds, "ok": 0, "agreed": [], "dropped": []}

    base = objs[0]
    merged_results, agreed, dropped = {}, [], []
    ids = set()
    for o in objs:
        ids |= set((o.get("results") or {}).keys())
    for cid in sorted(ids):
        votes = []
        for o in objs:
            r = (o.get("results") or {}).get(cid) or {}
            votes.append((str(r.get("result") or "").lower(), str(r.get("evidence") or "")))
        results = set(v[0] for v in votes)
        if len(results) == 1:                     # 全部一致 → 采纳
            r0 = (objs[0].get("results") or {}).get(cid) or {}
            merged_results[cid] = r0
            if votes[0][0] in ("warn", "bad"):
                agreed.append(cid)
        else:                                      # 有分歧 → 丢弃（宁漏不误）
            dropped.append(cid + ":" + "/".join(v[0] for v in votes))
    merged = {"summary": base.get("summary") or "", "drawing": base.get("drawing") or {},
              "results": merged_results}
    return merged, errs, {"rounds": rounds, "ok": len(objs),
                          "agreed": agreed, "dropped": dropped}


# ===========================================================================
# 结果映射（确定性：AI 只判 ok/warn/bad/na）
# ===========================================================================
PLASTIC_KW = ("pom", "abs", "pc", "pp", "pa", "塑胶", "塑料", "尼龙", "亚克力")
SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}


def _is_plastic(struct):
    blob = (json.dumps(struct["drawing"], ensure_ascii=False) + " " + struct["raw_text"]).lower()
    return any(k in blob for k in PLASTIC_KW)


def _has_metal_std(struct):
    return any(k in struct["raw_text"] for k in ("1804", "1184"))


# ---------------------------------------------------------------------------
# 本地确定性检查（规则判死，多轮 100% 一致；不依赖 AI）
# ---------------------------------------------------------------------------
def _notes_blob(struct):
    return "\n".join(struct.get("notes") or [])


def local_checks(struct):
    """返回 {check_id: {"result","evidence","note"}}，只含 mode=local 的项。"""
    blob = struct["raw_text"]
    notes = _notes_blob(struct)
    dr = struct["drawing"]
    out = {}

    # C01 一般公差声明
    if re.search(r"GB/?T?\s*1804|ISO\s*2768", notes or blob):
        out["C01"] = {"result": "ok", "evidence": "技术要求含 GB/T 1804",
                      "note": "已声明一般公差"}
    else:
        out["C01"] = {"result": "bad", "evidence": "技术要求未见 GB/T 1804",
                      "note": "未声明未注线性/角度公差依据"}

    # C02 几何公差声明
    if re.search(r"GB/?T?\s*1184|ISO\s*1101|未注形状和位置公差", notes or blob):
        out["C02"] = {"result": "ok", "evidence": "技术要求含 GB/T 1184",
                      "note": "已声明未注几何公差"}
    else:
        out["C02"] = {"result": "warn", "evidence": "技术要求未见 GB/T 1184",
                      "note": "未声明未注几何公差依据"}

    # C03 倒角/圆角
    if re.search(r"倒角|圆角|C\s*0\.\d|R\s*\d", notes or blob):
        out["C03"] = {"result": "ok", "evidence": "技术要求含倒角/圆角规定",
                      "note": "已规定未注倒角/圆角"}
    else:
        out["C03"] = {"result": "warn", "evidence": "未见倒角/圆角规定",
                      "note": "未规定未注倒角/圆角"}

    # C04 去毛刺
    if re.search(r"去毛刺|去飞边|飞边|锐角倒钝|倒钝|光整", notes or blob):
        out["C04"] = {"result": "ok", "evidence": "技术要求含去毛刺要求",
                      "note": "已要求去毛刺"}
    else:
        out["C04"] = {"result": "warn", "evidence": "未见去毛刺要求",
                      "note": "未要求去毛刺/飞边"}

    # C10 标注精度 vs m 级公差
    def m_tol(v):
        for lim, tv in _GB1804_M:
            if v <= lim:
                return tv
        return None
    bad_prec = []
    for d in struct["dimensions"]:
        txt = d["text"]
        if "." not in txt:
            continue
        dec = len(txt.split(".")[1])
        if dec < 2:
            continue
        tv = m_tol(float(txt))
        if tv is None:
            continue
        if 10 ** (-dec) < tv / 2:
            bad_prec.append((txt, tv))
    if bad_prec:
        ex = bad_prec[0]
        out["C10"] = {"result": "warn",
                      "evidence": f"{ex[0]}（标到 {len(ex[0].split('.')[1])} 位小数，"
                                  f"m 级该段 ±{ex[1]}）",
                      "note": f"有 {len(bad_prec)} 个尺寸标注精度细于未注公差"}
    else:
        out["C10"] = {"result": "ok", "evidence": "标注精度与 m 级相容", "note": ""}

    # C11 重复特征数量
    if re.search(r"\d+\s*[×xX]\s*(?:Ø|⌀|孔|M|槽|R)?", blob):
        out["C11"] = {"result": "ok", "evidence": "图中含 数量× 写法", "note": ""}
    else:
        cnt = {}
        for d in struct["dimensions"]:
            cnt[d["text"]] = cnt.get(d["text"], 0) + 1
        rep = [(k, v) for k, v in cnt.items() if v >= 4]
        if rep:
            out["C11"] = {"result": "warn",
                          "evidence": f"{rep[0][0]} 出现 {rep[0][1]} 次",
                          "note": "重复特征未见 数量× 标注"}
        else:
            out["C11"] = {"result": "na", "evidence": "", "note": ""}

    # C12 标题栏完整
    need = ["图号", "名称", "材料", "版本", "比例"]
    missing = [k for k in need if not dr.get(k)]
    if missing:
        out["C12"] = {"result": "warn", "evidence": "缺：" + "、".join(missing),
                      "note": "标题栏字段不全"}
    else:
        out["C12"] = {"result": "ok", "evidence": "图号/名称/材料/版本/比例齐全", "note": ""}

    # C13 投影法 + 张数（图上"投 影 法""第  张 共  张"数字常被拆块，按结构判断）
    has_proj = bool(re.search(r"投\s*影\s*法|第一角|第三角", blob))
    has_sheet = bool(re.search(r"第\s*\d*\s*张", blob) and re.search(r"共\s*\d*\s*张", blob))
    if has_proj and has_sheet:
        out["C13"] = {"result": "ok", "evidence": "含投影法与张数", "note": ""}
    else:
        out["C13"] = {"result": "warn",
                      "evidence": "缺" + ("投影法" if not has_proj else "") +
                                  ("张数" if not has_sheet else ""),
                      "note": "图框信息不完整"}

    # C14 签字/日期：图纸文字里通常读不到手写签名 → 一律 na，避免误报
    out["C14"] = {"result": "na", "evidence": "", "note": ""}
    return out


def map_results(ai_obj, struct, image_mode=False):
    """合并：本地确定性结果 + AI 语义结果 → 报告条目（确定性映射）"""
    checks = []
    # 图像模式（无矢量文字）：本地规则没有输入，只能全交给 AI
    results = {} if image_mode else dict(local_checks(struct))
    ai_results = (ai_obj or {}).get("results") or {}
    for cid in AI_CHECK_IDS:
        r = ai_results.get(cid)
        if isinstance(r, dict):
            results[cid] = r
    plastic_machined = _is_plastic(struct) and _has_metal_std(struct)

    for c in CHECKS:
        r = results.get(c["id"]) or {}
        if not isinstance(r, dict):
            continue
        res = str(r.get("result") or "").lower()
        ev = str(r.get("evidence") or "").strip()
        note = str(r.get("note") or "").strip()
        if res not in ("warn", "bad"):
            continue
        if not ev:
            continue
        # 证据闸门：语义类检查必须拿出对应关键词/数字
        gate = EVIDENCE_GATE.get(c["id"])
        if gate is not None and not gate.search(ev):
            continue
        ngate = NOTE_GATE.get(c["id"])
        if ngate is not None and not ngate.search(note + ev):
            continue
        if plastic_machined and ("脱模" in note or "收缩" in note or "47067" in note):
            continue
        sev = c["sev"] if res == "bad" else c["warn_sev"]
        checks.append({
            "rule_id": "AI-" + c["id"], "category": c["cat"], "severity": sev,
            "title": c["name"] + ("（缺失）" if res == "bad" else "（需确认）"),
            "detail": (("依据：「" + ev[:70] + "」　" + note)[:600] if note
                       else "依据：「" + ev[:70] + "」"),
            "suggestion": c["ask"][:200], "standard": "AI 图像审核",
            "location": "", "evidence": ev[:200],
        })
    checks.sort(key=lambda x: SEV_ORDER.get(x["severity"], 9))
    return checks, results


def to_report(pdf_path, struct, ai_obj, err, elapsed, imgs_n, vote_detail=None, image_mode=False):
    checks, results = map_results(ai_obj, struct, image_mode=image_mode)
    summary = {"critical": 0, "major": 0, "minor": 0}
    for c in checks:
        summary[c["severity"]] = summary.get(c["severity"], 0) + 1
    drawing = (ai_obj or {}).get("drawing") or {}
    props = {k: v for k, v in drawing.items() if v}
    for k, v in struct["drawing"].items():
        props.setdefault(k, v)
    passed = [c["name"] + "：已符合"
              for c in CHECKS
              if str((results.get(c["id"]) or {}).get("result", "")).lower() == "ok"]
    return {
        "source": "pdf-vision", "engine_version": ENGINE_VERSION,
        "file": os.path.basename(pdf_path),
        "counts": {"views": 0, "notes": len(struct["notes"]),
                   "dimensions": len(struct["dimensions"]),
                   "roughness": len(struct["roughness"]), "gtol": 0},
        "props": props, "header": props, "title_block": props,
        "notes": struct["notes"], "dimensions": struct["dimensions"],
        "roughness": struct["roughness"], "gtols": [],
        "checks": checks, "passed": passed, "summary": summary,
        "dim_analysis": [], "gtol_analysis": [],
        "warnings": (([] if not image_mode else
                      ["图像模式：该图纸没有矢量文字层，全部结论来自 AI 视觉，请人工复核"])
                     + (["AI 调用失败：" + err] if err else [])),
        "ai": {"enabled": bool(ai_obj), "configured": bool(os.environ.get("QC_AI_KEY")),
               "error": err or "", "skipped": False, "count": len(checks),
               "elapsed_sec": elapsed, "images": imgs_n,
               "summary": str((ai_obj or {}).get("summary") or "")[:400],
               "results": results,
               "vote": vote_detail or {}},
        "read_failed": False, "rule_preset": "pdf_vision",
    }


def _prep_image(path, workdir, page_no=1):
    """位图输入（SaveBMP 兜底时是 BMP）→ 转 PNG 并限制尺寸，供视觉模型读取。"""
    from PIL import Image
    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    if w < 1400:                      # 太小则放大，保证小字可读
        r = 1400.0 / max(1, w)
        im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
    elif w > 3200:                    # 太大则缩到 3200（控制 token）
        r = 3200.0 / w
        im = im.resize((3200, int(h * r)), Image.LANCZOS)
    out = os.path.join(workdir, "p%d_full.png" % page_no)
    im.save(out)
    return out


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF 视觉审核 v0.2")
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None)
    ap.add_argument("--text-only", action="store_true")
    ap.add_argument("--keep-images", action="store_true")
    ap.add_argument("--vote", type=int, default=2,
                    help="AI 语义判断投票轮数（默认2：两轮不一致的问题不报，宁漏不误）")
    args = ap.parse_args(argv)

    pdf = args.pdf
    if not os.path.exists(pdf):
        print(json.dumps({"error": "PDF 不存在：" + pdf}, ensure_ascii=False))
        return 2
    outdir = args.out or os.path.join(os.path.dirname(pdf), "pdf_audit_out")
    os.makedirs(outdir, exist_ok=True)
    work = os.path.join(outdir, "_tmp_imgs")
    os.makedirs(work, exist_ok=True)

    doc = None
    ext = os.path.splitext(pdf)[1].lower()
    image_mode = ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
    if image_mode:
        # SaveBMP 兜底路径：没有矢量文字，全部交给视觉 AI
        imgs = [_prep_image(pdf, work, 1)]
        texts = []
        struct = {"drawing": {}, "notes": [], "roughness": [], "dimensions": [], "raw_text": ""}
        print(f"[IMG] 位图输入 {os.path.basename(pdf)} 图={len(imgs)}（图像模式：无文字层）")
    else:
        doc = pymupdf.open(pdf)
        texts, imgs = [], []
        for i, page in enumerate(doc):
            t, im = prepare_page(page, work, i + 1)
            texts.append(t)
            imgs.extend(im)
        doc.close()
        struct = extract_structure(texts)
        print(f"[PDF] 页={len(texts)} 图={len(imgs)} 文字={sum(len(t) for t in texts)} "
              f"尺寸={len(struct['dimensions'])} 技术要求条款={len(struct['notes'])}")

    t0 = time.time()
    base_url, model_name = _endpoint()
    if image_mode and _no_vision(base_url, model_name):
        # 图像模式 + 纯文字模型 = 模型什么也看不到 → 明确报错，别给出空结论
        ai_obj, errs, vote_detail = None, ["当前 AI 模型不支持图像（" + model_name +
                                          "）。图像模式必须用视觉模型（如 gpt-4.1 / gpt-4o / qwen-vl-max）"], \
                                     {"rounds": 0, "ok": 0, "agreed": [], "dropped": []}
    else:
        ai_obj, errs, vote_detail = call_ai_vote(struct, imgs, rounds=args.vote,
                                                 force_text_only=args.text_only)
    elapsed = round(time.time() - t0, 1)
    err = "" if ai_obj else ("；".join([e for e in (errs or []) if e]) or "AI 未返回")
    print(f"[AI ] {'成功' if ai_obj else '失败'} {elapsed}s (投票 {vote_detail.get('rounds')} 轮, "
          f"采纳 {len(vote_detail.get('agreed') or [])} 项, 丢弃分歧 {len(vote_detail.get('dropped') or [])} 项)"
          + ("  错误：" + err if err else ""))

    rep = to_report(pdf, struct, ai_obj, err, elapsed, len(imgs), vote_detail,
                    image_mode=image_mode)
    rp = os.path.join(outdir, os.path.splitext(os.path.basename(pdf))[0] + ".pdf_report.json")
    with io.open(rp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("[报告] " + rp)
    print(json.dumps({"summary": rep["summary"], "count": len(rep["checks"]),
                      "ai_summary": rep["ai"]["summary"][:160]}, ensure_ascii=False))

    if not args.keep_images:
        for p in imgs:
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.rmdir(work)
        except Exception:
            pass
    # 即使 AI 失败，本地规则报告仍然有效 → 返回 0（AI 失败只作为 warning）
    return 0


if __name__ == "__main__":
    sys.exit(main())
