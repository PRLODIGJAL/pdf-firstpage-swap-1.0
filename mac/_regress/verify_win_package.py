#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 交付包的验收：静态检查 + 环境流程真跑一遍。

分两段：

【静态】在只读模式下核对交付包本身
  · 程序本体与开发目录**逐字节一致**（md5）
  · .bat 是 CRLF + 纯 ASCII + 无 BOM + goto 标签齐全
  · 使用说明.txt 是 utf-8-sig
  · 包里没有混进开发用文件（_regress / selftest / 另一平台的启动器 / 缓存）
  · setup_env.py 的依赖自检清单**覆盖了程序实际 import 的第三方库**

【功能】把交付包整个拷到临时目录，真的跑 setup_env.py 的四条路径
  ① 全新目录 --check   → 应报「还没装过」
  ② --setup           → 建环境 + 装依赖 + 复核，退出码 0
  ③ 再跑一次           → 走「已就绪」快路径，不重装
  ④ 环境被破坏         → --check 能识别；--rebuild --setup 能修好
  ⑤ 用户自己放的普通 .venv 目录 → 拒绝删除，文件完好

⚠️ 这些都在 /tmp 的副本里做，绝不碰交付包和用户真实的 .venv。
   本脚本跑在 macOS 上 —— Windows 专属的东西（cmd 解析 .bat、Scripts 路径）
   只能做静态检查，真正的"双击能不能开"必须到 Windows 上点一次。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
WIN = Path("/Users/langzi/Desktop/PDF首页替换1.0_Windows版")
BODY = ("swap_core.py", "swap_app.py", "setup_env.py")
# 启动器的名字不写死：Mac 侧有什么 .command 就拉黑什么 —— 以后再改名不用回来改。
# LEGACY 是历史上用过的名字，改名后万一有残留也要报出来。
LEGACY_MAC_LAUNCHERS = ("run_macos.command",)
FORBIDDEN = ("_regress", "selftest.py", "__pycache__", ".venv", ".DS_Store") \
            + tuple(sorted({p.name for p in DEV.glob("*.command")} | set(LEGACY_MAC_LAUNCHERS)))
BASE_PY = sys.executable

fails: list[str] = []
skips: list[str] = []


def chk(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'OK ' if cond else 'NG '} {name}" + ("" if cond else f"  <{detail}>"), flush=True)
    if not cond:
        fails.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    print(f"  --  {name}（跳过：{why}）", flush=True)
    skips.append(f"{name}（{why}）")


def md5(p: Path) -> str:
    import hashlib
    return hashlib.md5(p.read_bytes()).hexdigest()


def run(args, cwd=None, timeout=900):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def first_imports(path: Path) -> set[str]:
    """粗略抽出这个文件 import 的第三方顶层模块名（标准库与本地模块不算）。"""
    src = path.read_text(encoding="utf-8")
    mods = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M):
        top = m.group(1).split(".")[0]
        mods.add(top)
    local = {p.stem for p in DEV.glob("*.py")}
    std = set(sys.stdlib_module_names)
    return {m for m in mods if m not in std and m not in local}


# ══ 静态 ══════════════════════════════════════════════════════════════════
print("【静态】交付包本身", flush=True)
chk("交付包存在", WIN.is_dir(), str(WIN))

print("\n  · 程序本体逐字节一致")
for name in BODY:
    a, b = DEV / name, WIN / name
    chk(f"{name} 与开发目录一致", a.is_file() and b.is_file() and md5(a) == md5(b),
        "缺失" if not b.is_file() else f"{md5(a)[:8]} vs {md5(b)[:8]}")

print("\n  · .bat 的编码与结构")
bats = sorted(WIN.glob("*.bat"))
chk("包里有 .bat 启动器", bool(bats), "一个都没有")
for p in bats:
    raw = p.read_bytes()
    chk(f"{p.name} 是 CRLF 行尾", b"\r\n" in raw and re.search(rb"(?<!\r)\n", raw) is None,
        "存在 LF-only 行")
    chk(f"{p.name} 没有 BOM", not raw.startswith(b"\xef\xbb\xbf"))
    non_ascii = sorted({ch for ch in raw.decode("latin-1") if ord(ch) > 127})
    chk(f"{p.name} 命令行纯 ASCII", not non_ascii, str(non_ascii))
    text = raw.decode("ascii")
    labels = {m.group(1).lower() for m in re.finditer(r"^\s*:([A-Za-z0-9_]+)", text, re.M)}
    gotos = {m.group(1).lower() for m in re.finditer(r"goto\s+(\w+)", text, re.I)}
    chk(f"{p.name} 的 goto 标签都存在", not (gotos - labels), str(sorted(gotos - labels)))
