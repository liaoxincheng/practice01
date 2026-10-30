# AnythingLLM MCP Server

把 AnythingLLM 工作区的检索问答包装成两个 MCP 工具,让支持 MCP 的 AI 客户端
(opencode、Claude Desktop 等)直接查询你上传到 AnythingLLM 的知识库。

## 工具

| 工具 | 作用 | 参数 |
| --- | --- | --- |
| `list_workspaces` | 列出所有工作区及其 `slug` | 无 |
| `query_workspace` | 在指定工作区里检索并回答 | `workspace_slug`、`message`、`mode`(query / chat) |

## 环境要求

- Python 3.10+
- AnythingLLM 正在运行(默认 `http://localhost:3001`)
- AnythingLLM 中已有工作区,并在「设置 → 工具 → API 密钥」生成 Key

## 安装

```bash
cd AnythingLLMMCP
python -m venv .venv
.venv\Scripts\pip install -e .
```

## 配置

通过环境变量配置,不要把 Key 写进代码或提交到仓库:

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ANYTHINGLLM_API_URL` | `http://localhost:3001` | AnythingLLM 服务地址 |
| `ANYTHINGLLM_API_KEY` | 空 | API Key,必填 |
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` 或 `stdio` |
| `MCP_PORT` | `8082` | streamable-http 模式监听端口,端点 `/mcp` |

PowerShell 示例:

```powershell
$env:ANYTHINGLLM_API_KEY = "你的-API-KEY"
```

## 运行

```bash
# 方式一:streamable-http(默认,便于用 HTTP 调试)
python server.py
# 端点:http://localhost:8082/mcp

# 方式二:stdio(供 opencode / Claude Desktop 按本地进程启动)
$env:MCP_TRANSPORT = "stdio"
python server.py
```

## 接入 opencode

仓库里的 `opencode.json` 已配置好 stdio 启动方式。把 `ANYTHINGLLM_API_KEY`
加入当前环境(或在该文件的 `environment` 里补上),然后在 AnythingLLMMCP 目录启动 opencode:

```json
{
  "mcp": {
    "anythingllm": {
      "type": "local",
      "command": ["python", "server.py"],
      "environment": { "MCP_TRANSPORT": "stdio" }
    }
  }
}
```

之后对 AI 说「列出我的 AnythingLLM 工作区」「在 course 工作区里问:xAgent 怎么分层?」,
它就会调用上面的工具。

## 测试

```bash
.venv\Scripts\python -m unittest test_api -v
```

测试用 `httpx.MockTransport` 模拟 AnythingLLM,不联网、不需要真实 Key
(服务器工具本身需要安装 `mcp` 包后,配合真实 AnythingLLM 验证)。

## 代码结构

```
anythingllm_api.py   AnythingLLM HTTP 客户端(httpx AsyncClient,统一错误处理)
server.py            MCP 服务器:两个 @mcp.tool,stdio / streamable-http 双传输
test_api.py          客户端单元测试(MockTransport)
```
