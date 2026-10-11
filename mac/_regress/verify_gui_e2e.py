"""界面驱动端到端：真的调 on_preview() / on_run()，读 Treeview 与产物来验收。

覆盖两套模式：
  A. 两文件夹模式（原件目录 + 扫描件目录）
  B. 同文件夹模式（原件与扫描件混在一起，⑨ 按页数分流）+ 右键改判
"""
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

SW = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SW)
import swap_app as A  # noqa: E402
import swap_core as C  # noqa: E402
from selftest import build_pdf, page_texts  # noqa: E402

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

root_dir = Path(tempfile.gettempdir()) / "pdfswap_gui_e2e"
if root_dir.exists():
    shutil.rmtree(root_dir)
src, scan, out = root_dir / "原", root_dir / "扫", root_dir / "出"
for d in (src, scan):
    d.mkdir(parents=True)
build_pdf(src / "WI-2101 作业指导书.pdf", [(595, 842, "P1-COVER"), (595, 842, "P2-BODY")])
build_pdf(src / "GRA-3634 检验规范.pdf", [(595, 842, "Q1"), (842, 595, "Q2")])
build_pdf(src / "孤单原件.pdf", [(595, 842, "ONLY")])
build_pdf(scan / "WI-2101 作业指导书 已签字.pdf", [(595, 842, "SIGNED-A")])
build_pdf(scan / "GRA-3634 扫描件.pdf", [(1190, 1684, "SIGNED-B-A3")])
build_pdf(scan / "多余的扫描件.pdf", [(595, 842, "ORPHAN-SCAN")])

# 同文件夹模式的语料
hyb, hyb_out = root_dir / "混装", root_dir / "混装出"
hyb.mkdir(parents=True)
build_pdf(hyb / "WI-2101 作业指导书.pdf", [(595, 842, "P1-COVER"), (595, 842, "P2-BODY")])
build_pdf(hyb / "WI-2101 作业指导书 已签字扫描件.pdf", [(595, 842, "SIGNED-A")])
build_pdf(hyb / "GRA-3634 检验规范.pdf", [(595, 842, "Q1"), (595, 842, "Q2")])
build_pdf(hyb / "GRA-3634.pdf", [(595, 842, "SIGNED-B")])       # 1 页、名字无扫描字样 → 低置信
build_pdf(hyb / "坏文件.pdf", [])                                  # 读不出页数 → 无法判断
if Image is not None:
    Image.new("RGB", (1240, 1754), "white").save(hyb / "拍照的签字页.png")

A.messagebox.askokcancel = lambda *a, **k: True
A.messagebox.showinfo = lambda *a, **k: None
A.messagebox.showwarning = lambda *a, **k: print("   [警告弹窗]", a[1][:120].replace("\n", " | "), flush=True)

# 把「记住上次选择」的记录文件指到临时目录：
# 一是保证每次跑的初始状态一致，二是**绝不能覆盖用户真实的那份记录**。
A.CONFIG_PATH = Path(tempfile.gettempdir()) / "pdfswap_gui_e2e_config.json"
A.CONFIG_PATH.unlink(missing_ok=True)

root = tk.Tk()
root.withdraw()
app = A.App(root)
# 只设工作文件夹：扫描件应当自动跟随、输出应当自动补默认值（i==0 里检查这两条）
app.vars["src"].set(str(src))

fails = []


def chk(n, c, d=""):
    print(f"  {'OK ' if c else 'NG '} {n}" + ("" if c else f"  <{d}>"), flush=True)
    if not c:
        fails.append(n)


def rows_now():
    return [app.tree.item(x)["values"] for x in app.tree.get_children()]


def cols(name):
    """按名字取某一列的取值（列顺序变了也不会让用例跟着烂掉）。"""
    idx = [c[0] for c in A.COLUMNS].index(name)
    return [r[idx] for r in rows_now()]


state = {"i": 0}


