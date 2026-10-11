"""PDF 首页替换工具 —— 界面层（tkinter）

流程：选 1 个文件夹（原件与签字扫描件放一起）→ 点「扫描并匹配」看预览 →
      确认无误 → 点「开始替换」。想分成两个文件夹也行（去掉①区最上面那个勾）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).parent))
import swap_core as C  # noqa: E402

APP_TITLE = f"{C.TOOL_NAME}  v{C.TOOL_VERSION}"
CONFIG_PATH = Path.home() / ".pdf_firstpage_swap.json"
CONFIG_VERSION = 2
CONFIG_KEEP_KEYS = ("src", "scan", "out")   # 下次打开要自动填回去的三个文件夹
# 输出文件夹留空时，自动用「工作文件夹」下的这个子目录
# （核心扫描时会把它排除掉，所以放在工作文件夹里面是安全的）
AUTO_OUT_NAME = "输出_已替换"
ROW_PAD = {"padx": 4, "pady": 3}
# 关窗时记住窗口大小；恢复时低于这个尺寸就不恢复（免得恢复出一个看不见的窗口）
GEOM_MIN = (980, 620)
# 改动后隔多久落盘一次（打字/连续勾选只写一次，别每敲一个字就写文件）
SAVE_DEBOUNCE_MS = 900

COLUMNS: tuple[tuple[str, str, int, str], ...] = (
    ("src", "原件", 250, "w"),
    ("pages", "页数", 48, "center"),
    ("size", "首页尺寸", 86, "center"),
    ("scan", "扫描件（签署首页）", 250, "w"),
    ("strategy", "匹配方式", 76, "center"),
    ("score", "相似度", 56, "center"),
    ("status", "状态", 84, "center"),
    ("note", "说明", 220, "w"),
)

NEED_COLOR = {"冲突待定": "#b8860b", "无对应扫描件": "#b00020",
              "多余扫描件": "#8a6d00", "无法判断": "#8a6d00",
              "替换失败": "#b00020", "替换成功": "#1a7f37"}

class FolderRow(NamedTuple):
    """① 区每一行的定义 —— 加一行/改文案只动这张表。

    label_sole / tip_sole   —— 「一个文件夹」模式（默认）下的标签与右侧小字
    label_split / tip_split —— 「分成两个文件夹」时的标签与右侧小字
    hidden_in_sole          —— True = 一个文件夹模式下不显示这一行（值自动跟随 src）
    """

    key: str
    label_sole: str
    label_split: str
    tip_sole: str
    tip_split: str
    hidden_in_sole: bool = False


class RowWidgets(NamedTuple):
    """一行文件夹对应的控件 —— 模式切换时按名字取，不靠下标。"""

    frame: ttk.Frame
    label: ttk.Label
    entry: ttk.Entry
    button: ttk.Button
    hint: ttk.Label


FOLDER_SPECS: tuple[FolderRow, ...] = (
    FolderRow(
        "src",
        "工作文件夹",
        "原件文件夹",
        "没签字的原件 + 签好字扫回来的首页，都放这一个文件夹里",
        "没签字的原件（Excel 等转换来的 PDF）",
    ),
    FolderRow(
        "scan",
        "扫描件文件夹",
        "扫描件文件夹",
        "一个文件夹模式下用不到这一行（原件和扫描件在同一个文件夹里）",
        "签字后扫描上传的首页（PDF 或图片均可）",
        hidden_in_sole=True,
    ),
    FolderRow(
        "out",
        "输出文件夹",
        "输出文件夹",
        f"留空自动用「工作文件夹/{AUTO_OUT_NAME}」",
        f"留空自动用「原件文件夹/{AUTO_OUT_NAME}」",
    ),
)
LABELS_SOLE = {r.key: r.label_sole for r in FOLDER_SPECS}
LABELS_SPLIT = {r.key: r.label_split for r in FOLDER_SPECS}

# 一个文件夹模式（⑨ 分流）的说明 —— 这是"结构"层面的选择，所以放在 ① 区
SAME_DIR_TIP = (
    "默认就是这一个模式：原件和签字扫描件混在一个文件夹里，一个文件夹就够。\n"
    "工具先按【页数】把谁是谁分出来：\n"
    "    · 页数 ≥2 页      → 原件\n"
    "    · 只有 1 页 / 图片 → 签字扫描上来的那一页\n"
    "    · 读不出页数      → 单独列成一个「无法判断」行，绝不猜\n"
    "再按【文件名相似度】把两边一一配上（编号相同的一律直接命中）。\n\n"
    "为什么分角色只认页数、不认文件名：你的原件名字本身就带「- 签字版」这类词，\n"
    "光看名字会把整批原件错认成扫描件。那些词只用来写依据，不参与判定。\n\n"
    "分不清的那几份会在表格「说明」列点名，右键还能「改判成原件」后重新匹配。\n\n"
    "什么时候该去掉这个勾：扫描件不止一页（比如一份文件扫了多张、或者一份文件里\n"
    "有多张签署页），这时页数不再能说明问题，改成两个文件夹分开指更稳。"
)

# ── 选项表：勾选项的「标签 + 悬浮说明」都从这里取，加一个开关只改这里 ──────────
#    顺序即界面顺序；第一个是用户默认关心的主开关。
CHECK_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "scale", "扫描页缩放到原件首页尺寸",
        "默认开。扫描件比原件大还是小都无所谓，会等比缩放并居中。\n\n"
        "例外：两边的“版面比例”差太多时（例如原件封面是横向 A4、\n"
        "你扫上来的是纵向 A4），不会硬塞进去——会原样保留扫描页，\n"
        "并在表格“说明”列写明原因，免得两旁留一大片白边。\n\n"
        "关掉则一律原样保留扫描件尺寸（同一份 PDF 里页面大小可能不一致）。",
    ),
    (
        "overwrite", "覆盖输出文件夹里的同名文件",
        "管的是“输出目录里已经有同名 PDF 怎么办”。默认关。\n\n"
        "· 不勾（推荐）：绝不覆盖任何已有文件，遇到重名自动加编号，\n"
        "  输出成 xxx_2.pdf、xxx_3.pdf，并在“说明”列写明。\n"
        "· 勾上：直接覆盖同名文件，旧内容被新版替换掉，不可恢复。\n\n"
        "注意：这条只管输出目录，原始 PDF 文件夹任何时候都只读、不会被改。",
    ),
    (
        "recursive", "连同子文件夹一起处理",
        "默认关，只处理你选的那一层文件夹。\n\n"
        "勾上 → 原始/扫描件文件夹里的子文件夹也会一起扫描。\n"
        "输出的 PDF 会复刻相同的子目录结构（例如 原件/2026-09/ 里的文件\n"
        "输出到 输出/2026-09/），避免不同子目录的同名文件互相打架。",
    ),
)

# 后缀输入框的悬浮说明 + 推荐写法
SUFFIX_TIP = (
    "给输出文件的名字加一个字尾，方便和原始文件区分开。\n\n"
    "例：原名 WI-2101,rev13 作业指导书.pdf\n"
    "    填 _已签字首页 → WI-2101,rev13 作业指导书_已签字首页.pdf\n"
    "    留空         → WI-2101,rev13 作业指导书.pdf（与原名一致）\n\n"
    "· 只改文件名，不影响内容；填什么就是什么，界面会把最终名字实时显示出来。\n"
    "· 路径分隔符 / \\ 等非法字符会被自动去掉。\n"
    "· 建议以 _ 或 - 开头，例如 _已替换、-signed。\n"
    "· 最长 40 个字符。"
)

SUFFIX_SAMPLE_FALLBACK = "WI-2101,rev13 作业指导书"


class Tip:
    """极简悬浮提示：鼠标停留片刻弹出，移开或点击即消失。

    不引入任何第三方依赖，给用户解释那些一句话说不清的开关。
    """

    def __init__(self, widget: tk.Widget, text: str, delay: int = 350, width: int = 520) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.width = width
        self._after: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e: tk.Event | None = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:  # noqa: BLE001
                pass
            self._after = None

    def _show(self) -> None:
        if self._win or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._win = tk.Toplevel(self.widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._win, text=self.text, justify="left", anchor="w",
            background="#ffffe1", foreground="#222", relief="solid", borderwidth=1,
            wraplength=self.width, padx=10, pady=8,
        ).pack()

    def _hide(self, _e: tk.Event | None = None) -> None:
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._win = None


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.report: C.Report | None = None
        self.busy = False
        self.rows: dict[str, RowWidgets] = {}
        self._forced: dict[str, str] = {}        # ⑨ 人工改判：{文件绝对路径: 角色}
        self._save_after: str | None = None      # 延迟落盘的定时器
        self._config_broken = False              # 上次的记录文件读不出来
        self._restored: list[str] = []           # 这次自动填回来的字段
        self._missing_dirs: list[str] = []       # 填回来了、但文件夹已经不在了
        self._legacy_scan = ""                   # 上次分开用的扫描件文件夹（改成一个文件夹后提示用）
        self.vars = {
            "src": tk.StringVar(),
            "scan": tk.StringVar(),
            "out": tk.StringVar(),
            # ⑨ 默认就是"一个文件夹"：原件与签字扫描件混在一个文件夹里，按页数自动分
            "same_dir": tk.BooleanVar(value=True),
            "scale": tk.BooleanVar(value=True),
            "overwrite": tk.BooleanVar(value=False),
            "recursive": tk.BooleanVar(value=False),
            "threshold": tk.DoubleVar(value=0.80),
            "suffix": tk.StringVar(value=""),
        }
        self._build()
        self._load_config()
        # 后缀实时预览：一边打字一边看到最终文件名
        self.vars["suffix"].trace_add("write", lambda *_a: self._refresh_suffix_hint())
        self._refresh_suffix_hint()
        # 工作文件夹一变：一个文件夹模式下扫描件跟着走；输出留空时也顺手补上
        self.vars["src"].trace_add("write", lambda *_a: self._on_src_changed())
        self._apply_layout()
        # 任何改动都会在 0.9 秒后落盘 —— 所以「选完文件夹直接关窗」也记得住
        for var in self.vars.values():
            var.trace_add("write", lambda *_a: self._schedule_save())
        # 点右上角关闭也要先保存（preview/run 时也存，但用户可能什么都不点就关了）
        self.winfo_toplevel().protocol("WM_DELETE_WINDOW", self.on_close)
        self._announce_restore()

    # ── 界面搭建 ─────────────────────────────────────────────────────────
    def _build(self) -> None:
        box = ttk.LabelFrame(
            self, text="① 文件夹（只选一个就行，会自动记住）", padding=6)
        box.pack(fill="x")
        # ⑨ 默认开。这是"结构"层面的选择，所以放在文件夹区最上面：
        #    关掉它，「扫描件」那一行才会出现 —— 平时根本不用管它。
        cb_same = ttk.Checkbutton(
            box, text="原件和扫描件都放在同一个文件夹（默认，按页数自动分）",
            variable=self.vars["same_dir"], command=self._apply_layout)
        cb_same.pack(anchor="w", pady=(0, 4))
        Tip(cb_same, SAME_DIR_TIP)
        # 行按 FOLDER_SPECS 建，但先不 pack —— 由 _apply_layout 决定谁出现、什么顺序
        for spec in FOLDER_SPECS:
            row = ttk.Frame(box)
            lbl = ttk.Label(row, text=spec.label_sole, width=14, anchor="w")
            lbl.pack(side="left")
            ent = ttk.Entry(row, textvariable=self.vars[spec.key])
            ent.pack(side="left", fill="x", expand=True, padx=(0, 4))
            btn = ttk.Button(row, text="浏览…", width=8,
                             command=lambda k=spec.key: self._pick_dir(k))
            btn.pack(side="left")
            hint = ttk.Label(row, text=spec.tip_sole, foreground="#777")
            hint.pack(side="left", padx=(8, 0))
            self.rows[spec.key] = RowWidgets(row, lbl, ent, btn, hint)

        opt = ttk.LabelFrame(self, text="② 选项（鼠标停在文字上会弹出说明）", padding=6)
        opt.pack(fill="x", pady=(6, 0))
        l1 = ttk.Frame(opt)
        l1.pack(fill="x")
        for key, label, tip in CHECK_SPECS:
            cb = ttk.Checkbutton(l1, text=label, variable=self.vars[key])
            cb.pack(side="left", padx=(0, 14))
            Tip(cb, tip)
        l2 = ttk.Frame(opt)
        l2.pack(fill="x", pady=(4, 0))
        ttk.Label(l2, text="输出文件名后缀：").pack(side="left")
        ent_suffix = ttk.Entry(l2, textvariable=self.vars["suffix"], width=16)
        ent_suffix.pack(side="left", padx=(0, 10))
        Tip(ent_suffix, SUFFIX_TIP)
        self.lbl_suffix = ttk.Label(l2, text="", foreground="#1a5fb4")
        self.lbl_suffix.pack(side="left", padx=(0, 18))
        ttk.Label(l2, text="相似度门槛：").pack(side="left")
        ttk.Scale(l2, from_=0.5, to=1.0, variable=self.vars["threshold"], length=140,
                  command=lambda _v: self.lbl_thr.config(text=f"{self.vars['threshold'].get():.2f}")
                  ).pack(side="left", padx=(0, 4))
        self.lbl_thr = ttk.Label(l2, text="0.80", width=5)
        self.lbl_thr.pack(side="left")
        ttk.Label(l2, text="（编号相同的一律直接命中，不受此门槛影响）",
                  foreground="#777").pack(side="left", padx=(8, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 4))
        self.btn_scan = ttk.Button(bar, text="扫描并匹配（预览）", command=self.on_preview)
        self.btn_scan.pack(side="left")
        self.btn_run = ttk.Button(bar, text="开始替换", command=self.on_run, state="disabled")
        self.btn_run.pack(side="left", padx=6)
        ttk.Button(bar, text="打开输出文件夹", command=self.on_open_out).pack(side="left", padx=6)
        btn_forget = ttk.Button(bar, text="忘记记住的文件夹", command=self.on_forget)
        btn_forget.pack(side="left", padx=6)
        Tip(btn_forget, "清空①里的文件夹，并删掉记录文件。\n"
                        "下次打开软件就不会再自动填写了。\n"
                        f"记录文件位置：{CONFIG_PATH}")
        ttk.Button(bar, text="使用说明", command=self.on_help).pack(side="right")

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLUMNS], show="headings", height=14)
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key in ("src", "scan", "note")))
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        for st, color in NEED_COLOR.items():
            self.tree.tag_configure(st, foreground=color)
        self.tree.tag_configure("odd", background="#f5f6f8")
        self.tree.bind("<Double-1>", self.on_row_double)
        self.tree.bind("<Button-2>", self._popup)
        self.tree.bind("<Button-3>", self._popup)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="手动指定扫描件…", command=self.on_assign)
        self.menu.add_command(label="忽略此行（不处理）", command=self.on_ignore)
        # ⑨ 只在同文件夹模式、且选中的是"多余扫描件"那一行时可用
        self.menu.add_command(label="这份其实是原件（改判后重新匹配）",
                              command=self.on_force_original)
        self.idx_force = self.menu.index("end")
        self.menu.add_separator()
        self.menu.add_command(label="在访达/资源管理器中显示原件", command=self.on_reveal)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(6, 0))
        self.pb = ttk.Progressbar(bottom, mode="determinate", length=260)
        self.pb.pack(side="left")
        self.status = ttk.Label(bottom, text="就绪：先选好文件夹，点「扫描并匹配」", foreground="#333")
        self.status.pack(side="left", padx=10)

    # ── ⑨ 一个文件夹 / 两个文件夹 两种模式的切换 ─────────────────────────
    def _label(self, key: str) -> str:
        """当前模式下某一行的叫法（给提示语用）。"""
        return (LABELS_SOLE if self.vars["same_dir"].get() else LABELS_SPLIT)[key]

    @staticmethod
    def _same_dir_as(a: object, b: object) -> bool:
        """两个路径是不是同一个文件夹（配置里的老值不保证是字符串）。"""
        try:
            if not str(a).strip() or not str(b).strip():
                return False
            return Path(str(a)).resolve() == Path(str(b)).resolve()
        except Exception:  # noqa: BLE001
            return False

    def _on_src_changed(self) -> None:
        """工作文件夹改了：一个文件夹模式下扫描件跟着走；输出留空就顺手补默认值。"""
        if self.vars["same_dir"].get():
            self.vars["scan"].set(self.vars["src"].get())
        self._autofill_out()

    def _autofill_out(self) -> None:
        """输出文件夹留空时给个默认值 —— 用户只需要选「一个文件夹」。

        默认放在工作文件夹里面：核心扫描时会把输出目录排除掉，
        所以上一轮的成品绝不会被当成原件再吃回来。
        """
        if self.vars["out"].get().strip():
            return
        src = self.vars["src"].get().strip()
        if src:
            self.vars["out"].set(str(Path(src) / AUTO_OUT_NAME))

    def _apply_layout(self) -> None:
        """按当前模式重排 ① 区 —— 谁显示、什么文案、什么顺序，全由 FOLDER_SPECS 决定。

        一个文件夹模式：隐藏「扫描件」那一行（值自动等于工作文件夹）。
        两个文件夹模式：三行都在，扫描件恢复可编辑。
        """
        sole = bool(self.vars["same_dir"].get())
        if sole:
            self._on_src_changed()
        elif self._same_dir_as(self.vars["scan"].get(), self.vars["src"].get()):
            # 刚从一个文件夹切回来：别让「扫描件目录」还指着工作文件夹，
            # 那样等于把原件也当成扫描件一起配，结果会悄悄出错。
            self.vars["scan"].set("")
        for spec in FOLDER_SPECS:
            w = self.rows[spec.key]
            w.frame.pack_forget()                 # 先全部撤下，再按表顺序重排
            if sole and spec.hidden_in_sole:
                continue
            w.label.config(text=self._label(spec.key))
            w.hint.config(text=spec.tip_sole if sole else spec.tip_split, foreground="#777")
            w.entry.config(state="normal")
            w.button.config(state="normal")
            w.frame.pack(fill="x", **ROW_PAD)

    # ── 配置持久化：记住上次选的文件夹 ────────────────────────────────────
    #    存在 ~/.pdf_firstpage_swap.json 里（就是个 JSON 小文件，不是程序）。
    #    原则：**配置坏了绝不能让软件打不开** —— 读失败就当成第一次运行。
    def _load_config(self) -> None:
        if not CONFIG_PATH.is_file():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  被改坏了 / 写到一半断电
            self._config_broken = True
            return
        if not isinstance(data, dict):
            self._config_broken = True
            return

        # 老版本（v1）没写过 same_dir 这个键 —— 那时是"三个文件夹"的老布局。
        # 现在默认改成"一个文件夹"，但上次分开用的那个扫描件文件夹不能装看不见：
        # 记下来，启动提示里点一句名，让用户自己决定是把文件挪过来还是把勾去掉。
        legacy_split = "same_dir" not in data and bool(str(data.get("scan", "")).strip())

        for k, v in data.items():
            if k in self.vars:          # 不认识的键直接忽略（版本升级后不会炸）
                try:
                    self.vars[k].set(v)
                except Exception:  # noqa: BLE001  类型对不上就跳过这一项
                    pass

        if legacy_split and not self._same_dir_as(data.get("src", ""), data.get("scan", "")):
            self._legacy_scan = str(data["scan"])
            self.vars["same_dir"].set(True)
            self.vars["scan"].set("")

        # 三个文件夹：记着、但文件夹已经不在（U 盘拔了 / 改名了）要说清楚
        for key in CONFIG_KEEP_KEYS:
            if key == "scan" and self.vars["same_dir"].get():
                continue            # 一个文件夹模式下扫描件不是独立的一项，别重复报
            val = self.vars[key].get().strip()
            if not val:
                continue
            self._restored.append(self._label(key))
            if not Path(val).is_dir():
                self._missing_dirs.append(self._label(key))

        # 窗口大小：只恢复合理范围内的，避免恢复出一个看不见的窗口
        geo = data.get("window")
        if isinstance(geo, (list, tuple)) and len(geo) == 2:
            try:
                w, h = int(geo[0]), int(geo[1])
                top = self.winfo_toplevel()
                if (GEOM_MIN[0] <= w <= top.winfo_screenwidth()
                        and GEOM_MIN[1] <= h <= top.winfo_screenheight()):
                    top.geometry(f"{w}x{h}")
            except Exception:  # noqa: BLE001
                pass

    def _save_config(self) -> None:
        """先写临时文件再原子替换 —— 中途断电也不会留下半个坏文件。

        原子替换失败时退一步直接写：有些电脑上的安全软件会拦「重命名/替换」，
        与其默默记不住，不如用差一点的方式把配置存下去。两次都失败就算了，
        绝不能因为存配置这种事影响用户干活。
        """
        self._cancel_save()
        top = self.winfo_toplevel()
        data: dict[str, object] = {k: v.get() for k, v in self.vars.items()}
        data["_version"] = CONFIG_VERSION
        try:
            data["window"] = [top.winfo_width(), top.winfo_height()]
        except Exception:  # noqa: BLE001
            pass
        text = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return
        except Exception:  # noqa: BLE001  替换不成，走下面的退路
            pass
        try:
            tmp.unlink(missing_ok=True)      # 收拾干净：别在用户主目录留个 .tmp 垃圾
        except Exception:  # noqa: BLE001
            pass
        try:
            CONFIG_PATH.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001  存不下就算了，不能影响用
            pass

    def _schedule_save(self) -> None:
        """改一下等 0.9 秒再写盘；连续改只写最后一次。"""
        self._cancel_save()
        self._save_after = self.after(SAVE_DEBOUNCE_MS, self._save_config)

    def _cancel_save(self) -> None:
        if self._save_after:
            try:
                self.after_cancel(self._save_after)
            except Exception:  # noqa: BLE001
                pass
            self._save_after = None

    def _announce_restore(self) -> None:
        """启动时告诉用户「我记住了什么」，包括记住了但已经不存在的那些。"""
        if self._config_broken:
            self.status.config(text="上次的记录文件读不出来（已忽略，不影响使用）", foreground="#b00020")
        elif self._missing_dirs:
            self.status.config(
                text="已填好上次的文件夹，但「" + "、".join(self._missing_dirs)
                     + "」已经不存在了，请重新选一下",
                foreground="#b00020")
        elif self._legacy_scan:
            self.status.config(
                text=f"现在是「一个文件夹」模式，只看：{self.vars['src'].get()}"
                     f"　——　上次分开用的「{self._legacy_scan}」如果里面还有扫描件，"
                     f"把它们挪进同一个文件夹；想继续分开放，去掉①区最上面那个勾",
                foreground="#8a6d00")
        elif self._restored:
            self.status.config(
                text="已自动填好上次用的「" + "、".join(self._restored) + "」，直接点『扫描并匹配』即可",
                foreground="#1a5c2a")

    def on_forget(self) -> None:
        """忘掉记住的文件夹：清空输入框并删掉记录文件。"""
        if self._restored or any(self.vars[k].get().strip() for k in CONFIG_KEEP_KEYS):
            n = 1 if self.vars["same_dir"].get() else 2
            if not messagebox.askokcancel(
                APP_TITLE,
                f"将清空上面 {n} 个文件夹输入框，并删掉记录文件：\n"
                f"{CONFIG_PATH}\n\n"
                "下次打开软件就不会再自动填写了（选项区的勾选不受影响）。继续？",
            ):
                return
        for key in CONFIG_KEEP_KEYS:
            self.vars[key].set("")
        self._cancel_save()
        try:
            CONFIG_PATH.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(APP_TITLE, f"记录文件删不掉：\n{exc}")
        self._restored, self._missing_dirs = [], []
        self.status.config(text="已清空记录，下次打开需要重新选文件夹", foreground="#333")

    # ── 事件 ────────────────────────────────────────────────────────────
    def _pick_dir(self, key: str) -> None:
        d = filedialog.askdirectory(title=f"选择{self._label(key)}",
                                    initialdir=self.vars[key].get() or str(Path.home()))
        if d:
            # 联动（扫描件跟随、输出补默认值）交给 src 的 trace，这里不重复写一份
            self.vars[key].set(d)

    # ── 输出文件名后缀的实时预览 ─────────────────────────────────────────
    def _suffix_raw(self) -> str:
        return self.vars["suffix"].get()

    def _sample_stem(self) -> str:
        """拿一份真实文件名当例子，没有就退回固定示例 —— 让用户看到的是自己的文件。"""
        if self.report:
            for p in self.report.pairs:
                if p.src.stem:
                    return p.src.stem
        return SUFFIX_SAMPLE_FALLBACK

    def _refresh_suffix_hint(self) -> None:
        raw = self._suffix_raw()
        clean = C.clean_output_suffix(raw)
        stem = self._sample_stem()
        if clean:
            text = f"例：{stem}.pdf → {stem}{clean}.pdf"
        else:
            text = f"留空则保持原名（{stem}.pdf）"
        dropped = len(raw) - len(clean)
        if dropped > 0:
            text += f"　（已自动去掉 {dropped} 个不能用在后缀里的字符）"
        self.lbl_suffix.config(text=text)

    def _collect_options(self) -> C.Options:
        return C.Options(
            src_dir=self.vars["src"].get(),
            scan_dir=self.vars["scan"].get(),
            out_dir=self.vars["out"].get(),
            scale_to_original=self.vars["scale"].get(),
            overwrite=self.vars["overwrite"].get(),
            recursive=self.vars["recursive"].get(),
            threshold=round(float(self.vars["threshold"].get()), 2),
            output_suffix=self.vars["suffix"].get(),
            single_dir=bool(self.vars["same_dir"].get()),
            forced_roles=dict(self._forced),
        )

    def _guard(self) -> bool:
        if self.busy:
            return False
        # 输入目录必须已存在；输出目录可以还不存在，这里替用户建好
        targets = ["src"]
        if not self.vars["same_dir"].get():      # ⑨ 一个文件夹模式下扫描件就是工作文件夹
            targets.append("scan")
        for key in targets:
            p = self.vars[key].get().strip()
            if not p or not Path(p).is_dir():
                messagebox.showwarning(APP_TITLE, f"{self._label(key)}未选择或不存在：\n{p}")
                return False
        self._autofill_out()                     # 留空就补默认值，不让用户卡在这一步
        out = self.vars["out"].get().strip()
        if not out:
            messagebox.showwarning(APP_TITLE, "输出文件夹未选择。")
            return False
        if not Path(out).is_dir():
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning(APP_TITLE, f"输出文件夹不存在且无法创建：\n{out}\n{exc}")
                return False
        if Path(self.vars["src"].get()) == Path(self.vars["out"].get()):
            messagebox.showwarning(APP_TITLE, "输出文件夹不能和原始 PDF 文件夹相同，以免误覆盖。")
            return False
        return True

    def _set_busy(self, flag: bool) -> None:
        self.busy = flag
        state = "disabled" if flag else "normal"
        self.btn_scan.config(state=state)
        self.btn_run.config(state=state)

    def on_preview(self) -> None:
        if not self._guard():
            return
        self._set_busy(True)
        self._save_config()
        self.status.config(text="正在扫描并匹配…")
        self.pb.config(mode="indeterminate")
        self.pb.start(12)
        opts = self._collect_options()

        def work() -> None:
            try:
                rep = C.plan_pairs(opts)
            except Exception as exc:  # noqa: BLE001
                rep = C.Report(errors=[f"匹配过程异常：{exc}"])
            self.after(0, lambda: self._after_preview(rep))

        threading.Thread(target=work, daemon=True).start()

    def _after_preview(self, rep: C.Report) -> None:
        self.pb.stop()
        self.pb.config(mode="determinate", value=0)
        self._set_busy(False)
        self.report = rep
        self._render()
        self._refresh_suffix_hint()   # 匹配后例子换成你真实的文件名
        matched = sum(1 for p in rep.pairs if p.status == C.Status.MATCHED)
        parts = ["匹配完成"]
        if rep.split:                      # ⑨ 同文件夹模式：先说清"谁被认成什么"
            parts.append(rep.split.summary())
            if self._forced:
                parts.append(f"含人工改判 {len(self._forced)} 份")
        parts.append(
            f"待替换 {matched} 份｜需人工确认 "
            f"{sum(1 for p in rep.pairs if p.status in (C.Status.AMBIGUOUS, C.Status.NO_SCAN, C.Status.UNSURE))} 份｜"
            f"多余扫描件 {len(rep.orphan_scans)} 份"
        )
        self.status.config(text="　".join(parts))
        self.btn_run.config(state="normal" if matched else "disabled")
        if not matched and rep.errors:
            messagebox.showwarning(APP_TITLE, "\n".join(rep.errors[:6]))

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.report:
            return
        rows: list[tuple] = list(self.report.pairs)
        for i, p in enumerate(rows):
            size = f"{p.src.size[0]:g}×{p.src.size[1]:g}" if p.src.size[0] else "—"
            tags = [p.status.value]
            if i % 2:
                tags.append("odd")
            # ⑨ 分流依据（不确定的那几条）排在说明最前面，让人一眼看到"凭什么这么分"
            note = "；".join(x for x in (p.role_note, p.note) if x)
            self.tree.insert(
                "", "end", iid=f"p{i}", tags=tags,
                values=(p.src.path.name, p.src.pages or "—", size,
                        p.scan.path.name or "—", p.strategy,
                        f"{p.score:.2f}" if p.score else "—", p.status.value, note),
            )
        for j, s in enumerate(self.report.orphan_scans):
            note = "没有原始文件与之对应"
            if s.role_reason:
                note += f"；{s.role_reason}"
            self.tree.insert(
                "", "end", iid=f"o{j}", tags=[C.Status.EXTRA_SCAN.value],
                values=("—", s.pages or "—", "—", s.path.name, "—", "—",
                        C.Status.EXTRA_SCAN.value, note),
            )
        self.tree.tag_configure("odd", background="#f5f6f8")

    # ── 右键操作 ────────────────────────────────────────────────────────
    def _popup(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            # ⑨「这份其实是原件」只对同文件夹模式下、被判成"多余扫描件"的行开放
            self.menu.entryconfig(
                self.idx_force, state="normal" if self._selected_orphan() else "disabled")
            self.menu.tk_popup(event.x_root, event.y_root)

    def _selected_pair(self):
        sel = self.tree.selection()
        if not sel or not self.report or not sel[0].startswith("p"):
            return None
        idx = int(sel[0][1:])
        return self.report.pairs[idx] if idx < len(self.report.pairs) else None

    def _selected_orphan(self) -> C.FileItem | None:
        """选中的是不是「多余扫描件」那一行（同文件夹模式下才有改判意义）。"""
        sel = self.tree.selection()
        if not sel or not self.report or not self.report.single_dir or not sel[0].startswith("o"):
            return None
        j = int(sel[0][1:])
        return self.report.orphan_scans[j] if j < len(self.report.orphan_scans) else None

    def on_force_original(self) -> None:
        """⑨ 人工改判：把这一份当成原件，重新分流并发起匹配。

        分流规则表的第一条就是「人工改判」，所以这里只负责记下并重跑，不另写判定。
        """
        it = self._selected_orphan()
        if not it:
            return
        self._forced[str(it.path)] = C.Role.ORIGINAL.value
        self.status.config(text=f"已把「{it.path.name}」改判为原件，正在重新匹配…", foreground="#333")
        self.on_preview()

    def on_row_double(self, _e: tk.Event) -> None:
        self.on_assign()

    def on_assign(self) -> None:
        pair = self._selected_pair()
        if not pair or not self.report:
            messagebox.showinfo(APP_TITLE, "请先选中一行「原件」。")
            return
        try:
            opts = self._collect_options()
            path = self.vars["scan"].get()
            if opts.single_dir:
                # ⑨ 同文件夹模式下，可选项只有"被判成扫描件"的那些，别把原件也列出来误导
                scans = C.split_by_pages(
                    C.scan_folder(path, opts, want_pages=True), opts).scans
            else:
                scans = C.scan_folder(path, opts, want_pages=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"读取扫描件目录失败：{exc}")
            return
        if not scans:
            messagebox.showinfo(APP_TITLE, "扫描件文件夹里没有可用文件。")
            return

        win = tk.Toplevel(self)
        win.title("手动指定扫描件")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        ttk.Label(win, text=f"原始文件：{pair.src.path.name}", padding=8).pack(anchor="w")
        lb = tk.Listbox(win, width=76, height=14)
        for s in scans:
            lb.insert("end", f"{s.path.name}   (编号 {s.code or '无'})")
        lb.pack(padx=8, fill="both", expand=True)
        cur = next((i for i, s in enumerate(scans) if s.path == pair.scan.path), 0)
        lb.selection_set(cur)
        lb.see(cur)

        def ok() -> None:
            sel = lb.curselection()
            if not sel:
                return
            pair.scan = scans[sel[0]]
            pair.score = 1.0
            pair.strategy = "手动指定"
            pair.status = C.Status.MATCHED
            pair.note = "人工指定"
            self.report.orphan_scans = [s for s in self.report.orphan_scans if s.path != pair.scan.path]
            self._render()
            self.btn_run.config(state="normal")
            win.destroy()

        lb.bind("<Double-1>", lambda _e: ok())
        ttk.Button(win, text="确定", command=ok).pack(pady=8)

    def on_ignore(self) -> None:
        pair = self._selected_pair()
        if not pair:
            return
        pair.status = C.Status.SKIPPED
        pair.note = "已忽略（不处理此文件）"
        self._render()

    def on_reveal(self) -> None:
        pair = self._selected_pair()
        if not pair:
            return
        p = pair.src.path
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(p)], check=False)
        elif os.name == "nt":
            subprocess.run(["explorer", "/select,", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p.parent)], check=False)

    # ── 执行 ────────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if not self.report or not self._guard():
            return
        todo = [p for p in self.report.pairs if p.status == C.Status.MATCHED and p.scan.path.name]
        if not todo:
            messagebox.showinfo(APP_TITLE, "没有可替换的文件。")
            return
        if not messagebox.askokcancel(
            APP_TITLE,
            f"即将替换 {len(todo)} 份文件的首页，输出到：\n{self.vars['out'].get()}\n\n原始文件不会被修改。继续？",
        ):
            return
        self._set_busy(True)
        self._save_config()
        self.pb.config(mode="determinate", maximum=len(todo), value=0)
        opts = self._collect_options()

        def prog(i: int, n: int, name: str) -> None:
            self.after(0, lambda: (self.pb.config(value=i),
                                   self.status.config(text=f"[{i}/{n}] {name}")))

        def work() -> None:
            try:
                rep = C.run_batch(opts, prog, report=self.report)
            except Exception as exc:  # noqa: BLE001
                rep = self.report
                rep.errors.append(f"执行异常：{exc}")
            self.after(0, lambda: self._after_run(rep))

        threading.Thread(target=work, daemon=True).start()

    def _after_run(self, rep: C.Report) -> None:
        self._set_busy(False)
        self.report = rep
        self._render()
        failed = [r for r in rep.results if not r.ok]
        msg = f"完成：成功 {rep.ok_count} 份"
        if failed:
            msg += f"｜失败 {len(failed)} 份"
        self.status.config(text=msg)
        if failed:
            detail = "\n".join(f"· {r.src.name}：{r.note}" for r in failed[:10])
            messagebox.showwarning(APP_TITLE, f"{msg}\n\n失败明细：\n{detail}")
        else:
            messagebox.showinfo(APP_TITLE, f"{msg}。\n输出目录：{self.vars['out'].get()}")

    # ── 关窗 ────────────────────────────────────────────────────────────
    def on_close(self) -> None:
        """关窗前把当前选择落盘 —— 哪怕用户只是选完文件夹就关了。"""
        if self.busy and not messagebox.askokcancel(
                APP_TITLE, "还有任务在跑，现在关掉会中断它。确定要关吗？"):
            return
        self._save_config()
        self.winfo_toplevel().destroy()

    def on_open_out(self) -> None:
        d = self.vars["out"].get().strip()
        if not d or not Path(d).is_dir():
            messagebox.showinfo(APP_TITLE, "输出文件夹还不存在。")
            return
        if sys.platform == "darwin":
            subprocess.run(["open", d], check=False)
        elif os.name == "nt":
            subprocess.run(["explorer", d], check=False)
        else:
            subprocess.run(["xdg-open", d], check=False)

    # ── 说明 ────────────────────────────────────────────────────────────
    def on_help(self) -> None:
        win = tk.Toplevel(self)
        win.title("使用说明")
        win.transient(self.winfo_toplevel())
        text = tk.Text(win, width=78, height=26, wrap="word", padx=10, pady=8)
        text.pack(fill="both", expand=True)
        text.insert("1.0", HELP_TEXT)
        text.config(state="disabled")
        ttk.Button(win, text="知道了", command=win.destroy).pack(pady=6)


HELP_TEXT = """【这个工具做什么】
把「签字后扫描上来的首页」换掉原 PDF 的第一页：
    新的 PDF = [扫描件第 1 页] + [原 PDF 的第 2 页 ~ 最后一页]
