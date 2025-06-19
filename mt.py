import meshtastic
import time
import meshtastic.ble_interface
import meshtastic.serial_interface
from pubsub import pub
from dataclasses import dataclass
from typing import Any, Optional
import sqlite3
import json
from datetime import datetime
import threading
import asyncio
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static, Button, Log, Footer, Label
from textual.binding import Binding
from textual.screen import ModalScreen

import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

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

def get_all_nodes():
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
        
        return nodes
        
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

# Initialize database when script starts
init_database()

# Global packet list for the TUI
packet_list = []
packet_list_lock = threading.Lock()

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
                packet_info = {
                    'id': packet[0],
                    'fromId': packet[1] or 'N/A',
                    'toId': packet[2] or 'N/A',
                    'portnum': packet[3] or 'N/A',
                    'payload': packet[4] or b'',
                    'rxTime': packet[5] or 0,
                    'hopLimit': packet[6] or 0,
                    'priority': packet[7] or 'normal',
                    'telemetry': json.loads(packet[8]) if packet[8] else None,
                    'position': json.loads(packet[9]) if packet[9] else None
                }
                packet_list.append(packet_info)
        
        print(f"Loaded {len(packets)} packets from database")
        return len(packets)
        
    except Exception as e:
        print(f"Error loading packets from database: {e}")
        return 0

