import configparser
import requests

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")
base_url = config["llm"]["base_url"]
model = config["llm"]["model"]

user_input = input("你: ")

resp = requests.post(
    f"{base_url}/api/chat",
    json={
        "model": model,
        "messages": [{"role": "user", "content": user_input}],
        "stream": True,
    },
    stream=True,
)

print("AI: ", end="", flush=True)
for line in resp.iter_lines():
    if not line:
        continue
    import json
    chunk = json.loads(line)
    token = chunk["message"]["content"]
    print(token, end="", flush=True)
print()
