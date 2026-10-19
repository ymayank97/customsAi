import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app import db, deps, job_store, reviews
from backend.app.auth_token import mint_access_token, verify_google_token
from backend.app.config import settings
from backend.app.membership import capabilities


class PremiumReviews(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        db.Base.metadata.create_all(self.engine)
        self.source = {"row_idx": 0, "item_description": "Portable computer", "declared_cth": "84713090",
                       "final": {"cth_final": "84713010", "bcd_final": 10}}
        with Session(self.engine) as session:
            session.add_all([
                db.User(id=1, google_sub="one", email="officer@example.test", name="Officer", account_tier="premium"),
                db.User(id=2, google_sub="two", email="standard@example.test", name="Standard"),
                db.User(id=3, google_sub="three", email="other@example.test", name="Other", account_tier="premium"),
                db.AuditJob(id="owned", user_id=1, filename="test.xlsx", status="done"),
                db.AuditJob(id="other", user_id=3, filename="other.xlsx", status="done"),
                db.AuditJob(id="unfinished", user_id=1, filename="pending.xlsx", status="running"),
            ])
            session.commit()
        self.patches = [
            patch.object(settings, "google_oauth_client_id", "test-client"),
            patch.object(settings, "jwt_secret", "review-tests-only-secret-32-characters"),
            patch.object(settings, "premium_emails", []), patch.object(settings, "admin_emails", []),
            patch.object(reviews, "get_engine", return_value=self.engine),
            patch.object(deps, "get_engine", return_value=self.engine),
            patch.object(job_store, "get_engine", return_value=self.engine),
            patch.object(job_store, "_hydrate", side_effect=lambda job: SimpleNamespace(
                id=job.id, status=job.status, results=[self.source])),
            patch.object(reviews, "get_master_row", side_effect=lambda code: {"cth": code} if code in ("84713010", "84713090") else None),
        ]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(reviews.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def headers(self, uid=1):
        return {"Authorization": "Bearer " + mint_access_token(user_id=uid, email="unused", secret=settings.jwt_secret, expire_days=1)}

    def payload(self, **changes):
        return {"job_id": "owned", "row_idx": 0, "decision": "confirmed", "reference": "Invoice and tariff note",
                "reasoning": "Product specifications verified.", **changes}

    def save(self, uid=1, **changes):
        return self.client.post("/api/reviews", headers=self.headers(uid), json=self.payload(**changes))

    def export(self, uid=1):
        response = self.client.get("/api/reviews/export", headers=self.headers(uid))
        self.assertEqual(response.status_code, 200)
        return list(csv.DictReader(io.StringIO(response.text)))

    def test_only_authenticated_premium_accounts_can_save_or_export(self):
        self.assertEqual(self.client.post("/api/reviews", json=self.payload()).status_code, 401)
        self.assertEqual(self.save(uid=2).status_code, 403)
        self.assertEqual(self.client.get("/api/reviews", headers=self.headers(2)).status_code, 403)
        self.assertEqual(self.client.get("/api/reviews/export", headers=self.headers(2)).status_code, 403)
        with patch.object(settings, "google_oauth_client_id", ""):
            self.assertEqual(self.save().status_code, 401)

    def test_membership_is_resolved_from_database_each_request(self):
        self.assertEqual(self.save().status_code, 200)
        with Session(self.engine) as session:
            session.get(db.User, 1).account_tier = "standard"
            session.commit()
        self.assertEqual(self.save().status_code, 403)
        with patch.object(settings, "premium_emails", ["officer@example.test"]):
            self.assertEqual(self.save().status_code, 200)

    def test_snapshot_comes_from_owned_audit_not_browser_fields(self):
        saved = self.save().json()["review"]
        self.assertEqual(saved["item_description"], self.source["item_description"])
        self.assertEqual(saved["predicted_cth"], "84713010")
        self.assertEqual(saved["corrected_cth"], "84713010")
        self.assertEqual(self.save(item_description="Injected example").status_code, 422)
        self.assertEqual(self.save(job_id="other").status_code, 404)
        self.assertEqual(self.save(job_id="missing").status_code, 404)
        self.assertEqual(self.save(row_idx=1).status_code, 404)
        self.assertEqual(self.save(job_id="unfinished").status_code, 409)

    def test_tariff_and_evidence_validation(self):
        self.assertEqual(self.save(reference="  ").status_code, 422)
        self.assertEqual(self.save(reasoning=" ").status_code, 422)
        self.assertEqual(self.save(decision="corrected", corrected_cth="99999999").status_code, 422)
        self.assertEqual(self.save(decision="corrected", corrected_cth="8471").status_code, 422)
        self.assertEqual(self.save(decision="corrected", corrected_cth="84713010").status_code, 422)
        self.assertEqual(self.save(corrected_cth="84713090").status_code, 422)
        self.assertEqual(self.save(corrected_bcd=-1).status_code, 422)
        self.assertEqual(self.save(corrected_bcd="NaN").status_code, 422)

    def test_repeated_save_updates_one_label_and_preserves_original_suggestion(self):
        first = self.save().json()["review"]
        second = self.save(decision="corrected", corrected_cth="84713090", corrected_bcd=0).json()["review"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertEqual(second["predicted_cth"], "84713010")
        self.assertEqual(second["corrected_bcd"], 0)
        self.assertEqual(reviews.load_labels(1, "owned")[0]["corrected_cth"], "84713090")
        self.assertEqual(self.export()[0]["gold_cth"], "84713090")
        self.assertEqual(self.export()[0]["corrected_bcd"], "0.0")

    def test_needs_information_never_exports_a_training_code(self):
        saved = self.save(decision="needs_information", reference="", corrected_cth="84713090").json()["review"]
        self.assertIsNone(saved["corrected_cth"])
        self.assertEqual(self.export(), [])
        result = self.client.get("/api/reviews", headers=self.headers()).json()
        self.assertEqual(result["summary"]["needs_information"], 1)

    def test_export_deduplicates_and_holds_conflicts_until_resolved(self):
        self.save()
        self.source = {**self.source, "item_description": " PORTABLE   COMPUTER "}
        self.assertEqual(self.save(uid=3, job_id="other").status_code, 200)
        with patch.object(settings, "admin_emails", ["officer@example.test"]):
            self.assertEqual(len(self.export()), 1)
            self.save(uid=3, job_id="other", decision="corrected", corrected_cth="84713090")
            self.assertEqual(self.export(), [])
            result = self.client.get("/api/reviews", headers=self.headers()).json()
            self.assertEqual(result["summary"]["conflicts"], 2)
            self.save(uid=3, job_id="other", decision="corrected", corrected_cth="84713090", product_details="Different model, invoice verified")
            exported = self.export()
            self.assertEqual(len(exported), 2)
            detailed = next(row for row in exported if row["gold_cth"] == "84713090")
            self.assertIn("Product details:", detailed["item_description"])
            self.assertIn("Officer review", detailed["label_source"])

    def test_reviews_are_private_and_admin_can_export_all(self):
        self.save()
        self.assertEqual(self.client.get("/api/reviews", headers=self.headers(3)).json()["total"], 0)
        with patch.object(settings, "admin_emails", ["other@example.test"]):
            result = self.client.get("/api/reviews", headers=self.headers(3)).json()
            self.assertEqual(result["scope"], "all_reviewers")
            self.assertEqual(result["total"], 1)
            self.assertEqual(len(self.export(uid=3)), 1)
        self.assertEqual(self.client.get("/api/reviews?page=0", headers=self.headers()).status_code, 422)


class MembershipAndMigrations(unittest.TestCase):
    def test_verified_google_email_is_required(self):
        claims = {"iss": "accounts.google.com", "sub": "subject", "email": "officer@example.test"}
        with patch("backend.app.auth_token.id_token.verify_oauth2_token", return_value=claims):
            with self.assertRaises(ValueError):
                verify_google_token("credential", "audience")
            claims["email_verified"] = True
            self.assertEqual(verify_google_token("credential", "audience")["email"], claims["email"])

    def test_allowlist_uses_verified_email_not_display_name(self):
        with patch.object(settings, "admin_emails", ["admin@example.test"]), patch.object(settings, "premium_emails", []):
            self.assertFalse(capabilities(db.User(email="plain@example.test", name="admin@example.test", account_tier="standard"))["can_label_rows"])
            self.assertTrue(capabilities(db.User(email="ADMIN@example.test", account_tier="standard"))["is_admin"])

    def test_alembic_upgrade_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + (Path(directory) / "migration.db").as_posix()
            with patch.dict("os.environ", {"DATABASE_URL": url}):
                config = Config("backend/alembic.ini")
                command.upgrade(config, "head")
                engine = create_engine(url)
                self.assertIn("training_labels", inspect(engine).get_table_names())
                self.assertIn("account_tier", {column["name"] for column in inspect(engine).get_columns("users")})
                command.downgrade(config, "20260614_0002")
                self.assertNotIn("training_labels", inspect(engine).get_table_names())
                engine.dispose()

    def test_existing_sqlite_users_get_standard_access_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + (Path(directory) / "old.db").as_posix()
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, google_sub TEXT, email TEXT, name TEXT, picture TEXT, created_at DATETIME)"))
                conn.execute(text("INSERT INTO users (id, google_sub, email) VALUES (1, 'old', 'old@example.test')"))
            engine.dispose()
            with patch.object(db, "_ENGINE", None), patch.dict("os.environ", {"DATABASE_URL": url}):
                upgraded = db.get_engine()
                with Session(upgraded) as session:
                    user = session.get(db.User, 1)
                    self.assertEqual(user.account_tier, "standard")
                    self.assertEqual(user.email, "old@example.test")
                upgraded.dispose()


if __name__ == "__main__":
    unittest.main()
