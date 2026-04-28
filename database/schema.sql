-- ============================================================
-- PETROL STATION MANAGEMENT SYSTEM (PSMS)
-- PostgreSQL Schema
-- CS 2005: Database Systems
-- ============================================================

-- Drop tables if they exist (for clean reinitialization)
DROP TABLE IF EXISTS transaction_items CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS shifts CASCADE;
DROP TABLE IF EXISTS pump_fuel_types CASCADE;
DROP TABLE IF EXISTS pumps CASCADE;
DROP TABLE IF EXISTS tanks CASCADE;
DROP TABLE IF EXISTS fuel_types CASCADE;
DROP TABLE IF EXISTS convenience_items CASCADE;
DROP TABLE IF EXISTS employees CASCADE;

-- ============================================================
-- TABLE 1: employees
-- ============================================================
CREATE TABLE employees (
    employee_id     SERIAL PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL CHECK (role IN ('admin', 'cashier')),
    phone           VARCHAR(20),
    hire_date       DATE         NOT NULL DEFAULT CURRENT_DATE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- TABLE 2: fuel_types
-- ============================================================
CREATE TABLE fuel_types (
    fuel_type_id    SERIAL PRIMARY KEY,
    name            VARCHAR(50)      NOT NULL UNIQUE,   -- e.g. 'Petrol (RON 92)', 'Hi-Octane', 'Diesel'
    price_per_liter NUMERIC(8,2)     NOT NULL CHECK (price_per_liter > 0),
    updated_at      TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- TABLE 3: tanks
-- ============================================================
CREATE TABLE tanks (
    tank_id         SERIAL PRIMARY KEY,
    tank_name       VARCHAR(50)  NOT NULL UNIQUE,        -- e.g. 'Tank A'
    fuel_type_id    INT          NOT NULL REFERENCES fuel_types(fuel_type_id),
    capacity_liters NUMERIC(10,2) NOT NULL CHECK (capacity_liters > 0),
    current_level   NUMERIC(10,2) NOT NULL CHECK (current_level >= 0),
    low_stock_alert NUMERIC(10,2) NOT NULL DEFAULT 1000, -- alert threshold in liters
    last_refilled   TIMESTAMP,
    CONSTRAINT current_le_capacity CHECK (current_level <= capacity_liters)
);

-- ============================================================
-- TABLE 4: pumps
-- ============================================================
CREATE TABLE pumps (
    pump_id         SERIAL PRIMARY KEY,
    pump_number     INT         NOT NULL UNIQUE,
    location_label  VARCHAR(50),                         -- e.g. 'Lane 1 - Left'
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE
);

-- ============================================================
-- TABLE 5: pump_fuel_types  (junction: many pumps <-> many fuel types via tanks)
-- ============================================================
CREATE TABLE pump_fuel_types (
    pump_id         INT NOT NULL REFERENCES pumps(pump_id),
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(fuel_type_id),
    tank_id         INT NOT NULL REFERENCES tanks(tank_id),
    PRIMARY KEY (pump_id, fuel_type_id)
);

-- ============================================================
-- TABLE 6: convenience_items
-- ============================================================
CREATE TABLE convenience_items (
    item_id         SERIAL PRIMARY KEY,
    item_name       VARCHAR(100) NOT NULL,
    price           NUMERIC(8,2) NOT NULL CHECK (price > 0),
    stock_quantity  INT          NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    category        VARCHAR(50),
    is_available    BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ============================================================
-- TABLE 7: shifts
-- ============================================================
CREATE TABLE shifts (
    shift_id        SERIAL PRIMARY KEY,
    employee_id     INT          NOT NULL REFERENCES employees(employee_id),
    start_time      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time        TIMESTAMP,
    total_sales     NUMERIC(12,2) NOT NULL DEFAULT 0,
    cash_collected  NUMERIC(12,2) NOT NULL DEFAULT 0,
    card_collected  NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed'))
);

-- ============================================================
-- TABLE 8: transactions
-- ============================================================
CREATE TABLE transactions (
    transaction_id  SERIAL PRIMARY KEY,
    shift_id        INT          NOT NULL REFERENCES shifts(shift_id),
    employee_id     INT          NOT NULL REFERENCES employees(employee_id),
    pump_id         INT          REFERENCES pumps(pump_id),        -- NULL for convenience store only
    fuel_type_id    INT          REFERENCES fuel_types(fuel_type_id),
    liters_dispensed NUMERIC(8,3) CHECK (liters_dispensed >= 0),
    fuel_amount     NUMERIC(10,2) NOT NULL DEFAULT 0,
    convenience_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(10,2) NOT NULL,
    payment_method  VARCHAR(20)  NOT NULL CHECK (payment_method IN ('cash','card')),
    receipt_number  VARCHAR(30)  NOT NULL UNIQUE,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- TABLE 9: transaction_items  (for convenience store line items)
-- ============================================================
CREATE TABLE transaction_items (
    item_line_id    SERIAL PRIMARY KEY,
    transaction_id  INT          NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    item_id         INT          NOT NULL REFERENCES convenience_items(item_id),
    quantity        INT          NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(8,2) NOT NULL,
    subtotal        NUMERIC(10,2) NOT NULL
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_transactions_shift    ON transactions(shift_id);
CREATE INDEX idx_transactions_date     ON transactions(created_at);
CREATE INDEX idx_shifts_employee       ON shifts(employee_id);
CREATE INDEX idx_pump_fuel_pump        ON pump_fuel_types(pump_id);

-- ============================================================
-- TRIGGER: Auto-deduct fuel from tank after transaction insert
-- ============================================================
CREATE OR REPLACE FUNCTION deduct_fuel_from_tank()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.liters_dispensed IS NOT NULL AND NEW.liters_dispensed > 0 THEN
        UPDATE tanks
        SET current_level = current_level - NEW.liters_dispensed
        WHERE tank_id = (
            SELECT pft.tank_id
            FROM pump_fuel_types pft
            WHERE pft.pump_id = NEW.pump_id
              AND pft.fuel_type_id = NEW.fuel_type_id
            LIMIT 1
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_deduct_fuel
AFTER INSERT ON transactions
FOR EACH ROW EXECUTE FUNCTION deduct_fuel_from_tank();

-- ============================================================
-- TRIGGER: Auto-update shift total_sales on new transaction
-- ============================================================
CREATE OR REPLACE FUNCTION update_shift_totals()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE shifts
    SET total_sales    = total_sales + NEW.total_amount,
        cash_collected = cash_collected + CASE WHEN NEW.payment_method = 'cash' THEN NEW.total_amount ELSE 0 END,
        card_collected = card_collected + CASE WHEN NEW.payment_method = 'card' THEN NEW.total_amount ELSE 0 END
    WHERE shift_id = NEW.shift_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_shift
AFTER INSERT ON transactions
FOR EACH ROW EXECUTE FUNCTION update_shift_totals();

-- ============================================================
-- TRIGGER: Deduct convenience item stock on transaction_items insert
-- ============================================================
CREATE OR REPLACE FUNCTION deduct_item_stock()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE convenience_items
    SET stock_quantity = stock_quantity - NEW.quantity
    WHERE item_id = NEW.item_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_deduct_stock
AFTER INSERT ON transaction_items
FOR EACH ROW EXECUTE FUNCTION deduct_item_stock();

-- ============================================================
-- VIEWS
-- ============================================================

-- Daily revenue summary
CREATE OR REPLACE VIEW v_daily_revenue AS
SELECT
    DATE(created_at)          AS sale_date,
    COUNT(*)                  AS total_transactions,
    SUM(fuel_amount)          AS total_fuel_revenue,
    SUM(convenience_amount)   AS total_convenience_revenue,
    SUM(total_amount)         AS grand_total,
    SUM(CASE WHEN payment_method='cash' THEN total_amount ELSE 0 END) AS cash_total,
    SUM(CASE WHEN payment_method='card' THEN total_amount ELSE 0 END) AS card_total
FROM transactions
GROUP BY DATE(created_at)
ORDER BY sale_date DESC;

-- Fuel type revenue breakdown
CREATE OR REPLACE VIEW v_fuel_revenue AS
SELECT
    ft.name                   AS fuel_type,
    COUNT(t.transaction_id)   AS transactions,
    SUM(t.liters_dispensed)   AS total_liters,
    SUM(t.fuel_amount)        AS total_revenue
FROM transactions t
JOIN fuel_types ft ON t.fuel_type_id = ft.fuel_type_id
GROUP BY ft.name;

-- Low stock tanks alert
CREATE OR REPLACE VIEW v_low_stock_tanks AS
SELECT
    tk.tank_name,
    ft.name             AS fuel_type,
    tk.current_level,
    tk.low_stock_alert,
    tk.capacity_liters,
    ROUND((tk.current_level / tk.capacity_liters) * 100, 1) AS fill_percent
FROM tanks tk
JOIN fuel_types ft ON tk.fuel_type_id = ft.fuel_type_id
WHERE tk.current_level <= tk.low_stock_alert;

-- Shift summary per employee
CREATE OR REPLACE VIEW v_shift_summary AS
SELECT
    s.shift_id,
    e.full_name,
    s.start_time,
    s.end_time,
    s.total_sales,
    s.cash_collected,
    s.card_collected,
    s.status,
    COUNT(t.transaction_id) AS num_transactions
FROM shifts s
JOIN employees e ON s.employee_id = e.employee_id
LEFT JOIN transactions t ON s.shift_id = t.shift_id
GROUP BY s.shift_id, e.full_name, s.start_time, s.end_time,
         s.total_sales, s.cash_collected, s.card_collected, s.status;

-- ============================================================
-- SEED DATA
-- ============================================================

-- Admin user (password: admin1)
INSERT INTO employees (full_name, username, password_hash, role, phone, hire_date)
VALUES ('Station Manager', 'admin', 'scrypt:32768:8:1$RmpNjsTAUnRRPqlG$56d78d091263a82d45c223b6996f5c713c4c3b702bb317a771f77d9d53cf5540aaa62df9304d5fe8d572102230061e4c2f7df253e82a10ce7bdd87f05a91ba57', 'admin', '0337-8293449', '2026-04-01');

--Cashier (shaheer - shaheer1, hasan - hasa)
INSERT INTO employees (full_name, username, password_hash, role, phone, hire_date)
VALUES ('Shaheer Aamir', 'shaheer', 'scrypt:32768:8:1$DXOXgF4aA4HjA4sL$9d3c9d3e771113e4142da9ac957155f266a7fc1e4709827ee8b7f4d42ba21e1e6309d2779164f91ef05d2432c57b0108dc2dfd2672c033f0160191fd98d0177c', 'cashier', '0334-9995674', '2026-04-27');
VALUES ('Hasan Ayaz', 'hasan', 'scrypt:32768:8:1$RKjp1P9iiisOPrF2$2880492b1872bff3d38826bfb02425414f7cbf34da308b2310d44c2dc3dd26a46966fa8389953bcd7cf6ef04745818bbeb5f3b27b639ed19638520d03ad7b140', 'cashier', '0330-3760191', '2026-04-28');
VALUES ('Musaddiq Arbi', 'arbi', 'scrypt:32768:8:1$vHfcQIwBzafJX9ql$db8fe5d569c37684380032c121126cf555d47ee7d643753315cfa05ac66de03864992ed6062354c1aeacccee9e0a514cc5368175e933b155c244767f10eaf6ce', 'cashier', '0322-2934631', '2026-04-29');

-- Fuel types
INSERT INTO fuel_types (name, price_per_liter) VALUES
    ('EURO 5 (Premier)',  393.35),
    ('EURO 5 (Octance +)', 420.00),
    ('EURO 5 (Hi-Cetance Diesel)', 380.19),
    ('LPG',  304.12),
    ('CNG',  150.00);

-- Tanks
INSERT INTO tanks (tank_name, fuel_type_id, capacity_liters, current_level, low_stock_alert) VALUES
    ('Tank-A', 1, 20000, 14500, 2000),
    ('Tank-B', 2, 15000, 9200,  1500),
    ('Tank-C', 3, 25000, 18000, 2500),
    ('Tank-D', 4, 15000, 7500,  1500),
    ('Tank-E', 5, 10000, 8800,  1000);

-- Pumps
INSERT INTO pumps (pump_number, location_label) VALUES
    (1, 'Lane 1 - Left'),
    (2, 'Lane 1 - Right'),
    (3, 'Lane 2 - Left'),
    (4, 'Lane 2 - Right'),
    (5, 'Diesel Bay');

-- Pump-Fuel mappings
INSERT INTO pump_fuel_types (pump_id, fuel_type_id, tank_id) VALUES
    (1, 1, 1), (1, 2, 2),   -- Pump 1: Petrol + Hi-Octane
    (2, 1, 1), (2, 2, 2),   -- Pump 2: Petrol + Hi-Octane
    (3, 1, 1), (3, 3, 3),   -- Pump 3: Petrol + Diesel
    (4, 1, 1), (4, 3, 3),   -- Pump 4: Petrol + Diesel
    (5, 3, 3), (5, 4, 4);   -- Pump 5: Diesel + Premium Diesel

-- Convenience items
INSERT INTO convenience_items (item_name, price, stock_quantity, category) VALUES
    ('Nestle Mineral Water 500ml', 80.00,  200, 'Beverages'),
    ('Gatorade Energy Drink', 250.00,   80, 'Beverages'),
    ('Cola Next 500ml',      110.00,  150, 'Beverages'),
    ('Lays Chips (Small)',    60.00,  100, 'Snacks'),
    ('DairyMilk Chocolate',   80.00,   90, 'Snacks'),
    ('Engine Oil 1L',        900.00,   30, 'Automotive'),
    ('AREON Air Freshener', 1250.00,   25, 'Automotive'),
    ('Wiper Fluid',          350.00,   20, 'Automotive');

