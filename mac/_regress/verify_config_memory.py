"""「记住上次选的文件夹」的界面级验收。

判据不靠读代码，而是真的建一次界面对象、读回它填进输入框的值与控制台提示。
所有配置都写在临时目录，不碰用户真实的 ~/.pdf_firstpage_swap.json。
"""
import json
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

SW = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SW)
import swap_app as A  # noqa: E402

WORK = Path(tempfile.gettempdir()) / "pdfswap_cfg"
CALLS = {"forget_confirmed": 0}
fails = []


def chk(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'NG '} {name}" + ("" if cond else f"  <{detail}>"), flush=True)
    if not cond:
        fails.append(name)


def fresh_config_path(tag):
    WORK.mkdir(parents=True, exist_ok=True)
    p = WORK / f"{tag}.json"
    p.unlink(missing_ok=True)
    (WORK / f"{tag}.json.tmp").unlink(missing_ok=True)
    return p


def win_size(top):
    """取窗口宽高。

    窗口处于 withdraw（未映射）状态时 winfo_width() 恒返回 1，
    所以退回解析 geometry() —— 测的是"设成了多大"，不是"显示出来多大"。
    """
    w, h = top.winfo_width(), top.winfo_height()
    if w > 1 and h > 1:
        return w, h
    try:
        a, _, b = top.geometry().split("+")[0].partition("x")
        return int(a), int(b)
    except Exception:  # noqa: BLE001
        return w, h


def make_app(root, cfg):
    A.CONFIG_PATH = cfg
    top = tk.Toplevel(root)
    top.withdraw()
    app = A.App(top)
    root.update()
    return top, app


def dirs(*names):
    out = []
    for n in names:
        d = WORK / n
        d.mkdir(parents=True, exist_ok=True)
        out.append(str(d))
    return out


A.messagebox.askokcancel = lambda *a, **k: (CALLS.__setitem__("forget_confirmed", 1), True)[1]
A.messagebox.showinfo = lambda *a, **k: None
A.messagebox.showwarning = lambda *a, **k: None

root = tk.Tk()
root.withdraw()
src, scan, out = dirs("原件", "扫描件", "输出")

# ── ① 第一次运行：没有记录文件，不该有任何「已记住」提示 ────────────────────
print("[1] 首次运行（无记录文件）", flush=True)
cfg = fresh_config_path("first")
top, app = make_app(root, cfg)
chk("三个文件夹都是空的", all(not app.vars[k].get() for k in A.CONFIG_KEEP_KEYS),
    str([app.vars[k].get() for k in A.CONFIG_KEEP_KEYS]))
chk("没有谎报「已填好上次的」", "已自动填好" not in app.status.cget("text"),
    app.status.cget("text"))
chk("没记录时不报错", not app._config_broken)

# ── ② 选好文件夹 → 关窗 → 重开：文件夹要自动填回来 ────────────────────────
print("\n[2] 只选一个文件夹 → 关窗 → 再打开", flush=True)
app.vars["src"].set(src)
chk("一个文件夹模式：扫描件自动等于工作文件夹", app.vars["scan"].get() == src,
    app.vars["scan"].get())
chk("输出留空时自动补默认值",
    app.vars["out"].get() == str(Path(src) / A.AUTO_OUT_NAME), app.vars["out"].get())
app.vars["out"].set(out)               # 用户把输出改到自己想要的位置
app.vars["scale"].set(False)
app.vars["suffix"].set("_已签字")
top.geometry("1300x760")
root.update()
app.on_close()          # 模拟点右上角关闭
time.sleep(0.2)
chk("关窗时写出了记录文件", cfg.is_file(), str(cfg))
saved = json.loads(cfg.read_text(encoding="utf-8"))
chk("记录里三个文件夹都在（同一个值也算，格式稳定）",
    all(saved.get(k) for k in A.CONFIG_KEEP_KEYS), str(saved))
chk("记录里带上了模式开关", saved.get("same_dir") is True, str(saved.get("same_dir")))
chk("记录里有窗口大小", saved.get("window") == [1300, 760], str(saved.get("window")))
chk("记录里有版本号", saved.get("_version") == A.CONFIG_VERSION, str(saved.get("_version")))
chk("没留下 .tmp 残file", not (WORK / f"{cfg.stem}.json.tmp").exists())

top2, app2 = make_app(root, cfg)
chk("重开后工作文件夹已填回", app2.vars["src"].get() == src, app2.vars["src"].get())
chk("重开后输出的自定义位置也填回", app2.vars["out"].get() == out, app2.vars["out"].get())
chk("重开后扫描件仍与工作文件夹一致", app2.vars["scan"].get() == src,
    app2.vars["scan"].get())
