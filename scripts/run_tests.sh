#!/usr/bin/env bash
# 一键跑 test/ 下的历史纯 assert 脚本 + pytest（含覆盖率）。
#
# 用法:
#   bash scripts/run_tests.sh
#   PYTHON=/path/to/python bash scripts/run_tests.sh
#
# 说明:
# - 历史脚本是「纯 assert + __main__ 自跑」风格，失败时退出码非 0；
# - test_music_duration.py / test_music_tags.py 需要真实 music/ 音频，默认跳过；
# - pytest 的覆盖率参数写在 pyproject 的 [tool.pytest.ini_options].addopts 中。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
PY="${PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PY" ]; then
  echo "找不到解释器: ${PY}（可用 PYTHON=... 覆盖）" >&2
  exit 1
fi

# 需要真实音频目录、无法离线跑的脚本
SKIP_SCRIPTS="test_music_duration.py test_music_tags.py"

echo "== 1/2 历史纯 assert 脚本 =="
script_failed=0
for path in test/test_*.py; do
  name="$(basename "$path")"
  skip=0
  for s in $SKIP_SCRIPTS; do
    if [ "$name" = "$s" ]; then skip=1; fi
  done
  if [ "$skip" = "1" ]; then
    echo "  SKIP ${name}（依赖真实 music/ 音频）"
    continue
  fi
  logfile="$(mktemp)"
  if "$PY" "$path" >"$logfile" 2>&1; then
    echo "  PASS $name"
  else
    echo "  FAIL $name"
    tail -n 20 "$logfile" | sed 's/^/      /'
    script_failed=1
  fi
  rm -f "$logfile"
done

echo
echo "== 2/2 pytest + 覆盖率 =="
pytest_failed=0
"$PY" -m pytest -q || pytest_failed=1

echo
if [ "$script_failed" -ne 0 ] || [ "$pytest_failed" -ne 0 ]; then
  echo "结果: 失败（脚本=${script_failed} pytest=${pytest_failed}）"
  exit 1
fi
echo "结果: 全部通过"