class MeshtasticTUI(App):
    """A textual TUI for displaying Meshtastic packets with filtering."""
    
    TITLE = "Meshtastic Packet Monitor"
    
    CSS = """
    #filter_input {
        margin: 1 2;
        height: 3;
    }
    
    #packet_table {
        margin: 1 2;
        width: 100%;
    }
    
    #stats {
        margin: 1 2;
        height: 3;
    }
    
    #controls {
        margin: 1 2;
        height: 3;
    }
    
    .header {
        background: blue;
        color: white;
        text-align: center;
        height: 1;
    }
    
    DataTable {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }
    
    #detail_modal {
        align: center middle;
        width: 80%;
        height: 80%;
        background: $surface;
        border: thick $primary;
    }
    
    #detail_header {
        background: $primary;
        color: $text;
        text-align: center;
        height: 1;
        padding: 1;
    }
      #detail_content_left {
        width: 50%;
        height: 1fr;
        padding: 1;
    }
    
    #detail_content_right {
        width: 50%;
        height: 1fr;
        padding: 1;
        border-left: solid $primary;
    }
    
    #close_button {
        margin: 1;
        width: 20;
    }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("ctrl+f", "focus_filter", "Focus Filter"),
        Binding("ctrl+s", "toggle_session_filter", "Session Filter"),
    ]
    
    def __init__(self):
        super().__init__()
        self.filter_text = ""
        self.session_start_time = time.time()
        self.session_filter_active = False
        self.show_long_names = True  # Toggle between long names and hex IDs
        self.update_timer = None
    
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Static("Meshtastic Packet Monitor", classes="header", id="header")
        yield Input(placeholder="Filter packets (node ID, portnum, etc.)", id="filter_input")
        with Horizontal(id="controls"):
            yield Button("All Messages", id="all_button", variant="primary")
            yield Button("Session Only", id="session_button")
            yield Button("Long Names", id="name_toggle_button", variant="primary")
            yield Static(f"Session started: {datetime.fromtimestamp(self.session_start_time).strftime('%H:%M:%S')}", id="session_info")
        yield DataTable(id="packet_table")
        yield Static("Total packets: 0 | Filtered: 0", id="stats")
        yield Footer()
    
    def on_mount(self) -> None:
        """Called when app starts."""
        table = self.query_one("#packet_table", DataTable)
        table.add_columns("Time", "From", "To", "Port", "Payload", "Hops", "Priority", "Telemetry", "Position", "Notes")
        table.cursor_type = "row"
        
        # Load all packets from database on startup
        load_packets_from_database()
        
        # Start updating the table periodically
        self.set_interval(2.0, self.update_table)
          # Focus the filter input
        self.query_one("#filter_input", Input).focus()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "all_button":
            self.session_filter_active = False
            event.button.variant = "primary"
            self.query_one("#session_button", Button).variant = "default"
            self.update_table()
        elif event.button.id == "session_button":
            self.session_filter_active = True
            event.button.variant = "primary"
            self.query_one("#all_button", Button).variant = "default"
            self.update_table()
        elif event.button.id == "name_toggle_button":
            self.show_long_names = not self.show_long_names
            if self.show_long_names:
                event.button.label = "Long Names"
                event.button.variant = "primary"
            else:
                event.button.label = "Hex IDs"
                event.button.variant = "default"
            self.update_table()
    
    def on_input_changed(self, event: Input.Changed) -> None:
        """Called when filter input changes."""
        if event.input.id == "filter_input":
            self.filter_text = event.value.lower()
            self.update_table()
    
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Called when a row is selected in the packet table."""
        table = event.data_table
        if table.id == "packet_table":
            # Get the selected row index
            row_index = event.cursor_row
            
            with packet_list_lock:
                packets = packet_list.copy()
            
            # Apply current filters to get the same filtered list as displayed
            if self.session_filter_active:
                packets = [p for p in packets if p.get('rxTime', 0) >= self.session_start_time]
            
            if self.filter_text:
                filtered_packets = []
                for packet in packets:
                    search_text = f"{packet.get('fromId', '')} {packet.get('toId', '')} {packet.get('portnum', '')} {packet.get('payload', '')} {packet.get('priority', '')} {packet.get('notes', '')}".lower()
                    if packet.get('telemetry'):
                        search_text += f" {str(packet['telemetry'])}".lower()
                    if packet.get('position'):
                        search_text += f" {str(packet['position'])}".lower()
                    if self.filter_text in search_text:
                        filtered_packets.append(packet)
                packets = filtered_packets
            
            # Get the packet data for the selected row (accounting for reverse order display)
            display_packets = list(reversed(packets[-100:]))
            if 0 <= row_index < len(display_packets):
                selected_packet = display_packets[row_index]
                # Show the detail modal
                self.push_screen(PacketDetailModal(selected_packet))
    
    def update_table(self) -> None:
        """Update the packet table with filtered data."""
        table = self.query_one("#packet_table", DataTable)
        stats = self.query_one("#stats", Static)
        
        with packet_list_lock:
            packets = packet_list.copy()
        
        # Apply session filter if active
        if self.session_filter_active:
            packets = [p for p in packets if p.get('rxTime', 0) >= self.session_start_time]
        
        # Clear existing rows
        table.clear()
          # Filter packets based on filter text
        filtered_packets = []
        if self.filter_text:
            for packet in packets:
                # Search in various fields including new columns
                search_text = f"{packet.get('fromId', '')} {packet.get('toId', '')} {packet.get('portnum', '')} {packet.get('payload', '')} {packet.get('priority', '')} {packet.get('notes', '')}".lower()
                # Also search in telemetry and position data
                if packet.get('telemetry'):
                    search_text += f" {str(packet['telemetry'])}".lower()
                if packet.get('position'):
                    search_text += f" {str(packet['position'])}".lower()
                
                if self.filter_text in search_text:
                    filtered_packets.append(packet)
        else:
            filtered_packets = packets        # Add filtered packets to table (show most recent first)
        for packet in reversed(filtered_packets[-100:]):  # Show last 100 packets
            time_str = datetime.fromtimestamp(packet.get('rxTime', 0)).strftime('%H:%M:%S') if packet.get('rxTime') else 'N/A'
            
            # Handle From ID with name toggle
            from_id = packet.get('fromId', 'N/A')
            if self.show_long_names and from_id != 'N/A':
                from_display = get_node_name(from_id)
            else:
                from_display = from_id
                
            # Handle To ID with name toggle  
            to_id = packet.get('toId', 'N/A')
            if self.show_long_names and to_id != 'N/A':
                to_display = get_node_name(to_id)
            else:
                to_display = to_id
                
            portnum = packet.get('portnum', 'N/A')
            
            # Decode payload if it's text
            payload = packet.get('payload', b'')
            if isinstance(payload, bytes):
                try:
                    payload_str = payload.decode('utf-8')[:30]  # Limit to 30 chars for more columns
                except:
                    payload_str = f"<binary:{len(payload)} bytes>"
            else:
                payload_str = str(payload)[:30]
            
            # Get additional fields
            hop_limit = str(packet.get('hopLimit', 'N/A'))
            priority = packet.get('priority', 'N/A')
            
            # Format telemetry data
            telemetry = packet.get('telemetry')
            if telemetry:
                if isinstance(telemetry, dict) and telemetry:
                    # Show key metrics from telemetry
                    tel_parts = []
                    if 'battery_level' in telemetry:
                        tel_parts.append(f"Bat:{telemetry['battery_level']}%")
                    if 'voltage' in telemetry:
                        tel_parts.append(f"V:{telemetry['voltage']:.1f}")
                    if 'temperature' in telemetry:
                        tel_parts.append(f"T:{telemetry['temperature']:.1f}°C")
                    telemetry_str = " ".join(tel_parts)[:25] if tel_parts else "Yes"
                else:
                    telemetry_str = "Yes"
            else:
                telemetry_str = "-"
            
            # Format position data
            position = packet.get('position')
            if position:
                if isinstance(position, dict) and position:
                    lat = position.get('latitude_i', position.get('latitude', 0))
                    lon = position.get('longitude_i', position.get('longitude', 0))
                    if lat and lon:
                        # Convert from scaled integers if needed
                        if abs(lat) > 1000000:  # Likely scaled integer
                            lat = lat / 1e7
                            lon = lon / 1e7
                        position_str = f"{lat:.3f},{lon:.3f}"
                    else:
                        position_str = "Yes"
                else:
                    position_str = "Yes"
            else:
                position_str = "-"
            
            # Generate notes based on packet processing
            notes = packet.get('notes', '')
            
            table.add_row(time_str, from_display, to_display, portnum, payload_str, hop_limit, priority, telemetry_str, position_str, notes)
        
        # Update stats
        session_text = " (Session only)" if self.session_filter_active else ""
        stats.update(f"Total packets: {len(packets)} | Filtered: {len(filtered_packets)}{session_text}")
    
    def action_refresh(self) -> None:
        """Refresh the table."""
        self.update_table()
    
    def action_focus_filter(self) -> None:
        """Focus the filter input."""
        self.query_one("#filter_input", Input).focus()
    
    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

