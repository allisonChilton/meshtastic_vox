import time
import threading
from gui import MeshtasticTUI
from mt_backend import run_meshtastic_interface

if __name__ == "__main__":
    # Start meshtastic interface in background thread
    meshtastic_thread = threading.Thread(target=run_meshtastic_interface, daemon=True)
    meshtastic_thread.start()
    
    # Give the interface a moment to start
    time.sleep(2)
    
    # Run the TUI
    app = MeshtasticTUI()
    app.run()
