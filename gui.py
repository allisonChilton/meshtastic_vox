"""
GUI components for the Meshtastic Packet Monitor.
Contains the main TUI application and modal dialogs.
"""

from dataclasses import dataclass
import time
import json
from datetime import datetime
import threading
from typing import List
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static, Button, Log, Footer, Label, TabbedContent, TabPane, TextArea, Select
from textual.binding import Binding
from textual.screen import ModalScreen
from pubsub import pub

# Import database and packet functions from the main module
from mt_backend import (
    MESHVOX_PORTNUM, NodeInfo, packet_list, packet_list_lock, load_packets_from_database,
    get_node_name, query_packets, store_node_info
)
from audio import MicrophoneRecorder


import logging

@dataclass
class FormattedPacket:
    """A data class to hold formatted packet information for display."""
    time: str
    from_id: str
    to_id: str
    portnum: str
    payload: str
    hop_limit: str
    priority: str
    telemetry: str
    position: str
    notes: str

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
                    
                    # User Information
                    yield Label("")
                    yield Label("=== User Information ===")
                    user_data = self.packet_data.get('user')
                    if user_data:
                        if isinstance(user_data, dict):
                            yield Label(f"Long Name: {user_data.get('longName', 'N/A')}")
                            yield Label(f"Short Name: {user_data.get('shortName', 'N/A')}")
                            yield Label(f"Macaddr: {user_data.get('macaddr', 'N/A')}")
                            yield Label(f"Hardware Model: {user_data.get('hwModel', 'N/A')}")
                            yield Label(f"Public Key: {user_data.get('publicKey', 'N/A')}")
                            yield Label(f"Role: {user_data.get('role', 'N/A')}")
                            yield Label(f"Is Licensed: {user_data.get('isLicensed', 'N/A')}")
                        else:
                            yield Label(f"User Data: {str(user_data)}")
                    else:
                        yield Label("None")
                    
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
        """Handle button presses in the modal."""
        if event.button.id == "close_button":
            self.dismiss()


