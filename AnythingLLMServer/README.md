# AnythingLLMServer — 网页上传文档到 AnythingLLM

一个单页网页工具:在浏览器里选择 / 拖入本地文档,调用 AnythingLLM 的 HTTP API 完成
**上传 → 加入工作区 → 向量化** 两步操作,之后就能在 AnythingLLM 里针对该工作区检索问答。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `upload.html` | 单文件网页:工作区选择、拖拽上传、两步 API 调用、过程日志 |
| `config.example.js` | 配置模板(服务地址 + API Key);复制为 `config.js` 后填写 |
| `config.js` | 你自己的配置(**已被 .gitignore 排除,不会提交**) |

## 使用步骤

1. 启动 AnythingLLM 桌面版 / Docker 版,确认默认服务地址 `http://localhost:3001` 可访问。
2. 在 AnythingLLM 的「设置 → 工具 → API 密钥」里生成 API Key。
3. 复制配置模板并填写 Key:

   ```bash
   copy config.example.js config.js
   ```

   ```js
   window.ANYTHINGLLM_CONFIG = {
       apiUrl: 'http://localhost:3001',
       apiKey: '你的-API-KEY',
   };
   ```

4. 启动一个本地静态服务器,再用浏览器打开页面(推荐,避免直接双击打开时的跨域 / 缓存差异):

   ```bash
   python -m http.server 8000
   # 浏览器访问 http://localhost:8000/upload.html
   ```

5. 页面顶部选择(或刷新载入)目标工作区,把文件拖进虚线框或点击选择,点「上传并向量化」。
   下方深色日志会依次显示:上传成功 → 文档位置 → 向量化完成。

## 调用的 AnythingLLM API

| 步骤 | 方法与路径 | 作用 |
| --- | --- | --- |
| 工作区列表 | `GET /api/v1/workspaces` | 取 `workspaces[].name / slug` 供下拉框选择 |
| 上传文档 | `POST /api/v1/document/upload`(multipart 字段 `file`) | 返回 `success` 与 `documents[].location` |
| 向量化 | `POST /api/v1/workspace/{slug}/update-embeddings` | 请求体 `{ "adds": [location] }`,把文档加入工作区 |

所有请求都带 `Authorization: Bearer <API Key>`。文档 location 必须用上传接口返回值,不能自己拼。

## 安全提示

API Key 写在浏览器端(本地配置文件),**不要把这个页面部署到公网,也不要把 `config.js` 提交到仓库**。
