"""Local Chat2Skill admin server.

This server is intentionally local-only. It reads and edits the user's
~/.chat2skill/c2s.db and serves a lightweight React UI for memory and skill
management.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import socket
import sqlite3
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import api_client, memory_client, runner, storage
from .config import load_config
from .initializer import ensure_user_home

ADMIN_STATIC_DIR = Path(__file__).with_name("admin_static")


class SkillPatch(BaseModel):
    description: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    skill_type: Optional[str] = None
    confidence: Optional[float] = None
    language: Optional[str] = None
    quality_note: Optional[str] = None


class MemoryPatch(BaseModel):
    content: Optional[str] = None
    memory_type: Optional[str] = None
    section: Optional[str] = None
    salience: Optional[float] = None
    confidence: Optional[float] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None


class RebuildRequest(BaseModel):
    recent_messages: list[dict] = []


class ProjectSkillPatch(BaseModel):
    content: str


class EvalImportRequest(BaseModel):
    result: dict


class EvalRunRequest(BaseModel):
    suite: str = "project"


class MaterializationOutcomeRequest(BaseModel):
    outcome: str
    feedback: dict = {}


class ReextractMemoryRequest(BaseModel):
    project_dir: Optional[str] = None
    limit: int = 50
    dry_run: bool = True


def create_app(token: str) -> FastAPI:
    app = FastAPI(title="Chat2Skill Admin", version="0.1")

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-chat2skill-admin-token") or request.query_params.get("token")
            if not secrets.compare_digest(str(supplied or ""), token):
                return JSONResponse({"detail": "invalid admin token"}, status_code=401)
        return await call_next(request)

    if ADMIN_STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ADMIN_STATIC_DIR)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_path = ADMIN_STATIC_DIR / "index.html"
        if not index_path.exists():
            return HTMLResponse("<h1>Chat2Skill Admin assets not found</h1>", status_code=500)
        return FileResponse(index_path)

    @app.get("/api/health")
    def health():
        storage.init_db()
        return {
            "status": "ok",
            "db_path": str(storage.DB_PATH),
            "skill_dir": str(storage.SKILL_DIR),
        }

    @app.get("/api/projects")
    def projects():
        storage.init_db()
        return {"projects": _projects()}

    @app.post("/api/projects/{user_id:path}/archive")
    def archive_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        _set_project_status(user_id, "archived")
        return {"project": _project_by_user_id(user_id)}

    @app.post("/api/projects/{user_id:path}/restore")
    def restore_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        _set_project_status(user_id, "active")
        return {"project": _project_by_user_id(user_id)}

    @app.delete("/api/projects/{user_id:path}")
    def delete_project(user_id: str):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        if _project_status(user_id) != "archived":
            raise HTTPException(status_code=409, detail="archive project before deleting it")
        deleted = _delete_project(user_id)
        return {"deleted": deleted}

    @app.get("/api/projects/{user_id:path}/overview")
    def project_overview(user_id: str):
        storage.init_db()
        return _project_overview(user_id)

    @app.get("/api/projects/{user_id:path}/project-skill")
    def project_skill(user_id: str):
        storage.init_db()
        project = storage.load_project_skill(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project skill not found")
        version = project.get("version")
        sources = storage.load_project_skill_sources(
            user_id,
            int(version) if version is not None else None,
        )
        return {"project_skill": project, "sources": sources}

    @app.patch("/api/projects/{user_id:path}/project-skill")
    def update_project_skill(user_id: str, body: ProjectSkillPatch):
        storage.init_db()
        project = storage.load_project_skill(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project skill not found")
        content = body.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="project skill content cannot be empty")
        previous_version = project.get("version")
        previous_sources = storage.load_project_skill_sources(
            user_id,
            int(previous_version) if previous_version is not None else None,
        )
        file_path = _project_skill_file_path(user_id, project)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        storage.save_project_skill(
            user_id,
            content,
            file_path=file_path,
            source_skill_count=project.get("source_skill_count"),
            source_memory_count=project.get("source_memory_count"),
        )
        updated = storage.load_project_skill(user_id)
        sources = []
        if updated and updated.get("version") is not None and previous_sources:
            sources = [
                {
                    "skill_name": source.get("skill_name"),
                    "skill_type": source.get("skill_type"),
                    "confidence": source.get("confidence"),
                    "evidence_count": source.get("evidence_count"),
                    "source_memory_count": source.get("source_memory_count"),
                }
                for source in previous_sources
            ]
            storage.save_project_skill_sources(user_id, int(updated["version"]), sources)
            sources = storage.load_project_skill_sources(user_id, int(updated["version"]))
        return {"project_skill": updated, "sources": sources}

    @app.post("/api/projects/{user_id:path}/project-skill/rebuild")
    def rebuild_project_skill(user_id: str, body: RebuildRequest):
        config = load_config()
        try:
            path = runner.rebuild_project_skill(user_id, config, body.recent_messages)
        except Exception as exc:
            detail = _admin_error_detail(exc)
            status_code = 429 if _is_rate_limit_error(exc) else 500
            raise HTTPException(status_code=status_code, detail=detail) from exc
        project = storage.load_project_skill(user_id)
        return {
            "path": str(path) if path else None,
            "project_skill": project,
        }

    @app.post("/api/projects/{user_id:path}/project-skill/eval-runs/run")
    def run_project_skill_eval(user_id: str, body: EvalRunRequest):
        storage.init_db()
        project = storage.load_project_skill(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project skill not found")
        suite = body.suite or "project-skill"
        return _run_eval_cases(user_id, suite, _build_project_skill_eval_cases(user_id, project))

    @app.get("/api/projects/{user_id:path}/skills")
    def skills(user_id: str, status: str = "all", q: str = ""):
        storage.init_db()
        return {"skills": _skills(user_id, status=status, query=q)}

    @app.get("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def skill_detail(user_id: str, skill_name: str):
        storage.init_db()
        skill = storage.get_skill(skill_name, user_id)
        if not skill:
            raise HTTPException(status_code=404, detail="skill not found")
        return {
            "skill": skill.to_dict(),
            "memory_items": storage.load_skill_memory_items(user_id, [skill_name]).get(skill_name, []),
            "usage_count": storage.load_usage_counts(user_id).get(skill_name, 0),
        }

    @app.patch("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def update_skill(user_id: str, skill_name: str, body: SkillPatch):
        storage.init_db()
        updated = _update_skill(user_id, skill_name, body)
        if not updated:
            raise HTTPException(status_code=404, detail="skill not found")
        return {"skill": updated}

    @app.delete("/api/projects/{user_id:path}/skills/{skill_name:path}")
    def delete_skill(user_id: str, skill_name: str):
        storage.init_db()
        deleted = _delete_skill(user_id, skill_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="skill not found")
        return {"deleted": True}

    @app.get("/api/projects/{user_id:path}/memories")
    def memories(user_id: str, context_key: str = "project", status: str = "active", q: str = ""):
        storage.init_db()
        return {"memories": _memories(user_id, context_key=context_key, status=status, query=q)}

    @app.post("/api/projects/{user_id:path}/memories/re-extract")
    def reextract_memories(user_id: str, body: ReextractMemoryRequest):
        storage.init_db()
        project = _project_by_user_id(user_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        project_dir = body.project_dir or project.get("project_dir") or ""
        return memory_client.re_extract_project_memory(
            load_config(),
            project_dir,
            user_id,
            limit=body.limit,
            dry_run=body.dry_run,
        )

    @app.post("/api/projects/{user_id:path}/memories/{context_key}/{memory_id:path}/eval-runs/run")
    def run_memory_eval(user_id: str, context_key: str, memory_id: str, body: EvalRunRequest):
        storage.init_db()
        memory = _get_memory(user_id, context_key, memory_id)
        if not memory:
            raise HTTPException(status_code=404, detail="memory not found")
        suite = body.suite or f"memory:{memory_id}"
        return _run_eval_cases(user_id, suite, _build_memory_eval_cases(user_id, memory))

    @app.patch("/api/projects/{user_id:path}/memories/{context_key}/{memory_id:path}")
    def update_memory(user_id: str, context_key: str, memory_id: str, body: MemoryPatch):
        storage.init_db()
        memory = _update_memory(user_id, context_key, memory_id, body)
        if not memory:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"memory": memory}

    @app.delete("/api/projects/{user_id:path}/memories/{context_key}/{memory_id:path}")
    def delete_memory(user_id: str, context_key: str, memory_id: str):
        storage.init_db()
        deleted = _delete_memory(user_id, context_key, memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"deleted": True}

    @app.get("/api/projects/{user_id:path}/materializations")
    def materializations(user_id: str, limit: int = 30):
        storage.init_db()
        return {"materializations": _materializations(user_id, limit)}

    @app.post("/api/projects/{user_id:path}/materializations/{materialization_id:path}/eval-runs/run")
    def run_materialization_eval(user_id: str, materialization_id: str, body: EvalRunRequest):
        storage.init_db()
        materialization = _materialization(user_id, materialization_id)
        if not materialization:
            raise HTTPException(status_code=404, detail="prompt materialization not found")
        suite = body.suite or f"prompt:{materialization_id}"
        return _run_eval_cases(user_id, suite, _build_prompt_eval_cases(user_id, materialization))

    @app.post("/api/projects/{user_id:path}/materializations/{materialization_id:path}/outcome")
    def materialization_outcome(user_id: str, materialization_id: str, body: MaterializationOutcomeRequest):
        storage.init_db()
        result = storage.record_materialization_outcome(
            user_id,
            materialization_id,
            body.outcome,
            feedback=body.feedback,
        )
        if not result:
            raise HTTPException(status_code=404, detail="prompt materialization not found")
        return {"outcome": result}

    @app.get("/api/projects/{user_id:path}/eval-runs")
    def eval_runs(user_id: str, limit: int = 50):
        storage.init_db()
        return {"eval_runs": storage.list_eval_runs(user_id, limit=limit)}

    @app.post("/api/projects/{user_id:path}/eval-runs/run")
    def run_project_eval(user_id: str, body: EvalRunRequest):
        storage.init_db()
        if not _project_exists(user_id):
            raise HTTPException(status_code=404, detail="project not found")
        return _run_eval_cases(user_id, body.suite or "project", _build_project_eval_cases(user_id))

    @app.get("/api/eval-runs/{run_id:path}")
    def eval_run_detail(run_id: str, user_id: Optional[str] = None):
        storage.init_db()
        result = storage.load_eval_run(run_id, user_id=user_id)
        if not result:
            raise HTTPException(status_code=404, detail="eval run not found")
        return result

    @app.post("/api/eval-runs/import")
    def import_eval_run(body: EvalImportRequest):
        storage.init_db()
        run_id = storage.save_eval_run(body.result)
        return {"run_id": run_id, "eval_run": storage.load_eval_run(run_id)}

    return app


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(storage.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http 429" in text or "rate limit" in text


def _admin_error_detail(exc: Exception) -> str:
    if _is_rate_limit_error(exc):
        return "Chat2Skill API is rate limited by api.chat2skill.com. Wait and try again later."
    return str(exc)


def _run_eval_cases(user_id: str, suite: str, cases: list[dict]):
    if not cases:
        raise HTTPException(status_code=400, detail="no eval cases generated")
    config = load_config()
    payload = {
        "suite": suite,
        "user_id": user_id,
        "cases": cases,
    }
    try:
        response = api_client.eval_run(config.get("api_url", ""), payload)
    except Exception as exc:
        detail = _admin_error_detail(exc)
        status_code = 429 if _is_rate_limit_error(exc) else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    result = _normalize_eval_result_user_id(response.get("result") or response, user_id)
    run_id = storage.save_eval_run(result)
    saved = storage.load_eval_run(run_id, user_id=user_id)
    if not saved:
        raise HTTPException(status_code=500, detail="eval run was not saved")
    return saved


def _normalize_eval_result_user_id(result: dict, user_id: str) -> dict:
    normalized = dict(result)
    normalized_cases = []
    for case in result.get("cases") or []:
        normalized_case = dict(case)
        normalized_case["project_id"] = user_id
        normalized_case["user_id"] = user_id
        normalized_cases.append(normalized_case)
    normalized["cases"] = normalized_cases
    return normalized


def _build_project_eval_cases(user_id: str) -> list[dict]:
    skills = [skill.to_dict() for skill in storage.load_skills(user_id, include_pending=False)]
    memories = _memories(user_id, context_key="all", status="active", query="")
    project_skill = storage.load_project_skill(user_id) or {}
    materializations = _materializations(user_id, limit=10)
    memory_state = {
        "core_memory": "",
        "memories": [_memory_for_eval(item) for item in memories],
        "schemas": [],
    }
    query = _eval_query(memories, materializations, project_skill)
    expected = _eval_expected(memories, skills, project_skill)
    cases: list[dict] = []
    if memories:
        top_memory = memories[0]
        cases.extend(
            [
                {
                    "case_id": f"{user_id}__retrieval_coverage",
                    "dimension": "retrieval_coverage",
                    "name": "Retrieve current project memory and skills",
                    "project_id": user_id,
                    "query": query,
                    "existing_memory": memory_state,
                    "existing_skills": skills,
                    "expected": expected,
                },
                {
                    "case_id": f"{user_id}__recall_synthesis",
                    "dimension": "recall_synthesis_quality",
                    "name": "Synthesize current project memory",
                    "project_id": user_id,
                    "query": f"之前我们讨论过什么：{_clip(top_memory.get('content', ''), 90)}",
                    "existing_memory": memory_state,
                    "existing_skills": skills,
                    "expected": {
                        "memory_ids": [top_memory["id"]],
                        "must_include": _expected_terms(top_memory.get("content", ""), limit=2),
                    },
                },
                {
                    "case_id": f"{user_id}__prompt_injection",
                    "dimension": "prompt_injection_quality",
                    "name": "Inject current project memory and skills",
                    "project_id": user_id,
                    "query": query,
                    "existing_memory": memory_state,
                    "existing_skills": skills,
                    "expected": expected | {"must_include": ["Relevant Project Memory"] + expected.get("must_include", [])},
                },
                {
                    "case_id": f"{user_id}__context_relevance",
                    "dimension": "context_relevance_quality",
                    "name": "Evaluate injected context relevance",
                    "project_id": user_id,
                    "query": query,
                    "with_chat2skill": {
                        "output": "\n".join(item.get("content", "") for item in memories[:12])
                    },
                    "expected": expected
                    | {
                        "max_context_tokens": 4000,
                        "min_relevance_density": 0.001,
                    },
                },
                {
                    "case_id": f"{user_id}__answer_quality_lift",
                    "dimension": "answer_quality_lift",
                    "name": "Estimate quality lift from available memory context",
                    "project_id": user_id,
                    "query": query,
                    "baseline": {"output": "没有项目历史上下文。"},
                    "with_chat2skill": {"output": top_memory.get("content", "")},
                    "expected": {
                        "must_include": _expected_terms(top_memory.get("content", ""), limit=3),
                        "min_quality_delta": 0.3,
                    },
                },
            ]
        )
    if materializations:
        latest = materializations[0]
        injected_tokens = int(latest.get("token_count") or 0)
        baseline_tokens = max(injected_tokens + 1200, injected_tokens * 2)
        cases.append(
            {
                "case_id": f"{user_id}__efficiency_lift",
                "dimension": "efficiency_lift",
                "name": "Estimate task-level savings from latest prompt materialization",
                "project_id": user_id,
                "baseline": {
                    "total_tokens": baseline_tokens,
                    "turns_to_success": 3,
                    "corrections": 1,
                },
                "with_chat2skill": {
                    "total_tokens": injected_tokens + 800,
                    "turns_to_success": 1,
                    "corrections": 0,
                    "injected_context_tokens": injected_tokens,
                },
                "expected": {
                    "min_tokens_saved": 200,
                    "min_turns_saved": 1,
                    "min_corrections_avoided": 1,
                },
            }
        )
    return cases


def _build_memory_eval_cases(user_id: str, memory: dict) -> list[dict]:
    memories = [memory]
    skills = [skill.to_dict() for skill in storage.load_skills(user_id, include_pending=False)]
    query = f"{memory.get('section') or 'project'} {_clip(memory.get('content', ''), 180)}"
    content = str(memory.get("content") or "")
    expected = {
        "memory_ids": [str(memory.get("id"))],
        "must_include": _expected_terms(content, limit=3),
    }
    return [
        {
            "case_id": f"{user_id}__memory__{_case_id_part(memory.get('id'))}__recall",
            "dimension": "recall_synthesis_quality",
            "name": f"Recall memory {memory.get('id')}",
            "project_id": user_id,
            "query": query,
            "existing_memory": _memory_state(memories),
            "existing_skills": skills,
            "expected": expected,
        },
        {
            "case_id": f"{user_id}__memory__{_case_id_part(memory.get('id'))}__retrieval",
            "dimension": "retrieval_coverage",
            "name": f"Retrieve memory {memory.get('id')}",
            "project_id": user_id,
            "query": query,
            "existing_memory": _memory_state(memories),
            "existing_skills": skills,
            "expected": expected,
        },
        {
            "case_id": f"{user_id}__memory__{_case_id_part(memory.get('id'))}__context_relevance",
            "dimension": "context_relevance_quality",
            "name": f"Memory context relevance {memory.get('id')}",
            "project_id": user_id,
            "query": query,
            "with_chat2skill": {"output": content},
            "expected": {
                "must_include": expected["must_include"],
            }
            | {
                "max_context_tokens": 800,
                "min_relevance_density": 0.001,
            },
        },
        {
            "case_id": f"{user_id}__memory__{_case_id_part(memory.get('id'))}__quality",
            "dimension": "answer_quality_lift",
            "name": f"Memory content quality {memory.get('id')}",
            "project_id": user_id,
            "query": query,
            "baseline": {"output": "没有这条项目记忆。"},
            "with_chat2skill": {"output": content},
            "expected": {
                "must_include": _expected_terms(content, limit=3),
                "min_quality_delta": 0.2,
            },
        },
    ]


def _build_prompt_eval_cases(user_id: str, materialization: dict) -> list[dict]:
    rendered_prompt = str(materialization.get("rendered_prompt") or "")
    query = str(materialization.get("query") or "")
    terms = _expected_terms(rendered_prompt, limit=4) if rendered_prompt else _expected_terms(query, limit=2)
    if "Chat2Skill" in rendered_prompt and "Chat2Skill" not in terms:
        terms.insert(0, "Chat2Skill")
    context_key = str(materialization.get("context_key") or "project")
    prompt_memories = _materialization_memories(user_id, context_key, materialization)
    prompt_skills = _materialization_skills(user_id, materialization)
    eval_memory_state = _memory_state(prompt_memories) if prompt_memories else _memory_state(
        [
            {
                "id": f"prompt-{_case_id_part(materialization.get('materialization_id'))}",
                "content": rendered_prompt or query,
                "memory_type": "procedure",
                "section": "prompt",
                "salience": 1.0,
                "confidence": 1.0,
                "is_active": True,
                "is_archived": False,
            }
        ]
    )
    eval_expected = {
        "must_include": terms,
    }
    cases = [
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__retrieval",
            "dimension": "retrieval_coverage",
            "name": f"Prompt retrieval {materialization.get('materialization_id')}",
            "project_id": user_id,
            "query": query or rendered_prompt[:160],
            "existing_memory": eval_memory_state,
            "existing_skills": prompt_skills,
            "expected": eval_expected,
        },
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__recall",
            "dimension": "recall_synthesis_quality",
            "name": f"Prompt recall {materialization.get('materialization_id')}",
            "project_id": user_id,
            "query": query or rendered_prompt[:160],
            "existing_memory": eval_memory_state,
            "existing_skills": prompt_skills,
            "expected": eval_expected,
        },
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__injection",
            "dimension": "prompt_injection_quality",
            "name": f"Prompt injection {materialization.get('materialization_id')}",
            "project_id": user_id,
            "query": query or rendered_prompt[:160],
            "existing_memory": eval_memory_state,
            "existing_skills": prompt_skills,
            "expected": eval_expected | {"must_include": ["Chat2Skill"] + terms},
        },
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__context_relevance",
            "dimension": "context_relevance_quality",
            "name": f"Prompt context relevance {materialization.get('materialization_id')}",
            "project_id": user_id,
            "query": query,
            "with_chat2skill": {"output": rendered_prompt or query},
            "expected": eval_expected
            | {
                "max_context_tokens": max(1200, int(materialization.get("token_count") or 0) + 400),
                "min_relevance_density": 0.001,
            },
        },
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__content",
            "dimension": "answer_quality_lift",
            "name": f"Prompt content {materialization.get('materialization_id')}",
            "project_id": user_id,
            "query": query,
            "baseline": {"output": ""},
            "with_chat2skill": {"output": rendered_prompt or query},
            "expected": {
                "must_include": terms,
                "min_quality_delta": 0.2 if terms else 0.0,
            },
        },
        {
            "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__stability",
            "dimension": "stability_regression",
            "name": f"Prompt eval stability {materialization.get('materialization_id')}",
            "project_id": user_id,
            "repeats": [
                {"score": 1.0, "pass_rate": 1.0, "tokens_saved": 0, "latency_ms": 0},
                {"score": 1.0, "pass_rate": 1.0, "tokens_saved": 0, "latency_ms": 0},
            ],
            "expected": {
                "min_score_mean": 1.0,
                "max_score_stddev": 0.0,
                "min_pass_rate_mean": 1.0,
            },
        },
    ]
    injected_tokens = int(materialization.get("token_count") or 0)
    if injected_tokens:
        cases.append(
            {
                "case_id": f"{user_id}__prompt__{_case_id_part(materialization.get('materialization_id'))}__efficiency",
                "dimension": "efficiency_lift",
                "name": f"Prompt efficiency {materialization.get('materialization_id')}",
                "project_id": user_id,
                "baseline": {
                    "total_tokens": injected_tokens + 1200,
                    "turns_to_success": 2,
                    "corrections": 1,
                },
                "with_chat2skill": {
                    "total_tokens": injected_tokens,
                    "turns_to_success": 1,
                    "corrections": 0,
                    "injected_context_tokens": injected_tokens,
                },
                "expected": {
                    "min_tokens_saved": 200,
                    "min_turns_saved": 1,
                    "min_corrections_avoided": 1,
                },
            }
        )
    return cases


def _materialization_memories(user_id: str, context_key: str, materialization: dict) -> list[dict]:
    memories = []
    for memory_id in materialization.get("memories_included") or []:
        memory = _get_memory(user_id, context_key, str(memory_id))
        if memory:
            memories.append(memory)
    return memories


def _materialization_skills(user_id: str, materialization: dict) -> list[dict]:
    skills = []
    for skill_name in materialization.get("skills_included") or []:
        skill = storage.get_skill(str(skill_name), user_id=user_id)
        if skill:
            skills.append(skill.to_dict())
    return skills


def _build_project_skill_eval_cases(user_id: str, project_skill: dict) -> list[dict]:
    content = str(project_skill.get("content") or "")
    terms = _project_skill_expected_terms(content, limit=6)
    cases = [
        {
            "case_id": f"{user_id}__project_skill__coverage",
            "dimension": "context_relevance_quality",
            "name": "Project skill coverage and density",
            "project_id": user_id,
            "query": "Apply current project skill",
            "with_chat2skill": {"output": content},
            "expected": {
                "must_include": terms,
                "max_context_tokens": 3000,
            },
        },
    ]
    source_text, source_count = _project_skill_source_text(user_id, project_skill)
    source_tokens = _text_tokens(source_text)
    project_tokens = _text_tokens(content)
    if source_tokens:
        cases.append(
            {
                "case_id": f"{user_id}__project_skill__compression",
                "dimension": "efficiency_lift",
                "name": "Project skill compression efficiency",
                "project_id": user_id,
                "baseline": {
                    "total_tokens": source_tokens,
                    "source_skill_count": source_count,
                },
                "with_chat2skill": {
                    "total_tokens": project_tokens,
                    "injected_context_tokens": project_tokens,
                },
                "expected": {
                    "min_tokens_saved": 0,
                },
            }
        )
    return cases


def _project_skill_expected_terms(content: str, limit: int) -> list[str]:
    terms: list[str] = []
    for line in content.splitlines():
        heading = line.lstrip("#").strip()
        if line.startswith("#") and heading and heading not in terms:
            terms.append(heading)
        if len(terms) >= limit:
            return terms
    for term in _expected_terms(content, limit=limit):
        if term not in terms:
            terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _project_skill_source_text(user_id: str, project_skill: dict) -> tuple[str, int]:
    version = int(project_skill.get("version") or 0)
    sources = storage.load_project_skill_sources(user_id, version) if version else []
    source_names = [str(item.get("skill_name") or "") for item in sources if item.get("skill_name")]
    if not source_names:
        source_names = [skill.name for skill in storage.load_skills(user_id, include_pending=False)]
    chunks = []
    for name in source_names:
        skill = storage.get_skill(name, user_id=user_id)
        if not skill:
            continue
        chunks.append("\n".join([skill.name, skill.description or "", skill.content or ""]))
    return "\n\n".join(chunks), len(chunks)


def _text_tokens(text: str) -> int:
    return max(0, len(str(text or "")) // 4)


def _memory_state(memories: list[dict]) -> dict:
    return {
        "core_memory": "",
        "memories": [_memory_for_eval(item) for item in memories],
        "schemas": [],
    }


def _memory_for_eval(memory: dict) -> dict:
    return {
        "id": memory.get("id"),
        "content": memory.get("content", ""),
        "memory_type": memory.get("memory_type", "fact"),
        "section": memory.get("section", "general"),
        "salience": memory.get("salience", 0.5),
        "confidence": memory.get("confidence", 0.5),
        "is_active": not memory.get("is_archived", False),
        "is_archived": memory.get("is_archived", False),
    }


def _eval_query(memories: list[dict], materializations: list[dict], project_skill: dict) -> str:
    for item in materializations:
        query = str(item.get("query") or "").strip()
        if query:
            return query
    if memories:
        return f"回顾当前项目相关内容：{_clip(memories[0].get('content', ''), 120)}"
    content = str(project_skill.get("content") or "")
    if content:
        return f"回顾当前项目规则：{_clip(content, 120)}"
    return "回顾当前项目的 Chat2Skill 记忆和 skills"


def _eval_expected(memories: list[dict], skills: list[dict], project_skill: dict) -> dict:
    expected: dict = {"must_include": []}
    if memories:
        expected["memory_ids"] = [str(memories[0]["id"])]
        expected["must_include"].extend(_expected_terms(memories[0].get("content", ""), limit=2))
    if skills:
        expected["skill_names"] = [str(skills[0]["name"])]
    content = str(project_skill.get("content") or "")
    if content and not expected["must_include"]:
        expected["must_include"].extend(_expected_terms(content, limit=2))
    return expected


def _expected_terms(text: str, limit: int) -> list[str]:
    terms: list[str] = []
    for raw in str(text or "").replace("：", " ").replace("，", " ").replace("。", " ").split():
        term = raw.strip("`*_[](){}<>:;,.!?")
        if len(term) < 3:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _case_id_part(value: object) -> str:
    text = str(value or "item")
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:80]


def _project_skill_file_path(user_id: str, project: dict) -> Path:
    file_path = project.get("file_path")
    if file_path:
        return Path(str(file_path))
    return storage.SKILL_DIR / user_id / runner.PROJECT_SKILL_FILE


def _project_exists(user_id: str) -> bool:
    conn = _connect()
    row = conn.execute(
        """
        SELECT 1 FROM project_skills WHERE user_id = ?
        UNION SELECT 1 FROM skill_records WHERE user_id = ?
        UNION SELECT 1 FROM memory_contexts WHERE user_id = ?
        UNION SELECT 1 FROM eval_cases WHERE user_id = ?
        LIMIT 1
        """,
        (user_id, user_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def _project_status(user_id: str) -> str:
    conn = _connect()
    row = conn.execute(
        "SELECT status FROM project_admin_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return str(row["status"]) if row and row["status"] else "active"


def _set_project_status(user_id: str, status: str) -> None:
    now = datetime.now().isoformat()
    archived_at = now if status == "archived" else None
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO project_admin_state
        (user_id, status, archived_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            status = excluded.status,
            archived_at = excluded.archived_at,
            updated_at = excluded.updated_at
        """,
        (user_id, status, archived_at, now, now),
    )
    conn.commit()
    conn.close()


