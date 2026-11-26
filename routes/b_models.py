# -*- coding: utf-8 -*-
"""routes.b_models — 模型管理 API（列表/评测/扫描/导入导出）
"""
# -*- coding: utf-8 -*-
import json
import re
import time
import uuid

from flask import Blueprint, request, Response, jsonify, stream_with_context

from core.logging import _lazy_requests as requests
from core.config import mask_key
from core.logging import log_event
from core.paths import workspace_root
from core.config_store import load_config, update_config, _save_config_safe  # noqa: F401（_save_config_safe 兼容旧导入）
from core.strings import clean_url, strip_invisible as clean_text   # v1.0.621：零宽字符/BOM 清洗单点


class _ModelApplyError(Exception):
    """v1.0.555（P0-10）：update_config 事务内 _apply_models 校验失败——调用方转 400。"""
from llm_client import resolve_model, _probe_local_models, _LOCAL_MODEL_ENDPOINTS

from version import get as _version_get  # v1.0.510（P2-13）：版本兜底块收敛到 version.get
VERSION = _version_get()


bp = Blueprint("mgmt_models", __name__)

# v1.0.407 修复：v1.0.402 拆分 management→b_models 时 GET 装饰器遗失（POST /models 在，
# GET 404）——前端 loadModels 拿不到列表，表现为「模型添加不了/列表加载失败」。
# 同期 b_admin.api_experiences_list 同款丢失，一并修复（AST 扫描 routes/ 全量确认无第三处）。
@bp.route("/models", methods=["GET"])
def get_models():
    cfg = load_config()
    return jsonify([mask_models(m) for m in cfg.get("models", [])])


@bp.route("/models/bench7", methods=["POST"])
def api_models_bench7():
    """模型综合评测（v1.0.319）：7 项常用能力打分制，SSE 逐项推送进度。
    body: {model_id 或 model(参数名)}。精确匹配，找不到返回 404（不静默测默认模型）。
    与 v1.0.270 的 /models/benchmark（速度+质量多模型对比）互补：本端点是单模型细粒度打分。"""
    data = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    mid = str(data.get("model_id") or "")
    mparam = str(data.get("model") or "")
    model = next((m for m in cfg.get("models", []) if m.get("id") == mid), None) \
        or next((m for m in cfg.get("models", []) if m.get("model") == mparam), None)
    # v1.0.364 审查修复（M6）：删除第三个重复 next()（与第一个条件完全相同的死代码）
    if not model:
        return jsonify({"ok": False, "error": "未找到该模型（可能未保存成功）"}), 404
    from services.model_bench import run_benchmark

    def gen():
        # v1.0.342：客户端「⏹ 停止」断开 SSE 后，werkzeug 在下一次 yield 处对生成器
        # 抛 GeneratorExit——在途那一项 LLM 调用跑完后即中止，不再继续后续项。
        try:
            for ev in run_benchmark(model):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"bench_end\"}\n\n"
        except GeneratorExit:
            log_event("[bench] 客户端断开，综合评测中止")
            raise

    return Response(stream_with_context(gen()),
                    mimetype="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/models/test", methods=["POST"])
