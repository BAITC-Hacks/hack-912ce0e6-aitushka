"""Bounded, evidence-backed route repetition and directed cycle detection.

Daily observations establish date compatibility, never the provenance of money.
An edge-day may support different patterns; pattern amounts are not additive.
"""

from dataclasses import dataclass
from datetime import date

import networkx as nx

from .event_data import EventIndex, make_pattern


@dataclass
class _TemporalBudget:
    limit: int
    used: int = 0
    exhausted: bool = False

    def take(self):
        if self.used >= self.limit:
            self.exhausted = True
            return False
        self.used += 1
        return True


_COMMON_LIMITATIONS = [
    "Совместимость дат не доказывает движение одних и тех же денег.",
    "Разные паттерны могут использовать одни и те же операции; их суммы нельзя складывать.",
    "Вывод ограничен наблюдаемым периодом и полнотой выгрузки.",
]


def _steps_refs(steps):
    return sorted({ref for step in steps for ref in step["evidence_refs"]})


def _match_route(first, second, budget, ordinals, window):
    """Maximum date matching for two sorted streams, with each day used once.

    Since every admissible interval has the same width, matching the earliest
    incoming day to the earliest eligible outgoing day preserves cardinality.
    This does not allocate monetary amounts across routes.
    """
    incoming = outgoing = 0
    episodes = []
    while incoming < len(first) and outgoing < len(second):
        if not budget.take():
            return episodes, False
        lag = ordinals[second[outgoing]["date"]] - ordinals[first[incoming]["date"]]
        if lag <= 0:
            outgoing += 1
        elif lag > window:
            incoming += 1
        else:
            episodes.append({"steps": [first[incoming], second[outgoing]], "temporal_status": "chronological"})
            incoming += 1
            outgoing += 1
    return episodes, True


def _cycle_candidates(graph, maximum_length, search_budget):
    """Enumerate each rotation once; reversed directed cycles remain distinct."""
    components = [part for part in nx.strongly_connected_components(graph) if len(part) > 1]
    components.sort(key=lambda part: min(map(int, part)))
    successors = {node: sorted(graph.successors(node), key=int) for node in graph}
    for component in components:
        for start in sorted(component, key=int):
            def visit(path):
                for nxt in successors[path[-1]]:
                    # Count dead ends as well as completed cycles. A candidate
                    # cap alone does not bound exploration of dense components
                    # whose shortest cycles exceed the configured length.
                    if not search_budget.take():
                        return
                    if nxt == start and len(path) >= 2:
                        yield tuple(path)
                    elif len(path) < maximum_length and nxt in component and int(nxt) > int(start) and nxt not in path:
                        yield from visit(path + [nxt])
                        if search_budget.exhausted:
                            return
            yield from visit([start])
            if search_budget.exhausted:
                return


def _first_cycle_episode(pairs, index, budget, ordinals, window, allow_same_day=False):
    """Return one compatible episode, searching every possible cycle origin.

    The second result says whether existence/nonexistence was resolved before
    the budget ended. A found episode resolves existence without enumerating
    other episodes; no repeat count is inferred.
    """
    minimum_lag = 0 if allow_same_day else 1
    for offset in range(len(pairs)):
        rotated = pairs[offset:] + pairs[:offset]

        def extend(steps):
            for step in index.edge_series.get(rotated[len(steps)], []):
                if not budget.take():
                    return None
                if steps:
                    lag = ordinals[step["date"]] - ordinals[steps[-1]["date"]]
                    if lag < minimum_lag:
                        continue
                    if lag > window:
                        break
                new_steps = steps + [step]
                if len(new_steps) == len(rotated):
                    return new_steps
                result = extend(new_steps)
                if result is not None or budget.exhausted:
                    return result
            return None

        result = extend([])
        if result is not None:
            return result, True
        if budget.exhausted:
            return None, False
    return None, True