原始文件一律只读，不会被改动。

【怎么用】
1. ① 里只选一个「工作文件夹」—— 没签字的原件和签好字扫回来的首页，
   全都放这一个文件夹里就行。输出文件夹会自动填成「工作文件夹/输出_已替换」，
   不满意再改（输出目录会自动排除在扫描范围外，不会把自己的成品当成原件）。
2. 点「扫描并匹配（预览）」——先看清楚谁配谁，红了先别急着跑
3. 确认没问题，点「开始替换」

② 里的每个开关把鼠标停在文字上，会弹出更详细的说明。

【一个文件夹里，工具怎么分清谁是谁】
分两步，顺序不能反：
第一步 · 按页数分角色（只看页数，不看文件名）
    页数 ≥2 页       → 原件
    只有 1 页 / 图片  → 签字扫描上来的那一页
    读不出页数        → 列成「无法判断」，既不判原件也不判扫描件，绝不猜
    ⚠️ 为什么分角色不认文件名：原文件名本身就可能带「- 签字版」这类词，
      让名字参与判定会把整批原件错认成扫描件。那些词只用来写依据。
第二步 · 按名字把两边一一配上（编号相同的一律直接命中，一份只配一次）
状态栏会告诉你结果：「原件 X 份、扫描件 Y 份、无法判断 Z 份」。
分不清的那几份会在「说明」列点名；如果某一份其实是单页的原件，
右键点它选「这份其实是原件（改判后重新匹配）」即可（改判只对本次运行有效）。

