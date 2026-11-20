"""Print a native Norvale scenario. Pipe it to a JSON file for simulate.py."""
import json


def norvale_quarter(seed=42, days=90):
    orders = [
        {"customer": customer, "item": item, "quantity": quantity, "due_day": due}
        for customer, item, quantity, due in [
            ("CUST-5001", "SKU-30001", 240, 2),
            ("CUST-5002", "SKU-30002", 180, 2),
            ("CUST-5003", "SKU-30003", 120, 3),
            ("CUST-5004", "SKU-30004", 180, 3),
        ]
    ]
    return {"version": 1, "seed": seed, "days": days, "opening_orders": orders}


if __name__ == "__main__":
    print(json.dumps(norvale_quarter(), indent=2))
