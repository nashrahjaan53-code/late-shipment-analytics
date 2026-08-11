"""
Synthetic data generator for the Order Fulfillment & Late-Shipment
Analytics Warehouse.

Produces CSVs (Postgres-COPY-ready) in ../data/csv/ AND loads them
into a local SQLite DB (../data/logistics.db) so you can start
querying immediately without standing up a Postgres server.

Deliberately injected messiness (so the SQL analyses have something
real to find):
  - ~4% of orders are missing an intermediate event (e.g. no PICKED row)
  - ~6% of orders have an EXCEPTION event with a re-route / delay
  - ~8% of "hub" orders take a genuine multi-hop path (2-3 legs)
  - Stage dwell times are randomized with fat tails (some stages take
    far longer than typical -> feeds gaps-and-islands / bottleneck SQL)
  - A subset of orders are "stuck": > 48h with no status update
  - Carrier SLA is sometimes missed -> is_late_flag on DELIVERED rows
"""

import csv
import random
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path

random.seed(42)

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_DIR = BASE_DIR / "data" / "csv"
DB_PATH = BASE_DIR / "data" / "logistics.db"
CSV_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = date(2024, 1, 1)
END_DATE = date(2024, 6, 30)          # 6 months of history
N_ORDERS = 4000
N_CUSTOMERS = 600
N_PRODUCTS = 180

# ---------------------------------------------------------------------
# DIM: date
# ---------------------------------------------------------------------
def build_dim_date():
    rows = []
    d = START_DATE
    while d <= END_DATE:
        rows.append({
            "date_key": int(d.strftime("%Y%m%d")),
            "full_date": d.isoformat(),
            "day_of_week": d.isoweekday(),
            "day_name": d.strftime("%A"),
            "week_of_year": int(d.strftime("%V")),
            "month_num": d.month,
            "month_name": d.strftime("%B"),
            "quarter": (d.month - 1) // 3 + 1,
            "year": d.year,
            "is_weekend": d.isoweekday() in (6, 7),
        })
        d += timedelta(days=1)
    return rows

# ---------------------------------------------------------------------
# DIM: warehouse
# ---------------------------------------------------------------------
WAREHOUSES = [
    ("WH-ATL", "Atlanta DC", "Southeast", "USA", 12000, False),
    ("WH-DAL", "Dallas DC", "South", "USA", 15000, False),
    ("WH-CHI", "Chicago Hub", "Midwest", "USA", 20000, True),
    ("WH-LAX", "Los Angeles DC", "West", "USA", 18000, False),
    ("WH-NJ",  "New Jersey Hub", "Northeast", "USA", 22000, True),
    ("WH-PHX", "Phoenix DC", "Southwest", "USA", 9000, False),
]

def build_dim_warehouse():
    rows = []
    for i, (code, name, region, country, cap, is_hub) in enumerate(WAREHOUSES, start=1):
        rows.append({
            "warehouse_key": i, "warehouse_code": code, "warehouse_name": name,
            "region": region, "country": country, "capacity_units": cap,
            "is_hub": int(is_hub), "opened_date": "2019-01-01",
        })
    return rows

# ---------------------------------------------------------------------
# DIM: carrier
# ---------------------------------------------------------------------
CARRIERS = [
    ("UPS-GRD", "UPS Ground", "STANDARD", 96, "GROUND"),
    ("UPS-EXP", "UPS Express", "EXPRESS", 24, "AIR"),
    ("FDX-GRD", "FedEx Ground", "STANDARD", 96, "GROUND"),
    ("FDX-2DAY", "FedEx 2Day", "EXPRESS", 48, "AIR"),
    ("REGN-FRT", "Regional Freight Co", "ECONOMY", 120, "FREIGHT"),
]

def build_dim_carrier():
    rows = []
    for i, (code, name, svc, sla, mode) in enumerate(CARRIERS, start=1):
        rows.append({
            "carrier_key": i, "carrier_code": code, "carrier_name": name,
            "service_level": svc, "sla_hours": sla, "mode": mode,
        })
    return rows

# ---------------------------------------------------------------------
# DIM: product
# ---------------------------------------------------------------------
CATEGORIES = {
    "Electronics": ["Audio", "Computers", "Accessories"],
    "Home & Garden": ["Furniture", "Kitchen", "Decor"],
    "Apparel": ["Mens", "Womens", "Kids"],
    "Sports": ["Fitness", "Outdoor", "Team Sports"],
    "Office": ["Supplies", "Furniture", "Electronics"],
}

