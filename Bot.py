import asyncio
import logging
import math
import os
import re
import socket
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from uuid import uuid4

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    ChatPermissions,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

try:
    import whois
except ImportError:
    whois = None

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
AI_MODEL = "google/gemini-2.5-flash"

if not BOT_TOKEN:
    raise ValueError("Укажите токен бота в переменной окружения BOT_TOKEN")

AI_KEY = OPENROUTER_API_KEY or ""
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_SYSTEM = "Ты полезный ассистент в Telegram-чате. Отвечай на языке пользователя, кратко и по делу."

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RATES_URL = "https://open.er-api.com/v6/latest/USD"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
BOT_USERNAME = ""

PREFIXES = ("!", ".", "/")

ALIASES = {
    "start": "start", "старт": "start",
    "help": "help", "помощь": "help", "хелп": "help",
    "ban": "ban", "бан": "ban",
    "mute": "mute", "мут": "mute",
    "kick": "kick", "кик": "kick",
    "kickme": "kickme", "кикми": "kickme",
    "ai": "ai", "ии": "ai", "аи": "ai",
    "реши": "solve", "solve": "solve",
    "whois": "whois", "вхоис": "whois",
    "поиск": "search", "search": "search",
    "погода": "weather", "weather": "weather",
    "курс": "rate", "rate": "rate",
    "ид": "id", "id": "id",
}

NO_PREFIX_OK = {"бан", "мут", "кик", "реши", "поиск", "погода", "курс", "ид"}

START_TEXT = "Приветствую!\n/help для полного доступа команд."

HELP_TEXT = (
    "Все команды:\n"
    "Бан время @ – отправляет пользователя в бан на определённый срок.\n"
    "Мут время @ – отправляет пользователя в мут на определенный срок.\n"
    "Кик @ – кикает пользователя.\n"
    "/kickme – выйти из чата по собственному желанию.\n"
    "Реши пример – решает математические примеры.\n"
    "/ai текст – вызывает AI ассистента.\n"
    "/whois сайт – информация о домене.\n"
    "Поиск запрос – ссылка на поиск в Яндексе.\n"
    "Погода город – погода сейчас и информация об осадках.\n"
    "Курс – актуальные курсы Bitcoin, Ethereum, Solana, Stellar, USD, EUR, UAH, BYN, CNY, GBP и других в рублях.\n"
    "Курс валюта [валюта] – курс одной валюты (например: Курс USD или Курс доллар рубль).\n"
    "Ид @ / ответ – показывает Telegram ID пользователя."
)

USAGE_SOLVE = "Как пользоваться командой:\nРеши пример\n\nПример:\nРеши 1+1"
USAGE_WHOIS = "Как пользоваться командой:\n/whois сайт\n\nПример:\n/whois github.io"
USAGE_SEARCH = "Как пользоваться командой:\nПоиск запрос\n\nПример:\nПоиск погода в Стокгольме"
USAGE_AI = "Как пользоваться командой:\n/ai текст\n\nПример:\n/ai Расскажи анекдот"
USAGE_WEATHER = "Как пользоваться командой:\nПогода город\n\nПример:\nПогода Москва"
USAGE_RATE = (
    "Как пользоваться командой:\n"
    "Курс – показать популярные курсы в рублях\n"
    "Курс валюта [валюта] – курс одной валюты\n\n"
    "Примеры:\nКурс\nКурс USD\nКурс доллар рубль\nКурс BTC"
)
USAGE_ID = "Как пользоваться командой:\nИд\nИд @юзер\nИд (ответом на сообщение)"
GUEST_HINT = "Напишите запрос после моего юзернейма, например: реши 2+2 или любой вопрос."


def parse_command(text):
    text = (text or "").strip()
    if not text:
        return None

    prefixed = False
    if text[0] in PREFIXES:
        prefixed = True
        text = text[1:].lstrip()
        if not text:
            return None

    parts = re.split(r"\s+", text, maxsplit=1)
    word = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""

    if "@" in word:
        word, _, name = word.partition("@")
        if name.lower() != BOT_USERNAME.lower():
            return None

    word = word.lower()
    command = ALIASES.get(word)
    if command is None:
        return None
    if not prefixed and word not in NO_PREFIX_OK:
        return None
    return command, rest


