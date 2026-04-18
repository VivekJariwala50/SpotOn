from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from functools import wraps
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

app = Flask(__name__, template_folder="pages")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
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


def build_booking_alias(booking_id):
    raw_value = "".join(ch for ch in str(booking_id or "").upper() if ch.isalnum())
    if len(raw_value) < 10:
        return ""
    return f"SP-{raw_value[:6]}{raw_value[-4:]}"


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
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        # Supabase requires TLS; append sslmode if the URL does not already set it.
        if "supabase.co" in database_url and "sslmode=" not in database_url:
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


@app.before_request
def _ensure_db_integrity_once():
    if request.endpoint == "static":
        return
    if app.config.get("_db_integrity_constraints_ready"):
        return
    ensure_db_integrity_constraints()
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

    cur.execute("""
        SELECT
            r.id,
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
          AND (
              r.status <> 'CONFIRMED'
              OR r.end_time <= now()
          )
        ORDER BY r.start_time DESC
    """, (session.get("user_id"),))
    reservation_history = cur.fetchall()

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
                for reservation in (active_reservations + reservation_history)
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
              AND r.status = 'CONFIRMED'
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
              AND r.status = 'CONFIRMED'
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

@app.route("/operator-dashboard")
@login_required(role="operator")
def operator_dashboard():
    return render_template(
        "operator_dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role")
    )


