CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('driver', 'operator', 'admin')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE parking_lots (
    id SERIAL PRIMARY KEY,
    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name VARCHAR(150) NOT NULL,
    location VARCHAR(255) NOT NULL,
    parking_type VARCHAR(50) NOT NULL,
    price_per_hour NUMERIC(10,2) NOT NULL CHECK (price_per_hour >= 0),
    total_slots INTEGER NOT NULL CHECK (total_slots >= 0),
    available_slots INTEGER NOT NULL CHECK (available_slots >= 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE parking_slots (
    id SERIAL PRIMARY KEY,
    lot_id INTEGER NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
    slot_number VARCHAR(20) NOT NULL,
    slot_type VARCHAR(50) DEFAULT 'standard',
    is_available BOOLEAN DEFAULT TRUE,
    UNIQUE (lot_id, slot_number)
);

CREATE TABLE reservations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lot_id INTEGER NOT NULL REFERENCES parking_lots(id) ON DELETE CASCADE,
    slot_id INTEGER NOT NULL REFERENCES parking_slots(id) ON DELETE CASCADE,
    reservation_start TIMESTAMP NOT NULL,
    reservation_end TIMESTAMP NOT NULL,
    total_cost NUMERIC(10,2) NOT NULL CHECK (total_cost >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'cancelled', 'completed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (reservation_end > reservation_start)
);
