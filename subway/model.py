"""Local model extracts intent only. It never supplies routing facts."""

import json, os, re
import httpx

URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11435")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "journey",
                "location",
                "missed",
                "repeat",
                "restart",
                "progress",
                "question",
            ],
        },
        "origin": {"type": "string"},
        "destination": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["action", "origin", "destination", "reply"],
    "additionalProperties": False,
}
SYSTEM = """You extract intent for an NYC subway assistant. Return JSON only. Never provide trains, directions, schedules or coordinates. action is journey when the user asks to go somewhere, location when reporting current location, missed for missed train/stop or wrong direction, repeat to repeat instructions, restart to start over, progress to advance, question for other messages. origin and destination are place names stated by the user, otherwise empty strings. Keep the most specific known place, e.g. Bryant Park next to an unrecognized waffle shop -> Bryant Park. "Wall Street to the raging bull" destination = Charging Bull. "I'm at Bryant Park, I want to go to Charging Bull" -> origin Bryant Park, destination Charging Bull. Never infer a current location from history. reply is a brief friendly clarification only if no origin or destination was supplied, otherwise empty. Do not respond to unrelated questions beyond asking for an NYC origin and destination."""


async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(URL + "/api/tags")
            r.raise_for_status()
            return any(m["name"] == MODEL for m in r.json().get("models", []))
    except (httpx.HTTPError, ValueError):
        return False


def stated_place(value, message):
    """Do not promote a model's example or invented location into a user goal."""
    def tokens(text):
        text = text.lower().replace("raging bull", "charging bull")
        text = re.sub(r"\bthe met\b", "metropolitan museum of art", text)
        for short, full in [("st", "street"), ("sq", "square"), ("ave", "avenue")]:
            text = re.sub(r"\b"+short+r"\b", full, text)
        return set(re.findall(r"[a-z0-9]+", text)) - {"the", "of", "and", "terminal", "station", "subway", "nyc"}
    wanted = tokens(value)
    return bool(wanted) and wanted.issubset(tokens(message))


async def extract(message):
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(
                URL + "/api/chat",
                json={
                    "model": MODEL,
                    "stream": False,
                    "format": SCHEMA,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": message},
                    ],
                    "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 220},
                    "keep_alive": "15m",
                },
            )
            r.raise_for_status()
            obj = json.loads(r.json()["message"]["content"])
            if obj.get("action") not in SCHEMA["properties"]["action"]["enum"]:
                raise ValueError("Invalid action")
            for role in ("origin", "destination"):
                if obj.get(role) and not stated_place(obj[role], message):
                    obj[role] = ""
            return obj, True
    except (httpx.HTTPError, ValueError, KeyError):
        m = re.search(
            r"(?:i(?:'m| am) (?:at|in)|from)\s+(.+?)(?:[,;.]?\s+(?:and\s+)?(?:i (?:want|need|would like) to go|to go|going)\s+to\s+|\s+to\s+)(.+)",
            message,
            re.I,
        )
        if m:
            return {
                "action": "journey",
                "origin": m[1].strip(" ,."),
                "destination": m[2].strip(" ."),
                "reply": "",
            }, False
        m = re.search(r"(?:go|going|take me|get me|head) to\s+(.+)", message, re.I)
        if m:
            return {
                "action": "journey",
                "origin": "",
                "destination": m[1].strip(" ."),
                "reply": "",
            }, False
        return {
            "action": "location",
            "origin": re.sub(
                r"^(?:i(?:'m| am) (?:at|in)|at)\s+", "", message, flags=re.I
            ).strip(" ."),
            "destination": "",
            "reply": "",
        }, False