@app.route("/admin-dashboard")
@login_required(role="admin")
def admin_dashboard():
    return render_template(
        "admin_dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role")
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
                        AND r.status = 'CONFIRMED'
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
            id,
            lot_id,
            label,
            slot_type,
            status,
            is_active
        FROM parking_slots
        ORDER BY lot_id, label
        """
    )
    slots = cur.fetchall()

    slots_by_lot = {}
    for slot in slots:
        slots_by_lot.setdefault(slot["lot_id"], []).append(slot)

    for lot in lots:
        lot["slots"] = slots_by_lot.get(lot["id"], [])

    pricing_overrides_by_lot = load_pricing_overrides_for_lots(
        cur,
        [lot["id"] for lot in lots]
    )
    for lot in lots:
        override_rows = []
        for (slot_type, vehicle_type), price in sorted(
            pricing_overrides_by_lot.get(lot["id"], {}).items(),
            key=lambda item: (item[0][0], item[0][1])
        ):
            override_rows.append(
                {
                    "slot_type": slot_type,
                    "vehicle_type": vehicle_type,
                    "price_per_hour": price,
                }
            )
        lot["pricing_overrides"] = override_rows

    cur.close()
    conn.close()

    return render_template(
        "operator_inventory.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        lots=lots
    )


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
        flash("Pricing override saved.", "success")
        return redirect(url_for("operator_inventory"))
    except psycopg2.Error as e:
        conn.rollback()
        print("Database error in update_lot_price_override:", e)
        flash("Could not save pricing override right now.", "error")
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

@app.route("/search")
def search():
    location = request.args.get("location", "").strip()

    start_date = request.args.get("start_date", "").strip()
    start_time_only = request.args.get("start_time_only", "").strip()
    end_date = request.args.get("end_date", "").strip()
    end_time_only = request.args.get("end_time_only", "").strip()

    parking_type = request.args.get("parking_type", "").strip()
    slot_type = request.args.get("slot_type", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    vehicle_type = request.args.get("vehicle_type", "").strip().lower()

    quick_day = request.args.get("quick_day", "today").strip().lower()
    quick_duration = request.args.get("quick_duration", "60").strip()

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

    order_clause = "pl.created_at ASC"
    if sort_by == "price_asc":
        order_clause = "pl.price_per_hour ASC NULLS LAST"
    elif sort_by == "price_desc":
        order_clause = "pl.price_per_hour DESC NULLS LAST"
    elif sort_by == "available_desc":
        order_clause = "available_slots DESC, pl.created_at ASC"

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

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
                          AND r.status = 'CONFIRMED'
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
                      AND r.status = 'CONFIRMED'
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
                session.get("user_id"),
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
                session.get("user_id"),
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
    cur.close()
    conn.close()

    parking_lots = []
    for lot in lots:
        parking_lots.append({
            "id": str(lot["id"]),
            "name": lot["name"],
            "location": lot["address"] if lot["address"] else "Address not available",
            "price_per_hour": resolve_effective_price(
                lot["price_per_hour"],
                pricing_overrides_by_lot.get(lot["id"], {}),
                slot_type,
                vehicle_type
            ),
            "available_slots": lot["available_slots"] or 0,
            "type": lot["parking_type"] if lot["parking_type"] else "Standard Parking",
            "is_favorite": lot["is_favorite"],
        })

    if sort_by == "price_asc":
        parking_lots.sort(key=lambda lot: lot["price_per_hour"])
    elif sort_by == "price_desc":
        parking_lots.sort(key=lambda lot: lot["price_per_hour"], reverse=True)

    time_options = []
    base_time = datetime.strptime("00:00", "%H:%M")
    for i in range(48):
        t = (base_time + timedelta(minutes=30 * i)).strftime("%H:%M")
        label = datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
        time_options.append({"value": t, "label": label})

    return render_template(
        "search.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        parking_lots=parking_lots,
        location=location,
        start_date=start_date,
        start_time_only=start_time_only,
        end_date=end_date,
        end_time_only=end_time_only,
        combined_start_time=start_time_str,
        combined_end_time=end_time_str,
        parking_type=parking_type,
        slot_type=slot_type,
        vehicle_type=vehicle_type,
        sort_by=sort_by,
        quick_day=quick_day,
        quick_duration=quick_duration,
        time_options=time_options,
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
                      AND r.status = 'CONFIRMED'
                      AND tstzrange(r.start_time, r.end_time, '[)') &&
                          tstzrange(%s, %s, '[)')
                ) AS is_available_now,
                EXISTS (
                    SELECT 1
                    FROM reservations r
                    WHERE r.slot_id = ps.id
                      AND r.user_id = %s
                      AND r.status = 'CONFIRMED'
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

    cleaned_card_number = "".join(ch for ch in card_number if ch.isdigit())
    cleaned_cvv = "".join(ch for ch in cvv if ch.isdigit())

    if not cardholder_name or not cleaned_card_number or not expiry or not cleaned_cvv:
        flash("Please complete the payment details before reserving the slot.", "error")
        return back_to_lot()

    if cleaned_card_number != "8111111111111111":
        flash("For demo payment, use card number 8111 1111 1111 1111.", "error")
        return back_to_lot()

    if cleaned_cvv != "007":
        flash("For demo payment, use CVV 007.", "error")
        return back_to_lot()

    if len(expiry) != 5 or expiry[2] != "/":
        flash("Please enter expiry in MM/YY format.", "error")
        return back_to_lot()

    normalized_promo_code = (promo_code or "").strip().upper()
    promo_result = apply_promo_discount(0, promo_code)
    if promo_code and not promo_result["is_applied"]:
        flash(
            f"Invalid promo code. Use {FIRST_BOOKING_PROMO_CODE} or {PACE_PROMO_CODE}.",
            "error",
        )
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
        flash("Start time cannot be earlier than the current time.", "error")
        return back_to_lot()

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

        if normalized_promo_code == FIRST_BOOKING_PROMO_CODE:
            cur.execute(
                """
                SELECT COUNT(*) AS reservation_count
                FROM reservations
                WHERE user_id = %s
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

        cur.execute(
            """
            INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
            VALUES (%s, %s, %s, %s, 'CONFIRMED')
            RETURNING id
            """,
            (session.get("user_id"), slot_id, start_time, end_time)
        )
        inserted_reservation = cur.fetchone()
        record_transaction(
            cur,
            inserted_reservation["id"],
            session.get("user_id"),
            "CREATE_RESERVATION",
            total_cost,
            "SUCCESS"
        )
        conn.commit()

        flash(
            f"Demo payment processed for card ending in {cleaned_card_number[-4:]}. "
            f"Vehicle {selected_vehicle['plate_number']}."
            + (
                f" Promo {promo_result['applied_code']} applied "
                f"(-${promo_result['discount_amount']:.2f})."
                if promo_result["is_applied"]
                else ""
            ),
            "success",
        )
        return redirect(
            url_for(
                "booking_receipt",
                reservation_id=inserted_reservation["id"],
                user_timezone=user_timezone,
                applied_promo_code=promo_result["applied_code"],
            )
        )

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

        if not form_data["full_name"] or not form_data["email"] or not form_data["issue"]:
            flash("Please provide your name, email, and issue details.", "error")
        else:
            ticket_id = f"SUP-{uuid.uuid4().hex[:8].upper()}"
            flash(
                f"Support request submitted. Ticket ID: {ticket_id}. Our team will contact you soon.",
                "success",
            )
            return redirect(url_for("support_contact"))

    return render_template(
        "support_contact.html",
        is_logged_in=is_logged_in,
        user_role=session.get("user_role"),
        form_data=form_data,
    )


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
