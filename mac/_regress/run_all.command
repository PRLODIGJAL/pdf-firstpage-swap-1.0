#!/bin/bash
# 改完代码跑这一条：核心自测 + 界面端到端 + 记忆功能 + 真实语料（同文件夹模式）+ 渲染核对
# 用法：_regress/run_all.command
cd "$(dirname "$0")/.." || exit 1
PY=./.venv/bin/python
fail=0

echo "════ 1/6 核心自测（selftest.py）"
$PY -B selftest.py | tail -3 || fail=1

echo
echo "════ 2/6 界面驱动端到端（verify_gui_e2e.py，两套模式）"
$PY -B _regress/verify_gui_e2e.py | tail -4 || fail=1

echo
echo "════ 3/6 记住上次选择（verify_config_memory.py）"
$PY -B _regress/verify_config_memory.py > /tmp/verify_cfg.log 2>&1
rc=$?
tail -2 /tmp/verify_cfg.log
[ $rc -ne 0 ] && echo "  ！失败明细见 /tmp/verify_cfg.log" && fail=1

echo
echo "════ 4/6 真实语料验收（verify_real_corpus.py，两文件夹模式）"
$PY -B _regress/verify_real_corpus.py > /tmp/verify_real.log 2>&1
rc=$?
tail -3 /tmp/verify_real.log
[ $rc -ne 0 ] && echo "  ！失败明细见 /tmp/verify_real.log" && fail=1

echo
echo "════ 5/6 真实语料·同文件夹模式（verify_real_hybrid.py）"
$PY -B _regress/verify_real_hybrid.py > /tmp/verify_hyb.log 2>&1
rc=$?
tail -3 /tmp/verify_hyb.log
[ $rc -ne 0 ] && echo "  ！失败明细见 /tmp/verify_hyb.log" && fail=1

echo
echo "════ 6/6 渲染像素对拍（verify_render.py，需 /tmp/render_venv）"
if [ -x /tmp/render_venv/bin/python ]; then
  /tmp/render_venv/bin/python -B _regress/verify_render.py | tail -3 || fail=1
else
  echo "  跳过：没有 /tmp/render_venv（需要时再建，别装进交付 venv）"
fi

echo
[ $fail -eq 0 ] && echo "全部通过 ✓" || echo "有失败项，别急着交付 ✗"
echo "（要交付 Windows 版时另跑：_regress/run_win.command —— 同步 + 验收交付包）"
exit $fail
