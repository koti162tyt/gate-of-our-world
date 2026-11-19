"""Панель вебхуков Discord для ведущего.

Локальный сервер: создание и переименование вебхуков, отправка сообщений
от имени любого персонажа. Только стандартная библиотека.

Запуск:  python server.py   ->  http://127.0.0.1:3210
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
AVATAR_DIR = ROOT / "avatars"
CONFIG_PATH = ROOT / "config.json"
DISCORD_API = "https://discord.com/api/v10"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "3210"))

DATA_URI_RE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+)$")
IMAGE_EXT = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp"}

DEFAULT_CHARACTERS = [
    "Система",
    "Эрик",
    "Евдокия",
    "Ночь",
    "Эрнест",
    "Виктория",
    "Чифрин",
    "Лиза",
    "А.Д.",
]

DEFAULT_CONFIG = {
    "token": "",
    "channelId": "",
    "webhooks": [],
    "activeWebhookId": "",
    "characters": [{"name": name, "avatar": ""} for name in DEFAULT_CHARACTERS],
    "lastCharacter": "Система",
    "useUsername": True,
}

WEBHOOK_URL_RE = re.compile(
    r"^https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)/?$"
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class DiscordError(Exception):
    def __init__(self, message: str, status: int = 502, data=None):
        super().__init__(message)
        self.status = status
        self.data = data


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- config


def load_config() -> dict:
    try:
        with CONFIG_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data if isinstance(data, dict) else {})
    return merged


def save_config(cfg: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def public_state(cfg: dict) -> dict:
    return {
        "ok": True,
        "hasToken": bool(cfg.get("token")),
        "channelId": cfg.get("channelId", ""),
        "activeWebhookId": cfg.get("activeWebhookId", ""),
        "webhooks": [
            {
                "id": w.get("id", ""),
                "name": w.get("name", ""),
                "channelId": w.get("channelId", ""),
                "url": w.get("url", ""),
            }
            for w in cfg.get("webhooks", [])
        ],
        "characters": cfg.get("characters", []),
        "lastCharacter": cfg.get("lastCharacter", ""),
        "useUsername": bool(cfg.get("useUsername", True)),
    }


# -------------------------------------------------------------------- discord


def discord_request(endpoint: str, *, method: str = "GET", body=None, token: str = ""):
    if not token:
        raise ApiError("Не задан токен бота")

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(DISCORD_API + endpoint, data=data, method=method)
    request.add_header("Authorization", f"Bot {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "DiscordBot (gm-panel, 1.0)")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"raw": raw}
        message = payload.get("message") or f"Discord ответил {exc.code}"
        hints = {
            401: "Токен не принят Discord",
            403: "У бота нет прав: нужен Manage Webhooks и доступ к каналу",
            404: "Канал или вебхук не найден",
            429: "Слишком часто: Discord просит подождать",
        }
        raise DiscordError(hints.get(exc.code, message), exc.code, payload) from exc
    except urllib.error.URLError as exc:
        raise DiscordError(f"Нет связи с Discord: {exc.reason}") from exc


def execute_webhook(url: str, payload: dict, attempt: int = 0):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{url}?wait=true", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "gm-panel/1.0")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload_data = json.loads(raw) if raw else {}
        except ValueError:
            payload_data = {"raw": raw}

        if exc.code == 429 and attempt < 2:
            retry = float(payload_data.get("retry_after") or 1)
            time.sleep(min(retry, 5) + 0.1)
            return execute_webhook(url, payload, attempt + 1)

        message = payload_data.get("message") or f"Discord ответил {exc.code}"
        raise DiscordError(message, exc.code, payload_data) from exc
    except urllib.error.URLError as exc:
        raise DiscordError(f"Нет связи с Discord: {exc.reason}") from exc


def parse_webhook_url(url: str):
    match = WEBHOOK_URL_RE.match(str(url or "").strip())
    if not match:
        return None
    hook_id, hook_token = match.group(1), match.group(2)
    return {
        "id": hook_id,
        "token": hook_token,
        "url": f"https://discord.com/api/webhooks/{hook_id}/{hook_token}",
    }


# ------------------------------------------------------------------- аватары

AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def decode_avatar(data_uri: str):
    match = DATA_URI_RE.match(str(data_uri or "").strip())
    if not match:
        raise ApiError("Нужна картинка в base64: data:image/png;base64,…")
    payload = re.sub(r"\s+", "", match.group(2))
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError("Битые данные картинки") from exc
    if len(raw) > 6_000_000:
        raise ApiError("Файл больше 6 МБ — уменьши картинку")
    return raw, IMAGE_EXT[match.group(1).lower()]


def save_avatar_file(key: str, raw: bytes, ext: str) -> str:
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ext
    target = AVATAR_DIR / name
    target.write_bytes(raw)
    return f"/avatars/{name}"


def discord_avatar_url(hook_id: str, avatar_hash: str) -> str:
    if not avatar_hash:
        return ""
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{hook_id}/{avatar_hash}.{ext}?size=128"


def patch_webhook_avatar(cfg: dict, hook: dict, data_uri: str) -> dict:
    if not cfg.get("token"):
        raise ApiError("Загрузить фото можно только с токеном бота: без него Discord не примет картинку")
    info = discord_request(
        f"/webhooks/{hook['id']}",
        method="PATCH",
        body={"avatar": data_uri},
        token=cfg["token"],
    )
    hook["avatarHash"] = info.get("avatar") or ""
    hook["avatarUrl"] = discord_avatar_url(hook["id"], hook.get("avatarHash", ""))
    return hook


def ensure_character_webhook(cfg: dict, character_name: str, data_uri: str) -> dict:
    if not cfg.get("token"):
        raise ApiError("Загрузить фото можно только с токеном бота: без него Discord не примет картинку")

    for hook in cfg.get("webhooks", []):
        if hook.get("character") == character_name:
            return patch_webhook_avatar(cfg, hook, data_uri)

    channel_id = str(cfg.get("channelId") or "").strip()
    if not channel_id:
        raise ApiError("Укажи ID канала: для фото под персонажа нужен свой вебхук")

    created = discord_request(
        f"/channels/{channel_id}/webhooks",
        method="POST",
        body={"name": character_name, "avatar": data_uri},
        token=cfg["token"],
    )
    record = {
        "id": created["id"],
        "token": created["token"],
        "url": f"https://discord.com/api/webhooks/{created['id']}/{created['token']}",
        "name": created.get("name") or character_name,
        "channelId": created.get("channel_id", channel_id),
        "character": character_name,
        "avatarHash": created.get("avatar") or "",
        "avatarUrl": discord_avatar_url(created["id"], created.get("avatar", "")),
    }
    cfg["webhooks"].append(record)
    return record


def set_character_avatar(cfg: dict, body: dict) -> dict:
    name = str(body.get("name", "")).strip()
    character = next((c for c in cfg.get("characters", []) if c.get("name") == name), None)
    if not character:
        raise ApiError("Персонаж не найден", 404)

    raw, ext = decode_avatar(body.get("dataUrl", ""))
    path = save_avatar_file("char:" + name, raw, ext)
    character["avatar"] = path

    if cfg.get("token"):
        hook = ensure_character_webhook(cfg, name, body["dataUrl"])
        character["webhookId"] = hook["id"]
        character["avatarUrl"] = hook.get("avatarUrl", "")
        save_config(cfg)
        return {**public_state(cfg), "warning": ""}

    save_config(cfg)
    return {
        **public_state(cfg),
        "warning": "Фото сохранено, но Discord покажет его только с токеном бота. Без токена нужна ссылка https://…",
    }


def set_webhook_avatar(cfg: dict, body: dict) -> dict:
    hook = find_webhook(cfg, str(body.get("id", "")))
    if not hook:
        raise ApiError("Вебхук не выбран", 404)

    raw, ext = decode_avatar(body.get("dataUrl", ""))
    path = save_avatar_file("hook:" + hook["id"], raw, ext)
    hook["avatarFile"] = path
    patch_webhook_avatar(cfg, hook, body["dataUrl"])
    save_config(cfg)
    return public_state(cfg)


def parse_avatar_url(url: str):
    """Проверяет, что по ссылке действительно картинка, а не страница и не 403."""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ApiError("Ссылка должна начинаться с https://")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            chunk = response.read(4096)
            is_image = content_type.startswith("image/")
            return {
                "ok": True,
                "status": response.status,
                "contentType": content_type,
                "isImage": is_image,
                "hint": "" if is_image else "По ссылке не картинка — Discord аватар не поставит",
                "head": "" if is_image else chunk[:60].decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        hints = {
            403: "Сайт запрещает прямые ссылки (403). Скачай картинку и загрузи файлом.",
            404: "Ссылка битая (404).",
            429: "Сайт ограничивает запросы.",
        }
        return {
            "ok": False,
            "status": exc.code,
            "hint": hints.get(exc.code, f"Сайт ответил {exc.code}"),
            "isImage": False,
        }
    except urllib.error.URLError as exc:
        return {"ok": False, "status": 0, "hint": f"Не открывается: {exc.reason}", "isImage": False}


def find_webhook(cfg: dict, hook_id: str = ""):
    target = hook_id or cfg.get("activeWebhookId") or ""
    for hook in cfg.get("webhooks", []):
        if hook.get("id") == target:
            return hook
    return None


# --------------------------------------------------------------------- routes


def create_webhook(cfg: dict, body: dict) -> dict:
    name = str(body.get("name", "")).strip() or "Голос"
    channel_id = str(body.get("channelId") or cfg.get("channelId", "")).strip()
    if not channel_id:
        raise ApiError("Укажи ID канала")
    if not cfg.get("token"):
        raise ApiError("Создать вебхук через API можно только с токеном бота")

    hook = discord_request(
        f"/channels/{channel_id}/webhooks",
        method="POST",
        body={"name": name},
        token=cfg["token"],
    )
    record = {
        "id": hook["id"],
        "token": hook["token"],
        "url": f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}",
        "name": hook.get("name") or name,
        "channelId": hook.get("channel_id", channel_id),
    }
    cfg["webhooks"] = [w for w in cfg.get("webhooks", []) if w.get("id") != record["id"]]
    cfg["webhooks"].append(record)
    cfg["activeWebhookId"] = record["id"]
    save_config(cfg)
    return public_state(cfg)


def add_webhook(cfg: dict, body: dict) -> dict:
    parsed = parse_webhook_url(body.get("url", ""))
    if not parsed:
        raise ApiError("Ссылка должна выглядеть так: https://discord.com/api/webhooks/ID/TOKEN")

    name = str(body.get("name", "")).strip()
    if not name and cfg.get("token"):
        try:
            info = discord_request(f"/webhooks/{parsed['id']}", token=cfg["token"])
            name = info.get("name") or ""
        except DiscordError:
            pass

    record = {**parsed, "name": name or "Вебхук", "channelId": ""}
    cfg["webhooks"] = [w for w in cfg.get("webhooks", []) if w.get("id") != record["id"]]
    cfg["webhooks"].append(record)
    cfg["activeWebhookId"] = record["id"]
    save_config(cfg)
    return public_state(cfg)


def rename_webhook(cfg: dict, body: dict) -> dict:
    name = str(body.get("name", "")).strip()
    if not name:
        raise ApiError("Имя не может быть пустым")
    if len(name) > 80:
        raise ApiError("Discord разрешает не больше 80 символов")

    hook = find_webhook(cfg, str(body.get("id", "")))
    if not hook:
        raise ApiError("Вебхук не выбран", 404)

    if cfg.get("token"):
        info = discord_request(
            f"/webhooks/{hook['id']}",
            method="PATCH",
            body={"name": name},
            token=cfg["token"],
        )
        hook["name"] = info.get("name") or name
    else:
        hook["name"] = name

    save_config(cfg)
    return public_state(cfg)


def delete_webhook(cfg: dict, body: dict) -> dict:
    hook = find_webhook(cfg, str(body.get("id", "")))
    if not hook:
        raise ApiError("Вебхук не выбран", 404)

    if cfg.get("token") and body.get("removeFromDiscord", True):
        try:
            discord_request(f"/webhooks/{hook['id']}", method="DELETE", token=cfg["token"])
        except DiscordError as exc:
            if exc.status != 404:
                raise

    cfg["webhooks"] = [w for w in cfg.get("webhooks", []) if w.get("id") != hook["id"]]
    cfg["activeWebhookId"] = cfg["webhooks"][0]["id"] if cfg["webhooks"] else ""
    save_config(cfg)
    return public_state(cfg)


def send_message(cfg: dict, body: dict) -> dict:
    text = str(body.get("text", "")).strip()
    if not text:
        raise ApiError("Пустое сообщение")
    if len(text) > 2000:
        raise ApiError(f"Слишком длинно: {len(text)} из 2000 символов")

    character = str(body.get("character", "")).strip()
    preset = next((c for c in cfg.get("characters", []) if c.get("name") == character), None)

    # у персонажа может быть собственный вебхук с его аватаром
    own_hook_id = (preset or {}).get("webhookId", "")
    hook = find_webhook(cfg, str(body.get("webhookId") or own_hook_id or ""))
    if not hook:
        raise ApiError("Сначала добавь вебхук")

    payload: dict = {"content": text, "allowed_mentions": {"parse": []}}

    if character and cfg.get("useUsername", True) and body.get("asCharacter", True):
        payload["username"] = character[:80]
        avatar = str(body.get("avatar") or "").strip()
        if not avatar:
            stored = str((preset or {}).get("avatarUrl") or (preset or {}).get("avatar") or "").strip()
            if stored.startswith(("https://", "http://")):
                avatar = stored
        if avatar.startswith("https://"):
            payload["avatar_url"] = avatar

    message = execute_webhook(hook["url"], payload)
    if character:
        cfg["lastCharacter"] = character
    save_config(cfg)
    return {"ok": True, "messageId": message.get("id", ""), "channelId": message.get("channel_id", "")}


def edit_message(cfg: dict, body: dict) -> dict:
    hook = find_webhook(cfg, str(body.get("webhookId", "")))
    if not hook:
        raise ApiError("Вебхук не выбран")
    message_id = str(body.get("messageId", ""))
    if not message_id:
        raise ApiError("Не указан ID сообщения")

    text = str(body.get("text", ""))
    if len(text) > 2000:
        raise ApiError("Слишком длинно")

    request = urllib.request.Request(
        f"{hook['url']}/messages/{message_id}",
        data=json.dumps({"content": text}).encode("utf-8"),
        method="PATCH",
    )
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise DiscordError(f"Discord ответил {exc.code}", exc.code) from exc
    return {"ok": True, "messageId": data.get("id", "")}


def characters(cfg: dict, body: dict) -> dict:
    action = str(body.get("action", "add"))
    raw = body.get("character") or {}
    name = str(raw.get("name", "")).strip()
    avatar = str(raw.get("avatar", "")).strip()
    items = cfg.setdefault("characters", [])

    if action == "add":
        if not name:
            raise ApiError("Имя не может быть пустым")
        if any(c.get("name", "").lower() == name.lower() for c in items):
            raise ApiError("Такой персонаж уже есть")
        items.append({"name": name, "avatar": avatar})
    elif action == "update":
        old_name = str(body.get("oldName", ""))
        target = next((c for c in items if c.get("name") == old_name), None)
        if not target:
            raise ApiError("Персонаж не найден", 404)
        target["name"] = name or target.get("name", "")
        target["avatar"] = avatar
    elif action == "delete":
        cfg["characters"] = [c for c in items if c.get("name") != name]
    else:
        raise ApiError("Неизвестное действие")

    save_config(cfg)
    return public_state(cfg)


# --------------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "gm-panel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли
        if os.environ.get("VERBOSE"):
            super().log_message(fmt, *args)

    # --- helpers ---------------------------------------------------------

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 8_000_000:
            raise ApiError("Слишком большое тело запроса", 413)
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError("Тело запроса — не JSON") from exc

    def serve_static(self, pathname: str) -> None:
        if pathname.startswith("/avatars/"):
            file_path = (AVATAR_DIR / pathname[len("/avatars/"):]).resolve()
            root = AVATAR_DIR.resolve()
        else:
            rel = "index.html" if pathname in ("/", "") else pathname.lstrip("/")
            file_path = (PUBLIC_DIR / rel).resolve()
            root = PUBLIC_DIR.resolve()

        if not str(file_path).startswith(str(root)):
            return self.send_json(403, {"ok": False, "error": "Нет доступа"})
        if not file_path.is_file():
            return self.send_json(404, {"ok": False, "error": "Файл не найден"})
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=60")
        self.end_headers()
        self.wfile.write(data)

    # --- http ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        pathname, _, query = self.path.partition("?")
        try:
            if pathname == "/api/state":
                return self.send_json(200, public_state(load_config()))
            if pathname == "/api/avatar/check":
                params = dict(
                    pair.split("=", 1) for pair in query.split("&") if "=" in pair
                )
                target = urllib.parse.unquote(params.get("url", ""))
                return self.send_json(200, parse_avatar_url(target))
            if pathname.startswith("/api/"):
                return self.send_json(404, {"ok": False, "error": "Такого API нет"})
            return self.serve_static(pathname)
        except ApiError as exc:
            self.send_json(exc.status, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self):  # noqa: N802
        pathname = self.path.split("?", 1)[0]
        routes = {
            "/api/webhook/create": create_webhook,
            "/api/webhook/add": add_webhook,
            "/api/webhook/rename": rename_webhook,
            "/api/webhook/delete": delete_webhook,
            "/api/webhook/avatar": set_webhook_avatar,
            "/api/characters/avatar": set_character_avatar,
            "/api/send": send_message,
            "/api/message/edit": edit_message,
            "/api/characters": characters,
        }

        try:
            body = self.read_body()
            cfg = load_config()

            if pathname == "/api/settings":
                if isinstance(body.get("token"), str):
                    cfg["token"] = body["token"].strip()
                if isinstance(body.get("channelId"), str):
                    cfg["channelId"] = body["channelId"].strip()
                if isinstance(body.get("useUsername"), bool):
                    cfg["useUsername"] = body["useUsername"]
                if isinstance(body.get("activeWebhookId"), str):
                    cfg["activeWebhookId"] = body["activeWebhookId"]
                if isinstance(body.get("lastCharacter"), str):
                    cfg["lastCharacter"] = body["lastCharacter"]
                save_config(cfg)
                return self.send_json(200, public_state(cfg))

            handler = routes.get(pathname)
            if handler is None:
                return self.send_json(404, {"ok": False, "error": "Такого API нет"})

            return self.send_json(200, handler(cfg, body))

        except ApiError as exc:
            self.send_json(exc.status, {"ok": False, "error": str(exc)})
        except DiscordError as exc:
            payload = {"ok": False, "error": str(exc)}
            if exc.data:
                payload["discord"] = exc.data
            self.send_json(exc.status if 400 <= exc.status < 600 else 502, payload)
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"ok": False, "error": f"Внутренняя ошибка: {exc}"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Панель вебхуков: http://{HOST}:{PORT}")
    print(f"Конфиг: {CONFIG_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
