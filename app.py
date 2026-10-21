import os
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_from_directory,
    session, url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
Image.MAX_IMAGE_PIXELS = 20_000_000
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()

if not SECRET_KEY or not ADMIN_PASSWORD or not ADMIN_USERNAME:
    raise RuntimeError("Configure FLASK_SECRET_KEY, ADMIN_USERNAME y ADMIN_PASSWORD.")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
)


def db():
    if "db" not in g:
        connection = sqlite3.connect(DATA_DIR / "prestaya.sqlite3", timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_error):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = sqlite3.connect(DATA_DIR / "prestaya.sqlite3")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    password_hash = generate_password_hash(ADMIN_PASSWORD)
    connection.execute("DELETE FROM users WHERE username <> ?", (ADMIN_USERNAME,))
    connection.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?) "
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash",
        (ADMIN_USERNAME, password_hash),
    )
    connection.commit()
    connection.close()


init_db()


def money(value):
    return "$" + f"{value:,.0f}".replace(",", ".")


def percent(bps):
    return f"{bps / 100:g}%"


app.jinja_env.filters["money"] = money
app.jinja_env.filters["percent"] = percent


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def protect_forms():
    if request.method == "POST" and request.endpoint != "login":
        expected = session.get("csrf_token", "")
        received = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected, received):
            abort(400, "Formulario vencido. Actualice la página e intente de nuevo.")


@app.context_processor
def template_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"], "today": date.today().isoformat()}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = db().execute("SELECT * FROM users WHERE username = ?", (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def dashboard():
    connection = db()
    totals = connection.execute("""
        SELECT COUNT(*) AS loan_count,
               COALESCE(SUM(total_amount), 0) AS lent_total,
               COALESCE(SUM(total_amount - (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id = loans.id)), 0) AS outstanding
        FROM loans
    """).fetchone()
    due = connection.execute("""
        SELECT i.*, l.id AS loan_id, c.first_name, c.last_name,
               l.total_amount - (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id=l.id) AS loan_balance
        FROM installments i JOIN loans l ON l.id=i.loan_id JOIN clients c ON c.id=l.client_id
        WHERE i.due_date <= ? AND i.paid_amount < i.amount
        ORDER BY i.due_date, i.id LIMIT 25
    """, (date.today().isoformat(),)).fetchall()
    recent = connection.execute("""
        SELECT l.*, c.first_name, c.last_name,
               l.total_amount - (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id=l.id) AS balance
        FROM loans l JOIN clients c ON c.id=l.client_id ORDER BY l.id DESC LIMIT 8
    """).fetchall()
    return render_template("dashboard.html", totals=totals, due=due, recent=recent)


def save_photo(file):
    if not file or not file.filename:
        return None
    try:
        image = Image.open(file.stream)
        image.verify()
        file.stream.seek(0)
        image = ImageOps.exif_transpose(Image.open(file.stream))
        image.thumbnail((1200, 1200))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, "JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("La foto debe ser una imagen válida.")
    filename = f"{uuid.uuid4().hex}.jpg"
    (UPLOAD_DIR / filename).write_bytes(output.getvalue())
    return filename


