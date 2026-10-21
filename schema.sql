CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    identification TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    id_issued_in TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    photo TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    principal INTEGER NOT NULL CHECK (principal > 0),
    monthly_rate_bps INTEGER NOT NULL CHECK (monthly_rate_bps BETWEEN 0 AND 10000),
    term_months INTEGER NOT NULL CHECK (term_months BETWEEN 1 AND 36),
    frequency TEXT NOT NULL CHECK (frequency IN ('weekly', 'fortnightly', 'monthly')),
    start_date TEXT NOT NULL,
    interest_amount INTEGER NOT NULL CHECK (interest_amount >= 0),
    total_amount INTEGER NOT NULL CHECK (total_amount >= principal),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installments (
    id INTEGER PRIMARY KEY,
    loan_id INTEGER NOT NULL REFERENCES loans(id),
    number INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    paid_amount INTEGER NOT NULL DEFAULT 0 CHECK (paid_amount >= 0 AND paid_amount <= amount),
    UNIQUE (loan_id, number)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    loan_id INTEGER NOT NULL REFERENCES loans(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    paid_on TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id INTEGER PRIMARY KEY,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    installment_id INTEGER NOT NULL REFERENCES installments(id),
    amount INTEGER NOT NULL CHECK (amount > 0)
);

CREATE TABLE IF NOT EXISTS contract_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    lender_name TEXT NOT NULL DEFAULT '',
    lender_identification TEXT NOT NULL DEFAULT '',
    lender_id_issued_in TEXT NOT NULL DEFAULT '',
    lender_city TEXT NOT NULL DEFAULT '',
    lender_phone TEXT NOT NULL DEFAULT '',
    payment_instructions TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contracts (
    loan_id INTEGER PRIMARY KEY REFERENCES loans(id),
    filename TEXT NOT NULL UNIQUE,
    signed_at TEXT NOT NULL,
    debtor_signature_sha256 TEXT NOT NULL,
    lender_signature_sha256 TEXT NOT NULL,
    document_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reset_security (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_installments_due ON installments(due_date, loan_id);
CREATE INDEX IF NOT EXISTS idx_payments_loan ON payments(loan_id);
CREATE INDEX IF NOT EXISTS idx_loans_client ON loans(client_id);