什么时候该去掉①最上面那个勾（改回两个文件夹）：
扫描件本身不止一页，或者你的原件里确实有只有一页的 —— 这时页数不再能说明问题。

【会记住上次的选择】
下次打开软件时，① 里的文件夹会自动填回上次用的，选项区和窗口大小也一样 ——
不用每次重新选。改动后约 1 秒自动记下（选完文件夹直接关窗也算）。
记录文件是个小 JSON，放在：
    <你的用户主目录>/.pdf_firstpage_swap.json
（Windows 一般在 C:\\Users\\你的用户名\\ 下面，macOS 在 /Users/你的用户名/ 下面）
如果记错了、或者想清空重来，点「忘记记住的文件夹」。
如果记着的那几个文件夹已经不在（U 盘拔了、改名了），状态栏会提示，重新选一下就行。

【文件名是怎么配上的】
按下面顺序降级，命中一条就停：
    ① 文档编号完全相同（WI-2101、GRA-3634、T-2054-49 这类）
    ② 去掉"扫描件 / 已签字 / 副本 / scan"等词后全名相同
    ③ 一个是另一个的一部分（长度差 ≤ 12）
    ④ 前缀一致（前 70% 相同）
    ⑤ 整体相似度 ≥ 门槛（默认 0.80）
一份原始文件只会占用一个扫描件，不会重复使用。

