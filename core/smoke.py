# -*- coding: utf-8 -*-
"""core/smoke.py — frozen exe 冒烟门禁（v1.0.648 从 backend.py 的 __main__ 块迁出）。

必须住在被打包的包里（core/），不能放 tools/——build 的 hiddenimports 扫描显式
跳过 tools/（v1.0.592 的教训：`from tools import ...` 在正式包里必抛 ImportError）。
行为与原位一致：backend 的 `--smoke` 分支调用 run() 并按返回值 SystemExit。

v1.0.555（P0-11）：打包冒烟入口——frozen exe 内逐个 import 路由/服务模块，
hiddenimports 漏网（曾实证：services.asr 缺席 → 发货 exe 里 ASR 三端点连续
14 次 ImportError，而开发环境永远测不出）立即非零退出。必须位于单实例锁/
看门狗闸门之前（冒烟进程不该撞 mutex）。build.py 打包后自动调用。

v1.0.731（架构建议）：再补两层**真实请求**冒烟——`run_http_ping`（Flask
test_client 走完整 WSGI 栈）+ `run_live_server`（真 bind socket 起 WSGI 服务、
用 urllib 打 /api/ping）。旧冒烟只证明「模块能 import」，验不了路由注册、
before_request 鉴权闸门、after_request 安全头这些**只有请求才会跑**的代码。
两层都只在 frozen exe --smoke 生效：拿不到 app 时源码态软跳过、frozen 态
fail-closed（绝不允许正式包里这层「绿但什么都不查」）。
"""
import os
import sys


def run_http_ping(app, main_mod=None) -> int:
    """frozen 路由栈冒烟：GET /api/ping 必须 200 + ok:true + nosniff 头。

    v1.0.731（架构建议）：旧 --smoke 只验证「模块能 import」，验不了路由注册、
    before_request 鉴权闸门、after_request 安全头这些**只有真实请求才会跑**的代码。
    用 Flask test_client 走完整 WSGI 栈（不占端口、不起服务、零残留进程），
    是「打包版真实启动 + /api/ping」里最稳的一层。
    """
    if app is None or not hasattr(app, "test_client"):
        print("[smoke] FAILED: 拿不到 Flask app，/api/ping 冒烟无法执行（fail-closed）")
        return 1
    _old_auth = getattr(main_mod, "_AUTH_DISABLED", None) if main_mod else None
    try:
        if main_mod is not None and _old_auth is not None:
            main_mod._AUTH_DISABLED = True   # 仅本冒烟进程内，进程随即退出
        with app.test_client() as _c:
            _r = _c.get("/api/ping")
        _data = _r.get_json(silent=True) or {}
        if _r.status_code != 200 or _data.get("ok") is not True:
            print(f"[smoke] FAILED: /api/ping status={_r.status_code} body={_data}")
            return 1
        if _r.headers.get("X-Content-Type-Options") != "nosniff":
            print("[smoke] FAILED: /api/ping 缺 nosniff 响应头（after_request 没跑）")
            return 1
        print("[smoke] OK: /api/ping 200 + ok:true + nosniff（frozen 路由栈可用）")
        return 0
    except Exception as _e:  # noqa: BLE001
        print(f"[smoke] FAILED: /api/ping 冒烟异常：{type(_e).__name__}: {_e}")
        return 1
    finally:
        if main_mod is not None and _old_auth is not None:
            main_mod._AUTH_DISABLED = _old_auth


