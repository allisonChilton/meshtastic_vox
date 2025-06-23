"""
Meshtastic Packet Monitor - Backend Module
Contains database operations, packet processing, and Meshtastic interface.
"""

import meshtastic
import time
import meshtastic.ble_interface
import meshtastic.serial_interface
from pubsub import pub
from pubsub.core import Topic
from dataclasses import dataclass
from typing import Any, Optional, List
import sqlite3
import json
from datetime import datetime
import threading
import random
from functools import lru_cache

import logging

log = logging.getLogger(__name__)

# Global packet list for the TUI
packet_list: List['Packet'] = []
packet_list_lock = threading.Lock()
MESHVOX_PORTNUM = meshtastic.portnums_pb2.PRIVATE_APP

# Subtopics dictionary with descriptions
subtopics = {
    "meshtastic.connection.established": "published once we've successfully connected to the radio and downloaded the node DB",
    "meshtastic.connection.lost": "published once we've lost our link to the radio",
    "meshtastic.receive.text": "delivers a received packet as a dictionary, if you only care about a particular type of packet, you should subscribe to the full topic name. If you want to see all packets, simply subscribe to \"meshtastic.receive\".",
    "meshtastic.receive.position": "delivers a received position packet",
    "meshtastic.receive.user": "delivers a received user packet", 
    "meshtastic.receive.data.portnum": "delivers a received data packet (where portnum is an integer or well known PortNum enum)",
    "meshtastic.node.updated": "published when a node in the DB changes (appears, location changed, username changed, etc…)",
    "meshtastic.log.line": "a raw unparsed log line from the radio"
}

# Database setup
def init_database():
    """Initialize SQLite database and create packets and nodes tables if they don't exist"""
    conn = sqlite3.connect('meshtastic_packets.db')
    cursor = conn.cursor()
    
    # Create packets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id INTEGER,
            from_node INTEGER,
            to_node INTEGER,
            from_id TEXT,
            to_id TEXT,
            rx_time INTEGER,
            hop_limit INTEGER,
            priority TEXT,
            portnum TEXT,
            payload BLOB,
            telemetry TEXT,
            position TEXT,
            raw_data TEXT,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create nodes table for storing node metadata
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            long_name TEXT,
            short_name TEXT,
            hw_model TEXT,
            firmware_version TEXT,
            role TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            battery_level INTEGER,
            voltage REAL,
            channel_utilization REAL,
            air_util_tx REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized successfully")

def store_packet(original_packet, parsed_packet):
    """Store packet data in SQLite database with individual fields"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        if parsed_packet:
            # Convert complex objects to JSON strings for storage
            telemetry_json = json.dumps(dict(parsed_packet.decoded.telemetry)) if parsed_packet.decoded.telemetry else None
            position_json = json.dumps(dict(parsed_packet.decoded.position)) if parsed_packet.decoded.position else None
            
            cursor.execute('''
                INSERT INTO packets (
                    packet_id, from_node, to_node, from_id, to_id, rx_time,
                    hop_limit, priority, portnum, payload, telemetry, position, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                parsed_packet.id,
                parsed_packet.from_,
                parsed_packet.to,
                parsed_packet.fromId,
                parsed_packet.toId,
                parsed_packet.rxTime,
                parsed_packet.hopLimit,
                parsed_packet.priority,
                parsed_packet.decoded.portnum,
                parsed_packet.decoded.payload,
                telemetry_json,
                position_json,
                parsed_packet.raw
            ))
        else:
            # Store raw packet data as JSON if parsing failed
            cursor.execute('''
                INSERT INTO packets (raw_data) VALUES (?)
            ''', (json.dumps(original_packet),))
        
        conn.commit()
        conn.close()
        # print(f"Packet {parsed_packet.id if parsed_packet else 'unknown'} stored in database")
        
    except Exception as e:
        print(f"Error storing packet in database: {e}")

def get_packet_count():
    """Get total number of packets stored"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM packets')
        count = cursor.fetchone()[0]
        conn.close()
        
        print(f"Total packets stored: {count}")
        return count
        
    except Exception as e:
        print(f"Error getting packet count: {e}")
        return 0

