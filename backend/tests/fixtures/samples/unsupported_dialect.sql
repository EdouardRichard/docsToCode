-- Schema with PostgreSQL-specific dialect features

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_products_metadata ON products USING GIN (metadata);

-- PostgreSQL-specific: CREATE EXTENSION (private dialect)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- PostgreSQL-specific: materialized view
CREATE MATERIALIZED VIEW product_summary AS
SELECT id, name, price FROM products WHERE price > 100;
