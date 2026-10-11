"""PDF 首页替换工具 —— 核心逻辑层（无 GUI 依赖，可单测、可命令行调用）

用途：把「人工签字后扫描上来的首页」替换掉原 PDF 的第一页，另存到输出目录。

设计约定（表驱动，所有可调规律集中在本文件顶部的「规律表」区）：
    ① 归一化流水线   NORM_STEPS   —— 文件名怎么变成可比较的键
    ② 噪声词表       NOISE_WORDS  —— 只参与比较，绝不进入输出文件名
    ③ 编号抽取表     CODE_PATTERNS—— 从文件名里抽文档编号
    ④ 匹配策略表     MATCH_STRATEGIES —— 有序降级，命中即止
    ⑤ 状态枚举       Status       —— 全流程状态只有一个来源
    ⑥ 选项开关       Options      —— 面向未来扩展的开关位
    ⑦ 后缀规范化     SUFFIX_FORBIDDEN / clean_output_suffix —— 界面与命令行同一口径
    ⑧ 贴合决策表     FIT_*        —— 扫描页缩放到原件首页 vs 原样保留
    ⑨ 分流规则表     SPLIT_RULES  —— 原件与扫描件混在一个文件夹时，凭什么判定谁是谁
    ⑩ 角色枚举       Role         —— 同文件夹模式下只有「原件 / 扫描件 / 无法判断」三种
"""
from __future__ import annotations

import argparse
import difflib
import io
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator, NamedTuple, Sequence

TOOL_NAME = "PDF 首页替换工具"
TOOL_VERSION = "1.0"

# ══════════════════════════════════════════════════════════════════════════
# 规律表区 —— 改行为只改这里
# ══════════════════════════════════════════════════════════════════════════

PDF_EXTS = frozenset({".pdf"})
# 扫描件也可能是图片（手机拍照 / 平板扫描导出），统一当"一页扫描件"处理
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"})
SCAN_EXTS = PDF_EXTS | IMAGE_EXTS

# ① 噪声词表：这些词描述的是"载体/流程"，不是"文档身份"。
#    只在匹配比较时剔除；若真的剔除后双方一致，视为同一份文档的不同载体 —— 安全。
NOISE_WORDS: tuple[str, ...] = (
    "扫描签字版", "签字扫描版", "扫描件", "扫描版", "扫描图", "扫描", "拍照", "翻拍",
    "签字版", "签署版", "签名版", "签核版", "审批版", "受控版", "发放版", "确认版",
    "已签字", "已签名", "已签核", "已审批", "已确认", "已签", "签字", "签名", "签署",
    "签章", "盖章版", "盖章", "签核", "审批",
    "复印", "副本", "拷贝", "复印件", "归档", "回传", "上传",
    "电子版", "纸质版", "打印版", "原件",
    "scan", "scanned", "scanner", "signed", "signature", "sign", "copy", "print",
)

# ② 归一化流水线：有序执行，每一步都是 str -> str
NORM_RULES: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("去扩展名", lambda s: os.path.splitext(s)[0]),
    ("全角转半角", lambda s: unicodedata.normalize("NFKC", s)),
    ("统一小括号", lambda s: s.replace("（", "(").replace("）", ")")),
    ("去噪声词", None),  # 运行时由 _apply_norm_rules 用 _NOISE_RE 实现
    ("转大写", lambda s: s.upper()),
    ("仅留中文与字母数字", lambda s: re.sub(r"[^\u4e00-\u9fffA-Z0-9]+", "", s)),
)

_NOISE_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(NOISE_WORDS, key=len, reverse=True)),
    re.IGNORECASE,
)

# ③ 编号抽取表：有序，命中第一个即止。
#    code 的归一形式 = 字母 + 数字(+可选第三段)，无分隔符、全大写。
CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("字母-数字", re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,6})\s*[-_ ]\s*(\d{2,8})(?:\s*[-_]\s*(\d{1,4}))?(?!\d)")),
    ("字母数字连写", re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})(\d{3,8})(?:\s*[-_]\s*(\d{1,4}))?(?!\d)")),
    ("纯数字长串", re.compile(r"(?<!\d)(\d{6,})(?!\d)")),
)

# ④ 匹配策略表：有序降级；函数返回 None 表示"这条策略不适用"，否则返回相似度分值。
MATCH_STRATEGIES: tuple[tuple[str, Callable[["FileItem", "FileItem"], float | None]], ...] = (
    ("编号精确", lambda a, b: 1.00 if (a.code and a.code == b.code) else None),
    ("名称精确", lambda a, b: 0.98 if (a.norm and a.norm == b.norm) else None),
    ("名称包含", None),   # 运行时由 _m_contains 实现
    ("前缀一致", None),   # 运行时由 _m_prefix 实现
    ("相似度", None),     # 运行时由 _m_ratio 实现（受阈值约束）
)
STRATEGY_SCORES = {name: idx for idx, (name, _) in enumerate(MATCH_STRATEGIES)}

