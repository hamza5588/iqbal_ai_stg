from flask import Flask, session
from app import create_app
import os

def check_session():
    app = create_app()
    with app.app_context():
        print("Session contents:")
        for key, value in session.items():
            print(f"{key}: {value}")
        
        # Check if required session variables exist
        required_vars = ['user_id', 'groq_api_key']
        missing_vars = [var for var in required_vars if var not in session]
        if missing_vars:
            print(f"\nMissing required session variables: {', '.join(missing_vars)}")
        else:
            print("\nAll required session variables are present")

if __name__ == "__main__":
    check_session() 