def detect_route_patterns(index: EventIndex, graph: nx.DiGraph, config: dict):
    """Return reproducible patterns plus explicit coverage and search limits."""
    settings = config.get("patterns", {})
    window = int(config.get("temporal_window_days", 2))
    minimum_repeats = int(settings.get("route_min_repeats", 2))
    maximum_length = int(settings.get("cycle_max_length", 4))
    route_limit = int(settings.get("max_route_candidates", 50_000))
    cycle_limit = int(settings.get("max_cycle_candidates", 5_000))
    budget = _TemporalBudget(int(settings.get("max_temporal_states", 100_000)))
    cycle_search_budget = _TemporalBudget(int(settings.get("max_cycle_search_steps", 100_000)))
    if window < 1 or minimum_repeats < 2 or not 2 <= maximum_length <= 4:
        raise ValueError("Требуются окно >=1, минимум повторов >=2 и длина цикла от 2 до 4")
    if min(route_limit, cycle_limit, budget.limit, cycle_search_budget.limit) < 1:
        raise ValueError("Лимиты поиска паттернов должны быть положительными")

    ordered_graph = nx.DiGraph()
    ordered_graph.add_nodes_from(sorted((str(int(gid)) for gid in graph), key=int))
    ordered_graph.add_edges_from(sorted(
        ((str(int(src)), str(int(dst))) for src, dst in graph.edges if src != dst),
        key=lambda pair: (int(pair[0]), int(pair[1])),
    ))
    ordinals = {day: date.fromisoformat(day).toordinal() for _, _, day in index.edge_days}
    events = []
    routes_evaluated = cycles_evaluated = 0
    route_search_complete = cycle_search_complete = True
    route_temporal_complete = cycle_temporal_complete = True
    route_candidate_limit_hit = False
    cycle_candidate_limit_hit = False

    for middle in ordered_graph:
        stop = False
        for source in sorted(ordered_graph.predecessors(middle), key=int):
            for target in sorted(ordered_graph.successors(middle), key=int):
                if source == target:
                    continue
                if routes_evaluated >= route_limit:
                    route_search_complete = False
                    route_candidate_limit_hit = True
                    stop = True
                    break
                routes_evaluated += 1
                episodes, complete = _match_route(
                    index.edge_series.get((source, middle), []),
                    index.edge_series.get((middle, target), []), budget, ordinals, window,
                )
                route_temporal_complete &= complete
                if len(episodes) >= minimum_repeats:
                    steps = [step for episode in episodes for step in episode["steps"]]
                    qualifiers = [] if complete else ["Поиск прерван по лимиту: число повторений — нижняя оценка."]
                    events.append(make_pattern(
                        kind="repeated_route", focus_gid=middle, gids=[source, middle, target],
                        title="Повторяющийся маршрут",
                        summary=f"{source} → {middle} → {target}: {'не менее ' if not complete else ''}{len(episodes)} повторений в разные дни.",
                        dates=[step["date"] for step in steps],
                        measurements={"repeat_count": len(episodes), "repeat_count_is_lower_bound": not complete,
                                      "temporal_status": "chronological", "temporal_evaluation_complete": complete,
                                      "route": [source, middle, target], "evidence_scope": "selected_episodes",
                                      "unique_start_days": len(episodes)},
                        rule={"min_repeats": minimum_repeats, "min_lag_days": 1, "max_lag_days": window,
                              "edge_day_reuse_within_route": False, "route_length": 2},
                        evidence_refs=_steps_refs(steps),
                        edges=[{"src": source, "dst": middle}, {"src": middle, "dst": target}],
                        episodes=episodes, limitations=_COMMON_LIMITATIONS + qualifiers,
                    ))
                if not complete:
                    route_search_complete = False
                    stop = True
                    break
            if stop:
                break
        if stop:
            break

    for cycle in _cycle_candidates(ordered_graph, maximum_length, cycle_search_budget):
        if cycles_evaluated >= cycle_limit:
            cycle_search_complete = False
            cycle_candidate_limit_hit = True
            break
        cycles_evaluated += 1
        pairs = list(zip(cycle, cycle[1:] + cycle[:1]))
        episode, complete = _first_cycle_episode(pairs, index, budget, ordinals, window)
        status = "chronological" if episode else "structural_only"
        if episode is None and complete:
            episode, complete = _first_cycle_episode(pairs, index, budget, ordinals, window, allow_same_day=True)
            if episode:
                status = "date_order_unknown"
        if not complete:
            status = "not_evaluated"
            cycle_temporal_complete = False
        all_days = [day for pair in pairs for day in index.edge_series.get(pair, [])]
        evidence_steps = episode if episode else all_days
        label = " → ".join(cycle + cycle[:1])
        description = {
            "chronological": "Найдена последовательность по датам; показан один опорный эпизод.",
            "date_order_unknown": "Есть переводы в один день: порядок внутри дня неизвестен.",
            "structural_only": "Есть замкнутые связи за период; совместимый по датам эпизод в заданном окне не найден.",
            "not_evaluated": "Есть замкнутые связи за период; временная проверка не завершена из-за лимита.",
        }[status]
        limitations = _COMMON_LIMITATIONS + ["Наличие цикла не доказывает возврат первоначальной суммы."]
        if status == "date_order_unknown":
            limitations += ["Последовательность переводов одного дня установить по этим данным нельзя."]
        if not complete:
            limitations += ["Поиск остановлен по лимиту: отсутствие хронологического эпизода не установлено."]
        events.append(make_pattern(
            kind="cycle", focus_gid=cycle[0], gids=list(cycle), title="Возвратный маршрут: гипотеза",
            summary=f"{label}. {description}", dates=[day["date"] for day in evidence_steps],
            measurements={"cycle_length": len(cycle), "cycle": list(cycle), "temporal_status": status,
                          "temporal_evaluation_complete": complete,
                          "episode_selection": "first_support" if episode else "none",
                          "evidence_scope": "selected_episode" if episode else "full_observed_period"},
            rule={"min_lag_days": 1, "max_lag_days": window, "cycle_max_length": maximum_length,
                  "same_day_order_known": False},
            evidence_refs=_steps_refs(evidence_steps), edges=[{"src": src, "dst": dst} for src, dst in pairs],
            episodes=[{"steps": episode, "temporal_status": status}] if episode else [], limitations=limitations,
        ))

    if cycle_search_budget.exhausted:
        cycle_search_complete = False
    limits_hit = []
    if route_candidate_limit_hit:
        limits_hit.append("max_route_candidates")
    if cycle_candidate_limit_hit:
        limits_hit.append("max_cycle_candidates")
    if cycle_search_budget.exhausted:
        limits_hit.append("max_cycle_search_steps")
    if budget.exhausted:
        limits_hit.append("max_temporal_states")
    coverage = {
        "search_complete": route_search_complete and cycle_search_complete and route_temporal_complete and cycle_temporal_complete,
        "route_search_complete": route_search_complete and route_temporal_complete,
        "cycle_search_complete": cycle_search_complete and cycle_temporal_complete,
        "routes_evaluated": routes_evaluated, "cycles_evaluated": cycles_evaluated,
        "cycle_search_steps_used": cycle_search_budget.used,
        "temporal_states_used": budget.used, "limits_hit": limits_hit,
        "limits": {"max_route_candidates": route_limit, "max_cycle_candidates": cycle_limit,
                   "max_cycle_search_steps": cycle_search_budget.limit,
                   "max_temporal_states": budget.limit, "cycle_max_length": maximum_length},
        "scope": "two_edge_routes_and_cycles_up_to_configured_length",
        "cycle_episode_policy": "first_support_not_repeat_count",
        "overlapping_patterns_are_additive": False,
    }
    return events, coverage