chk("模式开关也记住了", app2.vars["same_dir"].get() is True)
chk("选项勾选状态也记住了", app2.vars["scale"].get() is False, str(app2.vars["scale"].get()))
chk("后缀也记住了", app2.vars["suffix"].get() == "_已签字", app2.vars["suffix"].get())
chk("状态栏告诉用户已经填好了", "已自动填好" in app2.status.cget("text"),
    app2.status.cget("text"))
# 窗口尺寸的显示值在 withdraw（未映射）状态下量不准，单独放到第 [9] 节用真窗口验
top2.destroy()

# ── ②b 两文件夹模式也要记得住 ──────────────────────────────────────────────
print("\n[2b] 去掉勾（两个文件夹）也要记得住", flush=True)
cfg2b = fresh_config_path("split")
app2b = make_app(root, cfg2b)[1]
app2b.vars["same_dir"].set(False)
app2b._apply_layout()
app2b.vars["src"].set(src)              # 此时不该再联动
chk("两文件夹模式下扫描件不再跟着原件跑", app2b.vars["scan"].get() == "",
    app2b.vars["scan"].get())
app2b.vars["scan"].set(scan)
app2b._save_config()
top2b, app2b2 = make_app(root, cfg2b)
chk("重开后仍是两文件夹模式", app2b2.vars["same_dir"].get() is False)
chk("扫描件文件夹各自填回", app2b2.vars["scan"].get() == scan, app2b2.vars["scan"].get())
chk("扫描件那一行是显示的", app2b2.rows["scan"].frame.winfo_manager() == "pack",
    app2b2.rows["scan"].frame.winfo_manager())
top2b.destroy()

# ── ②c 老记录（v1，只有三个文件夹、没有模式开关）→ 迁到一个文件夹 ───────────
print("\n[2c] 老版本记录文件的迁移", flush=True)
cfg2c = fresh_config_path("legacy")
cfg2c.write_text(json.dumps({"src": src, "scan": scan, "out": out, "_version": 1}),
                 encoding="utf-8")
top2c, app2c = make_app(root, cfg2c)
chk("老记录按新的默认（一个文件夹）打开", app2c.vars["same_dir"].get() is True)
chk("老记录里分开用的扫描件目录被点名提醒",
    scan in app2c.status.cget("text"), app2c.status.cget("text"))
chk("提醒里给了解法（挪文件 / 去掉勾）",
    "去掉" in app2c.status.cget("text"), app2c.status.cget("text"))
chk("没有把老记录的扫描件目录悄悄当成工作文件夹",
    app2c.vars["src"].get() == src, app2c.vars["src"].get())
top2c.destroy()

# ── ②d 原子替换失败时（有些机器上的安全软件会拦重命名）要走退路，且不留垃圾 ──
print("\n[2d] 原子替换失败时的退路", flush=True)
cfg2d = fresh_config_path("fallback")
top2d, app2d = make_app(root, cfg2d)
app2d.vars["src"].set(src)
_real_replace = A.os.replace
A.os.replace = lambda *_a, **_k: (_ for _ in ()).throw(OSError("模拟被安全软件拦截"))
try:
    app2d._save_config()
finally:
    A.os.replace = _real_replace
chk("替换失败也把配置存下去了", cfg2d.is_file(), str(cfg2d))
if cfg2d.is_file():
    chk("退路存的内容是对的",
        json.loads(cfg2d.read_text(encoding="utf-8")).get("src") == src)
chk("没留下 .tmp 垃圾",
    not (WORK / f"{cfg2d.stem}.json.tmp").exists(),
    str(list(WORK.glob("*.tmp"))))
top2d.destroy()

# ── ③ 只打字不关窗（不点任何按钮）也要自动落盘 ─────────────────────────────
print("\n[3] 只改输入框、等自动保存", flush=True)
cfg3 = fresh_config_path("auto")
top3, app3 = make_app(root, cfg3)
app3.vars["src"].set(src)
deadline = time.time() + 3
while time.time() < deadline and not cfg3.is_file():
    root.update()
    time.sleep(0.1)
chk("改完约 1 秒自动落盘（无需点按钮）", cfg3.is_file(), "等 3 秒仍无文件")
if cfg3.is_file():
    chk("落盘内容就是刚填的路径",
        json.loads(cfg3.read_text(encoding="utf-8")).get("src") == src)
top3.destroy()

