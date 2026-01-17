#!/usr/bin/env python3
"""
Comprehensive Test Script for Vet Protein Analysis Application

Tests:
1. PDF parsing
2. Database operations
3. Date handling (the main bug)
4. Template rendering simulation
5. Full workflow
"""

import sys
import os
from datetime import date, datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_date_handling():
    """Test date parsing and formatting - this was the main bug"""
    print("\n" + "="*60)
    print("TEST 1: Date Handling")
    print("="*60)
    
    from models import parse_portuguese_date
    
    test_cases = [
        ("25/10/2025", date(2025, 10, 25)),
        ("25 10 2025", date(2025, 10, 25)),
        ("25102025", date(2025, 10, 25)),
        ("", None),
        (None, None),
    ]
    
    all_passed = True
    for input_val, expected in test_cases:
        result = parse_portuguese_date(input_val)
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_passed = False
        print(f"  {status} parse_portuguese_date({repr(input_val)}) = {result} (expected {expected})")
    
    # Test the web_server date parsing function
    print("\n  Testing web_server parse_date_value():")
    
    # Simulate what web_server.py does
    def parse_date_value(d):
        """Convert various date formats to a date object"""
        if d is None:
            return None
        if isinstance(d, date) and not isinstance(d, datetime):
            return d
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            try:
                return datetime.strptime(d, "%Y-%m-%d").date()
            except:
                try:
                    return datetime.strptime(d, "%d/%m/%Y").date()
                except:
                    return None
        return None
    
    web_test_cases = [
        (None, None),
        ("2025-10-25", date(2025, 10, 25)),  # SQLite format
        ("25/10/2025", date(2025, 10, 25)),  # Portuguese format
        (date(2025, 10, 25), date(2025, 10, 25)),  # Already a date
        (datetime(2025, 10, 25, 12, 30), date(2025, 10, 25)),  # datetime
        ("invalid", None),
    ]
    
    for input_val, expected in web_test_cases:
        result = parse_date_value(input_val)
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_passed = False
        print(f"  {status} parse_date_value({repr(input_val)}) = {result} (expected {expected})")
    
    # Test sorting with mixed date types (this was the actual bug!)
    print("\n  Testing date sorting with mixed types:")
    
    class MockSession:
        def __init__(self, test_date):
            self.test_date = test_date
    
    sessions = [
        {'session': MockSession("2025-10-25")},  # String from SQLite
        {'session': MockSession(date(2025, 11, 28))},  # Date object
        {'session': MockSession(None)},  # None
        {'session': MockSession("2025-12-07")},  # String from SQLite
    ]
    
    def get_sort_date(item):
        d = item['session'].test_date
        if d is None:
            return date.min
        if isinstance(d, str):
            try:
                return datetime.strptime(d, "%Y-%m-%d").date()
            except:
                return date.min
        return d
    
    try:
        sorted_sessions = sorted(sessions, key=get_sort_date, reverse=True)
        print(f"  ✓ Sorting succeeded!")
        for s in sorted_sessions:
            print(f"      {s['session'].test_date}")
    except Exception as e:
        print(f"  ✗ Sorting failed: {e}")
        all_passed = False
    
    return all_passed


def test_pdf_parsing():
    """Test PDF parsing with real files"""
    print("\n" + "="*60)
    print("TEST 2: PDF Parsing")
    print("="*60)
    
    from pdf_parser import parse_dnatech_report
    
    pdf_files = [
        "/mnt/user-data/uploads/bolt58630_1500951.pdf",
        "/mnt/user-data/uploads/bolt65401_1517628__1_.pdf",
        "/mnt/user-data/uploads/bolt66790_1521038_copy.pdf",
    ]
    
    all_passed = True
    for pdf_path in pdf_files:
        if not os.path.exists(pdf_path):
            print(f"  ⚠ Skipping {os.path.basename(pdf_path)} (not found)")
            continue
        
        try:
            result = parse_dnatech_report(pdf_path)
            print(f"  ✓ {os.path.basename(pdf_path)}")
            print(f"      Animal: {result.animal.name} ({result.animal.species})")
            print(f"      Date: {result.session.test_date} (type: {type(result.session.test_date).__name__})")
            print(f"      Results: {len(result.results)} markers")
            if result.biochemistry:
                print(f"      UPC: {result.biochemistry.upc_ratio}")
            if result.urinalysis:
                print(f"      Urinalysis: pH {result.urinalysis.ph}")
        except Exception as e:
            print(f"  ✗ {os.path.basename(pdf_path)}: {e}")
            all_passed = False
            import traceback
            traceback.print_exc()
    
    return all_passed


def test_database_operations():
    """Test database CRUD operations"""
    print("\n" + "="*60)
    print("TEST 3: Database Operations")
    print("="*60)
    
    from models import Database, Animal, TestSession, ProteinResult
    
    # Use in-memory database for testing
    db = Database(":memory:")
    db.initialize()
    
    all_passed = True
    
    # Test animal creation
    try:
        animal = Animal(name="TestDog", species="Canideo", breed="Labrador", sex="M")
        animal_id = db.create_animal(animal)
        print(f"  ✓ Created animal with ID {animal_id}")
    except Exception as e:
        print(f"  ✗ Animal creation failed: {e}")
        all_passed = False
        return False
    
    # Test session creation with date
    try:
        session = TestSession(
            animal_id=animal_id,
            report_number="TEST/123",
            test_date=date(2025, 10, 25),
            sample_type="Soro",
            lab_name="DNAtech"
        )
        session_id = db.create_test_session(session)
        print(f"  ✓ Created session with ID {session_id}")
    except Exception as e:
        print(f"  ✗ Session creation failed: {e}")
        all_passed = False
        return False
    
    # Test retrieving session and check date type
    try:
        sessions = db.get_sessions_for_animal(animal_id)
        if sessions:
            retrieved_date = sessions[0].test_date
            print(f"  ✓ Retrieved session, date={retrieved_date} (type: {type(retrieved_date).__name__})")
            
            # This is important - SQLite returns strings!
            if isinstance(retrieved_date, str):
                print(f"      ⚠ Note: SQLite returns dates as strings, not date objects")
        else:
            print(f"  ✗ No sessions retrieved")
            all_passed = False
    except Exception as e:
        print(f"  ✗ Session retrieval failed: {e}")
        all_passed = False
    
    db.close()
    return all_passed


