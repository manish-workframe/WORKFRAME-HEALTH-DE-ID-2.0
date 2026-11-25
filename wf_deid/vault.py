"""Encrypted ID vault, kept only inside the hospital.

Gives every patient, admission and imaging study a random UUID (as the company requires) and remembers it,
so the same patient keeps the same anon_patient_id in every future batch. The vault never stores names or
raw hospital numbers: entries are keyed by a keyed hash (HMAC) of the hospital number, so even an opened
vault does not list patients. It is encrypted with a password chosen by the hospital (scrypt + Fernet/AES).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class VaultError(Exception):
    pass


def _key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class Vault:
    def __init__(self, path, password):
        if not password or len(password) < 8:
            raise VaultError("The vault password must be at least 8 characters.")
        self.path = Path(path)
        self.password = password
        if self.path.exists():
            blob = self.path.read_bytes()
            salt, token = blob[:16], blob[16:]
            try:
                self.data = json.loads(Fernet(_key(password, salt)).decrypt(token))
            except InvalidToken:
                raise VaultError("Wrong vault password. Codes can only stay consistent with the original password.")
            self.salt = salt
            self.new = False
        else:
            self.salt = os.urandom(16)
            self.data = {"secret": secrets.token_hex(32), "created": datetime.now().isoformat(timespec="seconds"),
                         "patients": {}, "encounters": {}, "studies": {}, "series": {}, "documents": {}, "batches": []}
            self.new = True
        self.secret = bytes.fromhex(self.data["secret"])

    def h(self, purpose, value):
        v = " ".join(str(value).strip().upper().split())
        return hmac.new(self.secret, f"{purpose}|{v}".encode(), hashlib.sha256).hexdigest()

    def _get(self, table, purpose, value):
        k = self.h(purpose, value)
        t = self.data[table]
        if k not in t:
            t[k] = str(uuid.uuid4())
        return t[k]

    def patient(self, key):
        return self._get("patients", "patient", key)

    def encounter(self, key):
        return self._get("encounters", "encounter", key)

    def study(self, original_study_uid):
        return self._get("studies", "study", original_study_uid)

    def document(self, encounter_key):
        return self._get("documents", "document", encounter_key)

    def is_known_patient(self, key):
        return self.h("patient", key) in self.data["patients"]

    def uid(self, original_uid):
        """Replacement DICOM UID (2.25 + 128-bit number), stable across batches, not reversible."""
        n = int(self.h("uid", original_uid)[:32], 16)
        return f"2.25.{n}"

    def lookup_patient(self, hospital_number):
        """For the hospital only: which anon_patient_id belongs to this hospital number?"""
        return self.data["patients"].get(self.h("patient", hospital_number))

    def save(self, batch_note=None):
        if batch_note:
            self.data["batches"].append(batch_note)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = Fernet(_key(self.password, self.salt)).encrypt(json.dumps(self.data).encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self.salt + token)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
