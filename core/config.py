# -*- coding: utf-8 -*-
"""core.config — 配置路径/脱敏/参数拾取/JSON 抽取与 Schema 校验/原子写（自 core_utils 拆分 v1.0.402）

v1.0.646：原 core/json_utils.py（94 行）并入本文件。二者本来就耦合——json_utils 的
`_ensure_jsonschema` 是从 core/config.py 迁出去的，config.py 又 re-export 回来；拆成两个
文件只是让这份来回依赖多绕一层。合并后 `core_utils` 的转发表面逐字不变。
"""
# -*- coding: utf-8 -*-
import copy
import os
import re
import json
import threading
from core.secrets import _is_sensitive_key  # noqa: v1.0.647 归位（原顶层 security.py）
# _fallback_dir：config_path() 的不可写回退目录，v1.0.402 拆分时漏导（回退分支必抛 NameError）
from core.paths import _app_dir, _fallback_dir  # noqa: F401

# ---------- jsonschema 可用性探测（v1.0.65 迁移；v1.0.324 惰性化：import 链 ~236ms） ----------
# 原实现在 core/config.py，后随 v1.0.402 拆分进 core/json_utils.py；v1.0.646 并回本文件。
# 惰性化原因：jsonschema 及其依赖的 import 链约 236ms，放在模块级会拖慢每次启动。
_jsonschema = None
_HAVE_JSONSCHEMA = None  # None=未探测


def _ensure_jsonschema():
    """首次使用时导入 jsonschema。可用返回模块并置 _HAVE_JSONSCHEMA=True；
    缺依赖置 False（禁用 schema 校验），与历史语义一致。"""
    global _jsonschema, _HAVE_JSONSCHEMA
    if _HAVE_JSONSCHEMA is None:
        try:
            import jsonschema as _js
            _jsonschema = _js
            _HAVE_JSONSCHEMA = True
        except Exception:  # noqa: BLE001
            _jsonschema = None
            _HAVE_JSONSCHEMA = False
    return _jsonschema


