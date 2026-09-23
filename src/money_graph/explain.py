"""Russian, numerical explanations derived only from measured features."""

from __future__ import annotations

import math
from collections.abc import Mapping


def compact_number(value: float) -> str:
    """Readable approximate number; detailed tables retain exact values."""
    value = float(value)
    if not math.isfinite(value):
        return "не определено"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} млн"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f} тыс."
    return f"{value:.2f}".rstrip("0").rstrip(".")


def role_evidence(row: Mapping, role: str) -> str:
    incoming = int(row["in_deg"])
    outgoing = int(row["out_deg"])
    seed_reach = int(row["seed_reach"])
    in_money = compact_number(row["in_kzt"])
    out_money = compact_number(row["out_kzt"])
    if role == "consolidator":
        text = (f"Вход от {incoming} клиентов: {in_money} KZT; "
                f"достижим от {seed_reach} seed. Схождение потоков.")
    elif role == "distributor":
        text = (f"{outgoing} получателей при {incoming} плательщиках; "
                f"выход {out_money} KZT. Веер исходящих переводов.")
    elif role == "transit":
        text = (f"Вход {in_money}, выход {out_money} KZT; "
                f"выход/вход={float(row['pass_through']):.2f}; "
                f"совпадение по датам {float(row.get('temporal_share', 0)):.0%}. Гипотеза транзита.")
    elif role == "terminal":
        text = (f"Вход {in_money} KZT от {incoming} клиентов; "
                f"выходящих связей {outgoing}, depth={int(row['depth'])}. "
                "Кандидат в конечные получатели за период.")
    elif role == "coordinator":
        text = (f"Посредничество {float(row['betweenness']):.5f}; "
                f"соседних сообществ {int(row['intercluster_degree'])}; "
                f"достижим от {seed_reach} seed. Структурный связующий узел.")
    else:
        text = (f"Связи: вход {incoming}, выход {outgoing}; "
                f"оборот max(вход,выход) {compact_number(max(row['in_kzt'], row['out_kzt']))} KZT. "
                "Выраженная роль не установлена.")
    if bool(row.get("truncated_by_depth", False)):
        text += " Граница: выход неизвестен."
    return text[:200]


def observation_note(row: Mapping) -> str:
    notes = []
    if int(row["in_deg"]) + int(row["out_deg"]) == 0:
        notes.append("Нет связей в предоставленной выборке; данных для роли недостаточно.")
    if bool(row.get("truncated_by_depth", False)):
        notes.append("Граница глубины: дальнейшие переводы неизвестны; нельзя оценить удержание средств.")
    if bool(row["is_seed"]):
        notes.append("Seed: входящие переводы неполны из-за обхода только по исходящим.")
    elif float(row["in_kzt"]) <= 0 and int(row["out_deg"]) > 0:
        notes.append("Наблюдаемый вход отсутствует; источник исходящих средств неизвестен.")
    if float(row["out_kzt"]) > float(row["in_kzt"]):
        notes.append("Выход выше наблюдаемого входа: возможны средства вне периода или выборки.")
    if not notes:
        notes.append("Вывод ограничен периодом, банком и порогом выгрузки; роль — гипотеза для проверки.")
    return " ".join(notes)


def priority_explanation(row: Mapping) -> str:
    labels = {
        "seed_convergence": f"достижим от {int(row['seed_reach'])} seed",
        "observed_flow": f"поток {compact_number(max(row['in_kzt'], row['out_kzt']))} KZT",
        "brokerage": f"посредничество {float(row['betweenness']):.5f}",
        "fan_in": f"{int(row['in_deg'])} плательщиков",
    }
    ordered = sorted(labels, key=lambda key: -float(row[f"contribution_{key}"]))
    contributions = [
        f"{labels[key]} (+{float(row[f'contribution_{key}']):.3f})"
        for key in ordered[:2]
    ]
    if float(row["priority_score"]) == 0:
        return "Приоритет 0: по наблюдаемым данным все четыре компонента рейтинга равны 0."
    text = "Главные вклады: " + "; ".join(contributions) + "."
    if bool(row.get("truncated_by_depth", False)):
        text += " Выход за границей неизвестен."
    return text[:200]


def cluster_hypothesis(n_nodes: int, n_seed: int, roles: Mapping[str, int], internal: float) -> str:
    if n_nodes == 1 and internal == 0:
        return f"Один узел, seed={n_seed}; внутренних переводов нет. Принадлежность к группе не установлена."
    descriptions = []
    for role, label in (
        ("consolidator", "консолидаторов"),
        ("distributor", "распределителей"),
        ("transit", "транзитных кандидатов"),
        ("coordinator", "структурных посредников"),
    ):
        count = int(roles.get(role, 0))
        if count:
            descriptions.append(f"{label}: {count}")
    pattern = "; ".join(descriptions) if descriptions else "выраженная структура ролей не установлена"
    return (f"Структурная группа: {n_nodes} узлов, {n_seed} seed, "
            f"внутренние переводы {compact_number(internal)} KZT; {pattern}. "
            "Общая организация не доказана.")
