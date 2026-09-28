-- Garment ERP schema (MySQL 8 / MariaDB 10.5+). InnoDB, utf8mb4.
-- Every master has: id, active, created_at.  Hierarchical masters have parent_id.

-- ---------------------------------------------------------------- generic
CREATE TABLE IF NOT EXISTS m_generic_type (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_generic (
  id INT AUTO_INCREMENT PRIMARY KEY,
  type_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_generic (type_id, name),
  FOREIGN KEY (type_id) REFERENCES m_generic_type(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_uom (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(40) NOT NULL UNIQUE,
  decimals TINYINT NOT NULL DEFAULT 0,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_branch (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  address VARCHAR(255),
  gstin VARCHAR(20),
  state VARCHAR(60),
  pincode VARCHAR(10),
  contact_person VARCHAR(80),
  mobile VARCHAR(20),
  email VARCHAR(120),
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_godown (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  parent_id INT NULL,
  branch_id INT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_godown (branch_id, name),
  FOREIGN KEY (parent_id) REFERENCES m_godown(id),
  FOREIGN KEY (branch_id) REFERENCES m_branch(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_process (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_machine (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- accounts
CREATE TABLE IF NOT EXISTS m_ledger_group (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  parent_id INT NULL,
  category VARCHAR(20) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (parent_id) REFERENCES m_ledger_group(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_ledger_account (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_pricelist (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- products
CREATE TABLE IF NOT EXISTS m_product_category (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_product_category_attr (
  id INT AUTO_INCREMENT PRIMARY KEY,
  category_id INT NOT NULL,
  attr_no INT NOT NULL,
  label VARCHAR(60) NOT NULL,
  mode VARCHAR(10) NOT NULL DEFAULT 'List',        -- List | Manual
  generic_type_id INT NULL,                        -- connected master when mode=List
  FOREIGN KEY (category_id) REFERENCES m_product_category(id) ON DELETE CASCADE,
  FOREIGN KEY (generic_type_id) REFERENCES m_generic_type(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_product_group (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  parent_id INT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (parent_id) REFERENCES m_product_group(id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- ledger master (parties etc.)
CREATE TABLE IF NOT EXISTS m_ledger (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(150) NOT NULL UNIQUE,
  ledger_group_id INT NULL,
  ledger_account_id INT NULL,
  ledger_type VARCHAR(20) NOT NULL DEFAULT 'General',
  address VARCHAR(255),
  city VARCHAR(80),
  state VARCHAR(60),
  pincode VARCHAR(10),
  gstin VARCHAR(20),
  pan_no VARCHAR(12),
  contact_person VARCHAR(80),
  mobile VARCHAR(20),
  email VARCHAR(120),
  tax_type VARCHAR(10) NULL,                       -- CGST/SGST/IGST/TDS/TCS
  broker_id INT NULL,
  salesman_id INT NULL,
  brokerage_rate DECIMAL(6,2) NULL,
  cash_discount DECIMAL(6,2) NULL,
  transporter_id INT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY ix_ledger_type (ledger_type),
  FOREIGN KEY (ledger_group_id) REFERENCES m_ledger_group(id),
  FOREIGN KEY (ledger_account_id) REFERENCES m_ledger_account(id),
  FOREIGN KEY (broker_id) REFERENCES m_ledger(id),
  FOREIGN KEY (salesman_id) REFERENCES m_ledger(id),
  FOREIGN KEY (transporter_id) REFERENCES m_ledger(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_ledger_discount (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ledger_id INT NOT NULL,
  product_group_id INT NULL,
  disc_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
  pricelist_id INT NULL,
  applicable_date DATE NULL,
  FOREIGN KEY (ledger_id) REFERENCES m_ledger(id) ON DELETE CASCADE,
  FOREIGN KEY (product_group_id) REFERENCES m_product_group(id),
  FOREIGN KEY (pricelist_id) REFERENCES m_pricelist(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_product (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  item_name VARCHAR(255) NOT NULL UNIQUE,          -- SKU code, generated from name + attributes
  category_id INT NOT NULL,
  group_id INT NULL,
  uom_id INT NOT NULL,
  barcode VARCHAR(20) NULL UNIQUE,
  attr_values JSON NULL,
  default_sales_ledger_id INT NULL,
  default_purchase_ledger_id INT NULL,
  gst_rate DECIMAL(5,2) NOT NULL DEFAULT 0,
  hsn_code VARCHAR(12),
  package_qty DECIMAL(12,3) NULL,
  min_stock DECIMAL(14,3) NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY ix_product_cat (category_id),
  FOREIGN KEY (category_id) REFERENCES m_product_category(id),
  FOREIGN KEY (group_id) REFERENCES m_product_group(id),
  FOREIGN KEY (uom_id) REFERENCES m_uom(id),
  FOREIGN KEY (default_sales_ledger_id) REFERENCES m_ledger(id),
  FOREIGN KEY (default_purchase_ledger_id) REFERENCES m_ledger(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_product_customer (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  customer_id INT NOT NULL,
  customer_item_name VARCHAR(200),
  FOREIGN KEY (product_id) REFERENCES m_product(id) ON DELETE CASCADE,
  FOREIGN KEY (customer_id) REFERENCES m_ledger(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_product_process (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  seq INT NOT NULL DEFAULT 0,
  process_id INT NOT NULL,
  material_id INT NULL,                             -- material to issue (a product)
  part_name VARCHAR(100),
  qty DECIMAL(14,4) NOT NULL DEFAULT 0,             -- per unit of finished product
  process_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
  out_status VARCHAR(4) NOT NULL DEFAULT 'WIP',     -- WIP | FP
  overhead_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
  FOREIGN KEY (product_id) REFERENCES m_product(id) ON DELETE CASCADE,
  FOREIGN KEY (process_id) REFERENCES m_process(id),
  FOREIGN KEY (material_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_pricelist_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  pricelist_id INT NOT NULL,
  product_id INT NOT NULL,
  rate DECIMAL(14,2) NOT NULL DEFAULT 0,
  disc_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
  disc_amt DECIMAL(14,2) NOT NULL DEFAULT 0,
  FOREIGN KEY (pricelist_id) REFERENCES m_pricelist(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- transaction types
CREATE TABLE IF NOT EXISTS m_txn_type (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  parent_id INT NULL,
  branch_id INT NULL,
  txn_kind VARCHAR(30) NOT NULL,
  start_date DATE NULL,
  prefix VARCHAR(20) DEFAULT '',
  suffix VARCHAR(20) DEFAULT '',
  start_number INT NOT NULL DEFAULT 1,
  max_number INT NOT NULL DEFAULT 0,               -- last used number, maintained by backend
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY ix_txn_kind (txn_kind),
  FOREIGN KEY (parent_id) REFERENCES m_txn_type(id),
  FOREIGN KEY (branch_id) REFERENCES m_branch(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS m_txn_type_terms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  txn_type_id INT NOT NULL,
  sl_no INT NOT NULL DEFAULT 0,
  description VARCHAR(500) NOT NULL,
  FOREIGN KEY (txn_type_id) REFERENCES m_txn_type(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS app_role (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(60) NOT NULL UNIQUE,
  permissions JSON NOT NULL,                        -- {"*": ["*"]} or {"sales_order": ["view","create"]}
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_user (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(60) NOT NULL UNIQUE,
  full_name VARCHAR(100) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role_id INT NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  last_login TIMESTAMP NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (role_id) REFERENCES app_role(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS api_client (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  key_prefix VARCHAR(12) NOT NULL,
  key_hash CHAR(64) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- vouchers
CREATE TABLE IF NOT EXISTS txn_header (
  id INT AUTO_INCREMENT PRIMARY KEY,
  doc_type VARCHAR(30) NOT NULL,                    -- sales_order, challan, sales, ...
  txn_type_id INT NOT NULL,
  branch_id INT NOT NULL,
  voucher_no VARCHAR(60) NOT NULL,
  voucher_date DATE NOT NULL,
  party_id INT NOT NULL,
  salesman_id INT NULL,
  broker_id INT NULL,
  retailer_name VARCHAR(150) NULL,
  remarks VARCHAR(500) NULL,
  approval_status VARCHAR(10) NOT NULL DEFAULT 'Pending',
  approver_id INT NULL,
  approver_name VARCHAR(100) NULL,
  approved_at DATETIME NULL,
  total_qty DECIMAL(16,3) NOT NULL DEFAULT 0,
  total_product_value DECIMAL(16,2) NOT NULL DEFAULT 0,
  total_value DECIMAL(16,2) NOT NULL DEFAULT 0,
  packing_list_id INT NULL,                         -- sales invoice -> challan
  ref_voucher_id INT NULL,                          -- returns -> original invoice
  ref_doc_no VARCHAR(60) NULL,                      -- party document no
  ref_doc_date DATE NULL,
  -- logistics updation (sales invoice)
  einvoice_no VARCHAR(80) NULL, einvoice_date DATE NULL,
  eway_bill_no VARCHAR(80) NULL, eway_bill_date DATE NULL,
  courier_slip_no VARCHAR(80) NULL, courier_slip_date DATE NULL,
  transporter_id INT NULL,
  transporter_cn_no VARCHAR(80) NULL, transporter_cn_date DATE NULL,
  freight_amount DECIMAL(14,2) NULL,
  source_channel VARCHAR(20) NOT NULL DEFAULT 'ui',  -- ui | api
  created_by INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_voucher (txn_type_id, voucher_no),
  KEY ix_hdr_doc (doc_type, voucher_date),
  KEY ix_hdr_party (party_id),
  FOREIGN KEY (txn_type_id) REFERENCES m_txn_type(id),
  FOREIGN KEY (branch_id) REFERENCES m_branch(id),
  FOREIGN KEY (party_id) REFERENCES m_ledger(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS requisition (
  id INT AUTO_INCREMENT PRIMARY KEY,
  req_no VARCHAR(60) NOT NULL UNIQUE,
  req_date DATE NOT NULL,
  source VARCHAR(10) NOT NULL DEFAULT 'Manual',     -- Planning | Manual
  plan_id INT NULL,
  status VARCHAR(12) NOT NULL DEFAULT 'Open',       -- Open | Closed | Cancelled
  remarks VARCHAR(500) NULL,
  txn_type_id INT NULL,
  created_by INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS requisition_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  requisition_id INT NOT NULL,
  product_id INT NOT NULL,
  qty DECIMAL(16,3) NOT NULL,
  remark VARCHAR(255) NULL,
  FOREIGN KEY (requisition_id) REFERENCES requisition(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS txn_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  header_id INT NOT NULL,
  line_no INT NOT NULL,
  barcode VARCHAR(40) NULL,
  godown_id INT NULL,
  bin_no VARCHAR(40) NULL,
  product_id INT NOT NULL,
  description VARCHAR(500) NULL,
  customer_item_name VARCHAR(200) NULL,
  qty DECIMAL(16,3) NOT NULL,
  pricelist_id INT NULL,
  rate DECIMAL(14,2) NOT NULL DEFAULT 0,
  disc_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
  disc_amt DECIMAL(14,2) NOT NULL DEFAULT 0,
  net_amount DECIMAL(16,2) NOT NULL DEFAULT 0,
  delivery_date DATE NULL,
  salesman_id INT NULL,
  broker_id INT NULL,
  retailer_name VARCHAR(150) NULL,
  src_header_id INT NULL,                           -- SO / PO / GRN this line was picked from
  src_item_id INT NULL,
  req_item_id INT NULL,                             -- requisition line (PO)
  ref_no VARCHAR(60) NULL,                          -- order no carried down the chain (SO no / PO no)
  indent_no VARCHAR(60) NULL,                       -- requisition / indent no
  KEY ix_item_hdr (header_id),
  KEY ix_item_src (src_item_id),
  KEY ix_item_req (req_item_id),
  KEY ix_item_barcode (barcode),
  FOREIGN KEY (header_id) REFERENCES txn_header(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS txn_ledger (
  id INT AUTO_INCREMENT PRIMARY KEY,
  header_id INT NOT NULL,
  line_no INT NOT NULL,
  ledger_id INT NOT NULL,
  rate DECIMAL(14,4) NOT NULL DEFAULT 0,
  rate_in VARCHAR(8) NOT NULL DEFAULT 'percent',    -- percent | value
  rate_on VARCHAR(24) NOT NULL DEFAULT 'auto',      -- auto | total_qty | total_product_value | current_subtotal | net_value
  amount DECIMAL(16,2) NOT NULL DEFAULT 0,
  FOREIGN KEY (header_id) REFERENCES txn_header(id) ON DELETE CASCADE,
  FOREIGN KEY (ledger_id) REFERENCES m_ledger(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS txn_terms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  header_id INT NOT NULL,
  kind VARCHAR(10) NOT NULL DEFAULT 'terms',        -- terms | payment
  sl_no INT NOT NULL,
  description VARCHAR(500) NOT NULL,
  FOREIGN KEY (header_id) REFERENCES txn_header(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- stock
CREATE TABLE IF NOT EXISTS stock_ledger (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  header_id INT NOT NULL,
  item_id INT NOT NULL,
  txn_date DATE NOT NULL,
  product_id INT NOT NULL,
  barcode VARCHAR(40) NOT NULL,
  godown_id INT NOT NULL,
  bin_no VARCHAR(40) NOT NULL DEFAULT '',
  qty DECIMAL(16,3) NOT NULL,                       -- + in, - out
  KEY ix_stock_bc (barcode, godown_id, bin_no),
  KEY ix_stock_prod (product_id),
  KEY ix_stock_hdr (header_id),
  FOREIGN KEY (header_id) REFERENCES txn_header(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- production planning
CREATE TABLE IF NOT EXISTS prod_plan (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_no VARCHAR(60) NOT NULL UNIQUE,
  plan_date DATE NOT NULL,
  status VARCHAR(12) NOT NULL DEFAULT 'Accepted',   -- Accepted | Cancelled
  remarks VARCHAR(500) NULL,
  txn_type_id INT NULL,
  requisition_id INT NULL,
  created_by INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS prod_plan_order (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  order_id INT NOT NULL,
  FOREIGN KEY (plan_id) REFERENCES prod_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (order_id) REFERENCES txn_header(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS prod_plan_fg (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  product_id INT NOT NULL,
  required_qty DECIMAL(16,3) NOT NULL,
  free_stock DECIMAL(16,3) NOT NULL,
  balance_qty DECIMAL(16,3) NOT NULL,
  FOREIGN KEY (plan_id) REFERENCES prod_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS prod_plan_material (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  product_id INT NOT NULL,
  required_qty DECIMAL(16,3) NOT NULL,
  free_stock DECIMAL(16,3) NOT NULL,
  po_pending DECIMAL(16,3) NOT NULL,
  shortfall_qty DECIMAL(16,3) NOT NULL,
  FOREIGN KEY (plan_id) REFERENCES prod_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS stock_reservation (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  product_id INT NOT NULL,
  qty DECIMAL(16,3) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  KEY ix_res_prod (product_id, active),
  FOREIGN KEY (plan_id) REFERENCES prod_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES m_product(id)
) ENGINE=InnoDB;