# 相似度策略里"包含/前缀"的长度容差：两侧归一化后长度差超过它就不算包含/前缀
CONTAINS_MAX_LEN_DIFF = 12
PREFIX_MIN_LEN = 4

# ⑦ 贴合决策表：原件首页与扫描件的"显示宽高比"相对差异超过它，
#    就认为两者版面不一致（例如横向封面 vs 纵向 A4 扫描），此时原样保留扫描页，
#    避免把纵向扫描图缩进横向页面里、两旁留出大片白边。
FIT_ASPECT_TOLERANCE = 0.15
FIT_FIT = "贴合"
FIT_ASIS = "原样"


class Status(str, Enum):
    """⑤ 全流程状态枚举 —— 唯一来源，界面/清单/日志都读它。"""

    MATCHED = "已匹配"
    AMBIGUOUS = "冲突待定"
    NO_SCAN = "无对应扫描件"
    EXTRA_SCAN = "多余扫描件"
    UNSURE = "无法判断"      # ⑨ 同文件夹模式：页数读不出，既不判原件也不判扫描件
    OK = "替换成功"
    FAILED = "替换失败"
    SKIPPED = "已跳过"


class Role(str, Enum):
    """⑩ 同文件夹模式下的文件角色 —— 只有这三个来源，别在流程里另造词。"""

    ORIGINAL = "原件"
    SCAN = "扫描件"
    UNSURE = "无法判断"


# ⑨ 分流口径（用户 2026-10-01 定的）：
#    原件 ≥2 页；签署后扫描上来的那一页只有 1 页。
#    ⚠️ 名称提示词只用来"补强依据"，**绝不单独据此判定角色** ——
#      用户的真实语料里，原文件名本身就带「- 签字版」，光看名字会把原件误判成扫描件。
MIN_ORIGINAL_PAGES = 2
SCAN_HINT_WORDS: tuple[str, ...] = (
    "扫描", "拍照", "翻拍", "已签", "签字", "签名", "签署", "签核", "签章", "盖章",
    "scan", "signed", "signature",
)
_SCAN_HINT_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(SCAN_HINT_WORDS, key=len, reverse=True)),
    re.IGNORECASE,
)


def scan_hint_in(name: str) -> str:
    """名字里出现的「扫描/签字」类提示词（只作依据补强，不用于判定）。"""
    m = _SCAN_HINT_RE.search(name)
    return m.group(0) if m else ""


@dataclass
class Options:
    """⑥ 选项开关 —— 面向未来扩展，新增能力只加字段不改签名。"""

    src_dir: str = ""
    scan_dir: str = ""
    out_dir: str = ""
    scale_to_original: bool = True      # 扫描页按原第一页尺寸等比缩放居中
    overwrite: bool = False             # 输出目录已有同名文件时是否直接覆盖
    recursive: bool = False             # 递归扫描子目录（输出保持相对目录结构）
    threshold: float = 0.80             # 相似度策略的门槛
    output_suffix: str = ""             # 输出文件名后缀，如 "_已替换"（会过 clean_output_suffix）
    keep_metadata: bool = True          # 继承原 PDF 元数据
    keep_scan_extra_pages: bool = False  # 扫描件多页时，是否把其余页也并进来
    dry_run: bool = False               # 只演练不写文件
    # ⑨ 同文件夹模式：原件与扫描件混在同一个目录里，靠 SPLIT_RULES 判定角色。
    #    显式开关；此外 src_dir 与 scan_dir 指向同一个目录时也会自动生效（命令行友好）。
    single_dir: bool = False
    min_original_pages: int = MIN_ORIGINAL_PAGES   # 「≥N 页算原件」的 N，留作可调点
    # 人工改判：{文件绝对路径: 角色值}。由 SPLIT_RULES 的第一条规则消费，优先级最高。
    forced_roles: dict[str, str] = field(default_factory=dict)


@dataclass
class FileItem:
    path: Path
    stem: str
    norm: str
    code: str
    pages: int = 0
    size: tuple[float, float] = (0.0, 0.0)
    is_image: bool = False
    error: str = ""
    role: str = ""            # ⑨ 分流结果（Role 的值），非同类模式为空
    role_reason: str = ""     # ⑨ 为什么判成这个角色 —— 证据，直接给人看
    notable: bool = False     # ⑨ 这条判定不确定，预览时要在「说明」列点名


