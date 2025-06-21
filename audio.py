"""
Audio recording module for Meshtastic.
Provides functionality for recording audio clips from microphone using PyAudio.
"""

import io
import pyaudio
import wave
import threading
import time
from typing import List, Dict, Optional, Callable, Union
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def play(audio_data: bytes, sample_rate: int = 44100):
    """
    Play audio data using PyAudio.
    
    Args:
        audio_data: Raw audio bytes to play
        sample_rate: Sample rate of the audio data (default is 44100Hz)
    """
    audio = pyaudio.PyAudio()
    
    try:
        stream = audio.open(format=pyaudio.paInt16,
                            channels=1,
                            rate=sample_rate,
                            output=True)
        stream.write(audio_data)
    except Exception as e:
        logger.error(f"Error playing audio: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

class AudioEncoder:

    @classmethod
    def encode(cls, audio: bytes):
        """
        Encode audio data to a specific format.
        This is a placeholder for actual encoding logic.
        """
        # For now, just return the raw audio bytes
        return audio

    @classmethod
    def decode(cls, audio: bytes):
        """
        Decode audio data from a specific format.
        This is a placeholder for actual decoding logic.
        """
        # For now, just return the raw audio bytes
        return audio


class AudioDevice:
    """Represents an audio input device."""
    
    def __init__(self, index: int, name: str, max_input_channels: int, default_sample_rate: float):
        self.index = index
        self.name = name
        self.max_input_channels = max_input_channels
        self.default_sample_rate = default_sample_rate
    
    def __str__(self):
        return f"Device {self.index}: {self.name} ({self.max_input_channels} channels, {self.default_sample_rate}Hz)"


class MicrophoneRecorder:
    """Class for recording audio clips from microphone using PyAudio."""
    
    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.is_recording = False
        self.audio_data = []
        self.selected_device_index = None
        
        # Default recording settings
        self.chunk_size = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 44100
        self.record_thread = None
        
    def list_audio_devices(self) -> List[AudioDevice]:
        """List all available audio input devices."""
        devices = []
        device_count = self.audio.get_device_count()
        
        logger.info(f"Found {device_count} audio devices:")
        
        for i in range(device_count):
            try:
                device_info = self.audio.get_device_info_by_index(i)
                
                # Only include devices that support input
                if device_info['maxInputChannels'] > 0:
                    device = AudioDevice(
                        index=i,
                        name=device_info['name'],
                        max_input_channels=device_info['maxInputChannels'],
                        default_sample_rate=device_info['defaultSampleRate']
                    )
                    devices.append(device)
                    logger.info(f"  {device}")
                    
            except Exception as e:
                logger.error(f"Error getting info for device {i}: {e}")
                
        return devices
    
    def get_default_input_device(self) -> Optional[AudioDevice]:
        """Get the default input device."""
        try:
            default_device_info = self.audio.get_default_input_device_info()
            return AudioDevice(
                index=default_device_info['index'],
                name=default_device_info['name'],
                max_input_channels=default_device_info['maxInputChannels'],
                default_sample_rate=default_device_info['defaultSampleRate']
            )
        except Exception as e:
            logger.error(f"Error getting default input device: {e}")
            return None
    
    def select_device(self, device_index: int) -> bool:
        """Select an audio input device by index."""
        try:
            # Validate that the device exists and supports input
            device_info = self.audio.get_device_info_by_index(device_index)
            
            if device_info['maxInputChannels'] == 0:
                logger.error(f"Device {device_index} does not support audio input")
                return False
                
            self.selected_device_index = device_index
            
            # Update sample rate to match device default if needed
            device_sample_rate = int(device_info['defaultSampleRate'])
            if device_sample_rate != self.sample_rate:
                logger.info(f"Updating sample rate from {self.sample_rate} to {device_sample_rate}")
                self.sample_rate = device_sample_rate
                
            logger.info(f"Selected audio device: {device_info['name']}")
            return True
            
        except Exception as e:
            logger.error(f"Error selecting device {device_index}: {e}")
            return False
    
    def start_recording(self, callback: Optional[Callable[[bytes], None]] = None) -> bool:
        """
        Start recording audio from the selected device.
        
        Args:
            callback: Optional callback function that receives audio chunks as they're recorded
            
        Returns:
            True if recording started successfully, False otherwise
        """
        if self.is_recording:
            logger.warning("Recording is already in progress")
            return False
            
        if self.selected_device_index is None:
            # Try to use default input device
            default_device = self.get_default_input_device()
            if default_device:
                self.select_device(default_device.index)
            else:
                logger.error("No audio input device selected and no default device available")
                return False
        
        try:
            # Open audio stream
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.selected_device_index,
                frames_per_buffer=self.chunk_size
            )
            self.is_recording = True
            if self.record_thread is None:
                self.record_thread = threading.Thread(target=self._record_worker, args=(callback,))
                self.record_thread.daemon = True
                self.record_thread.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Error starting recording: {e}")
            self.is_recording = False
            if self.stream:
                self.stream.close()
                self.stream = None
            return False
    
    def _record_worker(self, callback: Optional[Callable[[bytes], None]]):
        """Worker thread for recording audio."""
        try:
            while self.is_recording and self.stream:
                try:
                    # Read audio data from stream
                    audio_chunk = self.stream.read(self.chunk_size, exception_on_overflow=False)
                    self.audio_data.append(audio_chunk)
                    
                    # Call callback if provided
                    if callback:
                        callback(audio_chunk)
                        
                except Exception as e:
                    logger.error(f"Error reading audio data: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Error in recording worker: {e}")
        finally:
            logger.info("Recording worker thread ended")
    
    def pause_recording(self):
        audio_data = self.stop_recording(clear_buffer=False)
        return audio_data
    
    def clear_buffer(self):
        self.audio_data.clear()
    
    def stop_recording(self, clear_buffer: bool = True) -> Optional[bytes]:
        """
        Stop recording and return the recorded audio data.
        
        Returns:
            Recorded audio data as bytes, or None if no data was recorded
        """
        if not self.is_recording:
            logger.warning("No recording in progress")
            return None
            
        self.is_recording = False
        
        # Wait for recording thread to finish
        if self.record_thread:
            self.record_thread.join(timeout=2.0)
            self.record_thread = None
            
        # Close the stream
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
            finally:
                self.stream = None
        
        # Combine all audio chunks
        if self.audio_data:
            recorded_audio = b''.join(self.audio_data)
            logger.info(f"Recording stopped. Captured {len(recorded_audio)} bytes of audio data")

            if clear_buffer:
                self.audio_data.clear()
            return recorded_audio
        else:
            logger.warning("No audio data was recorded")
            return None
    
    def save_recording_to_stream(self, audio_data: bytes, destination: Union[str,io.BytesIO]) -> bool:
        """
        Save recorded audio data to a WAV file.
        
        Args:
            audio_data: Raw audio bytes from recording
            filename: Output filename (should end with .wav) or a writable byte stream
            
        Returns:
            True if saved successfully, False otherwise
        """
        if not destination.writable():
            logger.error("Destination BytesIO stream is not writable")
            return False
        destination.seek(0)
        
        destination.write(audio_data)


    def save_recording_to_file(self, audio_data: bytes, filename: str) -> bool:
        try:
            with wave.open(filename, 'wb') as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(self.audio.get_sample_size(self.format))
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(audio_data)
                
            logger.info(f"Audio saved to {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving audio to {filename}: {e}")
            return False
    
    def get_recording_info(self) -> Dict:
        """Get current recording configuration info."""
        return {
            'is_recording': self.is_recording,
            'selected_device_index': self.selected_device_index,
            'sample_rate': self.sample_rate,
            'channels': self.channels,
            'chunk_size': self.chunk_size,
            'format': self.format,
            'audio_data_length': len(self.audio_data) if self.audio_data else 0
        }
    
    def cleanup(self):
        """Clean up PyAudio resources."""
        if self.is_recording:
            self.stop_recording()
            
        try:
            self.audio.terminate()
            logger.info("PyAudio terminated")
        except Exception as e:
            logger.error(f"Error terminating PyAudio: {e}")


# Example usage
if __name__ == "__main__":
    recorder = MicrophoneRecorder()
    
    try:
        # List available devices
        print("Available audio input devices:")
        devices = recorder.list_audio_devices()
        
        if not devices:
            print("No audio input devices found!")
            exit(1)
        
        # Use default device or let user select
        default_device = recorder.get_default_input_device()
        if default_device:
            print(f"\nUsing default device: {default_device}")
            recorder.select_device(default_device.index)
        else:
            # Select first available device
            recorder.select_device(devices[0].index)
        
        # Record for 5 seconds
        print("\nStarting recording for 5 seconds...")
        recorder.start_recording()
        
        time.sleep(5)
        
        print("Stopping recording...")
        audio_data = recorder.stop_recording()
        
        if audio_data:
            # Save to file
            filename = f"recording_{int(time.time())}.wav"
            if recorder.save_recording_to_file(audio_data, filename):
                print(f"Recording saved to {filename}")
            
    except KeyboardInterrupt:
        print("\nRecording interrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        recorder.cleanup()
