import random, sqlite3, os

rng = random.Random(42)  # fixed seed: same data every time
if os.path.exists("eval.db"):
    os.remove("eval.db")
con = sqlite3.connect("eval.db")
con.executescript("""
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT, email TEXT, signup_date TEXT);
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT,
  FOREIGN KEY(customer_id) REFERENCES customers(id));
CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER,
  FOREIGN KEY(order_id) REFERENCES orders(id), FOREIGN KEY(product_id) REFERENCES products(id));
""")

names = ["Asha","Ravi","Meera","Kiran","Sana","Arjun","Neha","Vikram","Pooja","Imran",
         "Divya","Rohan","Anita","Farhan","Tara","Manish","Leela","Sameer","Gauri","Nikhil"]
cities = ["Pune","Nagpur","Mumbai","Delhi","Bhopal"]
for i, n in enumerate(names, 1):
    email = None if i % 5 == 0 else f"{n.lower()}@example.com"  # some missing emails
    signup = f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
    con.execute("INSERT INTO customers VALUES(?,?,?,?,?)", (i, n, rng.choice(cities), email, signup))

products = [("Notebook","Stationery",80),("Pen Set","Stationery",150),("Desk Lamp","Home",900),
            ("Mug","Home",250),("Backpack","Accessories",1400),("Headphones","Electronics",2200),
            ("Keyboard","Electronics",1800),("Mouse","Electronics",700),("Water Bottle","Accessories",450),
            ("Planner","Stationery",320)]
for i, (n, c, p) in enumerate(products, 1):
    con.execute("INSERT INTO products VALUES(?,?,?,?)", (i, n, c, p))

oid = iid = 0
for _ in range(60):
    oid += 1
    cust = rng.randint(1, 16)  # customers 17-20 never order
    date = f"2026-{rng.randint(1,11):02d}-{rng.randint(1,28):02d}"
    status = rng.choice(["completed","completed","completed","cancelled","pending"])
    con.execute("INSERT INTO orders VALUES(?,?,?,?)", (oid, cust, date, status))
    for p in rng.sample(range(1, 11), rng.randint(1, 3)):
        iid += 1
        con.execute("INSERT INTO order_items VALUES(?,?,?,?)", (iid, oid, p, rng.randint(1, 4)))
con.execute("INSERT INTO products VALUES(11, 'Calendar', 'Toys', 200)")  # never ordered, on purpose
con.commit()
for t in ("customers","products","orders","order_items"):
    print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
