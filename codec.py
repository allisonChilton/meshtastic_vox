"""
Audio codec module for Meshtastic voice messaging.
Provides audio compression and decompression with efficient bit packing.
Note: Full ML codec functionality requires torch, torchaudio, numpy, and focalcodec.
"""

import io
import os
from typing import Tuple, Union, BinaryIO, Optional
import logging

# Set up logging
logger = logging.getLogger(__name__)

# Optional ML imports
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger.warning("NumPy not available. ML codec features will be disabled.")

try:
    import torch
    import torchaudio
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available. ML codec features will be disabled.")

try:
    import focalcodec
    FOCALCODEC_AVAILABLE = True
except ImportError:
    FOCALCODEC_AVAILABLE = False
    logger.warning("FocalCodec not available. Audio encoding/decoding will be disabled.")

# Check if all ML dependencies are available
ML_AVAILABLE = NUMPY_AVAILABLE and TORCH_AVAILABLE and FOCALCODEC_AVAILABLE


class CodecConfig:
    """Configuration for codec models."""
    
    CONFIGS = {
        "12_5hz": "lucadellalib/focalcodec_12_5hz",
        "25hz": "lucadellalib/focalcodec_25hz", 
        "50hz": "lucadellalib/focalcodec_50hz"
    }
    
    @classmethod
    def get_config(cls, name: str) -> str:
        """Get configuration string by name."""
        return cls.CONFIGS.get(name, cls.CONFIGS["25hz"])


class BitPacker:
    """Utility class for bit packing and unpacking operations."""
    
    @staticmethod
    def pack_codes(codes: Union[torch.Tensor, np.ndarray]) -> Tuple[int, np.ndarray, int, int]:
        """
        Pack codes into bytes for minimal size.
        
        Args:
            codes: Tensor or array of shape (batch, time, n_bits)
            
        Returns:
            n_bits: Number of bits per code
            packed: Packed byte array
            num_valid: Number of valid codes
            batches: Number of batches
        """
        if hasattr(codes, "cpu"):
            codes = codes.cpu().numpy()
            
        # Convert to binary (0 or 1)
        bits = (codes > 0).astype(np.uint8)
        
        # Flatten: (batch, time, n_bits) -> (batch, time * n_bits)
        bits = bits.reshape(bits.shape[0], -1)
        
        n_bits = codes.shape[2]
        n_bytes = (bits.shape[1] + 7) // 8
        
        # Pack bits into bytes
        packed = np.packbits(bits, axis=-1, bitorder='big')
        packed = packed.reshape(-1, n_bytes).flatten()
        
        valid_codes = codes.shape[1]
        batches = codes.shape[0]
        
        logger.debug(f"Packed {batches} batches, {valid_codes} codes, {n_bits} bits each")
        return n_bits, packed, valid_codes, batches
    @staticmethod
    def unpack_codes(data_bytes: bytes, n_bits: int, num_valid: int, batches: int) -> Union[torch.Tensor, np.ndarray]:
        """
        Unpack bytes back into codes.
        
        Args:
            data_bytes: Packed byte data
            n_bits: Number of bits per code
            num_valid: Number of valid codes
            batches: Number of batches
            
        Returns:
            Unpacked codes as tensor or array
        """
        bits = np.unpackbits(np.frombuffer(data_bytes, dtype=np.uint8), bitorder='big')
        
        # Calculate expected total bits
        expected_bits = batches * num_valid * n_bits
        
        # Trim any padding bits
        bits = bits[:expected_bits]
        
        logger.debug(f"Unpacked {len(bits)} bits, expected {expected_bits}")
        
        # Reshape to original structure
        bits = bits.reshape(batches, num_valid, n_bits).astype(np.float32)
        
        # Convert back to original scale
        bits *= 2
        bits -= 1
        bits *= 1 / (np.sqrt(n_bits))
        
        try:
            return torch.tensor(bits, dtype=torch.float32)
        except:
            # If torch is not available, return numpy array
            return bits


