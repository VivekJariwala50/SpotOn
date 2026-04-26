from zoneinfo import ZoneInfo
from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.errors
import psycopg2.extras

# Allow binding Python uuid.UUID to PostgreSQL uuid columns (avoids "can't adapt type 'UUID'").
psycopg2.extras.register_uuid()

from collections import defaultdict
from functools import wraps
import os
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

app = Flask(__name__, template_folder="pages")
app.secret_key = os.getenv("SECRET_KEY") or "dev-secret"
app.permanent_session_lifetime = timedelta(minutes=30)

AVAILABLE_SLOT_STATUS = "AVAILABLE"
OUT_OF_SERVICE_SLOT_STATUS = "OUT_OF_SERVICE"
ALLOWED_SLOT_STATUSES = {AVAILABLE_SLOT_STATUS, OUT_OF_SERVICE_SLOT_STATUS}
ALLOWED_VEHICLE_TYPES = {"compact", "sedan", "suv", "truck"}
FIRST_BOOKING_PROMO_CODE = "SPOTON10"
FIRST_BOOKING_PROMO_DISCOUNT_PERCENT = 10
PACE_PROMO_CODE = "CS691PACE"
PACE_PROMO_DISCOUNT_PERCENT = 25
SUPPORTED_PROMOS = {
    FIRST_BOOKING_PROMO_CODE: FIRST_BOOKING_PROMO_DISCOUNT_PERCENT,
    PACE_PROMO_CODE: PACE_PROMO_DISCOUNT_PERCENT,
}

CONFIRMED_RESERVATION_STATUS = "CONFIRMED"
PENDING_APPROVAL_STATUS = "PENDING_APPROVAL"
REJECTED_RESERVATION_STATUS = "REJECTED"
# Slot-time overlaps treat pending requests like confirmed holds.
BOOKING_OVERLAP_STATUS_SQL = "('CONFIRMED', 'PENDING_APPROVAL')"
SUPPORT_TICKET_STATUS_OPEN = "OPEN"
SUPPORT_TICKET_STATUS_IN_PROGRESS = "IN_PROGRESS"
SUPPORT_TICKET_STATUS_RESOLVED = "RESOLVED"
SUPPORT_TICKET_STATUS_CLOSED = "CLOSED"
SUPPORT_TICKET_STATUSES = {
    SUPPORT_TICKET_STATUS_OPEN,
    SUPPORT_TICKET_STATUS_IN_PROGRESS,
    SUPPORT_TICKET_STATUS_RESOLVED,
    SUPPORT_TICKET_STATUS_CLOSED,
}


def build_booking_alias(booking_id):
    raw_value = "".join(ch for ch in str(booking_id or "").upper() if ch.isalnum())
    if len(raw_value) < 10:
        return ""
    return f"SP-{raw_value[:6]}{raw_value[-4:]}"


def validate_demo_payment_fields(cardholder_name, card_number, expiry, cvv):
    """
    Validate the demo payment form. Returns (None, cleaned_16_digit_card) if OK,
    or (error_message, None).

    Checks run in an order that surfaces format issues and the specific wrong field
    before the generic “use the demo card” hint.
    """
    if not (cardholder_name or "").strip():
        return "Enter the cardholder name as it appears on the card.", None

    cleaned_card = "".join(ch for ch in (card_number or "") if ch.isdigit())
    if not cleaned_card:
        return "Enter the full card number (16 digits).", None
    if len(cleaned_card) != 16:
        return (
            "The card number must be exactly 16 digits. "
            "For this demo, enter 8111 1111 1111 1111.",
            None,
        )

    exp = (expiry or "").strip()
    if len(exp) != 5 or exp[2] != "/":
        return (
            "Enter the expiry as MM/YY with a slash between month and year "
            "(for example, 12/28).",
            None,
        )
    try:
        month_val = int(exp[0:2])
        if month_val < 1 or month_val > 12:
            return "Expiry month must be between 01 and 12.", None
        int(exp[3:5])
    except ValueError:
        return (
            "Enter a valid expiry in MM/YY form (for example, 12/28).",
            None,
        )

    cleaned_cvv = "".join(ch for ch in (cvv or "") if ch.isdigit())
    if not cleaned_cvv:
        return "Enter the 3-digit security code (CVV) from the back of the card.", None
    if len(cleaned_cvv) != 3:
        return (
            "CVV must be exactly 3 digits. For this demo checkout, use 007.",
            None,
        )
    if cleaned_cvv != "007":
        return (
            "That security code is incorrect. For this demo, the CVV must be 007.",
            None,
        )

    if cleaned_card != "8111111111111111":
        return (
            "That card number is not valid for this demo checkout. "
            "Use the test number 8111 1111 1111 1111.",
            None,
        )

    return None, cleaned_card


def safe_internal_next(candidate):
    """Allow only same-origin relative paths (open-redirect safe)."""
    if candidate is None:
        return None
    path = (candidate or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return None
    if "://" in path or path.lower().startswith("/\\"):
        return None
    if "\n" in path or "\r" in path or ".." in path:
        return None
    return path


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "error")
                return redirect(url_for("login"))

            if role is not None:
                allowed_roles = role if isinstance(role, (list, tuple, set)) else [role]
                if session.get("user_role") not in allowed_roles:
                    flash("Access denied.", "error")
                    return redirect(url_for("home"))

            session.permanent = True
            return f(*args, **kwargs)

        return wrapper

    return decorator


def get_db_connection():
    """
    Reads DATABASE_URL (e.g. from Render). For Supabase + Render, use the
    **Session pooler** URI (host …pooler.supabase.com, port 5432): the direct
    db.*.supabase.co host is often IPv6-only and fails on IPv4-only hosts.
    """
    database_url = (os.getenv("DATABASE_URL") or "").strip()

    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        # Supabase (direct and pooler) expects TLS if sslmode is omitted.
        if "sslmode=" not in database_url and (
            "supabase.co" in database_url or "pooler.supabase.com" in database_url
        ):
            database_url += ("&" if "?" in database_url else "?") + "sslmode=require"
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        host="localhost",
        database="smart_parking",
        user="vyomraj",
        password="NewStrongPassword123"
    )

# --- Helper to record transactions ---
def record_transaction(cur, reservation_id, user_id, transaction_type, amount, status="SUCCESS"):
    cur.execute(
        """
        INSERT INTO transactions (reservation_id, user_id, transaction_type, amount, status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (reservation_id, user_id, transaction_type, amount, status)
    )


def record_refund_simulated(cur, reservation_id, user_id, refund_amount):
    """
    Simulated refund workflow (no payment gateway): insert REFUND as PENDING,
    then mark SUCCESS to represent pending → completed in one request.
    Falls back to a single SUCCESS row if PENDING is not allowed by the DB.
    """
    if refund_amount is None or float(refund_amount) <= 0:
        return
    cur.execute("SAVEPOINT sp_refund_sim")
    try:
        cur.execute(
            """
            INSERT INTO transactions (reservation_id, user_id, transaction_type, amount, status)
            VALUES (%s, %s, 'REFUND', %s, 'PENDING')
            RETURNING id
            """,
            (reservation_id, user_id, refund_amount),
        )
        refund_row = cur.fetchone()
        refund_id = refund_row["id"] if isinstance(refund_row, dict) else refund_row[0]
        cur.execute(
            """
            UPDATE transactions
            SET status = 'SUCCESS'
            WHERE id = %s
            """,
            (refund_id,),
        )
        cur.execute("RELEASE SAVEPOINT sp_refund_sim")
    except psycopg2.Error:
        cur.execute("ROLLBACK TO SAVEPOINT sp_refund_sim")
        record_transaction(cur, reservation_id, user_id, "REFUND", refund_amount, "SUCCESS")


def apply_promo_discount(subtotal, promo_code):
    subtotal_value = round(float(subtotal or 0), 2)
    normalized_code = (promo_code or "").strip().upper()
    if not normalized_code:
        return {
            "is_applied": False,
            "normalized_code": "",
            "applied_code": "",
            "discount_percent": 0,
            "discount_amount": 0.0,
            "final_total": subtotal_value,
        }
    discount_percent = SUPPORTED_PROMOS.get(normalized_code)
    if discount_percent is None:
        return {
            "is_applied": False,
            "normalized_code": normalized_code,
            "applied_code": "",
            "discount_percent": 0,
            "discount_amount": 0.0,
            "final_total": subtotal_value,
        }
    discount_amount = round(subtotal_value * (discount_percent / 100), 2)
    final_total = round(max(subtotal_value - discount_amount, 0), 2)
    return {
        "is_applied": True,
        "normalized_code": normalized_code,
        "applied_code": normalized_code,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "final_total": final_total,
    }


def ensure_db_integrity_constraints():
    """
    Idempotent DB hardening:
    - btree_gist for exclusion constraints
    - unique (lot_id, normalized label) on parking_slots
    - no overlapping CONFIRMED reservations on the same slot (time ranges)
    """
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS parking_slots_lot_id_label_normalized_uidx
            ON parking_slots (lot_id, upper(btrim(label::text)))
            """
        )
        cur.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'reservations_confirmed_no_overlap'
            """
        )
        if not cur.fetchone():
            cur.execute(
                """
                ALTER TABLE reservations
                ADD CONSTRAINT reservations_confirmed_no_overlap
                EXCLUDE USING gist (
                    slot_id WITH =,
                    tstzrange(start_time, end_time, '[)') WITH &&
                )
                WHERE (status = 'CONFIRMED')
                """
            )
    except psycopg2.Error as exc:
        print("ensure_db_integrity_constraints:", exc)
    finally:
        cur.close()
        conn.close()


def ensure_reservation_approval_schema():
    """
    Idempotent: widen reservation status check, add optional promo_code column,
    and replace confirmed-only exclusion with overlap for pending + confirmed.
    """
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            """
            ALTER TABLE reservations
            ADD COLUMN IF NOT EXISTS promo_code VARCHAR(64)
            """
        )
    except psycopg2.Error as exc:
        print("ensure_reservation_approval_schema promo column:", exc)

    try:
        cur.execute("ALTER TABLE reservations DROP CONSTRAINT IF EXISTS reservations_status_check")
        cur.execute(
            """
            ALTER TABLE reservations
            ADD CONSTRAINT reservations_status_check
            CHECK (
                status = ANY (
                    ARRAY[
                        'CONFIRMED',
                        'CANCELLED',
                        'EXPIRED',
                        'PENDING_APPROVAL',
                        'REJECTED'
                    ]::text[]
                )
            )
            """
        )
    except psycopg2.Error as exc:
        print("ensure_reservation_approval_schema status check:", exc)

    try:
        cur.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'reservations_slot_booking_overlap'
            """
        )
        if cur.fetchone():
            return

        cur.execute(
            "ALTER TABLE reservations DROP CONSTRAINT IF EXISTS reservations_confirmed_no_overlap"
        )
        cur.execute(
            "ALTER TABLE reservations DROP CONSTRAINT IF EXISTS reservations_no_overlap_confirmed"
        )
        cur.execute(
            """
            ALTER TABLE reservations
            ADD CONSTRAINT reservations_slot_booking_overlap
            EXCLUDE USING gist (
                slot_id WITH =,
                tstzrange(start_time, end_time, '[)') WITH &&
            )
            WHERE (status IN ('CONFIRMED', 'PENDING_APPROVAL'))
            """
        )
    except psycopg2.Error as exc:
        print("ensure_reservation_approval_schema overlap:", exc)
    finally:
        cur.close()
        conn.close()


def ensure_bulk_reservation_schema():
    """Optional bulk_group_id linking multiple reservation rows from one checkout."""
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            """
            ALTER TABLE reservations
            ADD COLUMN IF NOT EXISTS bulk_group_id UUID
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reservations_bulk_group_id
            ON reservations (bulk_group_id)
            WHERE bulk_group_id IS NOT NULL
            """
        )
    except psycopg2.Error as exc:
        print("ensure_bulk_reservation_schema:", exc)
    finally:
        cur.close()
        conn.close()


