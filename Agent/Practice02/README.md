# Practice02 — 带工具调用的流式对话程序

Practice02 在 Practice01 的基础上加入 **function calling**:模型除了回答文字,还可以主动调用工具,工具执行结果送回模型后继续回答(工具调用循环)。程序按课程推进保留了三个阶段的快照,最终完整版提供 **12 个工具**。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `chat-stream-tool-getDate.py` | 阶段一:基础流式对话 + `getDate` 一个工具。取时间调用本机命令(PowerShell 的 Get-Date,Windows 上最接近 Unix `date` 的本地命令) |
| `chat-stream-tool-isLeapYear.py` | 阶段二:在上一阶段基础上加 `isLeapYear`(纯 Python 取模计算) |
| `chat-stream-tool-fileOps.py` | 阶段三(完整版):加 10 个文件操作工具,共 12 个 |
| `chat.py` | 与完整版内容一致,便于直接 `python chat.py` |
| `chat-stream-tool-fileOps.mmd` | 完整版的 Mermaid 架构图源文件 |
| `提示词*.md` | 课程各阶段的原始提示词 |
| `实测流程.md` | 验证模型确实调用了工具的实测流程 |
| `config.example.ini` | 配置模板 |

## 运行

```bash
# 1. 配置:复制配置模板,填自己的接口地址、模型名和密钥
copy config.example.ini config.ini

# 2. 启动(三个阶段的文件任选其一)
python chat-stream-tool-fileOps.py
```

输入 `exit` 或 `退出` 结束对话。

## 工具清单(完整版共 12 个)

时间:

- `getDate` —— 获取当前日期时间。课程要求用本地命令而不是 Python 取时间:Windows 上没有 Unix 的 `date`,程序用 `subprocess` 列表形式调用 PowerShell `Get-Date`(固定命令,不拼接参数)。
- `isLeapYear` —— 判断闰年(纯 Python 计算,不传年份时用当前年份)。

文件操作(10 个):

- `getCurrentDirectory` —— 获取当前工作目录的绝对路径。
- `listDirectory` —— 列出目录内容,标出文件/目录和大小,可选递归。
- `getFileType` —— 按扩展名给出类型名、MIME,并判断是否纯文本。
- `readFile` —— 读纯文本文件内容,默认最多 200 行,UTF-8 / GBK 自适应。
- `createFile` —— 根据指定目录和文件名创建新文件,已存在时不覆盖。
- `editFile` —— 编辑已有文件:`overwrite` 整体替换、`append` 追加末尾。
- `createDirectory` —— 建目录,父目录自动创建。
- `deleteDirectory` —— 删目录,非空也连内容一起删。
- `deleteFile` —— 删单个文件。
- `renamePath` —— 重命名或移动文件、目录。

所有工具的错误(文件不存在、编码不支持、参数不对等)都会转成"错误:……"文本返回给模型,不会打断对话。

## 架构图

`chat-stream-tool-fileOps.mmd` 是程序的 Mermaid 架构图。渲染方法:打开 <https://mermaid.live>,把 `.mmd` 文件内容粘贴进去即可;需要图片时从网页导出。
