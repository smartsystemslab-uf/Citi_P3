from bank_adk.tools.aml_tools import detect_mule_rings

def test_task8_synthetic_closed_loop():
    """
    Test Requirement: Construct a synthetic closed loop in test data (account A sends to B, 
    B sends to C, C sends back to A) and confirm the traversal detects the cycle.
    """
    # 1. Construct a synthetic closed loop in test data
    synthetic_transactions = [
        {"from_account_id": "Account_A", "to_account_id": "Account_B"}, # A sends to B
        {"from_account_id": "Account_B", "to_account_id": "Account_C"}, # B sends to C
        {"from_account_id": "Account_C", "to_account_id": "Account_A"}  # C sends back to A
    ]
    
    # 2. Run the traversal
    detected_rings = detect_mule_rings(synthetic_transactions)
    
    # 3. Confirm the traversal detects the cycle
    assert len(detected_rings) == 1
    
    # Ensure all three accounts are caught in the loop
    detected_ring = detected_rings[0]
    assert "Account_A" in detected_ring
    assert "Account_B" in detected_ring
    assert "Account_C" in detected_ring