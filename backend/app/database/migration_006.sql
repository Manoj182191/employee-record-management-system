CREATE TABLE IF NOT EXISTS department_heads (
    department_name TEXT PRIMARY KEY COLLATE NOCASE
        REFERENCES departments(name) ON UPDATE CASCADE ON DELETE CASCADE,
    employee_id TEXT NOT NULL UNIQUE
        REFERENCES employees(employee_id) ON DELETE CASCADE,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS department_head_history (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    department_name TEXT NOT NULL,
    previous_head_id TEXT,
    new_head_id TEXT,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_department_head_history_name
    ON department_head_history(department_name, assigned_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_clear_ineligible_department_head
AFTER UPDATE OF department, status, active ON employees
WHEN NEW.department <> OLD.department COLLATE NOCASE
  OR NEW.status <> 'Approved'
  OR NEW.active <> 1
BEGIN
    INSERT INTO department_head_history(
        department_name, previous_head_id, new_head_id, assigned_by
    )
    SELECT department_name, employee_id, NULL, 'system:employee_update'
    FROM department_heads
    WHERE employee_id = NEW.employee_id;
    DELETE FROM department_heads WHERE employee_id = NEW.employee_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_record_department_head_employee_delete
BEFORE DELETE ON employees
BEGIN
    INSERT INTO department_head_history(
        department_name, previous_head_id, new_head_id, assigned_by
    )
    SELECT department_name, employee_id, NULL, 'system:employee_delete'
    FROM department_heads
    WHERE employee_id = OLD.employee_id;
END;
