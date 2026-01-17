#!/usr/bin/env python3
"""
Run the Vet Protein Analysis Web Application

Usage:
    python run_server.py
    
Then open http://localhost:8000 in your browser.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def main():
    print("=" * 60)
    print("🐕 Vet Protein Analysis - Web Server")
    print("=" * 60)
    
    try:
        import uvicorn
    except ImportError:
        print("\n❌ Missing dependencies!")
        print("\nPlease install required packages:")
        print("  pip install fastapi uvicorn jinja2 python-multipart pdfplumber")
        print("\nOr run:")
        print("  pip install -r requirements.txt")
        sys.exit(1)
    
    print("\n📂 Starting server...")
    print("   Database: data/vet_proteins.db")
    print("   Uploads: uploads/")
    print("\n🌐 Open http://localhost:8000 in your browser")
    print("\n   Press Ctrl+C to stop the server\n")
    print("=" * 60)
    
    # Import and run the web server
    from web_server import app
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
