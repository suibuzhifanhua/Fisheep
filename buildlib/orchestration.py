# -*- coding: utf-8 -*-
"""buildlib.orchestration — 打包执行段：杀旧进程、跑 PyInstaller、冒烟、中间产物清理。

自 build.py main() 内联块逐字迁出（v1.0.65x 拆分）；调用顺序仍在 main() 里显式排定。
"""
import glob
import os
import shutil
import subprocess
import sys
import time

from buildlib.env import BASE
from buildlib.spec_refresh import _SPEC_SCAN_PKGS, _source_module_list


def kill_running_app():
    # PyInstaller 写入新 exe 前要 os.remove 旧 exe——若旧进程还在持锁会 PermissionError 失败。
    # 主动 taskkill + 等待文件锁释放，避免每次手工先杀进程。
    # v1.0.680（改名）：新旧 exe 名都杀——改名过渡期老版可能还在跑，
    # 它持着旧 exe 的文件锁，会让新 exe 写不进去。
    try:
        from core.branding import ALL_EXE_NAMES   # v1.0.691：三代 exe 名都杀（老版持文件锁会挡新 exe 写入）
        for _img in ALL_EXE_NAMES:
            subprocess.run(
                ["taskkill", "/f", "/im", _img],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
        time.sleep(2)  # Windows 释放文件句柄需要一点时间
    except Exception:  # noqa: BLE001
        pass  # 没在跑也无所谓：taskkill 返回非 0 exit code 会被 run 当异常，捕获忽略


def rollback_version(old_v):
    """把 version.py 回滚到 old_v（B20：走 versioning.set_version，不另写一份正则实现）。

    v1.0.660（审查修复 B7/B20）：这是**唯一**的版本号回滚入口——
    PyInstaller 失败时用；bump 之后任意一步失败（冒烟失败/资源不一致）也用，
    避免"版本号已 +1 却无合格产物"的空号形态（376 空号那类事故）。

    幂等：已经是 old_v 时不再改写（打包失败路径里 run_pyinstaller 已回滚过一次，
    main() 的兜底再调一次不应刷两行同样的回滚日志）。
    """
    try:
        from buildlib.versioning import current_version, set_version
        if current_version() == old_v:
            print(f"[build] 版本号已是 v{old_v}（此前已回滚，无需重复处理）")
            return True
        set_version(old_v)
        print(f"[build] 版本号已回滚至 v{old_v}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：版本号回滚失败：{e}")
        return False


def run_pyinstaller(cmd, env, old_v):
    """执行 PyInstaller 并做「本项目包内模块缺失」升级拦截；失败回滚版本号后重新抛出。

    失败回滚版本号——版本与产物一一对应（376 空号另一半根因：PyInstaller 失败时
    version.py 已 +1 且无对应产物）。version_info.txt 可留给下次覆盖；
    版本登记/产物校验随异常跳过。

    v1.0.660（审查修复 B8）：改用 Popen **逐行实时透传** stdout/stderr——
    打包要跑好几分钟，原实现 capture_output=True 全程零输出，用户只能对着
    空白屏幕猜是不是卡死了；实时输出还能让人一眼看出卡在哪一步。
    同时把输出累积到内存，供下面的 missing-module 扫描使用（输入来源不变：
    stdout+stderr 合并 + build/*/warn-*.txt）。
    """
    try:
        # v1.0.555（P0-11）：捕获输出回显——PyInstaller 缺模块只 warning 不失败，
        # 原 check=True 只看退出码、警告被完全忽略。本项目包内模块的 missing
        # warning 现升级为构建失败（第三方缺失仍只警告，避免误杀可选依赖）。
        # v1.0.652（审计 D10）：bytes + utf-8/replace 解码——旧 text=True 走 locale
        # （cp936），PyInstaller 输出含中文路径/警告时 UnicodeDecodeError → 落进
        # except → 版本回滚 + 把合格构建误判为失败（v1.0.621 冒烟段同款教训）。
        # v1.0.660（B8）：stderr 合并进 stdout 一起透传——保持"终端看到的顺序 =
        # 实际发生顺序"，也免去两路管道各自读取的死锁风险。
        _proc = subprocess.Popen(cmd, cwd=BASE, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _chunks = []
        for _line in _proc.stdout:
            _chunks.append(_line)
            sys.stdout.write(_line.decode("utf-8", "replace"))
            sys.stdout.flush()
        _proc.stdout.close()
        _rc = _proc.wait()
        _out = b"".join(_chunks).decode("utf-8", "replace")
        _warn_txt = ""
        try:
            for _wf in glob.glob(os.path.join(BASE, "build", "*", "warn-*.txt")):
                with open(_wf, encoding="utf-8", errors="ignore") as _f:
                    _warn_txt += _f.read() + "\n"
        except Exception:  # noqa: BLE001
            pass
        # v1.0.652（审计 D7）：补双引号/无引号形态 + 扫 warn-*.txt——"missing module
        # named X" 由 PyInstaller 写进 warnfile 不进 stdout，旧过滤分支对它恒为空（死条件）。
        _scan_src = _out + "\n" + _warn_txt
        _own_missing = sorted({ln.strip() for ln in _scan_src.splitlines()
                               if ("missing module" in ln.lower() or "hidden import" in ln.lower())
                               and any(f".{p}." in ln or f"'{p}" in ln or f'"{p}' in ln or f"{p}." in ln
                                       for p in _SPEC_SCAN_PKGS)})
        if _own_missing:
            raise RuntimeError("PyInstaller 报告本项目包内模块缺失（hiddenimports 不完整）：\n  "
                               + "\n  ".join(_own_missing[:20]))
        if _rc != 0:
            raise RuntimeError(f"PyInstaller 退出码 {_rc}")
    except Exception:
        # v1.0.470 修复（P1-21）：打包失败回滚版本号——版本与产物一一对应（376 空号
        # 另一半根因：PyInstaller 失败时 version.py 已 +1 且无对应产物）。
        # version_info.txt 可留给下次覆盖；版本登记/产物校验随异常跳过。
        # v1.0.660（B20）：回滚实现收敛到 rollback_version()（复用 versioning.set_version），
        # 不再在此处另写一份正则重写——两份实现漂移过一次就会写出坏的 version.py。
        rollback_version(old_v)
        raise


def decode_smoke_stream(raw):
    """v1.0.736（遗留①）：frozen exe 冒烟输出按流自适应解码。

    实测（2026-11-20，dist/Fisheep.exe --smoke 原始字节）：纯 ASCII 行
    `[smoke] OK: 118 modules imported` utf-8 可解，含中文的「frozen 路由栈可用」
    /「真实 HTTP 服务」两行是 **cp936 字节**——因为 console=False 的 frozen exe
    里 sys.stdout 仍是 Windows locale 编码。旧实现无脑
    `.decode("utf-8", "replace")` 把它们全变替换字符（乱码）。backend.py 已在
    frozen 态把输出统一成 utf-8；这里对旧包/边界再做兜底：优先 utf-8，失败
    回退 cp936，两种包都能正确显示中文。
    """
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp936", "replace")


def run_smoke(dist_exe, env):
    """v1.0.555（P0-11）：打包后冒烟——frozen exe 内 import 全部路由/服务模块
    （backend --smoke，位于单实例锁之前不撞 mutex）。原"PyInstaller 报告成功即
    发货"让 asr 等模块只在发货 exe 里死、开发环境永远测不出（同根因事故两次）。
    v1.0.619（审查⑧）：包内前端资源与源码树逐字节核对——--smoke 只管 import，
    管不了"exe 里带的还是旧 index.html/js"（历史上真出过 dist 落后 4 个文件）。
    冒烟清单由 build 侧生成后经 WECOM_SMOKE_MODS 传进 exe：冻结态里 PyInstaller 的
    path finder 不在 sys.meta_path（在 sys.path_importer_cache），exe 自己枚举包内模块
    曾两次拿到空清单 → 冒烟长期只报 9 个模块、却打「import 全通」。源码树在这里是
    现成的，由它给清单最可靠，语义也更对：**源码里有的模块，包里必须都导得动**。"""
    try:
        _mods = _source_module_list()
        if len(_mods) < 40:
            raise RuntimeError(f"源码树模块清单异常（只有 {len(_mods)} 个）")
        _smoke_env = {**env, "WECOM_NO_GUI": "1", "WECOM_SMOKE_MODS": ",".join(_mods)}
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：冒烟清单生成失败（退回 exe 自查，覆盖面会小）：{e}")
        _smoke_env = {**env, "WECOM_NO_GUI": "1"}
    print(f"[build] 冒烟清单：{len(_smoke_env.get('WECOM_SMOKE_MODS', '').split(',')) if 'WECOM_SMOKE_MODS' in _smoke_env else 0} 个源码模块")
    try:
        _smoke = subprocess.run([dist_exe, "--smoke"], capture_output=True,
                                timeout=180, cwd=os.path.dirname(dist_exe),
                                env=_smoke_env)
        # v1.0.621：拿字节不用 text=True（cp936 会让读取线程抛 UnicodeDecodeError，
        # 真实失败原因「哪些模块导不动」全部丢失）。v1.0.736：再按流自适应解码，
        # utf-8 优先 / cp936 回退，中文不再乱码（见 decode_smoke_stream）。
        _smoke_out = (decode_smoke_stream(_smoke.stdout)
                      + decode_smoke_stream(_smoke.stderr))
        sys.stdout.write(_smoke_out[-6000:])
        if _smoke.returncode != 0:
            raise SystemExit("[build] 打包冒烟失败：frozen exe 内模块 import 异常"
                             "（hiddenimports 漏网）——详见上方 [smoke] 输出")
        print("[build] 冒烟通过：frozen exe 模块 import 全通")
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"[build] 打包冒烟执行异常（视为失败，宁可拦截不静默发货）：{e}")


def clean_pyinstaller_mid():
    # v1.0.301：版本垃圾策略——打包成功后清 PyInstaller 中间产物
    # （build/<exe名>/ 目录 ~183M：.toc/.pkg/.pyz/localpycs，纯过程文件）。
    # build/_dist_backup/（~21M）保留：config 最后防线，下次 build 会重新覆盖。
    # 若需查打包 warn/xref 诊断，重跑一次 build 即会重新生成。
    # v1.0.680（改名）：目录名随 exe 名（spec 文件名决定），旧名目录一并清掉
    # （改名后 PyInstaller 会另开新目录，旧目录是纯死重）。
    try:
        from core.branding import ALL_APP_IDS   # v1.0.691：三代中间产物目录都清
        for _stem in ALL_APP_IDS:
            _mid = os.path.join(BASE, "build", _stem)
            if os.path.isdir(_mid):
                shutil.rmtree(_mid)
                print(f"[build] 已清理 PyInstaller 中间产物 build/{_stem}/（~183M）")
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：中间产物清理失败（可手工 rm -rf build/<exe名>）：{e}")