FUNCS = {
    "sqrt": math.sqrt, "abs": abs,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log10, "log10": math.log10, "log2": math.log2,
    "ln": math.log, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "round": round,
    "fact": math.factorial,
}

CONSTS = {"pi": math.pi, "e": math.e}

UNICODE_MAP = {
    "×": "*", "✕": "*", "∙": "*", "⋅": "*",
    "÷": "/", "⁄": "/",
    "−": "-", "–": "-", "—": "-", "‐": "-",
    "⁻": "-", "⁺": "+",
    "π": "pi",
    "≤": "<=", "≥": ">=",
    ",": ".",
}


def normalize(src):
    for k, v in UNICODE_MAP.items():
        src = src.replace(k, v)
    src = re.sub(r"(?<=\d)\s*[xх]\s*(?=\d)", "*", src)
    return src


def tokenize(src):
    src = normalize(src)
    tokens = []
    i = 0
    while i < len(src):
        c = src[i]

        if c.isspace():
            i += 1
            continue

        if c.isdigit() or (c == "." and i + 1 < len(src) and src[i + 1].isdigit()):
            num = ""
            while i < len(src) and (src[i].isdigit() or src[i] == "."):
                num += src[i]
                i += 1
            if num.count(".") > 1:
                raise ValueError(f"Некорректное число: {num}")
            try:
                tokens.append(("num", float(num)))
            except ValueError:
                raise ValueError(f"Некорректное число: {num}")
            continue

        if c.isalpha():
            word = ""
            while i < len(src) and (src[i].isalpha() or src[i].isdigit()):
                word += src[i]
                i += 1
            word = word.lower()
            if word in CONSTS:
                tokens.append(("const", word))
            else:
                tokens.append(("func", word))
            continue

        if c == "√":
            tokens.append(("sqrt", "√"))
            i += 1
            continue

        if c == "%":
            prev = src[:i].rstrip()
            postfix = bool(prev) and (prev[-1].isdigit() or prev[-1] == ")" or prev[-1] == ".")
            tokens.append(("%p" if postfix else "%", "%"))
            i += 1
            continue

        if c == "!":
            tokens.append(("!", "!"))
            i += 1
            continue

        if c in "+-*/^()":
            tokens.append((c, c))
            i += 1
            continue

        raise ValueError(f"Недопустимый символ: {c}")

    return tokens


def evaluate(expression):
    tokens = tokenize(expression)
    state = [0]

    def peek():
        return tokens[state[0]] if state[0] < len(tokens) else None

    def eat(kind):
        if state[0] >= len(tokens) or tokens[state[0]][0] != kind:
            raise ValueError(f"ожидалось {kind}")
        tok = tokens[state[0]]
        state[0] += 1
        return tok

    def parse_expr():
        left = parse_term()
        while peek() and peek()[0] in "+-":
            op = tokens[state[0]][0]
            state[0] += 1
            right = parse_term()
            left = left + right if op == "+" else left - right
        return left

    def parse_term():
        left = parse_power()
        while peek() and peek()[0] in "*/%":
            op = tokens[state[0]][0]
            state[0] += 1
            right = parse_power()
            if op == "*":
                left = left * right
            elif op == "/":
                if right == 0:
                    raise ValueError("деление на ноль")
                left = left / right
            else:
                if right == 0:
                    raise ValueError("деление на ноль")
                left = left % right
        return left

    def parse_power():
        base = parse_unary()
        if peek() and peek()[0] == "^":
            state[0] += 1
            exp = parse_power()
            if abs(exp) > 10000:
                raise ValueError("слишком большая степень")
            result = base ** exp
            if isinstance(result, complex):
                raise ValueError("результат не является вещественным числом")
            return result
        return base

    def parse_unary():
        if peek() and peek()[0] in "+-":
            op = tokens[state[0]][0]
            state[0] += 1
            value = parse_unary()
            return -value if op == "-" else value
        if peek() and peek()[0] == "sqrt":
            state[0] += 1
            return math.sqrt(parse_unary())
        return parse_atom()

    def parse_atom():
        tok = peek()
        if tok is None:
            raise ValueError("незавершённое выражение")

        if tok[0] == "num":
            state[0] += 1
            value = tok[1]

        elif tok[0] == "const":
            state[0] += 1
            value = CONSTS[tok[1]]
            if peek() and peek()[0] == "(":
                state[0] += 1
                arg = parse_expr()
                eat(")")
                value = value * arg

        elif tok[0] == "(":
            state[0] += 1
            value = parse_expr()
            eat(")")

        elif tok[0] == "func":
            state[0] += 1
            fn = FUNCS.get(tok[1])
            if fn is None:
                raise ValueError(f"нет функции {tok[1]}")
            eat("(")
            arg = parse_expr()
            eat(")")
            value = fn(arg)

        else:
            raise ValueError("лишние символы")

        while peek() and peek()[0] == "%p":
            state[0] += 1
            value = value / 100.0
        while peek() and peek()[0] == "!":
            state[0] += 1
            if abs(value - round(value)) > 1e-9 or value < 0:
                raise ValueError("факториал только для целого неотрицательного числа")
            if value > 170:
                raise ValueError("факториал слишком большой (максимум 170)")
            value = math.factorial(int(round(value)))

        return value

    result = parse_expr()
    if state[0] != len(tokens):
        raise ValueError("лишние символы в конце")
    return result