class PacketDetailModal(ModalScreen):
    """A modal screen to show detailed packet information."""
    
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]
    
    def __init__(self, packet_data):
        super().__init__()
        self.packet_data = packet_data
    
    def compose(self) -> ComposeResult:
        """Create the modal content."""
        with Container(id="detail_modal"):
            yield Static("Packet Details", id="detail_header")
            with Horizontal():
                # Left column - Basic packet info and parsed data
                with VerticalScroll(id="detail_content_left"):
                    yield Label("=== Basic Packet Info ===")
                    yield Label(f"Packet ID: {self.packet_data.get('id', 'N/A')}")
                    yield Label(f"From: {self.packet_data.get('fromId', 'N/A')}")
                    yield Label(f"To: {self.packet_data.get('toId', 'N/A')}")
                    yield Label(f"Port: {self.packet_data.get('portnum', 'N/A')}")
                    yield Label(f"Time: {datetime.fromtimestamp(self.packet_data.get('rxTime', 0)).strftime('%Y-%m-%d %H:%M:%S') if self.packet_data.get('rxTime') else 'N/A'}")
                    yield Label(f"Hop Limit: {self.packet_data.get('hopLimit', 'N/A')}")
                    yield Label(f"Priority: {self.packet_data.get('priority', 'N/A')}")
                    
                    # Payload
                    yield Label("")
                    yield Label("=== Payload ===")
                    payload = self.packet_data.get('payload', b'')
                    if isinstance(payload, bytes):
                        try:
                            payload_str = payload.decode('utf-8')
                            yield Label(f"Text: {payload_str}")
                        except:
                            yield Label(f"Binary: {payload.hex()[:100]}{'...' if len(payload) > 50 else ''}")
                    else:
                        yield Label(f"Value: {str(payload)}")
                    
                    # Telemetry
                    yield Label("")
                    yield Label("=== Telemetry ===")
                    telemetry = self.packet_data.get('telemetry')
                    if telemetry:
                        for key, value in telemetry.items():
                            yield Label(f"  {key}: {value}")
                    else:
                        yield Label("None")
                    
                    # Position
                    yield Label("")
                    yield Label("=== Position ===")
                    position = self.packet_data.get('position')
                    if position:
                        for key, value in position.items():
                            if key in ['latitude_i', 'longitude_i'] and abs(value) > 1000000:
                                # Convert scaled integers
                                yield Label(f"  {key}: {value} ({value/1e7:.6f})")
                            else:
                                yield Label(f"  {key}: {value}")
                    else:
                        yield Label("None")
                    
                    # Notes
                    yield Label("")
                    yield Label("=== Notes ===")
                    notes = self.packet_data.get('notes', '')
                    yield Label(f"{notes if notes else 'None'}")
                
                # Right column - Original data
                with VerticalScroll(id="detail_content_right"):
                    # Packet Original
                    yield Label("=== Packet Original ===")
                    packet_original = self.packet_data.get('packet_original')
                    if packet_original:
                        if isinstance(packet_original, dict):
                            for key, value in packet_original.items():
                                yield Label(f"  {key}: {value}")
                        else:
                            yield Label(f"{str(packet_original)}")
                    else:
                        yield Label("None")
                    
                    # Payload Original
                    yield Label("")
                    yield Label("=== Payload Original ===")
                    payload_original = self.packet_data.get('payload_original')
                    if payload_original:
                        if isinstance(payload_original, dict):
                            for key, value in payload_original.items():
                                yield Label(f"  {key}: {value}")
                        else:
                            yield Label(f"{str(payload_original)}")
                    else:
                        yield Label("None")
                        
            yield Button("Close", id="close_button", variant="primary")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "close_button":
            self.dismiss()
    
    def action_dismiss(self) -> None:
        """Close the modal."""
        self.dismiss()

