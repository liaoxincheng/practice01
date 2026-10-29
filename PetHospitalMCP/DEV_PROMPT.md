# 宠物医院 MCP Server 开发提示词（Python MVP 版）

## 技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| **语言** | Python 3.10+ | |
| **MCP SDK** | `mcp>=2.0.0` (v2.1.1最新) | `pip install "mcp[cli]"` |
| **协议** | MCP `2026-07-28` | 无状态协议核心 |
| **传输层** | Streamable HTTP + Stdio | HTTP 用于远程，Stdio 用于本地调试 |
| **后端数据源** | 宠物医院 REST API `http://127.0.0.1:8080` | 通过 httpx 调用 |

**Python SDK v2 核心特点**：
- 装饰器 API：`@mcp.tool()`、`@mcp.resource()`、`@mcp.prompt()`
- 类型提示自动生成 JSON Schema，**无需手写 schema**
- `FastMCP` 快速启动类
- `mcp run server.py --transport streamable-http` 一键启动 HTTP 服务
- 支持 `server/discover`、`_meta`、`Mcp-Method`/`Mcp-Name` 头

---

## MCP 2026-07-28 关键变更（必须遵守）

1. **无状态**：移除 `initialize/initialized` 握手和 `Mcp-Session-Id` 头
2. **每请求自包含**：`_meta` 携带 `protocolVersion`、`clientInfo`、`clientCapabilities`
3. **HTTP 头要求**：`MCP-Protocol-Version: 2026-07-28`、`Mcp-Method`、`Mcp-Name`
4. **`server/discover`** 替代 `initialize`：客户端可主动发现服务器能力
5. **`resultType` 必填**：每个工具返回必须有 `"complete"` 或 `"input_required"`
6. **缓存提示**：`ttlMs` / `cacheScope` 用于 list 结果的缓存控制
7. **废弃功能**：roots、sampling、logging 标记为 deprecated（12个月过渡期，**不要使用**）
8. **错误码**：`-32602` (Invalid Params) 替代 `-32002`
9. **多轮请求 (MRTR)**：`resultType: "input_required"` + `inputRequests` + `requestState`
10. **`extensions` 字段**：`ClientCapabilities` 和 `ServerCapabilities` 支持扩展

---

## MVP 范围：仅实现 `list_pets` 一个 Tool

**目标**：暴露一个 `list_pets` 工具，使其他 MCP Client 能查询宠物档案列表，支持多种筛选参数。

**不实现**：其他 tool、resource、prompt 等后续功能。

---

## 项目结构

```
pethospital-mcp-server/
├── pyproject.toml              # 依赖声明
├── server.py                   # 主入口：构建 Server，启动传输
├── pet_api_client.py           # 宠物医院 REST API 客户端封装
├── tools.py                    # Tool 定义（仅 list_pets）
└── README.md
```

---

## 依赖配置 `pyproject.toml`

```toml
[project]
name = "pethospital-mcp-server"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "mcp[cli]>=2.0.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]
```

安装：
```bash
pip install "mcp[cli]" httpx
```

---

## `pet_api_client.py` — REST API 客户端

封装对现有宠物医院 REST API 的 HTTP 调用，使用 `httpx`。

```python
# 必须实现的方法：
# - list_pets(params) → 调用 GET /api/v1/pets 返回 JSON
#
# 请求参数对应 REST API 的查询字符串：
#   q, name, ownerName, ownerPhone, species, doctor, disease, status,
#   min, max, sortBy, order, page, pageSize
#
# 返回：REST API 的 JSON 响应（包含 items, total, page, pageSize, totalPages, totalCost）
# 失败时抛出 httpx.HTTPError 或自定义异常
```

---

## `tools.py` — Tool 定义（仅 `list_pets`）

使用 **`@mcp.tool()` 装饰器**，函数签名中类型提示自动生成 JSON Schema。

```python
# list_pets 工具定义：
#
# @mcp.tool()
# async def list_pets(
#     species: str | None = None,       # 过滤：种类
#     doctor: str | None = None,        # 过滤：医生
#     status: str | None = None,        # 过滤：就诊状态
#     search: str | None = None,        # 全文检索
#     owner_name: str | None = None,    # 过滤：主人姓名
#     owner_phone: str | None = None,   # 过滤：电话
#     disease: str | None = None,       # 过滤：疾病
#     min_cost: float | None = None,    # 最低总花费
#     max_cost: float | None = None,    # 最高总花费
#     sort_by: str = "createdAt",       # 排序字段
#     order: str = "desc",              # 排序方向
#     page: int = 1,                    # 页码
#     page_size: int = 20,              # 每页条数
# ) -> dict:
#     """
#     查询宠物档案列表。支持按种类、医生、状态、疾病等筛选，
#     支持全文检索、排序和分页。
#     """
#     ...
#
# 注意：
# - 所有筛选参数都是可选的（有默认值 None 或默认值）
# - 返回值类型自动推导为 JSON Schema
# - 函数 docstring 作为工具描述
# - 内部调用 pet_api_client.list_pets()
# - 失败时返回 {"error": "..."} 并确保 resultType 正确
```

---

## `server.py` — 主入口

```python
# 核心代码：
#
# 1. import FastMCP 或 MCPServer
# 2. 创建 mcp 实例
# 3. 注册 tools.py 中的工具
# 4. 启动服务
#
# 两种模式：
# - Streamable HTTP：mcp.run --transport streamable-http，端口从环境变量 MCP_PORT 读取（默认 8081）
# - Stdio：mcp.run --transport stdio
#
# 关键配置：
# - 协议版本支持 2026-07-28
# - 无状态模式 Stateless: true
# - Cache 控制（list 结果可缓存 60 秒）
#
# 启动命令：
#   uv run mcp run server.py --transport streamable-http --port 8081
#   或
#   python server.py  # 自动检测
```

