"""Replays the demo in order and checks every decision. Run: python test_pipeline.py"""
import pipeline
import store

EXPECTED = [  # order matters: duplicates and PO balances depend on history
    ("01_happy_bharat_furniture.pdf", "APPROVED"),
    ("05_edge_duplicate_bharat.pdf", "REJECTED"),
    ("03_split_po_nimbus_milestone1.pdf", "APPROVED"),
    ("04_edge_split_po_overbill_nimbus.pdf", "NEEDS_REVIEW"),
    ("02_edge_scanned_no_po_shakti.pdf", "NEEDS_REVIEW"),
    ("06_edge_price_variance_gst_inclusive.pdf", "NEEDS_REVIEW"),
    ("09_edge_gstin_mismatch_impersonation.pdf", "NEEDS_REVIEW"),
    ("07_unknown_vendor_zenith.pdf", "REJECTED"),
    ("08_blocked_vendor_apex.pdf", "REJECTED"),
]

if __name__ == "__main__":
    store.reset()
    ok = True
    for f, want in EXPECTED:
        got = pipeline.run_sync(pipeline.Path(__file__).parent / "samples" / f)
        flag = "PASS" if got["decision"] == want else "FAIL"
        ok &= flag == "PASS"
        print(f"{flag}  {f:45s} {got['decision']:13s} {got['reason'][:70]}")
    store.reset()
    raise SystemExit(0 if ok else 1)
