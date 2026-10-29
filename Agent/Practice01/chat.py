# practice01:与本地大模型对话的终端程序(OpenAI SDK)
# 用法:
#   python chat.py          非流式输出,循环交互,带聊天历史
#   python chat.py --stream 流式输出
# 输入 exit 或 退出 结束对话
import configparser
import sys

# Windows 下统一按 UTF-8 读写终端,避免中文乱码
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")
client = OpenAI(
    base_url=config["llm"]["base_url"],
    api_key=config["llm"]["api_key"],
)
MODEL = config["llm"]["model"]
STREAM = "--stream" in sys.argv

# 聊天历史:每次对话都带上,让模型记住上下文
messages = []

print("本地大模型对话程序" + (" [流式模式]" if STREAM else " [非流式模式]"))
print("输入 exit 或 退出 结束对话")

while True:
    user_input = input("你: ").strip()
    if user_input.lower() in ("exit", "quit", "退出"):
        break
    if not user_input:
        continue

    messages.append({"role": "user", "content": user_input})

    if STREAM:
        print("AI: ", end="", flush=True)
        stream = client.chat.completions.create(
            model=MODEL, messages=messages, stream=True
        )
        reply = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            reply += delta
            print(delta, end="", flush=True)
        print()
    else:
        resp = client.chat.completions.create(model=MODEL, messages=messages)
        reply = resp.choices[0].message.content
        print("AI:", reply)

    messages.append({"role": "assistant", "content": reply})