【状态怎么看】
  已匹配    —— 会自动处理
  冲突待定  —— 有多个同分候选，右键「手动指定扫描件…」挑一个
  无对应扫描件 —— 没找到对应的扫描件，跳过
  多余扫描件 —— 这份扫描件没找到对应原件，跳过（同文件夹模式下右键可「改判为原件」）
  无法判断  —— 同文件夹模式下页数读不出（文件损坏/加密），既不判原件也不判扫描件
  已忽略    —— 你右键手动排除的

【② 里的设置，分别管什么】
· 扫描页缩放到原件首页尺寸（默认开）
  扫描件比原件大/小都无所谓，会等比缩放居中。
  但如果两边"版面比例"明显不一致（比如原件封面是横向 A4、你扫上来的是纵向 A4），
  工具不会硬塞——会原样保留扫描页，并在"说明"列写明原因。
  关掉这个开关则一律原样保留扫描件尺寸。

· 覆盖输出文件夹里的同名文件（默认关）
  管的是"输出目录里已经有同名 PDF 怎么办"，跟原始文件无关（原始文件永远只读）。
  不勾 —— 绝不覆盖，重名自动加编号，输出成 xxx_2.pdf、xxx_3.pdf；
  勾上 —— 直接覆盖同名文件，旧内容被替换掉，不可恢复。