def format_number(value):
    if not math.isfinite(value):
        return "не число"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(float(f"{value:.12g}"))


def solve_text(expression):
    expression = expression.strip()
    if not expression:
        return USAGE_SOLVE
    try:
        return f"{expression} = {format_number(evaluate(expression))}"
    except OverflowError:
        return "Не смог решить: слишком большое число"
    except Exception as e:
        return f"Не смог решить: {e}"


async def get_json(url, params=None):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def ask_ai(prompt):
    if not AI_KEY:
        return "AI не настроен: укажите ключ OpenRouter в переменной окружения OPENROUTER_API_KEY."

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1000,
    }
    headers = {"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"}

    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers) as response:
                status = response.status
                data = await response.json(content_type=None)
    except asyncio.TimeoutError:
        return "ИИ слишком долго думает, попробуйте ещё раз."
    except aiohttp.ClientError:
        return "Не удалось связаться с OpenRouter."

    if status != 200:
        error = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        return f"Ошибка AI ({status}): {error or 'неизвестная ошибка'}"

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "AI вернул пустой ответ."
    return (text or "").strip() or "AI вернул пустой ответ."


db = sqlite3.connect("users.db")
db.execute(
    "CREATE TABLE IF NOT EXISTS users ("
    "chat_id INTEGER, user_id INTEGER, username TEXT, PRIMARY KEY (chat_id, user_id))"
)
db.commit()


def remember_user(message):
    if message.chat.type not in ("group", "supergroup"):
        return
    users = [message.from_user]
    if message.reply_to_message:
        users.append(message.reply_to_message.from_user)
    for user in users:
        if user and user.username:
            db.execute(
                "INSERT OR REPLACE INTO users VALUES (?, ?, ?)",
                (message.chat.id, user.id, user.username.lower()),
            )
    db.commit()


def find_user_id(chat_id, username):
    row = db.execute(
        "SELECT user_id FROM users WHERE chat_id = ? AND username = ? ORDER BY rowid DESC LIMIT 1",
        (chat_id, username.lower()),
    ).fetchone()
    return row[0] if row else None


FOREVER_WORDS = {"навсегда", "forever", "perm", "перм"}
MIN_SECONDS = 30
MAX_SECONDS = 366 * 86400


def unit_seconds(unit):
    unit = unit.lower()
    if unit in ("с", "s") or unit.startswith(("сек", "sec")):
        return 1
    if unit in ("м", "m") or unit.startswith(("мин", "min")):
        return 60
    if unit in ("ч", "h") or unit.startswith(("час", "hour")):
        return 3600
    if unit in ("д", "d") or unit.startswith(("ден", "дн", "day")):
        return 86400
    if unit in ("н", "w") or unit.startswith(("нед", "week")):
        return 604800
    if unit.startswith(("мес", "month")):
        return 2592000
    return None


