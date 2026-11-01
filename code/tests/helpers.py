"""Small helpers shared by several test files."""


def timeline_files(data):
    """The compact /api/timeline/scan rows as dicts with an absolute path."""
    root = data["root"].rstrip("/")
    columns = data["columns"]
    files = []
    for row in data["rows"]:
        item = dict(zip(columns, row))
        item["path"] = f"{root}/{item['path']}"
        files.append(item)
    return files


class FakeOllama:
    """
    Stands in for Ollama in every test (conftest's fake_ollama fixture), so no test talks
    to a real Ollama. Set .down, .models, .chat_status/.chat_lines or .chat_error.
    """

    def __init__(self):
        self.down = False
        self.models = ["qwen2.5:3b"]
        self.remote = set()         # names that /api/tags lists with a remote_host (Ollama cloud)
        self.chat_status = 200
        self.chat_lines = None      # list of dicts streamed as JSON lines
        self.chat_error = None      # an httpx exception raised for /api/chat
        self.requests = []

    def handler(self, request):
        import json
        import httpx
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("Connection refused", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": m, "size": 1929912432, "details": {"parameter_size": "3.1B"},
                 **({"remote_host": "https://ollama.com:443", "remote_model": m} if m in self.remote else {})}
                for m in self.models]})
        if request.url.path == "/api/chat":
            if self.chat_error is not None:
                raise self.chat_error
            body = json.loads(request.content)
            if body["model"] not in self.models:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": "kapot"})
            lines = self.chat_lines if self.chat_lines is not None else [
                {"message": {"content": "Hallo "}, "done": False},
                {"message": {"content": "daar, ik ben Pasha."}, "done": False},
                {"done": True},
            ]
            if not body.get("stream", True):
                text = "".join((l.get("message") or {}).get("content", "") for l in lines)
                return httpx.Response(200, json={"message": {"content": text}, "done": True})
            return httpx.Response(200, content="".join(json.dumps(l) + "\n" for l in lines).encode())
        return httpx.Response(404)