def ensure_support_tickets_schema():
    """Idempotent support ticket storage for contact + management workflows."""
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id UUID PRIMARY KEY,
                ticket_code VARCHAR(32) NOT NULL UNIQUE,
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                reservation_id UUID REFERENCES reservations(id) ON DELETE SET NULL,
                full_name VARCHAR(120) NOT NULL,
                email VARCHAR(255) NOT NULL,
                phone VARCHAR(40),
                booking_reference VARCHAR(64),
                issue TEXT NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'OPEN',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE support_tickets
            DROP CONSTRAINT IF EXISTS support_tickets_status_check
            """
        )
        cur.execute(
            """
            ALTER TABLE support_tickets
            ADD CONSTRAINT support_tickets_status_check
            CHECK (status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'))
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_support_tickets_status_created
            ON support_tickets (status, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_support_tickets_email_created
            ON support_tickets (email, created_at DESC)
            """
        )
    except psycopg2.Error as exc:
        print("ensure_support_tickets_schema:", exc)
    finally:
        cur.close()
        conn.close()


def _resolve_reservation_id_from_booking_reference(cur, booking_reference):
    raw = (booking_reference or "").strip()
    if not raw:
        return None

    normalized_lookup = "".join(ch for ch in raw.upper() if ch.isalnum())
    alias_body = normalized_lookup[2:] if normalized_lookup.startswith("SP") else normalized_lookup
    lookup_alias = f"SP-{alias_body}" if alias_body else ""

    cur.execute(
        """
        SELECT r.id
        FROM reservations r
        WHERE CAST(r.id AS TEXT) = %s
           OR UPPER(
                'SP-'
                || SUBSTRING(REPLACE(CAST(r.id AS TEXT), '-', '') FROM 1 FOR 6)
                || SUBSTRING(REPLACE(CAST(r.id AS TEXT), '-', '') FROM 29 FOR 4)
           ) = %s
        LIMIT 1
        """,
        (raw, lookup_alias),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def enrich_reservation_rows_bulk_metadata(rows):
    """Adds bulk_peer_count and bulk_peer_slots_display for grouped UI."""
    if not rows:
        return
    by_group = defaultdict(list)
    for row in rows:
        gid = row.get("bulk_group_id")
        if gid:
            by_group[gid].append(row)
    for row in rows:
        gid = row.get("bulk_group_id")
        if gid and gid in by_group:
            members = by_group[gid]
            row["bulk_peer_count"] = len(members)
            row["bulk_peer_slots_display"] = ", ".join(
                sorted(
                    (str(m.get("slot_label") or "").strip() or "—") for m in members
                )
            )
        else:
            row["bulk_peer_count"] = 1
            row["bulk_peer_slots_display"] = ""


@app.before_request
def _ensure_db_integrity_once():
    if request.endpoint in ("static", "health"):
        return
    if app.config.get("_db_integrity_constraints_ready"):
        return
    ensure_db_integrity_constraints()
    ensure_reservation_approval_schema()
    ensure_bulk_reservation_schema()
    ensure_support_tickets_schema()
    app.config["_db_integrity_constraints_ready"] = True


def ensure_pricing_overrides_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pricing_overrides (
            lot_id UUID NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
            slot_type VARCHAR(50) NOT NULL DEFAULT 'any',
            vehicle_type VARCHAR(50) NOT NULL DEFAULT 'any',
            price_per_hour NUMERIC(10,2) NOT NULL CHECK (price_per_hour >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (lot_id, slot_type, vehicle_type)
        )
        """
    )


def normalize_override_key(value):
    normalized = (value or "").strip().lower()
    return normalized if normalized else "any"


def parse_price_input(raw_price):
    try:
        parsed = Decimal((raw_price or "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if parsed < 0:
        return None
    return parsed.quantize(Decimal("0.01"))


def load_pricing_overrides_for_lots(cur, lot_ids):
    if not lot_ids:
        return {}

    ensure_pricing_overrides_table(cur)
    cur.execute(
        """
        SELECT lot_id, slot_type, vehicle_type, price_per_hour
        FROM pricing_overrides
        WHERE lot_id = ANY(%s::uuid[])
        """,
        (lot_ids,)
    )

    by_lot = {}
    for row in cur.fetchall():
        lot_overrides = by_lot.setdefault(row["lot_id"], {})
        lot_overrides[(row["slot_type"], row["vehicle_type"])] = float(row["price_per_hour"])
    return by_lot


def load_pricing_override_rows_with_meta(cur, lot_ids):
    """Rows for operator inventory: price overrides with last-updated timestamps."""
    if not lot_ids:
        return {}

    ensure_pricing_overrides_table(cur)
    cur.execute(
        """
        SELECT lot_id, slot_type, vehicle_type, price_per_hour, updated_at
        FROM pricing_overrides
        WHERE lot_id = ANY(%s::uuid[])
        ORDER BY lot_id, updated_at DESC NULLS LAST, slot_type, vehicle_type
        """,
        (list(lot_ids),),
    )
    by_lot = {}
    for row in cur.fetchall():
        ts = row.get("updated_at")
        display = "—"
        if ts is not None:
            try:
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                display = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except (TypeError, ValueError, OSError):
                display = str(ts)[:19]

        by_lot.setdefault(row["lot_id"], []).append(
            {
                "slot_type": row["slot_type"],
                "vehicle_type": row["vehicle_type"],
                "price_per_hour": float(row["price_per_hour"]),
                "updated_at_display": display,
            }
        )
    return by_lot


def resolve_effective_price(base_price, lot_overrides, slot_type=None, vehicle_type=None):
    slot_key = normalize_override_key(slot_type)
    vehicle_key = normalize_override_key(vehicle_type)
    candidates = [
        (slot_key, vehicle_key),
        (slot_key, "any"),
        ("any", vehicle_key),
        ("any", "any"),
    ]
    for key in candidates:
        if key in lot_overrides:
            return float(lot_overrides[key])
    return float(base_price or 0)


@app.route("/")
def home():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT name, address
        FROM parking_lots
        ORDER BY name ASC
    """)
    parking_lots = cur.fetchall()

    cur.close()
    conn.close()

    homepage_garage_suggestions = []
    seen_suggestions = set()

    for lot in parking_lots:
        lot_name = (lot["name"] or "").strip()
        lot_address = (lot["address"] or "").strip()

        if lot_name and lot_name.lower() not in seen_suggestions:
            homepage_garage_suggestions.append(lot_name)
            seen_suggestions.add(lot_name.lower())

        if lot_address and lot_address.lower() not in seen_suggestions:
            homepage_garage_suggestions.append(lot_address)
            seen_suggestions.add(lot_address.lower())

    return render_template(
        "index.html",
        homepage_garage_suggestions=homepage_garage_suggestions,
        is_logged_in=bool(session.get("user_id")),
        user_role=session.get("user_role"),
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    next_path = safe_internal_next(request.args.get("next")) or ""
    if request.method == "POST":
        next_path = safe_internal_next(request.form.get("next")) or ""
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "driver").strip().lower()

        if not full_name or not email or not password:
            flash("Please fill in all required fields.", "error")
            return render_template("signup.html", next_url=next_path)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("signup.html", next_url=next_path)

        if selected_role not in {"driver", "operator"}:
            flash("Invalid role selected.", "error")
            return render_template("signup.html", next_url=next_path)

        password_hash = generate_password_hash(password)

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        try:
            cur.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
                """,
                (full_name, email, password_hash, selected_role)
            )
            conn.commit()
            flash("Account created successfully. Please log in.", "success")
            if next_path:
                return redirect(url_for("login", next=next_path))
            return redirect(url_for("login"))

        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash("An account with this email already exists.", "error")
            return render_template("signup.html", next_url=next_path)

        finally:
            cur.close()
            conn.close()

    return render_template("signup.html", next_url=next_path)


@app.route("/login", methods=["GET", "POST"])
def login():
    next_path = None
    if request.method == "POST":
        next_path = safe_internal_next(request.form.get("next"))
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "error")
            return render_template("login.html", next_url=next_path or "")

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(
            "SELECT id, email, password_hash, role, is_active FROM users WHERE email = %s",
            (email,)
        )
        user = cur.fetchone()

        cur.close()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                flash("Your account has been deactivated. Please contact support.", "error")
                return render_template("login.html", next_url=next_path or "")

            session["user_id"] = str(user["id"])
            session["user_email"] = user["email"]
            session["user_role"] = user["role"]

            flash("Login successful.", "success")

            if user["role"] == "driver" and next_path:
                return redirect(next_path)
            if user["role"] == "driver":
                return redirect(url_for("dashboard"))
            elif user["role"] == "operator":
                return redirect(url_for("operator_dashboard"))
            elif user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            else:
                session.clear()
                flash("Invalid user role.", "error")
                return redirect(url_for("login"))

        flash("Invalid email or password.", "error")
        return render_template("login.html", next_url=next_path or "")

    next_url = safe_internal_next(request.args.get("next")) or ""
    return render_template("login.html", next_url=next_url)

@app.route("/deactivate-account", methods=["POST"])
@login_required(role="driver")
def deactivate_account():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        UPDATE users
        SET is_active = FALSE
        WHERE id = %s
    """, (session.get("user_id"),))
    conn.commit()

    cur.close()
    conn.close()

    session.clear()
    flash("Your account has been deactivated.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required(role="driver")
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            r.id,
            r.bulk_group_id,
            r.start_time,
            r.end_time,
            r.status,
            pl.name AS lot_name,
            pl.address AS lot_address,
            pl.id AS lot_id,
            pl.price_per_hour AS price_per_hour,
            ps.slot_type,
            ps.supported_vehicle_type,
            ps.label AS slot_label
        FROM reservations r
        JOIN parking_slots ps ON r.slot_id = ps.id
        JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE r.user_id = %s
          AND r.status = 'CONFIRMED'
          AND r.end_time > now()
        ORDER BY r.start_time DESC
    """, (session.get("user_id"),))
    active_reservations = cur.fetchall()

    cur.execute(
        """
        SELECT
            r.id,
            r.bulk_group_id,
            r.start_time,
            r.end_time,
            r.status,
            pl.name AS lot_name,
            pl.address AS lot_address,
            pl.id AS lot_id,
            pl.price_per_hour AS price_per_hour,
            ps.slot_type,
            ps.supported_vehicle_type,
            ps.label AS slot_label
        FROM reservations r
        JOIN parking_slots ps ON r.slot_id = ps.id
        JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE r.user_id = %s
          AND r.status = %s
          AND r.end_time > now()
        ORDER BY r.start_time ASC
        """,
        (session.get("user_id"), PENDING_APPROVAL_STATUS),
    )
    pending_approvals = cur.fetchall()

    cur.execute(
        """
        SELECT
            r.id,
            r.bulk_group_id,
            r.start_time,
            r.end_time,
            r.status,
            pl.name AS lot_name,
            pl.address AS lot_address,
            pl.id AS lot_id,
            pl.price_per_hour AS price_per_hour,
            ps.slot_type,
            ps.supported_vehicle_type,
            ps.label AS slot_label
        FROM reservations r
        JOIN parking_slots ps ON r.slot_id = ps.id
        JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE r.user_id = %s
          AND NOT (
              r.status = 'CONFIRMED' AND r.end_time > now()
          )
          AND NOT (
              r.status = %s AND r.end_time > now()
          )
        ORDER BY r.start_time DESC
        """,
        (session.get("user_id"), PENDING_APPROVAL_STATUS),
    )
    reservation_history = cur.fetchall()

    enrich_reservation_rows_bulk_metadata(active_reservations)
    enrich_reservation_rows_bulk_metadata(pending_approvals)
    enrich_reservation_rows_bulk_metadata(reservation_history)

    cur.execute("""
        SELECT
            t.id,
            t.reservation_id,
            t.transaction_type,
            t.amount,
            t.status,
            t.created_at,
            pl.name AS lot_name,
            ps.label AS slot_label
        FROM transactions t
        LEFT JOIN reservations r ON t.reservation_id = r.id
        LEFT JOIN parking_slots ps ON r.slot_id = ps.id
        LEFT JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE t.user_id = %s
        ORDER BY t.created_at DESC
    """, (session.get("user_id"),))
    transaction_history = cur.fetchall()

    pricing_overrides_by_lot = load_pricing_overrides_for_lots(
        cur,
        list(
            {
                reservation["lot_id"]
                for reservation in (active_reservations + reservation_history + pending_approvals)
                if reservation.get("lot_id")
            }
        )
    )

    cur.close()
    conn.close()

    def format_dt(dt_value):
        if not dt_value:
            return "—"
        return dt_value.strftime("%b %d, %Y • %I:%M %p").replace(" 0", " ")

    def format_currency(amount):
        if amount is None:
            return "—"
        amount_value = float(amount)
        if amount_value < 0:
            return f"-${abs(amount_value):.2f}"
        return f"${amount_value:.2f}"

    def format_status(reservation):
        if reservation["status"] == "CANCELLED":
            return "Cancelled"
        if reservation["status"] == REJECTED_RESERVATION_STATUS:
            return "Declined"
        if reservation["status"] == PENDING_APPROVAL_STATUS:
            return "Awaiting approval"
        if reservation["status"] == "CONFIRMED" and reservation["end_time"] and reservation["end_time"] <= datetime.now(timezone.utc):
            return "Completed"
        if reservation["status"] == "CONFIRMED":
            return "Confirmed"
        return reservation["status"].capitalize() if reservation["status"] else "—"

    def add_edit_fields(reservation):
        if reservation["start_time"]:
            reservation["edit_start_date"] = reservation["start_time"].strftime("%Y-%m-%d")
            reservation["edit_start_time_only"] = reservation["start_time"].strftime("%H:%M")
        else:
            reservation["edit_start_date"] = ""
            reservation["edit_start_time_only"] = ""

        if reservation["end_time"]:
            reservation["edit_end_date"] = reservation["end_time"].strftime("%Y-%m-%d")
            reservation["edit_end_time_only"] = reservation["end_time"].strftime("%H:%M")
        else:
            reservation["edit_end_date"] = ""
            reservation["edit_end_time_only"] = ""

    def add_cost_fields(reservation):
        if reservation["start_time"] and reservation["end_time"] and reservation.get("price_per_hour") is not None:
            effective_price = resolve_effective_price(
                reservation["price_per_hour"],
                pricing_overrides_by_lot.get(reservation.get("lot_id"), {}),
                reservation.get("slot_type"),
                reservation.get("supported_vehicle_type")
            )
            duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
            reservation["estimated_cost"] = round(float(effective_price) * duration_hours, 2)
        else:
            reservation["estimated_cost"] = None

    for reservation in active_reservations:
        reservation["booking_alias"] = build_booking_alias(reservation.get("id"))
        reservation["formatted_start"] = format_dt(reservation["start_time"])
        reservation["formatted_end"] = format_dt(reservation["end_time"])
        reservation["formatted_status"] = format_status(reservation)
        add_edit_fields(reservation)
        add_cost_fields(reservation)

    for reservation in pending_approvals:
        reservation["booking_alias"] = build_booking_alias(reservation.get("id"))
        reservation["formatted_start"] = format_dt(reservation["start_time"])
        reservation["formatted_end"] = format_dt(reservation["end_time"])
        reservation["formatted_status"] = format_status(reservation)
        add_cost_fields(reservation)

    for reservation in reservation_history:
        reservation["booking_alias"] = build_booking_alias(reservation.get("id"))
        reservation["formatted_start"] = format_dt(reservation["start_time"])
        reservation["formatted_end"] = format_dt(reservation["end_time"])
        reservation["formatted_status"] = format_status(reservation)
        add_edit_fields(reservation)
        add_cost_fields(reservation)

    for transaction in transaction_history:
        transaction["booking_alias"] = build_booking_alias(transaction.get("reservation_id"))
        transaction["formatted_amount"] = format_currency(transaction["amount"])
        transaction["formatted_created_at"] = format_dt(transaction["created_at"])
        amount_value = float(transaction["amount"] or 0)
        tx_type = (transaction.get("transaction_type") or "").upper()
        if tx_type == "REFUND" or amount_value < 0:
            transaction["display_type"] = "REFUND"
        else:
            transaction["display_type"] = "PAYMENT"
        transaction["formatted_action"] = transaction["transaction_type"].replace("_", " ").title() if transaction["transaction_type"] else "—"

    time_options = []
    base_time = datetime.strptime("00:00", "%H:%M")
    for i in range(48):
        t = (base_time + timedelta(minutes=30 * i)).strftime("%H:%M")
        label = datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
        time_options.append({"value": t, "label": label})

    active_map_query = "Jersey City, NJ"
    active_map_label = ""
    if active_reservations:
        active_map_query = (active_reservations[0].get("lot_address") or active_reservations[0].get("lot_name") or "Jersey City, NJ").strip()
        active_map_label = f"{active_reservations[0].get('lot_name', 'Active reservation')} • Slot {active_reservations[0].get('slot_label', '—')}"

    return render_template(
        "dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        active_reservations=active_reservations,
        pending_approvals=pending_approvals,
        reservation_history=reservation_history,
        transaction_history=transaction_history,
        time_options=time_options,
        active_map_query=active_map_query,
        active_map_label=active_map_label,
    )