def parse_duration(text):
    text = text.strip().lower()
    if not text or text in FOREVER_WORDS:
        return 0
    pattern = r"(\d+)\s*([^\W\d_]+)"
    matches = re.findall(pattern, text)
    if not matches or re.sub(pattern, "", text).strip():
        return None
    total = 0
    for number, unit in matches:
        multiplier = unit_seconds(unit)
        if multiplier is None:
            return None
        total += int(number) * multiplier
    return total


def plural(n, forms):
    n100 = n % 100
    if 11 <= n100 <= 14:
        return forms[2]
    n10 = n % 10
    if n10 == 1:
        return forms[0]
    if 2 <= n10 <= 4:
        return forms[1]
    return forms[2]


def format_duration(seconds):
    if not seconds:
        return "навсегда"
    parts = []
    sizes = (
        (86400, ("день", "дня", "дней")),
        (3600, ("час", "часа", "часов")),
        (60, ("минуту", "минуты", "минут")),
        (1, ("секунду", "секунды", "секунд")),
    )
    for size, forms in sizes:
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {plural(count, forms)}")
    return "на " + " ".join(parts)


async def resolve_target(message, rest):
    for entity in message.entities or []:
        if entity.type == "text_mention" and entity.user:
            name = entity.extract_from(message.text)
            return entity.user.id, entity.user.full_name, rest.replace(name, "", 1).strip()

    match = re.search(r"@([A-Za-z0-9_]{4,32})", rest)
    if match:
        username = match.group(1)
        remaining = (rest[:match.start()] + " " + rest[match.end():]).strip()
        user_id = find_user_id(message.chat.id, username)
        if user_id is None:
            try:
                chat = await bot.get_chat("@" + username)
                if chat.type == "private":
                    user_id = chat.id
            except TelegramAPIError:
                pass
        if user_id is None:
            raise ValueError(
                f"Не нашёл @{username}. Пусть напишет в чат, либо ответьте на его сообщение, либо укажите ID."
            )
        return user_id, "@" + username, remaining

    match = re.search(r"(?<!\S)(\d{7,15})(?!\S)", rest)
    if match:
        remaining = (rest[:match.start()] + " " + rest[match.end():]).strip()
        return int(match.group(1)), match.group(1), remaining

    reply = message.reply_to_message
    if reply and reply.from_user:
        return reply.from_user.id, reply.from_user.full_name, rest

    raise ValueError("Укажите пользователя: @юз, ID или ответьте на его сообщение.")


async def has_rights(chat_id, user_id):
    member = await bot.get_chat_member(chat_id, user_id)
    if member.status == "creator":
        return True
    return member.status == "administrator" and bool(getattr(member, "can_restrict_members", False))


async def is_creator(chat_id, user_id):
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status == "creator"


MUTED = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


async def moderate(message, rest, action):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группах.")
        return

    try:
        if not await has_rights(message.chat.id, message.from_user.id):
            await message.answer("У вас нет прав для этой команды.")
            return
        user_id, display, remaining = await resolve_target(message, rest)
    except ValueError as e:
        await message.answer(str(e))
        return
    except TelegramAPIError as e:
        await message.answer(f"Не получилось: {e.message}")
        return

    seconds = 0
    if action in ("ban", "mute"):
        seconds = parse_duration(remaining)
        if seconds is None:
            await message.answer(f"Не понял срок. Пример: {'Бан' if action == 'ban' else 'Мут'} 1 час @юз")
            return
        if 0 < seconds < MIN_SECONDS:
            seconds = MIN_SECONDS
        if seconds > MAX_SECONDS:
            seconds = 0

    if user_id == bot.id:
        await message.answer("Себя я трогать не буду.")
        return

    try:
        target = await bot.get_chat_member(message.chat.id, user_id)
        if target.status in ("creator", "administrator"):
            await message.answer("Нельзя применить команду к администратору.")
            return

        until = timedelta(seconds=seconds) if seconds else None

        if action == "ban":
            await bot.ban_chat_member(message.chat.id, user_id, until_date=until)
            await message.answer(f"{display} забанен {format_duration(seconds)}.")
        elif action == "mute":
            await bot.restrict_chat_member(
                message.chat.id, user_id, permissions=MUTED, until_date=until
            )
            await message.answer(f"{display} в муте {format_duration(seconds)}.")
        else:
            await bot.ban_chat_member(message.chat.id, user_id)
            await bot.unban_chat_member(message.chat.id, user_id, only_if_banned=True)
            await message.answer(f"{display} кикнут.")
    except TelegramAPIError as e:
        await message.answer(f"Не получилось: {e.message}")


