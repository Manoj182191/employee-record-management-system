import { useCallback, useEffect, useRef, useState } from "react";
import { api, clearToken, downloadFile, hasToken, jsonBody, saveToken } from "./api.js";
import { AdvancedModules, NotificationCenter } from "./AdvancedModules.jsx";

const EMPTY_EMPLOYEE = {
  employee_id: "",
  name: "",
  email: "",
  department: "",
  designation: "",
  phone: "",
  joining_date: "",
  salary: "",
  password: "",
};

const NAV_ITEMS = {
  admin: [
    ["overview", "Overview"],
    ["directory", "Employee directory"],
    ["organization", "Departments & roles"],
    ["requests", "Pending requests"],
    ["audit", "Audit trail"],
    ["benchmark", "Hash map analysis"],
    ["services", "HR tools"],
    ["notifications", "Notifications"],
  ],
  employee: [
    ["overview", "My dashboard"],
    ["profile", "My profile"],
    ["requests", "Profile requests"],
    ["security", "Account security"],
    ["services", "Employee services"],
    ["notifications", "Notifications"],
  ],
};

function initials(name = "") {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function prettyRole(role = "") {
  return { admin: "Administrator", employee: "Employee" }[role] || role;
}

function money(value) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(!hasToken());
  const [activeView, setActiveView] = useState("overview");
  const [error, setError] = useState("");
  const [unreadNotifications, setUnreadNotifications] = useState(0);
  const [serviceTarget, setServiceTarget] = useState(null);

  useEffect(() => {
    if (!hasToken()) return;
    api("/api/auth/me")
      .then(setUser)
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!user) return;
    api("/api/notifications?limit=1")
      .then((result) => setUnreadNotifications(result.unread_count))
      .catch(() => setUnreadNotifications(0));
  }, [user]);

  const signIn = async (username, password, loginType) => {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: jsonBody({ username, password, login_type: loginType }),
    });
    saveToken(result.access_token);
    try {
      const profile = await api("/api/auth/me");
      setUser(profile);
      setActiveView("overview");
    } catch (requestError) {
      clearToken();
      throw requestError;
    }
    setError("");
  };

  const registerEmployee = (values) => api("/api/auth/register", {
    method: "POST",
    body: jsonBody(values),
  });

  const signOut = () => {
    clearToken();
    setUser(null);
    setActiveView("overview");
    setServiceTarget(null);
    setError("");
  };

  const openNotificationRelated = (item) => {
    if (item.related_type === "profile_request") {
      setActiveView("requests");
      return;
    }
    const moduleByType = {
      employee: "timeline",
      onboarding: "onboarding",
      document: "documents",
      leave: "leave",
      attendance_correction: "attendance",
      goal: "performance",
      performance: "performance",
      id_card: "id-card",
      organization: "organization",
    };
    const module = moduleByType[item.related_type];
    if (!module) {
      setServiceTarget(null);
      setActiveView("services");
      return;
    }
    setServiceTarget({
      module,
      relatedId: item.related_id,
      employeeId: ["employee", "onboarding"].includes(item.related_type)
        ? item.related_id
        : "",
      relatedType: item.related_type,
    });
    setActiveView("services");
  };

  if (!ready) return <div className="boot-screen">Preparing your workspace…</div>;
  if (!user) return <Login onSignIn={signIn} onRegister={registerEmployee} />;

  const nav = NAV_ITEMS[user.role] || [];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">E</div>
          <div>
            <strong>ERMS</strong>
            <span>People operations</span>
          </div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {nav.map(([key, label]) => (
            <button
              key={key}
              className={`nav-link ${activeView === key ? "selected" : ""}`}
              onClick={() => {
                setActiveView(key);
                setServiceTarget(null);
                setError("");
              }}
            >
              <span className="nav-indicator" />
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="secure-note"><span className="secure-dot" /> Secure workspace</div>
          <button className="nav-link" onClick={signOut}>
            <span className="nav-indicator" /> Sign out
          </button>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb">People <span>/</span> {nav.find(([key]) => key === activeView)?.[1]}</div>
          <div className="user-menu">
            <button
              className="notification-shortcut"
              onClick={() => setActiveView("notifications")}
              aria-label={`Notifications, ${unreadNotifications} unread`}
            >
              Notifications{unreadNotifications > 0 && <span>{unreadNotifications}</span>}
            </button>
            <div className="user-copy">
              <strong>{user.employee?.name || user.username}</strong>
              <span>{prettyRole(user.role)}</span>
            </div>
            <div className="avatar">{initials(user.employee?.name || user.username)}</div>
          </div>
        </header>

        <main className="content">
          {error && <div className="alert error-alert" role="alert">{error}</div>}
          {activeView === "overview" && (
            user.role === "employee"
              ? <EmployeeHome user={user} onOpenRequests={() => setActiveView("requests")} onUserUpdate={setUser} />
              : <Overview role={user.role} />
          )}
          {activeView === "directory" && <Directory role={user.role} />}
          {activeView === "organization" && user.role === "admin" && <Organization />}
          {activeView === "requests" && user.role === "admin" && <AdminRequests />}
          {activeView === "requests" && user.role === "employee" && <EmployeeRequests />}
          {activeView === "audit" && user.role === "admin" && <AuditTrail />}
          {activeView === "benchmark" && user.role === "admin" && <Benchmark />}
          {activeView === "profile" && user.role === "employee" && <EmployeeProfile user={user} />}
          {activeView === "security" && user.role === "employee" && <PasswordChange />}
          {activeView === "services" && (
            <AdvancedModules
              role={user.role}
              user={user}
              initialModule={serviceTarget?.module}
              relatedId={serviceTarget?.relatedId}
              relatedType={serviceTarget?.relatedType}
              initialEmployeeId={serviceTarget?.employeeId}
              onClearTarget={() => setServiceTarget(null)}
            />
          )}
          {activeView === "notifications" && (
            <NotificationCenter
              onUnreadChange={setUnreadNotifications}
              onOpenRelated={openNotificationRelated}
            />
          )}
          <footer className="page-footer">ERMS <span>·</span> Employee Records Management</footer>
        </main>
      </div>
    </div>
  );
}

function Login({ onSignIn, onRegister }) {
  const [flow, setFlow] = useState("choose");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [registrationMessage, setRegistrationMessage] = useState("");
  const [registration, setRegistration] = useState({
    name: "",
    employee_id: "",
    email: "",
    phone: "",
    department: "",
    designation: "",
    joining_date: "",
    password: "",
    confirm_password: "",
  });
  const usernameRef = useRef(null);
  const passwordRef = useRef(null);
  const registrationNameRef = useRef(null);

  useEffect(() => {
    if (flow === "admin-login" || flow === "employee-login") {
      usernameRef.current?.focus();
    } else if (flow === "register") {
      registrationNameRef.current?.focus();
    }
  }, [flow]);

  async function submitLogin(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const loginType = flow === "admin-login" ? "admin" : "employee";
      await onSignIn(username.trim(), password, loginType);
    } catch (requestError) {
      setError(requestError.message);
      passwordRef.current?.focus();
      passwordRef.current?.select();
    } finally {
      setLoading(false);
    }
  }

  async function submitRegistration(event) {
    event.preventDefault();
    setError("");
    if (registration.password !== registration.confirm_password) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      const result = await onRegister(registration);
      setRegistrationMessage(result.message);
      setRegistration({
        name: "",
        employee_id: "",
        email: "",
        phone: "",
        department: "",
        designation: "",
        joining_date: "",
        password: "",
        confirm_password: "",
      });
      setFlow("registered");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  function backToChoices() {
    setFlow("choose");
    setUsername("");
    setPassword("");
    setError("");
    setShowPassword(false);
  }

  function updateRegistration(field, value) {
    setRegistration((current) => ({ ...current, [field]: value }));
  }

  const loginType = flow === "admin-login" ? "admin" : "employee";

  return (
    <div className="login-page">
      <section className="login-aside">
        <div className="brand brand-light">
          <div className="brand-mark">E</div>
          <div><strong>ERMS</strong><span>People operations</span></div>
        </div>
        <div className="login-message">
          <div className="login-message-tag"><span /> EMPLOYEE RECORDS MANAGEMENT</div>
          <h1>Your people.<br />One secure workspace.</h1>
          <p>Manage records, employee access and organizational updates from one trusted system.</p>
          <div className="login-feature-list" aria-label="ERMS benefits">
            <div><span className="login-feature-icon">01</span><div><strong>Protected records</strong><small>Role-based account access</small></div></div>
            <div><span className="login-feature-icon">02</span><div><strong>One source of truth</strong><small>Employee data, always organised</small></div></div>
            <div><span className="login-feature-icon">03</span><div><strong>Fast, simple workflows</strong><small>Built for your daily operations</small></div></div>
          </div>
        </div>
        <div className="login-foot">A private workspace for your organization</div>
      </section>
      <section className="login-form-wrap">
        {flow === "choose" && (
          <section className="login-card">
            <div className="login-card-header">
              <div className="eyebrow">SECURE SIGN IN</div>
              <h2>Welcome back</h2>
              <p>Choose the account type you want to sign in with.</p>
            </div>
            <div className="login-choice-actions">
              <button className="primary-button full-button" onClick={() => setFlow("admin-login")}>
                Admin / HR Login
              </button>
              <button className="secondary-button full-button" onClick={() => setFlow("employee-login")}>
                Employee Login
              </button>
            </div>
            <div className="login-security"><span className="secure-dot" /> Protected account access</div>
          </section>
        )}

        {(flow === "admin-login" || flow === "employee-login") && (
          <form className="login-card" onSubmit={submitLogin}>
            <button type="button" className="text-button login-back-button" onClick={backToChoices}>
              ← Choose another login
            </button>
            <div className="login-card-header">
              <div className="eyebrow">{loginType === "admin" ? "ADMINISTRATOR ACCESS" : "EMPLOYEE ACCESS"}</div>
              <h2>{loginType === "admin" ? "Admin / HR Login" : "Employee Login"}</h2>
              <p>Enter your credentials to continue.</p>
            </div>
            {error && <div className="alert error-alert" role="alert" aria-live="assertive">{error}</div>}
            <label>Username
              <input
                ref={usernameRef}
                name="username"
                placeholder="Enter your username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck="false"
                maxLength="80"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                aria-invalid={Boolean(error)}
                disabled={loading}
                required
              />
            </label>
            <label>Password
              <div className="password-field">
                <input
                  ref={passwordRef}
                  name="password"
                  placeholder="Enter your password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  maxLength="72"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(error)}
                  disabled={loading}
                  required
                />
                <button
                  type="button"
                  className="password-toggle"
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  disabled={loading}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <button className="primary-button full-button" disabled={loading} aria-busy={loading}>
              {loading ? "Signing in…" : "Sign in"}
            </button>
            {loginType === "employee" && (
              <div className="login-create-account">
                New employee?{" "}
                <button type="button" className="text-button" onClick={() => { setError(""); setFlow("register"); }}>
                  Create Account
                </button>
              </div>
            )}
            <div className="login-security"><span className="secure-dot" /> Protected account access</div>
          </form>
        )}

        {flow === "register" && (
          <form className="login-card registration-card" onSubmit={submitRegistration}>
            <button type="button" className="text-button login-back-button" onClick={() => { setError(""); setFlow("employee-login"); }}>
              ← Back to Employee Login
            </button>
            <div className="login-card-header">
              <div className="eyebrow">EMPLOYEE REGISTRATION</div>
              <h2>Create your account</h2>
              <p>Your account will be available after Admin approval.</p>
            </div>
            {error && <div className="alert error-alert" role="alert" aria-live="assertive">{error}</div>}
            <div className="registration-grid">
              <Field inputRef={registrationNameRef} label="Full Name" value={registration.name} onChange={(value) => updateRegistration("name", value)} maxLength="100" autoComplete="name" required />
              <Field label="Employee ID" value={registration.employee_id} onChange={(value) => updateRegistration("employee_id", value)} maxLength="30" autoCapitalize="none" required />
              <Field label="Email" type="email" value={registration.email} onChange={(value) => updateRegistration("email", value)} maxLength="254" autoComplete="email" required />
              <Field label="Phone Number" type="tel" value={registration.phone} onChange={(value) => updateRegistration("phone", value)} maxLength="30" autoComplete="tel" required />
              <Field label="Department" value={registration.department} onChange={(value) => updateRegistration("department", value)} maxLength="80" required />
              <Field label="Designation" value={registration.designation} onChange={(value) => updateRegistration("designation", value)} maxLength="80" required />
              <Field label="Joining Date" type="date" value={registration.joining_date} onChange={(value) => updateRegistration("joining_date", value)} required />
              <Field label="Password (12+ characters)" type="password" value={registration.password} onChange={(value) => updateRegistration("password", value)} minLength="12" maxLength="72" autoComplete="new-password" required />
              <Field label="Confirm Password" type="password" value={registration.confirm_password} onChange={(value) => updateRegistration("confirm_password", value)} minLength="12" maxLength="72" autoComplete="new-password" required />
            </div>
            <button className="primary-button full-button" disabled={loading} aria-busy={loading}>
              {loading ? "Submitting…" : "Submit Registration"}
            </button>
          </form>
        )}

        {flow === "registered" && (
          <section className="login-card">
            <div className="login-card-header">
              <div className="eyebrow">REGISTRATION RECEIVED</div>
              <h2>Request submitted</h2>
              <p>{registrationMessage}</p>
            </div>
            <div className="alert success-alert" role="status">
              Your status is Pending. You cannot access the employee dashboard until an Admin approves your registration.
            </div>
            <button className="primary-button full-button" onClick={() => { setFlow("employee-login"); setError(""); }}>
              Back to Employee Login
            </button>
            <button type="button" className="text-button" onClick={backToChoices}>
              Back to sign-in options
            </button>
          </section>
        )}
      </section>
    </div>
  );
}

function PageHeading({ eyebrow, title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}

function Overview({ role }) {
  const [stats, setStats] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [summary, workforce] = await Promise.all([
        api("/api/stats"),
        api("/api/reports/workforce"),
      ]);
      setStats(summary);
      setReport(workforce);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const departments = Object.entries(stats?.departments || {});
  const maxDepartment = Math.max(1, ...departments.map(([, count]) => count));

  return (
    <>
      <PageHeading eyebrow="WORKFORCE" title={role === "admin" ? "HR overview" : "Workforce overview"} description="A current view of employee records and organization status." />
      {error && <div className="alert error-alert">{error}</div>}
      <div className="metric-grid">
        <Metric label="Total employees" value={stats?.total ?? "—"} note="All employee records" />
        <Metric label="Active & approved" value={stats?.approved ?? "—"} note="Eligible active records" />
        <Metric label="Pending review" value={stats?.pending ?? "—"} note="Awaiting HR decision" />
        <Metric label="Inactive accounts" value={stats?.inactive ?? "—"} note="Access disabled" />
      </div>
      <div className="overview-grid">
        <section className="card">
          <div className="section-title"><div><div className="eyebrow">ORGANIZATION</div><h2>Employees by department</h2></div></div>
          {departments.length ? (
            <div className="department-bars">
              {departments.map(([name, count]) => (
                <div className="bar-row" key={name}>
                  <div className="bar-label"><span>{name}</span><strong>{count}</strong></div>
                  <div className="bar-track"><div style={{ width: `${(count / maxDepartment) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          ) : <EmptyState title="No department data yet" text="Add employee records to see workforce distribution." />}
        </section>
        <section className="card">
          <div className="section-title"><div><div className="eyebrow">RECORD STATUS</div><h2>Approval summary</h2></div></div>
          <div className="status-list">
            {["Approved", "Pending", "Rejected"].map((status) => (
              <div className="status-line" key={status}>
                <span><i className={`status-dot ${status.toLowerCase()}`} />{status}</span>
                <strong>{report?.statuses?.[status] ?? 0}</strong>
              </div>
            ))}
          </div>
          <p className="quiet-note">Statistics are calculated from the current database records.</p>
        </section>
      </div>
    </>
  );
}

function Metric({ label, value, note }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

function Directory({ role }) {
  const [data, setData] = useState({ items: [], total: 0 });
  const [departments, setDepartments] = useState([]);
  const [filters, setFilters] = useState({ search: "", department: "", status: "", active: "" });
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const isAdmin = role === "admin";

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => value && params.set(key, value));
    params.set("limit", "100");
    params.set("offset", String(page * 100));
    try {
      const [records, teams] = await Promise.all([
        api(`/api/employees?${params.toString()}`),
        api("/api/departments"),
      ]);
      setData(records);
      setDepartments(teams);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [filters, page]);

  function updateFilter(key, value) {
    setPage(0);
    setFilters((current) => ({ ...current, [key]: value }));
  }

  useEffect(() => { load(); }, [load]);

  async function changeStatus(employee, status) {
    try {
      await api(`/api/employees/${encodeURIComponent(employee.employee_id)}/status`, {
        method: "PATCH",
        body: jsonBody({ status }),
      });
      setMessage(`Record ${status.toLowerCase()}.`);
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function toggleActive(employee) {
    try {
      await api(`/api/employees/${encodeURIComponent(employee.employee_id)}/active?active=${!employee.active}`, { method: "PATCH" });
      setMessage(employee.active ? "Account deactivated." : "Account activated.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function deleteEmployee(employee) {
    if (!window.confirm(`Permanently delete ${employee.employee_id}? This cannot be undone.`)) return;
    try {
      await api(`/api/employees/${encodeURIComponent(employee.employee_id)}`, { method: "DELETE" });
      setMessage("Employee record deleted.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  function startEdit(employee) {
    setEditing({
      ...EMPTY_EMPLOYEE,
      ...employee,
      salary: String(employee.salary ?? ""),
      password: "",
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function saveEmployee(event) {
    event.preventDefault();
    try {
      const payload = {
        name: editing.name,
        email: editing.email,
        department: editing.department,
        designation: editing.designation,
        phone: editing.phone,
        joining_date: editing.joining_date,
        salary: Number(editing.salary),
      };
      await api(`/api/employees/${encodeURIComponent(editing.employee_id)}`, {
        method: "PUT",
        body: jsonBody(payload),
      });
      setEditing(null);
      setMessage("Employee record updated.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function createEmployee(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    values.salary = Number(values.salary);
    try {
      await api("/api/employees", { method: "POST", body: jsonBody(values) });
      form.reset();
      setMessage("Employee record and login created. The account starts pending HR approval.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function exportCsv() {
    try {
      await downloadFile("/api/reports/employees.csv", "employee_records.csv");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="PEOPLE DIRECTORY"
        title="Employee directory"
        description={isAdmin ? "Search, maintain and review employee records." : "Read-only directory with sensitive compensation details excluded."}
        action={isAdmin ? <button className="secondary-button" onClick={exportCsv}>Export CSV</button> : null}
      />
      {error && <div className="alert error-alert">{error}</div>}
      {message && <div className="alert success-alert">{message}</div>}
      {isAdmin && (
        <section className="card employee-form-card">
          <div className="section-title"><div><div className="eyebrow">HR WORKFLOW</div><h2>{editing ? `Edit ${editing.employee_id}` : "Add employee"}</h2></div>
            {editing && <button className="text-button" onClick={() => setEditing(null)}>Cancel edit</button>}
          </div>
          {editing ? (
            <form key="employee-edit" className="employee-form" onSubmit={saveEmployee}>
              <Field label="Full name" value={editing.name} onChange={(value) => setEditing({ ...editing, name: value })} required />
              <Field label="Work email" type="email" value={editing.email} onChange={(value) => setEditing({ ...editing, email: value })} required />
              <Field label="Department" value={editing.department} onChange={(value) => setEditing({ ...editing, department: value })} list="departments-list" required />
              <Field label="Designation" value={editing.designation} onChange={(value) => setEditing({ ...editing, designation: value })} required />
              <Field label="Phone" value={editing.phone} onChange={(value) => setEditing({ ...editing, phone: value })} />
              <Field label="Joining date" type="date" value={editing.joining_date} onChange={(value) => setEditing({ ...editing, joining_date: value })} />
              <Field label="Annual salary (INR)" type="number" min="0" step="0.01" value={editing.salary} onChange={(value) => setEditing({ ...editing, salary: value })} required />
              <div className="form-actions"><button className="primary-button">Save changes</button></div>
            </form>
          ) : (
            <form key="employee-create" className="employee-form" onSubmit={createEmployee}>
              <Field name="employee_id" label="Employee ID" placeholder="e.g. EMP-2001" required />
              <Field name="name" label="Full name" required />
              <Field name="email" label="Work email" type="email" required />
              <Field name="department" label="Department" list="departments-list" required />
              <Field name="designation" label="Designation" required />
              <Field name="phone" label="Phone" />
              <Field name="joining_date" label="Joining date" type="date" />
              <Field name="salary" label="Annual salary (INR)" type="number" min="0" step="0.01" required />
              <Field name="password" label="Initial password (12+ characters)" type="password" minLength="12" autoComplete="new-password" required />
              <datalist id="departments-list">{departments.map((name) => <option key={name} value={name} />)}</datalist>
              <div className="form-actions"><button className="primary-button">Create employee</button></div>
            </form>
          )}
        </section>
      )}
      <section className="card directory-card">
        <div className="section-title"><div><div className="eyebrow">RECORDS</div><h2>Employees <span className="count-pill">{data.total}</span></h2></div></div>
        <div className="filter-toolbar">
          <input aria-label="Search employees" placeholder="Search name, ID or email" value={filters.search} onChange={(event) => updateFilter("search", event.target.value)} />
          <select aria-label="Filter by department" value={filters.department} onChange={(event) => updateFilter("department", event.target.value)}>
            <option value="">All departments</option>{departments.map((department) => <option key={department}>{department}</option>)}
          </select>
          {isAdmin && <>
            <select aria-label="Filter by status" value={filters.status} onChange={(event) => updateFilter("status", event.target.value)}>
              <option value="">All statuses</option><option>Pending</option><option>Approved</option><option>Rejected</option>
            </select>
            <select aria-label="Filter by active status" value={filters.active} onChange={(event) => updateFilter("active", event.target.value)}>
              <option value="">All accounts</option><option value="true">Active</option><option value="false">Inactive</option>
            </select>
          </>}
        </div>
        <EmployeeTable items={data.items} isAdmin={isAdmin} onEdit={startEdit} onApprove={(employee) => changeStatus(employee, "Approved")} onReject={(employee) => changeStatus(employee, "Rejected")} onToggleActive={toggleActive} onDelete={deleteEmployee} />
        <div className="table-footer"><span>Showing {data.total ? page * 100 + 1 : 0}–{Math.min((page + 1) * 100, data.total)} of {data.total}</span><div><button className="secondary-button" disabled={page === 0} onClick={() => setPage((current) => current - 1)}>Previous</button><button className="secondary-button" disabled={(page + 1) * 100 >= data.total} onClick={() => setPage((current) => current + 1)}>Next</button></div></div>
      </section>
    </>
  );
}

function EmployeeTable({ items, isAdmin, onEdit, onApprove, onReject, onToggleActive, onDelete }) {
  const columns = ["Employee", "Department", "Designation", "Status", "Joined", ...(isAdmin ? ["Salary", "Actions"] : [])];
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {items.map((employee) => (
            <tr key={employee.employee_id}>
              <td><div className="person-cell"><div className="avatar small-avatar">{initials(employee.name)}</div><div><strong>{employee.name}</strong><span>{employee.employee_id}{employee.email ? ` · ${employee.email}` : ""}</span></div></div></td>
              <td>{employee.department}</td>
              <td>{employee.designation}</td>
              <td><span className={`badge ${employee.status.toLowerCase()}`}>{employee.status}</span>{!employee.active && <span className="badge inactive">Inactive</span>}</td>
              <td>{dateLabel(employee.joining_date)}</td>
              {isAdmin && <>
                <td>{money(employee.salary)}</td>
                <td><div className="row-actions">
                  {employee.status === "Pending" && <><button className="mini-action approve" onClick={() => onApprove(employee)}>Approve</button><button className="mini-action reject" onClick={() => onReject(employee)}>Reject</button></>}
                  <button className="mini-action" onClick={() => onEdit(employee)}>Edit</button>
                  <button className="mini-action" onClick={() => onToggleActive(employee)}>{employee.active ? "Deactivate" : "Activate"}</button>
                  <button className="mini-action danger" onClick={() => onDelete(employee)}>Delete</button>
                </div></td>
              </>}
            </tr>
          ))}
          {!items.length && <tr><td className="empty-table" colSpan={isAdmin ? 7 : 5}>No employee records match these filters.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function Field({ label, name, value, onChange, inputRef, ...props }) {
  return <label className="form-field">{label}<input ref={inputRef} name={name} value={value} onChange={onChange ? (event) => onChange(event.target.value) : undefined} {...props} /></label>;
}

function Organization() {
  const [departments, setDepartments] = useState([]);
  const [designations, setDesignations] = useState([]);
  const [department, setDepartment] = useState("");
  const [designation, setDesignation] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [teams, roles] = await Promise.all([api("/api/departments"), api("/api/designations")]);
      setDepartments(teams);
      setDesignations(roles);
    } catch (requestError) { setError(requestError.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function addItem(path, value, clear) {
    try {
      await api(path, { method: "POST", body: jsonBody({ name: value }) });
      clear("");
      setMessage("Organization value added.");
      setError("");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function removeItem(path, name) {
    if (!window.confirm(`Remove "${name}" from organization values?`)) return;
    try {
      await api(`${path}/${encodeURIComponent(name)}`, { method: "DELETE" });
      setMessage(`${name} removed.`);
      setError("");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function renameItem(path, oldName, newName) {
    try {
      await api(`${path}/${encodeURIComponent(oldName)}`, {
        method: "PATCH",
        body: jsonBody({ name: newName }),
      });
      setMessage(`${oldName} renamed to ${newName}.`);
      setError("");
      await load();
      return true;
    } catch (requestError) {
      setError(requestError.message);
      return false;
    }
  }

  return (
    <>
      <PageHeading eyebrow="ORGANIZATION SETUP" title="Departments & designations" description="Maintain the values used to classify employee records." />
      {error && <div className="alert error-alert">{error}</div>}{message && <div className="alert success-alert">{message}</div>}
      <div className="organization-grid">
        <OrganizationList title="Departments" values={departments} value={department} setValue={setDepartment} onAdd={() => addItem("/api/departments", department, setDepartment)} onRemove={(name) => removeItem("/api/departments", name)} onRename={(oldName, newName) => renameItem("/api/departments", oldName, newName)} />
        <OrganizationList title="Designations" values={designations} value={designation} setValue={setDesignation} onAdd={() => addItem("/api/designations", designation, setDesignation)} onRemove={(name) => removeItem("/api/designations", name)} onRename={(oldName, newName) => renameItem("/api/designations", oldName, newName)} />
      </div>
      <p className="quiet-note">Values assigned to employee records cannot be removed. Remove the assignment first, then delete the unused value.</p>
    </>
  );
}

function OrganizationList({ title, values, value, setValue, onAdd, onRemove, onRename }) {
  const [editing, setEditing] = useState("");
  const [editValue, setEditValue] = useState("");

  async function saveRename(event) {
    event.preventDefault();
    if (await onRename(editing, editValue.trim())) {
      setEditing("");
      setEditValue("");
    }
  }

  return (
    <section className="card">
      <div className="section-title"><div><div className="eyebrow">REFERENCE DATA</div><h2>{title}</h2></div><span className="count-pill">{values.length}</span></div>
      <form className="inline-form" onSubmit={(event) => { event.preventDefault(); onAdd(); }}>
        <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={`Add ${title.toLowerCase().slice(0, -1)}`} maxLength="80" required />
        <button className="primary-button">Add</button>
      </form>
      <ul className="value-list">{values.map((item) => <li key={item}>{editing === item ? <form className="rename-form" onSubmit={saveRename}><input value={editValue} onChange={(event) => setEditValue(event.target.value)} maxLength="80" required autoFocus /><button className="mini-action approve">Save</button><button type="button" className="text-button" onClick={() => setEditing("")}>Cancel</button></form> : <><span>{item}</span><div className="value-actions"><button className="text-button" onClick={() => { setEditing(item); setEditValue(item); }}>Rename</button><button className="value-remove" aria-label={`Remove ${item}`} onClick={() => onRemove(item)}>×</button></div></>}</li>)}</ul>
    </section>
  );
}

function AdminRequests() {
  const [requests, setRequests] = useState([]);
  const [registrations, setRegistrations] = useState([]);
  const [registrationTotal, setRegistrationTotal] = useState(0);
  const [registrationPage, setRegistrationPage] = useState(0);
  const [expandedRegistration, setExpandedRegistration] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const load = useCallback(async () => {
    try {
      const [pendingEmployees, pendingProfileRequests] = await Promise.all([
        api(`/api/admin/employee-registrations?limit=100&offset=${registrationPage * 100}`),
        api("/api/admin/profile-requests?status=Pending"),
      ]);
      setRegistrations(pendingEmployees.items);
      setRegistrationTotal(pendingEmployees.total);
      setRequests(pendingProfileRequests);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [registrationPage]);
  useEffect(() => { load(); }, [load]);

  async function reviewRegistration(item, status) {
    try {
      await api(`/api/employees/${encodeURIComponent(item.employee_id)}/status`, {
        method: "PATCH",
        body: jsonBody({ status }),
      });
      setMessage(`${item.name}'s registration was ${status.toLowerCase()}.`);
      setExpandedRegistration("");
      if (registrations.length === 1 && registrationPage > 0) {
        setRegistrationPage((page) => page - 1);
      } else {
        await load();
      }
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function reviewProfileRequest(item, status) {
    try {
      await api(`/api/admin/profile-requests/${item.request_id}`, { method: "PATCH", body: jsonBody({ status }) });
      setMessage(`Profile changes were ${status.toLowerCase()}.`);
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  return (
    <>
      <PageHeading eyebrow="HR REVIEW QUEUE" title="Pending requests" description="Review employee registrations and employee-submitted profile changes." action={<button className="secondary-button" onClick={load}>Refresh</button>} />
      {error && <div className="alert error-alert" role="alert">{error}</div>}
      {message && <div className="alert success-alert" role="status">{message}</div>}
      <section className="card">
        <div className="section-title">
          <div><div className="eyebrow">ACCOUNT APPROVAL</div><h2>Pending employee registrations <span className="count-pill">{registrationTotal}</span></h2></div>
        </div>
        <p className="quiet-note">Employees cannot sign in until an Admin approves their registration.</p>
        {registrations.length ? (
          <>
            <div className="request-list">{registrations.map((item) => {
              const expanded = expandedRegistration === item.employee_id;
              return (
                <article className="request-card" key={item.employee_id}>
                  <div className="request-heading">
                    <div><strong>{item.name}</strong><span>{item.employee_id} · registered {dateLabel(item.created_at)}</span></div>
                    <span className="badge pending">Pending</span>
                  </div>
                  {expanded && (
                    <dl className="registration-details">
                      <div><dt>Email</dt><dd>{item.email}</dd></div>
                      <div><dt>Phone</dt><dd>{item.phone}</dd></div>
                      <div><dt>Department</dt><dd>{item.department}</dd></div>
                      <div><dt>Designation</dt><dd>{item.designation}</dd></div>
                      <div><dt>Joining date</dt><dd>{dateLabel(item.joining_date)}</dd></div>
                    </dl>
                  )}
                  <div className="row-actions">
                    <button
                      type="button"
                      className="mini-action"
                      onClick={() => setExpandedRegistration(expanded ? "" : item.employee_id)}
                    >
                      {expanded ? "Hide details" : "Review details"}
                    </button>
                    {expanded && <>
                      <button type="button" className="mini-action approve" onClick={() => reviewRegistration(item, "Approved")}>Approve</button>
                      <button type="button" className="mini-action reject" onClick={() => reviewRegistration(item, "Rejected")}>Reject</button>
                    </>}
                  </div>
                </article>
              );
            })}</div>
            <div className="table-footer">
              <span>Showing {registrationPage * 100 + 1}–{Math.min((registrationPage + 1) * 100, registrationTotal)} of {registrationTotal}</span>
              <div>
                <button className="secondary-button" disabled={registrationPage === 0} onClick={() => setRegistrationPage((page) => page - 1)}>Previous</button>
                <button className="secondary-button" disabled={(registrationPage + 1) * 100 >= registrationTotal} onClick={() => setRegistrationPage((page) => page + 1)}>Next</button>
              </div>
            </div>
          </>
        ) : <EmptyState title="No pending registrations" text="New employee account requests will appear here." />}
      </section>
      <section className="card">
        <div className="section-title"><div><div className="eyebrow">PROFILE REVIEW</div><h2>Profile update requests</h2></div></div>
        <p className="quiet-note">Employee-submitted changes are applied only after HR approval.</p>
        {requests.length ? <div className="request-list">{requests.map((item) => (
          <article className="request-card" key={item.request_id}>
            <div className="request-heading"><div><strong>{item.name}</strong><span>{item.employee_id} · submitted {dateLabel(item.created_at)}</span></div><span className="badge pending">Pending</span></div>
            <dl>{Object.entries(item.changes).map(([field, value]) => <div key={field}><dt>{field === "name" ? "Full name" : "Phone"}</dt><dd>{value || "—"}</dd></div>)}</dl>
            <div className="row-actions"><button className="mini-action approve" onClick={() => reviewProfileRequest(item, "Approved")}>Approve changes</button><button className="mini-action reject" onClick={() => reviewProfileRequest(item, "Rejected")}>Reject</button></div>
          </article>
        ))}</div> : <EmptyState title="No pending requests" text="New employee profile change requests will appear here." />}
      </section>
    </>
  );
}

function EmployeeRequests() {
  const [requests, setRequests] = useState([]);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const load = useCallback(() => api("/api/me/profile-requests").then(setRequests).catch((requestError) => setError(requestError.message)), []);
  useEffect(() => { load(); }, [load]);

  async function submit(event) {
    event.preventDefault();
    const changes = {};
    if (name.trim()) changes.name = name.trim();
    if (phone.trim()) changes.phone = phone.trim();
    try {
      await api("/api/me/profile-requests", { method: "POST", body: jsonBody(changes) });
      setName("");
      setPhone("");
      setMessage("Your request has been submitted for HR review.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  return (
    <>
      <PageHeading eyebrow="EMPLOYEE SELF-SERVICE" title="Profile update requests" description="Request a change to your name or contact phone. HR will review it before your record changes." />
      {error && <div className="alert error-alert">{error}</div>}{message && <div className="alert success-alert">{message}</div>}
      <div className="overview-grid">
        <section className="card"><div className="section-title"><div><div className="eyebrow">NEW REQUEST</div><h2>Request a profile change</h2></div></div>
          <form className="stack-form" onSubmit={submit}><Field label="New full name (optional)" value={name} onChange={setName} maxLength="100" /><Field label="New phone (optional)" value={phone} onChange={setPhone} maxLength="30" /><button className="primary-button">Submit for review</button></form>
        </section>
        <section className="card"><div className="section-title"><div><div className="eyebrow">HISTORY</div><h2>Your requests</h2></div></div>
          {requests.length ? <div className="request-history">{requests.map((item) => <div className="history-item" key={item.request_id}><div><strong>{Object.entries(item.changes).map(([key, value]) => `${key}: ${value}`).join(" · ")}</strong><span>{dateLabel(item.created_at)}</span></div><span className={`badge ${item.status.toLowerCase()}`}>{item.status}</span></div>)}</div> : <EmptyState title="No requests yet" text="Your submitted change requests will be listed here." />}
        </section>
      </div>
    </>
  );
}

function EmployeeHome({ user, onOpenRequests, onUserUpdate }) {
  const employee = user.employee || {};
  const [completed, setCompleted] = useState(Boolean(employee.onboarding_complete));
  return (
    <>
      <PageHeading eyebrow="EMPLOYEE SELF-SERVICE" title={`Welcome, ${employee.name || user.username}`} description="Your personal employee record and onboarding status." />
      <div className="metric-grid employee-metrics">
        <Metric label="Account status" value={employee.active ? "Active" : "Inactive"} note={employee.status || "Pending"} />
        <Metric label="Onboarding" value={completed ? "Complete" : "In progress"} note="Update your onboarding checklist" />
        <Metric label="Department" value={employee.department || "—"} note={employee.designation || "—"} />
      </div>
      <section className="card onboarding-card">
        <div><div className="eyebrow">NEXT STEP</div>        <h2>{completed ? "Your onboarding is complete" : "Complete your onboarding"}</h2><p>{completed ? "Your account is set up. You can still submit profile corrections for HR review." : "Review the personal details in your profile and confirm that your onboarding information is complete."}</p></div>
        {!completed && <OnboardingButton onComplete={async () => {
          const updatedUser = await api("/api/auth/me");
          onUserUpdate(updatedUser);
          setCompleted(Boolean(updatedUser.employee?.onboarding_complete));
        }} />}
        <button className="secondary-button" onClick={onOpenRequests}>Request profile update</button>
      </section>
    </>
  );
}

function OnboardingButton({ onComplete }) {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function complete() {
    try {
      const result = await api("/api/me/onboarding", { method: "PATCH" });
      await onComplete();
      setMessage(result.message);
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }
  return <div className="onboarding-action">{error && <span className="inline-error">{error}</span>}{message && <span className="inline-success">{message}</span>}<button className="primary-button" onClick={complete}>Mark onboarding complete</button></div>;
}

function EmployeeProfile({ user }) {
  const employee = user.employee || {};
  return (
    <>
      <PageHeading eyebrow="MY RECORD" title="Personal profile" description="Review your current employee information. Contact HR to correct information in your record." />
      <section className="card profile-card">
        <div className="profile-identity"><div className="avatar profile-avatar">{initials(employee.name)}</div><div><h2>{employee.name}</h2><span>{employee.employee_id} · {employee.designation}</span></div><span className={`badge ${employee.status?.toLowerCase()}`}>{employee.status}</span></div>
        <dl className="profile-grid">
          <div><dt>Work email</dt><dd>{employee.email}</dd></div>
          <div><dt>Department</dt><dd>{employee.department}</dd></div>
          <div><dt>Phone</dt><dd>{employee.phone || "Not provided"}</dd></div>
          <div><dt>Joining date</dt><dd>{dateLabel(employee.joining_date)}</dd></div>
          <div><dt>Account</dt><dd>{employee.active ? "Active" : "Inactive"}</dd></div>
          <div><dt>Onboarding</dt><dd>{employee.onboarding_complete ? "Complete" : "In progress"}</dd></div>
        </dl>
      </section>
    </>
  );
}

function PasswordChange() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function submit(event) {
    event.preventDefault();
    try {
      const result = await api("/api/auth/change-password", { method: "POST", body: jsonBody({ current_password: current, new_password: next }) });
      saveToken(result.access_token);
      setCurrent("");
      setNext("");
      setMessage("Your password has been updated.");
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }
  return (
    <>
      <PageHeading eyebrow="ACCOUNT SECURITY" title="Change password" description="Choose a unique password with at least 12 characters." />
      {error && <div className="alert error-alert">{error}</div>}{message && <div className="alert success-alert">{message}</div>}
      <section className="card narrow-card"><form className="stack-form" onSubmit={submit}><Field label="Current password" type="password" value={current} onChange={setCurrent} autoComplete="current-password" required /><Field label="New password" type="password" minLength="12" value={next} onChange={setNext} autoComplete="new-password" required /><button className="primary-button">Update password</button></form></section>
    </>
  );
}

function AuditTrail() {
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api("/api/admin/audit-logs?limit=200").then(setLogs).catch((requestError) => setError(requestError.message));
  }, []);
  return (
    <>
      <PageHeading eyebrow="GOVERNANCE" title="Audit trail" description="Recent administrative and employee self-service actions." />
      {error && <div className="alert error-alert">{error}</div>}
      <section className="card"><div className="table-scroll"><table className="data-table">
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Record</th><th>Details</th></tr></thead>
        <tbody>{logs.map((log) => <tr key={log.audit_id}><td>{dateLabel(log.created_at)}</td><td>{log.actor_username}</td><td>{log.action}</td><td>{log.target_id}</td><td>{log.details || "—"}</td></tr>)}{!logs.length && <tr><td className="empty-table" colSpan="5">No audit activity recorded.</td></tr>}</tbody>
      </table></div></section>
    </>
  );
}

function Benchmark() {
  const [result, setResult] = useState(null);
  const [buckets, setBuckets] = useState([]);
  const [hashStats, setHashStats] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const loadStructure = useCallback(async () => {
    try {
      const [table, stats] = await Promise.all([
        api("/api/hash-table"),
        api("/api/hash-table/stats"),
      ]);
      setBuckets(table);
      setHashStats(stats);
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }, []);
  useEffect(() => { loadStructure(); }, [loadStructure]);
  async function run() {
    setLoading(true);
    setError("");
    try { setResult(await api("/api/benchmark?repetitions=100")); }
    catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }
  return (
    <>
      <PageHeading eyebrow="DATA STRUCTURES" title="Hash map analysis" description="Run lookup measurements against current SQLite employee records." action={<button className="primary-button" onClick={run} disabled={loading}>{loading ? "Measuring…" : "Run measurement"}</button>} />
      {error && <div className="alert error-alert">{error}</div>}
      {hashStats && <div className="metric-grid hash-metrics">
        <Metric label="Indexed employees" value={hashStats.size} note="Current in-memory map" />
        <Metric label="Bucket capacity" value={hashStats.capacity} note={`${hashStats.resizes} dynamic resize${hashStats.resizes === 1 ? "" : "s"}`} />
        <Metric label="Load factor" value={hashStats.load_factor} note="Records ÷ capacity" />
        <Metric label="Collision events" value={hashStats.collisions} note={`Maximum chain: ${hashStats.max_chain_length}`} />
      </div>}
      <div className="analysis-grid">
        <section className="card"><div className="section-title"><div><div className="eyebrow">IN-MEMORY INDEX</div><h2>Separate chaining buckets</h2></div><button className="text-button" onClick={loadStructure}>Refresh</button></div><BucketList buckets={buckets} /></section>
        <BenchmarkResults result={result} />
      </div>
    </>
  );
}

function BucketList({ buckets }) {
  return buckets.length ? <div className="bucket-grid">{buckets.filter((bucket) => bucket.count).map((bucket) => <div className="bucket-item" key={bucket.index}><strong>Index {bucket.index}</strong><span>{bucket.employee_ids.join(" → ")}</span></div>)}</div> : <EmptyState title="No bucket view loaded" text="Load the current table to inspect occupied buckets." />;
}

function BenchmarkResults({ result }) {
  return (
    <section className="card"><div className="section-title"><div><div className="eyebrow">MEASURED RESULTS</div><h2>Lookup comparison</h2></div></div>
      {result ? result.results.length ? <>
        <p className="quiet-note">{result.record_count} records · {result.repetitions} lookups</p>
        <div className="benchmark-list">{result.results.map((row) => <div className="benchmark-item" key={row.method}><div><strong>{row.method}</strong><span>{row.collisions} recorded collisions · load {row.load_factor ?? "n/a"}</span></div><b>{row.time_ms} ms</b></div>)}</div>
        <p className="quiet-note">{result.note}</p>
      </> : <EmptyState title="No records to measure" text={result.note} /> : <EmptyState title="Measurement not run" text="Timings are measured on demand and depend on this dataset and machine." />}
    </section>
  );
}

function WorkforceReport() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { api("/api/reports/workforce").then(setReport).catch((requestError) => setError(requestError.message)); }, []);
  return (
    <>
      <PageHeading eyebrow="MANAGEMENT REPORT" title="Department workforce report" description="Aggregated headcount and review status. Individual compensation and personal contact data are not included." />
      {error && <div className="alert error-alert">{error}</div>}
      <section className="card"><div className="table-scroll"><table className="data-table"><thead><tr><th>Department</th><th>Total records</th><th>Active & approved</th><th>Pending review</th></tr></thead><tbody>
        {report?.departments?.map((row) => <tr key={row.department}><td><strong>{row.department}</strong></td><td>{row.total}</td><td>{row.active_approved}</td><td>{row.pending}</td></tr>)}
        {!report?.departments?.length && <tr><td className="empty-table" colSpan="4">No workforce report data available.</td></tr>}
      </tbody></table></div></section>
    </>
  );
}

function EmptyState({ title, text }) {
  return <div className="empty-state"><div className="empty-icon">—</div><strong>{title}</strong><span>{text}</span></div>;
}

export default App;
