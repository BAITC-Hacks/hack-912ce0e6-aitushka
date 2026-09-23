"""One-command reproducible analysis, optionally followed by a local dashboard."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Граф денег — локальный анализ транзакционной сети")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--ui", action="store_true", help="Запустить интерфейс после расчёта")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
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
        environment = os.environ.copy()
        environment.update(MONEY_GRAPH_DATA=str(args.data.resolve()), MONEY_GRAPH_OUT=str(args.out.resolve()), MONEY_GRAPH_CONFIG=str(args.config.resolve()))
        print(f"Интерфейс: http://localhost:{args.port}", flush=True)
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"), "--server.address", "127.0.0.1", "--server.port", str(args.port), "--server.headless", "true", "--browser.gatherUsageStats", "false"], env=environment, cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

