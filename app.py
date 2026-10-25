import base64
import binascii
import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_file, send_from_directory,
    session, url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from contracts import build_contract_pdf, long_date, words


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
CONTRACT_DIR = DATA_DIR / "contracts"
Image.MAX_IMAGE_PIXELS = 20_000_000
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()

if not SECRET_KEY or not ADMIN_PASSWORD or not ADMIN_USERNAME:
    raise RuntimeError("Configure FLASK_SECRET_KEY, ADMIN_USERNAME y ADMIN_PASSWORD.")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CONTRACT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
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
    client_columns = {row[1] for row in connection.execute("PRAGMA table_info(clients)")}
    for name in ("id_issued_in", "city"):
        if name not in client_columns:
            connection.execute(f"ALTER TABLE clients ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    connection.execute("INSERT OR IGNORE INTO contract_settings (id) VALUES (1)")
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
app.jinja_env.filters["words"] = words
app.jinja_env.filters["long_date"] = long_date


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
    return_to_loan = request.args.get("return_to") == "loan"
    if request.method == "POST":
        identification = request.form.get("identification", "").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        id_issued_in = request.form.get("id_issued_in", "").strip()
        city = request.form.get("city", "").strip()
        if not identification or not first_name or not last_name:
            flash("Identificación, nombres y apellidos son obligatorios.", "error")
        else:
            try:
                photo = save_photo(request.files.get("photo"))
                with db():
                    cursor = db().execute("""
                        INSERT INTO clients (identification, first_name, last_name, phone, address, id_issued_in, city, photo)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (identification, first_name, last_name, phone, address, id_issued_in, city, photo))
                flash("Cliente registrado.", "success")
                if return_to_loan:
                    return redirect(url_for("loan_new", client_id=cursor.lastrowid))
                return redirect(url_for("client_detail", client_id=cursor.lastrowid))
            except sqlite3.IntegrityError:
                if photo:
                    (UPLOAD_DIR / photo).unlink(missing_ok=True)
                flash("Ya existe un cliente con esa identificación.", "error")
            except ValueError as error:
                flash(str(error), "error")
    return render_template("client_form.html", client=None, return_to_loan=return_to_loan)


@app.route("/clientes/<int:client_id>/editar", methods=["GET", "POST"])
@login_required
def client_edit(client_id):
    client = db().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        abort(404)
    contract_loan_id = request.args.get("loan_id", type=int)
    if contract_loan_id and not db().execute("SELECT 1 FROM loans WHERE id=? AND client_id=?", (contract_loan_id, client_id)).fetchone():
        contract_loan_id = None
    if request.method == "POST":
        fields = {key: request.form.get(key, "").strip() for key in ("identification", "first_name", "last_name", "phone", "address", "id_issued_in", "city")}
        if not all(fields[key] for key in ("identification", "first_name", "last_name")):
            flash("Identificación, nombres y apellidos son obligatorios.", "error")
        else:
            try:
                photo = save_photo(request.files.get("photo")) or client["photo"]
                with db():
                    db().execute("""
                        UPDATE clients SET identification=?, first_name=?, last_name=?, phone=?, address=?, id_issued_in=?, city=?, photo=? WHERE id=?
                    """, (*fields.values(), photo, client_id))
                if client["photo"] and photo != client["photo"]:
                    (UPLOAD_DIR / client["photo"]).unlink(missing_ok=True)
                flash("Cliente actualizado.", "success")
                if contract_loan_id:
                    return redirect(url_for("contract_page", loan_id=contract_loan_id))
                return redirect(url_for("client_detail", client_id=client_id))
            except sqlite3.IntegrityError:
                if photo != client["photo"]:
                    (UPLOAD_DIR / photo).unlink(missing_ok=True)
                flash("Ya existe un cliente con esa identificación.", "error")
            except ValueError as error:
                flash(str(error), "error")
    return render_template("client_form.html", client=client, contract_loan_id=contract_loan_id)


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


SETTING_FIELDS = {
    "lender_name": ("Nombre del prestamista", 150),
    "lender_identification": ("Identificación del prestamista", 50),
    "lender_id_issued_in": ("Ciudad de expedición del prestamista", 100),
    "lender_city": ("Ciudad del prestamista", 100),
    "lender_phone": ("Teléfono del prestamista", 40),
    "payment_instructions": ("Lugar y forma de pago", 300),
}


@app.route("/configuracion/contrato", methods=["GET", "POST"])
@login_required
def contract_settings():
    loan_id = request.args.get("loan_id", type=int)
    if request.method == "POST":
        values = {name: request.form.get(name, "").strip() for name in SETTING_FIELDS}
        errors = [label for name, (label, limit) in SETTING_FIELDS.items()
                  if not values[name] or len(values[name]) > limit]
        if errors:
            flash("Complete los datos del contrato y respete la longitud máxima de cada campo.", "error")
        else:
            with db():
                db().execute("""
                    UPDATE contract_settings SET lender_name=?, lender_identification=?,
                    lender_id_issued_in=?, lender_city=?, lender_phone=?, payment_instructions=?
                    WHERE id=1
                """, tuple(values.values()))
            flash("Datos del contrato guardados.", "success")
            if loan_id and db().execute("SELECT 1 FROM loans WHERE id=?", (loan_id,)).fetchone():
                return redirect(url_for("contract_page", loan_id=loan_id))
            return redirect(url_for("contract_settings"))
    settings = db().execute("SELECT * FROM contract_settings WHERE id=1").fetchone()
    reset_configured = db().execute("SELECT 1 FROM reset_security WHERE id=1").fetchone() is not None
    return render_template("contract_settings.html", settings=settings, loan_id=loan_id,
                           reset_configured=reset_configured)


@app.route("/configuracion/borrar-datos/clave", methods=["GET", "POST"])
@login_required
def reset_password():
    configured = db().execute("SELECT 1 FROM reset_security WHERE id=1").fetchone() is not None
    if request.method == "POST":
        user = db().execute("SELECT password_hash FROM users WHERE id=?", (session["user_id"],)).fetchone()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirmation = request.form.get("confirm_password", "")
        if not user or not check_password_hash(user["password_hash"], current_password):
            flash("La contraseña de acceso es incorrecta.", "error")
        elif not 12 <= len(new_password) <= 128:
            flash("La clave de borrado debe tener entre 12 y 128 caracteres.", "error")
        elif new_password != confirmation:
            flash("Las claves de borrado no coinciden.", "error")
        elif check_password_hash(user["password_hash"], new_password):
            flash("Use una clave distinta de la contraseña de acceso.", "error")
        else:
            with db():
                db().execute("""
                    INSERT INTO reset_security (id, password_hash, failed_attempts, locked_until)
                    VALUES (1, ?, 0, NULL)
                    ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,
                    failed_attempts=0, locked_until=NULL
                """, (generate_password_hash(new_password),))
            flash("Clave de borrado guardada.", "success")
            return redirect(url_for("reset_data"))
    return render_template("reset_password.html", configured=configured)


def reset_counts(connection):
    return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("clients", "loans", "installments", "payments", "payment_allocations", "contracts")}


@app.route("/configuracion/borrar-datos", methods=["GET", "POST"])
@login_required
def reset_data():
    connection = db()
    security = connection.execute("SELECT * FROM reset_security WHERE id=1").fetchone()
    if not security:
        return redirect(url_for("reset_password"))
    counts = reset_counts(connection)
    if request.method == "POST":
        if request.form.get("confirmation", "").strip() != "BORRAR TODO":
            flash("Escriba BORRAR TODO para confirmar.", "error")
        elif security["locked_until"] and datetime.now(timezone.utc) < datetime.fromisoformat(security["locked_until"]):
            flash("Demasiados intentos. Espere 15 minutos antes de volver a intentar.", "error")
        elif not check_password_hash(security["password_hash"], request.form.get("reset_password", "")):
            attempts = security["failed_attempts"] + 1
            locked_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat() if attempts >= 5 else None
            with connection:
                connection.execute("UPDATE reset_security SET failed_attempts=?, locked_until=? WHERE id=1",
                                   (attempts, locked_until))
            flash("Clave de borrado incorrecta." if not locked_until else
                  "Demasiados intentos. Espere 15 minutos antes de volver a intentar.", "error")
        else:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                # Capture files inside the write transaction so they match the rows being removed.
                photos = [row[0] for row in connection.execute("SELECT photo FROM clients WHERE photo IS NOT NULL")]
                contracts = [row[0] for row in connection.execute("SELECT filename FROM contracts")]
                for table in ("contracts", "payment_allocations", "payments", "installments", "loans", "clients"):
                    connection.execute(f"DELETE FROM {table}")
                connection.execute("UPDATE reset_security SET failed_attempts=0, locked_until=NULL WHERE id=1")
            failed_files = 0
            for directory, names in ((UPLOAD_DIR, photos), (CONTRACT_DIR, contracts)):
                for filename in names:
                    if Path(filename).name != filename:
                        failed_files += 1
                        continue
                    try:
                        (directory / filename).unlink(missing_ok=True)
                    except OSError:
                        failed_files += 1
            flash("Todos los registros, fotos y contratos se borraron." if not failed_files else
                  f"Registros borrados, pero {failed_files} archivo(s) no se pudieron eliminar del volumen.",
                  "success" if not failed_files else "error")
            return redirect(url_for("reset_data"))
    return render_template("reset_data.html", counts=counts)


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
            return redirect(url_for("contract_page", loan_id=cursor.lastrowid))
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
    contract = db().execute("SELECT signed_at FROM contracts WHERE loan_id=?", (loan_id,)).fetchone()
    return render_template("loan_detail.html", loan=loan, installments=installments, payments=payments, contract=contract)


def contract_details(loan_id):
    loan = db().execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not loan:
        abort(404)
    client = db().execute("SELECT * FROM clients WHERE id=?", (loan["client_id"],)).fetchone()
    settings = db().execute("SELECT * FROM contract_settings WHERE id=1").fetchone()
    installments = db().execute("SELECT * FROM installments WHERE loan_id=? ORDER BY number", (loan_id,)).fetchall()
    contract = db().execute("SELECT * FROM contracts WHERE loan_id=?", (loan_id,)).fetchone()
    return loan, client, settings, installments, contract


def missing_contract_data(client, settings):
    missing = []
    for name, (label, _limit) in SETTING_FIELDS.items():
        if not settings[name]:
            missing.append(label)
    for name, label in (("id_issued_in", "Ciudad de expedición del cliente"),
                        ("city", "Ciudad de domicilio del cliente"),
                        ("phone", "Teléfono del cliente")):
        if not client[name]:
            missing.append(label)
    return missing


@app.get("/prestamos/<int:loan_id>/contrato")
@login_required
def contract_page(loan_id):
    loan, client, settings, installments, contract = contract_details(loan_id)
    monthly_interest = int((Decimal(loan["principal"] * loan["monthly_rate_bps"]) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return render_template(
        "contract.html", loan=loan, client=client, settings=settings,
        installments=installments, contract=contract, monthly_interest=monthly_interest,
        missing=missing_contract_data(client, settings),
    )


def parse_signature(value):
    prefix = "data:image/png;base64,"
    if not value.startswith(prefix) or len(value) > 700_000:
        raise ValueError("La firma no tiene un formato válido.")
    try:
        raw = base64.b64decode(value[len(prefix):], validate=True)
        if len(raw) > 500_000:
            raise ValueError()
        image = Image.open(BytesIO(raw))
        if image.format != "PNG" or not (200 <= image.width <= 1200 and 80 <= image.height <= 500):
            raise ValueError()
        image.load()
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        image = white.convert("RGB")
        if sum(pixel < 200 for pixel in image.convert("L").getdata()) < 50:
            raise ValueError()
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except (binascii.Error, OSError, ValueError, UnidentifiedImageError) as error:
        raise ValueError("Dibuje ambas firmas antes de guardar el contrato.") from error


@app.post("/prestamos/<int:loan_id>/contrato/firmar")
@login_required
def contract_sign(loan_id):
    loan, client, settings, installments, contract = contract_details(loan_id)
    if contract:
        flash("Este contrato ya fue firmado y no puede reemplazarse.", "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    if missing_contract_data(client, settings):
        flash("Complete los datos del contrato antes de firmar.", "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    if not request.is_secure and not app.config["TESTING"]:
        flash("Abra PrestaYa mediante HTTPS para firmar el contrato.", "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    if request.form.get("accepted") != "yes":
        flash("Confirme la lectura y aceptación del contrato.", "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    try:
        debtor_signature = parse_signature(request.form.get("debtor_signature", ""))
        lender_signature = parse_signature(request.form.get("lender_signature", ""))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    signed_at = datetime.now(timezone(timedelta(hours=-5))).isoformat(timespec="seconds")
    pdf = build_contract_pdf(loan, client, settings, installments, debtor_signature, lender_signature, signed_at)
    filename = f"{uuid.uuid4().hex}.pdf"
    path = CONTRACT_DIR / filename
    path.write_bytes(pdf)
    try:
        with db():
            db().execute("""
                INSERT INTO contracts (loan_id, filename, signed_at, debtor_signature_sha256,
                                       lender_signature_sha256, document_sha256)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                loan_id, filename, signed_at,
                hashlib.sha256(debtor_signature).hexdigest(),
                hashlib.sha256(lender_signature).hexdigest(),
                hashlib.sha256(pdf).hexdigest(),
            ))
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        flash("Este contrato ya fue firmado en otra solicitud.", "error")
        return redirect(url_for("contract_page", loan_id=loan_id))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    flash("Contrato firmado y guardado en PDF.", "success")
    return redirect(url_for("contract_page", loan_id=loan_id))


