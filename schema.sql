-- Also run by POST /reset, so it starts by clearing the demo data.
drop table if exists runs, invoices, purchase_orders, vendors cascade;

create table vendors (
    id serial primary key,
    name text not null,
    status text not null default 'active'  -- active | on_hold
);

create table purchase_orders (
    po_number text primary key,
    vendor_id int not null references vendors,
    item text not null,
    quantity int not null,
    unit_price numeric(12, 2) not null
);

-- po_number is not a foreign key: invoices can arrive with a wrong or missing PO
create table invoices (
    id serial primary key,
    invoice_number text not null,
    vendor_id int not null references vendors,
    po_number text,
    item text not null,
    quantity int not null,
    unit_price numeric(12, 2) not null,
    status text not null default 'exception',  -- exception | approved | rejected | escalated
    resolution_note text
);

create table runs (
    id uuid primary key,
    invoice_id int not null references invoices,
    status text not null,  -- running | awaiting_approval | done | failed
    steps jsonb not null default '[]',
    pending jsonb,         -- the action waiting for a reviewer, if any
    summary text,          -- the agent's closing note, or the error if the run failed
    tokens int not null default 0,
    latency_ms int not null default 0,
    created_at timestamptz not null default now()
);

insert into vendors (name, status) values
    ('Sharma Steel Traders', 'active'),
    ('Kaveri Packaging', 'active'),
    ('Northline Logistics', 'on_hold');

insert into purchase_orders values
    ('PO-1001', 1, 'TMT bars 12mm', 200, 540.00),
    ('PO-1002', 2, 'Corrugated boxes', 5000, 18.50),
    ('PO-1003', 3, 'Freight Delhi-Pune', 4, 32000.00),
    ('PO-1004', 2, 'Bubble wrap rolls', 120, 410.00);

insert into invoices (invoice_number, vendor_id, po_number, item, quantity, unit_price) values
    ('SST/24/118', 1, 'PO-1001', 'TMT bars 12mm', 200, 545.00),      -- price off by <1%
    ('KP-7731',    2, 'PO-1002', 'Corrugated boxes', 5600, 18.50),   -- billed more than ordered
    ('NL-0092',    3, 'PO-1003', 'Freight Delhi-Pune', 4, 32000.00), -- vendor on hold
    ('KP-7740',    2, 'PO-1009', 'Stretch film', 60, 950.00),        -- PO does not exist
    ('KP-7745',    2, 'PO-1004', 'Bubble wrap rolls', 120, 410.00),
    ('KP-7745',    2, 'PO-1004', 'Bubble wrap rolls', 120, 410.00);  -- same invoice sent twice
