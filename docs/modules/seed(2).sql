-- =========================================
-- Additional parking lots
-- =========================================

INSERT INTO parking_lots (name, address, price_per_hour, parking_type)
VALUES
('Midtown Secure Garage', '200 W 34th St, New York, NY', 15.00, 'Multi-Level Garage'),
('Hoboken Waterfront Parking', '12 Sinatra Dr, Hoboken, NJ', 12.00, 'Covered Parking'),
('Journal Square EV Hub', '50 Sip Ave, Jersey City, NJ', 9.00, 'Open Parking Lot'),
('Newport Covered Parking', '125 River Dr S, Jersey City, NJ', 11.00, 'Covered Parking'),
('Exchange Place Smart Lot', '1 Montgomery St, Jersey City, NJ', 13.00, 'Multi-Level Garage');

-- =========================================
-- Slots for Midtown Secure Garage
-- =========================================

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'M1', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Midtown Secure Garage';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'M2', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Midtown Secure Garage';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'M3', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Midtown Secure Garage';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'M4', TRUE, 'accessible'
FROM parking_lots
WHERE name = 'Midtown Secure Garage';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'M5', FALSE, 'standard'
FROM parking_lots
WHERE name = 'Midtown Secure Garage';

-- =========================================
-- Slots for Hoboken Waterfront Parking
-- =========================================

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'H1', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Hoboken Waterfront Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'H2', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Hoboken Waterfront Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'H3', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Hoboken Waterfront Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'H4', TRUE, 'accessible'
FROM parking_lots
WHERE name = 'Hoboken Waterfront Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'H5', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Hoboken Waterfront Parking';

-- =========================================
-- Slots for Journal Square EV Hub
-- =========================================

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'J1', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Journal Square EV Hub';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'J2', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Journal Square EV Hub';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'J3', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Journal Square EV Hub';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'J4', TRUE, 'accessible'
FROM parking_lots
WHERE name = 'Journal Square EV Hub';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'J5', FALSE, 'standard'
FROM parking_lots
WHERE name = 'Journal Square EV Hub';

-- =========================================
-- Slots for Newport Covered Parking
-- =========================================

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'N1', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Newport Covered Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'N2', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Newport Covered Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'N3', TRUE, 'accessible'
FROM parking_lots
WHERE name = 'Newport Covered Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'N4', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Newport Covered Parking';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'N5', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Newport Covered Parking';

-- =========================================
-- Slots for Exchange Place Smart Lot
-- =========================================

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'E1', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Exchange Place Smart Lot';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'E2', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Exchange Place Smart Lot';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'E3', TRUE, 'ev'
FROM parking_lots
WHERE name = 'Exchange Place Smart Lot';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'E4', TRUE, 'accessible'
FROM parking_lots
WHERE name = 'Exchange Place Smart Lot';

INSERT INTO parking_slots (lot_id, label, is_active, slot_type)
SELECT id, 'E5', TRUE, 'standard'
FROM parking_lots
WHERE name = 'Exchange Place Smart Lot';

-- =========================================
-- Sample reservations
-- Assumes at least one user already exists
-- These create time-based conflicts for testing
-- =========================================

INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
SELECT
    u.id,
    ps.id,
    now() + interval '1 hour',
    now() + interval '2 hours',
    'CONFIRMED'
FROM users u
JOIN parking_slots ps ON ps.label = 'M1'
ORDER BY u.created_at
LIMIT 1;

INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
SELECT
    u.id,
    ps.id,
    now() + interval '30 minutes',
    now() + interval '90 minutes',
    'CONFIRMED'
FROM users u
JOIN parking_slots ps ON ps.label = 'H3'
ORDER BY u.created_at
LIMIT 1;

INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
SELECT
    u.id,
    ps.id,
    now() + interval '2 hours',
    now() + interval '4 hours',
    'CONFIRMED'
FROM users u
JOIN parking_slots ps ON ps.label = 'J2'
ORDER BY u.created_at
LIMIT 1;

INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
SELECT
    u.id,
    ps.id,
    now() + interval '1 day',
    now() + interval '1 day 2 hours',
    'CONFIRMED'
FROM users u
JOIN parking_slots ps ON ps.label = 'N4'
ORDER BY u.created_at
LIMIT 1;

INSERT INTO reservations (user_id, slot_id, start_time, end_time, status)
SELECT
    u.id,
    ps.id,
    now() + interval '3 hours',
    now() + interval '5 hours',
    'CONFIRMED'
FROM users u
JOIN parking_slots ps ON ps.label = 'E3'
ORDER BY u.created_at
LIMIT 1;