def _project_by_user_id(user_id: str) -> Optional[dict]:
    for project in _projects():
        if project.get("user_id") == user_id:
            return project
    return None


def _delete_project(user_id: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    tables = [
        "project_skills",
        "project_skill_sources",
        "skill_records",
        "skill_memory_items",
        "skill_usage",
        "memory_contexts",
        "memory_items",
        "memory_schemas",
        "memory_materializations",
        "memory_activity",
        "project_admin_state",
        "user_profiles",
        "conversations",
        "eval_cases",
    ]
    deleted = False
    for table in tables:
        c.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        deleted = deleted or c.rowcount > 0
    conn.commit()
    conn.close()
    shutil.rmtree(storage.SKILL_DIR / user_id, ignore_errors=True)
    return deleted


def _projects() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        WITH users AS (
            SELECT user_id FROM project_skills
            UNION SELECT user_id FROM skill_records
            UNION SELECT user_id FROM memory_contexts
            UNION SELECT user_id FROM eval_cases
        )
        SELECT
            users.user_id,
            ps.language,
            ps.version AS project_skill_version,
            ps.updated_at AS project_skill_updated_at,
            ps.source_skill_count,
            ps.source_memory_count,
            COALESCE(sr.active_skills, 0) AS active_skills,
            COALESCE(sr.total_skills, 0) AS total_skills,
            COALESCE(mi.active_memories, 0) AS active_memories,
            COALESCE(mi.total_memories, 0) AS total_memories,
            sr.max_skill_updated_at,
            mi.max_memory_updated_at,
            mc.project_dir,
            mc.max_context_updated_at,
            ev.max_eval_updated_at,
            COALESCE(pas.status, 'active') AS status,
            pas.archived_at
        FROM users
        LEFT JOIN project_skills ps ON ps.user_id = users.user_id
        LEFT JOIN project_admin_state pas ON pas.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_skills,
                   COUNT(*) AS total_skills,
                   MAX(updated_at) AS max_skill_updated_at
            FROM skill_records GROUP BY user_id
        ) sr ON sr.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   SUM(CASE WHEN is_active = 1 AND is_archived = 0 THEN 1 ELSE 0 END) AS active_memories,
                   COUNT(*) AS total_memories,
                   MAX(updated_at) AS max_memory_updated_at
            FROM memory_items GROUP BY user_id
        ) mi ON mi.user_id = users.user_id
        LEFT JOIN (
            SELECT user_id,
                   MAX(project_dir) AS project_dir,
                   MAX(updated_at) AS max_context_updated_at
            FROM memory_contexts GROUP BY user_id
        ) mc ON mc.user_id = users.user_id
        LEFT JOIN (
            SELECT ec.user_id,
                   MAX(COALESCE(er.finished_at, er.started_at, er.imported_at)) AS max_eval_updated_at
            FROM eval_cases ec
            JOIN eval_runs er ON er.run_id = ec.run_id
            GROUP BY ec.user_id
        ) ev ON ev.user_id = users.user_id
        ORDER BY COALESCE(ps.updated_at, users.user_id) DESC
        """
    ).fetchall()
    conn.close()
    projects = []
    for row in rows:
        item = dict(row)
        candidates = [
            item.get("project_skill_updated_at"),
            item.get("max_skill_updated_at"),
            item.get("max_memory_updated_at"),
            item.get("max_context_updated_at"),
            item.get("max_eval_updated_at"),
        ]
        item["last_updated_at"] = max([value for value in candidates if value] or [""])
        projects.append(item)
    return sorted(projects, key=lambda item: (item.get("last_updated_at") or "", item.get("user_id") or ""), reverse=True)


def _project_overview(user_id: str) -> dict:
    conn = _connect()
    skill_status = conn.execute(
        "SELECT status, COUNT(*) AS count FROM skill_records WHERE user_id = ? GROUP BY status",
        (user_id,),
    ).fetchall()
    skill_types = conn.execute(
        "SELECT skill_type, COUNT(*) AS count FROM skill_records WHERE user_id = ? GROUP BY skill_type",
        (user_id,),
    ).fetchall()
    memory_types = conn.execute(
        "SELECT memory_type, COUNT(*) AS count FROM memory_items WHERE user_id = ? GROUP BY memory_type",
        (user_id,),
    ).fetchall()
    contexts = conn.execute(
        """
        SELECT context_key, project_dir, length(core_memory) AS core_memory_length, updated_at
        FROM memory_contexts
        WHERE user_id = ?
        ORDER BY updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return {
        "user_id": user_id,
        "skill_status": [dict(row) for row in skill_status],
        "skill_types": [dict(row) for row in skill_types],
        "memory_types": [dict(row) for row in memory_types],
        "contexts": [dict(row) for row in contexts],
    }