@app.route("/transactions")
@login_required(role="driver")
def transaction_history_page():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            t.id,
            t.reservation_id,
            t.transaction_type,
            t.amount,
            t.status,
            t.created_at,
            pl.name AS lot_name,
            ps.label AS slot_label
        FROM transactions t
        LEFT JOIN reservations r ON t.reservation_id = r.id
        LEFT JOIN parking_slots ps ON r.slot_id = ps.id
        LEFT JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE t.user_id = %s
        ORDER BY t.created_at DESC
        """,
        (session.get("user_id"),),
    )
    transaction_history = cur.fetchall()
    cur.close()
    conn.close()

    def format_dt(dt_value):
        if not dt_value:
            return "—"
        return dt_value.strftime("%b %d, %Y • %I:%M %p").replace(" 0", " ")

    def format_currency(amount):
        if amount is None:
            return "—"
        amount_value = float(amount)
        if amount_value < 0:
            return f"-${abs(amount_value):.2f}"
        return f"${amount_value:.2f}"

    for transaction in transaction_history:
        transaction["booking_alias"] = build_booking_alias(transaction.get("reservation_id"))
        transaction["formatted_amount"] = format_currency(transaction["amount"])
        transaction["formatted_created_at"] = format_dt(transaction["created_at"])
        amount_value = float(transaction["amount"] or 0)
        tx_type = (transaction.get("transaction_type") or "").upper()
        if tx_type == "REFUND" or amount_value < 0:
            transaction["display_type"] = "REFUND"
        else:
            transaction["display_type"] = "PAYMENT"
        transaction["formatted_action"] = transaction["transaction_type"].replace("_", " ").title() if transaction["transaction_type"] else "—"

    return render_template(
        "transaction_history.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        transaction_history=transaction_history,
    )


@app.route("/transactions/clear", methods=["POST"])
@login_required(role="driver")
def clear_transaction_history():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            DELETE FROM transactions
            WHERE user_id = %s
            """,
            (session.get("user_id"),)
        )
        deleted_rows = cur.rowcount
        conn.commit()
        if deleted_rows > 0:
            flash("Transaction history cleared.", "success")
        else:
            flash("No transactions to clear.", "success")
        return redirect(url_for("transaction_history_page"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in clear_transaction_history:", e)
        flash("Could not clear transaction history right now.", "error")
        return redirect(url_for("transaction_history_page"))
    finally:
        cur.close()
        conn.close()


# ---- EXTEND RESERVATION ROUTE ----
@app.route("/extend-reservation/<reservation_id>", methods=["POST"])
@login_required(role="driver")
def extend_reservation(reservation_id):
    extension_minutes_raw = request.form.get("extension_minutes", "").strip()

    try:
        extension_minutes = int(extension_minutes_raw)
    except ValueError:
        flash("Invalid extension selection.", "error")
        return redirect(url_for("dashboard"))

    if extension_minutes not in {30, 60}:
        flash("Only 30-minute or 1-hour extensions are allowed.", "error")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT
                r.id,
                r.user_id,
                r.slot_id,
                r.start_time,
                r.end_time,
                r.status,
                pl.price_per_hour,
                pl.id AS lot_id,
                ps.slot_type,
                ps.supported_vehicle_type
            FROM reservations r
            JOIN parking_slots ps ON r.slot_id = ps.id
            JOIN parking_lots pl ON ps.lot_id = pl.id
            WHERE r.id = %s
        """, (reservation_id,))
        reservation = cur.fetchone()


        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("dashboard"))

        if str(reservation["user_id"]) != session.get("user_id"):
            flash("You can only extend your own reservations.", "error")
            return redirect(url_for("dashboard"))

        if reservation["status"] != "CONFIRMED":
            flash("Only confirmed reservations can be extended.", "error")
            return redirect(url_for("dashboard"))

        if reservation["end_time"] <= datetime.now(timezone.utc):
            flash("Completed reservations cannot be extended.", "error")
            return redirect(url_for("dashboard"))

        new_end_time = reservation["end_time"] + timedelta(minutes=extension_minutes)
        lot_overrides = load_pricing_overrides_for_lots(cur, [reservation["lot_id"]])
        effective_price_per_hour = resolve_effective_price(
            reservation["price_per_hour"],
            lot_overrides.get(reservation["lot_id"], {}),
            reservation["slot_type"],
            reservation["supported_vehicle_type"]
        )
        original_duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
        new_duration_hours = (new_end_time - reservation["start_time"]).total_seconds() / 3600
        original_total_cost = round(effective_price_per_hour * original_duration_hours, 2)
        new_total_cost = round(effective_price_per_hour * new_duration_hours, 2)
        added_cost = round(new_total_cost - original_total_cost, 2)

        cur.execute("""
            SELECT 1
            FROM reservations r
            WHERE r.slot_id = %s
              AND r.id <> %s
              AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
              AND tstzrange(r.start_time, r.end_time, '[)') &&
                  tstzrange(%s, %s, '[)')
            LIMIT 1
        """, (
            reservation["slot_id"],
            reservation["id"],
            reservation["end_time"],
            new_end_time,
        ))
        overlapping_reservation = cur.fetchone()

        if overlapping_reservation:
            flash("This reservation cannot be extended because the slot is not available for the additional time.", "error")
            return redirect(url_for("dashboard"))

        cur.execute("""
            UPDATE reservations
            SET end_time = %s
            WHERE id = %s
        """, (new_end_time, reservation["id"]))
        record_transaction(cur, reservation["id"], session.get("user_id"), "EXTEND_RESERVATION", added_cost, "SUCCESS")
        conn.commit()

        hours_added = extension_minutes / 60
        flash(
            f"Reservation extended successfully by {hours_added:g} hour(s). "
            f"Additional amount of ${added_cost:.2f} will be auto-charged to the same card ending in 1111. "
            f"New estimated total: ${new_total_cost:.2f}",
            "success"
        )
        return redirect(url_for("dashboard"))

    except psycopg2.errors.ExclusionViolation:
        conn.rollback()
        flash("This reservation cannot be extended because the slot is not available for the additional time.", "error")
        return redirect(url_for("dashboard"))
    except psycopg2.Error as exc:
        conn.rollback()
        print("Database error in extend_reservation:", exc)
        flash("Could not extend the reservation right now.", "error")
        return redirect(url_for("dashboard"))
    finally:
        cur.close()
        conn.close()

@app.route("/modify-reservation/<reservation_id>", methods=["POST"])
@login_required(role="driver")
def modify_reservation(reservation_id):
    start_date = request.form.get("start_date", "").strip()
    start_time_only = request.form.get("start_time_only", "").strip()
    end_date = request.form.get("end_date", "").strip()
    end_time_only = request.form.get("end_time_only", "").strip()

    start_time_str = f"{start_date}T{start_time_only}" if start_date and start_time_only else ""
    end_time_str = f"{end_date}T{end_time_only}" if end_date and end_time_only else ""

    if not start_time_str or not end_time_str:
        flash("Please provide updated reservation start and end times.", "error")
        return redirect(url_for("dashboard"))

    try:
        start_time = datetime.fromisoformat(start_time_str)
        end_time = datetime.fromisoformat(end_time_str)
    except ValueError:
        flash("Invalid date/time format.", "error")
        return redirect(url_for("dashboard"))

    if end_time <= start_time:
        flash("End time must be after start time.", "error")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT
                r.id,
                r.user_id,
                r.slot_id,
                r.start_time,
                r.end_time,
                r.status,
                pl.price_per_hour,
                pl.id AS lot_id,
                ps.slot_type,
                ps.supported_vehicle_type
            FROM reservations r
            JOIN parking_slots ps ON r.slot_id = ps.id
            JOIN parking_lots pl ON ps.lot_id = pl.id
            WHERE r.id = %s
        """, (reservation_id,))
        reservation = cur.fetchone()

        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("dashboard"))

        if str(reservation["user_id"]) != session.get("user_id"):
            flash("You can only modify your own reservations.", "error")
            return redirect(url_for("dashboard"))

        if reservation["status"] != "CONFIRMED":
            flash("Only confirmed reservations can be modified.", "error")
            return redirect(url_for("dashboard"))

        if reservation["end_time"] <= datetime.now(timezone.utc):
            flash("Completed reservations cannot be modified.", "error")
            return redirect(url_for("dashboard"))

        cur.execute("""
            SELECT 1
            FROM reservations r
            WHERE r.slot_id = %s
              AND r.id <> %s
              AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
              AND tstzrange(r.start_time, r.end_time, '[)') &&
                  tstzrange(%s, %s, '[)')
            LIMIT 1
        """, (
            reservation["slot_id"],
            reservation["id"],
            start_time,
            end_time,
        ))
        overlapping_reservation = cur.fetchone()

        if overlapping_reservation:
            flash("This reservation cannot be modified because the slot is not available for the selected time range.", "error")
            return redirect(url_for("dashboard"))

        lot_overrides = load_pricing_overrides_for_lots(cur, [reservation["lot_id"]])
        effective_price_per_hour = resolve_effective_price(
            reservation["price_per_hour"],
            lot_overrides.get(reservation["lot_id"], {}),
            reservation["slot_type"],
            reservation["supported_vehicle_type"]
        )

        original_duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
        original_total_cost = round(effective_price_per_hour * original_duration_hours, 2)

        new_duration_hours = (end_time - start_time).total_seconds() / 3600
        new_total_cost = round(effective_price_per_hour * new_duration_hours, 2)

        cost_difference = round(new_total_cost - original_total_cost, 2)

        cur.execute("""
            UPDATE reservations
            SET start_time = %s,
                end_time = %s
            WHERE id = %s
        """, (start_time, end_time, reservation["id"]))
        record_transaction(cur, reservation["id"], session.get("user_id"), "MODIFY_RESERVATION", cost_difference, "SUCCESS")
        conn.commit()

        if cost_difference > 0:
            flash(
                f"Reservation updated successfully. Additional amount of ${cost_difference:.2f} "
                f"will be auto-charged to the same card ending in 1111. "
                f"New estimated total: ${new_total_cost:.2f}",
                "success"
            )
        elif cost_difference < 0:
            flash(
                f"Reservation updated successfully. Refund of ${abs(cost_difference):.2f} "
                f"will be issued to the same card ending in 1111. "
                f"New estimated total: ${new_total_cost:.2f}",
                "success"
            )
        else:
            flash(
                f"Reservation updated successfully. Total remains ${new_total_cost:.2f}.",
                "success"
            )
        return redirect(url_for("dashboard"))

    except psycopg2.errors.ExclusionViolation:
        conn.rollback()
        flash("This reservation cannot be modified because the slot is not available for the selected time range.", "error")
        return redirect(url_for("dashboard"))
    except psycopg2.Error as exc:
        conn.rollback()
        print("Database error in modify_reservation:", exc)
        flash("Could not update the reservation right now.", "error")
        return redirect(url_for("dashboard"))
    finally:
        cur.close()
        conn.close()

def fetch_capacity_utilization_metrics(cur, days=7, lot_id=None):
    """
    Capacity/utilization snapshot for dashboard widgets.
    - Capacity baseline: all slots.
    - Utilization denominator: active + available slots (bookable inventory).
    """
    window_days = max(int(days or 7), 1)
    lot_filter = str(lot_id or "").strip()

    cur.execute(
        """
        WITH date_window AS (
            SELECT
                date_trunc('day', now()) - (%s::int - 1) * interval '1 day' AS window_start,
                date_trunc('day', now()) + interval '1 day' AS window_end
        ),
        lot_rollup AS (
            SELECT
                pl.id AS lot_id,
                pl.name AS lot_name,
                COUNT(ps.id) AS total_slots,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = TRUE
                      AND ps.status = 'AVAILABLE'
                ) AS active_bookable_slots,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = FALSE
                       OR ps.status <> 'AVAILABLE'
                ) AS unavailable_slots,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = TRUE
                      AND ps.status = 'AVAILABLE'
                      AND EXISTS (
                          SELECT 1
                          FROM reservations r
                          WHERE r.slot_id = ps.id
                            AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                            AND now() >= r.start_time
                            AND now() < r.end_time
                      )
                ) AS occupied_now,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = TRUE
                      AND ps.status = 'AVAILABLE'
                      AND EXISTS (
                          SELECT 1
                          FROM reservations r
                          CROSS JOIN date_window dw
                          WHERE r.slot_id = ps.id
                            AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                            AND tstzrange(r.start_time, r.end_time, '[)') &&
                                tstzrange(dw.window_start, dw.window_end, '[)')
                      )
                ) AS occupied_in_window
            FROM parking_lots pl
            LEFT JOIN parking_slots ps ON ps.lot_id = pl.id
            WHERE (%s::text = '' OR pl.id::text = %s::text)
            GROUP BY pl.id, pl.name
        )
        SELECT
            lot_id,
            lot_name,
            total_slots,
            active_bookable_slots,
            unavailable_slots,
            occupied_now,
            occupied_in_window,
            GREATEST(active_bookable_slots - occupied_now, 0) AS available_now,
            CASE
                WHEN active_bookable_slots > 0
                THEN ROUND((occupied_now::numeric / active_bookable_slots::numeric) * 100, 1)
                ELSE 0
            END AS utilization_pct,
            CASE
                WHEN active_bookable_slots > 0
                THEN ROUND((occupied_in_window::numeric / active_bookable_slots::numeric) * 100, 1)
                ELSE 0
            END AS utilization_window_pct
        FROM lot_rollup
        ORDER BY lot_name ASC
        """,
        (window_days, lot_filter, lot_filter),
    )
    lot_rows = cur.fetchall()

    summary = {
        "lot_count": len(lot_rows),
        "window_days": window_days,
        "total_slots": sum(int(row.get("total_slots") or 0) for row in lot_rows),
        "active_bookable_slots": sum(int(row.get("active_bookable_slots") or 0) for row in lot_rows),
        "unavailable_slots": sum(int(row.get("unavailable_slots") or 0) for row in lot_rows),
        "occupied_now": sum(int(row.get("occupied_now") or 0) for row in lot_rows),
        "occupied_in_window": sum(int(row.get("occupied_in_window") or 0) for row in lot_rows),
        "available_now": sum(int(row.get("available_now") or 0) for row in lot_rows),
    }
    if summary["active_bookable_slots"] > 0:
        summary["utilization_pct"] = round(
            (summary["occupied_now"] / summary["active_bookable_slots"]) * 100,
            1,
        )
    else:
        summary["utilization_pct"] = 0.0
    if summary["active_bookable_slots"] > 0:
        summary["utilization_window_pct"] = round(
            (summary["occupied_in_window"] / summary["active_bookable_slots"]) * 100,
            1,
        )
    else:
        summary["utilization_window_pct"] = 0.0

    return summary, lot_rows


def fetch_revenue_demand_trends(cur, days=7, lot_id=None):
    """
    Revenue + demand trends for the recent N-day window.
    Demand uses reservation starts; revenue uses transaction ledger events.
    """
    window_days = max(int(days or 7), 1)
    lot_filter = str(lot_id or "").strip()

    cur.execute(
        """
        SELECT
            date_trunc('day', r.start_time)::date AS day_bucket,
            COUNT(*) AS bookings
        FROM reservations r
        JOIN parking_slots ps ON ps.id = r.slot_id
        WHERE r.start_time >= date_trunc('day', now()) - (%s::int - 1) * interval '1 day'
          AND r.start_time < date_trunc('day', now()) + interval '1 day'
          AND (%s::text = '' OR ps.lot_id::text = %s::text)
        GROUP BY day_bucket
        ORDER BY day_bucket ASC
        """,
        (window_days, lot_filter, lot_filter),
    )
    reservation_rows = cur.fetchall()

    cur.execute(
        """
        SELECT
            date_trunc('day', t.created_at)::date AS day_bucket,
            SUM(
                CASE
                    WHEN COALESCE(UPPER(t.transaction_type), '') = 'REFUND' THEN 0
                    ELSE COALESCE(t.amount, 0)
                END
            ) AS gross_revenue,
            SUM(
                CASE
                    WHEN COALESCE(UPPER(t.transaction_type), '') = 'REFUND' THEN COALESCE(t.amount, 0)
                    ELSE 0
                END
            ) AS refunds,
            COUNT(*) FILTER (
                WHERE COALESCE(UPPER(t.transaction_type), '') <> 'REFUND'
            ) AS successful_charges
        FROM transactions t
        LEFT JOIN reservations r ON r.id = t.reservation_id
        LEFT JOIN parking_slots ps ON ps.id = r.slot_id
        WHERE t.created_at >= date_trunc('day', now()) - (%s::int - 1) * interval '1 day'
          AND t.created_at < date_trunc('day', now()) + interval '1 day'
          AND COALESCE(UPPER(t.status), 'SUCCESS') = 'SUCCESS'
          AND (%s::text = '' OR ps.lot_id::text = %s::text)
        GROUP BY day_bucket
        ORDER BY day_bucket ASC
        """,
        (window_days, lot_filter, lot_filter),
    )
    transaction_rows = cur.fetchall()

    reservations_by_day = {
        row["day_bucket"]: int(row.get("bookings") or 0)
        for row in reservation_rows
    }
    transactions_by_day = {
        row["day_bucket"]: row
        for row in transaction_rows
    }

    today_date = datetime.now(timezone.utc).date()
    trend_rows = []
    for i in range(window_days - 1, -1, -1):
        day_bucket = today_date - timedelta(days=i)
        tx_row = transactions_by_day.get(day_bucket) or {}

        gross_revenue = round(float(tx_row.get("gross_revenue") or 0), 2)
        refunds = round(float(tx_row.get("refunds") or 0), 2)
        net_revenue = round(gross_revenue - refunds, 2)

        trend_rows.append(
            {
                "day_bucket": day_bucket,
                "day_label": day_bucket.strftime("%b %d"),
                "bookings": reservations_by_day.get(day_bucket, 0),
                "successful_charges": int(tx_row.get("successful_charges") or 0),
                "gross_revenue": gross_revenue,
                "refunds": refunds,
                "net_revenue": net_revenue,
            }
        )

    summary = {
        "window_days": window_days,
        "total_bookings": sum(row["bookings"] for row in trend_rows),
        "total_successful_charges": sum(row["successful_charges"] for row in trend_rows),
        "gross_revenue": round(sum(row["gross_revenue"] for row in trend_rows), 2),
        "refunds": round(sum(row["refunds"] for row in trend_rows), 2),
        "net_revenue": round(sum(row["net_revenue"] for row in trend_rows), 2),
    }
    return summary, trend_rows


