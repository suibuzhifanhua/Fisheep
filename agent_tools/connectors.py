"""agent_tools.connectors — 连接器定义与 HTTP 执行（CONNECTORS / is_connector_configured / run_connector）。"""

# v1.0.324 启动提速 + v1.0.364 M8 收敛：requests 懒加载代理实现收敛到 core_utils
from core.logging import _lazy_requests as requests
import json

from core.logging import log_event

# 连接器：通用 HTTP 工具（配置后可让 agent 访问外部 API）
CONNECTORS = [
    {"id": "generic-http", "name": "通用 HTTP 工具", "icon": "🌐",
     "desc": "配置外部 API 的 Base URL 与密钥后，agent 即可调用 generic_http_call 访问该服务（内部 OA、SaaS、REST API 等）。",
     # v1.0.667：prompt 从写死工具名改为 {tool} 占位符——多连接器时每个实例的工具名
     # 各不相同（generic_http_call / {id}_call），写死名字会让 agent 调错连接器。
     "prompt": "你已接入外部 HTTP 服务（{id}：{name}）。需要调用它时，用 {tool} 指定 method/path/body；凭据由系统自动附带，无需手动拼接。",
     "config_schema": [
         {"key": "base_url", "label": "API Base URL", "type": "text",
          "placeholder": "https://api.example.com", "required": True},
         {"key": "api_key", "label": "API Key / Token", "type": "password",
          "placeholder": "可选，Bearer 自动附带", "required": False},
         {"key": "headers", "label": "额外请求头 (JSON)", "type": "textarea",
          "placeholder": '{"X-Tenant":"abc"}', "required": False},
     ],
     "tool": {"type": "function", "function": {
         "name": "generic_http_call",
         "description": "调用已配置的外部 HTTP 服务（generic-http 连接器）。系统会自动附带配置的 API Key 与额外请求头。",
         "parameters": {"type": "object", "properties": {
             "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP 方法，默认 GET"},
             "path": {"type": "string", "description": "接口路径，如 /v1/users 或 /orders/123"},
             "params": {"type": "object", "description": "GET 查询参数 (key->value)"},
             "body": {"type": "object", "description": "POST/PUT/PATCH 的请求体(JSON)"},
         }, "required": ["path"]}
     }}},
]

def connector_defs_with_user(conns):
    """v1.0.667：内置连接器 + 用户自定义（user-*）的动态构造 def 列表。

    build_agent_tools 与 dispatch_tool 都要用同一口径（否则注入清单里有的工具
    直调会漏掉、掉进 run_tool 报"未知工具"）。抽出来两处共用，保证永不漂移。
    """
    defs = list(CONNECTORS)
    for cid, cc in (conns or {}).items():
        if not isinstance(cid, str) or not cid.startswith("user-"):
            continue
        if cid not in {c["id"] for c in defs}:
            defs.append(user_connector_def(cid, cc or {}))
    return defs


def render_connector_prompt(c, cc):
    """v1.0.667：连接器 prompt 占位符渲染（{id}/{name}/{tool}）。
    旧定义无占位符时原样返回，行为与从前完全一致。"""
    p = c.get("prompt") or ""
    if not p:
        return p
    cc = cc or {}
    try:
        return p.format(id=c.get("id", ""), name=c.get("name") or cc.get("name") or c.get("id", ""),
                       tool=(c.get("tool") or {}).get("function", {}).get("name", ""))
    except (KeyError, IndexError, ValueError):
        return p


def user_connector_def(cid, cc):
    """v1.0.667：把用户自定义连接器（user-*）构造成与内置同构的 def。

    用户连接器全部走通用 HTTP 执行（run_connector 对 generic-http 分支
    只依赖 cc 的 base_url/api_key/headers，id 无关），工具名按实例命名
    避免多实例撞名；cc 里的 name 作为展示名（可空，空则回退 id）。
    """
    name = str((cc or {}).get("name") or "").strip() or cid
    # 名称会拼进工具描述（JSON 串）——含引号/反斜杠时转义，防破坏参数 schema
    name = name.replace('"', "'").replace("\\", "/")[:40]
    tool_name = cid + "_call"
    return {
        "id": cid, "name": name, "icon": "🌐", "user": True,
        "desc": "用户添加的外部 API 连接器（通用 HTTP）。",
        "prompt": "你已接入外部 HTTP 服务（{id}：{name}）。需要调用它时，用 {tool} 指定 method/path/body；凭据由系统自动附带，无需手动拼接。",
        "config_schema": [
            {"key": "base_url", "label": "API Base URL", "type": "text",
             "placeholder": "https://api.example.com", "required": True},
            {"key": "api_key", "label": "API Key / Token", "type": "password",
             "placeholder": "可选，Bearer 自动附带", "required": False},
            {"key": "headers", "label": "额外请求头 (JSON)", "type": "textarea",
             "placeholder": '{"X-Tenant":"abc"}', "required": False},
        ],
        "tool": {"type": "function", "function": {
            "name": tool_name,
            "description": f"调用用户配置的外部 HTTP 服务「{name}」（{cid}）。系统自动附带配置的 API Key 与额外请求头。",
            "parameters": {"type": "object", "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP 方法，默认 GET"},
                "path": {"type": "string", "description": "接口路径，如 /v1/users 或 /orders/123"},
                "params": {"type": "object", "description": "GET 查询参数 (key->value)"},
                "body": {"type": "object", "description": "POST/PUT/PATCH 的请求体(JSON)"},
            }, "required": ["path"]}
        }},
    }


def is_connector_configured(connector, cc):
    cc = cc or {}
    for f in connector.get("config_schema", []):
        if f.get("required") and not str(cc.get(f["key"], "")).strip():
            return False
    return True

def run_connector(connector, cc, args):
    # v1.0.667：用户自定义连接器（user-*）与内置 generic-http 走同一执行器
    # （只依赖 cc 的 base_url/api_key/headers，id 无关）。
    if connector["id"] == "generic-http" or connector.get("user"):
        method = (args.get("method") or "GET").upper()
        path = args.get("path", "") or ""
        base = (cc.get("base_url") or "").rstrip("/")
        url = base + (path if path.startswith("/") else "/" + path)
        headers = {}
        if cc.get("api_key"):
            headers["Authorization"] = "Bearer " + str(cc["api_key"])
        try:
            extra = json.loads(cc.get("headers") or "{}")
            if isinstance(extra, dict):
                headers.update({str(k): str(v) for k, v in extra.items()})
        except Exception as e:  # noqa: BLE001
            log_event(f"[connector] 连接器自定义 headers 解析失败，已忽略：{e}")
        try:
            body = args.get("body") if method in ("POST", "PUT", "PATCH") else None
            params = args.get("params") if method == "GET" else None
            r = requests.request(method, url, headers=headers, json=body, params=params, timeout=30)
            try:
                text = r.text or ""
            except Exception as e:  # noqa: BLE001
                log_event(f"[http] 读取响应正文失败：{e}")
                text = ""
            return {"status": r.status_code, "body": text[:6000]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP 调用失败：{e}"}
    return {"ok": False, "error": f"未知连接器：{connector.get('id')}"}
