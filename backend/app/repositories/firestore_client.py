"""
Firestore client initialisation.
Uses google-cloud-firestore with service account credentials.
Falls back to emulator if FIRESTORE_EMULATOR_HOST is set.
"""
from __future__ import annotations

import os
from functools import lru_cache

from google.cloud import firestore

from app.core.config import get_settings

_settings = get_settings()


@lru_cache(maxsize=1)
def get_firestore_client() -> firestore.Client:
    if _settings.use_firestore_emulator and _settings.firestore_emulator_host:
        os.environ["FIRESTORE_EMULATOR_HOST"] = _settings.firestore_emulator_host

    if _settings.google_application_credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _settings.google_application_credentials

    return firestore.Client(project=_settings.firebase_project_id)