def build_dim_product():
    rows = []
    for i in range(1, N_PRODUCTS + 1):
        cat = random.choice(list(CATEGORIES.keys()))
        subcat = random.choice(CATEGORIES[cat])
        weight = round(random.lognormvariate(0.5, 1.0), 3)
        length = round(random.uniform(5, 120), 2)
        width = round(random.uniform(5, 80), 2)
        height = round(random.uniform(5, 80), 2)
        is_oversized = (weight > 15 or length > 90)
        rows.append({
            "product_key": i, "sku": f"SKU-{i:05d}",
            "product_name": f"{subcat} Item {i}", "category": cat,
            "subcategory": subcat, "weight_kg": weight, "length_cm": length,
            "width_cm": width, "height_cm": height,
            "is_oversized": int(is_oversized),
            "unit_cost": round(random.uniform(8, 450), 2),
        })
    return rows

# ---------------------------------------------------------------------
# DIM: customer
# ---------------------------------------------------------------------
REGIONS = ["Northeast", "Southeast", "Midwest", "South", "West", "Southwest"]
SEGMENTS = ["CONSUMER", "SMB", "ENTERPRISE"]

def build_dim_customer():
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        rows.append({
            "customer_key": i, "customer_id": f"CUST-{i:06d}",
            "segment": random.choices(SEGMENTS, weights=[0.7, 0.22, 0.08])[0],
            "region": random.choice(REGIONS), "country": "USA",
        })
    return rows

# ---------------------------------------------------------------------
# FACT: order_events  (+ derived fact_shipment_cost legs)
# ---------------------------------------------------------------------
EVENT_FLOW = ["ORDER_PLACED", "PICKED", "PACKED", "SHIPPED", "IN_TRANSIT",
              "OUT_FOR_DELIVERY", "DELIVERED"]
HUB_INSERT_AFTER_SHIPPED = ["ARRIVED_HUB", "DEPARTED_HUB"]
EXCEPTION_REASONS = [
    "ADDRESS_ISSUE", "WEATHER_DELAY", "DAMAGED_IN_TRANSIT",
    "CARRIER_MISROUTE", "CUSTOMS_HOLD", "CAPACITY_OVERFLOW",
]

def random_ts_between(d1: date, d2: date):
    delta = (d2 - d1).days
    rand_days = random.randint(0, max(delta, 0))
    base = datetime.combine(d1 + timedelta(days=rand_days), datetime.min.time())
    return base + timedelta(hours=random.uniform(6, 20))

def stage_dwell_hours(event_type, is_late_order, warehouse_is_hub):
    """Fat-tailed dwell time per stage, worse for orders we've decided run late."""
    base = {
        "ORDER_PLACED": 0,
        "PICKED": random.gammavariate(2, 3),          # ~6h typical
        "PACKED": random.gammavariate(2, 1.5),          # ~3h typical
        "SHIPPED": random.gammavariate(2, 6),            # ~12h typical
        "ARRIVED_HUB": random.gammavariate(2, 4),
        "DEPARTED_HUB": random.gammavariate(2, 3),
        "IN_TRANSIT": random.gammavariate(3, 10),          # ~30h typical
        "OUT_FOR_DELIVERY": random.gammavariate(2, 2),
        "DELIVERED": random.gammavariate(2, 1.5),
    }[event_type]
    if is_late_order:
        base *= random.uniform(1.8, 4.5)
    if warehouse_is_hub and event_type in ("IN_TRANSIT", "ARRIVED_HUB"):
        base *= random.uniform(1.1, 1.6)
    return max(base, 0.25)