class PacketTable(Container):
    """A custom widget that encapsulates the packet table and its controls."""
    
    def __init__(self, session_start_time: float = None, **kwargs):
        super().__init__(**kwargs)
        # Use provided session start time or create a new one
        self.session_start_time = session_start_time or time.time()
    
    def compose(self) -> ComposeResult:
        """Create the packet table widget content."""
        yield Input(placeholder="Filter packets (node ID, portnum, etc.) - use !text to exclude", id="filter_input")
        with Horizontal(id="controls"):
            yield Button("All Messages", id="all_button", variant="primary")
            yield Button("Session Only", id="session_button")
            yield Button("Long Names", id="name_toggle_button", variant="primary")
            yield Static(f"Session started: {datetime.fromtimestamp(self.session_start_time).strftime('%H:%M:%S')}", id="session_info")
        yield DataTable(id="packet_table")
        yield Static("Total packets: 0 | Filtered: 0", id="stats")

    def add_columns(self, *args):
        table = self.query_one(DataTable)
        table.add_columns(*args)
        table.cursor_type = "row"

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

    #vox_controls {
        width: 30%;
        padding: 0;
    }

    #recording_controls {
        height: 70%;
        padding: 1;
        border: solid white;
    }
    
    #recording_control_area {
        padding: 0;
        border-bottom: solid white;
    }

    #encode_control_area {
        padding: 0;
    }
    
    #message_sending_area {
        height: 30%;
        border: solid white;
        padding: 1;
    }
    
    #destination_select {
        width: 70%;
        margin-left: 1;
    }
    
    #send_button {
        width: 100%;
        margin-top: 1;
    }
    
    #message_area {
        width: 70%;
        padding: 1;
        border: solid white;
    }

    #record_controls {
        margin: 1 0;
    }

    #encode_controls {
        margin: 1 0;
    }
    
    #recording_time_label {
        margin: 1 0;
        text-align: center;
        color: green;
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
    
    #detail_content_left Label {
        text-wrap: wrap;
        width: 100%;
    }
    
    #detail_content_right Label {
        text-wrap: wrap;
        width: 100%;
    }
    
    #close_button {
        margin: 1;
        width: 20;
    }
    
    #topic_input {
        margin: 1;
        height: 3;
    }
    
    #topic_log {
        margin: 1;
        height: 1fr;
    }
    
    #topic_controls {
        margin: 1;
        height: 3;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),        
        Binding("ctrl+r", "refresh", "Refresh"),        
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
        self.current_topic = None  # Track current subscription topic
        self.topic_callback = None  # Store callback reference
        self.filter_debounce_timer = None  # Timer for debouncing filter input
        self.node_name_cache = {}  # Cache for node names to improve performance
        self._last_rows = []
        self._last_row_key = None        # Microphone recording variables
        self.microphone_recorder = MicrophoneRecorder()
        self.recording_start_time = None
        self.accumulated_time = 0.0  # Total accumulated recording time
        self.recording_timer = None
        self.is_paused = False
        self.last_recorded_audio = None  # Store the last recording for playback
        
        # Codec variables
        self.codec = None  # Will be initialized in on_mount
        self.encoded_audio_data = None  # Store encoded audio bytes
        self.encoded_metadata = None  # Store encoding metadata

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        # yield Static("Meshtastic Packet Monitor", classes="header", id="header")
        
        with TabbedContent():
            with TabPane("Packets", id="packets_tab"):
                yield PacketTable(session_start_time=self.session_start_time, id="packet_table_widget")
            
            with TabPane("Topic Monitor", id="topic_tab"):
                yield Input(placeholder="Enter topic name (e.g., meshtastic.receive)", id="topic_input")
                with Horizontal(id="topic_controls"):
                    yield Button("Subscribe", id="subscribe_button", variant="primary")
                    yield Button("Unsubscribe", id="unsubscribe_button")
                    yield Button("Clear Log", id="clear_log_button")
                    yield Button("Show Topics", id="show_topics_button", variant="success")
                yield TextArea("", id="topic_log", read_only=True)
            
            with TabPane("Vox Msg", id="vox_tab"):
                with Horizontal():
                    # Left side - Message area placeholder
                    with VerticalScroll(id="message_area"):
                        yield PacketTable(id="packet_table_vox")
                      # Right side - Recording controls
                    with Vertical(id="vox_controls"):
                        with Vertical(id="recording_controls"):
                            with Vertical(id="recording_control_area"):
                                yield Label("Input Device:")
                                yield Select([], id="input_device_select")
                                yield Label("Output Device:")
                                yield Select([], id="output_device_select")
                                with Horizontal(id="record_controls"):
                                    yield Button("Record", id="record_button", variant="primary")
                                    yield Button("Play", id="play_button", disabled=True)
                                    yield Button("Reset", id="reset_button", disabled=True)
                                
                                yield Label("Recording time: 00:00", id="recording_time_label")
                            # Horizontal separator between recording and encoding controls
                            with Vertical(id="encode_control_area"):
                                yield Label("Codec:")
                                yield Select([("12.5 Hz", "12_5hz"), ("25 Hz", "25hz"), ("50 Hz", "50hz")], 
                                        value="12_5hz", id="codec_select", allow_blank=False)
                                
                                with Horizontal(id="encode_controls"):
                                    yield Button("Encode", id="encode_button", disabled=True)
                                    yield Button("Preview", id="preview_button", disabled=True)
                                # Compression statistics display
                                yield Label("Compression Stats:", id="stats_header")
                                yield Label("Original size: -- bytes", id="original_size_label")
                                yield Label("Compressed size: -- bytes", id="compressed_size_label")
                                yield Label("Compression ratio: --", id="compression_ratio_label")
                                
                        # Message sending area
                        with Vertical(id="message_sending_area"):
                            # yield Label("Message Sending:", id="message_sending_label")
                            with Horizontal():
                                yield Label("Destination:")
                                yield Select([], id="destination_select")
                            yield Button("Send", id="send_button", disabled=True)
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Called when app starts."""
        table = self.query_one("#packet_table_widget", PacketTable)
        table.add_columns("Time", "From", "To", "Port", "Payload", "Hops", "Priority", "Telemetry", "Position", "Notes")
        vox_table = self.query_one("#packet_table_vox", PacketTable)
        vox_table.add_columns("Time", "From", "To")
        
        # Load all packets from database on startup
        load_packets_from_database()
          # Start updating the table periodically
        self.set_interval(2.0, self.update_table)
          # Populate audio devices for Vox Msg tab
        self.populate_audio_devices()
        
        # Populate destination dropdown for voice messages
        self.populate_destination_dropdown()
          # Initialize codec for audio encoding
        try:
            import codec
            # Get initial codec type from dropdown (defaults to 12_5hz)
            codec_select = self.query_one("#codec_select", Select)
            codec_type = codec_select.value or "12_5hz"
            self.codec = codec.AudioCodec(codec_type)
            self.log(f"Audio codec initialized successfully ({codec_type})")
        except Exception as e:
            self.log(f"Failed to initialize codec: {e}")
            self.codec = None
        
        # Focus the filter input
        self.query_one("#filter_input", Input).focus()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._update_hex_id()

    def _update_hex_id(self):
        active_tab = self.get_active_tab()
        if not active_tab:
            return  # No active tab, nothing to do
        try:
            name_btn = active_tab.query_one("#name_toggle_button", Button)
        except:
            name_btn = None

        if name_btn:
            if self.show_long_names:
                name_btn.label = "Long Names"
                name_btn.variant = "primary"
            else:
                name_btn.label = "Hex IDs"
                name_btn.variant = "default"
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        active_tab = self.get_active_tab()
        if not active_tab:
            return  # No active tab, nothing to do

        if event.button.id == "all_button":
            self.session_filter_active = False
            event.button.variant = "primary"
            active_tab.query_one("#session_button", Button).variant = "default"
            self.update_table()
        elif event.button.id == "session_button":
            self.session_filter_active = True
            event.button.variant = "primary"
            active_tab.query_one("#all_button", Button).variant = "default"
            self.update_table()
        elif event.button.id == "name_toggle_button":
            self.show_long_names = not self.show_long_names
            self._update_hex_id()
            self.update_table()
            # Update destination dropdown when long names setting changes
            self.populate_destination_dropdown()
        elif event.button.id == "subscribe_button":
            self.subscribe_to_topic()
        elif event.button.id == "unsubscribe_button":
            self.unsubscribe_from_topic()
        elif event.button.id == "clear_log_button":
            self.clear_topic_log()
        elif event.button.id == "show_topics_button":
            self.show_available_topics()
        elif event.button.id == "record_button":
            self.toggle_recording()
        elif event.button.id == "reset_button":
            self.reset_recording()
        elif event.button.id == "play_button":
            self.play_recording()
        elif event.button.id == "encode_button":
            self.encode_audio()
        elif event.button.id == "preview_button":
            self.preview_encoded_audio()
        elif event.button.id == "send_button":
            self.send_voice_message()
    
    def on_input_changed(self, event: Input.Changed) -> None:
        """Called when filter input changes."""
        if event.input.id == "filter_input":
            self.filter_text = event.value.lower()
            
            # Cancel any existing debounce timer
            if self.filter_debounce_timer:
                self.filter_debounce_timer.cancel()
              # Set a new timer to delay the filter update
            self.filter_debounce_timer = threading.Timer(0.3, self._delayed_update_table)
            self.filter_debounce_timer.start()
    
    def on_select_changed(self, event: Select.Changed) -> None:
        """Called when a select dropdown value changes."""
        if event.select.id == "codec_select":
            self.change_codec(event.value)
    
    def _delayed_update_table(self) -> None:
        """Safely update table from timer thread."""
        # Use call_from_thread to safely update UI from timer thread
        self.call_from_thread(self.update_table)
    
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Called when a row is selected in the packet table."""
        table = event.data_table
        if table.id == "packet_table":
            # Get the selected row index
            row_index = event.cursor_row
            
            with packet_list_lock:
                packets = packet_list.copy()
            
            packets = [x.to_dict() for x in packets]

            # Apply current filters to get the same filtered list as displayed
            if self.session_filter_active:
                packets = [p for p in packets if p.get('rxTime', 0) >= self.session_start_time]
            
            if self.filter_text:
                filtered_packets = []
                for packet in packets:
                    # Get node names for from and to IDs using cache
                    from_id = packet.get('fromId', '')
                    to_id = packet.get('toId', '')
                    from_long_name = self.get_cached_node_name(from_id) if from_id else ''
                    to_long_name = self.get_cached_node_name(to_id) if to_id else ''
                    
                    # Search in various fields including new columns and long names
                    search_text = f"{from_id} {to_id} {from_long_name} {to_long_name} {packet.get('portnum', '')} {packet.get('payload', '')} {packet.get('priority', '')} {packet.get('notes', '')}".lower()
                    # Also search in telemetry and position data
                    if packet.get('telemetry'):
                        search_text += f" {str(packet['telemetry'])}".lower()
                    if packet.get('position'):
                        search_text += f" {str(packet['position'])}".lower()
                    
                    # Check if this is an exclusion filter (starts with !)
                    if self.filter_text.startswith('!'):
                        # Exclude packets that contain the substring after the !
                        exclude_text = self.filter_text[1:]  # Remove the ! prefix
                        if exclude_text and exclude_text not in search_text:
                            filtered_packets.append(packet)
                    else:
                        # Normal inclusion filter
                        if self.filter_text in search_text:
                            filtered_packets.append(packet)
                packets = filtered_packets
            
            # Get the packet data for the selected row (accounting for reverse order display)
            display_packets = list(reversed(packets[-100:]))
            if 0 <= row_index < len(display_packets):
                selected_packet = display_packets[row_index]
                # Show the detail modal
                self.push_screen(PacketDetailModal(selected_packet))

    def get_active_tab(self) -> TabPane | None:
        """Get the currently active tab pane."""
        tabbed_content = self.query_one(TabbedContent)
        return tabbed_content.active_pane if tabbed_content else None

    def _format_packet(self, packet: dict, show_long_names: bool) -> FormattedPacket:
        """Format a packet dictionary into a string for display."""
        time_str = datetime.fromtimestamp(packet.get('rxTime', 0)).strftime('%H:%M:%S') if packet.get('rxTime') else 'N/A'
            # Handle From ID with name toggle
        from_id = packet.get('fromId', 'N/A')
        if show_long_names and from_id != 'N/A':
            from_display = self.get_cached_node_name(from_id)
        else:
            from_display = from_id
            
        # Handle To ID with name toggle  
        to_id = packet.get('toId', 'N/A')
        if show_long_names and to_id != 'N/A':
            to_display = self.get_cached_node_name(to_id)
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

        formatted_packet = FormattedPacket(
            time=time_str,
            from_id=from_display,
            to_id=to_display,
            portnum=portnum,
            payload=payload_str,
            hop_limit=hop_limit,
            priority=priority,
            telemetry=telemetry_str,
            position=position_str,
            notes=notes
        )

        return formatted_packet

    
    def update_table(self) -> None:
        """Update the packet table with filtered data."""
        active_tab = self.get_active_tab()
        try:
            table = active_tab.query_one("#packet_table", DataTable)
        except:
            return # no packet table in this tab
        stats = active_tab.query_one("#stats", Static)
        
        with packet_list_lock:
            packets = packet_list.copy()

        packets = [x.to_dict() for x in packets]
        
        # Apply session filter if active
        if self.session_filter_active:
            packets = [p for p in packets if p.get('rxTime', 0) >= self.session_start_time]
          # Clear existing rows
        rows = []
        
        # Filter packets based on filter text
        exclude = self.filter_text.startswith('!')
        portnum = MESHVOX_PORTNUM if active_tab.id == "vox_tab" else None
        filtered_packets = query_packets(limit=100, substring=self.filter_text, exclude=exclude, portnum=portnum)
        filtered_packets = [p.to_dict() for p in filtered_packets]
        
        # Add filtered packets to table (show most recent first)
        for packet in reversed(filtered_packets[-100:]):  # Show last 100 packets
            formatted_packet = self._format_packet(packet, self.show_long_names)            
            if active_tab.id == "vox_tab":
                # For Vox Msg tab, only show basic info
                rows.append((formatted_packet.time, formatted_packet.from_id, formatted_packet.to_id))
            else:
                rows.append(( formatted_packet.time, formatted_packet.from_id, formatted_packet.to_id, formatted_packet.portnum, formatted_packet.payload, formatted_packet.hop_limit, formatted_packet.priority, formatted_packet.telemetry, formatted_packet.position, formatted_packet.notes))
        
        if tuple(self._last_rows) == tuple(rows):
            return 

        self._last_rows = rows
        # Get the currently highlighted row's time and from_id before clearing
        highlighted_row_index = table.cursor_row if table.row_count > 0 else None
        highlighted_time = None
        highlighted_from_id = None
        if highlighted_row_index is not None and 0 <= highlighted_row_index < len(self._last_rows):
            highlighted_time = self._last_rows[highlighted_row_index][0]
            highlighted_from_id = self._last_rows[highlighted_row_index][1]

        table.clear()
        selected_index = 0
        for idx, row in enumerate(rows):
            table.add_row(*row)
            # Try to find the row matching the previously highlighted row
            if highlighted_time is not None and highlighted_from_id is not None:
                if row[0] == highlighted_time and row[1] == highlighted_from_id:
                    selected_index = idx + 1        
        if rows:
            table.move_cursor(row=selected_index)
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
        # Unsubscribe from any topic before quitting
        if self.current_topic and self.topic_callback:
            pub.unsubscribe(self.topic_callback, self.current_topic)
        self.exit()
    
    def subscribe_to_topic(self) -> None:
        """Subscribe to the topic entered in the topic input field."""
        topic_input = self.query_one("#topic_input", Input)
        topic_log = self.query_one("#topic_log", TextArea)
        
        topic = topic_input.value.strip()
        if not topic:
            topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Please enter a topic name\n"
            return
        
        # Unsubscribe from current topic if any
        if self.current_topic and self.topic_callback:
            pub.unsubscribe(self.topic_callback, self.current_topic)
          # Create callback function
        def updateTopicLog(*args, **kwargs):
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{timestamp}] Topic: {topic}\n"
            
            # Handle known parameters
            if 'interface' in kwargs:
                log_entry += f"  Interface: {kwargs['interface']}\n"
            if 'line' in kwargs:
                log_entry += f"  Line: {kwargs['line']}\n"
            
            # Show all args and kwargs for debugging
            if args:
                log_entry += f"  Args: {args}\n"
            if kwargs:
                log_entry += f"  Kwargs: {kwargs}\n"
            log_entry += "---\n"
            
            # Update the text area (this needs to be done on the main thread)
            try:
                self.call_later(self._update_topic_log, log_entry)
            except Exception as e:
                # Fallback if call_later fails
                logging.error(f"Topic log update error: {e}")
                # print(log_entry)        # Subscribe to the topic
        try:
            pub.subscribe(updateTopicLog, topic)
            self.current_topic = topic
            self.topic_callback = updateTopicLog
            
            topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] Subscribed to topic: {topic}\n"
            topic_log.text += "---\n"
        except Exception as e:
            topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] ERROR subscribing to {topic}: {e}\n"
    
    def unsubscribe_from_topic(self) -> None:
        """Unsubscribe from the current topic."""
        topic_log = self.query_one("#topic_log", TextArea)
        
        if self.current_topic and self.topic_callback:
            try:
                pub.unsubscribe(self.topic_callback, self.current_topic)
                topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] Unsubscribed from topic: {self.current_topic}\n"
                topic_log.text += "---\n"
                self.current_topic = None
                self.topic_callback = None
            except Exception as e:
                topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] ERROR unsubscribing: {e}\n"
        else:
            topic_log.text += f"[{datetime.now().strftime('%H:%M:%S')}] No active subscription to unsubscribe from\n"
    
    def clear_topic_log(self) -> None:
        """Clear the topic log."""
        topic_log = self.query_one("#topic_log", TextArea)
        topic_log.text = ""
    
    def _update_topic_log(self, log_entry: str) -> None:
        """Update the topic log (called from main thread)."""
        topic_log = self.query_one("#topic_log", TextArea)
        topic_log.text += log_entry
    
    def show_available_topics(self) -> None:
        """Show all available subtopics in the log."""
        topic_log = self.query_one("#topic_log", TextArea)
        
        timestamp = datetime.now().strftime('%H:%M:%S')
        topic_log.text += f"[{timestamp}] Available Topics:\n"
        topic_log.text += "=" * 40 + "\n"
        
        # Import subtopics from the backend
        from mt_backend import subtopics
        
        if subtopics:
            for i, (topic, description) in enumerate(sorted(subtopics.items()), 1):
                if description:
                    topic_log.text += f"{i:2d}. {topic}\n"
                    topic_log.text += f"    {description}\n\n"
                else:
                    topic_log.text += f"{i:2d}. {topic}\n\n"
            topic_log.text += f"Total: {len(subtopics)} topics available\n"
        else:
            topic_log.text += "No topics available yet. Topics are discovered as packets are received.\n"
        
        topic_log.text += "=" * 40 + "\n"
        topic_log.text += f"[{timestamp}] End of topic list\n\n"
    
    def get_cached_node_name(self, node_id: str) -> str:
        """Get node name with caching for better performance."""
        if not node_id or node_id == 'N/A':
            return node_id
            
        # Check cache first
        if node_id in self.node_name_cache:
            return self.node_name_cache[node_id]
            
        # Get name and cache it
        name = get_node_name(node_id)
        self.node_name_cache[node_id] = name
        return name
    
    def clear_node_name_cache(self) -> None:
        """Clear the node name cache when nodes are updated."""
        self.node_name_cache.clear()
    
    def populate_audio_devices(self) -> None:
        """Populate the audio device dropdowns."""
        try:
            # Get input devices
            input_devices = self.microphone_recorder.list_audio_devices()
            input_options = [(device.name, device.index) for device in input_devices]
            
            # For now, use same devices for output (this could be enhanced later)
            output_options = input_options.copy()
            
            # Update the Select widgets
            input_select = self.query_one("#input_device_select", Select)
            output_select = self.query_one("#output_device_select", Select)
            
            input_select.set_options(input_options)
            output_select.set_options(output_options)
              # Select default device if available
            default_device = self.microphone_recorder.get_default_input_device()
            if default_device and input_options:
                input_select.value = default_device.index
                # Also set output device to same default
                output_select.value = default_device.index
                
        except Exception as e:
            self.log(f"Error populating audio devices: {e}")
    
    def populate_destination_dropdown(self) -> None:
        """Populate the destination dropdown with known nodes."""
        try:
            destination_select = self.query_one("#destination_select", Select)
            
            # Get all known nodes from the backend
            from mt_backend import get_all_nodes
            
            # Try to get nodes, but handle if function doesn't exist yet
            try:
                nodes: List[NodeInfo] = get_all_nodes()
            except (AttributeError, ImportError):
                # Fallback to empty nodes dict if function not available
                nodes = []
            
            # Create options for the dropdown
            options = []
            for node_info in nodes:
                if self.show_long_names:
                    display_name = node_info.long_name if hasattr(node_info, "long_name") else str(node_info.node_id)
                else:
                    display_name = node_info.node_id
                if display_name: # omit empty names
                    options.append((display_name, node_info.node_id))

            # Sort options by display name (case-insensitive)
            options.sort(key=lambda x: x[0].lower())
            
            # Add broadcast option
            options.insert(0, ("Broadcast", "^all"))
            
            # Update the select widget
            destination_select.set_options(options)
            
        except Exception as e:
            self.log(f"Error populating destination dropdown: {e}")
    
    def send_voice_message(self) -> None:
        """Send the encoded voice message (stub implementation)."""
        try:
            destination_select = self.query_one("#destination_select", Select)
            destination = destination_select.value
            
            if not destination:
                self.log("No destination selected")
                return
                
            # Check if we have encoded data
            if not hasattr(self, 'encoded_audio_data') or not self.encoded_audio_data:
                self.log("No encoded audio data to send")
                return
                
            # TODO: Implement actual message building and sending logic
            self.log(f"Sending voice message to {destination} (STUB - not implemented yet)")
            self.log(f"Encoded data size: {len(self.encoded_audio_data)} bytes")
            
        except Exception as e:
            self.log(f"Error sending voice message: {e}")
    
    def toggle_recording(self) -> None:
        """Toggle recording pause/resume (record button behavior)."""
        record_button = self.query_one("#record_button", Button)
        reset_button = self.query_one("#reset_button", Button)
        play_button = self.query_one("#play_button", Button)
        encode_button = self.query_one("#encode_button", Button)
        
        if not self.microphone_recorder.is_recording and not self.is_paused:
            # Start recording for the first time
            input_select = self.query_one("#input_device_select", Select)
            if input_select.value is not None:
                self.microphone_recorder.select_device(input_select.value)
            
            if self.microphone_recorder.start_recording():
                # Start new recording session
                self.recording_start_time = time.time()
                self.is_paused = False
                
                # Update UI
                record_button.label = "Pause"
                record_button.variant = "warning"
                reset_button.disabled = False
                play_button.disabled = True  # Disable play while actively recording
                
                # Start timer to update recording time
                self.recording_timer = self.set_interval(1.0, self.update_recording_time)
                
                self.log("Recording started")
            else:
                self.log("Failed to start recording")
                
        elif self.microphone_recorder.is_recording and not self.is_paused:
            # Pause recording - accumulate time and enable play button
            if self.recording_start_time:
                self.accumulated_time += time.time() - self.recording_start_time
                self.recording_start_time = None
            
            self.is_paused = True
            record_button.label = "Resume"
            record_button.variant = "success"
            play_button.disabled = False  # Enable play when paused
            
            # Stop timer
            if self.recording_timer:
                self.recording_timer.stop()
                self.recording_timer = None
                
            self.log("Recording paused")
            audio_data = self.microphone_recorder.pause_recording()
            if audio_data:
                self.last_recorded_audio = audio_data
                encode_button.disabled = False  # Enable encoding after recording
            
        elif self.is_paused:
            # Resume recording - restart timing
            self.recording_start_time = time.time()
            self.is_paused = False
            record_button.label = "Pause"
            record_button.variant = "warning"
            play_button.disabled = True  # Disable play while actively recording
            
            # Restart timer
            self.recording_timer = self.set_interval(1.0, self.update_recording_time)
            
            self.log("Recording resumed")
            self.microphone_recorder.start_recording()
    
    def update_recording_time(self) -> None:
        """Update the recording time label using accumulated time."""
        current_elapsed = 0.0

        # Add current session time if recording is active
        if self.recording_start_time and not self.is_paused:
            current_elapsed = time.time() - self.recording_start_time

        total_time = self.accumulated_time + current_elapsed
        minutes = int(total_time // 60)
        seconds = int(total_time % 60)
        time_str = f"Recording time: {minutes:02d}:{seconds:02d}"
        self.query_one("#recording_time_label", Label).update(time_str)

    def reset_recording(self) -> None:
        """Stop and reset the recording completely."""
        record_button = self.query_one("#record_button", Button)
        reset_button = self.query_one("#reset_button", Button)
        play_button = self.query_one("#play_button", Button)
        time_label = self.query_one("#recording_time_label", Label)
        encode_button = self.query_one("#encode_button", Button)
        preview_button = self.query_one("#preview_button", Button)
        
        # Stop recording if active
        if self.microphone_recorder.is_recording:
            self.microphone_recorder.stop_recording()
        else:
            self.microphone_recorder.clear_buffer()
        
        # Reset all UI elements
        record_button.label = "Record"
        record_button.variant = "primary"
        reset_button.disabled = True
        time_label.update("Recording time: 00:00")
        
        # Stop timer
        if self.recording_timer:
            self.recording_timer.stop()
            self.recording_timer = None          # Reset state
        self.recording_start_time = None
        self.accumulated_time = 0.0  # Reset accumulated time
        self.is_paused = False
        self.last_recorded_audio = None
        play_button.label = "Play"
        encode_button.disabled = True  # Disable encoding until new recording
        encode_button.variant = "default"
        encode_button.label = "Encode"
        preview_button.disabled = True  # Disable preview until encoding
        
        # Reset compression stats
        self.reset_compression_stats()
        
        self.log("Recording reset")

    def _play_audio(self, audio_data: bytes, sample_rate: float, play_button) -> None:            
        # Create a temporary wave file in memory to play
        import io
        import wave
        import threading
        
        # # Create a BytesIO buffer and write WAV data
        # audio_buffer = io.BytesIO()
        # with wave.open(audio_buffer, 'wb') as wav_file:
        #     wav_file.setnchannels(self.microphone_recorder.channels)
        #     wav_file.setsampwidth(self.microphone_recorder.audio.get_sample_size(self.microphone_recorder.format))
        #     wav_file.setframerate(self.microphone_recorder.sample_rate)
        #     wav_file.writeframes(audio_data)
        
        # audio_buffer.seek(0)

        previous_label = play_button.label  # Store previous label
        
        # Play audio in a separate thread
        def play_audio_thread():
            try:
                # Disable the play button during playback
                self.call_from_thread(lambda: setattr(play_button, "disabled", True))
                self.call_from_thread(lambda: setattr(play_button, "label", "Playing..."))
                
                # Open output stream
                output_stream = self.microphone_recorder.audio.open(
                    format=self.microphone_recorder.format,
                    channels=self.microphone_recorder.channels,
                    rate=sample_rate,
                    output=True
                )
                
                # Play the audio data
                chunk_size = self.microphone_recorder.chunk_size
                
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    output_stream.write(chunk)
                
                output_stream.stop_stream()
                output_stream.close()
                
                self.call_from_thread(lambda: self.log("Playback completed"))
                
            except Exception as e:
                self.call_from_thread(lambda: self.log(f"Error during playback: {e}"))
            finally:
                # Re-enable the play button
                self.call_from_thread(lambda: setattr(play_button, "disabled", False))
                self.call_from_thread(lambda: setattr(play_button, "label", previous_label))
        
        # Start playback in separate thread
        playback_thread = threading.Thread(target=play_audio_thread, daemon=True)
        playback_thread.start()
        
        self.log("Starting playback...")
    
    def play_recording(self) -> None:
        """Play the last recorded audio."""
        if not self.last_recorded_audio:
            self.log("No recording available to play")
            return
            
        play_button = self.query_one("#play_button", Button)
        
        try:
            self._play_audio(self.last_recorded_audio, self.microphone_recorder.sample_rate, play_button)
            
        except Exception as e:
            self.log(f"Error starting playback: {e}")
            play_button.disabled = False
            play_button.label = "Play"
    
    def change_codec(self, codec_type: str) -> None:
        """Change the codec type and reinitialize it."""
        try:
            # Free the existing codec if it exists
            if self.codec and hasattr(self.codec, 'free'):
                self.codec.free()
            
            # Import and initialize the new codec
            import codec
            self.codec = codec.AudioCodec(codec_type)
            self.log(f"Codec changed to {codec_type}")
            
            # Reset any encoded audio since we changed the codec
            self.encoded_audio_data = None
            self.encoded_metadata = None
            
            # Update button states - disable preview since we don't have encoded data anymore
            preview_button = self.query_one("#preview_button", Button)
            preview_button.disabled = True
              # Reset encode button to original state
            encode_button = self.query_one("#encode_button", Button)
            encode_button.variant = "default"
            encode_button.label = "Encode"
            
            # Reset compression stats since codec changed
            self.reset_compression_stats()
            
        except Exception as e:
            self.log(f"Failed to change codec to {codec_type}: {e}")
            self.codec = None
    
    def encode_audio(self) -> None:
        """Encode the recorded audio using the codec."""
        if not self.last_recorded_audio:
            self.log("No recorded audio to encode")
            return
            
        if not self.codec:
            self.log("Codec not available")
            return
            
        try:
            # Convert raw audio bytes to tensor
            import numpy as np
            import torch
            
            # Convert bytes to numpy array (assuming 16-bit PCM)
            audio_array = np.frombuffer(self.last_recorded_audio, dtype=np.int16)
            
            # Convert to float tensor and normalize
            audio_tensor = torch.from_numpy(audio_array).float() / 32768.0
            
            # Add batch dimension and ensure it's 2D (batch, samples)
            if audio_tensor.dim() == 1:
                audio_tensor = audio_tensor.unsqueeze(0)
            
            # Encode the audio
            _, encoded_data, metadata = self.codec.encode_audio(audio_tensor, sample_rate=self.microphone_recorder.sample_rate)
            
            # Store the encoded data
            self.encoded_audio_data = encoded_data
            self.encoded_metadata = metadata
              # Enable preview button
            preview_button = self.query_one("#preview_button", Button)
            preview_button.disabled = False
            
            # Enable send button
            try:
                send_button = self.query_one("#send_button", Button)
                send_button.disabled = False
            except:
                pass  # Send button might not exist in current context
            
            # Update encode button to show success
            encode_button = self.query_one("#encode_button", Button)
            encode_button.variant = "success"
            encode_button.label = "Encoded"
              # Log compression stats
            original_size = len(self.last_recorded_audio)
            compressed_size = len(encoded_data)
            compression_ratio = original_size / compressed_size if compressed_size > 0 else 0
            
            self.log(f"Audio encoded: {original_size} → {compressed_size} bytes (ratio: 1:{compression_ratio:.0f})")
            
            # Update compression stats in GUI
            self.update_compression_stats(original_size, compressed_size, compression_ratio)
            
        except Exception as e:
            self.log(f"Encoding failed: {e}")
    
    def update_compression_stats(self, original_size: int, compressed_size: int, compression_ratio: float) -> None:
        """Update the compression statistics display in the GUI."""
        try:
            # Update the labels with compression statistics
            original_label = self.query_one("#original_size_label", Label)
            compressed_label = self.query_one("#compressed_size_label", Label)
            ratio_label = self.query_one("#compression_ratio_label", Label)
            
            # Format file sizes in a human-readable format
            def format_bytes(bytes_val):
                if bytes_val >= 1024:
                    return f"{bytes_val / 1024:.1f} KB"
                else:
                    return f"{bytes_val} bytes"
            
            original_label.update(f"Original size: {format_bytes(original_size)}")
            compressed_label.update(f"Compressed size: {format_bytes(compressed_size)}")
            ratio_label.update(f"Compression ratio: 1:{compression_ratio:.1f}")
            
        except Exception as e:
            self.log(f"Failed to update compression stats: {e}")
    
    def reset_compression_stats(self) -> None:
        """Reset the compression statistics display to default values."""
        try:
            original_label = self.query_one("#original_size_label", Label)
            compressed_label = self.query_one("#compressed_size_label", Label)
            ratio_label = self.query_one("#compression_ratio_label", Label)
            
            original_label.update("Original size: -- bytes")
            compressed_label.update("Compressed size: -- bytes")
            ratio_label.update("Compression ratio: --")
            
        except Exception as e:
            self.log(f"Failed to reset compression stats: {e}")
    
    def preview_encoded_audio(self) -> None:
        """Preview the encoded audio by decoding and playing it."""
        if not self.encoded_audio_data or not self.encoded_metadata:
            self.log("No encoded audio to preview")
            return
            
        if not self.codec:
            self.log("Codec not available")
            return
            
        try:
            prev_btn = self.query_one("#preview_button", Button)
            tgt_sr = self.microphone_recorder.sample_rate
            # Decode the audio
            audio_data = self.codec.decode_audio(self.encoded_audio_data, metadata=self.encoded_metadata, target_sample_rate=tgt_sr, as_bytes=True)

            self._play_audio(audio_data, tgt_sr, prev_btn)
            
        except Exception as e:
            self.log(f"Preview failed: {e}")