def load_packet_id(packet_id):
    """Load packet data from database by ID"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM packets WHERE id = ?', (packet_id,))
        result = cursor.fetchone()
        conn.close()
        
        return result
        
    except Exception as e:
        print(f"Error loading packet: {e}")
        return None

def query_recent_packets(limit=5):
    """Query recent packets from database"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT packet_id, from_id, to_id, portnum, received_at 
            FROM packets 
            WHERE packet_id IS NOT NULL
            ORDER BY received_at DESC 
            LIMIT ?
        ''', (limit,))
        
        packets = cursor.fetchall()
        conn.close()
        
        if packets:
            print(f"\n--- Last {len(packets)} packets ---")
            for packet in packets:
                print(f"ID: {packet[0]}, From: {packet[1]}, To: {packet[2]}, "
                      f"Port: {packet[3]}, Time: {packet[4]}")
        
        return packets
        
    except Exception as e:
        print(f"Error querying packets: {e}")
        return []

def store_node_info(node_id, node_info):
    """Store or update node information in the database"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        # Extract node information
        user = node_info.get('user', {})
        long_name = user.get('longName', '')
        short_name = user.get('shortName', '')
        hw_model = user.get('hwModel', '')
        firmware_version = user.get('firmwareVersion', '')
        role = user.get('role', '')
        
        # Insert or update node info
        cursor.execute('''
            INSERT OR REPLACE INTO nodes 
            (node_id, long_name, short_name, hw_model, firmware_version, role, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (node_id, long_name, short_name, hw_model, firmware_version, role))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error storing node info: {e}")

@lru_cache(maxsize=1000)
def get_node_name(node_id):
    """Get the long name for a node ID, fallback to hex ID if not found"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT long_name, short_name FROM nodes WHERE node_id = ?', (node_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:  # If long_name exists and is not empty
            return result[0]
        elif result and result[1]:  # Fallback to short_name
            return result[1]
        else:
            return node_id  # Fallback to original ID
            
    except Exception as e:
        print(f"Error getting node name: {e}")
        return node_id

@dataclass
class NodeInfo:
    node_id: str
    long_name: str
    short_name: str
    hw_model: str
    firmware_version: str
    role: str
    last_seen: datetime
    battery_level: Optional[int] = None
    voltage: Optional[float] = None

def get_all_nodes() -> List[NodeInfo]:
    """Get all nodes from the database"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT node_id, long_name, short_name, hw_model, firmware_version, 
                   role, last_seen, battery_level, voltage
            FROM nodes 
            ORDER BY last_seen DESC
        ''')
        
        nodes = cursor.fetchall()
        conn.close()

        info_list = []
        for node in nodes:
            info = NodeInfo(
                node_id=node[0],
                long_name=node[1] or "",
                short_name=node[2] or "",
                hw_model=node[3] or "",
                firmware_version=node[4] or "",
                role=node[5] or "",
                last_seen=datetime.fromisoformat(node[6]) if node[6] else None,
                battery_level=node[7],
                voltage=node[8]
            )
            info_list.append(info)
        
        return info_list
        
    except Exception as e:
        print(f"Error getting nodes: {e}")
        return []

def update_node_telemetry(node_id, telemetry):
    """Update node telemetry data"""
    try:
        if not telemetry:
            return
            
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        battery_level = telemetry.get('batteryLevel')
        voltage = telemetry.get('voltage')
        channel_utilization = telemetry.get('channelUtilization')
        air_util_tx = telemetry.get('airUtilTx')
        
        cursor.execute('''
            UPDATE nodes 
            SET battery_level = ?, voltage = ?, channel_utilization = ?, 
                air_util_tx = ?, last_seen = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE node_id = ?
        ''', (battery_level, voltage, channel_utilization, air_util_tx, node_id))
        
        # If node doesn't exist, create a basic entry
        if cursor.rowcount == 0:
            cursor.execute('''
                INSERT INTO nodes (node_id, battery_level, voltage, channel_utilization, air_util_tx)
                VALUES (?, ?, ?, ?, ?)
            ''', (node_id, battery_level, voltage, channel_utilization, air_util_tx))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error updating node telemetry: {e}")

def load_packets_from_database():
    """Load all packets from database into the packet list"""
    try:
        conn = sqlite3.connect('meshtastic_packets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT packet_id, from_id, to_id, portnum, payload, rx_time,
                   hop_limit, priority, telemetry, position
            FROM packets 
            WHERE packet_id IS NOT NULL
            ORDER BY received_at ASC
        ''')
        
        packets = cursor.fetchall()
        conn.close()
        
        with packet_list_lock:
            packet_list.clear()
            for packet in packets:
                # Construct a Packet object from the database row
                decoded = Decoded(
                    portnum=packet[3] or 'N/A',
                    payload=packet[4] or b'',
                    telemetry=json.loads(packet[8]) if packet[8] else None,
                    position=json.loads(packet[9]) if packet[9] else None
                )
                packet_obj = Packet(
                    id=packet[0],
                    from_=0,  # Database does not store numeric from_/to, set to 0 or parse if needed
                    to=0,
                    fromId=packet[1] or 'N/A',
                    toId=packet[2] or 'N/A',
                    rxTime=packet[5] or 0,
                    hopLimit=packet[6] or 0,
                    priority=packet[7] or 'normal',
                    decoded=decoded
                )
                packet_info = packet_obj
                packet_list.append(packet_info)
        
        print(f"Loaded {len(packets)} packets from database")
        return len(packets)
        
    except Exception as e:
        print(f"Error loading packets from database: {e}")
        return 0

