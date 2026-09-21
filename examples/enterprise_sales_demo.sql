-- Synthetic enterprise-sales data for AgentBI 2.0 ontology validation.
-- This data is deterministic and fictional.  It must never be represented as
-- production business data or used for business decisions.

CREATE TABLE organizations (
  organization_id INTEGER PRIMARY KEY,
  organization_name TEXT NOT NULL UNIQUE,
  region TEXT NOT NULL
);

CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  customer_code TEXT NOT NULL UNIQUE,
  customer_name TEXT NOT NULL,
  region TEXT NOT NULL,
  customer_tier TEXT NOT NULL,
  created_at DATE NOT NULL
);

CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  product_name TEXT NOT NULL,
  category_l1 TEXT NOT NULL,
  category_l2 TEXT NOT NULL,
  list_price NUMERIC(12, 2) NOT NULL CHECK (list_price > 0)
);

CREATE TABLE sales_orders (
  order_id INTEGER PRIMARY KEY,
  order_number TEXT NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  organization_id INTEGER NOT NULL REFERENCES organizations(organization_id),
  order_date DATE NOT NULL,
  order_status TEXT NOT NULL CHECK (order_status IN ('confirmed', 'cancelled')),
  currency_code TEXT NOT NULL DEFAULT 'CNY'
);

CREATE TABLE sales_order_lines (
  order_id INTEGER NOT NULL REFERENCES sales_orders(order_id),
  line_number INTEGER NOT NULL,
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price > 0),
  discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
  net_amount NUMERIC(12, 2) NOT NULL CHECK (net_amount >= 0),
  PRIMARY KEY (order_id, line_number)
);

INSERT INTO organizations (organization_id, organization_name, region) VALUES
  (1, '华东销售一部', '华东'), (2, '华东销售二部', '华东'),
  (3, '华南销售部', '华南'), (4, '华北销售部', '华北'),
  (5, '华中销售部', '华中'), (6, '西南销售部', '西南'),
  (7, '西北销售部', '西北'), (8, '东北销售部', '东北');

INSERT INTO customers (customer_id, customer_code, customer_name, region, customer_tier, created_at)
SELECT
  value,
  'C' || lpad(value::text, 4, '0'),
  '企业客户' || lpad(value::text, 3, '0'),
  (ARRAY['华东', '华南', '华北', '华中', '西南', '西北', '东北'])[(value % 7) + 1],
  (ARRAY['战略', '重点', '标准'])[(value % 3) + 1],
  DATE '2022-01-01' + (value * 11)
FROM generate_series(1, 180) AS value;

INSERT INTO products (product_id, sku, product_name, category_l1, category_l2, list_price)
SELECT
  value,
  'SKU-' || lpad(value::text, 4, '0'),
  '产品' || lpad(value::text, 3, '0'),
  (ARRAY['工业设备', '软件服务', '办公协作', '技术支持'])[(value % 4) + 1],
  (ARRAY['基础型', '专业型', '旗舰型', '订阅服务', '实施服务'])[(value % 5) + 1],
  500 + ((value * 137) % 45) * 100
FROM generate_series(1, 80) AS value;

INSERT INTO sales_orders (
  order_id, order_number, customer_id, organization_id, order_date, order_status, currency_code
)
SELECT
  value,
  'SO-' || to_char(DATE '2024-01-01' + (value % 610), 'YYYY') || '-' || lpad(value::text, 6, '0'),
  1 + ((value * 7) % 180),
  1 + ((value * 5) % 8),
  DATE '2024-01-01' + (value % 610),
  CASE WHEN value % 23 = 0 THEN 'cancelled' ELSE 'confirmed' END,
  'CNY'
FROM generate_series(1, 3200) AS value;

INSERT INTO sales_order_lines (
  order_id, line_number, product_id, quantity, unit_price, discount_amount, net_amount
)
SELECT
  o.order_id,
  line_no,
  p.product_id,
  1 + ((o.order_id + line_no * 3) % 12),
  p.list_price,
  round((p.list_price * (1 + ((o.order_id + line_no) % 12)) *
        CASE WHEN o.order_id % 9 = 0 THEN 0.05 ELSE 0 END)::numeric, 2),
  round((p.list_price * (1 + ((o.order_id + line_no * 3) % 12)) -
        p.list_price * (1 + ((o.order_id + line_no) % 12)) *
        CASE WHEN o.order_id % 9 = 0 THEN 0.05 ELSE 0 END)::numeric, 2)
FROM sales_orders o
CROSS JOIN LATERAL generate_series(1, 1 + (o.order_id % 3)) AS line_no
JOIN products p ON p.product_id = 1 + ((o.order_id * 11 + line_no * 7) % 80);

CREATE INDEX sales_orders_customer_date_idx ON sales_orders (customer_id, order_date);
CREATE INDEX sales_orders_organization_date_idx ON sales_orders (organization_id, order_date);
CREATE INDEX sales_order_lines_product_idx ON sales_order_lines (product_id);

CREATE VIEW sales_order_line_enriched AS
SELECT
  o.order_id,
  o.order_number,
  o.order_date,
  o.order_status,
  c.customer_id,
  c.customer_code,
  c.customer_name,
  c.region AS customer_region,
  c.customer_tier,
  p.product_id,
  p.sku,
  p.product_name,
  p.category_l1,
  p.category_l2,
  org.organization_id,
  org.organization_name,
  org.region AS organization_region,
  l.line_number,
  l.quantity,
  l.unit_price,
  l.discount_amount,
  l.net_amount,
  o.currency_code
FROM sales_order_lines l
JOIN sales_orders o ON o.order_id = l.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.product_id = l.product_id
JOIN organizations org ON org.organization_id = o.organization_id;

COMMENT ON VIEW sales_order_line_enriched IS
  'Synthetic AgentBI 2.0 validation view; order-line grain; fictional data only.';

-- Governed execution projection for the "confirmed revenue" business rule.
-- Dashboards and semantic models that expose confirmed revenue must use this view
-- rather than relying on each consumer to remember the status predicate.
CREATE VIEW confirmed_sales_order_line_enriched AS
SELECT *
FROM sales_order_line_enriched
WHERE order_status = 'confirmed';

COMMENT ON VIEW confirmed_sales_order_line_enriched IS
  'Synthetic AgentBI 2.0 governed view; confirmed order lines only; fictional data only.';
