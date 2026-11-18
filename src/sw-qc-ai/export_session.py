# -*- coding: utf-8 -*-
"""
export_session.py — 把 DSH 会话记录导出成可读文档

DSH 把会话存成 **zstd 压缩的多帧 JSONL**：
    ~/.dsh/sessions/<工作区>/session-<id>/session.v4.jsonl.zstd

这个脚本把它解压、解析成对话，导出三种格式：
    • Markdown（精简版：只看人说的话与助手答复）
    • Markdown（完整版：含推理摘要与全部工具调用）
    • Word .docx（便于阅读/打印/归档）

用法：
    python export_session.py                     # 自动找最近的会话
    python export_session.py --session <路径>     # 指定会话文件
    python export_session.py --out D:\\导出目录
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys

DSH_HOME = os.path.join(os.path.expanduser("~"), ".dsh")
SESSIONS_ROOT = os.path.join(DSH_HOME, "sessions")

# 这些来源不是"用户真的打字"，而是系统/框架注入的内容
# （运行环境说明、技能清单、后台任务完成通知、审批状态变更…）。
# 不把它们当成用户发言，否则对话轮次会整体错位 —— 实测会让助手的回复
# 全部挂到"技能清单"那一条上，正文看起来就是空的。
SYSTEM_SOURCES = {
    "user-approval", "runtime-context", "skill-catalog", "tool-jobs",
    "system", "tool", "agent",
}


def find_latest_session() -> str | None:
    pattern = os.path.join(SESSIONS_ROOT, "**", "session*.jsonl.zstd")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def read_session(path: str) -> tuple[dict, list[dict]]:
    """解压并解析会话文件。返回 (会话头信息, 记录列表)。"""
    try:
        import zstandard
    except ImportError:
        raise SystemExit("缺少 zstandard：请运行  pip install zstandard")

    with open(path, "rb") as fh:
        raw = fh.read()
    # 注意：这是**多帧** zstd，必须用 stream_reader；
    # 直接 decompress() 只会解出第一帧（实测只能拿到 233 字节）。
    with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
        data = reader.read()

    records: list[dict] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # 会话头是顶层对象（没有 data 包装），其余记录才是 {type, seq, time, data}
    header = next((r for r in records if r.get("type") == "session"), {})
    return header, records


def _ts(ms) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _content_text(content) -> str:
    """从 content 数组里取正文（跳过 reasoning）。"""
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text"):
            parts.append(item["text"])
    return "\n\n".join(parts).strip()


def _reasoning_text(content) -> str:
    if isinstance(content, str):
        return ""
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            parts.append(item.get("text") or "")
    return "\n".join(parts).strip()


def build_turns(records: list[dict]) -> list[dict]:
    """把扁平记录整理成「轮次」结构（按 seq 顺序，以真实用户发言切分）。"""
    turns: list[dict] = []
    current: dict | None = None
    pending_notes: list[dict] = []      # 还没有归属轮次的系统通知

    def ensure_turn() -> dict:
        nonlocal current
        if current is None:
            current = {"index": len(turns) + 1, "time": "", "user": "(会话开始)",
                       "system": pending_notes[:], "replies": [], "tools": []}
            pending_notes.clear()
            turns.append(current)
        return current

    for rec in records:
        rtype = rec.get("type")
        data = rec.get("data") or {}
        when = _ts(rec.get("time"))

        if rtype == "user/message":
            kind = ((data.get("source") or {}).get("kind") or "user")
            text = _content_text(data.get("content"))
            if not text:
                continue
            if kind in SYSTEM_SOURCES:
                note = {"time": when, "text": text, "kind": kind}
                if current is None:
                    pending_notes.append(note)
                else:
                    current["system"].append(note)
                continue
            current = {"index": len(turns) + 1, "time": when, "user": text,
                       "system": pending_notes[:], "replies": [], "tools": []}
            pending_notes.clear()
            turns.append(current)

        elif rtype == "assistant/message":
            turn = ensure_turn()
            msg = data.get("message") or {}
            content = msg.get("content")
            text = _content_text(content)
            reasoning = _reasoning_text(content)
            # 工具调用就写在助手消息的 content 里（type='tool-call'）
            for item in content or []:
                if isinstance(item, dict) and item.get("type") == "tool-call":
                    args = item.get("arguments") or item.get("input") or ""
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                        except Exception:
                            args = str(args)
                    turn["tools"].append({"name": item.get("name", "?"),
                                          "args": args[:400]})
            if text or reasoning:
                turn["replies"].append({"time": when, "text": text,
                                        "reasoning": reasoning})

        elif rtype == "tool/call":
            turn = ensure_turn()
            args = data.get("arguments") or ""
            try:
                args = json.dumps(json.loads(args), ensure_ascii=False)
            except Exception:
                pass
            turn["tools"].append({"name": data.get("name", "?"),
                                  "args": args[:400]})

    return [t for t in turns if t.get("user") or t.get("replies") or t.get("tools")]


def _md_escape(s: str) -> str:
    return s or ""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…（此处省略 {len(text) - limit} 字）"


def to_markdown(header: dict, turns: list[dict], *, full: bool) -> str:
    lines: list[str] = []
    lines.append("# 工程图 QC 工具 — 开发对话记录")
    lines.append("")
    lines.append(f"- **会话 ID**：`{header.get('id', '')}`")
    lines.append(f"- **工作目录**：`{header.get('cwd', '')}`")
    lines.append(f"- **开始时间**：{_ts(header.get('createdAt'))}")
    lines.append(f"- **导出时间**：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"- **轮次**：{len(turns)}")
    lines.append(f"- **版本**：{'完整版（含推理与工具调用）' if full else '精简版（仅每轮最终答复）'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for t in turns:
        lines.append(f"## 第 {t['index']} 轮")
        lines.append("")
        for s in t.get("system", []):
            body = s["text"] if full else _clip(s["text"], 160).replace("\n", " ")
            lines.append(f"> ⚙️ *系统通知（{s['time']}）：{body}*")
            lines.append("")
        if t.get("user"):
            lines.append(f"### 👤 用户　`{t.get('time', '')}`")
            lines.append("")
            lines.append(t["user"])
            lines.append("")

        lines.append("### 🤖 助手")
        lines.append("")
        replies = t.get("replies", [])
        if full:
            for i, r in enumerate(replies, 1):
                if r.get("reasoning"):
                    lines.append("<details><summary>推理过程</summary>")
                    lines.append("")
                    lines.append("```")
                    lines.append(_clip(r["reasoning"], 6000))
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")
                if r.get("text"):
                    if len(replies) > 1:
                        lines.append(f"**（第 {i} 段）**")
                        lines.append("")
                    lines.append(r["text"])
                    lines.append("")
        else:
            # 精简版：只保留本轮**最后一段**正文 —— 那才是真正给用户的答复；
            # 中间那些「我先看一下…」属于过程播报，放完整版里。
            texts = [r["text"] for r in replies if r.get("text")]
            if texts:
                lines.append(texts[-1])
                lines.append("")

        if full and t.get("tools"):
            lines.append(f"<details><summary>工具调用（{len(t['tools'])} 次）</summary>")
            lines.append("")
            for tc in t["tools"]:
                lines.append(f"- `{tc['name']}`　{tc['args']}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def to_docx(header: dict, turns: list[dict], out_path: str, *, full: bool) -> str:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading("工程图 QC 工具 — 开发对话记录", level=0)
    meta = doc.add_paragraph()
    meta.add_run(f"会话 ID：{header.get('id', '')}\n").font.size = Pt(9)
    meta.add_run(f"工作目录：{header.get('cwd', '')}\n").font.size = Pt(9)
    meta.add_run(f"开始时间：{_ts(header.get('createdAt'))}\n").font.size = Pt(9)
    meta.add_run(f"导出时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n").font.size = Pt(9)
    meta.add_run(f"共 {len(turns)} 轮 · "
                 f"{'完整版' if full else '精简版'}").font.size = Pt(9)

    for t in turns:
        doc.add_heading(f"第 {t['index']} 轮", level=1)
        for s in t.get("system", []):
            p = doc.add_paragraph()
            r = p.add_run(f"⚙️ 系统通知：{s['text']}")
            r.italic = True
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        if t.get("user"):
            doc.add_heading("👤 用户", level=2)
            doc.add_paragraph(t["user"])
        doc.add_heading("🤖 助手", level=2)
        for r in t.get("replies", []):
            if full and r.get("reasoning"):
                p = doc.add_paragraph()
                run = p.add_run("推理：" + r["reasoning"][:2500])
                run.font.size = Pt(8)
                run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
            if r.get("text"):
                doc.add_paragraph(r["text"])
        if full and t.get("tools"):
            p = doc.add_paragraph()
            run = p.add_run("工具调用：" + "；".join(
                f"{tc['name']}" for tc in t["tools"]))
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    doc.save(out_path)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导出 DSH 会话记录")
    ap.add_argument("--session", default=None, help="会话 jsonl.zstd 路径（默认取最近一个）")
    ap.add_argument("--out", default=None, help="导出目录（默认当前目录）")
    ap.add_argument("--raw", action="store_true",
                    help="同时导出解压后的原始 jsonl（体积较大）")
    args = ap.parse_args(argv)

    session = args.session or find_latest_session()
    if not session or not os.path.isfile(session):
        print("找不到会话文件。请用 --session 指定路径。", file=sys.stderr)
        return 2

    outdir = args.out or os.getcwd()
    os.makedirs(outdir, exist_ok=True)

    header, records = read_session(session)
    turns = build_turns(records)

    stamp = datetime.datetime.now().strftime("%Y%m%d")
    base = f"对话记录_{stamp}"

    files = []
    for full, suffix in ((False, "精简版"), (True, "完整版")):
        md = os.path.join(outdir, f"{base}_{suffix}.md")
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(to_markdown(header, turns, full=full))
        files.append(md)

    docx_path = os.path.join(outdir, f"{base}_{'完整版' if True else ''}.docx")
    to_docx(header, turns, docx_path, full=True)
    files.append(docx_path)

    if args.raw:
        import zstandard
        with open(session, "rb") as fh:
            raw = fh.read()
        with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
            data = reader.read()
        raw_path = os.path.join(outdir, f"{base}_原始记录.jsonl")
        with open(raw_path, "wb") as fh:
            fh.write(data)
        files.append(raw_path)

    print(f"会话文件：{session}")
    print(f"记录条数：{len(records)}　轮次：{len(turns)}")
    print("已导出：")
    for f in files:
        print(f"  {f}  ({os.path.getsize(f) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
