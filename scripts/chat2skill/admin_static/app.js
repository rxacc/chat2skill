const e = React.createElement;

const token = new URLSearchParams(window.location.search).get("token") || "";

function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Chat2Skill-Admin-Token": token,
    ...(options.headers || {}),
  };
  return fetch(path, { ...options, headers }).then(async (response) => {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    return body;
  });
}

function enc(value) {
  return encodeURIComponent(value);
}

function formatTime(value) {
  if (!value) return "n/a";
  return String(value).replace("T", " ").slice(0, 19);
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 1000) / 10}%`;
}

function sortProjects(projects, sortMode) {
  const sorted = [...projects];
  const text = (value) => String(value || "").toLowerCase();
  const number = (value) => Number(value || 0);
  if (sortMode === "updated_asc") {
    sorted.sort((a, b) => text(a.last_updated_at).localeCompare(text(b.last_updated_at)) || text(a.user_id).localeCompare(text(b.user_id)));
  } else if (sortMode === "name_asc") {
    sorted.sort((a, b) => text(a.user_id).localeCompare(text(b.user_id)));
  } else if (sortMode === "name_desc") {
    sorted.sort((a, b) => text(b.user_id).localeCompare(text(a.user_id)));
  } else if (sortMode === "skills_desc") {
    sorted.sort((a, b) => number(b.active_skills) - number(a.active_skills) || text(b.last_updated_at).localeCompare(text(a.last_updated_at)));
  } else if (sortMode === "memories_desc") {
    sorted.sort((a, b) => number(b.active_memories) - number(a.active_memories) || text(b.last_updated_at).localeCompare(text(a.last_updated_at)));
  } else {
    sorted.sort((a, b) => text(b.last_updated_at).localeCompare(text(a.last_updated_at)) || text(a.user_id).localeCompare(text(b.user_id)));
  }
  return sorted;
}

function App() {
  const [projects, setProjects] = React.useState([]);
  const [selected, setSelected] = React.useState("");
  const [tab, setTab] = React.useState("skills");
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [projectQuery, setProjectQuery] = React.useState("");
  const [projectSort, setProjectSort] = React.useState("updated_desc");
  const [refreshKey, setRefreshKey] = React.useState(0);

  const loadProjects = React.useCallback(() => {
    setLoading(true);
    api("/api/projects")
      .then((data) => {
        const nextProjects = data.projects || [];
        setProjects(nextProjects);
        setSelected((current) =>
          nextProjects.some((project) => project.user_id === current) ? current : (nextProjects[0]?.user_id || "")
        );
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  React.useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      loadProjects();
      setRefreshKey((value) => value + 1);
    };
    const timer = window.setInterval(refresh, 10000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [loadProjects]);

  const current = projects.find((project) => project.user_id === selected);
  const archiveProject = () => {
    if (!current) return;
    setError("");
    api(`/api/projects/${enc(current.user_id)}/archive`, { method: "POST" })
      .then(loadProjects)
      .catch((err) => setError(err.message));
  };
  const restoreProject = () => {
    if (!current) return;
    setError("");
    api(`/api/projects/${enc(current.user_id)}/restore`, { method: "POST" })
      .then(loadProjects)
      .catch((err) => setError(err.message));
  };
  const deleteProject = () => {
    if (!current || current.status !== "archived") return;
    const confirmed = window.confirm(
      `Delete archived project ${current.user_id}?\n\nThis permanently deletes related skills, project skill, memories, materializations, activity, and local skill files.`
    );
    if (!confirmed) return;
    setError("");
    api(`/api/projects/${enc(current.user_id)}`, { method: "DELETE" })
      .then(() => {
        setSelected("");
        loadProjects();
      })
      .catch((err) => setError(err.message));
  };
  const shownProjects = sortProjects(
    projects.filter((project) => `${project.user_id} ${project.project_dir || ""}`.toLowerCase().includes(projectQuery.toLowerCase())),
    projectSort
  );
  return e("div", { className: "shell" },
    e("aside", { className: "sidebar" },
      e("div", { className: "brand" },
        e("h1", null, "Chat2Skill Admin"),
        e("button", { onClick: loadProjects, disabled: loading }, "Refresh")
      ),
      e("div", { className: "project-controls" },
        e("input", {
          placeholder: "Search projects",
          value: projectQuery,
          onChange: (event) => setProjectQuery(event.target.value),
        }),
        e("select", { value: projectSort, onChange: (event) => setProjectSort(event.target.value) },
          e("option", { value: "updated_desc" }, "Updated newest"),
          e("option", { value: "updated_asc" }, "Updated oldest"),
          e("option", { value: "name_asc" }, "Name A-Z"),
          e("option", { value: "name_desc" }, "Name Z-A"),
          e("option", { value: "skills_desc" }, "Skills most"),
          e("option", { value: "memories_desc" }, "Memories most")
        )
      ),
      e("div", { className: "project-list" },
        shownProjects.map((project) => e("button", {
          key: project.user_id,
          "data-project-row": true,
          className: `project-row ${project.user_id === selected ? "active" : ""}`,
          onClick: () => {
            setError("");
            setSelected(project.user_id);
          },
        },
          e("div", { className: "project-title" },
            e("span", { className: "project-name" }, project.user_id),
            project.status === "archived" ? e("span", { className: "badge warn" }, "archived") : null
          ),
          e("div", { className: "meta" }, project.project_dir || "No project_dir"),
          e("div", { className: "meta" },
            `${project.active_skills || 0}/${project.total_skills || 0} skills · ${project.active_memories || 0}/${project.total_memories || 0} memories`
          ),
          e("div", { className: "meta" }, `updated ${formatTime(project.last_updated_at)}`)
        ))
      )
    ),
    e("main", { className: "main" },
      error ? e("div", { className: "error" }, error) : null,
      !selected ? e("div", { className: "empty" }, "No Chat2Skill projects found.") :
        e(React.Fragment, null,
          e("div", { className: "topbar" },
            e("div", null,
              e("h2", null, selected),
              e("div", { className: "meta" }, current?.project_dir || "Local database project"),
              e("div", { className: "meta" }, `updated ${formatTime(current?.last_updated_at)}`)
            ),
            e("div", { className: "actions" },
              current?.status === "archived"
                ? e("button", { onClick: restoreProject }, "Restore")
                : e("button", { onClick: archiveProject }, "Archive"),
              e("button", {
                className: "danger",
                onClick: deleteProject,
                disabled: current?.status !== "archived",
              }, "Delete"),
              e("button", { onClick: loadProjects }, "Reload Projects")
            )
          ),
          e("div", { className: "tabs" },
            ["skills", "memories", "prompts", "project-skill", "overview", "evals"].map((name) =>
              e("button", {
                key: name,
                className: `tab ${tab === name ? "active" : ""}`,
                onClick: () => {
                  setError("");
                  setTab(name);
                },
              }, name)
            )
          ),
          tab === "skills" ? e(SkillsTab, { userId: selected, setError, refreshKey }) : null,
          tab === "memories" ? e(MemoriesTab, { userId: selected, setError, refreshKey }) : null,
          tab === "prompts" ? e(PromptsTab, { userId: selected, setError, refreshKey }) : null,
          tab === "evals" ? e(EvalsTab, { userId: selected, setError, refreshKey }) : null,
          tab === "project-skill" ? e(ProjectSkillTab, { userId: selected, setError, refreshProjects: loadProjects, refreshKey }) : null,
          tab === "overview" ? e(OverviewTab, { userId: selected, setError, refreshKey }) : null
        )
    )
  );
}

function SkillsTab({ userId, setError, refreshKey }) {
  const [skills, setSkills] = React.useState([]);
  const [selected, setSelected] = React.useState("");
  const [detail, setDetail] = React.useState(null);
  const [status, setStatus] = React.useState("active");
  const [q, setQ] = React.useState("");

  const load = React.useCallback(() => {
    api(`/api/projects/${enc(userId)}/skills?status=${enc(status)}&q=${enc(q)}`)
      .then((data) => {
        const nextSkills = data.skills || [];
        setSkills(nextSkills);
        setSelected((current) =>
          nextSkills.some((skill) => skill.name === current) ? current : (nextSkills[0]?.name || "")
        );
        if (!nextSkills.length) {
          setDetail(null);
        }
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [userId, status, q]);

  React.useEffect(() => {
    setSelected("");
    setDetail(null);
    setError("");
  }, [userId]);

  React.useEffect(() => {
    load();
  }, [userId, status, refreshKey]);

  React.useEffect(() => {
    if (!selected) return;
    if (!skills.some((skill) => skill.name === selected)) return;
    let active = true;
    api(`/api/projects/${enc(userId)}/skills/${enc(selected)}`)
      .then((data) => {
        if (active) {
          setDetail(data);
          setError("");
        }
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
    };
  }, [userId, selected, skills]);

  return e("section", null,
    e("div", { className: "toolbar" },
      e("input", { placeholder: "Search skills", value: q, onChange: (event) => setQ(event.target.value), onKeyDown: (event) => event.key === "Enter" && load() }),
      e("select", { value: status, onChange: (event) => setStatus(event.target.value) },
        ["active", "draft", "rejected", "archived", "all"].map((item) => e("option", { key: item, value: item }, item))
      ),
      e("button", { onClick: load }, "Search"),
      e("span", { className: "meta" }, `${skills.length} rows`)
    ),
    e("div", { className: "grid" },
      e("div", { className: "list" },
        skills.map((skill) => e("div", {
          key: skill.name,
          className: `item ${skill.name === selected ? "active" : ""}`,
          onClick: () => setSelected(skill.name),
        },
          e("div", { className: "item-title" },
            e("span", null, skill.name),
            e("span", { className: `badge ${skill.status === "active" ? "ok" : ""}` }, skill.status)
          ),
          e("div", { className: "meta" }, `${skill.skill_type || "preference"} · ${skill.language || "n/a"} · evidence ${skill.evidence_count || 0}`),
          e("div", { className: "meta" }, `updated ${formatTime(skill.updated_at)}`),
          e("div", { className: "meta" }, skill.description || "")
        ))
      ),
      detail ? e(SkillEditor, { userId, detail, onSaved: () => { load(); setSelected(detail.skill.name); }, setError }) :
        e("div", { className: "empty" }, "Select a skill.")
    )
  );
}

function SkillEditor({ userId, detail, onSaved, setError }) {
  const skill = detail.skill;
  const [draft, setDraft] = React.useState(skill);
  React.useEffect(() => {
    setDraft(skill);
  }, [skill.name]);

  const save = () => {
    api(`/api/projects/${enc(userId)}/skills/${enc(skill.name)}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }).then(() => onSaved()).catch((err) => setError(err.message));
  };
  const remove = () => {
    if (!window.confirm(`Delete skill ${skill.name}?`)) return;
    api(`/api/projects/${enc(userId)}/skills/${enc(skill.name)}`, { method: "DELETE" })
      .then(onSaved)
      .catch((err) => setError(err.message));
  };
  const set = (key, value) => setDraft({ ...draft, [key]: value });
  return e("div", { className: "panel" },
    e("h3", null, skill.name),
    e("div", { className: "meta panel-meta" }, `created ${formatTime(skill.created_at)} · updated ${formatTime(skill.updated_at)}`),
    e("div", { className: "fields" },
      e(Field, { label: "Status" }, e("select", { value: draft.status || "active", onChange: (event) => set("status", event.target.value) },
        ["active", "draft", "rejected", "archived"].map((item) => e("option", { key: item, value: item }, item))
      )),
      e(Field, { label: "Type" }, e("input", { value: draft.skill_type || "", onChange: (event) => set("skill_type", event.target.value) })),
      e(Field, { label: "Language" }, e("input", { value: draft.language || "", onChange: (event) => set("language", event.target.value) })),
      e(Field, { label: "Confidence" }, e("input", { type: "number", step: "0.01", value: draft.confidence || 0, onChange: (event) => set("confidence", Number(event.target.value)) })),
      e(Field, { label: "Description", full: true }, e("textarea", { value: draft.description || "", onChange: (event) => set("description", event.target.value) })),
      e(Field, { label: "Content", full: true }, e("textarea", { value: draft.content || "", onChange: (event) => set("content", event.target.value), style: { minHeight: 280 } }))
    ),
    e("div", { className: "actions" },
      e("button", { className: "primary", onClick: save }, "Save"),
      e("button", { onClick: () => set("status", draft.status === "active" ? "archived" : "active") }, draft.status === "active" ? "Mark Archived" : "Mark Active"),
      e("button", { className: "danger", onClick: remove }, "Delete")
    ),
    e("h3", { style: { marginTop: 20 } }, "Evidence"),
    (detail.memory_items || []).length ? e("div", { className: "list" },
      detail.memory_items.map((item, index) => e("div", { className: "item", key: index },
        e("div", { className: "item-title" }, e("span", null, item.title || item.item_type), e("span", { className: "badge" }, item.confidence || 0)),
        e("div", { className: "meta" }, item.description || item.content || ""),
        e("div", { className: "meta" }, item.created_at ? `created ${formatTime(item.created_at)}` : ""),
        e("div", { className: "meta" }, item.evidence || "")
      ))
    ) : e("div", { className: "empty" }, "No evidence items.")
  );
}