def query_packets(limit=100, portnum=None, from_id=None, to_id=None, substring=None, exclude=False):
    """Get packets from the global packet list with optional filters"""
    with packet_list_lock:
        filtered_packets = packet_list.copy()
        
    if portnum:
        filtered_packets = [p for p in filtered_packets if p.decoded.portnum == portnum]
    if from_id:
        filtered_packets = [p for p in filtered_packets if p.fromId == from_id]
    if to_id:
        filtered_packets = [p for p in filtered_packets if p.toId == to_id]
    if substring:
        if not exclude:
            filtered_packets = [p for p in filtered_packets if p.matches_substring(substring)]
        else:
            filtered_packets = [p for p in filtered_packets if not p.matches_substring(substring)]
    
    return filtered_packets[-limit:]  # Return the last 'limit' packets

def remove_key_recursive(data, key_to_remove):
    """Recursively remove a key from a dictionary or list of dictionaries."""
    if isinstance(data, dict):
        # Remove the key if it exists
        data.pop(key_to_remove, None)
        # Recursively process all values
        for value in data.values():
            remove_key_recursive(value, key_to_remove)
    elif isinstance(data, list):
        # Recursively process all items in the list
        for item in data:
            remove_key_recursive(item, key_to_remove)

@dataclass
class Decoded:
    portnum: str
    payload: bytes
    telemetry: Optional[dict] = None  
    position: Optional[dict] = None
    user: Optional[dict] = None  
    bitfield: Optional[int] = None
    wantResponse: Optional[bool] = None
    notes: Optional[str] = None
    payload_original: Optional[Any] = None  # Store original data for debugging

    @classmethod
    def from_dict(cls, data):
        data = data.copy()  # Avoid modifying the original data
        remove_key_recursive(data, "raw")
        r = cls(
            portnum=data.pop("portnum", ""),
            payload=data.pop("payload", ""),
            telemetry=data.pop("telemetry", None),
            position=data.pop("position", None),
            bitfield=data.pop("bitfield", None),
            user=data.pop("user", None),
            wantResponse=data.pop("wantResponse", None),
            payload_original=data,  # Store original data for debugging
        )
        if data.keys():
            # log.warning(f"Decoded.from_dict: Unrecognized keys {data.keys()} in data: {data}")
            r.notes = f"Unrecognized keys: {', '.join(data.keys())}"
        
        return r

@dataclass
class Packet:
    id: int
    from_: int
    to: int
    fromId: str
    toId: str
    rxTime: int
    hopLimit: int
    priority: str
    decoded: 'Decoded'
    raw: Optional[bytes] = None
    packet_original: Optional[Any] = None  # Store original data for debugging
    notes: Optional[str] = None  # Additional notes for processing


    def matches_substring(self, query: str) -> bool:
        """
        Check if the packet matches the given substring query.
        Optionally provide a node_lookup function to resolve node names.
        """
        query = query.lower()
        from_long_name = get_node_name(self.fromId) if get_node_name else self.fromId
        to_long_name = get_node_name(self.toId) if get_node_name else self.toId
        search_text = f"{self.fromId} {self.toId} {from_long_name} {to_long_name} {self.decoded.portnum} {self.decoded.payload} {self.priority} {getattr(self.decoded, 'notes', '')}".lower()
        return query in search_text

    def to_dict(self) -> dict:
        packet_info = {
            'id': self.id,
            'fromId': self.fromId,
            'toId': self.toId,
            'portnum': self.decoded.portnum,
            'payload': self.decoded.payload,
            'rxTime': self.rxTime,
            'hopLimit': self.hopLimit,
            'priority': self.priority,
            'payload_original': self.decoded.payload_original,
            'packet_original': self.packet_original
        }
        if self.decoded:
            u = {
            'telemetry': self.decoded.telemetry,
            'position': self.decoded.position,
            }
            packet_info.update(u)

        return packet_info

    @classmethod
    def from_dict(cls, data):
        data = data.copy()  # Avoid modifying the original data
        if 'raw' in data:
            raw = data.pop("raw")
            raw = raw.SerializeToString()
        else:
            raw = None

        if 'encrypted' not in data:
            decoded = Decoded.from_dict(data.pop("decoded", {}))
        else:
            decoded = Decoded('', b'encrypted')
        return cls(
            id=data.pop("id", 0),
            from_=data.get("from", 0),
            to=data.get("to", 0),
            fromId=data.pop("fromId", ""),
            toId=data.pop("toId", ""),
            rxTime=data.get("rxTime", decoded.telemetry.get('time', int(time.time())) if decoded.telemetry else int(time.time())),
            hopLimit=data.get("hopLimit", 0),
            priority=data.pop("priority", ""),
            decoded=decoded,
            raw=raw,
            packet_original=data  # Store original data for debugging
        )