def fetch_peak_analysis(cur, days=7, lot_id=None):
    """
    Peak demand + peak revenue analysis over a recent time window.
    - Demand peak: booking counts by reservation start hour.
    - Revenue peak: successful transaction totals by transaction hour/day.
    """
    window_days = max(int(days or 7), 1)
    lot_filter = str(lot_id or "").strip()

    cur.execute(
        """
        SELECT
            EXTRACT(HOUR FROM r.start_time)::int AS hour_of_day,
            COUNT(*) AS bookings
        FROM reservations r
        JOIN parking_slots ps ON ps.id = r.slot_id
        WHERE r.start_time >= now() - (%s::int * interval '1 day')
          AND r.start_time < now()
          AND (%s::text = '' OR ps.lot_id::text = %s::text)
        GROUP BY hour_of_day
        ORDER BY bookings DESC, hour_of_day ASC
        LIMIT 3
        """,
        (window_days, lot_filter, lot_filter),
    )
    demand_rows = cur.fetchall()

    def _hour_label(hour_value):
        return datetime.strptime(f"{int(hour_value):02d}:00", "%H:%M").strftime("%I:00 %p").lstrip("0")

    peak_hours = [
        {
            "hour_of_day": int(row.get("hour_of_day") or 0),
            "hour_label": _hour_label(row.get("hour_of_day") or 0),
            "bookings": int(row.get("bookings") or 0),
        }
        for row in demand_rows
    ]

    cur.execute(
        """
        SELECT
            date_trunc('hour', t.created_at) AS hour_bucket,
            SUM(COALESCE(t.amount, 0)) AS hour_revenue
        FROM transactions t
        LEFT JOIN reservations r ON r.id = t.reservation_id
        LEFT JOIN parking_slots ps ON ps.id = r.slot_id
        WHERE t.created_at >= now() - (%s::int * interval '1 day')
          AND t.created_at < now()
          AND COALESCE(UPPER(t.status), 'SUCCESS') = 'SUCCESS'
          AND COALESCE(UPPER(t.transaction_type), '') <> 'REFUND'
          AND (%s::text = '' OR ps.lot_id::text = %s::text)
        GROUP BY hour_bucket
        ORDER BY hour_revenue DESC, hour_bucket ASC
        LIMIT 1
        """,
        (window_days, lot_filter, lot_filter),
    )
    peak_hour_revenue_row = cur.fetchone() or {}

    cur.execute(
        """
        SELECT
            date_trunc('day', t.created_at)::date AS day_bucket,
            SUM(COALESCE(t.amount, 0)) AS day_revenue
        FROM transactions t
        LEFT JOIN reservations r ON r.id = t.reservation_id
        LEFT JOIN parking_slots ps ON ps.id = r.slot_id
        WHERE t.created_at >= date_trunc('day', now()) - (%s::int - 1) * interval '1 day'
          AND t.created_at < date_trunc('day', now()) + interval '1 day'
          AND COALESCE(UPPER(t.status), 'SUCCESS') = 'SUCCESS'
          AND COALESCE(UPPER(t.transaction_type), '') <> 'REFUND'
          AND (%s::text = '' OR ps.lot_id::text = %s::text)
        GROUP BY day_bucket
        ORDER BY day_revenue DESC, day_bucket ASC
        LIMIT 1
        """,
        (window_days, lot_filter, lot_filter),
    )
    peak_day_revenue_row = cur.fetchone() or {}

    peak_hour_bucket = peak_hour_revenue_row.get("hour_bucket")
    peak_day_bucket = peak_day_revenue_row.get("day_bucket")

    summary = {
        "window_days": window_days,
        "peak_booking_hour_label": _hour_label(peak_hours[0]["hour_of_day"]) if peak_hours else "—",
        "peak_booking_hour_count": peak_hours[0]["bookings"] if peak_hours else 0,
        "peak_revenue_hour_label": (
            peak_hour_bucket.strftime("%b %d, %I:%M %p").replace(" 0", " ")
            if peak_hour_bucket
            else "—"
        ),
        "peak_revenue_hour_amount": round(float(peak_hour_revenue_row.get("hour_revenue") or 0), 2),
        "peak_revenue_day_label": (
            peak_day_bucket.strftime("%b %d, %Y")
            if peak_day_bucket
            else "—"
        ),
        "peak_revenue_day_amount": round(float(peak_day_revenue_row.get("day_revenue") or 0), 2),
    }
    return summary, peak_hours


def parse_analytics_filters(req):
    range_key = (req.args.get("range") or "7d").strip().lower()
    if range_key not in {"today", "7d", "30d"}:
        range_key = "7d"
    range_days_map = {"today": 1, "7d": 7, "30d": 30}
    return {
        "range_key": range_key,
        "range_days": range_days_map[range_key],
        "lot_id": (req.args.get("lot_id") or "").strip(),
    }


def fetch_lot_filter_options(cur):
    cur.execute(
        """
        SELECT id, name
        FROM parking_lots
        ORDER BY name ASC
        """
    )
    return cur.fetchall()


