INSERT INTO parking_lots (name, address)
VALUES
('Downtown Central Garage', '123 Main St, Jersey City, NJ'),
('Riverfront Parking Plaza', '45 Hudson Ave, Hoboken, NJ'),
('City Square Open Lot', '78 Newark St, Hoboken, NJ');

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'A1', true
FROM parking_lots
WHERE name = 'Downtown Central Garage';

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'A2', true
FROM parking_lots
WHERE name = 'Downtown Central Garage';

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'A3', true
FROM parking_lots
WHERE name = 'Downtown Central Garage';

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'B1', true
FROM parking_lots
WHERE name = 'Riverfront Parking Plaza';

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'B2', true
FROM parking_lots
WHERE name = 'Riverfront Parking Plaza';

INSERT INTO parking_slots (lot_id, label, is_active)
SELECT id, 'C1', true
FROM parking_lots
WHERE name = 'City Square Open Lot';