def onReceive(packet, interface):
    """Called when a packet is received from meshtastic."""
    try:
        # Parse packet data
        parsed_packet = Packet.from_dict(packet)
        
        processing_notes = []
        
        # Update node telemetry if available
        if parsed_packet.decoded.telemetry:
            update_node_telemetry(parsed_packet.fromId, parsed_packet.decoded.telemetry)
        
        # Process position data if available
        # if parsed_packet.decoded.position:
        #     processing_notes.append("Position data available")

        if parsed_packet.decoded.portnum == 'NODEINFO_APP':
            # store or update node information
            user = parsed_packet.decoded.user or {}
            store_node_info(
                user.get('id', parsed_packet.fromId),
                {'user': {
                    'longName': user.get('longName', ''),
                    'shortName': user.get('shortName', ''),
                    'hwModel': user.get('hwModel', ''),
                    'firmwareVersion': user.get('firmwareVersion', ''),
                    'role': user.get('role', '')
                }}
            )
        
        # Check for notes from packet processing
        if parsed_packet.decoded.notes:
            processing_notes.append(parsed_packet.decoded.notes)
        
        # Add to global packet list for TUI
        with packet_list_lock:
            parsed_packet.notes = "; ".join(processing_notes) if processing_notes else "",

            packet_list.append(parsed_packet)
            
            # Keep only last 1000 packets in memory
            if len(packet_list) > 1000:
                packet_list.pop(0)
        
        # Store the packet with parsed data
        store_packet(packet, parsed_packet)
            
    except Exception as e:
        print(f"Error processing packet: {e}")
        
        # Add error packet to list for visibility
        with packet_list_lock:
            error_packet = {
                'id': 'ERROR',
                'fromId': 'ERROR',
                'toId': 'ERROR',
                'portnum': 'ERROR',
                'payload': f"Error: {str(e)}".encode(),
                'rxTime': int(time.time()),
                'hopLimit': 0,
                'priority': 'error',
                'telemetry': None,
                'position': None,
                'notes': f"Packet processing error: {e}"
            }
            packet_list.append(error_packet)

def recursive_topic_gather(node: Topic):
    """Recursively gather all subtopics from the root topic."""
    st = node.getSubtopics()
    for topic in st:
        # Add topic as key with None value if not already present
        if topic.name not in subtopics:
            subtopics[topic.name] = None
        recursive_topic_gather(topic)
    return subtopics

interface = None

def send_vox_message(destinationId: str, data: bytes):
    def onAckNak(x: dict):
        """Callback for ACK/NAK responses."""
        print("ACK/NAK received")
        print(x)

    if interface:
        pkt = interface.sendData(
            data=data,
            destinationId=destinationId,
            wantAck=True,
            wantResponse=False,
            portNum=meshtastic.portnums_pb2.TEXT_MESSAGE_APP,
            hopLimit=1,
            pkiEncrypted=True,
            onResponse=onAckNak
        )
        log.info(f"Packet sent: {pkt}")
        # while True:
        #     time.sleep(1)

def run_meshtastic_interface():
    """Run the meshtastic interface in a separate thread."""
    try:
        # Subscribe to packet events
        pub.subscribe(onReceive, "meshtastic.receive")
        # pub.subscribe(onConnection, "meshtastic.connection.established")
        
        # Connect to device
        name = "Meshtastic_db60"
        addr = "48:CA:43:3C:DB:61"
        global interface
        interface = meshtastic.serial_interface.SerialInterface('COM4')
        
        print("Meshtastic interface started...")

        refresh = 5
        mgr = pub.getDefaultTopicMgr()
        root = mgr.getRootAllTopics()
        
        # Keep the interface running
        while True:
            time.sleep(refresh)
            recursive_topic_gather(root)
            if refresh == 5:
                refresh = 60

            
    except KeyboardInterrupt:
        print("Meshtastic interface stopped by user")
    except Exception as e:
        print(f"Unexpected exception, terminating meshtastic reader: {e}")
        import traceback
        traceback.print_exc()

