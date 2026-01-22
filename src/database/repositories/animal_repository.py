"""
Animal Repository for VetScan

Handles all database operations for Animal entities.
"""

from typing import List, Optional

from models.domain import Animal, Symptom, Observation, ClinicalNote


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
                                age_months, sex, weight_kg, neutered,
                                medical_history, notes, responsible_vet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (animal.name, animal.species, animal.breed, animal.microchip,
              animal.age_years, animal.age_months, animal.sex, animal.weight_kg,
              animal.neutered, animal.medical_history, animal.notes,
              animal.responsible_vet))
        self.db.conn.commit()
        return cursor.lastrowid

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

    def find_or_create(self, animal: Animal) -> int:
        """
        Find existing animal or create new one.

        First tries to find by microchip (most reliable),
        then by name + species combination.

        Args:
            animal: Animal to find or create

        Returns:
            ID of existing or newly created animal
        """
        # Try to find by microchip first
        if animal.microchip:
            cursor = self.db.conn.execute(
                "SELECT id FROM animals WHERE microchip = ?", (animal.microchip,))
            row = cursor.fetchone()
            if row:
                return row['id']

        # Try to find by name + species
        cursor = self.db.conn.execute("""
            SELECT id FROM animals
            WHERE LOWER(name) = LOWER(?) AND LOWER(species) = LOWER(?)
        """, (animal.name, animal.species))
        row = cursor.fetchone()
        if row:
            # Update breed if it was "Indeterminado" or empty
            self.db.conn.execute("""
                UPDATE animals SET breed = ?
                WHERE id = ? AND (breed IS NULL OR breed = '' OR breed = 'Indeterminado')
            """, (animal.breed, row['id']))
            self.db.conn.commit()
            return row['id']

        # Create new animal
        return self.create(animal)

    def list_all(self) -> List[Animal]:
        """
        List all animals ordered by name.

        Returns:
            List of all animals
        """
        cursor = self.db.conn.execute("SELECT * FROM animals ORDER BY name")
        return [Animal(**dict(row)) for row in cursor.fetchall()]

    def update(self, animal_id: int, **kwargs) -> bool:
        """
        Update animal fields.

        Args:
            animal_id: ID of the animal to update
            **kwargs: Fields to update

        Returns:
            True if update was successful
        """
        allowed_fields = {'name', 'species', 'breed', 'microchip', 'age_years',
                         'age_months', 'sex', 'weight_kg', 'neutered',
                         'medical_history', 'notes', 'responsible_vet'}
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        values = list(update_fields.values()) + [animal_id]

        cursor = self.db.conn.execute(
            f"UPDATE animals SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values)
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
            INSERT INTO clinical_notes (animal_id, note_date, title, content)
            VALUES (?, ?, ?, ?)
        """, (note.animal_id, note.note_date, note.title, note.content))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_clinical_note(self, note_id: int) -> Optional[ClinicalNote]:
        """Get a clinical note by ID."""
        cursor = self.db.conn.execute(
            "SELECT * FROM clinical_notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        if row:
            return ClinicalNote(**dict(row))
        return None

    def get_clinical_notes(self, animal_id: int) -> List[ClinicalNote]:
        """Get all clinical notes for an animal."""
        cursor = self.db.conn.execute("""
            SELECT * FROM clinical_notes WHERE animal_id = ?
            ORDER BY note_date DESC, created_at DESC
        """, (animal_id,))
        return [ClinicalNote(**dict(row)) for row in cursor.fetchall()]

    def update_clinical_note(self, note_id: int, title: Optional[str],
                            content: str, note_date=None) -> bool:
        """Update a clinical note."""
        cursor = self.db.conn.execute("""
            UPDATE clinical_notes
            SET title = ?, content = ?, note_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (title, content, note_date, note_id))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def delete_clinical_note(self, note_id: int) -> bool:
        """Delete a clinical note."""
        cursor = self.db.conn.execute(
            "DELETE FROM clinical_notes WHERE id = ?", (note_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0