**两种启动方式**：

```python
# 方式 A：使用 FastMCP（推荐，代码最少）
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("pet-hospital", version="1.0.0")

# 方式 B：使用低层 MCPServer
from mcp.server import MCPServer
server = MCPServer("pet-hospital")
```

---

## `list_pets` 参数详情

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `species` | `str \| None` | `None` | 按种类筛选（犬/猫/兔/鸟/仓鼠/爬宠/其他） |
| `doctor` | `str \| None` | `None` | 按医生姓名筛选 |
| `status` | `str \| None` | `None` | 按就诊状态筛选（待就诊/就诊中/住院中/已康复/慢性病随访） |
| `disease` | `str \| None` | `None` | 按疾病筛选 |
| `search` | `str \| None` | `None` | 全文检索（跨字段，空格分词 AND） |
| `owner_name` | `str \| None` | `None` | 按主人姓名筛选 |
| `owner_phone` | `str \| None` | `None` | 按主人电话筛选 |
| `min_cost` | `float \| None` | `None` | 最低总花费 |
| `max_cost` | `float \| None` | `None` | 最高总花费 |
| `sort_by` | `str` | `"createdAt"` | 排序字段（id/name/ownerName/species/doctor/totalCost/visitCount/createdAt/updatedAt） |
| `order` | `str` | `"desc"` | 排序方向（asc/desc） |
| `page` | `int` | `1` | 页码 |
| `page_size` | `int` | `20` | 每页条数（最大500） |

**返回结构**（字典，自动转为 JSON）：

```python
{
    "items": [...],        # 宠物列表
    "total": 308,          # 总数
    "page": 1,
    "pageSize": 20,
    "totalPages": 16,
    "totalCost": 123456.78 # 结果集花费合计
}
```

---

## MCP 协议请求格式（供调用方参考）

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: list_pets
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/call",
"params":{"name":"list_pets",
"arguments":{"species":"犬","page":1,"pageSize":10}},
"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
"io.modelcontextprotocol/clientInfo":{"name":"my-agent","version":"1.0"}}}
```

---

## 缓存策略

```python
# list_pets 工具启用缓存（2026-07-28 新增）：
# ttlMs: 60000 (60秒)
# cacheScope: "origin"
# 在工具定义中通过 CacheHint 声明
```

---

## 构建与运行

```bash
# 安装依赖
pip install "mcp[cli]" httpx

# 启动 HTTP 模式（默认端口 8081）
python server.py
# 或
uv run mcp run server.py --transport streamable-http

# 启动 Stdio 模式
uv run mcp run server.py --transport stdio

# 自定义端口和 API 地址
MCP_PORT=9090 PET_API_URL=http://192.168.1.100:8080 python server.py
```

---

## 测试

```bash
# 验证 MCP 服务器
curl -X POST http://127.0.0.1:8081/mcp \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: list_pets" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","page":1,"pageSize":5}}}'

# 使用 MCP Inspector 调试
uv run mcp dev server.py
```

---

## 关键注意事项

1. **不要使用** deprecated 的 roots、sampling、logging 功能（2026-07-28 已标记废弃）
2. **不要使用** `Mcp-Session-Id` 头（2026-07-28 已移除）
3. 所有请求必须携带 `_meta` 中的 `protocolVersion`
4. 类型提示自动生成 JSON Schema，**不需要手写 schema 定义**
5. 异步函数（`async def`）推荐用于网络 I/O
6. 失败时返回 `IsError: true` 的结果
7. 数据读取**只读**通过 REST API，不直接操作 `pet.db`
8. 写入类操作（本次不实现）后续通过 REST API 代理

---

## 文件清单总结

| 文件 | 行数估算 | 内容 |
|------|----------|------|
| `pyproject.toml` | ~15行 | 依赖声明 |
| `pet_api_client.py` | ~60行 | REST API 客户端（httpx） |
| `tools.py` | ~80行 | `list_pets` 一个工具 |
| `server.py` | ~50行 | 入口：FastMCP + 启动 |
| **总计** | **~205行** | MVP 最小可用 |

---

## 开发步骤

| 步骤 | 任务 | 说明 |
|------|------|------|
| 1 | 初始化项目 | `pip install "mcp[cli]" httpx`，创建 pyproject.toml |
| 2 | 编写 config | 读取环境变量：`PET_API_URL`、`MCP_PORT`、`MCP_TRANSPORT` |
| 3 | 编写 REST 客户端 | 封装对 `127.0.0.1:8080` API 的调用 |
| 4 | 编写 `list_pets` Tool | 用 `@mcp.tool()` 装饰器定义 |
| 5 | 编写 `server.py` | 组合 FastMCP + 启动传输 |
| 6 | 测试 | `curl` 验证 HTTP 端点 + `uv run mcp dev server.py` 调试 |
| 7 | 构建发行 | 打包为可执行或 Docker 镜像 |

---

## 参考文档

- MCP Python SDK 文档：https://py.sdk.modelcontextprotocol.io/
- MCP 2026-07-28 规范：http://modelcontextprotocol.io/specification/2026-07-28
- MCP Go SDK（参考）：https://pkg.go.dev/github.com/modelcontextprotocol/go-sdk/mcp
- 宠物医院 REST API 说明：见 `pet-hospital-mcp-teaching-main` 项目 README.md
- 现有 29 个 REST 接口：`GET /api/v1/endpoints` 获取完整清单