def add_test_packets():
    """Add some test packets and node info for demonstration."""
    
    # Add some test node information first
    test_nodes = [
        {"node_id": "Node_00", "long_name": "Base Station Alpha", "short_name": "BSA"},
        {"node_id": "Node_01", "long_name": "Mobile Unit Beta", "short_name": "MUB"},
        {"node_id": "Node_02", "long_name": "Sensor Gamma", "short_name": "SG"},
        {"node_id": "Node_03", "long_name": "Repeater Delta", "short_name": "RD"},
        {"node_id": "Node_04", "long_name": "Weather Station", "short_name": "WS"},
        {"node_id": "Device_A", "long_name": "Emergency Beacon", "short_name": "EB"},
        {"node_id": "Device_B", "long_name": "Mobile Tracker", "short_name": "MT"},
    ]
    
    # Store test node information
    for node in test_nodes:
        store_node_info(node["node_id"], {
            'user': {
                'longName': node["long_name"],
                'shortName': node["short_name"],
                'hwModel': 'HELTEC_V3',
                'firmwareVersion': '2.3.2',
                'role': 'CLIENT'
            }
        })
    
    test_packets = []
    
    # Create varied test packets with different data types
    for i in range(15):
        portnum_options = ["TEXT_MESSAGE_APP", "POSITION_APP", "TELEMETRY_APP", "NODEINFO_APP", "ROUTING_APP"]
        portnum = random.choice(portnum_options)
        
        # Generate different telemetry based on packet type
        telemetry = None
        position = None
        notes = ""
        
        if portnum == "TELEMETRY_APP" or random.random() < 0.3:  # 30% chance for other types
            telemetry = {
                'battery_level': random.randint(20, 100),
                'voltage': round(random.uniform(3.2, 4.2), 2),
                'temperature': round(random.uniform(-10, 45), 1),
                'channel_utilization': round(random.uniform(0.1, 15.0), 1),
                'air_util_tx': round(random.uniform(0.1, 8.0), 1)
            }
            
        if portnum == "POSITION_APP" or random.random() < 0.2:  # 20% chance for other types
            # Generate realistic coordinates (roughly US area)
            base_lat = 39.8283  # Near center of US
            base_lon = -98.5795
            position = {
                'latitude_i': int((base_lat + random.uniform(-10, 10)) * 1e7),
                'longitude_i': int((base_lon + random.uniform(-20, 20)) * 1e7),
                'altitude': random.randint(100, 2000),
                'time': int(time.time()) - random.randint(0, 3600)
            }
            
        if random.random() < 0.15:  # 15% chance of having notes
            note_options = ["Signal weak", "Duplicate packet", "Out of range", "Battery low warning"]
            notes = random.choice(note_options)
        
        test_packet = {
            'id': random.randint(1000, 9999),
            'fromId': f"Node_{i:02d}" if i < 10 else f"Device_{chr(65 + i - 10)}",
            'toId': "Broadcast" if random.random() < 0.8 else f"Node_{random.randint(0, 5):02d}",
            'portnum': portnum,
            'payload': f"Test message {i}".encode('utf-8') if portnum == "TEXT_MESSAGE_APP" else b"binary_data_" + str(i).encode(),
            'rxTime': int(time.time()) - random.randint(0, 7200),  # Up to 2 hours ago
            'hopLimit': random.randint(1, 7),
            'priority': random.choice(["normal", "min", "background", "critical"]),
            'telemetry': telemetry,
            'position': position,
            'notes': notes
        }
        test_packets.append(test_packet)
    
    with packet_list_lock:
        packet_list.extend(test_packets)

# Initialize database when script starts
init_database()

if __name__ == "__main__":
    # Start meshtastic interface in background thread
    meshtastic_thread = threading.Thread(target=run_meshtastic_interface, daemon=True)
    meshtastic_thread.start()
    
    # # Give the interface a moment to start
    while not interface:
        time.sleep(0.1)
    
    # # Add test packets
    # # add_test_packets()
    
    # # Import and run the GUI
    # from gui import MeshtasticTUI
    # app = MeshtasticTUI()
    # app.run()
    send_message()