def remove_key_recursive(d, removeKey):
    """
    Recursively remove all occurrences of removeKey from dictionaries (including nested).
    Modifies the dictionary in place.
    """
    if isinstance(d, dict):
        if removeKey in d:
            del d[removeKey]
        for key, value in list(d.items()):
            remove_key_recursive(value, removeKey)
    elif isinstance(d, list):
        for item in d:
            remove_key_recursive(item, removeKey)

@dataclass
class Decoded:
    portnum: str
    payload: bytes
    telemetry: Optional[dict]
    position: Optional[dict]
    user: Optional[dict]
    bitfield: Optional[int]
    notes: Optional[str] = None
    data_original: Optional[Any] = None  # Store original data for debugging


    @classmethod
    def from_dict(cls, data):
        data = data.copy()  # Avoid modifying the original data
        remove_key_recursive(data, "raw")
        r = cls(
            portnum=data.pop("portnum", ""),
            payload=data.pop("payload", ""),
            telemetry=data.pop("telemetry", None),
            position=data.pop("position", None),
            user=data.pop("user", None),
            bitfield=data.pop("bitfield", None),
            data_original=data,  # Store original data for debugging
        )
        if data.keys():
            # log.warning(f"Decoded.from_dict: Unrecognized keys {data.keys()} in data: {data}")
            r.notes = f"Unrecognized keys: {', '.join(data.keys())}"
        
        return r