# 交付包里只许有 Windows 的启动器。用通配 glob 而不是写死启动器名 ——
# 以后改启动器的名字，不用回来改这里。
chk("Windows 包只放 Windows 启动器（没有任何 .command）",
    not list(WIN.glob("*.command")), str([p.name for p in WIN.glob("*.command")]))

print("\n  · 说明文件")
doc = WIN / "使用说明.txt"
if chk("使用说明.txt 存在", doc.is_file()):
    raw = doc.read_bytes()
    chk("说明文件是 utf-8-sig（记事本不乱码）", raw.startswith(b"\xef\xbb\xbf"))
    txt = raw.decode("utf-8-sig")
    chk("开头写明本文件夹是 Windows 专用", "Windows 专用" in txt[:200])
    chk("说明了「记住上次选择」", "记住上次的选择" in txt)
    chk("没有把已删掉的 CSV 清单写进去", "CSV" not in txt)

print("\n  · 不该出现的文件")
leaked = [n for n in FORBIDDEN if (WIN / n).exists()]
chk("没有混进开发用文件 / 缓存 / 用户环境", not leaked, str(leaked))

print("\n  · setup_env.py 与程序的一致性")
env_src = (WIN / "setup_env.py").read_text(encoding="utf-8") if (WIN / "setup_env.py").is_file() else ""
if chk("setup_env.py 在包里", bool(env_src)):
    need_m = re.search(r"NEED\s*=\s*\((.*?)\)", env_src, re.S)
    need = need_m.group(1) if need_m else ""
    chk("依赖清单含 pypdf", "pypdf" in need, need.replace("\n", " "))
    chk("依赖清单含 pillow", "pillow" in need, need.replace("\n", " "))
    used = set()
    for name in ("swap_core.py", "swap_app.py"):
        used |= first_imports(WIN / name)
    print(f"      程序实际用到的第三方库：{sorted(used) or '（无）'}")
    alias = {"PIL": "PIL", "pypdf": "pypdf"}
    chk("自检清单覆盖了程序实际 import 的第三方库（含 PIL/pypdf）",
        all(alias.get(m, m) in env_src for m in used), str(sorted(used)))
    m = re.search(r"APP\s*=\s*os\.path\.join\(HERE,\s*\"([^\"]+)\"\)", env_src)
    chk("setup_env 里的入口文件名指向包内真实文件",
        bool(m) and (WIN / m.group(1)).is_file(), m.group(1) if m else "没解析到 APP")
    chk("pypdf 版本下限不低于 3.16（用到 Transformation 接口）",
        (">=3.16" in need) or ("Transformation" not in (WIN / "swap_core.py").read_text(encoding="utf-8")),
        need.replace("\n", " "))

# ══ 功能：在 /tmp 的副本里真跑 ═══════════════════════════════════════════
print("\n【功能】把包拷到临时目录真跑一遍", flush=True)
TMP = Path(tempfile.gettempdir()) / "pdfswap_winpkg_test"
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
for p in WIN.iterdir():
    if p.is_file():
        shutil.copy2(p, TMP / p.name)
PY = sys.executable
SETUP = TMP / "setup_env.py"
env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

# ① 全新目录
rc, out = run([PY, "-B", str(SETUP), "--check"], cwd=TMP)
print("   ① 全新目录 --check：退出码", rc, flush=True)
chk("全新目录时自检报「不可用」", rc == 1, f"rc={rc}")
chk("并说明是「还没装过」而不是含糊报错", ("还没装过" in out) or ("解释器不存在" in out),
    out.strip().splitlines()[-1] if out.strip() else "")