async def kickme(message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группах.")
        return
    user = message.from_user
    try:
        await bot.ban_chat_member(message.chat.id, user.id)
        await bot.unban_chat_member(message.chat.id, user.id, only_if_banned=True)
        await message.answer(f"{user.full_name} вышел из чата.")
    except TelegramAPIError:
        await message.answer("Не получилось: администратора кикнуть нельзя.")


async def ai_command(message, rest):
    prompt = rest
    reply = message.reply_to_message
    if reply and (reply.text or reply.caption):
        quoted = reply.text or reply.caption
        prompt = f"{rest}\n\nСообщение, на которое ответили:\n{quoted}" if rest else quoted
    if not prompt:
        await message.answer(USAGE_AI)
        return
    await bot.send_chat_action(message.chat.id, "typing")
    answer = await ask_ai(prompt)
    await message.reply(answer[:4000])


def clean_domain(raw):
    raw = raw.strip().lower()
    raw = re.sub(r"^[a-z][a-z0-9+.-]*://", "", raw)
    raw = re.split(r"[/?#]", raw, maxsplit=1)[0]
    raw = raw.split("@")[-1].split(":")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    raw = raw.strip(".")
    try:
        ascii_name = raw.encode("idna").decode()
    except UnicodeError:
        return None
    if not re.fullmatch(r"[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})+", ascii_name):
        return None
    return ascii_name


def pick(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    if isinstance(value, set):
        return sorted(value, key=str)[0] if value else None
    return value


def format_date(value):
    value = pick(value)
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    return str(value) if value else None


def whois_lookup(domain):
    try:
        data = whois.whois(domain)
    except Exception as e:
        text = f"{type(e).__name__} {e}".lower()
        if any(m in text for m in ("not found", "notfound", "no match", "no entries", "not registered")):
            return None
        raise
    keys = ("domain_name", "registrar", "creation_date", "expiration_date", "name_servers", "status")
    if not data or not any(data.get(k) for k in keys):
        return None
    return data


async def resolve_ip(domain):
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(domain, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    return infos[0][4][0] if infos else None


async def ip_info(ip):
    url = f"http://ip-api.com/json/{ip}?fields=status,country,isp,org,as&lang=ru"
    try:
        data = await get_json(url)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        return None
    if isinstance(data, dict) and data.get("status") == "success":
        return data
    return None


async def whois_command(message, rest):
    if whois is None:
        await message.answer("Для этой команды не установлена библиотека python-whois.")
        return

    domain = clean_domain(rest)
    if not domain:
        await message.answer(USAGE_WHOIS)
        return

    await bot.send_chat_action(message.chat.id, "typing")
    loop = asyncio.get_running_loop()
    try:
        data = await asyncio.wait_for(loop.run_in_executor(None, whois_lookup, domain), timeout=25)
    except asyncio.TimeoutError:
        await message.answer("Whois-сервер долго не отвечает, попробуйте позже.")
        return
    except Exception as e:
        await message.answer(f"Не удалось получить данные: {e}")
        return

    if data is None:
        await message.answer(f"Домен: {domain}\nСтатус: свободен")
        return

    lines = [f"Домен: {domain}", "Статус: занят"]

    registrar = pick(data.get("registrar"))
    if registrar:
        lines.append(f"Регистратор: {registrar}")

    owner = pick(data.get("org"))
    if owner:
        lines.append(f"Организация: {owner}")

    created = format_date(data.get("creation_date"))
    if created:
        lines.append(f"Создан: {created}")

    expires = format_date(data.get("expiration_date"))
    if expires:
        lines.append(f"Истекает: {expires}")

    name_servers = data.get("name_servers")
    if name_servers:
        if isinstance(name_servers, str):
            name_servers = [name_servers]
        unique = sorted({str(n).lower() for n in name_servers})
        lines.append("NS: " + ", ".join(unique[:4]))

    ip = await resolve_ip(domain)
    if ip:
        lines.append(f"IP: {ip}")
        info = await ip_info(ip)
        if info:
            hosting = info.get("org") or info.get("isp")
            if hosting:
                lines.append(f"Хостинг: {hosting}")
            if info.get("country"):
                lines.append(f"Страна: {info['country']}")
            if info.get("as"):
                lines.append(f"AS: {info['as']}")
    else:
        lines.append("IP: не найден (DNS не настроен)")

    await message.answer("\n".join(lines))


async def search_command(message, rest):
    if not rest:
        await message.answer(USAGE_SEARCH)
        return
    url = "https://yandex.ru/search/?text=" + quote_plus(rest)
    await message.reply(f"Результаты поиска по запросу «{rest}»:\n{url}")


WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "слабая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "сильная ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень",
    85: "небольшой снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
WET_MM = 0.1


def when_text(moment, now):
    days = (moment.date() - now.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return f"в {clock}"
    if days == 1:
        return f"завтра в {clock}"
    return f"{moment.strftime('%d.%m')} в {clock}"


def precipitation_kind(snowfall):
    return "снег" if snowfall and snowfall > 0 else "дождь"


def precipitation_outlook(data, now):
    current = data["current"]
    hourly = data["hourly"]
    times = [datetime.fromisoformat(t) for t in hourly["time"]]
    amounts = [v or 0 for v in hourly["precipitation"]]
    snow = [v or 0 for v in hourly["snowfall"]]
    code = current.get("weather_code") or 0
    hours = max(int((times[-1] - now).total_seconds() // 3600), 1)
    first = next((i for i, t in enumerate(times) if t > now), len(times))

    def wet(i):
        return amounts[i] >= WET_MM

    wet_now = (current.get("precipitation") or 0) >= WET_MM or code >= 51

    if wet_now:
        if code in SNOW_CODES:
            kind = "снег"
        elif code >= 51:
            kind = "дождь"
        else:
            kind = precipitation_kind(snow[first] if first < len(times) else 0)
        end_index = next((i for i in range(first, len(times)) if not wet(i)), None)
        if end_index is None:
            return f"сейчас идёт {kind}, в ближайшие {hours} ч не закончится"
        end = times[end_index] - timedelta(hours=1)
        if end <= now:
            return f"сейчас идёт {kind}, скоро закончится"
        return f"сейчас идёт {kind}, закончится примерно {when_text(end, now)}"

    start_index = next((i for i in range(first, len(times)) if wet(i)), None)
    if start_index is None:
        return f"осадков в ближайшие {hours} ч не ожидается"

    kind = precipitation_kind(snow[start_index])
    start = times[start_index] - timedelta(hours=1)
    if start <= now:
        text = f"сейчас без осадков, но скоро начнётся {kind}"
    else:
        text = f"сейчас без осадков, {kind} начнётся примерно {when_text(start, now)}"

    end_index = next((i for i in range(start_index, len(times)) if not wet(i)), None)
    if end_index is None:
        return text + " и продлится до конца прогноза"
    end = times[end_index] - timedelta(hours=1)
    return text + f", закончится примерно {when_text(end, now)}"


async def weather_command(message, rest):
    city = rest.strip()
    if not city:
        await message.answer(USAGE_WEATHER)
        return

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        geo = await get_json(
            GEOCODING_URL, {"name": city, "count": 1, "language": "ru", "format": "json"}
        )
        results = geo.get("results") or []
        if not results:
            await message.answer(f"Не нашёл город «{city}».")
            return
        place = results[0]
        data = await get_json(
            FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "weather_code,wind_speed_10m,precipitation",
                "hourly": "precipitation,snowfall",
                "forecast_days": 2,
                "timezone": "auto",
                "wind_speed_unit": "ms",
            },
        )
    except (asyncio.TimeoutError, aiohttp.ClientError):
        await message.answer("Не удалось получить погоду, попробуйте позже.")
        return

    try:
        current = data["current"]
        now = datetime.fromisoformat(current["time"])
        code = current.get("weather_code") or 0
        title = ", ".join(p for p in (place.get("name"), place.get("country")) if p)
        lines = [
            f"Погода: {title}",
            f"Сейчас: {round(current['temperature_2m'])}°C "
            f"(ощущается как {round(current['apparent_temperature'])}°C), {WMO.get(code, 'нет данных')}",
            f"Ветер: {current['wind_speed_10m']:.0f} м/с, влажность: {current['relative_humidity_2m']}%",
            f"Осадки: {precipitation_outlook(data, now)}",
        ]
    except (KeyError, TypeError, ValueError, IndexError):
        await message.answer("Сервис погоды вернул неполные данные, попробуйте позже.")
        return

    await message.answer("\n".join(lines))


CURRENCY_STEMS = (
    (("доллар", "бакс", "usd"), "USD"),
    (("евро", "eur"), "EUR"),
    (("рубл", "руб", "rub"), "RUB"),
    (("юан", "cny"), "CNY"),
    (("фунт", "gbp"), "GBP"),
    (("иен", "йен", "jpy"), "JPY"),
    (("гривн", "грн", "uah"), "UAH"),
    (("белорус", "byn"), "BYN"),
    (("тенге", "kzt"), "KZT"),
    (("злот", "pln"), "PLN"),
    (("франк", "chf"), "CHF"),
    (("лир", "try"), "TRY"),
    (("крон", "sek"), "SEK"),
    (("дирхам", "aed"), "AED"),
    (("биткоин", "биток", "btc"), "BTC"),
    (("эфир", "эфириум", "eth"), "ETH"),
    (("солана", "sol"), "SOL"),
    (("стеллар", "xlm"), "XLM"),
)

CRYPTO_CODES = {"BTC", "ETH", "SOL", "XLM"}
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XLM": "stellar",
}

rates_cache = {"time": 0.0, "data": None}
crypto_cache = {"time": 0.0, "data": None}


def currency_code(word):
    word = word.lower().replace("ё", "е").strip(".,")
    if re.fullmatch(r"[a-z]{3}", word):
        return word.upper()
    for stems, code in CURRENCY_STEMS:
        if any(word.startswith(s) for s in stems):
            return code
    return None


async def load_rates():
    if rates_cache["data"] and time.time() - rates_cache["time"] < 1800:
        return rates_cache["data"]
    data = await get_json(RATES_URL)
    if data.get("result") != "success":
        raise ValueError("bad response")
    rates_cache["time"] = time.time()
    rates_cache["data"] = data
    return data


async def load_crypto():
    if crypto_cache["data"] and time.time() - crypto_cache["time"] < 300:
        return crypto_cache["data"]
    ids = ",".join(CRYPTO_IDS.values())
    data = await get_json(COINGECKO_URL, {"ids": ids, "vs_currencies": "rub"})
    crypto_cache["time"] = time.time()
    crypto_cache["data"] = data
    return data


def format_rate(value):
    if value >= 1000:
        text = f"{value:,.2f}".replace(",", " ")
    elif value >= 1:
        text = f"{value:.4f}"
    else:
        text = f"{value:.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


async def rate_command(message, rest):
    tokens = rest.replace(",", ".").split()
    amount = 1.0
    if tokens and re.fullmatch(r"\d+(\.\d+)?", tokens[0]):
        amount = float(tokens[0])
        tokens = tokens[1:]

    if not tokens:
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            fiat_data = await load_rates()
            crypto_data = await load_crypto()
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
            await message.answer("Не удалось получить курсы, попробуйте позже.")
            return

        rates = fiat_data.get("rates") or {}
        rub = rates.get("RUB")
        if not rub:
            await message.answer("Нет данных по рублю.")
            return

        lines = ["Курсы в RUB:"]
        for code, cg_id in (("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana"), ("XLM", "stellar")):
            price = (crypto_data.get(cg_id) or {}).get("rub")
            if price is not None:
                lines.append(f"1 {code} = {format_rate(price)} RUB")

        for code in ("USD", "EUR", "UAH", "BYN", "CNY", "GBP", "JPY", "KZT", "PLN", "CHF", "TRY", "AED"):
            if code in rates:
                one = rub / rates[code]
                lines.append(f"1 {code} = {format_rate(one)} RUB")

        updated = fiat_data.get("time_last_update_unix")
        if updated:
            date = datetime.fromtimestamp(updated, timezone.utc).strftime("%d.%m.%Y")
            lines.append(f"Курс на {date}")
        await message.answer("\n".join(lines))
        return

    if len(tokens) == 1:
        tokens.append("RUB")
    if len(tokens) != 2:
        await message.answer(USAGE_RATE)
        return

    source = currency_code(tokens[0])
    target = currency_code(tokens[1])
    if not source or not target:
        await message.answer(
            "Не знаю такую валюту. Используйте название (доллар, евро, юань, btc) или код, например SEK."
        )
        return

    try:
        if source in CRYPTO_CODES or target in CRYPTO_CODES:
            crypto_data = await load_crypto()
            fiat_data = await load_rates()
            rates = fiat_data.get("rates") or {}
            rub = rates.get("RUB")
            if not rub:
                await message.answer("Нет данных по рублю.")
                return

            def get_price_in_rub(code):
                if code in CRYPTO_CODES:
                    return (crypto_data.get(CRYPTO_IDS[code]) or {}).get("rub")
                if code == "RUB":
                    return 1.0
                if code in rates:
                    return rub / rates[code]
                return None

            src_price = get_price_in_rub(source)
            tgt_price = get_price_in_rub(target)
            if src_price is None or tgt_price is None:
                await message.answer(f"Нет данных по валюте {source if src_price is None else target}.")
                return
            one = src_price / tgt_price
        else:
            data = await load_rates()
            rates = data.get("rates") or {}
            for code in (source, target):
                if code not in rates:
                    await message.answer(f"Нет данных по валюте {code}.")
                    return
            one = rates[target] / rates[source]
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        await message.answer("Не удалось получить курс, попробуйте позже.")
        return

    lines = [f"{format_rate(amount)} {source} = {format_rate(amount * one)} {target}"]
    if amount != 1:
        lines.append(f"1 {source} = {format_rate(one)} {target}")
    await message.answer("\n".join(lines))


async def id_command(message, rest):
    try:
        if rest.strip() or message.reply_to_message:
            user_id, display, _ = await resolve_target(message, rest)
            await message.answer(f"ID пользователя {display}: `{user_id}`", parse_mode="Markdown")
        else:
            user = message.from_user
            await message.answer(f"Ваш ID: `{user.id}`", parse_mode="Markdown")
    except ValueError as e:
        await message.answer(str(e))
    except TelegramAPIError as e:
        await message.answer(f"Не получилось: {e.message}")


@dp.message(F.text)
async def on_text(message: Message):
    if message.from_user is None:
        return

    remember_user(message)

    parsed = parse_command(message.text)
    if parsed is None:
        return
    command, rest = parsed

    if command == "start":
        await message.answer(START_TEXT)
    elif command == "help":
        await message.answer(HELP_TEXT)
    elif command in ("ban", "mute", "kick"):
        await moderate(message, rest, command)
    elif command == "kickme":
        await kickme(message)
    elif command == "solve":
        await message.answer(solve_text(rest))
    elif command == "ai":
        await ai_command(message, rest)
    elif command == "whois":
        await whois_command(message, rest)
    elif command == "search":
        await search_command(message, rest)
    elif command == "weather":
        await weather_command(message, rest)
    elif command == "rate":
        await rate_command(message, rest)
    elif command == "id":
        await id_command(message, rest)


@dp.guest_message(F.text)
async def on_guest(message: Message):
    text = message.text or message.caption or ""
    query = re.sub(rf"@{re.escape(BOT_USERNAME)}", "", text, flags=re.IGNORECASE).strip()
    solve = re.match(r"^[!./]?реши\b\s*(.*)$", query, flags=re.IGNORECASE | re.DOTALL)

    if not query:
        reply = GUEST_HINT
    elif solve:
        reply = solve_text(solve.group(1))
    else:
        reply = await ask_ai(query)

    try:
        await message.answer_guest_query(
            result=InlineQueryResultArticle(
                id=uuid4().hex,
                title="Ответ",
                input_message_content=InputTextMessageContent(message_text=reply[:4000]),
            ),
        )
    except TelegramAPIError:
        logging.exception("Не удалось ответить на guest-запрос")


async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    logging.info("Бот @%s, поддержка guest mode: %s", BOT_USERNAME, getattr(me, "supports_guest_queries", None))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "guest_message"])


if __name__ == "__main__":
    asyncio.run(main())
