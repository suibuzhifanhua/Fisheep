# -*- coding: utf-8 -*-
"""
test_dual_model — 双模型分工集成验证（v1.0.235 起，v1.0.663 重建为标准 pytest 模块）

不依赖运行中的 exe，直接调用源码里的真实委派逻辑：
1. judge_model(cfg) 正确解析「判断大模型」（大模型）。
2. resolve_model 默认仍为小模型。
3. build_agent_tools 在配置了 judge 模型时自动注入 ask_judge 工具（单模型不注入）。
4. _run_ask_judge 把判断问题委派给大模型并拿到 advice（小模型→大模型委派链路）。
5. 同问二次调用命中缓存（不重复烧大模型）。

v1.0.663 重建背景：原文件是「导入即执行的脚本」——模块级 assert/print + 实调本地
Ollama（127.0.0.1:11434）。主机不可达时 import 即炸，整份文件在 pytest 收集期
报错，全量回归被迫 --ignore 掉它。重建为 test 函数后：
  · 纯逻辑断言（1/2/3）无条件跑，不碰网络；
  · 实调断言（4/5）走**生产同款路径**先探测 Ollama（probe_model_reachable）——
    不可达/非 200 即 skip（应用自己此刻也连不上，跑也是空测）。
  · v1.0.663（F）：探测从模块 import 期挪进 module 作用域 fixture（ollama_or_skip），
    --collect-only / 导入索引**零网络副作用**；skip 语义与 @requires_ollama 不变。
全量 pytest 从此不需要 --ignore tests/test_dual_model.py。
"""
import pytest

from llm_client import judge_model, resolve_model, probe_model_reachable
from agent_tools.executors import _run_ask_judge, _ASK_JUDGE_CACHE
from agent_tools.dispatch import build_agent_tools

OLLAMA = "http://127.0.0.1:11434/v1"

# 小模型（默认工具循环） + 大模型（判断）
SMALL = {"id": "m_small", "name": "qwen3.5:9b", "model": "qwen3.5:9b",
         "base_url": OLLAMA, "enabled": True, "is_default": True}
JUDGE = {"id": "m_judge", "name": "qwen3.8:latest", "model": "qwen3.8:latest",
         "base_url": OLLAMA, "enabled": True, "is_default": False}

cfg = {
    "models": [SMALL, JUDGE],
    "judge_model_id": "m_judge",          # 配置了判断模型 → 双模型
    "enabled_skills": None,               # None = 全技能启用（含 exam-solver: judge=model）
    "skill_overrides": {},
}

_Q = "判断题：公司月营收100万、成本80万，利润率是多少？选项：A.20% B.25% C.80%"
_CTX = "利润=营收-成本=20万；利润率=利润/营收。"

@pytest.fixture(scope="module")
def ollama_or_skip():
    """生产同款连通探测，**由 import 期延迟到 fixture 首次使用时**（v1.0.663 F）。

    旧形态在模块顶层直接 probe：pytest --collect-only（以及任何 import 本文件的
    工具/IDE 索引）都会对 127.0.0.1:11434 发起真实 TCP 请求——收集期被网络
    副作用污染，主机不可达时还会让 CI/离线环境多等超时。改成 module 作用域 fixture：
    · 收集期不执行 fixture → 零网络；
    · 只有实调用例跑到时才探一次（同模块复用结果）；
    · 探测走应用实际会走的请求路径（probe_model_reachable），不可达/经代理被劫持
      （detail 非 HTTP 200）即 pytest.skip——「应用此刻也连不上 Ollama」的 skip
      语义与旧 @requires_ollama 一致，不会把真故障误跳成绿。
    """
    o = probe_model_reachable({"base_url": OLLAMA, "api_key": ""})
    if o["state"] != "ok" or o.get("detail") != "HTTP 200":
        pytest.skip("本地 Ollama（127.0.0.1:11434）当前不可用：%s %s——"
                    "双模型实调部分跳过，纯逻辑断言不受影响"
                    % (o["state"], o.get("detail")))
    return o


# 保留装饰器名与调用形态（v1.0.663）：@requires_ollama 现在是「拉入 fixture」，
# 网络探测发生在用例执行期而非 import/收集期。
requires_ollama = pytest.mark.usefixtures("ollama_or_skip")


def test_judge_model_resolves_judge():
    """judge_model(cfg) 解析出配置的判断大模型。"""
    jm = judge_model(cfg)
    assert jm is not None, "judge_model 返回 None（应解析出大模型）"
    assert jm["id"] == "m_judge", f"judge 解析错：{jm['id']}"


def test_resolve_model_default_is_small():
    """默认模型仍是小模型（judge 只是判断点介入，不抢工具循环）。"""
    sm = resolve_model(cfg)
    assert sm["id"] == "m_small", f"默认模型错：{sm['id']}"


def test_build_agent_tools_injects_ask_judge():
    """配置了 judge 模型 → ask_judge 工具自动注入。"""
    tools, prompt_extra = build_agent_tools(cfg)
    names = [t["function"]["name"] for t in tools]
    assert "ask_judge" in names, f"ask_judge 未注入，工具集：{names}"
    assert isinstance(prompt_extra, str) and prompt_extra, "协作 prompt 不该为空"


def test_single_model_mode_omits_ask_judge():
    """judge_model_id 留空 → 退回单模型，ask_judge 不注入。"""
    cfg_single = {"models": [SMALL, JUDGE], "judge_model_id": "", "enabled_skills": None}
    assert judge_model(cfg_single) is None, "空 judge 应退回单模型(None)"
    tools2, _ = build_agent_tools(cfg_single)
    names2 = [t["function"]["name"] for t in tools2]
    assert "ask_judge" not in names2, "单模型下不应出现 ask_judge"


@requires_ollama
def test_run_ask_judge_delegates_to_judge_model():
    """实调大模型拿建议——小模型→大模型委派链路通（对 Ollama 实跑）。"""
    res = _run_ask_judge({"question": _Q, "context": _CTX}, cfg)
    assert "advice" in res, f"ask_judge 未返回 advice：{res}"
    assert res.get("judge") == "qwen3.8:latest", f"judge 模型名错：{res.get('judge')}"


@requires_ollama
def test_run_ask_judge_second_call_hits_cache():
    """同问二次调用命中缓存（不重复烧大模型），且 advice 与首次一致。"""
    n_before = len(_ASK_JUDGE_CACHE)
    first = _run_ask_judge({"question": _Q, "context": _CTX}, cfg)
    assert "advice" in first, f"首次调用未返回 advice：{first}"
    res2 = _run_ask_judge({"question": _Q, "context": _CTX}, cfg)
    assert res2.get("cached") is True, f"二次调用未命中缓存：{res2}"
    assert res2["advice"] == first["advice"], "缓存返回与首次不一致"
    assert len(_ASK_JUDGE_CACHE) == n_before, "缓存条数异常增长"
