-- =====================================================================
-- Order Fulfillment & Late-Shipment Analytics Warehouse
-- Star schema DDL (PostgreSQL dialect)
-- =====================================================================
-- Design notes:
--   * fact_order_events is an EVENT LOG, not a snapshot. Grain = one row
--     per (order, status_event). This is what makes lead-time math,
--     gaps-and-islands, and multi-hop tracing possible.
--   * fact_inventory_snapshot is a true daily snapshot (grain = warehouse
--     x sku x snapshot_date) for turnover calculations.
--   * fact_shipment_cost is grain = one row per shipment leg (a shipment
--     can have multiple legs if it hops between warehouses/hubs).
--   * agg_daily_ontime_performance is a pre-aggregated rollup for
--     dashboard-speed reads (see 04_rollup.sql for the build query).
-- =====================================================================

-- ---------------------------------------------------------------------
-- DIMENSIONS
-- ---------------------------------------------------------------------

CREATE TABLE dim_date (
    date_key            INT PRIMARY KEY,        -- YYYYMMDD
    full_date           DATE NOT NULL,
    day_of_week         SMALLINT NOT NULL,       -- 1=Mon ... 7=Sun
    day_name            VARCHAR(9) NOT NULL,
    week_of_year        SMALLINT NOT NULL,
    month_num           SMALLINT NOT NULL,
    month_name          VARCHAR(9) NOT NULL,
    quarter             SMALLINT NOT NULL,
    year                SMALLINT NOT NULL,
    is_weekend          BOOLEAN NOT NULL
);

CREATE TABLE dim_warehouse (
    warehouse_key        SERIAL PRIMARY KEY,
    warehouse_code        VARCHAR(10) NOT NULL UNIQUE,
    warehouse_name         VARCHAR(100) NOT NULL,
    region                VARCHAR(50) NOT NULL,
    country               VARCHAR(50) NOT NULL,
    capacity_units        INT NOT NULL,          -- max daily throughput, units
    is_hub                BOOLEAN NOT NULL DEFAULT FALSE,  -- true = transfer hub, not just origin
    opened_date            DATE
);

CREATE TABLE dim_carrier (
    carrier_key           SERIAL PRIMARY KEY,
    carrier_code           VARCHAR(10) NOT NULL UNIQUE,
    carrier_name           VARCHAR(100) NOT NULL,
    service_level          VARCHAR(20) NOT NULL,  -- STANDARD, EXPRESS, ECONOMY
    sla_hours              INT NOT NULL,           -- contracted delivery SLA in hours
    mode                  VARCHAR(20) NOT NULL     -- GROUND, AIR, FREIGHT
);

CREATE TABLE dim_product (
    product_key            SERIAL PRIMARY KEY,
    sku                    VARCHAR(20) NOT NULL UNIQUE,
    product_name            VARCHAR(150) NOT NULL,
    category               VARCHAR(50) NOT NULL,
    subcategory             VARCHAR(50),
    weight_kg              NUMERIC(8,3) NOT NULL,
    length_cm               NUMERIC(8,2),
    width_cm                NUMERIC(8,2),
    height_cm                NUMERIC(8,2),
    is_oversized            BOOLEAN NOT NULL DEFAULT FALSE,  -- derived flag, used in root-cause analysis
    unit_cost               NUMERIC(10,2) NOT NULL
);

CREATE TABLE dim_customer (
    customer_key            SERIAL PRIMARY KEY,
    customer_id             VARCHAR(20) NOT NULL UNIQUE,
    segment                 VARCHAR(30) NOT NULL,   -- CONSUMER, SMB, ENTERPRISE
    region                  VARCHAR(50) NOT NULL,
    country                 VARCHAR(50) NOT NULL
);

-- ---------------------------------------------------------------------
-- FACTS
-- ---------------------------------------------------------------------