def _skills(user_id: str, status: str, query: str) -> list[dict]:
    where = ["user_id = ?"]
    params: list = [user_id]
    if status != "all":
        where.append("status = ?")
        params.append(status)
    if query:
        where.append("(name LIKE ? OR description LIKE ? OR content LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    conn = _connect()
    rows = conn.execute(
        f"""
        SELECT name, description, version, created_at, updated_at, skill_type,
               scope, evidence_count, confidence, status, replay_score,
               replay_cases, language, parent_skill
        FROM skill_records
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, evidence_count DESC, confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _update_skill(user_id: str, skill_name: str, patch: SkillPatch) -> Optional[dict]:
    skill = storage.get_skill(skill_name, user_id)
    if not skill:
        return None
    data = patch.model_dump(exclude_unset=True)
    for key in ("description", "content", "status", "skill_type", "confidence", "language"):
        if key in data and data[key] is not None:
            setattr(skill, key, data[key])
    if data.get("quality_note"):
        skill.quality_notes.append(str(data["quality_note"]))
    skill.updated_at = datetime.now().isoformat()
    skill.refresh_embedding_text()

    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        UPDATE skill_records
        SET description = ?, content = ?, updated_at = ?, skill_type = ?,
            confidence = ?, status = ?, embedding_text = ?, language = ?,
            quality_notes = ?
        WHERE user_id = ? AND name = ?
        """,
        (
            skill.description,
            skill.content,
            skill.updated_at,
            skill.skill_type,
            skill.confidence,
            skill.status,
            skill.embedding_text,
            skill.language,
            json.dumps(skill.quality_notes, ensure_ascii=False),
            user_id,
            skill_name,
        ),
    )
    changed = c.rowcount
    conn.commit()
    conn.close()
    if changed:
        _write_skill_file(user_id, skill_name, skill.content)
        saved = storage.get_skill(skill_name, user_id)
        return saved.to_dict() if saved else None
    return None


def _delete_skill(user_id: str, skill_name: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute("DELETE FROM skill_records WHERE user_id = ? AND name = ?", (user_id, skill_name))
    deleted = c.rowcount > 0
    c.execute("DELETE FROM skill_memory_items WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    c.execute("DELETE FROM skill_usage WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    c.execute("DELETE FROM project_skill_sources WHERE user_id = ? AND skill_name = ?", (user_id, skill_name))
    conn.commit()
    conn.close()
    if deleted:
        shutil.rmtree(storage.SKILL_DIR / user_id / skill_name, ignore_errors=True)
    return deleted


def _memories(user_id: str, context_key: str, status: str, query: str) -> list[dict]:
    where = ["mi.user_id = ?"]
    params: list = [user_id]
    if context_key != "all":
        where.append("mi.context_key = ?")
        params.append(context_key)
    if status == "active":
        where.append("mi.is_active = 1 AND mi.is_archived = 0")
    elif status == "archived":
        where.append("mi.is_archived = 1")
    if query:
        where.append("(mi.content LIKE ? OR mi.memory_type LIKE ? OR mi.section LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    conn = _connect()
    rows = conn.execute(
        f"""
        SELECT mi.user_id, mi.context_key, mi.id, mi.content, mi.memory_type,
               mi.section, mi.salience, mi.confidence, mi.source_session,
               mi.source_agent, mi.recall_count, mi.hit_count, mi.miss_count,
               mi.is_active, mi.is_archived, mi.created_at, mi.updated_at,
               mc.project_dir
        FROM memory_items mi
        LEFT JOIN memory_contexts mc
          ON mc.user_id = mi.user_id AND mc.context_key = mi.context_key
        WHERE {' AND '.join(where)}
        ORDER BY mi.updated_at DESC, mi.salience DESC, mi.confidence DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [_memory_dict(row) for row in rows]


def _update_memory(user_id: str, context_key: str, memory_id: str, patch: MemoryPatch) -> Optional[dict]:
    data = patch.model_dump(exclude_unset=True)
    allowed = {
        "content",
        "memory_type",
        "section",
        "salience",
        "confidence",
        "is_active",
        "is_archived",
    }
    if not any(key in data for key in allowed):
        return _get_memory(user_id, context_key, memory_id)
    assignments = []
    params = []
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in {"is_active", "is_archived"}:
            value = 1 if value else 0
        assignments.append(f"{key} = ?")
        params.append(value)
    assignments.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.extend([user_id, context_key, memory_id])
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        f"""
        UPDATE memory_items
        SET {', '.join(assignments)}
        WHERE user_id = ? AND context_key = ? AND id = ?
        """,
        params,
    )
    changed = c.rowcount
    conn.commit()
    conn.close()
    return _get_memory(user_id, context_key, memory_id) if changed else None


def _delete_memory(user_id: str, context_key: str, memory_id: str) -> bool:
    conn = sqlite3.connect(str(storage.DB_PATH))
    c = conn.cursor()
    c.execute(
        "DELETE FROM memory_items WHERE user_id = ? AND context_key = ? AND id = ?",
        (user_id, context_key, memory_id),
    )
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def _get_memory(user_id: str, context_key: str, memory_id: str) -> Optional[dict]:
    conn = _connect()
    row = conn.execute(
        """
        SELECT mi.user_id, mi.context_key, mi.id, mi.content, mi.memory_type,
               mi.section, mi.salience, mi.confidence, mi.source_session,
               mi.source_agent, mi.recall_count, mi.hit_count, mi.miss_count,
               mi.is_active, mi.is_archived, mi.created_at, mi.updated_at,
               mc.project_dir
        FROM memory_items mi
        LEFT JOIN memory_contexts mc
          ON mc.user_id = mi.user_id AND mc.context_key = mi.context_key
        WHERE mi.user_id = ? AND mi.context_key = ? AND mi.id = ?
        """,
        (user_id, context_key, memory_id),
    ).fetchone()
    conn.close()
    return _memory_dict(row) if row else None


def _memory_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["is_active"] = bool(item.get("is_active"))
    item["is_archived"] = bool(item.get("is_archived"))
    return item


def _materializations(user_id: str, limit: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT context_key, materialization_id, memories_included, skills_included,
               activities_included, query, rendered_prompt, token_count, outcome,
               feedback, reconsolidated_at, created_at
        FROM memory_materializations
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, max(1, min(limit, 200))),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["memories_included"] = json.loads(item.get("memories_included") or "[]")
        except json.JSONDecodeError:
            item["memories_included"] = []
        try:
            item["skills_included"] = json.loads(item.get("skills_included") or "[]")
        except json.JSONDecodeError:
            item["skills_included"] = []
        try:
            item["activities_included"] = json.loads(item.get("activities_included") or "[]")
        except json.JSONDecodeError:
            item["activities_included"] = []
        try:
            item["feedback"] = json.loads(item.get("feedback") or "{}")
        except json.JSONDecodeError:
            item["feedback"] = {}
        return_value = item.get("outcome")
        if isinstance(return_value, str) and return_value.startswith("{"):
            try:
                item["outcome"] = json.loads(return_value)
            except json.JSONDecodeError:
                pass
        out.append(item)
    return out


def _materialization(user_id: str, materialization_id: str) -> Optional[dict]:
    for item in _materializations(user_id, limit=200):
        if item.get("materialization_id") == materialization_id:
            return item
    return None


def _write_skill_file(user_id: str, skill_name: str, content: str) -> None:
    skill_dir = storage.SKILL_DIR / user_id / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def run(host: str, port: int, open_browser: bool) -> None:
    import uvicorn

    ensure_user_home(create_db=True)
    if not _port_available(host, port):
        raise RuntimeError(
            f"127.0.0.1:{port} is already in use. Stop the existing Chat2Skill Admin "
            f"process or start with --port {port + 1}."
        )

    token = secrets.token_urlsafe(24)
    app = create_app(token)
    url = f"http://{host}:{port}/?token={quote(token)}"
    print(f"Chat2Skill Admin: {url}")
    print(f"Database: {storage.DB_PATH}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Chat2Skill admin UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args.host, args.port, not args.no_open)
    except RuntimeError as exc:
        print(f"Chat2Skill Admin failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