· 连同子文件夹一起处理（默认关）
  不勾只处理你选的那一层。勾上则子文件夹也一起扫，
  输出目录里复刻相同的子目录结构，避免不同子目录的同名文件互相打架。

· 输出文件名后缀（默认留空）
  只给输出文件的名字加一个字尾，不改变内容。
    原名 WI-2101,rev13 作业指导书.pdf
    填 _已签字首页 → WI-2101,rev13 作业指导书_已签字首页.pdf
    留空         → 与原名一致
  填什么就是什么，界面右侧会把拼接后的最终文件名实时显示出来。
  路径分隔符、冒号、星号等非法字符会被自动去掉，最长 40 个字符，建议以 _ 或 - 开头。

· 相似度门槛（默认 0.80）
  只有走到"整体相似度"这一档才用它；编号相同的一律直接命中。

【扫描件支持什么】
PDF（取第 1 页）或图片（jpg/png/bmp/tif/webp），图片按一页处理。
扫描件本身多页时，只取第 1 页。

【出错怎么办】
单份失败不影响其它文件，界面会列出失败明细和原因（如 PDF 加密、文件损坏）。
"""


def main() -> int:
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1180x720")
    root.minsize(980, 620)
    try:
        ttk.Style().theme_use("aqua" if sys.platform == "darwin" else "vista")
    except Exception:  # noqa: BLE001
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
