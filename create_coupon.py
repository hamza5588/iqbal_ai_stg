#!/usr/bin/env python3
"""
Standalone script to create coupons in the database.
Usage: python create_coupon.py
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Database configuration - update these if needed
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://myuser:mypassword@localhost:5432/mydatabase')

def create_coupon(code, subscription_tier, description=None, max_uses=None, expires_at=None, is_active=True):
    """
    Create a coupon in the database.
    
    Args:
        code: Coupon code (string, unique, required)
        subscription_tier: 'pro' or 'pro_plus' (required)
        description: Optional description
        max_uses: Maximum number of uses (None for unlimited)
        expires_at: Expiration datetime (None for never expires)
        is_active: Whether coupon is active (default: True)
    """
    try:
        # Create engine and session
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Check if coupon code already exists
        check_query = text("SELECT id FROM coupons WHERE code = :code")
        result = session.execute(check_query, {"code": code.upper()})
        existing = result.fetchone()
        
        if existing:
            print(f"❌ Error: Coupon code '{code}' already exists!")
            session.close()
            return False
        
        # Validate subscription tier
        if subscription_tier not in ['pro', 'pro_plus']:
            print(f"❌ Error: subscription_tier must be 'pro' or 'pro_plus', got '{subscription_tier}'")
            session.close()
            return False
        
        # Prepare the insert query
        insert_query = text("""
            INSERT INTO coupons (code, subscription_tier, description, max_uses, used_count, expires_at, is_active, created_at)
            VALUES (:code, :subscription_tier, :description, :max_uses, 0, :expires_at, :is_active, NOW())
            RETURNING id, code, subscription_tier
        """)
        
        # Execute insert
        result = session.execute(insert_query, {
            "code": code.upper(),
            "subscription_tier": subscription_tier,
            "description": description,
            "max_uses": max_uses,
            "expires_at": expires_at,
            "is_active": is_active
        })
        
        # Commit the transaction
        session.commit()
        
        # Get the inserted record
        inserted = result.fetchone()
        
        print(f"✅ Successfully created coupon!")
        print(f"   ID: {inserted[0]}")
        print(f"   Code: {inserted[1]}")
        print(f"   Tier: {inserted[2]}")
        print(f"   Description: {description or 'N/A'}")
        print(f"   Max Uses: {max_uses or 'Unlimited'}")
        print(f"   Expires: {expires_at or 'Never'}")
        print(f"   Active: {is_active}")
        
        session.close()
        return True
        
    except Exception as e:
        print(f"❌ Error creating coupon: {str(e)}")
        if 'session' in locals():
            session.rollback()
            session.close()
        return False

def main():
    """Main function with example coupon creation"""
    print("=" * 60)
    print("Coupon Creation Script")
    print("=" * 60)
    print(f"Database: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print()
    
    # Example 1: Simple Pro coupon
    print("Creating Example Coupon 1: PROMO2024 (Pro, unlimited uses)")
    create_coupon(
        code='PROMO2024',
        subscription_tier='pro',
        description='Promotional Pro subscription',
        max_uses=None,  # Unlimited
        expires_at=None,  # Never expires
        is_active=True
    )
    print()
    
    # Example 2: Pro Plus with expiration
    print("Creating Example Coupon 2: PROPLUS50 (Pro Plus, 50 uses, expires 2025-12-31)")
    create_coupon(
        code='PROPLUS50',
        subscription_tier='pro_plus',
        description='Pro Plus promotional code',
        max_uses=50,
        expires_at='2025-12-31 23:59:59',
        is_active=True
    )
    print()
    
    # Example 3: Limited use coupon
    print("Creating Example Coupon 3: LIMITED10 (Pro, 10 uses max)")
    create_coupon(
        code='LIMITED10',
        subscription_tier='pro',
        description='Limited to 10 redemptions',
        max_uses=10,
        expires_at=None,
        is_active=True
    )
    print()
    
    print("=" * 60)
    print("✅ All coupons created successfully!")
    print("=" * 60)

if __name__ == "__main__":
    # You can customize the coupons here or call create_coupon() directly
    if len(sys.argv) > 1:
        # Command line usage: python create_coupon.py CODE TIER [DESCRIPTION] [MAX_USES] [EXPIRES_AT]
        code = sys.argv[1]
        tier = sys.argv[2] if len(sys.argv) > 2 else 'pro'
        description = sys.argv[3] if len(sys.argv) > 3 else None
        max_uses = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else None
        expires_at = sys.argv[5] if len(sys.argv) > 5 else None
        
        create_coupon(code, tier, description, max_uses, expires_at)
    else:
        # Run examples
        main()































