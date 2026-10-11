"""自测脚本 —— 全部产物写在系统临时目录，不污染桌面。

跑法：.venv/bin/python -B selftest.py
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import swap_core as C  # noqa: E402

try:                       # pillow 缺失时跳过图片相关用例，不让整个自测挂掉
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  <{detail}>" if detail and not cond else ""))


# ── 纯标准库构造 PDF（含文字，便于验证内容是否保留） ──────────────────────
def build_pdf(path: Path, pages: list[tuple[float, float, str]]) -> None:
    objs: dict[int, bytes] = {}
    page_nums = [4 + 2 * i for i in range(len(pages))]
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, (w, h, label) in enumerate(pages):
        pn, cn = page_nums[i], page_nums[i] + 1
        objs[pn] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cn} 0 R >>"
        ).encode()
        stream = f"BT /F1 28 Tf 60 100 Td ({label}) Tj ET".encode()
        objs[cn] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n")
    xref_pos = out.tell()
    max_num = max(objs) + 1
    out.write(f"xref\n0 {max_num}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, max_num):
        off = offsets.get(num)
        out.write((f"{off:010d} 00000 n \n" if off else "0000000000 65535 f \n").encode())
    out.write(
        f"trailer\n<< /Size {max_num} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())


def page_texts(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "").strip() for p in PdfReader(str(pdf)).pages]


def page_sizes(pdf: Path) -> list[tuple[float, float]]:
    from pypdf import PdfReader

    out = []
    for p in PdfReader(str(pdf)).pages:
        b = p.mediabox
        out.append((round(float(b.width), 1), round(float(b.height), 1)))
    return out


def main() -> int:
    root = Path(tempfile.gettempdir()) / "pdfswap_selftest"
    if root.exists():
        shutil.rmtree(root)
    src_d = root / "原始"
    scan_d = root / "扫描"
    out_d = root / "输出"
    src_d.mkdir(parents=True)
    scan_d.mkdir(parents=True)
    print(f"临时工作目录：{root}\n")

    # ── ① 归一化与编号抽取 ──────────────────────────────────────────────
    print("[1] 归一化 / 编号抽取")
    check("去噪声词+大小写归一", C.normalize_name("WI-2101 已签字 扫描件.pdf") == "WI2101",
          C.normalize_name("WI-2101 已签字 扫描件.pdf"))
    check("全角转半角", C.normalize_name("ＷＩ－２１０１.pdf") == "WI2101",
          C.normalize_name("ＷＩ－２１０１.pdf"))
    check("中文名保留", C.normalize_name("作业指导书.docx") == "作业指导书",
          C.normalize_name("作业指导书.docx"))
    check("编号：WI-2101", C.extract_code("WI-2101 作业指导书.pdf") == "WI2101")
    check("编号：GRA_3634", C.extract_code("GRA_3634 scan.pdf") == "GRA3634")
    check("编号：T-2054-49", C.extract_code("T-2054-49 参数卡.pdf") == "T205449")
    check("编号：连写", C.extract_code("WI2101.pdf") == "WI2101")
    check("无编号返回空", C.extract_code("作业指导书.pdf") == "")

    # ── ② 造语料 ────────────────────────────────────────────────────────
    print("\n[2] 造测试语料")
    build_pdf(src_d / "WI-2101 作业指导书.pdf",
              [(595.0, 842.0, "SRC1-PAGE1-COVER"), (595.0, 842.0, "SRC1-PAGE2-BODY"),
               (595.0, 842.0, "SRC1-PAGE3-BODY")])
    build_pdf(src_d / "GRA-3634 检验规范.pdf",
              [(595.0, 842.0, "SRC2-PAGE1-COVER"), (842.0, 595.0, "SRC2-PAGE2-LANDSCAPE")])
    build_pdf(src_d / "没有扫描件的文件.pdf", [(595.0, 842.0, "SRC3-P1")])
    build_pdf(scan_d / "WI-2101 作业指导书 已签字扫描件.pdf", [(595.0, 842.0, "SIGNED-WI2101")])
    build_pdf(scan_d / "GRA-3634.pdf", [(595.0, 842.0, "SIGNED-GRA3634")])
    build_pdf(scan_d / "孤零零的扫描件.pdf", [(595.0, 842.0, "ORPHAN")])
    # 冲突场景：同一编号两个候选
    build_pdf(scan_d / "WI-2101 作业指导书 已签字扫描件(1).pdf", [(595.0, 842.0, "SIGNED-DUP")])
    check("语料生成完毕", len(list(src_d.glob("*.pdf"))) == 3 and len(list(scan_d.glob("*.pdf"))) == 4)

    # ── ③ 匹配 ──────────────────────────────────────────────────────────
    print("\n[3] 匹配逻辑")
    opts = C.Options(src_dir=str(src_d), scan_dir=str(scan_d), out_dir=str(out_d))
    rep = C.plan_pairs(opts)
    by_name = {p.src.path.name: p for p in rep.pairs}
    check("噪声后缀能对上", "WI-2101 作业指导书.pdf" in by_name)
    check("编号命中策略=编号精确",
          by_name.get("WI-2101 作业指导书.pdf") and
          by_name["WI-2101 作业指导书.pdf"].strategy == "编号精确",
          by_name.get("WI-2101 作业指导书.pdf").strategy if by_name.get("WI-2101 作业指导书.pdf") else "无")
    check("无扫描件的原件被识别",
          any(p.status == C.Status.NO_SCAN and p.src.path.name == "没有扫描件的文件.pdf" for p in rep.pairs))
    check("多余扫描件被识别",
          any("孤零零" in s.path.name for s in rep.orphan_scans),
          "、".join(s.path.name for s in rep.orphan_scans))

    # 阈值拉满 → 编号策略仍应命中（证明不是靠相似度蒙的）
    p_hi = C.score_pair(
        C.FileItem(src_d / "WI-2101 作业指导书.pdf", "", "WI2101", "WI2101"),
        C.FileItem(scan_d / "WI-2101 作业指导书 已签字扫描件.pdf", "", "WI2101", "WI2101"),
        0.999,
    )
    check("阈值 0.999 时仍走编号精确", p_hi[1] == "编号精确", str(p_hi))

    # ── ④ 首页替换 ──────────────────────────────────────────────────────
    print("\n[4] 首页替换")
    src1 = src_d / "WI-2101 作业指导书.pdf"
    scan1 = scan_d / "WI-2101 作业指导书 已签字扫描件.pdf"
    out1 = out_d / "WI-2101 作业指导书.pdf"
    r1 = C.swap_first_page(src1, scan1, out1, opts)
    check("执行成功", r1.ok, r1.note)
    check("页数不变（3 页）", r1.pages_out == 3, str(r1.pages_out))
    t = page_texts(out1)
    check("第 1 页 = 扫描首页", "SIGNED-WI2101" in t[0], t[0])
    check("原封面已被替换掉", "SRC1-PAGE1-COVER" not in " ".join(t))
    check("第 2/3 页内容保留", "SRC1-PAGE2-BODY" in t[1] and "SRC1-PAGE3-BODY" in t[2], str(t))
    check("第 1 页尺寸跟随原件 A4",
          page_sizes(out1)[0] == (595.0, 842.0), str(page_sizes(out1)[0]))

    # 不同尺寸扫描件 → 等比缩放居中，且不改变输出第一页尺寸
    build_pdf(scan_d / "big.pdf", [(1190.0, 1684.0, "SIGNED-BIG-A3")])
    out_big = out_d / "big-test.pdf"
    r_big = C.swap_first_page(src1, scan_d / "big.pdf", out_big, opts)
    check("A3 扫描件可缩放贴入", r_big.ok and page_sizes(out_big)[0] == (595.0, 842.0),
          f"{r_big.note} {page_sizes(out_big)[0] if r_big.ok else ''}")
    check("缩放后内容仍在", "SIGNED-BIG-A3" in page_texts(out_big)[0])

    # no-scale 开关
    opts_ns = C.Options(**{**opts.__dict__, "scale_to_original": False})
    out_ns = out_d / "noscaled.pdf"
    C.swap_first_page(src1, scan_d / "big.pdf", out_ns, opts_ns)
    check("关闭缩放后第一页保留 A3 原尺寸",
          page_sizes(out_ns)[0] == (1190.0, 1684.0), str(page_sizes(out_ns)[0]))

    # 单页原件
    one = src_d / "单页原件.pdf"
    build_pdf(one, [(400.0, 600.0, "SRC-ONEPAGE")])
    out_one = out_d / "单页原件.pdf"
    r_one = C.swap_first_page(one, scan1, out_one, opts)
    check("单页原件替换后仍 1 页且为扫描页",
          r_one.ok and page_sizes(out_one) == [(400.0, 600.0)]
          and "SIGNED-WI2101" in page_texts(out_one)[0], r_one.note)

    # 版面比例不一致 → 原样保留扫描页（横版封面 + 纵向扫描件）
    print("\n[4b] 贴合决策表")
    land = src_d / "横版封面.pdf"
    build_pdf(land, [(842.0, 595.0, "LAND-COVER"), (842.0, 595.0, "LAND-P2")])
    out_land = out_d / "横版封面.pdf"
    r_land = C.swap_first_page(land, scan1, out_land, opts)  # scan1 是纵向 A4
    check("横版封面判定为「原样」", r_land.fit_mode == C.FIT_ASIS, r_land.fit_mode)
    check("原样模式：第 1 页 = 扫描件尺寸",
          page_sizes(out_land)[0] == (595.0, 842.0), str(page_sizes(out_land)[0]))
    check("原样模式：第 2 页仍是横版",
          page_sizes(out_land)[1] == (842.0, 595.0), str(page_sizes(out_land)[1]))
    check("原样模式：说明里写清了原因", "比例不一致" in r_land.note, r_land.note)
    r_same = C.swap_first_page(src1, scan1, out_d / "同比例.pdf", opts)
    check("同为纵向时判定为「贴合」", r_same.fit_mode == C.FIT_FIT, r_same.fit_mode)
    check("贴合决策函数可直接调用",
          C.decide_fit((595.0, 842.0), (1190.0, 1684.0))[0] == C.FIT_FIT
          and C.decide_fit((842.0, 595.0), (595.0, 842.0))[0] == C.FIT_ASIS)

    # 图片扫描件
    try:
        from PIL import Image

        img_p = scan_d / "图片扫描件.png"
        Image.new("RGB", (1240, 1754), "white").save(img_p)
        out_img = out_d / "图片扫描件测试.pdf"
        r_img = C.swap_first_page(src1, img_p, out_img, opts)
        check("PNG 扫描件可用", r_img.ok and page_sizes(out_img)[0] == (595.0, 842.0), r_img.note)
    except Exception as exc:  # noqa: BLE001
        check("PNG 扫描件可用", False, str(exc))

    # ── ⑤ 批量 + 输出命名 ───────────────────────────────────────────────
    print("\n[5] 批量执行 / 输出命名")
    opts2 = C.Options(**{**opts.__dict__, "output_suffix": "_已替换"})
    rep2 = C.run_batch(opts2)
    check("批量至少成功 2 份", rep2.ok_count >= 2, str(rep2.ok_count))
    check("文件名后缀生效",
          bool(rep2.results) and all(r.out.stem.endswith("_已替换") for r in rep2.results),
          str([r.out.name for r in rep2.results]))
    check("输出目录里有成品", len(list(out_d.glob("*.pdf"))) >= 3)

    # 后缀规范化 —— 界面与命令行共用这一个口径
    check("后缀里的路径/非法字符被剔除", C.clean_output_suffix("_已/替\\换") == "_已替换",
          C.clean_output_suffix("_已/替\\换"))
    check("后缀首尾的点被去掉", C.clean_output_suffix(".已签.") == "已签",
          C.clean_output_suffix(".已签."))
    check("后缀超长被截断到 40", len(C.clean_output_suffix("x" * 60)) == C.SUFFIX_MAX_LEN)
    check("空后缀原样返回", C.clean_output_suffix("   ") == "")
    check("只填非法字符时等于没填", C.clean_output_suffix("/\\:*?") == "")

    # 不覆盖语义
    opts3 = C.Options(**{**opts.__dict__, "output_suffix": "_已替换", "overwrite": False})
    rep3 = C.run_batch(opts3)
    names = [r.out.name for r in rep3.results]
    check("不覆盖时自动改名（出现 _2）", any("_2.pdf" in n for n in names), str(names))

    # 覆盖语义
    opts4 = C.Options(**{**opts.__dict__, "output_suffix": "_已替换", "overwrite": True})
    rep4 = C.run_batch(opts4)
    names4 = [r.out.name for r in rep4.results]
    check("勾上覆盖后不再另起编号", not any("_2.pdf" in n for n in names4), str(names4))

    # ── ⑥ 命令行 ────────────────────────────────────────────────────────
    print("\n[6] 命令行入口")
    out_cli = root / "输出cli"
    rc = C.main(["--src", str(src_d), "--scan", str(scan_d), "--out", str(out_cli),
                 "--suffix", "_cli", "--threshold", "0.8"])
    check("CLI 返回 0", rc == 0, str(rc))
    cli_out = list(out_cli.glob("*.pdf"))
    check("CLI 产出成品", len(cli_out) >= 2, str(len(cli_out)))
    check("CLI 的后缀同样生效", cli_out and all(p.stem.endswith("_cli") for p in cli_out),
          str([p.name for p in cli_out]))

    # ── ⑦ 同文件夹模式：原件与扫描件混在一起，按页数分流 ──────────────────
    print("\n[7] 同文件夹分流（⑨ SPLIT_RULES）")
    hyb = root / "混装"
    hyb_out = root / "混装输出"
    hyb.mkdir(parents=True)
    build_pdf(hyb / "WI-2201 作业指导书.pdf", [(595.0, 842.0, "H1-P1"), (595.0, 842.0, "H1-P2"),
                                                (595.0, 842.0, "H1-P3")])
    build_pdf(hyb / "GRA-9001 检验规范.pdf", [(595.0, 842.0, "H2-P1"), (595.0, 842.0, "H2-P2")])
    build_pdf(hyb / "WI-2201 作业指导书 已签字扫描件.pdf", [(595.0, 842.0, "H-SIGNED-2201")])
    build_pdf(hyb / "GRA-9001.pdf", [(595.0, 842.0, "H-SIGNED-9001")])
    build_pdf(hyb / "闲着的一份单页文件.pdf", [(595.0, 842.0, "H-LONELY")])
    build_pdf(hyb / "坏文件.pdf", [])
    if Image is not None:
        Image.new("RGB", (1240, 1754), "white").save(hyb / "拍照的签字页.png")

    opts_h = C.Options(src_dir=str(hyb), scan_dir=str(hyb), out_dir=str(hyb_out))
    check("两个目录填成同一个 → 自动进同文件夹模式",
          C.plan_pairs(opts_h).single_dir is True)
    rep_h = C.plan_pairs(opts_h)
    sp = rep_h.split
    check("分流结果存在", sp is not None)
    n_png = 1 if Image is not None else 0
    check(f"原件 {len(sp.originals)} 份 / 扫描件 {len(sp.scans)} 份 / 无法判断 {len(sp.unsure)} 份",
          len(sp.originals) == 2 and len(sp.scans) == 3 + n_png and len(sp.unsure) == 1,
          f"{len(sp.originals)}/{len(sp.scans)}/{len(sp.unsure)}")

    by_role = {it.path.name: it for it in sp.originals + sp.scans + sp.unsure}
    item_3p = by_role["WI-2201 作业指导书.pdf"]
    check("≥2 页判为原件", item_3p.role == C.Role.ORIGINAL.value, item_3p.role)
    check("依据里写明了页数", "3 页" in item_3p.role_reason, item_3p.role_reason)
    item_1p = by_role["WI-2201 作业指导书 已签字扫描件.pdf"]
    check("1 页判为扫描件", item_1p.role == C.Role.SCAN.value, item_1p.role)
    hint_1p = C.scan_hint_in(item_1p.stem)
    check("名字带签字类提示词时依据里点了出来",
          bool(hint_1p) and f"「{hint_1p}」" in item_1p.role_reason, item_1p.role_reason)
    check("名字带「签字」的不算低置信", item_1p.notable is False)
    item_bad = by_role["坏文件.pdf"]
    check("读不出页数 → 无法判断（不猜）", item_bad.role == C.Role.UNSURE.value, item_bad.role)
    check("无法判断的文件在表格里露出来（没有悄悄消失）",
          any(p.status == C.Status.UNSURE and p.src.path.name == "坏文件.pdf" for p in rep_h.pairs))
    item_lonely = by_role["闲着的一份单页文件.pdf"]
    check("1 页且名字无扫描字样 → 仍按扫描件处理，但标记为低置信",
          item_lonely.role == C.Role.SCAN.value and item_lonely.notable is True,
          f"{item_lonely.role}/{item_lonely.notable}")
    if Image is not None:
        check("图片判为扫描件", by_role["拍照的签字页.png"].role == C.Role.SCAN.value,
              by_role["拍照的签字页.png"].role_reason)

    # 同一套匹配逻辑照旧生效
    h_by = {p.src.path.name: p for p in rep_h.pairs}
    check("同文件夹里也能按编号配上",
          h_by["WI-2201 作业指导书.pdf"].status == C.Status.MATCHED
          and h_by["WI-2201 作业指导书.pdf"].scan.path.name == "WI-2201 作业指导书 已签字扫描件.pdf",
          h_by["WI-2201 作业指导书.pdf"].note)
    check("孤零零的单页扫描件被列为多余", any("闲着的一份" in s.path.name for s in rep_h.orphan_scans))

    # 端到端：替换后的成品第 1 页来自扫描件、第 2 页起保留
    rep_h2 = C.run_batch(opts_h)
    out_h = hyb_out / "WI-2201 作业指导书.pdf"
    check("同文件夹模式能跑出成品", out_h.exists(), str([r.out.name for r in rep_h2.results]))
    if out_h.exists():
        th = page_texts(out_h)
        check("成品第 1 页 = 扫描页", "H-SIGNED-2201" in th[0], th[0])
        check("成品第 2 页起仍是原件正文", "H1-P2" in th[1] and "H1-P3" in th[2], str(th))
        check("成品共 3 页", len(th) == 3, str(len(th)))

    # 人工改判：那一页其实是原件 → 覆盖面最广的第一条规则生效
    opts_f = C.Options(src_dir=str(hyb), scan_dir=str(hyb), out_dir=str(hyb_out),
                       forced_roles={str(hyb / "闲着的一份单页文件.pdf"): C.Role.ORIGINAL.value})
    sp_f = C.plan_pairs(opts_f).split
    forced_item = next(it for it in sp_f.originals if it.path.name == "闲着的一份单页文件.pdf")
    check("人工改判优先于页数规则", "人工" in forced_item.role_reason, forced_item.role_reason)
    check("改判后不再出现在扫描件里",
          all(it.path.name != "闲着的一份单页文件.pdf" for it in sp_f.scans))

    # 输出目录被放在输入目录里时，绝不能把自己的成品当成原件吃回去
    (hyb / "输出").mkdir(exist_ok=True)
    build_pdf(hyb / "输出" / "上一次的成品.pdf", [(595.0, 842.0, "OLD1"), (595.0, 842.0, "OLD2")])
    opts_in = C.Options(src_dir=str(hyb), scan_dir=str(hyb), out_dir=str(hyb / "输出"))
    sp_in = C.plan_pairs(opts_in).split
    check("输出目录不会被当成输入吃回去",
          all("上一次的成品" not in it.path.name for it in sp_in.originals + sp_in.scans + sp_in.unsure),
          str([it.path.name for it in sp_in.originals]))

    # 命令行：省略 --scan 即同文件夹模式
    out_cli2 = root / "输出cli2"
    rc2 = C.main(["--src", str(hyb), "--out", str(out_cli2)])
    check("CLI 省略 --scan 即可跑同文件夹模式", rc2 == 0, str(rc2))

    print(f"\n{'=' * 56}\n通过 {len(PASS)} 项 / 失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
