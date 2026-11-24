CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    category TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    related_type TEXT,
    related_id TEXT,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS onboarding_task_templates (
    task_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    display_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_onboarding_tasks (
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    task_key TEXT NOT NULL REFERENCES onboarding_task_templates(task_key),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'submitted', 'approved', 'rejected')),
    response_json TEXT NOT NULL DEFAULT '{}',
    submitted_at TEXT,
    reviewed_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (employee_id, task_key)
);

CREATE TABLE IF NOT EXISTS employee_documents (
    document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_group_id TEXT NOT NULL,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (
        category IN ('resume', 'identity', 'education', 'offer_letter', 'photo', 'other')
    ),
    original_name TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    file_size INTEGER NOT NULL CHECK (file_size > 0),
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Verified', 'Rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL,
    reviewed_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    replaces_document_id INTEGER REFERENCES employee_documents(document_id) ON DELETE SET NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    UNIQUE (document_group_id, version)
);

CREATE TABLE IF NOT EXISTS employee_timeline (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    actor_username TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    previous_values_json TEXT NOT NULL DEFAULT '{}',
    new_values_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leave_policies (
    leave_type TEXT PRIMARY KEY COLLATE NOCASE,
    annual_allowance REAL NOT NULL DEFAULT 0 CHECK (annual_allowance >= 0),
    count_weekends INTEGER NOT NULL DEFAULT 0 CHECK (count_weekends IN (0, 1)),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    updated_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leave_requests (
    leave_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    leave_type TEXT NOT NULL REFERENCES leave_policies(leave_type),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    requested_days REAL NOT NULL CHECK (requested_days > 0),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
    rejection_reason TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS attendance_records (
    attendance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    local_work_date TEXT NOT NULL,
    check_in_utc TEXT NOT NULL,
    check_out_utc TEXT,
    is_late INTEGER NOT NULL DEFAULT 0 CHECK (is_late IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (employee_id, local_work_date)
);

CREATE TABLE IF NOT EXISTS attendance_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL REFERENCES attendance_records(attendance_id) ON DELETE CASCADE,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    requested_check_out_utc TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Rejected')),
    reviewed_by TEXT REFERENCES users(username) ON DELETE SET NULL,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS performance_periods (
    period_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'Closed')),
    created_by TEXT NOT NULL REFERENCES users(username),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS employee_goals (
    goal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    period_id INTEGER NOT NULL REFERENCES performance_periods(period_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    measurable_target TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN ('not_started', 'in_progress', 'completed')),
    progress_note TEXT NOT NULL DEFAULT '',
    assigned_by TEXT NOT NULL REFERENCES users(username),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    period_id INTEGER NOT NULL REFERENCES performance_periods(period_id) ON DELETE CASCADE,
    self_assessment TEXT NOT NULL DEFAULT '',
    self_rating INTEGER CHECK (self_rating BETWEEN 1 AND 5),
    self_submitted_at TEXT,
    manager_feedback TEXT NOT NULL DEFAULT '',
    manager_rating INTEGER CHECK (manager_rating BETWEEN 1 AND 5),
    manager_username TEXT REFERENCES users(username) ON DELETE SET NULL,
    manager_submitted_at TEXT,
    admin_note TEXT NOT NULL DEFAULT '',
    admin_username TEXT REFERENCES users(username) ON DELETE SET NULL,
    admin_reviewed_at TEXT,
    UNIQUE (employee_id, period_id)
);

CREATE TABLE IF NOT EXISTS employee_reporting (
    employee_id TEXT PRIMARY KEY REFERENCES employees(employee_id) ON DELETE CASCADE,
    manager_id TEXT REFERENCES employees(employee_id) ON DELETE SET NULL,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (manager_id IS NULL OR manager_id <> employee_id)
);

CREATE TABLE IF NOT EXISTS manager_assignment_history (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    previous_manager_id TEXT,
    new_manager_id TEXT,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS id_cards (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    verification_hash TEXT NOT NULL UNIQUE,
    pdf_storage_key TEXT,
    issued_by TEXT NOT NULL REFERENCES users(username),
    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS employee_import_batches (
    batch_id TEXT PRIMARY KEY,
    uploaded_by TEXT NOT NULL REFERENCES users(username),
    status TEXT NOT NULL DEFAULT 'preview'
        CHECK (status IN ('preview', 'committed', 'failed')),
    rows_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient_read
    ON notifications(recipient_username, is_read, notification_id DESC);
CREATE INDEX IF NOT EXISTS idx_onboarding_employee_status
    ON employee_onboarding_tasks(employee_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_employee_current
    ON employee_documents(employee_id, is_current, status);
CREATE INDEX IF NOT EXISTS idx_documents_status
    ON employee_documents(status, created_at);
CREATE INDEX IF NOT EXISTS idx_timeline_employee_created
    ON employee_timeline(employee_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leave_employee_dates
    ON leave_requests(employee_id, start_date, end_date, status);
CREATE INDEX IF NOT EXISTS idx_leave_status_dates
    ON leave_requests(status, start_date);
CREATE INDEX IF NOT EXISTS idx_attendance_employee_date
    ON attendance_records(employee_id, local_work_date DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_corrections_status
    ON attendance_corrections(status, created_at);
CREATE INDEX IF NOT EXISTS idx_goals_employee_period
    ON employee_goals(employee_id, period_id);
CREATE INDEX IF NOT EXISTS idx_reviews_period_employee
    ON performance_reviews(period_id, employee_id);
CREATE INDEX IF NOT EXISTS idx_reporting_manager
    ON employee_reporting(manager_id, employee_id);
CREATE INDEX IF NOT EXISTS idx_manager_history_employee
    ON manager_assignment_history(employee_id, assigned_at DESC);

INSERT OR IGNORE INTO onboarding_task_templates
    (task_key, title, description, required, display_order)
VALUES
    ('personal_info', 'Verify personal information', 'Confirm your name and employee information.', 1, 1),
    ('contact_info', 'Verify contact information', 'Confirm your email and phone number.', 1, 2),
    ('documents', 'Submit required documents', 'Upload requested identity and employment documents.', 1, 3),
    ('policy_acknowledgement', 'Acknowledge company policies', 'Read and acknowledge the current company policies.', 1, 4),
    ('emergency_contact', 'Add emergency contact', 'Provide an emergency contact name and phone number.', 1, 5),
    ('role_confirmation', 'Confirm department and designation', 'Confirm your assigned department and designation.', 1, 6);

INSERT OR IGNORE INTO leave_policies(leave_type, annual_allowance, count_weekends, active)
VALUES ('Casual', 0, 0, 1), ('Sick', 0, 0, 1), ('Earned', 0, 0, 1);

INSERT OR IGNORE INTO employee_onboarding_tasks(employee_id, task_key)
SELECT e.employee_id, t.task_key
FROM employees e CROSS JOIN onboarding_task_templates t;

CREATE TRIGGER IF NOT EXISTS trg_employee_onboarding_tasks_insert
AFTER INSERT ON employees
BEGIN
    INSERT OR IGNORE INTO employee_onboarding_tasks(employee_id, task_key)
    SELECT NEW.employee_id, task_key FROM onboarding_task_templates;
END;

INSERT OR IGNORE INTO employee_reporting(employee_id, manager_id, assigned_by)
SELECT employee_id, NULL, 'system:migration'
FROM employees;