@dataclass
class Packet:
    from_: int
    to: int
    decoded: 'Decoded'
    id: int
    rxTime: int
    hopLimit: int
    priority: str
    raw: str
    fromId: str
    toId: str
    data_original: Optional[Any] = None  # Store original data for debugging

    @classmethod
    def from_dict(cls, data):
        raw = data.pop("raw", None)
        decoded = Decoded.from_dict(data.pop("decoded", {}))
        return cls(
            from_=data.get("from", 0),
            to=data.get("to", 0),
            decoded=decoded, # pop not get, because we don't want to show in data_original
            id=data.get("id", 0),
            rxTime=data.get("rxTime", 0),
            hopLimit=data.get("hopLimit", 0),
            priority=data.get("priority", "normal"),
            raw=raw.SerializeToString() if raw else None,
            fromId=data.get("fromId", ""),
            toId=data.get("toId", ""),
            data_original=data  # Store original data for debugging
        )

def onReceive(packet, interface): # called when a packet arrives
    try:
        # Parse the packet first
        parsed_packet = Packet.from_dict(packet)

        if parsed_packet.fromId is None and parsed_packet.decoded.portnum == "NODEINFO_APP":
            # If fromId is None, use the node ID from the decoded data
            fromId = parsed_packet.decoded.user.get('id', None)
            if fromId:
                parsed_packet.fromId = fromId
        
        # Update node information if available
        if hasattr(interface, 'nodes') and parsed_packet.fromId in interface.nodes:
            node_info = interface.nodes[parsed_packet.fromId]
            store_node_info(parsed_packet.fromId, node_info)
        
        # Update telemetry if present
        if parsed_packet.decoded.telemetry:
            update_node_telemetry(parsed_packet.fromId, parsed_packet.decoded.telemetry)
        
        # Generate processing notes
        processing_notes = []
        
        # Check for missing or unusual fields
        if not parsed_packet.fromId:
            processing_notes.append("Missing fromId")
        if not parsed_packet.toId:
            processing_notes.append("Missing toId")
        if parsed_packet.hopLimit == 0:
            processing_notes.append("Zero hops")

        if parsed_packet.decoded.notes:
            processing_notes.append(parsed_packet.decoded.notes)
        
        # Check for unknown telemetry keys
        if False and parsed_packet.decoded.telemetry:
            unknown_tel_keys = set(parsed_packet.decoded.telemetry.keys()) - {
                'batteryLevel', 'voltage', 'channelUtilization', 'airUtilTx', 'temperature'
            }
            if unknown_tel_keys:
                processing_notes.append(f"Unknown telemetry: {','.join(list(unknown_tel_keys)[:2])}")
        
        # Check for unknown position keys
        if False and parsed_packet.decoded.position:
            unknown_pos_keys = set(parsed_packet.decoded.position.keys()) - {
                'latitudeI', 'longitudeI', 'latitude', 'longitude', 'altitude', 'time'
            }
            if unknown_pos_keys:
                processing_notes.append(f"Unknown position: {','.join(list(unknown_pos_keys)[:2])}")
          # Add to global packet list for TUI
        with packet_list_lock:
            packet_info = {
                'id': parsed_packet.id,
                'fromId': parsed_packet.fromId,
                'toId': parsed_packet.toId,
                'portnum': parsed_packet.decoded.portnum,
                'payload': parsed_packet.decoded.payload,
                'rxTime': parsed_packet.rxTime,
                'hopLimit': parsed_packet.hopLimit,
                'priority': parsed_packet.priority,
                'telemetry': parsed_packet.decoded.telemetry,
                'position': parsed_packet.decoded.position,
                'notes': "; ".join(processing_notes) if processing_notes else "",
                'payload_original': parsed_packet.decoded.data_original,
                'packet_original': parsed_packet.data_original
            }
            packet_list.append(packet_info)
            
            # Keep only last 1000 packets in memory
            if len(packet_list) > 1000:
                packet_list.pop(0)
        
        # Store the packet with parsed data
        store_packet(packet, parsed_packet)
            
    except Exception as e:
        error_msg = f"Error processing packet: {e}"
        print(error_msg)
        
        # Add error packet to list with notes
        with packet_list_lock:
            error_packet = {
                'id': packet.get('id', 0),
                'fromId': packet.get('fromId', 'Unknown'),
                'toId': packet.get('toId', 'Unknown'),
                'portnum': 'ERROR',
                'payload': str(packet).encode('utf-8')[:100],
                'rxTime': packet.get('rxTime', int(time.time())),
                'hopLimit': packet.get('hopLimit', 0),
                'priority': packet.get('priority', 'unknown'),
                'telemetry': None,
                'position': None,
                'notes': f"Parse error: {str(e)[:30]}"
            }
            packet_list.append(error_packet)
        
        # Still try to store the raw packet even if parsing fails
        store_packet(packet, None)