class AudioCodec:
    """Audio codec for encoding and decoding audio using FocalCodec."""
    
    def __init__(self, config_name: str = "25hz"):
        """
        Initialize the audio codec.
        
        Args:
            config_name: Configuration name ("12_5hz", "25hz", "50hz")
        """
        if not FOCALCODEC_AVAILABLE:
            raise ImportError("FocalCodec not available. Please install focalcodec.")
        
        self.config_name = config_name
        self.config_path = CodecConfig.get_config(config_name)
        self.codec = None
        self._load_model()
    
    def _load_model(self):
        """Load the FocalCodec model."""
        try:
            self.codec = focalcodec.FocalCodec.from_pretrained(self.config_path)
            self.codec.eval().requires_grad_(False)
            logger.info(f"Loaded FocalCodec model: {self.config_name}")
        except Exception as e:
            logger.error(f"Failed to load FocalCodec model: {e}")
            raise
    
    @property
    def sample_rate(self) -> int:
        """Get the codec's sample rate."""
        return self.codec.sample_rate if self.codec else 24000
    
    def encode_audio(self, audio_data: torch.Tensor, sample_rate: int) -> Tuple[int, bytes, dict]:
        """
        Encode audio data to compressed bytes.
        
        Args:
            audio_data: Audio tensor of shape (channels, samples)
            sample_rate: Sample rate of input audio
            
        Returns:
            compressed_size: Size of compressed data in bytes
            compressed_data: Compressed audio data as bytes
            metadata: Dictionary with encoding metadata
        """
        if self.codec is None:
            raise RuntimeError("Codec not initialized")
        
        # Resample if necessary
        if sample_rate != self.codec.sample_rate:
            audio_data = torchaudio.functional.resample(
                audio_data, sample_rate, self.codec.sample_rate
            )
        
        # Encode audio to tokens
        tokens = self.codec.sig_to_toks(audio_data)
        
        # Convert tokens to codes
        codes = self.codec.toks_to_codes(tokens)
        
        # Pack codes into bytes
        n_bits, packed_data, num_valid, batches = BitPacker.pack_codes(codes)
        
        # Create metadata
        metadata = {
            'n_bits': n_bits,
            'num_valid': num_valid,
            'batches': batches,
            'original_sample_rate': sample_rate,
            'codec_sample_rate': self.codec.sample_rate,
            'config_name': self.config_name,
            'audio_duration': audio_data.shape[1] / self.codec.sample_rate
        }
        
        logger.info(f"Encoded {metadata['audio_duration']:.2f}s audio to {len(packed_data)} bytes")
        return len(packed_data), packed_data.tobytes(), metadata
    
    def decode_audio(self, compressed_data: bytes, metadata: dict, target_sample_rate: int = None, as_bytes: bool = False) -> torch.Tensor | bytes:
        """
        Decode compressed bytes back to audio.
        
        Args:
            compressed_data: Compressed audio data
            metadata: Metadata from encoding
            target_sample_rate: Target sample rate for output (optional)
            
        Returns:
            Decoded audio tensor
        """
        if self.codec is None:
            raise RuntimeError("Codec not initialized")
        
        # Unpack codes from bytes
        codes = BitPacker.unpack_codes(
            compressed_data,
            metadata['n_bits'],
            metadata['num_valid'],
            metadata['batches']
        )
        
        # Convert codes to tokens
        tokens = self.codec.codes_to_toks(codes)
        
        # Decode tokens to audio
        audio_data = self.codec.toks_to_sig(tokens)
        
        # Resample to target sample rate if specified
        if target_sample_rate and target_sample_rate != self.codec.sample_rate:
            audio_data = torchaudio.functional.resample(
                audio_data, self.codec.sample_rate, target_sample_rate
            )
        
        logger.info(f"Decoded {len(compressed_data)} bytes to {metadata['audio_duration']:.2f}s audio")
        if as_bytes:
            # Convert to bytes
            audio_data = audio_data.numpy().tobytes()
            logger.debug(f"Converted audio tensor to bytes ({len(audio_data)} bytes)")
            return audio_data
        return audio_data
    
    def get_compression_stats(self, metadata: dict) -> dict:
        """
        Calculate compression statistics.
        
        Args:
            metadata: Metadata from encoding
            
        Returns:
            Dictionary with compression statistics
        """
        duration = metadata['audio_duration']
        compressed_size = metadata['num_valid'] * metadata['n_bits'] / 8
        bytes_per_second = compressed_size / duration if duration > 0 else 0
        
        return {
            'duration_seconds': duration,
            'compressed_bytes': compressed_size,
            'bytes_per_second': bytes_per_second,
            'bits_per_second': bytes_per_second * 8,
            'compression_ratio': f"1:{int(1/(bytes_per_second/16000)) if bytes_per_second > 0 else 0}"
        }
    
    def validate_round_trip(self, audio: torch.Tensor, sample_rate: float, tolerance: float = 1e-3) -> bool:
        """
        Validate that audio can be encoded and decoded without significant loss.
        
        Args:
            audio: Input audio tensor
            tolerance: Maximum allowed difference threshold
            
        Returns:
            True if round-trip is successful within tolerance
        """
        try:
            # Encode to compressed data
            compressed_size, compressed_data, metadata = self.encode_audio(audio, sample_rate)
            
            # Decode back to audio
            decoded_audio = self.decode_audio(compressed_data, metadata, self.sample_rate)
            
            # Compare shapes
            if audio.shape != decoded_audio.shape:
                logger.warning(f"Shape mismatch: {audio.shape} != {decoded_audio.shape}")
                return False
            
            # Calculate difference
            diff = torch.abs(audio - decoded_audio).mean().item()
            logger.info(f"Round-trip mean absolute difference: {diff:.6f}")
            
            return diff < tolerance
            
        except Exception as e:
            logger.error(f"Round-trip validation failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False


class AudioFile:
    """Utility class for audio file operations."""
    
    @staticmethod
    def load_audio(file_path: str) -> Tuple[torch.Tensor, int]:
        """
        Load audio from file.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            audio_data: Audio tensor
            sample_rate: Sample rate
        """
        try:
            audio_data, sample_rate = torchaudio.load(file_path)
            logger.info(f"Loaded audio: {file_path} ({audio_data.shape[1]/sample_rate:.2f}s)")
            return audio_data, sample_rate
        except Exception as e:
            logger.error(f"Failed to load audio file {file_path}: {e}")
            raise
    
    @staticmethod
    def save_audio(audio_data: torch.Tensor, sample_rate: int, file_path: str):
        """
        Save audio to file.
        
        Args:
            audio_data: Audio tensor
            sample_rate: Sample rate
            file_path: Output file path
        """
        try:
            torchaudio.save(file_path, audio_data, sample_rate)
            logger.info(f"Saved audio: {file_path}")
        except Exception as e:
            logger.error(f"Failed to save audio file {file_path}: {e}")
            raise
    
    @staticmethod
    def save_with_metadata(compressed_data: bytes, metadata: dict, file_path: str):
        """
        Save compressed audio and metadata to file.
        
        Args:
            compressed_data: Compressed audio bytes
            metadata: Encoding metadata
            file_path: Output file path
        """
        import json
        
        try:
            with open(file_path, 'wb') as f:
                # Write metadata as JSON header
                metadata_json = json.dumps(metadata).encode('utf-8')
                metadata_size = len(metadata_json)
                
                # Write header: [metadata_size:4][metadata][compressed_data]
                f.write(metadata_size.to_bytes(4, 'little'))
                f.write(metadata_json)
                f.write(compressed_data)
            
            logger.info(f"Saved compressed audio: {file_path} ({len(compressed_data)} bytes)")
        except Exception as e:
            logger.error(f"Failed to save compressed file {file_path}: {e}")
            raise
    
    @staticmethod
    def load_with_metadata(file_path: str) -> Tuple[bytes, dict]:
        """
        Load compressed audio and metadata from file.
        
        Args:
            file_path: Input file path
            
        Returns:
            compressed_data: Compressed audio bytes
            metadata: Encoding metadata
        """
        import json
        
        try:
            with open(file_path, 'rb') as f:
                # Read metadata size
                metadata_size = int.from_bytes(f.read(4), 'little')
                
                # Read metadata
                metadata_json = f.read(metadata_size)
                metadata = json.loads(metadata_json.decode('utf-8'))
                
                # Read compressed data
                compressed_data = f.read()
            
            logger.info(f"Loaded compressed audio: {file_path} ({len(compressed_data)} bytes)")
            return compressed_data, metadata
        except Exception as e:
            logger.error(f"Failed to load compressed file {file_path}: {e}")
            raise


class AudioStream:
    """Utility class for audio stream operations."""
    
    @staticmethod
    def save_compressed_to_stream(compressed_data: bytes, metadata: dict, stream: BinaryIO):
        """
        Save compressed audio and metadata to a binary stream.
        
        Args:
            compressed_data: Compressed audio bytes
            metadata: Encoding metadata
            stream: Binary stream to write to
        """
        import json
        
        # Write metadata as JSON header
        metadata_json = json.dumps(metadata).encode('utf-8')
        metadata_size = len(metadata_json)
        
        # Write header: [metadata_size:4][metadata][compressed_data]
        stream.write(metadata_size.to_bytes(4, 'little'))
        stream.write(metadata_json)
        stream.write(compressed_data)
        
        logger.debug(f"Wrote compressed audio to stream ({len(compressed_data)} bytes)")
    
    @staticmethod
    def load_compressed_from_stream(stream: BinaryIO) -> Tuple[bytes, dict]:
        """
        Load compressed audio and metadata from a binary stream.
        
        Args:
            stream: Binary stream to read from
            
        Returns:
            compressed_data: Compressed audio bytes
            metadata: Encoding metadata
        """
        import json
        
        # Read metadata size
        metadata_size_bytes = stream.read(4)
        if len(metadata_size_bytes) != 4:
            raise ValueError("Invalid stream format: cannot read metadata size")
        
        metadata_size = int.from_bytes(metadata_size_bytes, 'little')
        
        # Read metadata
        metadata_json = stream.read(metadata_size)
        if len(metadata_json) != metadata_size:
            raise ValueError("Invalid stream format: cannot read metadata")
        
        metadata = json.loads(metadata_json.decode('utf-8'))
        
        # Read compressed data
        compressed_data = stream.read()
        
        logger.debug(f"Read compressed audio from stream ({len(compressed_data)} bytes)")
        return compressed_data, metadata
    
    @staticmethod
    def create_bytes_stream(compressed_data: bytes, metadata: dict) -> io.BytesIO:
        """
        Create a BytesIO stream with compressed audio data.
        
        Args:
            compressed_data: Compressed audio bytes
            metadata: Encoding metadata
            
        Returns:
            BytesIO stream with the data
        """
        stream = io.BytesIO()
        AudioStream.save_compressed_to_stream(compressed_data, metadata, stream)
        stream.seek(0)
        return stream


# Example usage and testing
if __name__ == "__main__":
    import tempfile
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    if not FOCALCODEC_AVAILABLE:
        print("FocalCodec not available. Skipping examples.")
        exit(1)
    
    # Initialize codec
    codec = AudioCodec("25hz")
    
    # load ginjoints.wav
    filename = r'.\ginjoints.wav'
    test_audio, sample_rate = AudioFile.load_audio(filename)
    duration = test_audio.shape[1] / sample_rate
    
    print(f"Test audio: {duration}s at {sample_rate}Hz")
    print(f"Audio shape: {test_audio.shape}")
    
    # Encode audio
    compressed_size, compressed_data, metadata = codec.encode_audio(test_audio.clone(), sample_rate)
    
    # Print compression stats
    stats = codec.get_compression_stats(metadata)
    print(f"Compression stats:")
    print(f"  Duration: {stats['duration_seconds']:.2f}s")
    print(f"  Compressed size: {stats['compressed_bytes']:.0f} bytes")
    print(f"  Bitrate: {stats['bits_per_second']:.0f} bps")
    print(f"  Compression ratio: {stats['compression_ratio']}")
    
    # Test file operations
    with tempfile.NamedTemporaryFile(suffix='.vox', delete=False) as temp_file:
        temp_path = temp_file.name
    
    try:
        # Save compressed to file
        AudioFile.save_with_metadata(compressed_data, metadata, temp_path)
        
        # Load compressed from file
        loaded_data, loaded_metadata = AudioFile.load_with_metadata(temp_path)
        
        # Verify data integrity
        assert loaded_data == compressed_data
        assert loaded_metadata == metadata
        print("File operations: OK")
        
    finally:
        os.unlink(temp_path)
    
    # Test stream operations
    stream = AudioStream.create_bytes_stream(compressed_data, metadata)
    stream_data, stream_metadata = AudioStream.load_compressed_from_stream(stream)
    
    # Verify stream data integrity
    assert stream_data == compressed_data
    assert stream_metadata == metadata
    print("Stream operations: OK")
    
    # Decode audio
    decoded_audio = codec.decode_audio(compressed_data, metadata, sample_rate, as_bytes=False)

    decoded_audio_stream = io.BytesIO()
    torchaudio.save(decoded_audio_stream, decoded_audio, sample_rate, format='wav', bits_per_sample=16)
    
    # print(f"Decoded audio shape: {decoded_audio.shape}")
    print("Codec operations: OK")
    
    # Validate round-trip encoding/decoding
    # if codec.validate_round_trip(test_audio, sample_rate):
    #     print("Round-trip validation: OK")
    # else:
    #     print("Round-trip validation: FAILED")
    import audio
    decoded_audio_stream.seek(0)
    audio.play(decoded_audio_stream.read(), sample_rate=sample_rate)
