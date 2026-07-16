import os
import csv
from freight_db import FreightDB

def run_migration():
    print("Starting migration of CSV freight data to SQLite DB...")
    db = FreightDB()
    freight_dir = "freight"
    
    if not os.path.exists(freight_dir):
        print(f"Directory {freight_dir} not found. Skipping migration.")
        return

    migrated_count = 0
    skipped_count = 0

    for filename in os.listdir(freight_dir):
        if filename.endswith(".csv") and not filename.startswith("dummy_"):
            filepath = os.path.join(freight_dir, filename)
            print(f"Processing {filepath}...")
            
            with open(filepath, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    awb = row.get('AWB Code') or row.get('awb') or row.get('AWB Number')
                    order_num = row.get('Order Number') or row.get('order_id') or row.get('Order ID')
                    amount_str = row.get('Freight Total Amount') or row.get('shipping_cost') or row.get('Amount')
                    zone = row.get('Zone')
                    charged_weight_str = row.get('Charged Weight')
                    
                    if amount_str:
                        try:
                            cost = float(amount_str)
                            awb_clean = awb.strip() if awb else None
                            order_num_clean = str(order_num).strip() if order_num else None
                            
                            charged_weight = None
                            if charged_weight_str:
                                try:
                                    charged_weight = float(charged_weight_str)
                                except ValueError:
                                    pass
                            
                            if awb_clean or order_num_clean:
                                # shiprocket_order_id is usually not in standard CSV export
                                db.insert_freight(
                                    channel_order_id=order_num_clean,
                                    shiprocket_order_id=None,
                                    awb_number=awb_clean,
                                    freight_amount=cost,
                                    charged_weight=charged_weight,
                                    zone=zone,
                                    source='csv'
                                )
                                migrated_count += 1
                            else:
                                skipped_count += 1
                        except ValueError:
                            skipped_count += 1
    
    print(f"Migration complete. Inserted/updated {migrated_count} records. Skipped {skipped_count}.")

if __name__ == "__main__":
    run_migration()