def api_models_test():
    """真实验证一个模型是否可用（非流式单次调用）。

    v1.0.691（用户："软件模型测试 增加测试出字速度"）：测试提示语从 ping/8 token
    改为长输出句，并新增出字速度测量——elapsed 覆盖整个 HTTP 往返（端到端出字
    口径，含网络；注释钉住口径防止后人换成"纯解码"口径导致前后端数字对不上）。
    成功响应新增 speed:{elapsed_s, tokens, tok_per_s}——字段名是前后端契约
    （前端 js/05 testModel 按名取数，一字不差）。失败响应结构不变、不带 speed。"""
    data = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    model = resolve_model(cfg, data.get("model_id")) if data.get("model_id") else resolve_model(cfg)
    if not model:
        return jsonify({"ok": False, "error": "未配置模型"})
    from llm_client import _chat_url, build_headers
    url = _chat_url(model)
    headers = build_headers(model)   # v1.0.355：请求头构造收敛到 llm_client
    # v1.0.691：原 ping/max_tokens=8 太短，测不出稳定的 tok/s——长提示语给足字符量
    payload = {"model": model.get("model"),
               "messages": [{"role": "user",
                             "content": "请用 60 字左右介绍你自己，直接输出正文，不要标题。"}],
               "stream": False, "max_tokens": 160}
    # 推理参数注入（思考强度/开关/预算/自定义扩展/max_tokens），测试时真实生效
    try:
        from llm_client import _apply_reasoning_params
        _apply_reasoning_params(payload, model)
    except Exception:  # noqa: BLE001
        pass
    try:
        t0 = time.perf_counter()   # v1.0.691：端到端计时起点（含网络往返）
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        # v1.0.691：先定稿 elapsed_s 再算速度——存值与除数必须是同一个数，
        # 否则毫秒级往返下 round 后的 elapsed_s=0.0 而 tok_per_s 还是按原始
        # 微秒值算，前后端对不上（测试 fake 请求曾实锤此边界）。
        elapsed = round(time.perf_counter() - t0, 3)
        if r.status_code == 200:
            jr = {}
            try:
                jr = r.json()
                got = (jr.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()[:40]
            except Exception as e:  # noqa: BLE001
                log_event(f"[model-test] 解析模型测试响应失败：{e}")
                got = ""
            # v1.0.691：出字速度——token 数取 usage.completion_tokens（取不到按 0，
            # 前端见 tokens=0 只报连接成功不报速度，避免假数字误导）
            _usage = jr.get("usage") or {}
            _ct = _usage.get("completion_tokens")
            tokens = int(_ct) if isinstance(_ct, (int, float)) else 0
            tok_per_s = round(tokens / elapsed, 1) if (elapsed > 0 and tokens > 0) else 0
            return jsonify({"ok": True, "model": model.get("name"), "reply": got,
                            "speed": {"elapsed_s": round(elapsed, 3), "tokens": tokens,
                                      "tok_per_s": tok_per_s}})
        return jsonify({"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/models/scan", methods=["POST"])
def api_models_scan():
    """自动扫描本机 LM Studio / Ollama 本地模型 API，返回探测结果（含模型列表）。"""
    t0 = time.time()
    results = []
    reachable_by_provider = set()   # 同一 provider 只保留首个可达结果（127.0.0.1/localhost 去重）
    for provider, label, api_base, probes in _LOCAL_MODEL_ENDPOINTS:
        models, err = _probe_local_models(api_base, probes)
        if models is not None and provider in reachable_by_provider:
            continue
        if models is not None:
            reachable_by_provider.add(provider)
        results.append({
            "provider": provider, "label": label, "base_url": api_base,
            "reachable": models is not None, "models": models or [],
            "error": err or "",
        })
    return jsonify({"ok": True, "results": results,
                    "elapsed": round(time.time() - t0, 2)})


# jsonschema 可用性探测已迁移至 core_utils.py（v1.0.97）
# backend.py 通过 from core_utils import _valid_json_schema 使用


@bp.route("/models/probe", methods=["POST"])
def api_models_probe():
    """远程模型探测（v1.0.188）：POST {base_url, api_key} →
    调 GET {base}/models 列出该服务全部可用模型 + 按模型名启发式推断能力/思考强度档位。
    可选 probe_effort=true 时对首个模型真实试探 reasoning_effort 是否被接受。"""
    data = request.get_json(force=True, silent=True) or {}
    base_url = (data.get("base_url") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    from llm_client import probe_remote_models, probe_reasoning_effort
    t0 = time.time()
    models, err = probe_remote_models(base_url, api_key)
    if models is None:
        return jsonify({"ok": False, "error": err or "探测失败", "elapsed": round(time.time() - t0, 2)})
    # 可选：对首个模型真实试探 reasoning_effort（默认开启——只发一次 max_tokens=2 的小请求）
    effort_probe = None
    if models and data.get("probe_effort", True) is not False:
        effort_probe = probe_reasoning_effort(base_url, api_key, models[0]["id"])
    return jsonify({"ok": True, "models": models,
                    "effort_probe": effort_probe,
                    "elapsed": round(time.time() - t0, 2)})


@bp.route("/models", methods=["POST"])
def post_models():
    data = request.get_json(force=True, silent=True) or []
    if not isinstance(data, list):
        return jsonify({"ok": False, "error": "请求体必须是数组"}), 400
    # v1.0.555（P0-10）：update_config 事务——_apply_models 只做纯变换（读 cfg
    # 仅为拿旧 models 做合并基准），事务内用 fresh cfg 重算，杜绝并发覆盖。
    # v1.0.621：校验类错误此前一律变成 500「配置写入异常」——`update_config` 把所有异常
    # 都吞掉返回 False（core/config_store.py），所以 `except _ModelApplyError` 永远不触发。
    # 用户填错 base_url 看到的却是"配置写入异常（详见日志）"，等于把输入错误推给排障。
    # 现在把校验信息在事务里存一份带出来：仍然是"校验失败就不写盘"，但界面拿到人话。
    caught = []

    def _mutate(cfg):
        models, err = _apply_models(cfg, data)
        if err:
            caught.append(err)
            raise _ModelApplyError(err)
        cfg["models"] = models
        return True

    try:
        ok = update_config(_mutate)
    except _ModelApplyError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if caught:
        return jsonify({"ok": False, "error": caught[0]}), 400
    if not ok:
        return jsonify({"ok": False, "error": "保存失败：配置写入异常（详见日志）"}), 500
    cfg2 = load_config()
    return jsonify([mask_models(m) for m in cfg2.get("models", [])])


def _apply_models(cfg, data):
    """校验并规范化模型列表（post_models 与 /models/import 导入共用）。
    返回 (models, err)：err 非 None 表示校验失败（此时 models 为 None）。"""
    old = {m.get("id"): m for m in cfg.get("models", [])}
    models = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or ("m_" + uuid.uuid4().hex[:8])
        # v1.0.621（用户实测）：从别的软件/网页复制过来的地址常带 U+200B 之类零宽字符，
        # `str.strip()` 去不掉（它不算空白）→ 探测请求抛 InvalidSchema、别的却能正常用。
        # 写入侧统一清洗：base_url/api_key/model/name 都过一遍（key 带 BOM 更隐蔽——
        # 表现是 401，谁也想不到是复制带入的字符）。
        base_url = clean_url(m.get("base_url", ""))
        if base_url and not re.match(r"^https?://", base_url):
            return None, (f"模型「{m.get('name', mid)}」的 base_url 必须以 http:// 或 https:// 开头"
                          "（已剔除粘贴带入的零宽字符/BOM 后仍不是合法地址）")
        rec = {
            "id": mid,
            "name": clean_text(m.get("name", mid)),
            "base_url": base_url,
            "api_key": clean_text(m.get("api_key")) or old.get(mid, {}).get("api_key", ""),  # 空=保留旧 key
            "model": clean_text(m.get("model", "")),
            "enabled": bool(m.get("enabled", True)),
            "is_default": bool(m.get("is_default", False)),
            # 推理参数（可选，v1.0.186 新增）：
            #   reasoning_effort: _EFFORT_LEVELS 五值之一|None（v1.0.698 六档 UI；OpenAI o3/gpt-5 等）
            #   enable_thinking:  True|False|None（DeepSeek R1 / Qwen thinking 模式）
            #   thinking_budget:  int|None（Gemini thinking_config 思考预算 token）
            #   extra_params:     dict|None（任意自定义扩展参数，最灵活，合并进请求体）
            "reasoning_effort": m.get("reasoning_effort") if m.get("reasoning_effort") in _EFFORT_LEVELS else None,
            "enable_thinking": m.get("enable_thinking") if m.get("enable_thinking") in (True, False) else None,
            "thinking_budget": m.get("thinking_budget") if isinstance(m.get("thinking_budget"), int) and 0 < m.get("thinking_budget") < 200000 else None,
            "extra_params": m.get("extra_params") if isinstance(m.get("extra_params"), dict) and m.get("extra_params") else None,
            # 模型能力与协议（v1.0.187 新增）：
            #   protocol:                "openai" | "anthropic" | "custom" — 决定 llm_client 请求格式
            #   caps:                    模型能力位：thinking / can_disable_thinking
            #                            （v1.0.664：其余展示型死键无读取点，保存时丢弃；
            #                             自定义协议由 protocol 字段表达）
            #   thinking_supported_levels: ["low","medium","high","xhigh","max"] 的子集
            #                              模型支持哪些思考强度档位（多选 checkbox 来源）
            #   default_thinking_effort: _EFFORT_LEVELS 五值之一|None
            #                              默认思考强度（自动选项展示）
            #   max_input_tokens:        int|None — 上下文窗口 token 数（输入档位按钮来源）
            #   max_output_tokens:       int|None — 单次输出上限 token 数（输出档位按钮来源）
            "protocol": m.get("protocol") if m.get("protocol") in ("openai", "anthropic", "custom") else "openai",
            "caps": _norm_caps(m.get("caps")),
            "thinking_supported_levels": _norm_levels(m.get("thinking_supported_levels")),
            "default_thinking_effort": m.get("default_thinking_effort") if m.get("default_thinking_effort") in _EFFORT_LEVELS else None,
            "max_input_tokens": m.get("max_input_tokens") if isinstance(m.get("max_input_tokens"), int) and 0 < m.get("max_input_tokens") < 10000000 else None,
            "max_output_tokens": m.get("max_output_tokens") if isinstance(m.get("max_output_tokens"), int) and 0 < m.get("max_output_tokens") < 1000000 else None,
        }
        models.append(rec)
    # 默认模型唯一性（v1.0.188 修复）：多个 is_default=true 时只保留第一个启用的，
    # 其余全部清掉——旧逻辑只补默认不清理，前端编辑/新增勾「设为默认」后会出现多默认。
    enabled = [m for m in models if m["enabled"]]
    default_seen = False
    for m in models:
        if m["is_default"]:
            if not m["enabled"]:
                m["is_default"] = False  # 禁用模型不能当默认
            elif default_seen:
                m["is_default"] = False  # 重复默认 → 清掉，只留第一个
            else:
                default_seen = True
    if enabled and not any(m["is_default"] for m in models):
        enabled[0]["is_default"] = True
    return models, None


def _export_payload(cfg):
    """v1.0.355：模型导出 payload 收敛（/models/export 与 /export/save 原逐字重复）。"""
    from datetime import datetime
    return {
        "__wecom_companion_models__": True,
        "app_version": VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "judge_model_id": cfg.get("judge_model_id") or "",
        "models": cfg.get("models", []),
    }

# v1.0.773（暗病巡检）：GET /models/export 全仓零消费删除——v1.0.270 起 UI 走
# /models/export/save（后端落盘，pywebview 下 blob 下载不触发）；import 端点
# 仍接受 _export_payload 的同一 payload 格式，导入链路不受影响。
@bp.route("/models/export/save", methods=["POST"])
def export_models_save():
    """导出配置落盘（v1.0.270 修复）：pywebview 下前端 blob+a.download 点击不触发下载
    （只弹 toast 没有文件）→ 改为后端直接把 JSON 写到 产物库/产出/，返回文件路径。"""
    from datetime import datetime
    import os
    cfg = load_config()
    payload = _export_payload(cfg)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        out_dir = os.path.join(workspace_root(), "产出")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"模型配置备份_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"写入失败：{e}"}), 500
    log_event(f"[config] 模型配置已导出到 {path}（{len(payload['models'])} 个模型）")
    return jsonify({"ok": True, "path": path, "count": len(payload["models"])})


@bp.route("/models/import", methods=["POST"])
def import_models():
    """模型配置一键导入（v1.0.238）：接受 /models/export 的导出文件，整体覆盖 models + judge_model_id。"""
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or data.get("__wecom_companion_models__") is not True:
        return jsonify({"ok": False, "error": "不是有效的伴侣模型配置导出文件（缺 __wecom_companion_models__ 标记）"}), 400
    mlist = data.get("models")
    if not isinstance(mlist, list) or not mlist:
        return jsonify({"ok": False, "error": "导出文件中没有模型"}), 400
    # v1.0.555（P0-10）：update_config 事务（同上）
    imported, chosen_jid = [], ""

    def _mutate(cfg):
        nonlocal imported, chosen_jid
        models, err = _apply_models(cfg, mlist)
        if err:
            raise _ModelApplyError(err)
        cfg["models"] = models
        # 判断大模型：必须在导入后的启用模型中，否则静默置空（避免指向不存在的 id）
        jid = data.get("judge_model_id") or ""
        if jid and not any(m["id"] == jid and m["enabled"] for m in models):
            jid = ""
        cfg["judge_model_id"] = jid
        imported, chosen_jid = models, jid
        return True

    try:
        ok = update_config(_mutate)
    except _ModelApplyError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not ok:
        return jsonify({"ok": False, "error": "保存失败：配置写入异常（详见日志）"}), 500
    log_event("[config] 模型配置导入完成（update_config 事务）")
    return jsonify({"ok": True, "count": len(imported), "judge_model_id": chosen_jid})

def mask_models(m):
    m = dict(m)
    m["api_key_masked"] = mask_key(m.get("api_key", ""))
    m.pop("api_key", None)
    return m


# v1.0.664：只保留真正有读取点的能力位；四个展示型死键不再落盘
_VALID_CAPS = ("thinking", "can_disable_thinking")
# v1.0.698（用户需求：思考六档 自动/低/中/高/超高/极致）：合法档位扩到五值。
# 「自动」= 空值（None，不落档），由请求层用模型默认；五档全部透传
# reasoning_effort（llm_client 原样进请求体，中继/服务不支持的值由其自行降级）。
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_VALID_LEVELS = _EFFORT_LEVELS


def _norm_caps(raw):
    """规范化 caps 字段：仅保留合法 key、全转 bool、未知 key 丢弃；输入不是 dict 时返回空 dict。"""
    if not isinstance(raw, dict):
        return {}
    return {k: bool(v) for k, v in raw.items() if k in _VALID_CAPS}


def _norm_levels(raw):
    """规范化思考强度档位列表：去重 + 仅保留合法档位 + 保持顺序；输入不是 list 时返回空列表。"""
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for v in raw:
        if v in _VALID_LEVELS and v not in seen:
            seen.add(v)
            out.append(v)
    return out
