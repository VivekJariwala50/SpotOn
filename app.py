from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from functools import wraps
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__, template_folder="pages")
app.secret_key = os.getenv("Zg6V!5B40&%*+:Y6", "dev-secret")
app.permanent_session_lifetime = timedelta(minutes=30)


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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "driver").strip().lower()

        if not full_name or not email or not password:
            flash("Please fill in all required fields.", "error")
            return render_template("signup.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("signup.html")

        if selected_role not in {"driver", "operator"}:
            flash("Invalid role selected.", "error")
            return render_template("signup.html")

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
            return redirect(url_for("login"))

        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash("An account with this email already exists.", "error")
            return render_template("signup.html")

        finally:
            cur.close()
            conn.close()

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "error")
            return render_template("login.html")

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
                return render_template("login.html")

            session["user_id"] = str(user["id"])
            session["user_email"] = user["email"]
            session["user_role"] = user["role"]

            flash("Login successful.", "success")

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
        return render_template("login.html")

    return render_template("login.html")

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
            pl.price_per_hour AS price_per_hour,
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
            pl.price_per_hour AS price_per_hour,
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
        LIMIT 5
    """, (session.get("user_id"),))
    recent_transactions = cur.fetchall()

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
            duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
            reservation["estimated_cost"] = round(float(reservation["price_per_hour"] or 0) * duration_hours, 2)
        else:
            reservation["estimated_cost"] = None

    for reservation in active_reservations:
        reservation["formatted_start"] = format_dt(reservation["start_time"])
        reservation["formatted_end"] = format_dt(reservation["end_time"])
        reservation["formatted_status"] = format_status(reservation)
        add_edit_fields(reservation)
        add_cost_fields(reservation)

    for reservation in reservation_history:
        reservation["formatted_start"] = format_dt(reservation["start_time"])
        reservation["formatted_end"] = format_dt(reservation["end_time"])
        reservation["formatted_status"] = format_status(reservation)
        add_edit_fields(reservation)
        add_cost_fields(reservation)

    for transaction in recent_transactions:
        transaction["formatted_amount"] = format_currency(transaction["amount"])
        transaction["formatted_created_at"] = format_dt(transaction["created_at"])
        transaction["formatted_type"] = transaction["transaction_type"].replace("_", " ").title() if transaction["transaction_type"] else "—"

    time_options = []
    base_time = datetime.strptime("00:00", "%H:%M")
    for i in range(48):
        t = (base_time + timedelta(minutes=30 * i)).strftime("%H:%M")
        label = datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
        time_options.append({"value": t, "label": label})

    return render_template(
        "dashboard.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        active_reservations=active_reservations,
        reservation_history=reservation_history,
        recent_transactions=recent_transactions,
        time_options=time_options
    )


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
            SELECT r.id, r.user_id, r.slot_id, r.start_time, r.end_time, r.status, pl.price_per_hour
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
        original_duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
        new_duration_hours = (new_end_time - reservation["start_time"]).total_seconds() / 3600
        original_total_cost = round(float(reservation["price_per_hour"] or 0) * original_duration_hours, 2)
        new_total_cost = round(float(reservation["price_per_hour"] or 0) * new_duration_hours, 2)
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
            SELECT r.id, r.user_id, r.slot_id, r.start_time, r.end_time, r.status, pl.price_per_hour
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

        original_duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
        original_total_cost = round(float(reservation["price_per_hour"] or 0) * original_duration_hours, 2)

        new_duration_hours = (end_time - start_time).total_seconds() / 3600
        new_total_cost = round(float(reservation["price_per_hour"] or 0) * new_duration_hours, 2)

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
            COUNT(ps.id) AS total_slots,
            COUNT(ps.id) FILTER (WHERE ps.is_active = TRUE) AS active_slots,
            COUNT(ps.id) FILTER (WHERE ps.is_active = FALSE) AS inactive_slots,
            COUNT(ps.id) FILTER (
                WHERE ps.is_active = TRUE
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
        GROUP BY pl.id, pl.name, pl.address
        ORDER BY pl.name
    """)
    lots = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "operator_inventory.html",
        user_email=session.get("user_email"),
        user_role=session.get("user_role"),
        lots=lots
    )

@app.route("/search")
@login_required(role="driver")
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
    cur.close()
    conn.close()

    parking_lots = []
    for lot in lots:
        parking_lots.append({
            "id": str(lot["id"]),
            "name": lot["name"],
            "location": lot["address"] if lot["address"] else "Address not available",
            "price_per_hour": lot["price_per_hour"] if lot["price_per_hour"] is not None else 0,
            "available_slots": lot["available_slots"] or 0,
            "type": lot["parking_type"] if lot["parking_type"] else "Standard Parking",
            "is_favorite": lot["is_favorite"],
        })

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
            COUNT(ps.id) FILTER (WHERE ps.is_active = TRUE) AS available_slots,
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
        time_options=time_options
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
                user_timezone=user_timezone
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

        duration_hours = (end_time - start_time).total_seconds() / 3600
        total_cost = round(float(slot_record["price_per_hour"] or 0) * duration_hours, 2)

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
            f"Demo payment processed successfully for card ending in {cleaned_card_number[-4:]}. "
            f"Reservation confirmed for vehicle {selected_vehicle['plate_number']}. "
            f"Estimated cost: ${total_cost:.2f}",
            "success"
        )
        return back_to_lot()

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
            pl.price_per_hour
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

    duration_hours = (reservation["end_time"] - reservation["start_time"]).total_seconds() / 3600
    refund_amount = round(float(reservation["price_per_hour"] or 0) * duration_hours, 2)

    cur.execute("""
        UPDATE reservations
        SET status = 'CANCELLED'
        WHERE id = %s
    """, (reservation_id,))
    record_transaction(cur, reservation["id"], session.get("user_id"), "CANCEL_RESERVATION", -refund_amount, "SUCCESS")
    conn.commit()

    cur.close()
    conn.close()

    flash(
        f"Reservation cancelled successfully. Refund of ${refund_amount:.2f} "
        f"will be issued to the same card ending in 1111.",
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
            COUNT(ps.id) FILTER (WHERE ps.is_active = TRUE) AS available_slots
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
