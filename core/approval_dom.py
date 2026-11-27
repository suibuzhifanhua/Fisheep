# -*- coding: utf-8 -*-
"""core.approval_dom — 流程状态词的 **DOM 级硬闸**（评审 P1-5）。

历史上「绝不自动点同意 / 提交 / 通过 / 退回 / 保存 / 用印」主要写在技能提示词里，
`security_ops.approval_word_hit` 也只按**模型生成的参数文本**做语义匹配——同义改写、
新按钮文案、或模型换个说法都能绕过。对企业审批流这是「不可接受但可发生」的失效。

本模块是**执行器层的最后兜底**：
  · `dom_approval_hit()` 对最终点击目标 / 元素属性（text/id/name/aria-label）
    按归一化词表判定，含大量同义改写（验收要求「不在提示词示例里的同义文案」也拦）；
  · 命中且未预授权 → `guard_click()` 返回结构化拒绝并写安全审计；
  · 用户在配置 `security.dom_approval_allowlist` 预先登记的白名单文案放行（报告明确
    允许的「预先登记白名单」替代方案），放行同样留痕。

提示词层保留做「提前劝阻」，本层做「最后兜底」，两层互不替代。
"""
import re
import threading
import time

# 流程状态词表。刻收录同义改写与英文，覆盖「换个说法的按钮」。
FLOW_STATE_WORDS = (
    "同意", "批准", "审批通过", "审核通过", "予以通过", "准予通过", "通过",
    "提交", "确认提交", "最终提交", "递交", "上报", "送审",
    "退回", "驳回", "否决", "拒绝", "保存", "保存并提交", "提交保存",
    "用印", "盖章", "签发", "发布", "办结", "归档", "结案",
    "确认同意", "同意并继续", "核准", "审定", "批准通过", "确认生效",
    "approve", "approved", "submit", "confirm", "sign", "seal", "finalize",
)

_ALLOW_TTL = 5.0
_allow_cache = {"ts": 0.0, "items": frozenset()}
_cache_lock = threading.Lock()


def _norm(text):
    """归一化：全角转半角、小写、只留字母数字与汉字（去空白/标点/分隔符）。"""
    s = str(text or "").strip().lower()
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", "".join(out))


def dom_approval_hit(*texts):
    """任一字段命中词表 → 返回命中的词；否则 None。"""
    for t in texts:
        n = _norm(t)
        if not n:
            continue
        for w in FLOW_STATE_WORDS:
            if _norm(w) in n:
                return w
    return None


def _allowlist():
    """读用户预授权白名单（带 5s 缓存，避免每次点击都反序列化整份配置）。"""
    now = time.time()
    with _cache_lock:
        if now - _allow_cache["ts"] < _ALLOW_TTL:
            return _allow_cache["items"]
    items = frozenset()
    try:
        from core.config_store import load_config_view
        cfg = load_config_view() or {}
        raw = (cfg.get("security") or {}).get("dom_approval_allowlist") or []
        if isinstance(raw, list):
            items = frozenset(_norm(x) for x in raw if str(x).strip())
    except Exception:  # noqa: BLE001  silent-ok: 配置不可读按「无白名单」处理（最保守）
        items = frozenset()
    with _cache_lock:
        _allow_cache["ts"] = now
        _allow_cache["items"] = items
    return items


def is_preapproved(target, allowlist=None):
    """`*` 全放行；否则按归一化文案相等或包含匹配。"""
    allow = _allowlist() if allowlist is None else frozenset(_norm(x) for x in allowlist)
    if "*" in allow:
        return True
    n = _norm(target)
    return bool(n) and any(a and a in n for a in allow)


def _audit(target, hit, allowed, reason):
    try:
        from security_ops import audit
        audit("dom_guard", "点击目标 %s 命中流程状态词 %s" % (str(target)[:120], hit),
              allowed, reason)
    except Exception:  # noqa: BLE001  silent-ok: 审计写不进去也不能拖断点击链路
        return


def guard_click(target, element_meta=None, name="click_wecom_element"):
    """执行器层硬闸。返回 None 放行；命中红线且未放行时返回结构化结果。

    element_meta 可携带真实 DOM 属性（text/id/name/aria_label/value/title），
    供 CDP 侧取到元素属性后传入；数字索引 target 本身无法反映按钮文案，
    故这条路径必须靠 element_meta 才能覆盖（见 docs/testing-contracts.md 边界说明）。

    name 用于审批凭据的 (name, target) 命名空间（v1.0.759 D8 ①）——闸门放行登记
    的短时凭据、以及硬闸自己发起审批后被批准的一次性令牌，都以它为键，保证
    「用户批的那一次」与「硬闸要的那一次」是同一枚凭据，同一次批准不弹两遍窗。
    """
    texts = [str(target)] if target is not None else []
    if isinstance(element_meta, dict):
        for k in ("text", "innerText", "id", "name", "aria_label", "ariaLabel",
                  "value", "title", "placeholder"):
            v = element_meta.get(k)
            if v:
                texts.append(str(v))
    hit = dom_approval_hit(*texts)
    if not hit:
        return None
    if is_preapproved(target):
        _audit(target, hit, True, "用户预先登记白名单放行")
        return None
    # v1.0.759（D8 ①）：闸门放行时登记的短时凭据 / 硬闸自己审批被批准后的一次性令牌
    # → 放行（留痕），不再弹第二遍窗。改回旧写法（无条件拒绝）→ 文本 target 命中审批词
    # 时用户先批闸门、硬闸再弹第二遍 → tests 里 test_dom_guard_* 必红。
    from security_ops import dom_approval_state as _dom_state, dom_approval_hash as _dom_hash
    _ok, _why = _dom_state(name, target)
    if _ok:
        _audit(target, hit, True, "DOM 级硬闸：%s" % (_why or "用户已批准"))
        return None
    _audit(target, hit, False, "DOM 级硬闸：流程状态词命中且未预授权，需用户确认")
    # 保留 ok/needs_confirmation/dom_guard_hit（既有契约测试依赖），同时带上
    # __approval_needed__ + hash + kind:dom，让 loop 走通用的
    # 挂起→审批弹窗→/api/security/grant 一次性令牌→原样重试 管道（不新增第二套机制）。
    return {"ok": False,
            "error": ("点击目标命中流程状态词「%s」——这是不可撤销的业务写入，"
                      "执行器层已拦截并弹出确认。请在弹窗中批准本次点击"
                      "（或在安全设置 dom_approval_allowlist 预先登记该按钮文案）。" % hit),
            "dom_guard_hit": hit,
            "needs_confirmation": True,
            "__approval_needed__": True,
            "kind": "dom",
            "tool": name,
            "hash": _dom_hash(name, target),
            "command": str(target)[:120],
            "reason": "点击目标命中流程状态词「%s」——不可撤销的业务写入，需人工确认" % hit}
