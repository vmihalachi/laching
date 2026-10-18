using System.Buffers.Binary;
using System.Text;
using SkiaSharp;

namespace Webling.Images.Tests;

/// <summary>
/// Sample files, drawn and assembled in code so the tests carry no binaries.
/// </summary>
internal static class Samples
{
    /// <summary>
    /// A JPEG stored <paramref name="width"/> × <paramref name="height"/>, red on the left half and blue on the right,
    /// with an EXIF block that holds <paramref name="orientation"/>.
    /// </summary>
    public static byte[] JpegWithExif(int width, int height, ushort orientation)
    {
        using var bitmap = new SKBitmap(width, height);
        using (var canvas = new SKCanvas(bitmap))
        {
            canvas.Clear(SKColors.Blue);
            using var red = new SKPaint { Color = SKColors.Red };
            canvas.DrawRect(0, 0, width / 2f, height, red);
        }

        var jpeg = bitmap.Encode(SKEncodedImageFormat.Jpeg, 95).ToArray();

        // APP1 "Exif": a big-endian TIFF header and one IFD entry, Orientation (0x0112, SHORT).
        var tiff = new byte[26];
        "MM\0*"u8.CopyTo(tiff);
        BinaryPrimitives.WriteUInt32BigEndian(tiff.AsSpan(4), 8);
        BinaryPrimitives.WriteUInt16BigEndian(tiff.AsSpan(8), 1);
        BinaryPrimitives.WriteUInt16BigEndian(tiff.AsSpan(10), 0x0112);
        BinaryPrimitives.WriteUInt16BigEndian(tiff.AsSpan(12), 3);
        BinaryPrimitives.WriteUInt32BigEndian(tiff.AsSpan(14), 1);
        BinaryPrimitives.WriteUInt16BigEndian(tiff.AsSpan(18), orientation);
        var app1 = new List<byte> { 0xFF, 0xE1, 0, 0 };
        app1.AddRange("Exif\0\0"u8.ToArray());
        app1.AddRange(tiff);
        BinaryPrimitives.WriteUInt16BigEndian(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(app1)[2..], (ushort)(app1.Count - 2));

        return [.. jpeg.AsSpan(0, 2), .. app1, .. jpeg.AsSpan(2)];
    }

    /// <summary>
    /// A transparent PNG with an opaque red square in the middle.
    /// </summary>
    public static byte[] TransparentPng(int size)
    {
        using var bitmap = new SKBitmap(new SKImageInfo(size, size, SKColorType.Rgba8888, SKAlphaType.Premul));
        using (var canvas = new SKCanvas(bitmap))
        {
            canvas.Clear(SKColors.Transparent);
            using var red = new SKPaint { Color = SKColors.Red };
            canvas.DrawRect(size / 4f, size / 4f, size / 2f, size / 2f, red);
        }

        return bitmap.Encode(SKEncodedImageFormat.Png, 100).ToArray();
    }

    /// <summary>
    /// The chunks a PNG decoder reads before the pixels, for an image of any size: the signature, IHDR, a token
    /// IDAT and IEND, each with a valid CRC.
    /// </summary>
    public static byte[] PngHeader(int width, int height)
    {
        var header = new byte[13];
        BinaryPrimitives.WriteInt32BigEndian(header, width);
        BinaryPrimitives.WriteInt32BigEndian(header.AsSpan(4), height);
        header[8] = 8; // bit depth
        header[9] = 6; // RGBA

        return
        [
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            .. Chunk("IHDR", header),
            .. Chunk("IDAT", [0x78, 0x9C, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01]),
            .. Chunk("IEND", []),
        ];
    }

    /// <summary>
    /// The first bytes of an iPhone HEIC photo: an ISO media <c>ftyp</c> box with the <c>heic</c> brand.
    /// </summary>
    public static byte[] HeicHeader() =>
        [0x00, 0x00, 0x00, 0x18, .. "ftypheic"u8, 0x00, 0x00, 0x00, 0x00, .. "mif1heic"u8, .. new byte[64]];

    public static bool ContainsAscii(byte[] bytes, string text) =>
        bytes.AsSpan().IndexOf(Encoding.ASCII.GetBytes(text)) >= 0;

    private static byte[] Chunk(string type, byte[] data)
    {
        var chunk = new byte[12 + data.Length];
        BinaryPrimitives.WriteInt32BigEndian(chunk, data.Length);
        Encoding.ASCII.GetBytes(type).CopyTo(chunk, 4);
        data.CopyTo(chunk, 8);
        BinaryPrimitives.WriteUInt32BigEndian(chunk.AsSpan(8 + data.Length), Crc32(chunk.AsSpan(4, 4 + data.Length)));
        return chunk;
    }

    private static uint Crc32(ReadOnlySpan<byte> bytes)
    {
        var crc = 0xFFFFFFFFu;
        foreach (var b in bytes)
        {
            crc ^= b;
            for (var bit = 0; bit < 8; bit++)
            {
                crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
            }
        }

        return ~crc;
    }
}