function MemoriesTab({ userId, setError, refreshKey }) {
  const [memories, setMemories] = React.useState([]);
  const [selected, setSelected] = React.useState(null);
  const [status, setStatus] = React.useState("active");
  const [contextKey, setContextKey] = React.useState("all");
  const [q, setQ] = React.useState("");

  const load = React.useCallback(() => {
    api(`/api/projects/${enc(userId)}/memories?context_key=${enc(contextKey)}&status=${enc(status)}&q=${enc(q)}`)
      .then((data) => {
        const nextMemories = data.memories || [];
        setMemories(nextMemories);
        setSelected((current) => {
          if (current && nextMemories.some((item) => item.id === current.id && item.context_key === current.context_key)) {
            return current;
          }
          return nextMemories[0] || null;
        });
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [userId, contextKey, status, q]);

  React.useEffect(() => {
    setSelected(null);
    setContextKey("all");
  }, [userId]);

  React.useEffect(() => load(), [userId, contextKey, status, refreshKey]);

  return e("section", null,
    e("div", { className: "toolbar" },
      e("input", { placeholder: "Search memories", value: q, onChange: (event) => setQ(event.target.value), onKeyDown: (event) => event.key === "Enter" && load() }),
      e("select", { value: status, onChange: (event) => setStatus(event.target.value) },
        ["active", "archived", "all"].map((item) => e("option", { key: item, value: item }, item))
      ),
      e("select", { value: contextKey, onChange: (event) => setContextKey(event.target.value) },
        ["all", ...Array.from(new Set(memories.map((item) => item.context_key).filter(Boolean)))].map((item) =>
          e("option", { key: item, value: item }, item)
        )
      ),
      e("button", { onClick: load }, "Search"),
      e("span", { className: "meta" }, `${memories.length} rows`)
    ),
    e("div", { className: "grid" },
      e("div", { className: "list" }, memories.map((memory) => e("div", {
        key: `${memory.context_key}:${memory.id}`,
        className: `item ${selected && selected.id === memory.id && selected.context_key === memory.context_key ? "active" : ""}`,
        onClick: () => setSelected(memory),
      },
        e("div", { className: "item-title" },
          e("span", null, memory.content ? memory.content.slice(0, 80) : memory.id),
          e("span", { className: `badge ${memory.is_archived ? "warn" : "ok"}` }, memory.is_archived ? "archived" : "active")
        ),
        e("div", { className: "meta" }, `${memory.memory_type || "fact"} / ${memory.section || "general"} · salience ${memory.salience || 0}`),
        e("div", { className: "meta" }, `updated ${formatTime(memory.updated_at)}`)
      ))),
      selected ? e(MemoryEditor, { userId, memory: selected, onSaved: load, setError }) :
        e("div", { className: "empty" }, "Select a memory.")
    )
  );
}

function MemoryEditor({ userId, memory, onSaved, setError }) {
  const [draft, setDraft] = React.useState(memory);
  const [evaluating, setEvaluating] = React.useState(false);
  const [evalRun, setEvalRun] = React.useState("");
  React.useEffect(() => {
    setDraft(memory);
    setEvalRun("");
  }, [memory.id]);
  const set = (key, value) => setDraft({ ...draft, [key]: value });
  const save = () => {
    api(`/api/projects/${enc(userId)}/memories/${enc(memory.context_key)}/${enc(memory.id)}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }).then(onSaved).catch((err) => setError(err.message));
  };
  const remove = () => {
    if (!window.confirm("Delete this memory?")) return;
    api(`/api/projects/${enc(userId)}/memories/${enc(memory.context_key)}/${enc(memory.id)}`, { method: "DELETE" })
      .then(onSaved)
      .catch((err) => setError(err.message));
  };
  const runEval = () => {
    if (evaluating) return;
    setEvaluating(true);
    setError("");
    api(`/api/projects/${enc(userId)}/memories/${enc(memory.context_key)}/${enc(memory.id)}/eval-runs/run`, {
      method: "POST",
      body: JSON.stringify({ suite: `memory:${memory.id}` }),
    })
      .then((data) => setEvalRun(data.run?.run_id || "completed"))
      .catch((err) => setError(err.message))
      .finally(() => setEvaluating(false));
  };
  return e("div", { className: "panel" },
    e("div", { className: "topbar" },
      e("div", null,
        e("h3", null, memory.id),
        e("div", { className: "meta panel-meta" }, `created ${formatTime(memory.created_at)} · updated ${formatTime(memory.updated_at)}`)
      ),
      e("div", { className: "actions compact" },
        e("button", { onClick: runEval, disabled: evaluating }, evaluating ? "Evaluating..." : "Run Recall Eval")
      )
    ),
    evalRun ? e("div", { className: "meta panel-meta" }, `eval saved ${evalRun}`) : null,
    e("div", { className: "fields" },
      e(Field, { label: "Type" }, e("input", { value: draft.memory_type || "", onChange: (event) => set("memory_type", event.target.value) })),
      e(Field, { label: "Section" }, e("input", { value: draft.section || "", onChange: (event) => set("section", event.target.value) })),
      e(Field, { label: "Salience" }, e("input", { type: "number", step: "0.01", value: draft.salience || 0, onChange: (event) => set("salience", Number(event.target.value)) })),
      e(Field, { label: "Confidence" }, e("input", { type: "number", step: "0.01", value: draft.confidence || 0, onChange: (event) => set("confidence", Number(event.target.value)) })),
      e(Field, { label: "Content", full: true }, e("textarea", { value: draft.content || "", onChange: (event) => set("content", event.target.value) }))
    ),
    e("div", { className: "actions" },
      e("button", { className: "primary", onClick: save }, "Save"),
      e("button", {
        onClick: () => setDraft({
          ...draft,
          is_archived: !draft.is_archived,
          is_active: Boolean(draft.is_archived),
        }),
      }, draft.is_archived ? "Mark Active" : "Archive"),
      e("button", { className: "danger", onClick: remove }, "Delete")
    ),
    e("div", { className: "meta", style: { marginTop: 10 } }, `source_session: ${memory.source_session || ""}`)
  );
}

function PromptsTab({ userId, setError, refreshKey }) {
  const [records, setRecords] = React.useState([]);
  const [selected, setSelected] = React.useState(null);
  const [limit, setLimit] = React.useState(50);

  const load = React.useCallback(() => {
    api(`/api/projects/${enc(userId)}/materializations?limit=${enc(limit)}`)
      .then((data) => {
        const nextRecords = data.materializations || [];
        setRecords(nextRecords);
        setSelected((current) => {
          if (current && nextRecords.some((item) => item.materialization_id === current.materialization_id)) {
            return nextRecords.find((item) => item.materialization_id === current.materialization_id);
          }
          return nextRecords[0] || null;
        });
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [userId, limit]);

  React.useEffect(() => {
    setSelected(null);
  }, [userId]);

  React.useEffect(() => {
    load();
  }, [userId, limit, refreshKey]);

  return e("section", null,
    e("div", { className: "toolbar prompts-toolbar" },
      e("select", { value: limit, onChange: (event) => setLimit(Number(event.target.value)) },
        [25, 50, 100, 200].map((item) => e("option", { key: item, value: item }, `${item} latest`))
      ),
      e("button", { onClick: load }, "Reload"),
      e("span", { className: "meta" }, `${records.length} rows`)
    ),
    e("div", { className: "grid" },
      e("div", { className: "list" },
        records.map((record) => e("div", {
          key: record.materialization_id,
          className: `item ${selected && selected.materialization_id === record.materialization_id ? "active" : ""}`,
          onClick: () => setSelected(record),
        },
          e("div", { className: "item-title" },
            e("span", null, formatTime(record.created_at)),
            e("span", { className: "badge" }, `${record.token_count || 0} tokens`)
          ),
          e("div", { className: "meta" }, record.query || "(empty user prompt)"),
          e("div", { className: "meta" }, `${(record.memories_included || []).length} memories · ${(record.skills_included || []).length} skills`)
        ))
      ),
      selected ? e(PromptDetail, { userId, record: selected, setError }) : e("div", { className: "empty" }, "Select a prompt.")
    )
  );
}

function PromptDetail({ userId, record, setError }) {
  const injected = record.rendered_prompt || "";
  const [evaluating, setEvaluating] = React.useState(false);
  const [evalRun, setEvalRun] = React.useState("");
  React.useEffect(() => {
    setEvalRun("");
  }, [record.materialization_id]);
  const runEval = () => {
    if (evaluating) return;
    setEvaluating(true);
    setError("");
    api(`/api/projects/${enc(userId)}/materializations/${enc(record.materialization_id)}/eval-runs/run`, {
      method: "POST",
      body: JSON.stringify({ suite: `prompt:${record.materialization_id}` }),
    })
      .then((data) => setEvalRun(data.run?.run_id || "completed"))
      .catch((err) => setError(err.message))
      .finally(() => setEvaluating(false));
  };
  return e("div", { className: "panel prompt-detail" },
    e("div", { className: "topbar" },
      e("div", null,
        e("h3", null, "Chat2Skill Prompt"),
        e("div", { className: "meta" }, `${record.materialization_id} · ${record.context_key || "project"} · ${formatTime(record.created_at)}`)
      ),
      e("div", { className: "actions compact" },
        e("button", { onClick: runEval, disabled: evaluating }, evaluating ? "Evaluating..." : "Run Eval")
      )
    ),
    evalRun ? e("div", { className: "meta panel-meta" }, `eval saved ${evalRun}`) : null,
    e("div", { className: "prompt-section" },
      e("h4", null, "User Prompt"),
      e("div", { className: "markdown prompt-text" }, record.query || "")
    ),
    e("div", { className: "prompt-section" },
      e("h4", null, "Injected Prompt"),
      injected
        ? e("div", { className: "markdown prompt-text" }, injected)
        : e("div", { className: "empty" }, "This older materialization did not save rendered prompt text.")
    ),
    e("div", { className: "prompt-section" },
      e("h4", null, "Included Skills"),
      (record.skills_included || []).length
        ? e("div", { className: "chip-list" }, record.skills_included.map((name) => e("span", { className: "chip", key: name }, name)))
        : e("div", { className: "empty compact-empty" }, "No skills recorded.")
    ),
    e("div", { className: "prompt-section" },
      e("h4", null, "Included Memories"),
      (record.memories_included || []).length
        ? e("div", { className: "chip-list" }, record.memories_included.map((id) => e("span", { className: "chip", key: id }, id)))
        : e("div", { className: "empty compact-empty" }, "No memories recorded.")
    )
  );
}

function EvalsTab({ userId, setError, refreshKey }) {
  const [runs, setRuns] = React.useState([]);
  const [selectedRun, setSelectedRun] = React.useState("");
  const [detail, setDetail] = React.useState(null);
  const [limit, setLimit] = React.useState(50);
  const overallTokensSaved = runs.reduce((total, run) => total + tokensSavedTotal(run), 0);

  const load = React.useCallback(() => {
    api(`/api/projects/${enc(userId)}/eval-runs?limit=${enc(limit)}`)
      .then((data) => {
        const nextRuns = data.eval_runs || [];
        setRuns(nextRuns);
        setSelectedRun((current) =>
          nextRuns.some((run) => run.run_id === current) ? current : (nextRuns[0]?.run_id || "")
        );
        if (!nextRuns.length) setDetail(null);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [userId, limit]);

  React.useEffect(() => {
    setSelectedRun("");
    setDetail(null);
  }, [userId]);

  React.useEffect(() => {
    load();
  }, [userId, limit, refreshKey]);

  React.useEffect(() => {
    if (!selectedRun) return;
    api(`/api/eval-runs/${enc(selectedRun)}?user_id=${enc(userId)}`)
      .then((data) => {
        setDetail(data);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [userId, selectedRun]);

  return e("section", { className: "eval-page" },
    e("div", { className: "toolbar prompts-toolbar" },
      e("select", { value: limit, onChange: (event) => setLimit(Number(event.target.value)) },
        [25, 50, 100, 200].map((item) => e("option", { key: item, value: item }, `${item} latest`))
      ),
      e("button", { onClick: load }, "Reload"),
      e("span", { className: "meta" }, `${runs.length} runs`),
      e("span", { className: "meta" }, `overall saved ${formatInteger(overallTokensSaved)} tokens`)
    ),
    e("div", { className: "eval-layout" },
      e("div", { className: "panel eval-runs-panel" },
        e("div", { className: "panel-heading" },
          e("h3", null, "Eval runs"),
          e("span", { className: "meta" }, `${runs.length} total`)
        ),
        e("div", { className: "eval-run-list" },
        runs.map((run) => e("button", {
          key: run.run_id,
          className: `eval-run-card ${run.run_id === selectedRun ? "active" : ""}`,
          onClick: () => setSelectedRun(run.run_id),
        },
          e("div", { className: "item-title" },
            e("span", null, run.suite || run.run_id),
            e("span", { className: `badge ${run.status === "completed" ? "ok" : "warn"}` }, run.status || "unknown")
          ),
          e("div", { className: "meta" }, `${run.project_passed ?? run.passed_cases}/${run.project_cases ?? run.total_cases} passed · score ${Number(run.score_mean || 0).toFixed(2)}`),
          e("div", { className: "meta" }, `saved ${formatInteger(tokensSavedTotal(run))} tokens`),
          e("div", { className: "meta" }, formatTime(run.finished_at || run.imported_at))
        ))
        )
      ),
      detail ? e(EvalRunDetail, { detail }) : e("div", { className: "empty" }, "Select an eval run.")
    )
  );
}

function EvalRunDetail({ detail }) {
  const run = detail.run || {};
  const cases = detail.cases || [];
  const [selectedCaseId, setSelectedCaseId] = React.useState(cases[0]?.case_id || "");
  React.useEffect(() => {
    setSelectedCaseId(cases[0]?.case_id || "");
  }, [run.run_id]);
  const selectedCase = cases.find((item) => item.case_id === selectedCaseId) || cases[0];
  return e("div", { className: "panel eval-report" },
    e("div", { className: "topbar" },
      e("div", null,
        e("h3", null, run.suite || "eval run"),
        e("div", { className: "meta" }, `${run.run_id} · ${formatTime(run.finished_at || run.started_at || run.imported_at)}`)
      ),
      e("span", { className: `badge ${run.status === "completed" ? "ok" : "warn"}` }, run.status || "unknown")
    ),
    e("div", { className: "metric-grid eval-metrics" },
      e(Metric, { label: "Pass rate", value: formatPercent(run.pass_rate) }),
      e(Metric, { label: "Passed", value: `${run.passed_cases || 0}/${run.total_cases || 0}` }),
      e(Metric, { label: "Score mean", value: Number(run.score_mean || 0).toFixed(2) }),
      e(Metric, { label: "Score stddev", value: Number(run.score_stddev || 0).toFixed(3) }),
      e(Metric, { label: "Overall tokens saved", value: formatInteger(tokensSavedTotal(run)) }),
      e(Metric, { label: "Quality delta", value: Number(run.metrics?.quality_delta_mean || 0).toFixed(2) })
    ),
    e("div", { className: "section-heading" },
      e("h3", null, "Cases"),
      e("span", { className: "meta" }, `${cases.length} total`)
    ),
    e("div", { className: "eval-case-table" },
      e("div", { className: "eval-case-head" },
        e("span", null, "Case"),
        e("span", null, "Dimension"),
        e("span", null, "Score"),
        e("span", null, "Status"),
        e("span", null, "Reason")
      ),
      cases.map((item) => e("button", {
        key: item.case_id,
        className: `eval-case-row ${selectedCase && item.case_id === selectedCase.case_id ? "active" : ""}`,
        onClick: () => setSelectedCaseId(item.case_id),
      },
        e("span", { className: "eval-run-title" }, item.name || item.case_id),
        e("span", null, item.dimension),
        e("span", null, Number(item.score || 0).toFixed(2)),
        e("span", { className: `badge ${item.status === "passed" ? "ok" : "warn"}` }, item.status),
        e("span", null, item.failure_reason || "no failure")
      ))
    ),
    selectedCase ? e(EvalCaseDetail, { item: selectedCase }) : null
  );
}

function EvalCaseDetail({ item }) {
  return e("div", { className: "eval-case-inspector" },
    e("div", { className: "topbar" },
      e("div", null,
        e("h4", null, item.name || item.case_id),
        e("div", { className: "meta" }, `${item.dimension} · ${item.case_id}`)
      ),
      e("span", { className: `badge ${item.status === "passed" ? "ok" : "warn"}` }, item.status)
    ),
    e("div", { className: "metric-grid" },
      Object.entries(item.metrics || {}).slice(0, 12).map(([key, value]) =>
        e(Metric, { key, label: key, value: String(value) })
      )
    ),
    (item.missing_expected_items || []).length
      ? e("div", { className: "prompt-section" },
        e("h4", null, "Missing Expected Items"),
        e("div", { className: "chip-list" }, item.missing_expected_items.map((value) => e("span", { className: "chip", key: value }, value)))
      )
      : null,
    (item.incorrect_items || []).length
      ? e("div", { className: "prompt-section" },
        e("h4", null, "Incorrect Items"),
        e("div", { className: "chip-list" }, item.incorrect_items.map((value) => e("span", { className: "chip", key: value }, value)))
      )
      : null,
    e("details", { className: "eval-artifacts" },
      e("summary", null, "Artifacts"),
      e("div", { className: "markdown prompt-text eval-artifact" }, JSON.stringify(item.artifacts || {}, null, 2))
    )
  );
}

function Metric({ label, value }) {
  return e("div", { className: "metric" },
    e("div", { className: "meta" }, label),
    e("strong", null, value)
  );
}

function tokensSavedTotal(run) {
  return Number(run?.metrics?.tokens_saved_total || 0);
}

function formatInteger(value) {
  return Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function ProjectSkillTab({ userId, setError, refreshProjects, refreshKey }) {
  const [data, setData] = React.useState(null);
  const [rebuilding, setRebuilding] = React.useState(false);
  const [evaluating, setEvaluating] = React.useState(false);
  const [evalRun, setEvalRun] = React.useState("");
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const load = React.useCallback(() => {
    api(`/api/projects/${enc(userId)}/project-skill`)
      .then((next) => {
        setData(next);
        setError("");
      })
      .catch((err) => {
        setData(null);
        setError(err.message);
      });
  }, [userId]);
  React.useEffect(() => {
    if (!editing) load();
  }, [userId, refreshKey, editing]);
  React.useEffect(() => {
    setEditing(false);
    setSaving(false);
    setRebuilding(false);
    setEvaluating(false);
    setEvalRun("");
    setDraft("");
  }, [userId]);
  React.useEffect(() => {
    if (!editing) {
      setDraft(data?.project_skill?.content || "");
    }
  }, [data, editing]);
  const rebuild = () => {
    if (rebuilding) return;
    setRebuilding(true);
    setError("");
    api(`/api/projects/${enc(userId)}/project-skill/rebuild`, { method: "POST", body: JSON.stringify({ recent_messages: [] }) })
      .then((next) => {
        setData({ project_skill: next.project_skill, sources: [] });
        refreshProjects();
      })
      .catch((err) => setError(err.message))
      .finally(() => setRebuilding(false));
  };
  const save = () => {
    if (saving) return;
    setSaving(true);
    setError("");
    api(`/api/projects/${enc(userId)}/project-skill`, {
      method: "PATCH",
      body: JSON.stringify({ content: draft }),
    })
      .then((next) => {
        setData(next);
        setEditing(false);
        refreshProjects();
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  };
  const runEval = () => {
    if (evaluating) return;
    setEvaluating(true);
    setError("");
    api(`/api/projects/${enc(userId)}/project-skill/eval-runs/run`, {
      method: "POST",
      body: JSON.stringify({ suite: "project-skill" }),
    })
      .then((next) => setEvalRun(next.run?.run_id || "completed"))
      .catch((err) => setError(err.message))
      .finally(() => setEvaluating(false));
  };
  if (!data) return e("div", { className: "empty" }, "No project skill saved yet.");
  const ps = data.project_skill || {};
  return e("div", { className: "panel" },
    e("div", { className: "topbar" },
      e("div", null,
        e("h3", null, ps.name || "project-skill"),
        e("div", { className: "meta" }, `version ${ps.version || 0} · ${ps.language || "n/a"} · updated ${ps.updated_at || ""}`)
      ),
      e("div", { className: "actions compact" },
        editing ? e(React.Fragment, null,
          e("button", { className: "primary", onClick: save, disabled: saving }, saving ? "Saving..." : "Save"),
          e("button", {
            onClick: () => {
              setDraft(ps.content || "");
              setEditing(false);
              setError("");
            },
            disabled: saving,
          }, "Cancel")
        ) : e("button", { onClick: () => setEditing(true) }, "Edit"),
        e("button", { onClick: runEval, disabled: evaluating || saving || editing }, evaluating ? "Evaluating..." : "Run Eval"),
        e("button", { className: "primary", onClick: rebuild, disabled: rebuilding || saving || editing }, rebuilding ? "Rebuilding..." : "Rebuild")
      )
    ),
    evalRun ? e("div", { className: "meta panel-meta" }, `eval saved ${evalRun}`) : null,
    editing
      ? e("textarea", { className: "project-skill-editor", value: draft, onChange: (event) => setDraft(event.target.value) })
      : e("div", { className: "markdown" }, ps.content || ""),
    e("h3", { style: { marginTop: 18 } }, "Source Snapshot"),
    (data.sources || []).length ? e("div", { className: "list" },
      data.sources.map((source) => e("div", { className: "item", key: `${source.project_skill_version}:${source.skill_name}` },
        e("div", { className: "item-title" }, e("span", null, source.skill_name), e("span", { className: "badge" }, source.skill_type || "")),
        e("div", { className: "meta" }, `evidence ${source.evidence_count || 0} · memory ${source.source_memory_count || 0} · confidence ${source.confidence || 0}`)
      ))
    ) : e("div", { className: "empty" }, "No source snapshot saved for this version.")
  );
}

function OverviewTab({ userId, setError, refreshKey }) {
  const [data, setData] = React.useState(null);
  React.useEffect(() => {
    api(`/api/projects/${enc(userId)}/overview`).then(setData).catch((err) => setError(err.message));
  }, [userId, refreshKey]);
  if (!data) return e("div", { className: "empty" }, "Loading overview.");
  return e("div", { className: "grid" },
    e(StatsPanel, { title: "Skill Status", rows: data.skill_status, labelKey: "status" }),
    e(StatsPanel, { title: "Skill Types", rows: data.skill_types, labelKey: "skill_type" }),
    e(StatsPanel, { title: "Memory Types", rows: data.memory_types, labelKey: "memory_type" }),
    e("div", { className: "panel" },
      e("h3", null, "Contexts"),
      (data.contexts || []).map((ctx) => e("div", { className: "item", key: ctx.context_key },
        e("div", { className: "item-title" }, e("span", null, ctx.context_key), e("span", { className: "badge" }, `${ctx.core_memory_length || 0} chars`)),
        e("div", { className: "meta" }, ctx.project_dir || ""),
        e("div", { className: "meta" }, ctx.updated_at || "")
      ))
    )
  );
}

function StatsPanel({ title, rows, labelKey }) {
  return e("div", { className: "panel" },
    e("h3", null, title),
    rows && rows.length ? rows.map((row) => e("div", { className: "item", key: row[labelKey] || "empty" },
      e("div", { className: "item-title" },
        e("span", null, row[labelKey] || "(empty)"),
        e("span", { className: "badge" }, row.count)
      )
    )) : e("div", { className: "empty" }, "No data.")
  );
}

function Field({ label, full, children }) {
  return e("div", { className: `field ${full ? "full" : ""}` },
    e("label", null, label),
    children
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(e(App));
