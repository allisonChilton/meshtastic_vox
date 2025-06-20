"""
GUI components for the Meshtastic Packet Monitor.
Contains the main TUI application and modal dialogs.
"""

import time
import json
from datetime import datetime
import threading
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static, Button, Log, Footer, Label, TabbedContent, TabPane, TextArea
from textual.binding import Binding
from textual.screen import ModalScreen
from pubsub import pub

# Import database and packet functions from the main module
from mt_backend import (
    packet_list, packet_list_lock, subtopics, load_packets_from_database,
    get_node_name, store_node_info
)

import logging

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
    
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Static("Meshtastic Packet Monitor", classes="header", id="header")
        
        with TabbedContent():
            with TabPane("Packets", id="packets_tab"):
                yield Input(placeholder="Filter packets (node ID, portnum, etc.)", id="filter_input")
                with Horizontal(id="controls"):
                    yield Button("All Messages", id="all_button", variant="primary")
                    yield Button("Session Only", id="session_button")
                    yield Button("Long Names", id="name_toggle_button", variant="primary")
                    yield Static(f"Session started: {datetime.fromtimestamp(self.session_start_time).strftime('%H:%M:%S')}", id="session_info")
                yield DataTable(id="packet_table")
                yield Static("Total packets: 0 | Filtered: 0", id="stats")
            
            with TabPane("Topic Monitor", id="topic_tab"):
                yield Input(placeholder="Enter topic name (e.g., meshtastic.receive)", id="topic_input")
                with Horizontal(id="topic_controls"):
                    yield Button("Subscribe", id="subscribe_button", variant="primary")
                    yield Button("Unsubscribe", id="unsubscribe_button")
                    yield Button("Clear Log", id="clear_log_button")
                yield TextArea("", id="topic_log", read_only=True)
        
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
        elif event.button.id == "subscribe_button":
            self.subscribe_to_topic()
        elif event.button.id == "unsubscribe_button":
            self.unsubscribe_from_topic()
        elif event.button.id == "clear_log_button":
            self.clear_topic_log()
    
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
            filtered_packets = packets
        
        # Add filtered packets to table (show most recent first)
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