def run_live_server(app, main_mod=None) -> int:
    """frozen **真实启动**冒烟：起一个 WSGI 服务线程，用真实 HTTP 请求打 /api/ping。

    比 test_client 再进一层：验证打包版真能 bind socket + 承载 HTTP（waitress/
    Werkzeug 栈在冻结包里可用）。用 OS 分配的随机端口，不占 8787、不撞单实例锁、
    进程自测完即退出，零残留。
    """
    if app is None or not hasattr(app, "test_client"):
        print("[smoke-live] FAILED: 拿不到 Flask app（fail-closed）")
        return 1
    import threading
    import urllib.request
    _old_auth = getattr(main_mod, "_AUTH_DISABLED", None) if main_mod else None
    _srv = None
    try:
        from werkzeug.serving import make_server
        if main_mod is not None and _old_auth is not None:
            main_mod._AUTH_DISABLED = True
        _srv = make_server("127.0.0.1", 0, app, threaded=True)
        _port = _srv.server_port
        _t = threading.Thread(target=_srv.serve_forever, daemon=True,
                              name="smoke-live-http")
        _t.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{_port}/api/ping",
                                    timeout=8) as _r:
            _body = _r.read().decode("utf-8", "replace")
            _code = _r.status
        import json as _smoke_json
        try:
            _data = _smoke_json.loads(_body)
        except Exception as _je:  # noqa: BLE001
            print(f"[smoke-live] FAILED: /api/ping 返回非 JSON（{type(_je).__name__}）："
                  f"{_body[:200]}")
            return 1
        if _code != 200 or _data.get("ok") is not True:
            print(f"[smoke-live] FAILED: /api/ping status={_code} body={_body[:200]}")
            return 1
        print(f"[smoke-live] OK: 真实 HTTP 服务 127.0.0.1:{_port} → /api/ping 200")
        return 0
    except Exception as _e:  # noqa: BLE001
        print(f"[smoke-live] FAILED: 真实启动冒烟异常：{type(_e).__name__}: {_e}")
        return 1
    finally:
        if _srv is not None:
            try:
                _srv.shutdown()
            except Exception as _e:  # noqa: BLE001
                print(f"[smoke-live] shutdown 失败（忽略）：{type(_e).__name__}: {_e}")
        if main_mod is not None and _old_auth is not None:
            main_mod._AUTH_DISABLED = _old_auth


def _run_http_layers(app, main_mod):
    """frozen --smoke 的两层真实 HTTP 冒烟接线，返回 0/1。

    app / main_mod 由 backend 的 `--smoke` 分支显式传入：frozen exe 里 backend
    以 __main__ 身份运行，Flask app 与 `_AUTH_DISABLED` 都挂在那个模块对象上，
    before_request 闭包读的也正是它的 __dict__，所以改 `_AUTH_DISABLED` 生效。
    没传时从 sys.modules["__main__"] 兜底发现，兼容其它调用方。

    发现不到 app：源码态软跳过（不是 backend 入口，本层无从谈起）；frozen 态
    fail-closed 返回 1——打包版拿不到 app 还报绿，就是 v1.0.620 那种
    「[smoke] OK: 9 modules」的空闸门，必须红着停下来。
    """
    _main = main_mod or sys.modules.get("__main__")
    if app is None and _main is not None:
        app = getattr(_main, "app", None)
    if main_mod is None:
        main_mod = _main
    if app is None or not hasattr(app, "test_client"):
        if getattr(sys, "frozen", False):
            print("[smoke] FAILED: frozen 态拿不到 Flask app，/api/ping 实跑层"
                  "无法执行（fail-closed，拒绝空闸门报绿）")
            return 1
        print("[smoke] 提示：当前入口未提供 Flask app，跳过 /api/ping 实跑层"
              "（该层只在 frozen exe --smoke 生效）")
        return 0
    if run_http_ping(app, main_mod):
        return 1
    return run_live_server(app, main_mod)