def config_path():
    """配置路径解析（v1.0.739：config 收进 产物库/，顶层只剩 exe + 产物库/）。

    解析顺序：
      ① 产物库/config.json 存在 → 直接用它（新布局 / 已迁移）；
      ② 顶层 config.json 存在 → 仍指向旧位置（兼容尚未迁移的老 dist；
         migrate_runtime_layout 开闸后把旧文件并进产物库，此后自然落到 ①）；
      ③ 应用目录可写 → 返回 产物库/config.json（首启新用户：读路径不建目录，
         守住 v1.0.619 归位时序；首次**写**时由 config_store 补建父目录）；
      ④ 应用目录不可写 → %LOCALAPPDATA%\\<品牌目录>\\config.json（回退口径不变）。

    本函数是**只读口径**：除写权限探测（落一个再删一个临时文件）外无任何副作用，
    归位判定/单实例锁之前的早期调用也安全。
    """
    app_dir = _app_dir()
    from core import paths as _paths   # 延迟导入：core.paths 不依赖 core.config，无环
    ws_cfg = os.path.join(app_dir, _paths.WORKSPACE_NAME, "config.json")
    if os.path.exists(ws_cfg):
        return ws_cfg
    legacy = os.path.join(app_dir, "config.json")
    if os.path.exists(legacy):
        return legacy
    try:
        test = os.path.join(app_dir, ".wc_write_test")
        with open(test, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(test)
        return ws_cfg
    except Exception:  # noqa: BLE001
        pass  # silent-ok: 写权限探测失败即回退 fallback 目录，是预期分支不是故障
    return os.path.join(_fallback_dir(), "config.json")


def mask_key(k):
    """密钥脱敏显示：只露前 2 位。

    v1.0.660（审查修复 L14）：原实现露前 4 后 4（`k[:4] + "****" + k[-4:]`），
    8~16 位的短凭据（如 12 位 token）等于把一大半明文回显给前端/日志。
    现在只露前 2 位——够用户认出"这是哪一把钥匙"，又不足以被拿去试。
    观感保持"前缀 + 星号"形态，长度与原来接近。
    """
    if not k:
        return ""
    return k[:2] + "********" if len(k) > 8 else "****"


def _mask_args(args):
    """对工具参数做脱敏并截断到 200 字符，用于日志。

    v1.0.677：判据迁到 core.masking（唯一事实源）。原实现只按**键名**打码，
    `pc_run` 的命令行口令（`-u admin:s3cret` / `password=xxx`）原样写进
    产物库/日志/companion.log 与 产物库/对话记录/*.md 明文常驻——
    同一语义另一份判据（security_ops._mask_sensitive，按值形态）只管安全审计
    那条链路，管不到这里。现在两份判据合并，字符串值再过一遍值形态脱敏。
    """
    from core.masking import mask_args as _ma
    return _ma(args)
def _pick_int(args, key):
    """取整数参数，不是整数则当 None（与旧 _pick 行为一致）。"""
    v = args.get(key)
    return v if isinstance(v, int) else None


def _pick_flex(args, key="pick", default=None):
    """v1.0.413（pick 序号漂移修复）：工具 pick 参数弹性解析——
    int / 数字字符串 → int（索引寻址）；其他非空字符串 → 原样返回
    （标题/URL 模糊寻址，由 wecom_bridge.pick_window 解析）。
    背景：窗口开关后 CDP target 序号漂移，模型手里的旧 pick 要么越界
    要么指向错窗口——标题寻址（如"待办事宜"）跨窗口开关稳定。

    v1.0.592 修复（P0）：default 由 0 改为 None。原实现 `args.get(key, 0)` 让
    【省略 pick】等于【显式 pick=0】，而 `windows._page_ws` 的「跟随窗口 → 正文最长页」
    自动选择分支只在 `pick is None` 时可达（`if pick is not None: … return pages[_pi]`），
    于是那条分支对所有 agent 工具永久不可达——v1.0.192 起的点击跟随、registry 里
    写明的「不传则自动选正文最长的窗口」「省略则自动选当前窗口」全部落空，多窗口时
    一律打在 index 0（通常是企微主聊天窗）。
    同时归一模型显式传的 null：`str(None)`="None" 会被当成标题子串去模糊匹配，
    匹不到 → 返回「没有可用的企微窗口」这种误导性报错；现在 null/空串/"None" 一律视为省略。"""
    v = args.get(key, default)
    if v is None:
        return default
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    if not s or s == "None":
        return default
    return s


# ---------- 以下为 v1.0.646 自 core/json_utils.py 并入（函数体逐字未改） ----------
# ---------- 原子写（v1.0.510 优化 P2-13：原 16 处手写 tmp+os.replace 样板收敛） ----------
def atomic_write_json(path, obj, indent=2, ensure_ascii=False):
    """原子写 JSON 文件：先写 <path>.tmp 再 os.replace，崩溃不会留半截文件。
    调用方如需并发安全请自带锁（本函数只保证单次写不产生损坏文件）。"""
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=ensure_ascii, indent=indent)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    """原子写文本文件（UTF-8）。语义同 atomic_write_json。"""
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------- 结构化抽取 ----------
def extract_json(text):
    if not text:
        return None
    s = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass  # 预期：JSON 提取失败返回 None，由调用方兜底
    opens = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if opens:
        start = min(opens)
        end = max(s.rfind("}"), s.rfind("]"))
        if end > start:
            try:
                return json.loads(s[start:end + 1])
            except Exception:  # noqa: BLE001
                pass  # 预期：截取段非合法 JSON 时返回 None
    return None


def validate_schema(data, schema):
    if _ensure_jsonschema() is None:
        return "jsonschema 未安装，跳过校验"
    try:
        _jsonschema.validate(instance=data, schema=schema)
        return None
    except Exception as e:  # noqa: BLE001
        return f"Schema 校验不通过：{e}"


# ---------- v1.0.660（B7）：配置只读视图 + 显式拷贝接口 ----------
# 现状：load_config() 每次都把整份 config 深拷贝一遍（含 skill_overrides 里
# 单条超 8KB 的技能提示词），而 /api/status 8s 一拍、各路由随手调一次 →
# 每秒都在拷一份没人改的大对象。
#
# 只读视图的**容器仍是 dict/list 子类**（不是 MappingProxyType）——原因有二：
#   ① json.dumps / isinstance(x, dict) 在全仓大量使用，MappingProxyType 会让
#      它们分别抛 TypeError 与返回 False，把只读视图变成一颗定时炸弹；
#   ② 下游序列化（把 config 塞进 LLM 请求体等）需要它是真正的 dict。
# 写操作被显式禁掉（抛错），因此"外部通过只读包装拿到可变内部引用"这条路不通。

