#!/usr/bin/env python3
"""
Example: Complete workflow for veterinary protein analysis

This script demonstrates:
1. Importing a PDF report
2. Adding clinical context (symptoms, observations)
3. Viewing results and generating reports
4. Simulating a follow-up test with comparison
"""

import sys
import os
from datetime import date, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from models import Animal, TestSession, ProteinResult, Database
from pdf_parser import parse_dnatech_report
from app import VetProteinService


def main():
    print("=" * 70)
    print("🐕 VETERINARY PROTEIN ANALYSIS - EXAMPLE WORKFLOW")
    print("=" * 70)
    
    # Initialize with a persistent database
    db_path = os.path.join(os.path.dirname(__file__), "data", "example.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # Remove existing DB for clean demo
    if os.path.exists(db_path):
        os.remove(db_path)
    
    with VetProteinService(db_path=db_path) as service:
        
        # =====================================================================
        # STEP 1: Import the first PDF report
        # =====================================================================
        print("\n📄 STEP 1: Importing PDF Report")
        print("-" * 50)
        
        pdf_path = "/mnt/user-data/uploads/bolt66790_1521038_copy.pdf"
        
        try:
            animal_id, session_id, parsed = service.import_pdf(
                pdf_path, 
                copy_to_uploads=False
            )
            print(f"✓ Animal created: {parsed.animal.name} (ID: {animal_id})")
            print(f"✓ Test session created: {parsed.session.report_number} (ID: {session_id})")
            print(f"✓ {len(parsed.results)} protein markers extracted")
            
        except Exception as e:
            print(f"✗ Error importing PDF: {e}")
            return
        
        # =====================================================================
        # STEP 2: Add clinical context
        # =====================================================================
        print("\n📝 STEP 2: Adding Clinical Context")
        print("-" * 50)
        
        # Add symptoms
        symptom_id1 = service.add_symptom(
            animal_id,
            "Perda de peso progressiva",
            severity="moderate",
            category="metabolic"
        )
        print(f"✓ Added symptom: Perda de peso progressiva (ID: {symptom_id1})")
        
        symptom_id2 = service.add_symptom(
            animal_id,
            "Aumento da sede (polidipsia)",
            severity="mild",
            category="metabolic"
        )
        print(f"✓ Added symptom: Polidipsia (ID: {symptom_id2})")
        
        # Add observations
        obs_id = service.add_observation(
            animal_id,
            obs_type="weight",
            details="Peso na consulta",
            value=3.8,
            unit="kg"
        )
        print(f"✓ Added observation: Weight 3.8 kg (ID: {obs_id})")
        
        obs_id2 = service.add_observation(
            animal_id,
            obs_type="medication",
            details="Iniciou suplementação com aminoácidos",
            obs_date=date.today()
        )
        print(f"✓ Added observation: Medication started (ID: {obs_id2})")
        
        # =====================================================================
        # STEP 3: Generate initial analysis report
        # =====================================================================
        print("\n📊 STEP 3: Analysis Report")
        print("-" * 50)
        
        report = service.generate_analysis_report(session_id)
        
        print(f"\nAnimal: {report.animal.name} ({report.animal.breed})")
        print(f"Test Date: {report.session.test_date}")
        print(f"\nResults Summary:")
        print(f"  ✓ Normal: {report.flags_summary['normal']}")
        print(f"  ↑ High: {report.flags_summary['high']}")
        print(f"  ↓ Low: {report.flags_summary['low']}")
        
        print(f"\n{report.summary}")
        
        # =====================================================================
        # STEP 4: Simulate a follow-up test (for comparison demo)
        # =====================================================================
        print("\n🔄 STEP 4: Simulating Follow-up Test (30 days later)")
        print("-" * 50)
        
        # Create a simulated follow-up session
        followup_session = TestSession(
            animal_id=animal_id,
            report_number="66850/1521999",  # New report number
            test_date=date.today(),
            sample_type="Soro",
            lab_name="DNAtech"
        )
        followup_id = service.db.create_test_session(followup_session)
        
        # Add simulated improved results (after treatment)
        simulated_results = [
            ("Proteinas totais", 6.8, None, "g/dL", 5.7, 7.9),
            ("Albumina", 48.2, 3.3, "%", 36.8, 50.6),  # Improved from 53.7
            ("Alfa 1", 5.1, 0.35, "%", 3.5, 13.9),     # Improved from 2.3
            ("Alfa 2", 10.8, 0.73, "%", 7.0, 11.4),    # Improved from 19.4
            ("Beta", 16.2, 1.1, "%", 15.8, 24.1),      # Improved from 7.3
            ("Gama", 19.7, 1.34, "%", 22.8, 27.8),     # Still low but improved
            ("Rel. Albumina/Globulina", 0.93, None, "ratio", 0.45, 1.30),
        ]
        
        for marker, value, abs_val, unit, ref_min, ref_max in simulated_results:
            result = ProteinResult(
                session_id=followup_id,
                marker_name=marker,
                value=value,
                unit=unit,
                value_absolute=abs_val,
                reference_min=ref_min,
                reference_max=ref_max
            )
            result.compute_flags()
            service.db.create_protein_result(result)
        
        print(f"✓ Created follow-up session (ID: {followup_id})")
        print(f"✓ Added 7 simulated results (post-treatment)")
        
        # =====================================================================
        # STEP 5: Compare results
        # =====================================================================
        print("\n📈 STEP 5: Comparing Results (Follow-up vs Initial)")
        print("-" * 50)
        
        comparisons = service.compare_sessions(followup_id, session_id)
        
        print(f"\n{'Marker':<25} {'Before':<10} {'After':<10} {'Change':<10} {'Trend'}")
        print("-" * 65)
        
        for comp in comparisons:
            before = f"{comp.previous_value:.1f}" if comp.previous_value else "N/A"
            after = f"{comp.current_value:.1f}" if comp.current_value else "N/A"
            change = f"{comp.change_percent:+.1f}%" if comp.change_percent else "N/A"
            
            trend_icon = {
                "improved": "✓ Improved",
                "worsened": "⚠ Worsened", 
                "stable": "→ Stable",
                "new": "● New"
            }.get(comp.trend, comp.trend)
            
            print(f"{comp.marker_name:<25} {before:<10} {after:<10} {change:<10} {trend_icon}")
        
        # =====================================================================
        # STEP 6: Generate follow-up report
        # =====================================================================
        print("\n📋 STEP 6: Follow-up Analysis Report")
        print("-" * 50)
        
        followup_report = service.generate_analysis_report(followup_id)
        print(f"\n{followup_report.summary}")
        
        # =====================================================================
        # STEP 7: View marker history
        # =====================================================================
        print("\n📊 STEP 7: Albumin History")
        print("-" * 50)
        
        albumin_history = service.get_marker_trend(animal_id, "Albumina")
        print("\nDate          Value    Flag")
        print("-" * 35)
        for entry in albumin_history:
            print(f"{entry['test_date']}    {entry['value']:.1f}%    {entry['flag']}")
        
        # =====================================================================
        # SUMMARY
        # =====================================================================
        print("\n" + "=" * 70)
        print("✅ WORKFLOW COMPLETE")
        print("=" * 70)
        print(f"""
Database saved to: {db_path}

What you can do next:
1. Import more PDF reports for the same or different animals
2. Add symptoms and observations as they occur
3. Generate comparison reports after each new test
4. Track trends over time for specific markers

For AI-powered interpretation (future phase):
- Integrate with Claude API
- Build a veterinary research knowledge base
- Get contextual analysis based on symptoms + results + breed
        """)


if __name__ == "__main__":
    main()
