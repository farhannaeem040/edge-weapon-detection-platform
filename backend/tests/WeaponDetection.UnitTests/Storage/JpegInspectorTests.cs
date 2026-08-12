using WeaponDetection.Infrastructure.Storage;
using Xunit;

namespace WeaponDetection.UnitTests.Storage;

// FS-08 §9: "decode as a real JPEG" without a heavy image-library dependency (T-153/T-156). These
// tests build minimal, hand-crafted marker-segment byte sequences rather than loading real JPEG
// fixture files, so the exact structural edge cases (missing EOI, truncated segment, non-JPEG bytes)
// are precisely controlled.
public class JpegInspectorTests
{
    // A minimal, single-component SOF0 segment carrying the given width/height, wrapped in
    // SOI...EOI. Not a real, fully decodable JPEG (no DHT/SOS/entropy-coded scan data) — but
    // JpegInspector never claims to fully decode pixels, only to validate structure/magic
    // bytes/dimensions cheaply, so this is exactly the shape it is designed to accept.
    private static byte[] BuildMinimalJpeg(int width, int height)
    {
        var bytes = new List<byte>
        {
            0xFF, 0xD8, // SOI
            0xFF, 0xC0, // SOF0
            0x00, 0x0B, // Lf = 11
            0x08, // precision
            (byte)(height >> 8), (byte)(height & 0xFF),
            (byte)(width >> 8), (byte)(width & 0xFF),
            0x01, // Nf = 1 component
            0x01, 0x11, 0x00, // component id, sampling factors, quant table id
            0xFF, 0xD9, // EOI
        };
        return bytes.ToArray();
    }

    [Fact]
    public void TryGetDimensions_WellFormedMinimalJpeg_ReturnsTrueWithCorrectDimensions()
    {
        var bytes = BuildMinimalJpeg(width: 1280, height: 720);

        var result = JpegInspector.TryGetDimensions(bytes, out var dimensions);

        Assert.True(result);
        Assert.Equal(1280, dimensions.Width);
        Assert.Equal(720, dimensions.Height);
    }

    [Fact]
    public void TryGetDimensions_NotStartingWithSoiMarker_ReturnsFalse()
    {
        var bytes = "not a jpeg at all, just text bytes pretending to be one"u8.ToArray();

        var result = JpegInspector.TryGetDimensions(bytes, out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_EmptyArray_ReturnsFalse()
    {
        var result = JpegInspector.TryGetDimensions(Array.Empty<byte>(), out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_MissingEoiTrailer_ReturnsFalse()
    {
        var bytes = BuildMinimalJpeg(640, 480);
        var truncated = bytes[..^2]; // drop the trailing FF D9

        var result = JpegInspector.TryGetDimensions(truncated, out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_CorruptedSofSegmentLength_ReturnsFalse()
    {
        var bytes = BuildMinimalJpeg(640, 480);
        // Overwrite Lf (bytes[4..6]) with a length that walks off the end of the buffer.
        bytes[4] = 0xFF;
        bytes[5] = 0xFF;

        var result = JpegInspector.TryGetDimensions(bytes, out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_ZeroWidthOrHeight_ReturnsFalse()
    {
        var bytes = BuildMinimalJpeg(width: 0, height: 480);

        var result = JpegInspector.TryGetDimensions(bytes, out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_OnlySoiAndEoi_NoFrameHeader_ReturnsFalse()
    {
        byte[] bytes = [0xFF, 0xD8, 0xFF, 0xD9];

        var result = JpegInspector.TryGetDimensions(bytes, out _);

        Assert.False(result);
    }

    [Fact]
    public void TryGetDimensions_PngMagicBytes_ReturnsFalse()
    {
        byte[] pngSignature = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];

        var result = JpegInspector.TryGetDimensions(pngSignature, out _);

        Assert.False(result);
    }
}