@dataclass
class Pair:
    src: FileItem
    scan: FileItem
    score: float
    strategy: str
    status: Status = Status.MATCHED
    note: str = ""
    role_note: str = ""       # ⑨ 分流依据里"值得你看一眼"的那部分（不确定的判定）


@dataclass
class SplitResult:
    """⑨ 同文件夹模式的产物：谁被认成原件、谁被认成扫描件、谁没认出来。"""

    originals: list[FileItem] = field(default_factory=list)
    scans: list[FileItem] = field(default_factory=list)
    unsure: list[FileItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.originals) + len(self.scans) + len(self.unsure)

    def summary(self) -> str:
        s = (f"同文件夹分流：原件 {len(self.originals)}（≥{MIN_ORIGINAL_PAGES} 页）"
             f"｜扫描件 {len(self.scans)}（1 页）")
        if self.unsure:
            s += f"｜无法判断 {len(self.unsure)}"
        return s


@dataclass
class SwapResult:
    src: Path
    out: Path = Path()
    ok: bool = False
    status: Status = Status.FAILED
    note: str = ""
    pages_in: int = 0
    pages_out: int = 0
    fit_mode: str = ""
    fit_reason: str = ""


@dataclass
class Report:
    pairs: list[Pair] = field(default_factory=list)
    results: list[SwapResult] = field(default_factory=list)
    orphan_scans: list[FileItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    single_dir: bool = False                 # ⑨ 本次是否走了同文件夹模式
    split: SplitResult | None = None         # ⑨ 分流明细（非同类模式为 None）

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 分流：原件与扫描件放在同一个文件夹时，按页数判定角色
# ══════════════════════════════════════════════════════════════════════════


class RoleVerdict(NamedTuple):
    """一条分流规则的结论。

    role     —— 判成什么
    reason   —— 人话依据，始终写进 FileItem.role_reason（"不是我瞎猜"的证据）
    notable  —— True = 这条判定本身不确定，预览时要在「说明」列点名让人看一眼
    """

    role: Role
    reason: str
    notable: bool = False


def _s_forced(it: FileItem, opts: Options) -> RoleVerdict | None:
    """⓿ 人工改判优先 —— 界面右键「这份其实是原件」写进 forced_roles。"""
    val = opts.forced_roles.get(str(it.path))
    if not val:
        return None
    role = next((r for r in Role if r.value == val), None)
    if role is None:
        return None
    return RoleVerdict(role, "人工指定，覆盖自动判定")


def _s_unreadable(it: FileItem, opts: Options) -> RoleVerdict | None:
    """读不出页数（损坏/加密/空 PDF）—— 绝不猜，既不判原件也不判扫描件。"""
    if it.error or it.pages <= 0:
        return RoleVerdict(Role.UNSURE, it.error or "读不出页数")
    return None


def _s_image(it: FileItem, opts: Options) -> RoleVerdict | None:
    """图片没有"页数"概念，一律当一页扫描件。"""
    if it.is_image:
        return RoleVerdict(Role.SCAN, "图片文件，按一页扫描页处理")
    return None


def _s_multi_page(it: FileItem, opts: Options) -> RoleVerdict | None:
    """主规则：≥N 页 = 原件（扫描上来的签署页不会多扫）。"""
    n = max(2, int(opts.min_original_pages))
    if it.pages >= n:
        return RoleVerdict(Role.ORIGINAL, f"{it.pages} 页 ≥ {n} 页，按原件处理")
    return None


def _s_single_page(it: FileItem, opts: Options) -> RoleVerdict | None:
    """兜底：只剩 1 页 → 扫描件。名字里有「扫描/签字」是加分证据，
    没有则标 notable —— 有可能是"只有一页的原件"，让人自己看一眼（绝不擅自当成扫描件吞掉）。
    """
    if it.pages != 1:
        return None
    hint = scan_hint_in(it.stem)
    if hint:
        return RoleVerdict(Role.SCAN, f"只有 1 页，名字里也带「{hint}」→ 按签署扫描页处理")
    return RoleVerdict(
        Role.SCAN,
        "只有 1 页、名字里也没有扫描/签字字样 → 仍按签署扫描页处理",
        notable=True,
    )


# ⑨ 分流规则表：有序，命中第一条即返回。加规则只改这张表。
#    ⚠️ 判定**只认页数与文件类型**；文件名提示词只用来写依据，不参与判定。
SPLIT_RULES: tuple[tuple[str, Callable[[FileItem, Options], RoleVerdict | None]], ...] = (
    ("人工改判", _s_forced),
    ("读不出页数", _s_unreadable),
    ("图片即扫描件", _s_image),
    ("页数达标即原件", _s_multi_page),
    ("单页即扫描件", _s_single_page),
)


def split_by_pages(items: Sequence[FileItem], options: Options) -> SplitResult:
    """给同一文件夹里的每个文件判一个角色，并把依据写在 FileItem 上。"""
    res = SplitResult()
    for it in items:
        verdict = None
        for _name, fn in SPLIT_RULES:
            verdict = fn(it, options)
            if verdict is not None:
                break
        if verdict is None:                      # 规则表没覆盖 → 不表态
            verdict = RoleVerdict(Role.UNSURE, "规则表没有覆盖这种情况")
        it.role, it.role_reason = verdict.role.value, verdict.reason
        it.notable = verdict.notable
        if verdict.role is Role.ORIGINAL:
            res.originals.append(it)
        elif verdict.role is Role.SCAN:
            res.scans.append(it)
        else:
            res.unsure.append(it)
    return res


# ══════════════════════════════════════════════════════════════════════════
# 归一化 / 编号抽取
# ══════════════════════════════════════════════════════════════════════════


def _apply_norm_rules(name: str) -> str:
    s = name
    for label, fn in NORM_RULES:
        if fn is None:  # 去噪声词
            s = _NOISE_RE.sub("", s)
        else:
            s = fn(s)
    return s


def normalize_name(name: str) -> str:
    return _apply_norm_rules(name)


def extract_code(name: str) -> str:
    """从原始文件名里抽文档编号；抽不到返回空串。"""
    s = unicodedata.normalize("NFKC", os.path.splitext(name)[0]).upper()
    for _label, pat in CODE_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        parts = [g for g in m.groups() if g]
        return re.sub(r"[^A-Z0-9]", "", "".join(parts)).upper()
    return ""


def _m_contains(a: FileItem, b: FileItem) -> float | None:
    if not a.norm or not b.norm:
        return None
    short, long = sorted((a.norm, b.norm), key=len)
    if len(long) - len(short) > CONTAINS_MAX_LEN_DIFF or len(short) < PREFIX_MIN_LEN:
        return None
    return 0.90 if short in long else None


def _m_prefix(a: FileItem, b: FileItem) -> float | None:
    if not a.norm or not b.norm:
        return None
    n = 0
    for ca, cb in zip(a.norm, b.norm):
        if ca != cb:
            break
        n += 1
    if n >= max(PREFIX_MIN_LEN, int(min(len(a.norm), len(b.norm)) * 0.7)):
        return 0.85
    return None


def _m_ratio(a: FileItem, b: FileItem) -> float | None:
    if not a.norm or not b.norm:
        return None
    return difflib.SequenceMatcher(None, a.norm, b.norm).ratio()


def _resolve_strategies() -> tuple[tuple[str, Callable[[FileItem, FileItem], float | None]], ...]:
    table = []
    for name, fn in MATCH_STRATEGIES:
        if name == "名称包含":
            fn = _m_contains
        elif name == "前缀一致":
            fn = _m_prefix
        elif name == "相似度":
            fn = _m_ratio
        table.append((name, fn))
    return tuple(table)


def score_pair(a: FileItem, b: FileItem, threshold: float = 0.80) -> tuple[float, str]:
    """有序降级打分：命中第一条适用策略即返回 (分值, 策略名)。"""
    for name, fn in _resolve_strategies():
        s = fn(a, b)
        if s is None:
            continue
        if name == "相似度" and s < threshold:
            return 0.0, "未命中"
        return float(s), name
    return 0.0, "未命中"


# ══════════════════════════════════════════════════════════════════════════
# 扫描与匹配
# ══════════════════════════════════════════════════════════════════════════


def _iter_files(root: Path, exts: frozenset[str], recursive: bool,
                exclude: Path | None = None) -> Iterator[Path]:
    if not root.is_dir():
        return
    it = root.rglob("*") if recursive else root.glob("*")
    for p in sorted(it):
        if not p.is_file() or p.name.startswith("~$") or p.suffix.lower() not in exts:
            continue
        # 输出目录万一被放在输入目录里，绝不能把自己的成品再当成原件吃回去
        if exclude is not None:
            try:
                if exclude in p.resolve().parents:
                    continue
            except Exception:  # noqa: BLE001
                pass
        yield p


def _excluded_dir(root: str | Path, options: Options) -> Path | None:
    """输出目录落在扫描范围之内时返回它（用于排除），否则 None。"""
    if not str(options.out_dir).strip():
        return None
    try:
        out, r = Path(options.out_dir).resolve(), Path(root).resolve()
        if not out.exists():
            return None
        return out if (out == r or r in out.parents) else None
    except Exception:  # noqa: BLE001
        return None


def read_pdf_info(path: Path) -> tuple[int, tuple[float, float], str]:
    """返回 (页数, (宽,高), 错误信息)。图片按 1 页处理。"""
    if path.suffix.lower() in IMAGE_EXTS:
        return 1, (0.0, 0.0), ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        n = len(reader.pages)
        if n == 0:
            return 0, (0.0, 0.0), "PDF 内没有任何页面"
        box = reader.pages[0].mediabox
        w, h = float(box.width), float(box.height)
        if abs(float(getattr(reader.pages[0], "rotation", 0) or 0)) % 180 == 90:
            w, h = h, w
        return n, (round(w, 1), round(h, 1)), ""
    except Exception as exc:  # noqa: BLE001
        return 0, (0.0, 0.0), f"读取失败：{exc}"


def scan_folder(root: str | Path, options: Options, want_pages: bool = True) -> list[FileItem]:
    root = Path(root)
    excl = _excluded_dir(root, options)
    items: list[FileItem] = []
    for p in _iter_files(root, SCAN_EXTS, options.recursive, excl):
        pages, size, err = read_pdf_info(p) if want_pages else (0, (0.0, 0.0), "")
        items.append(
            FileItem(
                path=p,
                stem=p.stem,
                norm=normalize_name(p.name),
                code=extract_code(p.name),
                pages=pages,
                size=size,
                is_image=p.suffix.lower() in IMAGE_EXTS,
                error=err,
            )
        )
    return items


EPS = 1e-9


def match_pairs(
    srcs: Sequence[FileItem],
    scans: Sequence[FileItem],
    threshold: float = 0.80,
) -> tuple[list[Pair], list[FileItem], list[FileItem]]:
    """贪心一对一匹配。返回 (配对列表, 未配上的原始文件, 多余的扫描件)。

    规则：
      · 先算全量分值矩阵（只保留 >= 阈值 的候选）
      · 按分值降序、策略优先级升序贪心占用；同一扫描件不会被两份原始文件共用
      · 有候选但全被抢走 → 冲突待定；完全没有候选 → 无对应扫描件
    """
    cands: list[tuple[float, int, int, str]] = []
    for i, a in enumerate(srcs):
        if a.error:
            continue
        for j, b in enumerate(scans):
            if b.error:
                continue
            s, name = score_pair(a, b, threshold)
            if s >= threshold - EPS:
                cands.append((s, i, j, name))

    cands.sort(key=lambda t: (-t[0], STRATEGY_SCORES.get(t[3], 99)))

    used_src: set[int] = set()
    used_scan: set[int] = set()
    pairs: list[Pair] = []
    had_cand: dict[int, int] = {}
    for _s, i, j, _n in cands:
        had_cand[i] = had_cand.get(i, 0) + 1
    for s, i, j, name in cands:
        if i in used_src or j in used_scan:
            continue
        used_src.add(i)
        used_scan.add(j)
        pairs.append(Pair(src=srcs[i], scan=scans[j], score=round(s, 3), strategy=name))

    leftover_src: list[FileItem] = []
    for i, a in enumerate(srcs):
        if i in used_src:
            continue
        if a.error:
            leftover_src.append(a)
            continue
        if had_cand.get(i, 0) > 1:
            rivals = [scans[j] for (_s, ii, j, _n) in cands if ii == i]
            pairs.append(
                Pair(
                    src=a,
                    scan=rivals[0],
                    score=0.0,
                    strategy="—",
                    status=Status.AMBIGUOUS,
                    note=f"有 {len(rivals)} 个候选：{'、'.join(r.path.name for r in rivals[:3])}",
                )
            )
        else:
            leftover_src.append(a)

    orphan = [scans[j] for j in range(len(scans)) if j not in used_scan]
    pairs.sort(key=lambda p: (p.status != Status.MATCHED, -p.score, p.src.path.name))
    return pairs, leftover_src, orphan


# ══════════════════════════════════════════════════════════════════════════
# 首页替换
# ══════════════════════════════════════════════════════════════════════════


def _image_to_pdf_bytes(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as img:
        if img.mode in ("RGBA", "P", "LA", "PA"):
            img = img.convert("RGB")
        elif img.mode == "CMYK":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PDF", resolution=200.0)
        return buf.getvalue()


def _open_scan_reader(path: Path):
    from pypdf import PdfReader

    if path.suffix.lower() in IMAGE_EXTS:
        return PdfReader(io.BytesIO(_image_to_pdf_bytes(path)))
    return PdfReader(str(path))


def display_size(page) -> tuple[float, float]:
    """页面「显示尺寸」：把 /Rotate 90/270 的页面按横放后的尺寸返回。"""
    box = page.mediabox
    w, h = float(box.width), float(box.height)
    if abs(float(getattr(page, "rotation", 0) or 0)) % 180 == 90:
        w, h = h, w
    return w, h


def decide_fit(src_disp: tuple[float, float], scan_disp: tuple[float, float],
               tolerance: float = FIT_ASPECT_TOLERANCE) -> tuple[str, str]:
    """⑦ 贴合决策：返回 (FIT_FIT / FIT_ASIS, 人话原因)。"""
    if min(src_disp) <= 0 or min(scan_disp) <= 0:
        return FIT_ASIS, "尺寸信息缺失，原样保留扫描页"
    r_src = src_disp[0] / src_disp[1]
    r_scan = scan_disp[0] / scan_disp[1]
    diff = abs(r_src - r_scan) / max(r_src, r_scan)
    if diff <= tolerance:
        return FIT_FIT, f"版面比例接近（宽高比差异 {diff:.0%}），按原件首页尺寸等比缩放居中"
    return FIT_ASIS, (f"版面比例不一致（原件 {r_src:.2f} vs 扫描件 {r_scan:.2f}），"
                      f"扫描件原样作为第 1 页，不硬塞进原页面")


def _prepare_scan_page(scan_page, tw: float, th: float):
    """把扫描页等比缩放并居中到 (tw, th) 上，返回一个新的页面对象。"""
    from pypdf import Transformation

    try:  # pypdf >= 3.16：把 /Rotate 烘进内容流，避免旋转页错位
        scan_page.transfer_rotation_to_content()
    except Exception:  # noqa: BLE001
        pass

    box = scan_page.mediabox
    sw, sh = float(box.width), float(box.height)
    if sw <= 0 or sh <= 0:
        return scan_page
    k = min(tw / sw, th / sh)
    ox = (tw - sw * k) / 2.0
    oy = (th - sh * k) / 2.0
    scan_page.add_transformation(Transformation().scale(k, k).translate(ox, oy))
    scan_page.mediabox.lower_left = (0, 0)
    scan_page.mediabox.upper_right = (tw, th)
    return scan_page


def swap_first_page(src_path: Path, scan_path: Path, out_path: Path, options: Options) -> SwapResult:
    """核心动作：输出 = [扫描件首页] + [原件第 2..N 页]。"""
    from pypdf import PdfReader, PdfWriter

    res = SwapResult(src=src_path, out=out_path)
    try:
        src_reader = PdfReader(str(src_path))
        if src_reader.is_encrypted:
            try:
                src_reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"原 PDF 已加密，无法读取（{exc}）") from exc
        n_in = len(src_reader.pages)
        if n_in == 0:
            raise RuntimeError("原 PDF 内没有任何页面")
        res.pages_in = n_in

        scan_reader = _open_scan_reader(scan_path)
        if len(scan_reader.pages) == 0:
            raise RuntimeError("扫描件内没有任何页面")
        scan_page = scan_reader.pages[0]

        # ⑦ 先决定"贴合"还是"原样"，再动手
        mode, reason = (FIT_ASIS, "已关闭缩放，扫描件原样作为第 1 页")
        if options.scale_to_original:
            mode, reason = decide_fit(display_size(src_reader.pages[0]), display_size(scan_page))
        res.fit_mode, res.fit_reason = mode, reason

        writer = PdfWriter()
        if mode == FIT_FIT:
            tw, th = display_size(src_reader.pages[0])
            writer.add_blank_page(width=tw, height=th)
            writer.pages[-1].merge_page(_prepare_scan_page(scan_page, tw, th))
        else:
            writer.add_page(scan_page)

        for idx, page in enumerate(src_reader.pages):
            if idx == 0:
                continue
            writer.add_page(page)

        if options.keep_scan_extra_pages:
            for extra in list(scan_reader.pages)[1:]:
                writer.add_page(extra)

        if options.keep_metadata and src_reader.metadata:
            try:
                writer.add_metadata({k: str(v) for k, v in src_reader.metadata.items()})
            except Exception:  # noqa: BLE001
                pass

        res.pages_out = len(writer.pages)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out_path)

        res.ok = True
        res.status = Status.OK
        if mode == FIT_ASIS:
            res.note = res.fit_reason
        elif n_in == 1:
            res.note = "原 PDF 只有 1 页，输出即为扫描首页"
        return res
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.status = Status.FAILED
        res.note = str(exc)
        return res


