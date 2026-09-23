"""Read-only local API for the Next.js analyst workspace.

Run with ``uvicorn money_graph.api:app --app-dir src --host 127.0.0.1``.
MONEY_GRAPH_DATA and MONEY_GRAPH_CONFIG select inputs. MONEY_GRAPH_OUT remains
the CLI export destination; HTTP downloads are generated in memory, never from
potentially stale files in that directory.
"""

from collections.abc import Mapping
from collections import Counter, defaultdict
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path as APIPath, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
import numpy as np
import pandas as pd

from . import __version__
from .config import ROOT, ROLES, load_config
from .export import CLUSTER_COLUMNS, NODE_COLUMNS, TOP_COLUMNS
from .pipeline import AnalysisResult, input_fingerprint, run_analysis
from .visualization import ROLE_COLORS, ROLE_LABELS, graph_node_ids, render_graph_html
from .analyst_report import build_report, client_events, render_report_html
from .ai_service import AIError, AIRequest, AIService


IDENTIFIER_FIELDS = {"gid", "src", "dst"}
EXPORT_NAMES = {"nodes_roles.csv", "clusters.csv", "top_nodes.csv", "run_metadata.json"}
SeedFilter = Literal["all", "seed", "nonseed"]
BoundaryFilter = Literal["all", "boundary", "internal"]