def run(app=None, main_mod=None) -> int:
    import importlib as _smoke_il
    _smoke_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/ 上一级才是项目根
    # v1.0.761：wecom_bridge 改名 bridge——两者都 smoke：bridge 是实体包，wecom_bridge 是
    # 兼容 shim（验证冻结态下老 import 路径仍能导入 = 兼容层没断）。
    _smoke_pkgs = ("routes", "services", "core", "agent", "agent_tools", "desktop", "wecom_bridge", "bridge")
    # v1.0.619 交付时自查发现的口径坑（认下来）：onefile 冻结态里这些包**不以目录形式
    # 存在**（全在 PYZ 归档里），原 `os.walk(_smoke_root/<pkg>)` 只能扫到零星散文件——
    # 所以 619 打包时 `[smoke] OK: 9 modules` 而项目实际有 51+ 个包内模块。
    # 也就是说「--smoke 能发现 hiddenimports 漏模块」这句话长期只在源码态成立，
    # 冻结态几乎没测：正是本轮 `from tools import ...` 那条死路能一路活到发货的同型原因。
    # 现在优先从冻结导入器的归档 TOC 取模块清单（就是包里真正存在的那份），取不到才回落磁盘遍历。
    # 属性名实证：PyInstaller 6.x 运行时是 `finder._pyz_archive.toc`（不是 `_toc`）——
    # 620 那次只写了 `_toc`，所以冻结态依旧报 9 个模块（一个"绿但什么都不查"的门禁）。
    _smoke_env_mods = [x.strip() for x in
                       (os.environ.get("WECOM_SMOKE_MODS") or "").split(",") if x.strip()]
    # ① 首选 build.py 打包时传进来的**源码树模块清单**（最可靠，不猜 PyInstaller 结构，
    #    语义也最对："源码里有的模块，包里必须都导得动"）。
    # ② 退回冻结归档 TOC：`PyiFrozenFinder._pyz_archive.toc`——注意这个 finder 是 **path
    #    finder**，登记在 `sys.path_importer_cache` 而非 `sys.meta_path`（620 写错属性名、
    #    621 首打只翻 meta_path，两次都拿到空清单，冒烟照旧只报 9 个模块）。
    # ③ 再退回磁盘遍历（源码态）。
    _pyz_toc = None
    if not _smoke_env_mods:
        for _smoke_f in (list(sys.meta_path)
                         + list(getattr(sys, "path_importer_cache", {}).values())):
            for _attr in ("_pyz_archive", "_toc"):
                _obj = getattr(_smoke_f, _attr, None)
                _t = getattr(_obj, "toc", None) if _attr == "_pyz_archive" else _obj
                if isinstance(_t, dict) and _t:
                    _pyz_toc = _t
                    break
            if _pyz_toc:
                break
    _smoke_mods = list(_smoke_env_mods)
    if _pyz_toc and not _smoke_mods:
        _smoke_mods = sorted(n for n in _pyz_toc if n.split(".")[0] in _smoke_pkgs)
    elif not _smoke_mods:
        for _sp in _smoke_pkgs:
            _sd = os.path.join(_smoke_root, _sp)
            if not os.path.isdir(_sd):
                continue
            for _sr, _sdirs, _sfiles in os.walk(_sd):
                if "__pycache__" in _sr:
                    continue
                for _sf in _sfiles:
                    if _sf.endswith(".py"):
                        _srel = os.path.relpath(os.path.join(_sr, _sf), _smoke_root)
                        _smoke_mods.append(_srel[:-3].replace(os.sep, "."))
    # fail-loud：冻结态拿不到包内模块清单＝这条门禁在正式包里等于没跑。
    # 宁可让打包红着停下来，也不能再发一个"[smoke] OK: 9 modules"的绿灯。
    if getattr(sys, "frozen", False) and len(_smoke_mods) < 40:
        print(f"[smoke] FAILED: 冻结态只取到 {len(_smoke_mods)} 个本项目模块（期望 ≥40）——"
              "清单来源（PyiFrozenImporter._pyz_archive.toc）失效，"
              "这条门禁正在" "什么都不查" "地报绿。")
        return 1
    # 注意：主脚本自身不进清单——frozen exe 里它以 __main__ 身份存在（PyInstaller
    # 入口脚本不注册为可导入模块名），能跑到本冒烟代码即已证明其导入链完好；
    # 补 import backend 反而触发 ModuleNotFoundError 假失败（1.0.555 首打包实证）。
    # v1.0.647：顶层散件归位（security→core.secrets、model_bench/usage_stats→services、
    # skill_hub_data/skill_versions→agent_tools），冒烟名单同步新家。
    _smoke_mods += ["llm_client", "core.secrets", "security_ops", "core_utils",
                    "services.model_bench", "services.usage_stats", "version",
                    "agent_tools.skill_hub_data", "agent_tools.skill_versions"]
    _smoke_fail = []
    for _sm in _smoke_mods:
        try:
            _smoke_il.import_module(_sm)
        except Exception as _se:  # noqa: BLE001
            _smoke_fail.append(f"{_sm}: {type(_se).__name__}: {_se}")
    if _smoke_fail:
        print(f"[smoke] FAILED {len(_smoke_fail)}/{len(_smoke_mods)} modules:\n  "
              + "\n  ".join(_smoke_fail))
        return 1
    print(f"[smoke] OK: {len(_smoke_mods)} modules imported (frozen bundle)")
    # v1.0.731：import 全通之后继续跑真实 HTTP 冒烟（见 _run_http_layers）。
    # 源码态无 app 时软跳过，frozen 态必跑且拿不到 app 直接判失败。
    return _run_http_layers(app, main_mod)
