import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import http.client


OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:7b")


def strip_reasoning(text: str) -> str:
    t = re.sub(r"<think>[\s\S]*?</think>", "", text)
    t = re.sub(r"^\s*思考[：:][\s\S]*?(\n|$)", "", t)
    return t.strip()


def ollama_chat(messages, stream=True):
    u = urlparse(OLLAMA_BASE_URL)
    host = u.hostname or "localhost"
    port = u.port or (11434 if u.scheme == "http" else 443)
    use_https = u.scheme == "https"
    conn = http.client.HTTPSConnection(host, port, timeout=120) if use_https else http.client.HTTPConnection(host, port, timeout=120)
    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "stream": stream,
    }
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", "/api/chat", body, {
        "Content-Type": "application/json",
    })
    resp = conn.getresponse()
    data = resp.read()
    if stream:
        text = []
        for line in data.splitlines():
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            msg = obj.get("message") or {}
            c = msg.get("content") or obj.get("content") or ""
            if c:
                text.append(c)
            if obj.get("done"):
                break
        return "".join(text)
    else:
        obj = json.loads(data.decode("utf-8"))
        msg = obj.get("message") or {}
        return msg.get("content") or obj.get("content") or ""


def ollama_stream(messages):
    u = urlparse(OLLAMA_BASE_URL)
    host = u.hostname or "localhost"
    port = u.port or (11434 if u.scheme == "http" else 443)
    use_https = u.scheme == "https"
    conn = http.client.HTTPSConnection(host, port, timeout=120) if use_https else http.client.HTTPConnection(host, port, timeout=120)
    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "stream": True,
    }
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", "/api/chat", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    while True:
        line = resp.readline()
        if not line:
            break
        try:
            obj = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        msg = obj.get("message") or {}
        c = msg.get("content") or obj.get("content") or ""
        if c:
            yield c
        if obj.get("done"):
            break


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p == "/health":
            self._json(200, {"status": "ok"})
            return
        if p == "/":
            try:
                with open("static/index.html", "rb") as f:
                    b = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception:
                self._json(404, {"error": "not found"})
            return
        if p.startswith("/static"):
            p = self.path.lstrip("/")
            try:
                with open(p, "rb") as f:
                    b = f.read()
                ct = "application/octet-stream"
                if p.endswith(".css"):
                    ct = "text/css; charset=utf-8"
                elif p.endswith(".js"):
                    ct = "application/javascript; charset=utf-8"
                elif p.endswith(".html"):
                    ct = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception:
                self._json(404, {"error": "not found"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p not in ("/plan", "/plan_stream", "/plan-stream", "/api/plan_stream"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"error": str(e)})
            return
        name = req.get("name")
        background = req.get("background") or ""
        skills = req.get("skills") or []
        target_role = req.get("target_role") or ""
        timeframe_months = req.get("timeframe_months")
        preferences = req.get("preferences") or []
        level = req.get("level")
        hide_reasoning = bool(req.get("hide_reasoning", True))

        system = (
            "你是一名职业生涯与AI学习规划顾问。"
            "请基于用户背景、技能、目标岗位与时间范围，输出结构化、可执行的学习与实践计划。"
            "内容应包含阶段划分、里程碑、学习资源、每日/每周安排、项目实践与评估指标。"
            "语言使用中文，避免输出任何内部推理或思考过程。"
        )
        details = {
            "姓名": name or "",
            "背景": background,
            "技能": ", ".join(skills),
            "目标岗位": target_role,
            "时间范围(月)": timeframe_months or "未知",
            "偏好": ", ".join(preferences),
            "水平": level or "未知",
        }
        user = "\n".join([f"{k}: {v}" for k, v in details.items()])
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if p in ("/plan", "/api/plan_stream"):
            try:
                raw_out = ollama_chat(messages, stream=True)
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            text = strip_reasoning(raw_out) if hide_reasoning else raw_out
            self._json(200, {
                "plan": text,
                "model": DEFAULT_MODEL,
                "reasoning_hidden": hide_reasoning,
                "raw_output": raw_out,
            })
            return

        in_think = False
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        try:
            for chunk in ollama_stream(messages):
                out = chunk
                if hide_reasoning:
                    if in_think:
                        end_idx = out.find("</think>")
                        if end_idx != -1:
                            out = out[end_idx + len("</think>"):]
                            in_think = False
                        else:
                            out = ""
                    start_idx = out.find("<think>")
                    if start_idx != -1:
                        end_idx = out.find("</think>", start_idx)
                        if end_idx != -1:
                            out = out[:start_idx] + out[end_idx + len("</think>"):]
                            in_think = False
                        else:
                            out = out[:start_idx]
                            in_think = True
                if out:
                    self.wfile.write(out.encode("utf-8"))
                    try:
                        self.wfile.flush()
                    except Exception:
                        pass
        except Exception as e:
            err = ("错误: " + str(e)).encode("utf-8")
            self.wfile.write(err)


def run(host="127.0.0.1", port=9000):
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    h = os.getenv("HOST", "127.0.0.1")
    p = int(os.getenv("PORT", "9000"))
    run(h, p)
