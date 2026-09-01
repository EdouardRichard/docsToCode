-- Database schema for e-commerce application
-- Contains DDL statements and DML statements

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT chk_order_amount CHECK (total_amount >= 0)
);

CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);

CREATE VIEW active_orders AS
SELECT id, user_id, total_amount, status
FROM orders
WHERE status = 'pending';

CREATE PROCEDURE calculate_stats(IN start_date DATE)
BEGIN
    SELECT COUNT(*) INTO @total FROM orders WHERE created_at >= start_date;
    SELECT @total AS total_orders;
END;

-- DML statements below (should NOT produce chunks)
INSERT INTO users (username, email, password_hash) VALUES ('admin', 'admin@example.com', 'hash123');
UPDATE orders SET status = 'shipped' WHERE id = 1;
DELETE FROM orders WHERE status = 'cancelled';
