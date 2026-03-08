"""
Animal Repository for VetScan

Handles all database operations for Animal entities.
"""

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from models.domain import (
    Animal,
    Symptom,
    Observation,
    ClinicalNote,
    AnimalVetAssignment,
    AnimalIdentifier,
    AnimalMatchCandidate,
    AnimalMatchDecision,
)


class AnimalRepository:
    """Repository for Animal CRUD operations."""

    def __init__(self, db):
        """
        Initialize repository with database connection.

        Args:
            db: Database instance with active connection
        """
        self.db = db

    def create(self, animal: Animal) -> int:
        """
        Insert a new animal and return its ID.

        Args:
            animal: Animal instance to create

        Returns:
            ID of the created animal
        """
        cursor = self.db.conn.execute("""
            INSERT INTO animals (name, species, breed, microchip, age_years,
                                owner_name, age_months, sex, weight_kg, neutered,
                                patient_since, medical_history, notes, responsible_vet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (animal.name, animal.species, animal.breed, animal.microchip,
              animal.age_years, animal.owner_name, animal.age_months, animal.sex,
              animal.weight_kg, animal.neutered, animal.patient_since or date.today(),
              animal.medical_history, animal.notes, animal.responsible_vet))
        animal_id = cursor.lastrowid
        if animal.responsible_vet:
            self._record_vet_assignment(animal_id, animal.responsible_vet)
        self.db.conn.commit()
        return animal_id

    def get(self, animal_id: int) -> Optional[Animal]:
        """
        Retrieve an animal by ID.

        Args:
            animal_id: ID of the animal

        Returns:
            Animal instance or None if not found
        """
        cursor = self.db.conn.execute(
            "SELECT * FROM animals WHERE id = ?", (animal_id,))
        row = cursor.fetchone()
        if row:
            return Animal(**dict(row))
        return None

    def find_by_name(self, name: str) -> List[Animal]:
        """
        Find animals by name (partial match).

        Args:
            name: Name to search for

        Returns:
            List of matching animals
        """
        cursor = self.db.conn.execute(
            "SELECT * FROM animals WHERE name LIKE ?", (f"%{name}%",))
        return [Animal(**dict(row)) for row in cursor.fetchall()]

    def _normalize_text(self, value: Optional[str]) -> str:
        if not value:
            return ""
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9\s]", " ", value)
        return " ".join(value.split())

    def _canonical_species(self, value: Optional[str]) -> str:
        normalized = self._normalize_text(value)
        if "can" in normalized or "dog" in normalized:
            return "canine"
        if "fel" in normalized or "cat" in normalized:
            return "feline"
        return normalized

    def _owner_matches(self, left: Optional[str], right: Optional[str]) -> bool:
        left_norm = self._normalize_text(left)
        right_norm = self._normalize_text(right)
        if not left_norm or not right_norm:
            return False
        return (
            left_norm == right_norm
            or left_norm in right_norm
            or right_norm in left_norm
        )

    def _record_vet_assignment(self, animal_id: int, vet_name: Optional[str],
                               changed_by_user_id: Optional[int] = None,
                               change_reason: Optional[str] = None,
                               start_date: Optional[date] = None):
        normalized_vet = (vet_name or "").strip()
        if not normalized_vet:
            self._close_open_vet_assignment(animal_id, changed_by_user_id)
            return

        current = self.db.conn.execute("""
            SELECT id, vet_name
            FROM animal_vet_assignments
            WHERE animal_id = ? AND end_date IS NULL
            ORDER BY start_date DESC, id DESC
            LIMIT 1
        """, (animal_id,)).fetchone()

        if current and (current["vet_name"] or "").strip() == normalized_vet:
            return

        self._close_open_vet_assignment(animal_id, changed_by_user_id, start_date)
        self.db.conn.execute("""
            INSERT INTO animal_vet_assignments (
                animal_id, vet_name, start_date, change_reason, changed_by_user_id
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            animal_id,
            normalized_vet,
            start_date or date.today(),
            change_reason,
            changed_by_user_id,
        ))

    def _close_open_vet_assignment(self, animal_id: int,
                                   changed_by_user_id: Optional[int] = None,
                                   end_date: Optional[date] = None):
        self.db.conn.execute("""
            UPDATE animal_vet_assignments
            SET end_date = COALESCE(?, CURRENT_DATE),
                changed_by_user_id = COALESCE(changed_by_user_id, ?)
            WHERE animal_id = ? AND end_date IS NULL
        """, (end_date, changed_by_user_id, animal_id))

    def _update_from_report(self, animal_id: int, animal: Animal):
        existing = self.get(animal_id)
        if not existing:
            return

        updates = {}
        if animal.breed and (not existing.breed or existing.breed == "Indeterminado"):
            updates["breed"] = animal.breed
        if animal.owner_name and not existing.owner_name:
            updates["owner_name"] = animal.owner_name
        if animal.microchip and not existing.microchip:
            updates["microchip"] = animal.microchip
        if animal.age_years and not existing.age_years:
            updates["age_years"] = animal.age_years
        if animal.age_months and not existing.age_months:
            updates["age_months"] = animal.age_months
        if animal.patient_since and not existing.patient_since:
            updates["patient_since"] = animal.patient_since
        if animal.sex and existing.sex == "U" and animal.sex != "U":
            updates["sex"] = animal.sex
        if animal.neutered is not None and existing.neutered is None:
            updates["neutered"] = animal.neutered
        if animal.responsible_vet and not existing.responsible_vet:
            updates["responsible_vet"] = animal.responsible_vet
        if updates:
            self.update(animal_id, **updates)

    def find_identifier(self, source_system: str, identifier_type: str,
                        identifier_value: str) -> Optional[int]:
        cursor = self.db.conn.execute("""
            SELECT animal_id FROM animal_identifiers
            WHERE source_system = ? AND identifier_type = ? AND identifier_value = ?
        """, (source_system, identifier_type, identifier_value))
        row = cursor.fetchone()
        return row["animal_id"] if row else None

    def upsert_identifier(self, animal_id: int, identifier: AnimalIdentifier):
        if not identifier.identifier_value:
            return
        self.db.conn.execute("""
            INSERT INTO animal_identifiers (
                animal_id, source_system, identifier_type, identifier_value
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(source_system, identifier_type, identifier_value)
            DO UPDATE SET animal_id = excluded.animal_id
        """, (
            animal_id,
            identifier.source_system,
            identifier.identifier_type,
            identifier.identifier_value,
        ))
        self.db.conn.commit()

    def _candidate_from_animal(self, animal: Animal, confidence: float,
                               reason: str) -> AnimalMatchCandidate:
        return AnimalMatchCandidate(
            animal_id=animal.id,
            name=animal.name,
            species=animal.species,
            owner_name=animal.owner_name,
            microchip=animal.microchip,
            confidence=round(confidence, 4),
            reason=reason,
        )

    def _get_all_candidates(self) -> List[Animal]:
        cursor = self.db.conn.execute("SELECT * FROM animals")
        return [Animal(**dict(row)) for row in cursor.fetchall()]

    def analyze_match(self, animal: Animal,
                      identifiers: Optional[List[AnimalIdentifier]] = None) -> AnimalMatchDecision:
        identifiers = identifiers or []

        for identifier in identifiers:
            animal_id = self.find_identifier(
                identifier.source_system,
                identifier.identifier_type,
                identifier.identifier_value,
            )
            if animal_id:
                matched = self.get(animal_id)
                return AnimalMatchDecision(
                    action="match_existing",
                    animal_id=animal_id,
                    confidence=1.0,
                    reason=f"exact_{identifier.identifier_type}",
                    candidates=[self._candidate_from_animal(
                        matched, 1.0, f"Exact {identifier.identifier_type}"
                    )] if matched else [],
                )

        if animal.microchip:
            cursor = self.db.conn.execute(
                "SELECT id FROM animals WHERE microchip = ?", (animal.microchip,))
            row = cursor.fetchone()
            if row:
                matched = self.get(row["id"])
                return AnimalMatchDecision(
                    action="match_existing",
                    animal_id=row["id"],
                    confidence=0.99,
                    reason="exact_microchip",
                    candidates=[self._candidate_from_animal(
                        matched, 0.99, "Exact microchip"
                    )] if matched else [],
                )

        target_name = self._normalize_text(animal.name)
        target_species = self._canonical_species(animal.species)
        target_owner = self._normalize_text(animal.owner_name)

        if not target_name:
            return AnimalMatchDecision(
                action="manual_review",
                confidence=0.0,
                reason="missing_patient_name",
            )

        candidates = self._get_all_candidates()

        exact_matches: List[AnimalMatchCandidate] = []
        exact_missing_owner_matches: List[AnimalMatchCandidate] = []
        exact_conflicting_owner_matches: List[AnimalMatchCandidate] = []
        for candidate in candidates:
            if (
                self._normalize_text(candidate.name) == target_name
                and self._canonical_species(candidate.species) == target_species
            ):
                if target_owner:
                    candidate_owner = self._normalize_text(candidate.owner_name)
                    if self._owner_matches(candidate.owner_name, animal.owner_name):
                        confidence = 0.98
                        reason = "Exact name, species, and owner"
                        exact_matches.append(self._candidate_from_animal(
                            candidate, confidence, reason
                        ))
                        continue
                    if not candidate_owner:
                        exact_missing_owner_matches.append(self._candidate_from_animal(
                            candidate,
                            0.94,
                            "Exact name and species; owner missing on existing record",
                        ))
                        continue
                    exact_conflicting_owner_matches.append(self._candidate_from_animal(
                        candidate,
                        0.9,
                        "Exact name and species but owner differs",
                    ))
                    continue
                else:
                    confidence = 0.95
                    reason = "Exact name and species"
                    exact_matches.append(self._candidate_from_animal(
                        candidate, confidence, reason
                    ))

        if len(exact_matches) == 1:
            best = exact_matches[0]
            return AnimalMatchDecision(
                action="match_existing",
                animal_id=best.animal_id,
                confidence=best.confidence,
                reason="exact_match",
                candidates=exact_matches,
            )
        if len(exact_matches) > 1:
            exact_matches.sort(key=lambda item: item.confidence, reverse=True)
            return AnimalMatchDecision(
                action="manual_review",
                confidence=exact_matches[0].confidence,
                reason="multiple_exact_matches",
                candidates=exact_matches[:5],
            )

        if (
            not exact_matches
            and len(exact_missing_owner_matches) == 1
            and not exact_conflicting_owner_matches
        ):
            best = exact_missing_owner_matches[0]
            return AnimalMatchDecision(
                action="match_existing",
                animal_id=best.animal_id,
                confidence=best.confidence,
                reason="exact_match_missing_owner_on_existing_record",
                candidates=[best],
            )

        if exact_missing_owner_matches or exact_conflicting_owner_matches:
            combined_exact = exact_matches + exact_missing_owner_matches + exact_conflicting_owner_matches
            combined_exact.sort(key=lambda item: item.confidence, reverse=True)
            return AnimalMatchDecision(
                action="manual_review",
                confidence=combined_exact[0].confidence,
                reason="ambiguous_exact_match",
                candidates=combined_exact[:5],
            )

        scored: List[AnimalMatchCandidate] = []
        for candidate in candidates:
            if self._canonical_species(candidate.species) != target_species:
                continue

            name_score = SequenceMatcher(
                None,
                self._normalize_text(candidate.name),
                target_name,
            ).ratio()
            if name_score < 0.72:
                continue

            confidence = name_score * 0.72
            reasons = [f"Name similarity {name_score:.0%}"]

            if self._owner_matches(candidate.owner_name, animal.owner_name):
                confidence += 0.18
                reasons.append("owner match")
            if candidate.age_years and animal.age_years:
                if abs(candidate.age_years - animal.age_years) <= 1:
                    confidence += 0.05
                    reasons.append("age aligned")
            if candidate.breed and animal.breed:
                if self._normalize_text(candidate.breed) == self._normalize_text(animal.breed):
                    confidence += 0.03
                    reasons.append("breed match")

            scored.append(self._candidate_from_animal(
                candidate,
                min(confidence, 0.99),
                ", ".join(reasons),
            ))

        scored.sort(key=lambda item: item.confidence, reverse=True)

        if scored:
            best = scored[0]
            runner_up = scored[1].confidence if len(scored) > 1 else 0.0
            if best.confidence >= 0.93 and (best.confidence - runner_up) >= 0.08:
                return AnimalMatchDecision(
                    action="match_existing",
                    animal_id=best.animal_id,
                    confidence=best.confidence,
                    reason="high_confidence_fuzzy_match",
                    candidates=[best],
                )

            return AnimalMatchDecision(
                action="manual_review",
                confidence=best.confidence,
                reason="ambiguous_existing_match",
                candidates=scored[:5],
            )

        return AnimalMatchDecision(
            action="create_new",
            confidence=0.9,
            reason="no_plausible_existing_match",
        )

    def attach_report_to_animal(self, animal_id: int, animal: Animal,
                                identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        self._update_from_report(animal_id, animal)
        for item in identifiers or []:
            self.upsert_identifier(animal_id, item)
        return animal_id

    def create_from_report(self, animal: Animal,
                           identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        animal_id = self.create(animal)
        for item in identifiers or []:
            self.upsert_identifier(animal_id, item)
        return animal_id

    def find_or_create(self, animal: Animal,
                       identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        """
        Find existing animal or create new one.

        First tries to find by microchip (most reliable),
        then by name + species combination.

        Args:
            animal: Animal to find or create

        Returns:
            ID of existing or newly created animal
        """
        decision = self.analyze_match(animal, identifiers)
        if decision.action == "match_existing" and decision.animal_id:
            return self.attach_report_to_animal(decision.animal_id, animal, identifiers)
        return self.create_from_report(animal, identifiers)

    def list_animals_paginated(self, search: Optional[str] = None,
                               responsible_vet: Optional[str] = None,
                               species: Optional[str] = None,
                               sort: str = "updated_desc",
                               page: int = 1,
                               page_size: int = 25) -> Tuple[List[Dict], int]:
        """List animals with overview metrics for the index page."""
        filters = []
        params: List[object] = []

        if search:
            wildcard = f"%{search.strip()}%"
            filters.append("""
                (
                    a.name LIKE ?
                    OR a.owner_name LIKE ?
                    OR a.microchip LIKE ?
                    OR a.responsible_vet LIKE ?
                    OR a.breed LIKE ?
                )
            """)
            params.extend([wildcard] * 5)

        if responsible_vet:
            filters.append("a.responsible_vet = ?")
            params.append(responsible_vet)

        if species:
            filters.append("a.species = ?")
            params.append(species)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_row = self.db.conn.execute(
            f"SELECT COUNT(*) AS total FROM animals a {where_clause}",
            tuple(params),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        order_clause = {
            "name_asc": "a.name COLLATE NOCASE ASC, a.id ASC",
            "name_desc": "a.name COLLATE NOCASE DESC, a.id DESC",
            "vet_asc": "COALESCE(a.responsible_vet, '') COLLATE NOCASE ASC, a.name COLLATE NOCASE ASC",
            "vet_desc": "COALESCE(a.responsible_vet, '') COLLATE NOCASE DESC, a.name COLLATE NOCASE ASC",
            "last_report_desc": "COALESCE(latest_report_at, '') DESC, a.name COLLATE NOCASE ASC",
            "last_report_asc": "COALESCE(latest_report_at, '') ASC, a.name COLLATE NOCASE ASC",
            "reports_desc": "test_count DESC, latest_report_at DESC, a.name COLLATE NOCASE ASC",
            "reports_asc": "test_count ASC, latest_report_at DESC, a.name COLLATE NOCASE ASC",
            "updated_desc": "COALESCE(latest_report_at, latest_note_at, a.updated_at, a.created_at) DESC, a.name COLLATE NOCASE ASC",
        }.get(sort, "COALESCE(latest_report_at, latest_note_at, a.updated_at, a.created_at) DESC, a.name COLLATE NOCASE ASC")

        offset = max(page - 1, 0) * page_size
        rows = self.db.conn.execute(f"""
            SELECT
                a.*,
                COUNT(DISTINCT ts.id) AS test_count,
                MAX(COALESCE(ts.test_date, DATE(ts.created_at))) AS latest_report_at,
                MAX(COALESCE(cn.note_date, DATE(cn.created_at))) AS latest_note_at
            FROM animals a
            LEFT JOIN test_sessions ts ON ts.animal_id = a.id
            LEFT JOIN clinical_notes cn ON cn.animal_id = a.id
            {where_clause}
            GROUP BY a.id
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
        """, tuple(params + [page_size, offset])).fetchall()

        items = []
        for row in rows:
            data = dict(row)
            animal = Animal(**{key: data.get(key) for key in Animal.__dataclass_fields__.keys() if key in data})
            items.append({
                "animal": animal,
                "test_count": data.get("test_count", 0),
                "last_test": data.get("latest_report_at"),
                "last_note_at": data.get("latest_note_at"),
            })
        return items, total

    def search_animals(self, search: str, limit: int = 8,
                       exclude_id: Optional[int] = None) -> List[Dict]:
        """Search animals for global search and typeahead."""
        if not search or not search.strip():
            return []

        wildcard = f"%{search.strip()}%"
        filters = [
            "a.name LIKE ?",
            "a.owner_name LIKE ?",
            "a.microchip LIKE ?",
            "a.responsible_vet LIKE ?",
            "a.breed LIKE ?",
        ]
        params: List[object] = [wildcard, wildcard, wildcard, wildcard, wildcard]
        exclusion_clause = ""
        if exclude_id is not None:
            exclusion_clause = "AND a.id != ?"
            params.append(exclude_id)
        rows = self.db.conn.execute("""
            SELECT
                a.*,
                COUNT(DISTINCT ts.id) AS test_count,
                MAX(COALESCE(ts.test_date, DATE(ts.created_at))) AS latest_report_at
            FROM animals a
            LEFT JOIN test_sessions ts ON ts.animal_id = a.id
            WHERE (
                """ + " OR ".join(filters) + """
            )
            """ + exclusion_clause + """
            GROUP BY a.id
            ORDER BY
                CASE WHEN a.name LIKE ? THEN 0 ELSE 1 END,
                latest_report_at DESC,
                a.name COLLATE NOCASE ASC
            LIMIT ?
        """, tuple(params + [wildcard, limit])).fetchall()

        return [dict(row) for row in rows]

    def list_responsible_vets(self) -> List[str]:
        """Distinct responsible vets used for filters."""
        rows = self.db.conn.execute("""
            SELECT DISTINCT responsible_vet
            FROM animals
            WHERE responsible_vet IS NOT NULL AND TRIM(responsible_vet) != ''
            ORDER BY responsible_vet COLLATE NOCASE ASC
        """).fetchall()
        return [row["responsible_vet"] for row in rows]

    def get_vet_assignment_history(self, animal_id: int) -> List[AnimalVetAssignment]:
        """Return responsible-vet ownership history for an animal."""
        rows = self.db.conn.execute("""
            SELECT
                ava.*,
                COALESCE(changer.display_name, changer.email) AS changed_by_name
            FROM animal_vet_assignments ava
            LEFT JOIN users changer ON changer.id = ava.changed_by_user_id
            WHERE ava.animal_id = ?
            ORDER BY COALESCE(ava.start_date, DATE(ava.created_at)) DESC, ava.id DESC
        """, (animal_id,)).fetchall()
        return [AnimalVetAssignment(**dict(row)) for row in rows]

    def list_all(self) -> List[Animal]:
        """
        List all animals ordered by name.

        Returns:
            List of all animals
        """
        cursor = self.db.conn.execute("SELECT * FROM animals ORDER BY name")
        return [Animal(**dict(row)) for row in cursor.fetchall()]

    def update(self, animal_id: int,
               changed_by_user_id: Optional[int] = None,
               assignment_reason: Optional[str] = None,
               **kwargs) -> bool:
        """
        Update animal fields.

        Args:
            animal_id: ID of the animal to update
            **kwargs: Fields to update

        Returns:
            True if update was successful
        """
        existing = self.get(animal_id)
        if not existing:
            return False

        allowed_fields = {'name', 'species', 'breed', 'microchip', 'owner_name', 'age_years',
                         'age_months', 'sex', 'weight_kg', 'neutered',
                         'patient_since', 'medical_history', 'notes', 'responsible_vet'}
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        values = list(update_fields.values()) + [animal_id]

        cursor = self.db.conn.execute(
            f"UPDATE animals SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values)

        if "responsible_vet" in update_fields:
            previous_vet = (existing.responsible_vet or "").strip()
            next_vet = (update_fields.get("responsible_vet") or "").strip()
            if previous_vet != next_vet:
                self._record_vet_assignment(
                    animal_id,
                    next_vet,
                    changed_by_user_id=changed_by_user_id,
                    change_reason=assignment_reason,
                )

        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete(self, animal_id: int) -> bool:
        """
        Delete an animal and all related data (cascades).

        Args:
            animal_id: ID of the animal to delete

        Returns:
            True if deletion was successful
        """
        cursor = self.db.conn.execute(
            "DELETE FROM animals WHERE id = ?", (animal_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def merge_into(self, source_animal_id: int, target_animal_id: int) -> bool:
        """Merge a duplicate animal into an existing animal record."""
        if source_animal_id == target_animal_id:
            return False

        source = self.get(source_animal_id)
        target = self.get(target_animal_id)
        if not source or not target:
            return False

        def merge_text(primary: Optional[str], secondary: Optional[str]) -> Optional[str]:
            primary_clean = (primary or "").strip()
            secondary_clean = (secondary or "").strip()
            if not primary_clean:
                return secondary_clean or None
            if not secondary_clean or secondary_clean == primary_clean:
                return primary_clean
            if secondary_clean in primary_clean:
                return primary_clean
            return f"{primary_clean}\n\nMerged from {source.name}:\n{secondary_clean}"

        update_fields: Dict[str, object] = {}
        if not target.owner_name and source.owner_name:
            update_fields["owner_name"] = source.owner_name
        if not target.breed and source.breed:
            update_fields["breed"] = source.breed
        if not target.species and source.species:
            update_fields["species"] = source.species
        if not target.microchip and source.microchip:
            update_fields["microchip"] = source.microchip
        if target.age_years is None and source.age_years is not None:
            update_fields["age_years"] = source.age_years
        if target.age_months is None and source.age_months is not None:
            update_fields["age_months"] = source.age_months
        if target.patient_since is None and source.patient_since is not None:
            update_fields["patient_since"] = source.patient_since
        elif target.patient_since and source.patient_since:
            update_fields["patient_since"] = min(target.patient_since, source.patient_since)
        if (target.sex or "U") == "U" and source.sex and source.sex != "U":
            update_fields["sex"] = source.sex
        if target.weight_kg is None and source.weight_kg is not None:
            update_fields["weight_kg"] = source.weight_kg
        if not target.responsible_vet and source.responsible_vet:
            update_fields["responsible_vet"] = source.responsible_vet

        merged_medical_history = merge_text(target.medical_history, source.medical_history)
        if merged_medical_history != target.medical_history:
            update_fields["medical_history"] = merged_medical_history

        merged_notes = merge_text(target.notes, source.notes)
        if merged_notes != target.notes:
            update_fields["notes"] = merged_notes

        if update_fields:
            assignments = ", ".join(f"{field} = ?" for field in update_fields)
            self.db.conn.execute(
                f"UPDATE animals SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                tuple(update_fields.values()) + (target_animal_id,)
            )

        if source.microchip and target.microchip and source.microchip != target.microchip:
            self.db.conn.execute("""
                INSERT OR IGNORE INTO animal_identifiers (
                    animal_id, source_system, identifier_type, identifier_value
                ) VALUES (?, ?, ?, ?)
            """, (target_animal_id, "merge", "microchip", source.microchip))

        self.db.conn.execute("""
            INSERT OR IGNORE INTO animal_identifiers (
                animal_id, source_system, identifier_type, identifier_value, created_at
            )
            SELECT ?, source_system, identifier_type, identifier_value, created_at
            FROM animal_identifiers
            WHERE animal_id = ?
        """, (target_animal_id, source_animal_id))
        self.db.conn.execute(
            "DELETE FROM animal_identifiers WHERE animal_id = ?",
            (source_animal_id,),
        )

        for table in (
            "test_sessions",
            "symptoms",
            "observations",
            "clinical_notes",
            "animal_vet_assignments",
            "diagnosis_reports",
        ):
            self.db.conn.execute(
                f"UPDATE {table} SET animal_id = ? WHERE animal_id = ?",
                (target_animal_id, source_animal_id),
            )

        self.db.conn.execute("""
            UPDATE unassigned_reports
            SET assigned_animal_id = ?
            WHERE assigned_animal_id = ?
        """, (target_animal_id, source_animal_id))
        self.db.conn.execute("""
            UPDATE email_import_log
            SET animal_id = ?
            WHERE animal_id = ?
        """, (target_animal_id, source_animal_id))

        cursor = self.db.conn.execute(
            "DELETE FROM animals WHERE id = ?",
            (source_animal_id,),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Symptoms
    # -------------------------------------------------------------------------

    def create_symptom(self, symptom: Symptom) -> int:
        """Create a symptom record for an animal."""
        cursor = self.db.conn.execute("""
            INSERT INTO symptoms (animal_id, observed_date, description,
                                 severity, category, resolved_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (symptom.animal_id, symptom.observed_date, symptom.description,
              symptom.severity, symptom.category, symptom.resolved_date,
              symptom.notes))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_symptoms(self, animal_id: int, active_only: bool = False) -> List[Symptom]:
        """Get symptoms for an animal."""
        if active_only:
            cursor = self.db.conn.execute("""
                SELECT * FROM symptoms
                WHERE animal_id = ? AND resolved_date IS NULL
                ORDER BY observed_date DESC
            """, (animal_id,))
        else:
            cursor = self.db.conn.execute("""
                SELECT * FROM symptoms WHERE animal_id = ?
                ORDER BY observed_date DESC
            """, (animal_id,))
        return [Symptom(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Observations
    # -------------------------------------------------------------------------

    def create_observation(self, observation: Observation) -> int:
        """Create an observation record."""
        cursor = self.db.conn.execute("""
            INSERT INTO observations (animal_id, observation_date,
                                     observation_type, details, value, unit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (observation.animal_id, observation.observation_date,
              observation.observation_type, observation.details,
              observation.value, observation.unit))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_observations(self, animal_id: int,
                        obs_type: Optional[str] = None) -> List[Observation]:
        """Get observations for an animal, optionally filtered by type."""
        if obs_type:
            cursor = self.db.conn.execute("""
                SELECT * FROM observations
                WHERE animal_id = ? AND observation_type = ?
                ORDER BY observation_date DESC
            """, (animal_id, obs_type))
        else:
            cursor = self.db.conn.execute("""
                SELECT * FROM observations WHERE animal_id = ?
                ORDER BY observation_date DESC
            """, (animal_id,))
        return [Observation(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Clinical Notes
    # -------------------------------------------------------------------------

    def create_clinical_note(self, note: ClinicalNote) -> int:
        """Create a clinical note."""
        cursor = self.db.conn.execute("""
            INSERT INTO clinical_notes (
                animal_id, note_date, title, content,
                author_user_id, updated_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            note.animal_id,
            note.note_date,
            note.title,
            note.content,
            note.author_user_id,
            note.updated_by_user_id or note.author_user_id,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_clinical_note(self, note_id: int) -> Optional[ClinicalNote]:
        """Get a clinical note by ID."""
        cursor = self.db.conn.execute("""
            SELECT
                cn.*,
                COALESCE(author.display_name, author.email) AS author_name,
                COALESCE(editor.display_name, editor.email) AS updated_by_name
            FROM clinical_notes cn
            LEFT JOIN users author ON author.id = cn.author_user_id
            LEFT JOIN users editor ON editor.id = cn.updated_by_user_id
            WHERE cn.id = ?
        """, (note_id,))
        row = cursor.fetchone()
        if row:
            return ClinicalNote(**dict(row))
        return None

    def get_clinical_notes(self, animal_id: int) -> List[ClinicalNote]:
        """Get all clinical notes for an animal."""
        cursor = self.db.conn.execute("""
            SELECT
                cn.*,
                COALESCE(author.display_name, author.email) AS author_name,
                COALESCE(editor.display_name, editor.email) AS updated_by_name
            FROM clinical_notes cn
            LEFT JOIN users author ON author.id = cn.author_user_id
            LEFT JOIN users editor ON editor.id = cn.updated_by_user_id
            WHERE cn.animal_id = ?
            ORDER BY cn.note_date DESC, cn.created_at DESC
        """, (animal_id,))
        return [ClinicalNote(**dict(row)) for row in cursor.fetchall()]

    def update_clinical_note(self, note_id: int, title: Optional[str],
                            content: str, note_date=None,
                            updated_by_user_id: Optional[int] = None) -> bool:
        """Update a clinical note."""
        cursor = self.db.conn.execute("""
            UPDATE clinical_notes
            SET title = ?,
                content = ?,
                note_date = ?,
                updated_by_user_id = COALESCE(?, updated_by_user_id),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (title, content, note_date, updated_by_user_id, note_id))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete_clinical_note(self, note_id: int) -> bool:
        """Delete a clinical note."""
        cursor = self.db.conn.execute(
            "DELETE FROM clinical_notes WHERE id = ?", (note_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0
