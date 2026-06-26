"""Local storage. Runs on USER's machine.

Stores: conversations, skills, user profile.
"""
import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .config import DATA_HOME
from .models import MemoryItem, Skill, UserModel
from .similarity import (
    MERGE_COSINE_THRESHOLD,
    MERGE_LEXICAL_THRESHOLD,
    cosine as _cosine,
    jaccard as _jaccard,
    tokens as _tokens,
)

DB_PATH = DATA_HOME / "c2s.db"
LEGACY_DB_PATH = DATA_HOME / "chat2skill.db"
SKILL_DIR = DATA_HOME / "skills"


def init_db():
    """Initialize SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_db()
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            messages TEXT,
            feedback TEXT,
            timestamp TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            name TEXT PRIMARY KEY,
            description TEXT,
            content TEXT,
            version INTEGER,
            source_sessions TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skill_records (
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            content TEXT,
            version INTEGER,
            source_sessions TEXT,
            created_at TEXT,
            updated_at TEXT,
            skill_type TEXT,
            scope TEXT,
            evidence_count INTEGER,
            confidence REAL,
            status TEXT,
            embedding_text TEXT,
            embedding_vector TEXT,
            embedding_model TEXT,
            replay_score REAL,
            replay_cases INTEGER,
            replay_wins INTEGER,
            replay_losses INTEGER,
            replay_rationale TEXT,
            language TEXT,
            parent_skill TEXT,
            quality_notes TEXT,
            judge_rationale TEXT,
            PRIMARY KEY (user_id, name)
        )
    """)

    _ensure_column(c, "skill_records", "embedding_vector", "TEXT")
    _ensure_column(c, "skill_records", "embedding_model", "TEXT")
    _ensure_column(c, "skill_records", "replay_score", "REAL")
    _ensure_column(c, "skill_records", "replay_cases", "INTEGER")
    _ensure_column(c, "skill_records", "replay_wins", "INTEGER")
    _ensure_column(c, "skill_records", "replay_losses", "INTEGER")
    _ensure_column(c, "skill_records", "replay_rationale", "TEXT")
    _ensure_column(c, "skill_records", "language", "TEXT")
    _migrate_memory_table_names(c)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skill_memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            skill_name TEXT,
            item_type TEXT,
            title TEXT,
            description TEXT,
            content TEXT,
            evidence TEXT,
            source_session TEXT,
            confidence REAL,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            profile_json TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS project_skills (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            language TEXT,
            file_path TEXT,
            version INTEGER,
            source_skill_count INTEGER,
            source_memory_count INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skill_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            used_at TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_skill_usage_user ON skill_usage (user_id, used_at)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_contexts (
            user_id TEXT NOT NULL,
            context_key TEXT NOT NULL,
            project_dir TEXT,
            core_memory TEXT,
            recent_raw_hashes TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (user_id, context_key)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_items (
            user_id TEXT NOT NULL,
            context_key TEXT NOT NULL,
            id TEXT NOT NULL,
            content TEXT,
            memory_type TEXT,
            section TEXT,
            salience REAL,
            confidence REAL,
            embedding TEXT,
            source_session TEXT,
            source_agent TEXT,
            recall_count INTEGER,
            hit_count INTEGER,
            miss_count INTEGER,
            is_active INTEGER,
            is_archived INTEGER,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (user_id, context_key, id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_schemas (
            user_id TEXT NOT NULL,
            context_key TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT,
            description TEXT,
            memory_ids TEXT,
            created_at TEXT,
            PRIMARY KEY (user_id, context_key, id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_materializations (
            user_id TEXT NOT NULL,
            context_key TEXT NOT NULL,
            materialization_id TEXT PRIMARY KEY,
            memories_included TEXT,
            query TEXT,
            outcome TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            context_key TEXT NOT NULL,
            session_id TEXT,
            raw_input_hash TEXT,
            delta_batch TEXT,
            created_at TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_memory_context ON memory_items (user_id, context_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_memory_activity ON memory_activity (user_id, context_key, created_at)")

    c.execute("""
        INSERT OR IGNORE INTO skill_records
        (user_id, name, description, content, version, source_sessions, created_at,
         updated_at, skill_type, scope, evidence_count, confidence, status,
         embedding_text, embedding_vector, embedding_model, replay_score,
         replay_cases, replay_wins, replay_losses, replay_rationale,
         language, parent_skill, quality_notes, judge_rationale)
        SELECT 'default', name, description, content, version, source_sessions,
               created_at, updated_at, 'preference', 'user', 0, 0.0,
               'active', name || char(10) || description, NULL, NULL,
               0.0, 0, 0, 0, NULL, 'en', NULL, '[]',
               'migrated from legacy skills table'
        FROM skills
    """)
    _sync_project_skill_files(c)
    
    conn.commit()
    conn.close()


def _migrate_legacy_db():
    if DB_PATH.exists() or not LEGACY_DB_PATH.exists():
        return
    LEGACY_DB_PATH.replace(DB_PATH)


def _ensure_column(cursor, table: str, column: str, column_type: str):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise


def _migrate_memory_table_names(cursor):
    memory_items_columns = _table_columns(cursor, "memory_items")
    if "skill_name" in memory_items_columns and not _table_exists(cursor, "skill_memory_items"):
        cursor.execute("ALTER TABLE memory_items RENAME TO skill_memory_items")

    mappings = [
        ("c2s_memory_contexts", "memory_contexts"),
        ("c2s_memory_items", "memory_items"),
        ("c2s_memory_schemas", "memory_schemas"),
        ("c2s_memory_materializations", "memory_materializations"),
        ("c2s_memory_activity", "memory_activity"),
    ]
    for old_name, new_name in mappings:
        if not _table_exists(cursor, old_name):
            continue
        if _table_exists(cursor, new_name):
            cursor.execute(f"INSERT OR IGNORE INTO {new_name} SELECT * FROM {old_name}")
            cursor.execute(f"DROP TABLE {old_name}")
        else:
            cursor.execute(f"ALTER TABLE {old_name} RENAME TO {new_name}")


def _table_exists(cursor, table: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _sync_project_skill_files(cursor):
    if not SKILL_DIR.exists():
        return
    now = datetime.now().isoformat()
    for path in SKILL_DIR.glob("*/PROJECT_SKILL.md"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        user_id = path.parent.name
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        cursor.execute(
            """
            INSERT INTO project_skills
            (user_id, name, content, language, file_path, version,
             source_skill_count, source_memory_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                content = excluded.content,
                language = excluded.language,
                file_path = excluded.file_path,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                "project-skill",
                content,
                _project_skill_language(content),
                str(path),
                1,
                None,
                None,
                now,
                updated_at,
            ),
        )


def save_project_skill(
    user_id: str,
    content: str,
    *,
    file_path: Optional[Path] = None,
    source_skill_count: Optional[int] = None,
    source_memory_count: Optional[int] = None,
):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO project_skills
        (user_id, name, content, language, file_path, version,
         source_skill_count, source_memory_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            content = excluded.content,
            language = excluded.language,
            file_path = excluded.file_path,
            version = project_skills.version + 1,
            source_skill_count = excluded.source_skill_count,
            source_memory_count = excluded.source_memory_count,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            "project-skill",
            content,
            _project_skill_language(content),
            str(file_path) if file_path else None,
            1,
            source_skill_count,
            source_memory_count,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def load_project_skill(user_id: str) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    row = c.execute(
        """
        SELECT user_id, name, content, language, file_path, version,
               source_skill_count, source_memory_count, created_at, updated_at
        FROM project_skills
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0],
        "name": row[1],
        "content": row[2],
        "language": row[3],
        "file_path": row[4],
        "version": row[5],
        "source_skill_count": row[6],
        "source_memory_count": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


def _project_skill_language(content: str) -> Optional[str]:
    match = re.search(r"^language:\s*([A-Za-z-]+)\s*$", content or "", flags=re.MULTILINE)
    return match.group(1) if match else None


def save_conversation(session_id: str, user_id: str, messages: list, feedback: Optional[dict] = None):
    """Save conversation to local DB."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, json.dumps(messages), json.dumps(feedback) if feedback else None, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def load_conversations(user_id: str, limit: int = 100) -> list:
    """Load recent conversations for a user."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT session_id, messages, feedback, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "session_id": r[0],
            "messages": json.loads(r[1]),
            "feedback": json.loads(r[2]) if r[2] else None,
            "timestamp": r[3]
        }
        for r in rows
    ]


def save_skill(skill: Skill, user_id: str = "default", embedding_client=None):
    """Save skill to local DB and filesystem."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    if not skill.embedding_text:
        skill.refresh_embedding_text()
    if embedding_client and not skill.embedding_vector and hasattr(embedding_client, "embed"):
        try:
            skill.embedding_vector = embedding_client.embed(skill.embedding_text)
            skill.embedding_model = getattr(
                embedding_client,
                "embedding_model",
                skill.embedding_model or "text-embedding-3-small",
            )
        except Exception as e:
            skill.quality_notes.append(f"embedding_failed:{type(e).__name__}")

    existing_skills = load_skills(user_id, include_pending=True) if DB_PATH.exists() else []
    same_name = next((existing for existing in existing_skills if existing.name == skill.name), None)
    if same_name:
        _merge_same_name(skill, same_name)
    else:
        merge_target = _find_merge_target(skill, existing_skills)
        if merge_target:
            _merge_into_existing(skill, merge_target)
    _sync_skill_content_metadata(skill)
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT OR REPLACE INTO skill_records
        (user_id, name, description, content, version, source_sessions, created_at,
         updated_at, skill_type, scope, evidence_count, confidence, status,
         embedding_text, embedding_vector, embedding_model, replay_score,
         replay_cases, replay_wins, replay_losses, replay_rationale,
         language, parent_skill, quality_notes, judge_rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            skill.name,
            skill.description,
            skill.content,
            skill.version,
            json.dumps(skill.source_sessions),
            skill.created_at,
            datetime.now().isoformat(),
            skill.skill_type,
            skill.scope,
            skill.evidence_count,
            skill.confidence,
            skill.status,
            skill.embedding_text,
            json.dumps(skill.embedding_vector) if skill.embedding_vector else None,
            skill.embedding_model or None,
            skill.replay_score,
            skill.replay_cases,
            skill.replay_wins,
            skill.replay_losses,
            skill.replay_rationale,
            skill.language,
            skill.parent_skill,
            json.dumps(skill.quality_notes, ensure_ascii=False),
            skill.judge_rationale,
        ),
    )

    # Keep the legacy table populated for old local callers.
    c.execute(
        "INSERT OR REPLACE INTO skills VALUES (?, ?, ?, ?, ?, ?, ?)",
        (skill.name, skill.description, skill.content, skill.version,
         json.dumps(skill.source_sessions), skill.created_at, skill.updated_at)
    )
    conn.commit()
    conn.close()
    
    skill_dir = SKILL_DIR / user_id / skill.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content, encoding="utf-8")

    if skill.memory_items:
        _save_memory_dicts(skill.memory_items, user_id=user_id, skill_name=skill.name)


def _find_merge_target(candidate: Skill, existing_skills: List[Skill]) -> Optional[Skill]:
    best_skill = None
    best_score = 0.0
    candidate_tokens = _tokens(candidate.embedding_text or candidate.content)

    for existing in existing_skills:
        if existing.name == candidate.name:
            continue
        if existing.status != "active":
            continue
        if existing.skill_type != candidate.skill_type:
            continue

        vector_score = 0.0
        if candidate.embedding_vector and existing.embedding_vector:
            vector_score = _cosine(candidate.embedding_vector, existing.embedding_vector)
        existing_text = existing.embedding_text or existing.content
        lexical_score = _jaccard(candidate_tokens, _tokens(existing_text))
        if vector_score < MERGE_COSINE_THRESHOLD and lexical_score < MERGE_LEXICAL_THRESHOLD:
            continue

        score = max(vector_score, lexical_score)

        if score > best_score:
            best_score = score
            best_skill = existing

    return best_skill


def _merge_into_existing(candidate: Skill, existing: Skill) -> None:
    candidate.quality_notes.append(f"merged_into_existing:{existing.name}")
    candidate.name = existing.name
    _merge_existing_metadata(candidate, existing)


def _merge_same_name(candidate: Skill, existing: Skill) -> None:
    candidate.quality_notes.append(f"updated_existing:{existing.name}")
    _merge_existing_metadata(candidate, existing)


def _merge_existing_metadata(candidate: Skill, existing: Skill) -> None:
    if candidate.version <= existing.version:
        candidate.version = existing.version + 1
    candidate.created_at = existing.created_at
    candidate.parent_skill = existing.name
    has_new_sessions = any(
        session not in existing.source_sessions for session in candidate.source_sessions
    )
    candidate.source_sessions = sorted(set(existing.source_sessions + candidate.source_sessions))
    # Stop hooks re-process the same growing transcript, so a session can be
    # extracted many times. Only new sessions add evidence; a re-extraction
    # may refresh the count but never stack it.
    if has_new_sessions:
        candidate.evidence_count = existing.evidence_count + candidate.evidence_count
    else:
        candidate.evidence_count = max(existing.evidence_count, candidate.evidence_count)
    candidate.confidence = max(existing.confidence, candidate.confidence)
    candidate.status = "active"


def _sync_skill_content_metadata(skill: Skill) -> None:
    if not skill.content.startswith("---"):
        return
    replacements = {
        "name": skill.name,
        "description": json.dumps(skill.description, ensure_ascii=False),
        "version": str(skill.version),
        "created": skill.created_at,
    }
    content = skill.content
    for key, value in replacements.items():
        pattern = rf"(?m)^{key}:\s*.*$"
        line = f"{key}: {value}"
        if re.search(pattern, content):
            content = re.sub(pattern, line, content, count=1)
    skill.content = content








def save_memory_items(items: List[MemoryItem], user_id: str, skill_name: Optional[str] = None):
    """Persist extracted evidence items."""
    if not items:
        return
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executemany(
        """
        INSERT INTO skill_memory_items
        (user_id, skill_name, item_type, title, description, content, evidence,
         source_session, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_id,
                skill_name,
                item.item_type,
                item.title,
                item.description,
                item.content,
                item.evidence,
                item.source_session,
                item.confidence,
                item.created_at,
            )
            for item in items
        ],
    )
    conn.commit()
    conn.close()


def load_skill_memory_items(
    user_id: str,
    skill_names: Optional[List[str]] = None,
) -> dict[str, List[dict]]:
    """Load C2S evidence items grouped by source skill name."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    params: list[str] = [user_id]
    where = "WHERE user_id = ?"
    if skill_names:
        placeholders = ",".join("?" for _ in skill_names)
        where += f" AND skill_name IN ({placeholders})"
        params.extend(skill_names)
    c.execute(
        f"""
        SELECT skill_name, item_type, title, description, content, evidence,
               source_session, confidence, created_at
        FROM skill_memory_items
        {where}
        ORDER BY skill_name, confidence DESC, created_at DESC
        """,
        params,
    )
    grouped: dict[str, List[dict]] = {}
    for row in c.fetchall():
        skill_name = row["skill_name"] or ""
        grouped.setdefault(skill_name, []).append(
            {
                "item_type": row["item_type"] or "",
                "title": row["title"] or "",
                "description": row["description"] or "",
                "content": row["content"] or "",
                "evidence": row["evidence"] or "",
                "source_session": row["source_session"] or "",
                "confidence": float(row["confidence"] or 0.0),
                "created_at": row["created_at"] or "",
            }
        )
    conn.close()
    return grouped


def _save_memory_dicts(items: List[dict], user_id: str, skill_name: Optional[str] = None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # Re-extraction of the same session replaces its items instead of stacking.
    sessions = {item.get("source_session", "") for item in items if item.get("source_session")}
    for session in sessions:
        c.execute(
            "DELETE FROM skill_memory_items WHERE user_id = ? AND skill_name IS ? AND source_session = ?",
            (user_id, skill_name, session),
        )
    c.executemany(
        """
        INSERT INTO skill_memory_items
        (user_id, skill_name, item_type, title, description, content, evidence,
         source_session, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_id,
                skill_name,
                item.get("item_type", ""),
                item.get("title", ""),
                item.get("description", ""),
                item.get("content", ""),
                item.get("evidence", ""),
                item.get("source_session", ""),
                item.get("confidence", 0.0),
                item.get("created_at", datetime.now().isoformat()),
            )
            for item in items
        ],
    )
    conn.commit()
    conn.close()


def load_skills(user_id: Optional[str] = None, include_pending: bool = True) -> List[Skill]:
    """Load skills from local DB, optionally scoped to one user."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    where = []
    params = []
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if not include_pending:
        where.append("status = 'active'")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    c.execute(
        f"""
        SELECT name, description, content, version, source_sessions, created_at,
               updated_at, skill_type, scope, evidence_count, confidence, status,
               embedding_text, embedding_vector, embedding_model, replay_score,
               replay_cases, replay_wins, replay_losses, replay_rationale,
               language, parent_skill, quality_notes, judge_rationale
        FROM skill_records
        {where_sql}
        """,
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [_skill_from_record(r) for r in rows]


def get_skill(name: str, user_id: str = "default") -> Optional[Skill]:
    """Get single skill by name."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        SELECT name, description, content, version, source_sessions, created_at,
               updated_at, skill_type, scope, evidence_count, confidence, status,
               embedding_text, embedding_vector, embedding_model, replay_score,
               replay_cases, replay_wins, replay_losses, replay_rationale,
               language, parent_skill, quality_notes, judge_rationale
        FROM skill_records WHERE user_id = ? AND name = ?
        """,
        (user_id, name),
    )
    row = c.fetchone()
    conn.close()
    if row:
        return _skill_from_record(row)
    return None


def record_skill_usage(user_id: str, skill_names: List[str]):
    """Log retrieval hits so maintenance can score utilization."""
    if not skill_names:
        return
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executemany(
        "INSERT INTO skill_usage (user_id, skill_name, used_at) VALUES (?, ?, ?)",
        [(user_id, name, now) for name in skill_names],
    )
    conn.commit()
    conn.close()


def load_usage_counts(user_id: str, days: int = 30) -> dict:
    """Per-skill retrieval counts within the recent window."""
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT skill_name, COUNT(*) FROM skill_usage WHERE user_id = ? AND used_at >= ? GROUP BY skill_name",
        (user_id, cutoff),
    )
    counts = dict(c.fetchall())
    conn.close()
    return counts


def set_skill_status(name: str, user_id: str, status: str, note: Optional[str] = None):
    """Change a skill's lifecycle status (e.g. archive during maintenance)."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if note:
        row = c.execute(
            "SELECT quality_notes FROM skill_records WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        notes = []
        if row and row[0]:
            try:
                notes = json.loads(row[0])
            except json.JSONDecodeError:
                notes = [row[0]]
        notes.append(note)
        c.execute(
            "UPDATE skill_records SET status = ?, quality_notes = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (status, json.dumps(notes, ensure_ascii=False), datetime.now().isoformat(), user_id, name),
        )
    else:
        c.execute(
            "UPDATE skill_records SET status = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (status, datetime.now().isoformat(), user_id, name),
        )
    conn.commit()
    conn.close()


def absorb_skill_sources(winner_name: str, loser_name: str, user_id: str):
    """Metadata-only merge: the winner inherits the loser's sessions.

    Evidence only accumulates when the loser brings sessions the winner
    has not already counted (same rule as _merge_existing_metadata).
    """
    winner = get_skill(winner_name, user_id)
    loser = get_skill(loser_name, user_id)
    if not winner or not loser:
        return
    new_sessions = [s for s in loser.source_sessions if s not in winner.source_sessions]
    merged_sessions = sorted(set(winner.source_sessions + loser.source_sessions))
    evidence = winner.evidence_count + (loser.evidence_count if new_sessions else 0)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "UPDATE skill_records SET source_sessions = ?, evidence_count = ?, updated_at = ? WHERE user_id = ? AND name = ?",
        (json.dumps(merged_sessions), evidence, datetime.now().isoformat(), user_id, winner_name),
    )
    conn.commit()
    conn.close()


def load_user_profile(user_id: str) -> UserModel:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT profile_json FROM user_profiles WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return UserModel.from_dict(json.loads(row[0]))
    return UserModel(user_id=user_id)


def save_user_profile(profile: UserModel):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO user_profiles VALUES (?, ?, ?)",
        (profile.user_id, json.dumps(profile.to_dict(), ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_project_memory_context(user_id: str, context_key: str) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    context_row = c.execute(
        """
        SELECT project_dir, core_memory, recent_raw_hashes
        FROM memory_contexts
        WHERE user_id = ? AND context_key = ?
        """,
        (user_id, context_key),
    ).fetchone()
    if not context_row:
        conn.close()
        return None

    item_rows = c.execute(
        """
        SELECT id, content, memory_type, section, salience, confidence, embedding,
               source_session, source_agent, recall_count, hit_count, miss_count,
               is_active, is_archived, created_at, updated_at
        FROM memory_items
        WHERE user_id = ? AND context_key = ?
        ORDER BY created_at, id
        """,
        (user_id, context_key),
    ).fetchall()
    schema_rows = c.execute(
        """
        SELECT id, name, description, memory_ids, created_at
        FROM memory_schemas
        WHERE user_id = ? AND context_key = ?
        ORDER BY created_at, id
        """,
        (user_id, context_key),
    ).fetchall()
    materialization_row = c.execute(
        """
        SELECT materialization_id, memories_included, query, outcome
        FROM memory_materializations
        WHERE user_id = ? AND context_key = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id, context_key),
    ).fetchone()
    conn.close()

    return {
        "version": 1,
        "project_dir": context_row[0] or "",
        "user_id": user_id,
        "core_memory": context_row[1] or "",
        "memories": [_memory_item_from_row(row) for row in item_rows],
        "schemas": [_memory_schema_from_row(row) for row in schema_rows],
        "recent_raw_hashes": _json_list(context_row[2]),
        "last_materialization": _materialization_from_row(materialization_row),
    }


def save_project_memory_context(user_id: str, context_key: str, context: dict):
    now = datetime.now().isoformat()
    project_dir = context.get("project_dir", "")
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO memory_contexts
        (user_id, context_key, project_dir, core_memory, recent_raw_hashes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, context_key) DO UPDATE SET
            project_dir = excluded.project_dir,
            core_memory = excluded.core_memory,
            recent_raw_hashes = excluded.recent_raw_hashes,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            context_key,
            project_dir,
            context.get("core_memory", ""),
            json.dumps(context.get("recent_raw_hashes") or [], ensure_ascii=False),
            now,
            now,
        ),
    )
    c.execute("DELETE FROM memory_items WHERE user_id = ? AND context_key = ?", (user_id, context_key))
    c.executemany(
        """
        INSERT INTO memory_items
        (user_id, context_key, id, content, memory_type, section, salience, confidence,
         embedding, source_session, source_agent, recall_count, hit_count, miss_count,
         is_active, is_archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            _memory_item_to_row(user_id, context_key, item, now)
            for item in context.get("memories") or []
            if item.get("id")
        ],
    )
    c.execute("DELETE FROM memory_schemas WHERE user_id = ? AND context_key = ?", (user_id, context_key))
    c.executemany(
        """
        INSERT INTO memory_schemas
        (user_id, context_key, id, name, description, memory_ids, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_id,
                context_key,
                str(schema.get("id")),
                schema.get("name", ""),
                schema.get("description", ""),
                json.dumps(schema.get("memory_ids") or [], ensure_ascii=False),
                schema.get("created_at") or now,
            )
            for schema in context.get("schemas") or []
            if schema.get("id")
        ],
    )
    conn.commit()
    conn.close()


def save_project_memory_materialization(user_id: str, context_key: str, materialization: dict):
    materialization_id = materialization.get("materialization_id")
    if not materialization_id:
        return
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT OR REPLACE INTO memory_materializations
        (user_id, context_key, materialization_id, memories_included, query, outcome, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            context_key,
            materialization_id,
            json.dumps(materialization.get("memories_included") or [], ensure_ascii=False),
            materialization.get("query", ""),
            materialization.get("outcome"),
            now,
        ),
    )
    conn.commit()
    conn.close()


def record_project_memory_activity(user_id: str, context_key: str, session_id: str, memory: dict):
    raw_input_hash = memory.get("raw_input_hash")
    if not raw_input_hash and not memory.get("delta_batch"):
        return
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO memory_activity
        (user_id, context_key, session_id, raw_input_hash, delta_batch, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            context_key,
            session_id,
            raw_input_hash,
            json.dumps(memory.get("delta_batch") or {}, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def _memory_item_to_row(user_id: str, context_key: str, item: dict, now: str) -> tuple:
    return (
        user_id,
        context_key,
        str(item.get("id")),
        item.get("content", ""),
        item.get("memory_type", "fact"),
        item.get("section", "general"),
        float(item.get("salience") or 0.5),
        float(item.get("confidence") or 0.5),
        json.dumps(item.get("embedding") or [], ensure_ascii=False),
        item.get("source_session"),
        item.get("source_agent"),
        int(item.get("recall_count") or 0),
        int(item.get("hit_count") or 0),
        int(item.get("miss_count") or 0),
        1 if item.get("is_active", True) else 0,
        1 if item.get("is_archived", False) else 0,
        item.get("created_at") or now,
        item.get("updated_at") or now,
    )


def _memory_item_from_row(row) -> dict:
    return {
        "id": row[0],
        "content": row[1] or "",
        "memory_type": row[2] or "fact",
        "section": row[3] or "general",
        "salience": row[4] if row[4] is not None else 0.5,
        "confidence": row[5] if row[5] is not None else 0.5,
        "embedding": _json_list(row[6]),
        "source_session": row[7],
        "source_agent": row[8],
        "recall_count": row[9] or 0,
        "hit_count": row[10] or 0,
        "miss_count": row[11] or 0,
        "is_active": bool(row[12]),
        "is_archived": bool(row[13]),
        "created_at": row[14],
        "updated_at": row[15],
    }


def _memory_schema_from_row(row) -> dict:
    return {
        "id": row[0],
        "name": row[1] or "",
        "description": row[2] or "",
        "memory_ids": _json_list(row[3]),
        "created_at": row[4],
    }


def _materialization_from_row(row) -> Optional[dict]:
    if not row:
        return None
    return {
        "materialization_id": row[0],
        "memories_included": _json_list(row[1]),
        "query": row[2] or "",
        "outcome": row[3],
    }


def _json_list(value: Optional[str]) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _skill_from_record(row) -> Skill:
    notes = []
    if row[22]:
        try:
            notes = json.loads(row[22])
        except json.JSONDecodeError:
            notes = [row[22]]
    embedding_vector = []
    if row[13]:
        try:
            embedding_vector = json.loads(row[13])
        except json.JSONDecodeError:
            embedding_vector = []
    skill = Skill(
        name=row[0],
        description=row[1] or "",
        content=row[2] or "",
        version=row[3] or 1,
        source_sessions=json.loads(row[4]) if row[4] else [],
        created_at=row[5] or datetime.now().isoformat(),
        updated_at=row[6] or datetime.now().isoformat(),
        skill_type=row[7] or "preference",
        scope=row[8] or "user",
        evidence_count=row[9] or 0,
        confidence=row[10] or 0.0,
        status=row[11] or "draft",
        embedding_text=row[12] or "",
        embedding_vector=embedding_vector,
        embedding_model=row[14] or "",
        replay_score=row[15] or 0.0,
        replay_cases=row[16] or 0,
        replay_wins=row[17] or 0,
        replay_losses=row[18] or 0,
        replay_rationale=row[19] or "",
        language=row[20] or "en",
        parent_skill=row[21],
        quality_notes=notes,
        judge_rationale=row[23] or "",
        response_guard=_response_guard_from_content(row[2] or ""),
    )
    if not skill.embedding_text:
        skill.refresh_embedding_text()
    return skill


def _response_guard_from_content(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    frontmatter = content[3:end]
    lines = frontmatter.splitlines()
    guard_lines: list[str] = []
    collecting = False
    for line in lines:
        if re.match(r"^response_guard\s*:", line):
            collecting = True
            guard_lines.append(line)
            continue
        if collecting:
            if line and not line.startswith((" ", "\t", "-")):
                break
            guard_lines.append(line)
    if not guard_lines:
        return {}

    enabled = any(re.match(r"\s*enabled\s*:\s*true\s*$", line, re.IGNORECASE) for line in guard_lines)
    mode = "forbidden_terms"
    requires_evidence = False
    allow_evidence_gap_disclosure = False
    lists: dict[str, list[str]] = {
        "forbidden_terms": [],
        "strict_terms": [],
        "evidence_markers": [],
        "gap_markers": [],
    }
    current_list = ""
    for line in guard_lines:
        mode_match = re.match(r"\s*mode\s*:\s*(.+?)\s*$", line)
        if mode_match:
            mode = mode_match.group(1).strip().strip("\"'") or mode
            continue
        if re.match(r"\s*requires_evidence\s*:\s*true\s*$", line, re.IGNORECASE):
            requires_evidence = True
            continue
        if re.match(r"\s*allow_evidence_gap_disclosure\s*:\s*true\s*$", line, re.IGNORECASE):
            allow_evidence_gap_disclosure = True
            continue
        list_match = re.match(r"\s*(forbidden_terms|strict_terms|evidence_markers|gap_markers)\s*:", line)
        if list_match:
            current_list = list_match.group(1)
            continue
        if current_list:
            match = re.match(r"\s*-\s*(.+?)\s*$", line)
            if match:
                term = match.group(1).strip().strip("\"'")
                if term:
                    lists[current_list].append(term)
            elif line and not line.startswith((" ", "\t", "-")):
                break
    terms = lists["forbidden_terms"]
    if not enabled or not terms:
        return {}
    guard = {
        "enabled": True,
        "mode": mode,
        "forbidden_terms": terms,
    }
    if requires_evidence:
        guard["requires_evidence"] = True
    if allow_evidence_gap_disclosure:
        guard["allow_evidence_gap_disclosure"] = True
    if lists["evidence_markers"]:
        guard["evidence_markers"] = lists["evidence_markers"]
    if lists["gap_markers"]:
        guard["gap_markers"] = lists["gap_markers"]
    if lists["strict_terms"]:
        guard["strict_terms"] = lists["strict_terms"]
    return guard
