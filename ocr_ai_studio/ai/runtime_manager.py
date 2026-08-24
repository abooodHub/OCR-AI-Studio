from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from ocr_ai_studio.domain.models import EngineKind


class RuntimeState(str, Enum):
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    ONLINE = "online"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    engine: EngineKind
    state: RuntimeState
    executable: str = ""
    detail: str = ""
    reachable: bool = False
    owned: bool = False


class EngineRuntimeError(RuntimeError):
    """The selected local inference runtime could not be managed safely."""


class EngineRuntimeManager:
    """Discover, probe, and safely manage local inference runtimes.

    A runtime is considered ready only after its provider-specific HTTP endpoint
    responds. Processes started outside this application are never terminated.
    """

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self._owned_processes: dict[EngineKind, subprocess.Popen[bytes]] = {}
        self._started_by_app: set[EngineKind] = set()

    def discover_executable(self, engine: EngineKind, configured: str = "") -> Path | None:
        if engine is EngineKind.CUSTOM:
            return None
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_file():
                return candidate.resolve()

        command_names = {
            EngineKind.LM_STUDIO: ("lms.exe", "lms"),
            EngineKind.OLLAMA: ("ollama.exe", "ollama"),
            EngineKind.UNSLOTH: ("unsloth.exe", "unsloth"),
        }[engine]
        for command in command_names:
            resolved = shutil.which(command)
            if resolved:
                return Path(resolved).resolve()

        local_app_data = Path(os.getenv("LOCALAPPDATA", ""))
        known_paths = {
            EngineKind.LM_STUDIO: (
                Path.home() / ".lmstudio" / "bin" / "lms.exe",
                local_app_data / "LM Studio" / "bin" / "lms.exe",
            ),
            EngineKind.OLLAMA: (
                local_app_data / "Programs" / "Ollama" / "ollama.exe",
                local_app_data / "Ollama" / "ollama.exe",
            ),
            EngineKind.UNSLOTH: (
                Path.home() / ".unsloth" / "studio" / "bin" / "unsloth.exe",
            ),
        }[engine]
        return next((path.resolve() for path in known_paths if path.is_file()), None)

    def inspect(
        self,
        engine: EngineKind,
        base_url: str,
        *,
        api_key: str = "",
        configured_executable: str = "",
        probe: bool = True,
    ) -> RuntimeSnapshot:
        executable = self.discover_executable(engine, configured_executable)
        executable_text = str(executable) if executable else ""
        owned = engine in self._started_by_app
        if engine is not EngineKind.CUSTOM and executable is None:
            return RuntimeSnapshot(
                engine,
                RuntimeState.NOT_INSTALLED,
                detail="لم يتم العثور على ملف تشغيل المحرك",
                owned=owned,
            )
        if not probe:
            detail = (
                "خادم مخصص — اضغط فحص الحالة للتحقق من الاتصال"
                if engine is EngineKind.CUSTOM
                else "المحرك مثبت ولم يتم فحص الخادم بعد"
            )
            return RuntimeSnapshot(
                engine,
                RuntimeState.STOPPED,
                executable_text,
                detail,
                owned=owned,
            )
        return self._probe(engine, base_url, api_key, executable_text, owned)

    def ensure_ready(
        self,
        engine: EngineKind,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        configured_executable: str = "",
        auto_start: bool = True,
        timeout_seconds: float = 45,
    ) -> RuntimeSnapshot:
        snapshot = self.inspect(
            engine,
            base_url,
            api_key=api_key,
            configured_executable=configured_executable,
        )
        if snapshot.state is RuntimeState.ONLINE or snapshot.reachable:
            return snapshot
        if not auto_start or engine is EngineKind.CUSTOM:
            return snapshot
        if snapshot.state is RuntimeState.NOT_INSTALLED:
            return snapshot

        starting = self.start(
            engine,
            base_url,
            model,
            configured_executable=configured_executable,
        )
        if starting.state is RuntimeState.ERROR:
            return starting

        deadline = time.monotonic() + max(1.0, timeout_seconds)
        delay = 0.25
        last = starting
        while time.monotonic() < deadline:
            time.sleep(delay)
            last = self.inspect(
                engine,
                base_url,
                api_key=api_key,
                configured_executable=configured_executable,
            )
            if last.state is RuntimeState.ONLINE or last.reachable:
                return last
            process = self._owned_processes.get(engine)
            if process is not None and process.poll() not in {None, 0}:
                return RuntimeSnapshot(
                    engine,
                    RuntimeState.ERROR,
                    last.executable,
                    f"توقف أمر تشغيل المحرك بالرمز {process.returncode}",
                    owned=True,
                )
            delay = min(delay * 1.5, 1.5)
        return RuntimeSnapshot(
            engine,
            RuntimeState.ERROR,
            last.executable,
            f"لم يصبح الخادم جاهزًا خلال {round(timeout_seconds)} ثانية",
            owned=True,
        )

    def start(
        self,
        engine: EngineKind,
        base_url: str,
        model: str,
        *,
        configured_executable: str = "",
    ) -> RuntimeSnapshot:
        if engine is EngineKind.CUSTOM:
            raise EngineRuntimeError("الخوادم المخصصة تدعم الاتصال فقط ولا يمكن تشغيلها تلقائيًا")
        host, port = self._local_address(base_url)
        executable = self.discover_executable(engine, configured_executable)
        if executable is None:
            return RuntimeSnapshot(
                engine,
                RuntimeState.NOT_INSTALLED,
                detail="المحرك غير مثبت أو أن مسار تشغيله غير معروف",
            )

        existing = self._owned_processes.get(engine)
        if existing is not None and existing.poll() is None:
            return RuntimeSnapshot(
                engine,
                RuntimeState.STARTING,
                str(executable),
                "أمر التشغيل يعمل بالفعل",
                owned=True,
            )

        command, environment = self._start_command(engine, executable, host, port, model)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(Path.home()),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as exc:
            return RuntimeSnapshot(
                engine,
                RuntimeState.ERROR,
                str(executable),
                f"تعذر تشغيل المحرك: {exc}",
            )
        self._owned_processes[engine] = process
        self._started_by_app.add(engine)
        return RuntimeSnapshot(
            engine,
            RuntimeState.STARTING,
            str(executable),
            "تم إرسال أمر التشغيل وجارٍ انتظار الخادم",
            owned=True,
        )

    def stop_owned(self, engine: EngineKind, configured_executable: str = "") -> bool:
        if engine not in self._started_by_app:
            return False
        process = self._owned_processes.get(engine)
        if engine is EngineKind.LM_STUDIO:
            executable = self.discover_executable(engine, configured_executable)
            if executable is not None:
                try:
                    subprocess.run(
                        [str(executable), "server", "stop"],
                        cwd=str(Path.home()),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    return False
        elif process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self._owned_processes.pop(engine, None)
        self._started_by_app.discard(engine)
        return True

    def shutdown_owned(self, configured_paths: dict[str, str] | None = None) -> None:
        paths = configured_paths or {}
        for engine in tuple(self._started_by_app):
            self.stop_owned(engine, paths.get(engine.value, ""))

    def _probe(
        self,
        engine: EngineKind,
        base_url: str,
        api_key: str,
        executable: str,
        owned: bool,
    ) -> RuntimeSnapshot:
        try:
            origin = self._origin(base_url)
        except EngineRuntimeError as exc:
            return RuntimeSnapshot(engine, RuntimeState.ERROR, executable, str(exc), owned=owned)
        endpoints = {
            EngineKind.LM_STUDIO: (f"{origin}/api/v0/models", f"{origin}/v1/models"),
            EngineKind.OLLAMA: (f"{origin}/api/tags",),
            EngineKind.UNSLOTH: (f"{origin}/api/health", f"{origin}/v1/models"),
            EngineKind.CUSTOM: (f"{base_url.rstrip('/')}/models",),
        }[engine]
        headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}
        last_error = "الخادم لا يستجيب"
        try:
            with httpx.Client(
                timeout=2.5,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                for endpoint in endpoints:
                    try:
                        response = client.get(endpoint, headers=headers)
                    except httpx.HTTPError as exc:
                        last_error = str(exc)
                        continue
                    if 200 <= response.status_code < 300:
                        return RuntimeSnapshot(
                            engine,
                            RuntimeState.ONLINE,
                            executable,
                            "الخادم متصل ويستجيب للطلبات",
                            reachable=True,
                            owned=owned,
                        )
                    if response.status_code in {401, 403}:
                        return RuntimeSnapshot(
                            engine,
                            RuntimeState.ERROR,
                            executable,
                            "الخادم يعمل لكنه يحتاج مفتاح API صحيحًا",
                            reachable=True,
                            owned=owned,
                        )
                    last_error = f"استجابة HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        return RuntimeSnapshot(
            engine,
            RuntimeState.STOPPED,
            executable,
            f"الخادم متوقف أو غير متاح: {last_error}",
            owned=owned,
        )

    @staticmethod
    def _start_command(
        engine: EngineKind,
        executable: Path,
        host: str,
        port: int,
        _model: str,
    ) -> tuple[list[str], dict[str, str]]:
        environment = os.environ.copy()
        if engine is EngineKind.LM_STUDIO:
            return (
                [str(executable), "server", "start", "--port", str(port), "--bind", host],
                environment,
            )
        if engine is EngineKind.OLLAMA:
            environment["OLLAMA_HOST"] = f"{host}:{port}"
            return [str(executable), "serve"], environment
        return (
            [
                str(executable),
                "studio",
                "--host",
                host,
                "--port",
                str(port),
            ],
            environment,
        )

    @staticmethod
    def _local_address(base_url: str) -> tuple[str, int]:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        if not EngineRuntimeManager._is_loopback(host):
            raise EngineRuntimeError("التشغيل التلقائي مسموح لخادم هذا الجهاز فقط")
        if parsed.scheme not in {"http", "https"}:
            raise EngineRuntimeError("عنوان الخادم يجب أن يبدأ بـ http:// أو https://")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return "127.0.0.1", port

    @staticmethod
    def _is_loopback(host: str) -> bool:
        if host.casefold() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _origin(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EngineRuntimeError("عنوان الخادم غير صالح")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
