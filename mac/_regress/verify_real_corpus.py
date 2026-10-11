"""真实 WI 文件端到端实测（产物全在 /tmp）

验证判据（不依赖文本层，因为真实 WI 的封面是整页扫描图）：
  A 文件名匹配：全部靠「编号精确」命中
  B 每份输出：页数一致
  C 第 2 页起：内容流逐页逐字节不变
  D 第 1 页：内嵌图像数据哈希 == 扫描件的图像哈希（首页真的换成了扫描件）
  E 第 1 页：图像哈希 != 原件封面的图像哈希（原封面确实被换掉）
  F 尺寸：贴合模式跟随原件显示尺寸；原样模式跟随扫描件显示尺寸
"""
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader

SW = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SW)
import swap_core as C

_fails: list[str] = []


def chk(n, c, d=""):
    print(f"  {'OK ' if c else 'NG '} {n}" + ("" if c else f"  <{d}>"))
    if not c:
        _fails.append(n)


def page_img(reader, idx=0):
    pg = reader.pages[idx]
    res = pg.get("/Resources")
    xo = res.get("/XObject") if res else None
    if not xo:
        return None
    for k in xo.get_object():
        o = xo.get_object()[k].get_object()
        if o.get("/Subtype") == "/Image":
            return (hashlib.sha256(o.get_data()).hexdigest()[:16],
                    int(o.get("/Width")), int(o.get("/Height")))
    return None


def content_of(reader, idx):
    c = reader.pages[idx].get_contents()
    return c.get_data() if c else b""


# ── 语料：真实 WI + 合成的"签字后扫描封面" ────────────────────────────────
pool = [p for p in sorted(Path("/Users/langzi/Desktop/成功1").glob("*.pdf"))
        + sorted(Path("/Users/langzi/Desktop/成功2").glob("*.pdf")) if "签字" in p.name]
if not pool:
    print("跳过：桌面「成功1 / 成功2」里没找到带「签字」的真实 WI 语料。")
    sys.exit(0)

root = Path(tempfile.gettempdir()) / "pdfswap_real"
if root.exists():
    shutil.rmtree(root)
src_d, scan_d, out_d = root / "原件", root / "签字扫描", root / "输出"
src_d.mkdir(parents=True)
scan_d.mkdir(parents=True)

from PIL import Image, ImageDraw  # noqa: E402

print("真实语料：")
for i, p in enumerate(pool):
    shutil.copy2(p, src_d / p.name)
    # 扫描件=把原件封面重画成一张"已签字"图，尺寸默认与原件封面显示尺寸一致
    r = PdfReader(str(p))
    w, h = C.display_size(r.pages[0])
    if i == len(pool) - 1:  # 最后一份故意用纵向 A4，测"原样"分支
        w, h = 595.0, 842.0
    img = Image.new("RGB", (int(w * 2), int(h * 2)), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, int(w * 2) - 40, 160], fill="black")
    d.text((60, 80), f"SIGNED-COVER-{i}", fill="white")
    scan_name = p.name.replace(".pdf", " 已签字扫描件.pdf")
    img.save(scan_d / scan_name.replace(".pdf", ".pdf"))
    print(f"  {p.name}\n      ← {scan_name}   (封面显示尺寸 {w:.0f}x{h:.0f})")

# ── 匹配 ─────────────────────────────────────────────────────────────────
print("\n[A] 文件名匹配")
opts = C.Options(src_dir=str(src_d), scan_dir=str(scan_d), out_dir=str(out_d))
rep = C.plan_pairs(opts)
for p in rep.pairs:
    print(f"    {p.src.path.name}  ←  [{p.strategy} {p.score} {p.status.value}]")
chk("全部配对成功", len(rep.pairs) == len(pool) and all(p.status == C.Status.MATCHED for p in rep.pairs))
chk("全部走编号精确", all(p.strategy == "编号精确" for p in rep.pairs),
    str(sorted({p.strategy for p in rep.pairs})))
chk("没有多余扫描件", not rep.orphan_scans, str([s.path.name for s in rep.orphan_scans]))

# ── 执行 ─────────────────────────────────────────────────────────────────
print("\n[B~F] 首页替换")
rep = C.run_batch(opts, report=rep)
chk("全部替换成功", all(r.ok for r in rep.results),
    str([(r.src.name, r.note) for r in rep.results if not r.ok]))

src_by_name = {p.src.path.name: p for p in rep.pairs}
for r in rep.results:
    pair = src_by_name[r.src.name]
    pre = r.src.name[:36]
    so, sn, sc = PdfReader(str(r.src)), PdfReader(str(r.out)), PdfReader(str(pair.scan.path))
    chk(f"[{pre}] B 页数一致 {len(sn.pages)}/{len(so.pages)}", len(sn.pages) == len(so.pages))
    same = all(content_of(sn, i) == content_of(so, i) for i in range(1, len(so.pages)))
    chk(f"[{pre}] C 第2页起内容流逐页不变", same)
    io_, is_, ic = page_img(sn), page_img(so), page_img(sc)
    chk(f"[{pre}] D 第1页图 == 扫描件图", io_ and ic and io_[0] == ic[0],
        f"out={io_ and io_[0]} scan={ic and ic[0]}")
    chk(f"[{pre}] E 第1页图 != 原封面图", io_ and is_ and io_[0] != is_[0],
        f"out={io_ and io_[0]} src={is_ and is_[0]}")
    d_out, d_src, d_scan = (C.display_size(sn.pages[0]), C.display_size(so.pages[0]),
                            C.display_size(sc.pages[0]))
    if r.fit_mode == C.FIT_FIT:
        chk(f"[{pre}] F 贴合：显示尺寸跟随原件",
            abs(d_out[0] - d_src[0]) < 0.6 and abs(d_out[1] - d_src[1]) < 0.6, f"{d_out} vs {d_src}")
    else:
        chk(f"[{pre}] F 原样：显示尺寸跟随扫描件",
            abs(d_out[0] - d_scan[0]) < 0.6 and abs(d_out[1] - d_scan[1]) < 0.6, f"{d_out} vs {d_scan}")
    print(f"       ↳ fit={r.fit_mode}｜{r.fit_reason}")

print(f"\n结果：{'全部通过' if not _fails else f'失败 {len(_fails)} 项'}")
for f in _fails:
    print("  - " + f)
sys.exit(1 if _fails else 0)
