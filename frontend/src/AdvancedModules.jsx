import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadFile, jsonBody } from "./api.js";

const ADMIN_MODULES = [
  ["onboarding", "Onboarding progress"],
  ["documents", "Document review"],
  ["timeline", "Employee timeline"],
  ["leave", "Leave management"],
  ["attendance", "Attendance"],
  ["performance", "Performance"],
  ["organization", "Organization chart"],
  ["id-card", "Employee ID cards"],
  ["imports", "Import and reports"],
  ["hash-lab", "Hash map lab"],
];

const EMPLOYEE_MODULES = [
  ["onboarding", "My onboarding"],
  ["documents", "My documents"],
  ["timeline", "My timeline"],
  ["leave", "Leave"],
  ["attendance", "Attendance"],
  ["performance", "Performance"],
  ["organization", "Organization chart"],
  ["id-card", "My ID card"],
];

function PageHeading({ title, description, action }) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1><p>{description}</p></div>
      {action}
    </div>
  );
}

function Field({ label, value, onChange, ...props }) {
  return (
    <label className="form-field">
      {label}
      <input
        value={value ?? ""}
        onChange={(event) => onChange?.(event.target.value)}
        {...props}
      />
    </label>
  );
}

function Notice({ error, message }) {
  return (
    <>
      {error && <div className="alert error-alert" role="alert">{error}</div>}
      {message && <div className="alert success-alert" role="status">{message}</div>}
    </>
  );
}

function useRequestState() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  return { loading, setLoading, error, setError, message, setMessage };
}