class FrozenDict(dict):
    """只读 dict：仍是 dict 子类（可 json.dumps / isinstance 为真），写入抛错。"""

    __slots__ = ()

    def _readonly(self, *a, **k):
        raise TypeError("配置为只读视图；需要修改请用 core.config.config_copy() 取得可写副本")

    __setitem__ = _readonly
    __delitem__ = _readonly
    update = _readonly
    pop = _readonly
    popitem = _readonly
    clear = _readonly
    setdefault = _readonly

    def __copy__(self):
        # 显式拷贝语义：拷贝出来就是可写的普通 dict（否则 copy.deepcopy 会
        # 走 __setitem__ 逐项回填 → 撞上只读拦截抛 TypeError）。
        return dict(self)

    def __deepcopy__(self, memo):
        import copy as _copy
        return _copy.deepcopy(dict(self), memo)


class FrozenList(list):
    """只读 list：仍是 list 子类，写入抛错。"""

    __slots__ = ()

    def _readonly(self, *a, **k):
        raise TypeError("配置为只读视图；需要修改请用 core.config.config_copy() 取得可写副本")

    __setitem__ = _readonly
    __delitem__ = _readonly
    append = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    clear = _readonly
    sort = _readonly
    reverse = _readonly

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        import copy as _copy
        return _copy.deepcopy(list(self), memo)


