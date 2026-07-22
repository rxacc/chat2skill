"""Skill retrieval and prompt formatting.

Lightweight SkillRL/SkillX-style retrieval foundation. It prefers stored
embedding vectors and falls back to deterministic lexical similarity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from . import similarity
from .models import Skill
from .i18n import LANGUAGES


@dataclass
class RetrievedSkill:
    skill: Skill
    score: float


@dataclass
class RetrievedMemory:
    memory: dict
    score: float


class SkillRetriever:
    """Retrieve a compact top-k skill set for the current task."""

    TYPE_BUDGET = {
        "atomic": 3,
        "planning": 2,
        "procedure": 2,
        "preference": 3,
        "mistake": 2,
        "success_pattern": 2,
    }

    def __init__(self, embedding_client=None, embedding_model: Optional[str] = None):
        self.embedding_client = embedding_client
        self.embedding_model = embedding_model

    def retrieve(
        self,
        task_text: str,
        skills: Iterable[Skill],
        top_k: int = 6,
        active_only: bool = True,
        min_score: float = 0.0,
    ) -> List[RetrievedSkill]:
        query_tokens = self._tokens(task_text)
        query_vector = self._embed_query(task_text)
        candidates: List[RetrievedSkill] = []
        type_counts: dict[str, int] = {}

        for skill in skills:
            if active_only and skill.status != "active":
                continue
            text = skill.embedding_text or f"{skill.name}\n{skill.description}\n{skill.content[:1000]}"
            score = self._score(query_vector, query_tokens, skill, text)
            if score <= 0 or score < min_score:
                continue
            candidates.append(RetrievedSkill(skill=skill, score=score))

        candidates.sort(key=lambda item: (item.score, item.skill.confidence), reverse=True)

        selected: List[RetrievedSkill] = []
        for candidate in candidates:
            skill_type = candidate.skill.skill_type
            budget = self.TYPE_BUDGET.get(skill_type, 2)
            if type_counts.get(skill_type, 0) >= budget:
                continue
            selected.append(candidate)
            type_counts[skill_type] = type_counts.get(skill_type, 0) + 1
            if len(selected) >= top_k:
                break

        return selected

    def format_for_prompt(self, retrieved: List[RetrievedSkill]) -> str:
        if not retrieved:
            return ""

        sections: dict[str, list[str]] = {}
        for item in retrieved:
            skill = item.skill
            sections.setdefault(skill.skill_type, []).append(
                f"- **{skill.name}** ({item.score:.2f}): {skill.description}"
            )

        labels = {
            "atomic": "Atomic Constraints",
            "planning": "Planning Skills",
            "procedure": "Procedures",
            "preference": "User Preferences",
            "mistake": "Mistakes to Avoid",
            "success_pattern": "Success Patterns",
        }
        parts = []
        for skill_type, lines in sections.items():
            parts.append(f"### {labels.get(skill_type, skill_type.title())}")
            parts.extend(lines)
            parts.append("")
        return "\n".join(parts).strip()

    def _embed_query(self, task_text: str) -> Optional[List[float]]:
        if not self.embedding_client or not hasattr(self.embedding_client, "embed"):
            return None
        try:
            return self.embedding_client.embed(task_text, model=self.embedding_model)
        except Exception:
            return None

    def _score(
        self,
        query_vector: Optional[List[float]],
        query_tokens: set[str],
        skill: Skill,
        skill_text: str,
    ) -> float:
        if query_vector and skill.embedding_vector:
            vector_score = similarity.cosine(query_vector, skill.embedding_vector)
            if vector_score > 0:
                return vector_score
        return similarity.jaccard(query_tokens, self._tokens(skill_text))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        tokens = similarity.tokens(text)
        tokens.update(SkillRetriever._concept_tokens(text))
        return tokens

    @staticmethod
    def _concept_tokens(text: str) -> set[str]:
        lower = (text or "").lower()
        concepts: set[str] = set()
        marker_groups = {
            "concept_plan": "plan_markers",
            "concept_confirm": "confirmation_markers",
            "concept_concise": "concise_markers",
            "concept_no_modify": "no_modification_markers",
            "concept_correction": "correction_markers",
            "concept_constraint": "constraint_markers",
        }
        for concept, attr in marker_groups.items():
            for profile in LANGUAGES.values():
                if any(marker.lower() in lower for marker in getattr(profile, attr)):
                    concepts.add(concept)
                    break
        if "plan-before-action" in lower:
            concepts.update({"concept_plan", "concept_confirm"})
        if "confirm-before-execute" in lower:
            concepts.add("concept_confirm")
        return concepts


class MemoryRetriever:
    """Retrieve compact project memories from the local c2s.db state."""

    TYPE_WEIGHT = {
        "decision": 0.16,
        "procedure": 0.14,
        "warning": 0.13,
        "strategy": 0.12,
        "principle": 0.1,
        "fact": 0.08,
        "exception": 0.08,
        "episodic": 0.03,
    }

    def __init__(
        self,
        embedding_client=None,
        embedding_model: Optional[str] = None,
        min_vector_score: float = 0.3,
    ):
        self.embedding_client = embedding_client
        self.embedding_model = embedding_model
        self.min_vector_score = min_vector_score

    def retrieve(
        self,
        task_text: str,
        memories: Iterable[dict],
        top_k: int = 12,
        active_only: bool = True,
        min_score: float = 0.0,
    ) -> List[RetrievedMemory]:
        query_tokens = SkillRetriever._tokens(task_text)
        query_vector = self._embed_query(task_text)
        candidates: List[RetrievedMemory] = []

        for memory in memories:
            if active_only and (
                not memory.get("is_active", True) or memory.get("is_archived", False)
            ):
                continue
            text = self._memory_text(memory)
            score = self._score(query_tokens, query_vector, memory, text)
            if score <= 0 or score < min_score:
                continue
            candidates.append(RetrievedMemory(memory=memory, score=score))

        candidates.sort(
            key=lambda item: (
                item.score,
                float(item.memory.get("salience") or 0.0),
                float(item.memory.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        return _mmr_memory_order(candidates)[: max(0, top_k)]

    def format_for_prompt(self, retrieved: List[RetrievedMemory]) -> str:
        if not retrieved:
            return ""

        lines = []
        for item in retrieved:
            memory = item.memory
            memory_type = str(memory.get("memory_type") or "fact")
            section = str(memory.get("section") or "general")
            content = str(memory.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"- [{memory_type}/{section}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _memory_text(memory: dict) -> str:
        return "\n".join(
            str(part)
            for part in [
                memory.get("memory_type"),
                memory.get("section"),
                memory.get("content"),
                memory.get("source_session"),
            ]
            if part
        )

    def _embed_query(self, task_text: str) -> Optional[List[float]]:
        if not self.embedding_client or not hasattr(self.embedding_client, "embed"):
            return None
        try:
            return self.embedding_client.embed(task_text, model=self.embedding_model)
        except Exception:
            return None

    def _score(
        self,
        query_tokens: set[str],
        query_vector: Optional[List[float]],
        memory: dict,
        memory_text: str,
    ) -> float:
        lexical = similarity.jaccard(query_tokens, SkillRetriever._tokens(memory_text))
        exact = self._exact_boost(query_tokens, memory_text)
        vector = 0.0
        memory_vector = memory.get("embedding") or []
        if query_vector and memory_vector:
            vector = similarity.cosine(query_vector, memory_vector)
            if vector < self.min_vector_score:
                vector = 0.0
        base = max(lexical + exact, vector)
        if query_tokens and base <= 0:
            return 0.0
        salience = float(memory.get("salience") or 0.5)
        confidence = float(memory.get("confidence") or 0.5)
        memory_type = str(memory.get("memory_type") or "fact")
        type_boost = self.TYPE_WEIGHT.get(memory_type, 0.05)
        return base + (salience * 0.08) + (confidence * 0.06) + type_boost

    @staticmethod
    def _exact_boost(query_tokens: set[str], memory_text: str) -> float:
        lower = memory_text.lower()
        boost = 0.0
        for token in query_tokens:
            if len(token) >= 4 and token in lower:
                boost += 0.02
        return min(boost, 0.2)


def _mmr_memory_order(candidates: List[RetrievedMemory], lambda_: float = 0.72) -> List[RetrievedMemory]:
    remaining = candidates[:64]
    picked: List[RetrievedMemory] = []
    while remaining:
        best_idx = 0
        best_score = -1.0
        for idx, candidate in enumerate(remaining):
            relevance = candidate.score
            vector = candidate.memory.get("embedding") or []
            redundancy = 0.0
            if vector and picked:
                redundancy = max(
                    (
                        similarity.cosine(vector, item.memory.get("embedding") or [])
                        for item in picked
                        if item.memory.get("embedding")
                    ),
                    default=0.0,
                )
            score = lambda_ * relevance - (1 - lambda_) * redundancy
            if score > best_score:
                best_idx = idx
                best_score = score
        picked.append(remaining.pop(best_idx))
    return picked + remaining