def build_facts(dim_warehouse, dim_carrier, dim_product, dim_customer, dim_date_rows):
    order_events = []
    shipment_costs = []
    date_key_lookup = {r["date_key"]: r for r in dim_date_rows}
    valid_dates = sorted(date_key_lookup.keys())

    for order_num in range(1, N_ORDERS + 1):
        order_id = f"ORD-{order_num:07d}"
        origin_wh = random.choice(dim_warehouse)
        carrier = random.choice(dim_carrier)
        product = random.choice(dim_product)
        customer = random.choice(dim_customer)

        order_start = random_ts_between(START_DATE, END_DATE - timedelta(days=10))

        # Decide order "personality"
        is_late_order = random.random() < 0.22          # ~22% of orders run late
        has_exception = random.random() < 0.06           # ~6% hit an exception
        missing_event = random.random() < 0.04            # ~4% missing a mid-flow event
        is_multihop = origin_wh["is_hub"] == 0 and random.random() < 0.08  # 8% multi-hop
        is_stuck = random.random() < 0.03                  # ~3% stuck 48h+ with no update
        is_returned = random.random() < 0.015                # ~1.5% end in RETURNED

        flow = EVENT_FLOW.copy()
        if is_multihop:
            # insert hub arrival/departure after SHIPPED
            idx = flow.index("SHIPPED") + 1
            flow = flow[:idx] + HUB_INSERT_AFTER_SHIPPED + flow[idx:]

        if missing_event and len(flow) > 4:
            drop_idx = random.choice(range(1, len(flow) - 2))  # never drop ORDER_PLACED/DELIVERED
            flow = flow[:drop_idx] + flow[drop_idx + 1:]

        exception_insert_idx = None
        if has_exception:
            exception_insert_idx = random.randint(2, max(len(flow) - 2, 2))

        ts = order_start
        seq = 0
        promised = (order_start + timedelta(hours=carrier["sla_hours"])).date().isoformat()
        leg_seq = 1
        current_wh = origin_wh
        events_for_order = []

        for i, ev in enumerate(flow):
            if ev != "ORDER_PLACED":
                gap = stage_dwell_hours(ev, is_late_order, current_wh["is_hub"] == 1)
                if is_stuck and ev == "IN_TRANSIT":
                    gap += random.uniform(48, 96)   # inject a stuck gap
                ts = ts + timedelta(hours=gap)

            seq += 1
            date_key = int(ts.date().strftime("%Y%m%d"))
            if date_key not in date_key_lookup:
                date_key = valid_dates[-1]

            wh_key = current_wh["warehouse_key"] if ev not in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED") else current_wh["warehouse_key"]
            carrier_key = carrier["carrier_key"] if ev in ("SHIPPED", "ARRIVED_HUB", "DEPARTED_HUB", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED") else None

            events_for_order.append({
                "order_id": order_id, "event_seq": seq, "event_type": ev,
                "event_timestamp": ts.isoformat(sep=" "), "date_key": date_key,
                "warehouse_key": wh_key, "carrier_key": carrier_key,
                "product_key": product["product_key"], "customer_key": customer["customer_key"],
                "quantity": random.choices([1, 2, 3], weights=[0.75, 0.18, 0.07])[0],
                "exception_reason": None, "promised_delivery_date": promised,
                "is_late_flag": None, "source_system": "WMS",
            })

            if ev == "DEPARTED_HUB":
                # log the completed leg (origin -> hub) as a shipment cost row
                shipment_costs.append(make_leg(order_id, leg_seq, origin_wh["warehouse_key"],
                                                current_wh["warehouse_key"], carrier, date_key))
                leg_seq += 1
                origin_wh = current_wh  # next leg originates from the hub

            if has_exception and i == exception_insert_idx:
                exc_ts = ts + timedelta(hours=random.uniform(1, 6))
                exc_date_key = int(exc_ts.date().strftime("%Y%m%d"))
                if exc_date_key not in date_key_lookup:
                    exc_date_key = valid_dates[-1]
                seq += 1
                events_for_order.append({
                    "order_id": order_id, "event_seq": seq, "event_type": "EXCEPTION",
                    "event_timestamp": exc_ts.isoformat(sep=" "), "date_key": exc_date_key,
                    "warehouse_key": current_wh["warehouse_key"], "carrier_key": carrier["carrier_key"],
                    "product_key": product["product_key"], "customer_key": customer["customer_key"],
                    "quantity": 1, "exception_reason": random.choice(EXCEPTION_REASONS),
                    "promised_delivery_date": promised, "is_late_flag": None,
                    "source_system": "WMS",
                })
                ts = exc_ts + timedelta(hours=random.uniform(4, 30))  # exception adds delay

        if is_returned and events_for_order[-1]["event_type"] == "DELIVERED":
            ret_ts = ts + timedelta(hours=random.uniform(24, 240))
            seq += 1
            ret_date_key = int(ret_ts.date().strftime("%Y%m%d"))
            if ret_date_key not in date_key_lookup:
                ret_date_key = valid_dates[-1]
            events_for_order.append({
                "order_id": order_id, "event_seq": seq, "event_type": "RETURNED",
                "event_timestamp": ret_ts.isoformat(sep=" "), "date_key": ret_date_key,
                "warehouse_key": current_wh["warehouse_key"], "carrier_key": carrier["carrier_key"],
                "product_key": product["product_key"], "customer_key": customer["customer_key"],
                "quantity": 1, "exception_reason": None,
                "promised_delivery_date": promised, "is_late_flag": None,
                "source_system": "WMS",
            })

        # Flag lateness on the DELIVERED row
        for row in events_for_order:
            if row["event_type"] == "DELIVERED":
                delivered_date = datetime.fromisoformat(row["event_timestamp"]).date()
                row["is_late_flag"] = int(delivered_date.isoformat() > row["promised_delivery_date"])

        # final leg cost: last hub/origin -> customer
        last_wh = current_wh["warehouse_key"]
        final_date_key = events_for_order[-1]["date_key"]
        shipment_costs.append(make_leg(order_id, leg_seq, last_wh, None, carrier, final_date_key))

        order_events.extend(events_for_order)

    return order_events, shipment_costs

def make_leg(order_id, leg_seq, origin_wh_key, dest_wh_key, carrier, date_key):
    base_rate = {"GROUND": 6.5, "AIR": 22.0, "FREIGHT": 14.0}[carrier["mode"]]
    freight = round(base_rate * random.uniform(0.8, 1.6), 2)
    fuel = round(freight * random.uniform(0.05, 0.18), 2)
    return {
        "order_id": order_id, "leg_seq": leg_seq,
        "origin_warehouse_key": origin_wh_key, "destination_warehouse_key": dest_wh_key,
        "carrier_key": carrier["carrier_key"], "date_key": date_key,
        "freight_cost": freight, "fuel_surcharge": fuel,
        "total_cost": round(freight + fuel, 2),
    }

# ---------------------------------------------------------------------
# FACT: inventory snapshot (daily, per warehouse x product, sampled)
# ---------------------------------------------------------------------
def build_inventory_snapshot(dim_warehouse, dim_product, dim_date_rows):
    rows = []
    # Sample products per warehouse to keep volume sane (every product isn't
    # stocked everywhere) -- ~60% of products per warehouse
    for wh in dim_warehouse:
        stocked_products = random.sample(dim_product, k=int(len(dim_product) * 0.6))
        for prod in stocked_products:
            on_hand = random.randint(50, 800)
            for d_row in dim_date_rows:
                received = random.choices([0, random.randint(10, 120)], weights=[0.7, 0.3])[0]
                shipped = random.randint(0, min(on_hand + received, 60))
                ending = max(on_hand + received - shipped, 0)
                rows.append({
                    "date_key": d_row["date_key"], "warehouse_key": wh["warehouse_key"],
                    "product_key": prod["product_key"], "beginning_on_hand": on_hand,
                    "received_units": received, "shipped_units": shipped,
                    "ending_on_hand": ending,
                })
                on_hand = ending
    return rows

# ---------------------------------------------------------------------
# WRITE CSVs + LOAD SQLITE
# ---------------------------------------------------------------------
def write_csv(rows, filename):
    if not rows:
        return
    path = CSV_DIR / filename
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {len(rows):>7,} rows -> {path.name}")

def main():
    print("Building dimensions...")
    dim_date_rows = build_dim_date()
    dim_warehouse = build_dim_warehouse()
    dim_carrier = build_dim_carrier()
    dim_product = build_dim_product()
    dim_customer = build_dim_customer()

    print("Building fact_order_events (this is the slow one)...")
    order_events, shipment_costs = build_facts(dim_warehouse, dim_carrier, dim_product,
                                                 dim_customer, dim_date_rows)

    print("Building fact_inventory_snapshot (sampled)...")
    # keep snapshot volume reasonable: sample every 3rd day instead of daily
    sampled_dates = dim_date_rows[::3]
    inventory_snapshot = build_inventory_snapshot(dim_warehouse, dim_product, sampled_dates)

    print("Writing CSVs...")
    write_csv(dim_date_rows, "dim_date.csv")
    write_csv(dim_warehouse, "dim_warehouse.csv")
    write_csv(dim_carrier, "dim_carrier.csv")
    write_csv(dim_product, "dim_product.csv")
    write_csv(dim_customer, "dim_customer.csv")
    write_csv(order_events, "fact_order_events.csv")
    write_csv(shipment_costs, "fact_shipment_cost.csv")
    write_csv(inventory_snapshot, "fact_inventory_snapshot.csv")

    print("Loading into SQLite for local querying...")
    load_sqlite({
        "dim_date": dim_date_rows, "dim_warehouse": dim_warehouse,
        "dim_carrier": dim_carrier, "dim_product": dim_product,
        "dim_customer": dim_customer, "fact_order_events": order_events,
        "fact_shipment_cost": shipment_costs, "fact_inventory_snapshot": inventory_snapshot,
    })
    print(f"\nDone. SQLite DB at {DB_PATH}")

def load_sqlite(tables: dict):
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for name, rows in tables.items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        col_defs = ", ".join(f'"{c}"' for c in cols)
        cur.execute(f'CREATE TABLE {name} ({col_defs})')
        placeholders = ", ".join("?" for _ in cols)
        cur.executemany(
            f'INSERT INTO {name} ({col_defs}) VALUES ({placeholders})',
            [tuple(r[c] for c in cols) for r in rows]
        )
        # helpful indexes for the interesting tables
        if name == "fact_order_events":
            cur.execute('CREATE INDEX idx_foe_order ON fact_order_events(order_id, event_seq)')
            cur.execute('CREATE INDEX idx_foe_type ON fact_order_events(event_type)')
        if name == "fact_inventory_snapshot":
            cur.execute('CREATE INDEX idx_fis ON fact_inventory_snapshot(warehouse_key, product_key, date_key)')
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