# ══════════════════════════════════════════════════════════════════════════
# 输出文件名后缀
# ══════════════════════════════════════════════════════════════════════════

# 文件名非法字符（Windows 保留 + 控制字符）；后缀里出现这些一律剔除
SUFFIX_FORBIDDEN = frozenset('\\/:*?"<>|') | {chr(i) for i in range(32)}
SUFFIX_MAX_LEN = 40


def clean_output_suffix(raw: str) -> str:
    """后缀规范化 —— 界面与命令行共用这一个口径，避免两边规则漂移。

    规则：① 去掉首尾空白 ② 剔除路径分隔符与文件名非法字符
          ③ 去掉首尾的点（Windows 上会变成隐藏/异常名）④ 限长 40。
    不做任何“自动补下划线”的猜测——用户填什么就是什么，
    界面会把拼接后的最终文件名实时显示出来，所见即所得。
    """
    s = (raw or "").strip()
    s = "".join(ch for ch in s if ch not in SUFFIX_FORBIDDEN)
    s = s.strip().strip(".")
    return s[:SUFFIX_MAX_LEN]


def _unique_out_path(base: Path, used: set[Path]) -> Path:
    cand = base
    n = 2
    while cand in used or (cand.exists()):
        cand = base.with_name(f"{base.stem}_{n}{base.suffix}")
        n += 1
    used.add(cand)
    return cand