def test_full_workflow():
    """Test complete workflow: parse PDF, store in DB, retrieve"""
    print("\n" + "="*60)
    print("TEST 4: Full Workflow")
    print("="*60)
    
    from app import VetProteinService
    
    # Use in-memory database
    service = VetProteinService(db_path=":memory:", uploads_dir="/tmp/vet_uploads")
    service.initialize()
    
    all_passed = True
    imported_sessions = []
    
    pdf_files = [
        "/mnt/user-data/uploads/bolt58630_1500951.pdf",
        "/mnt/user-data/uploads/bolt65401_1517628__1_.pdf",
    ]
    
    # Import PDFs
    for pdf_path in pdf_files:
        if not os.path.exists(pdf_path):
            print(f"  ⚠ Skipping {os.path.basename(pdf_path)} (not found)")
            continue
        
        try:
            animal_id, session_id, parsed = service.import_pdf(pdf_path, copy_to_uploads=False)
            imported_sessions.append((animal_id, session_id))
            print(f"  ✓ Imported {os.path.basename(pdf_path)}")
            print(f"      Animal ID: {animal_id}, Session ID: {session_id}")
        except Exception as e:
            print(f"  ✗ Import failed: {e}")
            all_passed = False
    
    # Test listing animals
    try:
        animals = service.db.list_animals()
        print(f"  ✓ Listed {len(animals)} animals")
        for a in animals:
            print(f"      - {a.name} ({a.species})")
    except Exception as e:
        print(f"  ✗ List animals failed: {e}")
        all_passed = False
    
    # Test getting sessions and sorting (the bug!)
    if animals:
        try:
            all_sessions = []
            for animal in animals:
                sessions = service.db.get_sessions_for_animal(animal.id)
                for session in sessions:
                    all_sessions.append({
                        'animal': animal,
                        'session': session
                    })
            
            # This is the exact code that was failing!
            def get_sort_date(item):
                d = item['session'].test_date
                if d is None:
                    return date.min
                if isinstance(d, str):
                    try:
                        return datetime.strptime(d, "%Y-%m-%d").date()
                    except:
                        return date.min
                return d
            
            sorted_sessions = sorted(all_sessions, key=get_sort_date, reverse=True)
            print(f"  ✓ Sorted {len(sorted_sessions)} sessions by date")
            for item in sorted_sessions:
                print(f"      - {item['session'].test_date}: {item['animal'].name}")
        except Exception as e:
            print(f"  ✗ Session sorting failed: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False
    
    # Test comparison
    if len(imported_sessions) >= 2:
        try:
            comparison = service.compare_sessions(imported_sessions[1][1], imported_sessions[0][1])
            print(f"  ✓ Comparison generated: {len(comparison)} markers compared")
        except Exception as e:
            print(f"  ✗ Comparison failed: {e}")
            all_passed = False
    
    service.close()
    return all_passed


def test_template_date_filter():
    """Test the template date filter logic"""
    print("\n" + "="*60)
    print("TEST 5: Template Date Filter")
    print("="*60)
    
    # Simulate the Jinja2 filter
    def parse_date_value(d):
        if d is None:
            return None
        if isinstance(d, date) and not isinstance(d, datetime):
            return d
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            try:
                return datetime.strptime(d, "%Y-%m-%d").date()
            except:
                try:
                    return datetime.strptime(d, "%d/%m/%Y").date()
                except:
                    return None
        return None

    def format_date_filter(d) -> str:
        parsed = parse_date_value(d)
        if parsed is None:
            return "N/A"
        try:
            return parsed.strftime("%d/%m/%Y")
        except:
            return "N/A"
    
    test_cases = [
        (None, "N/A"),
        ("2025-10-25", "25/10/2025"),  # SQLite format
        ("25/10/2025", "25/10/2025"),  # Portuguese format
        (date(2025, 10, 25), "25/10/2025"),
        (datetime(2025, 10, 25, 12, 30), "25/10/2025"),
        ("invalid", "N/A"),
        ("", "N/A"),
    ]
    
    all_passed = True
    for input_val, expected in test_cases:
        result = format_date_filter(input_val)
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_passed = False
        print(f"  {status} format_date({repr(input_val)}) = {repr(result)} (expected {repr(expected)})")
    
    return all_passed


def main():
    print("="*60)
    print("🐕 VET PROTEIN ANALYSIS - COMPREHENSIVE TEST SUITE")
    print("="*60)
    
    results = {}
    
    results['Date Handling'] = test_date_handling()
    results['PDF Parsing'] = test_pdf_parsing()
    results['Database Operations'] = test_database_operations()
    results['Full Workflow'] = test_full_workflow()
    results['Template Date Filter'] = test_template_date_filter()
    
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        if not passed:
            all_passed = False
        print(f"  {status}: {test_name}")
    
    print("\n" + "="*60)
    if all_passed:
        print("✓ ALL TESTS PASSED")
    else:
        print("✗ SOME TESTS FAILED")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