export function AdvancedModules({
  role,
  user,
  initialModule = "",
  relatedId = "",
  relatedType = "",
  initialEmployeeId = "",
  onClearTarget,
}) {
  const [selected, setSelected] = useState(initialModule);
  const modules = role === "admin" ? ADMIN_MODULES : EMPLOYEE_MODULES;
  const selectedLabel = modules.find(([key]) => key === selected)?.[1];

  if (!selected) {
    return (
      <>
        <PageHeading
          title={role === "admin" ? "HR tools" : "Employee services"}
          description="Choose a service. Available actions depend on your account permissions."
        />
        <div className="module-grid">
          {modules.map(([key, label]) => (
            <button key={key} className="module-card" onClick={() => setSelected(key)}>
              <strong>{label}</strong>
              <span>Open service</span>
            </button>
          ))}
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeading
        title={selectedLabel}
        description="Use this workspace to review and manage the related employee information."
        action={<button className="secondary-button" onClick={() => { setSelected(""); onClearTarget?.(); }}>All services</button>}
      />
      {relatedId && <p className="alert success-alert">Opened from a {relatedType.replace("_", " ")} notification · reference {relatedId}</p>}
      {selected === "onboarding" && <OnboardingModule role={role} initialEmployeeId={initialEmployeeId} />}
      {selected === "documents" && <DocumentsModule role={role} user={user} relatedId={relatedId} />}
      {selected === "timeline" && <TimelineModule role={role} user={user} initialEmployeeId={initialEmployeeId} />}
      {selected === "leave" && <LeaveModule role={role} user={user} relatedId={relatedId} />}
      {selected === "attendance" && <AttendanceModule role={role} user={user} relatedId={relatedId} />}
      {selected === "performance" && <PerformanceModule role={role} user={user} relatedId={relatedId} initialEmployeeId={initialEmployeeId} />}
      {selected === "organization" && <OrganizationChartModule role={role} user={user} />}
      {selected === "id-card" && <IdCardModule role={role} user={user} />}
      {selected === "imports" && <ImportReportModule />}
      {selected === "hash-lab" && <HashMapLab />}
    </>
  );
}

export function NotificationCenter({ onUnreadChange, onOpenRelated }) {
  const [data, setData] = useState({ items: [], unread_count: 0, total: 0 });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api("/api/notifications?limit=100");
      setData(result);
      onUnreadChange(result.unread_count);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, [onUnreadChange]);

  useEffect(() => { load(); }, [load]);

  async function markRead(id, read) {
    try {
      await api(`/api/notifications/${id}`, {
        method: "PATCH",
        body: jsonBody({ read }),
      });
      const currentItem = data.items.find((item) => item.notification_id === id);
      if (currentItem && Boolean(currentItem.is_read) !== read) {
        const unreadCount = Math.max(0, data.unread_count + (read ? -1 : 1));
        setData((current) => ({
          ...current,
          unread_count: unreadCount,
          items: current.items.map((item) => item.notification_id === id
            ? { ...item, is_read: read, read_at: read ? new Date().toISOString() : null }
            : item),
        }));
        onUnreadChange(unreadCount);
      }
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function markAllRead() {
    try {
      await api("/api/notifications/read-all", { method: "PATCH" });
      setData((current) => ({
        ...current,
        unread_count: 0,
        items: current.items.map((item) => ({ ...item, is_read: true })),
      }));
      onUnreadChange(0);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const result = await api(`/api/notifications?limit=100&offset=${data.items.length}`);
      setData((current) => ({
        ...result,
        items: [...current.items, ...result.items],
      }));
      onUnreadChange(result.unread_count);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoadingMore(false);
    }
  }

  function openRelated(item) {
    onOpenRelated(item);
  }

  return (
    <>
      <PageHeading
        title="Notifications"
        description={`${data.unread_count} unread · ${data.total} total`}
        action={<button className="secondary-button" onClick={markAllRead} disabled={!data.unread_count}>Mark all read</button>}
      />
      {error && <Notice error={error} />}
      {loading ? <p className="quiet-note">Loading notifications…</p> : data.items.length ? (
        <div className="notification-list">
          {data.items.map((item) => (
            <article className={`notification-item ${item.is_read ? "read" : "unread"}`} key={item.notification_id}>
              <div className="notification-copy">
                <div className="notification-meta">
                  <span className={`badge ${item.priority === "high" ? "rejected" : item.priority === "low" ? "" : "approved"}`}>{item.priority}</span>
                  <span>{item.category}</span>
                  <time>{item.created_at}</time>
                </div>
                <strong>{item.title}</strong>
                <p>{item.message}</p>
                {item.related_type && <button className="text-button" onClick={() => openRelated(item)}>Open related item</button>}
              </div>
              <button className="secondary-button" onClick={() => markRead(item.notification_id, !item.is_read)}>
                {item.is_read ? "Mark unread" : "Mark read"}
              </button>
            </article>
          ))}
        </div>
      ) : <EmptyState title="No notifications" text="Updates related to your account will appear here." />}
      {!loading && data.items.length < data.total && (
        <div className="form-actions"><button className="secondary-button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load older notifications"}</button></div>
      )}
    </>
  );
}

function OnboardingModule({ role, initialEmployeeId }) {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState(initialEmployeeId || "");
  const [drafts, setDrafts] = useState({});
  const [dirtyTask, setDirtyTask] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const result = role === "admin"
        ? await api(`/api/admin/onboarding?search=${encodeURIComponent(search)}`)
        : await api("/api/onboarding/me");
      setData(result);
      if (role !== "admin") {
        setDrafts(Object.fromEntries(result.tasks.map((task) => [task.task_key, task.response || {}])));
      }
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [role, search]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (role !== "employee" || !dirtyTask) return undefined;
    const timer = window.setTimeout(async () => {
      try {
        await api(`/api/onboarding/tasks/${dirtyTask}`, {
          method: "PUT",
          body: jsonBody({ response: drafts[dirtyTask] || {}, submit: false }),
        });
        setMessage("Onboarding progress saved.");
        setError("");
        setDirtyTask("");
      } catch (requestError) {
        setError(requestError.message);
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [role, dirtyTask, drafts]);

  function edit(task, key, value) {
    setDrafts((current) => ({
      ...current,
      [task.task_key]: { ...(current[task.task_key] || {}), [key]: value },
    }));
    setDirtyTask(task.task_key);
  }

  async function submitTask(task) {
    setBusy(task.task_key);
    try {
      await api(`/api/onboarding/tasks/${task.task_key}`, {
        method: "PUT",
        body: jsonBody({ response: drafts[task.task_key] || {}, submit: true }),
      });
      setMessage("Task submitted for Admin review.");
      setDirtyTask("");
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy("");
    }
  }

  async function reviewTask(employeeId, taskKey, status) {
    setBusy(`${employeeId}-${taskKey}`);
    try {
      await api(`/api/admin/onboarding/${encodeURIComponent(employeeId)}/${taskKey}`, {
        method: "PATCH",
        body: jsonBody({ status, note: "" }),
      });
      setMessage(`Onboarding task ${status}.`);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <Notice error={error} message={message} />
      {role === "admin" ? (
        <>
          <div className="module-toolbar">
            <Field label="Search employees" value={search} onChange={setSearch} placeholder="Name or employee ID" />
            <button className="secondary-button" onClick={load}>Refresh</button>
          </div>
          {data?.items?.length ? data.items.map((employee) => (
            <section className="card module-card-section" key={employee.employee_id}>
              <div className="section-title">
                <div><strong>{employee.name} · {employee.employee_id}</strong><p>{employee.department} · {employee.designation}</p></div>
                <span className="badge">{employee.completion_percentage}% complete</span>
              </div>
              <div className="task-list">
                {employee.tasks.map((task) => (
                  <article className="task-row" key={task.task_key}>
                    <div><strong>{task.title}</strong><span>{task.status}{task.review_note ? ` · ${task.review_note}` : ""}</span></div>
                    {task.status === "submitted" && <div className="row-actions">
                      <button className="mini-action approve" disabled={Boolean(busy)} onClick={() => reviewTask(employee.employee_id, task.task_key, "approved")}>Approve</button>
                      <button className="mini-action reject" disabled={Boolean(busy)} onClick={() => reviewTask(employee.employee_id, task.task_key, "rejected")}>Reject</button>
                    </div>}
                  </article>
                ))}
              </div>
            </section>
          )) : <EmptyState title="No onboarding records" text="Employee progress will appear here once they begin onboarding." />}
        </>
      ) : data ? (
        <>
          <section className="card progress-card">
            <strong>{data.completion_percentage}% complete</strong>
            <span>{data.completed_tasks} of {data.required_tasks} required tasks approved</span>
            <div className="progress-track"><div style={{ width: `${data.completion_percentage}%` }} /></div>
          </section>
          <section className="card task-list">
            {data.tasks.map((task) => {
              const response = drafts[task.task_key] || {};
              const isApproved = task.status === "approved";
              return (
                <article className="task-row onboarding-task" key={task.task_key}>
                  <div className="section-title">
                    <div><strong>{task.title}</strong><p>{task.description}</p></div>
                    <span className={`badge ${task.status === "approved" ? "approved" : task.status === "rejected" ? "rejected" : "pending"}`}>{task.status}</span>
                  </div>
                  {task.task_key === "emergency_contact" ? (
                    <div className="employee-form">
                      {["name", "phone", "relationship"].map((key) => (
                        <Field key={key} label={key.replace("_", " ")} value={response[key] || ""} onChange={(value) => edit(task, key, value)} disabled={isApproved} />
                      ))}
                    </div>
                  ) : task.task_key === "policy_acknowledgement" ? (
                    <div className="stack-form">
                      <Field label="Policy version" value={response.policy_version || ""} onChange={(value) => edit(task, "policy_version", value)} disabled={isApproved} />
                      <label className="checkbox-field"><input type="checkbox" checked={Boolean(response.acknowledged)} onChange={(event) => edit(task, "acknowledged", event.target.checked)} disabled={isApproved} /> I acknowledge the current company policies.</label>
                    </div>
                  ) : (
                    <label className="checkbox-field"><input type="checkbox" checked={Boolean(response.confirmed)} onChange={(event) => edit(task, "confirmed", event.target.checked)} disabled={isApproved} /> I confirm this information.</label>
                  )}
                  {task.review_note && <p className="quiet-note">Admin note: {task.review_note}</p>}
                  {!isApproved && task.status !== "submitted" && <button className="primary-button" disabled={Boolean(busy)} onClick={() => submitTask(task)}>{busy === task.task_key ? "Submitting…" : "Submit for review"}</button>}
                </article>
              );
            })}
          </section>
        </>
      ) : <p className="quiet-note">Loading onboarding tasks…</p>}
    </>
  );
}

function DocumentsModule({ role, user, relatedId }) {
  const [documents, setDocuments] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [employeeId, setEmployeeId] = useState(user.employee_id || "");
  const [category, setCategory] = useState("resume");
  const [replacementDocumentId, setReplacementDocumentId] = useState("");
  const [file, setFile] = useState(null);
  const [includeHistory, setIncludeHistory] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ include_history: String(includeHistory) });
      if (role === "admin" && employeeId) params.set("employee_id", employeeId);
      const [list, directory] = await Promise.all([
        api(`/api/documents?${params}`),
        role === "admin" ? api("/api/employees?limit=500") : Promise.resolve(null),
      ]);
      setDocuments(list);
      if (directory) setEmployees(directory.items);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, [role, employeeId, includeHistory]);

  useEffect(() => { load(); }, [load]);

  async function upload(event) {
    event.preventDefault();
    if (!file) return;
    const form = event.currentTarget;
    const body = new FormData();
    body.set("category", category);
    body.set("file", file);
    if (role === "admin") body.set("employee_id", employeeId);
    if (replacementDocumentId) body.set("replaces_document_id", replacementDocumentId);
    try {
      await api("/api/documents", { method: "POST", body });
      setMessage("Document uploaded securely and queued for verification.");
      setReplacementDocumentId("");
      setFile(null);
      form.reset();
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function review(item, status) {
    try {
      await api(`/api/admin/documents/${item.document_id}`, {
        method: "PATCH",
        body: jsonBody({ status, note: "" }),
      });
      setMessage(`Document ${status.toLowerCase()}.`);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <>
      <Notice error={error} message={message} />
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Upload a document for an employee" : "Upload a document"}</h2>
        <form className="module-toolbar" onSubmit={upload}>
          {role === "admin" && <label className="form-field">Employee
            <select value={employeeId} onChange={(event) => {
              setEmployeeId(event.target.value);
              setReplacementDocumentId("");
            }} required>
              <option value="">Choose employee</option>
              {employees.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name} · {employee.employee_id}</option>)}
            </select>
          </label>}
          <label className="form-field">Category
            <select value={category} onChange={(event) => {
              setCategory(event.target.value);
              setReplacementDocumentId("");
            }}>
              {["resume", "identity", "education", "offer_letter", "photo", "other"].map((value) => <option key={value} value={value}>{value.replace("_", " ")}</option>)}
            </select>
          </label>
          <label className="form-field">Replace current document (optional)
            <select value={replacementDocumentId} onChange={(event) => setReplacementDocumentId(event.target.value)}>
              <option value="">Upload as a new document</option>
              {documents.filter((item) => item.is_current && item.category === category && (role !== "admin" || item.employee_id === employeeId)).map((item) => (
                <option key={item.document_id} value={item.document_id}>{item.original_name} · v{item.version}</option>
              ))}
            </select>
          </label>
          <label className="form-field">PDF, JPEG or PNG (maximum configured size)
            <input type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" onChange={(event) => setFile(event.target.files?.[0] || null)} required />
          </label>
          <button className="primary-button" disabled={!file || (role === "admin" && !employeeId)}>Upload securely</button>
        </form>
        <p className="quiet-note">Files are stored privately and downloaded only through authenticated API access.</p>
      </section>
      <section className="card module-card-section">
        <div className="section-title">
          <h2>Documents</h2>
          <label className="checkbox-field"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} /> Include versions</label>
        </div>
        {loading ? <p className="quiet-note">Loading documents…</p> : documents.length ? (
          <div className="table-scroll"><table className="data-table">
            <thead><tr><th>File</th><th>Owner</th><th>Category</th><th>Version</th><th>Status</th><th>Uploaded</th><th>Actions</th></tr></thead>
            <tbody>{documents.map((item) => (
              <tr className={String(item.document_id) === relatedId ? "related-row" : ""} key={item.document_id}>
                <td>{item.original_name}</td><td>{item.employee_id}</td><td>{item.category}</td><td>{item.version}</td><td>{item.status}</td><td>{item.created_at}</td>
                <td><div className="row-actions">
                  <button className="mini-action" onClick={() => downloadFile(`/api/documents/${item.document_id}/download`, item.original_name).catch((requestError) => setError(requestError.message))}>Download</button>
                  {role === "admin" && item.status === "Pending" && Boolean(item.is_current) && <>
                    <button className="mini-action approve" onClick={() => review(item, "Verified")}>Verify</button>
                    <button className="mini-action reject" onClick={() => review(item, "Rejected")}>Reject</button>
                  </>}
                </div></td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <EmptyState title="No documents" text="Uploaded documents and verification history will appear here." />}
      </section>
    </>
  );
}

function TimelineModule({ role, user, initialEmployeeId }) {
  const [employeeId, setEmployeeId] = useState(initialEmployeeId || user.employee_id || "");
  const [employees, setEmployees] = useState([]);
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (role !== "admin") return;
    api("/api/employees?limit=500")
      .then((result) => setEmployees(result.items))
      .catch((requestError) => setError(requestError.message));
  }, [role]);

  const load = useCallback(async () => {
    if (!employeeId) return;
    setLoading(true);
    try {
      const result = await api(`/api/employees/${encodeURIComponent(employeeId)}/timeline`);
      setItems(result.items);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, [employeeId]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <Notice error={error} />
      {role === "admin" && <label className="form-field module-select">Employee
        <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}>
          <option value="">Choose employee</option>
          {employees.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name} · {employee.employee_id}</option>)}
        </select>
      </label>}
      {loading ? <p className="quiet-note">Loading timeline…</p> : items.length ? (
        <div className="timeline-list">{items.map((item) => (
          <article className="timeline-event" key={item.event_id}>
            <span className="timeline-dot" />
            <div><strong>{item.summary}</strong><span>{item.created_at} · {item.actor_username} · {item.event_type}</span>
              {(Object.keys(item.previous_values).length > 0 || Object.keys(item.new_values).length > 0) && (
                <details><summary>View changes</summary><pre>{JSON.stringify({ previous: item.previous_values, updated: item.new_values }, null, 2)}</pre></details>
              )}
            </div>
          </article>
        ))}</div>
      ) : <EmptyState title="No history yet" text="Employment and profile events will be recorded here." />}
    </>
  );
}

function LeaveModule({ role, user, relatedId }) {
  const [policies, setPolicies] = useState([]);
  const [balances, setBalances] = useState(null);
  const [requests, setRequests] = useState([]);
  const [calendar, setCalendar] = useState([]);
  const [calendarMonth, setCalendarMonth] = useState(new Date().toISOString().slice(0, 7));
  const [leaveType, setLeaveType] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [newPolicy, setNewPolicy] = useState({ leave_type: "", annual_allowance: "0", count_weekends: false });
  const [rejectingRequestId, setRejectingRequestId] = useState(null);
  const [rejectionReason, setRejectionReason] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [year, monthNumber] = calendarMonth.split("-").map(Number);
      const lastDay = new Date(year, monthNumber, 0).getDate();
      const [available, balance, requestsData, calendarData] = await Promise.all([
        api("/api/leave/policies"),
        api("/api/leave/balances"),
        api("/api/leave/requests"),
        api(`/api/leave/calendar?start_date=${calendarMonth}-01&end_date=${calendarMonth}-${String(lastDay).padStart(2, "0")}`),
      ]);
      setPolicies(available);
      setBalances(balance);
      setRequests(requestsData.items);
      setCalendar(calendarData);
      if (!leaveType && available.length) setLeaveType(available.find((policy) => policy.active)?.leave_type || "");
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }, [calendarMonth, leaveType]);

  useEffect(() => { load(); }, [load]);

  async function apply(event) {
    event.preventDefault();
    try {
      await api("/api/leave/requests", {
        method: "POST",
        body: jsonBody({ leave_type: leaveType, start_date: startDate, end_date: endDate, reason }),
      });
      setMessage("Leave request submitted.");
      setReason("");
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function decide(item, status, rejection_reason = "") {
    try {
      await api(`/api/leave/requests/${item.leave_request_id}`, {
        method: "PATCH",
        body: jsonBody({ status, rejection_reason }),
      });
      setMessage(`Leave request ${status.toLowerCase()}.`);
      await load();
      setRejectingRequestId(null);
      setRejectionReason("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  function submitRejection(event, item) {
    event.preventDefault();
    if (!rejectionReason.trim()) {
      setError("A rejection reason is required.");
      return;
    }
    decide(item, "Rejected", rejectionReason.trim());
  }

  async function savePolicy(policy) {
    try {
      await api(`/api/admin/leave/policies/${encodeURIComponent(policy.leave_type)}`, {
        method: "PUT",
        body: jsonBody({
          annual_allowance: Number(policy.annual_allowance),
          count_weekends: Boolean(policy.count_weekends),
          active: Boolean(policy.active),
        }),
      });
      setMessage(`${policy.leave_type} policy saved.`);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function addPolicy(event) {
    event.preventDefault();
    try {
      await api(`/api/admin/leave/policies/${encodeURIComponent(newPolicy.leave_type)}`, {
        method: "PUT",
        body: jsonBody({
          annual_allowance: Number(newPolicy.annual_allowance),
          count_weekends: Boolean(newPolicy.count_weekends),
          active: true,
        }),
      });
      setNewPolicy({ leave_type: "", annual_allowance: "0", count_weekends: false });
      setMessage("Leave policy added.");
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  const personalBalances = balances?.balances || [];

  return (
    <>
      <Notice error={error} message={message} />
      {role === "admin" && (
        <section className="card module-card-section">
          <h2>Configurable leave policies</h2>
          {policies.map((policy) => (
            <PolicyEditor key={policy.leave_type} policy={policy} onSave={savePolicy} />
          ))}
          <form className="module-toolbar" onSubmit={addPolicy}>
            <Field label="Leave type" value={newPolicy.leave_type} onChange={(value) => setNewPolicy({ ...newPolicy, leave_type: value })} required />
            <Field label="Annual allowance (days)" type="number" min="0" max="366" step="0.5" value={newPolicy.annual_allowance} onChange={(value) => setNewPolicy({ ...newPolicy, annual_allowance: value })} required />
            <button className="primary-button">Add policy</button>
          </form>
        </section>
      )}
      {role === "employee" && (
        <section className="card module-card-section">
          <h2>Apply for leave</h2>
          <form className="employee-form" onSubmit={apply}>
            <label className="form-field">Leave type
              <select value={leaveType} onChange={(event) => setLeaveType(event.target.value)} required>
                {policies.filter((policy) => policy.active).map((policy) => <option key={policy.leave_type}>{policy.leave_type}</option>)}
              </select>
            </label>
            <Field label="Start date" type="date" value={startDate} onChange={setStartDate} required />
            <Field label="End date" type="date" value={endDate} onChange={setEndDate} required />
            <label className="form-field form-full">Reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength="3" maxLength="1000" required /></label>
            <div className="form-actions"><button className="primary-button" disabled={busy || !leaveType}>Submit request</button></div>
          </form>
        </section>
      )}
      <section className="card module-card-section">
        <div className="section-title">
          <h2>Approved leave calendar</h2>
          <Field label="Month" type="month" value={calendarMonth} onChange={setCalendarMonth} />
        </div>
        {calendar.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Employee</th><th>Department</th><th>Leave type</th><th>Dates</th><th>Days</th></tr></thead>
          <tbody>{calendar.map((item) => <tr key={item.leave_request_id}>
            <td>{item.name} · {item.employee_id}</td><td>{item.department}</td><td>{item.leave_type}</td><td>{item.start_date} – {item.end_date}</td><td>{item.requested_days}</td>
          </tr>)}</tbody>
        </table></div> : <EmptyState title="No approved leave" text="Approved leave in the selected month will appear here." />}
      </section>
      <section className="metric-grid employee-metrics">
        {(balances?.employees || (balances ? [balances] : [])).flatMap((record) => record.balances.map((balance) => (
          <article className="metric-card" key={`${record.employee_id}-${balance.leave_type}`}>
            <span>{record.employee_id} · {balance.leave_type}</span>
            <strong>{balance.available_days}</strong>
            <small>days available of {balance.annual_allowance}</small>
          </article>
        )))}
      </section>
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Leave requests" : "My leave history and team requests"}</h2>
        {requests.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Status</th><th>Reason</th><th>Actions</th></tr></thead>
          <tbody>{requests.map((item) => (
            <tr className={String(item.leave_request_id) === relatedId ? "related-row" : ""} key={item.leave_request_id}>
              <td>{item.name} · {item.employee_id}</td><td>{item.leave_type}</td><td>{item.start_date} – {item.end_date}</td><td>{item.requested_days}</td><td>{item.status}</td><td>{item.rejection_reason || item.reason}</td>
              <td>{item.status === "Pending" && (role === "admin" || (user.is_manager && item.employee_id !== user.employee_id)) && (
                rejectingRequestId === item.leave_request_id ? (
                  <form className="row-actions" onSubmit={(event) => submitRejection(event, item)}>
                    <label className="form-field">Rejection reason
                      <input
                        value={rejectionReason}
                        onChange={(event) => setRejectionReason(event.target.value)}
                        maxLength="1000"
                        required
                      />
                    </label>
                    <button className="mini-action reject">Confirm rejection</button>
                    <button
                      type="button"
                      className="mini-action"
                      onClick={() => { setRejectingRequestId(null); setRejectionReason(""); }}
                    >
                      Cancel
                    </button>
                  </form>
                ) : <div className="row-actions">
                  <button className="mini-action approve" onClick={() => decide(item, "Approved")}>Approve</button>
                  <button
                    className="mini-action reject"
                    onClick={() => { setRejectingRequestId(item.leave_request_id); setRejectionReason(""); }}
                  >
                    Reject
                  </button>
                </div>
              )}</td>
            </tr>
          ))}</tbody>
        </table></div> : <EmptyState title="No leave requests" text="Submitted requests and their decisions will appear here." />}
      </section>
      {busy && <p className="quiet-note">Refreshing leave information…</p>}
    </>
  );
}

function PolicyEditor({ policy, onSave }) {
  const [draft, setDraft] = useState(policy);
  useEffect(() => { setDraft(policy); }, [policy]);
  return (
    <div className="policy-row">
      <strong>{policy.leave_type}</strong>
      <Field label="Allowance" type="number" min="0" max="366" step="0.5" value={draft.annual_allowance} onChange={(value) => setDraft({ ...draft, annual_allowance: value })} />
      <label className="checkbox-field"><input type="checkbox" checked={Boolean(draft.count_weekends)} onChange={(event) => setDraft({ ...draft, count_weekends: event.target.checked })} /> Count weekends</label>
      <label className="checkbox-field"><input type="checkbox" checked={Boolean(draft.active)} onChange={(event) => setDraft({ ...draft, active: event.target.checked })} /> Active</label>
      <button className="mini-action" onClick={() => onSave(draft)}>Save</button>
    </div>
  );
}

function AttendanceModule({ role, user, relatedId }) {
  const [history, setHistory] = useState({ items: [], total: 0 });
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [report, setReport] = useState([]);
  const [corrections, setCorrections] = useState([]);
  const [correctionDateTime, setCorrectionDateTime] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [records, monthly, pending] = await Promise.all([
        api(`/api/attendance/history?month=${month}`),
        role === "admin" ? api(`/api/admin/attendance/monthly?month=${month}`) : Promise.resolve([]),
        api("/api/admin/attendance/corrections"),
      ]);
      setHistory(records);
      setReport(monthly);
      setCorrections(pending);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [month, role]);
  useEffect(() => { load(); }, [load]);

  async function punch(action) {
    try {
      const result = await api(`/api/attendance/${action}`, { method: action === "check-in" ? "POST" : "PATCH" });
      setMessage(action === "check-in" ? `Checked in at ${result.check_in}.` : `Checked out at ${result.check_out}.`);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function reviewCorrection(item, status) {
    try {
      await api(`/api/admin/attendance/corrections/${item.correction_id}`, {
        method: "PATCH",
        body: jsonBody({ status, note: "" }),
      });
      setMessage(`Attendance correction ${status.toLowerCase()}.`);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function submitCorrection(event) {
    event.preventDefault();
    try {
      await api("/api/attendance/corrections", {
        method: "POST",
        body: jsonBody({
          requested_check_out: correctionDateTime,
          reason: correctionReason,
        }),
      });
      setCorrectionDateTime("");
      setCorrectionReason("");
      setMessage("Missed check-out correction sent for review.");
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <>
      <Notice error={error} message={message} />
      <div className="module-toolbar">
        <Field label="Month" type="month" value={month} onChange={setMonth} />
        {role === "employee" && <>
          <button className="primary-button" onClick={() => punch("check-in")}>Check in</button>
          <button className="secondary-button" onClick={() => punch("check-out")}>Check out</button>
        </>}
      </div>
      {role === "employee" && <section className="card module-card-section">
        <h2>Request a missed check-out correction</h2>
        <p className="quiet-note">Enter the time in the organization timezone configured for ERMS.</p>
        <form className="employee-form" onSubmit={submitCorrection}>
          <Field label="Requested check-out" type="datetime-local" value={correctionDateTime} onChange={setCorrectionDateTime} required />
          <label className="form-field form-full">Reason<textarea value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} minLength="3" maxLength="1000" required /></label>
          <div className="form-actions"><button className="primary-button">Submit correction request</button></div>
        </form>
      </section>}
      {role === "admin" && <section className="card module-card-section">
        <h2>Monthly attendance overview</h2>
        {report.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Employee</th><th>Department</th><th>Present</th><th>Late</th><th>Missed check-outs</th></tr></thead><tbody>
          {report.map((item) => <tr key={item.employee_id}><td>{item.name} · {item.employee_id}</td><td>{item.department}</td><td>{item.days_present}</td><td>{item.late_days}</td><td>{item.missed_checkouts}</td></tr>)}
        </tbody></table></div> : <EmptyState title="No attendance data" text="Monthly records will appear after employees check in." />}
      </section>}
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Attendance records" : "My attendance history"}</h2>
        {history.items.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Date</th><th>Employee</th><th>Check-in</th><th>Check-out</th><th>Hours</th><th>Late</th></tr></thead><tbody>
          {history.items.map((item) => <tr key={item.attendance_id}><td>{item.local_work_date}</td><td>{item.name} · {item.employee_id}</td><td>{item.check_in_utc}</td><td>{item.check_out_utc || "Missing"}</td><td>{item.worked_hours ?? "—"}</td><td>{item.is_late ? "Yes" : "No"}</td></tr>)}
        </tbody></table></div> : <EmptyState title="No attendance records" text="Check-in and check-out records will appear here." />}
      </section>
      {(role === "admin" || role === "employee") && <section className="card module-card-section"><h2>{role === "admin" ? "Missed check-out corrections" : user.is_manager ? "Team check-out correction requests" : "My correction requests"}</h2>
        {corrections.length ? corrections.map((item) => <article className={`request-card ${String(item.correction_id) === relatedId ? "related-row" : ""}`} key={item.correction_id}>
          <strong>{item.name} · {item.employee_id}</strong><p>{item.local_work_date} · requested {item.requested_check_out_utc}</p><p>{item.reason}</p>
          {(role === "admin" || (user.is_manager && item.employee_id !== user.employee_id)) && <div className="row-actions"><button className="mini-action approve" onClick={() => reviewCorrection(item, "Approved")}>Approve</button><button className="mini-action reject" onClick={() => reviewCorrection(item, "Rejected")}>Reject</button></div>}
        </article>) : <EmptyState title="No pending corrections" text="Employee correction requests will appear here." />}
      </section>}
    </>
  );
}

function PerformanceModule({ role, user, relatedId, initialEmployeeId }) {
  const [periods, setPeriods] = useState([]);
  const [goals, setGoals] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [employeeId, setEmployeeId] = useState(initialEmployeeId || user.employee_id || "");
  const [employees, setEmployees] = useState([]);
  const [period, setPeriod] = useState({ name: "", start_date: "", end_date: "" });
  const [goal, setGoal] = useState({ title: "", measurable_target: "", due_date: "", period_id: "" });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [periodList, goalList, reviewList, directory] = await Promise.all([
        api("/api/performance/periods"),
        api(`/api/performance/goals${role === "admin" && employeeId ? `?employee_id=${encodeURIComponent(employeeId)}` : ""}`),
        api(`/api/performance/reviews${role === "admin" && employeeId ? `?employee_id=${encodeURIComponent(employeeId)}` : ""}`),
        role === "admin" ? api("/api/employees?limit=500") : Promise.resolve(null),
      ]);
      setPeriods(periodList);
      setGoals(goalList);
      setReviews(reviewList);
      if (directory) setEmployees(directory.items);
      if (!goal.period_id && periodList.length) setGoal((current) => ({ ...current, period_id: String(periodList[0].period_id) }));
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [role, employeeId, goal.period_id]);
  useEffect(() => { load(); }, [load]);

  async function createPeriod(event) {
    event.preventDefault();
    try {
      await api("/api/admin/performance/periods", { method: "POST", body: jsonBody(period) });
      setMessage("Review period created.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function createGoal(event) {
    event.preventDefault();
    try {
      await api("/api/performance/goals", {
        method: "POST",
        body: jsonBody({ ...goal, employee_id: employeeId, period_id: Number(goal.period_id) }),
      });
      setMessage("Goal assigned.");
      setGoal({ ...goal, title: "", measurable_target: "", due_date: "" });
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function updateGoal(item, status) {
    try {
      await api(`/api/performance/goals/${item.goal_id}`, {
        method: "PATCH",
        body: jsonBody({ status, progress_note: item.progress_note || "" }),
      });
      setMessage("Goal progress saved.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function submitSelfAssessment(event, periodId) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/performance/reviews/${periodId}/self-assessment`, {
        method: "PUT",
        body: jsonBody({ self_assessment: data.get("self_assessment"), self_rating: Number(data.get("self_rating")) }),
      });
      setMessage("Self-assessment submitted and locked.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function managerFeedback(event, reviewId) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/performance/reviews/${reviewId}/manager-feedback`, {
        method: "PUT",
        body: jsonBody({ manager_feedback: data.get("manager_feedback"), manager_rating: Number(data.get("manager_rating")) }),
      });
      setMessage("Manager feedback submitted.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function submitAdminReview(event, reviewId) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/admin/performance/reviews/${reviewId}`, {
        method: "PATCH",
        body: jsonBody({ admin_note: data.get("admin_note") }),
      });
      setMessage("Admin review note saved.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function closePeriod(periodId) {
    try {
      await api(`/api/admin/performance/periods/${periodId}/close`, { method: "PATCH" });
      setMessage("Review period closed.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  return (
    <>
      <Notice error={error} message={message} />
      {role === "admin" && <section className="card module-card-section">
        <h2>Create a review period</h2>
        <form className="module-toolbar" onSubmit={createPeriod}>
          <Field label="Period name" value={period.name} onChange={(value) => setPeriod({ ...period, name: value })} required />
          <Field label="Start date" type="date" value={period.start_date} onChange={(value) => setPeriod({ ...period, start_date: value })} required />
          <Field label="End date" type="date" value={period.end_date} onChange={(value) => setPeriod({ ...period, end_date: value })} required />
          <button className="primary-button">Create period</button>
        </form>
        <div className="request-list">{periods.map((item) => <div className="history-item" key={item.period_id}>
          <strong>{item.name} · {item.start_date} – {item.end_date} · {item.status}</strong>
          {item.status === "Open" && <button className="mini-action" onClick={() => closePeriod(item.period_id)}>Close period</button>}
        </div>)}</div>
      </section>}
      {role === "admin" && <section className="card module-card-section">
        <h2>Assign a measurable goal</h2>
        <label className="form-field">Employee<select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}><option value="">Choose employee</option>{employees.map((item) => <option key={item.employee_id} value={item.employee_id}>{item.name} · {item.employee_id}</option>)}</select></label>
        <form className="module-toolbar" onSubmit={createGoal}>
          <Field label="Goal title" value={goal.title} onChange={(value) => setGoal({ ...goal, title: value })} required />
          <Field label="Measurable target" value={goal.measurable_target} onChange={(value) => setGoal({ ...goal, measurable_target: value })} required />
          <Field label="Due date" type="date" value={goal.due_date} onChange={(value) => setGoal({ ...goal, due_date: value })} required />
          <label className="form-field">Review period<select value={goal.period_id} onChange={(event) => setGoal({ ...goal, period_id: event.target.value })} required>{periods.map((item) => <option key={item.period_id} value={item.period_id}>{item.name}</option>)}</select></label>
          <button className="primary-button" disabled={!employeeId || !periods.length}>Assign goal</button>
        </form>
      </section>}
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Goals and reviews" : "My goals and review"}</h2>
        {goals.length ? goals.map((item) => <GoalRow key={item.goal_id} item={item} role={role} onSave={updateGoal} related={String(item.goal_id) === relatedId} />) : <EmptyState title="No goals assigned" text="Goals for available review periods will appear here." />}
        {reviews.map((item) => (
          <article className={`request-card ${String(item.review_id) === relatedId ? "related-row" : ""}`} key={item.review_id}>
            <div className="request-heading"><strong>{item.period_name} · {item.name}</strong><span>{item.period_status}</span></div>
            {item.self_submitted_at ? <p>Self rating: {item.self_rating}/5 · {item.self_assessment}</p> : role === "employee" && item.period_status === "Open" ? (
              <form className="stack-form" onSubmit={(event) => submitSelfAssessment(event, item.period_id)}>
                <textarea name="self_assessment" minLength="5" maxLength="5000" placeholder="Describe your progress and outcomes" required />
                <label className="form-field">Self rating<select name="self_rating" defaultValue="3">{[1, 2, 3, 4, 5].map((rating) => <option key={rating}>{rating}</option>)}</select></label>
                <button className="primary-button">Submit self-assessment</button>
              </form>
            ) : null}
            {item.manager_feedback && <p>Manager feedback ({item.manager_rating}/5): {item.manager_feedback}</p>}
            {role === "employee" && user.is_manager && item.employee_id !== user.employee_id && item.self_submitted_at && !item.manager_submitted_at && item.period_status === "Open" && (
              <form className="stack-form" onSubmit={(event) => managerFeedback(event, item.review_id)}>
                <textarea name="manager_feedback" minLength="5" maxLength="5000" placeholder="Manager feedback" required />
                <label className="form-field">Manager rating<select name="manager_rating" defaultValue="3">{[1, 2, 3, 4, 5].map((rating) => <option key={rating}>{rating}</option>)}</select></label>
                <button className="primary-button">Submit manager feedback</button>
              </form>
            )}
            {item.admin_note && <p>Admin review: {item.admin_note}</p>}
            {role === "admin" && item.self_submitted_at && !item.admin_reviewed_at && (
              <form className="stack-form" onSubmit={(event) => submitAdminReview(event, item.review_id)}>
                <textarea name="admin_note" minLength="2" maxLength="2000" placeholder="Admin review note" required />
                <button className="primary-button">Save Admin review</button>
              </form>
            )}
          </article>
        ))}
      </section>
    </>
  );
}

function GoalRow({ item, role, onSave, related }) {
  const [status, setStatus] = useState(item.status);
  const [note, setNote] = useState(item.progress_note || "");
  return (
    <article className={`task-row ${related ? "related-row" : ""}`}>
      <div><strong>{item.title}</strong><span>{item.measurable_target} · due {item.due_date} · {item.period_name}</span></div>
      {role === "employee" ? <div className="module-toolbar">
        <select value={status} onChange={(event) => setStatus(event.target.value)}>{["not_started", "in_progress", "completed"].map((value) => <option key={value}>{value}</option>)}</select>
        <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Progress note" />
        <button className="mini-action" onClick={() => onSave({ ...item, progress_note: note }, status)}>Save progress</button>
      </div> : <span className="badge">{item.status}</span>}
    </article>
  );
}

function OrganizationChartModule({ role, user }) {
  const [nodes, setNodes] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [departmentHeads, setDepartmentHeads] = useState([]);
  const [headDrafts, setHeadDrafts] = useState({});
  const [headHistory, setHeadHistory] = useState([]);
  const [showHeadHistory, setShowHeadHistory] = useState(false);
  const [search, setSearch] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [managerId, setManagerId] = useState("");
  const [expanded, setExpanded] = useState({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyDepartment, setBusyDepartment] = useState("");

  const load = useCallback(async () => {
    try {
      const [chart, directory, heads] = await Promise.all([
        api(`/api/organization/chart?search=${encodeURIComponent(search)}`),
        role === "admin" ? api("/api/employees?limit=500") : Promise.resolve(null),
        role === "admin" ? api("/api/admin/departments/heads") : Promise.resolve(null),
      ]);
      setNodes(chart);
      if (directory) setEmployees(directory.items);
      if (heads) {
        setDepartmentHeads(heads);
        setHeadDrafts((current) => Object.fromEntries(heads.map((item) => [
          item.department,
          current[item.department] ?? item.head_employee_id ?? "",
        ])));
      }
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }, [search, role]);
  useEffect(() => { load(); }, [load]);

  const children = useMemo(() => {
    const grouped = new Map();
    nodes.forEach((node) => {
      const key = node.manager_id || "";
      grouped.set(key, [...(grouped.get(key) || []), node]);
    });
    return grouped;
  }, [nodes]);

  const departmentGroups = useMemo(() => {
    const groups = new Map();
    const ensure = (name) => {
      if (!groups.has(name)) groups.set(name, { name, head: null, employees: [] });
      return groups.get(name);
    };
    departmentHeads.forEach((head) => {
      const group = ensure(head.department);
      group.head = head;
    });
    nodes.forEach((node) => {
      const group = ensure(node.department);
      group.employees.push(node);
      if (!group.head && node.department_head_assigned) {
        group.head = {
          head_employee_id: node.department_head_id,
          head_name: node.department_head_name,
          head_designation: node.department_head_designation,
        };
      }
    });
    return [...groups.values()].sort((first, second) => first.name.localeCompare(second.name));
  }, [departmentHeads, nodes]);

  async function assign(event) {
    event.preventDefault();
    try {
      await api(`/api/admin/employees/${encodeURIComponent(employeeId)}/manager`, {
        method: "PUT",
        body: jsonBody({ manager_id: managerId || null }),
      });
      setMessage("Reporting manager updated.");
      await load();
    } catch (requestError) { setError(requestError.message); }
  }

  async function saveDepartmentHead(department) {
    const employeeId = headDrafts[department] || null;
    setBusyDepartment(department);
    try {
      await api(`/api/admin/departments/${encodeURIComponent(department)}/head`, {
        method: "PUT",
        body: jsonBody({ employee_id: employeeId }),
      });
      setMessage(employeeId ? `Department head for ${department} updated.` : `Department head for ${department} cleared.`);
      await load();
    } catch (requestError) { setError(requestError.message); }
    finally { setBusyDepartment(""); }
  }

  async function toggleHeadHistory() {
    if (showHeadHistory) {
      setShowHeadHistory(false);
      return;
    }
    try {
      const history = await api("/api/admin/departments/heads/history");
      setHeadHistory(history);
      setShowHeadHistory(true);
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }

  function renderPerson(node, allowedIds, depth = 0, visited = new Set()) {
    if (visited.has(node.employee_id)) return null;
    const nextVisited = new Set(visited).add(node.employee_id);
    const directReports = (children.get(node.employee_id) || [])
      .filter((child) => allowedIds.has(child.employee_id) && child.department === node.department);
    const isExpanded = expanded[node.employee_id] !== false;
    return (
      <div className="org-node" key={node.employee_id} style={{ marginLeft: `${depth * 22}px` }}>
        <article className="request-card">
          <div className="request-heading">
            <div>
              <strong>{node.name}{node.employee_id === node.department_head_id ? " · Department head" : ""}</strong>
              <span>{node.employee_id} · {node.designation}</span>
            </div>
            {directReports.length > 0 && (
              <button
                className="text-button"
                onClick={() => setExpanded({ ...expanded, [node.employee_id]: !isExpanded })}
              >
                {isExpanded ? "Collapse" : `Expand (${directReports.length})`}
              </button>
            )}
          </div>
        </article>
        {isExpanded && directReports.map((report) => renderPerson(report, allowedIds, depth + 1, nextVisited))}
      </div>
    );
  }

  return (
    <>
      <Notice error={error} message={message} />
      <div className="module-toolbar"><Field label="Search employees" value={search} onChange={setSearch} placeholder="Name, ID, department" /></div>
      {role === "admin" && <section className="card module-card-section">
        <h2>Assign reporting manager</h2>
        <form className="module-toolbar" onSubmit={assign}>
          <label className="form-field">Employee<select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} required><option value="">Choose employee</option>{employees.map((item) => <option key={item.employee_id} value={item.employee_id}>{item.name} · {item.employee_id}</option>)}</select></label>
          <label className="form-field">Manager<select value={managerId} onChange={(event) => setManagerId(event.target.value)}><option value="">No manager</option>{employees.filter((item) => item.employee_id !== employeeId && item.status === "Approved" && item.active).map((item) => <option key={item.employee_id} value={item.employee_id}>{item.name} · {item.employee_id}</option>)}</select></label>
          <button className="primary-button" disabled={!employeeId}>Save assignment</button>
        </form>
      </section>}
      {role === "admin" && (
        <div className="section-title">
          <h2>Department heads</h2>
          <button className="secondary-button" onClick={toggleHeadHistory}>
            {showHeadHistory ? "Hide assignment history" : "View assignment history"}
          </button>
        </div>
      )}
      {showHeadHistory && <section className="card module-card-section">
        {headHistory.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Department</th><th>Previous head</th><th>New head</th><th>Assigned by</th><th>Timestamp</th></tr></thead>
          <tbody>{headHistory.map((item) => <tr key={item.assignment_id}>
            <td>{item.department_name}</td><td>{item.previous_head_id || "—"}</td><td>{item.new_head_id || "—"}</td><td>{item.assigned_by}</td><td>{item.assigned_at}</td>
          </tr>)}</tbody>
        </table></div> : <EmptyState title="No department-head changes" text="Head assignment history will appear here." />}
      </section>}
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Company structure" : user.is_manager ? "My assigned team" : "My organization"}</h2>
        {departmentGroups.length ? <div className="org-departments">
          {departmentGroups.map((group) => {
            const allowedIds = new Set(group.employees.map((item) => item.employee_id));
            const roots = group.employees.filter((item) => !allowedIds.has(item.manager_id));
            const head = group.head;
            const assignedHeadIsVisible = head?.head_employee_id && allowedIds.has(head.head_employee_id);
            return (
              <section className="org-department" key={group.name}>
                <div className="org-department-heading">
                  <div>
                    <h3>{group.name}</h3>
                    {head?.head_name ? (
                      <p>Department head: {head.head_name} · {head.head_employee_id} · {head.head_designation}</p>
                    ) : role === "employee" && head && !assignedHeadIsVisible ? (
                      <p>Department head is outside your authorized team view.</p>
                    ) : <p>No department head assigned.</p>}
                  </div>
                  {role === "admin" && head && (
                    <div className="department-head-assignment">
                      <label className="form-field">Set department head
                        <select
                          value={headDrafts[group.name] || ""}
                          onChange={(event) => setHeadDrafts({ ...headDrafts, [group.name]: event.target.value })}
                        >
                          <option value="">No department head</option>
                          {employees.filter((item) =>
                            item.department.toLowerCase() === group.name.toLowerCase()
                            && item.status === "Approved" && item.active
                          ).map((item) => (
                            <option key={item.employee_id} value={item.employee_id}>
                              {item.name} · {item.employee_id}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        className="mini-action"
                        disabled={busyDepartment === group.name}
                        onClick={() => saveDepartmentHead(group.name)}
                      >
                        {busyDepartment === group.name ? "Saving…" : "Save head"}
                      </button>
                    </div>
                  )}
                </div>
                {roots.length ? <div className="org-tree">
                  {roots.map((root) => renderPerson(root, allowedIds))}
                </div> : role === "admin" && !search ? (
                  <p className="quiet-note">No active approved employees in this department.</p>
                ) : <p className="quiet-note">No matching employees in this department.</p>}
              </section>
            );
          })}
        </div> : <EmptyState title="No organization structure" text="Add departments and approved employees to build the organization." />}
      </section>
    </>
  );
}

function IdCardModule({ role, user }) {
  const [employeeId, setEmployeeId] = useState("");
  const [employees, setEmployees] = useState([]);
  const [card, setCard] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (role === "admin") {
      api("/api/employees?limit=500")
        .then((result) => setEmployees(result.items))
        .catch((requestError) => setError(requestError.message));
    } else {
      api("/api/id-cards/me")
        .then(setCard)
        .catch((requestError) => {
          if (!requestError.message.includes("No active ID card")) setError(requestError.message);
        });
    }
  }, [role]);

  async function issue() {
    try {
      const result = await api(`/api/admin/id-cards/${encodeURIComponent(employeeId)}/issue`, { method: "POST" });
      setCard(result);
      setMessage(result.message);
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }

  return (
    <>
      <Notice error={error} message={message} />
      {role === "admin" && <label className="form-field module-select">Employee
        <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}><option value="">Choose employee</option>{employees.map((item) => <option key={item.employee_id} value={item.employee_id}>{item.name} · {item.employee_id}</option>)}</select>
      </label>}
      <section className="card module-card-section">
        <h2>{role === "admin" ? "Issue or reissue an employee card" : "My employee ID card"}</h2>
        {card?.card_id && <p>Card #{card.card_id} · issued {card.issued_at || "just now"}</p>}
        {role === "admin" ? <button className="primary-button" disabled={!employeeId} onClick={issue}>Generate / reissue card</button> : null}
        {role === "employee" ? card ? <button className="primary-button" onClick={() => downloadFile("/api/id-cards/me/pdf", `employee-card-${card.card_id}.pdf`).catch((requestError) => setError(requestError.message))}>Download PDF</button> : <EmptyState title="No card yet" text="Ask Admin to issue your employee card." /> : null}
        {role === "admin" && card?.card_id && <button className="secondary-button" onClick={() => downloadFile(`/api/admin/id-cards/${encodeURIComponent(employeeId)}/pdf`, `employee-card-${card.card_id}.pdf`).catch((requestError) => setError(requestError.message))}>Download issued card</button>}
        <p className="quiet-note">The QR code contains a random verification identifier, not credentials or personal tokens.</p>
      </section>
    </>
  );
}

function ImportReportModule() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [reportName, setReportName] = useState("department-employees");
  const [reportStartDate, setReportStartDate] = useState("");
  const [reportEndDate, setReportEndDate] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);

  async function makePreview(event) {
    event.preventDefault();
    if (!file) return;
    const data = new FormData();
    data.set("file", file);
    setBusy(true);
    try {
      const result = await api("/api/admin/import/employees/preview", { method: "POST", body: data });
      setPreview(result);
      setError("");
      setMessage("Preview is ready. Nothing has been saved yet.");
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  }

  async function commit() {
    setBusy(true);
    try {
      const result = await api(`/api/admin/import/employees/${preview.batch_id}/commit`, {
        method: "POST",
        body: jsonBody({ confirm: true }),
      });
      setMessage(`${result.imported_count} employees imported as Pending accounts.`);
      setPreview(null);
      await downloadFile(`/api/admin/import/employees/${result.batch_id}/summary`, `employee-import-${result.batch_id}.csv`);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  }

  async function exportReport(extension) {
    setReportBusy(true);
    const query = new URLSearchParams();
    if (reportStartDate) query.set("start_date", reportStartDate);
    if (reportEndDate) query.set("end_date", reportEndDate);
    const suffix = query.size ? `?${query}` : "";
    try {
      await downloadFile(
        `/api/admin/reports/${reportName}.${extension}${suffix}`,
        `erms-${reportName}.${extension}`,
      );
      setError("");
    } catch (requestError) { setError(requestError.message); }
    finally { setReportBusy(false); }
  }

  return (
    <>
      <Notice error={error} message={message} />
      <section className="card module-card-section">
        <h2>Bulk employee import</h2>
        <p className="quiet-note">Upload CSV or XLSX. Preview validation errors before committing; successful imports are transactional and start Pending.</p>
        <form className="module-toolbar" onSubmit={makePreview}>
          <label className="form-field">CSV or Excel file<input type="file" accept=".csv,.xlsx" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label>
          <button className="primary-button" disabled={!file || busy}>{busy ? "Working…" : "Preview import"}</button>
        </form>
      </section>
      <section className="card module-card-section">
        <h2>Workforce and operations reports</h2>
        <div className="module-toolbar">
          <label className="form-field">Report
            <select value={reportName} onChange={(event) => setReportName(event.target.value)}>
              <option value="department-employees">Department-wise employees</option>
              <option value="employee-status">Active and inactive employees</option>
              <option value="joining-dates">Joining dates</option>
              <option value="approval-status">Approval status</option>
              <option value="leave">Leave report</option>
              <option value="attendance">Attendance report</option>
              <option value="employee-growth">Employee growth by month</option>
              <option value="workforce">Department workforce summary</option>
            </select>
          </label>
          <Field label="From (optional)" type="date" value={reportStartDate} onChange={setReportStartDate} />
          <Field label="To (optional)" type="date" value={reportEndDate} onChange={setReportEndDate} />
          <button type="button" className="secondary-button" disabled={reportBusy} onClick={() => exportReport("csv")}>Export CSV</button>
          <button type="button" className="secondary-button" disabled={reportBusy} onClick={() => exportReport("xlsx")}>Export Excel</button>
          <button type="button" className="text-button" onClick={() => downloadFile("/api/reports/employees.xlsx", "employee_records.xlsx").catch((requestError) => setError(requestError.message))}>Export full employee directory (includes HR-only fields)</button>
        </div>
        <p className="quiet-note">Date range filters apply to joining-date, employee-growth, leave, and attendance reports. Full-directory export contains sensitive HR fields and is Admin-only.</p>
      </section>
      {preview && <section className="card module-card-section">
        <h2>Import preview</h2>
        <p>{preview.valid_count} valid · {preview.error_count} row errors · {preview.total_rows} total</p>
        {preview.rows?.length > 0 && <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Row</th><th>Employee ID</th><th>Name</th><th>Email</th><th>Department</th><th>Designation</th><th>Joining date</th><th>Salary</th></tr></thead>
          <tbody>{preview.rows.slice(0, 20).map((item) => <tr key={item.row}><td>{item.row}</td><td>{item.employee_id}</td><td>{item.name}</td><td>{item.email}</td><td>{item.department}</td><td>{item.designation}</td><td>{item.joining_date}</td><td>{item.salary}</td></tr>)}</tbody>
        </table></div>}
        {preview.rows?.length > 20 && <p className="quiet-note">Showing the first 20 of {preview.rows.length} valid rows.</p>}
        {preview.errors?.length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th>Row</th><th>Employee ID</th><th>Error</th></tr></thead><tbody>{preview.errors.map((item, index) => <tr key={`${item.row}-${index}`}><td>{item.row}</td><td>{item.employee_id || "—"}</td><td>{item.error}</td></tr>)}</tbody></table></div>}
        {preview.valid_count > 0 && preview.error_count === 0 && <button className="primary-button" disabled={busy} onClick={commit}>Commit import</button>}
      </section>}
    </>
  );
}

function HashMapLab() {
  const [size, setSize] = useState("100");
  const [result, setResult] = useState(null);
  const [demoKey, setDemoKey] = useState("EMP-DEMO-1");
  const [demoAction, setDemoAction] = useState("search");
  const [demoResult, setDemoResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function benchmark() {
    setBusy(true);
    try {
      const data = await api(`/api/hash-map-lab/benchmark?size=${size}&repetitions=3`, { method: "POST" });
      setResult(data);
      setError("");
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  }

  async function demonstrate() {
    try {
      const data = await api("/api/hash-map-lab/demo", {
        method: "POST",
        body: jsonBody({ action: demoAction, employee_id: demoKey }),
      });
      setDemoResult(data);
      setError("");
    } catch (requestError) { setError(requestError.message); }
  }

  return (
    <>
      <Notice error={error} />
      <section className="card module-card-section">
        <h2>Custom hash map performance lab</h2>
        <p className="quiet-note">Measured timings use generated demonstration keys. Complexity descriptions are theoretical and separate from observed runtime.</p>
        <div className="module-toolbar">
          <label className="form-field">Dataset size<select value={size} onChange={(event) => setSize(event.target.value)}>{["100", "1000", "10000"].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button className="primary-button" onClick={benchmark} disabled={busy}>{busy ? "Measuring…" : "Run repeated benchmark"}</button>
        </div>
        {result && <BenchmarkResults result={result} />}
        <h3>Interactive insert / search / delete</h3>
        <div className="module-toolbar">
          <Field label="Employee key" value={demoKey} onChange={setDemoKey} />
          <label className="form-field">Operation<select value={demoAction} onChange={(event) => setDemoAction(event.target.value)}>{["insert", "search", "delete"].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button className="secondary-button" onClick={demonstrate}>Run demonstration</button>
        </div>
        {demoResult && <HashMapVisualization result={demoResult} />}
      </section>
    </>
  );
}

function HashMapVisualization({ result }) {
  const chain = result.separate_chaining;
  const probing = result.linear_probing;
  return (
    <section className="hash-lab-visualization">
      <p className="alert success-alert">
        {result.action} {result.employee_id}: {result.result.found
          ? "found in both maps"
          : result.result.inserted
            ? "inserted in both maps"
            : result.result.deleted
              ? "deleted from both maps"
              : result.result.existed === false && result.action === "delete"
                ? "key did not exist in either map"
                : "search result consistent in both maps"}
        {" · "}results match: {result.results_match ? "yes" : "no"}
      </p>
      <div className="metric-grid">
        <LabMetric label="Separate chaining collisions" value={chain.stats.collisions} note={`${chain.stats.resizes} resizes · capacity ${chain.stats.capacity}`} />
        <LabMetric label="Maximum chain length" value={chain.stats.max_chain_length} note={`${chain.stats.size} keys · load ${chain.stats.load_factor}`} />
        <LabMetric label="Linear probing collisions" value={probing.stats.collisions} note={`${probing.stats.resizes} resizes · capacity ${probing.stats.capacity}`} />
        <LabMetric label="Linear probing probes" value={probing.stats.probes} note={`${probing.stats.size} keys · load ${probing.stats.load_factor}`} />
      </div>
      <section className="card module-card-section">
        <h3>Separate chaining buckets</h3>
        <div className="hash-bucket-grid">
          {chain.buckets.filter((bucket) => bucket.keys.length > 0).map((bucket) => (
            <div className={`hash-bucket ${bucket.keys.length > 1 ? "has-collision" : ""}`} key={bucket.index}>
              <strong>Bucket {bucket.index}</strong>
              <div>{bucket.keys.map((key, index) => <span className="hash-chain-key" key={key}>{index > 0 && <b>→</b>}{key}</span>)}</div>
            </div>
          ))}
        </div>
        {!chain.buckets.some((bucket) => bucket.keys.length > 0) && <EmptyState title="No occupied buckets" text="Insert a demonstration key to populate the chaining map." />}
      </section>
      <section className="card module-card-section">
        <h3>Linear probing slots</h3>
        <div className="hash-slot-grid">
          {probing.slots.map((slot) => (
            <div className={`hash-slot ${slot.state} ${slot.key ? "has-key" : ""}`} key={slot.index} title={slot.key || slot.state}>
              <strong>{slot.index}</strong><span>{slot.key || (slot.state === "deleted" ? "deleted" : "empty")}</span>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

function LabMetric({ label, value, note }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

function BenchmarkResults({ result }) {
  const max = Math.max(
    1,
    ...result.methods.flatMap((item) => [item.insert_ms_average, item.search_ms_average]),
  );
  const complexity = {
    "Custom Separate Chaining": "Best O(1) · average O(1 + load factor) · worst O(n)",
    "Custom Linear Probing": "Best O(1) · average O(1) expected · worst O(n)",
    "Linear Search baseline": "Best O(1) · average O(n) · worst O(n)",
  };
  return (
    <section className="benchmark-results">
      <p>{result.dataset_size} records · {result.repetitions} repetitions · {result.search_count_per_repetition} searches per repetition · results identical: {result.results_match ? "yes" : "no"}</p>
      {result.methods.map((method) => (
        <div className="benchmark-bar" key={method.name}>
          <div className="benchmark-bar-label"><strong>{method.name}</strong><span>Insert avg {method.insert_ms_average} ms · Search avg {method.search_ms_average} ms · collisions {method.collisions} · probes {method.probes}</span></div>
          <div className="benchmark-measure"><span>Insert</span><div className="bar-track"><div style={{ width: `${Math.max(1, method.insert_ms_average / max * 100)}%` }} /></div></div>
          <div className="benchmark-measure"><span>Search</span><div className="bar-track"><div style={{ width: `${Math.max(1, method.search_ms_average / max * 100)}%` }} /></div></div>
          <p className="quiet-note">Theoretical complexity: {complexity[method.name]}.</p>
        </div>
      ))}
      <p className="quiet-note">Measured timings use synthetic records in this runtime and are not guarantees of production performance.</p>
    </section>
  );
}

function EmptyState({ title, text }) {
  return <div className="empty-state"><div className="empty-icon">—</div><strong>{title}</strong><span>{text}</span></div>;
}
