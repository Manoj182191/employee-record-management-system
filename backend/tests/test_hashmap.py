from app.hashmap import EmployeeHashMap, LinearProbingMap


def test_separate_chaining_handles_collisions_updates_and_resize():
    employee_map = EmployeeHashMap[str, dict](capacity=3, max_load_factor=0.6)
    employee_map.put("EMP1", {"name": "First"})
    employee_map.put("EMP4", {"name": "Second"})
    employee_map.put("EMP1", {"name": "Updated"})
    employee_map.put("EMP7", {"name": "Third"})

    assert employee_map.get("EMP1") == {"name": "Updated"}
    assert employee_map.get("EMP4") == {"name": "Second"}
    assert employee_map.delete("EMP4") is True
    assert employee_map.delete("EMP4") is False
    assert employee_map.size == 2
    assert employee_map.stats()["resizes"] >= 1
    assert employee_map.stats()["collisions"] >= 1
    assert employee_map.stats()["load_factor"] <= 0.6


def test_linear_probing_resolves_collisions_and_deleted_slots():
    employee_map = LinearProbingMap[str, str](capacity=3, max_load_factor=0.8)
    employee_map.put("EMP1", "first")
    employee_map.put("EMP4", "second")
    employee_map.put("EMP7", "third")
    assert employee_map.get("EMP4") == "second"
    assert employee_map.delete("EMP4") is True
    assert employee_map.get("EMP7") == "third"
    employee_map.put("EMP10", "fourth")
    assert employee_map.get("EMP10") == "fourth"
    assert employee_map.collisions > 0
    assert employee_map.size == 3
