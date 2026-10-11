"""用真实 WI + 合成的「签字扫描页」验同文件夹模式（产物只写 /tmp）。"""
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

SW = Path("/Users/langzi/Desktop/PDF首页替换1.0_Mac版")
sys.path.insert(0, str(SW))
import swap_core as C  # noqa: E402
from selftest import build_pdf  # noqa: E402

from pypdf import PdfReader  # noqa: E402

ROOT = Path(tempfile.gettempdir()) / "pdfswap_hybrid_real"
if ROOT.exists():
    shutil.rmtree(ROOT)
hyb, out = ROOT / "混装", ROOT / "输出"
hyb.mkdir(parents=True)

pool = ([p for p in sorted(Path("/Users/langzi/Desktop/成功1").glob("*.pdf")) if "签字" in p.name][:2]
        + [p for p in sorted(Path("/Users/langzi/Desktop/成功2").glob("*.pdf")) if "签字" in p.name][:1])
assert pool, "没找到真实语料"

fails = []


def chk(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'NG '} {name}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        fails.append(name)


def cover_hash(pdf):
    """第 1 页内嵌图像数据的哈希（真实 WI 封面是整页扫描图）。"""
    pg = PdfReader(str(pdf)).pages[0]
    res = pg.get("/Resources")
    xo = res.get("/XObject") if res else None
    if not xo:
        return ""
    for k in xo.get_object():
        o = xo.get_object()[k].get_object()
        if o.get("/Subtype") == "/Image":
            return hashlib.sha256(o.get_data()).hexdigest()[:16]
    return ""


print("真实语料：")
for p in pool:
    shutil.copy2(p, hyb / p.name)                       # 原件（多页）
    code = C.extract_code(p.name)
    scan = hyb / f"{p.stem.split(',')[0]} {code} 扫描签字页.pdf"
    build_pdf(scan, [(595.0, 842.0, f"SIGNED-{code}")])  # 合成的单页扫描件
    print(f"  {p.name}\n    ↳ 扫描页 {scan.name}")

opts = C.Options(src_dir=str(hyb), scan_dir=str(hyb), out_dir=str(out))
rep = C.plan_pairs(opts)
sp = rep.split
chk("自动进同文件夹模式", rep.single_dir is True)
chk(f"原件 {len(sp.originals)} 份（真实 WI 都是多页）", len(sp.originals) == len(pool),
    str([(i.path.name, i.pages) for i in sp.originals]))
chk(f"扫描件 {len(sp.scans)} 份（合成的单页）", len(sp.scans) == len(pool),
    str([(i.path.name, i.pages) for i in sp.scans]))
chk("无法判断 0 份", not sp.unsure, str([i.path.name for i in sp.unsure]))
matched = [p for p in rep.pairs if p.status == C.Status.MATCHED]
chk("全部按编号精确配上", len(matched) == len(pool) and
    all(p.strategy == "编号精确" for p in matched),
    str([(p.src.path.name, p.strategy) for p in matched]))

run = C.run_batch(opts, report=rep)
chk(f"全部替换成功（{run.ok_count}/{len(pool)}）", run.ok_count == len(pool),
    str([(r.src.name, r.note) for r in run.results if not r.ok]))

for p in pool:
    o = out / p.name
    if not o.exists():
        chk(f"[{p.name}] 产出成品", False, str(sorted(x.name for x in out.glob('*.pdf'))))
        continue
    code = C.extract_code(p.name)
    scan = hyb / f"{p.stem.split(',')[0]} {code} 扫描签字页.pdf"
    src_rd, out_rd = PdfReader(str(p)), PdfReader(str(o))
    chk(f"[{code}] 页数不变", len(out_rd.pages) == len(src_rd.pages),
        f"{len(out_rd.pages)}/{len(src_rd.pages)}")
    chk(f"[{code}] 第 1 页图像 = 扫描件首页", cover_hash(o) == cover_hash(scan),
        f"{cover_hash(o)} vs {cover_hash(scan)}")
    chk(f"[{code}] 原封面确实被换掉", cover_hash(o) != cover_hash(p))
    same = all(out_rd.pages[i].get_contents().get_data() == src_rd.pages[i].get_contents().get_data()
               for i in range(1, len(src_rd.pages)))
    chk(f"[{code}] 第 2 页起内容流逐页逐字节不变", same)

print("\n真实语料同文件夹模式：" + ("全部通过" if not fails else "失败 " + "、".join(fails)))
sys.exit(1 if fails else 0)