def tick():
    if app.busy:
        root.after(120, tick)
        return
    i = state["i"]
    state["i"] += 1

    # ── 0. 默认布局：只选一个文件夹 ─────────────────────────────────────
    if i == 0:
        print("[界面0] 检查①区的默认布局（只选一个文件夹）", flush=True)
        chk("默认就是一个文件夹模式", app.vars["same_dir"].get() is True,
            str(app.vars["same_dir"].get()))
        chk("扫描件那一行被收起来了",
            app.rows["scan"].frame.winfo_manager() == "",
            app.rows["scan"].frame.winfo_manager())
        chk("可见的行只有两行（工作文件夹 + 输出）",
            [k for k in app.rows if app.rows[k].frame.winfo_manager()] == ["src", "out"],
            str([k for k in app.rows if app.rows[k].frame.winfo_manager()]))
        chk("标签换成了「工作文件夹」",
            app.rows["src"].label.cget("text") == "工作文件夹",
            app.rows["src"].label.cget("text"))
        chk("输出框留空 → 自动补「工作文件夹/输出_已替换」",
            app.vars["out"].get() == str(Path(str(src)) / A.AUTO_OUT_NAME),
            app.vars["out"].get())
        chk("文件夹表：只有扫描件那一行会被收起",
            [r.hidden_in_sole for r in A.FOLDER_SPECS] == [False, True, False],
            str([(r.key, r.hidden_in_sole) for r in A.FOLDER_SPECS]))
        chk("两种模式的标签/说明都齐备（不会出现空文案）",
            all(r.label_sole and r.label_split and r.tip_sole and r.tip_split
                for r in A.FOLDER_SPECS),
            str([(r.key, r.label_sole, r.label_split) for r in A.FOLDER_SPECS]))
        chk("扫描件变量悄悄跟着工作文件夹走",
            app.vars["scan"].get() == str(src), app.vars["scan"].get())
        print("[界面A] 去掉勾 → 切回两文件夹模式", flush=True)
        app.vars["same_dir"].set(False)
        app._apply_layout()
        chk("去掉勾后扫描件那一行回来了",
            app.rows["scan"].frame.winfo_manager() == "pack",
            app.rows["scan"].frame.winfo_manager())
        chk("行顺序仍是 原件 → 扫描件 → 输出",
            [k for k in app.rows if app.rows[k].frame.winfo_manager()] == ["src", "scan", "out"],
            str([k for k in app.rows if app.rows[k].frame.winfo_manager()]))
        chk("标签切回「原件文件夹」",
            app.rows["src"].label.cget("text") == "原件文件夹",
            app.rows["src"].label.cget("text"))
        chk("扫描件那一行恢复可编辑",
            str(app.rows["scan"].entry.cget("state")) == "normal"
            and str(app.rows["scan"].button.cget("state")) == "normal")
        # 两文件夹模式的语料：把两个目录各指一个
        app.vars["scan"].set(str(scan))
        app.vars["out"].set(str(out))

    # ── A. 两文件夹模式 ────────────────────────────────────────────────
    if i == 0:
        print("[界面A] 检查 ② 选项区的表驱动与后缀实时预览", flush=True)
        chk("勾选项由选项表驱动（3 个）", len(A.CHECK_SPECS) == 3,
            str([k for k, _, _ in A.CHECK_SPECS]))
        chk("CSV 清单开关已移除", "manifest" not in app.vars, str(sorted(app.vars)))
        chk("每个勾选项都带悬浮说明",
            all(len(tip) > 20 for _k, _l, tip in A.CHECK_SPECS))
        app.vars["suffix"].set("_已签字")
        chk("后缀预览给出拼接后的文件名",
            "_已签字.pdf" in app.lbl_suffix.cget("text"), app.lbl_suffix.cget("text"))
        app.vars["suffix"].set("_已/签\\字")
        txt = app.lbl_suffix.cget("text")
        chk("后缀里的非法字符被剔除并提示",
            "_已签字.pdf" in txt and "已自动去掉" in txt, txt)
        app.vars["suffix"].set("")
        chk("后缀留空时提示保持原名",
            "保持原名" in app.lbl_suffix.cget("text"), app.lbl_suffix.cget("text"))
        app.vars["suffix"].set("_已签字")   # 后面用它验证输出命名
        print("[界面A] 点『扫描并匹配（预览）』", flush=True)
        app.on_preview()
        root.after(120, tick)
    elif i == 1:
        rows = rows_now()
        print(f"[界面A] 表格 {len(rows)} 行：", flush=True)
        for r in rows:
            print("      ", r[0], "|", r[3], "|", r[4], "|", r[6], flush=True)
        chk("预览表格有数据", len(rows) >= 4, str(len(rows)))
        chk("状态栏有统计", "匹配完成" in app.status.cget("text"), app.status.cget("text"))
        chk("『开始替换』已解禁", str(app.btn_run.cget("state")) == "normal")
        stem_real = app.report.pairs[0].src.stem
        chk("后缀示例换成了你的真实文件名",
            stem_real in app.lbl_suffix.cget("text")
            and A.SUFFIX_SAMPLE_FALLBACK not in app.lbl_suffix.cget("text"),
            app.lbl_suffix.cget("text"))
        chk("无对应扫描件被标出", any(r[6] == C.Status.NO_SCAN.value for r in rows))
        chk("多余的扫描件单独列出", any(r[6] == C.Status.EXTRA_SCAN.value for r in rows),
            str([r[6] for r in rows]))
        no_scan_rows = [r for r in rows if r[6] == C.Status.NO_SCAN.value]
        chk("未配对行不再回显自身文件名", all(r[3] == "—" for r in no_scan_rows),
            str([(r[0], r[3]) for r in no_scan_rows]))
        used = [r[3] for r in rows if r[6] == C.Status.MATCHED.value]
        chk("一个扫描件只用一次", len(used) == len(set(used)), str(used))
        print("[界面A] 点『开始替换』", flush=True)
        app.on_run()
        root.after(120, tick)
    elif i == 2:
        from pypdf import PdfReader

        outs = sorted(p.name for p in out.glob("*.pdf"))
        print(f"[界面A] 输出：{outs}", flush=True)
        chk("输出 2 份成品", len(outs) == 2, str(outs))
        chk("输出名带上了后缀", all("_已签字.pdf" in n for n in outs), str(outs))
        a = out / "WI-2101 作业指导书_已签字.pdf"
        chk("A 第1页=扫描页", "SIGNED-A" in page_texts(a)[0])
        chk("A 第2页保留正文", "P2-BODY" in page_texts(a)[1])
        b = out / "GRA-3634 检验规范_已签字.pdf"
        rb = PdfReader(str(b))
        chk("B 扫描件已缩放成 A4", round(float(rb.pages[0].mediabox.width)) == 595,
            str(float(rb.pages[0].mediabox.width)))
        chk("B 横版第2页保留", round(float(rb.pages[1].mediabox.width)) == 842)
        chk("状态栏显示完成", "成功 2" in app.status.cget("text"), app.status.cget("text"))
        print("[界面B] 切到同文件夹模式", flush=True)
        app.vars["src"].set(str(hyb))          # 原件与扫描件都在这一个文件夹里
        app.vars["out"].set(str(hyb_out))
        app.vars["suffix"].set("")
        app.vars["same_dir"].set(True)
        app._apply_layout()
        root.after(60, tick)
    # ── B. 一个文件夹模式 ──────────────────────────────────────────────
    elif i == 3:
        w = app.rows["scan"]
        chk("扫描件那一行自动跟随工作文件夹", app.vars["scan"].get() == str(hyb),
            app.vars["scan"].get())
        chk("跟随之后这一行被收起来了", w.frame.winfo_manager() == "", w.frame.winfo_manager())
        chk("标签换成了「工作文件夹」", app.rows["src"].label.cget("text") == "工作文件夹",
            app.rows["src"].label.cget("text"))
        chk("提示文字换成了看得懂的说明",
            "都放这一个文件夹里" in app.rows["src"].hint.cget("text"),
            app.rows["src"].hint.cget("text"))
        print("[界面B] 点『扫描并匹配（预览）』", flush=True)
        app.on_preview()
        root.after(120, tick)
    elif i == 4:
        n_scan = 3 if Image is not None else 2
        print(f"[界面B] 表格 {len(rows_now())} 行，状态栏：{app.status.cget('text')}", flush=True)
        for r in rows_now():
            print("      ", r[0], "|", r[1], "|", r[3], "|", r[6], "|", r[7][:60], flush=True)
        st = app.status.cget("text")
        chk("状态栏交代了分流结果", "同文件夹分流" in st and "原件 2" in st, st)
        chk("分流结果里扫描件数正确", f"扫描件 {n_scan}" in st, st)
        chk("无法判断的份数也报出来", "无法判断 1" in st, st)
        expect = {"WI-2101 作业指导书.pdf": "已匹配", "GRA-3634 检验规范.pdf": "已匹配",
                  "坏文件.pdf": "无法判断"}
        got = dict(zip(cols("src"), cols("status")))
        chk("每份文件都判到了位", all(got.get(k) == v for k, v in expect.items()), str(got))
        notes = dict(zip(cols("src"), cols("note")))
        chk("低置信的扫描件在说明里点了名并标明是哪一边",
            notes.get("GRA-3634 检验规范.pdf", "").startswith("[扫描件]"),
            notes.get("GRA-3634 检验规范.pdf", ""))
        chk("无法判断的行说明了原因",
            "没有任何页面" in notes.get("坏文件.pdf", ""), notes.get("坏文件.pdf", ""))
        chk("『开始替换』已解禁", str(app.btn_run.cget("state")) == "normal")
        # 右键改判：被当成"多余扫描件"的那一份其实是原件
        target = "拍照的签字页.png" if Image is not None else None
        if target:
            j = next((k for k, s in enumerate(app.report.orphan_scans) if s.path.name == target), None)
            chk("多余扫描件里有那张图片", j is not None,
                str([s.path.name for s in app.report.orphan_scans]))
            if j is not None:
                app.tree.selection_set(f"o{j}")
                chk("界面认得这一行是可改判的",
                    app._selected_orphan() is not None)
                print(f"[界面B] 右键把「{target}」改判成原件", flush=True)
                app.on_force_original()
        root.after(120, tick)
    elif i == 5:
        if Image is not None:
            st = app.status.cget("text")
            got = dict(zip(cols("src"), cols("status")))
            chk("改判后重新匹配，状态栏写明有人工改判",
                "含人工改判 1 份" in st, st)
            chk("改判为原件的那份不再算多余扫描件",
                all(s.path.name != "拍照的签字页.png" for s in app.report.orphan_scans),
                str([s.path.name for s in app.report.orphan_scans]))
            chk("它变成「无对应扫描件」（原件没配上扫描件）",
                got.get("拍照的签字页.png") == C.Status.NO_SCAN.value, str(got))
            chk("分流统计里它已被算作原件", "原件 3" in st, st)
        print("[界面B] 点『开始替换』", flush=True)
        app.on_run()
        root.after(120, tick)
    elif i == 6:
        outs = sorted(p.name for p in hyb_out.glob("*.pdf"))
        print(f"[界面B] 输出：{outs}", flush=True)
        chk("同文件夹模式跑出 2 份成品", len(outs) == 2, str(outs))
        a = hyb_out / "WI-2101 作业指导书.pdf"
        chk("成品首页换成了同文件夹里的扫描页",
            a.exists() and "SIGNED-A" in page_texts(a)[0],
            page_texts(a)[0] if a.exists() else "文件不存在")
        chk("原封面已被替换", a.exists() and "P1-COVER" not in " ".join(page_texts(a)))
        chk("保存的成品没有被当成下一轮的输入",
            all("作业指导书_2" not in n for n in outs), str(outs))
        chk("状态栏显示完成", "成功 2" in app.status.cget("text"), app.status.cget("text"))
        root.destroy()
    else:
        root.destroy()


root.after(200, tick)
root.mainloop()
print("\n界面端到端：" + ("全部通过" if not fails else "失败 " + "、".join(fails)), flush=True)
sys.exit(1 if fails else 0)
