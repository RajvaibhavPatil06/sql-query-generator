import sqlite3
con = sqlite3.connect("sample.db")
con.executescript("""
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, customer_id INTEGER,
  amount REAL, order_date TEXT, FOREIGN KEY(customer_id) REFERENCES customers(id));
INSERT INTO customers(name, city) VALUES ('Asha','Pune'),('Ravi','Nagpur'),('Meera','Pune');
INSERT INTO orders(customer_id, amount, order_date) VALUES
 (1,500,'2026-10-01'),(2,1200,'2026-10-05'),(1,300,'2026-11-02'),(3,900,'2026-11-10');
""")
con.commit()
