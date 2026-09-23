"""One-command reproducible analysis, optionally followed by a local dashboard."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from time import monotonic, perf_counter, sleep
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def node_runtime():
    """Resolve an existing runtime without shell interpolation on Windows."""
    node = shutil.which(os.environ.get("NODE_BINARY") or "node")
    if not node:
        raise RuntimeError("Не найден Node.js. Установите Node.js и npm или задайте NODE_BINARY.")
    search_path = str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")
    npm = shutil.which(os.environ.get("NPM_BINARY") or ("npm.cmd" if os.name == "nt" else "npm"), path=search_path)
    if not npm:
        raise RuntimeError("Не найден npm. Проверьте установку Node.js/npm или задайте NPM_BINARY.")
    if os.name == "nt":
        # Executing a .cmd can invoke cmd.exe implicitly. Use npm's JavaScript
        # entry point directly so paths and arguments remain literal values.
        candidates = [
            Path(npm) if Path(npm).suffix.lower() == ".js" else Path(npm).parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
            Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        ]
        npm_cli = next((path for path in candidates if path.is_file()), None)
        if npm_cli is None:
            raise RuntimeError("Не найден npm-cli.js рядом с Node.js/npm. Задайте NPM_BINARY полным путём к npm-cli.js.")
        return [node, str(npm_cli)], search_path
    return [npm], search_path


def ensure_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Порт {port} занят. Выберите другой --port или --api-port.") from exc


def wait_for_backend(process, url, timeout=90):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Python API завершился до готовности (код {process.returncode}).")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        sleep(0.25)
    raise RuntimeError(f"Python API не стал готов за {timeout} с. Проверьте сообщения выше.")


def stop_process_tree(process):
    """Stop only a process started by this launcher and its child processes."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)


def launch_ui(args):
    frontend = ROOT / "frontend"
    if not (frontend / "package.json").is_file():
        raise RuntimeError("Не найден frontend/package.json. Нужна полная копия проекта с Next.js-интерфейсом.")
    if not (frontend / "node_modules" / "next" / "package.json").is_file():
        raise RuntimeError("Не установлены зависимости интерфейса. Выполните: npm install --prefix frontend")
    npm_command, search_path = node_runtime()
    ensure_port_available(args.api_port)
    ensure_port_available(args.port)
    environment = os.environ.copy()
    environment.update(
        MONEY_GRAPH_DATA=str(args.data.resolve()),
        MONEY_GRAPH_OUT=str(args.out.resolve()),
        MONEY_GRAPH_CONFIG=str(args.config.resolve()),
        API_BASE_URL=f"http://127.0.0.1:{args.api_port}",
        NEXT_TELEMETRY_DISABLED="1",
        PYTHONUNBUFFERED="1",
        PATH=search_path,
    )
    process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    processes = []
    try:
        backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "money_graph.api:app", "--app-dir", str(ROOT / "src"),
             "--host", "127.0.0.1", "--port", str(args.api_port)],
            cwd=ROOT, env=environment, **process_options,
        )
        processes.append(backend)
        print(f"Ожидаем Python API: {environment['API_BASE_URL']}", flush=True)
        wait_for_backend(backend, environment["API_BASE_URL"] + "/api/health")
        web = subprocess.Popen(
            npm_command + ["run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(args.port)],
            cwd=frontend, env=environment, **process_options,
        )
        processes.append(web)
        print(f"Интерфейс Next.js: http://localhost:{args.port}", flush=True)
        print("Ctrl+C останавливает интерфейс и Python API.", flush=True)
        while True:
            for name, process in (("Python API", backend), ("Next.js", web)):
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"{name} завершился (код {code}); связанные процессы останавливаются.")
            sleep(0.35)
    except KeyboardInterrupt:
        print("\nОстанавливаем интерфейс и Python API…", flush=True)
        return 130
    finally:
        for process in reversed(processes):
            try:
                stop_process_tree(process)
            except (OSError, subprocess.SubprocessError) as exc:
                # A failure stopping one child must not leave the other server
                # unhandled; report the exact owned PID for manual recovery.
                print(f"Не удалось полностью остановить процесс {process.pid}: {exc}", file=sys.stderr)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Граф денег — локальный анализ транзакционной сети")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--ui", action="store_true", help="Запустить Next.js и Python API после расчёта")
    parser.add_argument("--port", type=int, default=3000, help="Порт интерфейса Next.js (по умолчанию 3000)")
    parser.add_argument("--api-port", type=int, default=8000, help="Порт Python API (по умолчанию 8000)")
    args = parser.parse_args()
    if args.ui and (not 1 <= args.port <= 65535 or not 1 <= args.api_port <= 65535 or args.port == args.api_port):
        parser.error("--port и --api-port должны быть разными числами от 1 до 65535")
    started = perf_counter()
    try:
        from money_graph.config import load_config
        from money_graph.pipeline import run_analysis
        from money_graph.export import export_result
        config = load_config(args.config)
        result = run_analysis(args.data, config)
        export_result(result, args.out)
        result.metadata["timings"]["cold_end_to_end_seconds"] = perf_counter() - started
        export_result(result, args.out)
    except (ValueError, OSError, ImportError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    summary = result.metadata["data_summary"]
    print(f"Готово: {len(result.nodes)} узлов, {summary['n_edges']} рёбер, {summary['n_transactions']} операций.")
    print(f"Сообществ: {len(result.clusters)}. Приоритетных клиентов: {len(result.top_nodes)}.")
    print(f"Полный запуск: {result.metadata['timings']['cold_end_to_end_seconds']:.2f} с.")
    print(f"Выгрузки: {args.out.resolve()}")
    print("Роли: " + ", ".join(f"{role}={count}" for role, count in result.metadata["role_counts"].items()))
    if args.ui:
        try:
            return launch_ui(args)
        except (RuntimeError, OSError) as exc:
            print(f"Ошибка запуска интерфейса: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