def _same_dir(a: str | Path, b: str | Path) -> bool:
    """两个路径是否指向同一个文件夹（大小写差异、符号链接都算同一个）。"""
    if not str(a).strip() or not str(b).strip():
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:  # noqa: BLE001
        return False


def plan_pairs(options: Options) -> Report:
    """只做扫描 + 匹配，不落盘（给界面预览用）。

    两种模式：
      · 两文件夹：原件、扫描件各一个目录，直接匹配。
      · 同文件夹：原件与扫描件混在一起，先按 ⑨ SPLIT_RULES 分流（≥2 页=原件、1 页/图片=扫描件），
        再走同一套匹配逻辑。显式开关或"两个目录填成同一个"都会走这条。
    """
    report = Report()
    single = options.single_dir or _same_dir(options.src_dir, options.scan_dir)
    report.single_dir = single

    if single:
        items = scan_folder(options.src_dir, options, want_pages=True)
        split = split_by_pages(items, options)
        report.split = split
        srcs, scans = split.originals, split.scans
        if not items:
            report.errors.append(f"文件夹里没有找到 PDF/图片：{options.src_dir}")
        else:
            if not srcs:
                report.errors.append(
                    f"没识别出任何原件（页数 ≥{max(2, options.min_original_pages)}）：{options.src_dir}")
            if not scans:
                report.errors.append("没识别出任何扫描件（1 页 PDF 或图片）")
    else:
        srcs = scan_folder(options.src_dir, options, want_pages=True)
        scans = scan_folder(options.scan_dir, options, want_pages=True)
        if not srcs:
            report.errors.append(f"原始 PDF 目录里没有找到 PDF：{options.src_dir}")
        if not scans:
            report.errors.append(f"扫描件目录里没有找到 PDF/图片：{options.scan_dir}")
    report.errors += [f"{it.path.name}：{it.error}" for it in srcs + scans if it.error]

    pairs, leftover, orphan = match_pairs(srcs, scans, options.threshold)
    # ⑨ 不确定的分流判定要跟着行走 —— 让人在预览里就看见「凭什么这么分」，
    #    并标明这条依据说的是哪一边（原件/扫描件），免得看错对象。
    if report.split:
        for p in pairs:
            if p.scan.notable and p.scan.role_reason:
                p.role_note = f"[扫描件] {p.scan.role_reason}"
            elif p.src.notable and p.src.role_reason:
                p.role_note = f"[原件] {p.src.role_reason}"
    empty = FileItem(path=Path(""), stem="", norm="", code="")
    for it in leftover:
        pairs.append(
            Pair(src=it, scan=empty, score=0.0, strategy="—",
                 status=Status.NO_SCAN, note=it.error or "未找到对应扫描件")
        )
    # ⑨ 分不出来的文件必须露出来，不能让它在两张表之间悄悄消失
    for it in (report.split.unsure if report.split else []):
        pairs.append(
            Pair(src=it, scan=empty, score=0.0, strategy="—",
                 status=Status.UNSURE, note=it.role_reason or it.error)
        )
    pairs.sort(key=lambda p: (p.status != Status.MATCHED, -p.score, p.src.path.name))
    report.pairs = pairs
    report.orphan_scans = orphan
    return report