@app.get("/clientes")
@login_required
def clients():
    query = request.args.get("q", "").strip()
    rows = db().execute("""
        SELECT * FROM clients WHERE identification LIKE ? OR first_name LIKE ? OR last_name LIKE ?
        ORDER BY id DESC LIMIT 200
    """, tuple([f"%{query}%"] * 3)).fetchall()
    return render_template("clients.html", clients=rows, query=query)


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@login_required
def client_new():
    if request.method == "POST":
        identification = request.form.get("identification", "").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        if not identification or not first_name or not last_name:
            flash("Identificación, nombres y apellidos son obligatorios.", "error")
        else:
            try:
                photo = save_photo(request.files.get("photo"))
                with db():
                    cursor = db().execute("""
                        INSERT INTO clients (identification, first_name, last_name, phone, address, photo)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (identification, first_name, last_name, phone, address, photo))
                flash("Cliente registrado.", "success")
                return redirect(url_for("client_detail", client_id=cursor.lastrowid))
            except sqlite3.IntegrityError:
                if photo:
                    (UPLOAD_DIR / photo).unlink(missing_ok=True)
                flash("Ya existe un cliente con esa identificación.", "error")
            except ValueError as error:
                flash(str(error), "error")
    return render_template("client_form.html", client=None)


@app.route("/clientes/<int:client_id>/editar", methods=["GET", "POST"])
@login_required
def client_edit(client_id):
    client = db().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        abort(404)
    if request.method == "POST":
        fields = {key: request.form.get(key, "").strip() for key in ("identification", "first_name", "last_name", "phone", "address")}
        if not all(fields[key] for key in ("identification", "first_name", "last_name")):
            flash("Identificación, nombres y apellidos son obligatorios.", "error")
        else:
            try:
                photo = save_photo(request.files.get("photo")) or client["photo"]
                with db():
                    db().execute("""
                        UPDATE clients SET identification=?, first_name=?, last_name=?, phone=?, address=?, photo=? WHERE id=?
                    """, (*fields.values(), photo, client_id))
                if client["photo"] and photo != client["photo"]:
                    (UPLOAD_DIR / client["photo"]).unlink(missing_ok=True)
                flash("Cliente actualizado.", "success")
                return redirect(url_for("client_detail", client_id=client_id))
            except sqlite3.IntegrityError:
                if photo != client["photo"]:
                    (UPLOAD_DIR / photo).unlink(missing_ok=True)
                flash("Ya existe un cliente con esa identificación.", "error")
            except ValueError as error:
                flash(str(error), "error")
    return render_template("client_form.html", client=client)


@app.get("/clientes/<int:client_id>")
@login_required
def client_detail(client_id):
    client = db().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        abort(404)
    loans = db().execute("""
        SELECT l.*, l.total_amount - (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id=l.id) AS balance
        FROM loans l WHERE client_id=? ORDER BY id DESC
    """, (client_id,)).fetchall()
    return render_template("client_detail.html", client=client, loans=loans)


@app.get("/fotos/<path:filename>")
@login_required
def photo(filename):
    if not db().execute("SELECT 1 FROM clients WHERE photo=?", (filename,)).fetchone():
        abort(404)
    return send_from_directory(UPLOAD_DIR, filename)


def calculate_schedule(principal, rate_bps, months, frequency, start):
    if not 1 <= principal <= 1_000_000_000_000 or not 0 <= rate_bps <= 10000 or not 1 <= months <= 36:
        raise ValueError("Revise el monto, la tasa y el plazo.")
    if frequency not in {"weekly": 7, "fortnightly": 15, "monthly": 30}:
        raise ValueError("Frecuencia de pago inválida.")
    interest = int((Decimal(principal * rate_bps * months) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    total = principal + interest
    term_days = months * 30
    interval = {"weekly": 7, "fortnightly": 15, "monthly": 30}[frequency]
    days = list(range(interval, term_days, interval)) + [term_days]
    base, remainder = divmod(total, len(days))
    installments = [(start + timedelta(days=day), base + (1 if index < remainder else 0)) for index, day in enumerate(days)]
    return interest, total, installments


@app.route("/prestamos/nuevo", methods=["GET", "POST"])
@login_required
def loan_new():
    clients = db().execute("SELECT id, first_name, last_name, identification FROM clients ORDER BY first_name, last_name").fetchall()
    if request.method == "POST":
        try:
            client_id = int(request.form.get("client_id", ""))
            principal = int(request.form.get("principal", ""))
            months = int(request.form.get("months", ""))
            rate = Decimal(request.form.get("rate", "10"))
            if not rate.is_finite() or rate.as_tuple().exponent < -2:
                raise ValueError()
            rate_bps = int(rate * 100)
            frequency = request.form.get("frequency", "")
            start = date.fromisoformat(request.form.get("start_date", ""))
            if not db().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone():
                raise ValueError()
            interest, total, schedule = calculate_schedule(principal, rate_bps, months, frequency, start)
            if len(schedule) > total:
                raise ValueError()
        except (ValueError, InvalidOperation, OverflowError):
            flash("Revise cliente, monto, tasa, plazo y fecha.", "error")
        else:
            connection = db()
            with connection:
                cursor = connection.execute("""
                    INSERT INTO loans (client_id, principal, monthly_rate_bps, term_months, frequency, start_date, interest_amount, total_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (client_id, principal, rate_bps, months, frequency, start.isoformat(), interest, total))
                connection.executemany("INSERT INTO installments (loan_id, number, due_date, amount) VALUES (?, ?, ?, ?)",
                                       [(cursor.lastrowid, index + 1, due.isoformat(), amount) for index, (due, amount) in enumerate(schedule)])
            flash("Préstamo creado.", "success")
            return redirect(url_for("loan_detail", loan_id=cursor.lastrowid))
    return render_template("loan_form.html", clients=clients, selected_client=request.args.get("client_id", ""))


@app.get("/prestamos/<int:loan_id>")
@login_required
def loan_detail(loan_id):
    loan = db().execute("""
        SELECT l.*, c.first_name, c.last_name, c.identification,
               l.total_amount - (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id=l.id) AS balance
        FROM loans l JOIN clients c ON c.id=l.client_id WHERE l.id=?
    """, (loan_id,)).fetchone()
    if not loan:
        abort(404)
    installments = db().execute("SELECT * FROM installments WHERE loan_id=? ORDER BY number", (loan_id,)).fetchall()
    payments = db().execute("SELECT * FROM payments WHERE loan_id=? ORDER BY paid_on DESC, id DESC", (loan_id,)).fetchall()
    return render_template("loan_detail.html", loan=loan, installments=installments, payments=payments)


@app.post("/prestamos/<int:loan_id>/pagos")
@login_required
def payment_new(loan_id):
    try:
        amount = int(request.form.get("amount", ""))
        paid_on = date.fromisoformat(request.form.get("paid_on", ""))
        note = request.form.get("note", "").strip()[:500]
        if amount <= 0 or paid_on > date.today():
            raise ValueError()
    except ValueError:
        flash("Ingrese un monto positivo y una fecha válida que no sea futura.", "error")
        return redirect(url_for("loan_detail", loan_id=loan_id))
    connection = db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        loan = connection.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            abort(404)
        balance = loan["total_amount"] - connection.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE loan_id=?", (loan_id,)).fetchone()[0]
        if amount > balance or paid_on < date.fromisoformat(loan["start_date"]):
            connection.rollback()
            flash("El pago supera el saldo o tiene fecha anterior al préstamo.", "error")
            return redirect(url_for("loan_detail", loan_id=loan_id))
        cursor = connection.execute("INSERT INTO payments (loan_id, amount, paid_on, note) VALUES (?, ?, ?, ?)",
                                    (loan_id, amount, paid_on.isoformat(), note))
        remaining = amount
        for installment in connection.execute("SELECT * FROM installments WHERE loan_id=? AND paid_amount < amount ORDER BY number", (loan_id,)):
            applied = min(remaining, installment["amount"] - installment["paid_amount"])
            connection.execute("UPDATE installments SET paid_amount=paid_amount+? WHERE id=?", (applied, installment["id"]))
            connection.execute("INSERT INTO payment_allocations (payment_id, installment_id, amount) VALUES (?, ?, ?)",
                               (cursor.lastrowid, installment["id"], applied))
            remaining -= applied
            if remaining == 0:
                break
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    flash("Pago registrado.", "success")
    return redirect(url_for("loan_detail", loan_id=loan_id))


@app.get("/health")
def health():
    db().execute("SELECT 1")
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