@app.route("/operator-dashboard")
@login_required(role="operator")
def operator_dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    capacity_summary = {}
    lot_capacity_rows = []
    pending_requests_count = 0
    trend_summary = {}
    trend_rows = []
    peak_summary = {}
    peak_hours = []
    filters = parse_analytics_filters(request)
    lot_filter_options = []
    try:
        lot_filter_options = fetch_lot_filter_options(cur)
        capacity_summary, lot_capacity_rows = fetch_capacity_utilization_metrics(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
        trend_summary, trend_rows = fetch_revenue_demand_trends(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
        peak_summary, peak_hours = fetch_peak_analysis(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
        cur.execute(
            """
            SELECT COUNT(*) AS pending_count
            FROM reservations
            WHERE status = %s
              AND end_time > now()
            """,
            (PENDING_APPROVAL_STATUS,),
        )
        pending_row = cur.fetchone() or {}
        pending_requests_count = int(pending_row.get("pending_count") or 0)
    except psycopg2.Error as e:
        print("Database error in operator_dashboard capacity metrics:", e)
        flash("Could not load capacity/utilization metrics right now.", "error")
    finally:
        cur.close()
        conn.close()

    return render_template(
        "operator_dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        capacity_summary=capacity_summary,
        lot_capacity_rows=lot_capacity_rows,
        pending_requests_count=pending_requests_count,
        trend_summary=trend_summary,
        trend_rows=trend_rows,
        peak_summary=peak_summary,
        peak_hours=peak_hours,
        analytics_filters=filters,
        lot_filter_options=lot_filter_options,
    )


@app.route("/admin-dashboard")
@login_required(role="admin")
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    capacity_summary = {}
    lot_capacity_rows = []
    trend_summary = {}
    trend_rows = []
    peak_summary = {}
    peak_hours = []
    filters = parse_analytics_filters(request)
    lot_filter_options = []
    try:
        lot_filter_options = fetch_lot_filter_options(cur)
        capacity_summary, lot_capacity_rows = fetch_capacity_utilization_metrics(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
        trend_summary, trend_rows = fetch_revenue_demand_trends(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
        peak_summary, peak_hours = fetch_peak_analysis(
            cur,
            days=filters["range_days"],
            lot_id=filters["lot_id"],
        )
    except psycopg2.Error as e:
        print("Database error in admin_dashboard capacity metrics:", e)
        flash("Could not load capacity/utilization metrics right now.", "error")
    finally:
        cur.close()
        conn.close()

    return render_template(
        "admin_dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        capacity_summary=capacity_summary,
        lot_capacity_rows=lot_capacity_rows,
        trend_summary=trend_summary,
        trend_rows=trend_rows,
        peak_summary=peak_summary,
        peak_hours=peak_hours,
        analytics_filters=filters,
        lot_filter_options=lot_filter_options,
    )

@app.route("/operator/lot-slot-labels/<lot_id>", methods=["GET"])
@login_required(role="operator")
def get_lot_slot_labels(lot_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT label
            FROM parking_slots
            WHERE lot_id = %s
            ORDER BY label
            """,
            (lot_id,)
        )
        rows = cur.fetchall()
        labels = [row["label"] for row in rows]

        cur.execute(
            """
            SELECT DISTINCT slot_type
            FROM parking_slots
            WHERE lot_id = %s
              AND slot_type IS NOT NULL
              AND btrim(slot_type) <> ''
            ORDER BY slot_type
            """,
            (lot_id,)
        )
        slot_type_rows = cur.fetchall()
        slot_types = [row["slot_type"] for row in slot_type_rows]

        return {"labels": labels, "slot_types": slot_types}, 200

    except psycopg2.Error as e:
        print("Database error in get_lot_slot_labels:", e)
        return {"labels": [], "slot_types": []}, 500

    finally:
        cur.close()
        conn.close()

@app.route("/operator/inventory")
@login_required(role="operator")
def operator_inventory():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            pl.id,
            pl.name,
            pl.address,
            pl.price_per_hour,
            COUNT(ps.id) AS total_slots,
            COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                  AND ps.status = 'AVAILABLE'
            ) AS active_slots,
            COUNT(ps.id) FILTER (WHERE ps.is_active = FALSE) AS inactive_slots,
            COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                  AND ps.status = 'AVAILABLE'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM reservations r
                      WHERE r.slot_id = ps.id
                        AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                        AND now() >= r.start_time
                        AND now() < r.end_time
                  )
            ) AS available_now
        FROM parking_lots pl
        LEFT JOIN parking_slots ps ON pl.id = ps.lot_id
        GROUP BY pl.id, pl.name, pl.address, pl.price_per_hour
        ORDER BY pl.name
    """)
    lots = cur.fetchall()

    cur.execute(
        """
        SELECT
            ps.id,
            ps.lot_id,
            ps.label,
            ps.slot_type,
            ps.status,
            ps.is_active,
            EXISTS (
                SELECT 1
                FROM reservations r
                WHERE r.slot_id = ps.id
                  AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                  AND now() >= r.start_time
                  AND now() < r.end_time
            ) AS occupied_now
        FROM parking_slots ps
        ORDER BY ps.lot_id, ps.label
        """
    )
    slots = cur.fetchall()

    slots_by_lot = {}
    for slot in slots:
        slots_by_lot.setdefault(slot["lot_id"], []).append(slot)

    for lot in lots:
        lot["slots"] = slots_by_lot.get(lot["id"], [])

    inventory_summary = {
        "lot_count": len(lots),
        "slot_count": len(slots),
        "available_now_total": sum(int(lot["available_now"] or 0) for lot in lots),
        "inactive_slot_count": sum(1 for s in slots if not s.get("is_active")),
        "oos_slot_count": sum(
            1 for s in slots if (s.get("status") or "") == OUT_OF_SERVICE_SLOT_STATUS
        ),
        "occupied_slot_count": sum(1 for s in slots if s.get("occupied_now")),
    }

    override_rows_by_lot = load_pricing_override_rows_with_meta(
        cur,
        [lot["id"] for lot in lots],
    )
    for lot in lots:
        lot["pricing_overrides"] = override_rows_by_lot.get(lot["id"], [])

    cur.close()
    conn.close()

    return render_template(
        "operator_inventory.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        lots=lots,
        inventory_summary=inventory_summary,
    )


@app.route("/operator/inventory/export.csv")
@login_required(role="operator")
def operator_inventory_export_csv():
    import csv
    from io import StringIO

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            pl.name AS lot_name,
            pl.address AS lot_address,
            ps.label AS slot_label,
            ps.slot_type,
            ps.supported_vehicle_type,
            ps.status AS operational_status,
            ps.is_active AS listed_active,
            EXISTS (
                SELECT 1
                FROM reservations r
                WHERE r.slot_id = ps.id
                  AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                  AND now() >= r.start_time
                  AND now() < r.end_time
            ) AS occupied_now
        FROM parking_slots ps
        JOIN parking_lots pl ON pl.id = ps.lot_id
        ORDER BY pl.name, ps.label
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "lot_name",
            "lot_address",
            "slot_label",
            "slot_type",
            "supported_vehicle_type",
            "operational_status",
            "listed_active",
            "occupied_now",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["lot_name"],
                row["lot_address"],
                row["slot_label"],
                row["slot_type"],
                row["supported_vehicle_type"],
                row["operational_status"],
                "yes" if row["listed_active"] else "no",
                "yes" if row["occupied_now"] else "no",
            ]
        )

    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=spoton-slot-inventory.csv"
        },
    )


@app.route("/operator/reservations/pending")
@login_required(role="operator")
def operator_pending_reservations():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            r.id,
            r.bulk_group_id,
            r.start_time,
            r.end_time,
            r.promo_code,
            u.email AS driver_email,
            u.full_name AS driver_name,
            pl.name AS lot_name,
            pl.address AS lot_address,
            pl.id AS lot_id,
            pl.price_per_hour,
            ps.label AS slot_label,
            ps.slot_type,
            ps.supported_vehicle_type
        FROM reservations r
        JOIN users u ON u.id = r.user_id
        JOIN parking_slots ps ON ps.id = r.slot_id
        JOIN parking_lots pl ON pl.id = ps.lot_id
        WHERE r.status = %s
          AND r.end_time > now()
        ORDER BY r.start_time ASC
        """,
        (PENDING_APPROVAL_STATUS,),
    )
    rows = cur.fetchall()
    enrich_reservation_rows_bulk_metadata(rows)
    lot_ids = list({r["lot_id"] for r in rows if r.get("lot_id")})
    pricing_overrides_by_lot = load_pricing_overrides_for_lots(cur, lot_ids)
    for r in rows:
        eff = resolve_effective_price(
            r["price_per_hour"],
            pricing_overrides_by_lot.get(r["lot_id"], {}),
            r.get("slot_type"),
            r.get("supported_vehicle_type"),
        )
        hours = (r["end_time"] - r["start_time"]).total_seconds() / 3600
        subtotal = round(float(eff) * hours, 2)
        promo_result = apply_promo_discount(subtotal, r.get("promo_code") or "")
        r["estimated_total"] = promo_result["final_total"]
        r["promo_summary"] = (
            f"{promo_result['applied_code']} (-${promo_result['discount_amount']:.2f})"
            if promo_result["is_applied"]
            else "—"
        )
    cur.close()
    conn.close()
    return render_template(
        "operator_pending_reservations.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        rows=rows,
    )


@app.route("/operator/reservations/<reservation_id>/approve", methods=["POST"])
@login_required(role="operator")
def operator_approve_reservation(reservation_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT
                r.id,
                r.user_id,
                r.promo_code,
                r.start_time,
                r.end_time,
                r.status,
                pl.price_per_hour,
                pl.id AS lot_id,
                ps.slot_type,
                ps.supported_vehicle_type
            FROM reservations r
            JOIN parking_slots ps ON ps.id = r.slot_id
            JOIN parking_lots pl ON pl.id = ps.lot_id
            WHERE r.id = %s
            """,
            (reservation_id,),
        )
        row = cur.fetchone()
        if not row or row["status"] != PENDING_APPROVAL_STATUS:
            flash("That request is not pending anymore.", "error")
            return redirect(url_for("operator_pending_reservations"))

        lot_overrides = load_pricing_overrides_for_lots(cur, [row["lot_id"]])
        eff = resolve_effective_price(
            row["price_per_hour"],
            lot_overrides.get(row["lot_id"], {}),
            row["slot_type"],
            row["supported_vehicle_type"],
        )
        hours = (row["end_time"] - row["start_time"]).total_seconds() / 3600
        subtotal = round(float(eff) * hours, 2)
        promo_result = apply_promo_discount(subtotal, row.get("promo_code") or "")
        total_cost = promo_result["final_total"]

        cur.execute(
            """
            UPDATE reservations
            SET status = %s
            WHERE id = %s AND status = %s
            """,
            (CONFIRMED_RESERVATION_STATUS, reservation_id, PENDING_APPROVAL_STATUS),
        )
        if cur.rowcount == 0:
            conn.rollback()
            flash("Could not confirm (it may have been updated already).", "error")
            return redirect(url_for("operator_pending_reservations"))

        record_transaction(
            cur,
            reservation_id,
            row["user_id"],
            "CREATE_RESERVATION",
            total_cost,
            "SUCCESS",
        )
        conn.commit()
        flash("Reservation confirmed for the driver.", "success")
        return redirect(url_for("operator_pending_reservations"))

    except psycopg2.errors.ExclusionViolation:
        conn.rollback()
        flash(
            "Another booking now occupies that window. Decline this request or ask the driver to pick new times.",
            "error",
        )
        return redirect(url_for("operator_pending_reservations"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in operator_approve_reservation:", e)
        flash("Could not approve right now.", "error")
        return redirect(url_for("operator_pending_reservations"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/reservations/<reservation_id>/reject", methods=["POST"])
@login_required(role="operator")
def operator_reject_reservation(reservation_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            UPDATE reservations
            SET status = %s
            WHERE id = %s AND status = %s
            """,
            (REJECTED_RESERVATION_STATUS, reservation_id, PENDING_APPROVAL_STATUS),
        )
        if cur.rowcount == 0:
            flash("Nothing to decline (already handled).", "error")
        else:
            flash("Booking request declined.", "success")
        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in operator_reject_reservation:", e)
        flash("Could not decline right now.", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("operator_pending_reservations"))


# --- Add Slot Route for Operator ---
@app.route("/operator/add-slot", methods=["POST"])
@login_required(role="operator")
def add_slot():
    lot_id = request.form.get("lot_id", "").strip()
    label = request.form.get("label", "").strip().upper()
    slot_type = request.form.get("slot_type", "").strip().lower() or "standard"
    supported_vehicle_type = request.form.get("supported_vehicle_type", "").strip().lower()
    status = request.form.get("status", "AVAILABLE").strip().upper() or "AVAILABLE"
    is_active = request.form.get("is_active") == "on"

    if not lot_id or not label or not supported_vehicle_type:
        flash("Please fill in lot, slot label, and supported vehicle type.", "error")
        return redirect(url_for("operator_inventory"))

    if supported_vehicle_type not in ALLOWED_VEHICLE_TYPES:
        flash("Please select a valid supported vehicle type.", "error")
        return redirect(url_for("operator_inventory"))

    if status not in ALLOWED_SLOT_STATUSES:
        flash("Please select a valid slot status.", "error")
        return redirect(url_for("operator_inventory"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT id, name
            FROM parking_lots
            WHERE id = %s
            """,
            (lot_id,)
        )
        lot = cur.fetchone()

        if not lot:
            flash("Selected parking lot was not found.", "error")
            return redirect(url_for("operator_inventory"))

        cur.execute(
            """
            INSERT INTO parking_slots (
                lot_id,
                label,
                slot_type,
                supported_vehicle_type,
                status,
                is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (lot_id, label, slot_type, supported_vehicle_type, status, is_active)
        )
        conn.commit()

        flash(f"Slot {label} added successfully to {lot['name']}.", "success")
        return redirect(url_for("operator_inventory"))

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        flash("A slot with this label already exists for the selected parking lot.", "error")
        return redirect(url_for("operator_inventory"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in add_slot:", e)
        flash("Could not add the slot right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/lots/<lot_id>/update-base-price", methods=["POST"])
@login_required(role="operator")
def update_lot_base_price(lot_id):
    raw_price = request.form.get("price_per_hour", "")
    parsed_price = parse_price_input(raw_price)

    if parsed_price is None:
        flash("Please enter a valid non-negative base price.", "error")
        return redirect(url_for("operator_inventory"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            UPDATE parking_lots
            SET price_per_hour = %s
            WHERE id = %s
            RETURNING name
            """,
            (parsed_price, lot_id)
        )
        updated = cur.fetchone()
        if not updated:
            flash("Selected parking lot was not found.", "error")
            conn.rollback()
            return redirect(url_for("operator_inventory"))
        conn.commit()
        flash(f"Updated base price for {updated['name']} to ${parsed_price:.2f}/hr.", "success")
        return redirect(url_for("operator_inventory"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in update_lot_base_price:", e)
        flash("Could not update base price right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/lots/<lot_id>/update-price-override", methods=["POST"])
@login_required(role="operator")
def update_lot_price_override(lot_id):
    slot_type = normalize_override_key(request.form.get("slot_type"))
    vehicle_type = normalize_override_key(request.form.get("vehicle_type"))
    parsed_price = parse_price_input(request.form.get("price_per_hour", ""))

    if parsed_price is None:
        flash("Please enter a valid non-negative override price.", "error")
        return redirect(url_for("operator_inventory"))

    if vehicle_type != "any" and vehicle_type not in ALLOWED_VEHICLE_TYPES:
        flash("Please select a valid vehicle type for pricing override.", "error")
        return redirect(url_for("operator_inventory"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_pricing_overrides_table(cur)
        cur.execute(
            """
            INSERT INTO pricing_overrides (lot_id, slot_type, vehicle_type, price_per_hour, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (lot_id, slot_type, vehicle_type)
            DO UPDATE SET price_per_hour = EXCLUDED.price_per_hour, updated_at = now()
            """,
            (lot_id, slot_type, vehicle_type, parsed_price)
        )
        conn.commit()
        flash(
            f"Saved pricing override ${float(parsed_price):.2f}/hr "
            f'(slot type "{slot_type}", vehicle "{vehicle_type}").',
            "success",
        )
        return redirect(url_for("operator_inventory"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in update_lot_price_override:", e)
        flash("Could not save pricing override right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/lots/<lot_id>/remove-price-override", methods=["POST"])
@login_required(role="operator")
def remove_lot_price_override(lot_id):
    slot_type = normalize_override_key(request.form.get("slot_type"))
    vehicle_type = normalize_override_key(request.form.get("vehicle_type"))

    if vehicle_type != "any" and vehicle_type not in ALLOWED_VEHICLE_TYPES:
        flash("Invalid vehicle type for override removal.", "error")
        return redirect(url_for("operator_inventory"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_pricing_overrides_table(cur)
        cur.execute(
            """
            DELETE FROM pricing_overrides
            WHERE lot_id = %s
              AND slot_type = %s
              AND vehicle_type = %s
            RETURNING lot_id
            """,
            (lot_id, slot_type, vehicle_type),
        )
        deleted = cur.fetchone()
        if not deleted:
            flash("No matching pricing override was found.", "error")
            conn.rollback()
            return redirect(url_for("operator_inventory"))
        conn.commit()
        flash(
            f"Removed override ({slot_type or 'any'} / {vehicle_type or 'any'}). "
            "Bookings now use the next matching rule or the lot base rate.",
            "success",
        )
        return redirect(url_for("operator_inventory"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in remove_lot_price_override:", e)
        flash("Could not remove pricing override right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/slots/<slot_id>/toggle-active", methods=["POST"])
@login_required(role="operator")
def toggle_slot_active(slot_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT id, label, is_active
            FROM parking_slots
            WHERE id = %s
            """,
            (slot_id,)
        )
        slot = cur.fetchone()

        if not slot:
            flash("Selected slot was not found.", "error")
            return redirect(url_for("operator_inventory"))

        next_is_active = not slot["is_active"]

        cur.execute(
            """
            UPDATE parking_slots
            SET is_active = %s
            WHERE id = %s
            """,
            (next_is_active, slot_id)
        )
        conn.commit()

        state_label = "active" if next_is_active else "inactive"
        flash(f"Slot {slot['label']} is now {state_label}.", "success")
        return redirect(url_for("operator_inventory"))

    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in toggle_slot_active:", e)
        flash("Could not update slot status right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/slots/<slot_id>/toggle-status", methods=["POST"])
@login_required(role="operator")
def toggle_slot_status(slot_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT id, label, status
            FROM parking_slots
            WHERE id = %s
            """,
            (slot_id,)
        )
        slot = cur.fetchone()

        if not slot:
            flash("Selected slot was not found.", "error")
            return redirect(url_for("operator_inventory"))

        next_status = (
            OUT_OF_SERVICE_SLOT_STATUS
            if slot["status"] == AVAILABLE_SLOT_STATUS
            else AVAILABLE_SLOT_STATUS
        )

        cur.execute(
            """
            UPDATE parking_slots
            SET status = %s
            WHERE id = %s
            """,
            (next_status, slot_id)
        )
        conn.commit()

        flash(
            f"Slot {slot['label']} status updated to {next_status.replace('_', ' ').title()}.",
            "success"
        )
        return redirect(url_for("operator_inventory"))

    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in toggle_slot_status:", e)
        flash("Could not update slot service status right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


@app.route("/operator/slots/<slot_id>/update-details", methods=["POST"])
@login_required(role="operator")
def update_slot_details(slot_id):
    slot_type = request.form.get("slot_type", "").strip().lower() or "standard"
    supported_vehicle_type = request.form.get("supported_vehicle_type", "").strip().lower()

    if supported_vehicle_type not in ALLOWED_VEHICLE_TYPES:
        flash("Please select a valid supported vehicle type.", "error")
        return redirect(url_for("operator_inventory"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT id, label
            FROM parking_slots
            WHERE id = %s
            """,
            (slot_id,)
        )
        slot = cur.fetchone()

        if not slot:
            flash("Selected slot was not found.", "error")
            return redirect(url_for("operator_inventory"))

        cur.execute(
            """
            UPDATE parking_slots
            SET slot_type = %s,
                supported_vehicle_type = %s
            WHERE id = %s
            """,
            (slot_type, supported_vehicle_type, slot_id)
        )
        conn.commit()

        flash(f"Slot {slot['label']} details updated.", "success")
        return redirect(url_for("operator_inventory"))

    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in update_slot_details:", e)
        flash("Could not update slot details right now.", "error")
        return redirect(url_for("operator_inventory"))
    finally:
        cur.close()
        conn.close()


def parse_search_request_args(req):
    """Shared GET args for /search and /compare (card vs table view)."""
    location = req.args.get("location", "").strip()
    start_date = req.args.get("start_date", "").strip()
    start_time_only = req.args.get("start_time_only", "").strip()
    end_date = req.args.get("end_date", "").strip()
    end_time_only = req.args.get("end_time_only", "").strip()
    parking_type = req.args.get("parking_type", "").strip()
    slot_type = req.args.get("slot_type", "").strip()
    sort_by = req.args.get("sort_by", "").strip()
    vehicle_type = req.args.get("vehicle_type", "").strip().lower()
    quick_day = req.args.get("quick_day", "today").strip().lower()
    quick_duration = req.args.get("quick_duration", "60").strip()

    start_time_str = f"{start_date}T{start_time_only}" if start_date and start_time_only else ""
    end_time_str = f"{end_date}T{end_time_only}" if end_date and end_time_only else ""

    selected_start = None
    selected_end = None

    if start_time_str and end_time_str:
        try:
            selected_start = datetime.fromisoformat(start_time_str)
            selected_end = datetime.fromisoformat(end_time_str)
            if selected_end <= selected_start:
                flash("End time must be after start time.", "error")
                selected_start = None
                selected_end = None
                start_time_str = ""
                end_time_str = ""
        except ValueError:
            flash("Invalid date/time format.", "error")
            selected_start = None
            selected_end = None
            start_time_str = ""
            end_time_str = ""

    return {
        "location": location,
        "start_date": start_date,
        "start_time_only": start_time_only,
        "end_date": end_date,
        "end_time_only": end_time_only,
        "parking_type": parking_type,
        "slot_type": slot_type,
        "sort_by": sort_by,
        "vehicle_type": vehicle_type,
        "quick_day": quick_day,
        "quick_duration": quick_duration,
        "selected_start": selected_start,
        "selected_end": selected_end,
        "start_time_str": start_time_str,
        "end_time_str": end_time_str,
    }


def _search_sql_order_clause(sort_by):
    if sort_by == "price_asc":
        return "pl.price_per_hour ASC NULLS LAST"
    if sort_by == "price_desc":
        return "pl.price_per_hour DESC NULLS LAST"
    if sort_by == "available_desc":
        return "available_slots DESC, pl.created_at ASC"
    return "pl.created_at ASC"


def _fetch_enriched_parking_lots(cur, p, user_id):
    """
    Same lot list as /search: availability respects optional window and filters;
    price_per_hour uses overrides for the requested slot_type / vehicle_type keys.
    """
    selected_start = p["selected_start"]
    selected_end = p["selected_end"]
    location = p["location"]
    parking_type = p["parking_type"]
    slot_type = p["slot_type"]
    vehicle_type = p["vehicle_type"]
    sort_by = p["sort_by"]

    order_clause = _search_sql_order_clause(sort_by)

    if selected_start and selected_end:
        query = f"""
            SELECT
                pl.id,
                pl.name,
                pl.address,
                pl.price_per_hour,
                pl.parking_type,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = TRUE
                    AND ps.status = 'AVAILABLE'
                    AND (%s = '' OR ps.slot_type = %s)
                    AND (%s = '' OR ps.supported_vehicle_type = %s)
                    AND NOT EXISTS (
                        SELECT 1
                        FROM reservations r
                        WHERE r.slot_id = ps.id
                          AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                          AND tstzrange(r.start_time, r.end_time, '[)') &&
                              tstzrange(%s, %s, '[)')
                    )
                ) AS available_slots,
                EXISTS (
                    SELECT 1
                    FROM favorite_locations fl
                    WHERE fl.user_id = %s
                      AND fl.parking_lot_id = pl.id
                ) AS is_favorite
            FROM parking_lots pl
            LEFT JOIN parking_slots ps ON pl.id = ps.lot_id
            WHERE (%s = '' OR pl.name ILIKE %s OR pl.address ILIKE %s)
              AND (%s = '' OR pl.parking_type = %s)
            GROUP BY pl.id, pl.name, pl.address, pl.price_per_hour, pl.parking_type
            HAVING COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                AND ps.status = 'AVAILABLE'
                AND (%s = '' OR ps.slot_type = %s)
                AND (%s = '' OR ps.supported_vehicle_type = %s)
                AND NOT EXISTS (
                    SELECT 1
                    FROM reservations r
                    WHERE r.slot_id = ps.id
                      AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                      AND tstzrange(r.start_time, r.end_time, '[)') &&
                          tstzrange(%s, %s, '[)')
                )
            ) > 0
            ORDER BY {order_clause}
        """
        cur.execute(
            query,
            (
                slot_type,
                slot_type,
                vehicle_type,
                vehicle_type,
                selected_start,
                selected_end,
                user_id,
                location,
                f"%{location}%",
                f"%{location}%",
                parking_type,
                parking_type,
                slot_type,
                slot_type,
                vehicle_type,
                vehicle_type,
                selected_start,
                selected_end,
            ),
        )
    else:
        query = f"""
            SELECT
                pl.id,
                pl.name,
                pl.address,
                pl.price_per_hour,
                pl.parking_type,
                COUNT(ps.id) FILTER (
                    WHERE ps.is_active = TRUE
                    AND ps.status = 'AVAILABLE'
                    AND (%s = '' OR ps.slot_type = %s)
                    AND (%s = '' OR ps.supported_vehicle_type = %s)
                ) AS available_slots,
                EXISTS (
                    SELECT 1
                    FROM favorite_locations fl
                    WHERE fl.user_id = %s
                      AND fl.parking_lot_id = pl.id
                ) AS is_favorite
            FROM parking_lots pl
            LEFT JOIN parking_slots ps ON pl.id = ps.lot_id
            WHERE (%s = '' OR pl.name ILIKE %s OR pl.address ILIKE %s)
              AND (%s = '' OR pl.parking_type = %s)
            GROUP BY pl.id, pl.name, pl.address, pl.price_per_hour, pl.parking_type
            HAVING COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                AND ps.status = 'AVAILABLE'
                AND (%s = '' OR ps.slot_type = %s)
                AND (%s = '' OR ps.supported_vehicle_type = %s)
            ) > 0
            ORDER BY {order_clause}
        """
        cur.execute(
            query,
            (
                slot_type,
                slot_type,
                vehicle_type,
                vehicle_type,
                user_id,
                location,
                f"%{location}%",
                f"%{location}%",
                parking_type,
                parking_type,
                slot_type,
                slot_type,
                vehicle_type,
                vehicle_type,
            ),
        )

    lots = cur.fetchall()
    pricing_overrides_by_lot = load_pricing_overrides_for_lots(
        cur,
        [lot["id"] for lot in lots]
    )

    parking_lots = []
    for lot in lots:
        parking_lots.append(
            {
                "id": str(lot["id"]),
                "name": lot["name"],
                "location": lot["address"] if lot["address"] else "Address not available",
                "price_per_hour": resolve_effective_price(
                    lot["price_per_hour"],
                    pricing_overrides_by_lot.get(lot["id"], {}),
                    slot_type,
                    vehicle_type,
                ),
                "available_slots": lot["available_slots"] or 0,
                "type": lot["parking_type"] if lot["parking_type"] else "Standard Parking",
                "is_favorite": lot["is_favorite"],
            }
        )

    if sort_by == "price_asc":
        parking_lots.sort(key=lambda lot: lot["price_per_hour"])
    elif sort_by == "price_desc":
        parking_lots.sort(key=lambda lot: lot["price_per_hour"], reverse=True)

    return parking_lots


def _search_time_options():
    time_options = []
    base_time = datetime.strptime("00:00", "%H:%M")
    for i in range(48):
        t = (base_time + timedelta(minutes=30 * i)).strftime("%H:%M")
        label = datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
        time_options.append({"value": t, "label": label})
    return time_options


@app.route("/search")
def search():
    p = parse_search_request_args(request)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    parking_lots = _fetch_enriched_parking_lots(cur, p, session.get("user_id"))
    cur.close()
    conn.close()

    time_options = _search_time_options()

    return render_template(
        "search.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        parking_lots=parking_lots,
        location=p["location"],
        start_date=p["start_date"],
        start_time_only=p["start_time_only"],
        end_date=p["end_date"],
        end_time_only=p["end_time_only"],
        combined_start_time=p["start_time_str"],
        combined_end_time=p["end_time_str"],
        parking_type=p["parking_type"],
        slot_type=p["slot_type"],
        vehicle_type=p["vehicle_type"],
        sort_by=p["sort_by"],
        quick_day=p["quick_day"],
        quick_duration=p["quick_duration"],
        time_options=time_options,
    )


@app.route("/compare")
def price_compare():
    """Table view of the same results as /search for side-by-side price comparison."""
    p = parse_search_request_args(request)
    fetch_p = dict(p)
    if fetch_p["sort_by"] not in ("price_asc", "price_desc", "available_desc"):
        fetch_p["sort_by"] = "price_asc"

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    parking_lots = _fetch_enriched_parking_lots(cur, fetch_p, session.get("user_id"))
    cur.close()
    conn.close()

    hours = None
    if p["selected_start"] and p["selected_end"]:
        hours = (p["selected_end"] - p["selected_start"]).total_seconds() / 3600.0

    min_rate = None
    min_total = None
    if parking_lots:
        min_rate = min(lot["price_per_hour"] for lot in parking_lots)
        for lot in parking_lots:
            lot["estimated_total"] = (
                round(lot["price_per_hour"] * hours, 2) if hours is not None else None
            )
        totals = [lot["estimated_total"] for lot in parking_lots if lot["estimated_total"] is not None]
        if totals:
            min_total = min(totals)

    for lot in parking_lots:
        lot["is_lowest_hourly_rate"] = (
            min_rate is not None and abs(lot["price_per_hour"] - min_rate) < 0.005
        )
        et = lot.get("estimated_total")
        lot["is_lowest_estimated_total"] = (
            et is not None
            and min_total is not None
            and abs(et - min_total) < 0.005
        )

    time_options = _search_time_options()

    return render_template(
        "price_compare.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        parking_lots=parking_lots,
        location=p["location"],
        start_date=p["start_date"],
        start_time_only=p["start_time_only"],
        end_date=p["end_date"],
        end_time_only=p["end_time_only"],
        combined_start_time=p["start_time_str"],
        combined_end_time=p["end_time_str"],
        parking_type=p["parking_type"],
        slot_type=p["slot_type"],
        vehicle_type=p["vehicle_type"],
        sort_by=fetch_p["sort_by"],
        quick_day=p["quick_day"],
        quick_duration=p["quick_duration"],
        time_options=time_options,
        has_time_window=bool(hours),
        compare_hours=round(hours, 4) if hours is not None else None,
    )


@app.route("/lot/<lot_id>")
@login_required(role="driver")
def lot_details(lot_id):
    start_time_str = request.args.get("start_time", "").strip()
    end_time_str = request.args.get("end_time", "").strip()
    user_timezone = request.args.get("user_timezone", "UTC").strip()
    retry_slot_id = request.args.get("retry_slot_id", "").strip()
    retry_vehicle_id = request.args.get("retry_vehicle_id", "").strip()
    retry_promo_code = request.args.get("retry_promo_code", "").strip()

    try:
        user_tz = ZoneInfo(user_timezone)
    except Exception:
        user_tz = ZoneInfo("UTC")

    selected_start = None
    selected_end = None

    start_date = ""
    start_time_only = ""
    end_date = ""
    end_time_only = ""

    if start_time_str and end_time_str:
        try:
            local_start = datetime.fromisoformat(start_time_str)
            local_end = datetime.fromisoformat(end_time_str)

            start_date = local_start.strftime("%Y-%m-%d")
            start_time_only = local_start.strftime("%H:%M")
            end_date = local_end.strftime("%Y-%m-%d")
            end_time_only = local_end.strftime("%H:%M")

            selected_start = local_start.replace(tzinfo=user_tz).astimezone(timezone.utc)
            selected_end = local_end.replace(tzinfo=user_tz).astimezone(timezone.utc)

        except ValueError:
            flash("Invalid search time range.", "error")
            return redirect(url_for("search"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            pl.id,
            pl.name,
            pl.address,
            pl.price_per_hour,
            pl.parking_type,
            COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                  AND ps.status = 'AVAILABLE'
            ) AS available_slots,
            EXISTS (
                SELECT 1
                FROM favorite_locations fl
                WHERE fl.user_id = %s
                  AND fl.parking_lot_id = pl.id
            ) AS is_favorite
        FROM parking_lots pl
        LEFT JOIN parking_slots ps ON pl.id = ps.lot_id
        WHERE pl.id = %s
        GROUP BY pl.id, pl.name, pl.address, pl.price_per_hour, pl.parking_type
    """, (session.get("user_id"), lot_id))
    lot = cur.fetchone()

    if not lot:
        cur.close()
        conn.close()
        flash("Parking lot not found.", "error")
        return redirect(url_for("search"))

    if selected_start and selected_end:
        cur.execute("""
            SELECT
                ps.id,
                ps.label,
                ps.slot_type,
                ps.supported_vehicle_type,
                ps.is_active,
                ps.status,
                NOT EXISTS (
                    SELECT 1
                        FROM reservations r
                        WHERE r.slot_id = ps.id
                          AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                          AND tstzrange(r.start_time, r.end_time, '[)') &&
                              tstzrange(%s, %s, '[)')
                ) AS is_available_now,
                EXISTS (
                    SELECT 1
                    FROM reservations r
                    WHERE r.slot_id = ps.id
                      AND r.user_id = %s
                      AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                      AND tstzrange(r.start_time, r.end_time, '[)') &&
                          tstzrange(%s, %s, '[)')
                ) AS reserved_by_current_user
            FROM parking_slots ps
            WHERE ps.lot_id = %s
            ORDER BY ps.label
        """, (
            selected_start,
            selected_end,
            session.get("user_id"),
            selected_start,
            selected_end,
            lot_id
        ))
    else:
        cur.execute("""
            SELECT
                ps.id,
                ps.label,
                ps.slot_type,
                ps.supported_vehicle_type,
                ps.is_active,
                ps.status,
                TRUE AS is_available_now,
                FALSE AS reserved_by_current_user
            FROM parking_slots ps
            WHERE ps.lot_id = %s
            ORDER BY ps.label
        """, (lot_id,))

    slots = cur.fetchall()

    cur.execute("""
        SELECT id, plate_number, vehicle_make, vehicle_model, vehicle_color, vehicle_type
        FROM vehicles
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (session.get("user_id"),))
    vehicles = cur.fetchall()

    cur.close()
    conn.close()

    bulk_eligible_count = sum(
        1
        for s in slots
        if s.get("is_active")
        and s.get("status") == AVAILABLE_SLOT_STATUS
        and s.get("is_available_now")
        and not s.get("reserved_by_current_user")
    )

    bulk_retry_map = {}
    bulk_reopen_payment = False
    bulk_retry_cardholder = ""
    br = session.get("bulk_reservation_payment_retry")
    if br and str(br.get("lot_id")) == str(lot_id):
        sv = br.get("slot_vehicles") or {}
        bulk_retry_map = {str(k): str(v) for k, v in sv.items() if v}
        bulk_reopen_payment = bool(bulk_retry_map)
        bulk_retry_cardholder = (br.get("cardholder_name") or "").strip()
        session.pop("bulk_reservation_payment_retry", None)

    time_options = []
    base_time = datetime.strptime("00:00", "%H:%M")
    for i in range(48):
        t = (base_time + timedelta(minutes=30 * i)).strftime("%H:%M")
        label = datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
        time_options.append({"value": t, "label": label})

    return render_template(
        "lot_details.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        lot=lot,
        slots=slots,
        vehicles=vehicles,
        start_date=start_date,
        start_time_only=start_time_only,
        end_date=end_date,
        end_time_only=end_time_only,
        time_options=time_options,
        retry_slot_id=retry_slot_id,
        retry_vehicle_id=retry_vehicle_id,
        retry_promo_code=retry_promo_code,
        show_bulk_panel=bulk_eligible_count >= 1 and len(vehicles) > 0,
        bulk_eligible_count=bulk_eligible_count,
        bulk_retry_map=bulk_retry_map,
        bulk_reopen_payment=bulk_reopen_payment,
        bulk_retry_cardholder=bulk_retry_cardholder,
    )


@app.route("/reserve/<slot_id>", methods=["POST"])
@login_required(role="driver")
def reserve_slot(slot_id):
    lot_id = request.form.get("lot_id", "").strip()
    vehicle_id = request.form.get("vehicle_id", "").strip()
    user_timezone = request.form.get("user_timezone", "UTC").strip()
    cardholder_name = request.form.get("cardholder_name", "").strip()
    card_number = request.form.get("card_number", "").strip()
    expiry = request.form.get("expiry", "").strip()
    cvv = request.form.get("cvv", "").strip()
    promo_code = request.form.get("promo_code", "").strip()

    try:
        user_tz = ZoneInfo(user_timezone)
    except Exception:
        user_tz = ZoneInfo("UTC")

    start_date = request.form.get("start_date", "").strip()
    start_time_only = request.form.get("start_time_only", "").strip()
    end_date = request.form.get("end_date", "").strip()
    end_time_only = request.form.get("end_time_only", "").strip()

    start_time_str = f"{start_date}T{start_time_only}" if start_date and start_time_only else ""
    end_time_str = f"{end_date}T{end_time_only}" if end_date and end_time_only else ""

    def back_to_lot():
        return redirect(
            url_for(
                "lot_details",
                lot_id=lot_id or "",
                start_time=start_time_str,
                end_time=end_time_str,
                user_timezone=user_timezone,
                retry_slot_id=slot_id,
                retry_vehicle_id=vehicle_id,
                retry_promo_code=promo_code,
            )
        )

    if not lot_id or not vehicle_id or not start_time_str or not end_time_str:
        flash("Please select a vehicle and provide reservation start and end times.", "error")
        return back_to_lot()

    try:
        start_local = datetime.fromisoformat(start_time_str).replace(tzinfo=user_tz)
        end_local = datetime.fromisoformat(end_time_str).replace(tzinfo=user_tz)
    except ValueError:
        flash("Invalid date/time format.", "error")
        return back_to_lot()

    start_time = start_local.astimezone(timezone.utc)
    end_time = end_local.astimezone(timezone.utc)

    now_utc = datetime.now(timezone.utc)
    minimum_start_time = now_utc + timedelta(minutes=1)

    if end_time <= start_time:
        flash("End time must be after start time.", "error")
        return back_to_lot()

    if start_time < minimum_start_time:
        flash(
            "Start date and time must be in the future in your time zone (not in the past).",
            "error",
        )
        return back_to_lot()

    normalized_promo_code = (promo_code or "").strip().upper()
    promo_probe = apply_promo_discount(0, promo_code)
    if promo_code and not promo_probe["is_applied"]:
        flash(
            f"Invalid promo code. Use {FIRST_BOOKING_PROMO_CODE} or {PACE_PROMO_CODE}.",
            "error",
        )
        return back_to_lot()

    ensure_db_integrity_constraints()
    ensure_reservation_approval_schema()
    ensure_bulk_reservation_schema()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            """
            SELECT
                ps.id,
                ps.lot_id,
                ps.is_active,
                ps.status,
                ps.slot_type,
                ps.supported_vehicle_type,
                pl.price_per_hour
            FROM parking_slots ps
            JOIN parking_lots pl ON pl.id = ps.lot_id
            WHERE ps.id = %s
            """,
            (slot_id,)
        )
        slot_record = cur.fetchone()

        if not slot_record:
            flash("Slot not found.", "error")
            return redirect(url_for("search"))

        lot_id = str(slot_record["lot_id"])

        if not slot_record["is_active"]:
            flash("This slot is currently inactive.", "error")
            return back_to_lot()

        if slot_record["status"] != AVAILABLE_SLOT_STATUS:
            flash("This slot is currently out of service.", "error")
            return back_to_lot()

        cur.execute(
            """
            SELECT id, plate_number, vehicle_type
            FROM vehicles
            WHERE id = %s
              AND user_id = %s
            """,
            (vehicle_id, session.get("user_id"))
        )
        selected_vehicle = cur.fetchone()

        if not selected_vehicle:
            flash("Selected vehicle not found.", "error")
            return back_to_lot()

        selected_vehicle_type = (selected_vehicle["vehicle_type"] or "").strip().lower()
        supported_vehicle_type = (slot_record["supported_vehicle_type"] or "").strip().lower()

        if selected_vehicle_type != supported_vehicle_type:
            flash(
                f"Vehicle type mismatch. Slot supports {slot_record['supported_vehicle_type']}, "
                f"but selected vehicle is {selected_vehicle['vehicle_type']}.",
                "error"
            )
            return back_to_lot()

        pay_err, cleaned_card_number = validate_demo_payment_fields(
            cardholder_name, card_number, expiry, cvv
        )
        if pay_err:
            flash(pay_err, "error")
            return back_to_lot()

        if normalized_promo_code == FIRST_BOOKING_PROMO_CODE:
            cur.execute(
                """
                SELECT COUNT(*) AS reservation_count
                FROM reservations
                WHERE user_id = %s
                  AND status = 'CONFIRMED'
                """,
                (session.get("user_id"),),
            )
            first_booking_row = cur.fetchone()
            prior_booking_count = int(first_booking_row["reservation_count"] or 0)
            if prior_booking_count > 0:
                flash(
                    f"{FIRST_BOOKING_PROMO_CODE} is valid only for your first booking.",
                    "error",
                )
                return back_to_lot()

        lot_overrides = load_pricing_overrides_for_lots(cur, [slot_record["lot_id"]])
        effective_price_per_hour = resolve_effective_price(
            slot_record["price_per_hour"],
            lot_overrides.get(slot_record["lot_id"], {}),
            slot_record["slot_type"],
            selected_vehicle_type
        )

        duration_hours = (end_time - start_time).total_seconds() / 3600
        subtotal = round(effective_price_per_hour * duration_hours, 2)
        promo_result = apply_promo_discount(subtotal, promo_code)
        total_cost = promo_result["final_total"]

        promo_to_store = (
            promo_result["applied_code"] if promo_result.get("is_applied") else None
        )
        cur.execute(
            """
            INSERT INTO reservations (user_id, slot_id, start_time, end_time, status, promo_code, bulk_group_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                str(session.get("user_id")),
                str(slot_id),
                start_time,
                end_time,
                PENDING_APPROVAL_STATUS,
                promo_to_store,
                None,
            ),
        )
        inserted_reservation = cur.fetchone()
        conn.commit()

        flash(
            f"Request submitted for {selected_vehicle['plate_number']}. "
            "The garage will review your times; you will see it as “Awaiting approval” on your dashboard. "
            "Demo card ending in "
            f"{cleaned_card_number[-4:]} will be charged when the booking is confirmed."
            + (
                f" Promo {promo_result['applied_code']} will apply if still valid at approval "
                f"(-${promo_result['discount_amount']:.2f})."
                if promo_result["is_applied"]
                else ""
            ),
            "success",
        )
        return redirect(url_for("dashboard"))

    except psycopg2.errors.ExclusionViolation:
        conn.rollback()
        flash("That slot is already reserved for the selected time range.", "error")
        return back_to_lot()
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in reserve_slot:", e)
        flash("Something went wrong while saving the reservation.", "error")
        return back_to_lot()

    finally:
        cur.close()
        conn.close()


@app.route("/lot/<lot_id>/reserve-bulk", methods=["POST"])
@login_required(role="driver")
def reserve_bulk(lot_id):
    """Reserve multiple slots in one lot for the same window (single checkout, shared bulk_group_id)."""
    user_timezone = request.form.get("user_timezone", "UTC").strip()
    cardholder_name = request.form.get("cardholder_name", "").strip()
    card_number = request.form.get("card_number", "").strip()
    expiry = request.form.get("expiry", "").strip()
    cvv = request.form.get("cvv", "").strip()

    start_date = request.form.get("b_start_date", "").strip()
    start_time_only = request.form.get("b_start_time_only", "").strip()
    end_date = request.form.get("b_end_date", "").strip()
    end_time_only = request.form.get("b_end_time_only", "").strip()

    raw_slots = request.form.getlist("slot_id")
    slot_ids = list(dict.fromkeys(s.strip() for s in raw_slots if (s or "").strip()))

    start_time_str = f"{start_date}T{start_time_only}" if start_date and start_time_only else ""
    end_time_str = f"{end_date}T{end_time_only}" if end_date and end_time_only else ""

    def redirect_lot():
        return redirect(
            url_for(
                "lot_details",
                lot_id=lot_id,
                start_time=start_time_str,
                end_time=end_time_str,
                user_timezone=user_timezone,
            )
        )

    if len(slot_ids) < 1:
        flash("Select at least one available slot for this booking.", "error")
        return redirect_lot()

    try:
        user_tz = ZoneInfo(user_timezone)
    except Exception:
        user_tz = ZoneInfo("UTC")

    if not start_time_str or not end_time_str:
        flash("Provide start and end times for the group booking.", "error")
        return redirect_lot()

    try:
        start_local = datetime.fromisoformat(start_time_str).replace(tzinfo=user_tz)
        end_local = datetime.fromisoformat(end_time_str).replace(tzinfo=user_tz)
    except ValueError:
        flash("Invalid date/time format.", "error")
        return redirect_lot()

    start_time = start_local.astimezone(timezone.utc)
    end_time = end_local.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    minimum_start_time = now_utc + timedelta(minutes=1)

    if end_time <= start_time:
        flash("End time must be after start time.", "error")
        return redirect_lot()

    if start_time < minimum_start_time:
        flash(
            "Start date and time must be in the future in your time zone (not in the past).",
            "error",
        )
        return redirect_lot()

    pay_err, cleaned_card_number = validate_demo_payment_fields(
        cardholder_name, card_number, expiry, cvv
    )
    if pay_err:
        flash(pay_err, "error")
        # Preserve slot + vehicle selection across redirect (URL only carries times).
        session["bulk_reservation_payment_retry"] = {
            "lot_id": str(lot_id),
            "slot_vehicles": {
                str(sid): (request.form.get(f"vehicle_for_{sid}", "") or "").strip()
                for sid in slot_ids
            },
            "cardholder_name": cardholder_name,
        }
        return redirect_lot()

    # Successful payment field validation — clear any bulk retry state for this lot.
    br = session.get("bulk_reservation_payment_retry")
    if br and str(br.get("lot_id")) == str(lot_id):
        session.pop("bulk_reservation_payment_retry", None)

    # Re-run idempotent DDL right before booking: some deployments skip the first
    # before_request migration (e.g. health-only probes), leaving an old status CHECK
    # or missing bulk_group_id / promo_code columns. Many hosted DBs require the
    # owner to run migrations/001_reservations_approval_and_bulk.sql manually.
    ensure_db_integrity_constraints()
    ensure_reservation_approval_schema()
    ensure_bulk_reservation_schema()

    conn = None
    cur = None
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id FROM parking_lots WHERE id = %s", (lot_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        flash("Parking lot not found.", "error")
        return redirect(url_for("search"))

    # Pass a string: psycopg2 does not adapt uuid.UUID unless register_uuid() is used.
    bulk_group_id = str(uuid.uuid4())
    user_id = session.get("user_id")

    try:
        validated_rows = []
        for sid in slot_ids:
            vid = (request.form.get(f"vehicle_for_{sid}", "") or "").strip()
            if not vid:
                conn.rollback()
                flash("Choose a vehicle for each selected slot.", "error")
                return redirect_lot()

            cur.execute(
                """
                SELECT id, plate_number, vehicle_type
                FROM vehicles
                WHERE id = %s AND user_id = %s
                """,
                (vid, user_id),
            )
            veh_row = cur.fetchone()
            if not veh_row:
                conn.rollback()
                flash("One of the selected vehicles was not found.", "error")
                return redirect_lot()

            selected_vehicle_type = (veh_row["vehicle_type"] or "").strip().lower()

            cur.execute(
                """
                SELECT
                    ps.id,
                    ps.lot_id,
                    ps.label,
                    ps.is_active,
                    ps.status,
                    ps.supported_vehicle_type
                FROM parking_slots ps
                JOIN parking_lots pl ON pl.id = ps.lot_id
                WHERE ps.id = %s
                """,
                (sid,),
            )
            slot_record = cur.fetchone()
            if not slot_record or str(slot_record["lot_id"]) != str(lot_id):
                conn.rollback()
                flash("One or more slots are not part of this lot.", "error")
                return redirect_lot()

            if not slot_record["is_active"]:
                conn.rollback()
                flash(f"Slot {slot_record['label']} is inactive.", "error")
                return redirect_lot()

            if slot_record["status"] != AVAILABLE_SLOT_STATUS:
                conn.rollback()
                flash(f"Slot {slot_record['label']} is out of service.", "error")
                return redirect_lot()

            supported = (slot_record["supported_vehicle_type"] or "").strip().lower()
            if selected_vehicle_type != supported:
                conn.rollback()
                flash(
                    f"Vehicle for slot {slot_record['label']} must match supported type "
                    f"({slot_record['supported_vehicle_type']}).",
                    "error",
                )
                return redirect_lot()

            cur.execute(
                """
                SELECT 1
                FROM reservations r
                WHERE r.slot_id = %s
                  AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                  AND tstzrange(r.start_time, r.end_time, '[)') &&
                      tstzrange(%s, %s, '[)')
                """,
                (sid, start_time, end_time),
            )
            if cur.fetchone():
                conn.rollback()
                flash(
                    f"Slot {slot_record['label']} is no longer available for that window.",
                    "error",
                )
                return redirect_lot()

            cur.execute(
                """
                SELECT 1
                FROM reservations r
                WHERE r.slot_id = %s
                  AND r.user_id = %s
                  AND r.status IN ('CONFIRMED', 'PENDING_APPROVAL')
                  AND tstzrange(r.start_time, r.end_time, '[)') &&
                      tstzrange(%s, %s, '[)')
                """,
                (sid, user_id, start_time, end_time),
            )
            if cur.fetchone():
                conn.rollback()
                flash(
                    f"You already hold slot {slot_record['label']} for this time range.",
                    "error",
                )
                return redirect_lot()

            validated_rows.append({"slot": slot_record, "vehicle": veh_row})

        for row in validated_rows:
            slot_record = row["slot"]
            cur.execute(
                """
                INSERT INTO reservations (user_id, slot_id, start_time, end_time, status, promo_code, bulk_group_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(user_id),
                    str(slot_record["id"]),
                    start_time,
                    end_time,
                    PENDING_APPROVAL_STATUS,
                    None,
                    bulk_group_id,
                ),
            )

        conn.commit()
        n = len(validated_rows)
        plates = [
            (r["vehicle"].get("plate_number") or "").strip()
            for r in validated_rows
            if r.get("vehicle")
        ]
        plates_display = ", ".join(dict.fromkeys(p for p in plates if p)) or "your vehicles"
        flash(
            f"Group request submitted for {n} slots ({plates_display}). "
            "Each space appears as awaiting approval on your dashboard. "
            f"Demo card ••••{cleaned_card_number[-4:]} will be charged per slot when confirmed.",
            "success",
        )
        return redirect(url_for("dashboard"))

    except psycopg2.errors.ExclusionViolation:
        conn.rollback()
        flash(
            "A slot in your group was just booked by someone else. Refresh and try again.",
            "error",
        )
        return redirect_lot()
    except psycopg2.errors.CheckViolation as e:
        conn.rollback()
        print("Database error in reserve_bulk (check constraint):", e)
        flash(
            "Could not save the group booking: the database rejected the reservation record. "
            "Run the SQL in migrations/001_reservations_approval_and_bulk.sql on your database (Supabase "
            "SQL editor as owner), then try again.",
            "error",
        )
        return redirect_lot()
    except psycopg2.IntegrityError as e:
        conn.rollback()
        print("Database error in reserve_bulk (integrity):", repr(e))
        flash(
            "Could not complete the group booking (a database rule was violated). "
            "Try a different time or refresh the page.",
            "error",
        )
        return redirect_lot()
    except psycopg2.errors.UndefinedColumn as e:
        conn.rollback()
        print("Database error in reserve_bulk (missing column):", e)
        flash(
            "The database is missing a required column (e.g. bulk_group_id or promo_code). "
            "Run migrations/001_reservations_approval_and_bulk.sql in the SQL editor, then try again.",
            "error",
        )
        return redirect_lot()
    except psycopg2.ProgrammingError as e:
        conn.rollback()
        err_text = (str(e) or "").lower()
        if "column" in err_text and "does not exist" in err_text:
            print("Database error in reserve_bulk (missing column, programming):", e)
            flash(
                "The database schema is out of date. "
                "Run migrations/001_reservations_approval_and_bulk.sql, then try again.",
                "error",
            )
        else:
            print("Database error in reserve_bulk (programming):", repr(e))
            flash("Something went wrong while saving the group reservation.", "error")
        return redirect_lot()
    except psycopg2.Error as e:
        conn.rollback()
        pg = getattr(e, "pgcode", None)
        d = getattr(e, "diag", None)
        det = getattr(d, "message_primary", None) if d else None
        print("Database error in reserve_bulk:", repr(e), "pgcode=", pg, "detail=", det)
        if pg in ("42501", "42503"):
            flash(
                "The database user cannot apply required schema changes. "
                "Have an admin run migrations/001_reservations_approval_and_bulk.sql in the SQL editor.",
                "error",
            )
        else:
            flash("Something went wrong while saving the group reservation.", "error")
        return redirect_lot()
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print("Non-database error in reserve_bulk:", repr(e))
        traceback.print_exc()
        flash("Something went wrong while saving the group reservation.", "error")
        return redirect_lot()
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.route("/receipt/<reservation_id>")
@login_required(role="driver")
def booking_receipt(reservation_id):
    user_timezone = request.args.get("user_timezone", "UTC").strip() or "UTC"
    receipt_promo_code = request.args.get("applied_promo_code", "").strip().upper()
    try:
        user_tz = ZoneInfo(user_timezone)
    except Exception:
        user_tz = ZoneInfo("UTC")
        user_timezone = "UTC"

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT
            r.id,
            r.user_id,
            r.start_time,
            r.end_time,
            r.status,
            r.promo_code,
            pl.id AS lot_id,
            pl.name AS lot_name,
            pl.address AS lot_address,
            pl.price_per_hour,
            ps.label AS slot_label,
            ps.slot_type,
            ps.supported_vehicle_type
        FROM reservations r
        JOIN parking_slots ps ON r.slot_id = ps.id
        JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE r.id = %s
        """,
        (reservation_id,),
    )
    row = cur.fetchone()

    if not row or str(row["user_id"]) != session.get("user_id"):
        cur.close()
        conn.close()
        flash("Receipt not found.", "error")
        return redirect(url_for("dashboard"))

    if row.get("status") != CONFIRMED_RESERVATION_STATUS:
        cur.close()
        conn.close()
        flash("Receipt is available after the garage confirms your booking.", "error")
        return redirect(url_for("dashboard"))

    cur.execute(
        """
        SELECT amount
        FROM transactions
        WHERE reservation_id = %s
          AND user_id = %s
          AND transaction_type = 'CREATE_RESERVATION'
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (reservation_id, session.get("user_id")),
    )
    tx = cur.fetchone()

    overrides = load_pricing_overrides_for_lots(cur, [row["lot_id"]])
    effective = resolve_effective_price(
        row["price_per_hour"],
        overrides.get(row["lot_id"], {}),
        row["slot_type"],
        row["supported_vehicle_type"],
    )
    hours = (row["end_time"] - row["start_time"]).total_seconds() / 3600
    subtotal_cost = round(float(effective) * hours, 2)

    if tx and tx.get("amount") is not None:
        total_cost = round(float(tx["amount"]), 2)
    else:
        total_cost = subtotal_cost

    discount_amount = round(max(subtotal_cost - total_cost, 0), 2)
    applied_promo_code = ""
    if discount_amount > 0:
        if receipt_promo_code in SUPPORTED_PROMOS:
            applied_promo_code = receipt_promo_code
        else:
            inferred_percent = round((discount_amount / subtotal_cost) * 100) if subtotal_cost > 0 else 0
            for code, percent in SUPPORTED_PROMOS.items():
                if inferred_percent == percent:
                    applied_promo_code = code
                    break
    if (
        discount_amount > 0
        and not applied_promo_code
        and (row.get("promo_code") or "").strip().upper() in SUPPORTED_PROMOS
    ):
        applied_promo_code = (row.get("promo_code") or "").strip().upper()

    cur.close()
    conn.close()

    start_local = row["start_time"].astimezone(user_tz)
    end_local = row["end_time"].astimezone(user_tz)
    formatted_start = start_local.strftime("%b %d, %Y • %I:%M %p").replace(" 0", " ")
    formatted_end = end_local.strftime("%b %d, %Y • %I:%M %p").replace(" 0", " ")
    start_iso = start_local.replace(tzinfo=None).isoformat(timespec="minutes")
    end_iso = end_local.replace(tzinfo=None).isoformat(timespec="minutes")

    lot = {
        "id": row["lot_id"],
        "name": row["lot_name"],
        "address": row["lot_address"] or "",
    }

    return render_template(
        "booking_receipt.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        reservation_id=reservation_id,
        booking_alias=build_booking_alias(reservation_id),
        lot=lot,
        slot_label=row["slot_label"],
        formatted_start=formatted_start,
        formatted_end=formatted_end,
        subtotal_cost=subtotal_cost,
        discount_amount=discount_amount,
        applied_promo_code=applied_promo_code,
        total_cost=total_cost,
        tz_label=user_timezone,
        user_timezone=user_timezone,
        start_iso=start_iso,
        end_iso=end_iso,
    )


@app.route("/cancel-reservation/<reservation_id>", methods=["POST"])
@login_required(role="driver")
def cancel_reservation(reservation_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            r.id,
            r.user_id,
            r.status,
            r.start_time,
            r.end_time,
            pl.price_per_hour,
            pl.id AS lot_id,
            ps.slot_type,
            ps.supported_vehicle_type
        FROM reservations r
        JOIN parking_slots ps ON r.slot_id = ps.id
        JOIN parking_lots pl ON ps.lot_id = pl.id
        WHERE r.id = %s
    """, (reservation_id,))
    reservation = cur.fetchone()

    if not reservation:
        cur.close()
        conn.close()
        flash("Reservation not found.", "error")
        return redirect(url_for("dashboard"))

    if str(reservation["user_id"]) != session.get("user_id"):
        cur.close()
        conn.close()
        flash("You can only cancel your own reservations.", "error")
        return redirect(url_for("dashboard"))

    if reservation["status"] == PENDING_APPROVAL_STATUS:
        cur.execute(
            """
            UPDATE reservations
            SET status = 'CANCELLED'
            WHERE id = %s
            """,
            (reservation_id,),
        )
        conn.commit()
        cur.close()
        conn.close()
        flash("Pending booking request withdrawn.", "success")
        return redirect(url_for("dashboard"))

    if reservation["status"] != "CONFIRMED":
        cur.close()
        conn.close()
        flash("Only confirmed reservations can be cancelled.", "error")
        return redirect(url_for("dashboard"))

    cur.execute(
        """
        SELECT amount
        FROM transactions
        WHERE reservation_id = %s
          AND user_id = %s
          AND transaction_type = 'CREATE_RESERVATION'
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (reservation_id, session.get("user_id")),
    )
    create_tx = cur.fetchone()

    if create_tx and create_tx.get("amount") is not None:
        refund_amount = round(float(create_tx["amount"]), 2)
    else:
        lot_overrides = load_pricing_overrides_for_lots(cur, [reservation["lot_id"]])
        effective_price_per_hour = resolve_effective_price(
            reservation["price_per_hour"],
            lot_overrides.get(reservation["lot_id"], {}),
            reservation["slot_type"],
            reservation["supported_vehicle_type"]
        )
        duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
        refund_amount = round(effective_price_per_hour * duration_hours, 2)

    record_refund_simulated(cur, reservation["id"], session.get("user_id"), refund_amount)
    cur.execute("""
        UPDATE reservations
        SET status = 'CANCELLED'
        WHERE id = %s
    """, (reservation_id,))
    conn.commit()

    cur.close()
    conn.close()

    flash(
        f"Reservation cancelled. Simulated refund of ${refund_amount:.2f} "
        f"(pending → completed) will appear in your transaction history.",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/legal")
def legal():
    return render_template("legal.html")


@app.route("/support/faq")
def support_faq():
    return render_template(
        "support_faq.html",
        is_logged_in=bool(session.get("user_id")),
        user_role=session.get("user_role"),
    )


@app.route("/support/self-service", methods=["GET", "POST"])
def support_self_service():
    booking_id = request.form.get("booking_id", "").strip() if request.method == "POST" else ""
    booking = None

    if request.method == "POST":
        if not booking_id:
            flash("Please enter a booking ID.", "error")
        else:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            normalized_lookup = "".join(ch for ch in booking_id.upper() if ch.isalnum())
            alias_body = normalized_lookup[2:] if normalized_lookup.startswith("SP") else normalized_lookup
            lookup_alias = f"SP-{alias_body}" if alias_body else ""
            cur.execute(
                """
                SELECT
                    r.id,
                    r.status,
                    r.start_time,
                    r.end_time,
                    pl.name AS lot_name,
                    pl.address AS lot_address,
                    ps.label AS slot_label,
                    u.email AS booking_email
                FROM reservations r
                JOIN parking_slots ps ON r.slot_id = ps.id
                JOIN parking_lots pl ON ps.lot_id = pl.id
                JOIN users u ON r.user_id = u.id
                WHERE CAST(r.id AS TEXT) = %s
                   OR UPPER(
                        'SP-'
                        || SUBSTRING(REPLACE(CAST(r.id AS TEXT), '-', '') FROM 1 FOR 6)
                        || SUBSTRING(REPLACE(CAST(r.id AS TEXT), '-', '') FROM 29 FOR 4)
                   ) = %s
                LIMIT 1
                """,
                (booking_id, lookup_alias),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()

            if not row:
                flash("No booking found for that booking ID.", "error")
            else:
                booking = {
                    "id": str(row["id"]),
                    "booking_alias": build_booking_alias(row["id"]),
                    "status": row["status"],
                    "lot_name": row["lot_name"],
                    "lot_address": row["lot_address"] or "",
                    "slot_label": row["slot_label"],
                    "booking_email": row["booking_email"],
                    "formatted_start": row["start_time"].strftime("%b %d, %Y • %I:%M %p").replace(" 0", " "),
                    "formatted_end": row["end_time"].strftime("%b %d, %Y • %I:%M %p").replace(" 0", " "),
                }

    return render_template(
        "support_self_service.html",
        is_logged_in=bool(session.get("user_id")),
        user_role=session.get("user_role"),
        booking=booking,
        booking_id=booking_id,
    )


@app.route("/support/contact", methods=["GET", "POST"])
def support_contact():
    is_logged_in = bool(session.get("user_id"))
    user_id = session.get("user_id")

    form_data = {
        "full_name": "",
        "email": "",
        "phone": "",
        "booking_id": "",
        "issue": "",
    }

    if is_logged_in:
        form_data["email"] = (session.get("user_email") or "").strip()
        profile_name = ""
        profile_phone = ""

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT full_name, phone
            FROM profiles
            WHERE user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        profile = cur.fetchone()
        cur.close()
        conn.close()

        if profile:
            profile_name = (profile.get("full_name") or "").strip()
            profile_phone = (profile.get("phone") or "").strip()

        if not profile_name and form_data["email"]:
            profile_name = form_data["email"].split("@")[0].replace(".", " ").title()

        form_data["full_name"] = profile_name
        form_data["phone"] = profile_phone

    if request.method == "POST":
        form_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "phone": request.form.get("phone", "").strip(),
            "booking_id": request.form.get("booking_id", "").strip(),
            "issue": request.form.get("issue", "").strip(),
        }

        if not form_data["full_name"] or not form_data["email"] or not form_data["booking_id"] or not form_data["issue"]:
            flash("Please provide your name, email, booking ID, and issue details.", "error")
        else:
            ticket_id = f"SUP-{uuid.uuid4().hex[:8].upper()}"
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                ensure_support_tickets_schema()
                reservation_id = _resolve_reservation_id_from_booking_reference(
                    cur,
                    form_data["booking_id"],
                )
                cur.execute(
                    """
                    INSERT INTO support_tickets (
                        id,
                        ticket_code,
                        user_id,
                        reservation_id,
                        full_name,
                        email,
                        phone,
                        booking_reference,
                        issue,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        ticket_id,
                        user_id if is_logged_in else None,
                        reservation_id,
                        form_data["full_name"],
                        form_data["email"],
                        form_data["phone"] or None,
                        form_data["booking_id"] or None,
                        form_data["issue"],
                        SUPPORT_TICKET_STATUS_OPEN,
                    ),
                )
                conn.commit()
                flash(
                    f"Support request submitted. Ticket ID: {ticket_id}. Our team will contact you soon.",
                    "success",
                )
                return redirect(url_for("support_contact"))
            except psycopg2.Error as exc:
                conn.rollback()
                print("Database error in support_contact:", exc)
                flash("Could not submit your request right now. Please try again shortly.", "error")
            finally:
                cur.close()
                conn.close()

    return render_template(
        "support_contact.html",
        is_logged_in=is_logged_in,
        user_role=session.get("user_role"),
        form_data=form_data,
    )


@app.route("/support/my-tickets")
@login_required(role="driver")
def my_support_tickets():
    user_id = session.get("user_id")
    user_email = (session.get("user_email") or "").strip().lower()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_support_tickets_schema()
        cur.execute(
            """
            SELECT
                st.id,
                st.ticket_code,
                st.booking_reference,
                st.issue,
                st.status,
                st.created_at,
                st.updated_at,
                pl.name AS lot_name,
                ps.label AS slot_label
            FROM support_tickets st
            LEFT JOIN reservations r ON r.id = st.reservation_id
            LEFT JOIN parking_slots ps ON ps.id = r.slot_id
            LEFT JOIN parking_lots pl ON pl.id = ps.lot_id
            WHERE st.user_id = %s
               OR LOWER(st.email) = %s
            ORDER BY st.created_at DESC
            """,
            (user_id, user_email),
        )
        tickets = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    return render_template(
        "my_support_tickets.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        tickets=tickets,
    )


def _fetch_support_tickets_for_management(cur, status_filter):
    status_value = (status_filter or "").strip().upper()
    if status_value and status_value not in SUPPORT_TICKET_STATUSES:
        status_value = ""

    query = """
        SELECT
            st.id,
            st.ticket_code,
            st.full_name,
            st.email,
            st.phone,
            st.booking_reference,
            st.issue,
            st.status,
            st.created_at,
            st.updated_at,
            st.reservation_id,
            pl.name AS lot_name,
            ps.label AS slot_label
        FROM support_tickets st
        LEFT JOIN reservations r ON r.id = st.reservation_id
        LEFT JOIN parking_slots ps ON ps.id = r.slot_id
        LEFT JOIN parking_lots pl ON pl.id = ps.lot_id
    """
    params = []
    if status_value:
        query += " WHERE st.status = %s"
        params.append(status_value)
    query += " ORDER BY st.created_at DESC"
    cur.execute(query, tuple(params))
    rows = cur.fetchall()

    status_counts = {}
    cur.execute(
        """
        SELECT status, COUNT(*)::int AS count
        FROM support_tickets
        GROUP BY status
        """
    )
    for row in cur.fetchall():
        status_counts[row["status"]] = int(row["count"] or 0)

    return rows, status_value, status_counts


def _render_support_tickets_management_page(page_title, back_url, status_filter):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_support_tickets_schema()
        rows, active_status, status_counts = _fetch_support_tickets_for_management(cur, status_filter)
        return render_template(
            "support_tickets_management.html",
            page_title=page_title,
            back_url=back_url,
            user_email=session.get("user_email"),
            user_role=session.get("user_role"),
            tickets=rows,
            active_status=active_status,
            status_counts=status_counts,
            status_options=[
                SUPPORT_TICKET_STATUS_OPEN,
                SUPPORT_TICKET_STATUS_IN_PROGRESS,
                SUPPORT_TICKET_STATUS_RESOLVED,
                SUPPORT_TICKET_STATUS_CLOSED,
            ],
        )
    finally:
        cur.close()
        conn.close()


@app.route("/operator/support-tickets")
@login_required(role="operator")
def operator_support_tickets():
    return _render_support_tickets_management_page(
        page_title="Support ticket management",
        back_url=url_for("operator_dashboard"),
        status_filter=request.args.get("status", ""),
    )


@app.route("/admin/support-tickets")
@login_required(role="admin")
def admin_support_tickets():
    return _render_support_tickets_management_page(
        page_title="Support ticket management",
        back_url=url_for("admin_dashboard"),
        status_filter=request.args.get("status", ""),
    )


@app.route("/support-tickets/<ticket_id>/status", methods=["POST"])
@login_required(role=("operator", "admin"))
def update_support_ticket_status(ticket_id):
    next_status = (request.form.get("status") or "").strip().upper()
    if next_status not in SUPPORT_TICKET_STATUSES:
        flash("Invalid ticket status.", "error")
        target = "operator_support_tickets" if session.get("user_role") == "operator" else "admin_support_tickets"
        return redirect(url_for(target))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_support_tickets_schema()
        cur.execute(
            """
            UPDATE support_tickets
            SET status = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING ticket_code
            """,
            (next_status, ticket_id),
        )
        updated = cur.fetchone()
        if not updated:
            conn.rollback()
            flash("Support ticket not found.", "error")
        else:
            conn.commit()
            flash(f"{updated['ticket_code']} updated to {next_status.replace('_', ' ').title()}.", "success")
    except psycopg2.Error as exc:
        conn.rollback()
        print("Database error in update_support_ticket_status:", exc)
        flash("Could not update ticket status right now.", "error")
    finally:
        cur.close()
        conn.close()

    target = "operator_support_tickets" if session.get("user_role") == "operator" else "admin_support_tickets"
    return redirect(url_for(target))


@app.route("/system-status")
def system_status_page():
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template("system_status.html", checked_at=checked)


@app.route("/account/manage")
@login_required(role="driver")
def manage_account():
    email = (session.get("user_email") or "").strip()
    display_name = email.split("@")[0].replace(".", " ").title() if email else "Driver"
    return render_template(
        "manage_account.html",
        user_email=email,
        user_name=display_name,
    )


@app.route("/account/personal-info")
@login_required(role="driver")
def account_personal_info():
    email = (session.get("user_email") or "").strip()
    display_name = email.split("@")[0].replace(".", " ").title() if email else "Driver"
    return render_template(
        "account_personal_info.html",
        user_email=email,
        user_name=display_name,
    )


@app.route("/account/security", methods=["GET", "POST"])
@login_required(role="driver")
def account_security():
    email = (session.get("user_email") or "").strip()
    display_name = email.split("@")[0].replace(".", " ").title() if email else "Driver"

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all password fields.", "error")
            return render_template(
                "account_security.html",
                user_email=email,
                user_name=display_name,
            )

        if len(new_password) < 6:
            flash("New password must be at least 6 characters long.", "error")
            return render_template(
                "account_security.html",
                user_email=email,
                user_name=display_name,
            )

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return render_template(
                "account_security.html",
                user_email=email,
                user_name=display_name,
            )

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE id = %s
            """,
            (session.get("user_id"),),
        )
        user = cur.fetchone()

        if not user or not check_password_hash(user["password_hash"], current_password):
            cur.close()
            conn.close()
            flash("Current password is incorrect.", "error")
            return render_template(
                "account_security.html",
                user_email=email,
                user_name=display_name,
            )

        new_password_hash = generate_password_hash(new_password)
        cur.execute(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (new_password_hash, session.get("user_id")),
        )
        conn.commit()
        cur.close()
        conn.close()

        flash("Password updated successfully.", "success")
        return redirect(url_for("account_security"))

    return render_template(
        "account_security.html",
        user_email=email,
        user_name=display_name,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required(role="driver")
def profile():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()

        cur.execute("""
            SELECT id
            FROM profiles
            WHERE user_id = %s
        """, (session.get("user_id"),))
        existing_profile = cur.fetchone()

        if existing_profile:
            cur.execute("""
                UPDATE profiles
                SET full_name = %s,
                    phone = %s,
                    updated_at = now()
                WHERE user_id = %s
            """, (full_name, phone, session.get("user_id")))
        else:
            cur.execute("""
                INSERT INTO profiles (user_id, full_name, phone)
                VALUES (%s, %s, %s)
            """, (session.get("user_id"), full_name, phone))

        conn.commit()
        flash("Profile updated successfully.", "success")

    cur.execute("""
        SELECT full_name, phone
        FROM profiles
        WHERE user_id = %s
    """, (session.get("user_id"),))
    profile_data = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "profile.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        profile=profile_data
    )


@app.route("/vehicles", methods=["GET", "POST"])
@login_required(role="driver")
def vehicles():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == "POST":
        plate_number = request.form.get("plate_number", "").strip().upper()
        vehicle_make = request.form.get("vehicle_make", "").strip()
        vehicle_model = request.form.get("vehicle_model", "").strip()
        vehicle_color = request.form.get("vehicle_color", "").strip()
        vehicle_type = request.form.get("vehicle_type", "").strip().lower()

        if not plate_number:
            flash("Plate number is required.", "error")
        elif vehicle_type not in {"compact", "sedan", "suv", "truck"}:
            flash("Please select a valid vehicle type.", "error")
        else:
            try:
                cur.execute("""
                    INSERT INTO vehicles (
                        user_id,
                        plate_number,
                        vehicle_make,
                        vehicle_model,
                        vehicle_color,
                        vehicle_type
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    session.get("user_id"),
                    plate_number,
                    vehicle_make,
                    vehicle_model,
                    vehicle_color,
                    vehicle_type
                ))
                conn.commit()
                flash("Vehicle added successfully.", "success")
            except psycopg2.Error:
                conn.rollback()
                flash("Could not add vehicle. Plate may already exist.", "error")

    cur.execute("""
        SELECT id, plate_number, vehicle_make, vehicle_model, vehicle_color, vehicle_type
        FROM vehicles
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (session.get("user_id"),))
    vehicle_list = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "vehicles.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        vehicles=vehicle_list
    )


@app.route("/delete-vehicle/<vehicle_id>", methods=["POST"])
@login_required(role="driver")
def delete_vehicle(vehicle_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        DELETE FROM vehicles
        WHERE id = %s
          AND user_id = %s
    """, (vehicle_id, session.get("user_id")))

    conn.commit()

    cur.close()
    conn.close()

    flash("Vehicle deleted successfully.", "success")
    return redirect(url_for("vehicles"))

import secrets
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your email address.", "error")
            return render_template("forgot_password.html")

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT id, email
            FROM users
            WHERE email = %s
        """, (email,))
        user = cur.fetchone()

        if user:
            token = secrets.token_urlsafe(32)

            cur.execute("""
                INSERT INTO password_resets (user_id, token, expires_at, used)
                VALUES (%s, %s, now() + interval '1 hour', FALSE)
            """, (user["id"], token))
            conn.commit()

            reset_link = url_for("reset_password", token=token, _external=True)
            print("\n=== PASSWORD RESET LINK ===")
            print(reset_link)
            print("===========================\n")

        cur.close()
        conn.close()

        flash("If an account with that email exists, a reset link has been generated.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT pr.id, pr.user_id, pr.token, pr.expires_at, pr.used
        FROM password_resets pr
        WHERE pr.token = %s
    """, (token,))
    reset_record = cur.fetchone()

    if not reset_record:
        cur.close()
        conn.close()
        flash("Invalid reset link.", "error")
        return redirect(url_for("forgot_password"))

    cur.execute("SELECT now() AS current_time")
    current_time_row = cur.fetchone()
    current_time = current_time_row["current_time"]

    if reset_record["used"]:
        cur.close()
        conn.close()
        flash("This reset link has already been used.", "error")
        return redirect(url_for("forgot_password"))

    if current_time > reset_record["expires_at"]:
        cur.close()
        conn.close()
        flash("This reset link has expired.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or not confirm_password:
            flash("Please fill in both password fields.", "error")
            return render_template("reset_password.html", token=token)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        password_hash = generate_password_hash(password)

        cur.execute("""
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
        """, (password_hash, reset_record["user_id"]))

        cur.execute("""
            UPDATE password_resets
            SET used = TRUE
            WHERE id = %s
        """, (reset_record["id"],))

        conn.commit()
        cur.close()
        conn.close()

        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("login"))

    cur.close()
    conn.close()
    return render_template("reset_password.html", token=token)


@app.route("/toggle-favorite/<lot_id>", methods=["POST"])
@login_required(role="driver")
def toggle_favorite(lot_id):
    next_url = request.form.get("next_url") or url_for("search")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT id
        FROM favorite_locations
        WHERE user_id = %s
          AND parking_lot_id = %s
    """, (session.get("user_id"), lot_id))
    favorite = cur.fetchone()

    if favorite:
        cur.execute("""
            DELETE FROM favorite_locations
            WHERE user_id = %s
              AND parking_lot_id = %s
        """, (session.get("user_id"), lot_id))
        conn.commit()
        flash("Removed from favorites.", "success")
    else:
        cur.execute("""
            INSERT INTO favorite_locations (user_id, parking_lot_id)
            VALUES (%s, %s)
        """, (session.get("user_id"), lot_id))
        conn.commit()
        flash("Added to favorites.", "success")

    cur.close()
    conn.close()

    return redirect(next_url)


@app.route("/favorites")
@login_required(role="driver")
def favorites():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            pl.id,
            pl.name,
            pl.address,
            pl.price_per_hour,
            pl.parking_type,
            COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
                  AND ps.status = 'AVAILABLE'
            ) AS available_slots
        FROM favorite_locations fl
        JOIN parking_lots pl ON fl.parking_lot_id = pl.id
        LEFT JOIN parking_slots ps ON pl.id = ps.lot_id
        WHERE fl.user_id = %s
        GROUP BY pl.id, pl.name, pl.address, pl.price_per_hour, pl.parking_type
        ORDER BY pl.name ASC
    """, (session.get("user_id"),))

    favorite_lots = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "favorites.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        favorite_lots=favorite_lots
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5055)