def run_batch(
    options: Options,
    progress: Callable[[int, int, str], None] | None = None,
    report: Report | None = None,
) -> Report:
    """全流程：扫描 → 匹配 → 逐份替换 → 汇总。

    report 由外部传入时复用其匹配结果（界面上的手动指定/忽略才会生效）。
    """
    if report is None:
        report = plan_pairs(options)
    report.results = []
    todo = [p for p in report.pairs if p.status == Status.MATCHED and p.scan.path.name]
    used: set[Path] = set()
    out_root = Path(options.out_dir)

    for idx, pair in enumerate(todo, 1):
        if progress:
            progress(idx, len(todo), pair.src.path.name)
        stem = pair.src.stem + clean_output_suffix(options.output_suffix)
        out_path = out_root / f"{stem}.pdf"
        if options.recursive:
            try:
                rel = pair.src.path.parent.relative_to(Path(options.src_dir))
                out_path = out_root / rel / f"{stem}.pdf"
            except ValueError:
                pass
        if out_path.exists() and not options.overwrite:
            out_path = _unique_out_path(out_path, used)
            pair.note = "输出已存在，自动改名"
        else:
            used.add(out_path)

        if options.dry_run:
            report.results.append(
                SwapResult(src=pair.src.path, out=out_path, ok=True, status=Status.SKIPPED,
                           note="演练模式，未写文件", pages_in=pair.src.pages,
                           pages_out=pair.src.pages)
            )
            continue

        res = swap_first_page(pair.src.path, pair.scan.path, out_path, options)
        if not res.ok:
            pair.status = Status.FAILED
            pair.note = res.note
        elif res.note:
            pair.note = res.note
        report.results.append(res)

    return report


