import pytest
from unittest.mock import patch, MagicMock
import sys

# Patch environment variables before importing sync_awb
with patch.dict('os.environ', {
    'SHIPROCKET_EMAIL': 'test@test.com',
    'SHIPROCKET_PASSWORD': 'test',
    'AMAZON_CLIENT_ID': 'test',
    'AMAZON_CLIENT_SECRET': 'test',
    'AMAZON_REFRESH_TOKEN': 'test',
}):
    import sync_awb

@pytest.fixture
def mock_state():
    return {
        "last_sync": "never",
        "orders": []
    }

def test_main_liriya_flag(mock_state):
    # Test that --liriya sets args.amazon=True and args.skip_scrape=True
    # and bypasses Phase 2
    test_args = ["sync_awb.py", "--liriya", "--dry-run"]
    
    with patch.object(sys, 'argv', test_args):
        with patch("sync_awb.load_state", return_value=mock_state):
            with patch("sync_awb.auto_assign_couriers") as mock_assign:
                with patch("sync_awb.scrape_shiprocket_api") as mock_scrape:
                    with patch("sync_awb.confirm_shipment_api") as mock_confirm:
                        with patch("sync_awb.pdf_tool.process_folder", return_value=[]):
                            with patch("sync_awb.SHIPROCKET_EMAIL", "test"), patch("sync_awb.SHIPROCKET_PASSWORD", "test"), patch.dict("os.environ", {"AMAZON_CLIENT_ID": "test", "AMAZON_CLIENT_SECRET": "test", "AMAZON_REFRESH_TOKEN": "test"}):
                                sync_awb.main()
                                
                                # Phase 0 should be called with LIRIYA (WOOCOMMERCE)
                                mock_assign.assert_called_once()
                                args, kwargs = mock_assign.call_args
                                assert kwargs.get("channel_name") == "LIRIYA (WOOCOMMERCE)"
                                
                                # Phase 1 should be skipped because --liriya implies --skip-scrape
                                mock_scrape.assert_not_called()
                                
                                # Phase 2 should be skipped because --liriya bypasses amazon sp-api sync
                                mock_confirm.assert_not_called()

def test_main_amazon_flag(mock_state):
    test_args = ["sync_awb.py", "--amazon", "--dry-run"]
    
    mock_state["orders"] = [
        {"shiprocket_order_id": "1", "amazon_order_id": "A1", "courier_name": "Delhivery", "awb_number": "123", "synced_to_amazon": False}
    ]
    
    with patch.object(sys, 'argv', test_args):
        with patch("sync_awb.load_state", return_value=mock_state):
            with patch("sync_awb.auto_assign_couriers") as mock_assign:
                with patch("sync_awb.scrape_shiprocket_api", return_value=[]) as mock_scrape:
                    with patch("sync_awb.confirm_shipment_api", return_value="SUCCESS") as mock_confirm:
                        with patch("sync_awb.pdf_tool.process_folder", return_value=[]):
                            with patch("sync_awb.SHIPROCKET_EMAIL", "test"), patch("sync_awb.SHIPROCKET_PASSWORD", "test"), patch.dict("os.environ", {"AMAZON_CLIENT_ID": "test", "AMAZON_CLIENT_SECRET": "test", "AMAZON_REFRESH_TOKEN": "test"}):
                                sync_awb.main()
                                
                                # Phase 0 called with LIRIYA (AMAZON)
                                mock_assign.assert_called_once()
                                args, kwargs = mock_assign.call_args
                                assert kwargs.get("channel_name") == "LIRIYA (AMAZON)"
                                
                                # Phase 1 called
                                mock_scrape.assert_called_once()
                                
                                # Phase 2 called for pending order
                                mock_confirm.assert_called_once()
                                args, kwargs = mock_confirm.call_args
                                assert kwargs.get("amazon_order_id") == "A1"