-- Grain: one row per order per lifecycle status event.
-- An order will have MANY rows here (one per stage it passes through).
CREATE TABLE fact_order_events (
    order_event_key         BIGSERIAL PRIMARY KEY,
    order_id                VARCHAR(20) NOT NULL,      -- natural key, repeats across event rows
    event_seq                INT NOT NULL,               -- 1,2,3... order of events within the order
    event_type               VARCHAR(30) NOT NULL,       -- ORDER_PLACED, PICKED, PACKED, SHIPPED,
                                                          -- ARRIVED_HUB, DEPARTED_HUB, IN_TRANSIT,
                                                          -- OUT_FOR_DELIVERY, DELIVERED, EXCEPTION, RETURNED
    event_timestamp          TIMESTAMP NOT NULL,
    date_key                 INT NOT NULL REFERENCES dim_date(date_key),
    warehouse_key             INT REFERENCES dim_warehouse(warehouse_key),   -- warehouse/hub where event occurred
    carrier_key                INT REFERENCES dim_carrier(carrier_key),      -- null until SHIPPED
    product_key                 INT NOT NULL REFERENCES dim_product(product_key),
    customer_key                 INT NOT NULL REFERENCES dim_customer(customer_key),
    quantity                     INT NOT NULL DEFAULT 1,
    exception_reason              VARCHAR(100),          -- populated only on EXCEPTION rows
    promised_delivery_date         DATE,                 -- carried on every row for convenience
    is_late_flag                    BOOLEAN,              -- populated only on the DELIVERED row
    source_system                    VARCHAR(20) NOT NULL DEFAULT 'WMS'
);

CREATE INDEX idx_foe_order_id       ON fact_order_events(order_id, event_seq);
CREATE INDEX idx_foe_event_type     ON fact_order_events(event_type);
CREATE INDEX idx_foe_date_key       ON fact_order_events(date_key);
CREATE INDEX idx_foe_warehouse      ON fact_order_events(warehouse_key);
CREATE INDEX idx_foe_carrier        ON fact_order_events(carrier_key);
CREATE INDEX idx_foe_timestamp      ON fact_order_events(event_timestamp);

-- Grain: one row per warehouse per SKU per snapshot day.
CREATE TABLE fact_inventory_snapshot (
    inventory_snapshot_key    BIGSERIAL PRIMARY KEY,
    date_key                  INT NOT NULL REFERENCES dim_date(date_key),
    warehouse_key              INT NOT NULL REFERENCES dim_warehouse(warehouse_key),
    product_key                  INT NOT NULL REFERENCES dim_product(product_key),
    beginning_on_hand              INT NOT NULL,
    received_units                  INT NOT NULL DEFAULT 0,
    shipped_units                    INT NOT NULL DEFAULT 0,
    ending_on_hand                    INT NOT NULL,
    UNIQUE (date_key, warehouse_key, product_key)
);

CREATE INDEX idx_fis_wh_sku_date ON fact_inventory_snapshot(warehouse_key, product_key, date_key);

-- Grain: one row per shipment LEG (an order's shipment may have >1 leg
-- if it transfers between warehouses/hubs before final delivery).
CREATE TABLE fact_shipment_cost (
    shipment_cost_key         BIGSERIAL PRIMARY KEY,
    order_id                  VARCHAR(20) NOT NULL,
    leg_seq                    INT NOT NULL,             -- 1 = first leg, 2 = second, etc.
    origin_warehouse_key         INT NOT NULL REFERENCES dim_warehouse(warehouse_key),
    destination_warehouse_key      INT REFERENCES dim_warehouse(warehouse_key), -- null if final leg goes to customer
    carrier_key                     INT NOT NULL REFERENCES dim_carrier(carrier_key),
    date_key                          INT NOT NULL REFERENCES dim_date(date_key),
    freight_cost                       NUMERIC(10,2) NOT NULL,
    fuel_surcharge                       NUMERIC(10,2) NOT NULL DEFAULT 0,
    total_cost                             NUMERIC(10,2) NOT NULL
);

CREATE INDEX idx_fsc_order_id ON fact_shipment_cost(order_id, leg_seq);

-- ---------------------------------------------------------------------
-- ROLLUP / AGGREGATE TABLE (dashboard-speed reads)
-- ---------------------------------------------------------------------
-- Grain: one row per day per warehouse per carrier.
-- Populated by the refresh query in 04_rollup.sql, run nightly (batch)
-- or on a rolling window if intraday freshness is needed.
CREATE TABLE agg_daily_ontime_performance (
    date_key                INT NOT NULL REFERENCES dim_date(date_key),
    warehouse_key             INT NOT NULL REFERENCES dim_warehouse(warehouse_key),
    carrier_key                 INT NOT NULL REFERENCES dim_carrier(carrier_key),
    orders_delivered              INT NOT NULL,
    orders_on_time                  INT NOT NULL,
    orders_late                       INT NOT NULL,
    avg_lead_time_hours                 NUMERIC(8,2) NOT NULL,
    p90_lead_time_hours                   NUMERIC(8,2) NOT NULL,
    on_time_pct                             NUMERIC(5,2) NOT NULL,
    PRIMARY KEY (date_key, warehouse_key, carrier_key)
);
