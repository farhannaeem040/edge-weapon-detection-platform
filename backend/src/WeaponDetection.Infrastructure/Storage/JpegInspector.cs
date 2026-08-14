namespace WeaponDetection.Infrastructure.Storage;

// FS-08 §9: "decode as a real JPEG" without adding a heavy image-library dependency to the project
// (none is already referenced anywhere in this codebase's dependency graph — SixLabors.ImageSharp,
// System.Drawing.Common, etc. are all absent). Rather than fully decoding pixel data, this walks the
// JPEG marker-segment structure far enough to (a) confirm the byte stream is structurally a JPEG, not
// merely bytes that happen to start with the SOI marker, and (b) recover the encoded width/height
// from the frame header — cheap, dependency-free, and sufficient to reject non-JPEG/corrupt/truncated
// uploads before they are ever written to disk.
public static class JpegInspector
{
    public readonly record struct JpegDimensions(int Width, int Height);

    // True only if: the stream starts with the JPEG SOI marker (FF D8), a well-formed marker-segment
    // chain leads to a Start-Of-Frame segment with non-zero width/height, and the stream ends with
    // the JPEG EOI marker (FF D9) — a truncated/corrupt file (missing EOI, or a length field that
    // walks off the end of the buffer) is rejected rather than accepted on partial evidence.
    public static bool TryGetDimensions(ReadOnlySpan<byte> data, out JpegDimensions dimensions)
    {
        dimensions = default;

        if (data.Length < 4 || data[0] != 0xFF || data[1] != 0xD8)
        {
            return false;
        }

        if (data.Length < 2 || data[^2] != 0xFF || data[^1] != 0xD9)
        {
            return false;
        }

        var offset = 2;
        while (offset + 1 < data.Length)
        {
            if (data[offset] != 0xFF)
            {
                // A stray non-marker byte where a marker was expected — not a well-formed JPEG
                // marker chain.
                return false;
            }

            var marker = data[offset + 1];
            offset += 2;

            // Markers with no length/parameter segment: standalone RST0-RST7, TEM, and padding
            // 0xFF fill bytes (skip repeats of 0xFF itself).
            if (marker == 0xFF)
            {
                offset--;
                continue;
            }

            if (marker is >= 0xD0 and <= 0xD7 or 0x01)
            {
                continue;
            }

            if (marker == 0xD9)
            {
                // Reached EOI without ever finding a SOF segment.
                return false;
            }

            if (offset + 1 >= data.Length)
            {
                return false;
            }

            var segmentLength = (data[offset] << 8) | data[offset + 1];
            if (segmentLength < 2 || offset + segmentLength > data.Length)
            {
                return false;
            }

            // SOF0-SOF15 (0xC0-0xCF) EXCLUDING DHT (0xC4), JPG extension (0xC8), and DAC (0xCC),
            // which share the numeric range but are not frame headers.
            var isStartOfFrame = marker is >= 0xC0 and <= 0xCF && marker is not (0xC4 or 0xC8 or 0xCC);
            if (isStartOfFrame)
            {
                if (segmentLength < 7)
                {
                    return false;
                }

                var height = (data[offset + 3] << 8) | data[offset + 4];
                var width = (data[offset + 5] << 8) | data[offset + 6];
                if (width <= 0 || height <= 0)
                {
                    return false;
                }

                dimensions = new JpegDimensions(width, height);
                return true;
            }

            if (marker == 0xDA)
            {
                // Start-Of-Scan reached before any SOF segment was found — malformed.
                return false;
            }

            offset += segmentLength;
        }

        return false;
    }
}