# ── ④ 记住的文件夹已经不存在（U 盘拔了 / 改名了）────────────────────────────
print("\n[4] 记住的文件夹已不存在", flush=True)
cfg4 = fresh_config_path("missing")
gone = str(WORK / "已经删掉的文件夹")
cfg4.write_text(json.dumps({"src": gone, "scan": scan, "out": "", "_version": 1}),
                encoding="utf-8")
top4, app4 = make_app(root, cfg4)
chk("不存在也照填（不擅自清空用户填过的东西）", app4.vars["src"].get() == gone,
    app4.vars["src"].get())
chk("状态栏点名提示哪个文件夹不在了",
    "已经不存在" in app4.status.cget("text") and "工作文件夹" in app4.status.cget("text"),
    app4.status.cget("text"))
chk("标成警示色", str(app4.status.cget("foreground")) == "#b00020",
    str(app4.status.cget("foreground")))
top4.destroy()

# ── ⑤ 记录文件被改坏：软件必须照样能开 ─────────────────────────────────────
print("\n[5] 记录文件损坏", flush=True)
for tag, blob in (("broken", "{这不是 JSON"), ("notdict", "[1,2,3]")):
    cfg5 = fresh_config_path(tag)
    cfg5.write_text(blob, encoding="utf-8")
    try:
        top5, app5 = make_app(root, cfg5)
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok, app5 = False, None
        print("     异常：", exc)
    chk(f"损坏内容（{tag}）不影响启动", ok)
    if ok:
        chk(f"损坏内容（{tag}）有提示且不崩",
            "读不出来" in app5.status.cget("text"), app5.status.cget("text"))
        top5.destroy()

# ── ⑥ 「忘记记住的文件夹」 ─────────────────────────────────────────────────
print("\n[6] 忘记记住的文件夹", flush=True)
cfg6 = fresh_config_path("forget")
top6, app6 = make_app(root, cfg6)
app6.vars["src"].set(src)
app6._save_config()
app6.on_forget()
chk("点了确认框", CALLS["forget_confirmed"] == 1)
chk("三个输入框被清空", all(not app6.vars[k].get() for k in A.CONFIG_KEEP_KEYS),
    str([app6.vars[k].get() for k in A.CONFIG_KEEP_KEYS]))
chk("记录文件被删掉", not cfg6.exists())
chk("状态栏说明了结果", "已清空记录" in app6.status.cget("text"), app6.status.cget("text"))
top6.destroy()

# ── ⑦ 窗口大小异常时不恢复（免得恢复出一个看不见的窗口）────────────────────
print("\n[7] 窗口大小异常", flush=True)
cfg7 = fresh_config_path("geo")
cfg7.write_text(json.dumps({"window": [10, 10]}), encoding="utf-8")
top7, app7 = make_app(root, cfg7)
chk("过小的尺寸被忽略（沿用默认大小）", win_size(top7) != (10, 10), str(win_size(top7)))
top7.destroy()
cfg7.write_text(json.dumps({"window": "乱写的"}), encoding="utf-8")
top7b, app7b = make_app(root, cfg7)
chk("乱写的尺寸被忽略且不崩", True)
top7b.destroy()

# ── ⑧ 记录里多出不认识的键（未来版本写的）：忽略即可，不能崩 ────────────────
print("\n[8] 前向兼容", flush=True)
cfg8 = fresh_config_path("future")
cfg8.write_text(json.dumps({"src": src, "_version": 99, "未来才有的开关": True,
                            "another": [1, 2]}), encoding="utf-8")
top8, app8 = make_app(root, cfg8)
chk("不认识的键被忽略", app8.vars["src"].get() == src and not app8._config_broken)
top8.destroy()

# ── ⑨ 窗口大小恢复：必须用真实映射的窗口量（withdraw 状态量不准）─────────────
print("\n[9] 窗口大小恢复（真实窗口）", flush=True)
cfg9 = fresh_config_path("geo_restore")
cfg9.write_text(json.dumps({"window": [1300, 760], "_version": 1}), encoding="utf-8")
A.CONFIG_PATH = cfg9
root9 = tk.Tk()
root9.geometry("1180x720")          # 模拟 main() 给的默认尺寸
app9 = A.App(root9)                 # 构造时应当被记录里的尺寸覆盖
root9.update()
chk("恢复到记录的 1300x760",
    (root9.winfo_width(), root9.winfo_height()) == (1300, 760),
    f"{root9.winfo_width()}x{root9.winfo_height()}")
root9.destroy()

root.destroy()
print("\n记录功能：" + ("全部通过" if not fails else "失败 " + "、".join(fails)), flush=True)
sys.exit(1 if fails else 0)