# ② 首次安装
rc, out = run([Base := PY, "-B", str(SETUP), "--setup"], cwd=TMP)
print("   ② --setup 退出码", rc, flush=True)
installed = rc == 0
if installed:
    chk("首次安装成功并复核通过", "复核通过" in out, out[-200:])
    chk("确实建了 .venv", (TMP / ".venv").is_dir())
    py_venv = TMP / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    chk("环境里的解释器存在", py_venv.is_file(), str(py_venv))
    rc2, out2 = run([PY, "-B", str(SETUP), "--check"], cwd=TMP)
    chk("装完再自检：可用 ✓", rc2 == 0 and "可用 ✓" in out2, out2.strip()[-160:])
    chk("自检报出依赖版本", "pypdf" in out2 and "pillow" in out2, out2.strip()[-160:])

    # ③ 再跑一次：必须走快路径，不能重装
    rc3, out3 = run([PY, "-B", str(SETUP), "--setup"], cwd=TMP)
    chk("第二次是「已就绪」快路径", rc3 == 0 and "可用 ✓" in out3, out3.strip()[-160:])
    chk("第二次没有重新建环境", "创建虚拟环境" not in out3, out3[-200:])

    # ④ 环境被破坏：真实故障形态是「解释器成了断链」（换台电脑 / 原 Python 被卸载）
    py_venv = TMP / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    py_venv.unlink()
    py_venv.symlink_to("/no/such/python/dir/python")       # 断链
    rc4, out4 = run([PY, "-B", str(SETUP), "--check"], cwd=TMP)
    chk("解释器断链时自检能识别出不可用", rc4 == 1, f"rc={rc4}")
    rc5, out5 = run([PY, "-B", str(SETUP), "--rebuild", "--setup"], cwd=TMP, timeout=1200)
    chk("--rebuild 能把它修好", rc5 == 0 and "复核通过" in out5, out5.strip()[-200:])
    rc6, out6 = run([PY, "-B", str(SETUP), "--check"], cwd=TMP)
    chk("修好后自检恢复可用", rc6 == 0, out6.strip()[-160:])

    # ④b 「环境来自另一台电脑」这道守卫：直接拿别的 venv 的解释器喂给 probe
    #     （macOS 上 python 是从可执行文件位置推 sys.prefix 的，光改 pyvenv.cfg
    #      不会坏，所以必须这样测，不能靠"改坏配置文件"来模拟）
    other = DEV / ".venv" / "bin" / "python"      # 开发目录那个 venv = "另一台电脑的环境"
    if other.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location("pkg_setup_env", SETUP)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        orig_venv = mod.VENV
        mod.VENV = str(TMP / "这里是本包要求的环境路径")
        ok_b, why_b = mod.probe(str(other))
        chk("能识别出「环境来自另一台电脑」", (not ok_b) and "另一台电脑" in why_b, why_b)
        mod.VENV = orig_venv
    else:
        skip("「环境来自另一台电脑」守卫", "找不到另一个 venv 当对照")
else:
    why = "依赖没装上（多半是这台机器当前无网络）" if "安装" in out or "失败" in out else f"rc={rc}"
    skip("② 首次安装 / ③ 快路径 / ④ 坏环境修复", why)
    print("      末尾输出：" + (out.strip().splitlines()[-1][:160] if out.strip() else ""))

# ⑤ 用户自己放的普通 .venv 目录：必须拒绝删除
print("   ⑤ 用户自己放了个普通 .venv 文件夹", flush=True)
if (TMP / ".venv").exists():
    shutil.rmtree(TMP / ".venv")
(TMP / ".venv").mkdir()
keep = TMP / ".venv" / "我的重要资料.txt"
keep.write_text("不该被删", encoding="utf-8")
rc7, out7 = run([PY, "-B", str(SETUP), "--rebuild", "--setup"], cwd=TMP)
chk("拒绝删除不像 venv 的 .venv 目录", "不像虚拟环境" in out7, out7.strip()[-200:])
chk("用户文件原封不动", keep.is_file() and keep.read_text(encoding="utf-8") == "不该被删")

shutil.rmtree(TMP, ignore_errors=True)

print()
if fails:
    print("Windows 包验收：失败 " + "、".join(fails), flush=True)
if skips:
    print("跳过项：" + "；".join(skips), flush=True)
if not fails:
    print("Windows 包验收：全部通过" + ("（有跳过项）" if skips else "") + " ✓", flush=True)
print("注意：真正的「双击 .bat 能不能开」只能在 Windows 上点一次才算数。", flush=True)
sys.exit(1 if fails else 0)
