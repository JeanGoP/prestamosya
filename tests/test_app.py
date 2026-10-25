import base64
import importlib
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


class LoanFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_data_dir = Path(__file__).resolve().parent.parent / "data"
        test_data_dir.mkdir(exist_ok=True)
        cls.temp_dir = tempfile.TemporaryDirectory(dir=test_data_dir)
        os.environ["DATA_DIR"] = cls.temp_dir.name
        os.environ["FLASK_SECRET_KEY"] = "test-secret-only"
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "test-password"
        os.environ["COOKIE_SECURE"] = "false"
        cls.module = importlib.import_module("app")
        cls.module.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.client = self.module.app.test_client()
        response = self.client.post("/login", data={"username": "admin", "password": "test-password"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.csrf = session["csrf_token"]

    def test_examples_and_schedule(self):
        start = date(2026, 10, 20)
        interest, total, installments = self.module.calculate_schedule(200_000, 1000, 1, "weekly", start)
        self.assertEqual((interest, total), (20_000, 220_000))
        self.assertEqual([due.day for due, _ in installments], [27, 3, 10, 17, 19])
        self.assertEqual(sum(amount for _, amount in installments), 220_000)
        self.assertEqual(installments[-1][0], start + self.module.timedelta(days=30))
        self.assertEqual(self.module.calculate_schedule(200_000, 1000, 2, "monthly", start)[1], 240_000)

    def test_client_loan_and_partial_payment(self):
        response = self.client.post("/clientes/nuevo", data={
            "csrf_token": self.csrf,
            "identification": "12345", "first_name": "Ana", "last_name": "Pérez",
        })
        self.assertEqual(response.status_code, 302)
        client_id = int(response.location.rstrip("/").split("/")[-1])
        response = self.client.post("/prestamos/nuevo", data={
            "csrf_token": self.csrf, "client_id": client_id, "principal": 200000,
            "rate": "10", "months": 1, "frequency": "fortnightly",
            "start_date": date.today().isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        loan_id = int(response.location.rstrip("/").split("/")[-2])
        response = self.client.post(f"/prestamos/{loan_id}/pagos", data={
            "csrf_token": self.csrf, "amount": 50000, "paid_on": date.today().isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "SELECT amount, paid_amount FROM installments WHERE loan_id=? ORDER BY number", (loan_id,)
            ).fetchall()
            self.assertEqual([(row["amount"], row["paid_amount"]) for row in rows], [(110000, 50000), (110000, 0)])
        self.assertIn(b"$170.000", self.client.get(f"/prestamos/{loan_id}").data)

    def test_new_client_from_loan_returns_with_client_selected(self):
        form = self.client.get("/prestamos/nuevo")
        self.assertIn(b"/clientes/nuevo?return_to=loan", form.data)
        response = self.client.post("/clientes/nuevo?return_to=loan", data={
            "csrf_token": self.csrf,
            "identification": "98765", "first_name": "Luis", "last_name": "Gómez",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/prestamos/nuevo?client_id=", response.location)
        client_id = response.location.rsplit("=", 1)[-1]
        loan_form = self.client.get(response.location)
        self.assertIn(b"Luis G", loan_form.data)
        self.assertIn(f'<option value="{client_id}" selected>'.encode(), loan_form.data)

    def test_contract_signing_saves_immutable_pdf(self):
        settings = {
            "csrf_token": self.csrf,
            "lender_name": "María Gómez", "lender_identification": "101010",
            "lender_id_issued_in": "Bogotá", "lender_city": "Bogotá",
            "lender_phone": "3001234567", "payment_instructions": "Transferencia bancaria acordada",
        }
        self.assertEqual(self.client.post("/configuracion/contrato", data=settings).status_code, 302)
        client = self.client.post("/clientes/nuevo", data={
            "csrf_token": self.csrf, "identification": "contract-1",
            "first_name": "Andrés", "last_name": "López", "phone": "3111234567",
            "id_issued_in": "Medellín", "city": "Cali",
        })
        client_id = int(client.location.rsplit("/", 1)[-1])
        response = self.client.post("/prestamos/nuevo", data={
            "csrf_token": self.csrf, "client_id": client_id, "principal": 500000,
            "rate": "10", "months": 2, "frequency": "fortnightly",
            "start_date": date.today().isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/contrato"))
        contract_url = response.location
        preview = self.client.get(contract_url)
        self.assertIn("$600.000".encode(), preview.data)
        self.assertIn("Andrés López".encode(), preview.data)
        self.assertIn("Medellín".encode(), preview.data)
        self.assertIn(b"debtor-pad", preview.data)

        image = Image.new("RGB", (800, 240), "white")
        draw = ImageDraw.Draw(image)
        draw.line([(50, 140), (230, 70), (450, 145), (700, 80)], fill="black", width=5)
        output = BytesIO()
        image.save(output, format="PNG")
        signature = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        self.module.app.config["TESTING"] = False
        try:
            insecure = self.client.post(contract_url + "/firmar", data={
                "csrf_token": self.csrf, "accepted": "yes",
                "debtor_signature": signature, "lender_signature": signature,
            })
            self.assertEqual(insecure.status_code, 302)
            self.assertEqual(self.client.get(contract_url + ".pdf").status_code, 404)
        finally:
            self.module.app.config["TESTING"] = True
        signed = self.client.post(contract_url + "/firmar", data={
            "csrf_token": self.csrf, "accepted": "yes",
            "debtor_signature": signature, "lender_signature": signature,
        })
        self.assertEqual(signed.status_code, 302)
        pdf_url = contract_url + ".pdf"
        pdf = self.client.get(pdf_url)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertGreater(len(pdf.data), 5000)
        self.assertIn("attachment", pdf.headers["Content-Disposition"])
        preview_url = contract_url + "/visualizar"
        self.assertIn(b"/contrato/ver.pdf", self.client.get(preview_url).data)
        inline_pdf = self.client.get(contract_url + "/ver.pdf")
        self.assertEqual(inline_pdf.status_code, 200)
        self.assertEqual(inline_pdf.data, pdf.data)
        self.assertIn("inline", inline_pdf.headers["Content-Disposition"])
        self.assertEqual(inline_pdf.headers["Cache-Control"], "private, no-store")
        loan_id = int(contract_url.rstrip("/").split("/")[-2])
        self.assertIn(preview_url.encode(), self.client.get(f"/prestamos/{loan_id}").data)
        self.assertEqual(self.module.app.test_client().get(contract_url + "/ver.pdf").status_code, 302)
        settings["lender_name"] = "Otra persona"
        self.client.post("/configuracion/contrato", data=settings)
        self.assertEqual(self.client.get(pdf_url).data, pdf.data)
        self.client.post(contract_url + "/firmar", data={
            "csrf_token": self.csrf, "accepted": "yes",
            "debtor_signature": signature, "lender_signature": signature,
        })
        self.assertEqual(self.client.get(pdf_url).data, pdf.data)
        with self.module.app.app_context():
            contract = self.module.db().execute("SELECT filename FROM contracts WHERE loan_id=?", (loan_id,)).fetchone()
            path = self.module.CONTRACT_DIR / contract["filename"]
            path.write_bytes(b"alterado")
        self.assertEqual(self.client.get(pdf_url).status_code, 409)
        self.assertEqual(self.client.get(contract_url + "/ver.pdf").status_code, 409)

    def test_existing_client_database_is_migrated(self):
        original_data_dir = self.module.DATA_DIR
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / "data") as legacy_dir:
            database = Path(legacy_dir) / "prestaya.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("""
                CREATE TABLE clients (id INTEGER PRIMARY KEY, identification TEXT NOT NULL UNIQUE,
                    first_name TEXT NOT NULL, last_name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '', photo TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)
            """)
            connection.execute("INSERT INTO clients (identification, first_name, last_name) VALUES ('old-1', 'Ana', 'Pérez')")
            connection.commit()
            connection.close()
            try:
                self.module.DATA_DIR = Path(legacy_dir)
                self.module.init_db()
                connection = sqlite3.connect(database)
                row = connection.execute("SELECT first_name, id_issued_in, city FROM clients WHERE identification='old-1'").fetchone()
                self.assertEqual(row, ("Ana", "", ""))
                connection.close()
            finally:
                self.module.DATA_DIR = original_data_dir

    def test_z_reset_requires_separate_key_and_preserves_configuration(self):
        key_url = "/configuracion/borrar-datos/clave"
        reset_url = "/configuracion/borrar-datos"
        self.assertEqual(self.client.get(reset_url).status_code, 302)
        bad_setup = self.client.post(key_url, data={
            "csrf_token": self.csrf, "current_password": "wrong",
            "new_password": "my-private-reset-key", "confirm_password": "my-private-reset-key",
        })
        self.assertEqual(bad_setup.status_code, 200)
        with self.module.app.app_context():
            self.assertIsNone(self.module.db().execute("SELECT 1 FROM reset_security WHERE id=1").fetchone())
        setup = self.client.post(key_url, data={
            "csrf_token": self.csrf, "current_password": "test-password",
            "new_password": "my-private-reset-key", "confirm_password": "my-private-reset-key",
        })
        self.assertEqual(setup.status_code, 302)
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("INSERT INTO clients (identification, first_name, last_name, photo) VALUES (?, ?, ?, ?)",
                               ("reset-test", "Reset", "Test", "reset-photo.jpg"))
            client_id = connection.execute("SELECT id FROM clients WHERE identification='reset-test'").fetchone()[0]
            connection.execute("""INSERT INTO loans (client_id, principal, monthly_rate_bps, term_months,
                               frequency, start_date, interest_amount, total_amount)
                               VALUES (?, 100000, 1000, 1, 'monthly', '2026-10-21', 10000, 110000)""", (client_id,))
            loan_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO installments (loan_id, number, due_date, amount) VALUES (?, 1, '2026-11-20', 110000)", (loan_id,))
            connection.execute("INSERT INTO payments (loan_id, amount, paid_on) VALUES (?, 1000, '2026-10-21')", (loan_id,))
            payment_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            installment_id = connection.execute("SELECT id FROM installments WHERE loan_id=?", (loan_id,)).fetchone()[0]
            connection.execute("INSERT INTO payment_allocations (payment_id, installment_id, amount) VALUES (?, ?, 1000)",
                               (payment_id, installment_id))
            connection.execute("""INSERT INTO contracts (loan_id, filename, signed_at, debtor_signature_sha256,
                               lender_signature_sha256, document_sha256)
                               VALUES (?, 'reset-contract.pdf', '2026-10-21', 'a', 'b', 'c')""", (loan_id,))
            connection.commit()
        photo = self.module.UPLOAD_DIR / "reset-photo.jpg"
        contract = self.module.CONTRACT_DIR / "reset-contract.pdf"
        photo.write_bytes(b"photo")
        contract.write_bytes(b"pdf")
        for confirmation, password in (("wrong", "my-private-reset-key"), ("BORRAR TODO", "wrong")):
            self.assertEqual(self.client.post(reset_url, data={
                "csrf_token": self.csrf, "confirmation": confirmation, "reset_password": password,
            }).status_code, 200)
        for _ in range(4):
            self.client.post(reset_url, data={
                "csrf_token": self.csrf, "confirmation": "BORRAR TODO", "reset_password": "wrong",
            })
        blocked = self.client.post(reset_url, data={
            "csrf_token": self.csrf, "confirmation": "BORRAR TODO", "reset_password": "my-private-reset-key",
        })
        self.assertEqual(blocked.status_code, 200)
        self.assertIn(b"15 minutos", blocked.data)
        with self.module.app.app_context():
            self.assertGreater(self.module.db().execute("SELECT COUNT(*) FROM loans").fetchone()[0], 0)
        self.assertEqual(self.client.post(key_url, data={
            "csrf_token": self.csrf, "current_password": "test-password",
            "new_password": "my-private-reset-key", "confirm_password": "my-private-reset-key",
        }).status_code, 302)
        self.assertEqual(self.client.post(reset_url, data={
            "csrf_token": self.csrf, "confirmation": "BORRAR TODO", "reset_password": "my-private-reset-key",
        }).status_code, 302)
        with self.module.app.app_context():
            connection = self.module.db()
            for table in ("contracts", "payment_allocations", "payments", "installments", "loans", "clients"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM contract_settings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reset_security").fetchone()[0], 1)
        self.assertFalse(photo.exists())
        self.assertFalse(contract.exists())


if __name__ == "__main__":
    unittest.main()
