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
        settings["lender_name"] = "Otra persona"
        self.client.post("/configuracion/contrato", data=settings)
        self.assertEqual(self.client.get(pdf_url).data, pdf.data)
        self.client.post(contract_url + "/firmar", data={
            "csrf_token": self.csrf, "accepted": "yes",
            "debtor_signature": signature, "lender_signature": signature,
        })
        self.assertEqual(self.client.get(pdf_url).data, pdf.data)
        loan_id = int(contract_url.rstrip("/").split("/")[-2])
        with self.module.app.app_context():
            contract = self.module.db().execute("SELECT filename FROM contracts WHERE loan_id=?", (loan_id,)).fetchone()
            path = self.module.CONTRACT_DIR / contract["filename"]
            path.write_bytes(b"alterado")
        self.assertEqual(self.client.get(pdf_url).status_code, 409)

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


if __name__ == "__main__":
    unittest.main()
