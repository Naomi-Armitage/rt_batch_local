from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import string
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_WXrF1LSkiTtfYqiL6XtjygvX"
DELAY = 2
MAX_RETRY = 3
RETRY_DELAY = 3
EXPORT_WRITE_RETRY = 2
REQUEST_TIMEOUT = 30
AUTO_CLEANUP_ON_ALL_SUCCESS = True
IMPORT_BACKUP_KEEP_BATCHES = 5
RESULTS_INCLUDE_INPUT_RT = True
RESULTS_INCLUDE_RAW_RESPONSE = True
MASK_CONSOLE_OUTPUT = True

INPUT_FILE = "rt_input.txt"
OUTPUT_FILE = "rt_output.json"
IMPORT_DIR_NAME = "rt_import"
IMPORT_MANUAL_FILE_NAME = "manual_input.txt"
IMPORT_BACKUP_DIR_NAME = "rt_import_backup"
CODEX_OUTPUT_DIR_NAME = "codex_output"
REFRESHED_RT_DIR_NAME = "refreshed_rts"
REFRESHED_RT_FILE_PREFIX = "refreshed_rts"
FAILED_RT_DIR_NAME = "failed_rts"
FAILED_RT_FILE_PREFIX = "failed_rts"
LOG_DIR_NAME = "logs"

EXPORT_TZ = timezone(timedelta(hours=8))
RT_START_PATTERN = re.compile(r"rt_")
SEPARATOR_RUN_PATTERN = re.compile(r"[^A-Za-z0-9\s]{3,}")
RT_ALLOWED_CHARS = set(string.ascii_letters + string.digits + "._-")
HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
    ),
}

SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_FILE_PATH = SCRIPT_DIR / INPUT_FILE
OUTPUT_FILE_PATH = SCRIPT_DIR / OUTPUT_FILE
IMPORT_DIR = SCRIPT_DIR / IMPORT_DIR_NAME
IMPORT_MANUAL_FILE_PATH = IMPORT_DIR / IMPORT_MANUAL_FILE_NAME
IMPORT_BACKUP_DIR = SCRIPT_DIR / IMPORT_BACKUP_DIR_NAME
CODEX_OUTPUT_DIR = SCRIPT_DIR / CODEX_OUTPUT_DIR_NAME
REFRESHED_RT_DIR = SCRIPT_DIR / REFRESHED_RT_DIR_NAME
FAILED_RT_DIR = SCRIPT_DIR / FAILED_RT_DIR_NAME
LOG_DIR = SCRIPT_DIR / LOG_DIR_NAME


