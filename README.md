# 📦 Order Fulfillment & Late-Shipment Analytics Warehouse

<div align="center">

### Event-Driven Logistics Data Warehouse Built for Bottleneck Detection, SLA Analysis & Shipment Intelligence

![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Data%20Warehouse-blue?style=for-the-badge&logo=postgresql)
![SQLite](https://img.shields.io/badge/SQLite-Analytics-green?style=for-the-badge&logo=sqlite)
![Python](https://img.shields.io/badge/Python-Data%20Generation-yellow?style=for-the-badge&logo=python)
![SQL](https://img.shields.io/badge/SQL-Advanced%20Analytics-orange?style=for-the-badge)

</div>

---

## 🎯 Business Problem

Late deliveries cost logistics companies millions through customer churn, refund requests, SLA penalties, and operational inefficiencies.

Most analytics projects store orders as a single record with a status column.

That approach makes it nearly impossible to answer questions like:

- Which stage creates the biggest delivery delays?
- Which warehouses consistently miss SLAs?
- Which carriers create the highest exception rates?
- How long do orders spend waiting between fulfillment stages?
- What percentage of delays originate from hubs versus final-mile delivery?

This project solves that problem by modeling the supply chain as an **event-driven warehouse**, where every order lifecycle action is captured and analyzed.

---

# 🚀 Key Highlights

### Event-Sourced Logistics Model

Instead of storing one row per order, the warehouse stores:

```
ORDER_PLACED
      ↓
PICKED
      ↓
PACKED
      ↓
SHIPPED
      ↓
ARRIVED_HUB
      ↓
DEPARTED_HUB
      ↓
IN_TRANSIT
      ↓
OUT_FOR_DELIVERY
      ↓
DELIVERED
```

This enables true process analytics rather than simple status reporting.

---

### Advanced SQL Techniques

This project demonstrates:

- Window Functions (`LAG`, `LEAD`, Running Aggregates)
- Recursive CTEs
- Gaps & Islands Analysis
- Star Schema Modeling
- Root Cause Analysis Queries
- SLA Performance Monitoring
- Event Stream Analytics
- Query Optimization
- Warehouse Aggregation Tables

---

## 📊 Dataset Overview

| Metric | Value |
|----------|----------|
| Orders | 4,000+ |
| Event Records | 28,600+ |
| Warehouses | 6 |
| Carriers | 5 |
| Products | 180 |
| Customers | 600 |
| Delivery Period | 6 Months |
| Return Rate | 1.5% |
| Exception Rate | 7% |
| Multi-Hop Shipments | 5% |

---

# 🏗 Warehouse Architecture

## Dimension Tables

| Table | Purpose |
|---------|---------|
| dim_date | Calendar dimension |
| dim_warehouse | Facility metadata |
| dim_carrier | Carrier & SLA information |
| dim_product | SKU attributes |
| dim_customer | Customer segmentation |

## Fact Tables

| Table | Grain |
|---------|---------|
| fact_order_events | One row per order event |
| fact_inventory_snapshot | Warehouse × SKU × Date |
| fact_shipment_cost | One row per shipment leg |
| agg_daily_ontime_performance | Daily KPI aggregate |

---

# 📂 Repository Structure

```text
logistics-warehouse/
│
├── README.md
├── schema.sql
├── generate_data.py
├── logistics.db
├── dim_warehouse.csv
├── fact_order_events.csv
│
└── analytics/
    ├── bottleneck_analysis.sql
    ├── sla_analysis.sql
    ├── shipment_pathing.sql
    └── warehouse_performance.sql
```

---

# 🔍 Business Questions Answered

### SLA Performance

- Which carriers miss delivery commitments most often?
- Which warehouses contribute most to delays?
- What is the average delivery lead time by region?

### Bottleneck Analysis

- Which fulfillment stage causes the longest wait times?
- Where do orders spend most of their lifecycle?

### Shipment Intelligence

- Which routes require multiple shipment hops?
- Which hubs create the largest delay propagation?

### Operational Efficiency

- Inventory turnover by warehouse
- Return-rate analysis
- Exception trend monitoring

---

# 📈 Realistic Data Challenges Simulated

To make the analysis meaningful, the dataset intentionally includes:

- Missing fulfillment events
- Delivery exceptions
- Weather disruptions
- Capacity constraints
- Address issues
- Multi-hop shipments
- Returned orders
- SLA violations

This creates realistic edge cases commonly encountered in production logistics systems.

---

# ⚡ Example Analytics

### Detect Stuck Shipments

```sql
SELECT *
FROM shipment_events
WHERE hours_since_last_update > 48;
```

### Warehouse Delay Ranking

```sql
SELECT warehouse_name,
       AVG(delay_hours) AS avg_delay
FROM warehouse_delays
GROUP BY warehouse_name
ORDER BY avg_delay DESC;
```

### Carrier SLA Performance

```sql
SELECT carrier_name,
       on_time_rate
FROM carrier_sla_metrics
ORDER BY on_time_rate DESC;
```

---

# 🛠 Tech Stack

| Category | Technology |
|------------|------------|
| Database | PostgreSQL |
| Analytics | SQL |
| Local Storage | SQLite |
| Data Generation | Python |
| Data Modeling | Star Schema |
| Optimization | Indexing & Query Tuning |

---

# 🎯 Skills Demonstrated

- Data Warehousing
- Dimensional Modeling
- Event-Driven Data Architecture
- Advanced SQL
- Logistics Analytics
- KPI Design
- Supply Chain Intelligence
- Query Optimization
- Root Cause Analysis
- Business Intelligence

---

## 💡 Portfolio Value

Unlike typical SQL portfolio projects that focus only on CRUD operations or basic reporting, this project demonstrates how modern analytics teams investigate operational bottlenecks using event-driven data models, dimensional warehousing techniques, and advanced SQL analytics.

It is designed to mirror the type of logistics, supply-chain, and fulfillment analytics challenges solved by Data Analysts, Analytics Engineers, BI Developers, and Data Engineers in production environments.
