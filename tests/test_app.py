import importlib
import os
import tempfile
import unittest
from datetime import date


class LoanFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
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
        loan_id = int(response.location.rstrip("/").split("/")[-1])
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


if __name__ == "__main__":
    unittest.main()
