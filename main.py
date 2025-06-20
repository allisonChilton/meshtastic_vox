import time
import sys
import threading
from gui import MeshtasticTUI
from mt_backend import run_meshtastic_interface
from pubsub import pub

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s'
)

def anyListener(*args, **kwargs):
    """
    Listener function that receives messages from the pubsub system.
    """
    logging.info("Received message: %s", args)
    for key, value in kwargs.items():
        logging.info("  %s: %s", key, value)
    # You can process the message here if needed
    # For now, just log it

if __name__ == "__main__":
    # Start meshtastic interface in background thread
    meshtastic_thread = threading.Thread(target=run_meshtastic_interface, daemon=True)
    meshtastic_thread.start()
    
    # Give the interface a moment to start
    time.sleep(2)

    # from pubsub.core import TopicManager, Publisher
    # manager = TopicManager()
    # topic = manager.getTopic("meshtastic.log.line", okIfNone=True)

    # if topic:
    #     print(topic.hasMDS())
    #     print(topic.getArgDescriptions())
    #     print()
    # else:
    #     sys.exit("Topic 'meshtastic.log.line' not found. Ensure the Meshtastic interface is running.")

    # pub.sendMessage("meshtastic.log.line", line="1234", interface="test_interface")

    # pub.subscribe(anyListener, "meshtastic.log.line")

    
    # Run the TUI
    app = MeshtasticTUI()
    app.run()