class Console:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def section(self, title: str) -> None:
        line = "=" * 60
        print(f"\n{line}\n  {title}\n{line}")
        self.logger.info("[SECTION] %s", title)

    def info(self, message: str) -> None:
        print(f"[·] {message}")
        self.logger.info(message)

    def ok(self, message: str) -> None:
        print(f"[√] {message}")
        self.logger.info("[OK] %s", message)

    def warn(self, message: str) -> None:
        print(f"[!] {message}")
        self.logger.warning(message)

    def error(self, message: str) -> None:
        print(f"[X] {message}")
        self.logger.error(message)


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def setup_logger() -> tuple[logging.Logger, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = ensure_unique_path(LOG_DIR / f"rt_batch_local_{datetime.now(EXPORT_TZ).strftime('%Y%m%d_%H%M%S')}.log")
    logger = logging.getLogger(f"rt_batch_local_{log_path.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(file_handler)
    return logger, log_path


LOGGER, LOG_PATH = setup_logger()
CONSOLE = Console(LOGGER)


def detect_repeated_separators(line: str) -> set[str]:
    counts = Counter(match.group(0) for match in SEPARATOR_RUN_PATTERN.finditer(line))
    return {separator for separator, count in counts.items() if count >= 2}


def normalize_repeated_separators(line: str) -> str:
    normalized_line = line
    for separator in sorted(detect_repeated_separators(line), key=len, reverse=True):
        normalized_line = normalized_line.replace(separator, " ")
    return normalized_line


def extract_rts_from_line(line: str) -> list[str]:
    normalized_line = normalize_repeated_separators(line)
    separator_runs = {
        match.start(): match.group(0)
        for match in SEPARATOR_RUN_PATTERN.finditer(normalized_line)
    }
    results: list[str] = []

    for match in RT_START_PATTERN.finditer(normalized_line):
        start = match.start()
        end = start

        while end < len(normalized_line):
            separator = separator_runs.get(end)
            if separator and end > start:
                break

            if normalized_line[end] in RT_ALLOWED_CHARS:
                end += 1
                continue
            break

        token = normalized_line[start:end]
        if token != "rt_":
            results.append(token)

    return results


def extract_rts_from_text(text: str) -> list[str]:
    results: list[str] = []
    for line in text.splitlines():
        results.extend(extract_rts_from_line(line))
    return results


def now_str() -> str:
    return datetime.now(EXPORT_TZ).strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return datetime.now(EXPORT_TZ).replace(microsecond=0).isoformat()


def redact_secret(value: Any, visible_start: int = 8, visible_end: int = 6) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= visible_start + visible_end:
        return text
    return f"{text[:visible_start]}...{text[-visible_end:]}"


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", str(name or "")).strip()
    sanitized = re.sub(r"\s+", "_", sanitized).strip(" ._")
    return sanitized or "account"


def write_json_atomic(path: Path, data: Any, *, ensure_ascii: bool = False, indent: int | None = None, separators: tuple[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=ensure_ascii, indent=indent, separators=separators)
    temp_path.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{line}\n")


def resolve_timestamped_output_path(directory: Path, prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(EXPORT_TZ).strftime("%Y%m%d_%H%M%S")
    return ensure_unique_path(directory / f"{prefix}_{timestamp}.txt")


def safe_relative_to(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path(path.name)


def read_text_safe(path: Path, *, log_error: bool = True) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        if log_error:
            CONSOLE.warn(f"读取文件失败，已跳过：{path} ({exc})")
        return None


def decode_jwt_payload(token: Any) -> dict[str, Any]:
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_codex_claims(id_token: Any) -> dict[str, str]:
    claims = decode_jwt_payload(id_token)
    auth_claims = claims.get("https://api.openai.com/auth", {})
    if not isinstance(auth_claims, dict):
        auth_claims = {}
    return {
        "chatgpt_account_id": str(auth_claims.get("chatgpt_account_id", "") or ""),
        "chatgpt_plan_type": str(auth_claims.get("chatgpt_plan_type", "") or ""),
    }


def extract_email(access_claims: dict[str, Any], id_claims: dict[str, Any]) -> str:
    for claims in (id_claims, access_claims):
        for key in ("email", "preferred_username", "upn"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        profile_claims = claims.get("https://api.openai.com/profile", {})
        if isinstance(profile_claims, dict):
            email = profile_claims.get("email")
            if isinstance(email, str) and email.strip():
                return email.strip()
    return ""


def resolve_expired_at(access_claims: dict[str, Any], id_claims: dict[str, Any], payload: dict[str, Any]) -> str:
    for claims in (access_claims, id_claims):
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, tz=EXPORT_TZ).replace(microsecond=0).isoformat()
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        return (datetime.now(EXPORT_TZ) + timedelta(seconds=int(expires_in))).replace(microsecond=0).isoformat()
    return now_iso()


def preview_payload(payload: Any, limit: int = 260) -> str:
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(token_key in lowered for token_key in ("token", "refresh", "id_token", "access_token")):
                    result[key] = redact_secret(item)
                else:
                    result[key] = scrub(item)
            return result
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            if value.startswith("rt_"):
                return redact_secret(value)
            if len(value.split(".")) == 3:
                return redact_secret(value)
        return value
    try:
        text = json.dumps(scrub(payload), ensure_ascii=False)
    except Exception:
        text = str(payload)
    return text[:limit] + ("..." if len(text) > limit else "")


def ensure_runtime_paths() -> list[Path]:
    created_paths = []
    for path in (IMPORT_DIR, IMPORT_BACKUP_DIR, CODEX_OUTPUT_DIR, REFRESHED_RT_DIR, FAILED_RT_DIR, LOG_DIR):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created_paths.append(path)
    if not IMPORT_MANUAL_FILE_PATH.exists():
        IMPORT_MANUAL_FILE_PATH.write_text("", encoding="utf-8")
        created_paths.append(IMPORT_MANUAL_FILE_PATH)
    return created_paths


def prompt_for_runtime_setup(created_paths: list[Path], reason: str) -> bool:
    if not sys.stdin.isatty():
        CONSOLE.warn(f"{reason}，但当前环境不是交互终端，没法暂停等待输入。")
        CONSOLE.warn(f"请先把 RT 放进：{IMPORT_DIR} 或 {INPUT_FILE_PATH}，然后重新运行。")
        return False
    CONSOLE.section("开始前先准备一下")
    CONSOLE.info(f"原因：{reason}")
    if created_paths:
        CONSOLE.info("我已经先把这些路径准备好了：")
        for path in created_paths:
            print(f"    {path}")
    CONSOLE.info(f"你可以把任意包含 RT 的文本直接写进：{IMPORT_MANUAL_FILE_PATH}")
    CONSOLE.info(f"也可以把任意包含 rt_* 的文件扔进：{IMPORT_DIR}")
    CONSOLE.info("准备好后按回车继续；输入 q 再回车就退出。")
    try:
        return input("继续扫描? [Enter/q]: ").strip().lower() != "q"
    except EOFError:
        return False


def extract_rts(text: str) -> list[str]:
    return extract_rts_from_text(text)


def collect_rts_from_import_dir() -> tuple[list[str], list[Path], dict[str, list[str]]]:
    if not IMPORT_DIR.exists():
        return [], [], {}
    rt_list: list[str] = []
    used_files: list[Path] = []
    source_tags_by_rt: dict[str, list[str]] = {}
    for file_path in sorted(path for path in IMPORT_DIR.rglob("*") if path.is_file()):
        text = read_text_safe(file_path)
        if text is None:
            continue
        extracted = extract_rts(text)
        if not extracted:
            continue
        used_files.append(file_path)
        source_tag = f"import:{safe_relative_to(file_path, IMPORT_DIR).as_posix()}"
        for rt in extracted:
            if rt not in source_tags_by_rt:
                rt_list.append(rt)
                source_tags_by_rt[rt] = []
            if source_tag not in source_tags_by_rt[rt]:
                source_tags_by_rt[rt].append(source_tag)
    return rt_list, used_files, source_tags_by_rt


def collect_rts_from_legacy_input() -> tuple[list[str], dict[str, list[str]]]:
    if not INPUT_FILE_PATH.exists():
        return [], {}
    text = read_text_safe(INPUT_FILE_PATH)
    if text is None:
        return [], {}
    extracted = extract_rts(text)
    if not extracted:
        extracted = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    rt_list: list[str] = []
    source_tags_by_rt: dict[str, list[str]] = {}
    for rt in extracted:
        if rt not in source_tags_by_rt:
            rt_list.append(rt)
            source_tags_by_rt[rt] = [f"legacy:{INPUT_FILE_PATH.name}"]
    return rt_list, source_tags_by_rt


def load_rt_sources() -> dict[str, Any]:
    import_rts, used_import_files, import_sources = collect_rts_from_import_dir()
    legacy_rts, legacy_sources = collect_rts_from_legacy_input()
    merged_rts: list[str] = []
    merged_sources: dict[str, list[str]] = {}
    for rt in import_rts + legacy_rts:
        if rt not in merged_sources:
            merged_rts.append(rt)
            merged_sources[rt] = []
        for source_tag in import_sources.get(rt, []) + legacy_sources.get(rt, []):
            if source_tag not in merged_sources[rt]:
                merged_sources[rt].append(source_tag)
    return {
        "rt_list": merged_rts,
        "source_tags_by_rt": merged_sources,
        "used_import_files": used_import_files,
        "import_rt_count": len(import_rts),
        "legacy_rt_count": len(legacy_rts),
        "used_legacy_input": bool(legacy_rts),
    }


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def refresh_rt(session: requests.Session, rt: str) -> dict[str, Any]:
    attempts = [
        ("json", lambda: session.post(OAUTH_TOKEN_URL, json={"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": rt}, timeout=REQUEST_TIMEOUT)),
        ("form", lambda: session.post(OAUTH_TOKEN_URL, data={"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": rt}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=REQUEST_TIMEOUT)),
    ]
    last_error: Exception | None = None
    for label, sender in attempts:
        try:
            response = sender()
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"接口返回格式异常：{payload!r}")
            return payload
        except Exception as exc:
            last_error = exc
            CONSOLE.warn(f"官方刷新接口的 {label} 提交方式失败：{exc}")
    raise RuntimeError(f"刷新 RT 失败：{last_error}") from last_error


def build_export_json(response_payload: dict[str, Any], input_rt: str) -> tuple[dict[str, Any], str]:
    access_token = str(response_payload.get("access_token") or "").strip()
    id_token = str(response_payload.get("id_token") or "").strip()
    refresh_token = str(response_payload.get("refresh_token") or input_rt or "").strip()
    if not access_token or not id_token or not refresh_token:
        raise ValueError("接口返回缺少 access_token / id_token / refresh_token")
    access_claims = decode_jwt_payload(access_token)
    id_claims = decode_jwt_payload(id_token)
    export_json = {
        "type": "codex",
        "access_token": access_token,
        "disabled": True,
        "email": extract_email(access_claims, id_claims),
        "expired": resolve_expired_at(access_claims, id_claims, response_payload),
        "id_token": id_token,
        "last_refresh": now_iso(),
        "refresh_token": refresh_token,
    }
    return export_json, refresh_token


def save_export(export_json: dict[str, Any], index: int) -> Path:
    email = str(export_json.get("email", "") or "").strip()
    name = sanitize_filename(email) if email else f"account_{index}"
    output_path = ensure_unique_path(CODEX_OUTPUT_DIR / f"codex_{name}.json")
    write_json_atomic(output_path, export_json, ensure_ascii=False, separators=(",", ":"))
    return output_path


def persist_results(results: list[dict[str, Any]]) -> None:
    records = []
    for result in results:
        record = dict(result)
        if not RESULTS_INCLUDE_INPUT_RT:
            record["input_rt"] = ""
            record["refreshed_rt"] = ""
        if not RESULTS_INCLUDE_RAW_RESPONSE:
            record["refresh_response"] = None
        record["input_rt_preview"] = redact_secret(result.get("input_rt", ""), 14, 6)
        record["refreshed_rt_preview"] = redact_secret(result.get("refreshed_rt", ""), 14, 6)
        records.append(record)
    write_json_atomic(OUTPUT_FILE_PATH, records, ensure_ascii=False, indent=2)


def remove_empty_dirs(root_dir: Path) -> None:
    if not root_dir.exists():
        return
    for dir_path in sorted((path for path in root_dir.rglob("*") if path.is_dir()), reverse=True):
        try:
            dir_path.rmdir()
        except OSError:
            pass


def prune_import_backup_batches() -> list[Path]:
    if not IMPORT_BACKUP_DIR.exists():
        return []
    batch_dirs = sorted((path for path in IMPORT_BACKUP_DIR.iterdir() if path.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
    removed_dirs = []
    for dir_path in batch_dirs[IMPORT_BACKUP_KEEP_BATCHES:]:
        shutil.rmtree(dir_path, ignore_errors=True)
        removed_dirs.append(dir_path)
    return removed_dirs


def cleanup_input_sources(source_summary: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "batch_backup_dir": None,
        "archived_paths": [],
        "removed_backup_dirs": [],
        "manual_input_cleared": False,
        "legacy_input_cleared": False,
    }
    batch_dir: Path | None = None

    def ensure_batch_dir() -> Path:
        nonlocal batch_dir
        if batch_dir is None:
            batch_dir = ensure_unique_path(IMPORT_BACKUP_DIR / f"batch_{datetime.now(EXPORT_TZ).strftime('%Y%m%d_%H%M%S')}")
            batch_dir.mkdir(parents=True, exist_ok=True)
        return batch_dir

    manual_resolved = IMPORT_MANUAL_FILE_PATH.resolve()
    for file_path in source_summary["used_import_files"]:
        if not file_path.exists() or file_path.resolve() == manual_resolved:
            continue
        target = ensure_unique_path(ensure_batch_dir() / "import" / safe_relative_to(file_path, IMPORT_DIR))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), str(target))
        summary["archived_paths"].append(target)

    remove_empty_dirs(IMPORT_DIR)

    if any(path.resolve() == manual_resolved for path in source_summary["used_import_files"]) and IMPORT_MANUAL_FILE_PATH.exists():
        manual_text = read_text_safe(IMPORT_MANUAL_FILE_PATH, log_error=False) or ""
        if extract_rts(manual_text):
            target = ensure_unique_path(ensure_batch_dir() / "import" / IMPORT_MANUAL_FILE_PATH.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(IMPORT_MANUAL_FILE_PATH, target)
            summary["archived_paths"].append(target)
        write_text_atomic(IMPORT_MANUAL_FILE_PATH, "")
        summary["manual_input_cleared"] = True

    if source_summary["used_legacy_input"] and INPUT_FILE_PATH.exists():
        legacy_text = read_text_safe(INPUT_FILE_PATH, log_error=False) or ""
        if legacy_text.strip():
            target = ensure_unique_path(ensure_batch_dir() / "legacy" / INPUT_FILE_PATH.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(INPUT_FILE_PATH, target)
            summary["archived_paths"].append(target)
        write_text_atomic(INPUT_FILE_PATH, "")
        summary["legacy_input_cleared"] = True

    summary["batch_backup_dir"] = batch_dir
    summary["removed_backup_dirs"] = prune_import_backup_batches()
    return summary


def build_result(rt: str, index: int, source_tags: list[str]) -> dict[str, Any]:
    return {
        "index": index,
        "input_rt": rt,
        "source_tags": source_tags,
        "status": "pending",
        "attempt": 0,
        "refresh_attempts": 0,
        "export_attempts": 0,
        "attempt_history": [],
        "refresh_response": None,
        "refresh_response_preview": "",
        "export_file": None,
        "export_email": "",
        "refreshed_rt": "",
        "chatgpt_account_id": "",
        "chatgpt_plan_type": "",
        "codex_ready": False,
        "timestamp": now_str(),
        "finished_at": "",
        "duration_seconds": 0.0,
        "error": "",
    }


def process_single_rt(session: requests.Session, rt: str, index: int, total: int, source_tags: list[str]) -> dict[str, Any]:
    display_rt = redact_secret(rt, 14, 6) if MASK_CONSOLE_OUTPUT else rt
    CONSOLE.section(f"第 {index}/{total} 个 RT")
    CONSOLE.info(f"当前处理：{display_rt}")
    if source_tags:
        CONSOLE.info(f"来源：{', '.join(source_tags)}")

    result = build_result(rt, index, source_tags)
    started_all = time.monotonic()
    export_json: dict[str, Any] | None = None

    for attempt in range(1, MAX_RETRY + 1):
        result["attempt"] = attempt
        result["refresh_attempts"] = attempt
        attempt_record = {"phase": "refresh", "attempt": attempt, "started_at": now_str(), "finished_at": "", "duration_seconds": 0.0, "stage": "pending", "error": ""}
        if attempt > 1:
            wait_seconds = RETRY_DELAY * (attempt - 1)
            CONSOLE.info(f"前一次没成功，我准备再试一次，先等 {wait_seconds}s。")
            time.sleep(wait_seconds)
        CONSOLE.info(f"第 {attempt}/{MAX_RETRY} 次调用 OpenAI 官方刷新接口 ...")
        started_attempt = time.monotonic()
        try:
            refresh_response = refresh_rt(session, rt)
            export_json, refreshed_rt = build_export_json(refresh_response, rt)
            claims = extract_codex_claims(export_json.get("id_token", ""))
            result["refresh_response"] = refresh_response
            result["refresh_response_preview"] = preview_payload(refresh_response)
            result["refreshed_rt"] = refreshed_rt
            result["chatgpt_account_id"] = claims["chatgpt_account_id"]
            result["chatgpt_plan_type"] = claims["chatgpt_plan_type"]
            result["codex_ready"] = bool(result["chatgpt_account_id"] and result["chatgpt_plan_type"])
            attempt_record["stage"] = "refresh_ok"
            CONSOLE.ok(f"官方接口返回正常：{result['refresh_response_preview']}")
            break
        except Exception as exc:
            result["status"] = "refresh_error"
            result["error"] = str(exc)
            attempt_record["stage"] = "refresh_error"
            attempt_record["error"] = str(exc)
            CONSOLE.error(f"刷新失败：{exc}")
            if attempt == MAX_RETRY:
                result["finished_at"] = now_str()
                result["duration_seconds"] = round(time.monotonic() - started_all, 3)
        finally:
            attempt_record["finished_at"] = now_str()
            attempt_record["duration_seconds"] = round(time.monotonic() - started_attempt, 3)
            result["attempt_history"].append(attempt_record)
        if export_json is None and attempt == MAX_RETRY:
            return result

    if export_json is None:
        result["status"] = "refresh_error"
        result["error"] = result["error"] or "刷新结果为空"
        result["finished_at"] = now_str()
        result["duration_seconds"] = round(time.monotonic() - started_all, 3)
        return result

    for export_attempt in range(1, EXPORT_WRITE_RETRY + 1):
        result["export_attempts"] = export_attempt
        attempt_record = {"phase": "export", "attempt": export_attempt, "started_at": now_str(), "finished_at": "", "duration_seconds": 0.0, "stage": "pending", "error": ""}
        if export_attempt > 1:
            wait_seconds = max(1, RETRY_DELAY // 2) * (export_attempt - 1)
            CONSOLE.info(f"刷新结果已经拿到了，我这次只重试保存文件，先等 {wait_seconds}s。")
            time.sleep(wait_seconds)
        CONSOLE.info(f"第 {export_attempt}/{EXPORT_WRITE_RETRY} 次保存导出文件 ...")
        started_attempt = time.monotonic()
        try:
            export_path = save_export(export_json, index)
            result["export_file"] = str(export_path)
            result["export_email"] = str(export_json.get("email", "") or "")
            result["status"] = "success"
            result["error"] = ""
            attempt_record["stage"] = "success"
            CONSOLE.ok(f"导出完成：{export_path}")
            if not result["codex_ready"]:
                CONSOLE.warn("当前结果缺少 chatgpt_account_id 或 chatgpt_plan_type，导入后可能看不到额度或套餐信息。")
            result["finished_at"] = now_str()
            result["duration_seconds"] = round(time.monotonic() - started_all, 3)
            return result
        except Exception as exc:
            result["status"] = "export_error"
            result["error"] = str(exc)
            attempt_record["stage"] = "export_error"
            attempt_record["error"] = str(exc)
            CONSOLE.error(f"保存导出文件失败：{exc}")
            if export_attempt == EXPORT_WRITE_RETRY:
                result["finished_at"] = now_str()
                result["duration_seconds"] = round(time.monotonic() - started_all, 3)
        finally:
            attempt_record["finished_at"] = now_str()
            attempt_record["duration_seconds"] = round(time.monotonic() - started_attempt, 3)
            result["attempt_history"].append(attempt_record)

    return result


def print_startup_summary(source_summary: dict[str, Any]) -> None:
    CONSOLE.section("RT 本地批量刷新工具")
    CONSOLE.info("当前模式：本地 OpenAI 刷新 / Codex 导入文件")
    CONSOLE.info(f"官方接口：{OAUTH_TOKEN_URL}")
    CONSOLE.info(f"输出目录：{CODEX_OUTPUT_DIR}")
    CONSOLE.info(f"结果文件：{OUTPUT_FILE_PATH}")
    CONSOLE.info(f"批量导入目录：{IMPORT_DIR}")
    CONSOLE.info(f"导入备份目录：{IMPORT_BACKUP_DIR}")
    CONSOLE.info(f"刷新 RT 输出目录：{REFRESHED_RT_DIR}")
    CONSOLE.info(f"失败 RT 输出目录：{FAILED_RT_DIR}")
    CONSOLE.info(f"日志文件：{LOG_PATH}")
    CONSOLE.info(f"重试策略：官方刷新最多 {MAX_RETRY} 次，请求间隔 {DELAY}s，重试基准等待 {RETRY_DELAY}s，单次超时 {REQUEST_TIMEOUT}s；导出落盘最多 {EXPORT_WRITE_RETRY} 次")
    CONSOLE.info("这个版本不再依赖 fk.accgood.com，也不需要 cf_clearance / ACG-SHOP。")
    if source_summary["import_rt_count"] or source_summary["legacy_rt_count"]:
        CONSOLE.info(f"已发现 RT：导入目录 {source_summary['import_rt_count']} 个，旧输入文件 {source_summary['legacy_rt_count']} 个")


def main() -> int:
    created_paths = ensure_runtime_paths()
    source_summary = load_rt_sources()
    print_startup_summary(source_summary)

    if created_paths and not source_summary["rt_list"]:
        if not prompt_for_runtime_setup(created_paths, "检测到运行目录刚创建好，顺手等你把 RT 放进来"):
            return 2
        source_summary = load_rt_sources()

    while not source_summary["rt_list"]:
        if not prompt_for_runtime_setup([], "当前还没有提取到任何 RT"):
            return 2
        source_summary = load_rt_sources()

    if source_summary["used_legacy_input"] and source_summary["import_rt_count"] == 0:
        CONSOLE.ok(f"已从旧输入文件读取到 {source_summary['legacy_rt_count']} 个 RT")
    elif source_summary["used_legacy_input"]:
        CONSOLE.ok(f"已合并读取到 {len(source_summary['rt_list'])} 个 RT（导入目录 {source_summary['import_rt_count']} 个，旧输入文件 {source_summary['legacy_rt_count']} 个）")
    else:
        CONSOLE.ok(f"已从批量导入目录读取到 {source_summary['import_rt_count']} 个 RT，实际命中的来源文件 {len(source_summary['used_import_files'])} 个")

    refreshed_rt_output_path = resolve_timestamped_output_path(REFRESHED_RT_DIR, REFRESHED_RT_FILE_PREFIX)
    write_text_atomic(refreshed_rt_output_path, "")

    failed_rt_output_path: Path | None = None
    results: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0
    refreshed_rt_count = 0

    session = build_session()
    try:
        for index, rt in enumerate(source_summary["rt_list"], 1):
            result = process_single_rt(session, rt, index, len(source_summary["rt_list"]), source_summary["source_tags_by_rt"].get(rt, []))
            results.append(result)
            if result.get("refreshed_rt"):
                append_line(refreshed_rt_output_path, result["refreshed_rt"])
                refreshed_rt_count += 1
            if result["status"] == "success":
                success_count += 1
            else:
                fail_count += 1
                if failed_rt_output_path is None:
                    failed_rt_output_path = resolve_timestamped_output_path(FAILED_RT_DIR, FAILED_RT_FILE_PREFIX)
                append_line(failed_rt_output_path, rt)
            persist_results(results)
            if index < len(source_summary["rt_list"]):
                CONSOLE.info(f"这条处理完了，我先缓 {DELAY}s，避免请求太密。")
                time.sleep(DELAY)
    finally:
        session.close()

    cleanup_summary = {"batch_backup_dir": None, "archived_paths": [], "removed_backup_dirs": [], "manual_input_cleared": False, "legacy_input_cleared": False}
    if not AUTO_CLEANUP_ON_ALL_SUCCESS:
        CONSOLE.warn("你已经关闭自动清理；原始导入源我会保留不动。")
    elif fail_count == 0:
        cleanup_summary = cleanup_input_sources(source_summary)
    else:
        CONSOLE.warn("这次还有失败项。为了保险起见，我没有自动清理原始导入源。")

    CONSOLE.section("这一轮处理完成")
    CONSOLE.info(f"总计：{len(source_summary['rt_list'])} | 成功：{success_count} | 失败：{fail_count}")
    CONSOLE.info(f"结果明细：{OUTPUT_FILE_PATH}")
    CONSOLE.info(f"刷新后 RT：{refreshed_rt_output_path}（共 {refreshed_rt_count} 条）")
    CONSOLE.info(f"导出文件目录：{CODEX_OUTPUT_DIR}")
    CONSOLE.info(f"运行日志：{LOG_PATH}")
    if failed_rt_output_path is not None:
        CONSOLE.warn(f"失败 RT 已单独整理到：{failed_rt_output_path}")
    if fail_count == 0 and AUTO_CLEANUP_ON_ALL_SUCCESS:
        if cleanup_summary["batch_backup_dir"]:
            CONSOLE.info(f"本次导入源已备份到：{cleanup_summary['batch_backup_dir']}")
        CONSOLE.info(f"已备份 {len(cleanup_summary['archived_paths'])} 份导入源")
        if cleanup_summary["manual_input_cleared"]:
            CONSOLE.info(f"手工导入文件已清空：{IMPORT_MANUAL_FILE_PATH}")
        if cleanup_summary["legacy_input_cleared"]:
            CONSOLE.info(f"旧输入文件已清空：{INPUT_FILE_PATH}")
        if cleanup_summary["removed_backup_dirs"]:
            CONSOLE.info(f"已顺手清理旧备份批次 {len(cleanup_summary['removed_backup_dirs'])} 个")
    if fail_count > 0:
        CONSOLE.section("需要你回头看一下的失败项")
        for result in results:
            if result["status"] != "success":
                display_rt = redact_secret(result["input_rt"], 18, 6) if MASK_CONSOLE_OUTPUT else result["input_rt"]
                print(f"    [{result['index']}] {display_rt} -> {result['status']}: {result.get('error', '')}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[!] 你手动中断了这次运行；已落盘的结果文件和导出文件都会保留。")
        LOGGER.warning("运行被用户手动中断")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n[X] 脚本异常退出：{exc}")
        LOGGER.exception("脚本异常退出")
        raise SystemExit(1)