@app.get("/prestamos/<int:loan_id>/contrato.pdf")
@login_required
def contract_download(loan_id):
    return contract_pdf_response(loan_id, as_attachment=True)


@app.get("/prestamos/<int:loan_id>/contrato/visualizar")
@login_required
def contract_preview(loan_id):
    _loan, _client, _settings, _installments, contract = contract_details(loan_id)
    if not contract:
        abort(404)
    return render_template("contract_preview.html", loan_id=loan_id)


@app.get("/prestamos/<int:loan_id>/contrato/ver.pdf")
@login_required
def contract_inline_pdf(loan_id):
    return contract_pdf_response(loan_id, as_attachment=False)


def contract_pdf_response(loan_id, as_attachment):
    _loan, _client, _settings, _installments, contract = contract_details(loan_id)
    if not contract:
        abort(404)
    try:
        pdf = (CONTRACT_DIR / contract["filename"]).read_bytes()
    except OSError:
        abort(404)
    if hashlib.sha256(pdf).hexdigest() != contract["document_sha256"]:
        abort(409, "El archivo del contrato no coincide con el documento firmado.")
    response = send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=as_attachment,
                         download_name=f"prestaya-contrato-{loan_id}.pdf")
    response.headers["Cache-Control"] = "private, no-store"
    return response


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
