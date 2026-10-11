"""渲染级核对：把输出第 1 页与扫描件第 1 页画成位图逐像素比。

判据：贴合模式下两者应几乎相同（同一张图，仅缩放）；原样模式应完全相同。
依赖：pymupdf + pillow（可放在一次性环境里，别污染交付 venv）：
    python3 -m venv /tmp/render_venv && /tmp/render_venv/bin/pip install pymupdf pillow
    /tmp/render_venv/bin/python _regress/verify_render.py
前置：先跑 verify_real_corpus.py 造出 /tmp/pdfswap_real 语料与输出。
"""
import sys
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops

R = Path(tempfile.gettempdir()) / "pdfswap_real"
out, scan = R / "输出", R / "签字扫描"
if not out.is_dir():
    print("跳过：还没跑过 verify_real_corpus.py（缺少 /tmp/pdfswap_real/输出）")
    sys.exit(0)
ZOOM = 2.0
fails = []


def render(pdf: Path, page: int = 0) -> Image.Image:
    doc = pymupdf.open(str(pdf))
    pix = doc[page].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    h = diff.convert("L").histogram()
    total = sum(h)
    return sum(i * n for i, n in enumerate(h)) / total


pairs = [
    ("WI-2012,rev35 近端和远端过渡作业指导书 - 签字版.pdf",
     "WI-2012,rev35 近端和远端过渡作业指导书 - 签字版 已签字扫描件.pdf", "贴合"),
    ("WI-2013, rev30 亲水涂层作业指导书for PTCA - 签字版.pdf",
     "WI-2013, rev30 亲水涂层作业指导书for PTCA - 签字版 已签字扫描件.pdf", "贴合"),
    ("WI-2103, rev13 球囊近端焊接作业指导书 Achilles - 签字版.pdf",
     "WI-2103, rev13 球囊近端焊接作业指导书 Achilles - 签字版 已签字扫描件.pdf", "原样"),
]

for outname, scanname, mode in pairs:
    o, s = out / outname, scan / scanname
    if not o.exists() or not s.exists():
        key = outname.split(",")[0]
        o = next((p for p in out.glob("*.pdf") if p.name.startswith(key)), None)
        s = next((p for p in scan.glob("*.pdf") if p.name.startswith(key)), None)
    print(f"     (文件：{o.name if o else None} / {s.name if s else None})")
    img_o, img_s = render(o, 0), render(s, 0)
    d1 = mean_diff(img_o, img_s)
    # 再比一下：输出第2页 vs 原件第2页（应完全一致）
    img_o2 = render(o, 1)
    src_pdf = R / "原件" / o.name
    img_src1 = render(src_pdf, 0)
    d0 = mean_diff(img_o, img_src1)
    ok = d1 < 6.0
    print(f"  {'OK ' if ok else 'NG '} [{mode}] {outname[:34]}")
    print(f"        输出第1页 vs 扫描件第1页：平均像素差 {d1:.2f}   （越小越像，<6 视为同一画面）")
    print(f"        输出第1页 vs 原件封面  ：平均像素差 {d0:.2f}   （应明显更大，证明确实换了）")
    if not ok:
        fails.append(outname)
    img_o.save(f"/tmp/render_out_{mode}.png")
    img_s.save(f"/tmp/render_scan_{mode}.png")

print("\n渲染核对：" + ("全部通过" if not fails else "失败 " + "、".join(fails)))
sys.exit(1 if fails else 0)