def freeze(obj):
    """递归把 dict/list 包成只读容器（其余类型原样返回）。"""
    if isinstance(obj, dict):
        return FrozenDict((k, freeze(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return FrozenList(freeze(v) for v in obj)
    return obj


def config_copy(cfg):
    """显式取一份**可写**深拷贝。

    v1.0.660（B7）：依赖"load_config() 返回值可随意修改"的调用方（如
    routes/wecom.py 的 ensure_binding 往 cfg['wecom'] 写绑定四元组），
    改用本函数取可写副本——写操作语义不变，只是把"深拷贝"从
    load_config 的隐式行为变成显式意图。

    v1.0.702：改用 copy.deepcopy（原 json 往返）。等价性：配置内容恒为纯
    JSON 数据（磁盘 json 解析 + DEFAULT_CONFIG 合并），deepcopy 不经过
    序列化、更快，且 FrozenDict/FrozenList 已实现 __deepcopy__ 返回可写的
    普通 dict/list，只读视图输入同样得到可写副本。
    """
    return copy.deepcopy(cfg)


# 进程内只读配置快照缓存。
#
# v1.0.660（修复）：缓存键与有效性判定必须与 core.config_store 的缓存口径
# **完全一致**，否则会出现"读到别的配置文件内容"这种极难查的串味：
#   ① 键必须含**配置路径**——config_path 可被替换（多实例/测试切目录），
#      两份 mtime+size 相同的文件不能互相命中；
#   ② 路径必须取 **config_store 实际在用的那个**（config_store.config_path），
#      不能用本模块的 config_path()——那是另一个函数对象，替换的是前者；
#   ③ 有效性跟随 config_store 的缓存状态（path + stamp + cfg 是否在位），
#      这样 save_config 主动作废缓存后，本视图同步失效，不会读到旧配置。
_CFG_VIEW_CACHE = {"key": None, "view": None, "src": None}
_CFG_VIEW_LOCK = threading.RLock()


def load_config_view():
    """返回配置的**只读快照**（进程内缓存，配置文件变更即失效）。

    与 load_config() 的语义差别只有一处：返回值不可写。读取方（/api/status
    等高频只读路径）用它即可省掉每次那份大深拷贝；确需修改配置的调用方
    继续用 load_config()，或用 config_copy(load_config_view()) 显式取可写副本。

    v1.0.660（B7）：稳态（配置未变）下直接复用同一份只读视图，零拷贝返回；
    配置一变才重建（重建时复用 config_store 已解密的缓存对象，不再额外深拷贝）。
    """
    # 延迟导入：core.config_store 依赖 core_utils → core.config，模块级互相 import 会成环
    from core import config_store
    with _CFG_VIEW_LOCK:
        cp = config_store.config_path()
        st = config_store._cfg_stamp(cp)
        cache = config_store._CFG_CACHE
        src = cache.get("cfg")
        # config_store 缓存有效 → 它的缓存对象就是"当前配置"的权威副本
        if src is not None and cache.get("path") == cp and cache.get("stamp") == st:
            key = (cp, st, id(src))
            if _CFG_VIEW_CACHE["key"] == key:
                return _CFG_VIEW_CACHE["view"]
            view = freeze(src)
            _CFG_VIEW_CACHE["key"] = key
            _CFG_VIEW_CACHE["view"] = view
            _CFG_VIEW_CACHE["src"] = src      # 持引用：防 id(src) 被复用
            return view
    # 慢路径：config_store 缓存无效（首次读盘 / 刚写盘被作废 / 文件已变）
    config_store.load_config()
    with _CFG_VIEW_LOCK:
        cache = config_store._CFG_CACHE
        src = cache.get("cfg")
        if src is None:                       # 读盘异常降级时的兜底
            src = config_store.load_config()
        key = (cp, config_store._cfg_stamp(cp), id(src))
        view = freeze(src)
        _CFG_VIEW_CACHE["key"] = key
        _CFG_VIEW_CACHE["view"] = view
        _CFG_VIEW_CACHE["src"] = src
        return view


# ---------- v1.0.660（B8）：加载期字段校验（复用 validate_schema） ----------
def schema_ok(data, schema):
    """按 schema 校验 data，返回 (是否通过, 错误描述)。

    jsonschema 未安装时视为**通过**（与 validate_schema 的"跳过校验"语义一致）——
    缺依赖不该让整个功能瘫掉，故把"跳过"与"不通过"区分开。
    """
    err = validate_schema(data, schema)
    if err is None:
        return True, ""
    if "未安装" in err:      # 跳过校验 → 视为通过
        return True, ""
    return False, err


def valid_or_default(data, schema, defaults, label=""):
    """按 schema 校验 data；不通过则用 defaults 兜底（fail-closed，不抛崩）。

    返回 (是否用了默认值, 数据)。字段缺失/类型不对时用安全默认值补齐——
    坏数据不该让整个功能瘫掉，但也不能带着坏数据继续跑。
    """
    ok, err = schema_ok(data, schema)
    if ok:
        return False, data
    merged = dict(defaults or {})
    if isinstance(data, dict):
        for k, v in data.items():
            if k in merged or not merged:
                merged[k] = v
    return True, merged


# ---------- v1.0.660（B4）：全局每日 token 预算 ----------
# 默认 200 万 token/天。用户可在 config.json 的 "agent" 段改 "daily_token_budget"；
# 缺键（或非法值）一律用这里的默认值——不因为配置没写就失去预算闸。
DAILY_TOKEN_BUDGET_DEFAULT = 2000000


def daily_token_budget(cfg):
    """取每日 token 软预算（用户可配，缺键用默认值）。

    0 = 不限制（与本仓其他预算项的 0 语义一致）。
    """
    ag = (cfg or {}).get("agent")
    if not isinstance(ag, dict):
        return DAILY_TOKEN_BUDGET_DEFAULT
    v = ag.get("daily_token_budget")
    if v is None:
        return DAILY_TOKEN_BUDGET_DEFAULT
    try:
        n = int(v)
    except (TypeError, ValueError):
        return DAILY_TOKEN_BUDGET_DEFAULT
    return max(0, n)


def _valid_json_schema(schema):
    """校验 output_schema 是否为合法 JSON Schema；环境无 jsonschema 库时退化为基本类型检查。"""
    if not isinstance(schema, dict):
        return False
    if _ensure_jsonschema():
        try:
            _jsonschema.Draft7Validator.check_schema(schema)
            return True
        except Exception:  # noqa: BLE001
            return False
    return schema.get("type") in ("object", "array", "string", "number", "integer", "boolean", "null")
