"""
schemas.py — Dataclass definitions for the parsed resume output.

Using dataclasses + asdict() keeps serialisation trivial while still
giving type hints and defaults.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PersonalDetails:
    dob: str = ""
    marital_status: str = ""
    nationality: str = ""
    gender: str = ""
    father_name: str = ""


@dataclass
class WorkEntry:
    """A single work-experience block."""
    company: str = ""
    title: str = ""
    duration: str = ""
    description: str = ""


@dataclass
class EducationEntry:
    """A single education block."""
    institution: str = ""
    degree: str = ""
    year: str = ""
    score: str = ""


@dataclass
class CandidateRecord:
    """Top-level record for one resume."""
    full_name: str = ""
    phone_number: str = ""
    email: str = ""
    location_city: str = ""
    summary_objective_profile: str = ""
    work_experience: list = field(default_factory=list)
    education: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    linkedin_profile: str = ""
    languages: list = field(default_factory=list)
    certifications_courses: list = field(default_factory=list)
    personal_details: PersonalDetails = field(default_factory=PersonalDetails)
    internships_projects: list = field(default_factory=list)
    awards_achievements: list = field(default_factory=list)
    declaration_reference: str = ""
    source_file: str = ""
    raw_text: str = ""
    field_confidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