# ══════════════════════════════════════════════════════════════════════════
# 命令行入口（无界面也能跑）
# ══════════════════════════════════════════════════════════════════════════


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="swap_core", description=f"{TOOL_NAME} v{TOOL_VERSION}")
    ap.add_argument("--src", required=True, help="原始 PDF 文件夹")
    ap.add_argument("--scan", default="", help="扫描件文件夹（签署后的首页）；省略或与 --src 相同 → 同文件夹模式")
    ap.add_argument("--out", required=True, help="输出文件夹")
    ap.add_argument("--min-original-pages", type=int, default=MIN_ORIGINAL_PAGES,
                    help=f"同文件夹模式：≥N 页算原件，默认 {MIN_ORIGINAL_PAGES}")
    ap.add_argument("--threshold", type=float, default=0.80, help="相似度门槛，默认 0.80")
    ap.add_argument("--suffix", default="", help="输出文件名后缀，如 _已替换，默认无")
    ap.add_argument("--no-scale", action="store_true", help="不缩放扫描页（保留扫描件原尺寸）")
    ap.add_argument("--overwrite", action="store_true", help="输出目录已有同名文件时直接覆盖（默认自动加 _2 编号）")
    ap.add_argument("--recursive", action="store_true", help="连同子文件夹一起处理，输出复刻目录结构")
    ap.add_argument("--dry-run", action="store_true", help="只演练，不写文件")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    opts = Options(
        src_dir=args.src,
        scan_dir=args.scan or args.src,     # 省略 --scan 即同文件夹模式
        out_dir=args.out,
        threshold=args.threshold,
        output_suffix=args.suffix,
        scale_to_original=not args.no_scale,
        overwrite=args.overwrite,
        recursive=args.recursive,
        dry_run=args.dry_run,
        min_original_pages=args.min_original_pages,
    )

    def _p(i: int, n: int, name: str) -> None:
        print(f"  [{i}/{n}] {name}")

    rep = run_batch(opts, _p)
    if rep.split:
        print(f"\n{rep.split.summary()}")
        for it in rep.split.unsure:
            print(f"  ? 无法判断：{it.path.name} — {it.role_reason}")
    print(f"\n已匹配 {sum(1 for p in rep.pairs if p.status == Status.MATCHED)} 份｜"
          f"成功 {rep.ok_count} 份｜失败 {sum(1 for r in rep.results if not r.ok)} 份")
    for p in rep.pairs:
        if p.status != Status.MATCHED:
            print(f"  · {p.status.value}：{p.src.path.name} — {p.note}")
        elif p.role_note:
            print(f"  · {p.src.path.name} 的分流依据：{p.role_note}")
    if rep.orphan_scans:
        print(f"  · {Status.EXTRA_SCAN.value}：{'、'.join(s.path.name for s in rep.orphan_scans)}")
    for e in rep.errors:
        print(f"  ! {e}")
    return 0 if rep.ok_count or opts.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
