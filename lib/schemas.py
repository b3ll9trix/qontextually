from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

REF_PATTERN = re.compile(r"^ent_[a-zA-Z0-9_]+$")


class Entity(BaseModel):
    """An entity the LLM identified in the source chunk.

    `ref` is LLM-chosen and scoped to the containing ExtractionResult. The
    application maps it to a persistent DB entity_id during write-time
    resolution (Tier 1 exact alias, Tier 2 embedding similarity).

    `type` and new predicates landing in triples are advisory — the seeded
    entity_types / predicates registry is preferred but the extractor may
    invent new ones, which will be auto-registered with `auto_added=1`.
    """

    ref: str = Field(description="Local reference id, e.g. 'ent_1'. Must match ^ent_[a-zA-Z0-9_]+$.")
    type: str = Field(description="Entity type from the seeded vocabulary when possible (Person, Organization, Project, Document, Ticket, Policy, Product, Meeting, Message, Event). A new type is acceptable if none fit.")
    name: str = Field(min_length=1, description="Canonical display name.")
    aliases: list[str] = Field(default_factory=list, description="Alternative names, emails, handles, or initials observed in this chunk.")
    properties: dict[str, str] = Field(default_factory=dict, description="Structured attributes, e.g. {'title': 'VP Engineering', 'department': 'Platform'}. Values must be strings.")

    @field_validator("ref")
    @classmethod
    def _ref_pattern(cls, v: str) -> str:
        if not REF_PATTERN.match(v):
            raise ValueError(
                f"ref {v!r} must match {REF_PATTERN.pattern}"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_stripped(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name cannot be blank")
        return s


class Triple(BaseModel):
    """A single fact (subject predicate object).

    `subject_ref` refers to an Entity in the same ExtractionResult. `object_ref`
    points to another entity; `object_value` carries a literal string (title,
    status, email, date). Exactly one of (object_ref, object_value) must be set
    - this is the discriminated union that maps to triples.object_is_entity at
    DB write time.
    """

    subject_ref: str = Field(description="Ref of the subject entity declared in this ExtractionResult.")
    predicate: str = Field(min_length=1, description="Predicate name from the seeded vocabulary when possible (works_at, reports_to, manages, owns, part_of, mentions, authored, attended, references, supersedes, located_in, has_title, has_email, has_status). A new predicate is acceptable.")
    object_ref: Optional[str] = Field(default=None, description="Ref of the object entity, if the object is a thing in the graph.")
    object_value: Optional[str] = Field(default=None, description="Literal value, if the object is a string (job title, status, email address, date).")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Extractor confidence in this fact, 0..1. Default 1.0.")

    @model_validator(mode="after")
    def _object_xor(self) -> "Triple":
        if (self.object_ref is None) == (self.object_value is None):
            raise ValueError(
                "exactly one of object_ref or object_value must be set"
            )
        return self

    @property
    def object_is_entity(self) -> bool:
        return self.object_ref is not None


class ExtractionResult(BaseModel):
    """Everything the LLM extracted from one source chunk.

    A chunk is one Document from lib.ingestor - one email, one resume page,
    one JSON record. The chunk's (file_path, record_index / page / row_index)
    becomes the sources row; every triple here gets a triple_sources link to
    that row. Provenance is at the chunk level by design.
    """

    entities: list[Entity] = Field(default_factory=list, description="All entities identified in the chunk.")
    triples: list[Triple] = Field(default_factory=list, description="All facts identified in the chunk. Every subject_ref and object_ref must appear in entities.")
    notes: Optional[str] = Field(default=None, description="Optional free-text notes for anything ambiguous, unresolvable, or that the extractor wants to flag for a reviewer.")

    @model_validator(mode="after")
    def _validate_refs(self) -> "ExtractionResult":
        seen: set[str] = set()
        for e in self.entities:
            if e.ref in seen:
                raise ValueError(f"duplicate entity ref: {e.ref}")
            seen.add(e.ref)

        for i, t in enumerate(self.triples):
            if t.subject_ref not in seen:
                raise ValueError(
                    f"triples[{i}].subject_ref={t.subject_ref!r} not declared in entities"
                )
            if t.object_ref is not None and t.object_ref not in seen:
                raise ValueError(
                    f"triples[{i}].object_ref={t.object_ref!r} not declared in entities"
                )
        return self

    def entity_by_ref(self, ref: str) -> Optional[Entity]:
        for e in self.entities:
            if e.ref == ref:
                return e
        return None
