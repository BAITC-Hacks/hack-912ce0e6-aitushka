from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def widget(widgets, label):
    return next(item for item in widgets if item.label == label)


@pytest.fixture
def app_test(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("MONEY_GRAPH_DATA", str(data_dir))
    monkeypatch.setenv("MONEY_GRAPH_OUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("MONEY_GRAPH_CONFIG", str(ROOT / "config.json"))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()


def test_dashboard_loads_all_views_and_export_controls(app_test):
    assert not app_test.exception
    assert not app_test.error
    assert [tab.label for tab in app_test.tabs] == ["Обзор сети", "Граф переводов", "Карточка клиента"]
    assert widget(app_test.metric, "Клиентов в сети").value == "26"
    assert len(app_test.get("iframe")) == 1
    labels = {item.label for item in app_test.get("download_button")}
    assert {"nodes_roles.csv", "clusters.csv", "top_nodes.csv"}.issubset(labels)


def test_search_finds_exact_large_gid_of_an_isolate_excluded_by_role_filter(app_test, valid_frames):
    _, nodes, _ = valid_frames
    isolated_gid = str(nodes["gid"].iloc[-1])
    widget(app_test.multiselect, "Роли клиентов").set_value(["terminal"]).run()
    widget(app_test.text_input, "Поиск клиента по gid").set_value(isolated_gid)
    widget(app_test.button, "Найти клиента").click().run()
    assert not app_test.exception
    assert not app_test.error
    assert app_test.session_state["selected_gid"] == isolated_gid
    assert any(item.value == f"Клиент {isolated_gid}" for item in app_test.subheader)
    assert any("нет операций" in item.value for item in app_test.info)
    assert widget(app_test.multiselect, "Роли клиентов").value == ["terminal"]
    assert len(app_test.get("iframe")) == 1


def test_missing_data_folder_is_an_actionable_error_not_a_crash(app_test, tmp_path):
    widget(app_test.text_input, "Папка данных").set_value(str(tmp_path / "missing-data")).run()
    assert not app_test.exception
    assert len(app_test.error) == 1
    assert "Не удалось выполнить анализ" in app_test.error[0].value
    assert any("nodes.parquet" in item.value for item in app_test.info)