def onConnection(interface, topic=pub.AUTO_TOPIC): # called when we (re)connect to the radio
    # defaults to broadcast, specify a destination ID if you wish
    interface.sendText("hello mesh")

def run_meshtastic_interface():
    """Run the meshtastic interface in a separate thread."""
    try:
        # Subscribe to packet events
        pub.subscribe(onReceive, "meshtastic.receive")
        # pub.subscribe(onConnection, "meshtastic.connection.established")
        
        # Connect to device
        name = "Meshtastic_db60"
        addr = "48:CA:43:3C:DB:61"
        interface = meshtastic.serial_interface.SerialInterface('COM4')
        
        print("Meshtastic interface started...")
        
        # Keep the interface running
        while True:
            time.sleep(1)
            
    except Exception as e:
        print(f"Error in meshtastic interface: {e}")

def add_test_packets():
    """Add some test packets and node info for demonstration."""
    import random
    
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
            'longName': node["long_name"],
            'shortName': node["short_name"],
            'hwModel': 'HELTEC_V3',
            'firmwareVersion': '2.3.2',
            'role': 'CLIENT'
        })
    
    test_packets = []
    
    # Create varied test packets with different data types
    for i in range(15):
        portnum_options = ["TEXT_MESSAGE_APP", "POSITION_APP", "TELEMETRY_APP", "NODEINFO_APP", "ROUTING_APP"]
        portnum = random.choice(portnum_options)
        
        # Generate test telemetry data
        telemetry = None
        if portnum == "TELEMETRY_APP" or random.random() < 0.3:
            telemetry = {
                'battery_level': random.randint(20, 100),
                'voltage': round(random.uniform(3.2, 4.2), 2),
                'temperature': round(random.uniform(-10, 40), 1)
            }
            # Sometimes add unknown keys for testing
            if random.random() < 0.3:
                telemetry['unknown_field'] = random.randint(1, 100)
        
        # Generate test position data  
        position = None
        if portnum == "POSITION_APP" or random.random() < 0.2:
            position = {
                'latitude_i': int(random.uniform(40.0, 41.0) * 1e7),  # NYC area
                'longitude_i': int(random.uniform(-74.5, -73.5) * 1e7),
                'altitude': random.randint(0, 500)
            }
            # Sometimes add unknown keys for testing
            if random.random() < 0.2:
                position['unknown_pos_field'] = "test_value"
        
        # Generate notes for some packets
        notes = ""
        if random.random() < 0.3:
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

if __name__ == "__main__":
    # Start meshtastic interface in background thread
    meshtastic_thread = threading.Thread(target=run_meshtastic_interface, daemon=True)
    meshtastic_thread.start()
    
    # Give the interface a moment to start
    time.sleep(2)
    
    # Add test packets
    # add_test_packets()
    
    # Run the TUI
    app = MeshtasticTUI()
    app.run()


# print(interface.scan())

# client = interface.connect(addr)