def json_safe(value, key: str | None = None):
    """Convert pandas values without ever passing identifiers through float."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if key in IDENTIFIER_FIELDS:
        return str(value)
    if isinstance(value, Mapping):
        return {str(name): json_safe(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def records(frame: pd.DataFrame) -> list[dict]:
    # to_dict operates column-wise. iterrows could coerce int64 gid to float.
    return json_safe(frame.to_dict(orient="records"))


class AnalysisCache:
    """One immutable-by-convention result per process, refreshed by content."""

    def __init__(self):
        self._lock = Lock()
        self._key: tuple | None = None
        self._result: AnalysisResult | None = None

    def get(self) -> AnalysisResult:
        # Fingerprinting and computation share a lock: concurrent first requests
        # perform a single run, and no request observes a half-built result.
        with self._lock:
            data_dir = Path(os.environ.get("MONEY_GRAPH_DATA", str(ROOT / "data"))).resolve()
            config_path = Path(os.environ.get("MONEY_GRAPH_CONFIG", str(ROOT / "config.json"))).resolve()
            try:
                config = load_config(config_path)
                fingerprint = input_fingerprint(data_dir, config)
                key = (str(data_dir), str(config_path), fingerprint)
                if self._key != key or self._result is None:
                    result = run_analysis(data_dir, config)
                    self._result = result
                    self._key = key
                return self._result
            except Exception as exc:
                # Do not serve a previous dataset if new inputs became invalid.
                raise HTTPException(
                    status_code=503,
                    detail=(f"Не удалось выполнить анализ: {exc}. Проверьте MONEY_GRAPH_DATA "
                            "(nodes.parquet, edges.parquet, transactions.parquet) и MONEY_GRAPH_CONFIG."),
                ) from exc


def filter_query(
    roles: Annotated[str, Query(max_length=1000)] = "",
    clusters: Annotated[str, Query(max_length=10000)] = "",
    seed: SeedFilter = "all",
    boundary: BoundaryFilter = "all",
    has_patterns: bool = False,
) -> dict:
    chosen_roles = [item.strip() for item in roles.split(",") if item.strip()]
    unknown = sorted(set(chosen_roles) - set(ROLES))
    if unknown:
        raise HTTPException(422, detail=f"Неизвестные роли: {', '.join(unknown)}")
    try:
        chosen_clusters = [int(item.strip()) for item in clusters.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(422, detail="clusters: нужны целые номера сообществ через запятую") from exc
    if any(cluster < 0 for cluster in chosen_clusters):
        raise HTTPException(422, detail="Номер сообщества не может быть отрицательным")
    return {"roles": chosen_roles, "clusters": chosen_clusters, "seed": seed, "boundary": boundary, "has_patterns": has_patterns}


def filtered_nodes(result: AnalysisResult, filters: dict) -> pd.DataFrame:
    frame = result.nodes
    if filters["roles"]:
        frame = frame.loc[frame.role.isin(filters["roles"])]
    if filters["clusters"]:
        frame = frame.loc[frame.cluster_id.isin(filters["clusters"])]
    if filters["seed"] != "all":
        frame = frame.loc[frame.is_seed.eq(filters["seed"] == "seed")]
    if filters["boundary"] != "all":
        frame = frame.loc[frame.truncated_by_depth.eq(filters["boundary"] == "boundary")]
    if filters.get("has_patterns"):
        frame = frame.loc[frame.pattern_count.gt(0)]
    return frame.sort_values(["priority_score", "gid"], ascending=[False, True], kind="stable")


def selected_client(result: AnalysisResult, gid: str) -> dict:
    found = result.nodes.loc[result.nodes.gid.map(str).eq(gid)]
    if found.empty:
        raise HTTPException(404, detail=f"Клиент {gid} отсутствует в предоставленном наборе")
    return records(found)[0]


def check_fingerprint(result: AnalysisResult, fingerprint: str | None):
    if fingerprint is not None and fingerprint != result.metadata["input_fingerprint"]:
        raise HTTPException(409, detail="Данные изменились. Обновите события клиента и повторите действие.")


def selected_pattern(result: AnalysisResult, pattern_id: str) -> dict:
    for pattern in result.patterns:
        if pattern["pattern_id"] == pattern_id:
            return pattern
    raise HTTPException(404, detail="Событие не найдено. Обновите список событий клиента.")


def create_app() -> FastAPI:
    application = FastAPI(title="Граф денег · API", version=__version__)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
        expose_headers=["Content-Disposition"],
    )
    cache = AnalysisCache()
    application.state.analysis_cache = cache
    ai_service = AIService()
    application.state.ai_service = ai_service

    @application.get("/api/ai/status")
    def ai_status():
        return ai_service.status()

    @application.post("/api/clients/{gid}/ai")
    def explain_client(gid: str, body: AIRequest, request: Request):
        from urllib.parse import urlsplit
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(403, detail="AI доступен из локального приложения.")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, detail="Требуется application/json.")
        result = cache.get()
        selected_client(result, gid)
        try:
            answer = ai_service.explain(result, gid, body)
        except AIError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from None
        # Do not display an answer for a snapshot replaced during generation.
        check_fingerprint(cache.get(), body.fingerprint)
        return Response(json.dumps(json_safe(answer), ensure_ascii=False, allow_nan=False),
                        media_type="application/json", headers={"Cache-Control": "no-store"})

    def analysis() -> AnalysisResult:
        return cache.get()

    @application.get("/api/health")
    def health(result: Annotated[AnalysisResult, Depends(analysis)]):
        return {"status": "ok", "version": __version__, "fingerprint": result.metadata["input_fingerprint"]}

    @application.get("/api/overview")
    def overview(result: Annotated[AnalysisResult, Depends(analysis)]):
        nodes, tx = result.nodes, result.transactions
        dates = pd.to_datetime(tx.date)
        summary = {
            "n_nodes": len(nodes), "n_edges": len(result.edges), "n_transactions": len(tx),
            "n_seed": int(nodes.is_seed.sum()), "n_clusters": len(result.clusters),
            "boundary_count": int(nodes.truncated_by_depth.sum()),
            "sum_kzt": float(tx.sum_kzt.sum()),
            "date_min": dates.min().date().isoformat() if len(dates) else None,
            "date_max": dates.max().date().isoformat() if len(dates) else None,
        }
        counts = nodes.role.value_counts().to_dict()
        return json_safe({
            "summary": summary,
            "roles": [{"role": role, "label": ROLE_LABELS[role], "color": ROLE_COLORS[role],
                       "count": int(counts.get(role, 0))} for role in ROLE_LABELS],
            "clusters": records(result.clusters), "metadata": result.metadata,
        })

    @application.get("/api/clients")
    def clients(
        result: Annotated[AnalysisResult, Depends(analysis)],
        filters: Annotated[dict, Depends(filter_query)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        frame = filtered_nodes(result, filters)
        return {"items": records(frame.iloc[offset:offset + limit]), "total": len(frame),
                "role_counts": {str(role): int(count) for role, count in frame.role.value_counts().items()}}

    @application.get("/api/clients/{gid}")
    def client_detail(
        gid: Annotated[str, APIPath(pattern=r"^-?\d+$", max_length=20)],
        result: Annotated[AnalysisResult, Depends(analysis)],
    ):
        client = selected_client(result, gid)
        incoming = result.edges.loc[result.edges.dst.map(str).eq(gid)].sort_values(["sum_kzt", "src"], ascending=[False, True])
        outgoing = result.edges.loc[result.edges.src.map(str).eq(gid)].sort_values(["sum_kzt", "dst"], ascending=[False, True])
        tx = result.transactions
        incoming_tx = tx.loc[tx.dst.map(str).eq(gid)]
        outgoing_tx = tx.loc[tx.src.map(str).eq(gid)]
        edge_dates = (tx.groupby(["src", "dst"], as_index=False)
                      .agg(first_date=("date", "min"), last_date=("date", "max")))
        edge_dates["first_date"] = pd.to_datetime(edge_dates["first_date"]).dt.strftime("%Y-%m-%d")
        edge_dates["last_date"] = pd.to_datetime(edge_dates["last_date"]).dt.strftime("%Y-%m-%d")
        incoming = incoming.merge(edge_dates, on=["src", "dst"], how="left", validate="one_to_one")
        outgoing = outgoing.merge(edge_dates, on=["src", "dst"], how="left", validate="one_to_one")
        daily = pd.concat([
            incoming_tx.groupby("date").sum_kzt.sum().rename("in_kzt"),
            outgoing_tx.groupby("date").sum_kzt.sum().rename("out_kzt"),
        ], axis=1).fillna(0.0).sort_index()
        if len(tx):
            all_dates = pd.date_range(pd.to_datetime(tx.date).min(), pd.to_datetime(tx.date).max(), freq="D")
            daily = daily.reindex(all_dates, fill_value=0.0)
        daily.index.name = "date"
        daily = daily.reset_index()
        daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
        client_tx = tx.loc[tx.src.map(str).eq(gid) | tx.dst.map(str).eq(gid)].sort_values(["date", "src", "dst"], kind="stable")
        return {"client": client, "incoming": records(incoming), "outgoing": records(outgoing),
                "daily": records(daily), "transactions": records(client_tx)}

    @application.get("/api/clients/{gid}/navigation")
    def client_navigation(
        gid: Annotated[str, APIPath(pattern=r"^-?\d+$", max_length=20)],
        result: Annotated[AnalysisResult, Depends(analysis)],
        filters: Annotated[dict, Depends(filter_query)],
    ):
        selected_client(result, gid)
        ordered = filtered_nodes(result, filters).gid.map(str).tolist()
        total = len(ordered)
        try:
            index = ordered.index(gid)
        except ValueError:
            return {"position": None, "total": total, "previous_gid": None, "next_gid": None}
        return {"position": index + 1, "total": total,
                "previous_gid": ordered[index - 1] if index > 0 else None,
                "next_gid": ordered[index + 1] if index + 1 < total else None}

    @application.get("/api/clients/{gid}/patterns")
    def patterns(
        gid: Annotated[str, APIPath(pattern=r"^-?\d+$", max_length=20)],
        result: Annotated[AnalysisResult, Depends(analysis)],
        kind: Literal["all", "burst", "group_receipts", "repeated_group", "transit_window", "repeated_route", "cycle"] = "all",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        fingerprint: str | None = None,
    ):
        selected_client(result, gid)
        check_fingerprint(result, fingerprint)
        events = client_events(result, gid)
        counts = dict(Counter(event["kind"] for event in events))
        chosen = events if kind == "all" else [event for event in events if event["kind"] == kind]
        return json_safe({"fingerprint": result.metadata["input_fingerprint"], "items": chosen[offset:offset + limit],
                          "total": len(chosen), "kind_counts": counts, "status": result.pattern_status.get(gid, {}),
                          "coverage": result.pattern_coverage})

    @application.get("/api/patterns/{pattern_id}")
    def pattern_detail(pattern_id: str, result: Annotated[AnalysisResult, Depends(analysis)], fingerprint: str | None = None):
        check_fingerprint(result, fingerprint)
        pattern = selected_pattern(result, pattern_id)
        transactions = [result.event_index.rows[ref] for ref in pattern["evidence_refs"]]
        transactions.sort(key=lambda row: (row["date"], int(row["src"]), int(row["dst"]), row["ref"]))
        comparison_refs = {ref for day in pattern["measurements"].get("baseline", []) for ref in day["evidence_refs"]}
        comparison_transactions = [result.event_index.rows[ref] for ref in sorted(comparison_refs)]
        return json_safe({"fingerprint": result.metadata["input_fingerprint"], "pattern": pattern,
                          "transactions": transactions, "comparison_transactions": comparison_transactions})

    @application.get("/api/clients/{gid}/report")
    def report(
        gid: Annotated[str, APIPath(pattern=r"^-?\d+$", max_length=20)],
        result: Annotated[AnalysisResult, Depends(analysis)],
        format: Literal["html", "json"] = "html",
        fingerprint: str | None = None,
    ):
        selected_client(result, gid)
        check_fingerprint(result, fingerprint)
        brief = build_report(result, gid)
        payload = render_report_html(brief) if format == "html" else json.dumps(json_safe(brief), ensure_ascii=False, indent=2, allow_nan=False)
        return Response(payload.encode("utf-8"), media_type="text/html" if format == "html" else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="client_{gid}_report.{format}"', "Cache-Control": "no-store"})

    @application.get("/api/graph", response_class=HTMLResponse)
    def graph(
        gid: Annotated[str, Query(pattern=r"^-?\d+$", max_length=20)],
        result: Annotated[AnalysisResult, Depends(analysis)],
        filters: Annotated[dict, Depends(filter_query)],
        mode: Literal["ego", "community", "filtered"] = "ego",
        hops: Annotated[int, Query(ge=1, le=2)] = 1,
        color_by: Literal["role", "cluster"] = "role",
        full: bool = False,
        pattern_id: str | None = None,
        fingerprint: str | None = None,
    ):
        client = selected_client(result, gid)
        check_fingerprint(result, fingerprint)
        pattern = selected_pattern(result, pattern_id) if pattern_id else None
        event_edges = None
        if pattern:
            if gid not in pattern["gids"]:
                raise HTTPException(422, detail="Выбранный клиент не участвует в этом событии.")
            visible = set(pattern["gids"])
            supported = {(edge["src"], edge["dst"]) for edge in pattern["edges"]}
            sums = defaultdict(list)
            for ref in pattern["evidence_refs"]:
                row = result.event_index.rows[ref]
                pair = (row["src"], row["dst"])
                if pair in supported:
                    sums[pair].append(row["sum_kzt"])
            event_edges = {pair: {"sum_kzt": math.fsum(amounts), "n_tx": len(amounts)} for pair, amounts in sums.items()}
        elif mode == "ego":
            visible = graph_node_ids(result.graph, gid, hops)
        elif mode == "community":
            visible = set(result.nodes.loc[result.nodes.cluster_id.eq(client["cluster_id"]), "gid"].map(str))
        else:
            frame = filtered_nodes(result, filters)
            visible = set(frame.gid.map(str)) | {gid}
        try:
            rendered = render_graph_html(result.graph, result.nodes, gid, color_by=color_by,
                                         node_ids=visible, max_nodes=None if full or pattern else 180,
                                         event_edges=event_edges, event_label=pattern["title"] if pattern else None)
        except Exception as exc:
            raise HTTPException(503, detail=f"Не удалось построить граф: {exc}") from exc
        return HTMLResponse(rendered, headers={"Cache-Control": "no-store"})

    @application.get("/api/exports/{filename}")
    def export(
        filename: str,
        result: Annotated[AnalysisResult, Depends(analysis)],
    ):
        if filename not in EXPORT_NAMES:
            raise HTTPException(404, detail="Доступны nodes_roles.csv, clusters.csv, top_nodes.csv и run_metadata.json")
        if filename == "run_metadata.json":
            payload = json.dumps(json_safe(result.metadata), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
            media_type = "application/json"
        else:
            node_order = NODE_COLUMNS + [column for column in result.nodes if column not in NODE_COLUMNS]
            frames = {"nodes_roles.csv": result.nodes[node_order],
                      "clusters.csv": result.clusters[CLUSTER_COLUMNS],
                      "top_nodes.csv": result.top_nodes[TOP_COLUMNS]}
            payload = frames[filename].to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
            media_type = "text/csv"
        return Response(payload, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})

    return application


app = create_app()
