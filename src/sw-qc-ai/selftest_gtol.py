# -*- coding: utf-8 -*-
"""自测汇总：真实图纸 + 合成场景 + 插件运行时一致性 + 回归"""
import glob
import io
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

TEMP = os.environ["TEMP"]
# 便携化：输出目录放到脚本同级（受限环境 %TEMP% 可能不可写）
_HERE0 = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(_HERE0, "_selftest_out")
_IN = os.path.join(TEMP, "SwQcAddin")   # 输入 .sw.json 仍从临时目录读
# 便携化：优先用本机解释器与本目录脚本；找不到再退回老路径
_HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_PY = sys.executable or r"D:\SwQcAddin\sw-qc-ai\.venv\Scripts\python.exe"
LIVE_SCRIPT = os.path.join(_HERE, "sw_native_qc.py")
NATIVE = LIVE_PY

CASES = [
    ("真实图纸 左铰链", os.path.join(_IN, "左铰链.sw.json")),
    ("合成 3 条几何（—/⌖/⊥）", os.path.join(_IN, "g3.sw.json")),
    ("合成 涵盖（圆柱度+圆度）", os.path.join(_IN, "g2.sw.json")),
    ("合成 Ⓔ 包容要求", os.path.join(_IN, "multigtol_env.sw.json")),
    ("合成 未关联（几何远离尺寸）", os.path.join(_IN, "unlinked.sw.json")),
]


def run(exe, script, js, outdir):
    for f in glob.glob(os.path.join(outdir, "*.qc_report.json")):
        try:
            os.remove(f)
        except Exception:
            pass
    subprocess.run([exe, script, js, "--out", outdir], capture_output=True)
    files = glob.glob(os.path.join(outdir, "*.qc_report.json"))
    if not files:
        return None
    return json.load(io.open(files[0], encoding="utf-8"))


def summarize(rep):
    rows = rep.get("dim_analysis") or []
    gt = [r for r in rows if r.get("gtols")]
    n_gt = sum(len(r["gtols"]) for r in gt)
    inc = sum(1 for r in gt for g in r["gtols"] if g.get("include"))
    sig = max([r.get("sigma_t") or 0 for r in gt], default=0)
    mmvc = max([r.get("mmvc_shaft") or 0 for r in gt], default=0)
    unl = len(rep.get("unlinked_gtols") or [])
    return dict(rows=len(rows), gt_lines=n_gt, inc=inc, sigma=sig, mmvc=mmvc, unlinked=unl)


print("=" * 92)
print("工程图QC 自测报告（几何公差多框格 / 关联 / 未关联 / 计算）")
print("=" * 92)
print(f"插件运行时 Python: {LIVE_PY}")
print(f"插件运行时 脚本  : {LIVE_SCRIPT}")
print()

print("-" * 92)
print("一、用**插件实际运行时**跑各场景（和插件点「审图」完全同一条链路）")
print("-" * 92)
print(f"{'场景':<26}{'尺寸行':>6}{'几何条数':>8}{'计入':>5}{'Σt':>8}{'轴边界':>9}{'未关联':>7}")
for name, js in CASES:
    if not os.path.exists(js):
        print(f"{name:<26}  文件不存在: {js}")
        continue
    outdir = os.path.join(BASE, "self_" + os.path.basename(js).replace(".sw.json", ""))
    os.makedirs(outdir, exist_ok=True)
    rep = run(LIVE_PY, LIVE_SCRIPT, js, outdir)
    if rep is None:
        print(f"{name:<26}  无报告")
        continue
    s = summarize(rep)
    print(f"{name:<26}{s['rows']:>6}{s['gt_lines']:>8}{s['inc']:>5}"
          f"{s['sigma']:>8}{s['mmvc']:>9}{s['unlinked']:>7}")

print()
print("-" * 92)
print("二、真实图纸 左铰链 明细（逐条几何公差的判定与原因）")
print("-" * 92)
js = os.path.join(_IN, "左铰链.sw.json")
rep = run(LIVE_PY, LIVE_SCRIPT, js, os.path.join(BASE, "self_detail"))
for r in ((rep or {}).get("dim_analysis") or []):
    if not r.get("gtols"):
        continue
    print(f"  尺寸 {r.get('content')}  公差 {r.get('tol')}  IT {r.get('it')}")
    for g in r["gtols"]:
        print(f"    {g['sym']}{g['val']}  基准={g.get('dat1') or '-'}  "
              f"计入={g.get('include')}  原因={g.get('reason')}")
    print(f"    Σt={r.get('sigma_t')}  MMS轴={r.get('mms_shaft')}  "
          f"作用边界轴={r.get('mmvc_shaft')}  孔={r.get('mmvc_hole')}")
    print(f"    备注：{r.get('note')}")

print()
print("-" * 92)
print("三、插件运行时 vs 开发运行时 结果一致性")
print("-" * 92)
a = run(LIVE_PY, LIVE_SCRIPT, js, os.path.join(BASE, "self_a"))
b = run(NATIVE, os.path.join(_HERE, "sw_native_qc.py"), js, os.path.join(BASE, "self_b"))
sa, sb = summarize(a or {}), summarize(b or {})
print(f"  插件运行时: {sa}")
print(f"  开发运行时: {sb}")
print(f"  一致: {sa == sb}")
